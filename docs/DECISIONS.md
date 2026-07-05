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
