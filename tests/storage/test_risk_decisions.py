from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import Engine, select
from tests.risk.builders import LOGIN, context

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import RiskOutcome, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import SlotStatus
from tradingagent.risk.engine import decide
from tradingagent.storage.models import RiskDecisionRow, SignalEventRow, SignalRow
from tradingagent.storage.risk_decisions import RiskDecisionStore
from tradingagent.storage.signals import SignalRecord, SignalRepository, idempotency_key
from tradingagent.strategies.manifest import StrategyManifest

DAY = datetime(2026, 10, 6, tzinfo=UTC)
MANIFEST = StrategyManifest(
    strategy_id="witness",
    version="1.0.0",
    max_mode=TradingMode.DEMO,
    allowed_symbols=("XAUUSD",),
    timeframes=(Timeframe.M15,),
    history_bars=20,
)


def new_signal(engine: Engine, minutes: int) -> int:
    at = DAY + timedelta(hours=12, minutes=minutes)
    signal_id = SignalRepository(engine).record(
        SignalRecord(
            idempotency_key=idempotency_key(MANIFEST.ref, "XAUUSD", Timeframe.M15, at),
            manifest=MANIFEST,
            symbol="XAUUSD",
            timeframe=Timeframe.M15,
            direction=Direction.BUY,
            mode=TradingMode.DEMO,
            observed_price=2400.0,
            entry_low=2399.0,
            entry_high=2401.0,
            stop_loss=2387.8927,
            take_profits=(2420.0,),
            reason="test",
            indicators={},
            generated_at=at,
            expires_at=at + timedelta(minutes=30),
        )
    )
    assert signal_id is not None
    return signal_id


def test_an_authorized_decision_is_stored_and_validates_the_signal(engine: Engine) -> None:
    signal_id = new_signal(engine, 0)
    decision = decide(context(), LOGIN)
    at = DAY + timedelta(hours=12, seconds=1)
    RiskDecisionStore(engine).record(signal_id, decision, at)
    with engine.connect() as connection:
        row = connection.execute(select(RiskDecisionRow)).one()
        state = connection.execute(select(SignalRow.state)).scalar_one()
        events = (
            connection.execute(select(SignalEventRow.state).order_by(SignalEventRow.id))
            .scalars()
            .all()
        )
    assert row.outcome is RiskOutcome.AUTHORIZED
    assert (row.volume, row.risk_eur, row.margin_eur) == (D("0.02"), D("21.88"), D("367.86"))
    assert row.checks["stop_loss"]["passed"] is True
    assert row.decided_at == at
    assert state is SignalState.VALIDATED
    assert events == [SignalState.CANDIDATE, SignalState.VALIDATED]


def test_a_refusal_is_stored_with_its_reason_and_rejects_the_signal(engine: Engine) -> None:
    signal_id = new_signal(engine, 0)
    decision = decide(context(market=SlotStatus.CLOSED), LOGIN)
    RiskDecisionStore(engine).record(signal_id, decision, DAY + timedelta(hours=12))
    with engine.connect() as connection:
        row = connection.execute(select(RiskDecisionRow)).one()
        state = connection.execute(select(SignalRow.state)).scalar_one()
        detail = connection.execute(
            select(SignalEventRow.detail).where(SignalEventRow.state == SignalState.RISK_REJECTED)
        ).scalar_one()
    assert row.outcome is RiskOutcome.REFUSED
    assert row.volume is None
    assert "trading_hours" in row.reason
    assert state is SignalState.RISK_REJECTED
    assert detail is not None and "trading_hours" in detail


def test_refusals_are_counted_by_reason_for_the_daily_report(engine: Engine) -> None:
    store = RiskDecisionStore(engine)
    closed = decide(context(market=SlotStatus.CLOSED), LOGIN)
    base = context(market=SlotStatus.CLOSED)
    both = decide(replace(base, portfolio=replace(base.portfolio, trades_today=9)), LOGIN)
    store.record(new_signal(engine, 0), closed, DAY + timedelta(hours=12))
    store.record(new_signal(engine, 15), both, DAY + timedelta(hours=13))
    store.record(new_signal(engine, 30), decide(context(), LOGIN), DAY + timedelta(hours=14))
    store.record(new_signal(engine, 45), closed, DAY + timedelta(days=1, hours=1))
    summary = store.refusals(DAY, DAY + timedelta(days=1))
    assert summary.decisions == 3
    assert summary.refused == 2
    assert summary.by_check == {"trading_hours": 2, "trades_today": 1}


def test_a_decision_for_an_unknown_signal_is_refused_by_the_database(engine: Engine) -> None:
    with pytest.raises(Exception):  # noqa: B017 - any integrity error will do
        RiskDecisionStore(engine).record(999, decide(context(), LOGIN), DAY)


def test_a_missing_stop_counts_as_one_root_cause(engine: Engine) -> None:
    store = RiskDecisionStore(engine)
    live = context(TradingMode.LIVE)
    no_stop = replace(live, intent=replace(live.intent, stop_loss=None))
    store.record(new_signal(engine, 0), decide(no_stop, LOGIN), DAY + timedelta(hours=12))
    summary = store.refusals(DAY, DAY + timedelta(days=1))
    assert "stop_loss" in summary.by_check
    assert not {"sizing", "spread", "live_eligibility"} & set(summary.by_check)
