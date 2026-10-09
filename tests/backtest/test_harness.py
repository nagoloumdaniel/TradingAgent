"""TASK-061: the simulation loop, look-ahead impossibility, determinism, Trade format.

Hand-checked figures (M15, no costs, risk 10 EUR):
    signal at bar 1 (close 100.0): entry zone [100.0, 100.5], stop 99.0, target 102.0
    bar 2 opens at 100.2 inside the zone  -> fill 100.2, risk distance 1.2
    bar 3 reaches the target 102.0       -> R = (102.0 - 100.2) / 1.2 = 1.5
    pnl = 10 EUR * 1.5 = 15 EUR
With spread 0.4 and fixed slippage 0.1 on both sides and a 1 EUR commission:
    entry = 100.2 + 0.2 + 0.1 = 100.5, risk = 1.5
    exit  = 102.0 - 0.2 - 0.1 = 101.7   -> R = 1.2 / 1.5 = 0.8
    pnl = 10 * 0.8 - 1 = 7 EUR
With two targets at 102.0 and 103.2, half each:
    R = 0.5 * 1.5 + 0.5 * 2.5 = 2.0 -> 20 EUR
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from tradingagent.analytics.model import Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.harness import (
    BacktestConfig,
    TradingSession,
    decision_prefix,
    run_backtest,
)
from tradingagent.core.market import Candle, Direction
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.manifest import StrategyManifest

START = datetime(2026, 10, 3, tzinfo=UTC)
GOLD = "frxXAUUSD"
M15 = Timeframe.M15
STEP = timedelta(seconds=M15.seconds)


class NoParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OneShot(Strategy[NoParameters]):
    """Emits one BUY the first time the window closes at 100.0."""

    strategy_id = "oneshot"
    parameters_model = NoParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        if context.closes(M15)[-1] != 100.0:
            return None
        return SignalCandidate(
            direction=Direction.BUY,
            entry_low=100.0,
            entry_high=100.5,
            stop_loss=99.0,
            take_profits=(102.0,),
            reason="hand-checked fixture",
            indicators={"atr": 1.0},
        )


class TwoTargets(OneShot):
    strategy_id = "twotargets"

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        signal = super().evaluate(context)
        if signal is None:
            return None
        return SignalCandidate(
            direction=signal.direction,
            entry_low=signal.entry_low,
            entry_high=signal.entry_high,
            stop_loss=signal.stop_loss,
            take_profits=(102.0, 103.2),
            reason=signal.reason,
            indicators=signal.indicators,
        )


class EveryBar(Strategy[NoParameters]):
    """Signals on every evaluated bar, with objectives the fixture never reaches."""

    strategy_id = "everybar"
    parameters_model = NoParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        close = context.closes(M15)[-1]
        return SignalCandidate(
            direction=Direction.BUY,
            entry_low=close - 0.1,
            entry_high=close + 0.1,
            stop_loss=1.0,
            take_profits=(200.0,),
            reason="every bar",
            indicators={},
        )


class Tripwire(Strategy[NoParameters]):
    """Records every window and fails loudly if a candle is not closed yet."""

    strategy_id = "tripwire"
    parameters_model = NoParameters

    def __init__(self) -> None:
        super().__init__(NoParameters())
        self.windows: list[tuple[datetime, tuple[Candle, ...]]] = []
        self.violations: list[datetime] = []

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        window = context.series(M15)
        self.windows.append((context.evaluated_at, window))
        if any(candle.close_time > context.evaluated_at for candle in window):
            self.violations.append(context.evaluated_at)
            raise AssertionError("a future candle reached the strategy")
        return None


def manifest(strategy_id: str, history_bars: int = 2, expiry_bars: int = 1) -> StrategyManifest:
    return StrategyManifest.model_validate(
        {
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "max_mode": "SIGNAL",
            "allowed_symbols": [GOLD],
            "timeframes": ["M15"],
            "history_bars": history_bars,
            "expiry_bars": expiry_bars,
        }
    )


def candles_from(rows: Sequence[tuple[float, float, float, float]]) -> tuple[Candle, ...]:
    return tuple(
        Candle(
            timeframe=M15,
            open_time=START + STEP * index,
            open=row[0],
            high=row[1],
            low=row[2],
            close=row[3],
        )
        for index, row in enumerate(rows)
    )


PNL_ROWS = [
    (99.0, 100.0, 98.5, 100.0),
    (100.0, 100.5, 99.5, 100.0),
    (100.2, 101.0, 100.1, 100.8),
    (100.8, 102.5, 100.5, 102.0),
    (102.0, 102.5, 101.5, 102.2),
]


def run(
    strategy: Strategy[Any],
    rows: Sequence[tuple[float, float, float, float]],
    *,
    history_bars: int = 2,
    expiry_bars: int = 1,
    **config: Any,
) -> Any:
    built = manifest(strategy.strategy_id, history_bars=history_bars, expiry_bars=expiry_bars)
    return run_backtest(
        strategy,
        built,
        {M15: candles_from(rows)},
        BacktestConfig(symbol=GOLD, **config),
    )


def test_hand_computed_trade() -> None:
    result = run(OneShot(NoParameters()), PNL_ROWS)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert isinstance(trade, Trade)
    assert trade.direction is Direction.BUY
    assert trade.strategy_ref == "oneshot@1.0.0"
    assert trade.opened_at == START + STEP * 3
    assert trade.closed_at == START + STEP * 4
    assert trade.pnl_eur == Decimal("15")
    assert trade.risk_eur == Decimal("10")


def test_hand_computed_trade_with_costs() -> None:
    costs = CostModel(spread=0.4, slippage_fixed=0.1, commission_per_trade=Decimal("1"))
    result = run(OneShot(NoParameters()), PNL_ROWS, costs=costs)
    assert len(result.trades) == 1
    assert result.trades[0].pnl_eur == Decimal("7")
    assert result.trades[0].spread == pytest.approx(0.4)


def test_cost_stress_degrades_the_result() -> None:
    costs = CostModel(spread=0.4, slippage_fixed=0.1, commission_per_trade=Decimal("1"))
    base = run(OneShot(NoParameters()), PNL_ROWS, costs=costs)
    hard = run(OneShot(NoParameters()), PNL_ROWS, costs=costs.stressed(2.0))
    assert base.trades[0].pnl_eur == Decimal("7")
    assert hard.trades[0].pnl_eur < base.trades[0].pnl_eur


def test_hand_computed_partial_exits() -> None:
    rows = [*PNL_ROWS[:3], (100.8, 102.5, 100.5, 102.0), (102.0, 103.5, 101.8, 103.0)]
    result = run(TwoTargets(NoParameters()), rows, partial_exit_fractions=(0.5, 0.5))
    assert len(result.trades) == 1
    assert result.trades[0].pnl_eur == Decimal("20")


def test_stop_loss_is_honoured_and_hand_computed() -> None:
    rows = [*PNL_ROWS[:3], (99.0, 99.2, 98.5, 98.6)]
    result = run(OneShot(NoParameters()), rows)
    assert len(result.trades) == 1
    assert result.trades[0].pnl_eur == Decimal("-10")


def test_no_future_candle_reaches_the_strategy() -> None:
    rows = [(100.0 + index, 101.0 + index, 99.0 + index, 100.0 + index) for index in range(20)]
    poisoned = list(rows)
    poisoned[15] = (1_000_000.0, 1_000_001.0, 999_999.0, 1_000_000.0)
    tripwire = Tripwire()
    result = run(tripwire, poisoned, history_bars=3)
    assert tripwire.violations == []
    assert result.strategy_errors == ()
    assert len(tripwire.windows) == 18
    for evaluated_at, window in tripwire.windows:
        assert len(window) == 3
        assert window[-1].close_time == evaluated_at
        assert all(candle.close_time <= evaluated_at for candle in window)


def test_a_poisoned_future_bar_cannot_change_an_earlier_trade() -> None:
    rows = [(100.0 + index, 101.0 + index, 99.0 + index, 100.0 + index) for index in range(20)]
    clean = run(EveryBar(NoParameters()), rows, max_holding_bars=5)
    poisoned_rows = list(rows)
    poisoned_rows[15] = (1_000_000.0, 1_000_001.0, 999_999.0, 1_000_000.0)
    poisoned = run(EveryBar(NoParameters()), poisoned_rows, max_holding_bars=5)
    assert clean.trades[0] == poisoned.trades[0]


def test_decision_prefix_is_a_bisect_on_close_time() -> None:
    series = candles_from(PNL_ROWS)
    assert decision_prefix(series, series[2].close_time) == series[:3]
    assert decision_prefix(series, series[2].close_time - timedelta(seconds=1)) == series[:2]


def test_two_identical_runs_give_an_identical_result() -> None:
    first = run(EveryBar(NoParameters()), PNL_ROWS + PNL_ROWS, max_holding_bars=2)
    second = run(EveryBar(NoParameters()), PNL_ROWS + PNL_ROWS, max_holding_bars=2)
    assert first == second
    assert first.trades == second.trades
    assert first.performance == second.performance


def test_trades_are_measured_by_the_production_analytics() -> None:
    result = run(EveryBar(NoParameters()), PNL_ROWS + PNL_ROWS, max_holding_bars=2)
    assert result.trades
    assert all(isinstance(trade, Trade) for trade in result.trades)
    assert result.performance == compute_performance(list(result.trades))


def test_expired_signal_creates_no_trade() -> None:
    rows = [
        (99.0, 100.0, 98.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (110.0, 111.0, 109.0, 110.0),
        (110.0, 111.0, 109.0, 110.0),
    ]
    result = run(OneShot(NoParameters()), rows, expiry_bars=1)
    assert result.signals == 1
    assert result.entries == 0
    assert result.expired_signals == 1
    assert result.trades == ()


def test_simultaneous_positions_are_limited() -> None:
    rows = [(100.0 + index, 101.0 + index, 99.0 + index, 100.0 + index) for index in range(12)]
    result = run(EveryBar(NoParameters()), rows, max_concurrent_positions=1, max_holding_bars=5)
    assert result.skipped_no_room >= 1
    assert result.entries == len(result.trades)
    ordered = sorted(result.trades, key=lambda trade: trade.opened_at)
    for earlier, later in pairwise(ordered):
        assert later.opened_at >= earlier.closed_at


def test_trading_session_blocks_entries_outside_allowed_hours() -> None:
    closed = TradingSession(open_slots=frozenset())
    result = run(EveryBar(NoParameters()), PNL_ROWS + PNL_ROWS, session=closed)
    assert result.signals > 0
    assert result.entries == 0
    assert result.trades == ()


def test_trailing_stop_improves_a_runaway_trend() -> None:
    rows = [
        (99.0, 100.0, 98.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.2, 101.0, 100.1, 100.8),
        (100.8, 101.0, 100.5, 100.9),
        (100.9, 101.2, 100.0, 100.2),
        (100.2, 100.5, 98.0, 98.5),
    ]
    fixed = run(OneShot(NoParameters()), rows, atr_period=2)
    trailed = run(OneShot(NoParameters()), rows, atr_period=2, trailing_stop_atr=1.0)
    assert fixed.trades[0].pnl_eur == Decimal("-10")
    assert trailed.trades[0].pnl_eur > fixed.trades[0].pnl_eur


def test_missing_primary_series_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="primary timeframe"):
        run_backtest(
            OneShot(NoParameters()),
            manifest("oneshot"),
            {},
            BacktestConfig(symbol=GOLD),
        )


def test_manifest_history_longer_than_data_is_refused() -> None:
    with pytest.raises(ValueError, match="history bars"):
        run(OneShot(NoParameters()), PNL_ROWS[:2], history_bars=10)


# -- entry-zone parity: the gate production applies and the harness did not -----------------
#
# Hand-checked on PNL_ROWS: the signal fires on bar 1 (close 100.0) with the band
# [100.0, 100.5]. Bar 2 opens at 100.2, so the reference is min(100.2, 100.5) = 100.2.
# With spread 1.0 and fixed slippage 0.1 the paid price is 100.2 + 0.5 + 0.1 = 100.8, i.e.
# **0.3 above the band**: RM-012 refuses that price in production, and so must the harness.

ABOVE_THE_BAND = CostModel(spread=1.0, slippage_fixed=0.1)


def test_entry_zone_parity_is_off_by_default() -> None:
    """Every campaign measured so far must keep its meaning: the gate never acts unasked."""
    assert BacktestConfig(symbol=GOLD).entry_zone_parity is False

    result = run(OneShot(NoParameters()), PNL_ROWS, costs=ABOVE_THE_BAND)

    assert len(result.trades) == 1
    assert result.refused_entry_zone == 0


def test_entry_zone_parity_refuses_a_price_paid_above_the_band() -> None:
    result = run(OneShot(NoParameters()), PNL_ROWS, costs=ABOVE_THE_BAND, entry_zone_parity=True)

    assert result.signals == 1
    assert result.entries == 0
    assert result.refused_entry_zone == 1
    assert result.trades == ()
    # Refused, not expired: the two are different events and the counters keep them apart.
    assert result.expired_signals == 0


def test_entry_zone_parity_accepts_a_price_paid_inside_the_band() -> None:
    """Without costs the fill is 100.2, inside [100.0, 100.5]: the gate is not a blanket no."""
    result = run(OneShot(NoParameters()), PNL_ROWS, entry_zone_parity=True)

    assert result.entries == 1
    assert result.refused_entry_zone == 0
    assert len(result.trades) == 1


def test_entry_zone_parity_refuses_a_price_paid_below_the_band() -> None:
    """The other side of RM-012, the one signal #2 of the incident met: the price gapped
    under the zone, so the breakout the strategy described no longer exists."""
    rows = [
        (99.0, 100.0, 98.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (99.5, 100.4, 99.2, 99.6),
        (100.8, 102.5, 100.5, 102.0),
    ]
    taken = run(OneShot(NoParameters()), rows)  # fill 99.5, below entry_low
    gated = run(OneShot(NoParameters()), rows, entry_zone_parity=True)

    assert len(taken.trades) == 1 and taken.refused_entry_zone == 0
    assert gated.trades == ()
    assert gated.refused_entry_zone == 1


def test_entry_zone_parity_refusal_is_final_and_never_deferred() -> None:
    """Production ends the signal at the refusal; it does not wait for a better bar.

    Bar 2 pays 100.8 (outside), bar 3 would pay 100.4 (inside) and is still within the
    two-bar window. A gate that merely returned None would fill on bar 3 and make this
    test fail: the refusal must drop the pending entry.
    """
    rows = [
        (99.0, 100.0, 98.5, 100.0),
        (100.0, 100.5, 99.5, 100.0),
        (100.2, 101.0, 100.1, 100.8),
        (99.8, 100.6, 99.7, 100.0),
    ]
    retried = run(
        OneShot(NoParameters()),
        rows,
        costs=ABOVE_THE_BAND,
        expiry_bars=2,
        entry_zone_parity=True,
    )

    assert retried.refused_entry_zone == 1
    assert retried.entries == 0
    assert retried.trades == ()


def test_entry_zone_parity_on_the_reference_is_the_band_shifted_by_the_costs() -> None:
    """The `reference` basis judges the level the fill started from: 100.2, inside the band.

    It is what "compare the ask to a band shifted by the spread" is worth, measured rather
    than argued — the same order, judged where the strategy built its band instead of where
    the costs left it. Only reachable when the gate is on.
    """
    on_the_reference = run(
        OneShot(NoParameters()),
        PNL_ROWS,
        costs=ABOVE_THE_BAND,
        entry_zone_parity=True,
        entry_zone_parity_basis="reference",
    )
    on_the_paid_price = run(
        OneShot(NoParameters()), PNL_ROWS, costs=ABOVE_THE_BAND, entry_zone_parity=True
    )

    assert on_the_reference.entries == 1
    assert on_the_reference.refused_entry_zone == 0
    assert on_the_paid_price.entries == 0


def test_the_basis_is_inert_when_the_gate_is_off() -> None:
    """Only one switch changes a measurement: the second field alone must do nothing."""
    plain = run(OneShot(NoParameters()), PNL_ROWS, costs=ABOVE_THE_BAND)
    shifted = run(
        OneShot(NoParameters()),
        PNL_ROWS,
        costs=ABOVE_THE_BAND,
        entry_zone_parity_basis="reference",
    )

    assert plain.trades == shifted.trades
    assert plain.refused_entry_zone == shifted.refused_entry_zone == 0
