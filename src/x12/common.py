"""Shared X12 envelope helpers and code tables."""

from datetime import date, datetime

ELEMENT_SEP = "*"
SEGMENT_TERM = "~"
COMPONENT_SEP = ":"
REPETITION_SEP = "^"
IMPLEMENTATION_GUIDE = "005010X279A1"

# EB01 - Eligibility or Benefit Information
EB_CODES = {
    "1": "Active Coverage",
    "6": "Inactive",
    "A": "Co-Insurance",
    "B": "Co-Payment",
    "C": "Deductible",
    "G": "Out of Pocket (Stop Loss)",
    "L": "Primary Care Provider",
    "R": "Other or Additional Payor",
    "V": "Cannot Process",
}

# EB06 - Time Period Qualifier
TIME_PERIODS = {"23": "Calendar Year", "27": "Visit", "29": "Remaining", "22": "Service Year"}

# EB03 - Service Type Codes (subset)
SERVICE_TYPES = {
    "1": "Medical Care",
    "30": "Health Benefit Plan Coverage",
    "33": "Chiropractic",
    "35": "Dental Care",
    "47": "Hospital",
    "48": "Hospital - Inpatient",
    "50": "Hospital - Outpatient",
    "86": "Emergency Services",
    "88": "Pharmacy",
    "98": "Professional (Physician) Visit - Office",
    "UC": "Urgent Care",
}

# AAA03 - Reject Reason Codes
REJECT_REASONS = {
    "15": "Required Application Data Missing",
    "41": "Authorization/Access Restrictions",
    "42": "Unable to Respond at Current Time",
    "43": "Invalid/Missing Provider Identification",
    "58": "Invalid/Missing Date-of-Birth",
    "65": "Invalid/Missing Patient Name",
    "71": "Patient Birth Date Does Not Match That for the Patient on the Database",
    "72": "Invalid/Missing Subscriber/Insured ID",
    "73": "Invalid/Missing Subscriber/Insured Name",
    "75": "Subscriber/Insured Not Found",
    "76": "Duplicate Subscriber/Insured ID Number",
}

# AAA04 - Follow-up Action Codes
FOLLOW_UP_ACTIONS = {
    "C": "Please Correct and Resubmit",
    "N": "Resubmission Not Allowed",
    "P": "Please Resubmit Original Transaction",
    "R": "Resubmission Allowed",
    "S": "Do Not Resubmit; Inquiry Initiated to a Third Party",
    "Y": "Do Not Resubmit; We Will Hold Your Request and Respond Again Shortly",
}


def segment(*elements: object) -> str:
    """Join elements into one segment, trimming trailing empty elements."""
    values = ["" if e is None else str(e) for e in elements]
    while values and values[-1] == "":
        values.pop()
    return ELEMENT_SEP.join(values) + SEGMENT_TERM


def d8(value: date) -> str:
    return value.strftime("%Y%m%d")


def parse_d8(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def envelope(
    body: list[str],
    *,
    transaction_set: str,
    functional_id: str,
    sender_id: str,
    receiver_id: str,
    control_number: int,
    now: datetime,
) -> str:
    """Wrap ST..SE body segments (without ST/SE) in ISA/GS/ST/SE/GE/IEA."""
    icn = f"{control_number:09d}"
    st_control = "0001"
    st = segment("ST", transaction_set, st_control, IMPLEMENTATION_GUIDE)
    se = segment("SE", len(body) + 2, st_control)
    isa = segment(
        "ISA", "00", " " * 10, "00", " " * 10,
        "ZZ", sender_id.ljust(15)[:15], "ZZ", receiver_id.ljust(15)[:15],
        now.strftime("%y%m%d"), now.strftime("%H%M"), REPETITION_SEP, "00501", icn, "0", "T",
        COMPONENT_SEP,
    )
    gs = segment(
        "GS", functional_id, sender_id, receiver_id, now.strftime("%Y%m%d"), now.strftime("%H%M"),
        control_number, "X", IMPLEMENTATION_GUIDE,
    )
    ge = segment("GE", "1", control_number)
    iea = segment("IEA", "1", icn)
    return "\n".join([isa, gs, st, *body, se, ge, iea])


def split_segments(x12: str) -> list[list[str]]:
    """Split raw X12 text into a list of segments, each a list of elements."""
    raw = x12.replace("\r", "").replace("\n", "")
    return [s.strip().split(ELEMENT_SEP) for s in raw.split(SEGMENT_TERM) if s.strip()]


def element(seg: list[str], index: int) -> str | None:
    """Return element N (1-based, like X12 docs) or None if absent/empty."""
    if index < len(seg) and seg[index] != "":
        return seg[index]
    return None
