"""Run a host-local smoke-test agent; keep its one-day node key in memory only.

Requires the local controller container and Ollama on localhost:11434.
No provider keys, cloud calls, model auto-downloads or LAN discovery.
"""

import asyncio
import json
import logging
import subprocess

from lycosa_agent.config import AgentSettings
from lycosa_agent.main import run


def main():
    # Authenticate inside the configured controller: no admin credential leaves
    # that container. stdout is captured, never echoed or written to disk.
    provision = """
import json
from datetime import UTC, datetime, timedelta
import httpx
from app.core.config import get_settings
s = get_settings()
with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15) as c:
    r = c.post("/api/v1/auth/login", json={"email":s.default_admin_email,"password":s.default_admin_password})
    r.raise_for_status()
    c.headers["Authorization"] = "Bearer " + r.json()["access_token"]
    r = c.post("/api/v1/admin/api-keys", json={"name":"Sprint12-local-smoke", "role":"node",
        "expires_at":(datetime.now(UTC)+timedelta(days=1)).isoformat()})
    r.raise_for_status()
    print(json.dumps({"key":r.json()["api_key"]}))
"""
    result = subprocess.run(
        ["docker", "exec", "-i", "lycosa-api-1", "python", "-"],
        input=provision,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit("Node-key provisioning failed; credentials were not printed.")
    key = json.loads(result.stdout)["key"]
    del result
    logging.basicConfig(level=logging.INFO)
    asyncio.run(
        run(
            AgentSettings(
                _env_file=None,
                api_key=key,
                controller_url="http://127.0.0.1:8000",
                node_name="Sprint12-Local-Ollama",
                exec_host="0.0.0.0",
                exec_port=8010,
                advertise_url="http://host.docker.internal:8010",
                ollama_url="http://127.0.0.1:11434",
                cloud_execution_enabled=False,
                auto_pull_model=False,
                discovery_enabled=False,
            )
        )
    )


if __name__ == "__main__":
    main()
