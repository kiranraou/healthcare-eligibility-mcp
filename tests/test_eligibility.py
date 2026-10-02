from datetime import date, timedelta

import pytest

from src import db, denials, service
from src.models import RuleFinding, Severity

DOS = date(2026, 10, 2)


@pytest.mark.parametrize(
    ("patient_id", "status", "category", "carc"),
    [
        ("P001", "REJECTED", "INVALID_MEMBER_ID", ["31"]),
        ("P002", "ELIGIBLE", None, []),
        ("P003", "INELIGIBLE", "COVERAGE_TERMINATED", ["27"]),
        ("P004", "ELIGIBLE_WITH_ISSUES", "COORDINATION_OF_BENEFITS", ["22"]),
        ("P005", "ELIGIBLE_WITH_ISSUES", "PCP_NOT_ASSIGNED", ["242", "288"]),
        ("P006", "REJECTED", "SUBSCRIBER_NOT_FOUND", ["31"]),
        ("P007", "REJECTED", "DOB_MISMATCH", ["31"]),
        ("P008", "ELIGIBLE", "PATIENT_RESPONSIBILITY", []),
        ("P009", "PAYER_UNAVAILABLE", "PAYER_UNAVAILABLE", []),
    ],
)
def test_scenarios(patient_id, status, category, carc):
    result = service.check_eligibility(patient_id, DOS, save=False)
    analysis = result["denial_analysis"]
    assert result["status"] == status
    assert analysis["category"] == category
    assert analysis["carc_codes"] == carc
    assert analysis["actions"], "every scenario should recommend at least one action"


def test_terminated_plan_is_active_before_term_date():
    result = service.check_eligibility("P003", date(2026, 6, 15), save=False)
    assert result["status"] == "ELIGIBLE"


def test_benefits_summary():
    benefits = service.check_eligibility("P008", DOS, save=False)["benefits"]
    assert benefits["plan"] == "CIGNA HDHP"
    assert benefits["deductible"] == 3500 and benefits["deductible_remaining"] == 3100
    assert benefits["coinsurance_pct"] == 30
    assert benefits["out_of_pocket_max"] == 7500


def test_pcp_rules_with_rendering_provider():
    findings = service.check_eligibility("P005", DOS, rendering_npi="1801234567", save=False)["findings"]
    rules = {f["rule"] for f in findings}
    assert "PCP_MISMATCH" in rules and "REFERRAL_REQUIRED" in rules
    assert "NOT_ASSIGNED_PCP" not in rules  # rendering provider is the assigned PCP


def test_cob_finding_names_the_primary_payer():
    findings = service.check_eligibility("P004", DOS, save=False)["findings"]
    cob = next(f for f in findings if f["rule"] == "COB_ORDER")
    assert cob["severity"] == "error"
    assert cob["details"]["other_payer"] == "MEDICARE PART B"


def test_check_eligibility_records_transaction():
    result = service.check_eligibility("P001", DOS)
    [saved] = db.list_transactions()
    assert saved["transaction_id"] == result["transaction_id"]
    assert saved["status"] == "REJECTED"
    assert saved["request_270"].startswith("ISA*") and "ST*271" in saved["response_271"]


def test_unknown_patient():
    with pytest.raises(service.EligibilityError, match="P999 not found"):
        service.check_eligibility("P999", DOS)


def test_validate_request_ok():
    assert service.validate_request("P002", DOS) == {"valid": True, "errors": [], "warnings": []}


def test_validate_request_flags_bad_dates_and_hmo_without_pcp():
    result = service.validate_request("P009", date.today() + timedelta(days=60))
    assert not result["valid"]
    assert any("future" in e for e in result["errors"])
    assert "HMO plan with no PCP recorded." in result["warnings"]


@pytest.mark.parametrize(("npi", "valid"), [("1234567893", True), ("1234567890", False), ("12345", False)])
def test_npi_check_digit(npi, valid):
    assert service.is_valid_npi(npi) is valid


def test_find_patients():
    assert [p.patient_id for p in db.find_patients("john smith")] == ["P001"]
    assert [p.patient_id for p in db.find_patients("Smith, John")] == ["P001"]
    assert db.find_patients("nobody") == []


def test_denial_analysis_without_findings():
    analysis = denials.analyze([])
    assert analysis.denial_risk == "low" and analysis.category is None


def test_denial_analysis_picks_most_fundamental_cause():
    findings = [
        RuleFinding(rule="REFERRAL_REQUIRED", severity=Severity.WARNING, message=""),
        RuleFinding(rule="COB_ORDER", severity=Severity.ERROR, message=""),
    ]
    analysis = denials.analyze(findings)
    assert analysis.category == "COORDINATION_OF_BENEFITS"
    assert analysis.denial_risk == "high"
    assert analysis.carc_codes == ["22", "288"]
    assert [a.priority for a in analysis.actions] == list(range(1, len(analysis.actions) + 1))
