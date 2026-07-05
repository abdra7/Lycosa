#!/usr/bin/env bash
# Lycosa controller installer (headless stack: api, postgres, qdrant,
# prometheus, grafana). Safe to run from a clone or via:
#   curl -fsSL https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install.sh | bash
#
# Non-interactive overrides:
#   LYCOSA_ADMIN_EMAIL / LYCOSA_ADMIN_PASSWORD  admin credentials
#   LYCOSA_DIR                                  clone destination (default ./Lycosa)
set -euo pipefail

REPO_URL="https://github.com/abdra7/Lycosa.git"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

rand_hex() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$1"
    else
        head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'
    fi
}

# Prompt that works under `curl | bash` (stdin is the script) by reading
# from the terminal directly. Returns empty when no terminal is available.
ask() {
    local prompt="$1" silent="${2:-}" reply=""
    if [ -r /dev/tty ]; then
        if [ -n "$silent" ]; then
            read -r -s -p "$prompt" reply < /dev/tty > /dev/tty 2>&1 || true
            printf '\n' > /dev/tty
        else
            read -r -p "$prompt" reply < /dev/tty > /dev/tty 2>&1 || true
        fi
    fi
    printf '%s' "$reply"
}

# Replace KEY=... in .env (the key is known to exist in .env.example).
set_env() {
    local key="$1" value="$2" file="$3"
    # values are hex/emails — no sed metacharacters expected, but escape & and |
    local escaped
    escaped=$(printf '%s' "$value" | sed -e 's/[&|]/\\&/g')
    sed -i.bak "s|^${key}=.*|${key}=${escaped}|" "$file" && rm -f "${file}.bak"
}

say "Lycosa controller installer"

# --- prerequisites -----------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "Docker not found — install it first: https://docs.docker.com/get-docker/"
docker info >/dev/null 2>&1 || die "Docker is installed but the daemon is not running (or you lack permission)."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 not found — 'docker compose' must work."
command -v git >/dev/null 2>&1 || die "git not found — install it first."

# --- locate or clone the repo ------------------------------------------------
if [ -f "infra/docker-compose.yml" ]; then
    REPO_DIR="$(pwd)"
elif [ -f "../infra/docker-compose.yml" ] && [ "$(basename "$(pwd)")" = "scripts" ]; then
    REPO_DIR="$(cd .. && pwd)"
else
    REPO_DIR="${LYCOSA_DIR:-$(pwd)/Lycosa}"
    if [ -d "$REPO_DIR/.git" ]; then
        say "using existing clone at $REPO_DIR"
    else
        say "cloning Lycosa into $REPO_DIR"
        git clone "$REPO_URL" "$REPO_DIR"
    fi
fi
cd "$REPO_DIR"

# --- .env --------------------------------------------------------------------
if [ -f .env ]; then
    say ".env already exists — keeping it (delete it to re-generate)"
else
    say "creating .env from .env.example"
    cp .env.example .env

    JWT_SECRET=$(rand_hex 32)
    PG_PASSWORD=$(rand_hex 16)
    GRAFANA_PASSWORD=$(rand_hex 12)

    set_env JWT_SECRET "$JWT_SECRET" .env
    set_env POSTGRES_PASSWORD "$PG_PASSWORD" .env
    set_env DATABASE_URL "postgresql+asyncpg://lycosa:${PG_PASSWORD}@postgres:5432/lycosa" .env
    set_env GF_SECURITY_ADMIN_PASSWORD "$GRAFANA_PASSWORD" .env
    set_env ENVIRONMENT "production" .env

    ADMIN_EMAIL="${LYCOSA_ADMIN_EMAIL:-}"
    ADMIN_PASSWORD="${LYCOSA_ADMIN_PASSWORD:-}"
    if [ -z "$ADMIN_EMAIL" ]; then
        ADMIN_EMAIL=$(ask "Admin email [admin@lycosa.local]: ")
        ADMIN_EMAIL="${ADMIN_EMAIL:-admin@lycosa.local}"
    fi
    if [ -z "$ADMIN_PASSWORD" ]; then
        ADMIN_PASSWORD=$(ask "Admin password [generate one]: " silent)
        if [ -z "$ADMIN_PASSWORD" ]; then
            ADMIN_PASSWORD=$(rand_hex 12)
            GENERATED_ADMIN_PASSWORD="$ADMIN_PASSWORD"
        fi
    fi
    set_env DEFAULT_ADMIN_EMAIL "$ADMIN_EMAIL" .env
    set_env DEFAULT_ADMIN_PASSWORD "$ADMIN_PASSWORD" .env
fi

# --- bring the stack up ------------------------------------------------------
say "starting the controller stack (docker compose up --build -d)"
docker compose -f infra/docker-compose.yml up --build -d

say "waiting for the API to become healthy"
HEALTHY=""
for _ in $(seq 1 60); do
    if curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then
        HEALTHY=1
        break
    fi
    sleep 2
done
[ -n "$HEALTHY" ] || die "API did not become healthy within 120s — check: docker compose -f infra/docker-compose.yml logs api"

# --- report ------------------------------------------------------------------
LAN_IP=""
case "$(uname -s)" in
    Linux)  LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}') || true ;;
    Darwin) LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null) || true ;;
    MINGW*|MSYS*|CYGWIN*)  # Git Bash on Windows (install.ps1 is the native path)
        LAN_IP=$(ipconfig 2>/dev/null | grep -m1 'IPv4 Address' | sed 's/.*: //' | tr -d '\r') || true ;;
esac
LAN_IP="${LAN_IP:-<this-machine-ip>}"

ADMIN_EMAIL_SHOWN=$(grep '^DEFAULT_ADMIN_EMAIL=' .env | cut -d= -f2-)

printf '\n'
say "Lycosa controller is up"
cat <<EOF

  Controller URL (enter this in the desktop app):

      http://${LAN_IP}:8000

  Local endpoints:
      API docs     http://localhost:8000/docs
      Prometheus   http://localhost:9090
      Grafana      http://localhost:3001  (user: admin, password: in .env)

  Admin login:     ${ADMIN_EMAIL_SHOWN}
EOF
if [ -n "${GENERATED_ADMIN_PASSWORD:-}" ]; then
    printf '  Admin password:  %s  (generated — stored in .env)\n' "$GENERATED_ADMIN_PASSWORD"
fi
cat <<'EOF'

  Next steps:
    1. Download the Lycosa desktop app for your OS from the GitHub Releases page.
    2. Launch it, enter the controller URL above, and log in.
    3. Use "Add node" in the dashboard to join machines to the fabric.

  Manage the stack:
      docker compose -f infra/docker-compose.yml logs -f api    # logs
      docker compose -f infra/docker-compose.yml down           # stop
EOF
