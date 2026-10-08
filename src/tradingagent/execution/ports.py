"""Ports of the execution layer (TASK-070, TASK-081, TASK-083).

The shared value objects live in `tradingagent.risk.model`, where the composition root
already reads them: `OrderRequest`, `OrderResult`, `BrokerPosition`, `ClosedPosition`,
`CloseResult`, `AccountState`, `InstrumentSpec`, `MarketQuote`, `OpenPosition`. This module
only adds what is internal to the executors: the errors they raise, the log they write to
and the reconciliation report.

`Broker` is structural: the composition root and the loop type against it, never against a
concrete executor, so PAPER never needs the MetaTrader5 package.
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.risk.model import (
    AccountState,
    BrokerPosition,
    ClosedPosition,
    CloseResult,
    InstrumentSpec,
    MarketQuote,
    OpenPosition,
    OrderRequest,
    OrderResult,
)

# The operator is told, synchronously: an alert must never be able to block an order path.
Alert = Callable[[str], None]
AsyncAlert = Callable[[str], Awaitable[None]]


class BrokerError(Exception):
    """The broker could not be reached or answered something unusable."""


class BrokerUnavailableError(BrokerError):
    """No quote, no specification or no terminal: the order must be refused, not guessed."""


class OrderRefusedError(BrokerError):
    """The order was refused before being sent (halt, wrong mode, missing price)."""


@dataclass(frozen=True)
class OrderSnapshot:
    """What the local journal knows about one idempotency key."""

    order_id: int
    idempotency_key: str
    signal_id: int
    mode: TradingMode
    ticket: int | None
    retcode: int | None
    state: str


@dataclass(frozen=True)
class LocalPosition:
    """A position the executor believes is open, as stored in `positions`."""

    position_id: int
    ticket: int
    order_id: int
    signal_id: int
    symbol: str
    direction: Direction
    volume: Decimal
    open_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    mode: TradingMode


class TradeLog(Protocol):
    """Persistence the executors depend on: orders, fills, positions and closed trades.

    Implemented by `execution.tracking.PositionTracker`. The executor is the single writer
    of `orders`, `positions`, `executions` and `trades`, so PAPER and DEMO write the same
    rows and are told apart by `mode` alone (F-015). Every method is synchronous and runs
    in the caller's thread.
    """

    def find_order(self, idempotency_key: str) -> OrderSnapshot | None: ...

    def record_request(self, request: OrderRequest, requested_price: Decimal, at: datetime) -> int:
        """Store the intent before anything leaves the process. Raises if it cannot."""
        ...

    def record_result(self, order_id: int, result: OrderResult, at: datetime) -> None: ...

    def record_fill(
        self,
        order_id: int,
        request: OrderRequest,
        result: OrderResult,
        *,
        deal_ticket: int,
        at: datetime,
    ) -> int | None:
        """Store the fill and open the local position. Returns the position id, if any."""
        ...

    def record_closures(self, closed: Iterable[ClosedPosition], at: datetime) -> tuple[int, ...]:
        """Store each closed trade and move its signal to CLOSED. Idempotent per ticket."""
        ...

    def local_positions(self, mode: TradingMode) -> tuple[LocalPosition, ...]: ...

    def position_for_ticket(self, ticket: int) -> LocalPosition | None: ...

    def realized_pnl(self, mode: TradingMode) -> Decimal: ...


@dataclass(frozen=True)
class Divergence:
    """One difference between the local state and the broker's. Never auto-corrected."""

    kind: str
    ticket: int | None
    detail: str


@dataclass(frozen=True)
class Reconciliation:
    """Report of TASK-083. `balanced` is true only when nothing differs at all."""

    checked_at: datetime
    mode: TradingMode
    local_count: int
    broker_count: int
    divergences: tuple[Divergence, ...]

    @property
    def balanced(self) -> bool:
        return not self.divergences

    def summary(self) -> str:
        if self.balanced:
            return f"coherent: {self.local_count} local, {self.broker_count} broker"
        lines = [
            f"{len(self.divergences)} divergence(s): {self.local_count} local, "
            f"{self.broker_count} broker"
        ]
        lines.extend(f"- {d.kind} {d.ticket}: {d.detail}" for d in self.divergences)
        return "\n".join(lines)


class Broker(Protocol):
    """What the agent loop may ask of an executor. Every method is asynchronous: the
    concrete implementations confine their blocking terminal calls to a worker thread.

    `open_positions` answers the risk engine (one entry per market, F-006); the richer
    `positions` answers tracking and reconciliation (TASK-082, TASK-083).
    """

    async def account(self) -> AccountState: ...

    async def instrument(self, symbol: str) -> InstrumentSpec: ...

    async def quote(
        self, symbol: str, direction: Direction, stop_loss: Decimal | None
    ) -> MarketQuote: ...

    async def open_positions(self) -> tuple[OpenPosition, ...]: ...

    async def positions(self) -> tuple[BrokerPosition, ...]: ...

    async def place(self, request: OrderRequest) -> OrderResult: ...

    async def close(self, ticket: int, reason: str) -> CloseResult: ...

    async def on_candle(self, symbol: str, candle: Candle) -> tuple[ClosedPosition, ...]: ...

    async def on_tick(
        self, symbol: str, bid: Decimal, ask: Decimal
    ) -> tuple[ClosedPosition, ...]: ...

    async def collect_closures(self) -> tuple[ClosedPosition, ...]: ...

    async def reconcile(self) -> tuple[str, ...]: ...
