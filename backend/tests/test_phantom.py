import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, func, select

from app.core.config import get_settings
from app.models import AuditLog, Task, TaskExecution
from app.schemas.phantom import PhantomRequest
from app.services import phantom
from app.services.phantom import PhantomError, PhantomExecutor
from tests.conftest import ADMIN_EMAIL, bearer, login

SENSITIVE = "synthetic-sensitive-marker-98411"


@pytest.fixture
def isolation(monkeypatch, tmp_path):
    model = tmp_path / "fixture.gguf"
    model.write_bytes(b"not-a-real-model")
    settings = get_settings()
    monkeypatch.setattr(settings, "phantom_enabled", True)
    monkeypatch.setattr(settings, "phantom_models", {"test_model": str(model)})
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    calls = []

    async def command(*args, **kwargs):
        calls.append((args, kwargs))
        if args[0] == "context":
            return 0, b'"unix:///var/run/docker.sock"'
        if args[0] == "start":
            return 0, json.dumps({"output": "synthetic insight"}).encode()
        return 0, b""

    monkeypatch.setattr(phantom, "_command", command)
    return calls


async def counts(db):
    return [
        await db.scalar(select(func.count()).select_from(m))
        for m in (Task, TaskExecution, AuditLog)
    ]


async def test_api_no_task_execution_or_audit_rows(client, users, db_session, db_engine, isolation):
    token = await login(client, ADMIN_EMAIL)
    before = await counts(db_session)
    writes = []

    def inspect_sql(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split()[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(db_engine.sync_engine, "before_cursor_execute", inspect_sql)
    try:
        response = await client.post(
            "/api/v1/phantom/tasks",
            headers=bearer(token),
            json={"prompt": SENSITIVE, "model": "test_model"},
        )
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", inspect_sql)
    assert writes == []
    assert response.status_code == 200, response.text
    assert response.json()["persisted"] is False
    assert response.json()["cleanup"] == "confirmed"
    assert response.headers["cache-control"] == "no-store"
    assert "x-request-id" not in response.headers
    assert await counts(db_session) == before
    assert SENSITIVE not in response.text
    create = next(args for args, _ in isolation if args[0] == "create")
    for flag in [
        "--network=none",
        "--read-only",
        "--log-driver=none",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--memory-swap",
        "--rm",
    ]:
        assert flag in create
    assert not any(SENSITIVE in arg for args, _ in isolation for arg in args)
    sent = next(kwargs["data"] for args, kwargs in isolation if args[0] == "start")
    assert json.loads(sent)["prompt"] == SENSITIVE
    assert isolation[-1][0][0] == "ps"


@pytest.mark.parametrize(
    "extra",
    [
        {"provider": "openai"},
        {"provider": "ollama"},
        {"knowledge_query": SENSITIVE},
        {"options": {"cache": True}},
        {"stream": True},
        {SENSITIVE: "extra"},
    ],
)
async def test_forbidden_input_never_runs_or_echoes(client, users, isolation, extra):
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/phantom/tasks",
        headers=bearer(token),
        json={"prompt": SENSITIVE, "model": "test_model", **extra},
    )
    assert response.status_code == 422
    assert SENSITIVE not in response.text
    assert isolation == []
    assert response.headers["cache-control"] == "no-store"


async def test_auth_required_and_no_api_key_write(client, node_api_key, db_session, isolation):
    full_key, record = node_api_key
    response = await client.post(
        "/api/v1/phantom/tasks",
        headers={"X-API-Key": full_key},
        json={"prompt": SENSITIVE, "model": "test_model"},
    )
    assert response.status_code == 401
    await db_session.refresh(record)
    assert record.last_used_at is None
    assert isolation == []


async def test_cleanup_failure_does_not_return_answer(isolation, monkeypatch):
    original = phantom._command

    async def failed_cleanup(*args, **kwargs):
        if args[0] == "ps":
            return 0, b"container-still-present"
        return await original(*args, **kwargs)

    monkeypatch.setattr(phantom, "_command", failed_cleanup)
    engine = PhantomExecutor()
    with pytest.raises(PhantomError, match="cleanup unconfirmed"):
        await engine.execute(PhantomRequest(prompt=SENSITIVE, model="test_model"))
    assert engine.active == 0


async def test_cancellation_removes_container(isolation, monkeypatch):
    original = phantom._command
    started = asyncio.Event()

    async def blocked(*args, **kwargs):
        if args[0] == "start":
            started.set()
            await asyncio.Event().wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(phantom, "_command", blocked)
    engine = PhantomExecutor()
    job = asyncio.create_task(engine.execute(PhantomRequest(prompt=SENSITIVE, model="test_model")))
    await started.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert any(args[0] == "rm" for args, _ in isolation)
    assert isolation[-1][0][0] == "ps"
    assert engine.active == 0


@pytest.mark.parametrize("failure", ["timeout", "malformed", "error"])
async def test_execution_failure_still_cleans_up(isolation, monkeypatch, failure):
    original = phantom._command

    async def broken(*args, **kwargs):
        if args[0] == "start":
            if failure == "timeout":
                raise TimeoutError(SENSITIVE)
            return (0, SENSITIVE.encode()) if failure == "malformed" else (1, b"")
        return await original(*args, **kwargs)

    monkeypatch.setattr(phantom, "_command", broken)
    engine = PhantomExecutor()
    with pytest.raises(PhantomError) as e:
        await engine.execute(PhantomRequest(prompt=SENSITIVE, model="test_model"))
    assert SENSITIVE not in str(e.value)
    assert isolation[-1][0][0] == "ps"
    assert engine.active == 0


async def test_remote_daemon_and_busy_fail_closed(isolation, monkeypatch):
    engine = PhantomExecutor()
    engine.active = get_settings().phantom_max_concurrent
    with pytest.raises(PhantomError, match="capacity"):
        await engine.execute(PhantomRequest(prompt=SENSITIVE, model="test_model"))
    assert isolation == []
    engine.active = 0
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote:2376")
    with pytest.raises(PhantomError, match="local Linux"):
        await engine.execute(PhantomRequest(prompt=SENSITIVE, model="test_model"))
    assert not any(args[0] in {"create", "start"} for args, _ in isolation)


async def test_normal_task_cannot_silently_ignore_phantom_flag(client, users, db_session):
    token = await login(client, ADMIN_EMAIL)
    before = await counts(db_session)
    for flag in ["phantom", "ephemeral", "retention"]:
        response = await client.post(
            "/api/v1/tasks", headers=bearer(token), json={"prompt": SENSITIVE, flag: True}
        )
        assert response.status_code == 422
    assert await counts(db_session) == before


async def test_body_limit_and_query_rejection(client, users, isolation):
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/phantom/tasks", headers=bearer(token), content=b"x" * 262145
    )
    assert response.status_code in {413, 422}
    response = await client.post(
        "/api/v1/phantom/tasks?prompt=secret",
        headers=bearer(token),
        json={"prompt": SENSITIVE, "model": "test_model"},
    )
    assert response.status_code == 400
    assert "secret" not in response.text
    assert isolation == []


async def test_unknown_failure_sanitized(client, users, monkeypatch, caplog):
    token = await login(client, ADMIN_EMAIL)
    monkeypatch.setattr(phantom.executor, "execute", AsyncMock(side_effect=RuntimeError(SENSITIVE)))
    response = await client.post(
        "/api/v1/phantom/tasks",
        headers=bearer(token),
        json={"prompt": SENSITIVE, "model": "test_model"},
    )
    assert response.status_code == 502
    assert SENSITIVE not in response.text + caplog.text
