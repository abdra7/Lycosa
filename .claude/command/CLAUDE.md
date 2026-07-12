# Lycosa — Project Context for Claude

This file is auto-loaded at the start of every session. Read it, then read the
linked docs below to catch up on all decisions and open work before acting.
You do **not** need to be handed the "System Primer" manually anymore — it lives here.

---

## What Lycosa is

A **LAN-first, distributed multi-agent AI orchestration platform**. It turns
multiple heterogeneous devices (workstations, laptops, homelab boxes, mini-PCs)
into one cooperative AI execution fabric. Each device is a *node* running a Local
Agent that can host a local LLM (Ollama/llama.cpp), tools, memory, and optionally
a RAG knowledge base. A central control plane discovers devices, recommends a node
role from its hardware profile, schedules tasks, routes knowledge, runs multi-step
workflows, and monitors everything from one dashboard.

- **Node roles:** AI Compute, Hybrid, Knowledge, Tool, Vision, Storage.
- **Layers:** Presentation (dashboard) → Access (auth + API gateway) → Control
  Plane (Orchestrator, Agent Manager, Scheduler, Knowledge Router, Workflow
  Engine, Node Recommendation Engine) → Execution Plane (Local Agents + runtimes)
  → Knowledge Plane (ingestion, embeddings, Qdrant) → Observability
  (Prometheus/Grafana/logs) → Infrastructure (PostgreSQL, Qdrant, Docker).
- **Stack:** Python + async FastAPI backend; **native Flutter Desktop**
  (macOS/Windows/Linux) dashboard — NOT Flutter Web; PostgreSQL (metadata) +
  Qdrant (vectors); REST + WebSocket; Ollama/llama.cpp for local models;
  Prometheus + Grafana; Docker Compose for the **backend/controller only** (the
  desktop app ships as installable release artifacts).
- **Non-negotiables:** security-first (auth on every sensitive op, secure node
  registration, audit logs); observable by default; loosely coupled (agents never
  hardcode where knowledge lives); incrementally scalable.

## Repo layout

`/backend` (FastAPI) · `/agent` (Local Agent runtime) · `/dashboard` (Flutter
Desktop) · `/infra` (compose, prometheus/grafana) · `/docs` · `/scripts`.
Repo: https://github.com/abdra7/Lycosa

---

## Your role & how we work

You are the engineering partner building and maintaining Lycosa.

- **Plan before you code.** For any non-trivial phase or change, produce a short
  plan + file-by-file change list, then wait for `go` before writing code.
- **Tests gate merges.** No change is "done" without a green test run for its
  scope. Reproduce every bug with a failing test first (red → green).
- **Conventions are locked** (see ADR-001): monorepo layout above; trunk-based
  branching (`main` always deployable, work on `feat/<sprint>-<slug>`, merge via
  PR); Conventional Commits (`feat:` `fix:` `chore:` `docs:` `test:` `refactor:`);
  Python 3.11+, `ruff`, `pytest`, `pydantic` v2, full type hints; API under
  `/api/v1`; all config via env vars with a committed `.env.example`.
- **Record architecture decisions.** Whenever you make a design choice (a schema,
  a protocol, a trade-off), append an ADR to `docs/DECISIONS.md`. Never edit or
  renumber past entries — supersede them with a new one.
- **Log new/deferred work** in `docs/BACKLOG.md` instead of silently building it.
- **`main` is protected** (ADR-021): Dependabot + a ruleset requiring green CI on
  PRs. Don't skip hooks or CI.

---

## Current state (as of 2026-07-12)

- **v0.1.0** shipped (Sprints 0–10 + Sprint 11 mDNS discovery, ADR-018).
- **v0.2.0 + v0.2.1** released: full QA/security/resilience audit (health
  **86/100**), RAG grounding fix (ADR-019), rate-limit bypass fix (ADR-020),
  dependency automation (ADR-021).
- **v0.3.0** — production release audit complete (readiness **92/100**, see
  `docs/QA_v0.3.0.md`): zero-config startup + deployment hardening + production
  fail-fast on default secrets (ADR-022, closes #7), per-IP login brute-force
  throttle (ADR-023), dashboard theme no-flash. Backend `0.3.0`, dashboard
  `0.3.0+4`.
- **v0.3.0 + v0.3.1 released** (tags, GHCR images + desktop installers).
- **v0.4.0 RELEASED** (tag `v0.4.0`, GHCR image + 4 desktop installers) —
  knowledge-plane format fixes + multi-worker controller: structure-aware
  CSV/JSON loaders (ADR-024, #2), embedding benchmark (#3), `.docx`
  silent-corruption fix + binary-content guard (ADR-025, #28), scanned-PDF
  detection + opt-in `[ocr]` extra (ADR-026, #29), Redis-backed shared
  throttle windows (ADR-027) and multi-worker launch — cross-worker event
  bus, leader-gated background jobs, `WORKERS` fail-fast, `TRUSTED_PROXIES`
  (ADR-028, closes #4). Backend `0.4.0`, dashboard `0.4.0+6`; suite 280
  tests. The operator's live stack runs v0.4.0 with `WORKERS=2` + redis.
  Open items and next-work candidates live in `docs/BACKLOG.md`.

---

## Where the detail lives (read these to catch up)

| File | What it holds |
|---|---|
| [docs/DECISIONS.md](docs/DECISIONS.md) | Architecture decision log — ADR-001…028, every design choice and its rationale |
| [docs/BACKLOG.md](docs/BACKLOG.md) | Open work: GitHub issues, local backlog, ops/config follow-ups |
| [docs/AUDIT_v0.2.0.md](docs/AUDIT_v0.2.0.md) | v0.2.0 end-to-end QA/security/resilience audit report |
| [docs/QA_v0.3.0.md](docs/QA_v0.3.0.md) | v0.3.0 production release audit + readiness scorecard (92/100) |
| [.claude/command/Lycosa_Claude_Build_Playbook.md](.claude/command/Lycosa_Claude_Build_Playbook.md) | The original sprint-by-sprint build playbook (Sprints 0–11) |
| [.claude/command/Lycosa_Claude_Build_Playbook_v0.2.0.md](.claude/command/Lycosa_Claude_Build_Playbook_v0.2.0.md) | v0.2.0 QA/validation playbook |
| [.claude/command/Lycosa_Claude_Build_Playbook_v0.3.0.md](.claude/command/Lycosa_Claude_Build_Playbook_v0.3.0.md) | v0.3.0 playbook (current) |
| [README.md](README.md) · [CONTRIBUTING.md](CONTRIBUTING.md) | Install/usage front door; contribution rules |

**At the start of real work:** skim `docs/DECISIONS.md` (recent ADRs) and
`docs/BACKLOG.md`, then state current project state in a few bullets before
continuing.
