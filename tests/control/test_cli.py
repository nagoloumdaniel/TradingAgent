from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tradingagent.config.errors import ConfigError
from tradingagent.control.cli import main
from tradingagent.control.quarantine import PersistentQuarantine
from tradingagent.core.halt import HaltStatus
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.migrate import upgrade

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def url(tmp_path: Path) -> str:
    database = f"sqlite:///{tmp_path / 'cli.db'}"
    upgrade(database)
    return database


def factory(url: str) -> Callable[[], Engine]:
    return lambda: create_database_engine(url)


def run(url: str, *argv: str) -> int:
    return main(list(argv), engine_factory=factory(url), now=lambda: NOW)


def status_of(url: str) -> HaltStatus:
    engine = create_database_engine(url)
    try:
        return HaltStore(engine).status()
    finally:
        engine.dispose()


def test_status_on_a_running_agent(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(url, "status") == 0
    assert "TRADING" in capsys.readouterr().out


def test_halt_then_resume_from_the_server(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(url, "halt", "--reason", "news risk", "--actor", "daniel") == 0
    out = capsys.readouterr().out
    assert "HALTED" in out
    assert "news risk" in out
    assert "daniel" in out
    assert status_of(url).halted
    assert not status_of(url).close_positions
    assert run(url, "resume", "--reason", "checked", "--actor", "daniel") == 0
    assert not status_of(url).halted


def test_closing_positions_must_be_asked_explicitly(
    url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run(url, "halt", "--reason", "flash crash", "--close-positions")
    assert status_of(url).close_positions
    assert "closing open positions" in capsys.readouterr().out


def test_a_halt_needs_a_reason(url: str) -> None:
    with pytest.raises(SystemExit):
        run(url, "halt")


def test_rearm_lifts_a_quarantine(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    engine = create_database_engine(url)
    try:
        PersistentQuarantine(HaltStore(engine)).quarantine("flaky@1.0.0", "XAUUSD", "3 errors", NOW)
    finally:
        engine.dispose()
    run(url, "status")
    assert "QUARANTINED: flaky@1.0.0 on XAUUSD" in capsys.readouterr().out
    run(url, "rearm", "--strategy", "flaky@1.0.0", "--symbol", "XAUUSD")
    assert "QUARANTINED" not in capsys.readouterr().out


def test_a_missing_database_url_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    def no_database() -> Engine:
        raise ConfigError("Invalid environment configuration:\n  DATABASE_URL: Field required")

    assert main(["status"], engine_factory=no_database) == 2
    assert "DATABASE_URL" in capsys.readouterr().err


def test_an_unreadable_database_shows_halted_without_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = f"sqlite:///{tmp_path / 'empty.db'}"
    assert main(["status"], engine_factory=factory(empty)) == 0
    out = capsys.readouterr().out
    assert "HALTED" in out
    assert "unreadable" in out
