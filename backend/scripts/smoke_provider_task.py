"""Opt-in synthetic task using the controller's already configured OpenRouter key."""

import argparse
import json

import httpx

from app.core.config import get_settings
from app.services.openrouter import MODEL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.parse_args()
    settings = get_settings()
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180) as client:
        auth = client.post(
            "/api/v1/auth/login",
            json={
                "email": settings.default_admin_email,
                "password": settings.default_admin_password,
            },
        )
        auth.raise_for_status()
        client.headers["Authorization"] = "Bearer " + auth.json()["access_token"]
        try:
            response = client.post(
                "/api/v1/tasks",
                json={
                    "prompt": "What is 7 + 8? Reply with the number only.",
                    "type": "general",
                    "provider": "openrouter",
                    "model": MODEL,
                    "max_tokens": 1024,
                },
            )
            response.raise_for_status()
            task = response.json()
            result = task.get("result") or {}
            output = result.get("output", "")
            passed = task["status"] == "succeeded" and output.strip() == "15"
            print(
                json.dumps(
                    {
                        "passed": passed,
                        "task_id": task["id"],
                        "status": task["status"],
                        "model": MODEL,
                        "output": output,
                        "usage": result.get("usage", {}),
                    },
                    indent=2,
                )
            )
            if not passed:
                raise SystemExit(1)
        finally:
            client.post("/api/v1/auth/logout").raise_for_status()


if __name__ == "__main__":
    main()
