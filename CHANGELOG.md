# Changelog

All notable changes to Lycosa are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
