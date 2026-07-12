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

---

## ADR-017: Packaging & release — install scripts, GHCR image, tag-driven installer matrix

**Date:** 2026-07-05

**Context:** Sprint 10 must make Lycosa installable from GitHub by someone
who has never seen the repo: a headless controller (ADR-002's compose stack),
a desktop app with per-OS installers (ADR-006), and a joinable agent — plus
a repeatable way to cut releases.

**Decision:**
- **Controller install:** `scripts/install.sh` (bash) and `scripts/install.ps1`
  (PowerShell) wrap the documented compose command. Contract: verify
  Docker/Compose v2/git → clone if run standalone (`curl | bash` supported;
  prompts read from `/dev/tty`) → generate `.env` from `.env.example` with
  random `JWT_SECRET`/`POSTGRES_PASSWORD`/Grafana password → prompt for admin
  credentials (env overrides `LYCOSA_ADMIN_EMAIL`/`LYCOSA_ADMIN_PASSWORD` for
  non-interactive use; password generated if skipped) → `compose up --build -d`
  → poll `/healthz` → print the LAN controller URL. Re-runs are idempotent:
  an existing `.env` is never regenerated.
- **Release pipeline:** pushing a `v*` tag runs `.github/workflows/release.yml`,
  which (a) builds and pushes `ghcr.io/abdra7/lycosa-backend:<version>` +
  `:latest`, (b) builds desktop installers on a 3-OS matrix — macOS `.dmg`
  via `hdiutil` (no extra tooling), Windows per-user `.exe` via Inno Setup
  (`dashboard/installers/windows/lycosa.iss`; VC++ runtime DLLs bundled;
  chosen over `.msi` for far simpler tooling at equal operator value), Linux
  `.AppImage` + `.tar.gz` (AppImage covers the ".deb" intent with one
  distro-agnostic artifact) — and (c) creates the GitHub Release with notes
  extracted from the version's `CHANGELOG.md` section.
- **Versioning:** single source per component — backend version comes from
  package metadata (`app/version.py`, surfaced in `/healthz` and OpenAPI),
  dashboard from `pubspec.yaml` (mirrored in `lib/core/app_info.dart` for
  display). Components share one release tag; `CHANGELOG.md` follows
  Keep-a-Changelog with an `Unreleased` section.
- Installers are unsigned in v0 (macOS Gatekeeper right-click-open, Windows
  SmartScreen "run anyway") — code signing requires paid certificates and is
  deferred until distribution warrants it.

**Consequences:** A release is exactly `git tag v0.x.0 && git push --tags`;
everything publishable is built by CI, nothing on a laptop. The install
story is the README's front door: controller one-liner, per-OS desktop
download, dashboard-minted agent join command. Unsigned binaries will show
OS warnings — documented, and the first thing to revisit if adoption grows.

---

## ADR-018: LAN discovery — agents advertise over mDNS, the desktop dashboard scans

**Date:** 2026-07-06

**Context:** Operators expected LAN devices running `lycosa-agent` to show up
in the dashboard automatically (Sprint 11 Ticket #103); v0.1.0 only had the
manual minted-key join flow, and an agent that was running but unregistered
was invisible. Discovery needs multicast, but the controller runs inside a
bridge-network Docker container that LAN multicast (UDP 5353) never reaches,
so controller-side scanning would silently find nothing in the default
install.

**Decision:** Discovery is split by where multicast actually works:

- Each agent advertises a `_lycosa-agent._tcp.local.` DNS-SD service
  (python `zeroconf`, TXT records: node name + agent version, port = exec
  API). Advertising is best-effort — if multicast is unavailable the agent
  logs a warning and continues; `LYCOSA_DISCOVERY_ENABLED=false` opts out.
- The desktop dashboard — a native app on the operator's LAN — performs the
  scan (`multicast_dns` package) behind a "Discovered on LAN" panel on the
  Nodes screen, on explicit operator action (no unprompted multicast
  traffic), and flags discovered agents whose name has no registered node.
- Discovery is advisory only: registration still requires the minted-key
  join flow (ADR-005/ADR-011 security model is unchanged; no auto-join).
- The scan function sits behind a provider seam (`lanScanProvider`) so
  widget tests inject fakes — tests stay hermetic per the maintenance
  guardrails.

**Consequences:** Zero-config visibility of agent-running machines wherever
mDNS works, with no new controller surface area or auth changes. Networks
that block UDP 5353 degrade to the documented manual flow (README's port
table covers 8000/8010/5353). SSDP was rejected: DNS-SD is the standard for
service discovery on modern LANs and both ecosystems (python zeroconf, Dart
multicast_dns) are mature. Controller-side discovery can be revisited if the
controller ever runs with host networking.

---

## ADR-019: Grounded knowledge tasks — inject a grounding instruction, refuse when no relevant context

**Date:** 2026-07-11

**Context:** v0.2.0 Phase 4 & 5 validation, run live against a real Ollama
`llama3.2:1b` node, showed the RAG path could hallucinate: a neutral,
out-of-scope prompt ("In which city is the Lycosa headquarters located?" — not
in any document) made the model fabricate "Dallas, Texas" and falsely attribute
it to the retrieved context. Two platform causes: (1) the Knowledge Router had
no relevance threshold, so it always returned top-k chunks even when the best
match was irrelevant (with the default `hashing` embedder an out-of-scope query
even scored *higher*, 0.40, than an in-scope one, 0.23 — bag-of-words overlap on
common words); (2) nothing instructed the LLM to answer only from context, so
grounding happened only if the operator's own prompt said so. FR-6/FR-9 and the
v0.2.0 playbook require the model to admit uncertainty ("I cannot answer this
based on the retrieved knowledge.") when the answer is not in the sources.

**Decision:**
- **Grounding instruction:** when a task retrieves knowledge and finds relevant
  context, the orchestrator wraps the dispatched prompt with an instruction to
  answer using ONLY the retrieved context and, if the answer is absent, to reply
  with the exact sentence `GROUNDED_REFUSAL`. The instruction is embedder- and
  model-agnostic — it works even when scores are unreliable.
- **Refusal short-circuit:** when retrieval *succeeds* but yields no relevant
  chunks, the orchestrator returns `GROUNDED_REFUSAL` directly (task
  `succeeded`, `result.grounded=false`, no node, no LLM call) instead of
  dispatching an ungrounded prompt. A retrieval *infra failure* (e.g. Qdrant
  down) is explicitly distinguished and still degrades to dispatch-without-
  context — an outage is not an out-of-scope question.
- **Tunable threshold:** `RETRIEVAL_MIN_SCORE` (default `0.0` = off, so the
  public `/retrieve` API is unchanged) filters low-score chunks before they
  reach an LLM; the task path passes it in. It is mainly useful with the
  `fastembed` backend, whose scores are semantically meaningful; the grounding
  instruction is the guarantee that does not depend on score calibration.

**Consequences:** Knowledge-driven tasks no longer confidently invent answers
for out-of-scope questions; in-scope grounded answers are unchanged (verified
live: returned "Tarantula-7" and "42 nodes" from the ingested doc). The refusal
is identical whether the LLM emits it or the orchestrator short-circuits it.
Non-knowledge tasks are untouched. The `hashing` embedder's scores remain
unreliable for thresholding — semantic precision/recall still wants `fastembed`
(tracked as a separate backlog/issue). No API contract change.

---

## ADR-020: Rate limiter keys on client IP only (fixes the X-API-Key bypass)

**Date:** 2026-07-11

**Status:** Accepted (refines ADR-008)

**Context:** ADR-008's in-process limiter keyed each bucket on the presented
`X-API-Key` header (first 16 chars), falling back to client IP only when absent.
v0.2.0 Phase 7 active fuzzing confirmed this is bypassable (finding F-2, issue
#6): because the limiter runs before authentication and never validates the key,
a caller can send a **different arbitrary `X-API-Key` on each request** to get a
fresh bucket every time — escaping the limit entirely, including credential
brute-force throttling on `/api/v1/auth/login` (150 attempts with a rotating
header → 0 × 429, vs 30 × 429 normally).

**Decision:** Key every rate-limit bucket on **client IP only**; ignore the
`X-API-Key` header for limiting. A forged/rotating header can no longer spawn a
new bucket. At LAN scope each node has its own IP, so an IP bucket still yields
effectively per-node budgets. The bucket store is now module-level with a
`reset_rate_limit()` helper so the process-wide middleware singleton can be
isolated between tests.

**Consequences:** The brute-force/DoS bypass is closed. Multiple logical clients
behind a single IP (NAT, or a reverse proxy that doesn't forward the real IP)
now share one bucket — acceptable at LAN scope, and the documented upgrade path
is unchanged: a Redis-backed store plus trusted-proxy `X-Forwarded-For` handling
when the controller runs behind a proxy or scales horizontally. No API contract
change; the 429 envelope and `Retry-After` header are unchanged.

---

## ADR-021: Automated dependency updates via Dependabot; `main` protected by a ruleset

**Date:** 2026-07-11

**Context:** Dependency freshness was manual (the maintenance guardrails call
for a CVE audit each release, but nothing ran between releases), and `main`
had no enforcement behind the "tests gate merges" convention — GitHub showed
the "branch not protected" warning, and nothing technically stopped an
un-reviewed merge or force-push.

**Decision:**
- **Dependabot version updates** (`.github/dependabot.yml`): weekly Monday
  scans across six surfaces — `pip` for `/backend` and `/agent`
  (PEP 621 `pyproject.toml`), `pub` for `/dashboard`, `docker` for
  `backend/Dockerfile`, `docker-compose` for `infra/docker-compose.yml` image
  tags, and `github-actions`. Minor/patch bumps are grouped into one PR per
  package; majors arrive individually. Commit prefixes follow our convention
  (`chore(deps)` / `chore(ci)`); PRs are labeled per area. Dependabot
  **security alerts + security updates** are enabled repo-side, so CVE-driven
  PRs arrive immediately regardless of the weekly schedule.
- **Branch protection** via a `protect-main` ruleset: no deletion or
  force-push; merges into `main` require a PR with all three CI checks green
  (Backend / Dashboard / Agent lint + test). The repository-admin role has an
  always-on bypass so the solo-maintainer direct-push workflow (phase commits)
  keeps working; Dependabot and feature branches are fully gated. Repo-level
  auto-merge is enabled.
- **First scan triage (same day):** merged the Actions group (checkout v7,
  setup-python v6, artifact v7/v8, gh-release v3), Prometheus v2.53 → v3.13
  (our `prometheus.yml` is static scrape configs — v3-compatible),
  Grafana 11 → 13, Qdrant v1.14.1 → v1.18.2, and the backend base image
  python 3.11-slim → 3.14-slim (validated by a local image build + in-container
  `import app.main` smoke test, since CI does not build the Dockerfile).
  **Rejected Postgres 16 → 18** (`@dependabot ignore this major version`):
  an existing `postgres_data` volume written by 16 cannot start under 18
  without a dump/restore or `pg_upgrade` migration — deferred until that
  migration is planned (backlog).

**Consequences:** ADR-002's image pins are no longer frozen at their original
versions; Dependabot proposes bumps and CI + review gate them (ADR-002's
pin-for-reproducibility intent stands — versions are still exact, just
maintained). Two costs surfaced immediately: the flaky
`test_client_disconnect_does_not_lose_completion` test now blocks merges when
it trips on unrelated PRs (deflake is on the backlog), and running deployments
must rebuild/pull images (`docker compose up -d --build --pull always`) to
actually receive merged bumps. Postgres stays on 16 until a migration story
exists.

## ADR-022: Zero-configuration startup — layered compose env defaults, first-run generated secrets, production fail-fast

**Date:** 2026-07-12

**Context:** v0.3.0's release goals include "clone → run" with no manual
configuration. Until now `docker compose -f infra/docker-compose.yml up`
hard-required a root `.env` (the compose `env_file` was mandatory), the
backend shipped placeholder secrets (`change-me`, an insecure JWT fallback)
that nothing stopped from reaching production (issue #7), every container
received the entire `.env` (Grafana could read the DB password and JWT
secret), Postgres/Qdrant/Prometheus ports were published to the whole LAN
(Qdrant unauthenticated — a direct bypass of API auth), no service had a
restart policy, and container logs grew unbounded.

**Decision:**
- **Layered compose env files:** each service reads a committed
  `infra/compose-defaults.env` first, then an *optional* root `.env`
  (`required: false`; later file wins — verified against Compose v5.1.4).
  Fresh clones run with zero configuration; the installer-generated or
  hand-written `.env` overrides everything, so existing installs behave
  unchanged. Grafana only reads the optional `.env` — it has no use for the
  DB defaults.
- **First-run generated secrets** (`app/core/bootstrap.py`): when
  `JWT_SECRET` or `DEFAULT_ADMIN_PASSWORD` are unset/placeholder,
  `get_settings()` generates strong values and persists them to
  `<DATA_DIR>/runtime-secrets.json` (0600 where supported; `DATA_DIR`
  defaults to `./data`, a named `api_data` volume in Docker). The seed
  script prints a generated admin password exactly once, on admin creation,
  in the api logs. Because both the seed process and the API read the same
  persisted file, they always agree.
- **Production fail-fast (closes #7):** with `ENVIRONMENT=production` the
  API refuses to start when the database password is a known default
  (`change-me`, `lycosa`, `postgres`, empty) or — defense-in-depth, normally
  auto-generated first — JWT/admin secrets are placeholders. The committed
  compose default DB password (`lycosa`) is deliberately in the deny list.
- **Network posture:** Postgres (5432), Qdrant (6333/6334), and Prometheus
  (9090) now bind to `127.0.0.1` — reachable for local tooling, not the LAN.
  Only the API (8000) and Grafana (3001) stay LAN-exposed; Grafana reaches
  Prometheus over the internal network. `QDRANT_API_KEY` is now plumbed
  through the backend client for operators who enable Qdrant auth.
- **Operational hardening:** `restart: unless-stopped` and json-file log
  rotation (10 MB × 3) on every service; Grafana waits for a *healthy*
  Prometheus.
- The bare-metal fallback `DATABASE_URL` default password changed from
  `change-me` to `lycosa` to match the compose default, so a backend run
  outside Docker connects to the zero-config Postgres out of the box.

**Consequences:** `.env.example` is now override documentation rather than a
required first step; install scripts still generate a full production-grade
`.env` and continue to work unchanged. Restarting the api container no longer
invalidates sessions in zero-config mode (the JWT secret persists in the
`api_data` volume) — but deleting that volume rotates the JWT secret and the
generated admin password credential of record. A user who ran the stack with
a `.env` and later deletes it keeps the old Postgres password inside
`postgres_data` while the api now expects the default — that mismatch needs
either the `.env` restored or the volume re-initialised. Residual gaps
logged to the backlog: per-service env scoping still ships the full `.env`
to postgres/api/grafana when one exists, Qdrant auth is opt-in rather than
auto-generated (it is localhost-bound by default), and TLS remains
undocumented for LAN deployments. Tests pin `JWT_SECRET` /
`DEFAULT_ADMIN_PASSWORD` in `tests/__init__.py` so the suite never triggers
generation.

## ADR-023: Per-IP brute-force throttle on /auth/login

**Date:** 2026-07-12

**Context:** v0.3.0's security probe (playbook Step 2) reviewed the auth
boundary for brute-force vectors. The global rate limiter (ADR-008/020) keys
on client IP and covers `/auth/login`, and Argon2 makes each password check
deliberately slow — but the default global budget (120 requests/60 s) is
generous enough to grind ~120 password guesses per minute against a known
account from a single IP, and operators routinely raise that limit for normal
API traffic. There was no login-specific defense. (Path-traversal / zip-slip
on document ingestion and the token-gated node model-pull path were both
reviewed and found not vulnerable — filenames never touch the filesystem and
there is no archive extraction; the pull API requires the agent token.)

**Decision:** A dedicated, tighter sliding window (`app/core/loginguard.py`)
keyed on client IP that counts only *failed* logins. After
`AUTH_MAX_FAILED_LOGINS` failures (default 10) within
`AUTH_LOGIN_WINDOW_SECONDS` (default 300 s), further login attempts — including
one carrying the correct password — get `429` with `Retry-After` until the
oldest failure ages out. A successful login clears the IP's counter, so
legitimate users are never locked out by their own activity. Throttle events
are audited (`auth.login.throttled`). `AUTH_MAX_FAILED_LOGINS=0` disables it.

Deliberately a per-IP throttle, not an account lockout: locking by email would
let an attacker deny service to a known admin by spamming bad passwords. Keyed
by IP so at LAN scope one noisy host cannot lock out others. In-process and
single-node like the rate limiter (ADR-020); a horizontally-scaled deployment
needs a shared store and trusted-proxy `X-Forwarded-For` handling (backlog).

**Consequences:** Brute-forcing a password now costs at most
`AUTH_MAX_FAILED_LOGINS` guesses per window per IP on top of Argon2's per-guess
cost. Shared-NAT LAN caveat: many users behind one IP share the failure
budget — the default (10 / 5 min) is well clear of normal human mistyping, and
the value is tunable. Tests reset the process-wide failure buckets via an
autouse fixture so failed-login cases across the suite don't accumulate in the
shared 127.0.0.1 bucket.

## ADR-024: Structure-aware CSV/JSON ingestion loaders

**Date:** 2026-07-12

**Context:** The ingestion loader (ADR-013) branched only on `.pdf`; every
other file — including `.csv` and `.json` — was decoded as one UTF-8 blob and
handed to the paragraph-aware chunker. Because CSV rows and JSON records aren't
separated by blank lines, a spreadsheet or record file collapsed into one giant
chunk (or arbitrary hard-splits), so retrieval could never isolate a single row
or record. (Issue #2.)

**Decision:** `extract_text` now routes `.csv` and `.json` by extension (the
same pattern as `.pdf`) to structure-aware loaders in
`app/services/knowledge/loader.py`:

- **CSV** (`_extract_csv`, stdlib `csv`): the first row is taken as the header;
  each subsequent row becomes a `header: value | header: value` paragraph, and
  rows are joined with blank lines so each record is an independent "paragraph"
  the existing `chunk_text` packs to size. Empty cells are dropped; ragged rows
  keep the labels they have; a UTF-8 BOM is stripped. A file with no data rows
  below the header raises `ExtractionError`.
- **JSON** (`_extract_json`, stdlib `json`): a top-level array yields one
  record (paragraph) per element; any other value is flattened to
  `a.b[0]: value` leaf lines (dot paths for objects, `[i]` for arrays), with
  JSON-typed scalars (`null`/`true`/`false`). Malformed JSON raises
  `ExtractionError`.

Malformed CSV/JSON raise `ExtractionError` (a `ValueError` subclass), so the
API maps them to a 4xx and ingestion records a clean `failed` document instead
of a 500 — consistent with the PDF path.

**Consequences:** CSV/JSON documents are now retrievable per-row/record, which
is the point of ingesting structured data. Known limits (logged, not blocking):
the first CSV row is always treated as the header (headerless CSVs mislabel one
row); very wide single rows or huge single JSON objects can still exceed the
chunk size and get hard-split mid-value; and deeply nested JSON produces long
path prefixes. Routing is by file extension only — a CSV uploaded as `.txt` is
still treated as plain text. Nothing changes for text/markdown/code or PDF.
