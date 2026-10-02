"""Domain models for the eligibility workflow."""

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class Patient(BaseModel):
    patient_id: str
    first_name: str
    last_name: str
    dob: date
    gender: str
    address: str
    city: str
    state: str
    zip: str


class Payer(BaseModel):
    payer_id: str
    name: str
    edi_payer_id: str
    online: bool = True


class Coverage(BaseModel):
    """Insurance coverage as recorded by the provider at registration."""

    coverage_id: str
    patient_id: str
    payer_id: str
    payer_name: str
    member_id: str
    group_number: str | None = None
    plan_name: str
    plan_type: str
    priority: str = "primary"
    pcp_npi: str | None = None
    pcp_name: str | None = None
    effective_date: date
    termination_date: date | None = None


class EligibilityRequest(BaseModel):
    patient: Patient
    coverage: Coverage
    provider_name: str
    provider_npi: str
    date_of_service: date
    service_type_code: str = "30"
    trace_number: str


class Benefit(BaseModel):
    """One EB (eligibility/benefit) segment from a 271."""

    code: str
    description: str
    coverage_level: str | None = None
    service_type_codes: list[str] = Field(default_factory=list)
    insurance_type: str | None = None
    plan_description: str | None = None
    time_period: str | None = None
    amount: float | None = None
    percent: float | None = None
    in_network: bool | None = None
    authorization_required: bool | None = None
    messages: list[str] = Field(default_factory=list)
    related_entity: dict | None = None


class RejectReason(BaseModel):
    """An AAA segment: the payer could not process the request."""

    loop: str
    valid_request: bool
    code: str
    description: str
    follow_up_action: str
    follow_up_description: str


class EligibilityResponse(BaseModel):
    """Parsed 271 eligibility response."""

    trace_number: str | None = None
    payer_name: str | None = None
    payer_id: str | None = None
    subscriber_first_name: str | None = None
    subscriber_last_name: str | None = None
    member_id: str | None = None
    group_number: str | None = None
    dob: date | None = None
    gender: str | None = None
    plan_begin: date | None = None
    plan_end: date | None = None
    rejections: list[RejectReason] = Field(default_factory=list)
    benefits: list[Benefit] = Field(default_factory=list)

    @property
    def coverage_active(self) -> bool:
        return any(b.code == "1" for b in self.benefits)


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class RuleFinding(BaseModel):
    rule: str
    severity: Severity
    message: str
    details: dict = Field(default_factory=dict)


class EligibilityStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    ELIGIBLE_WITH_ISSUES = "ELIGIBLE_WITH_ISSUES"
    INELIGIBLE = "INELIGIBLE"
    REJECTED = "REJECTED"
    PAYER_UNAVAILABLE = "PAYER_UNAVAILABLE"


class RcmAction(BaseModel):
    priority: int
    owner: str
    action: str


class DenialAnalysis(BaseModel):
    denial_risk: str
    category: str | None = None
    carc_codes: list[str] = Field(default_factory=list)
    root_cause: str
    actions: list[RcmAction] = Field(default_factory=list)
