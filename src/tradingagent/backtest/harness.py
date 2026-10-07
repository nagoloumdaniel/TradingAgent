"""The backtest harness (F-025, TASK-061).

The loop reuses the production decision path: every decision goes through
`strategies.evaluation.evaluate`, and every closed operation is an `analytics.model.Trade`
measured by `analytics.performance.compute_performance`. No strategy is reimplemented.

Look-ahead is impossible by construction. At bar `i` the harness builds each declared
timeframe's window with a bisect on the close time, so the strategy only ever receives
candles closed at or before `evaluated_at`. The only later data the simulator itself reads
is the bar in which an order actually fills, which is execution, not information. Same
inputs and same seed-free code give the same trades on every run (ENF-008).
"""

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from typing import Any

from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.costs import CostModel
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar, Slot
from tradingagent.indicators.volatility import atr
from tradingagent.strategies.base import Strategy
from tradingagent.strategies.evaluation import Outcome, OutcomeKind, evaluate
from tradingagent.strategies.manifest import StrategyManifest

DEFAULT_ATR_PERIOD = 14
DEFAULT_RISK_EUR = Decimal("10")


def _slot(moment: datetime) -> Slot:
    utc = moment.astimezone(UTC)
    return utc.weekday(), (utc.hour * 60 + utc.minute) // 15


@dataclass(frozen=True)
class TradingSession:
    """Open quarter-hour slots in UTC; `None` means every hour trades (crypto)."""

    open_slots: frozenset[Slot] | None = None

    @classmethod
    def always(cls) -> "TradingSession":
        return cls()

    @classmethod
    def from_calendar(cls, calendar: MarketCalendar) -> "TradingSession":
        return cls(frozenset(calendar.open_slots))

    def allows(self, moment: datetime) -> bool:
        return self.open_slots is None or _slot(moment) in self.open_slots


@dataclass(frozen=True)
class BacktestConfig:
    """Everything the simulation needs that is not the strategy itself."""

    symbol: str
    costs: CostModel = field(default_factory=CostModel)
    risk_eur: Decimal = DEFAULT_RISK_EUR
    mode: TradingMode = TradingMode.SIGNAL
    atr_period: int = DEFAULT_ATR_PERIOD
    max_concurrent_positions: int = 1
    partial_exit_fractions: tuple[float, ...] = ()
    move_stop_to_breakeven_after_first_target: bool = False
    trailing_stop_atr: float | None = None
    max_holding_bars: int | None = None
    session: TradingSession | None = None

    def __post_init__(self) -> None:
        if self.risk_eur <= 0:
            raise ValueError("risk_eur must be positive")
        if self.max_concurrent_positions < 1:
            raise ValueError("max_concurrent_positions must be at least 1")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive")
        if self.trailing_stop_atr is not None and self.trailing_stop_atr <= 0:
            raise ValueError("trailing_stop_atr must be positive when set")
        if self.max_holding_bars is not None and self.max_holding_bars < 1:
            raise ValueError("max_holding_bars must be at least 1 when set")
        if self.partial_exit_fractions and any(
            weight <= 0 for weight in self.partial_exit_fractions
        ):
            raise ValueError("partial exit fractions must be positive")


@dataclass(frozen=True)
class BacktestResult:
    """The replayed operations plus the counters a research report needs."""

    trades: tuple[Trade, ...]
    performance: Performance
    decisions: int
    signals: int
    entries: int
    expired_signals: int
    skipped_no_room: int
    invalid_signals: int
    insufficient_history: int
    strategy_errors: tuple[str, ...]
    forced_closures: int


@dataclass
class _PendingEntry:
    signal: SignalCandidate
    decision_index: int
    first_fill_index: int
    last_fill_index: int
    atr: float | None


@dataclass
class _Position:
    strategy_ref: str
    timeframe: Timeframe
    signal: SignalCandidate
    entry_index: int
    opened_at: datetime
    entry_price: float
    initial_stop: float
    stop: float
    targets: tuple[float, ...]
    weights: tuple[float, ...]
    taken: list[bool]
    remaining: float
    risk_price: float
    atr: float | None
    contributions: list[tuple[float, float]]
    adverse: list[float]


def decision_prefix(series: Sequence[Candle], evaluated_at: datetime) -> Sequence[Candle]:
    """The closed candles a decision at `evaluated_at` may see: a bisect on close time."""
    return series[: bisect_right(series, evaluated_at, key=lambda candle: candle.close_time)]


