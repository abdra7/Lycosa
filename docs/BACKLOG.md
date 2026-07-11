# Backlog

Open work for Lycosa. GitHub issues are linked by number; items without one are
local notes not yet filed. Full v0.2.0 QA detail lives in
[docs/AUDIT_v0.2.0.md](AUDIT_v0.2.0.md); architecture rationale in
[docs/DECISIONS.md](DECISIONS.md).

## Resolved (kept for reference)

- **Grounded RAG answers** — FIXED in v0.2.0 (ADR-019). Out-of-scope questions no
  longer hallucinate; the model returns the grounded refusal.
- **Rate-limit `X-API-Key` bypass (F-2)** — FIXED in v0.2.1 (ADR-020, issue #6
  closed). Limiter keys on client IP only.
- **Delete a decommissioned node** — shipped (`DELETE /api/v1/nodes/{id}` +
  dashboard admin action).
- **Fail-fast on default/placeholder secrets in production (#7)** — FIXED in
  v0.3.0 (ADR-022), together with zero-config startup (first-run generated
  secrets) and compose hardening (localhost-bound datastores, restart
  policies, log rotation).

## Open GitHub issues (from the v0.2.0 QA audit)

- **#2** RAG: structure-aware loaders for CSV/JSON (currently ingested as plain
  text) — *enhancement*
- **#3** RAG: benchmark precision/recall on the `fastembed` backend; the default
  `hashing` embedder is keyword-level only — *tech-debt*
- **#4** Controller: run multiple uvicorn/gunicorn workers (single worker today =
  throughput ceiling under concurrency) — *enhancement*
- **#5** QA: distributed load-testing harness (hey/k6) off-laptop for real
  capacity numbers — *tech-debt*
- **Login brute-force throttle** — SHIPPED in v0.3.0 (ADR-023): per-IP
  failed-login sliding window on `/auth/login`, 429 + audit past the threshold.
  (Security probe also re-confirmed no path-traversal/zip-slip on ingestion and
  a token-gated node model-pull path.)
- **#8** Security: document/harden the RAG prompt-injection trust boundary
  (currently operator-gated upload) — *security*
- **#9** Observability: return `503` (not opaque `500`) when a datastore is down
  — *enhancement*

## Local backlog (not yet filed as issues)

- Re-embed job: switching a knowledge collection's embedding backend requires
  re-ingestion (ADR-013).
- Redis-backed rate limiting + trusted-proxy `X-Forwarded-For` handling when the
  API scales horizontally or runs multi-worker (ADR-008 / ADR-020; ties to #4).
- Hot-reload of recommendation rules without an api restart (ADR-010).
- Async task queue behind `POST /tasks` returning 202 + polling (ADR-012).
- Shield synchronous workflow runs from client disconnects the same way
  `POST /tasks` is (Ticket #102 pattern); a long run cancelled by a dashboard
  timeout currently stays `running` forever.
- mTLS / enrollment handshake for agent exec API hardening (ADR-011).
- Workflow step-kind gaps (F-9): no `tool`/web-search or document-ingestion step
  kind, and nesting is one level (parallel substeps only). Blocks expressing the
  "Autonomous Researcher" / "Memory-Enhanced Agent" scenarios as workflows.
- Tech-debt (F-10): clear Starlette deprecation warnings
  (`HTTP_422_UNPROCESSABLE_ENTITY` → `HTTP_422_UNPROCESSABLE_CONTENT`;
  `starlette.testclient`/httpx warning). Still present after the 2026-07-11
  dependency bumps — confirmed on the python 3.14 image smoke test.
- **Deflake `test_client_disconnect_does_not_lose_completion`**
  (`backend/tests/test_tasks_api.py`): timing-sensitive, failed twice on
  unrelated Dependabot PRs (2026-07-11). Now that the `protect-main` ruleset
  requires green CI to merge (ADR-021), every flake blocks a merge until rerun.
- **Plan the Postgres 16 → 18 upgrade** (Dependabot PR #12, closed with
  ignore-major): the existing `postgres_data` volume is data-directory
  incompatible with 18; needs a dump/restore or `pg_upgrade` migration path,
  ideally scripted for existing installs. Re-enable the major when planned.
- ADR-022 residuals: per-service compose env scoping (postgres/api/grafana
  still receive the full root `.env` when one exists); auto-generated Qdrant
  API key shared between the qdrant service and the api (today Qdrant auth is
  opt-in via `QDRANT_API_KEY`, mitigated by the 127.0.0.1 port binding); TLS
  guidance for LAN deployments (reverse-proxy example).
- Align the Python version story: the backend image now runs
  `python:3.14-slim` (merged 2026-07-11) while CI, `requires-python`, and
  ruff `target-version` still say 3.11. Works (validated), but pick one:
  bump CI/tooling to 3.14 or pin the image back to the tested version.

## Config / ops (not code)

- Dependency automation is live (ADR-021): Dependabot weekly + security
  updates, `protect-main` ruleset requiring green CI on PRs. 2026-07-11 bumps
  merged (Actions, Prometheus v3.13, Grafana 13, Qdrant v1.18, python
  3.14-slim image) — the running stack won't pick them up until
  `docker compose -f infra/docker-compose.yml up -d --build --pull always`.
- The live dev stack runs with placeholder secrets (`JWT_SECRET`,
  `POSTGRES_PASSWORD`, Grafana) and a corrupted root `.env` (an accidental
  PowerShell paste clobbered `DEFAULT_ADMIN_PASSWORD` and left a node API key in
  the file). Fine on a trusted LAN; **rotate secrets and clean `.env` before any
  exposure.** Left untouched per instruction — see #7 for the code-side guard.
