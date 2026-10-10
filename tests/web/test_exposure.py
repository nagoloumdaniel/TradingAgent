"""A dashboard with no credential must not be published beyond this machine.

The decision this file pins, made on 2026-10-10: with ``TRADINGAGENT_WEB_TOKEN`` blank the
dashboard stays **open** — that is the documented, deliberate default on ``127.0.0.1``, where
"reaching the port" is already the whole authentication — but the open surface stops at the
loopback interface. ``--host 0.0.0.0`` with no token used to be accepted in silence: every
figure of the account, readable by anything that could route to the machine, with no warning
anywhere. The bind address was the only thing standing between the figures and the network,
and it was one command-line flag away from being removed.

The rule is fail-closed: the process refuses to start and says what to set. Nothing is
silently downgraded to ``127.0.0.1`` either — an operator who asked for a public bind and got
a private one would believe the dashboard is reachable when it is not.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from tradingagent.web import app as web_app
from tradingagent.web.auth import ACCESS_ENV_VAR, exposure_problem

# A test value, not a credential: it only ever exists inside this file and a temporary `.env`.
TOKEN = "un-jeton-de-test-suffisamment-long"  # pragma: allowlist secret

#: Every interface. Bandit flags the literal (S104); here it is the whole point of the file —
#: the address the dashboard must refuse to listen on while it has no credential.
ANY_INTERFACE = "0.0.0.0"  # noqa: S104


def _write_env(tmp_path: Path, *, with_token: bool) -> Path:
    lines = [f"DATABASE_URL=sqlite:///{tmp_path / 'agent.db'}"]
    if with_token:
        lines.append(f"TRADINGAGENT_WEB_TOKEN={TOKEN}")
    path = tmp_path / ".env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class _EntryPoint:
    """The result of running the real ``main()``: what uvicorn was handed, and whether the
    database was opened at all."""

    def __init__(self) -> None:
        self.code: int | None = None
        self.application: object | None = None
        self.engine_built = False


class _SpyEngine:
    """Enough of an engine for ``create_app``, which opens no connection at build time."""

    def dispose(self) -> None:
        pass


def _run_entry(
    monkeypatch: pytest.MonkeyPatch, env_path: Path, argv: Sequence[str] = ()
) -> _EntryPoint:
    entry = _EntryPoint()
    monkeypatch.setattr(web_app, "ENV_FILE", env_path)

    def fake_run(application: object, **kwargs: object) -> None:
        entry.application = application

    def fake_engine(url: str) -> object:
        entry.engine_built = True
        return _SpyEngine()

    monkeypatch.setattr(web_app.uvicorn, "run", fake_run)
    monkeypatch.setattr(web_app, "create_database_engine", fake_engine)
    entry.code = web_app.main(list(argv))
    return entry


# --- the rule ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.0.0.2", "::1", "localhost", "localhost.", "LOCALHOST"],
)
def test_a_loopback_bind_needs_no_credential(host: str) -> None:
    """`localhost`, the whole 127/8 block and `::1`: reaching the port is the authentication."""
    assert exposure_problem(host, None) is None


@pytest.mark.parametrize(
    "host",
    [ANY_INTERFACE, "::", "", "   ", "192.168.1.10", "10.0.0.5", "dashboard.lan", "127.0.0.1.evil"],
)
def test_any_other_bind_without_a_credential_is_refused(host: str) -> None:
    """Including the near-misses: `0.0.0.0` is not loopback, and neither is a hostname that
    merely starts like one. A blank host is refused rather than trusted."""
    problem = exposure_problem(host, None)

    assert problem is not None
    assert ACCESS_ENV_VAR in problem  # the remedy names the variable to set
    assert "127.0.0.1" in problem  # …and the safe alternative


def test_a_declared_credential_makes_every_bind_acceptable() -> None:
    for host in (ANY_INTERFACE, "::", "192.168.1.10"):
        assert exposure_problem(host, TOKEN) is None


def test_a_blank_credential_counts_as_absent() -> None:
    """`TRADINGAGENT_WEB_TOKEN=` is not protection, and must not be read as one."""
    assert exposure_problem(ANY_INTERFACE, "   ") is not None


def test_the_refusal_never_carries_the_credential() -> None:
    """No message is ever built when a token is set — the token cannot leak through this
    path. With no token there is nothing to leak."""
    assert exposure_problem(ANY_INTERFACE, TOKEN) is None
    assert TOKEN not in (exposure_problem(ANY_INTERFACE, None) or "")


# --- the entry point --------------------------------------------------------------------


def test_the_default_bind_still_starts_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _run_entry(monkeypatch, _write_env(tmp_path, with_token=False))

    assert entry.code == 0
    assert entry.application is not None
    assert entry.engine_built is True


def test_an_explicit_loopback_bind_still_starts_without_a_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _run_entry(monkeypatch, _write_env(tmp_path, with_token=False), ["--host", "127.0.0.1"])

    assert entry.code == 0
    assert entry.application is not None


def test_a_public_bind_without_a_token_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = _run_entry(
        monkeypatch, _write_env(tmp_path, with_token=False), ["--host", ANY_INTERFACE]
    )

    captured = capsys.readouterr()
    assert entry.code == 2
    assert entry.application is None  # uvicorn was never reached
    assert entry.engine_built is False  # and neither was the database
    assert ACCESS_ENV_VAR in captured.err
    assert ANY_INTERFACE in captured.err


def test_a_public_bind_starts_once_a_token_is_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _run_entry(
        monkeypatch, _write_env(tmp_path, with_token=True), ["--host", ANY_INTERFACE]
    )

    assert entry.code == 0
    assert entry.application is not None


def test_a_blank_token_with_a_public_bind_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_env(tmp_path, with_token=False)
    path.write_text(
        path.read_text(encoding="utf-8") + "TRADINGAGENT_WEB_TOKEN=\n", encoding="utf-8"
    )

    entry = _run_entry(monkeypatch, path, ["--host", ANY_INTERFACE])

    assert entry.code == 2
    assert entry.application is None


def test_the_rule_is_discoverable_from_the_command_line(capsys: pytest.CaptureFixture[str]) -> None:
    """An operator reading `--help` learns the rule before hitting the refusal: the bind
    address is the access control, so any other one needs a credential."""
    with pytest.raises(SystemExit):
        web_app.main(["--help"])

    assert ACCESS_ENV_VAR in capsys.readouterr().out


def test_the_process_environment_also_protects_a_public_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The supervisor's escape hatch keeps working: exporting the token is enough."""
    monkeypatch.setenv(ACCESS_ENV_VAR, "un-jeton-d-ambiance-tres-long-mais-vrai")
    entry = _run_entry(
        monkeypatch, _write_env(tmp_path, with_token=False), ["--host", ANY_INTERFACE]
    )

    assert entry.code == 0
    assert entry.application is not None
