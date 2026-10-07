"""Loss classification and degradation detection (cahier v3 §15, §16).

The verdict is a pure function of the closed trade and its context. A model may only add
a comment; it can never change the verdict, create an order or touch a stop (§39).
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tradingagent.ai.analyst import (
    LossContext,
    LossKind,
    LossObservation,
    TradeAnalyst,
    analyse_series,
    classify_loss,
    detect_degradation,
    detect_recurring_pattern,
)
from tradingagent.ai.lab_store import LabStore
from tradingagent.analytics.model import Trade
from tradingagent.core.market import Direction
from tradingagent.core.states import AnalysisKind
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.ai_calls import AiReply
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import (
    AiAnalysisRow,
    OrderRow,
    PositionRow,
    SignalRow,
    TradeRow,
)

T0 = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'analyst.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def a_trade(
    pnl: float,
    *,
    risk: float = 100.0,
    closed_at: datetime = T0,
    slippage: float | None = None,
    spread: float | None = None,
    direction: Direction = Direction.BUY,
    timeframe: Timeframe = Timeframe.M15,
) -> Trade:
    return Trade(
        symbol="frxXAUUSD",
        strategy_ref="witness@1.0.0",
        direction=direction,
        timeframe=timeframe,
        mode="DEMO",  # type: ignore[arg-type]
        opened_at=closed_at - timedelta(minutes=30),
        closed_at=closed_at,
        pnl_eur=Decimal(str(pnl)),
        risk_eur=Decimal(str(risk)),
        slippage=slippage,
        spread=spread,
    )


def an_observation(
    pnl: float,
    *,
    closed_at: datetime = T0,
    regime: str = "TREND_UP",
    session: str = "LONDON",
    volatility: float = 1.0,
    slippage: float | None = None,
    spread: float | None = None,
    direction: Direction = Direction.BUY,
    timeframe: Timeframe = Timeframe.M15,
) -> LossObservation:
    trade = a_trade(
        pnl,
        closed_at=closed_at,
        slippage=slippage,
        spread=spread,
        direction=direction,
        timeframe=timeframe,
    )
    return LossObservation(
        trade=trade,
        context=LossContext(
            regime=regime,
            session=session,
            volatility=volatility,
            duration=timedelta(minutes=30),
        ),
    )


class FakeClient:
    """A model under test: canned reply, counted calls, optional failure."""

    def __init__(self, reply: str | Exception = "") -> None:
        self.reply = reply
        self.calls = 0

    async def complete(self, system: str, user: str) -> AiReply:
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return AiReply(text=self.reply, model="fake-model", input_tokens=10, output_tokens=5)


def an_analyst(engine: Engine, client: FakeClient | None = None) -> TradeAnalyst:
    return TradeAnalyst(LabStore(engine), client)


def analysis_rows(engine: Engine) -> list[AiAnalysisRow]:
    with Session(engine) as session:
        return list(session.scalars(select(AiAnalysisRow).order_by(AiAnalysisRow.id)).all())


def trading_counts(engine: Engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            "signals": session.scalar(select(func.count()).select_from(SignalRow)) or 0,
            "orders": session.scalar(select(func.count()).select_from(OrderRow)) or 0,
            "positions": session.scalar(select(func.count()).select_from(PositionRow)) or 0,
            "trades": session.scalar(select(func.count()).select_from(TradeRow)) or 0,
        }


# --- one loss: pure classification -------------------------------------------


def test_a_loss_within_the_risk_taken_is_normal() -> None:
    verdict = classify_loss(an_observation(-100.0))

    assert verdict.kind is LossKind.NORMAL
    assert verdict.figures["loss_eur"] == 100.0
    assert verdict.figures["risk_eur"] == 100.0


def test_a_loss_materially_bigger_than_the_risk_is_an_isolated_anomaly() -> None:
    verdict = classify_loss(an_observation(-400.0))

    assert verdict.kind is LossKind.ISOLATED_ANOMALY
    assert verdict.figures["loss_multiple"] == 4.0


def test_an_abnormal_slippage_is_an_execution_problem() -> None:
    verdict = classify_loss(an_observation(-100.0, slippage=5.0))

    assert verdict.kind is LossKind.EXECUTION_PROBLEM
    assert verdict.figures["slippage"] == 5.0


def test_an_abnormal_spread_is_an_execution_problem() -> None:
    verdict = classify_loss(an_observation(-100.0, spread=3.0))

    assert verdict.kind is LossKind.EXECUTION_PROBLEM
    assert verdict.figures["spread"] == 3.0


def test_an_execution_problem_takes_precedence_over_an_anomaly() -> None:
    verdict = classify_loss(an_observation(-400.0, slippage=5.0))

    assert verdict.kind is LossKind.EXECUTION_PROBLEM


def test_a_winning_trade_produces_no_loss_verdict() -> None:
    verdict = classify_loss(an_observation(150.0))

    assert verdict.kind is LossKind.NORMAL
    assert "perte" in verdict.reason


# --- a series: recurring pattern ----------------------------------------------


def test_a_trait_shared_by_several_losses_is_a_recurring_pattern() -> None:
    observations = [
        an_observation(-100.0, regime="CHAOS", closed_at=T0 + index * MINUTE) for index in range(4)
    ] + [an_observation(-100.0, regime="TREND_UP", closed_at=T0 + 10 * MINUTE)]

    verdict = detect_recurring_pattern(observations)

    assert verdict is not None
    assert verdict.kind is LossKind.RECURRING_PATTERN
    assert verdict.figures["trait"] == "regime"
    assert verdict.figures["value"] == "CHAOS"
    assert verdict.figures["matching_losses"] == 4
    assert verdict.figures["losses"] == 5


def test_losses_without_a_shared_trait_show_no_pattern() -> None:
    observations = [
        an_observation(
            -100.0,
            regime=f"R{index}",
            session=f"S{index}",
            timeframe=timeframe,
            direction=Direction.BUY if index % 2 == 0 else Direction.SELL,
            closed_at=T0 + index * MINUTE,
        )
        for index, timeframe in enumerate(
            (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.M30)
        )
    ]

    assert detect_recurring_pattern(observations) is None


def test_winners_are_not_counted_as_pattern_losses() -> None:
    observations = [
        an_observation(-100.0, regime="CHAOS", closed_at=T0 + index * MINUTE) for index in range(3)
    ] + [an_observation(120.0, regime="CHAOS", closed_at=T0 + 10 * MINUTE) for _ in range(7)]

    verdict = detect_recurring_pattern(observations)

    assert verdict is not None
    assert verdict.figures["matching_losses"] == 3
    assert verdict.figures["losses"] == 3


# --- a series: degradation ----------------------------------------------------


def test_a_recent_collapse_against_a_profitable_baseline_is_a_degradation() -> None:
    baseline = [
        an_observation(100.0 if index % 5 else -100.0, closed_at=T0 + index * MINUTE)
        for index in range(25)
    ]
    recent = [
        an_observation(-100.0, closed_at=T0 + (25 + index) * MINUTE, regime="PANIC")
        for index in range(5)
    ]

    verdict = detect_degradation(baseline + recent)

    assert verdict is not None
    assert verdict.kind is LossKind.DEGRADATION
    assert verdict.figures["baseline_win_rate"] == 0.8
    assert verdict.figures["recent_win_rate"] == 0.0


def test_a_series_that_stays_healthy_shows_no_degradation() -> None:
    observations = [
        an_observation(100.0 if index % 5 else -100.0, closed_at=T0 + index * MINUTE)
        for index in range(30)
    ]

    assert detect_degradation(observations) is None


def test_analyse_series_prefers_degradation_over_a_recurring_pattern() -> None:
    baseline = [
        an_observation(100.0 if index % 5 else -100.0, closed_at=T0 + index * MINUTE)
        for index in range(25)
    ]
    recent = [
        an_observation(-100.0, closed_at=T0 + (25 + index) * MINUTE, regime="PANIC", session="ASIA")
        for index in range(5)
    ]

    verdict = analyse_series(baseline + recent)

    assert verdict.kind is LossKind.DEGRADATION


# --- persistence --------------------------------------------------------------


def test_analyse_loss_persists_the_deterministic_verdict_without_any_api_key(
    engine: Engine,
) -> None:
    verdict = asyncio.run(
        an_analyst(engine).analyse_loss(
            an_observation(-400.0), market="frxXAUUSD", at=T0, ref="witness@1.0.0"
        )
    )

    assert verdict.kind is LossKind.ISOLATED_ANOMALY
    rows = analysis_rows(engine)
    assert len(rows) == 1
    assert rows[0].kind is AnalysisKind.LOSS_ANALYSIS
    assert rows[0].model == "deterministic"
    assert rows[0].response is None
    assert rows[0].findings["kind"] == "isolated_anomaly"
    assert rows[0].findings["figures"]["loss_multiple"] == 4.0


def test_analyse_series_persists_a_degradation_analysis_with_its_figures(engine: Engine) -> None:
    baseline = [
        an_observation(100.0 if index % 5 else -100.0, closed_at=T0 + index * MINUTE)
        for index in range(25)
    ]
    recent = [
        an_observation(-100.0, closed_at=T0 + (25 + index) * MINUTE, regime="PANIC")
        for index in range(5)
    ]

    verdict = asyncio.run(
        an_analyst(engine).analyse_series(baseline + recent, market="frxXAUUSD", at=T0)
    )

    assert verdict.kind is LossKind.DEGRADATION
    rows = analysis_rows(engine)
    assert len(rows) == 1
    assert rows[0].kind is AnalysisKind.DEGRADATION
    assert rows[0].findings["figures"]["recent_win_rate"] == 0.0
    assert rows[0].findings["trades"] == 30


def test_analyse_series_records_the_common_trait_as_a_degradation_analysis(engine: Engine) -> None:
    observations = [
        an_observation(-100.0, regime="CHAOS", closed_at=T0 + index * MINUTE) for index in range(4)
    ] + [an_observation(-100.0, regime="TREND_UP", closed_at=T0 + 10 * MINUTE)]

    verdict = asyncio.run(
        an_analyst(engine).analyse_series(observations, market="frxXAUUSD", at=T0)
    )

    assert verdict.kind is LossKind.RECURRING_PATTERN
    rows = analysis_rows(engine)
    assert len(rows) == 1
    assert rows[0].kind is AnalysisKind.DEGRADATION
    assert rows[0].findings["figures"]["value"] == "CHAOS"


def test_analyse_series_records_nothing_when_nothing_stands_out(engine: Engine) -> None:
    observations = [
        an_observation(
            -100.0,
            regime=f"R{index}",
            session=f"S{index}",
            timeframe=timeframe,
            direction=Direction.BUY if index % 2 == 0 else Direction.SELL,
            closed_at=T0 + index * MINUTE,
        )
        for index, timeframe in enumerate((Timeframe.M1, Timeframe.M5, Timeframe.M15))
    ]

    verdict = asyncio.run(
        an_analyst(engine).analyse_series(observations, market="frxXAUUSD", at=T0)
    )

    assert verdict.kind is LossKind.NORMAL
    assert analysis_rows(engine) == []


# --- the model only comments --------------------------------------------------


def test_the_model_adds_a_comment_without_touching_the_verdict(engine: Engine) -> None:
    client = FakeClient('{"comment": "La perte dépasse le risque prévu de quatre fois."}')

    asyncio.run(
        an_analyst(engine, client).analyse_loss(
            an_observation(-400.0), market="frxXAUUSD", at=T0, ref="witness@1.0.0"
        )
    )

    rows = analysis_rows(engine)
    assert rows[0].response == "La perte dépasse le risque prévu de quatre fois."
    assert rows[0].model == "fake-model"
    assert rows[0].findings["kind"] == "isolated_anomaly"
    assert rows[0].findings["overrun_attempts"] == []


def test_a_broken_model_still_leaves_the_deterministic_analysis(engine: Engine) -> None:
    client = FakeClient(RuntimeError("api down"))

    verdict = asyncio.run(
        an_analyst(engine, client).analyse_loss(
            an_observation(-100.0), market="frxXAUUSD", at=T0, ref="witness@1.0.0"
        )
    )

    assert verdict.kind is LossKind.NORMAL
    rows = analysis_rows(engine)
    assert rows[0].findings["kind"] == "normal"
    assert rows[0].response is None
    assert rows[0].findings["model_error"] == "api down"


def test_a_malicious_model_cannot_create_an_order_or_touch_a_stop(engine: Engine) -> None:
    malicious = (
        '{"comment": "ok", "kind": "normal", "create_order": {"symbol": "frxXAUUSD",'
        ' "volume": 100}, "stop_loss": 0.0, "promote": "witness@1.0.0", "new_signal": "BUY"}'
    )
    client = FakeClient(malicious)

    asyncio.run(
        an_analyst(engine, client).analyse_loss(
            an_observation(-400.0), market="frxXAUUSD", at=T0, ref="witness@1.0.0"
        )
    )

    rows = analysis_rows(engine)
    # The deterministic verdict stands; every out-of-scope instruction is journalled.
    assert rows[0].findings["kind"] == "isolated_anomaly"
    assert set(rows[0].findings["overrun_attempts"]) == {
        "kind",
        "create_order",
        "stop_loss",
        "promote",
        "new_signal",
    }
    assert trading_counts(engine) == {"signals": 0, "orders": 0, "positions": 0, "trades": 0}
