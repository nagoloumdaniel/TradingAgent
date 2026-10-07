"""Tests for TASK-053: backup/restore scripts refuse missing secrets and round-trip.

The scripts are PowerShell; they are actually executed here. A skip keeps the Linux CI
job honest while the Windows job runs them for real.
"""

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from tools.detect_secrets_plugins.project_tokens import ProjectTokenAssignmentDetector

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
PASSPHRASE = "integration-passphrase-not-a-secret"

pytestmark = pytest.mark.skipif(
    POWERSHELL is None, reason="PowerShell is required to exercise the operations scripts"
)

MANAGED_SCRIPTS = (
    "backup.ps1",
    "restore.ps1",
    "install_windows.ps1",
    "install_deps.ps1",
    "register_service.ps1",
    "check_health.ps1",
)


def run_script(name: str, *arguments: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    assert POWERSHELL is not None
    return subprocess.run(  # noqa: S603 - fixed interpreter, repository-owned script
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(SCRIPTS / name), *arguments],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )


def clean_environment(**values: str) -> dict[str, str]:
    env = os.environ.copy()
    for name in ("DATABASE_URL", "BACKUP_PASSPHRASE", "BACKUP_DIR", "MT5_TERMINAL_PATH"):
        env.pop(name, None)
    env.update(values)
    return env


def sqlite_url(path: Path) -> str:
    return "sqlite:///" + path.as_posix()


def make_database(path: Path, value: int) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE sample (value INTEGER)")
        connection.execute("INSERT INTO sample VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def read_value(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT value FROM sample").fetchone()
    finally:
        connection.close()
    return int(row[0])


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


def test_backup_reads_its_settings_from_an_env_file(tmp_path: Path) -> None:
    """The feature the operators were promised: a key set in `.env` is used.

    Nothing is exported in the environment here — only the file speaks, which is exactly
    how the documented setup works.
    """
    source = tmp_path / "source.db"
    make_database(source, 3)
    env_file = tmp_path / "operator.env"
    env_file.write_text(
        f"DATABASE_URL={sqlite_url(source)}\nBACKUP_PASSPHRASE=passphrase-from-the-file\n",
        encoding="utf-8",
    )
    backups = tmp_path / "backups"
    result = run_script(
        "backup.ps1",
        "-EnvFile",
        str(env_file),
        "-BackupDir",
        str(backups),
        env=clean_environment(),
    )
    assert result.returncode == 0, result.stderr
    assert list(backups.glob("*.enc")), "an encrypted archive should exist"


def test_the_environment_still_overrides_the_env_file(tmp_path: Path) -> None:
    """Same precedence as everywhere else in the project: environment, then file."""
    source = tmp_path / "source.db"
    make_database(source, 4)
    env_file = tmp_path / "operator.env"
    env_file.write_text(
        f"DATABASE_URL={sqlite_url(source)}\nBACKUP_PASSPHRASE=the-file-one\n",
        encoding="utf-8",
    )
    backups = tmp_path / "backups"
    result = run_script(
        "backup.ps1",
        "-EnvFile",
        str(env_file),
        "-BackupDir",
        str(backups),
        env=clean_environment(BACKUP_PASSPHRASE="the-environment-one"),
    )
    assert result.returncode == 0, result.stderr
    archive = next(backups.glob("*.enc"))
    # Restoring with the file's passphrase must fail: the environment's was used.
    restored = tmp_path / "restored.db"
    wrong = run_script(
        "restore.ps1",
        "-BackupFile",
        str(archive),
        "-Provider",
        "sqlite",
        "-DatabaseUrl",
        sqlite_url(restored),
        "-Passphrase",
        "the-file-one",
        "-Force",
        env=clean_environment(),
    )
    assert wrong.returncode != 0
    assert not restored.exists()


def test_backup_refuses_a_missing_database_url(tmp_path: Path) -> None:
    result = run_script(
        "backup.ps1",
        "-DryRun",
        "-BackupDir",
        str(tmp_path / "backups"),
        # The script falls back on the repository's `.env`; this test is about the case
        # where nothing is configured at all, environment or file.
        "-EnvFile",
        str(tmp_path / "absent.env"),
        env=clean_environment(),
    )
    assert result.returncode == 2
    assert "DATABASE_URL" in result.stderr


