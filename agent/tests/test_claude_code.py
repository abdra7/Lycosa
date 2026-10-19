"""Claude subscription runtime (ADR-032): runs a fake Claude Code CLI."""

import json
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from lycosa_agent.config import AgentSettings
from lycosa_agent.executor import AGENT_TOKEN_HEADER, OWNER_HEADER, create_app
from lycosa_agent.main import _claude_runtime, _with_capabilities
from lycosa_agent.runtimes.chat import ChatMessage
from lycosa_agent.runtimes.claude_code import (
    ClaudeCodeRuntime,
    classify_failure,
    render_prompt,
)
from tests.test_executor import TOKEN, FakeAdapter

FAKE_CLI = [sys.executable, str(Path(__file__).with_name("fake_claude.py"))]
OWNER = "owner@example.com"


def _exists(path: str) -> bool:
    return Path(path).exists()


def runtime(**kwargs) -> ClaudeCodeRuntime:
    return ClaudeCodeRuntime(command=FAKE_CLI, **kwargs)


def user(text: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=text)]


async def collect(rt: ClaudeCodeRuntime, messages, model: str = "default") -> list[dict]:
    return [event async for event in rt.stream(messages, model)]


@pytest.fixture
def record(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(path))
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    return path


async def test_stream_yields_normalized_events(record) -> None:
    events = await collect(runtime(), user("hi"))
    assert events[0] == {"type": "start", "model": "claude-test-5"}
    assert [e["text"] for e in events if e["type"] == "text"] == ["Hel", "lo"]
    usage = next(e for e in events if e["type"] == "usage")
    assert usage == {"type": "usage", "input_tokens": 712, "output_tokens": 4}
    assert events[-1] == {"type": "finish", "reason": "stop", "model": "claude-test-5"}


