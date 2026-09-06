"""Live, local-only smoke check. Run inside the configured API container.

Uses configured bootstrap credentials in memory; never prints credentials.
Default is read-only. --execute submits one short Ollama task on an online
node with an installed model. No cloud provider requests are made.
"""

import argparse
import json

import httpx

from app.core.config import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180) as client:
        health = client.get("/healthz")
        health.raise_for_status()
        print("health:", health.status_code)
        assert client.get("/api/v1/nodes").status_code == 401
        print("unauthenticated access: rejected")
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": settings.default_admin_email,
                "password": settings.default_admin_password,
            },
        )
        if response.status_code != 200:
            raise SystemExit(
                f"Login failed ({response.status_code}); use operator credentials locally. "
                "No password was changed."
            )
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        print("login: passed")
        response = client.get("/api/v1/nodes")
        response.raise_for_status()
        nodes = response.json()
        candidates = []
        for node in nodes:
            models = [
                model
                for runtime in (node.get("hardware_profile") or {}).get("runtimes", [])
                if runtime.get("name") == "ollama"
                for model in runtime.get("models", [])
            ]
            print(
                json.dumps(
                    {
                        "node": node["name"],
                        "status": node["status"],
                        "models": models,
                        "runtime_health": (node.get("metrics") or {}).get("runtime_health"),
                    },
                    ensure_ascii=False,
                )
            )
            if node["status"] == "online" and models:
                candidates.append((node, models[0]))
        for path in ("/api/v1/tasks", "/openapi.json"):
            checked = client.get(path)
            checked.raise_for_status()
            print(path + ": passed")
        if not args.execute:
            return
        if not candidates:
            raise SystemExit(
                "Local inference pending: no online Ollama node with an installed model."
            )
        response = client.post(
            "/api/v1/tasks",
            json={
                "prompt": "What is 2 + 2? Reply with only the number.",
                "type": "general",
                "provider": "ollama",
                "model": candidates[0][1],
                "requires_privacy": True,
                "max_tokens": 8,
                "temperature": 0,
                "options": {"num_thread": 2},
            },
        )
        response.raise_for_status()
        task = response.json()
        print(
            json.dumps(
                {
                    "task_id": task["id"],
                    "status": task["status"],
                    "output": (task.get("result") or {}).get("output"),
                },
                ensure_ascii=False,
            )
        )
        assert task["status"] == "succeeded", (
            "Local task did not succeed; inspect its trace in the dashboard."
        )
        fetched = client.get("/api/v1/tasks/" + task["id"])
        fetched.raise_for_status()
        assert fetched.json()["status"] == "succeeded"
        print("local execution and persisted trace: passed")
        output = (task.get("result") or {}).get("output", "").strip()
        assert output == "4", "Transport passed, but the model failed the simple answer check."
        print("model answer check: passed (4)")


if __name__ == "__main__":
    main()
