from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, overload

import pytest
from pydantic import BaseModel, ConfigDict

from tradingagent.core.market import Candle, Direction
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.evaluation import Outcome, OutcomeKind, evaluate
from tradingagent.strategies.manifest import StrategyManifest

START = datetime(2026, 10, 3, tzinfo=UTC)
GOLD = "frxXAUUSD"


class NoParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Spy(Strategy[NoParameters]):
    strategy_id = "spy"
    parameters_model = NoParameters

    def __init__(self) -> None:
        super().__init__(NoParameters())
        self.contexts: list[StrategyContext] = []

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        self.contexts.append(context)
        return None


class Breakout(Strategy[NoParameters]):
    """Buys when the last close exceeds every earlier close of the window."""

    strategy_id = "breakout"
    parameters_model = NoParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        closes = context.closes(Timeframe.M15)
        if closes[-1] <= max(closes[:-1]):
            return None
        return SignalCandidate(
            direction=Direction.BUY,
            entry_low=closes[-1],
            entry_high=closes[-1] + 0.5,
            stop_loss=min(closes) - 1,
            take_profits=(closes[-1] + 5,),
            reason="new high of the window",
            indicators={"window_high": max(closes[:-1])},
        )


class Dip(Strategy[NoParameters]):
    """Sells when the last close is below every earlier close of the window."""

    strategy_id = "dip"
    parameters_model = NoParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        closes = context.closes(Timeframe.M15)
        if closes[-1] >= min(closes[:-1]):
            return None
        return SignalCandidate(
            direction=Direction.SELL,
            entry_low=closes[-1] - 0.5,
            entry_high=closes[-1],
            stop_loss=max(closes) + 1,
            take_profits=(closes[-1] - 5,),
            reason="new low of the window",
            indicators={"window_low": min(closes[:-1])},
        )


class Raising(Spy):
    strategy_id = "raising"

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        raise ZeroDivisionError("division by zero")


class Incoherent(Spy):
    strategy_id = "incoherent"

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        return SignalCandidate(
            direction=Direction.BUY,
            entry_low=100,
            entry_high=101,
            stop_loss=105,
            take_profits=(110,),
            reason="stop above entry",
            indicators={},
        )


class WrongType(Spy):
    strategy_id = "wrong_type"

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        return "BUY"  # type: ignore[return-value]


class Interrupted(Spy):
    strategy_id = "interrupted"

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        raise KeyboardInterrupt


def manifest(
    strategy_id: str = "spy",
    timeframes: tuple[str, ...] = ("M15",),
    history_bars: int = 3,
    symbols: tuple[str, ...] = (GOLD,),
) -> StrategyManifest:
    return StrategyManifest.model_validate(
        {
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "max_mode": "SIGNAL",
            "allowed_symbols": list(symbols),
            "timeframes": list(timeframes),
            "history_bars": history_bars,
        }
    )


def series(timeframe: Timeframe, closes: Sequence[float]) -> list[Candle]:
    step = timedelta(seconds=timeframe.seconds)
    return [
        Candle(
            timeframe=timeframe,
            open_time=START + step * index,
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
        )
        for index, close in enumerate(closes)
    ]


def close_of(timeframe: Timeframe, index: int) -> datetime:
    return START + timedelta(seconds=timeframe.seconds) * (index + 1)


M15 = series(Timeframe.M15, [10, 11, 9, 12, 13, 11, 14, 10, 15, 16, 12, 17])


def run(
    strategy: Strategy[Any],
    at: datetime,
    candles: dict[Timeframe, Sequence[Candle]] | None = None,
    **manifest_overrides: Any,
) -> Outcome:
    built = manifest(strategy.strategy_id, **manifest_overrides)
    return evaluate(strategy, built, GOLD, candles or {Timeframe.M15: M15}, at)


def test_no_signal() -> None:
    assert run(Spy(), close_of(Timeframe.M15, 5)).kind is OutcomeKind.NO_SIGNAL


