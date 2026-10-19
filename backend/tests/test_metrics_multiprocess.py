"""With WORKERS>1 every uvicorn worker keeps its own metric values, so one
scrape of /metrics saw only the worker that served it: counters jumped
between scrapes (149 → 20 → 150 on the Sprint 14 QA stack) and Prometheus
read them as resets. With PROMETHEUS_MULTIPROC_DIR set, /metrics must
aggregate every worker's samples."""

import os
import pathlib
import subprocess
import sys
import textwrap

BACKEND = pathlib.Path(__file__).resolve().parents[1]

WORKER = """
    from app.core.metrics import LLM_REQUESTS, NODE_CPU
    LLM_REQUESTS.labels("claude_subscription", "succeeded").inc()
    NODE_CPU.labels("n1").set({cpu})
"""

SCRAPE = """
    from starlette.testclient import TestClient
    from app.main import app
    print(TestClient(app).get("/metrics/").text)
"""


def _python(code: str, env: dict[str, str]) -> str:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout


def test_metrics_aggregate_every_worker(tmp_path):
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(tmp_path)}
    _python(WORKER.format(cpu=10), env)  # worker 1
    _python(WORKER.format(cpu=55), env)  # worker 2, the later heartbeat
    text = _python(SCRAPE, env)  # a third process serves the scrape
    assert (
        'lycosa_llm_requests_total{provider="claude_subscription",status="succeeded"} 2.0' in text
    )
    # node gauges come from heartbeats on any worker: the latest write wins
    assert 'lycosa_node_cpu_percent{node="n1"} 55.0' in text
