# Changelog

All notable changes to Lycosa are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- **Task history (Ticket #102)** — completed tasks no longer vanish from the
  dashboard: `POST /api/v1/tasks` dispatch is now shielded from client
  disconnects and runs on its own DB session, so a task whose caller times
  out mid-run still gets its terminal state (succeeded/failed) recorded
  instead of hanging in `running` forever. The dashboard's task submission
  timeout now covers the controller's worst-case synchronous dispatch
  (7 minutes, was 15 s), and a dropped connection explains that the task may
  still finish and appear in Recent.

- **Knowledge ingestion (Ticket #101)** — document upload failures now surface
  actionable errors instead of raw tracebacks or silent timeouts: corrupt and
  password-protected PDFs are reported as such, a missing `fastembed` extra or
  a failed model download says how to fix it, and Qdrant outages name the
  service and URL to check. The dashboard no longer aborts uploads after 15 s
  (synchronous ingestion gets a 5-minute budget), shows connection failures,
  and refreshes the document list even when ingestion fails so failed rows and
  their errors are visible.

## [0.1.0] - 2026-07-05

First public release: a LAN-first distributed multi-agent AI orchestration
platform — headless controller stack, per-node Local Agent, and a native
desktop dashboard.

### Added

- **Control plane (FastAPI)** — versioned REST API under `/api/v1` with a
  consistent machine-readable error envelope and in-process rate limiting.
- **Auth & RBAC** — JWT logins backed by revocable server-side sessions,
  argon2id password hashing, prefixed API keys for services/nodes, and
  role-based access control (`admin`, `operator`, `node`) over a unified
  principal model.
- **Node fabric** — API-key-bound node registration (idempotent across
  reboots), 15-second heartbeats with live metrics, offline sweeper, and a
  transparent rule-based role recommender (AI Compute · Hybrid · Knowledge ·
  Tool · Vision · Storage) with per-role scores and rationale.
- **Local Agent** (`lycosa-agent`) — self-registering node runtime with a
  token-secured execution API and pluggable model runtimes (Ollama first).
- **Task orchestration** — submit → classify (keyword rules) → schedule
  (role/capacity scoring) → dispatch with ordered-candidate failover and a
  full per-attempt execution trace.
- **Knowledge plane** — document ingestion (text/PDF) with paragraph-aware
  chunking, pluggable embedders (hermetic hashing default, ONNX MiniLM via
  `[embeddings]` extra), Qdrant vector storage, and a federated-lite
  retrieval router that injects context into dispatched tasks.
- **Workflow engine** — declarative multi-step definitions (task, retrieve,
  approval, parallel) with template context propagation, conditional
  branching, retries, and pause/resume for human approval.
- **Desktop dashboard (Flutter)** — native macOS/Windows/Linux operator app:
  multi-controller profiles in the OS keychain, node inventory and role
  management, add-node key minting, tasks, workflows, knowledge, and admin
  screens, with live updates over WebSocket.
- **Observability** — structured JSON logs with request/task/workflow
  correlation ids, Prometheus metrics at `/metrics`, provisioned Grafana
  dashboards (System Overview, Node Health), alert rules, and a
  `WS /api/v1/events` live event stream.
- **Packaging & install** — one-command controller install
  (`scripts/install.sh` / `install.ps1`) on Docker Compose, GHCR backend
  image, desktop installers (.dmg / .exe / .AppImage) built by a tagged
  release workflow, and an agent installer script.

[Unreleased]: https://github.com/abdra7/Lycosa/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/abdra7/Lycosa/releases/tag/v0.1.0
