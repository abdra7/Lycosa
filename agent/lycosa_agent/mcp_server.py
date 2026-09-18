"""Opt-in stdio MCP companion. No sockets, shell tools or filesystem access."""

import asyncio
from contextlib import asynccontextmanager

from lycosa_agent.knowledge import GroundedRequest, answer, capabilities


def build_server(adapter):
    from mcp.server.fastmcp import FastMCP

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            await adapter.aclose()

    server = FastMCP("Lycosa Agent", lifespan=lifespan)
    slots = asyncio.Semaphore(2)

    @server.tool()
    def agent_capabilities() -> dict:
        """Describe supported capabilities and explicit limitations."""
        return capabilities()

    @server.tool()
    async def local_models() -> list[str]:
        """List installed models; does not download or modify any model."""
        try:
            return await asyncio.wait_for(adapter.list_models(), timeout=5)
        except Exception:
            raise ValueError("Local runtime unavailable") from None

    @server.tool()
    async def answer_from_context(request: GroundedRequest) -> dict:
        """Answer locally from caller-supplied evidence; IDs do not prove factual accuracy."""
        if slots.locked():
            raise ValueError("Grounded-answer capacity busy; retry later")
        try:
            async with slots:
                return (await answer(adapter, request)).model_dump()
        except TimeoutError:
            raise ValueError("Local grounded answer exceeded its deadline") from None
        except Exception:
            raise ValueError("Local grounded answer failed; check model/runtime") from None

    return server


def main():
    from lycosa_agent.config import AgentSettings
    from lycosa_agent.runtimes.ollama import OllamaAdapter

    settings = AgentSettings()
    adapter = OllamaAdapter(settings.ollama_url)
    try:
        server = build_server(adapter)
    except ImportError:
        raise SystemExit("Install the optional agent[mcp] dependency first") from None
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