def test_signal_carries_the_candidate() -> None:
    outcome = run(Breakout(NoParameters()), close_of(Timeframe.M15, 4))
    assert outcome.kind is OutcomeKind.SIGNAL
    assert outcome.candidate is not None
    assert outcome.candidate.entry_low == 13


def test_window_holds_exactly_history_bars_closed_candles() -> None:
    spy = Spy()
    run(spy, close_of(Timeframe.M15, 5))
    window = spy.contexts[0].series(Timeframe.M15)
    assert window == tuple(M15[3:6])
    assert window[-1].close_time == close_of(Timeframe.M15, 5)
    assert spy.contexts[0].evaluated_at == close_of(Timeframe.M15, 5)
    assert spy.contexts[0].primary_timeframe is Timeframe.M15


def test_open_higher_timeframe_candle_is_never_passed() -> None:
    spy = Spy()
    m15 = series(Timeframe.M15, [10.0] * 12)
    h1 = series(Timeframe.H1, [20.0, 21.0, 22.0])
    at = close_of(Timeframe.M15, 9)  # 02:30, inside the third H1 candle
    run(spy, at, {Timeframe.M15: m15, Timeframe.H1: h1}, timeframes=("M15", "H1"), history_bars=2)
    assert spy.contexts[0].series(Timeframe.H1) == tuple(h1[:2])


@pytest.mark.parametrize(
    ("candles", "detail"),
    [
        ({Timeframe.M15: M15}, "M15: 3/5"),
        ({}, "M15: 0/5"),
    ],
)
def test_insufficient_history_never_calls_the_strategy(
    candles: dict[Timeframe, Sequence[Candle]], detail: str
) -> None:
    spy = Spy()
    built = manifest(history_bars=5)
    outcome = evaluate(spy, built, GOLD, candles, close_of(Timeframe.M15, 2))
    assert outcome.kind is OutcomeKind.INSUFFICIENT_HISTORY
    assert detail in outcome.detail
    assert spy.contexts == []


def test_insufficient_secondary_timeframe_is_reported() -> None:
    spy = Spy()
    h1 = series(Timeframe.H1, [20.0])
    outcome = run(
        spy,
        close_of(Timeframe.M15, 9),
        {Timeframe.M15: M15, Timeframe.H1: h1},
        timeframes=("M15", "H1"),
        history_bars=2,
    )
    assert outcome.kind is OutcomeKind.INSUFFICIENT_HISTORY
    assert "H1: 1/2" in outcome.detail
    assert spy.contexts == []


def test_strategy_exception_is_contained() -> None:
    outcome = run(Raising(), close_of(Timeframe.M15, 5))
    assert outcome.kind is OutcomeKind.STRATEGY_EXCEPTION
    assert outcome.detail == "ZeroDivisionError: division by zero"


def test_incoherent_signal_is_reported_as_invalid() -> None:
    outcome = run(Incoherent(), close_of(Timeframe.M15, 5))
    assert outcome.kind is OutcomeKind.INVALID_SIGNAL
    assert "stop-loss" in outcome.detail


def test_wrong_return_type_is_reported_as_invalid() -> None:
    outcome = run(WrongType(), close_of(Timeframe.M15, 5))
    assert outcome.kind is OutcomeKind.INVALID_SIGNAL
    assert "str" in outcome.detail


def test_keyboard_interrupt_is_not_swallowed() -> None:
    with pytest.raises(KeyboardInterrupt):
        run(Interrupted(), close_of(Timeframe.M15, 5))


@pytest.mark.parametrize(
    ("kind", "is_error"),
    [
        (OutcomeKind.SIGNAL, False),
        (OutcomeKind.NO_SIGNAL, False),
        (OutcomeKind.INSUFFICIENT_HISTORY, False),
        (OutcomeKind.INVALID_SIGNAL, True),
        (OutcomeKind.STRATEGY_EXCEPTION, True),
    ],
)
def test_only_strategy_faults_count_towards_quarantine(kind: OutcomeKind, is_error: bool) -> None:
    assert kind.is_strategy_error is is_error


