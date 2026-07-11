# Lycosa v0.3.0 — Production Release Audit & Readiness Report

**Date:** 2026-07-12
**Scope:** Production-readiness audit per the v0.3.0 playbook — scaffolding
discovery, security probe, load/stress regression, and release. Builds on the
v0.2.0 end-to-end audit (`docs/AUDIT_v0.2.0.md`, health 86/100).

---

## 1. Executive summary

v0.3.0 turns Lycosa from "runs on a trusted LAN once configured" into
**clone → run, safe by default**. The three shipped workstreams close the
highest-severity production gaps from the v0.2.0 audit:

- Zero-configuration startup with first-run generated secrets (ADR-022).
- Deployment hardening: datastores off the LAN, restart policies, log rotation,
  and a production fail-fast on default secrets — closing issue #7 (ADR-022).
- A per-IP login brute-force throttle (ADR-023).

No new defects were found in the security probe or the load regression. The
release is **ready for its LAN-first target**, with the same horizontal-scale
caveats as v0.2.0 (single uvicorn worker, in-process limiter) tracked in the
backlog.

**Readiness score: 92 / 100** (was 86/100 at v0.2.0 — see §6).

---

## 2. Step 1 — Production scaffolding audit

Inspected `infra/docker-compose.yml`, `.env.example`, `backend/app/core/config.py`.

