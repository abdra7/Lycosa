"""The guarded transport over real sockets (respx bypasses it elsewhere): a
local HTTP server on 127.0.0.1 is reachable only when the policy allows
loopback, and the full adapter path works end to end."""

import asyncio
import json

import pytest

from app.llm import catalog, errors
from app.llm.http import Connection, request_json, stream_lines
from app.llm.netpolicy import PUBLIC_ONLY, local_policy, parse_networks
from app.llm.types import LLMRequest, Message

LOOPBACK = local_policy(parse_networks("127.0.0.0/8,::1/128"))


@pytest.fixture
async def server():
    """Minimal HTTP/1.1 server; responses keyed by request path."""
    routes: dict[str, tuple[int, bytes, str]] = {}
    seen: list[str] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        request_line = head.split(b"\r\n", 1)[0].decode()
        path = request_line.split(" ")[1].split("?")[0]
        seen.append(request_line)
        length = 0
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":")[1])
        if length:
            await reader.readexactly(length)
        status, body, ctype = routes.get(path, (404, b'{"error":"not found"}', "application/json"))
        writer.write(
            f"HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", routes, seen
    srv.close()
    await srv.wait_closed()


async def test_loopback_server_reachable_only_when_policy_allows(server):
    base, routes, _ = server
    routes["/models"] = (200, b'{"data":[{"id":"m1"}]}', "application/json")
    allowed = Connection(provider="vllm", base_url=base, policy=LOOPBACK, timeout=5)
    assert await request_json(allowed, "GET", "/models") == {"data": [{"id": "m1"}]}
    blocked = Connection(provider="vllm", base_url=base, policy=PUBLIC_ONLY, timeout=5)
    with pytest.raises(errors.EndpointNotAllowedError):
        await request_json(blocked, "GET", "/models")


async def test_hostname_is_resolved_and_vetted_through_the_system_resolver(server):
    base, routes, _ = server
    routes["/v"] = (200, b"{}", "application/json")
    by_name = base.replace("127.0.0.1", "localhost")
    ok = Connection(provider="vllm", base_url=by_name, policy=LOOPBACK, timeout=5)
    assert await request_json(ok, "GET", "/v") == {}
    with pytest.raises(errors.EndpointNotAllowedError):
        await request_json(
            Connection(provider="vllm", base_url=by_name, policy=PUBLIC_ONLY, timeout=5),
            "GET",
            "/v",
        )


async def test_streaming_over_a_real_socket(server):
    base, routes, _ = server
    routes["/s"] = (200, b"data: one\n\ndata: two\n\n", "text/event-stream")
    conn = Connection(provider="vllm", base_url=base, policy=LOOPBACK, timeout=5)
    lines = [line async for line in stream_lines(conn, "POST", "/s", json_body={})]
    assert lines == ["data: one", "", "data: two", ""]


async def test_ollama_adapter_end_to_end_without_a_key(server):
    base, routes, seen = server
    routes["/api/tags"] = (200, b'{"models":[{"name":"llama3.2:1b"}]}', "application/json")
    routes["/api/chat"] = (
        200,
        json.dumps(
            {
                "model": "llama3.2:1b",
                "message": {"role": "assistant", "content": "local hello"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 3,
                "eval_count": 2,
            }
        ).encode(),
        "application/json",
    )
    conn = Connection(provider="ollama", base_url=base, policy=LOOPBACK, timeout=5)
    adapter = catalog.get("ollama")
    health = await adapter.health_check(conn)
    assert health.status == "ready" and health.models_available == 1
    response = await adapter.generate(
        conn, LLMRequest(model="llama3.2:1b", messages=[Message(role="user", content="hi")])
    )
    assert response.content == "local hello" and response.usage.total_tokens == 5
    assert any(line.startswith("POST /api/chat") for line in seen)


async def test_refused_connection_is_reported_offline():
    # a loopback port with nothing listening
    sock_server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = sock_server.sockets[0].getsockname()[1]
    sock_server.close()
    await sock_server.wait_closed()
    conn = Connection(
        provider="ollama", base_url=f"http://127.0.0.1:{port}", policy=LOOPBACK, timeout=3
    )
    health = await catalog.get("ollama").health_check(conn)
    assert health.status == "offline"
