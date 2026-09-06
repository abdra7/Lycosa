"""Live checks for the temporary Sprint12 local node; no cloud requests.

Run inside the API container via stdin. Agent credentials stay in memory.
Does not stop services or modify existing nodes. Performs one local chat call.
"""

import asyncio
import json
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models import Node

NODE_NAME = "Sprint12-Local-Ollama"


async def agent_contact():
    async with get_sessionmaker()() as db:
        node = (await db.execute(select(Node).where(Node.name == NODE_NAME))).scalar_one()
        return str(node.id), node.agent_url, node.agent_token


def main():
    node_id, agent_url, agent_token = asyncio.run(agent_contact())
    settings = get_settings()
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180) as controller:
        response = controller.post(
            "/api/v1/auth/login",
            json={
                "email": settings.default_admin_email,
                "password": settings.default_admin_password,
            },
        )
        response.raise_for_status()
        controller.headers["Authorization"] = "Bearer " + response.json()["access_token"]

        def snapshot():
            response = controller.get("/api/v1/nodes/" + node_id)
            response.raise_for_status()
            assert agent_token not in response.text
            node = response.json()
            assert node["status"] == "online"
            age = (
                datetime.now(UTC) - datetime.fromisoformat(node["last_heartbeat_at"])
            ).total_seconds()
            assert 0 <= age < 15, "Heartbeat is stale"
            metrics = node["metrics"]
            for field in ("cpu_percent", "ram_percent", "disk_percent"):
                assert 0 <= metrics[field] <= 100
            assert metrics["ram_available_mb"] >= 0
            assert metrics["runtime_health"]["ollama"] is True
            if not metrics["gpus"]:
                assert metrics["gpu_unavailable_reason"]
            return node

        before = snapshot()
        print("telemetry ranges, fresh heartbeat and token redaction: passed", flush=True)
        with httpx.Client(base_url=agent_url, timeout=180) as agent:
            body = {
                "provider": "ollama",
                "model": "llama3.2:1b",
                "messages": [{"role": "user", "content": "What is 2+2? Answer only the number."}],
                "max_tokens": 4,
                "temperature": 0,
            }
            assert agent.post("/execute", json=body).status_code == 401
            agent.headers["X-Agent-Token"] = "invalid-smoke-token"
            assert agent.post("/execute", json=body).status_code == 401
            agent.headers["X-Agent-Token"] = agent_token
            models = agent.get("/models")
            models.raise_for_status()
            assert "llama3.2:1b" in models.json()
            for unsupported in ({"stream": True}, {"agent_loop": True}, {"tools": [{"name": "x"}]}):
                assert agent.post("/execute", json={**body, **unsupported}).status_code == 422
            print(
                "agent auth, model inventory and unsupported-feature rejection: passed", flush=True
            )
            started = time.monotonic()
            response = agent.post("/execute", json=body)
            response.raise_for_status()
            assert agent_token not in response.text
            result = response.json()
            assert result["status"] == "succeeded"
            assert result["output"].strip() == "4", "Chat answer check failed"
            print(
                json.dumps(
                    {
                        "structured_chat": "passed",
                        "output": "4",
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                    }
                ),
                flush=True,
            )
        time.sleep(6)  # allow the normal five-second heartbeat to publish idle state
        after = snapshot()
        assert after["last_heartbeat_at"] > before["last_heartbeat_at"]
        assert after["metrics"]["running_tasks"] == 0
        print("heartbeat advanced; running_tasks returned to zero: passed", flush=True)


if __name__ == "__main__":
    main()
