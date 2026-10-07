"""Ports the agent loop depends on, so it never imports `execution` or `MetaTrader5`.

`risk` stays the only package allowed to reach `execution` besides the composition root
(`tradingagent.app`). The loop therefore talks to an executor through these structural
protocols: the concrete brokers defined in `execution` satisfy them without being imported.
"""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from tradingagent.ai.layer import ReviewContext, ReviewOutcome
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import AiFilter
from tradingagent.core.states import Severity
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_data import Subscription

if TYPE_CHECKING:  # the lab imports this module, so the reference stays one-way
    from tradingagent.ai.daily import LabRun
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


class MarketPort(Protocol):
    async def select(self, symbols: Iterable[str]) -> set[str]: ...

    async def poll_new(self, subscriptions: Iterable[Subscription]) -> list[Candle]: ...

    async def closed_candles(
        self, symbol: str, timeframe: Timeframe, count: int
    ) -> list[Candle]: ...

    async def last_tick_at(self, symbol: str) -> datetime | None: ...

    async def verify_clock(self) -> None: ...

    async def ensure_connected(self, max_attempts: int | None = None) -> bool: ...


class BrokerPort(Protocol):
    async def account(self) -> AccountState: ...

    async def instrument(self, symbol: str) -> InstrumentSpec: ...

    async def quote(
        self, symbol: str, direction: Direction, stop_loss: Decimal | None = None
    ) -> MarketQuote: ...

    async def open_positions(self) -> tuple[OpenPosition, ...]: ...

    async def positions(self) -> tuple[BrokerPosition, ...]: ...

    async def place(self, request: OrderRequest) -> OrderResult: ...

    async def close(self, ticket: int, reason: str) -> CloseResult: ...

    async def on_candle(self, symbol: str, candle: Candle) -> tuple[ClosedPosition, ...]: ...

    async def reconcile(self) -> tuple[str, ...]: ...


class NotifierPort(Protocol):
    async def send(self, text: str, *, parse_mode: str | None = None) -> bool: ...


class DailyLabPort(Protocol):
    """The once-a-day AI Lab pass. It analyses and proposes; it can never trade."""

    async def run_once(self, now: datetime) -> "LabRun": ...


class AiReviewerPort(Protocol):
    """The only thing the loop needs from the AI layer: a verdict that can block, never
    create or loosen (C-002)."""

    async def review(
        self,
        context: ReviewContext,
        at: datetime,
        signal_id: int | None = None,
        ai_filter: AiFilter | None = None,
    ) -> ReviewOutcome: ...


class SystemEventPort(Protocol):
    def record(
        self,
        kind: str,
        severity: Severity,
        detail: dict[str, object],
        at: datetime,
    ) -> None: ...
