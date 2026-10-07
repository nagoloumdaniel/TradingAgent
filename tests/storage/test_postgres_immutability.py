"""Append-only immutability on a real PostgreSQL server (TASK-080, F-020).

`tests/storage/test_constraints.py` already replays the immutability scenarios against
whatever `TEST_DATABASE_URL` names; this module is the PostgreSQL-specific evidence the
ROADMAP asks for: the trigger function exists, its `search_path` is pinned, every
append-only table carries its `UPDATE`/`DELETE` and `TRUNCATE` guards, RLS is on, and the
four scenarios of the constraints suite are refused by the server itself.

The suite is inert without `TEST_DATABASE_URL`, which is deliberately absent from `.env`:
no test may ever touch the production `DATABASE_URL`.
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from tests._database_guard import guard_test_database
from tests.conftest import env_value

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import (
    HaltAction,
    HaltSource,
    OrderState,
    PositionState,
    SignalState,
)
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import downgrade, upgrade
from tradingagent.storage.models import (
    AuditLogRow,
    ExecutionRow,
    HaltCommandRow,
    OrderRow,
    PositionRow,
    SignalEventRow,
    SignalRow,
    StrategyVersionRow,
    TradeRow,
)

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
APPEND_ONLY = ("signal_events", "executions", "trades", "audit_log", "halt_commands")
GUARD_FUNCTION = "forbid_append_only_change"

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set: PostgreSQL immutability tests skipped",
)


def _postgres_url() -> str:
    """The test database only; the production URL is never read for a connection.

    The safety rule itself lives in `tests/_database_guard.py`, in one place: two copies of
    it had already drifted, and the stricter one made a perfectly safe setup unrunnable.
    """
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    parsed = make_url(TEST_DATABASE_URL)
    if parsed.get_backend_name() != "postgresql":
        pytest.skip("TEST_DATABASE_URL does not name a PostgreSQL server")
    try:
        return guard_test_database(TEST_DATABASE_URL, env_value("DATABASE_URL"))
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    url = _postgres_url()
    downgrade(url)  # start clean whatever a previous run left
    upgrade(url)
    built = create_database_engine(url)
    try:
        yield built
    finally:
        built.dispose()
        downgrade(url)


@pytest.fixture(scope="module", autouse=True)
def seeded(engine: Engine) -> None:
    """One seed for the whole module: the guard leaves it untouched by construction."""
    _seed(engine)


def _seed(engine: Engine) -> None:
    """One row in every append-only table, plus the parents their foreign keys need.

    The tables the guards cover are append-only, so the seed is written once per test
    through a fresh transaction and never updated afterwards.
    """
    with Session(engine) as session:
        version = StrategyVersionRow(
            ref="witness@1.0.0",
            strategy_id="witness",
            version="1.0.0",
            manifest={"strategy_id": "witness"},
            content_hash="a" * 64,
            first_seen_at=NOW,
        )
        signal = SignalRow(
            idempotency_key="pg-sig-1",
            strategy_version=version,
            symbol="XAUUSD",
            timeframe=Timeframe.M15,
            direction=Direction.BUY,
            mode=TradingMode.DEMO,
            observed_price=4139.0,
            entry_low=4138.5,
            entry_high=4139.5,
            stop_loss=4126.7,
            take_profits=[4163.6],
            reason="test",
            indicators={"atr": 8.2},
            generated_at=NOW,
            expires_at=NOW + timedelta(minutes=15),
            state=SignalState.CANDIDATE,
        )
        order = OrderRow(
            idempotency_key="pg-ord-1",
            signal=signal,
            symbol="XAUUSD",
            direction=Direction.BUY,
            volume=Decimal("0.02"),
            requested_price=4139.0,
            stop_loss=4126.7,
            take_profit=4163.6,
            mode=TradingMode.DEMO,
            state=OrderState.FILLED,
            created_at=NOW,
            updated_at=NOW,
        )
        position = PositionRow(
            broker_position_ticket=9827313194,
            order=order,
            symbol="XAUUSD",
            direction=Direction.BUY,
            volume=Decimal("0.02"),
            open_price=4139.0,
            stop_loss=4126.7,
            take_profit=4163.6,
            mode=TradingMode.DEMO,
            state=PositionState.CLOSED,
            opened_at=NOW,
            updated_at=NOW,
        )
        session.add_all(
            [
                SignalEventRow(signal=signal, state=SignalState.CANDIDATE, occurred_at=NOW),
                ExecutionRow(
                    order=order,
                    broker_deal_ticket=9700390355,
                    price=4139.0,
                    volume=Decimal("0.02"),
                    executed_at=NOW,
                ),
                position,
                TradeRow(
                    position=position,
                    mode=TradingMode.DEMO,
                    closed_at=NOW + timedelta(hours=1),
                    close_price=4126.7,
                    pnl_eur=Decimal("-21.88"),
                    risk_eur=Decimal("21.88"),
                    exit_reason="stop_loss",
                ),
                AuditLogRow(actor="operator", action="live-activation", detail={}, occurred_at=NOW),
                HaltCommandRow(
                    scope="global",
                    action=HaltAction.HALT,
                    close_positions=False,
                    source=HaltSource.SERVER,
                    reason="test",
                    actor="operator",
                    occurred_at=NOW,
                ),
            ]
        )
        session.commit()


def test_the_guard_function_exists_with_a_pinned_search_path(engine: Engine) -> None:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT p.proconfig FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE p.proname = :name AND n.nspname = 'public'"
            ),
            {"name": GUARD_FUNCTION},
        ).all()
    assert rows, f"{GUARD_FUNCTION}() is missing from the public schema"
    assert any(setting.startswith("search_path=") for setting in (rows[0][0] or []))


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_every_append_only_table_carries_both_guards(engine: Engine, table: str) -> None:
    with engine.connect() as connection:
        names = set(
            connection.execute(
                text(
                    "SELECT t.tgname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE c.relname = :table AND NOT t.tgisinternal"
                ),
                {"table": table},
            ).scalars()
        )
    assert {f"{table}_no_change", f"{table}_no_truncate"} <= names


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_every_append_only_table_is_covered_by_row_level_security(
    engine: Engine, table: str
) -> None:
    with engine.connect() as connection:
        enabled = connection.execute(
            text("SELECT relrowsecurity FROM pg_class WHERE relname = :table"),
            {"table": table},
        ).scalar()
    assert enabled is True


def test_appending_is_allowed(engine: Engine) -> None:
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM trades")).scalar() == 1
        assert connection.execute(text("SELECT count(*) FROM halt_commands")).scalar() == 1


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_update_is_refused_by_the_server(engine: Engine, table: str) -> None:
    with engine.begin() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text(f"UPDATE {table} SET id = id"))  # noqa: S608


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_delete_is_refused_by_the_server(engine: Engine, table: str) -> None:
    with engine.begin() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text(f"DELETE FROM {table}"))  # noqa: S608


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_truncate_is_refused_by_the_server(engine: Engine, table: str) -> None:
    with engine.begin() as connection, pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text(f"TRUNCATE {table} CASCADE"))


def test_mutable_tables_still_accept_updates(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("UPDATE orders SET state = 'filled'"))
        connection.execute(text("UPDATE positions SET state = 'closed'"))
        connection.execute(text("UPDATE signals SET state = 'closed'"))


def test_the_test_url_is_never_the_production_url() -> None:
    """A safety net, not a connection: the two may coexist in the operator's file."""
    production = env_value("DATABASE_URL")
    if production is None:
        pytest.skip("no DATABASE_URL in this environment")
    assert production != TEST_DATABASE_URL, "refusing to run the suite against production"
