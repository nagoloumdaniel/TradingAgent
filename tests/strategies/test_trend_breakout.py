from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from tradingagent.config.strategy_catalog import load_strategy_catalog
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode, mode_rank
from tradingagent.core.timeframe import Timeframe
from tradingagent.indicators.moving_average import ema
from tradingagent.indicators.volatility import atr
from tradingagent.strategies.contract import check_strategy_contract
from tradingagent.strategies.evaluation import Outcome, OutcomeKind, evaluate
from tradingagent.strategies.library.trend_breakout import TrendBreakout, TrendBreakoutParameters
from tradingagent.strategies.manifest import StrategyManifest
from tradingagent.strategies.registry import REGISTRY

START = datetime(2026, 10, 5, tzinfo=UTC)
STEP = timedelta(minutes=15)
BTC = "BTCUSD"
SHIPPED = Path(__file__).resolve().parents[2] / "config" / "strategies"

SMALL = TrendBreakoutParameters(
    channel_period=5,
    trend_period=5,
    atr_period=3,
    stop_atr_multiplier=1.5,
    take_profit_rr=2.5,
    entry_zone_atr=0.1,
)
HISTORY = 20


def manifest(history_bars: int = HISTORY) -> StrategyManifest:
    return StrategyManifest.model_validate(
        {
            "strategy_id": "trend_breakout",
            "version": "1.0.0",
            "max_mode": "SIGNAL",
            "allowed_symbols": [BTC],
            "timeframes": ["M15"],
            "history_bars": history_bars,
        }
    )


def candles_from(closes: Sequence[float]) -> list[Candle]:
    result = []
    previous = closes[0]
    for index, close in enumerate(closes):
        result.append(
            Candle(
                Timeframe.M15,
                START + STEP * index,
                previous,
                max(previous, close) + 1.0,
                min(previous, close) - 1.0,
                close,
            )
        )
        previous = close
    return result


def close_of(index: int) -> datetime:
    return START + STEP * (index + 1)


def replay(strategy: TrendBreakout, closes: Sequence[float]) -> list[Outcome]:
    series = {Timeframe.M15: candles_from(closes)}
    return [
        evaluate(strategy, manifest(), BTC, series, close_of(index))
        for index in range(HISTORY - 1, len(closes))
    ]


def oracle_breakout(closes: Sequence[float], index: int) -> Direction | None:
    """Independent channel computation, including the triggering candle in the window."""
    window = candles_from(closes)[index - HISTORY + 1 : index + 1]
    highs = [candle.high for candle in window]
    lows = [candle.low for candle in window]
    channel_high = max(highs[-SMALL.channel_period - 1 : -1])
    channel_low = min(lows[-SMALL.channel_period - 1 : -1])
    trend = ema([candle.close for candle in window], SMALL.trend_period)[-1]
    assert trend is not None
    close = closes[index]
    if close > channel_high and close > trend:
        return Direction.BUY
    if close < channel_low and close < trend:
        return Direction.SELL
    return None


FLAT_THEN_UP = [100.0] * 40 + [130.0]
FLAT_THEN_DOWN = [100.0] * 40 + [70.0]


@pytest.mark.parametrize(
    ("closes", "direction"), [(FLAT_THEN_UP, Direction.BUY), (FLAT_THEN_DOWN, Direction.SELL)]
)
def test_signals_exactly_on_the_channel_break(closes: list[float], direction: Direction) -> None:
    outcomes = replay(TrendBreakout(SMALL), closes)
    signals = [
        (index, outcome)
        for index, outcome in zip(range(HISTORY - 1, len(closes)), outcomes, strict=True)
        if outcome.kind is OutcomeKind.SIGNAL
    ]
    expected = [
        index for index in range(HISTORY - 1, len(closes)) if oracle_breakout(closes, index)
    ]
    assert [index for index, _ in signals] == expected
    assert len(expected) == 1
    assert signals[0][1].candidate is not None
    assert signals[0][1].candidate.direction is direction


@pytest.mark.parametrize("closes", [FLAT_THEN_UP, FLAT_THEN_DOWN])
def test_levels_derive_from_the_window_atr(closes: list[float]) -> None:
    outcomes = replay(TrendBreakout(SMALL), closes)
    index, outcome = next(
        (index, outcome)
        for index, outcome in zip(range(HISTORY - 1, len(closes)), outcomes, strict=True)
        if outcome.kind is OutcomeKind.SIGNAL
    )
    window = candles_from(closes)[index - HISTORY + 1 : index + 1]
    volatility = atr(
        [c.high for c in window], [c.low for c in window], [c.close for c in window], 3
    )[-1]
    assert volatility is not None
    close = closes[index]
    candidate = outcome.candidate
    assert candidate is not None
    sign = 1 if candidate.direction is Direction.BUY else -1
    assert candidate.entry_low == pytest.approx(close - 0.1 * volatility)
    assert candidate.entry_high == pytest.approx(close + 0.1 * volatility)
    assert candidate.stop_loss == pytest.approx(close - sign * 1.5 * volatility)
    assert candidate.take_profits == pytest.approx((close + sign * 2.5 * 1.5 * volatility,))
    assert candidate.indicators["atr"] == pytest.approx(volatility)


def test_no_signal_without_a_break() -> None:
    flat = [100.0] * 60
    assert all(
        outcome.kind is OutcomeKind.NO_SIGNAL for outcome in replay(TrendBreakout(SMALL), flat)
    )


def test_the_channel_excludes_the_triggering_candle() -> None:
    # Window highs are close + 1. If the triggering candle were folded into its own
    # channel, channel_high would be 111 and the 110 close would not break out.
    closes = [100.0] * 40 + [110.0]
    outcomes = replay(TrendBreakout(SMALL), closes)
    signal = next(outcome for outcome in outcomes if outcome.kind is OutcomeKind.SIGNAL)
    assert signal.candidate is not None
    assert signal.candidate.indicators["channel_high"] == pytest.approx(101.0)


def test_respects_the_strategy_contract() -> None:
    closes = [100.0] * 30 + [130.0] + [100.0] * 30
    series = {Timeframe.M15: candles_from(closes)}
    times = [close_of(index) for index in range(HISTORY - 1, len(closes))]
    assert check_strategy_contract(TrendBreakout(SMALL), manifest(), BTC, series, times) == []


def test_shipped_manifest_is_capped_at_signal_and_warmed_up() -> None:
    catalog = load_strategy_catalog(SHIPPED, REGISTRY)
    loaded = catalog["trend_breakout@1.0.0"]
    parameters = loaded.strategy.parameters
    assert isinstance(parameters, TrendBreakoutParameters)
    assert loaded.manifest.allowed_symbols == (BTC,)
    assert mode_rank(loaded.manifest.max_mode) <= mode_rank(TradingMode.SIGNAL)
    longest = max(parameters.trend_period, parameters.atr_period, parameters.channel_period)
    assert loaded.manifest.history_bars >= 5 * longest


def test_trend_breakout_is_in_the_production_registry() -> None:
    assert REGISTRY["trend_breakout"] is TrendBreakout


@pytest.mark.parametrize(
    "overrides",
    [
        {"entry_zone_atr": 2.0},
        {"take_profit_rr": 0.01},
        {"stop_atr_multiplier": 0},
        {"atr_period": 0},
        {"trend_period": 2},
        {"typo": 1},
    ],
)
def test_incoherent_parameters_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TrendBreakoutParameters.model_validate({**SMALL.model_dump(), **overrides})
