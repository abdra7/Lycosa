# scripts/

Install and release tooling.

| Script | Purpose |
|---|---|
| `install.sh` | Controller installer (macOS/Linux): checks Docker, clones the repo if needed, generates `.env` secrets, prompts for admin credentials, brings up the compose stack, prints the controller URL. Also works via `curl -fsSL .../install.sh \| bash`. |
| `install.ps1` | Same flow for Windows hosts (PowerShell). Also opens TCP 8000 in Windows Firewall (any profile) so dashboards/agents on other LAN devices can reach it. |
| `install-agent.sh` | Node-side agent installer (macOS/Linux): checks Python 3.11+, installs pipx if missing, installs `lycosa-agent`. |
| `install-agent.ps1` | Same flow for Windows hosts. Also opens UDP 5353 (mDNS discovery) and TCP 8010 (exec API) in Windows Firewall (any profile) so the dashboard's LAN scan can find this node. Run via `irm https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.ps1 \| iex`. |

Both Windows scripts add their firewall rules with a one-time UAC prompt if
not already running elevated; declining it just prints the equivalent
`New-NetFirewallRule` commands to run manually. Firewall rules are scoped to
"any" network profile, so a connection classified as Public (the Windows
default for new networks) doesn't silently break discovery — no need to
change the network to Private.

Non-interactive controller install (CI, provisioning):

```bash
LYCOSA_ADMIN_EMAIL=ops@example.com LYCOSA_ADMIN_PASSWORD=... ./scripts/install.sh
```

Re-running the installers is safe: an existing `.env` is kept, compose
converges, and admin seeding is idempotent.
