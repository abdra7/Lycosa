"""Per-node LLM recommendations + remote model install (agent configure)."""

import uuid

import respx
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.services.llm_recommendation import recommend_models
from tests.conftest import ADMIN_EMAIL, bearer, login, make_node


def _by_model(recs):
    return {r.model: r for r in recs}


def test_gpu_box_recommends_biggest_model_that_fits() -> None:
    recs = recommend_models(ram_gb=32, gpu_vram_gb=24)
    by_model = _by_model(recs)
    # 27B fits the 24 GB card and is the biggest runnable general model
    assert by_model["gemma2:27b"].recommended
    assert by_model["gemma2:27b"].runs_on == "gpu"
    # 70B needs 40 GB VRAM or 64 GB RAM — out of reach for this box
    assert not by_model["llama3.1:70b"].runnable
    assert "needs 40" in by_model["llama3.1:70b"].reason
    # smaller models still runnable, just not the headline pick
    assert by_model["llama3.1:8b"].runnable
    assert not by_model["llama3.1:8b"].recommended


def test_cpu_only_box_falls_back_to_ram() -> None:
    recs = recommend_models(ram_gb=16, gpu_vram_gb=None)
    by_model = _by_model(recs)
    assert by_model["llama3.1:8b"].recommended
    assert by_model["llama3.1:8b"].runs_on == "cpu"
    assert "slower than GPU" in by_model["llama3.1:8b"].reason
    assert by_model["qwen2.5-coder:7b"].recommended  # best coding pick
    assert not by_model["gemma2:27b"].runnable


def test_tiny_box_still_gets_a_model() -> None:
    recs = recommend_models(ram_gb=4, gpu_vram_gb=None)
    runnable = [r for r in recs if r.runnable]
    assert [r.model for r in runnable] == ["llama3.2:1b"]
    assert runnable[0].recommended


def test_one_recommendation_per_use_case_and_ordering() -> None:
    recs = recommend_models(ram_gb=64, gpu_vram_gb=8)
    per_use_case: dict[str, int] = {}
    for r in recs:
        if r.recommended:
            per_use_case[r.use_case] = per_use_case.get(r.use_case, 0) + 1
            assert r.runnable
    assert all(count == 1 for count in per_use_case.values())
    # recommended entries sort first
    leading = [r.recommended for r in recs[: len(per_use_case)]]
    assert all(leading)


def test_installed_models_are_flagged() -> None:
    recs = recommend_models(ram_gb=32, gpu_vram_gb=8, installed_models={"llama3.1:8b"})
    by_model = _by_model(recs)
    assert by_model["llama3.1:8b"].installed
    assert not by_model["mistral:7b"].installed


async def test_llm_recommendations_endpoint(
    client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    node = await make_node(db_session, "gpu-box", ram_gb=32, gpu_vram_gb=24, models=["llama3.1:8b"])
    token = await login(client, ADMIN_EMAIL)
    response = await client.get(
        f"/api/v1/nodes/{node.id}/llm-recommendations", headers=bearer(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    top = body[0]
    assert top["recommended"] and top["runnable"]
    installed = {r["model"] for r in body if r["installed"]}
    assert installed == {"llama3.1:8b"}


async def test_llm_recommendations_require_auth(
    client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    node = await make_node(db_session, "quiet-box")
    response = await client.get(f"/api/v1/nodes/{node.id}/llm-recommendations")
    assert response.status_code == 401


async def test_node_key_reads_its_own_recommendations(
    client: AsyncClient, users: dict, db_session: AsyncSession, node_api_key: tuple
) -> None:
    """The agent's zero-config setup: a node key may ask about its own node."""
    from app.core.security import API_KEY_HEADER

    node = await make_node(db_session, "self-box", ram_gb=16)
    full_key, record = node_api_key
    record.node_id = node.id  # bind the key, as registration does
    await db_session.commit()

    response = await client.get(
        f"/api/v1/nodes/{node.id}/llm-recommendations", headers={API_KEY_HEADER: full_key}
    )
    assert response.status_code == 200, response.text
    assert any(r["runnable"] for r in response.json())


async def test_node_key_cannot_read_other_nodes_recommendations(
    client: AsyncClient, users: dict, db_session: AsyncSession, node_api_key: tuple
) -> None:
    from app.core.security import API_KEY_HEADER

    mine = await make_node(db_session, "mine")
    other = await make_node(db_session, "other")
    full_key, record = node_api_key
    record.node_id = mine.id
    await db_session.commit()

    response = await client.get(
        f"/api/v1/nodes/{other.id}/llm-recommendations", headers={API_KEY_HEADER: full_key}
    )
    assert response.status_code == 403


@respx.mock(assert_all_mocked=False)
async def test_install_model_pulls_via_agent_and_refreshes_inventory(
    respx_mock, client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    node = await make_node(db_session, "gpu-box", ram_gb=32, gpu_vram_gb=24)
    respx_mock.post("http://gpu-box:8010/models/pull").mock(
        return_value=Response(
            200, json={"status": "succeeded", "models": ["llama3:8b", "gemma2:27b"]}
        )
    )
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        f"/api/v1/nodes/{node.id}/models",
        json={"model": "gemma2:27b"},
        headers=bearer(token),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "succeeded", "models": ["llama3:8b", "gemma2:27b"]}

    db_session.expire_all()
    await db_session.refresh(node)
    runtimes = {r["name"]: r for r in node.hardware_profile["runtimes"]}
    assert runtimes["ollama"]["models"] == ["llama3:8b", "gemma2:27b"]
    entry = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "node.model.install"))
    ).scalar_one()
    assert entry.resource_id == str(node.id)
    assert entry.detail == {"model": "gemma2:27b"}

    # recommendations now show it installed
    recs = await client.get(f"/api/v1/nodes/{node.id}/llm-recommendations", headers=bearer(token))
    installed = {r["model"] for r in recs.json() if r["installed"]}
    assert "gemma2:27b" in installed


@respx.mock(assert_all_mocked=False)
async def test_install_model_agent_failure_is_502(
    respx_mock, client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    node = await make_node(db_session, "sad-box")
    respx_mock.post("http://sad-box:8010/models/pull").mock(
        return_value=Response(200, json={"status": "failed", "error": "disk full"})
    )
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        f"/api/v1/nodes/{node.id}/models", json={"model": "mistral:7b"}, headers=bearer(token)
    )
    assert response.status_code == 502
    assert "disk full" in response.text


async def test_install_model_offline_node_is_409(
    client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    node = await make_node(db_session, "offline-box", status="offline")
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        f"/api/v1/nodes/{node.id}/models", json={"model": "mistral:7b"}, headers=bearer(token)
    )
    assert response.status_code == 409
    assert "offline" in response.text


async def test_install_model_without_agent_is_409(
    client: AsyncClient, users: dict, db_session: AsyncSession
) -> None:
    node = await make_node(db_session, "bare-box", agent_url=None, agent_token=None)
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        f"/api/v1/nodes/{node.id}/models", json={"model": "mistral:7b"}, headers=bearer(token)
    )
    assert response.status_code == 409
    assert "lycosa-agent" in response.text


async def test_install_model_missing_node_is_404(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        f"/api/v1/nodes/{uuid.uuid4()}/models", json={"model": "mistral:7b"}, headers=bearer(token)
    )
    assert response.status_code == 404
