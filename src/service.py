"""Eligibility workflow: validate -> 270 -> payer -> 271 -> rules -> denial intelligence."""

import re
import uuid
from datetime import date, timedelta

from src import db, denials, rules
from src.config import get_settings
from src.models import Coverage, EligibilityRequest, Patient
from src.payer.mock_payer import submit_270
from src.x12.x270 import generate_270
from src.x12.x271 import parse_271

MAX_FUTURE_DAYS = 30
MAX_PAST_DAYS = 365


class EligibilityError(ValueError):
    """Raised for unknown patients or missing coverage."""


def is_valid_npi(npi: str) -> bool:
    """Validate a 10-digit NPI with the Luhn check digit (prefix 80840)."""
    if not re.fullmatch(r"\d{10}", npi or ""):
        return False
    digits = [int(d) for d in "80840" + npi[:-1]]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(npi[-1])


def resolve(patient_id: str) -> tuple[Patient, Coverage]:
    patient = db.get_patient(patient_id)
    if patient is None:
        raise EligibilityError(f"Patient {patient_id} not found.")
    coverage = db.get_primary_coverage(patient_id)
    if coverage is None:
        raise EligibilityError(f"Patient {patient_id} has no coverage on file.")
    return patient, coverage


def validate_request(patient_id: str, date_of_service: date | None = None) -> dict:
    """Check that a 270 can be built and is likely to be accepted."""
    dos = date_of_service or date.today()
    errors: list[str] = []
    warnings: list[str] = []
    settings = get_settings()

    patient = db.get_patient(patient_id)
    if patient is None:
        return {"valid": False, "errors": [f"Patient {patient_id} not found."], "warnings": []}
    coverage = db.get_primary_coverage(patient_id)
    if coverage is None:
        errors.append("No insurance coverage on file.")
    else:
        if not re.fullmatch(r"[A-Za-z0-9]{2,80}", coverage.member_id or ""):
            errors.append(f"Member ID '{coverage.member_id}' is missing or has invalid characters.")
        if coverage.termination_date and coverage.termination_date < dos:
            warnings.append(f"Registration shows coverage ended {coverage.termination_date}.")
        if coverage.plan_type == "HMO" and not coverage.pcp_npi:
            warnings.append("HMO plan with no PCP recorded.")
    if not patient.first_name or not patient.last_name:
        errors.append("Patient first and last name are required.")
    if patient.dob >= date.today():
        errors.append("Date of birth must be in the past.")
    if patient.gender not in ("M", "F", "U"):
        errors.append("Gender must be M, F or U.")
    if dos > date.today() + timedelta(days=MAX_FUTURE_DAYS):
        errors.append(f"Date of service is more than {MAX_FUTURE_DAYS} days in the future.")
    if dos < date.today() - timedelta(days=MAX_PAST_DAYS):
        errors.append(f"Date of service is more than {MAX_PAST_DAYS} days in the past.")
    if not is_valid_npi(settings.provider_npi):
        errors.append(f"Provider NPI {settings.provider_npi} fails the NPI check digit.")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def build_request(patient_id: str, date_of_service: date | None = None,
                  service_type_code: str = "30") -> EligibilityRequest:
    patient, coverage = resolve(patient_id)
    settings = get_settings()
    return EligibilityRequest(
        patient=patient,
        coverage=coverage,
        provider_name=settings.provider_name,
        provider_npi=settings.provider_npi,
        date_of_service=date_of_service or date.today(),
        service_type_code=service_type_code,
        trace_number=uuid.uuid4().hex[:15].upper(),
    )


def create_270(patient_id: str, date_of_service: date | None = None, service_type_code: str = "30") -> str:
    return generate_270(build_request(patient_id, date_of_service, service_type_code))


def check_eligibility(patient_id: str, date_of_service: date | None = None,
                      rendering_npi: str | None = None, service_type_code: str = "30",
                      save: bool = True) -> dict:
    """Run the full eligibility workflow for one patient."""
    request = build_request(patient_id, date_of_service, service_type_code)
    x12_270 = generate_270(request)
    x12_271 = submit_270(x12_270)
    response = parse_271(x12_271)
    findings = rules.evaluate(request.patient, request.coverage, response, request.date_of_service,
                              rendering_npi)
    status = rules.overall_status(response, findings)
    analysis = denials.analyze(findings)

    transaction_id = None
    if save:
        transaction_id = db.save_transaction(
            trace_number=request.trace_number,
            patient_id=patient_id,
            payer_id=request.coverage.payer_id,
            date_of_service=request.date_of_service,
            request_270=x12_270,
            response_271=x12_271,
            status=status.value,
            denial_category=analysis.category,
        )

    return {
        "transaction_id": transaction_id,
        "trace_number": request.trace_number,
        "status": status.value,
        "date_of_service": request.date_of_service.isoformat(),
        "patient": request.patient.model_dump(mode="json"),
        "coverage_on_file": request.coverage.model_dump(mode="json"),
        "payer_response": {
            "member_id": response.member_id,
            "plan_begin": response.plan_begin.isoformat() if response.plan_begin else None,
            "plan_end": response.plan_end.isoformat() if response.plan_end else None,
            "rejections": [r.model_dump() for r in response.rejections],
        },
        "benefits": rules.summarize_benefits(response),
        "findings": [f.model_dump(mode="json") for f in findings],
        "denial_analysis": analysis.model_dump(),
        "x12_270": x12_270,
        "x12_271": x12_271,
    }
