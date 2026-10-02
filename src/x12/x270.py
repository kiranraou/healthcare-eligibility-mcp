"""Build X12 270 eligibility inquiries."""

from datetime import date, datetime

from src.models import EligibilityRequest
from src.x12.common import d8, envelope, parse_d8, segment, split_segments, element


def generate_270(request: EligibilityRequest, *, now: datetime | None = None, control_number: int = 1) -> str:
    now = now or datetime.now()
    patient, coverage = request.patient, request.coverage
    body = [
        segment("BHT", "0022", "13", request.trace_number, d8(now.date()), now.strftime("%H%M")),
        # 2000A / 2100A - Information source (payer)
        segment("HL", "1", "", "20", "1"),
        segment("NM1", "PR", "2", coverage.payer_name, "", "", "", "", "PI", coverage.payer_id),
        # 2000B / 2100B - Information receiver (provider)
        segment("HL", "2", "1", "21", "1"),
        segment("NM1", "1P", "2", request.provider_name, "", "", "", "", "XX", request.provider_npi),
        # 2000C / 2100C - Subscriber
        segment("HL", "3", "2", "22", "0"),
        segment("TRN", "1", request.trace_number, "9" + request.provider_npi),
        segment(
            "NM1", "IL", "1", patient.last_name.upper(), patient.first_name.upper(), "", "", "",
            "MI", coverage.member_id,
        ),
    ]
    if coverage.group_number:
        body.append(segment("REF", "6P", coverage.group_number))
    body += [
        segment("DMG", "D8", d8(patient.dob), patient.gender),
        segment("DTP", "291", "D8", d8(request.date_of_service)),
        segment("EQ", request.service_type_code),
    ]
    return envelope(
        body,
        transaction_set="270",
        functional_id="HS",
        sender_id=request.provider_npi,
        receiver_id=coverage.payer_id,
        control_number=control_number,
        now=now,
    )


def read_270(x12: str) -> dict:
    """Extract the fields a payer needs from a 270 (used by the mock payer)."""
    fields: dict = {"service_type_codes": []}
    for seg in split_segments(x12):
        tag = seg[0]
        if tag == "ST" and element(seg, 1) != "270":
            raise ValueError(f"Not a 270 transaction (ST01={element(seg, 1)})")
        if tag == "NM1" and element(seg, 1) == "PR":
            fields["payer_name"] = element(seg, 3)
            fields["payer_id"] = element(seg, 9)
        elif tag == "NM1" and element(seg, 1) == "1P":
            fields["provider_name"] = element(seg, 3)
            fields["provider_npi"] = element(seg, 9)
        elif tag == "NM1" and element(seg, 1) == "IL":
            fields["last_name"] = element(seg, 3)
            fields["first_name"] = element(seg, 4)
            fields["member_id"] = element(seg, 9)
        elif tag == "TRN":
            fields["trace_number"] = element(seg, 2)
        elif tag == "REF" and element(seg, 1) == "6P":
            fields["group_number"] = element(seg, 2)
        elif tag == "DMG":
            dob = element(seg, 2)
            fields["dob"] = parse_d8(dob) if dob else None
            fields["gender"] = element(seg, 3)
        elif tag == "DTP" and element(seg, 1) == "291":
            fields["date_of_service"] = parse_d8(element(seg, 3))
        elif tag == "EQ":
            fields["service_type_codes"].append(element(seg, 1))
    fields.setdefault("date_of_service", date.today())
    return fields
