"""Build and parse X12 271 eligibility responses."""

from datetime import date, datetime

from src.models import Benefit, EligibilityResponse, RejectReason
from src.x12.common import (
    EB_CODES,
    FOLLOW_UP_ACTIONS,
    REJECT_REASONS,
    d8,
    element,
    envelope,
    parse_d8,
    segment,
    split_segments,
)


class X271Builder:
    """Assembles a 271 body loop by loop. Used by the mock payer."""

    def __init__(self, *, payer_name: str, payer_id: str, provider_name: str, provider_npi: str,
                 trace_number: str | None):
        self.payer_name = payer_name
        self.payer_id = payer_id
        self.provider_name = provider_name
        self.provider_npi = provider_npi
        self.trace_number = trace_number
        self.payer_rejection: tuple[str, str] | None = None
        self.subscriber: list[str] = []
        self.benefits: list[str] = []

    def reject_payer(self, reason: str, follow_up: str) -> None:
        self.payer_rejection = (reason, follow_up)

    def set_subscriber(self, *, last_name: str, first_name: str, member_id: str | None,
                       group_number: str | None = None, dob: date | None = None,
                       gender: str | None = None, plan_begin: date | None = None,
                       plan_end: date | None = None, rejection: tuple[str, str] | None = None) -> None:
        segs = [segment("NM1", "IL", "1", last_name, first_name, "", "", "", "MI" if member_id else "",
                        member_id)]
        if group_number:
            segs.append(segment("REF", "6P", group_number))
        if rejection:
            segs.append(segment("AAA", "Y", "", rejection[0], rejection[1]))
        if dob:
            segs.append(segment("DMG", "D8", d8(dob), gender))
        if plan_begin:
            segs.append(segment("DTP", "346", "D8", d8(plan_begin)))
        if plan_end:
            segs.append(segment("DTP", "347", "D8", d8(plan_end)))
        self.subscriber = segs

    def add_benefit(self, code: str, *, service_type: str = "30", insurance_type: str | None = None,
                    plan_description: str | None = None, time_period: str | None = None,
                    amount: float | None = None, percent: float | None = None,
                    authorization_required: bool | None = None, in_network: bool | None = None,
                    messages: list[str] | None = None,
                    related_entity: tuple[str, str, str | None, str | None] | None = None) -> None:
        """related_entity = (entity code, last/org name, first name, NPI)."""
        flag = {True: "Y", False: "N", None: ""}
        self.benefits.append(segment(
            "EB", code, "IND", service_type, insurance_type, plan_description, time_period,
            "" if amount is None else f"{amount:.2f}",
            "" if percent is None else f"{percent:g}",
            "", "", flag[authorization_required], flag[in_network],
        ))
        for msg in messages or []:
            self.benefits.append(segment("MSG", msg))
        if related_entity:
            entity_code, last, first, npi = related_entity
            self.benefits += [
                segment("LS", "2120"),
                segment("NM1", entity_code, "1" if first else "2", last, first, "", "", "",
                        "XX" if npi else "", npi),
                segment("LE", "2120"),
            ]

    def build(self, *, now: datetime | None = None, control_number: int = 1) -> str:
        now = now or datetime.now()
        body = [
            segment("BHT", "0022", "11", self.trace_number, d8(now.date()), now.strftime("%H%M")),
            segment("HL", "1", "", "20", "1"),
            segment("NM1", "PR", "2", self.payer_name, "", "", "", "", "PI", self.payer_id),
        ]
        if self.payer_rejection:
            body.append(segment("AAA", "Y", "", *self.payer_rejection))
        else:
            body += [
                segment("HL", "2", "1", "21", "1"),
                segment("NM1", "1P", "2", self.provider_name, "", "", "", "", "XX", self.provider_npi),
                segment("HL", "3", "2", "22", "0"),
            ]
            if self.trace_number:
                body.append(segment("TRN", "2", self.trace_number, "9" + self.provider_npi))
            body += self.subscriber + self.benefits
        return envelope(body, transaction_set="271", functional_id="HB", sender_id=self.payer_id,
                        receiver_id=self.provider_npi, control_number=control_number, now=now)


_LOOP_BY_HL_LEVEL = {"20": "2100A", "21": "2100B", "22": "2100C", "23": "2100D"}


def parse_271(x12: str) -> EligibilityResponse:
    """Parse a 271 into a structured EligibilityResponse."""
    response = EligibilityResponse()
    loop = None
    current: Benefit | None = None
    in_2120 = False

    for seg in split_segments(x12):
        tag = seg[0]
        if tag == "ST" and element(seg, 1) != "271":
            raise ValueError(f"Not a 271 transaction (ST01={element(seg, 1)})")
        if tag == "HL":
            loop = _LOOP_BY_HL_LEVEL.get(element(seg, 3) or "", loop)
            current = None
        elif tag == "TRN":
            response.trace_number = element(seg, 2)
        elif tag == "NM1" and in_2120 and current is not None:
            current.related_entity = {
                "entity_code": element(seg, 1),
                "name": " ".join(p for p in (element(seg, 4), element(seg, 3)) if p),
                "npi": element(seg, 9),
            }
        elif tag == "NM1" and element(seg, 1) == "PR":
            response.payer_name = element(seg, 3)
            response.payer_id = element(seg, 9)
        elif tag == "NM1" and element(seg, 1) == "IL":
            response.subscriber_last_name = element(seg, 3)
            response.subscriber_first_name = element(seg, 4)
            response.member_id = element(seg, 9)
        elif tag == "REF" and element(seg, 1) == "6P" and current is None:
            response.group_number = element(seg, 2)
        elif tag == "DMG":
            if dob := element(seg, 2):
                response.dob = parse_d8(dob)
            response.gender = element(seg, 3)
        elif tag == "DTP" and current is None:
            qualifier, value = element(seg, 1), element(seg, 3)
            if value and qualifier in ("346", "291", "356"):
                response.plan_begin = parse_d8(value[:8])
            elif value and qualifier in ("347", "357"):
                response.plan_end = parse_d8(value[:8])
        elif tag == "AAA":
            code, follow_up = element(seg, 3) or "", element(seg, 4) or ""
            response.rejections.append(RejectReason(
                loop="2110C" if current is not None else (loop or "unknown"),
                valid_request=element(seg, 1) == "Y",
                code=code,
                description=REJECT_REASONS.get(code, "Unknown reject reason"),
                follow_up_action=follow_up,
                follow_up_description=FOLLOW_UP_ACTIONS.get(follow_up, "Unknown"),
            ))
        elif tag == "EB":
            amount, percent = element(seg, 7), element(seg, 8)
            flags = {"Y": True, "N": False}
            current = Benefit(
                code=element(seg, 1) or "",
                description=EB_CODES.get(element(seg, 1) or "", "Unknown"),
                coverage_level=element(seg, 2),
                service_type_codes=(element(seg, 3) or "").split("^") if element(seg, 3) else [],
                insurance_type=element(seg, 4),
                plan_description=element(seg, 5),
                time_period=element(seg, 6),
                amount=float(amount) if amount else None,
                percent=float(percent) if percent else None,
                authorization_required=flags.get(element(seg, 11) or ""),
                in_network=flags.get(element(seg, 12) or ""),
            )
            response.benefits.append(current)
        elif tag == "MSG" and current is not None:
            current.messages.append(element(seg, 1) or "")
        elif tag == "LS":
            in_2120 = True
        elif tag == "LE":
            in_2120 = False

    return response
