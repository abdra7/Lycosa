"""Prometheus metrics (ADR-016). Exposed at /metrics.

With several uvicorn workers (ADR-028) every worker keeps its own values, so
a scrape would only see the worker that served it. When
PROMETHEUS_MULTIPROC_DIR is set (the image sets it; the launch command
empties it before the workers start), prometheus_client keeps values in
shared files and /metrics aggregates all workers.
"""

import os

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    make_asgi_app,
    multiprocess,
)

MULTIPROC_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "")
if MULTIPROC_DIR:
    # metrics write their files on creation, below
    os.makedirs(MULTIPROC_DIR, exist_ok=True)

HTTP_REQUESTS = Counter(
    "lycosa_http_requests_total",
    "HTTP requests by method, route template, and status code",
    ["method", "path", "status"],
)
HTTP_DURATION = Histogram(
    "lycosa_http_request_duration_seconds",
    "HTTP request duration by route template",
    ["method", "path"],
)

TASKS_TOTAL = Counter("lycosa_tasks_total", "Tasks by type and final status", ["type", "status"])
TASK_DURATION = Histogram(
    "lycosa_task_duration_seconds",
    "Task wall time from submission to finish",
    ["type"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)
TASK_FAILOVERS = Counter("lycosa_task_failovers_total", "Dispatch attempts beyond the first")

RETRIEVALS_TOTAL = Counter("lycosa_retrievals_total", "Knowledge retrievals")
RETRIEVAL_DURATION = Histogram(
    "lycosa_retrieval_duration_seconds",
    "Knowledge router retrieval latency",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)

WORKFLOW_RUNS = Counter("lycosa_workflow_runs_total", "Workflow runs by final status", ["status"])
WORKFLOW_STEPS = Counter(
    "lycosa_workflow_steps_total", "Workflow steps by kind and status", ["kind", "status"]
)

# Universal LLM layer (ADR-031). Provider-level labels only: per-model labels
# would be unbounded (aggregators list hundreds of models).
LLM_REQUESTS = Counter(
    "lycosa_llm_requests_total",
    "LLM provider calls by provider and outcome",
    ["provider", "status"],
)
LLM_LATENCY = Histogram(
    "lycosa_llm_request_duration_seconds",
    "LLM provider call latency including retries",
    ["provider"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 40, 80, 160),
)
LLM_TOKENS = Counter(
    "lycosa_llm_tokens_total", "LLM tokens by provider and kind", ["provider", "kind"]
)
LLM_FALLBACKS = Counter(
    "lycosa_llm_fallbacks_total", "Route fallbacks away from a failing provider", ["from_provider"]
)

# Gauges are written by whichever worker handles the heartbeat or sweep:
# across workers the most recent write is the current value.
NODES = Gauge("lycosa_nodes", "Nodes by status", ["status"], multiprocess_mode="mostrecent")
NODE_CPU = Gauge(
    "lycosa_node_cpu_percent", "Node CPU utilization", ["node"], multiprocess_mode="mostrecent"
)
NODE_RAM = Gauge(
    "lycosa_node_ram_percent", "Node RAM utilization", ["node"], multiprocess_mode="mostrecent"
)
NODE_TASKS = Gauge(
    "lycosa_node_running_tasks", "Node running tasks", ["node"], multiprocess_mode="mostrecent"
)


def metrics_app():
    """The /metrics ASGI app: every worker's samples in multiprocess mode,
    else this process's default registry."""
    if not MULTIPROC_DIR:
        return make_asgi_app()
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return make_asgi_app(registry=registry)
