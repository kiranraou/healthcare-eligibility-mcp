import json

import pytest
from mcp import Client

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {
    "hello_eligibility", "find_patient", "list_patients", "get_patient_coverage",
    "validate_eligibility_request", "generate_270", "submit_eligibility", "parse_271",
    "check_patient_eligibility", "check_benefits", "detect_cob", "validate_pcp", "identify_denial",
    "recommend_rcm_action", "run_medallion_pipeline", "get_eligibility_analytics",
}


@pytest.fixture
def mcp_server():
    import server  # imported here so it uses the test database

    return server.mcp


async def test_lists_tools_resources_prompts(mcp_server):
    async with Client(mcp_server) as client:
        assert {t.name for t in (await client.list_tools()).tools} == EXPECTED_TOOLS
        resources = {str(r.uri) for r in (await client.list_resources()).resources}
        assert {"eligibility://status", "eligibility://payers"} <= resources
        templates = [t.uri_template for t in (await client.list_resource_templates()).resource_templates]
        assert templates == ["eligibility://patients/{patient_id}"]
        prompts = {p.name for p in (await client.list_prompts()).prompts}
        assert prompts == {"eligibility_analysis", "denial_root_cause", "daily_rcm_worklist"}


async def test_hello(mcp_server):
    async with Client(mcp_server) as client:
        result = await client.call_tool("hello_eligibility", {"name": "Kiran"})
        assert result.content[0].text == "Healthcare Eligibility MCP is working, Kiran!"


async def test_check_patient_eligibility(mcp_server):
    async with Client(mcp_server) as client:
        result = await client.call_tool("check_patient_eligibility", {"patient_id": "P001"})
        data = result.structured_content
        assert data["status"] == "REJECTED"
        assert data["denial_analysis"]["category"] == "INVALID_MEMBER_ID"
        assert "x12_271" not in data


async def test_x12_tools_chain(mcp_server):
    async with Client(mcp_server) as client:
        x270 = (await client.call_tool("generate_270", {"patient_id": "P002"})).content[0].text
        x271 = (await client.call_tool("submit_eligibility", {"x12_270": x270})).content[0].text
        parsed = (await client.call_tool("parse_271", {"x12_271": x271})).structured_content
        assert parsed["coverage_active"] is True
        assert parsed["benefits_summary"]["plan"] == "BLUE CHOICE PPO"


async def test_focused_tools(mcp_server):
    async with Client(mcp_server) as client:
        cob = (await client.call_tool("detect_cob", {"patient_id": "P004"})).structured_content
        assert cob["has_cob_issue"] is True
        pcp = (await client.call_tool("validate_pcp", {"patient_id": "P005"})).structured_content
        assert pcp["pcp_ok"] is False
        actions = (await client.call_tool("recommend_rcm_action", {"patient_id": "P003"})).structured_content
        assert actions["actions"][0]["owner"] == "Front Desk"


async def test_unknown_patient_error_reaches_model(mcp_server):
    async with Client(mcp_server) as client:
        result = await client.call_tool("check_patient_eligibility", {"patient_id": "P999"})
        assert result.is_error
        assert "Patient P999 not found" in result.content[0].text


async def test_pipeline_and_analytics(mcp_server):
    async with Client(mcp_server) as client:
        await client.call_tool("check_patient_eligibility", {"patient_id": "P002"})
        run = (await client.call_tool("run_medallion_pipeline", {"backend": "sqlite"})).structured_content
        assert run["silver_rows_added"] == 1
        gold = (await client.call_tool("get_eligibility_analytics", {"backend": "sqlite"})).structured_content
        assert gold["gold_payer_performance"][0]["payer_id"] == "BCBS"


async def test_resources(mcp_server):
    async with Client(mcp_server) as client:
        status = (await client.read_resource("eligibility://status")).contents[0].text
        assert "Payers offline: HUMANA" in status
        record = json.loads((await client.read_resource("eligibility://patients/P004")).contents[0].text)
        assert record["coverages"][0]["payer_id"] == "CIGNA"


async def test_prompt(mcp_server):
    async with Client(mcp_server) as client:
        prompt = await client.get_prompt("denial_root_cause", {"patient_name": "John Smith"})
        assert "find_patient" in prompt.messages[0].content.text
