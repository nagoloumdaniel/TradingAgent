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
from typing import Any, Literal

from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.costs import CostModel
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar, Slot
from tradingagent.indicators.features import entry_features
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
    #: Follow the last **confirmed** swing instead of a fixed distance: a rising sequence of
    #: swing lows raises a long's stop, a falling sequence of swing highs lowers a short's.
    #:
    #: This is the piece `indicators/structure.py` was built for. A fixed ATR distance retreats
    #: when volatility rises even though the trend is intact, so it exits on noise; the swing
    #: only moves the stop when the market has actually made a new confirmed extreme. The value
    #: is the swing strength, and only swings confirmed as of the current bar are ever read --
    #: using an unconfirmed one would put a level into the stop that nobody knew was a swing
    #: yet, and the backtest would be beautiful and false.
    trailing_stop_swing_strength: int | None = None
    max_holding_bars: int | None = None
    session: TradingSession | None = None
    #: RM-012 as production applies it: the price actually **paid** must stay inside the band
    #: the strategy published. `risk.checks.check_entry_zone` measures the executable price
    #: against `entry_low`/`entry_high` and refuses the signal when it falls out; the harness
    #: never did, so a strategy cleared here could meet a gate that rejects most of its entries
    #: (BTCUSD, 2026-10-09: a 0.1 x ATR band 18.31 USD wide against an 18.424 USD spread).
    #:
    #: **Off by default, and it has to be.** Every campaign measured so far ran without it;
    #: switching it on silently would change what those numbers mean. A refusal here is final,
    #: exactly as it is in production: the signal dies, it is not deferred to the next bar.
    entry_zone_parity: bool = False
    #: Which price the gate judges. `paid` is the harness's own convention: the fill, half the
    #: spread included, against a band the strategy built on the close. `production` is the
    #: price a real account pays for that same fill — the whole spread at the ask on a buy, the
    #: bid on a sell — which is what `risk.checks.check_entry_zone` compares. `reference` is
    #: the gate moved into the band's own price space (spread excluded): what "compare the ask
    #: to a band shifted by the spread" is worth. All three are research bases; none is a
    #: production behaviour of its own beyond choosing which price the gate reads.
    #: Ignored unless `entry_zone_parity` is on.
    entry_zone_parity_basis: Literal["paid", "production", "reference"] = "paid"

    def __post_init__(self) -> None:
        if self.risk_eur <= 0:
            raise ValueError("risk_eur must be positive")
        if self.max_concurrent_positions < 1:
            raise ValueError("max_concurrent_positions must be at least 1")
        if self.atr_period < 1:
            raise ValueError("atr_period must be positive")
        if self.trailing_stop_atr is not None and self.trailing_stop_atr <= 0:
            raise ValueError("trailing_stop_atr must be positive when set")
        if self.trailing_stop_swing_strength is not None and self.trailing_stop_swing_strength < 1:
            raise ValueError("trailing stop swing strength must be at least 1 when set")
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
    #: Entries `entry_zone_parity` refused because the price paid fell outside the band.
    #: Zero unless the gate is on: it counts a refusal the harness previously did not model.
    refused_entry_zone: int = 0
    #: Signals the manifest's entry filter refused (session or volatility). Zero unless the
    #: manifest declares `entry_filter`: without one, `evaluate` returns what it always did.
    #:
    #: Counted separately from `signals` on purpose: a filtered signal is a decision the rule
    #: actually made, and folding it into "no signal" would make a closed door indistinguishable
    #: from an empty market.
    filtered_signals: int = 0


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
    #: Excursions recorded while the position lives, in R. Seeded with the entry bar, whose
    #: own range is real even though the barrier order inside that bar is unknowable.
    worst_r: float = 0.0
    best_r: float = 0.0
    #: The market context read off the bars known at the fill, kept for the closed trade.
    features: Mapping[str, float] = field(default_factory=dict)


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
    refused_entry_zone = filtered_signals = 0
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
            position = _try_enter(entry, bar, config, manifest, index, primary)
            if position is None:
                continue
            if config.entry_zone_parity and not _zone_accepts(
                entry.signal, _gated_price(position, config)
            ):
                # RM-012: production refuses a price paid outside the band the strategy
                # published, and the refusal ends the signal there. Deferring it to the next
                # bar would let the harness take a trade production has already refused.
                pending.remove(entry)
                refused_entry_zone += 1
                continue
            pending.remove(entry)
            open_positions.append(position)
            entries += 1

        for position in list(open_positions):
            if position.entry_index >= index:
                continue
            _record_excursion(position, bar)
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

        if config.trailing_stop_atr is not None or config.trailing_stop_swing_strength is not None:
            for position in open_positions:
                if position.entry_index < index:
                    _trail(position, primary, index, config)
                    _trail_structure(position, primary, index, config)

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
        if outcome.kind is OutcomeKind.FILTERED:
            # The manifest's entry filter said no. The signal is counted where it belongs --
            # with the decisions, not with the empty bars -- and it never reaches `pending`.
            filtered_signals += 1
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
        refused_entry_zone=refused_entry_zone,
        filtered_signals=filtered_signals,
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


