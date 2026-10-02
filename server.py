"""Healthcare Eligibility MCP server.

Run locally over stdio:          python server.py
Run over HTTP (e.g. in Docker):  MCP_TRANSPORT=streamable-http python server.py
Open in MCP Inspector:           mcp dev server.py
"""

import functools
import json
import os
from datetime import date
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from src import db, denials, rules, service
from src.payer.mock_payer import submit_270
from src.pipeline import medallion
from src.seed import ensure_seeded
from src.x12.common import EB_CODES, FOLLOW_UP_ACTIONS, REJECT_REASONS, SERVICE_TYPES
from src.x12.x271 import parse_271 as parse_271_text

ensure_seeded()

mcp = MCPServer(
    "Healthcare Eligibility MCP",
    instructions=(
        "Tools for healthcare insurance eligibility verification (X12 270/271) and revenue cycle "
        "management. Look patients up with find_patient, then use check_patient_eligibility for "
        "the full workflow, or the focused tools (check_benefits, detect_cob, validate_pcp, "
        "identify_denial, recommend_rcm_action). All data is synthetic."
    ),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
INQUIRY = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                          openWorldHint=False)


def tool(annotations: ToolAnnotations):
    """Register an MCP tool whose ValueErrors (unknown patient, bad X12) reach the model as messages."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ValueError as exc:
                raise ToolError(str(exc)) from exc

        return mcp.tool(annotations=annotations)(wrapper)

    return decorator


def _inquiry(patient_id: str, date_of_service: date | None, rendering_npi: str | None = None) -> dict[str, Any]:
    """Run a 270/271 cycle without recording it (used by the focused tools)."""
    return service.check_eligibility(patient_id, date_of_service, rendering_npi, save=False)


# ---------------------------------------------------------------- tools


@tool(annotations=READ_ONLY)
def hello_eligibility(name: str) -> str:
    """
    Test tool for the Healthcare Eligibility MCP server.
    """
    return f"Healthcare Eligibility MCP is working, {name}!"


@tool(annotations=READ_ONLY)
def find_patient(name: str) -> list[dict[str, Any]]:
    """Search patients by name (e.g. "John Smith", "Smith"). Returns matching patient records."""
    return [p.model_dump(mode="json") for p in db.find_patients(name)]


@tool(annotations=READ_ONLY)
def list_patients() -> list[dict[str, Any]]:
    """List all patients with their primary payer."""
    result = []
    for p in db.list_patients():
        coverage = db.get_primary_coverage(p.patient_id)
        result.append({
            "patient_id": p.patient_id,
            "name": f"{p.first_name} {p.last_name}",
            "dob": p.dob.isoformat(),
            "payer": coverage.payer_name if coverage else None,
        })
    return result


@tool(annotations=READ_ONLY)
def get_patient_coverage(patient_id: str) -> dict[str, Any]:
    """Return the patient's demographics and the insurance coverage recorded at registration."""
    patient, _ = service.resolve(patient_id)
    return {
        "patient": patient.model_dump(mode="json"),
        "coverages": [c.model_dump(mode="json") for c in db.get_coverages(patient_id)],
    }


@tool(annotations=READ_ONLY)
def validate_eligibility_request(patient_id: str, date_of_service: date | None = None) -> dict[str, Any]:
    """Check registration data before sending a 270: required fields, member ID format, NPI, dates."""
    return service.validate_request(patient_id, date_of_service)


@tool(annotations=READ_ONLY)
def generate_270(patient_id: str, date_of_service: date | None = None, service_type_code: str = "30") -> str:
    """Build an X12 005010X279A1 270 eligibility inquiry for the patient's primary coverage.

    service_type_code: X12 EB/EQ service type, e.g. 30 (health benefit plan), 98 (office visit).
    """
    return service.create_270(patient_id, date_of_service, service_type_code)


@tool(annotations=INQUIRY)
def submit_eligibility(x12_270: str) -> str:
    """Send a 270 to the (mock) payer and return the raw X12 271 response."""
    return submit_270(x12_270)


