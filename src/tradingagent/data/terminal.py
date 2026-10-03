"""Narrow interface to a trading terminal. Only data.mt5_terminal talks to MetaTrader5."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from tradingagent.core.timeframe import Timeframe


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
