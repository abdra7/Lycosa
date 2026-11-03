# scripts/

Install and release tooling.

| Script | Purpose |
|---|---|
| `install.sh` | Controller installer (macOS/Linux): checks Docker, clones the repo if needed, generates `.env` secrets, prompts for admin credentials, brings up the compose stack, prints the controller URL. Also works via `curl -fsSL .../install.sh \| bash`. |
| `install.ps1` | Same flow for Windows hosts (PowerShell). Also opens TCP 8000 in Windows Firewall (any profile) so dashboards/agents on other LAN devices can reach it. |
| `install-agent.sh` | Node-side agent installer (macOS/Linux): selects Python 3.11+, bootstraps pipx in a venv if missing, installs the agent from a GitHub archive without Git, and checks the CLI. Open a new terminal afterward or use the printed full path. |
| `install-agent.ps1` | Same flow for Windows hosts. Also opens UDP 5353 (mDNS discovery) and TCP 8010 (exec API) in Windows Firewall (any profile) so the dashboard's LAN scan can find this node. Run via `irm https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.ps1 \| iex`. |

Both Windows scripts request elevation for their firewall rules. Declining
the prompt prints guidance for manual setup. Firewall rules are scoped to
"any" network profile, so a connection classified as Public (the Windows
default for new networks) doesn't silently break discovery — no need to
change the network to Private.

The Windows agent installer checks the firewall subprocess's exit code and
warns if setup failed. `LYCOSA_SKIP_FIREWALL=1` skips this step for managed
deployments. The agent installers respect custom pipx paths and do not install
Ollama; see [agent setup and troubleshooting](../agent/README.md).

Non-interactive controller install (CI, provisioning):

```bash
LYCOSA_ADMIN_EMAIL=ops@example.com LYCOSA_ADMIN_PASSWORD=... ./scripts/install.sh
```

Re-running the installers is safe: an existing `.env` is kept, compose
converges, and admin seeding is idempotent.
