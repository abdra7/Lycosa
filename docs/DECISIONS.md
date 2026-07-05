# Architecture Decision Log

This file records notable architecture and process decisions for Lycosa, in
the order they were made. Append new entries at the bottom; do not edit or
renumber past entries — if a decision is later reversed, add a new entry
that supersedes it and note the supersession on the old one.

Format per entry: context, decision, consequences.

---

## ADR-001: Project conventions locked in at project start

**Date:** 2026-07-05

**Context:** Before any code is written, Lycosa needs a stable set of
conventions so that every subsequent sprint (and every future session,
human or agent) produces consistent, mergeable output rather than
re-deriving structure and style each time.

**Decision:**
- Monorepo with fixed top-level layout: `/backend`, `/agent`, `/dashboard`,
  `/infra`, `/docs`, `/scripts`.
- Trunk-based branching: `main` always deployable; work on
  `feat/<sprint>-<slug>` branches merged via PR.
- Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`,
  `refactor:`); one squash-friendly commit per completed phase.
- Backend stack pinned to Python 3.11+, `ruff` (lint + format), `pytest`,
  `pydantic` v2, async FastAPI, full type hints.
- API versioned under `/api/v1`; breaking changes get a new `/api/v2`
  prefix rather than mutating v1 in place.
- All config/secrets via environment variables, with a committed
  `.env.example` as the documentation of what exists; real `.env` files are
  gitignored.
- No phase is "done" without a green test run for that phase's scope.

**Consequences:** Every future phase plan is written against this
structure — new services go under `/backend`, node-side code under
`/agent`, UI under `/dashboard`. Introducing a new top-level directory,
changing the branching model, or bypassing `/api/v1` versioning requires a
new ADR here, not a silent deviation.

---

## ADR-002: Compose file lives in /infra, invoked with -f; env via root .env

**Date:** 2026-07-05

**Context:** Our layout puts all infrastructure config under `/infra`, but
Docker Compose conventionally expects `docker-compose.yml` at the repo root,
and `${VAR}` interpolation only reads a `.env` adjacent to the compose file.

**Decision:** Keep `docker-compose.yml` in `/infra` (invoked as
`docker compose -f infra/docker-compose.yml up`) rather than adding a
root-level compose file. Containers receive configuration via
`env_file: ../.env` (the single root `.env`), and the compose file avoids
`${VAR}` interpolation entirely so behavior doesn't depend on the invocation
directory. Grafana credentials therefore use Grafana's native
`GF_SECURITY_ADMIN_*` variable names directly in `.env`. Base images are
version-pinned (postgres 16, qdrant v1.14.1, prometheus v2.53.0,
grafana 11.1.0) for reproducible boots.

**Consequences:** One canonical `.env` at the root configures every service.
The longer `-f` command is documented in the README; Sprint 10's install
script will wrap it. If interpolation is ever needed, pass
`--project-directory .` or `--env-file .env` explicitly.

---

## ADR-003: JWT access tokens backed by server-side session records

**Date:** 2026-07-05

**Context:** FR-1 needs login/logout. Pure stateless JWTs cannot be revoked
on logout; pure DB sessions lose the self-describing token benefits.

**Decision:** Hybrid: login issues an HS256 JWT (PyJWT, `JWT_SECRET`,
expiry from `ACCESS_TOKEN_EXPIRE_MINUTES`) whose `jti` is stored hashed in
the `sessions` table. Every authenticated request validates signature +
expiry *and* that the session row is neither revoked nor expired. Logout
sets `revoked_at`, killing the token immediately.

**Consequences:** One DB lookup per authenticated request (acceptable at
LAN scale; a cache layer can absorb it later). Tokens are revocable,
auditable, and listable per user. No refresh tokens yet — sessions simply
expire; that's a backlog item if longer-lived dashboard sessions are needed.

---

## ADR-004: pwdlib (argon2id) for passwords; prefix+SHA-256 for API keys

**Date:** 2026-07-05

**Context:** Passlib is unmaintained and breaks with modern bcrypt.
API keys need constant-time verification without argon2's per-request cost.

**Decision:** User passwords are hashed with argon2id via `pwdlib`
(`PasswordHash.recommended()`). API keys use the format
`lyc_<prefix>_<secret>`; only the prefix (indexed lookup) and the SHA-256
of the full key are stored, compared with `hmac.compare_digest`. Keys carry
a role (typically `node`), optional node binding, expiry, and revocation.

**Consequences:** A leaked database exposes no usable credentials. API-key
auth is cheap (one hash) which matters for high-frequency node heartbeats
from Sprint 4 on. Key rotation = revoke + issue new.

---

## ADR-005: RBAC via a unified Principal; tests on SQLite, migrations on Postgres

**Date:** 2026-07-05

**Context:** Two credential types (user JWT, service API key) must flow
through one authorization model; the test suite must run without external
services (CI, contributor machines).

**Decision:** A single `Principal` (type, id, role) is produced by
`get_current_principal` regardless of credential type; routes guard with
`require_roles(...)`. Roles are DB rows (`admin`, `operator`, `node`)
seeded by `scripts/seed_admin.py`. Tests run against in-memory SQLite
(aiosqlite) using a `JSON().with_variant(JSONB, "postgresql")` column type;
Alembic migrations are written by hand and verified against real Postgres
(upgrade → downgrade → upgrade) in the compose stack.

**Consequences:** Later sprints add endpoints with one dependency line and
get RBAC + both auth paths for free. SQLite/Postgres divergence is
contained to the JSON variant and timezone normalization (`as_utc`); any
future Postgres-only feature (e.g. JSONB operators) needs integration
tests against the compose Postgres instead.

---

## ADR-006: Dashboard is a native Flutter Desktop app, not Flutter Web

**Date:** 2026-07-05

**Status:** Accepted (supersedes the Flutter Web assumption)

**Context:**
The dashboard was initially assumed to be Flutter Web served alongside the
backend. The SDD refers to a "Desktop Application" dashboard, and a native
desktop client better fits a LAN-first operator tool: OS-native windows/menus,
secure credential storage in the OS keychain, and no browser dependency.

**Decision:**
Build the dashboard as a native Flutter Desktop application targeting macOS,
Windows, and Linux, distributed as OS-native installers (.dmg / .msi /
.AppImage/.deb) via GitHub Releases, not as a Docker service.

**Consequences:**
- docker-compose runs the headless backend only (api, postgres, qdrant,
  prometheus, grafana). No dashboard container.
- The desktop app has no web origin, so it needs a first-run connection-setup
  screen where the operator enters the controller API URL and credentials,
  stored in local/secure app config. Multiple controller profiles supported.
- The API must allow remote/cross-origin desktop clients and keep exposing
  REST + WebSocket over the LAN.
- Release engineering gains a desktop build matrix (macOS/Windows/Linux) plus
  backend Docker image builds.
- Install becomes two-part: headless controller via compose + desktop app
  downloaded per-OS.

---

## ADR-007: Consistent error envelope on the API edge

**Date:** 2026-07-05

**Context:** FR-2 requires the gateway to return clear, machine-readable
errors; ad-hoc FastAPI defaults mix `{"detail": ...}` shapes.

**Decision:** Every non-2xx response uses
`{"error": {"code": "<machine_code>", "message": "<human text>", "details": [...]}}`.
Exception handlers cover HTTPException, request-validation errors (422 with
per-field `{"field", "message"}` entries), and a catch-all 500 that logs the
traceback but returns an opaque message.

**Consequences:** The desktop dashboard and agents parse one error shape.
Any handler raising HTTPException automatically conforms; internals never
leak to clients.

---

## ADR-008: In-process sliding-window rate limiting (v1)

**Date:** 2026-07-05

**Context:** The gateway needs rate limiting, but the controller currently
runs as a single api container.

**Decision:** A small in-process middleware keyed by API key (first 16
chars) or client IP, sliding window via monotonic-clock deque, configured by
`RATE_LIMIT_ENABLED/REQUESTS/WINDOW_SECONDS`. `/healthz`, `/docs`,
`/openapi.json` are exempt. 429 responses carry Retry-After and the ADR-007
envelope. No external dependency.

**Consequences:** Limits are per-process and reset on restart — acceptable
for the LAN single-container controller. Horizontal scaling requires
swapping the backing store for Redis behind the same middleware; that is a
known upgrade path, not a redesign.

---

## ADR-009: One API key = one node identity; idempotent re-registration

**Date:** 2026-07-05

**Context:** Nodes authenticate registration with API keys (FR-2). Reboots
and reinstalls must not create duplicate node rows.

**Decision:** `POST /nodes/register` requires an API key with the `node`
role. An unbound key creates a node and binds to it (`api_keys.node_id`,
201); a bound key updates its node's name/profile (200). Users cannot
register nodes. Normalized scheduling columns (cpu_cores, ram_gb, gpu_count,
max gpu_vram_gb, storage_gb, os_name) are derived from the raw JSONB profile
at registration time.

**Consequences:** Node identity is stable across restarts; revoking the key
severs the node's access. A machine wanting multiple logical nodes needs
multiple keys (intentional). Sprint 4's agent uses this same contract for
its startup registration.

---

## ADR-010: Rule-based node-role recommendation, rules in config, ML behind the same interface

**Date:** 2026-07-05

**Context:** FR-7 wants hardware profile → recommended role with a rationale
operators can trust. A learned model needs data we don't have yet.

**Decision:** v1 is a transparent rule engine: signals (gpu/ram/cpu/storage
tiers, LLM-runtime and vision hints) are derived from the profile, then each
role is scored 0–1 as the weight-fraction of matched conditions defined in
`backend/config/recommendation_rules.yml` (tunable without code changes;
ties resolve in config order). Output always includes the winning role,
confidence (= winning score), human-readable rationale, and *all six*
scores. The engine sits behind a `Recommender` protocol returned by
`get_recommender()`; registration recomputes the recommendation on every
(re-)register but never touches the operator-assigned `role` — accept =
PATCH role to the recommendation, override = PATCH to anything else, both
audited.

**Consequences:** Recommendations are explainable and tunable in the field.
A future ML recommender replaces the rule engine behind the same protocol
with no caller changes. Rule edits require an api-container restart (rules
are cached at first use); hot-reload is a backlog item if field-tuning
becomes frequent.

---

## ADR-011: Heartbeat liveness with a sweeper; agent exec API secured by a registration-time token

**Date:** 2026-07-05

**Context:** The controller must know which nodes are alive (FR-2/FR-4) and
must later dispatch tasks to agents (Sprint 5). Agents sit behind the
controller's API-key auth, but the reverse direction — controller calling
the agent — needs its own credential.

**Decision:**
- **Liveness:** agents POST `/api/v1/nodes/heartbeat` (bound node key) every
  15 s with live metrics; the response echoes the interval so it is
  controller-tunable. The latest metrics snapshot and `last_heartbeat_at`
  live on the node row. A lifespan background sweeper flips nodes to
  `offline` when the last heartbeat is older than `HEARTBEAT_TIMEOUT_SECONDS`
  (45 s = 3× interval). Only transitions are audited (`node.online` /
  `node.offline`), not every heartbeat.
- **Agent exec API:** at startup the agent generates a random token, serves
  its execution API (uvicorn, port 8010) requiring `X-Agent-Token`, and
  sends `agent_url` + `agent_token` in its registration payload. The
  controller stores the token retrievable (not hashed) because it must
  replay it verbatim when dispatching; it is never exposed via any API
  response. A new token is generated on every agent restart and re-registered.
- **Runtime adapters:** the agent's executor talks to a `RuntimeAdapter`
  protocol; Ollama is the first implementation, llama.cpp/HF slot in later.

**Consequences:** Node health is visible within one heartbeat interval and
outages within ~1 minute, with an audit trail. The plaintext-in-DB agent
token is an accepted LAN-scope trade-off: compromise of the controller DB
already implies fabric compromise. Hardening path (backlog): mTLS or an
enrollment handshake per node, plus per-dispatch nonces. Metrics history is
not retained on the controller (only the latest snapshot); Prometheus owns
time series in Sprint 9.

---

## ADR-012: Synchronous task dispatch v1; keyword classifier; ordered-candidate failover

**Date:** 2026-07-05

**Context:** FR-5 needs submit → classify → schedule → dispatch → result with
failover. A distributed queue adds operational surface the LAN v1 doesn't
need yet.

**Decision:**
- **Sync dispatch:** `POST /api/v1/tasks` executes within the request and
  returns the finished task (per-attempt timeout
  `TASK_DISPATCH_TIMEOUT_SECONDS`, default 120 s; at most
  `TASK_MAX_ATTEMPTS` nodes tried). Every attempt is persisted as a
  TaskExecution row *before* the network call, so traces survive crashes.
- **Classifier:** explicit `type` wins; otherwise ordered keyword rules map
  the prompt to coding/retrieval/vision/tool, defaulting to general. Each
  type has a role-preference list consumed by the scheduler. An LLM-based
  classifier can replace `classify()` behind the same signature.
- **Scheduler:** candidates = online nodes with agent contact info whose
  effective role (assigned, else recommended) is in the preference list;
  scored by role rank (dominant), RAM/VRAM, model availability bonus, minus
  cpu/running-task load. Returns an ordered list; the orchestrator walks it
  for failover — agent-down and agent-reported failures both advance to the
  next candidate.

**Consequences:** Results are immediate and the trace is complete
(queued/assigned/started/finished timestamps + per-node attempts). Long
tasks hold an HTTP request open — acceptable on LAN, and Sprint 7's workflow
engine calls the same `submit_task` per step. Upgrade path: a queue/worker
pool behind the same endpoint returning 202 + polling, without schema
changes. Nodes with an unassigned role are schedulable via their
recommendation by design (fresh fabric works out of the box).

---

## ADR-013: Knowledge plane — controller-hosted Qdrant, pluggable embedders, federated-lite router

**Date:** 2026-07-05

**Context:** FR-6/FR-9 need ingest → embed → retrieve where callers never
name a node, designed so multiple knowledge nodes can federate later. The
controller must not require a GPU or huge ML dependencies.

**Decision:**
- **Layout:** metadata in Postgres (KnowledgeCollection, Document,
  EmbeddingJob trace, RetrievalRequest audit); vectors in Qdrant, one Qdrant
  collection per KnowledgeCollection (`kc_<uuid>`), chunk text kept in point
  payloads. `KnowledgeCollection.node_id` exists (null = controller-hosted)
  so multi-node ownership needs no schema change.
- **Embedders** behind an `Embedder` protocol, chosen by
  `EMBEDDING_BACKEND`: `hashing` (default — deterministic bag-of-words
  projection, 384-dim, zero downloads, keyword-level relevance, hermetic
  tests) and `fastembed` (ONNX MiniLM, CPU-only, semantic; `[embeddings]`
  extra, ~90 MB model on first use). Each collection records its backend;
  queries are embedded with the collection's own backend. Switching a
  collection's backend requires re-ingestion (backlog: re-embed job).
- **Ingestion** is synchronous per upload (consistent with ADR-012), 20 MB
  cap, pypdf for PDFs, paragraph-aware ~800-char chunks; failures land on
  the document row (`failed` + error), never a 500.
- **Router:** named collection scopes the search; otherwise federated-lite —
  search every collection, merge by score (stable sort keeps freshest
  collection first on ties), top-k. Returns chunks + assembled
  `context_text`. Every retrieval is recorded (query, latency, count,
  requester).
- **Orchestrator:** `knowledge_query` (explicit) or retrieval-type tasks
  (implicit, via prompt) trigger retrieval; context is prepended to the
  dispatched prompt. Agents receive text only — storage location never
  leaks (FR-9). Retrieval failure degrades to dispatch-without-context.

**Consequences:** RAG works out of the box with zero model downloads;
semantic quality is one env var away. Qdrant in-memory mode lets unit tests
exercise real vector-search code. Real federation later = implement the
router interface over per-node Qdrant instances; API contract unchanged.

---

## ADR-014: Workflow engine — declarative steps, template context, pause-at-approval under sync execution

**Date:** 2026-07-05

**Context:** FR-8 needs multi-step orchestration (the SDD
planner→coder→test→review flow) with branching, retries, parallelism,
shared context, human approval, and full traces — without introducing a
worker/queue runtime yet (ADR-012).

**Decision:**
- **Definitions** are declarative JSON validated at creation (pydantic
  discriminated union). Step kinds: `task` (runs through the Phase-5
  orchestrator — scheduling, failover, knowledge injection included),
  `retrieve` (Knowledge Router), `approval`, `parallel` (task substeps via
  asyncio.gather, each on its own DB session).
- **Context propagation** is `{{input}}` / `{{steps.<id>.output}}` template
  substitution; references and `when` clauses may only point at earlier
  steps, enforced at creation time. Branching is `when {step,
  contains|equals}` — unmet ⇒ step recorded `skipped`. Per-step `retries`
  re-submits the task; every attempt is a WorkflowStepRun row, and task
  steps link `task_id` to the full Phase-5 execution trace.
- **Approval under sync execution:** a run executes inside the request
  until an approval step, persists `paused` + position, and returns; the
  approve endpoint resumes execution from the next step inside its own
  request (reject ⇒ run failed). Paused/approved/rejected/finished are all
  audited.

**Consequences:** No new runtime infrastructure; the dashboard can render
live status from WorkflowRun/StepRun rows at any point, including while
paused. Long chains hold an HTTP request open — same trade-off and same
queue-based upgrade path as ADR-012, unchanged API contract. Parallel
substep concurrency is per-branch sessions, which required a runtime
session-factory seam (`get_runtime_sessionmaker`) also usable by future
background work. `when` is deliberately a two-operator matcher, not an
expression language.

---

## ADR-015: Dashboard foundation — Riverpod, hand-written API client, keychain profiles, state-driven navigation

**Date:** 2026-07-05

**Context:** ADR-006 made the dashboard a native Flutter Desktop app that
must manage controller connections itself (no web origin): first-run setup,
multiple controller profiles, secure credential storage.

**Decision:**
- **State management: Riverpod** — providers are compile-safe, overridable
  in tests (the widget tests swap the profile store and API client factory
  with fakes), and carry no BuildContext coupling.
- **API client: hand-written typed client** over `package:http`. The API
  surface is small and versioned; OpenAPI codegen would add a toolchain for
  little gain. All non-2xx responses parse the ADR-007 envelope into
  `ApiException` (code, message, per-field details, `friendly` text);
  transport failures become `ControllerUnreachableException`.
- **Profiles & secrets:** `ControllerProfile` (name, URL, token) list plus
  active-profile id in the OS keychain via `flutter_secure_storage`
  (Windows Credential Manager / macOS Keychain / libsecret), behind a
  `ProfileStore` interface with an in-memory test double.
- **Navigation is session-state-driven** (no router package): a root gate
  renders setup → login → shell from the Riverpod session state. Restoring
  a profile validates its token against `/me`; 401 wipes the stale token
  and drops to login. Deep links can motivate go_router later.
- **Live data:** REST polling until Sprint 9's WebSocket event stream,
  which will feed the same providers.

**Consequences:** Feature screens (8b–8d) consume `activeApiClientProvider`
and inherit auth, profile switching, and error shaping for free. Windows
dev builds require OS Developer Mode (Flutter plugin symlinks) — documented
in dashboard/README.md. CI runs `flutter analyze` + `flutter test` on
Ubuntu; desktop release builds are Sprint 10's release-matrix problem.

---

## ADR-016: Observability — JSON logs with correlation ids, Prometheus metrics, in-process WebSocket event bus

**Date:** 2026-07-05

**Context:** NFR-8 requires the platform to be observable by default:
structured logs, metrics, dashboards, alerts, and a live event stream the
desktop dashboard can render without polling.

**Decision:**
- **Logging:** stdlib-only JSON formatter on the root logger (uvicorn
  loggers propagate through it). Correlation ids live in contextvars —
  HTTP middleware sets `request_id` (echoed as X-Request-ID), the
  orchestrator sets `task_id`, the workflow executor sets
  `workflow_run_id` — so every log line in a dispatch carries its ids.
- **Metrics:** prometheus-client mounted at `/metrics`: HTTP
  rate/duration by route template (template, not raw path, to bound
  cardinality), task totals/duration/failovers, retrieval latency,
  workflow run/step counters, node-status gauge, and per-node cpu/ram/task
  gauges fed by heartbeats. Prometheus scrapes the api service; alert
  rules (NodeOffline, WorkflowFailures, RetrievalLatencyDegraded,
  ApiErrorRateHigh) and two Grafana dashboards (System Overview, Node
  Health) are provisioned as code in /infra.
- **Events:** an in-process EventBus (bounded per-connection queues; slow
  consumers drop rather than block) feeds `WS /api/v1/events`,
  authenticated with the same JWT+session validation as REST (token via
  query param for WS friendliness). Services publish node.connected/
  disconnected/metrics.updated, task.started/finished,
  workflow.started/step.completed/paused/finished, and alert.created
  (node offline, workflow failed). The Flutter shell subscribes with
  auto-reconnect: alert snackbars, a live status strip, and push-driven
  invalidation of the node list (polling stays as fallback).

**Consequences:** One `docker compose up` now includes working dashboards
and alerts, and the desktop app reacts to fabric changes in real time.
The event bus is single-process like ADR-008's rate limiter — multi-node
control planes would swap in a broker behind the same publish/subscribe
seam. Prometheus alerting has no Alertmanager (rules are visible in the
Prometheus UI; operator-facing alerts arrive via the WS event) — routing
to email/chat is a backlog item.
