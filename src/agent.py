"""AI eligibility agent: Claude using this project's MCP server as its toolbox.

The agent starts server.py as an MCP server over stdio, exposes every MCP tool
to Claude, and lets Claude decide which tools to call to answer the question.

    python -m src.agent "Check John Smith's eligibility and tell me why he was rejected and what the RCM team should do."
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field

import anthropic
from anthropic.lib.tools.mcp import async_mcp_tool
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.config import PROJECT_ROOT, get_settings
from src.llm import FALLBACK_BETA, LLMRefusalError, make_async_client

AGENT_SYSTEM = """You are an eligibility and revenue cycle management (RCM) agent for a medical group.
You have MCP tools for patient lookup, X12 270/271 eligibility checks, benefits, coordination of
benefits, PCP/referral validation, denial prediction and Gold-layer analytics.

Gather facts with the tools before answering; never invent patient, payer or benefit data. When a
user names a patient, look them up first. Explain results for front-desk and billing staff: the
eligibility status, the root cause (cite the payer's AAA reject code or the rule that fired), the
likely denial category with CARC codes, and the recommended RCM actions with their owners.
Keep the final answer concise, with short headed sections."""

MAX_TOOL_ROUNDS = 15


@dataclass
class AgentResult:
    answer: str
    tool_calls: list[str] = field(default_factory=list)


def server_parameters() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=[str(PROJECT_ROOT / "server.py")],
        cwd=str(PROJECT_ROOT),
        env=dict(os.environ),
    )


async def run_with_session(question: str, session: ClientSession,
                           client: anthropic.AsyncAnthropic | None = None) -> AgentResult:
    """Answer a question with Claude, using the tools of an initialized MCP session."""
    client = client or make_async_client()
    mcp_tools = (await session.list_tools()).tools
    runner = client.beta.messages.tool_runner(
        model=get_settings().anthropic_model,
        max_tokens=16000,
        max_iterations=MAX_TOOL_ROUNDS,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": "medium"},
        system=AGENT_SYSTEM,
        tools=[async_mcp_tool(t, session) for t in mcp_tools],
        messages=[{"role": "user", "content": question}],
    )

    tool_calls: list[str] = []
    final = None
    async for message in runner:
        final = message
        tool_calls += [b.name for b in message.content if b.type == "tool_use"]

    if final is None:
        raise RuntimeError("Claude returned no response.")
    if final.stop_reason == "refusal":
        raise LLMRefusalError("Claude declined this request.")
    answer = "\n".join(b.text for b in final.content if b.type == "text").strip()
    if final.stop_reason == "tool_use":
        answer += f"\n\n[Stopped after {MAX_TOOL_ROUNDS} tool rounds.]"
    return AgentResult(answer=answer, tool_calls=tool_calls)


async def ask(question: str, client: anthropic.AsyncAnthropic | None = None) -> AgentResult:
    """Start the MCP server, answer the question end to end, and shut the server down."""
    # Errors are re-raised outside the MCP task groups so callers get the original
    # exception instead of an ExceptionGroup.
    error: Exception | None = None
    async with stdio_client(server_parameters()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            try:
                return await run_with_session(question, session, client)
            except (anthropic.APIError, LLMRefusalError) as exc:
                error = exc
    raise error


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the eligibility agent a question.")
    parser.add_argument("question", nargs="+")
    parser.add_argument("--show-tools", action="store_true", help="Print the MCP tools Claude called.")
    args = parser.parse_args()

    try:
        result = asyncio.run(ask(" ".join(args.question)))
    except anthropic.AuthenticationError:
        sys.exit("Anthropic authentication failed. Set ANTHROPIC_API_KEY in .env.")
    except anthropic.APIConnectionError:
        sys.exit("Could not reach the Anthropic API. Check your network connection.")
    except anthropic.APIStatusError as exc:
        sys.exit(f"Anthropic API error ({exc.status_code}): {exc.message}")
    except LLMRefusalError as exc:
        sys.exit(str(exc))

    if args.show_tools:
        print("Tools called:", ", ".join(result.tool_calls) or "none", "\n")
    print(result.answer)


if __name__ == "__main__":
    main()
