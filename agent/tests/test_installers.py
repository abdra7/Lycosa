"""Execute the real installers with isolated fake package/download commands.

No network, user PATH, firewall, or installed applications are changed.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
GIT_BASH = Path("C:/Program Files/Git/bin/bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.exists() else shutil.which("bash")
POWERSHELLS = [p for p in (shutil.which("pwsh"), shutil.which("powershell")) if p]

BASH_MOCK = r"""#!/bin/bash
printf '<%s>' "$@" >> "$TEST_LOG"
printf '\n' >> "$TEST_LOG"
if [ "${1:-}" = "-c" ]; then
    [ "$TEST_CASE" != no_python ] || exit 1
    if [ "$TEST_CASE" = old_default ] && [ "$0" = "$FAKE_DEFAULT" ]; then exit 1; fi
    printf '%s\n' "$FAKE_PYTHON"
    exit 0
fi
if [ "${1:-}" = "-m" ]; then
    shift
    case "$1" in
        venv) [ "$TEST_CASE" != venv_fail ]; exit $? ;;
        pip) [ "$TEST_CASE" != pip_fail ]; exit $? ;;
        pipx) shift ;;
    esac
fi
case "${1:-}" in
    --version) [ "$TEST_CASE" = existing ]; exit $? ;;
    install) [ "$TEST_CASE" != install_fail ]; exit $? ;;
    environment) printf '%s\n' "$TEST_BIN" ;;
    ensurepath) [ "$TEST_CASE" != path_fail ]; exit $? ;;
    --help) [ "$TEST_CASE" != help_fail ]; exit $? ;;