def _zone_accepts(signal: SignalCandidate, paid: float) -> bool:
    """RM-012: the price paid must still be inside the band the strategy published.

    Both sides count. A price above `entry_high` is the chase the rule exists to stop; a price
    below `entry_low` means the move the strategy described no longer exists — the same two
    refusals `risk.checks.check_entry_zone` produces in production, measured the same way.
    """
    return signal.entry_low <= paid <= signal.entry_high


def _gated_price(position: _Position, config: BacktestConfig) -> float:
    """What the gate judges: the paid price, the price a real account pays, or the reference.

    `position.adverse[0]` is the cost added at the entry, and it moves *away* from the
    reference: a buy pays more, a sell receives less. Undoing it therefore needs the direction's
    sign — subtracting it from both would push a sell two spreads below its reference and refuse
    entries the rule never meant to refuse.

    Adding half the spread back gives the price a real account pays: the harness charges half the
    spread on each side, while a buy pays the whole one at the ask and a sell receives the bid,
    spread-free — the same round-trip cost booked on one side, which is exactly what changes the
    gate's verdict.
    """
    if position.adverse:
        if config.entry_zone_parity_basis == "reference":
            sign = 1.0 if position.signal.direction is Direction.BUY else -1.0
            return position.entry_price - sign * position.adverse[0]
        if config.entry_zone_parity_basis == "production":
            return position.entry_price + config.costs.half_spread
    return position.entry_price


