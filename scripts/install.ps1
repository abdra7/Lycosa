# Lycosa controller installer for Windows (headless stack: api, postgres,
# qdrant, prometheus, grafana). Run from a clone, or standalone:
#   irm https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install.ps1 | iex
#
# Non-interactive overrides (set before running):
#   $env:LYCOSA_ADMIN_EMAIL / $env:LYCOSA_ADMIN_PASSWORD   admin credentials
#   $env:LYCOSA_DIR                                        clone destination
$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/abdra7/Lycosa.git"

function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "error: $msg" -ForegroundColor Red; exit 1 }

function New-RandomHex($bytes) {
    $buf = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buf)
    return -join ($buf | ForEach-Object { $_.ToString("x2") })
}

function Set-EnvValue($key, $value, $file) {
    $content = Get-Content $file
    $content = $content -replace "^$([regex]::Escape($key))=.*", "$key=$value"
    Set-Content -Path $file -Value $content -Encoding utf8
}

Say "Lycosa controller installer"

# --- prerequisites -----------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker not found - install Docker Desktop first: https://docs.docker.com/get-docker/"
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker is installed but not running - start Docker Desktop." }
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker Compose v2 not found - 'docker compose' must work." }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git not found - install it first." }

# --- locate or clone the repo ------------------------------------------------
if (Test-Path "infra/docker-compose.yml") {
    $RepoDir = (Get-Location).Path
} elseif ((Split-Path -Leaf (Get-Location)) -eq "scripts" -and (Test-Path "../infra/docker-compose.yml")) {
    $RepoDir = (Resolve-Path "..").Path
} else {
    if ($env:LYCOSA_DIR) { $RepoDir = $env:LYCOSA_DIR } else { $RepoDir = Join-Path (Get-Location) "Lycosa" }
    if (Test-Path (Join-Path $RepoDir ".git")) {
        Say "using existing clone at $RepoDir"
    } else {
        Say "cloning Lycosa into $RepoDir"
        git clone $RepoUrl $RepoDir
        if ($LASTEXITCODE -ne 0) { Fail "git clone failed" }
    }
}
Set-Location $RepoDir

# --- .env --------------------------------------------------------------------
$GeneratedAdminPassword = $null
if (Test-Path ".env") {
    Say ".env already exists - keeping it (delete it to re-generate)"
} else {
    Say "creating .env from .env.example"
    Copy-Item ".env.example" ".env"

    $JwtSecret = New-RandomHex 32
    $PgPassword = New-RandomHex 16
    $GrafanaPassword = New-RandomHex 12

    Set-EnvValue "JWT_SECRET" $JwtSecret ".env"
    Set-EnvValue "POSTGRES_PASSWORD" $PgPassword ".env"
    Set-EnvValue "DATABASE_URL" "postgresql+asyncpg://lycosa:$PgPassword@postgres:5432/lycosa" ".env"
    Set-EnvValue "GF_SECURITY_ADMIN_PASSWORD" $GrafanaPassword ".env"
    Set-EnvValue "ENVIRONMENT" "production" ".env"

    $AdminEmail = $env:LYCOSA_ADMIN_EMAIL
    if (-not $AdminEmail) {
        $AdminEmail = Read-Host "Admin email [admin@lycosa.local]"
        if (-not $AdminEmail) { $AdminEmail = "admin@lycosa.local" }
    }
    $AdminPassword = $env:LYCOSA_ADMIN_PASSWORD
    if (-not $AdminPassword) {
        $secure = Read-Host "Admin password [generate one]" -AsSecureString
        $AdminPassword = [System.Net.NetworkCredential]::new("", $secure).Password
        if (-not $AdminPassword) {
            $AdminPassword = New-RandomHex 12
            $GeneratedAdminPassword = $AdminPassword
        }
    }
    Set-EnvValue "DEFAULT_ADMIN_EMAIL" $AdminEmail ".env"
    Set-EnvValue "DEFAULT_ADMIN_PASSWORD" $AdminPassword ".env"
}

# --- bring the stack up ------------------------------------------------------
Say "starting the controller stack (docker compose up --build -d)"
docker compose -f infra/docker-compose.yml up --build -d
if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed" }

Say "waiting for the API to become healthy"
$healthy = $false
foreach ($i in 1..60) {
    try {
        Invoke-WebRequest -Uri "http://localhost:8000/healthz" -UseBasicParsing -TimeoutSec 3 | Out-Null
        $healthy = $true
        break
    } catch {
        Start-Sleep -Seconds 2
    }
}
if (-not $healthy) {
    Fail "API did not become healthy within 120s - check: docker compose -f infra/docker-compose.yml logs api"
}

# --- firewall ------------------------------------------------------------
# Dashboards and agents on other LAN devices need to reach port 8000. Scoped
# to "any" profile so a Public-classified network connection doesn't block it.
Say "opening firewall port 8000 for LAN access"
$FirewallCommand = @'
if (-not (Get-NetFirewallRule -DisplayName "Lycosa Controller API" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Lycosa Controller API" -Direction Inbound -Protocol TCP -LocalPort 8000 -Profile Any -Action Allow | Out-Null
}
'@
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
try {
    if ($IsAdmin) {
        Invoke-Expression $FirewallCommand
    } else {
        Start-Process powershell -Verb RunAs -Wait -ErrorAction Stop `
            -ArgumentList "-NoProfile", "-Command", $FirewallCommand
    }
} catch {
    Write-Host "warn: could not add firewall rule for port 8000 automatically (elevation declined or blocked by policy)" -ForegroundColor Yellow
    Write-Host '  New-NetFirewallRule -DisplayName "Lycosa Controller API" -Direction Inbound -Protocol TCP -LocalPort 8000 -Profile Any -Action Allow' -ForegroundColor Yellow
}

# --- report ------------------------------------------------------------------
$LanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.PrefixOrigin -ne "WellKnown" } |
    Select-Object -First 1 -ExpandProperty IPAddress)
if (-not $LanIp) { $LanIp = "<this-machine-ip>" }

$AdminEmailShown = (Select-String -Path ".env" -Pattern "^DEFAULT_ADMIN_EMAIL=(.*)").Matches[0].Groups[1].Value

Write-Host ""
Say "Lycosa controller is up"
Write-Host ""
Write-Host "  Controller URL (enter this in the desktop app):"
Write-Host ""
Write-Host "      http://${LanIp}:8000" -ForegroundColor Green
Write-Host ""
Write-Host "  Local endpoints:"
Write-Host "      API docs     http://localhost:8000/docs"
Write-Host "      Prometheus   http://localhost:9090"
Write-Host "      Grafana      http://localhost:3001  (user: admin, password: in .env)"
Write-Host ""
Write-Host "  Admin login:     $AdminEmailShown"
if ($GeneratedAdminPassword) {
    Write-Host "  Admin password:  $GeneratedAdminPassword  (generated - stored in .env)"
}
Write-Host ""
Write-Host "  Next steps:"
Write-Host "    1. Download the Lycosa desktop app for your OS from the GitHub Releases page."
Write-Host "    2. Launch it, enter the controller URL above, and log in."
Write-Host "    3. Use 'Add node' in the dashboard to join machines to the fabric."
Write-Host ""
Write-Host "  Manage the stack:"
Write-Host "      docker compose -f infra/docker-compose.yml logs -f api    # logs"
Write-Host "      docker compose -f infra/docker-compose.yml down           # stop"
