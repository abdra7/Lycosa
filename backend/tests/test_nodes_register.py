from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import API_KEY_HEADER
from app.models import AuditLog
from tests.conftest import OPERATOR_EMAIL, bearer, login

PROFILE = {
    "cpu_model": "AMD Ryzen 9 7950X",
    "cpu_cores": 32,
    "ram_gb": 64,
    "gpus": [{"model": "NVIDIA RTX 4090", "vram_gb": 24}],
    "storage_gb": 2000,
    "storage_type": "nvme",
    "os": {"name": "Ubuntu", "version": "24.04", "arch": "x86_64"},
    "runtimes": [{"name": "ollama", "version": "0.3.0", "models": ["llama3:8b"]}],
}


def payload(name: str = "workstation-01") -> dict:
    return {"name": name, "hardware_profile": PROFILE}


async def test_register_creates_node(client: AsyncClient, node_api_key: tuple) -> None:
    full_key, _ = node_api_key
    response = await client.post(
        "/api/v1/nodes/register", json=payload(), headers={API_KEY_HEADER: full_key}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "workstation-01"
    assert body["status"] == "registered"
    # normalized columns derived from the raw profile
    assert body["cpu_cores"] == 32
    assert body["gpu_count"] == 1
    assert body["gpu_vram_gb"] == 24
    assert body["os_name"] == "Ubuntu"
    assert body["hardware_profile"]["cpu_model"] == "AMD Ryzen 9 7950X"


async def test_registered_node_appears_in_list(
    client: AsyncClient, node_api_key: tuple, users: dict
) -> None:
    full_key, _ = node_api_key
    created = (
        await client.post(
            "/api/v1/nodes/register", json=payload(), headers={API_KEY_HEADER: full_key}
        )
    ).json()

    token = await login(client, OPERATOR_EMAIL)
    listed = await client.get("/api/v1/nodes", headers=bearer(token))
    assert listed.status_code == 200
    assert created["id"] in [n["id"] for n in listed.json()]


async def test_reregister_updates_same_node(client: AsyncClient, node_api_key: tuple) -> None:
    full_key, _ = node_api_key
    headers = {API_KEY_HEADER: full_key}
    first = await client.post("/api/v1/nodes/register", json=payload(), headers=headers)
    assert first.status_code == 201

    updated = dict(PROFILE, ram_gb=128)
    second = await client.post(
        "/api/v1/nodes/register",
        json={"name": "workstation-01-renamed", "hardware_profile": updated},
        headers=headers,
    )
    assert second.status_code == 200  # re-register, not a new node
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["ram_gb"] == 128
    assert second.json()["name"] == "workstation-01-renamed"


async def test_register_unauthenticated_rejected(client: AsyncClient, roles: dict) -> None:
    response = await client.post("/api/v1/nodes/register", json=payload())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_register_with_user_token_rejected(client: AsyncClient, users: dict) -> None:
    token = await login(client, OPERATOR_EMAIL)
    response = await client.post("/api/v1/nodes/register", json=payload(), headers=bearer(token))
    assert response.status_code == 403


async def test_register_invalid_payload_gives_field_errors(
    client: AsyncClient, node_api_key: tuple
) -> None:
    full_key, _ = node_api_key
    bad = {
        "name": "bad-node",
        "hardware_profile": {
            # cpu_model missing entirely, ram negative
            "cpu_cores": 8,
            "ram_gb": -4,
            "storage_gb": 100,
            "os": {"name": "Ubuntu"},
        },
    }
    response = await client.post(
        "/api/v1/nodes/register", json=bad, headers={API_KEY_HEADER: full_key}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    fields = [d["field"] for d in error["details"]]
    assert any("cpu_model" in f for f in fields)
    assert any("ram_gb" in f for f in fields)


async def test_register_writes_audit_row(
    client: AsyncClient, node_api_key: tuple, db_session: AsyncSession
) -> None:
    full_key, record = node_api_key
    await client.post("/api/v1/nodes/register", json=payload(), headers={API_KEY_HEADER: full_key})
    entry = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "node.register"))
    ).scalar_one()
    assert entry.actor_api_key_id == record.id
    assert entry.resource_type == "node"
