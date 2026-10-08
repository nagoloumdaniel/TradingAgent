"""The market dimension on `reports` and `system_events` (migration 0007).

The operator wants every figure of the dashboard separable by market. Two tables carried
no market at all: `/reports` had no column to filter on, and `/risk` read `system_events`
with no symbol. Adding the column is not enough — a column that stays empty would let a
page show a market selector that filters nothing, which is worse than saying so.

These tests pin the three things that make the column real: it exists after `upgrade`, it
disappears after `downgrade` (the migration is reversible), and the writers fill it
whenever the caller knows the market. The backfill is only asserted where the value comes
from the data itself — a signal inside the report window, or the symbol the event's own
detail already carries — never from a guess.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert, inspect, select
from sqlalchemy.orm import Session

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import Severity, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.migrate import downgrade, upgrade
from tradingagent.storage.models import ReportRow, SignalRow, StrategyVersionRow, SystemEventRow
from tradingagent.storage.reports import ReportStore

BEFORE = "0006"  # the revision that predates the market dimension
DAY = datetime(2026, 10, 6, tzinfo=UTC)
NEXT_DAY = DAY + timedelta(days=1)
VERSION_REF = "witness@1.0.0"


@pytest.fixture
def url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'market.db'}"


@pytest.fixture
def engine(url: str) -> Iterator[Engine]:
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def _columns(url: str, table: str) -> dict[str, dict[str, object]]:
    built = create_database_engine(url)
    try:
        return {column["name"]: dict(column) for column in inspect(built).get_columns(table)}
    finally:
        built.dispose()


def _indexes(url: str, table: str) -> set[str]:
    built = create_database_engine(url)
    try:
        return {str(index["name"]) for index in inspect(built).get_indexes(table)}
    finally:
        built.dispose()


def _seed_signal(engine: Engine, symbol: str, at: datetime) -> None:
    """A closed signal, written the way revision 0006 held it: no market on the report."""
    with engine.begin() as connection:
        version = select(StrategyVersionRow.id).where(StrategyVersionRow.ref == VERSION_REF)
        version_id = connection.execute(version).scalar_one_or_none()
        if version_id is None:
            connection.execute(
                insert(StrategyVersionRow).values(
                    ref=VERSION_REF,
                    strategy_id="witness",
                    version="1.0.0",
                    manifest={},
                    content_hash="0" * 64,
                    first_seen_at=DAY,
                )
            )
            version_id = connection.execute(version).scalar_one()
        connection.execute(
            insert(SignalRow).values(
                idempotency_key=f"{symbol}:{at.isoformat()}",
                strategy_version_id=version_id,
                symbol=symbol,
                timeframe=Timeframe.M15,
                direction=Direction.BUY,
                mode=TradingMode.DEMO,
                observed_price=2650.0,
                entry_low=2649.5,
                entry_high=2650.5,
                stop_loss=2647.5,
                take_profits=[2652.5],
                reason="test",
                indicators={},
                generated_at=at,
                expires_at=at + timedelta(minutes=15),
                state=SignalState.CLOSED,
            )
        )


def _seed_report(engine: Engine, start: datetime, end: datetime) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(ReportRow).values(
                period="daily",
                window_start=start,
                window_end=end,
                content="rapport de la période",
                generated_at=end,
                sent_at=None,
            )
        )


def _seed_event(engine: Engine, kind: str, detail: dict[str, object]) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(SystemEventRow).values(
                kind=kind,
                severity=Severity.WARNING,
                detail=detail,
                occurred_at=DAY,
            )
        )


def _report_market(url: str) -> str | None:
    built = create_database_engine(url)
    try:
        with Session(built) as session:
            return session.scalars(select(ReportRow.market)).one()
    finally:
        built.dispose()


def _event_symbols(url: str) -> dict[str, str | None]:
    built = create_database_engine(url)
    try:
        with Session(built) as session:
            rows = session.scalars(select(SystemEventRow)).all()
            return {row.kind: row.symbol for row in rows}
    finally:
        built.dispose()


# --- the schema, up and down ----------------------------------------------------


def test_upgrade_adds_a_nullable_market_to_reports(url: str) -> None:
    upgrade(url)
    assert _columns(url, "reports")["market"]["nullable"] is True


def test_upgrade_adds_a_nullable_symbol_to_system_events(url: str) -> None:
    upgrade(url)
    assert _columns(url, "system_events")["symbol"]["nullable"] is True


def test_both_columns_are_indexed_because_they_filter(url: str) -> None:
    upgrade(url)
    assert "ix_reports_market" in _indexes(url, "reports")
    assert "ix_system_events_symbol" in _indexes(url, "system_events")


def test_downgrade_removes_what_upgrade_added(url: str) -> None:
    upgrade(url)
    downgrade(url, BEFORE)

    assert "market" not in _columns(url, "reports")
    assert "ix_reports_market" not in _indexes(url, "reports")
    assert "symbol" not in _columns(url, "system_events")
    assert "ix_system_events_symbol" not in _indexes(url, "system_events")

    upgrade(url)  # and the chain replays, from the exact state 0006 left
    assert _columns(url, "reports")["market"]["nullable"] is True
    assert _columns(url, "system_events")["symbol"]["nullable"] is True


# --- the backfill of what the data already says ---------------------------------


def test_a_report_of_a_single_market_window_is_attributed_to_that_market(url: str) -> None:
    upgrade(url, BEFORE)
    built = create_database_engine(url)
    try:
        _seed_signal(built, "XAUUSD", DAY + timedelta(hours=1))
        _seed_report(built, DAY, NEXT_DAY)
    finally:
        built.dispose()

    upgrade(url)

    assert _report_market(url) == "XAUUSD"


def test_a_report_of_a_two_market_window_keeps_no_market(url: str) -> None:
    upgrade(url, BEFORE)
    built = create_database_engine(url)
    try:
        _seed_signal(built, "XAUUSD", DAY + timedelta(hours=1))
        _seed_signal(built, "BTCUSD", DAY + timedelta(hours=2))
        _seed_report(built, DAY, NEXT_DAY)
    finally:
        built.dispose()

    upgrade(url)

    assert _report_market(url) is None


def test_a_signal_outside_the_window_does_not_attribute_the_report(url: str) -> None:
    upgrade(url, BEFORE)
    built = create_database_engine(url)
    try:
        _seed_signal(built, "XAUUSD", DAY - timedelta(minutes=1))
        _seed_report(built, DAY, NEXT_DAY)
    finally:
        built.dispose()

    upgrade(url)

    assert _report_market(url) is None


def test_the_symbol_of_a_legacy_event_is_read_from_its_own_detail(url: str) -> None:
    upgrade(url, BEFORE)
    built = create_database_engine(url)
    try:
        _seed_event(built, "evaluation_skipped", {"symbol": "BTCUSD", "detail": "closed"})
        _seed_event(built, "ea_offline", {"symbol": "XAUUSD"})
        _seed_event(built, "clock_mismatch", {"detail": "no symbol in this one"})
    finally:
        built.dispose()

    upgrade(url)

    assert _event_symbols(url) == {
        "evaluation_skipped": "BTCUSD",
        "ea_offline": "XAUUSD",
        "clock_mismatch": None,
    }


# --- what the writers store ------------------------------------------------------


def test_the_report_writer_stores_the_market_it_is_given(engine: Engine) -> None:
    ReportStore(engine).save("daily", DAY, NEXT_DAY, "contenu", NEXT_DAY, market="BTCUSD")

    with Session(engine) as session:
        row = session.scalars(select(ReportRow)).one()
    assert row.market == "BTCUSD"


def test_the_report_writer_leaves_the_market_empty_when_it_has_none(engine: Engine) -> None:
    ReportStore(engine).save("daily", DAY, NEXT_DAY, "contenu", NEXT_DAY)

    with Session(engine) as session:
        row = session.scalars(select(ReportRow)).one()
    assert row.market is None


def test_the_event_writer_stores_the_symbol_it_is_given(engine: Engine) -> None:
    SystemEventStore(engine).record(
        "ea_offline", Severity.WARNING, {"symbol": "BTCUSD"}, DAY, symbol="BTCUSD"
    )

    with Session(engine) as session:
        row = session.scalars(select(SystemEventRow)).one()
    assert row.symbol == "BTCUSD"


def test_the_event_writer_leaves_the_symbol_empty_when_the_caller_has_none(
    engine: Engine,
) -> None:
    SystemEventStore(engine).record("clock_mismatch", Severity.CRITICAL, {"detail": "x"}, DAY)

    with Session(engine) as session:
        row = session.scalars(select(SystemEventRow)).one()
    assert row.symbol is None
