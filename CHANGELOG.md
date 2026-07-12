# Changelog

All notable changes to Lycosa are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- **`.docx` uploads were silently corrupted (ADR-025, #28)** — a `.docx` fell
  through to the plain-text fallback, so its ZIP bytes were embedded as
  replacement-character garbage while the document still reported `embedded`.
  DOCX is now parsed with `python-docx` (paragraphs and table rows become
  retrievable chunks), and the plain-text fallback refuses binary content
  (ZIP/OLE2 signatures, NUL bytes, high replacement-character ratio) with a
  clear extraction error instead of storing junk.

### Added

- **Shared rate-limit / login-guard state via Redis (ADR-027, #4 phase 1)** —
  the request rate limiter and the failed-login throttle now keep their
  sliding windows behind a store abstraction. By default nothing changes
  (in-process buckets, no Redis required); setting `REDIS_URL` moves both
  windows into Redis so they hold their configured limits across multiple
  uvicorn workers. An optional `redis` compose service ships behind
  `--profile redis`. If Redis is unreachable, the rate limiter fails open
  (admits and logs) while the login guard fails closed.
- **Scanned-PDF detection + opt-in OCR (ADR-026, #29)** — a PDF with pages but
  no text layer (a scan) now fails with an actionable message telling the
  operator it looks like a scanned document and how to enable OCR, instead of
  the generic "no extractable text in document". Installing the new backend
  `[ocr]` extra (`pytesseract` + `pillow`, plus the `tesseract` binary) turns
  on OCR over the page images, making scanned PDFs retrievable; the base
  install stays unchanged. Text-layer PDFs are unaffected.
- **Structure-aware CSV/JSON ingestion (ADR-024, #2)** — uploaded `.csv` and
  `.json` documents are now parsed by structure instead of decoded as one
  opaque text blob. Each CSV row becomes a `header: value | …` record and each
  JSON array element / object leaf becomes a `path: value` line, so retrieval
  can isolate an individual row or record. Malformed files report a clean
  extraction error rather than a 500. Text, markdown, code, and PDF are
  unchanged.
- **Embedding backend benchmark (#3)** — `backend/scripts/benchmark_embeddings.py`
  measures precision@k / recall@k / MRR of the default `hashing` backend against
  `fastembed` on a labeled retrieval set. Measured results and guidance on when
  to switch backends are in `docs/rag_embedding_benchmark.md` (fastembed reached
  perfect recall@3 and MRR on the paraphrased-query set; hashing stayed
  keyword-level).

## [0.3.1] - 2026-07-12

### Fixed

- **Desktop app displayed the wrong version** — the in-app version constant
  (`dashboard/lib/core/app_info.dart`) wasn't bumped alongside `pubspec.yaml`
  for the 0.3.0 release, so the dashboard's shell screen showed `v0.2.1` on the
  0.3.0 build. Corrected to match the release, with a test asserting the
  constant tracks `pubspec.yaml` so the two can't drift again.

## [0.3.0] - 2026-07-12

Production-readiness release: zero-configuration startup, hardened deployment
scaffolding, and a brute-force defense on login. Full detail in
`docs/QA_v0.3.0.md` (release audit + readiness scorecard).

### Added

- **Zero-configuration startup (ADR-022)** — a fresh clone now runs with no
  `.env`: `docker compose -f infra/docker-compose.yml up --build -d` boots
  directly against committed safe defaults (`infra/compose-defaults.env`). The
  API generates a strong `JWT_SECRET` and a random admin password on first run,
  persists them in the `api_data` volume, and prints the admin password once in
  the `api` container logs. An optional root `.env` still overrides everything
  (the installers continue to generate one), so existing setups are unchanged.

- **Login brute-force throttle (ADR-023)** — `/api/v1/auth/login` now enforces a
  per-IP failed-login sliding window on top of the global rate limit. After
  `AUTH_MAX_FAILED_LOGINS` failures (default 10) within
  `AUTH_LOGIN_WINDOW_SECONDS` (default 300 s) an IP gets `429` + `Retry-After`
  and an `auth.login.throttled` audit event; a successful login clears the
  counter. Per-IP rather than per-account so an attacker can't lock out a known
  admin. Set `AUTH_MAX_FAILED_LOGINS=0` to disable.

### Changed

- **Deployment hardening (ADR-022)** — Postgres, Qdrant, and Prometheus now bind
  to `127.0.0.1` instead of every interface (Qdrant ships no auth by default and
  was previously LAN-reachable, bypassing API auth); only the API (`:8000`) and
  Grafana (`:3001`) stay LAN-exposed. Every service gained `restart:
  unless-stopped` and json-file log rotation (10 MB × 3), Grafana now waits for a
  healthy Prometheus, and `QDRANT_API_KEY` is plumbed through the backend client
  for operators who enable Qdrant auth.

- **Dashboard theme no longer flashes on launch** — the saved light/dark choice
  is read before the first frame instead of asynchronously after startup, so
  dark-mode users no longer see a light flash on every launch. Light remains the
  default.

### Security

- **Fail-fast on default secrets in production (#7, ADR-022)** — with
  `ENVIRONMENT=production` the API refuses to start when the database password is
  a known default/placeholder (and, as defense-in-depth, when the JWT or admin
  secrets are placeholders), instead of silently serving with weak credentials.

- **Login brute-force throttle (ADR-023)** — see Added. The v0.3.0 security probe
  also re-confirmed no path-traversal / zip-slip exposure in document ingestion
  (uploaded filenames never touch the filesystem and no archive is ever
  extracted) and that the node model-pull API is token-gated.

## [0.2.1] - 2026-07-11

### Security

- **Rate-limit bypass fixed (F-2, ADR-020)** — the in-process rate limiter keyed
  each bucket on the presented `X-API-Key` header, so a caller could send a
  different arbitrary key on every request to get a fresh bucket and escape the
  limit — including credential brute-force throttling on `/api/v1/auth/login`.
  Buckets are now keyed on client IP only, so a forged/rotating header can't
  spawn a new bucket. Found and reproduced live in the v0.2.0 Phase 7 security
  audit (150 brute-force logins with a rotating header → 0 throttled).

## [0.2.0] - 2026-07-11

### Added

- **Grounded knowledge answers (ADR-019)** — knowledge-driven tasks now answer
  strictly from retrieved context. The orchestrator injects a grounding
  instruction telling the model to use only the retrieved context and, when the
  answer isn't there, to reply "I cannot answer this based on the retrieved
  knowledge." If retrieval finds nothing relevant, the controller returns that
  refusal directly without calling an LLM. A tunable `RETRIEVAL_MIN_SCORE`
  (default off) drops low-score chunks before they reach the model. Found by the
  v0.2.0 Phase 4 & 5 live validation, where an out-of-scope question previously
  made the model fabricate an answer and cite the context for it.

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

[Unreleased]: https://github.com/abdra7/Lycosa/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/abdra7/Lycosa/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/abdra7/Lycosa/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/abdra7/Lycosa/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/abdra7/Lycosa/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/abdra7/Lycosa/releases/tag/v0.1.0