async def test_call_runs_locked_down_in_an_empty_directory(record, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-pass")
    await collect(runtime(), user("the prompt"), model="sonnet")
    seen = json.loads(record.read_text(encoding="utf-8"))
    argv = seen["argv"]
    for flag in ("-p", "--safe-mode", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""  # every tool disabled
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert seen["stdin"] == "the prompt"  # the prompt never goes on the command line
    assert "the prompt" not in " ".join(argv)
    assert seen["cwd_entries"] == ["system-prompt.txt"]
    assert seen["api_key_in_env"] is False  # the plan is used, not an API key
    assert not _exists(seen["cwd"])  # temporary directory removed
    # Claude Code tells the model its working directory; the name must not
    # suggest a project (Sprint 14 live QA: "the directory is lycosa-claude…")
    name = Path(seen["cwd"]).name.lower()
    assert "lycosa" not in name and "claude" not in name


async def test_default_model_passes_no_model_flag(record) -> None:
    await collect(runtime(), user("x"))
    assert "--model" not in json.loads(record.read_text(encoding="utf-8"))["argv"]


async def test_plan_usage_is_kept_without_identity(record) -> None:
    rt = runtime()
    await collect(rt, user("x"))
    assert rt.last_plan_usage == {
        "status": "allowed",
        "windows": {"five_hour": {"utilization": 0.07, "resets_at": 1792068000}},
    }
    assert "should-not-leak" not in json.dumps(rt.last_plan_usage)


@pytest.mark.parametrize(
    ("mode", "code"),
    [("not_signed_in", "not_signed_in"), ("usage_limit", "usage_limit"), ("no_result", "failed")],
)
async def test_failures_become_public_error_codes(record, monkeypatch, mode, code) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    events = await collect(runtime(), user("x"))
    error = events[-1]
    assert error["type"] == "error" and error["code"] == code
    assert "/login" not in error["message"]  # fixed messages, never the CLI's text


async def test_timeout_kills_the_cli(record, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "hang")
    events = await collect(runtime(timeout_seconds=1), user("x"))
    assert events[-1]["code"] == "timeout"


async def test_output_cap(record, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "big")
    monkeypatch.setattr("lycosa_agent.runtimes.claude_code.MAX_OUTPUT_CHARS", 1000)
    events = await collect(runtime(), user("x"))
    assert events[-1]["code"] == "output_too_large"


async def test_missing_binary_reports_not_installed() -> None:
    rt = ClaudeCodeRuntime("definitely-not-a-real-claude-binary")
    events = await collect(rt, user("x"))
    assert events == [
        {
            "type": "error",
            "code": "not_installed",
            "message": "Claude Code is not installed on this node",
        }
    ]
    status = await rt.status()
    assert status["installed"] is False and status["signed_in"] is False


async def test_status_reports_plan_but_never_identity(record) -> None:
    status = await runtime().status()
    assert status["installed"] and status["signed_in"]
    assert status["version"] == "2.1.300"
    assert status["auth_method"] == "subscription" and status["plan"] == "pro"
    dumped = json.dumps(status)
    assert "owner@example.com" not in dumped and "org-secret" not in dumped


async def test_status_signed_out(record, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "signed_out")
    status = await runtime().status()
    assert status["installed"] and not status["signed_in"]
    assert status["auth_method"] == "none" and status["plan"] is None


def test_render_prompt_single_and_multi_turn() -> None:
    system, prompt = render_prompt([ChatMessage(role="system", content="Be brief."), *user("Hi")])
    assert prompt == "Hi"
    assert system.startswith("Be brief.")
    assert "no tools" in system and "working directory" in system
    system, prompt = render_prompt(
        [
            ChatMessage(role="user", content="Hi"),
            ChatMessage(role="assistant", content="Hello!"),
            ChatMessage(role="user", content="And now?"),
        ]
    )
    assert system.startswith("You are a helpful assistant.")
    assert (
        prompt.index("<user>\nHi")
        < prompt.index("<assistant>\nHello!")
        < prompt.index("<user>\nAnd now?")
    )


def test_classify_failure() -> None:
    assert classify_failure("Invalid API key · Please run /login") == "not_signed_in"
    assert classify_failure("", 429) == "usage_limit"
    assert classify_failure("model claude-x not found") == "model_unavailable"
    assert classify_failure("boom") == "failed"


# --- executor endpoints ------------------------------------------------------------


def client_for(claude: ClaudeCodeRuntime | None, owner: str = OWNER) -> AsyncClient:
    app = create_app(FakeAdapter(), token=TOKEN, claude=claude, claude_owner=owner)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://agent")


def headers(owner: str | None = OWNER) -> dict[str, str]:
    out = {AGENT_TOKEN_HEADER: TOKEN}
    if owner is not None:
        out[OWNER_HEADER] = owner
    return out


async def test_complete_streams_ndjson_for_the_owner(record) -> None:
    async with client_for(runtime()) as client:
        response = await client.post(
            "/claude/complete",
            json={"model": "haiku", "messages": [{"role": "user", "content": "hi"}]},
            headers=headers("Owner@Example.com"),  # case-insensitive email match
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in response.text.splitlines()]
    assert "".join(e.get("text", "") for e in events if e["type"] == "text") == "Hello"


async def test_complete_requires_agent_token(record) -> None:
    async with client_for(runtime()) as client:
        response = await client.post(
            "/claude/complete",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers={OWNER_HEADER: OWNER},
        )
    assert response.status_code == 401


@pytest.mark.parametrize("owner", [None, "someone-else@example.com"])
async def test_complete_refuses_other_users(record, owner) -> None:
    async with client_for(runtime()) as client:
        response = await client.post(
            "/claude/complete",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=headers(owner),
        )
    assert response.status_code == 403
    assert not record.exists()  # the CLI never ran


async def test_complete_rejects_flag_like_model(record) -> None:
    async with client_for(runtime()) as client:
        response = await client.post(
            "/claude/complete",
            json={
                "model": "--dangerously-skip-permissions",
                "messages": [{"role": "user", "content": "x"}],
            },
            headers=headers(),
        )
    assert response.status_code == 422


async def test_disabled_runtime(record) -> None:
    async with client_for(None) as client:
        status = await client.get("/claude/status", headers=headers())
        complete = await client.post(
            "/claude/complete",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=headers(),
        )
    assert status.json() == {"enabled": False, "owner_matches": False}
    assert complete.status_code == 404


async def test_status_endpoint_reports_owner_match(record) -> None:
    async with client_for(runtime()) as client:
        mine = (await client.get("/claude/status", headers=headers())).json()
        other = (await client.get("/claude/status", headers=headers("x@example.com"))).json()
    assert mine["owner_matches"] is True and mine["signed_in"] is True
    assert other["owner_matches"] is False


# --- configuration -------------------------------------------------------------------


def test_runtime_off_by_default() -> None:
    settings = AgentSettings(_env_file=None)
    assert _claude_runtime(settings) is None
    caps = _with_capabilities({}, settings)["extra"]["agent_capabilities"]
    assert caps["claude_subscription"] is False


def test_enabling_requires_an_owner() -> None:
    with pytest.raises(SystemExit):
        _claude_runtime(AgentSettings(_env_file=None, claude_subscription=True))
    settings = AgentSettings(
        _env_file=None, claude_subscription=True, claude_subscription_owner=OWNER
    )
    assert isinstance(_claude_runtime(settings), ClaudeCodeRuntime)
    assert _with_capabilities({}, settings)["extra"]["agent_capabilities"]["claude_subscription"]
