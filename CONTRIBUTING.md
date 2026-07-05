# Contributing to Lycosa

Lycosa is built incrementally, one sprint/phase at a time. This document is
the single source of truth for how we structure the repo, branch, commit,
write Python, version the API, handle config, and gate merges. Every
contributor (human or agent) follows these conventions.

## Monorepo layout

```
/backend   FastAPI services (control plane: orchestrator, agent manager,
           scheduler, knowledge router, workflow engine, node recommendation)
/agent     Local Agent runtime — the installable unit that runs on each node
/dashboard Flutter / Flutter Web operator dashboard
/infra     docker compose, Kubernetes manifests, Prometheus/Grafana config
/docs      architecture decisions, roadmap, backlog, retros, primer
/scripts   install scripts, release tooling, one-off automation
```

New top-level directories require a decision recorded in `docs/DECISIONS.md`.

## Branching

Trunk-based development. `main` is always deployable.

- Feature/phase work happens on `feat/<sprint>-<slug>` branches
  (e.g. `feat/03-node-recommendation`).
- Bug fixes use `fix/<slug>`; chores use `chore/<slug>`.
- Merge to `main` via pull request. No direct pushes to `main` except for
  the initial scaffold commit.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` — new capability
- `fix:` — bug fix
- `chore:` — tooling, deps, config, no behavior change
- `docs:` — documentation only
- `test:` — tests only, no production code change
- `refactor:` — code change that neither fixes a bug nor adds a feature

Each phase ends with one squash-friendly commit summarizing that phase's
deliverable (e.g. `feat: node registration and hardware profile ingestion`).

## Python

- Python 3.11+.
- [`ruff`](https://docs.astral.sh/ruff/) for linting and formatting — one
  tool, one config, no separate black/isort/flake8.
- [`pytest`](https://docs.pytest.org/) for tests.
- [`pydantic` v2](https://docs.pydantic.dev/latest/) for all data models.
- FastAPI routes and service code are `async` by default.
- Type hints everywhere — public functions must be fully annotated.

## API contract

- Every endpoint is documented via FastAPI's generated OpenAPI schema
  (visible at `/docs` and `/openapi.json`).
- Routes live under a version prefix: `/api/v1/...`.
- Breaking changes to a resource's shape or semantics get a new prefix
  (`/api/v2/...`) rather than mutating `/api/v1` in place. Additive,
  backward-compatible changes (new optional fields, new endpoints) do not
  require a version bump.

## Config and secrets

- All configuration and secrets are supplied via environment variables.
- `.env.example` is committed and kept up to date with every variable the
  stack needs, documented with a comment and a safe placeholder/default.
- Real secrets (`.env`, credentials, keys) are never committed — see
  `.gitignore`.

## Tests gate merges

- No phase is "done" until its tests pass.
- Every phase's definition-of-done includes a green test run; CI enforces
  lint + test on every push/PR once CI exists (Sprint 0).
- A bug fix requires a failing test that reproduces the bug before the fix
  (red → green).

## Architecture decisions

Whenever a non-obvious architectural choice is made (a library, a schema
shape, a protocol, a trade-off), append an entry to `docs/DECISIONS.md`
before the phase is considered complete.
