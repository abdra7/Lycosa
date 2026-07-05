# scripts/

Install and release tooling.

| Script | Purpose |
|---|---|
| `install.sh` | Controller installer (macOS/Linux): checks Docker, clones the repo if needed, generates `.env` secrets, prompts for admin credentials, brings up the compose stack, prints the controller URL. Also works via `curl -fsSL .../install.sh \| bash`. |
| `install.ps1` | Same flow for Windows hosts (PowerShell). |
| `install-agent.sh` | Node-side agent installer: checks Python 3.11+, installs pipx if missing, installs `lycosa-agent`. |

Non-interactive controller install (CI, provisioning):

```bash
LYCOSA_ADMIN_EMAIL=ops@example.com LYCOSA_ADMIN_PASSWORD=... ./scripts/install.sh
```

Re-running the installers is safe: an existing `.env` is kept, compose
converges, and admin seeding is idempotent.