| Area | v0.2.x state | v0.3.0 outcome |
|---|---|---|
| Mandatory `.env` | compose hard-required it | **Fixed** — committed `compose-defaults.env`; root `.env` optional |
| Placeholder secrets → prod | nothing stopped them | **Fixed** — fail-fast on default DB/JWT/admin secrets (#7) |
| Secret distribution | whole `.env` to every container incl. Grafana | **Improved** — Grafana reads only the optional `.env`; residual per-service scoping tracked in backlog |
| Datastore exposure | Postgres/Qdrant/Prometheus published to all interfaces; Qdrant unauthenticated | **Fixed** — bound to `127.0.0.1`; only API + Grafana LAN-exposed |
| Restart policy | none | **Fixed** — `restart: unless-stopped` on all services |
| Log rotation | unbounded json-file | **Fixed** — 10 MB × 3 per service |
| Health checks | present, sane intervals | unchanged (already good); Grafana now waits for healthy Prometheus |
| Secret generation | manual / installer only | **Added** — first-run generation, persisted in `api_data` volume |

Remaining scaffolding gaps (logged, not blocking for LAN): per-service env
scoping, auto-generated Qdrant API key (opt-in today; mitigated by localhost
binding), and TLS guidance for LAN deployments.

---

## 3. Step 2 — Security probe

| Probe target | Result |
|---|---|
| Auth brute-force / rate-limit effectiveness | **Gap found & fixed.** Global IP limiter + Argon2 slowed but didn't stop grinding a known account under a raised global budget. Added per-IP failed-login throttle (ADR-023): 429 + `Retry-After` + audit past the threshold; success clears the counter; per-IP to avoid admin-lockout DoS. |
| Document file-picker / parsing — path traversal, Zip Slip | **Not vulnerable.** Uploaded filenames are used only for `.pdf` suffix detection, error text, and inert Qdrant metadata; extraction is fully in-memory (`BytesIO`); no archive is ever extracted. |
| Node model-pull firewall boundary | **Not a bypass.** The agent's `POST /models/pull` is token-gated and controller-initiated; pulls are ordinary outbound fetches to the Ollama registry. |

Carried forward from v0.2.0 (still valid, all clean): SQLi (ORM),
command injection (fixed-arg subprocess only), authz/IDOR (node keys scoped to
own node), constant-time agent-token compare, `yaml.safe_load`. The
`X-API-Key` rate-limit bypass (F-2) remains fixed since v0.2.1 (ADR-020).

---

## 4. Step 3 — Load & stress regression

Goal was a **regression check** that the ADR-022/023 hardening didn't degrade
behavior under concurrency — not a fresh capacity benchmark (the v0.2.0 audit
already established the ceiling is client/loopback-bound on this hardware, not
the controller; real numbers need an off-host load tool, issue #5).

Method: fresh **zero-config** clone booted on a throwaway compose project
(shifted ports, separate volumes — live stack untouched); load client run in a
container on the stack's internal network hitting `api:8000` directly.

Raw capacity sweep (`/healthz`, rate-limit exempt), 9,000 requests total:

| Concurrency | Requests | Throughput | p50 | p95 | 5xx | conn errors |
|---:|---:|---:|---:|---:|---:|---:|
| 50 | 500 | 81 req/s | 337 ms | 1898 ms | 0 | 0 |
| 100 | 1000 | 89 req/s | 625 ms | 4442 ms | 0 | 0 |
| 250 | 2500 | 63 req/s | 2564 ms | 11236 ms | 0 | 2 |
| 500 | 5000 | 72 req/s | 5625 ms | 18563 ms | 0 | 1 |

Hardening correctness under load: 5 sequential valid logins → all `200` (the
login throttle does not accumulate on success); 50 concurrent authenticated
`GET /api/v1/nodes` → all `200` (localhost-bound datastores + internal-network
API path work under concurrency).

**Findings:** **0 5xx across all 9,000 requests, 0 API restarts, 0 error-level
logs.** API memory 118 MB → 145 MB and back to idle CPU afterwards — stable, no
leak. The handful of client-side connection errors at 250–500 concurrency and
the rising latency are the known single-worker + loopback ceiling (issues #4,
#5), unchanged from v0.2.0. **No regression from the v0.3.0 hardening.**

---

## 5. Step 4 — Release

- `CHANGELOG.md` — `## [0.3.0]` section (Keep a Changelog): Added / Changed /
  Security, with links updated. Drives the GitHub Release notes via the tag
  workflow.
- Versions bumped: backend `0.3.0`, dashboard `0.3.0+4`. Agent unchanged
  (`0.1.0` — no agent code changed this release).
- ADRs **022** (zero-config + hardening) and **023** (login throttle) recorded
  in `docs/DECISIONS.md`; `docs/BACKLOG.md` updated (#7 closed; residuals filed).
- Tag `v0.3.0` triggers `release.yml`: GHCR backend image + macOS/Windows/Linux
  desktop installers + GitHub Release.

---

## 6. Readiness scorecard

| Dimension | v0.2.0 | v0.3.0 | Notes |
|---|---:|---:|---|
| Secure defaults / secrets | 6 | 9 | first-run generation + prod fail-fast + datastores off-LAN |
| Zero-config install | 7 | 10 | clone → run, no `.env` |
| Auth hardening | 8 | 9 | per-IP login throttle added |
| Input/parsing safety | 9 | 9 | re-confirmed clean |
| Resilience / recovery | 9 | 9 | restart policies added; recovery already strong |
| Observability | 9 | 9 | + bounded log growth |
| Throughput / scale | 6 | 6 | unchanged: single worker, in-process limiter (#4, #5) |
| Docs / release hygiene | 9 | 10 | audit report + scorecard + ADRs |
| **Overall** | **86** | **92** | LAN-ready; scale caveats tracked |

## 7. Conditions & known limitations

- **Horizontal scale:** single uvicorn worker (#4) and in-process rate/login
  limiters (ADR-020/023) — a multi-worker or multi-host deployment needs a
  shared store and trusted-proxy `X-Forwarded-For` handling.
- **TLS:** all traffic is plain HTTP; for exposure beyond a trusted LAN, front
  the API with a TLS-terminating reverse proxy (guidance is a backlog item).
- **Qdrant auth** is opt-in (`QDRANT_API_KEY`); default posture relies on the
  new `127.0.0.1` binding.
- The live dev stack still runs pre-v0.3.0 images and a corrupted root `.env`
  (see backlog "Config / ops"); rebuild + rotate before any exposure.
