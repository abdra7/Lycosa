from typing import Any

from httpx import ASGITransport, AsyncClient

from lycosa_agent.executor import AGENT_TOKEN_HEADER, create_app

TOKEN = "s3cret-agent-token-of-decent-length"


class FakeAdapter:
    name = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.pulled: list[str] = []

    async def list_models(self) -> list[str]:
        return ["fake:latest", *self.pulled]

    async def pull_model(self, model: str) -> None:
        if self.fail:
            raise RuntimeError("registry unreachable")
        self.pulled.append(model)

    async def generate(self, model: str, prompt: str, options: dict[str, Any] | None = None) -> str:
        if self.fail:
            raise RuntimeError("model exploded")
        return f"{model} says: {prompt}"


def make_client(fail: bool = False) -> AsyncClient:
    app = create_app(FakeAdapter(fail=fail), token=TOKEN)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://agent")


async def test_execute_requires_token() -> None:
    async with make_client() as client:
        response = await client.post("/execute", json={"model": "m", "prompt": "p"})
        assert response.status_code == 401


async def test_execute_rejects_wrong_token() -> None:
    async with make_client() as client:
        response = await client.post(
            "/execute", json={"model": "m", "prompt": "p"}, headers={AGENT_TOKEN_HEADER: "nope"}
        )
        assert response.status_code == 401


async def test_execute_runs_task() -> None:
    async with make_client() as client:
        response = await client.post(
            "/execute",
            json={"model": "fake:latest", "prompt": "hi"},
            headers={AGENT_TOKEN_HEADER: TOKEN},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["output"] == "fake:latest says: hi"


async def test_execute_reports_failure_without_crashing() -> None:
    async with make_client(fail=True) as client:
        response = await client.post(
            "/execute",
            json={"model": "m", "prompt": "p"},
            headers={AGENT_TOKEN_HEADER: TOKEN},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "model exploded" in body["error"]


async def test_healthz_is_open() -> None:
    async with make_client() as client:
        assert (await client.get("/healthz")).status_code == 200


async def test_models_requires_token() -> None:
    async with make_client() as client:
        assert (await client.get("/models")).status_code == 401
        ok = await client.get("/models", headers={AGENT_TOKEN_HEADER: TOKEN})
        assert ok.json() == ["fake:latest"]


async def test_pull_requires_token() -> None:
    async with make_client() as client:
        response = await client.post("/models/pull", json={"model": "llama3.1:8b"})
        assert response.status_code == 401


async def test_pull_downloads_and_returns_inventory() -> None:
    async with make_client() as client:
        response = await client.post(
            "/models/pull",
            json={"model": "llama3.1:8b"},
            headers={AGENT_TOKEN_HEADER: TOKEN},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["models"] == ["fake:latest", "llama3.1:8b"]


async def test_pull_reports_failure_without_crashing() -> None:
    async with make_client(fail=True) as client:
        response = await client.post(
            "/models/pull",
            json={"model": "llama3.1:8b"},
            headers={AGENT_TOKEN_HEADER: TOKEN},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "registry unreachable" in body["error"]
