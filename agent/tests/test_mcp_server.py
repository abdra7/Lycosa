"""Actual stdio JSON-RPC handshake/tool discovery; no cloud or model download."""

import json
import sys

import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_stdio_handshake_and_bounded_rag():
    params = StdioServerParameters(command=sys.executable, args=["-m", "lycosa_agent.mcp_server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "Lycosa Agent"
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {
                "agent_capabilities",
                "local_models",
                "answer_from_context",
            }
            result = await session.call_tool("agent_capabilities", {})
            assert not result.isError
            assert json.loads(result.content[0].text)["shell_execution"] is False
            result = await session.call_tool(
                "answer_from_context",
                {
                    "request": {
                        "model": "not-needed",
                        "question": "What is the code?",
                        "evidence": [],
                    }
                },
            )
            assert not result.isError
            assert json.loads(result.content[0].text)["citation_status"] == "no_evidence"
            invalid = await session.call_tool(
                "answer_from_context",
                {"request": {"model": "not-needed", "question": "x", "context_chars": 999999}},
            )
            assert invalid.isError
            assert (await session.call_tool("execute_shell", {"command": "anything"})).isError
