# Lycosa — Claude Build Playbook
### From `git init` to production, engineered as a sequence of prompts

This is a **prompt-engineering playbook**: a start-to-finish set of copy-paste prompts for building **Lycosa** (the distributed multi-agent AI platform from your SDD) using Claude — ideally **Claude Code**, so Claude can create the repo, write files, run tests, and commit. Each phase gives you: what to paste, what you should get back, and the "definition of done" before moving on.

The whole thing is organized as **Agile sprints** so a single person (or a small team) can ship it incrementally, and the end user experience is **GitHub-first** (`git clone` → `docker compose up` → open dashboard).

---

> **Your repository:** [`https://github.com/abdra7/Lycosa`](https://github.com/abdra7/Lycosa) — already created. All clone/push commands in this playbook target it. In your first Claude Code session, connect the local scaffold to this remote (see the end of Sprint 0).

## 0. How to use this playbook

**Golden rules for prompting Claude on a project this size:**

1. **One phase per prompt.** Never ask Claude to "build the whole platform." Context and quality both collapse. Feed it the phase prompts in order.
2. **Always give Claude the SDD as an anchor.** At the start of each new session, paste the *System Primer* (Section 1) so Claude knows the architecture. Then paste the phase prompt.
3. **Make Claude plan before it codes.** Every phase prompt below ends with "produce a plan first, wait for my `go`." This catches wrong assumptions cheaply.
4. **Demand tests + the "done" checklist in the same prompt.** Otherwise you get code that looks right and isn't wired up.
5. **Commit at the end of every phase** with the exact message format in Section 2. This gives you a clean, reviewable history and easy rollback.
6. **Keep a `docs/DECISIONS.md`.** Tell Claude to append an entry whenever it makes an architecture choice. Future-you and future-Claude will thank you.

**Tooling assumption:** You're using **Claude Code** (the terminal/desktop agent) so Claude can actually create files, run `git`, run `docker compose`, and execute tests. If you're in the chat interface instead, everything still works — you'll just paste Claude's output into files yourself.

---

## 1. The System Primer (paste at the top of every new session)

> **You are my engineering partner building "Lycosa", a distributed multi-agent AI orchestration platform. Hold this context for the whole session.**
>
> **What Lycosa is:** a LAN-first platform that turns multiple heterogeneous devices into one cooperative AI execution fabric. Each device is a *node* running a Local Agent that can host a local LLM (via Ollama/llama.cpp), tools, memory, and optionally a RAG knowledge base. A central control plane discovers devices, recommends a node role from its hardware profile, schedules tasks, routes knowledge, runs multi-step workflows, and monitors everything from one dashboard.
>
> **Node roles:** AI Compute Node, Hybrid Node, Knowledge Node, Tool Node, Vision Node, Storage Node.
>
> **Architecture layers:** Presentation (dashboard) → Access (auth + API gateway) → Control Plane (Orchestrator, Agent Manager, Scheduler, Knowledge Router, Workflow Engine, Node Recommendation Engine) → Execution Plane (Local Agents + runtimes) → Knowledge Plane (ingestion, embeddings, Qdrant vector search) → Observability Plane (Prometheus/Grafana/logs) → Infrastructure (PostgreSQL, Qdrant, cache, Docker).
>
> **Tech stack:** Python + FastAPI backend; Flutter / Flutter Web dashboard; PostgreSQL (metadata) + Qdrant (vectors); REST + WebSocket + gRPC; Ollama/llama.cpp for local models; Prometheus + Grafana; Docker + Docker Compose; Kubernetes as a future target.
>
> **Core entities:** User, Role, Session, Node, Agent, AgentCapability, Workflow, WorkflowRun, Task, TaskExecution, KnowledgeCollection, Document, EmbeddingJob, RetrievalRequest, SystemEvent, AuditLog, Plugin.
>
> **Non-negotiables:** security-first (auth on every sensitive op, secure node registration, audit logs); observable by default; loosely coupled (agents never hardcode where knowledge lives); incrementally scalable (add nodes with no core redesign).
>
> **How we work:** Agile, one sprint/phase at a time. Before writing any code for a phase, produce a short plan and a file-by-file change list, then stop and wait for me to reply `go`. Always include tests and a "definition of done" checklist. When you make an architecture decision, append it to `docs/DECISIONS.md`.
>
> Acknowledge this context in 2 sentences, then wait for my first phase prompt.

Keep this primer in a file (`docs/PRIMER.md`) so you can paste it fast.

---

## 2. Conventions to lock in once

Paste this right after the primer, in the very first session only:

> Establish and record these project conventions in `CONTRIBUTING.md` and honor them for the rest of the project:
>
> - **Monorepo layout:** `/backend` (FastAPI services), `/agent` (Local Agent runtime), `/dashboard` (Flutter), `/infra` (docker compose, k8s, prometheus/grafana configs), `/docs`, `/scripts`.
> - **Branching:** trunk-based. `main` is always deployable. Feature work on `feat/<sprint>-<slug>` branches, merged via PR.
> - **Commits:** Conventional Commits — `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`. Each phase ends with one squash-friendly commit.
> - **Python:** 3.11+, `ruff` for lint/format, `pytest` for tests, `pydantic` v2 models, async FastAPI, type hints everywhere.
> - **API contract:** every endpoint documented via FastAPI OpenAPI; breaking changes bump an `/api/v1` → `/api/v2` prefix.
> - **Config:** all secrets/config via environment variables and a committed `.env.example` (never commit real secrets).
> - **Tests gate merges:** no phase is "done" until its tests pass.
>
> Create `CONTRIBUTING.md`, `.gitignore`, `.env.example`, and `docs/DECISIONS.md` now. Show me the files, then wait for `go` before the next phase.

---

## 3. The Sprint Plan (the map)

| Sprint | Phase | Goal (thin vertical slice) |
|---|---|---|
| **0** | Repo & scaffolding | GitHub repo, monorepo skeleton, CI, `docker compose up` boots empty services |
| **1** | Data + Auth | PostgreSQL schema, migrations, auth service (login/logout, RBAC, API keys) |
| **2** | API Gateway + Node Registry | Register a node, store hardware profile, list nodes |
| **3** | Node Recommendation Engine | Hardware profile → recommended role, accept/override |
| **4** | Local Agent runtime | Installable agent that registers, heartbeats, reports health |
| **5** | Scheduler + Orchestrator | Submit a task → classify → pick a node → run → return result |
| **6** | Knowledge Plane (RAG) | Ingest docs → embed → Qdrant → retrieve; Knowledge Router |
| **7** | Workflow Engine | Multi-step planner→coder→test→review workflows with traces |
| **8** | Dashboard | Flutter Web: nodes, agents, live metrics, workflows, logs |
| **9** | Observability | Prometheus + Grafana + structured logs + alerts + WebSocket streams |
| **10** | Packaging & GitHub install UX | One-command install, docs, release, `install.sh` |
| **11+** | Agile maintenance | Backlog grooming, issues, releases, iteration |

Each sprint below is a **ready-to-paste prompt**. Run them in order.

---

## Sprint 0 — Repository & scaffolding

> **Phase 0 — Repo & scaffolding.** Create the Lycosa monorepo skeleton and prove the plumbing works end to end (empty but bootable).
>
> Deliver:
> 1. Full directory tree per our conventions (`/backend`, `/agent`, `/dashboard`, `/infra`, `/docs`, `/scripts`).
> 2. `backend/` FastAPI app with a `/healthz` endpoint and a `pyproject.toml` (ruff + pytest configured).
> 3. `infra/docker-compose.yml` that starts: `postgres`, `qdrant`, `api` (the FastAPI app), and placeholders for `prometheus`/`grafana`. Wire healthchecks.
> 4. A GitHub Actions workflow `.github/workflows/ci.yml`: lint + test on push/PR.
> 5. `README.md` with a real "Quick start": `git clone` → `cp .env.example .env` → `docker compose up` → open `http://localhost:8000/docs`.
> 6. One passing pytest that hits `/healthz`.
>
> Definition of done: `docker compose up` boots all containers healthy; `pytest` is green; `/healthz` returns 200.
>
> Plan first (file list + compose services), then wait for `go`.

**After Claude finishes**, your repo already exists at **`https://github.com/abdra7/Lycosa`**, so just connect and push. If you're in Claude Code let it run:
```bash
git init
git add -A && git commit -m "chore: scaffold Lycosa monorepo and CI"
git branch -M main
git remote add origin https://github.com/abdra7/Lycosa.git
git push -u origin main
```
If the repo already has a commit (e.g. a README from GitHub's "create repo" screen), pull first to avoid a rejected push:
```bash
git remote add origin https://github.com/abdra7/Lycosa.git
git pull origin main --allow-unrelated-histories
git push -u origin main
```

---

## Sprint 1 — Data model + Authentication

> **Phase 1 — Data + Auth.** Implement the persistence layer and the Authentication Service (SDD FR-1).
>
> Deliver:
> 1. SQLAlchemy 2.0 (async) models + Alembic migrations for the core entities: User, Role, Session, Node, Agent, AgentCapability, AuditLog (we'll add workflow/knowledge tables in later sprints — leave clean extension points).
> 2. Auth service: password login/logout, JWT (or session token) issuance, **role-based access control** (roles: admin, operator, node), and **API keys** for node/service-to-service auth.
> 3. Audit logging for privileged actions.
> 4. Endpoints: `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, plus a protected `GET /api/v1/me`.
> 5. Password hashing (argon2/bcrypt), token expiry, and a seed script that creates a default admin from env vars.
> 6. Tests: login success/failure, RBAC denial for non-admin, API-key auth path, audit row written.
>
> Definition of done: migrations apply cleanly; all auth tests green; unauthorized requests are rejected; audit entries recorded.
>
> Plan first (ERD + endpoint list), then wait for `go`.

---

## Sprint 2 — API Gateway + Node Registration

> **Phase 2 — API Gateway + Node Registry.** Implement node registration and inventory (SDD FR-2, Use Case 1 steps 1–4, 7–8).
>
> Deliver:
> 1. API Gateway concerns on the FastAPI edge: auth enforcement, request validation, rate limiting, consistent error envelope, and `/api/v1` versioning.
> 2. Node registration flow: `POST /api/v1/nodes/register` accepts a **hardware profile** (CPU model, cores, GPU model + VRAM, total RAM, storage, OS, installed runtimes like Ollama). Registration is authenticated via API key.
> 3. `GET /api/v1/nodes`, `GET /api/v1/nodes/{id}`, `PATCH /api/v1/nodes/{id}` (update role/metadata), and node status field (registered / online / offline).
> 4. Store the raw hardware profile as JSON plus normalized columns for scheduling later.
> 5. Tests: register a node, reject bad/unauthenticated registration, list + fetch, patch role.
>
> Definition of done: a node can register with a hardware profile and appear in `GET /nodes`; invalid payloads rejected with clear errors.
>
> Plan first (payload schema for the hardware profile), then wait for `go`.

---

## Sprint 3 — Intelligent Node Recommendation Engine

> **Phase 3 — Node Recommendation Engine.** This is Lycosa's signature feature (SDD FR-7).
>
> Deliver:
> 1. A `recommendation` module that takes a hardware profile and returns a **recommended role** with a confidence score and human-readable rationale. Roles: AI Compute, Hybrid, Knowledge, Tool, Vision, Storage.
> 2. Start rule-based and transparent (e.g. high-VRAM GPU + large RAM → AI Compute; strong balanced specs → Hybrid; low compute + large storage → Knowledge/Storage; CPU-only → Tool). Encode the SDD's example table as test cases:
>    - Ryzen 9 / RTX 5090 / 64GB → Hybrid
>    - i5 / 8GB / no GPU → Knowledge or Tool
>    - RTX 4090 / 128GB → AI Compute
> 3. Endpoint `POST /api/v1/recommendations/node-role` (profile in → recommendation out) and wire it into registration so a new node gets a suggested role.
> 4. Accept/override: operator can confirm or change the role; store recommendation metadata for the scheduler.
> 5. Keep the rules in a config file so they're tunable without code changes. Make room for a future ML-based recommender behind the same interface.
> 6. Tests covering all six roles and the three SDD examples.
>
> Definition of done: the SDD example table passes as tests; registration surfaces a recommended role; overrides persist.
>
> Plan first (the ruleset + scoring approach), then wait for `go`.

---

## Sprint 4 — Local Agent runtime

> **Phase 4 — Local Agent runtime.** The installable unit that runs on each node (SDD Local Agent component).
>
> Deliver:
> 1. A standalone Python agent in `/agent` that: collects its own hardware profile, registers with the controller (API key), sends periodic **heartbeats**, and reports health metrics (CPU, RAM, GPU util/temp where available, disk, running tasks).
> 2. A `Runtime Adapter` interface with an **Ollama adapter** first (list/pull models, run a prompt), designed so llama.cpp / HF adapters slot in later.
> 3. A local execution API on the agent (receive a task, run it, return a result) and a secure comms client to the controller.
> 4. Controller side: heartbeat ingestion endpoint, mark nodes online/offline on heartbeat timeout, expose latest metrics per node.
> 5. Packaging: a `pipx`/`pip install`-able agent plus a one-liner install script and a systemd/service example. Document how a user installs the agent on a new machine.
> 6. Tests: agent registers + heartbeats (mock controller); controller flips a node offline after missed heartbeats; Ollama adapter behind a mock.
>
> Definition of done: run the agent locally → it registers, heartbeats, and its metrics show up via the API; missing heartbeats mark it offline.
>
> Plan first (agent module layout + heartbeat protocol), then wait for `go`.

---

## Sprint 5 — Scheduler + Orchestrator

> **Phase 5 — Scheduler + Orchestrator.** The control-plane brain (SDD FR-5, Use Case 2, sequence: Task Execution).
>
> Deliver:
> 1. **Orchestrator**: `POST /api/v1/tasks` accepts a request, classifies task type + resource needs (start with a simple classifier: coding / retrieval / tool / vision / general), calls the Scheduler, dispatches to the chosen agent, aggregates the result, and persists a TaskExecution with logs.
> 2. **Scheduler**: capability-aware + load-aware selection using node role, available RAM, GPU availability, model compatibility, current load. Implement best-fit selection plus retry/failover to another node on failure.
> 3. Task lifecycle states (queued → assigned → running → succeeded/failed) with timestamps.
> 4. Dispatch to the Local Agent's execution API from Sprint 4; handle agent-down mid-task with failover.
> 5. Tests: submit a task → scheduler picks a compatible online node → result returned; no compatible node → graceful error; node dies mid-task → failover path.
>
> Definition of done: end-to-end — submit a task via API, it runs on a real/mock agent, result + execution trace are stored and retrievable.
>
> Plan first (scheduler scoring + task state machine), then wait for `go`.

---

## Sprint 6 — Knowledge Plane (RAG) + Knowledge Router

> **Phase 6 — Knowledge Plane.** Distributed RAG and the Knowledge Router (SDD FR-6, FR-9, Use Case 3).
>
> Deliver:
> 1. Ingestion: `POST /api/v1/knowledge/collections` and a document upload/ingest endpoint. Document Loader for PDF/markdown/text/code.
> 2. Embedding pipeline (pluggable embedding model) → store vectors + metadata in **Qdrant**, organized by collection.
> 3. Retriever (similarity + metadata filter) → Context Builder that assembles agent-ready context chunks.
> 4. **Knowledge Router**: `POST /api/v1/knowledge/retrieve` accepts a semantic request (e.g. "Retrieve Flutter documentation") **without** the caller naming a node; the router picks the best knowledge node/collection by ownership, freshness, and relevance, and returns normalized context. Design for multiple knowledge nodes now, federated retrieval later.
> 5. Wire retrieval into the Orchestrator so a task can request knowledge mid-execution.
> 6. Tests: ingest → embed → retrieve returns relevant chunks; router selects best collection; agent never specifies a physical node.
>
> Definition of done: upload docs, then a retrieval request routed by the Knowledge Router returns relevant, normalized context.
>
> Plan first (collection schema + router selection logic), then wait for `go`.

---

## Sprint 7 — Workflow Engine

> **Phase 7 — Workflow Engine.** Multi-step orchestration (SDD FR-8, Use Case 2).
>
> Deliver:
> 1. Workflow definitions (declarative — JSON/YAML) with steps, each step bound to an agent role/capability. Support the SDD reference flow: Planner → Coding → Knowledge → Testing → Reviewer → Response.
> 2. Execution features: sequential steps, conditional branching, retry logic, parallel subtasks, shared context propagation between steps, and full execution-trace persistence (WorkflowRun + per-step records).
> 3. Optional human-approval checkpoints (a step can pause pending operator approval).
> 4. Endpoints: `POST /api/v1/workflows`, `POST /api/v1/workflows/{id}/run`, `GET /api/v1/workflows/{id}/runs/{run_id}` (status + trace).
> 5. Reuse the Orchestrator/Scheduler for each step's dispatch and the Knowledge Router for retrieval steps.
> 6. Tests: define + run the planner→coder→test→review workflow (agents mocked); branching + retry + a paused approval step.
>
> Definition of done: a multi-step workflow runs end to end, each step's trace is stored, and status is queryable live.
>
> Plan first (workflow schema + step executor design), then wait for `go`.

---

## Sprint 8 — Dashboard (Flutter Web)

> **Phase 8 — Dashboard.** The operational workspace (SDD Dashboard component, Use Case 4).
>
> Deliver a Flutter Web app in `/dashboard` that talks to our REST + WebSocket API:
> 1. Auth: login screen, token storage, role-aware navigation.
> 2. **Node inventory**: table/cards with status, role, model, uptime, health; node detail page with live metrics.
> 3. Node registration UX that shows the **recommended role** and lets the operator accept/override.
> 4. **Workflow** views: create/select a workflow, run it, and watch step progress live.
> 5. **Live metrics**: CPU/GPU/RAM/storage/network charts fed by WebSocket updates.
> 6. Knowledge view: collections + document counts. Logs/events + alerts panel. User/role management (admin only).
> 7. Responsive, theme-aware (light/dark) layout for desktop and tablet.
>
> Definition of done: log in, see live nodes + metrics, register a node with role recommendation, launch a workflow and watch it progress — all against the running backend.
>
> Plan first (screen list + state management choice + API client structure), then wait for `go`. Build screen-by-screen; don't dump the whole app in one response.

*(Note: Sprint 8 is large. Run it as sub-prompts: 8a auth+shell+API client, 8b nodes, 8c workflows, 8d metrics/knowledge/logs. Same "plan → go" pattern each time.)*

---

## Sprint 9 — Observability

> **Phase 9 — Observability Plane.** Metrics, logs, alerts (SDD Monitoring Service, NFR-8).
>
> Deliver:
> 1. Structured JSON logging across services with correlation IDs (task/workflow/run).
> 2. Prometheus metrics: per-service and per-node (task duration, queue depth, model latency, retrieval latency, API error rate, node up/down). Expose `/metrics`.
> 3. Grafana dashboards provisioned as code in `/infra` (system overview, node health, workflow throughput).
> 4. Alert rules: offline node, failed workflow, degraded retrieval latency.
> 5. WebSocket event stream for the dashboard: `node.connected`, `node.disconnected`, `node.metrics.updated`, `workflow.started`, `workflow.step.completed`, `workflow.failed`, `agent.status.changed`, `alert.created`.
> 6. Tests: metrics endpoint exposes expected series; an offline node fires the alert rule; WebSocket emits the right events.
>
> Definition of done: Grafana shows live node + workflow metrics; killing a node raises an alert and pushes a WebSocket event the dashboard renders.
>
> Plan first (metric catalog + event schema), then wait for `go`.

---

## Sprint 10 — Packaging & GitHub install experience

This is the sprint that delivers the **"people install Lycosa from GitHub"** experience you asked for.

> **Phase 10 — Packaging & install UX.** Make Lycosa trivially installable from GitHub.
>
> Deliver:
> 1. **Controller install:** a single `docker compose up -d` from a fresh clone brings up the entire controller stack (api, postgres, qdrant, prometheus, grafana) with sane defaults and a first-run admin bootstrap. Provide `.env.example` with every variable documented.
> 2. **`scripts/install.sh`** (and a Windows `install.ps1`) that: checks Docker, copies `.env.example`, prompts for admin credentials, runs compose, and prints the dashboard URL. Aim for: `curl -fsSL https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install.sh | bash`.
> 3. **Agent install:** documented `pipx install` (or one-liner script) to add a node, with a printed command the dashboard generates (embedding a scoped API key).
> 4. **README** rewritten as the front door: what Lycosa is, a diagram, 3-step quick start, screenshots placeholder, node-role explainer, and a "Deploy modes" section (single-machine, multi-machine LAN, compose, future k8s).
> 5. **Release engineering:** version the API and app, add `CHANGELOG.md`, tag `v0.1.0`, and a GitHub Actions release workflow that builds/pushes Docker images and attaches artifacts.
> 6. `LICENSE`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, and issue/PR templates in `.github/`.
>
> Definition of done: on a clean machine, `git clone` → `./scripts/install.sh` → open the dashboard → log in → see the "add a node" command. A tagged `v0.1.0` release exists with images published.
>
> Plan first (install script flow + release pipeline), then wait for `go`.

**Your GitHub-first user story now reads:**
```bash
git clone https://github.com/abdra7/Lycosa.git
cd Lycosa
./scripts/install.sh          # or: docker compose up -d
# open http://localhost:8000 → log in → copy the "add node" command
pipx install lycosa-agent && lycosa-agent join --url ... --key ...
```

---

## 4. Sprint 11+ — Agile maintenance (the "keep it alive" loop)

Once `v0.1.0` ships, switch from building to **iterating**. This is the Agile rhythm to run with Claude indefinitely.

**Set up the board (one-time prompt):**

> Create a GitHub-based Agile setup for Lycosa: an `.github/ISSUE_TEMPLATE/` with `bug`, `feature`, and `tech-debt` templates; a `docs/ROADMAP.md` seeded from the SDD "Future Work" (auto-discovery, load balancing, plugin system, visual workflow builder, multi-tenant, k8s, federated knowledge sync, model benchmarking); and a `docs/BACKLOG.md` with user-story format ("As a <role>, I want <goal> so that <value>") for each. Prioritize into Now / Next / Later.

**The recurring sprint loop (repeat every iteration):**

1. **Backlog grooming** — *"Here's our current `BACKLOG.md`. Propose the next 1-week sprint: 3–5 stories sized S/M/L with acceptance criteria. Flag dependencies and risks. Wait for my approval."*
2. **Sprint execution** — feed approved stories one at a time using the same **plan → `go` → code + tests → commit** pattern from the sprints above.
3. **Definition of done** stays constant: tests green, docs updated, `CHANGELOG.md` entry, `docs/DECISIONS.md` updated if an architecture choice was made.
4. **Release** — *"Cut release v0.x.0: update CHANGELOG, bump version, tag, and summarize what changed for the GitHub release notes."*
5. **Retro** — *"Given the diffs and issues this sprint, write a 5-bullet retro: what worked, what slowed us down, and one process change for next sprint. Append to `docs/RETROS.md`."*

**Handling incoming issues with Claude:**

> Triage this GitHub issue: [paste]. Decide bug vs feature vs tech-debt, reproduce it against our codebase, propose a fix or a story with acceptance criteria, estimate size, and tell me where it fits in Now/Next/Later. Don't code yet.

**Guardrails for maintenance quality:**
- Require a failing test that reproduces every bug **before** the fix (red → green).
- Keep `main` deployable; every change goes through a PR Claude can draft (`gh pr create`).
- Run a dependency + security review each release: *"Audit our dependencies and Docker images for known CVEs and propose upgrades as a tech-debt story."*

---

## 5. Cross-cutting prompt patterns (reuse anywhere)

**When Claude's output is too big / stalls:** *"Stop. Give me only the plan and file list for this phase. We'll implement one file at a time."*

**When something breaks:** *"Here's the failing test output: [paste]. Diagnose root cause, state your hypothesis, propose the minimal fix, then wait for `go`. Don't refactor unrelated code."*

**When you want a design decision explained:** *"Before coding, give me 2–3 options for <X> with trade-offs against our NFRs (scalability, security, observability, maintainability) and a recommendation."*

**To prevent scope creep:** *"Only implement what this phase's definition of done requires. List anything you think is missing as backlog items in `docs/BACKLOG.md` instead of building it."*

**To keep context fresh across sessions:** start each session with the **System Primer** (Section 1) + *"Read `docs/DECISIONS.md` and `CHANGELOG.md` to catch up, then summarize current project state in 5 bullets before we continue."*

**Security review prompt (run before each release):** *"Review this sprint's code against our security model: auth on every sensitive op, secure node registration, encrypted service-to-agent comms, secret handling, audit logging, tool isolation on nodes. List gaps as prioritized issues."*

---

## 6. First three prompts to send right now

1. **Session 1, message 1:** the System Primer (Section 1).
2. **Session 1, message 2:** the Conventions prompt (Section 2). Reply `go`, let Claude scaffold, commit + push to GitHub.
3. **Session 1, message 3:** Sprint 0 phase prompt. Reply `go`, get a booting skeleton, tag nothing yet — just push.

From there, walk Sprints 1 → 10 in order, then enter the Sprint 11+ maintenance loop. That's the whole journey: **create the repo → build vertically, sprint by sprint → ship a GitHub-installable v0.1.0 → maintain it Agile-style, forever.**