def test_backup_refuses_a_missing_passphrase(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    make_database(source, 1)
    result = run_script(
        "backup.ps1",
        "-BackupDir",
        str(tmp_path / "backups"),
        # Point the `.env` fallback at a file that does not exist: the script reads the
        # repository's own `.env` otherwise, and this test is precisely about the case
        # where no passphrase is configured anywhere.
        "-EnvFile",
        str(tmp_path / "absent.env"),
        env=clean_environment(DATABASE_URL=sqlite_url(source)),
    )
    assert result.returncode == 2
    assert "BACKUP_PASSPHRASE" in result.stderr


def test_backup_dry_run_writes_nothing(tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    source = tmp_path / "source.db"
    make_database(source, 1)
    result = run_script(
        "backup.ps1",
        "-DryRun",
        "-BackupDir",
        str(backup_dir),
        env=clean_environment(DATABASE_URL=sqlite_url(source), BACKUP_PASSPHRASE=PASSPHRASE),
    )
    assert result.returncode == 0, result.stderr
    assert "MODE SIMULATION" in result.stdout
    assert not backup_dir.exists()


def test_the_postgres_dump_is_scoped_to_the_project_schema(tmp_path: Path) -> None:
    """A whole-database dump also carries Supabase's `auth`, `storage` and `realtime`.

    Restoring that with `--clean` tried to drop and recreate objects owned by another role,
    so the archive could not be restored into a fresh Supabase project. The project's tables
    all live in `public`, and the scope is stated rather than implied.
    """
    result = run_script(
        "backup.ps1",
        "-DryRun",
        "-BackupDir",
        str(tmp_path / "backups"),
        env=clean_environment(
            # No credentials in the URL: the script only reads its scheme to pick the
            # provider, and `-DryRun` prints the plan without connecting to anything.
            DATABASE_URL="postgresql://example.invalid:5432/postgres",
            BACKUP_PASSPHRASE=PASSPHRASE,
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "--schema=public" in result.stdout
    assert "pg_dump" in result.stdout


def test_the_dump_schema_can_be_overridden(tmp_path: Path) -> None:
    result = run_script(
        "backup.ps1",
        "-DryRun",
        "-Schema",
        "autre_schema",
        "-BackupDir",
        str(tmp_path / "backups"),
        env=clean_environment(
            DATABASE_URL="postgresql://example.invalid:5432/postgres",
            BACKUP_PASSPHRASE=PASSPHRASE,
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "--schema=autre_schema" in result.stdout


def test_restore_refuses_a_missing_backup_file(tmp_path: Path) -> None:
    result = run_script(
        "restore.ps1",
        "-BackupFile",
        str(tmp_path / "absent.enc"),
        "-Force",
        env=clean_environment(DATABASE_URL=sqlite_url(tmp_path / "target.db")),
    )
    assert result.returncode == 2
    assert "introuvable" in result.stderr


def test_restore_refuses_a_missing_passphrase(tmp_path: Path) -> None:
    archive = tmp_path / "backup.enc"
    archive.write_bytes(b"placeholder, the passphrase check comes first")
    result = run_script(
        "restore.ps1",
        "-BackupFile",
        str(archive),
        "-Force",
        # Same reason as the backup case: isolate the script from the repository's `.env`.
        "-EnvFile",
        str(tmp_path / "absent.env"),
        env=clean_environment(DATABASE_URL=sqlite_url(tmp_path / "target.db")),
    )
    assert result.returncode == 2
    assert "BACKUP_PASSPHRASE" in result.stderr


def test_backup_then_restore_round_trips_a_sqlite_database(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    make_database(source, 42)
    backup_dir = tmp_path / "backups"
    environment = clean_environment(
        DATABASE_URL=sqlite_url(source),
        BACKUP_PASSPHRASE=PASSPHRASE,
        BACKUP_DIR=str(backup_dir),
    )

    backed_up = run_script("backup.ps1", env=environment)
    assert backed_up.returncode == 0, backed_up.stderr
    archives = sorted(backup_dir.glob("tradingagent-*.dump.enc"))
    assert len(archives) == 1
    assert archives[0].with_suffix(".enc.sha256").is_file()
    # The archive must not contain the plaintext database in clear.
    assert b"SQLite format 3" not in archives[0].read_bytes()

    target = tmp_path / "restored" / "target.db"
    environment["DATABASE_URL"] = sqlite_url(target)
    restored = run_script("restore.ps1", "-BackupFile", str(archives[0]), "-Force", env=environment)
    assert restored.returncode == 0, restored.stderr
    assert read_value(target) == 42


def test_restore_refuses_a_wrong_passphrase(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    make_database(source, 7)
    backup_dir = tmp_path / "backups"
    environment = clean_environment(
        DATABASE_URL=sqlite_url(source),
        BACKUP_PASSPHRASE=PASSPHRASE,
        BACKUP_DIR=str(backup_dir),
    )
    assert run_script("backup.ps1", env=environment).returncode == 0
    archive = next(backup_dir.glob("tradingagent-*.dump.enc"))

    environment["DATABASE_URL"] = sqlite_url(tmp_path / "target.db")
    environment["BACKUP_PASSPHRASE"] = "the-wrong-passphrase"
    result = run_script("restore.ps1", "-BackupFile", str(archive), "-Force", env=environment)
    assert result.returncode == 6
    assert "HMAC" in result.stderr
    assert not (tmp_path / "target.db").exists()


def test_restore_refuses_an_existing_database_without_force(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    make_database(source, 3)
    target = tmp_path / "target.db"
    make_database(target, 99)
    backup_dir = tmp_path / "backups"
    environment = clean_environment(
        DATABASE_URL=sqlite_url(source),
        BACKUP_PASSPHRASE=PASSPHRASE,
        BACKUP_DIR=str(backup_dir),
    )
    assert run_script("backup.ps1", env=environment).returncode == 0
    archive = next(backup_dir.glob("tradingagent-*.dump.enc"))

    environment["DATABASE_URL"] = sqlite_url(target)
    result = run_script("restore.ps1", "-BackupFile", str(archive), env=environment)
    assert result.returncode == 7
    assert read_value(target) == 99
