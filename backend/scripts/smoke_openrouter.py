"""Explicit live free-model acceptance. Run inside the configured API container.

Pass --execute and provide the OpenRouter key on stdin (never a CLI argument).
Creates synthetic QA data only. Keeps the demo workflow and task history;
removes its own collection. Configures a session key until controller restart.
"""

import argparse
import json
import sys
import time
import uuid

import httpx

from app.core.config import get_settings
from app.services.openrouter import MODEL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.parse_args()
    key = sys.stdin.readline().strip()
    if not key:
        raise SystemExit("Provide a key on stdin")
    settings = get_settings()
    report = {"model": MODEL, "checks": [], "tasks": [], "reported_cost": 0}
    name = "qa-openrouter-" + uuid.uuid4().hex[:10]
    collection_id = None
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180) as c:

        def request(method, path, **kwargs):
            r = c.request(method, path, **kwargs)
            if not r.is_success:
                raise RuntimeError(f"{method} {path}: HTTP {r.status_code}")
            return r.json() if r.content else None

        auth = request(
            "POST",
            "/api/v1/auth/login",
            json={
                "email": settings.default_admin_email,
                "password": settings.default_admin_password,
            },
        )
        c.headers["Authorization"] = "Bearer " + auth["access_token"]
        request(
            "PUT",
            "/api/v1/admin/providers/openrouter/credential",
            json={"key": key, "storage": "session"},
        )
        key = None
        report["checks"].append("admin session credential configured")
        for path in [
            "/healthz",
            "/api/v1/nodes",
            "/api/v1/tasks",
            "/api/v1/workflows",
            "/api/v1/knowledge/collections",
            "/api/v1/admin/providers",
            "/api/v1/admin/audit-logs",
        ]:
            request("GET", path)
        report["checks"].append("authenticated desktop section APIs reachable")

        def task(prompt, **extra):
            start = time.monotonic()
            result = request(
                "POST",
                "/api/v1/tasks",
                json={
                    "prompt": prompt,
                    "type": "general",
                    "provider": "openrouter",
                    "model": MODEL,
                    "max_tokens": 512,
                    **extra,
                },
            )
            if result["status"] != "succeeded":
                raise RuntimeError(result.get("error") or "Task failed")
            persisted = request("GET", "/api/v1/tasks/" + result["id"])
            assert persisted["result"] == result["result"]
            usage = result["result"].get("usage", {})
            if "cost" in usage:
                assert usage["cost"] == 0
                report["reported_cost"] += usage["cost"]
            report["tasks"].append(
                {"id": result["id"], "seconds": round(time.monotonic() - start, 2), "usage": usage}
            )
            return result["result"]["output"]

        try:
            assert "4" in task("What is 2 + 2? Reply with the number only.")
            report["checks"].append("real model task and persisted output")
            collection = request("POST", "/api/v1/knowledge/collections", json={"name": name})
            collection_id = collection["id"]
            document = request(
                "POST",
                f"/api/v1/knowledge/collections/{collection_id}/documents",
                files={
                    "file": (
                        "qa-openrouter.txt",
                        b"The QA test support code is LYCOSA-OR-731. The QA test owner is abdra7.",
                        "text/plain",
                    )
                },
            )
            assert document["status"] == "embedded", "Synthetic document did not embed"
            hits = request(
                "POST",
                "/api/v1/knowledge/retrieve",
                json={"query": "QA test support code", "collection": name},
            )
            assert hits["chunks"] and all(h["collection"] == name for h in hits["chunks"])
            assert any("LYCOSA-OR-731" in h["text"] for h in hits["chunks"])
            assert "LYCOSA-OR-731" in task(
                "What is the QA test support code?",
                knowledge_query="QA test support code",
                knowledge_collection=name,
            )
            report["checks"].append(
                "real document ingestion, scoped vector retrieval and grounded OpenRouter answer"
            )
            private = request(
                "POST",
                "/api/v1/tasks",
                json={
                    "prompt": "private fixture",
                    "provider": "openrouter",
                    "model": MODEL,
                    "requires_privacy": True,
                },
            )
            assert private["status"] == "failed"
            report["checks"].append("privacy blocks external execution")
            workflow = request(
                "POST",
                "/api/v1/workflows",
                json={
                    "name": name,
                    "definition": {
                        "steps": [
                            {
                                "id": "lookup",
                                "kind": "retrieve",
                                "query": "QA test support code",
                                "collection": name,
                            },
                            {
                                "id": "answer",
                                "kind": "task",
                                "provider": "openrouter",
                                "model": MODEL,
                                "max_tokens": 512,
                                "prompt": "Return only the support code from this context: "
                                "{{steps.lookup.output}}",
                            },
                            {
                                "id": "gate",
                                "kind": "approval",
                                "message": "Approve synthetic QA summary",
                            },
                            {
                                "id": "summary",
                                "kind": "task",
                                "provider": "openrouter",
                                "model": MODEL,
                                "max_tokens": 512,
                                "prompt": "Repeat this code exactly: {{steps.answer.output}}",
                            },
                        ]
                    },
                },
            )
            wid = workflow["id"]
            report["workflow_id"] = wid
            for approved in [True, False]:
                run = request(
                    "POST", f"/api/v1/workflows/{wid}/run", json={"input": "synthetic QA"}
                )
                assert run["status"] == "paused", "Workflow did not pause"
                assert not any(
                    s["step_id"] == "summary" and s["status"] == "succeeded"
                    for s in run["step_runs"]
                )
                run = request(
                    "POST",
                    f"/api/v1/workflows/{wid}/runs/{run['id']}/approve",
                    json={"approved": approved},
                )
                assert run["status"] == ("succeeded" if approved else "failed")
                for step in run["step_runs"]:
                    if step["kind"] == "task" and step["status"] == "succeeded":
                        assert "LYCOSA-OR-731" in (step["output"] or "")
                report["checks"].append(
                    "RAG workflow "
                    + ("approve/resume passed" if approved else "reject blocks downstream passed")
                )
            report["status"] = "passed"
        except Exception as exc:
            report["status"] = "failed"
            report["error"] = str(exc)
        finally:
            if collection_id:
                request("DELETE", f"/api/v1/knowledge/collections/{collection_id}")
                report["checks"].append(
                    "synthetic collection deleted; workflow retained as QA history "
                    "(collection must be recreated before rerun)"
                )
            request("POST", "/api/v1/auth/logout")
            print(json.dumps(report, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
