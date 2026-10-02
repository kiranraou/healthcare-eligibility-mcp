"""LLM and agent tests. Claude's replies are scripted with a mock HTTP transport;
everything else (tool runner, MCP server, eligibility workflow) runs for real."""

import json
from datetime import date

import anthropic
import httpx2
import pytest
from mcp import Client

from src import agent, llm, service


def _message(content, stop_reason):
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


def _tool_use(tool_id, name, tool_input):
    return _message([{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}], "tool_use")


def _text(text, stop_reason="end_turn"):
    return _message([{"type": "text", "text": text}], stop_reason)


class ScriptedClaude:
    """Returns the scripted responses in order and records every request body."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        return httpx2.Response(200, json=self.responses.pop(0))

    def sync_client(self):
        return anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(self)))

    def async_client(self):
        return anthropic.AsyncAnthropic(
            api_key="test", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self))
        )


def test_explain_eligibility_sends_result_without_x12():
    claude = ScriptedClaude([_text("John Smith was rejected: invalid member ID (AAA 72).")])
    result = service.check_eligibility("P001", date(2026, 10, 2), save=False)

    answer = llm.explain_eligibility(result, client=claude.sync_client())

    assert answer == "John Smith was rejected: invalid member ID (AAA 72)."
    [body] = claude.requests
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    user_content = body["messages"][0]["content"]
    assert "INVALID_MEMBER_ID" in user_content and "ISA*" not in user_content


def test_explain_eligibility_refusal():
    claude = ScriptedClaude([_text("", stop_reason="refusal")])
    with pytest.raises(llm.LLMRefusalError):
        llm.explain_eligibility({"status": "REJECTED"}, client=claude.sync_client())


@pytest.mark.anyio
async def test_agent_answers_with_mcp_tools():
    import server

    claude = ScriptedClaude([
        _tool_use("toolu_1", "find_patient", {"name": "John Smith"}),
        _tool_use("toolu_2", "check_patient_eligibility", {"patient_id": "P001"}),
        _text("John Smith was rejected for an invalid member ID. Front Desk: rescan the card."),
    ])

    async with Client(server.mcp) as mcp_client:
        result = await agent.run_with_session(
            "Check John Smith's eligibility and tell me why he was rejected.",
            mcp_client.session,
            client=claude.async_client(),
        )

    assert result.tool_calls == ["find_patient", "check_patient_eligibility"]
    assert "invalid member ID" in result.answer

    first, second, third = claude.requests
    assert len(first["tools"]) == 16
    assert first["system"] == agent.AGENT_SYSTEM
    # Claude's tool calls were executed against the MCP server and the results sent back.
    find_result = second["messages"][-1]["content"][0]
    assert find_result["tool_use_id"] == "toolu_1" and "P001" in json.dumps(find_result["content"])
    check_result = third["messages"][-1]["content"][0]
    assert "INVALID_MEMBER_ID" in json.dumps(check_result["content"])
