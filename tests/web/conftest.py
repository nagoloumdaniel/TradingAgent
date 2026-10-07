"""Fixtures for the dashboard tests: a disposable migrated SQLite database and a client.

The database is built by the migrations, never by ``create_all``: triggers, CHECK constraints
and the exact v3 tables exist only there. ``TEST_DATABASE_URL`` is honoured the same way the
storage tests honour it, but the default is a file under ``tmp_path`` that disappears with the
test.
"""

import json
import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, insert, make_url
from tests.web.seed import NOW, Seeded, seed

from tradingagent.core.states import HaltAction, HaltSource
from tradingagent.ea.bridge import PROTOCOL_VERSION, format_utc, report_path
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import downgrade, upgrade
from tradingagent.storage.models import HaltCommandRow
from tradingagent.web.app import create_app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
# DEFAULT_HEARTBEAT_TIMEOUT_SECONDS: a heartbeat older than this is OFFLINE.
HEARTBEAT_TIMEOUT = 15.0


def _guard(url: str) -> str:
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        raise pytest.UsageError(
            f"TEST_DATABASE_URL must name a database ending in _test, got {database!r}"
        )
    return url


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    if TEST_DATABASE_URL:
        return _guard(TEST_DATABASE_URL)
    return f"sqlite:///{tmp_path / 'web.db'}"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    shared_server = TEST_DATABASE_URL is not None
    if shared_server:
        downgrade(database_url)
    upgrade(database_url)
    built = create_database_engine(database_url)
    yield built
    built.dispose()
    if shared_server:
        downgrade(database_url)


@pytest.fixture
def empty_engine(engine: Engine) -> Engine:
    """A migrated database with no business row at all: every page must still render."""
    return engine


@pytest.fixture
def populated(engine: Engine) -> Seeded:
    return seed(engine)


@pytest.fixture
def frozen_now() -> datetime:
    return NOW


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    """A client on the empty database, with a frozen clock."""
    with TestClient(create_app(engine, now=lambda: NOW)) as test_client:
        yield test_client


@pytest.fixture
def seeded_client(engine: Engine, populated: Seeded) -> Iterator[TestClient]:
    with TestClient(create_app(engine, now=lambda: NOW)) as test_client:
        yield test_client


@pytest.fixture
def seeded(engine: Engine, populated: Seeded) -> Seeded:
    return populated


@pytest.fixture
def seed_halt(engine: Engine, populated: Seeded) -> None:
    """A global halt, inserted after the dataset, for the pages that must show a banner."""
    with engine.begin() as connection:
        connection.execute(
            insert(HaltCommandRow).values(
                scope="global",
                action=HaltAction.HALT,
                close_positions=True,
                source=HaltSource.AUTOMATIC,
                reason="perte quotidienne dépassée",
                actor="guardian",
                occurred_at=NOW,
            )
        )


def ea_report(
    symbol: str,
    *,
    heartbeat: datetime = NOW,
    connected: bool = True,
    trade_allowed: bool = True,
    kill_switch: bool = False,
    local_halt: bool = False,
    halt_reason: str = "",
    applied_revision: int = 7,
) -> dict:
    """One EA report, shaped exactly as the bridge reads it."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "ea_version": "1.0.0",
        "symbol": symbol,
        "magic": 3031,
        "updated_at": format_utc(heartbeat),
        "applied_revision": applied_revision,
        "connected": connected,
        "trade_allowed": trade_allowed,
        "kill_switch": kill_switch,
        "local_halt": local_halt,
        "halt_reason": halt_reason,
        "state_age_seconds": 1.0,
        "positions": [
            {
                "ticket": 5001,
                "symbol": symbol,
                "direction": "BUY",
                "volume": 0.01,
                "price_open": 63_000.0,
                "stop_loss": 62_000.0,
                "take_profit": 65_000.0,
                "profit": 1.5,
                "comment": "ta-0123456789abcdef",
            }
        ],
        "counters": {"orders_sent": 3, "errors": 0},
        "events": [
            {
                "seq": 12,
                "at": format_utc(heartbeat - timedelta(seconds=1)),
                "kind": "EXECUTION",
                "severity": "INFO",
                "ticket": 5001,
                "message": "order 5001 filled",
                "data": {"slippage": 2.5},
            }
        ],
    }


def write_report(directory: Path, symbol: str, payload: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = report_path(directory, symbol)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def ea_reports_dir(tmp_path: Path) -> Path:
    """A bridge ``reports/`` directory holding one healthy EA.

    The BTCUSD heartbeat is older than the bridge's tolerance on purpose: the dashboard must
    show it OFFLINE without any exception.
    """
    directory = tmp_path / "bridge" / "reports"
    write_report(directory, "XAUUSD", ea_report("XAUUSD"))
    write_report(
        directory,
        "BTCUSD",
        ea_report("BTCUSD", heartbeat=NOW - timedelta(seconds=HEARTBEAT_TIMEOUT * 4)),
    )
    return directory


@pytest.fixture
def ea_halted_reports_dir(tmp_path: Path) -> Path:
    """A bridge directory whose Guardian has thrown its kill switch."""
    directory = tmp_path / "bridge-halted" / "reports"
    write_report(
        directory,
        "XAUUSD",
        ea_report(
            "XAUUSD",
            trade_allowed=False,
            kill_switch=True,
            local_halt=True,
            halt_reason="perte journalière dépassée",
        ),
    )
    return directory


@pytest.fixture
def unreadable_reports_dir(tmp_path: Path) -> Path:
    """A bridge directory whose only report is corrupt: the EA must still be listed."""
    directory = tmp_path / "bridge-corrupt" / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "XAUUSD_report.json").write_text("{not json", encoding="utf-8")
    return directory


@pytest.fixture
def ea_client(engine: Engine, populated: Seeded, ea_reports_dir: Path) -> Iterator[TestClient]:
    with TestClient(
        create_app(engine, now=lambda: NOW, ea_reports_dir=ea_reports_dir)
    ) as test_client:
        yield test_client


@pytest.fixture
def halted_ea_client(
    engine: Engine, populated: Seeded, ea_halted_reports_dir: Path
) -> Iterator[TestClient]:
    with TestClient(
        create_app(engine, now=lambda: NOW, ea_reports_dir=ea_halted_reports_dir)
    ) as test_client:
        yield test_client


@pytest.fixture
def corrupt_ea_client(
    engine: Engine, populated: Seeded, unreadable_reports_dir: Path
) -> Iterator[TestClient]:
    with TestClient(
        create_app(engine, now=lambda: NOW, ea_reports_dir=unreadable_reports_dir)
    ) as test_client:
        yield test_client
