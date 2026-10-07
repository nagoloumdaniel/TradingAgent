"""Narrow interfaces to a trading terminal. Only data.mt5_terminal talks to MetaTrader5.

`Terminal` is the read-only market-data surface. `TradingTerminal` adds the order,
position and deal operations the executors need (TASK-081); it is deliberately separate
so paper trading and market data can be wired without ever exposing an order entry point.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from tradingagent.core.market import Direction
from tradingagent.core.timeframe import Timeframe

# Orders placed by this agent. The adapter only ever reports positions and deals carrying
# it, so a manual trade on the same account is never mistaken for one of ours.
MAGIC = 3031


class TerminalError(Exception):
    """The terminal could not be reached or refused a request."""


@dataclass(frozen=True)
class Credentials:
    login: int
    password: str = field(repr=False)
    server: str
    terminal_path: Path | None = None


@dataclass(frozen=True)
class AccountSnapshot:
    login: int
    is_demo: bool
    currency: str
    leverage: int
    trade_allowed: bool
    server: str


@dataclass(frozen=True)
class RawBar:
    """A bar as the terminal reports it: its epoch is the broker server's wall clock."""

    server_epoch: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class RawTick:
    server_epoch: int
    bid: float
    ask: float


class Terminal(Protocol):
    """Blocking and not re-entrant: call it from one dedicated thread only."""

    def initialize(self, credentials: Credentials) -> None: ...

    def shutdown(self) -> None: ...

    def account(self) -> AccountSnapshot: ...

    def max_bars(self) -> int: ...

    def select(self, symbol: str) -> bool: ...

    def rates(self, symbol: str, timeframe: Timeframe, count: int) -> list[RawBar]:
        """The most recent `count` bars, oldest first, the forming bar included."""
        ...

    def last_tick(self, symbol: str) -> RawTick | None: ...

    def is_connected(self) -> bool: ...


@dataclass(frozen=True)
class AccountFunds:
    """Live equity and margin, read separately from the identity of the account."""

    balance: float
    equity: float
    free_margin: float


@dataclass(frozen=True)
class SymbolSpec:
    """Contract specification as the broker reports it (TASK-003, section 12.1)."""

    symbol: str
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    point: float
    stops_level: int  # minimum stop distance, in points
    digits: int


@dataclass(frozen=True)
class TradeRequest:
    """One market order, opening or closing.

    When `position_ticket` is set the request closes that position and `direction` is the
    side of the closing deal (the opposite of the position's side).
    """

    symbol: str
    direction: Direction
    volume: float
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    comment: str = ""
    deviation: int = 50
    position_ticket: int | None = None
    magic: int = MAGIC


@dataclass(frozen=True)
class TradeCheck:
    """`order_check` answer. Informative only: the jurisdiction refusal proves it lies."""

    retcode: int
    margin: float
    comment: str


@dataclass(frozen=True)
class TradeResult:
    """`order_send` answer. `retcode` alone decides whether the order was accepted."""

    retcode: int
    order_ticket: int
    deal_ticket: int
    price: float
    volume: float
    comment: str


@dataclass(frozen=True)
class PositionInfo:
    ticket: int
    symbol: str
    direction: Direction
    volume: float
    price_open: float
    stop_loss: float
    take_profit: float
    profit: float
    magic: int
    comment: str


@dataclass(frozen=True)
class DealInfo:
    """One deal of the account's history, opening or closing a position."""

    ticket: int
    position_ticket: int
    order_ticket: int
    symbol: str
    is_entry: bool  # True when the deal opened the position
    is_buy: bool
    volume: float
    price: float
    profit: float  # profit + swap + commission, in the account currency
    comment: str
    server_epoch: int


class TradingTerminal(Terminal, Protocol):
    """The Terminal interface plus order entry. Implemented by Mt5Terminal only."""

    def funds(self) -> AccountFunds: ...

    def symbol_spec(self, symbol: str) -> SymbolSpec | None: ...

    def order_check(self, request: TradeRequest) -> TradeCheck: ...

    def order_send(self, request: TradeRequest) -> TradeResult | None:
        """None means the answer was lost: the caller must reconcile, never resend."""
        ...

    def positions(self, symbol: str | None = None) -> tuple[PositionInfo, ...]:
        """Open positions carrying MAGIC, oldest first."""
        ...

    def deals_since(self, server_epoch: int) -> tuple[DealInfo, ...]:
        """Deals closed after that server epoch, oldest first."""
        ...

    def calc_profit(
        self,
        direction: Direction,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None: ...

    def calc_margin(
        self, direction: Direction, symbol: str, volume: float, price: float
    ) -> float | None: ...

    def last_error(self) -> tuple[int, str]: ...