esac
"""


@pytest.mark.skipif(not BASH, reason="Bash unavailable")
@pytest.mark.parametrize(
    "case,success",
    [
        ("existing", True),
        ("bootstrap", True),
        ("old_default", True),
        ("path_fail", True),
        ("no_python", False),
        ("venv_fail", False),
        ("pip_fail", False),
        ("install_fail", False),
        ("help_fail", False),
    ],
)
def test_bash_installer(tmp_path, case, success):
    bindir = tmp_path / "custom apps"
    bootstrap = tmp_path / "data" / "lycosa" / "installer" / "bin"
    commands = tmp_path / "commands"
    for folder in (bindir, bootstrap, commands):
        folder.mkdir(parents=True)
    for script in (
        commands / "python3",
        commands / "python3.11",
        bootstrap / "python",
        bindir / "lycosa-agent",
    ):
        script.write_text(BASH_MOCK, encoding="utf-8", newline="\n")
        script.chmod(0o755)
    # The final instruction block only needs cat; avoid the host's Python/pipx.
    cat = commands / "cat"
    cat.write_text('#!/bin/bash\nwhile IFS= read -r line; do printf "%s\\n" "$line"; done\n')
    cat.chmod(0o755)
    log = tmp_path / "commands.log"
    env = {
        **os.environ,
        "PATH": commands.as_posix(),
        "TEST_CASE": case,
        "TEST_LOG": log.as_posix(),
        "FAKE_PYTHON": (commands / "python3.11").as_posix(),
        "FAKE_DEFAULT": (commands / "python3").as_posix(),
        "TEST_BIN": bindir.as_posix(),
        "XDG_DATA_HOME": (tmp_path / "data").as_posix(),
    }
    result = subprocess.run(
        [BASH, str(SCRIPTS / "install-agent.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert (result.returncode == 0) is success, output
    assert ("Done." in output) is success, output
    calls = log.read_text()
    if success:
        assert "<--python><" + env["FAKE_PYTHON"] + ">" in calls
        assert "main.zip#subdirectory=agent" in calls
        assert "<--help>" in calls
        assert "NEW terminal" in output
    if case in {"no_python", "venv_fail", "pip_fail"}:
        assert "<--force>" not in calls
    assert "<--user>" not in calls


PS_MOCK = r"""
$ErrorActionPreference = 'Stop'
$env:LYCOSA_SKIP_FIREWALL = '1'
function FakePython {
    Add-Content -LiteralPath $env:TEST_LOG -Value ($args -join '|')
    $global:LASTEXITCODE = 0
    if ($args -contains '-c') {
        if ($env:TEST_CASE -eq 'no_python') { $global:LASTEXITCODE=1; return }
        'FakePython'; return
    }
    if ($args -contains '--version') {
        if ($env:TEST_CASE -ne 'existing') { $global:LASTEXITCODE=1 }
    } elseif ($args -contains 'venv') {
        if ($env:TEST_CASE -eq 'venv_fail') { $global:LASTEXITCODE=1 }
    } elseif ($args -contains 'pip') {
        if ($env:TEST_CASE -eq 'pip_fail') { $global:LASTEXITCODE=1 }
    } elseif ($args -contains '--force') {
        if ($env:TEST_CASE -eq 'install_fail') { $global:LASTEXITCODE=1 }
    } elseif ($args -contains 'environment') {
        Join-Path $env:TEST_ROOT 'custom apps'
    } elseif ($args -contains 'ensurepath') {
        if ($env:TEST_CASE -eq 'path_fail') { $global:LASTEXITCODE=1 }
    } elseif ($args -contains '--help') {
        if ($env:TEST_CASE -eq 'help_fail') { $global:LASTEXITCODE=1 }
    }
}
function py {
    if ($env:TEST_CASE -eq 'versioned_launcher' -and $args[0] -ne '-3.11') {
        $global:LASTEXITCODE=1; return
    }
    FakePython @args
}
function python { FakePython @args }
function python3 { FakePython @args }
function Get-Command {
    param($Name, $ErrorAction)
    if ($Name -eq 'pipx') {
        if ($env:TEST_CASE -eq 'pipx_command') { return [pscustomobject]@{Source='FakePython'} }
        return
    }
    if ($Name -eq 'py' -and $env:TEST_CASE -eq 'python_only') { return }
    if ($Name -in @('python','python3') -and $env:TEST_CASE -eq 'versioned_launcher') { return }
    Microsoft.PowerShell.Core\Get-Command $Name -ErrorAction SilentlyContinue
}
function Join-Path {
    param($Path, $ChildPath)
    if ($ChildPath -in @('Scripts\python.exe','lycosa-agent.exe')) { return 'FakePython' }
    Microsoft.PowerShell.Management\Join-Path $Path $ChildPath
}
function Start-Process {
    param($FilePath, $Verb, [switch]$Wait, [switch]$PassThru, $WindowStyle, $ArgumentList)
    $decoded = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($ArgumentList[-1]))
    $tokens=$null; $parseErrors=$null
    [void][Management.Automation.Language.Parser]::ParseInput($decoded,[ref]$tokens,[ref]$parseErrors)
    if ($parseErrors.Count) { throw 'Invalid firewall command' }
    Add-Content -LiteralPath $env:TEST_LOG -Value "firewall:$WindowStyle"
    if ($env:TEST_CASE -eq 'firewall_fail') { return [pscustomobject]@{ExitCode=1} }
    [pscustomobject]@{ExitCode=0}
}
if ($env:TEST_CASE -like 'firewall*') { $env:LYCOSA_SKIP_FIREWALL='0' }
try {
    Invoke-Expression (Get-Content -Raw -LiteralPath $env:TEST_INSTALLER)
} catch { Write-Host $_; exit 1 }
"""


@pytest.mark.skipif(not POWERSHELLS, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", POWERSHELLS)
@pytest.mark.parametrize(
    "case,success",
    [
        ("existing", True),
        ("bootstrap", True),
        ("path_fail", True),
        ("no_python", False),
        ("venv_fail", False),
        ("pip_fail", False),
        ("install_fail", False),
        ("help_fail", False),
        ("firewall_fail", True),
        ("firewall_ok", True),
        ("python_only", True),
        ("versioned_launcher", True),
        ("pipx_command", True),
    ],
)
def test_powershell_installer(tmp_path, shell, case, success):
    harness = tmp_path / "harness.ps1"
    harness.write_text(PS_MOCK, encoding="utf-8")
    log = tmp_path / "commands.log"
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
        ],
        env={
            **os.environ,
            "TEST_CASE": case,
            "TEST_LOG": str(log),
            "TEST_ROOT": str(tmp_path),
            "LOCALAPPDATA": str(tmp_path),
            "TEST_INSTALLER": str(SCRIPTS / "install-agent.ps1"),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert (result.returncode == 0) is success, output
    assert ("Done." in output) is success, output
    calls = log.read_text()
    if success:
        assert "--python|FakePython" in calls
        assert "main.zip#subdirectory=agent" in calls
        assert "--help" in calls
    if case in {"no_python", "venv_fail", "pip_fail"}:
        assert "--force" not in calls
    if case.startswith("firewall"):
        assert "firewall:Hidden" in calls
        assert ("firewall setup failed" in output) is (case == "firewall_fail")
