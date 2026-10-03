"""Asynchronous access to market data through a blocking terminal (F-001, F-003, RM-017)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TypeVar

from tradingagent.core.market import Candle
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.server_clock import ServerClock, StaleTickError, measure_offset
from tradingagent.data.terminal import (
    AccountSnapshot,
    Credentials,
    RawBar,
    RawTick,
    Terminal,
    TerminalError,
)

log = logging.getLogger(__name__)
T = TypeVar("T")

POLL_LOOKBACK_BARS = 5
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 60.0
HISTORY_SYNC_READS = 4
HISTORY_SYNC_WAIT_SECONDS = 0.5


class HistoryNotSyncedError(TerminalError):
    """The terminal serves bars older than its own live tick: its history is not synced yet."""


class AccountMismatchError(Exception):
    """The terminal is logged into an account that does not match the configuration (RM-017)."""


class ClockMismatchError(Exception):
    """The broker server clock no longer matches the expected offset to UTC (R-16)."""


@dataclass(frozen=True)
class Subscription:
    symbol: str
    timeframe: Timeframe


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MarketDataClient:
    def __init__(
        self,
        terminal: Terminal,
        credentials: Credentials,
        mode: TradingMode,
        *,
        expected_server_offset: timedelta,
        clock_probe_symbol: str,
        now: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._terminal = terminal
        self._credentials = credentials
        self._mode = mode
        self._clock = ServerClock(expected_server_offset)
        self._probe = clock_probe_symbol
        self._now = now
        self._sleep = sleep
        # One worker: MetaTrader5 calls are blocking and must never run concurrently.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mt5")
        self._max_bars = 0
        self._selected: set[str] = set()
        self._last_published: dict[Subscription, datetime] = {}

    async def _call(self, function: Callable[..., T], *args: object) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, partial(function, *args))

    async def connect(self) -> AccountSnapshot:
        await self._call(self._terminal.initialize, self._credentials)
        account = await self._call(self._terminal.account)
        self._check_account(account)
        await self.verify_clock()
        self._max_bars = await self._call(self._terminal.max_bars)
        log.info("connected to %s (%s)", account.server, "demo" if account.is_demo else "real")
        return account

    def _check_account(self, account: AccountSnapshot) -> None:
        if account.login != self._credentials.login:
            raise AccountMismatchError("terminal is logged into another account than configured")
        if self._mode is TradingMode.LIVE and account.is_demo:
            raise AccountMismatchError(
                "LIVE mode requires a real account, the terminal reports a demo"
            )
        if self._mode is not TradingMode.LIVE and not account.is_demo:
            raise AccountMismatchError(
                f"{self._mode} mode requires a demo account, the terminal reports a real one"
            )

    async def verify_clock(self) -> None:
        tick = await self._call(self._terminal.last_tick, self._probe)
        if tick is None:
            raise ClockMismatchError(f"no tick on {self._probe} to verify the server clock")
        try:
            measured = measure_offset(tick.server_epoch, self._now())
        except StaleTickError as error:
            raise ClockMismatchError(f"cannot verify the server clock: {error}") from error
        if measured != self._clock.offset:
            raise ClockMismatchError(
                f"server offset is {measured}, expected {self._clock.offset}: "
                "converting would shift every candle"
            )

    async def select(self, symbols: Iterable[str]) -> set[str]:
        for symbol in sorted(set(symbols)):
            if await self._call(self._terminal.select, symbol):
                self._selected.add(symbol)
            else:
                log.warning("symbol %s could not be selected, skipped", symbol)
        return set(self._selected)

    async def closed_candles(self, symbol: str, timeframe: Timeframe, count: int) -> list[Candle]:
        """Closed candles only, oldest first, in UTC. The forming bar is always dropped."""
        # Requests for max_bars or more fail whole on MT5 (measured in TASK-003).
        requested = min(count, self._max_bars - 1)
        raw_bars = await self._synced_rates(symbol, timeframe, requested)
        now = self._now()
        candles = []
        for raw in raw_bars:
            try:
                candle = Candle(
                    timeframe=timeframe,
                    open_time=self._clock.to_utc(raw.server_epoch),
                    open=raw.open,
                    high=raw.high,
                    low=raw.low,
                    close=raw.close,
                )
            except ValueError as error:
                log.warning("malformed %s %s bar skipped: %s", symbol, timeframe, error)
                continue
            if candle.close_time <= now:
                candles.append(candle)
        return candles

    async def _synced_rates(self, symbol: str, timeframe: Timeframe, count: int) -> list[RawBar]:
        """Bars whose latest one contains the live tick.

        Seen on the real terminal: right after selecting a symbol, it can answer with a full
        but stale cached history. A closed market is fine: its old tick falls in its last bar.
        """
        for attempt in range(HISTORY_SYNC_READS):
            raw_bars = await self._call(self._terminal.rates, symbol, timeframe, count)
            tick = await self._call(self._terminal.last_tick, symbol)
            if not raw_bars or tick is None or not self._lags(raw_bars[-1], tick, timeframe):
                return raw_bars
            if attempt < HISTORY_SYNC_READS - 1:
                await self._sleep(HISTORY_SYNC_WAIT_SECONDS)
        raise HistoryNotSyncedError(f"{symbol} {timeframe} history lags its live tick")

    def _lags(self, latest: RawBar, tick: RawTick, timeframe: Timeframe) -> bool:
        latest_close = self._clock.to_utc(latest.server_epoch) + timedelta(
            seconds=timeframe.seconds
        )
        return self._clock.to_utc(tick.server_epoch) >= latest_close

    async def poll_new(self, subscriptions: Iterable[Subscription]) -> list[Candle]:
        """Candles that closed since the previous poll. The first poll only sets a baseline."""
        published: list[Candle] = []
        for subscription in subscriptions:
            try:
                candles = await self.closed_candles(
                    subscription.symbol, subscription.timeframe, POLL_LOOKBACK_BARS
                )
            except TerminalError as error:
                log.warning(
                    "%s %s not polled: %s", subscription.symbol, subscription.timeframe, error
                )
                continue
            if not candles:
                continue
            last = self._last_published.get(subscription)
            fresh = [c for c in candles if last is None or c.open_time > last]
            self._last_published[subscription] = candles[-1].open_time
            if last is not None:
                published.extend(fresh)
        return published

    async def ensure_connected(self, max_attempts: int | None = None) -> bool:
        """Reconnect with capped exponential backoff, then restore the symbol selection."""
        if await self._call(self._terminal.is_connected):
            return True
        attempt = 0
        while max_attempts is None or attempt < max_attempts:
            if attempt > 0:
                await self._sleep(
                    min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_CAP_SECONDS)
                )
            attempt += 1
            try:
                await self.connect()
            except TerminalError as error:
                log.warning("reconnection attempt %d failed: %s", attempt, error)
                continue
            previous, self._selected = self._selected, set()
            await self.select(previous)
            log.info("reconnected after %d attempt(s)", attempt)
            return True
        return False

    async def close(self) -> None:
        try:
            await self._call(self._terminal.shutdown)
        finally:
            self._executor.shutdown(wait=True)
