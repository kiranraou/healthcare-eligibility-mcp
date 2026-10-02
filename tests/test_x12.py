from datetime import date, datetime

import pytest

from src import service
from src.payer.mock_payer import submit_270
from src.x12.common import split_segments
from src.x12.x270 import generate_270, read_270
from src.x12.x271 import X271Builder, parse_271

NOW = datetime(2026, 10, 2, 9, 30)


def _request(patient_id="P002"):
    return service.build_request(patient_id, date(2026, 10, 2))


def test_270_envelope_is_well_formed():
    x12 = generate_270(_request(), now=NOW, control_number=42)
    segments = split_segments(x12)
    tags = [s[0] for s in segments]

    assert tags[:3] == ["ISA", "GS", "ST"] and tags[-3:] == ["SE", "GE", "IEA"]
    assert len(x12.splitlines()[0]) == 106  # ISA is fixed width
    st, se = segments[2], segments[-3]
    assert st[1] == "270" and st[3] == "005010X279A1"
    assert int(se[1]) == tags.index("SE") - tags.index("ST") + 1  # SE01 counts ST..SE
    assert segments[0][13] == "000000042" == segments[-1][2]


def test_270_contains_subscriber_and_inquiry():
    x12 = generate_270(_request(), now=NOW)
    assert "NM1*IL*1*GARCIA*MARIA****MI*XYZ987654321~" in x12
    assert "DMG*D8*19750930*F~" in x12
    assert "DTP*291*D8*20261002~" in x12
    assert "EQ*30~" in x12


def test_read_270_round_trip():
    request = _request()
    fields = read_270(generate_270(request, now=NOW))
    assert fields["member_id"] == "XYZ987654321"
    assert fields["dob"] == date(1975, 9, 30)
    assert fields["date_of_service"] == date(2026, 10, 2)
    assert fields["trace_number"] == request.trace_number
    assert fields["payer_id"] == "BCBS"


def test_read_270_rejects_other_transactions():
    builder = X271Builder(payer_name="X", payer_id="X", provider_name="P", provider_npi="1", trace_number="T")
    with pytest.raises(ValueError, match="Not a 270"):
        read_270(builder.build(now=NOW))


def test_parse_271_active_coverage_with_benefits():
    response = parse_271(submit_270(generate_270(_request(), now=NOW)))
    assert response.coverage_active
    assert response.member_id == "XYZ987654321"
    assert response.plan_begin == date(2024, 1, 1)
    codes = [b.code for b in response.benefits]
    assert codes[0] == "1" and {"B", "C", "A", "G"} <= set(codes)
    copay = next(b for b in response.benefits if b.code == "B")
    assert copay.amount == 30.0 and copay.service_type_codes == ["98"] and copay.in_network is True


def test_parse_271_rejection():
    response = parse_271(submit_270(generate_270(_request("P001"), now=NOW)))
    assert not response.coverage_active
    [reject] = response.rejections
    assert (reject.code, reject.follow_up_action, reject.loop) == ("72", "C", "2100C")
    assert reject.description == "Invalid/Missing Subscriber/Insured ID"


def test_parse_271_related_entities():
    response = parse_271(submit_270(generate_270(_request("P005"), now=NOW)))
    pcp = next(b for b in response.benefits if b.code == "L")
    assert pcp.related_entity == {"entity_code": "P3", "name": "KAREN PATEL", "npi": "1801234567"}

    response = parse_271(submit_270(generate_270(_request("P004"), now=NOW)))
    cob = next(b for b in response.benefits if b.code == "R")
    assert cob.related_entity["name"] == "MEDICARE PART B"
    assert "MEDICARE PART B IS PRIMARY" in cob.messages


def test_parse_271_rejects_other_transactions():
    with pytest.raises(ValueError, match="Not a 271"):
        parse_271(generate_270(_request(), now=NOW))
