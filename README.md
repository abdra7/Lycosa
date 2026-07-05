# Lycosa

**Lycosa** is a LAN-first, distributed multi-agent AI orchestration platform.
It turns multiple heterogeneous devices into one cooperative AI execution
fabric: each device runs a Local Agent that can host a local LLM (Ollama /
llama.cpp), tools, memory, and optionally a RAG knowledge base. A central
control plane discovers devices, recommends each node a role based on its
hardware, schedules tasks, routes knowledge requests, runs multi-step
workflows, and monitors everything from one dashboard.

**Node roles:** AI Compute · Hybrid · Knowledge · Tool · Vision · Storage

## Repository layout

| Directory | Contents |
|---|---|
| `backend/` | FastAPI control plane (orchestrator, scheduler, knowledge router, …) |
| `agent/` | Local Agent runtime installed on each node |
| `dashboard/` | Flutter Desktop operator dashboard (native macOS/Windows/Linux) |
| `infra/` | Docker Compose, Prometheus/Grafana config, future k8s manifests |
| `docs/` | Architecture decisions, roadmap, project docs |
| `scripts/` | Install and release tooling |

## Quick start (headless controller)

This brings up the **headless controller stack** only — API, PostgreSQL,
Qdrant, Prometheus, and Grafana. There is no web UI in this stack; the
operator dashboard is a separate native desktop application (see below).

Prerequisites: [Docker](https://docs.docker.com/get-docker/) with Compose v2.

```bash
git clone https://github.com/abdra7/Lycosa.git
cd Lycosa
cp .env.example .env          # then edit passwords/secrets
docker compose -f infra/docker-compose.yml up --build -d
```

Then open:

- **API docs (OpenAPI):** http://localhost:8000/docs
- **Health check:** http://localhost:8000/healthz
- **Prometheus:** http://localhost:9090
- **Grafana:** http://localhost:3001

To stop everything:

```bash
docker compose -f infra/docker-compose.yml down
```

## Dashboard (desktop app)

The Lycosa dashboard is a **native Flutter Desktop application** for
macOS, Windows, and Linux, installed separately from per-OS installers —
it is not part of the Docker stack. On first run it asks for the
controller API URL and credentials and connects over the LAN.
Build and install instructions land in a later sprint (see ADR-006 in
[docs/DECISIONS.md](docs/DECISIONS.md)).

## Development

Backend (Python 3.11+):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                        # run tests
ruff check . && ruff format --check .               # lint
uvicorn app.main:app --reload # run the API locally
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for branching, commit, and code
conventions, and [docs/DECISIONS.md](docs/DECISIONS.md) for the architecture
decision log.
