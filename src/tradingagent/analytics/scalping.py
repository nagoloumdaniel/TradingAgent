"""The scalping statistics of the cahier v3 §32, as pure functions.

Same doctrine as `performance.py`: every figure comes from the trade list alone — no clock, no
database, no network — so a backtest window and a production window are measured by exactly
the same code (C-001). Nothing is invented here:

* a trade that carries no spread, no slippage or no duration reading is left out of the figure
  that needs it, and the figure stays `None`; the count of what was actually measurable is
  reported next to it (`sample`, `slippage_sample`, `spread_sample`, `cost_sample`);
* a band no trade falls into is omitted, not filled with zeros;
* two fields of §32 are not part of the `Trade` record — the volatility of the market at the
  signal and the size of the position. They are never guessed: `by_volatility` and `by_size`
  take the reading as a callable, and the caller (the dashboard, or `storage.scalping`) owns
  the lookup. Likewise `cost_summary` takes the euro cost of a trade as an optional callable:
  `Trade` stores no euro cost, so without it that figure stays `None`.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from statistics import fmean

from tradingagent.analytics.model import Trade


def _sort_key(trade: Trade) -> tuple[datetime, datetime, str, str]:
    """A trade is measured on its closing time, like `axes.group`; ties break deterministically."""
    return (trade.closed_at, trade.opened_at, trade.symbol, trade.strategy_ref)


@dataclass(frozen=True)
class Bucket:
    """One slice of the trades along an axis, with the figures of that slice.

    `win_rate` is `None` only for a bucket with no trade; the constructors below never
    produce one, because an empty slice is reported by its absence, not by a zero.
    """

    key: str
    trades: tuple[Trade, ...]
    net_profit: Decimal
    win_rate: float | None
    expectancy: Decimal

    @property
    def sample(self) -> int:
        """How many trades the bucket actually holds."""
        return len(self.trades)


class Session(StrEnum):
    """The four UTC sessions of §32, in the order the dashboard shows them."""

    ASIA = "asie"
    LONDON = "londres"
    NEW_YORK = "new_york"
    AFTER_HOURS = "apres_cloture"


# Half-open hours: [start, end[. 0-7, 7-13, 13-21, 21-24 UTC, covering every hour once.
SESSION_HOURS: tuple[tuple[Session, int, int], ...] = (
    (Session.ASIA, 0, 7),
    (Session.LONDON, 7, 13),
    (Session.NEW_YORK, 13, 21),
    (Session.AFTER_HOURS, 21, 24),
)


@dataclass(frozen=True)
class CostSummary:
    """What a trade cost in the market, and on how many trades each figure was measured.

    `sample` counts the trades carrying at least one cost reading; each figure has its own
    count, so an average is never read as if it covered the whole series.
    """

    sample: int
    slippage_sample: int
    average_slippage: float | None
    max_slippage: float | None
    spread_sample: int
    average_spread: float | None
    cost_sample: int
    average_cost_eur: Decimal | None


def session_of(trade: Trade) -> Session:
    """The UTC session a trade closed in (boundaries: 7h, 13h and 21h)."""
    for session, start, end in SESSION_HOURS:
        if start <= trade.closed_at.hour < end:
            return session
    raise AssertionError(f"hour {trade.closed_at.hour} belongs to no session")


def by_hour(trades: Iterable[Trade]) -> tuple[Bucket, ...]:
    """One bucket per closing hour actually traded, from 00 to 23."""
    grouped = _group(trades, lambda trade: trade.closed_at.hour)
    return tuple(_bucket(f"{hour:02d}", grouped[hour]) for hour in sorted(grouped))


def by_session(trades: Iterable[Trade]) -> tuple[Bucket, ...]:
    """One bucket per UTC session actually traded, in session order."""
    grouped = _group(trades, session_of)
    return tuple(
        _bucket(session.value, grouped[session])
        for session, _, _ in SESSION_HOURS
        if session in grouped
    )


def by_weekday(trades: Iterable[Trade]) -> tuple[Bucket, ...]:
    """One bucket per weekday actually traded, Monday first."""
    grouped = _group(trades, lambda trade: trade.closed_at.weekday())
    return tuple(
        _bucket(grouped[day][0].closed_at.strftime("%A"), grouped[day]) for day in sorted(grouped)
    )


def by_spread(trades: Iterable[Trade], edges: Sequence[float]) -> tuple[Bucket, ...]:
    """Spread bands, `edges` in observed spread units: `[e0, e1[`, `<e0`, `>=eN`.

    The lower edge is inclusive and the upper edge exclusive, so `edges=(0.5, 1.0)` puts a
    0.5 spread in `[0.5,1[` and a 1.0 spread in `>=1`. Trades without a recorded spread are
    excluded from every band.
    """
    return _banded(trades, lambda trade: trade.spread, list(edges))


def by_duration(trades: Iterable[Trade], edges: Sequence[float]) -> tuple[Bucket, ...]:
    """Holding-time bands, `edges` in seconds and half-open like `by_spread`.

    The duration is `closed_at - opened_at`, the only duration the `Trade` record carries.
    """
    return _banded(trades, _duration_seconds, list(edges))


def by_volatility(
    trades: Iterable[Trade],
    volatility_of: Callable[[Trade], float | None],
    edges: Sequence[float],
) -> tuple[Bucket, ...]:
    """Volatility bands, half-open like `by_spread`.

    `Trade` carries no volatility, so the caller supplies the reading: the dashboard passes
    the ATR stored with the signal (`storage.scalping.volatility_of`), and a trade the
    reading cannot resolve returns `None` and leaves the axis.
    """
    return _banded(trades, volatility_of, list(edges))


def by_size(
    trades: Iterable[Trade],
    size_of: Callable[[Trade], Decimal | None],
    edges: Sequence[Decimal],
) -> tuple[Bucket, ...]:
    """Position-size bands, decimal edges and half-open like `by_spread`.

    `Trade` carries no volume, so the caller supplies the reading
    (`storage.scalping.size_of` reads the position's volume). Decimal edges keep a 0.005
    lot in its own band instead of rounding it into a neighbour.
    """
    return _banded(trades, lambda trade: _as_float(size_of(trade)), [float(edge) for edge in edges])


def cost_summary(
    trades: Iterable[Trade],
    cost_eur_of: Callable[[Trade], Decimal | None] | None = None,
) -> CostSummary:
    """Slippage, spread and euro cost, each with the count of trades it was measured on.

    `Trade.slippage` is a magnitude in price units, never signed (both brokers record
    `abs(executed - requested)`), so the mean and the worst are taken as they are recorded.
    A figure with no sample stays `None`: it is never reported as a zero.
    """
    ordered = list(trades)
    slippages = [trade.slippage for trade in ordered if trade.slippage is not None]
    spreads = [trade.spread for trade in ordered if trade.spread is not None]
    costs: list[Decimal | None] = (
        [cost_eur_of(trade) for trade in ordered]
        if cost_eur_of is not None
        else [None] * len(ordered)
    )
    measured_costs = [cost for cost in costs if cost is not None]

    return CostSummary(
        sample=sum(
            1
            for trade, cost in zip(ordered, costs, strict=True)
            if trade.slippage is not None or trade.spread is not None or cost is not None
        ),
        slippage_sample=len(slippages),
        average_slippage=fmean(slippages) if slippages else None,
        max_slippage=max(slippages) if slippages else None,
        spread_sample=len(spreads),
        average_spread=fmean(spreads) if spreads else None,
        cost_sample=len(measured_costs),
        average_cost_eur=(
            sum(measured_costs, Decimal(0)) / Decimal(len(measured_costs))
            if measured_costs
            else None
        ),
    )


def _bucket(key: str, trades: Sequence[Trade]) -> Bucket:
    chosen = tuple(sorted(trades, key=_sort_key))
    net = sum((trade.pnl_eur for trade in chosen), Decimal(0))
    wins = sum(1 for trade in chosen if trade.pnl_eur > 0)
    return Bucket(
        key=key,
        trades=chosen,
        net_profit=net,
        win_rate=(float(wins) / len(chosen)) if chosen else None,
        expectancy=(net / Decimal(len(chosen))) if chosen else Decimal(0),
    )


def _group[Key](trades: Iterable[Trade], key_of: Callable[[Trade], Key]) -> dict[Key, list[Trade]]:
    grouped: dict[Key, list[Trade]] = defaultdict(list)
    for trade in trades:
        grouped[key_of(trade)].append(trade)
    return dict(grouped)


def _banded(
    trades: Iterable[Trade],
    value_of: Callable[[Trade], float | None],
    edges: Sequence[float],
) -> tuple[Bucket, ...]:
    """Bands in threshold order, each trading bucket holding the trades it caught."""
    _require_increasing(edges)
    grouped: dict[int, list[Trade]] = defaultdict(list)
    for trade in trades:
        value = value_of(trade)
        if value is not None:
            grouped[_band_index(value, edges)].append(trade)
    return tuple(_bucket(_band_key(index, edges), grouped[index]) for index in sorted(grouped))


def _band_index(value: float, edges: Sequence[float]) -> int:
    """Index of the band holding `value`: lower edge inclusive, upper edge exclusive."""
    index = 0
    while index < len(edges) and value >= edges[index]:
        index += 1
    return index


def _band_key(index: int, edges: Sequence[float]) -> str:
    if not edges:
        return "all"
    if index == 0:
        return f"<{edges[0]:g}"
    if index == len(edges):
        return f">={edges[-1]:g}"
    return f"[{edges[index - 1]:g},{edges[index]:g}["


def _require_increasing(edges: Sequence[float]) -> None:
    for lower, upper in pairwise(edges):
        if upper <= lower:
            raise ValueError(f"edges must be strictly increasing, got {list(edges)!r}")


def _duration_seconds(trade: Trade) -> float:
    return (trade.closed_at - trade.opened_at).total_seconds()


def _as_float(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


__all__ = [
    "SESSION_HOURS",
    "Bucket",
    "CostSummary",
    "Session",
    "by_duration",
    "by_hour",
    "by_session",
    "by_size",
    "by_spread",
    "by_volatility",
    "by_weekday",
    "cost_summary",
    "session_of",
]
