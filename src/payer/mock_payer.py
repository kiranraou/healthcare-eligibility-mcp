"""Mock payer: answers a 270 with a 271 using the payer's own enrollment records.

It behaves like a real payer: it only sees what is in the 270 and matches it
against its enrollment file, so registration errors on the provider side
surface as AAA rejections exactly as they would in production.
"""

from datetime import date

from src import db
from src.x12.x270 import read_270
from src.x12.x271 import X271Builder

_INSURANCE_TYPE = {"PPO": "PR", "HMO": "HM", "POS": "PS", "EPO": "EP", "HDHP": "C1"}


def submit_270(x12_270: str) -> str:
    """Process a 270 eligibility inquiry and return the 271 response."""
    req = read_270(x12_270)
    payer = db.get_payer(req.get("payer_id") or "")
    builder = X271Builder(
        payer_name=req.get("payer_name") or "UNKNOWN PAYER",
        payer_id=req.get("payer_id") or "",
        provider_name=req.get("provider_name") or "",
        provider_npi=req.get("provider_npi") or "",
        trace_number=req.get("trace_number"),
    )

    if payer is None:
        builder.reject_payer("41", "N")  # not a payer we route to
        return builder.build()
    if not payer.online:
        builder.reject_payer("42", "R")  # payer system down, retry later
        return builder.build()

    subscriber = dict(last_name=req.get("last_name") or "", first_name=req.get("first_name") or "",
                      member_id=req.get("member_id"))

    enrollment = db.find_enrollment(payer.payer_id, req.get("member_id") or "")
    if enrollment is None:
        dob: date | None = req.get("dob")
        by_demo = dob and db.find_enrollment_by_demographics(
            payer.payer_id, req.get("last_name") or "", dob.isoformat()
        )
        # Member exists under another ID -> invalid ID; otherwise not found at all.
        builder.set_subscriber(**subscriber, rejection=("72", "C") if by_demo else ("75", "C"))
        return builder.build()

    if req.get("dob") and req["dob"].isoformat() != enrollment["dob"]:
        builder.set_subscriber(**subscriber, rejection=("71", "C"))
        return builder.build()
    if (req.get("last_name") or "").upper() != enrollment["last_name"].upper():
        builder.set_subscriber(**subscriber, rejection=("73", "C"))
        return builder.build()

    effective = date.fromisoformat(enrollment["effective_date"])
    terminated = (
        date.fromisoformat(enrollment["termination_date"]) if enrollment["termination_date"] else None
    )
    builder.set_subscriber(
        last_name=enrollment["last_name"],
        first_name=enrollment["first_name"],
        member_id=enrollment["member_id"],
        group_number=enrollment["group_number"],
        dob=date.fromisoformat(enrollment["dob"]),
        gender=enrollment["gender"],
        plan_begin=effective,
        plan_end=terminated,
    )

    dos: date = req["date_of_service"]
    insurance_type = _INSURANCE_TYPE.get(enrollment["plan_type"])
    if dos < effective or (terminated and dos > terminated):
        builder.add_benefit("6", insurance_type=insurance_type, plan_description=enrollment["plan_name"])
        return builder.build()

    builder.add_benefit("1", insurance_type=insurance_type, plan_description=enrollment["plan_name"])
    if enrollment["copay"] is not None:
        builder.add_benefit("B", service_type="98", time_period="27", amount=enrollment["copay"],
                            in_network=True)
    if enrollment["deductible"]:
        builder.add_benefit("C", time_period="23", amount=enrollment["deductible"], in_network=True)
        builder.add_benefit("C", time_period="29", amount=enrollment["deductible_remaining"],
                            in_network=True)
    if enrollment["coinsurance_pct"]:
        builder.add_benefit("A", percent=enrollment["coinsurance_pct"] / 100, in_network=True)
    if enrollment["oop_max"]:
        builder.add_benefit("G", time_period="23", amount=enrollment["oop_max"], in_network=True)
        builder.add_benefit("G", time_period="29", amount=enrollment["oop_remaining"], in_network=True)
    if enrollment["pcp_required"]:
        first, _, last = enrollment["pcp_name"].removeprefix("DR. ").rpartition(" ")
        builder.add_benefit(
            "L",
            messages=["PCP SELECTION REQUIRED"],
            related_entity=("P3", last, first or None, enrollment["pcp_npi"]),
        )
    if enrollment["referral_required"]:
        builder.add_benefit("1", service_type="98", authorization_required=True,
                            messages=["REFERRAL REQUIRED FOR SPECIALIST VISITS"])
    if enrollment["other_payer_name"]:
        order = "PRIMARY" if enrollment["other_payer_primary"] else "SECONDARY"
        builder.add_benefit(
            "R",
            messages=[f"{enrollment['other_payer_name']} IS {order}",
                      f"OTHER PAYER MEMBER ID {enrollment['other_payer_member_id']}"],
            related_entity=("PRP" if enrollment["other_payer_primary"] else "SEP",
                            enrollment["other_payer_name"], None, None),
        )
    return builder.build()
