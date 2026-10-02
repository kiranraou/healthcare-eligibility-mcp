"""Smoke-test a running Healthcare Eligibility MCP server over streamable HTTP.

    python scripts/smoke_test.py [http://localhost:8000/mcp]
"""

import asyncio
import sys

from mcp import Client


async def main(url: str) -> None:
    async with Client(url) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert "check_patient_eligibility" in tools, tools

        hello = await client.call_tool("hello_eligibility", {"name": "CI"})
        assert hello.content[0].text == "Healthcare Eligibility MCP is working, CI!"

        result = await client.call_tool("check_patient_eligibility", {"patient_id": "P001"})
        assert result.structured_content["status"] == "REJECTED", result.structured_content

    print(f"OK: {len(tools)} tools, eligibility check returned REJECTED for P001 as expected.")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000/mcp"), 60))
