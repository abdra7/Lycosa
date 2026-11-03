# Lycosa Local Agent

The installable runtime that turns a device into a Lycosa node: it collects
the machine's hardware profile, registers with the controller, sends
heartbeats with live health metrics, and exposes a token-protected local
execution API that the controller dispatches tasks to.

## Install

Requires Python 3.11+ and internet access to GitHub and PyPI. The installers
select a compatible Python, reuse pipx if available or bootstrap it in an
isolated venv, and verify that the installed agent starts. Git is not required.

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.ps1 | iex
```

macOS / Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.sh | bash
```

Open a new terminal after the Bash installer, or use the full executable path
it prints. A child shell cannot update the current terminal's PATH. Windows
updates the current PowerShell PATH as well as asking pipx to persist it.

With an existing [pipx](https://pipx.pypa.io) installation, you can also install
directly (replace `python3.11` with your Python 3.11+ executable):

```bash
pipx install --python python3.11 "https://github.com/abdra7/Lycosa/archive/refs/heads/main.zip#subdirectory=agent"
```

For local inference, install and start Ollama separately. The agent installer
does not install Ollama; an agent without a working runtime may register but
cannot execute Ollama tasks. With an empty running Ollama, the agent attempts
to download a hardware-fit model on first start (`LYCOSA_AUTO_PULL_MODEL=false`
disables this).

### Installation troubleshooting

- **Python not found / too old:** install Python 3.11+ and reopen the terminal.
  The Windows installer also checks the `py` launcher; the Bash installer checks
  versioned executables when `python3` is older.
- **venv / ensurepip missing (Debian/Ubuntu):** install the venv package for the
  selected Python (for example `python3-venv`), or install pipx with the OS package
  manager, then rerun. The installer does not modify OS-managed Python packages.
- **Download/build fails:** the installer stops and retains the underlying
  pip/pipx error. Check access to GitHub and PyPI, proxy/certificate settings and
  the Python version reported by the installer.
- **Command not found:** use the full path printed at the end, or open a new
  terminal after successful `ensurepath`. Custom `PIPX_BIN_DIR` is respected.
- **Windows firewall warning:** package installation succeeded but network
  access may still be blocked. Rerun and accept UAC or configure inbound TCP
  8010 and UDP 5353 manually. Managed deployments can explicitly set
  `LYCOSA_SKIP_FIREWALL=1`; this does not open ports.
- **Registers but tasks cannot reach it:** allow TCP 8010 on the agent and
  verify its advertised address is reachable from the controller. With VPNs or
  multiple adapters, set `LYCOSA_ADVERTISE_URL` explicitly. Port 5353 is for
  discovery only; the controller URL must be reachable independently.

## Run

Windows PowerShell (replace the example URL and key):

```powershell
$env:LYCOSA_CONTROLLER_URL = "http://192.168.1.10:8000"
$env:LYCOSA_API_KEY = "lyc_xxxx_yyyy"
lycosa-agent run
```

macOS / Linux:

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

Find the installed executable with `command -v lycosa-agent` (or use the full
path printed by the installer). In `packaging/lycosa-agent.service`, replace
`ExecStart=/usr/local/bin/lycosa-agent run` with that absolute path and set
`User=` to the account that installed the agent. If the path contains spaces,
quote the executable path. The pipx default is usually under that user's
`~/.local/bin`, not `/usr/local/bin`.

Copy the edited service to `/etc/systemd/system/`, put the env vars in
`/etc/lycosa/agent.env`, then:

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
