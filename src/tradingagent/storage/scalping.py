"""The §32 scalping statistics that need the database.

`analytics/scalping.py` stays pure: it never reads a table, so its bands take the volatility
and the size of a trade as callables. This module is the only place that builds those
callables, and the only place that measures execution costs, straight from the append-only
telemetry table of §20 and §47.

Nothing is invented here either: a trade whose position is unknown, or whose signal stored no
ATR, reads `None` and leaves the band; a hop that was never measured stays `None` instead of
becoming a zero.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import fmean

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.analytics.model import Trade
from tradingagent.core.states import ExecutionEventKind
from tradingagent.storage.models import OrderRow, PositionRow, SignalRow
from tradingagent.storage.telemetry import ExecutionEventStore

# The ATR the strategies store in `signals.indicators` (witness, trend_breakout). The same
# three spellings the AI lab accepts, so a signal is read the same way everywhere.
ATR_KEYS = ("atr", "ATR", "atr14")

# How many of the most recent execution events the slippage mean looks at.
DEFAULT_EVENT_WINDOW = 1000


@dataclass(frozen=True)
class ExecutionCosts:
    """What execution really cost, measured on the telemetry table.

    `sample` is the number of `order_sent -> filled` hops paired; `slippage_sample` is the
    number of fills that actually carried a slippage reading. Each figure is `None` when its
    sample is empty.
    """

    symbol: str | None
    sample: int
    median_latency_ms: float | None
    worst_latency_ms: float | None
    slippage_sample: int
    average_slippage: float | None
    max_slippage: float | None


@dataclass(frozen=True)
class _Opening:
    """What the stored chain knows about one opened position."""

    indicators: dict[str, float]
    volume: Decimal


def volatility_of(engine: Engine) -> Callable[[Trade], float | None]:
    """Read the ATR the signal was generated with, for the `by_volatility` bands.

    The key is `(symbol, position.opened_at)`, the two fields a `Trade` shares with the
    stored chain. A trade formed from a different source, or whose signal stored no ATR,
    reads `None` — the band leaves it out rather than assuming a volatility.
    """
    openings = _openings(engine)

    def read(trade: Trade) -> float | None:
        opening = openings.get((trade.symbol, trade.opened_at))
        if opening is None:
            return None
        return _atr(opening.indicators)

    return read


def size_of(engine: Engine) -> Callable[[Trade], Decimal | None]:
    """Read the position's volume, for the `by_size` bands.

    Same key as `volatility_of`. Once the position is gone from the chain, the trade reads
    `None`: a size is never guessed from the risk or from a configured default.
    """
    openings = _openings(engine)

    def read(trade: Trade) -> Decimal | None:
        opening = openings.get((trade.symbol, trade.opened_at))
        return None if opening is None else opening.volume

    return read


def execution_costs(
    engine: Engine,
    symbol: str | None = None,
    *,
    limit: int = DEFAULT_EVENT_WINDOW,
) -> ExecutionCosts:
    """Median and worst `order_sent -> filled` latency, plus the slip the fills recorded.

    The slippage is the one the runtime stored in the `filled` event detail (a string in
    price units); a fill without it adds no sample. `limit` bounds the telemetry window the
    slippage is read from, newest first.
    """
    telemetry = ExecutionEventStore(engine)
    latency = telemetry.latency(
        ExecutionEventKind.ORDER_SENT, ExecutionEventKind.FILLED, symbol=symbol
    )
    slippages = [
        value
        for event in telemetry.recent(limit=limit, symbol=symbol)
        if event.kind is ExecutionEventKind.FILLED
        and (value := _slippage_of(event.detail)) is not None
    ]
    return ExecutionCosts(
        symbol=symbol,
        sample=latency.sample,
        median_latency_ms=latency.median_ms,
        worst_latency_ms=latency.worst_ms,
        slippage_sample=len(slippages),
        average_slippage=fmean(slippages) if slippages else None,
        max_slippage=max(slippages) if slippages else None,
    )


def _openings(engine: Engine) -> dict[tuple[str, datetime], _Opening]:
    """Every stored position, with the indicators of the signal that produced it."""
    statement = (
        select(
            PositionRow.symbol,
            PositionRow.opened_at,
            PositionRow.volume,
            SignalRow.indicators,
        )
        .join(OrderRow, PositionRow.order_id == OrderRow.id)
        .join(SignalRow, OrderRow.signal_id == SignalRow.id)
        .order_by(PositionRow.opened_at, PositionRow.id)
    )
    with Session(engine) as session:
        rows: Sequence[tuple[str, datetime, Decimal, dict[str, float] | None]] = session.execute(
            statement
        ).all()
    return {
        (symbol, opened_at): _Opening(indicators=dict(indicators or {}), volume=Decimal(volume))
        for symbol, opened_at, volume, indicators in rows
    }


def _atr(indicators: dict[str, float]) -> float | None:
    for key in ATR_KEYS:
        value = indicators.get(key)
        if isinstance(value, int | float):
            return float(value)
    return None


def _slippage_of(detail: dict[str, object]) -> float | None:
    """The slippage recorded with a fill, or `None` when the fill carried none."""
    raw = detail.get("slippage")
    if raw is None:
        return None
    try:
        return float(str(raw))
    except ValueError:
        return None


__all__ = [
    "DEFAULT_EVENT_WINDOW",
    "ExecutionCosts",
    "execution_costs",
    "size_of",
    "volatility_of",
]
