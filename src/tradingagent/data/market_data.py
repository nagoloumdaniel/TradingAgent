"""Asynchronous access to market data through a blocking terminal (F-001, F-003, RM-017)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TypeVar

from tradingagent.core.account import AccountModeMismatchError, verify_account_mode
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
# The probe symbol may need a moment before the terminal serves its first tick: a terminal
# that has just restarted downloads its symbols lazily.
CLOCK_PROBE_ATTEMPTS = 3
CLOCK_PROBE_WAIT_SECONDS = 0.5


class HistoryNotSyncedError(TerminalError):
    """The terminal serves bars older than its own live tick: its history is not synced yet."""


AccountMismatchError = AccountModeMismatchError  # RM-017, one rule shared with risk


class ClockMismatchError(Exception):
    """The broker server clock no longer matches the expected offset to UTC (R-16)."""


class ClockUnverifiableError(Exception):
    """The server clock could not be measured: the probe served no usable tick.

    Deliberately **not** a `ClockMismatchError`, because the two say opposite things. A
    mismatch is a measurement: the server answered, and its offset is not the expected one.
    This is the absence of a measurement: there was nothing to measure. The terminal may
    have restarted and lost the symbol it watches, the symbol may still be downloading, or
    the market may simply be quiet.

    Conflating them is what wedged the agent on 2026-10-07. Between 21:51:54 and 23:59:39
    UTC, 383 consecutive cycles were dropped as if the clock had moved — every one of them
    recording a CRITICAL `clock_mismatch` whose reason was "no tick on BTCUSD" — and the
    recovery path could never succeed, because it verified the clock *before* it selected
    the symbol, so the tick it was waiting for could never arrive. The agent stayed alive
    doing nothing for two hours and eight minutes.
    """


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
        # Read before the clock, and that order is not cosmetic: a clock that cannot be
        # measured must leave the client usable, and `max_bars` bounds every later read. A
        # client whose connection succeeded but whose clock did not is exactly the state a
        # reconnecting agent has to survive.
        self._max_bars = await self._call(self._terminal.max_bars)
        await self._watch_probe()
        await self.verify_clock()
        log.info("connected to %s (%s)", account.server, "demo" if account.is_demo else "real")
        return account

    def _check_account(self, account: AccountSnapshot) -> None:
        verify_account_mode(account.login, account.is_demo, self._credentials.login, self._mode)

    async def _watch_probe(self) -> None:
        """Make the clock probe watchable *before* its tick is read.

        A terminal that has just restarted serves no tick for a symbol it is not watching,
        and `symbol_info_tick` answers None. Reading first and selecting never is the trap
        of 2026-10-07: the check could not pass, so the recovery path that ran only after a
        successful check could never run, and the agent waited two hours for a tick that
        nothing was going to produce.

        The probe is part of what this client watches, never beside it: the composition
        root passes a configured market as `clock_probe_symbol` (`app.py` passes BTCUSD,
        which `config/agent.yaml` also trades), so this adds no symbol nobody asked for.
        """
        if await self._call(self._terminal.select, self._probe):
            self._selected.add(self._probe)
        else:
            log.warning("clock probe %s could not be selected", self._probe)

    async def verify_clock(self) -> None:
        tick = await self._read_probe_tick()
        try:
            measured = measure_offset(tick.server_epoch, self._now())
        except StaleTickError as error:
            raise ClockUnverifiableError(f"cannot measure the server clock: {error}") from error
        if measured != self._clock.offset:
            raise ClockMismatchError(
                f"server offset is {measured}, expected {self._clock.offset}: "
                "converting would shift every candle"
            )

    async def _read_probe_tick(self) -> RawTick:
        """The probe's last tick, waiting for the terminal to serve one.

        None from the terminal means "nothing to read", not "the clock moved": it is
        retried, with a selection attempt in between, and only then declared unverifiable.
        """
        for attempt in range(CLOCK_PROBE_ATTEMPTS):
            tick = await self._call(self._terminal.last_tick, self._probe)
            if tick is not None:
                return tick
            if attempt < CLOCK_PROBE_ATTEMPTS - 1:
                await self._watch_probe()
                await self._sleep(CLOCK_PROBE_WAIT_SECONDS)
        raise ClockUnverifiableError(
            f"no tick on {self._probe} to verify the server clock: the terminal serves none"
        )

    async def last_tick_at(self, symbol: str) -> datetime | None:
        """UTC time of the last tick seen on `symbol`, for the freshness lock (RM-001).

        None means the terminal has no tick for it: callers must treat that as "unknown",
        never as "fresh".
        """
        tick = await self._call(self._terminal.last_tick, symbol)
        return None if tick is None else self._clock.to_utc(tick.server_epoch)

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
                    volume=raw.volume,
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
        """Reconnect with capped exponential backoff, then restore the symbol selection.

        A reconnect that comes back with a clock it could not measure is still a reconnect:
        the terminal answers, the account is the right one, and the data path works. Only
        the probe had nothing to say. Reporting that as a failed reconnection would keep
        the selection un-restored — and the selection is precisely what makes the next
        measurement possible. So it is restored, the client is declared usable, and the
        loop reports the unverifiable clock through its own rate-limited path.
        """
        if await self._call(self._terminal.is_connected):
            return True
        # Captured before the reconnect: `connect` watches the probe and adds it, and the
        # restored set must be the one the caller asked for, not the one this call grew.
        wanted = set(self._selected)
        attempt = 0
        while max_attempts is None or attempt < max_attempts:
            if attempt > 0:
                await self._sleep(
                    min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_CAP_SECONDS)
                )
            attempt += 1
            try:
                await self.connect()
            except ClockUnverifiableError as error:
                log.warning("reconnected, but the server clock is unverifiable: %s", error)
                self._selected = set()
                await self.select(wanted)
                return True
            except TerminalError as error:
                log.warning("reconnection attempt %d failed: %s", attempt, error)
                continue
            self._selected = set()
            await self.select(wanted)
            log.info("reconnected after %d attempt(s)", attempt)
            return True
        return False

    async def close(self) -> None:
        try:
            await self._call(self._terminal.shutdown)
        finally:
            self._executor.shutdown(wait=True)
