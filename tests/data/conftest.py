import threading
from collections import defaultdict
from dataclasses import dataclass, field

import pytest

from tradingagent.core.timeframe import Timeframe
from tradingagent.data.terminal import AccountSnapshot, Credentials, RawBar, RawTick, TerminalError

CREDENTIALS = Credentials(
    login=40123456,
    password="fake",  # pragma: allowlist secret
    server="Deriv-Demo",
)


@dataclass
class FakeTerminal:
    """Same interface as the MT5 adapter, fully controllable, and records which thread calls it."""

    account_snapshot: AccountSnapshot = field(
        default_factory=lambda: AccountSnapshot(
            login=CREDENTIALS.login,
            is_demo=True,
            currency="EUR",
            leverage=30,
            trade_allowed=True,
            server="Deriv-Demo",
        )
    )
    bars: dict[tuple[str, Timeframe], list[RawBar]] = field(default_factory=dict)
    ticks: dict[str, RawTick] = field(default_factory=dict)
    unselectable: set[str] = field(default_factory=set)
    failing_rates: set[str] = field(default_factory=set)
    # First `stale_reads` rate requests answer `stale_bars`, like a terminal not yet synced.
    stale_reads: int = 0
    stale_bars: list[RawBar] = field(default_factory=list)
    max_bars_setting: int = 100_000
    connected: bool = True
    initialize_failures: int = 0
    calls: list[str] = field(default_factory=list)
    threads: set[str] = field(default_factory=set)
    selected: list[str] = field(default_factory=list)
    requested_counts: dict[tuple[str, Timeframe], list[int]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def _record(self, name: str) -> None:
        self.calls.append(name)
        self.threads.add(threading.current_thread().name)

    def initialize(self, credentials: Credentials) -> None:
        self._record("initialize")
        if self.initialize_failures > 0:
            self.initialize_failures -= 1
            raise TerminalError("terminal not reachable")
        self.connected = True

    def shutdown(self) -> None:
        self._record("shutdown")

    def account(self) -> AccountSnapshot:
        self._record("account")
        return self.account_snapshot

    def max_bars(self) -> int:
        self._record("max_bars")
        return self.max_bars_setting

    def select(self, symbol: str) -> bool:
        self._record("select")
        if symbol in self.unselectable:
            return False
        # Idempotent, like `symbol_select` in the terminal: it reports *which* symbols are
        # watched, not how many times somebody asked for them.
        if symbol not in self.selected:
            self.selected.append(symbol)
        return True

    def rates(self, symbol: str, timeframe: Timeframe, count: int) -> list[RawBar]:
        self._record("rates")
        self.requested_counts[(symbol, timeframe)].append(count)
        if symbol in self.failing_rates:
            raise TerminalError(f"no history for {symbol}")
        if self.stale_reads > 0:
            self.stale_reads -= 1
            return list(self.stale_bars)[-count:]
        return list(self.bars.get((symbol, timeframe), []))[-count:]

    def last_tick(self, symbol: str) -> RawTick | None:
        self._record("last_tick")
        return self.ticks.get(symbol)

    def is_connected(self) -> bool:
        self._record("is_connected")
        return self.connected


@pytest.fixture
def terminal() -> FakeTerminal:
    return FakeTerminal()


@pytest.fixture
def credentials() -> Credentials:
    return CREDENTIALS
