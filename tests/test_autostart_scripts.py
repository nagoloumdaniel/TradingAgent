"""The local autostart chain, executed for real: supervisor and installer.

The two scripts are PowerShell; they are actually run here, never read and assumed. A skip
keeps the Linux CI job honest while the Windows job exercises them.

What is deliberately **not** tested: anything that would modify the operator's machine. The
installer is only ever run with `-WhatIf`, the supervisor only with a temporary log directory
and a throwaway command. `-Stop` and the Startup-folder installer are verified by hand, with
their output recorded in `docs/operations/demarrage-automatique.md` § 8.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tools.detect_secrets_plugins.project_tokens import ProjectTokenAssignmentDetector

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
SUPERVISOR = "supervise_agent.ps1"
INSTALLER = "install_autostart.ps1"
#: The tests start their own supervisor. The real one holds its own lock on this machine, so
#: they ask for a different lock name instead of skipping: a suite that only passes while
#: nothing runs is a suite that lies about the machine it is supposed to protect.
TEST_MUTEX = r"Local\TradingAgentSupervisorTest"

pytestmark = pytest.mark.skipif(
    POWERSHELL is None, reason="PowerShell is required to exercise the operations scripts"
)

MANAGED_SCRIPTS = (
    SUPERVISOR,
    INSTALLER,
    "register_service.ps1",
    "check_health.ps1",
)


def run_script(name: str, *arguments: str, timeout: int = 180) -> subprocess.CompletedProcess:
    assert POWERSHELL is not None
    return subprocess.run(  # noqa: S603 - fixed interpreter, repository-owned script
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(SCRIPTS / name), *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def test_managed_scripts_exist() -> None:
    for name in MANAGED_SCRIPTS:
        assert (SCRIPTS / name).is_file(), name


@pytest.mark.parametrize("name", MANAGED_SCRIPTS)
def test_scripts_carry_no_secret_literal(name: str) -> None:
    detector = ProjectTokenAssignmentDetector()
    text = (SCRIPTS / name).read_text(encoding="utf-8")
    findings = [
        (number, line)
        for number, line in enumerate(text.splitlines(), start=1)
        if detector.analyze_line(filename=name, line=line, line_number=number)
    ]
    assert findings == []


@pytest.mark.parametrize("name", sorted(path.name for path in SCRIPTS.glob("*.ps1")))
def test_every_script_parses_under_the_installed_powershell(name: str) -> None:
    """An accented `.ps1` saved without a UTF-8 BOM does not survive Windows PowerShell 5.1.

    It reads the file as ANSI, so `—` becomes `â€"` — and that last character is a typographic
    closing quote, which *ends a string early*. The script then fails to parse, and the only
    way to know is to ask the parser. This test is that question.
    """
    command = (
        "$errors = $null; $tokens = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{SCRIPTS / name}', "
        "[ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors) { $errors | ForEach-Object { $_.Message }; exit 1 } else { 'ok' }"
    )
    assert POWERSHELL is not None
    result = subprocess.run(  # noqa: S603 - fixed interpreter, repository-owned script
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, f"{name}: {result.stdout}{result.stderr}"


def test_the_supervisor_refuses_an_incomplete_installation(tmp_path: Path) -> None:
    """No `.env` means no start-up, and the message says which file is missing."""
    result = run_script(
        SUPERVISOR,
        "-ProjectRoot",
        str(tmp_path),
        "-LogDirectory",
        str(tmp_path / "logs"),
        "-NoTerminal",
        "-MutexName",
        TEST_MUTEX,
    )
    assert result.returncode == 2
    assert "installation incomplète" in result.stderr
    assert ".env" in result.stderr


def test_the_supervisor_dry_run_names_the_production_command(tmp_path: Path) -> None:
    result = run_script(
        SUPERVISOR,
        "-LogDirectory",
        str(tmp_path),
        "-NoTerminal",
        "-DryRun",
    )
    assert result.returncode == 0, result.stderr
    assert "MODE SIMULATION" in result.stdout
    assert "tradingagent.app" in result.stdout
    assert "interpréteur" in result.stdout


def test_the_supervisor_propagates_the_exit_code_of_what_it_supervises(tmp_path: Path) -> None:
    """`-Once` runs the real chain: wrapper `.cmd`, redirection, exit code, state file."""
    assert POWERSHELL is not None
    command = Path(shutil.which("cmd") or r"C:\Windows\System32\cmd.exe")
    result = run_script(
        SUPERVISOR,
        "-Once",
        "-NoTerminal",
        "-IgnoreRunningAgent",
        "-PythonPath",
        str(command),
        "-AgentArguments",
        "/c exit 7",
        "-LogDirectory",
        str(tmp_path),
        "-MutexName",
        TEST_MUTEX,
    )
    assert result.returncode == 7, result.stdout + result.stderr

    state = json.loads((tmp_path / "superviseur.json").read_text(encoding="utf-8"))
    assert state["services"][0]["name"] == "agent"
    assert state["services"][0]["last_exit"] == 7
    # The wrapper is what the supervisor actually launches, and it must stay readable.
    wrapper = (tmp_path / "agent.cmd").read_text(encoding="ascii")
    assert ">>" in wrapper, "the service log is appended, never truncated"
    assert (tmp_path / "superviseur.log").is_file()


def test_the_installer_whatif_writes_nothing() -> None:
    """`-WhatIf` must be a real promise: no launcher, no task, on the operator's machine."""
    result = run_script(INSTALLER, "-WhatIf", "-WithDashboard")
    assert result.returncode == 0, result.stderr
    assert "TradingAgent.cmd" in result.stdout
    assert "WhatIf" in result.stdout


def test_the_installer_refuses_without_an_env_file(tmp_path: Path) -> None:
    # The supervisor is present, `.env` is not: that is the case being tested.
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / SUPERVISOR).write_text("# present\n", encoding="utf-8")

    result = run_script(INSTALLER, "-ProjectRoot", str(tmp_path))
    assert result.returncode == 2
    assert "aucun .env" in result.stderr


def test_the_installer_status_reports_and_exits_cleanly() -> None:
    """Read-only: it says what is installed, whatever the answer is."""
    result = run_script(INSTALLER, "-Status")
    assert "tâche planifiée" in result.stdout
    assert "dossier Démarrage" in result.stdout