@tool(annotations=READ_ONLY)
def parse_271(x12_271: str) -> dict[str, Any]:
    """Parse a raw X12 271 into structured JSON: subscriber, plan dates, AAA rejections, EB benefits."""
    response = parse_271_text(x12_271)
    return {
        **response.model_dump(mode="json"),
        "coverage_active": response.coverage_active,
        "benefits_summary": rules.summarize_benefits(response),
    }


@tool(annotations=INQUIRY)
def check_patient_eligibility(patient_id: str, date_of_service: date | None = None,
                              rendering_npi: str | None = None, include_x12: bool = False) -> dict[str, Any]:
    """Run the full eligibility workflow and record it: 270 -> payer -> 271 -> rules -> denial analysis.

    Returns status (ELIGIBLE, ELIGIBLE_WITH_ISSUES, INELIGIBLE, REJECTED, PAYER_UNAVAILABLE),
    benefits, rule findings, and the denial analysis with recommended RCM actions.
    Set include_x12 to also return the raw 270 and 271.
    """
    result = service.check_eligibility(patient_id, date_of_service, rendering_npi)
    if not include_x12:
        result.pop("x12_270")
        result.pop("x12_271")
    return result


@tool(annotations=INQUIRY)
def check_benefits(patient_id: str, date_of_service: date | None = None) -> dict[str, Any]:
    """Return copay, deductible (total/remaining), coinsurance and out-of-pocket from the 271."""
    result = _inquiry(patient_id, date_of_service)
    return {"status": result["status"], "benefits": result["benefits"]}


@tool(annotations=INQUIRY)
def detect_cob(patient_id: str, date_of_service: date | None = None) -> dict[str, Any]:
    """Detect coordination-of-benefits problems (other payer primary, unreported other coverage)."""
    result = _inquiry(patient_id, date_of_service)
    cob = [f for f in result["findings"] if f["rule"] in ("COB_ORDER", "OTHER_COVERAGE")]
    return {"has_cob_issue": bool(cob), "findings": cob, "status": result["status"]}


@tool(annotations=INQUIRY)
def validate_pcp(patient_id: str, rendering_npi: str | None = None,
                 date_of_service: date | None = None) -> dict[str, Any]:
    """Check PCP assignment and referral requirements against the payer's records.

    rendering_npi: optional NPI of the provider seeing the patient, to check it is the assigned PCP.
    """
    result = _inquiry(patient_id, date_of_service, rendering_npi)
    pcp_rules = ("PCP_MISSING", "PCP_MISMATCH", "NOT_ASSIGNED_PCP", "REFERRAL_REQUIRED")
    issues = [f for f in result["findings"] if f["rule"] in pcp_rules]
    return {"pcp_ok": not issues, "findings": issues, "status": result["status"]}


@tool(annotations=INQUIRY)
def identify_denial(patient_id: str, date_of_service: date | None = None) -> dict[str, Any]:
    """Predict the denial category, CARC codes and root cause for this patient's eligibility."""
    result = _inquiry(patient_id, date_of_service)
    analysis = result["denial_analysis"]
    return {
        "status": result["status"],
        "denial_risk": analysis["denial_risk"],
        "category": analysis["category"],
        "carc_codes": analysis["carc_codes"],
        "root_cause": analysis["root_cause"],
        "findings": result["findings"],
    }


@tool(annotations=INQUIRY)
def recommend_rcm_action(patient_id: str, date_of_service: date | None = None) -> dict[str, Any]:
    """Return prioritized RCM actions (owner + action) to resolve this patient's eligibility issues."""
    result = _inquiry(patient_id, date_of_service)
    return {
        "status": result["status"],
        "denial_risk": result["denial_analysis"]["denial_risk"],
        "actions": result["denial_analysis"]["actions"],
    }


@tool(annotations=INQUIRY)
def run_medallion_pipeline(backend: str | None = None) -> dict[str, Any]:
    """Load recorded eligibility transactions into Bronze -> Silver -> Gold tables.

    backend: "sqlite" or "databricks". Default: Databricks if configured in .env, else SQLite.
    """
    return medallion.run(backend)


