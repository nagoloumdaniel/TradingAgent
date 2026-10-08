"""The paper-trading campaign measurement (cahier v3 §44, §53; Q-15; TASK-071).

Every figure asserted here is computed by hand in the comment above it, so a wrong verdict
is a failing test and not a coincidence. The three exit criteria are frozen in `PLAN`:
thirty calendar days, thirty operations per strategy, and a drawdown ceiling of 50 EUR.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import OrderState, PositionState, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.reporting.campaign import (
    PLAN,
    CampaignPlan,
    Verdict,
    progress,
    render,
)
from tradingagent.storage.daily import DailyPerformanceStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import (
    OrderRow,
    PositionRow,
    SignalRow,
    StrategyVersionRow,
    TradeRow,
)

AT = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
WITNESS = "witness@1.0.0"
BREAKOUT = "trend_breakout@1.0.0"
XAU = "XAUUSD"
BTC = "BTCUSD"
RISK = Decimal("5.00")


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'campaign.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def _chain(
    engine: Engine,
    *,
    index: int,
    market: str,
    ref: str,
    closed_at: datetime,
    pnl: Decimal,
    mode: TradingMode = TradingMode.PAPER,
    risk: Decimal = RISK,
) -> None:
    """One signal → order → position → closed trade, the same chain production writes."""
    with Session(engine) as session:
        version = session.query(StrategyVersionRow).filter_by(ref=ref).one_or_none()
        if version is None:
            strategy_id, _, number = ref.partition("@")
            version = StrategyVersionRow(
                ref=ref,
                strategy_id=strategy_id,
                version=number,
                manifest={"ref": ref},
                content_hash="a" * 64,
                first_seen_at=closed_at,
            )
            session.add(version)
            session.flush()
        signal = SignalRow(
            idempotency_key=f"sig:{index}",
            strategy_version_id=version.id,
            symbol=market,
            timeframe=Timeframe.H1,
            direction=Direction.BUY,
            mode=mode,
            observed_price=2650.0,
            entry_low=2649.0,
            entry_high=2651.0,
            stop_loss=2640.0,
            take_profits=[2660.0],
            reason="campagne",
            indicators={},
            generated_at=closed_at - timedelta(hours=1),
            expires_at=closed_at,
            state=SignalState.CLOSED,
        )
        session.add(signal)
        session.flush()
        order = OrderRow(
            idempotency_key=f"ord:{index}",
            signal_id=signal.id,
            symbol=market,
            direction=Direction.BUY,
            volume=Decimal("0.01"),
            requested_price=2650.0,
            stop_loss=2640.0,
            take_profit=2660.0,
            mode=mode,
            state=OrderState.FILLED,
            broker_order_ticket=500_000 + index,
            retcode=10009,
            broker_comment="done",
            created_at=closed_at,
            updated_at=closed_at,
        )
        session.add(order)
        session.flush()
        position = PositionRow(
            broker_position_ticket=600_000 + index,
            order_id=order.id,
            symbol=market,
            direction=Direction.BUY,
            volume=Decimal("0.01"),
            open_price=2650.0,
            stop_loss=2640.0,
            take_profit=2660.0,
            mode=mode,
            state=PositionState.CLOSED,
            opened_at=closed_at - timedelta(hours=1),
            updated_at=closed_at,
        )
        session.add(position)
        session.flush()
        session.add(
            TradeRow(
                position_id=position.id,
                mode=mode,
                closed_at=closed_at,
                close_price=2655.0,
                pnl_eur=pnl,
                risk_eur=risk,
                exit_reason="take_profit",
            )
        )
        session.commit()
    DailyPerformanceStore(engine).apply_trade(
        day=closed_at,
        mode=mode,
        market=market,
        ref=ref,
        pnl=pnl,
        risk_eur=risk,
        won=pnl > 0,
        at=closed_at,
    )


def _campaign(
    engine: Engine,
    pnls: list[Decimal],
    *,
    first_day: datetime,
    market: str = XAU,
    ref: str = WITNESS,
    index_base: int = 0,
) -> None:
    """One closed paper trade per P&L, closed one minute apart in the given order."""
    for offset, pnl in enumerate(pnls):
        _chain(
            engine,
            index=index_base + offset,
            market=market,
            ref=ref,
            closed_at=first_day + timedelta(minutes=offset),
            pnl=pnl,
        )


# --- the empty campaign -----------------------------------------------------------


def test_an_empty_campaign_has_not_started(engine: Engine) -> None:
    state = progress(engine, at=AT)

    assert state.strategies == ()
    assert state.verdict is Verdict.EN_COURS
    text = render(state)
    assert "EN COURS" in text
    assert "Aucune opération de campagne (PAPER ou DEMO) enregistrée" in text


# --- a campaign that just started -------------------------------------------------


def test_a_one_day_campaign_counts_one_day_elapsed(engine: Engine) -> None:
    # One trade closed yesterday: 2026-10-06, read on 2026-10-07 → 1 day, 29 remaining.
    _campaign(engine, [Decimal("12.50")], first_day=AT - timedelta(days=1))

    state = progress(engine, at=AT)

    assert state.verdict is Verdict.EN_COURS
    assert len(state.strategies) == 1
    strategy = state.strategies[0]
    assert (strategy.market, strategy.strategy_ref) == (XAU, WITNESS)
    assert strategy.elapsed_days == 1
    assert strategy.remaining_days == 29
    assert strategy.trades == 1
    assert strategy.net_profit == Decimal("12.50")
    assert strategy.max_drawdown == Decimal("0")
    criteria = {criterion.key: criterion for criterion in strategy.criteria}
    assert not criteria["duree"].satisfied
    assert not criteria["operations"].satisfied
    assert criteria["drawdown"].satisfied

    text = render(state)
    assert "1 / 30" in text
    assert "29 restant" in text
    assert "opérations cumulées : 1 / 30" in text


# --- the campaign is ready --------------------------------------------------------


def test_a_full_campaign_is_ready(engine: Engine) -> None:
    # 30 winning trades of +10, the first one 30 calendar days before the reading.
    _campaign(engine, [Decimal("10.00")] * 30, first_day=AT - timedelta(days=30))

    state = progress(engine, at=AT)

    assert state.verdict is Verdict.PRET
    strategy = state.strategies[0]
    assert strategy.elapsed_days == 30
    assert strategy.remaining_days == 0
    assert strategy.trades == 30
    assert strategy.net_profit == Decimal("300.00")
    assert strategy.max_drawdown == Decimal("0")
    assert strategy.missing == ()
    assert len(strategy.satisfied) == 3

    text = render(state)
    assert "Verdict global : PRÊT" in text
    assert "critères manquants" not in text
    assert "30 / 30" in text


# --- the campaign fails on the drawdown ------------------------------------------


def test_a_drawdown_breach_fails_the_campaign(engine: Engine) -> None:
    # +10, then -70, then 28 x +1. The equity peaks at 10 and troughs at -60, so the
    # maximum drawdown is 10 - (-60) = 70 EUR, above the 50 EUR ceiling. Net = -32.
    pnls = [Decimal("10.00"), Decimal("-70.00")] + [Decimal("1.00")] * 28
    _campaign(engine, pnls, first_day=AT - timedelta(days=30))

    state = progress(engine, at=AT)

    assert state.verdict is Verdict.ECHEC
    strategy = state.strategies[0]
    assert strategy.trades == 30
    assert strategy.elapsed_days == 30
    assert strategy.net_profit == Decimal("-32.00")
    assert strategy.max_drawdown == Decimal("70.00")
    criteria = {criterion.key: criterion for criterion in strategy.criteria}
    assert not criteria["drawdown"].satisfied
    assert criteria["drawdown"].observed == "70.00 EUR"
    assert criteria["drawdown"].target == "<= 50.00 EUR"

    text = render(state)
    assert "ÉCHEC" in text
    assert "70.00 EUR" in text
    assert "50.00 EUR" in text
    assert "TASK-064" in text


# --- the campaign fails on the number of operations -------------------------------


def test_too_few_operations_after_the_full_duration_fails(engine: Engine) -> None:
    # The full thirty days ran, but only twenty-nine trades closed: no comparable sample.
    _campaign(engine, [Decimal("1.00")] * 29, first_day=AT - timedelta(days=30))

    state = progress(engine, at=AT)

    assert state.verdict is Verdict.ECHEC
    strategy = state.strategies[0]
    assert strategy.elapsed_days == 30
    assert strategy.remaining_days == 0
    assert strategy.trades == 29
    criteria = {criterion.key: criterion for criterion in strategy.criteria}
    assert criteria["duree"].satisfied
    assert not criteria["operations"].satisfied
    assert criteria["drawdown"].satisfied

    text = render(state)
    assert "opérations cumulées : 29 / 30" in text
    assert "critères manquants" in text
    assert "29, cible 30 par stratégie" in text


# --- determinism and the injected instant -----------------------------------------


def test_the_reading_is_deterministic_and_takes_the_instant_as_input(
    engine: Engine,
) -> None:
    _campaign(engine, [Decimal("3.00")] * 3, first_day=AT - timedelta(days=5))

    first = progress(engine, at=AT)
    second = progress(engine, at=AT)
    assert first.to_dict() == second.to_dict()
    assert render(first) == render(second)

    later = progress(engine, at=AT + timedelta(days=2))
    assert later.strategies[0].trades == first.strategies[0].trades
    assert later.strategies[0].elapsed_days == first.strategies[0].elapsed_days + 2
    assert later.strategies[0].remaining_days == first.strategies[0].remaining_days - 2

    with pytest.raises(ValueError, match="timezone-aware"):
        # A naive instant is rejected on purpose: every moment in this project is UTC.
        progress(engine, at=datetime(2026, 10, 7, 12, 0))  # noqa: DTZ001


# --- per market and per strategy, paper only --------------------------------------


def test_each_market_and_strategy_is_measured_separately(engine: Engine) -> None:
    first_day = AT - timedelta(days=10)
    _campaign(engine, [Decimal("10.00"), Decimal("5.00")], first_day=first_day)
    _campaign(
        engine,
        [Decimal("-4.00")],
        first_day=first_day,
        market=BTC,
        ref=BREAKOUT,
        index_base=100,
    )
    # A demo trade now counts too: both venues are rehearsals, and the operator asked for the
    # thirty days to run in either. The report names the venues so the blend is visible.
    _chain(
        engine,
        index=200,
        market=XAU,
        ref=WITNESS,
        closed_at=first_day,
        pnl=Decimal("100.00"),
        mode=TradingMode.DEMO,
    )

    state = progress(engine, at=AT)

    keys = {(strategy.market, strategy.strategy_ref) for strategy in state.strategies}
    assert keys == {(XAU, WITNESS), (BTC, BREAKOUT)}
    assert state.markets == (BTC, XAU)
    by_key = {(s.market, s.strategy_ref): s for s in state.strategies}
    assert by_key[(XAU, WITNESS)].trades == 3, "two paper trades and one demo trade"
    assert by_key[(XAU, WITNESS)].net_profit == Decimal("115.00")
    assert by_key[(XAU, WITNESS)].venues == ("DEMO", "PAPER")
    assert by_key[(BTC, BREAKOUT)].trades == 1
    assert by_key[(BTC, BREAKOUT)].net_profit == Decimal("-4.00")

    only_btc = progress(engine, at=AT, market=BTC)
    assert len(only_btc.strategies) == 1
    assert only_btc.strategies[0].market == BTC


def test_a_plan_can_tighten_the_frozen_criteria() -> None:
    with pytest.raises(ValueError):
        CampaignPlan(min_days=0)
    with pytest.raises(ValueError):
        CampaignPlan(max_drawdown_eur=Decimal("-1"))
    assert PLAN.min_days == 30
    assert PLAN.min_trades == 30
    assert "Q-15" in PLAN.rule


def test_the_progress_is_json_serialisable(engine: Engine) -> None:
    _campaign(engine, [Decimal("10.00")], first_day=AT - timedelta(days=1))

    payload = json.dumps(progress(engine, at=AT).to_dict(), ensure_ascii=False)

    assert '"net_profit": "10.00"' in payload
    assert '"market": "XAUUSD"' in payload
    assert '"verdict": "EN COURS"' in payload
