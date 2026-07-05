"""Prometheus metrics (ADR-016). Exposed at /metrics."""

from prometheus_client import Counter, Gauge, Histogram

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

NODES = Gauge("lycosa_nodes", "Nodes by status", ["status"])
NODE_CPU = Gauge("lycosa_node_cpu_percent", "Node CPU utilization", ["node"])
NODE_RAM = Gauge("lycosa_node_ram_percent", "Node RAM utilization", ["node"])
NODE_TASKS = Gauge("lycosa_node_running_tasks", "Node running tasks", ["node"])
