"""The configuration catalogue and the `doctor` report.

What is tested here is what an operator relies on: the report names every key it should,
it never prints a value, and it answers on a configuration that is not yet complete — which
is the only moment it is useful.
"""

from pathlib import Path

from sqlalchemy import Engine, create_engine

from tradingagent.config.doctor import (
    KEYS,
    SPECS,
    Diagnosis,
    KeyState,
    KeyStatus,
    diagnose,
    missing_required,
    read_env,
    render,
)
from tradingagent.config.settings import Settings
from tradingagent.control.cli import main


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    return path


def test_every_required_setting_is_documented() -> None:
    """Adding a required field without documenting it must fail here, not surprise an
    operator at start-up."""
    assert missing_required() == ()


def test_the_catalogue_documents_keys_settings_does_not_know() -> None:
    """TEST_DATABASE_URL, BACKUP_PASSPHRASE and the web token are used by the tests, the
    backup scripts and the dashboard — the agent never reads them, and they still belong in
    the report."""
    for name in ("TEST_DATABASE_URL", "BACKUP_PASSPHRASE", "TRADINGAGENT_WEB_TOKEN"):
        assert name in SPECS
    assert "TEST_DATABASE_URL" not in Settings.model_fields


def test_a_missing_file_is_an_empty_configuration_not_an_error(tmp_path: Path) -> None:
    diagnosis = diagnose(tmp_path / "absent.env")
    assert not diagnosis.ok
    assert {status.spec.name for status in diagnosis.blocked} == {
        name for name, spec in SPECS.items() if spec.required
    }


def test_a_complete_file_is_reported_as_ok(tmp_path: Path) -> None:
    content = "\n".join(f"{spec.name}=valeur" for spec in KEYS)
    diagnosis = diagnose(_write(tmp_path, content))
    assert diagnosis.ok
    assert all(status.state is KeyState.OK for status in diagnosis.keys)


def test_a_blank_value_counts_as_empty(tmp_path: Path) -> None:
    """`KEY=` is what a half-filled template looks like: it must not read as configured."""
    for blank in ("", " ", '""', "''"):
        diagnosis = diagnose(_write(tmp_path, f"MT5_LOGIN={blank}\n"))
        status = next(s for s in diagnosis.keys if s.spec.name == "MT5_LOGIN")
        assert status.state is KeyState.EMPTY, repr(blank)


def test_no_value_is_ever_printed(tmp_path: Path) -> None:
    # Obvious nonsense, and the only thing it can do here is prove it stays out of the
    # report. The scanner flags the shape, not the meaning, hence the allowlist.
    secret = "sk-ant-super-secret-value"  # pragma: allowlist secret
    diagnosis = diagnose(_write(tmp_path, f"MT5_LOGIN=12345\nANTHROPIC_API_KEY={secret}\n"))
    report = render(diagnosis)
    assert secret not in report
    assert "12345" not in report
    assert "ANTHROPIC_API_KEY" in report  # the name is printed, never the value


def test_the_report_says_where_to_get_a_missing_key(tmp_path: Path) -> None:
    report = render(diagnose(tmp_path / "absent.env"))
    assert "MT5_SERVER" in report
    assert "Deriv" in report  # the instructions travel with the report
    assert "TEST_DATABASE_URL" in report


def test_the_report_points_at_a_safe_test_database(tmp_path: Path) -> None:
    """The one instruction that can destroy data if read too quickly."""
    report = render(diagnose(tmp_path / "absent.env"))
    assert "_test" in report
    assert "détruisent" in report


def test_comments_and_blank_lines_are_ignored(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "# a comment\n\nMT5_LOGIN=42\n   \n# MT5_SERVER=not-this-one\nMT5_SERVER=Deriv-Demo\n",
    )
    values = read_env(path)
    assert values == {"MT5_LOGIN": "42", "MT5_SERVER": "Deriv-Demo"}


def test_quoted_values_are_read_and_reported_as_set(tmp_path: Path) -> None:
    diagnosis = diagnose(_write(tmp_path, 'MT5_SERVER="Deriv-Demo"\n'))
    status = next(s for s in diagnosis.keys if s.spec.name == "MT5_SERVER")
    assert status.state is KeyState.OK


def test_status_and_diagnosis_agree_on_the_required_set() -> None:
    status = KeyStatus(spec=SPECS["MT5_LOGIN"], state=KeyState.MISSING)
    assert status.blocked
    optional = KeyStatus(spec=SPECS["BACKUP_PASSPHRASE"], state=KeyState.MISSING)
    assert not optional.blocked
    assert Diagnosis(keys=(optional,)).ok
    assert not Diagnosis(keys=(optional, status)).ok


def test_doctor_exits_zero_on_a_complete_configuration(tmp_path: Path, capsys) -> None:
    content = "\n".join(f"{spec.name}=valeur" for spec in KEYS)
    path = _write(tmp_path, content)
    engine = create_engine("sqlite://")
    code = main(["doctor"], engine_factory=lambda: engine, env_path=path)
    assert code == 0
    assert "Configuration complète" in capsys.readouterr().out


def test_doctor_exits_one_and_names_the_missing_keys(tmp_path: Path, capsys) -> None:
    code = main(
        ["doctor"],
        engine_factory=lambda: create_engine("sqlite://"),
        env_path=tmp_path / "absent.env",
    )
    output = capsys.readouterr().out
    assert code == 1
    assert "MT5_LOGIN" in output
    assert "clé(s) obligatoire(s) manquante(s)" in output


def test_doctor_survives_an_unreachable_database(tmp_path: Path, capsys) -> None:
    """A wrong password or a dead network is a diagnosis, not a crash: the whole point is to
    be runnable on a configuration that does not work yet."""
    content = "\n".join(f"{spec.name}=valeur" for spec in KEYS)

    def broken() -> Engine:
        raise RuntimeError("no route to host")

    code = main(["doctor"], engine_factory=broken, env_path=_write(tmp_path, content))
    output = capsys.readouterr().out
    assert code == 0
    assert "injoignable" in output
    assert "RuntimeError" in output


def test_doctor_reports_the_ea_directory(tmp_path: Path, capsys) -> None:
    content = "\n".join(f"{spec.name}=valeur" for spec in KEYS)
    content += f"\nEA_FILES_DIR={tmp_path}\n"
    main(
        ["doctor"],
        engine_factory=lambda: create_engine("sqlite://"),
        env_path=_write(tmp_path, content),
    )
    assert "répertoire EA: présent" in capsys.readouterr().out
