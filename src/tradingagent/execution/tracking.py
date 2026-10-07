"""Position tracking and signal lifecycle (TASK-082, F-017).

The executors report facts (an order was accepted, a position appeared, a position ended);
somebody has to turn those facts into the ledger. `OrderJournal` already does the writing and
the RM-018 transitions; `PositionTracker` is that same ledger plus the follow-up helpers the
loop needs: ask a broker what ended since the previous call (`follow`, `follow_candle`) and
summarise the result for the operator (M-02).

Keeping the two in one inheritance line means the composition root can pass either to a
broker: both satisfy `ports.TradeLog`.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Engine

from tradingagent.core.market import Candle
from tradingagent.execution.journal import ZERO, OrderJournal
from tradingagent.risk.model import ClosedPosition

log = logging.getLogger(__name__)

# Every way a position can end, stored in `trades.exit_reason` (TASK-082, F-017).
STOP_LOSS = "stop_loss"
TAKE_PROFIT = "take_profit"
RULE = "rule"
MANUAL = "manual"
STOP_MISSING = "stop_missing"
BACKSTOP = "backstop"
EMERGENCY = "emergency"
BROKER = "broker"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PositionTracker(OrderJournal):
    """The ledger plus the closure-following helpers of the agent loop."""

    def __init__(
        self,
        engine: Engine,
        journal: OrderJournal | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(engine)
        self._now = now if now is not None else _utc_now
        self._shared = journal

    @property
    def journal(self) -> OrderJournal:
        """The journal the executor writes to; the tracker itself unless one was given."""
        return self._shared if self._shared is not None else self

    @property
    def clock(self) -> Callable[[], datetime]:
        return self._now

    # -- follow: what ended since the previous call -------------------------------------

    async def follow(
        self, broker: object, symbol: str, bid: Decimal, ask: Decimal
    ) -> tuple[ClosedPosition, ...]:
        """Tick-driven closure detection; the broker has already persisted the closures."""
        return await self._call_on(broker, "on_tick", symbol, bid, ask)

    async def follow_candle(
        self, broker: object, symbol: str, candle: Candle
    ) -> tuple[ClosedPosition, ...]:
        """Candle-driven closure detection: stops and targets are honoured through the bar."""
        return await self._call_on(broker, "on_candle", symbol, candle)

    @staticmethod
    async def _call_on(
        broker: object, method: str, symbol: str, *args: object
    ) -> tuple[ClosedPosition, ...]:
        handler = getattr(broker, method)
        closed: tuple[ClosedPosition, ...] = await handler(symbol, *args)
        return closed

    # -- aggregation --------------------------------------------------------------------

    def summarize(self, closed: tuple[ClosedPosition, ...]) -> str:
        """One item per closure, for the log and the operator's update (M-02)."""
        if not closed:
            return "no position closed"
        return "; ".join(
            f"{item.ticket} {item.symbol} {item.exit_reason} {item.pnl_eur:+.2f} EUR"
            for item in closed
        )

    def net_of(self, closed: tuple[ClosedPosition, ...]) -> Decimal:
        return sum((item.pnl_eur for item in closed), ZERO)


__all__ = [
    "BACKSTOP",
    "BROKER",
    "EMERGENCY",
    "MANUAL",
    "RULE",
    "STOP_LOSS",
    "STOP_MISSING",
    "TAKE_PROFIT",
    "PositionTracker",
]
