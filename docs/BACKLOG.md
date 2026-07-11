# Backlog

- As an operator, I want to remove a decommissioned node (DELETE
  /api/v1/nodes/{id} + dashboard action) so that stale nodes don't clutter
  the inventory. (Found during 8b live verification: a Phase-2 test node
  lingered as "registered" forever; no removal path exists.)
- Re-embed job: switching a knowledge collection's embedding backend
  requires re-ingestion (ADR-013).
- Redis-backed rate limiting when the API scales horizontally (ADR-008).
- Hot-reload of recommendation rules without api restart (ADR-010).
- Async task queue behind POST /tasks returning 202 + polling (ADR-012).
- Shield synchronous workflow runs from client disconnects the same way
  POST /tasks is (see Ticket #102 fix); long runs cancelled by a dashboard
  timeout currently stay "running" forever.
- mTLS / enrollment handshake for agent exec API hardening (ADR-011).
- Tech-debt: clear Starlette deprecation warnings before the next dependency
  bump — `HTTP_422_UNPROCESSABLE_ENTITY` → `HTTP_422_UNPROCESSABLE_CONTENT`, and
  the `starlette.testclient`/httpx warning. Surfaced by the v0.2.0 Phase 2 node
  validation run (230 tests green; warnings only, non-blocking).

- v0.2.0 Phase 3 (Workflow Engine Validation) findings — engine is green
  (19 tests: sequential SDD planner→coder→test→review chain, parallel substeps,
  branching/skip, retries, approval pause/resume, retrieve→task; traces persisted
  in `workflow_runs`/`workflow_step_runs`). Gaps vs the v0.2.0 named scenarios,
  none blocking:
  - No `tool`/web-search or DB-query step kind, so "Autonomous Researcher"
    (web search) and "Memory-Enhanced Agent" (search audit logs / query VLM)
    can't be expressed as workflows yet. Add a tool step kind or descope.
  - No document-ingestion step kind: "Ingest-to-Retrieval" can retrieve→answer
    (tested) but PDF ingestion runs via the knowledge upload endpoint, not as a
    workflow step. Decide whether ingestion should be a step.
  - Nesting is one level (parallel substeps only); no sub-workflow step kind.
  - The playbook's "30 distinct workflow scenarios" stress matrix is not built
    as automated tests — current 19 tests cover every shape primitive. Decide
    whether to author the full scenario matrix.

- v0.2.0 Phase 4 & 5 (RAG & Knowledge Retrieval) findings — knowledge plane is
  green (59 tests: markdown + PDF ingest, chunking, routing without naming a
  node, top-k, scoping, empty-fabric returns empty, retrieval audit, corrupt/
  encrypted/empty-doc and Qdrant-outage error paths, E2E upload→hash→vectorize→
  retrieve, ingestion-recovery, upload concurrency). Gaps, none blocking:
  - ~~Uncertainty admission not enforced~~ **FIXED (ADR-019)** — grounding
    instruction + refusal short-circuit + tunable `RETRIEVAL_MIN_SCORE` added to
    the orchestrator/router; 205 backend tests green incl. 2 new grounding tests.
    Live re-verification needs an api image rebuild + restart when redeployed.
  - CSV and JSON ingest as plain UTF-8 text (loader parses PDF via pypdf, all
    else as text) — searchable but not structure-aware (no row/field parsing).
    Add structured loaders if field-level retrieval is needed.
  - Semantic precision/recall depends on the `fastembed` backend; the default
    `hashing` embedder is keyword-level only (ADR-013). Benchmark accuracy on
    `fastembed` before claiming semantic quality.

- v0.2.0 Phase 6 (Concurrency Stress) findings — ran 100/500/1000/5000 concurrent
  on a throwaway stack. Controller was **stable**: zero 5xx, flat ~106 MB memory
  (no leak), instant recovery (13 ms), and the in-process rate limiter correctly
  shed excess auth-endpoint load with 429s (ADR-008). Improvements to file as
  issues (see `scripts/file_v020_qa_issues.sh`):
  - Run the controller with multiple uvicorn/gunicorn workers — it serves from a
    single worker, so throughput was ceiling-bound and latency grew linearly with
    concurrency (CPU never saturated). Depends on Redis-backed rate limiter since
    the in-process limiter is per-worker.
  - Use a real distributed load tool (hey/k6/wrk) from separate hardware for
    accurate capacity numbers — the Phase 6 client was single-process on the same
    laptop, so its ~60 req/s ceiling was client/loopback-bound, not the
    controller (which sat at ~14% CPU).

- v0.2.0 Phase 7 (Security & Vulnerability, static/code-level) findings — mostly
  clean. Confirmed NOT vulnerable: SQL injection (ORM-only, parameterized), path
  traversal on uploads (filenames never used as fs paths; storage keyed by
  collection UUID), command injection (Ollama HTTP API not shell; only subprocess
  is fixed-arg `nvidia-smi`), authz/IDOR (every route role-guarded; node keys
  restricted to their own node via `api_key.node_id`), agent exec token
  (constant-time `hmac.compare_digest`), unsafe deserialization (`yaml.safe_load`
  only). Issues to file (see `scripts/file_v020_qa_issues.sh`, label security):
  - ~~Rate-limit bypass via a rotating/bogus `X-API-Key` header~~ **FIXED in
    v0.2.1 (F-2, ADR-020, issue #6)** — limiter now keys on client IP only, so a
    forged/rotating header can't spawn a fresh bucket. Reproduced live then
    closed with a regression test.
  - **Fail-fast on default secrets in production** — placeholder `JWT_SECRET`
    (HS256) allows forged admin tokens; refuse to boot when
    `ENVIRONMENT=production` and secrets match known insecure defaults.
  - **RAG prompt-injection/poisoning** trust boundary — gated by operator-only
    upload; document it, harden if untrusted upload is ever enabled.
  - NOTE (not filed, config not code, `.env` left untouched per instruction):
    the live dev stack currently runs with placeholder `JWT_SECRET`,
    `POSTGRES_PASSWORD`, and Grafana password — fine on a trusted LAN, must be
    rotated before any exposure.
  - Active probing (live fuzzing/injection against a running API) NOT done —
    invasive, needs throwaway stack + explicit `go`.

- v0.2.0 Phase 8 & 9 (Failure Recovery) findings — done live on a throwaway
  stack (sim-killing datastores mid-operation). **Resilience is excellent, no
  defects:** Postgres down -> clean 500 in ~4s, no crash; Postgres restart ->
  pool auto-recovers in ~2s with no API restart; Qdrant down -> retrieve returns
  clean 500 (~4s) and upload marks the document `failed` with an actionable
  "is the qdrant service running?" error (never a 500/hang); Qdrant restart ->
  retrieve + upload recover automatically; task dispatch with no online node ->
  task `failed` with a clear actionable error. API container: 0 restarts through
  all fault injection, `/healthz` stayed 200. Agent "planning" resilience:
  workflow DAGs are acyclic by construction (steps reference only earlier steps,
  enforced at creation) and scheduler failover is bounded by `TASK_MAX_ATTEMPTS`
  — no infinite loops possible by design (Phase 3 + scheduler tests). Only
  enhancement filed: return 503 (not opaque 500) on datastore outage.
  (Note: a mid-test "ingestion hang" was a false alarm — Windows `curl.exe`
  can't open git-bash `/tmp/` paths, so multipart uploads never sent; with a
  real path, uploads work in 0.5s. No such bug exists.)