def run_backtest(
    strategy: Strategy[Any],
    manifest: StrategyManifest,
    candles: Mapping[Timeframe, Sequence[Candle]],
    config: BacktestConfig,
) -> BacktestResult:
    """Replay `strategy` over pre-loaded candles. Same inputs, same result, every time."""
    primary = candles.get(manifest.primary_timeframe)
    if primary is None:
        raise ValueError(f"no series for the primary timeframe {manifest.primary_timeframe}")
    if config.symbol not in manifest.allowed_symbols:
        raise ValueError(f"symbol {config.symbol!r} is not allowed by {manifest.ref}")
    _reject_unsorted(primary, manifest.primary_timeframe)

    evaluation_start = max(manifest.history_bars - 1, 0)
    if evaluation_start >= len(primary):
        raise ValueError(
            f"{len(primary)} candle(s) cannot feed {manifest.history_bars} history bars"
        )

    decisions = signals = entries = expired_signals = 0
    skipped_no_room = invalid_signals = insufficient_history = forced_closures = 0
    strategy_errors: list[str] = []
    pending: list[_PendingEntry] = []
    open_positions: list[_Position] = []
    trades: list[Trade] = []

    for index, bar in enumerate(primary):
        surviving: list[_PendingEntry] = []
        for entry in pending:
            if index > entry.last_fill_index:
                expired_signals += 1
            else:
                surviving.append(entry)
        pending = surviving

        for entry in list(pending):
            if index < entry.first_fill_index:
                continue
            if config.session is not None and not config.session.allows(bar.open_time):
                continue
            position = _try_enter(entry, bar, config, manifest, index)
            if position is None:
                continue
            pending.remove(entry)
            open_positions.append(position)
            entries += 1

        for position in list(open_positions):
            if position.entry_index >= index:
                continue
            trade = _manage(position, bar, config)
            if trade is not None:
                trades.append(trade)
                open_positions.remove(position)
            elif (
                config.max_holding_bars is not None
                and index - position.entry_index >= config.max_holding_bars
            ):
                _add_exit(position, position.remaining, bar.close, config)
                trades.append(_close(position, bar, config))
                open_positions.remove(position)
                forced_closures += 1

        if config.trailing_stop_atr is not None:
            for position in open_positions:
                if position.entry_index < index:
                    _trail(position, primary, index, config)

        if index < evaluation_start:
            continue

        outcome = _decide(strategy, manifest, candles, primary[index], config)
        decisions += 1
        if outcome.kind is OutcomeKind.INSUFFICIENT_HISTORY:
            insufficient_history += 1
            continue
        if outcome.kind is OutcomeKind.INVALID_SIGNAL:
            invalid_signals += 1
            continue
        if outcome.kind is OutcomeKind.STRATEGY_EXCEPTION:
            strategy_errors.append(outcome.detail)
            continue
        if outcome.kind is not OutcomeKind.SIGNAL or outcome.candidate is None:
            continue

        signals += 1
        if len(pending) + len(open_positions) >= config.max_concurrent_positions:
            # A signal nobody can act on is still a signal, but it creates no trade.
            skipped_no_room += 1
            continue
        needs_atr = config.costs.slippage_atr_fraction > 0 or config.trailing_stop_atr is not None
        first_fill = index + 1 + config.costs.execution_delay_bars
        pending.append(
            _PendingEntry(
                signal=outcome.candidate,
                decision_index=index,
                first_fill_index=first_fill,
                last_fill_index=first_fill + manifest.expiry_bars - 1,
                atr=_atr_at(primary, index, config.atr_period) if needs_atr else None,
            )
        )

    last = primary[-1]
    for position in open_positions:
        _add_exit(position, position.remaining, last.close, config)
        trades.append(_close(position, last, config))
        forced_closures += 1
    expired_signals += len(pending)

    ordered = tuple(sorted(trades, key=lambda trade: (trade.closed_at, trade.opened_at)))
    return BacktestResult(
        trades=ordered,
        performance=compute_performance(list(ordered)),
        decisions=decisions,
        signals=signals,
        entries=entries,
        expired_signals=expired_signals,
        skipped_no_room=skipped_no_room,
        invalid_signals=invalid_signals,
        insufficient_history=insufficient_history,
        strategy_errors=tuple(strategy_errors),
        forced_closures=forced_closures,
    )


def _decide(
    strategy: Strategy[Any],
    manifest: StrategyManifest,
    candles: Mapping[Timeframe, Sequence[Candle]],
    bar: Candle,
    config: BacktestConfig,
) -> Outcome:
    evaluated_at = bar.close_time
    windows = {
        timeframe: decision_prefix(candles[timeframe], evaluated_at)
        for timeframe in manifest.timeframes
        if timeframe in candles
    }
    return evaluate(strategy, manifest, config.symbol, windows, evaluated_at)


def _atr_at(series: Sequence[Candle], index: int, period: int) -> float | None:
    window = series[: index + 1]
    if len(window) <= period:
        return None
    values = atr(
        [candle.high for candle in window],
        [candle.low for candle in window],
        [candle.close for candle in window],
        period,
    )
    return values[-1]


