"""Denial intelligence: turns rule findings into likely denial causes and RCM actions."""

from dataclasses import dataclass

from src.models import DenialAnalysis, RcmAction, RuleFinding, Severity


@dataclass(frozen=True)
class DenialRule:
    category: str
    carc: tuple[str, ...]
    root_cause: str
    actions: tuple[tuple[str, str], ...]  # (owner, action)


# CARC = Claim Adjustment Reason Code (what the payer would put on the denial).
DENIAL_RULES: dict[str, DenialRule] = {
    "PAYER_UNAVAILABLE": DenialRule(
        "PAYER_UNAVAILABLE", (),
        "The payer's eligibility system did not respond; coverage is unverified.",
        (("Eligibility Team", "Resubmit the 270 in 1-2 hours."),
         ("Front Desk", "If the visit is today, verify coverage on the payer portal or by phone.")),
    ),
    "PAYER_REJECTED_72": DenialRule(
        "INVALID_MEMBER_ID", ("31",),
        "The member ID on the registration does not match the payer's records "
        "(patient matched on name and DOB under a different ID).",
        (("Front Desk", "Scan the front and back of the insurance card and correct the member ID."),
         ("Eligibility Team", "Resubmit the 270 with the corrected member ID."),
         ("Patient Access Lead", "Review registration QA for member-ID keying errors.")),
    ),
    "PAYER_REJECTED_75": DenialRule(
        "SUBSCRIBER_NOT_FOUND", ("31",),
        "The payer has no record of this patient; the patient is likely registered to the wrong payer.",
        (("Front Desk", "Confirm the patient's current insurance and obtain the card."),
         ("Eligibility Team", "Run eligibility against the correct payer (or a batch discovery search)."),
         ("Financial Counselor", "If no coverage is found, provide a self-pay estimate.")),
    ),
    "PAYER_REJECTED_71": DenialRule(
        "DOB_MISMATCH", ("31",),
        "The date of birth on the registration does not match the payer's records.",
        (("Front Desk", "Verify DOB against photo ID and the insurance card; correct registration."),
         ("Eligibility Team", "Resubmit the 270 with the corrected DOB.")),
    ),
    "PAYER_REJECTED_58": DenialRule(
        "DOB_MISMATCH", ("31",),
        "The date of birth is missing or invalid.",
        (("Front Desk", "Capture a valid DOB and resubmit the 270."),),
    ),
    "PAYER_REJECTED_73": DenialRule(
        "NAME_MISMATCH", ("140",),
        "The patient name does not match the name the payer has for this member ID.",
        (("Front Desk", "Match the patient's name exactly to the insurance card (check legal name changes)."),
         ("Eligibility Team", "Resubmit the 270.")),
    ),
    "PAYER_REJECTED_41": DenialRule(
        "PAYER_NOT_SUPPORTED", (),
        "The inquiry was routed to a payer the clearinghouse does not support.",
        (("Eligibility Team", "Check the payer ID and clearinghouse enrollment."),),
    ),
    "COVERAGE_INACTIVE": DenialRule(
        "COVERAGE_TERMINATED", ("27",),
        "Coverage was not active on the date of service.",
        (("Front Desk", "Ask the patient for new or replacement insurance before the visit."),
         ("Eligibility Team", "Update the registration with the payer's termination date."),
         ("Financial Counselor", "If uninsured, screen for Medicaid/marketplace and give a self-pay estimate.")),
    ),
    "COB_ORDER": DenialRule(
        "COORDINATION_OF_BENEFITS", ("22",),
        "Another payer is primary; billing this payer first will deny for COB.",
        (("Front Desk", "Collect the primary payer's card and complete a COB/MSP questionnaire."),
         ("Eligibility Team", "Reorder coverage (other payer primary) and verify primary eligibility."),
         ("Billing", "Bill the primary payer first, then this payer as secondary with the primary EOB.")),
    ),
    "OTHER_COVERAGE": DenialRule(
        "COORDINATION_OF_BENEFITS", ("22",),
        "The payer reports other coverage; COB order is unconfirmed.",
        (("Front Desk", "Ask the patient about other insurance and update COB order."),),
    ),
    "PCP_MISMATCH": DenialRule(
        "PCP_NOT_ASSIGNED", ("242",),
        "The PCP on file is not the payer's assigned PCP for this HMO member.",
        (("Front Desk", "Update the PCP on the registration to the payer's assigned PCP."),
         ("Care Coordinator", "If the patient wants to keep their PCP, have them change PCP with the plan.")),
    ),
    "PCP_MISSING": DenialRule(
        "PCP_NOT_ASSIGNED", ("242",),
        "The plan requires a PCP but none is recorded.",
        (("Front Desk", "Record the payer's assigned PCP on the registration."),),
    ),
    "NOT_ASSIGNED_PCP": DenialRule(
        "PCP_NOT_ASSIGNED", ("242",),
        "The rendering provider is not the member's assigned PCP.",
        (("Care Coordinator", "Obtain a referral from the assigned PCP or reschedule with the PCP."),),
    ),
    "REFERRAL_REQUIRED": DenialRule(
        "REFERRAL_REQUIRED", ("288",),
        "The plan requires a referral for specialist visits.",
        (("Care Coordinator", "Obtain a referral from the PCP before the specialist visit and record the number."),),
    ),
    "MEMBER_ID_MISMATCH": DenialRule(
        "INVALID_MEMBER_ID", ("31",),
        "The payer returned a different member ID than the registration.",
        (("Front Desk", "Update the registration with the member ID returned by the payer."),),
    ),
    "REGISTRATION_OUTDATED": DenialRule(
        "COVERAGE_TERMINATED", ("27",),
        "Registration does not reflect the payer's termination date.",
        (),
    ),
    "DEDUCTIBLE_NOT_MET": DenialRule(
        "PATIENT_RESPONSIBILITY", (),
        "The deductible is not met; the patient will owe part or all of the allowed amount.",
        (("Front Desk", "Give the patient a cost estimate and collect a deposit at check-in."),),
    ),
}

