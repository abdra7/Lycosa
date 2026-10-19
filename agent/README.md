# Lycosa Local Agent

The installable runtime that turns a device into a Lycosa node: it collects
the machine's hardware profile, registers with the controller, sends
heartbeats with live health metrics, and exposes a token-protected local
execution API that the controller dispatches tasks to.

## Install

Requires Python 3.11+. With [pipx](https://pipx.pypa.io) (recommended):

```bash
pipx install "git+https://github.com/abdra7/Lycosa.git#subdirectory=agent"
```

Or the one-liner (checks Python, installs pipx if missing, then the agent):

```bash
curl -fsSL https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.sh | bash
```

## Run

1. Ask a Lycosa admin to mint a node API key:
   `POST /api/v1/admin/api-keys` with `{"name": "my-machine", "role": "node"}`.
2. Start the agent:

```bash
LYCOSA_CONTROLLER_URL=http://<controller-host>:8000 \
LYCOSA_API_KEY=lyc_xxxx_yyyy \
lycosa-agent run
```

or `lycosa-agent run --controller-url http://... --api-key lyc_...`

On startup the agent registers (the controller replies with a recommended
node role), then heartbeats every 15 s (interval is controller-tunable) and
serves its execution API on port `8010`.

## Configuration (env vars, `LYCOSA_` prefix)

| Variable | Default | Meaning |
|---|---|---|
| `LYCOSA_CONTROLLER_URL` | `http://localhost:8000` | Controller base URL |
| `LYCOSA_API_KEY` | — (required) | Node-role API key |
| `LYCOSA_NODE_NAME` | hostname | Display name for this node |
| `LYCOSA_HEARTBEAT_INTERVAL_SECONDS` | `15` | Initial heartbeat interval |
| `LYCOSA_EXEC_HOST` / `LYCOSA_EXEC_PORT` | `0.0.0.0` / `8010` | Execution API bind |
| `LYCOSA_ADVERTISE_URL` | autodetected LAN IP | URL the controller uses to reach this agent |
| `LYCOSA_DISCOVERY_ENABLED` | `true` | Announce this agent over mDNS for the dashboard LAN scan |
| `LYCOSA_OLLAMA_URL` | `http://localhost:11434` | Local Ollama instance |
| `LYCOSA_CLAUDE_SUBSCRIPTION` | `false` | Run this machine's own Claude Code for its owner (see below) |
| `LYCOSA_CLAUDE_SUBSCRIPTION_OWNER` | — | The Lycosa user email this machine's Claude plan serves (required when enabled) |
| `LYCOSA_CLAUDE_BINARY` | `claude` | Claude Code executable |
| `LYCOSA_CLAUDE_TIMEOUT_SECONDS` | `300` | Per-call limit; keep the controller's `LLM_REQUEST_TIMEOUT_SECONDS` at least this high |
| `LYCOSA_CLAUDE_MAX_CONCURRENCY` | `1` | Calls run at once (plans assume individual use) |

### Claude subscription runtime

With `LYCOSA_CLAUDE_SUBSCRIPTION=true` the agent runs the machine's own,
unmodified Claude Code for one Lycosa user, so they can use their Claude plan
in Lycosa. Sign in first with `claude auth login` on this machine; the agent
only reads `claude auth status` and never handles a Claude credential. Each
call runs `claude -p` in safe mode with no tools, no MCP servers and no
saved session, in an empty temporary directory, with `ANTHROPIC_API_KEY`
removed so the plan is used. The agent refuses requests for any other
Lycosa user.

## Run as a service (systemd)

Copy `packaging/lycosa-agent.service` to `/etc/systemd/system/`, put the env
vars in `/etc/lycosa/agent.env`, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lycosa-agent
```

## Development

```bash
cd agent
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
```
# Capability and MCP follow-up

See [CAPABILITIES.md](CAPABILITIES.md) for the optional local MCP companion, authenticated grounded-answer endpoint, limits and tests. Existing running agents must restart to load this increment.
