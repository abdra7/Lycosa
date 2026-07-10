# Lycosa Local Agent installer for Windows: checks Python 3.11+, installs pipx
# if missing, installs lycosa-agent, and opens the firewall ports LAN discovery
# and task dispatch need.
#   irm https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.ps1 | iex
$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/abdra7/Lycosa.git"

function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "warn: $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "error: $msg" -ForegroundColor Red; exit 1 }

Say "Lycosa agent installer"

# --- prerequisites -----------------------------------------------------------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fail "python not found - install Python 3.11+ first: https://www.python.org/downloads/"
}

$PyOk = & python -c "import sys; print(int(sys.version_info >= (3, 11)))"
if ($PyOk -ne "1") { Fail "Python 3.11+ required (found $(python --version))" }

if (-not (Get-Command pipx -ErrorAction SilentlyContinue)) {
    Say "installing pipx"
    python -m pip install --user pipx
    python -m pipx ensurepath
    $env:Path = "$env:Path;$env:USERPROFILE\.local\bin"
}

Say "installing lycosa-agent"
pipx install --force "git+$RepoUrl#subdirectory=agent"
if ($LASTEXITCODE -ne 0) { Fail "pipx install failed" }

# --- firewall ------------------------------------------------------------
# UDP 5353 (mDNS) lets the dashboard's LAN scan find this agent; TCP 8010 is
# the exec API the controller dispatches tasks to. Scoped to "any" profile so
# a Public-classified network connection doesn't silently break discovery.
Say "opening firewall ports for LAN discovery and task dispatch"
$FirewallCommand = @'
if (-not (Get-NetFirewallRule -DisplayName "Lycosa Agent (mDNS discovery)" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Lycosa Agent (mDNS discovery)" -Direction Inbound -Protocol UDP -LocalPort 5353 -Profile Any -Action Allow | Out-Null
}
if (-not (Get-NetFirewallRule -DisplayName "Lycosa Agent (exec API)" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Lycosa Agent (exec API)" -Direction Inbound -Protocol TCP -LocalPort 8010 -Profile Any -Action Allow | Out-Null
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
    Warn "could not add firewall rules automatically (elevation declined or blocked by policy)"
    Warn "run as Administrator to fix LAN discovery:"
    Warn '  New-NetFirewallRule -DisplayName "Lycosa Agent (mDNS discovery)" -Direction Inbound -Protocol UDP -LocalPort 5353 -Profile Any -Action Allow'
    Warn '  New-NetFirewallRule -DisplayName "Lycosa Agent (exec API)" -Direction Inbound -Protocol TCP -LocalPort 8010 -Profile Any -Action Allow'
}

Write-Host ""
Say "Done. Next steps (run each line separately in PowerShell):"
Write-Host "  1. Get a node API key from your Lycosa admin (dashboard: Nodes -> Add node)."
Write-Host "  2. Set the controller URL. Use the controller PC's LAN IP (run 'ipconfig'"
Write-Host "     there), NOT localhost -- localhost means THIS device, not the controller:"
Write-Host '       $env:LYCOSA_CONTROLLER_URL = "http://192.168.1.10:8000"'
Write-Host "  3. Set your key and start the agent:"
Write-Host '       $env:LYCOSA_API_KEY = "lyc_..."'
Write-Host "       lycosa-agent run"
Write-Host ""
Write-Host "  Tip: paste one line at a time. Do not add backslashes (\) at line ends --"
Write-Host "  that is bash syntax and PowerShell will error."
