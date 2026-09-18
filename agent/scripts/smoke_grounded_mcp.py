"""Opt-in local MCP-to-Ollama acceptance using synthetic evidence only."""

import argparse
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(model, unsupported=False):
    params = StdioServerParameters(command=sys.executable, args=["-m", "lycosa_agent.mcp_server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            models = await session.call_tool("local_models", {})
            if models.isError:
                print("Ollama unavailable; no cloud fallback")
                return False
            result = await session.call_tool(
                "answer_from_context",
                {
                    "request": {
                        "model": model,
                        "question": (
                            "What is the launch date?"
                            if unsupported
                            else "What is the test code? Answer briefly and cite [S1]."
                        ),
                        "evidence": [
                            {"source": "synthetic-test.txt", "text": "The test code is LYCOSA-731."}
                        ],
                        "max_tokens": 128,
                    }
                },
            )
            if result.isError:
                print("MCP grounded answer failed; inspect local runtime readiness/deadline")
                return False
            payload = json.loads(result.content[0].text)
            print(json.dumps({"transport": "stdio", "model": model, **payload}, indent=2))
            if unsupported:
                from lycosa_agent.knowledge import REFUSAL

                passed = payload["output"].strip() == REFUSAL
                if not passed:
                    print("Unsupported-question refusal acceptance failed")
                return passed
            if "LYCOSA-731" not in payload["output"]:
                print("Transport succeeded but answer-quality check failed")
                return False
            if payload["citation_status"] != "ids_valid":
                print("Answer contains code but citation acceptance failed")
                return False
            return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Already installed local Ollama model")
    parser.add_argument(
        "--unsupported", action="store_true", help="Verify refusal for an absent fact"
    )
    args = parser.parse_args()
    if not asyncio.run(run(args.model, args.unsupported)):
        raise SystemExit(1)
