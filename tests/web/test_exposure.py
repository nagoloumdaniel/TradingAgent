"""The dashboard must not be published beyond this machine.

Four digits keep a neighbouring browser tab out; they do not keep a network out. The rule is
therefore fail-closed and has no override: any `--host` that is not the loopback interface
makes the process refuse to start and say why. Nothing is silently downgraded to `127.0.0.1`
either — an operator who asked for a public bind and got a private one would believe the
dashboard is reachable when it is not.
"""

from collections.abc import Sequence
from pathlib import Path

import pytest

from tradingagent.web import app as web_app
from tradingagent.web.auth import exposure_problem

#: Every interface. Bandit flags the literal (S104); here it is the whole point of the file —
#: the address the dashboard must refuse to listen on.
ANY_INTERFACE = "0.0.0.0"  # noqa: S104


def _write_env(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(f"DATABASE_URL=sqlite:///{tmp_path / 'agent.db'}\n", encoding="utf-8")
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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, argv: Sequence[str] = ()
) -> _EntryPoint:
    entry = _EntryPoint()
    monkeypatch.setattr(web_app, "ENV_FILE", _write_env(tmp_path))
    monkeypatch.setattr(web_app, "PIN_FILE", tmp_path / "web_pin.json")

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
def test_a_loopback_bind_is_accepted(host: str) -> None:
    """`localhost`, the whole 127/8 block and `::1`."""
    assert exposure_problem(host) is None


@pytest.mark.parametrize(
    "host",
    [ANY_INTERFACE, "::", "", "   ", "192.168.1.10", "10.0.0.5", "dashboard.lan", "127.0.0.1.evil"],
)
def test_any_other_bind_is_refused(host: str) -> None:
    """Including the near-misses: `0.0.0.0` is not loopback, and neither is a hostname that
    merely starts like one. A blank host is refused rather than trusted."""
    problem = exposure_problem(host)

    assert problem is not None
    assert "127.0.0.1" in problem  # the safe alternative is named


# --- the entry point --------------------------------------------------------------------


def test_the_default_bind_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _run_entry(monkeypatch, tmp_path)

    assert entry.code == 0
    assert entry.application is not None
    assert entry.engine_built is True


def test_an_explicit_loopback_bind_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _run_entry(monkeypatch, tmp_path, ["--host", "127.0.0.1"])

    assert entry.code == 0
    assert entry.application is not None


def test_a_public_bind_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = _run_entry(monkeypatch, tmp_path, ["--host", ANY_INTERFACE])

    captured = capsys.readouterr()
    assert entry.code == 2
    assert entry.application is None  # uvicorn was never reached
    assert entry.engine_built is False  # and neither was the database
    assert ANY_INTERFACE in captured.err
