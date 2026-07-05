#!/usr/bin/env bash
# Lycosa Local Agent installer: python3.11+ + pipx + lycosa-agent
set -euo pipefail

REPO_URL="https://github.com/abdra7/Lycosa.git"

echo "==> Lycosa agent installer"

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found — install Python 3.11+ first" >&2
    exit 1
fi

PY_OK=$(python3 -c 'import sys; print(int(sys.version_info >= (3, 11)))')
if [ "$PY_OK" != "1" ]; then
    echo "error: Python 3.11+ required (found $(python3 --version))" >&2
    exit 1
fi

if ! command -v pipx >/dev/null 2>&1; then
    echo "==> installing pipx"
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> installing lycosa-agent"
pipx install --force "git+${REPO_URL}#subdirectory=agent"

cat <<'EOF'

Done. Next steps:
  1. Get a node API key from your Lycosa admin.
  2. Run:
       LYCOSA_CONTROLLER_URL=http://<controller-host>:8000 \
       LYCOSA_API_KEY=lyc_... \
       lycosa-agent run
EOF
