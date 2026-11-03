# Lycosa agent installer (Windows PowerShell 5.1+ / PowerShell 7).
# irm https://raw.githubusercontent.com/abdra7/Lycosa/main/scripts/install-agent.ps1 | iex
# Keep helpers/preferences scoped even when invoked with iex.
& {
    $ErrorActionPreference = "Stop"
    $PSNativeCommandUseErrorActionPreference = $false
    $Package = "https://github.com/abdra7/Lycosa/archive/refs/heads/main.zip#subdirectory=agent"

    function Say($Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
    function Warn($Message) { Write-Host "warn: $Message" -ForegroundColor Yellow }
    function Invoke-Checked($Executable, [string[]]$Arguments, $Failure) {
        & $Executable @Arguments
        if ($LASTEXITCODE -ne 0) { throw "$Failure (exit $LASTEXITCODE)" }
    }

    Say "Lycosa agent installer"
    $Probe = "import sys; sys.exit(1) if sys.version_info < (3, 11) else print(sys.executable)"
    $Python = $null
    $Candidates = @(@("py", "-3"), @("python"), @("python3"))
    foreach ($Version in @("3.15", "3.14", "3.13", "3.12", "3.11")) {
        $Candidates += ,@("py", "-$Version")
    }
    foreach ($Candidate in $Candidates) {
        if (-not (Get-Command $Candidate[0] -ErrorAction SilentlyContinue)) { continue }
        try {
            $Arguments = @($Candidate | Select-Object -Skip 1) + @("-c", $Probe)
            $Detected = & $Candidate[0] @Arguments 2>$null
            if ($LASTEXITCODE -eq 0 -and $Detected) {
                $Python = ([string]$Detected).Trim()
                break
            }
        } catch { continue }
    }
    if (-not $Python) {
        throw "Python 3.11+ not found. Install it from https://www.python.org/downloads/ and reopen PowerShell."
    }
    Say "using Python: $Python"

    # Run pipx as a module: its Scripts directory need not be on PATH.
    # PS 5.1 can turn redirected native stderr into a terminating error.
    $HasPipx = $false
    try {
        & $Python -m pipx --version 2>$null
        $HasPipx = $LASTEXITCODE -eq 0
    } catch { $HasPipx = $false }
    if ($HasPipx) {
        $Pipx = $Python
        $PipxPrefix = @("-m", "pipx")
    } elseif (Get-Command pipx -ErrorAction SilentlyContinue) {
        $Pipx = (Get-Command pipx).Source
        $PipxPrefix = @()
    } else {
        $Bootstrap = Join-Path $env:LOCALAPPDATA "Lycosa\installer"
        Say "creating an isolated pipx environment: $Bootstrap"
        Invoke-Checked $Python @("-m", "venv", $Bootstrap) "Could not create the installer environment; check Python's venv support"
        $Pipx = Join-Path $Bootstrap "Scripts\python.exe"
        Invoke-Checked $Pipx @("-m", "pip", "install", "pipx") "Could not install pipx; check the download error above"
        $PipxPrefix = @("-m", "pipx")
    }

    Say "installing lycosa-agent (Git is not required)"
    Invoke-Checked $Pipx ($PipxPrefix + @("install", "--force", "--python", $Python, $Package)) "Agent installation failed; check the download/build error above"
    $BinDir = & $Pipx @PipxPrefix environment --value PIPX_BIN_DIR
    if ($LASTEXITCODE -ne 0 -or -not $BinDir) { throw "Could not locate pipx's application directory" }
    $BinDir = ([string]$BinDir).Trim()
    $env:Path = "$BinDir;$env:Path"
    & $Pipx @PipxPrefix ensurepath
    if ($LASTEXITCODE -ne 0) { Warn "Could not persist PATH. Use the full agent path printed below." }
    $Agent = Join-Path $BinDir "lycosa-agent.exe"
    Invoke-Checked $Agent @("--help") "Agent was installed but could not start"

    # EncodedCommand preserves quotes/spaces across Start-Process on PS 5.1.
    # Only firewall setup is elevated, never package installation.
    $FirewallCommand = @'
$ErrorActionPreference = "Stop"
try {
    if (-not (Get-NetFirewallRule -DisplayName "Lycosa Agent (mDNS discovery)" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "Lycosa Agent (mDNS discovery)" -Direction Inbound -Protocol UDP -LocalPort 5353 -Profile Any -Action Allow | Out-Null
    }
    if (-not (Get-NetFirewallRule -DisplayName "Lycosa Agent (exec API)" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "Lycosa Agent (exec API)" -Direction Inbound -Protocol TCP -LocalPort 8010 -Profile Any -Action Allow | Out-Null
    }
    exit 0
} catch { Write-Error $_ -ErrorAction Continue; exit 1 }
'@
    if ($env:LYCOSA_SKIP_FIREWALL -eq "1") {
        Warn "Firewall setup skipped. Allow inbound TCP 8010 and UDP 5353 for LAN access."
    } else {
        Say "opening firewall ports for LAN discovery and task dispatch"
        try {
            $Encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($FirewallCommand))
            $Process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -WindowStyle Hidden `
                -ArgumentList @("-NoProfile", "-NonInteractive", "-EncodedCommand", $Encoded)
            if ($Process.ExitCode -ne 0) { throw "Firewall setup exited with $($Process.ExitCode)" }
        } catch {
            Warn "Agent installed, but firewall setup failed or UAC was declined: $($_.Exception.Message)"
            Warn "Allow inbound UDP 5353 (discovery) and TCP 8010 (execution), or rerun this installer and accept UAC."
        }
    }

    Say "Done. Run each line separately in PowerShell:"
    Write-Host '  $env:LYCOSA_CONTROLLER_URL = "http://192.168.1.10:8000"'
    Write-Host '  $env:LYCOSA_API_KEY = "lyc_..."'
    Write-Host "  lycosa-agent run"
    $QuotedAgent = $Agent.Replace("'", "''")
    Write-Host "  Full-path alternative: & '$QuotedAgent' run"
    Write-Host "Use your controller's LAN IP and a key from Nodes -> Add node."
    Write-Host "For local inference, install/start Ollama separately; the agent does not install it."
}
