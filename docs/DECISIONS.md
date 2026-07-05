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
