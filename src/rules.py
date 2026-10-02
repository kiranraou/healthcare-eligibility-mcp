"""Eligibility rules engine: compares the provider's registration data with the 271."""

from datetime import date

from src.models import (
    Coverage,
    EligibilityResponse,
    EligibilityStatus,
    Patient,
    RuleFinding,
    Severity,
)
from src.x12.common import TIME_PERIODS


def summarize_benefits(response: EligibilityResponse) -> dict:
    """Flatten 271 EB segments into a benefits summary."""
    summary: dict = {
        "coverage_active": response.coverage_active,
        "plan": None,
        "office_visit_copay": None,
        "deductible": None,
        "deductible_remaining": None,
        "coinsurance_pct": None,
        "out_of_pocket_max": None,
        "out_of_pocket_remaining": None,
        "messages": [],
    }
    for b in response.benefits:
        if b.code in ("1", "6") and b.plan_description and not summary["plan"]:
            summary["plan"] = b.plan_description
        elif b.code == "B" and summary["office_visit_copay"] is None:
            summary["office_visit_copay"] = b.amount
        elif b.code == "C":
            key = "deductible_remaining" if TIME_PERIODS.get(b.time_period or "") == "Remaining" else "deductible"
            summary[key] = b.amount
        elif b.code == "A" and b.percent is not None:
            summary["coinsurance_pct"] = round(b.percent * 100)
        elif b.code == "G":
            key = ("out_of_pocket_remaining" if TIME_PERIODS.get(b.time_period or "") == "Remaining"
                   else "out_of_pocket_max")
            summary[key] = b.amount
        summary["messages"] += b.messages
    return summary


def check_cob(coverage: Coverage, response: EligibilityResponse) -> list[RuleFinding]:
    findings = []
    for b in response.benefits:
        if b.code != "R":
            continue
        other = (b.related_entity or {}).get("name") or "another payer"
        other_is_primary = any("IS PRIMARY" in m for m in b.messages)
        if other_is_primary and coverage.priority == "primary":
            findings.append(RuleFinding(
                rule="COB_ORDER",
                severity=Severity.ERROR,
                message=(f"{other} is primary per {response.payer_name}, but {coverage.payer_name} "
                         "is registered as primary. Claims billed in this order will deny."),
                details={"other_payer": other, "payer_messages": b.messages},
            ))
        else:
            findings.append(RuleFinding(
                rule="OTHER_COVERAGE",
                severity=Severity.WARNING,
                message=f"Payer reports additional coverage with {other}; confirm COB order.",
                details={"other_payer": other, "payer_messages": b.messages},
            ))
    return findings


def check_pcp(coverage: Coverage, response: EligibilityResponse,
              rendering_npi: str | None = None) -> list[RuleFinding]:
    findings = []
    pcp = next((b.related_entity for b in response.benefits if b.code == "L" and b.related_entity), None)
    if pcp:
        if not coverage.pcp_npi:
            findings.append(RuleFinding(
                rule="PCP_MISSING", severity=Severity.WARNING,
                message=f"Plan requires a PCP. Payer has {pcp['name']} (NPI {pcp['npi']}); none on file.",
                details={"payer_pcp": pcp},
            ))
        elif coverage.pcp_npi != pcp["npi"]:
            findings.append(RuleFinding(
                rule="PCP_MISMATCH", severity=Severity.WARNING,
                message=(f"PCP on file is {coverage.pcp_name} (NPI {coverage.pcp_npi}), but the payer has "
                         f"{pcp['name']} (NPI {pcp['npi']}). Visits with a non-assigned PCP may deny."),
                details={"payer_pcp": pcp, "pcp_on_file": {"name": coverage.pcp_name, "npi": coverage.pcp_npi}},
            ))
        if rendering_npi and rendering_npi != pcp["npi"]:
            findings.append(RuleFinding(
                rule="NOT_ASSIGNED_PCP", severity=Severity.WARNING,
                message=f"Rendering provider {rendering_npi} is not the patient's assigned PCP.",
                details={"payer_pcp": pcp, "rendering_npi": rendering_npi},
            ))
    for b in response.benefits:
        if b.authorization_required:
            findings.append(RuleFinding(
                rule="REFERRAL_REQUIRED", severity=Severity.WARNING,
                message="; ".join(b.messages) or "Referral/authorization required.",
                details={"service_type_codes": b.service_type_codes},
            ))
    return findings


def evaluate(patient: Patient, coverage: Coverage, response: EligibilityResponse,
             date_of_service: date, rendering_npi: str | None = None) -> list[RuleFinding]:
    """Run every eligibility rule and return the findings."""
    findings: list[RuleFinding] = []

    for r in response.rejections:
        rule = "PAYER_UNAVAILABLE" if r.code == "42" else f"PAYER_REJECTED_{r.code}"
        findings.append(RuleFinding(
            rule=rule,
            severity=Severity.ERROR,
            message=f"Payer rejected the inquiry: {r.description} ({r.code}). {r.follow_up_description}.",
            details=r.model_dump(),
        ))
    if response.rejections:
        return findings  # no benefits to evaluate

    if not response.coverage_active:
        findings.append(RuleFinding(
            rule="COVERAGE_INACTIVE",
            severity=Severity.ERROR,
            message=(f"Coverage is not active on {date_of_service.isoformat()}"
                     + (f"; plan ended {response.plan_end.isoformat()}." if response.plan_end else ".")),
            details={"plan_begin": response.plan_begin, "plan_end": response.plan_end},
        ))
        if response.plan_end and coverage.termination_date is None:
            findings.append(RuleFinding(
                rule="REGISTRATION_OUTDATED",
                severity=Severity.WARNING,
                message="Registration shows open-ended coverage, but the payer has a termination date.",
                details={"payer_plan_end": response.plan_end},
            ))
        return findings

    if response.member_id and response.member_id != coverage.member_id:
        findings.append(RuleFinding(
            rule="MEMBER_ID_MISMATCH", severity=Severity.WARNING,
            message=f"Payer returned member ID {response.member_id}; registration has {coverage.member_id}.",
        ))
    if response.subscriber_last_name and response.subscriber_last_name.upper() != patient.last_name.upper():
        findings.append(RuleFinding(
            rule="NAME_MISMATCH", severity=Severity.WARNING,
            message=f"Payer name {response.subscriber_last_name} differs from {patient.last_name}.",
        ))

    findings += check_cob(coverage, response)
    findings += check_pcp(coverage, response, rendering_npi)

    benefits = summarize_benefits(response)
    if benefits["deductible_remaining"]:
        findings.append(RuleFinding(
            rule="DEDUCTIBLE_NOT_MET", severity=Severity.INFO,
            message=(f"${benefits['deductible_remaining']:,.2f} of the ${benefits['deductible']:,.2f} "
                     "deductible remains; patient responsibility likely."),
            details={k: benefits[k] for k in ("deductible", "deductible_remaining", "coinsurance_pct")},
        ))
    return findings


def overall_status(response: EligibilityResponse, findings: list[RuleFinding]) -> EligibilityStatus:
    if any(f.rule == "PAYER_UNAVAILABLE" for f in findings):
        return EligibilityStatus.PAYER_UNAVAILABLE
    if response.rejections:
        return EligibilityStatus.REJECTED
    if not response.coverage_active:
        return EligibilityStatus.INELIGIBLE
    if any(f.severity in (Severity.ERROR, Severity.WARNING) for f in findings):
        return EligibilityStatus.ELIGIBLE_WITH_ISSUES
    return EligibilityStatus.ELIGIBLE
