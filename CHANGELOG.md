# Changelog

All notable changes to Lycosa are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- **Add-node command no longer hands out an unreachable `localhost`** — when the
  dashboard is connected to the controller over `localhost` (the common case
  when it runs on the controller PC), the generated agent command now
  substitutes the controller host's detected LAN IP so it works when pasted on
  a different device. It skips virtual adapters (VirtualBox 192.168.56.x,
  Docker/WSL 172.16–31.x, link-local 169.254.x) and prefers a real
  192.168.x/10.x address, warning clearly if none can be detected.

### Added

- **Zero-config agent setup** — installing the agent is now the whole job.
  `scripts/install-agent.ps1` already opens the needed firewall ports
  (UDP 5353 mDNS, TCP 8010 exec API) with elevation; on first run the agent
  now also configures its own model: if Ollama is empty, it asks the
  controller which model best fits its hardware (node keys may read their own
  node's recommendations), pulls it, and re-registers with the updated
  inventory — all over the agent's outbound connection, so no inbound ports
  or operator action are required. Opt out with `LYCOSA_AUTO_PULL_MODEL=false`.

- **Per-device LLM recommendations & one-click model install** — clicking a
  device (a node row, or a discovered LAN device that is registered) opens its
  detail page with a new "Recommended models" card: the controller ranks a
  tunable catalog (`backend/config/llm_catalog.yml`) against the device's
  hardware (GPU VRAM first, CPU-RAM fallback) and explains why each model
  does or doesn't fit. Pressing **Install** configures the agent with that
  model: the controller calls the agent's new `POST /models/pull` endpoint,
  Ollama downloads the weights, and the node's installed-model inventory
  refreshes immediately (`GET /api/v1/nodes/{id}/llm-recommendations`,
  `POST /api/v1/nodes/{id}/models`, audited). Discovered-but-unregistered
  devices open the add-node key flow on click instead.

- **Delete collection (Ticket #105)** —
  `DELETE /api/v1/knowledge/collections/{id}` removes a knowledge collection:
  its Qdrant vectors, documents, and embedding jobs (retrieval audit rows are
  kept with the collection reference cleared). The dashboard's Knowledge
  screen gained a delete button with a confirmation dialog on each
  collection.

- **LAN discovery (Ticket #103, ADR-018)** — agents now advertise themselves
  over mDNS (`_lycosa-agent._tcp`, opt out with
  `LYCOSA_DISCOVERY_ENABLED=false`), and the dashboard's Nodes screen gained
  a "Discovered on LAN" scan that lists machines running `lycosa-agent` and
  flags the ones not yet registered. The README documents the ports involved
  (8000/TCP controller, 8010/TCP agent exec, 5353/UDP mDNS) for firewall
  troubleshooting.

### Fixed

- **Stuck ingestions recovered on restart** — a controller crash mid-ingest
  (power loss, OOM-kill, `docker restart`) used to strand the document in
  `uploaded` with its embedding job `running` forever, since the in-request
  safety timeout dies with the process. Startup now recovers those orphans:
  they are marked `failed` with a "re-upload the document" error so the
  operator sees an actionable state instead of a forever-pending upload.
  Found by the E2E-02 recovery tests.

- **Upload/delete race on knowledge collections** — deleting a collection
  while one of its documents was still embedding let the in-flight ingestion
  re-create the collection's Qdrant store and write orphaned vectors, then
  crash with an unhandled 500. The losing upload now receives a clean
  `409 Conflict` and any vectors written after the delete are removed, so a
  deleted collection can never leave zombie embeddings behind. Found by the
  IT-API-02 concurrency tests, which now cover parallel uploads, client
  disconnect mid-ingestion, and this race.

- **LAN scan on Windows (Ticket #106)** — the dashboard's mDNS scan no longer
  crashes with errno 10042 (`WSAENOPROTOOPT`) when a virtual adapter (VPN
  tunnel, Docker/WSL switch) refuses multicast group membership; those
  adapters are now skipped and discovery proceeds over the remaining
  interfaces.

- **Knowledge ingestion freeze (Ticket #104)** — a document upload whose
  caller disconnects or times out mid-run no longer leaves the document stuck
  in `uploaded` with its embedding job `running` forever: ingestion now runs
  shielded on its own DB session (the same treatment task dispatch got in
  Ticket #102), CPU-bound embedding happens off the event loop in a worker
  thread, and a 5-minute safety-net timeout marks a stuck run `failed` with
  an actionable error instead of hanging indefinitely.

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