# Lower number = more fundamental; decides the primary category.
_CATEGORY_PRIORITY = [
    "PAYER_UNAVAILABLE", "PAYER_NOT_SUPPORTED", "SUBSCRIBER_NOT_FOUND", "INVALID_MEMBER_ID",
    "DOB_MISMATCH", "NAME_MISMATCH", "COVERAGE_TERMINATED", "COORDINATION_OF_BENEFITS",
    "PCP_NOT_ASSIGNED", "REFERRAL_REQUIRED", "PATIENT_RESPONSIBILITY",
]


def analyze(findings: list[RuleFinding]) -> DenialAnalysis:
    """Predict denial risk and recommend RCM actions from rule findings."""
    matched = [(f, DENIAL_RULES[f.rule]) for f in findings if f.rule in DENIAL_RULES]
    if not matched:
        return DenialAnalysis(
            denial_risk="low",
            root_cause="No eligibility issues found.",
            actions=[RcmAction(priority=1, owner="Front Desk", action="Collect the copay at check-in.")],
        )

    matched.sort(key=lambda m: _CATEGORY_PRIORITY.index(m[1].category))
    primary_finding, primary = matched[0]

    if primary.category == "PAYER_UNAVAILABLE":
        risk = "unknown"
    elif any(f.severity == Severity.ERROR for f, _ in matched):
        risk = "high"
    elif any(f.severity == Severity.WARNING for f, _ in matched):
        risk = "medium"
    else:
        risk = "low"

    carc: list[str] = []
    actions: list[RcmAction] = []
    seen_actions: set[str] = set()
    for _, rule in matched:
        carc += [c for c in rule.carc if c not in carc]
        for owner, action in rule.actions:
            if action not in seen_actions:
                seen_actions.add(action)
                actions.append(RcmAction(priority=len(actions) + 1, owner=owner, action=action))

    return DenialAnalysis(
        denial_risk=risk,
        category=primary.category,
        carc_codes=carc,
        root_cause=primary.root_cause,
        actions=actions,
    )