def test_strategy_id_mismatch_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="strategy_id"):
        evaluate(Spy(), manifest("other"), GOLD, {Timeframe.M15: M15}, close_of(Timeframe.M15, 5))


def test_symbol_outside_the_manifest_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="cryBTCUSD"):
        evaluate(Spy(), manifest(), "cryBTCUSD", {Timeframe.M15: M15}, close_of(Timeframe.M15, 5))


def test_naive_evaluation_time_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="UTC"):
        run(Spy(), close_of(Timeframe.M15, 5).replace(tzinfo=None))


def test_unsorted_window_is_a_programming_error() -> None:
    shuffled = [*M15[:4], M15[5], M15[4], *M15[6:]]
    with pytest.raises(ValueError, match="sorted"):
        run(Spy(), close_of(Timeframe.M15, 6), {Timeframe.M15: shuffled})


def test_candle_of_another_timeframe_is_a_programming_error() -> None:
    intruder = series(Timeframe.H1, [10.0])[0]
    mixed = [*M15[:5], intruder]
    with pytest.raises(ValueError, match="timeframe"):
        run(Spy(), close_of(Timeframe.M15, 4) + timedelta(hours=1), {Timeframe.M15: mixed})


def test_missing_triggering_candle_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="triggering"):
        run(Spy(), close_of(Timeframe.M15, 5) + timedelta(minutes=5))


def test_future_candles_do_not_change_the_decision() -> None:
    strategy = Breakout(NoParameters())
    at = close_of(Timeframe.M15, 6)
    truncated = run(strategy, at, {Timeframe.M15: M15[:7]})
    complete = run(strategy, at, {Timeframe.M15: M15})
    assert truncated == complete
    assert complete.kind is OutcomeKind.SIGNAL


def test_two_markets_with_two_strategies_do_not_interfere() -> None:
    breakout, dip = Breakout(NoParameters()), Dip(NoParameters())
    gold = M15
    crypto = series(Timeframe.M15, [50, 48, 51, 47, 46, 49, 45, 44, 48, 43, 42, 47])
    times = [close_of(Timeframe.M15, index) for index in range(3, 12)]

    def on_gold(at: datetime) -> Outcome:
        return evaluate(breakout, manifest("breakout"), GOLD, {Timeframe.M15: gold}, at)

    def on_crypto(at: datetime) -> Outcome:
        built = manifest("dip", symbols=("cryBTCUSD",))
        return evaluate(dip, built, "cryBTCUSD", {Timeframe.M15: crypto}, at)

    isolated = ([on_gold(at) for at in times], [on_crypto(at) for at in times])
    interleaved: tuple[list[Outcome], list[Outcome]] = ([], [])
    for at in times:
        interleaved[1].append(on_crypto(at))
        interleaved[0].append(on_gold(at))
    assert interleaved == isolated
    for outcomes in isolated:
        assert any(outcome.kind is OutcomeKind.SIGNAL for outcome in outcomes)


class CountingSeries(Sequence[Candle]):
    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles
        self.accesses = 0

    def __len__(self) -> int:
        return len(self._candles)

    @overload
    def __getitem__(self, index: int) -> Candle: ...
    @overload
    def __getitem__(self, index: slice) -> Sequence[Candle]: ...
    def __getitem__(self, index: int | slice) -> Candle | Sequence[Candle]:
        self.accesses += 1
        return self._candles[index]


def test_long_history_is_searched_not_scanned() -> None:
    size = 100_000
    long_series = CountingSeries(series(Timeframe.M15, [10.0 + (i % 7) for i in range(size)]))
    outcome = run(Spy(), close_of(Timeframe.M15, size // 2), {Timeframe.M15: long_series})
    assert outcome.kind is OutcomeKind.NO_SIGNAL
    assert long_series.accesses < 100