def _try_enter(
    entry: _PendingEntry,
    bar: Candle,
    config: BacktestConfig,
    manifest: StrategyManifest,
    index: int,
    primary: Sequence[Candle],
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
        # The entry bar counts for the excursion: its range is real, even though which
        # barrier came first inside it is not knowable. Dropping it would shrink the MAE of
        # every trade that dipped right after being filled.
        worst_r=(
            (fill - bar.low) / risk_price
            if signal.direction is Direction.BUY
            else (bar.high - fill) / risk_price
        ),
        best_r=(
            (bar.high - fill) / risk_price
            if signal.direction is Direction.BUY
            else (fill - bar.low) / risk_price
        ),
        features=_entry_features(primary, index, config),
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


def _trail_structure(
    position: _Position, primary: Sequence[Candle], index: int, config: BacktestConfig
) -> None:
    """Move the stop to the last swing confirmed **at bar `index`**, never loosening it.

    Only bars up to `index` are read, so a swing appearing later can never be used: the whole
    guarantee `structure.py` enforces, that a swing is confirmed only once `strength` further
    bars have closed, is what makes this safe.

    **The scan is incremental, and it has to be.** Recomputing the swing series on every bar
    made the backtest quadratic: measured at over twenty minutes of CPU for a single pass over
    60 000 bars, against seconds for the rest of the harness. A swing at index `k` is confirmed
    exactly when bar `k + strength` closes, so extending a prefix by one bar can only add
    **that one** candidate -- and only once it is confirmed. Tracking it is therefore
    equivalent, and linear.
    """
    strength = config.trailing_stop_swing_strength
    if strength is None or index < strength:
        return
    # Le seul candidat qui devient confirmable en fermant la barre `index` est celle d'il y a
    # `strength` barres : rien d'autre ne change dans un préfixe qui grandit d'un cran.
    candidate = index - strength
    if position.signal.direction is Direction.BUY:
        level = _confirmed_swing_low(primary, candidate, strength)
        if level is not None:
            position.stop = max(position.stop, level)
    else:
        level = _confirmed_swing_high(primary, candidate, strength)
        if level is not None:
            position.stop = min(position.stop, level)


def _confirmed_swing_low(primary: Sequence[Candle], index: int, strength: int) -> float | None:
    """Le plus bas de la barre `index` s'il est un creux confirmé, sinon rien.

    La barre n'est examinée que si ses `strength` voisines de **chaque côté** existent déjà :
    un creux ne se confirme pas sur un futur qui n'est pas encore écrit.
    """
    if index < strength or index + strength >= len(primary):
        return None
    low = primary[index].low
    neighbours = [candle.low for candle in primary[index - strength : index]]
    neighbours += [candle.low for candle in primary[index + 1 : index + strength + 1]]
    return low if all(low < other for other in neighbours) else None


def _confirmed_swing_high(primary: Sequence[Candle], index: int, strength: int) -> float | None:
    """Le plus haut de la barre `index` s'il est un sommet confirmé, sinon rien."""
    if index < strength or index + strength >= len(primary):
        return None
    high = primary[index].high
    neighbours = [candle.high for candle in primary[index - strength : index]]
    neighbours += [candle.high for candle in primary[index + 1 : index + strength + 1]]
    return high if all(high > other for other in neighbours) else None


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


def _entry_features(
    primary: Sequence[Candle], index: int, config: BacktestConfig
) -> dict[str, float]:
    """The market context at the fill, from the bars known **at** the fill and no others.

    The prefix stops at the entry bar: a context computed over later bars would describe a
    market the decision did not face, and would make the features look predictive when they
    are only painted after the fact. Whatever cannot be measured on that prefix is left out
    by `entry_features`, which is the honest reading of "not measured".
    """
    prefix = primary[: index + 1]
    if not prefix:
        return {}
    return entry_features(
        [candle.close_time for candle in prefix],
        [candle.high for candle in prefix],
        [candle.low for candle in prefix],
        [candle.close for candle in prefix],
        atr_period=config.atr_period,
    )


def _record_excursion(position: _Position, bar: Candle) -> None:
    """Remember how far this bar took the trade against us and for us, in R.

    Both numbers are measured against the price actually paid at entry and the risk the trade
    was sized on, so they are comparable across instruments and across stop sizes. They are
    only read when the trade closes: nothing here feeds a decision, which is what makes them
    safe to compute from the real OHLC of the bars the position lived through.
    """
    if position.risk_price <= 0:
        return
    sign = 1 if position.signal.direction is Direction.BUY else -1
    if sign > 0:
        adverse = (position.entry_price - bar.low) / position.risk_price
        favourable = (bar.high - position.entry_price) / position.risk_price
    else:
        adverse = (bar.high - position.entry_price) / position.risk_price
        favourable = (position.entry_price - bar.low) / position.risk_price
    position.worst_r = max(position.worst_r, adverse)
    position.best_r = max(position.best_r, favourable)


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
        mae_r=round(position.worst_r, 12),
        mfe_r=round(position.best_r, 12),
        features=dict(position.features),
    )


def _reject_unsorted(series: Sequence[Candle], timeframe: Timeframe) -> None:
    for earlier, later in pairwise(series):
        if earlier.open_time >= later.open_time:
            raise ValueError(
                f"{timeframe} series is not strictly ordered at {later.open_time.isoformat()}"
            )