@tool(annotations=READ_ONLY)
def get_eligibility_analytics(backend: str | None = None) -> dict[str, Any]:
    """Return Gold-layer analytics: payer performance, denial categories, latest status per patient.

    Run run_medallion_pipeline first to refresh.
    """
    return medallion.read_gold(backend)


# ---------------------------------------------------------------- resources


@mcp.resource("eligibility://status")
def eligibility_status() -> str:
    """
    Return the current status of the eligibility platform.
    """
    offline = [p.name for p in db.list_payers() if not p.online]
    lines = [
        "Healthcare Eligibility MCP is operational.",
        f"Patients: {len(db.list_patients())}",
        f"Recorded eligibility checks: {len(db.list_transactions(limit=1_000_000))}",
        f"Payers offline: {', '.join(offline) if offline else 'none'}",
    ]
    return "\n".join(lines)


@mcp.resource("eligibility://payers", mime_type="application/json")
def payers() -> str:
    """Payers the clearinghouse routes to, with their EDI payer IDs and online status."""
    return json.dumps([p.model_dump() for p in db.list_payers()], indent=2)


@mcp.resource("eligibility://patients/{patient_id}", mime_type="application/json")
def patient_record(patient_id: str) -> str:
    """A patient's demographics and registered coverage."""
    return json.dumps(get_patient_coverage(patient_id), indent=2)


@mcp.resource("eligibility://reference/x12-codes", mime_type="application/json")
def x12_codes() -> str:
    """X12 271 code tables: EB01 benefit codes, service types, AAA reject reasons, follow-up actions."""
    return json.dumps({
        "eb01_benefit_codes": EB_CODES,
        "service_type_codes": SERVICE_TYPES,
        "aaa_reject_reasons": REJECT_REASONS,
        "aaa_follow_up_actions": FOLLOW_UP_ACTIONS,
    }, indent=2)


@mcp.resource("eligibility://reference/denial-rules", mime_type="application/json")
def denial_rules() -> str:
    """How each eligibility finding maps to a denial category, CARC codes and RCM actions."""
    return json.dumps({
        rule: {"category": r.category, "carc": list(r.carc), "root_cause": r.root_cause,
               "actions": [{"owner": o, "action": a} for o, a in r.actions]}
        for rule, r in denials.DENIAL_RULES.items()
    }, indent=2)


# ---------------------------------------------------------------- prompts


@mcp.prompt()
def eligibility_analysis(patient_id: str) -> str:
    """
    Generate an eligibility-analysis prompt.
    """
    return f"""
Analyze eligibility for patient {patient_id}.

Use the check_patient_eligibility tool, then review:
1. Patient demographics
2. Insurance coverage
3. Member ID
4. Coverage status
5. Effective and termination dates
6. Benefits
7. PCP requirements
8. Referral requirements
9. Eligibility errors
10. Recommended RCM action
"""


@mcp.prompt()
def denial_root_cause(patient_name: str) -> str:
    """Explain why a patient's eligibility failed and what the RCM team should do."""
    return f"""
Check {patient_name}'s eligibility and tell me why it failed and what the RCM team should do.

Steps:
1. Use find_patient to get the patient ID.
2. Use check_patient_eligibility.
3. Explain the root cause in plain language, citing the payer's AAA reject code or the rule finding.
4. List the likely denial category and CARC codes.
5. Give the RCM actions as a numbered list with the owner of each.
"""


@mcp.prompt()
def daily_rcm_worklist() -> str:
    """Build today's eligibility worklist across all patients."""
    return """
Build today's eligibility worklist.

1. Use list_patients, then check_patient_eligibility for each patient.
2. Group patients by status, highest denial risk first.
3. For each patient with issues, give the root cause and the first RCM action with its owner.
4. Finish with run_medallion_pipeline and summarize get_eligibility_analytics by payer.
"""


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport, host=os.getenv("MCP_HOST", "127.0.0.1"), port=int(os.getenv("MCP_PORT", "8000")))
