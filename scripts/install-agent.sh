#!/usr/bin/env bash
# Lycosa agent installer for Linux/macOS, including externally managed Python.
set -euo pipefail

PACKAGE="https://github.com/abdra7/Lycosa/archive/refs/heads/main.zip#subdirectory=agent"
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
echo "==> Lycosa agent installer"

PYTHON=""
for candidate in python3 python3.15 python3.14 python3.13 python3.12 python3.11 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if detected=$("$candidate" -c 'import sys; sys.exit(1) if sys.version_info < (3, 11) else print(sys.executable)' 2>/dev/null); then
            PYTHON="$detected"
            break
        fi
    fi
done
[ -n "$PYTHON" ] || fail "Python 3.11+ not found. Install it using your OS package manager or python.org."
printf '==> using Python: %s\n' "$PYTHON"

if "$PYTHON" -m pipx --version >/dev/null 2>&1; then
    PIPX=("$PYTHON" -m pipx)
elif command -v pipx >/dev/null 2>&1; then
    PIPX=(pipx)
else
    BOOTSTRAP="${XDG_DATA_HOME:-$HOME/.local/share}/lycosa/installer"
    printf '==> creating an isolated pipx environment: %s\n' "$BOOTSTRAP"
    "$PYTHON" -m venv "$BOOTSTRAP" || fail "Cannot create a venv. On Debian/Ubuntu install the matching python3-venv package; alternatively install pipx with your OS package manager."
    "$BOOTSTRAP/bin/python" -m pip install pipx || fail "Could not install pipx; check the download error above."
    PIPX=("$BOOTSTRAP/bin/python" -m pipx)
fi

echo "==> installing lycosa-agent (Git is not required)"
"${PIPX[@]}" install --force --python "$PYTHON" "$PACKAGE" || fail "Agent installation failed; check the download/build error above."
BIN_DIR=$("${PIPX[@]}" environment --value PIPX_BIN_DIR) || fail "Could not locate pipx's application directory."
[ -n "$BIN_DIR" ] || fail "pipx returned an empty application directory."
export PATH="$BIN_DIR:$PATH"
if ! "${PIPX[@]}" ensurepath; then
    echo "warn: Could not persist PATH. Use the full agent path below." >&2
fi
"$BIN_DIR/lycosa-agent" --help || fail "Agent was installed but could not start."

cat <<'EOF'

Done. Open a NEW terminal before using the short command below
(a curl | bash installer cannot update its parent shell's PATH):
  export LYCOSA_CONTROLLER_URL='http://192.168.1.10:8000'
  export LYCOSA_API_KEY='lyc_...'
  lycosa-agent run

Use your controller's LAN IP and a key from Nodes -> Add node.
For local inference, install/start Ollama separately; the agent does not install it.
Allow inbound TCP 8010 (execution) and UDP 5353 (discovery) in your firewall.
EOF
printf 'Full-path alternative (works in the current terminal): %q run\n' "$BIN_DIR/lycosa-agent"