def _try_enter(
    entry: _PendingEntry,
    bar: Candle,
    config: BacktestConfig,
    manifest: StrategyManifest,
    index: int,
) -> _Position | None:
    signal = entry.signal
    if signal.direction is Direction.BUY:
        if bar.low > signal.entry_high or bar.high < signal.entry_low:
            return None
        reference = min(bar.open, signal.entry_high)
        fill = config.costs.fill_price(reference, Direction.BUY, entry.atr)
    else:
        if bar.high < signal.entry_low or bar.low > signal.entry_high:
            return None
        reference = max(bar.open, signal.entry_low)
        fill = config.costs.fill_price(reference, Direction.SELL, entry.atr)

    sign = 1 if signal.direction is Direction.BUY else -1
    risk_price = abs(fill - signal.stop_loss)
    if risk_price <= 0 or sign * (signal.take_profits[0] - fill) <= 0:
        # Costs moved the entry past the first objective: the trade no longer exists.
        return None
    return _Position(
        strategy_ref=manifest.ref,
        timeframe=manifest.primary_timeframe,
        signal=signal,
        entry_index=index,
        opened_at=bar.close_time,
        entry_price=fill,
        initial_stop=signal.stop_loss,
        stop=signal.stop_loss,
        targets=tuple(signal.take_profits),
        weights=_weights(config.partial_exit_fractions, len(signal.take_profits)),
        taken=[False] * len(signal.take_profits),
        remaining=1.0,
        risk_price=risk_price,
        atr=entry.atr,
        contributions=[],
        adverse=[abs(fill - reference)],
    )


def _weights(configured: tuple[float, ...], targets: int) -> tuple[float, ...]:
    if not configured:
        return (1.0, *(0.0 for _ in range(targets - 1)))
    if len(configured) != targets:
        raise ValueError(
            f"{len(configured)} partial exit fraction(s) for {targets} take-profit level(s)"
        )
    total = sum(configured)
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"partial exit fractions must sum to 1.0, got {total}")
    return configured


def _manage(position: _Position, bar: Candle, config: BacktestConfig) -> Trade | None:
    sign = 1 if position.signal.direction is Direction.BUY else -1
    stop_hit = bar.low <= position.stop if sign > 0 else bar.high >= position.stop
    if stop_hit:
        # Pessimistic tie-break: a bar touching both the stop and a target stops out first.
        reference = min(bar.open, position.stop) if sign > 0 else max(bar.open, position.stop)
        _add_exit(position, position.remaining, reference, config)
        return _close(position, bar, config)

    for number, level in enumerate(position.targets):
        if position.taken[number]:
            continue
        reached = bar.high >= level if sign > 0 else bar.low <= level
        if not reached:
            continue
        fraction = position.weights[number]
        if fraction > 0:
            _add_exit(position, fraction, level, config)
        position.taken[number] = True
        if position.remaining <= 1e-12:
            return _close(position, bar, config)
        if config.move_stop_to_breakeven_after_first_target:
            position.stop = (
                max(position.stop, position.entry_price)
                if sign > 0
                else min(position.stop, position.entry_price)
            )
        return None
    return None


def _add_exit(
    position: _Position, fraction: float, reference: float, config: BacktestConfig
) -> None:
    exit_direction = Direction.SELL if position.signal.direction is Direction.BUY else Direction.BUY
    fill = config.costs.fill_price(reference, exit_direction, position.atr)
    position.contributions.append((fraction, fill))
    position.adverse.append(abs(fill - reference))
    position.remaining = max(0.0, position.remaining - fraction)


def _trail(
    position: _Position, primary: Sequence[Candle], index: int, config: BacktestConfig
) -> None:
    volatility = _atr_at(primary, index, config.atr_period)
    if volatility is None or config.trailing_stop_atr is None:
        return
    distance = config.trailing_stop_atr * volatility
    if position.signal.direction is Direction.BUY:
        position.stop = max(position.stop, primary[index].high - distance)
    else:
        position.stop = min(position.stop, primary[index].low + distance)


def _close(position: _Position, bar: Candle, config: BacktestConfig) -> Trade:
    sign = 1 if position.signal.direction is Direction.BUY else -1
    realized = sum(
        fraction * sign * (exit_price - position.entry_price) / position.risk_price
        for fraction, exit_price in position.contributions
    )
    pnl = Decimal(str(config.risk_eur)) * Decimal(repr(round(realized, 12)))
    pnl -= config.costs.commission()
    return Trade(
        symbol=config.symbol,
        strategy_ref=position.strategy_ref,
        direction=position.signal.direction,
        timeframe=position.timeframe,
        mode=config.mode,
        opened_at=position.opened_at,
        closed_at=bar.close_time,
        pnl_eur=pnl,
        risk_eur=config.risk_eur,
        slippage=(sum(position.adverse) / len(position.adverse)) if position.adverse else None,
        spread=config.costs.spread * config.costs.multiplier,
    )


def _reject_unsorted(series: Sequence[Candle], timeframe: Timeframe) -> None:
    for earlier, later in pairwise(series):
        if earlier.open_time >= later.open_time:
            raise ValueError(
                f"{timeframe} series is not strictly ordered at {later.open_time.isoformat()}"
            )
