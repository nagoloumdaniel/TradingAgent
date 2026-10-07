"""Execution telemetry and the daily aggregates (cahier v3 §20, §31, §47)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.core.mode import TradingMode
from tradingagent.core.states import ExecutionEventKind
from tradingagent.storage.daily import DailyPerformanceStore, day_floor
from tradingagent.storage.models import ExecutionEventRow
from tradingagent.storage.telemetry import ExecutionEventStore

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _signal(engine: Engine, symbol: str = "XAUUSD") -> int:
    from tests.runtime.test_pipeline import record_signal

    return record_signal(engine, symbol=symbol)


def test_an_event_is_recorded_with_its_utc_timestamp(engine: Engine) -> None:
    store = ExecutionEventStore(engine)
    signal_id = _signal(engine)
    store.record(
        ExecutionEventKind.ORDER_SENT,
        "XAUUSD",
        {"volume": "0.02"},
        NOW,
        signal_id=signal_id,
    )
    events = store.recent()
    assert len(events) == 1
    assert events[0].kind is ExecutionEventKind.ORDER_SENT
    assert events[0].symbol == "XAUUSD"
    assert events[0].occurred_at == NOW
    assert events[0].detail == {"volume": "0.02"}


def test_telemetry_is_append_only(engine: Engine) -> None:
    store = ExecutionEventStore(engine)
    event_id = store.record(ExecutionEventKind.FILLED, "XAUUSD", {}, NOW)
    with Session(engine) as session, pytest.raises(Exception, match="append-only"):
        row = session.get(ExecutionEventRow, event_id)
        assert row is not None
        row.symbol = "BTCUSD"
        session.commit()


def test_latency_pairs_the_two_hops_of_the_same_order(engine: Engine) -> None:
    store = ExecutionEventStore(engine)
    gold, btc = _signal(engine, "XAUUSD"), _signal(engine, "BTCUSD")
    store.record(ExecutionEventKind.ORDER_SENT, "XAUUSD", {}, NOW, signal_id=gold)
    store.record(
        ExecutionEventKind.FILLED, "XAUUSD", {}, NOW + timedelta(milliseconds=250), signal_id=gold
    )
    store.record(ExecutionEventKind.ORDER_SENT, "BTCUSD", {}, NOW, signal_id=btc)

    stats = store.latency(ExecutionEventKind.ORDER_SENT, ExecutionEventKind.FILLED, symbol="XAUUSD")
    assert stats.sample == 1
    assert stats.median_ms == pytest.approx(250.0)

    assert (
        store.latency(
            ExecutionEventKind.ORDER_SENT, ExecutionEventKind.FILLED, symbol="BTCUSD"
        ).sample
        == 0
    )


def test_counts_by_kind(engine: Engine) -> None:
    store = ExecutionEventStore(engine)
    store.record(ExecutionEventKind.ORDER_SENT, "XAUUSD", {}, NOW)
    store.record(ExecutionEventKind.ORDER_SENT, "BTCUSD", {}, NOW)
    store.record(ExecutionEventKind.FILLED, "XAUUSD", {}, NOW)
    assert store.count_by_kind() == {"order_sent": 2, "filled": 1}
    assert store.count_by_kind("BTCUSD") == {"order_sent": 1}


def test_a_trade_lands_in_its_daily_bucket(engine: Engine) -> None:
    store = DailyPerformanceStore(engine)
    store.apply_trade(
        day=NOW,
        mode=TradingMode.DEMO,
        market="XAUUSD",
        ref="witness@1.1.0",
        pnl=Decimal("12.00"),
        risk_eur=Decimal("20.00"),
        won=True,
        at=NOW,
    )
    store.apply_trade(
        day=NOW + timedelta(hours=3),
        mode=TradingMode.DEMO,
        market="XAUUSD",
        ref="witness@1.1.0",
        pnl=Decimal("-8.00"),
        risk_eur=Decimal("20.00"),
        won=False,
        at=NOW,
    )
    entries = store.for_day(NOW)
    assert len(entries) == 1
    assert entries[0].trades == 2
    assert entries[0].wins == 1
    assert entries[0].pnl == Decimal("4.00")
    assert entries[0].win_rate == pytest.approx(0.5)
    assert entries[0].r_multiple == Decimal("0.1")


def test_totals_over_a_window_filter_by_market(engine: Engine) -> None:
    store = DailyPerformanceStore(engine)
    for market, pnl in (("XAUUSD", "10.00"), ("BTCUSD", "-4.00")):
        store.apply_trade(
            day=NOW,
            mode=TradingMode.PAPER,
            market=market,
            ref=f"ref-{market}",
            pnl=Decimal(pnl),
            risk_eur=Decimal("10.00"),
            won=Decimal(pnl) > 0,
            at=NOW,
        )
    gold = store.totals(at=NOW, days=7, market="XAUUSD")
    assert gold.trades == 1
    assert gold.pnl == Decimal("10.00")
    every = store.totals(at=NOW, days=7)
    assert every.trades == 2
    assert every.pnl == Decimal("6.00")
    assert store.for_day(NOW - timedelta(days=3)) == []


def test_the_same_day_is_the_utc_calendar_day(engine: Engine) -> None:
    store = DailyPerformanceStore(engine)
    late = datetime(2026, 10, 7, 23, 59, tzinfo=UTC)
    store.apply_trade(
        day=late,
        mode=TradingMode.DEMO,
        market="XAUUSD",
        ref="r",
        pnl=Decimal("1"),
        risk_eur=Decimal("1"),
        won=True,
        at=late,
    )
    assert day_floor(late) == datetime(2026, 10, 7, tzinfo=UTC)
    assert len(store.for_day(datetime(2026, 10, 7, 0, 1, tzinfo=UTC))) == 1
    assert store.for_day(datetime(2026, 10, 8, tzinfo=UTC)) == []


def test_the_pipeline_writes_its_execution_events(engine: Engine) -> None:
    from tests.runtime.test_pipeline import Parts, record_signal

    parts = Parts(engine, mode=TradingMode.PAPER)
    parts.run(record_signal(engine, mode=TradingMode.PAPER))
    kinds = {event.kind for event in ExecutionEventStore(engine).recent()}
    assert ExecutionEventKind.ORDER_SENT in kinds
    assert ExecutionEventKind.FILLED in kinds
    assert ExecutionEventKind.POSITION_OPENED in kinds
    with Session(engine) as session:
        rows = session.scalars(select(ExecutionEventRow)).all()
    assert all(row.signal_id is not None for row in rows)
