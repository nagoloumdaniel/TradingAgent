import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.ai.evaluation import evaluate_shadow_filter, render
from tradingagent.core.market import Direction
from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.states import OrderState, PositionState, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.ai_calls import AiCall, AiCallStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import (
    OrderRow,
    PositionRow,
    SignalRow,
    StrategyVersionRow,
    TradeRow,
)

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)
END = T0 + timedelta(days=365)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'shadow.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


_counter = iter(range(1, 1000))


def seed_signal_with_trade(
    engine: Engine, *, ai_verdict: str, pnl: int, closed_at: datetime
) -> None:
    """One signal, its shadow AI verdict, and its closed trade."""
    number = next(_counter)
    with Session(engine) as session:
        version = session.scalars(select(StrategyVersionRow)).first()
        if version is None:
            version = StrategyVersionRow(
                ref="witness@1.0.0",
                strategy_id="witness",
                version="1.0.0",
                manifest={},
                content_hash="0" * 64,
                first_seen_at=T0,
            )
            session.add(version)
            session.flush()
        signal = SignalRow(
            idempotency_key=f"witness@1.0.0:XAUUSD:M15:2026-10-{number:02d}T12:00Z",
            strategy_version_id=version.id,
            symbol="XAUUSD",
            timeframe=Timeframe.M15,
            direction=Direction.BUY,
            mode=TradingMode.DEMO,
            observed_price=2650.0,
            entry_low=2649.5,
            entry_high=2650.5,
            stop_loss=2647.5,
            take_profits=[2652.5],
            reason="crossover",
            indicators={},
            generated_at=T0,
            expires_at=T0 + timedelta(hours=1),
            state=SignalState.CLOSED,
        )
        session.add(signal)
        session.flush()
        signal_id = int(signal.id)
        order = OrderRow(
            idempotency_key=f"order-{number}",
            signal_id=signal.id,
            symbol="XAUUSD",
            direction=Direction.BUY,
            volume=Decimal("0.01"),
            requested_price=2650.0,
            stop_loss=2647.5,
            take_profit=2652.5,
            mode=TradingMode.DEMO,
            state=OrderState.FILLED,
            created_at=T0,
            updated_at=T0,
        )
        session.add(order)
        session.flush()
        position = PositionRow(
            broker_position_ticket=number,
            order_id=order.id,
            symbol="XAUUSD",
            direction=Direction.BUY,
            volume=Decimal("0.01"),
            open_price=2650.0,
            stop_loss=2647.5,
            take_profit=2652.5,
            mode=TradingMode.DEMO,
            state=PositionState.CLOSED,
            opened_at=closed_at - timedelta(minutes=30),
            updated_at=closed_at,
        )
        session.add(position)
        session.flush()
        session.add(
            TradeRow(
                position_id=position.id,
                mode=TradingMode.DEMO,
                closed_at=closed_at,
                close_price=2652.0,
                pnl_eur=Decimal(pnl),
                risk_eur=Decimal(100),
                exit_reason="take_profit",
            )
        )
        session.commit()
    store = AiCallStore(engine)
    call = AiCall(
        signal_id=signal_id,
        purpose="signal_filter",
        ai_filter=AiFilter.SHADOW,
        request={},
        response="",
        verdict=ai_verdict,
        error=None,
        latency_ms=1,
        cost_eur=Decimal("0.0001"),
        called_at=T0,
        model="fake",
    )
    asyncio.run(asyncio.to_thread(store.record, call))


def test_both_series_cover_the_same_signals(engine: Engine) -> None:
    seed_signal_with_trade(engine, ai_verdict="approved", pnl=100, closed_at=T0 + DAY)
    seed_signal_with_trade(engine, ai_verdict="approved", pnl=-30, closed_at=T0 + 2 * DAY)
    seed_signal_with_trade(engine, ai_verdict="rejected", pnl=-50, closed_at=T0 + 3 * DAY)

    evaluation = evaluate_shadow_filter(engine, T0, END)

    assert evaluation.actual.trades == 3
    assert evaluation.counterfactual.trades == 2  # the rejected signal is removed
    assert evaluation.evaluated_signals == 3
    assert evaluation.cost_eur == Decimal("0.0003")
    assert evaluation.insufficient_sample is True  # 3 << 100
    assert "GARDER SHADOW" in evaluation.recommendation
    assert "insuffisant" in evaluation.recommendation
    assert "Signaux évalués : 3" in render(evaluation)


def test_a_verdict_that_would_have_helped_promotes_to_advisory_advice(engine: Engine) -> None:
    # The model rejected three losing trades; keeping them is the only difference.
    seed_signal_with_trade(engine, ai_verdict="approved", pnl=40, closed_at=T0 + DAY)
    seed_signal_with_trade(engine, ai_verdict="rejected", pnl=-20, closed_at=T0 + 2 * DAY)
    seed_signal_with_trade(engine, ai_verdict="rejected", pnl=-20, closed_at=T0 + 3 * DAY)
    seed_signal_with_trade(engine, ai_verdict="rejected", pnl=-20, closed_at=T0 + 4 * DAY)
    for index in range(100):  # reach the significance threshold with neutral trades
        seed_signal_with_trade(
            engine, ai_verdict="approved", pnl=0, closed_at=T0 + (5 + index) * DAY
        )

    evaluation = evaluate_shadow_filter(engine, T0, END)

    assert evaluation.evaluated_signals == 104
    assert evaluation.insufficient_sample is False
    assert evaluation.counterfactual.net_profit > evaluation.actual.net_profit
    assert evaluation.recommendation.startswith("PROPOSER")


def test_an_edge_that_lives_only_in_the_top_five_is_declared_inconclusive(
    engine: Engine,
) -> None:
    # Full sample: the model rejected two big winners and one small loser, so applying
    # its verdicts would have hurt — GARDER SHADOW. But exclude the top five wins of the
    # real series: those two winners disappear from both sides and only the avoided
    # loss remains, which flips the verdict to "better". The disagreement is exactly
    # what the cahier calls non-conclusive: an edge living in a handful of trades.
    seed_signal_with_trade(engine, ai_verdict="rejected", pnl=10_000, closed_at=T0 + DAY)
    seed_signal_with_trade(engine, ai_verdict="rejected", pnl=10_000, closed_at=T0 + 2 * DAY)
    seed_signal_with_trade(engine, ai_verdict="rejected", pnl=-1_000, closed_at=T0 + 3 * DAY)
    for index in range(100):
        seed_signal_with_trade(
            engine, ai_verdict="approved", pnl=0, closed_at=T0 + (4 + index) * DAY
        )

    evaluation = evaluate_shadow_filter(engine, T0, END)

    assert evaluation.insufficient_sample is False
    assert evaluation.counterfactual.net_profit < evaluation.actual.net_profit
    assert evaluation.excluded_counterfactual.net_profit > evaluation.excluded_actual.net_profit
    assert evaluation.inconclusive is True
    assert "NON CONCLUE" in evaluation.recommendation
