import asyncio
import threading
from collections.abc import Coroutine
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_data import (
    AccountMismatchError,
    ClockMismatchError,
    HistoryNotSyncedError,
    MarketDataClient,
    Subscription,
)
from tradingagent.data.terminal import Credentials, RawBar, RawTick, TerminalError

NOW = datetime(2026, 10, 4, 12, 7, 30, tzinfo=UTC)
PROBE = "BTCUSD"


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def server_epoch(utc: datetime, offset: timedelta) -> int:
    return int((utc + offset).timestamp())


def bars(open_times_utc: list[datetime], offset: timedelta = timedelta(0)) -> list[RawBar]:
    return [
        RawBar(server_epoch(at, offset), 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i)
        for i, at in enumerate(open_times_utc)
    ]


def m15_series(last_open: datetime, count: int) -> list[datetime]:
    return [last_open - timedelta(minutes=15 * (count - 1 - i)) for i in range(count)]


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def client(
    terminal: Any,
    credentials: Credentials,
    mode: TradingMode = TradingMode.SIGNAL,
    offset: timedelta = timedelta(0),
    clock: Clock | None = None,
    delays: list[float] | None = None,
) -> MarketDataClient:
    async def fake_sleep(seconds: float) -> None:
        if delays is not None:
            delays.append(seconds)

    terminal.ticks[PROBE] = RawTick(server_epoch(NOW, offset) - 2, 84000.0, 84010.0)
    return MarketDataClient(
        terminal,
        credentials,
        mode,
        expected_server_offset=offset,
        clock_probe_symbol=PROBE,
        now=clock or Clock(NOW),
        sleep=fake_sleep,
    )


def test_connect_on_a_demo_account(terminal: Any, credentials: Credentials) -> None:
    async def scenario() -> None:
        market = client(terminal, credentials)
        account = await market.connect()
        assert account.is_demo
        await market.close()

    run(scenario())


@pytest.mark.parametrize(
    "mode", [TradingMode.OBSERVATION, TradingMode.SIGNAL, TradingMode.PAPER, TradingMode.DEMO]
)
def test_real_account_outside_live_mode_is_fatal(
    terminal: Any, credentials: Credentials, mode: TradingMode
) -> None:
    terminal.account_snapshot = replace(terminal.account_snapshot, is_demo=False)
    with pytest.raises(AccountMismatchError, match="real"):
        run(client(terminal, credentials, mode).connect())


def test_demo_account_in_live_mode_is_fatal(terminal: Any, credentials: Credentials) -> None:
    with pytest.raises(AccountMismatchError, match="demo"):
        run(client(terminal, credentials, TradingMode.LIVE).connect())


def test_other_account_number_is_fatal(terminal: Any, credentials: Credentials) -> None:
    terminal.account_snapshot = replace(terminal.account_snapshot, login=99999999)
    with pytest.raises(AccountMismatchError, match="account"):
        run(client(terminal, credentials).connect())


def test_unreachable_terminal_fails_the_connection(terminal: Any, credentials: Credentials) -> None:
    terminal.initialize_failures = 1
    with pytest.raises(TerminalError):
        run(client(terminal, credentials).connect())


def test_unexpected_server_offset_refuses_to_start(terminal: Any, credentials: Credentials) -> None:
    market = client(terminal, credentials, offset=timedelta(0))
    terminal.ticks[PROBE] = RawTick(server_epoch(NOW, timedelta(hours=3)), 1.0, 1.0)
    with pytest.raises(ClockMismatchError, match="offset"):
        run(market.connect())


def test_stale_probe_tick_refuses_to_start(terminal: Any, credentials: Credentials) -> None:
    market = client(terminal, credentials)
    terminal.ticks[PROBE] = RawTick(server_epoch(NOW - timedelta(hours=1), timedelta(0)), 1.0, 1.0)
    with pytest.raises(ClockMismatchError):
        run(market.connect())


def test_offset_change_while_running_is_detected(terminal: Any, credentials: Credentials) -> None:
    async def scenario() -> None:
        market = client(terminal, credentials)
        await market.connect()
        terminal.ticks[PROBE] = RawTick(server_epoch(NOW, timedelta(hours=1)), 1.0, 1.0)
        with pytest.raises(ClockMismatchError):
            await market.verify_clock()
        await market.close()

    run(scenario())


def test_only_closed_candles_come_out_in_utc(terminal: Any, credentials: Credentials) -> None:
    offset = timedelta(hours=3)
    # 12:00 bar is still forming at 12:07:30 and must be dropped.
    terminal.bars[("XAUUSD", Timeframe.M15)] = bars(
        m15_series(NOW.replace(minute=0, second=0), 5), offset
    )

    async def scenario() -> None:
        market = client(terminal, credentials, offset=offset)
        await market.connect()
        candles = await market.closed_candles("XAUUSD", Timeframe.M15, 10)
        assert [c.open_time for c in candles] == m15_series(
            datetime(2026, 10, 4, 11, 45, tzinfo=UTC), 4
        )
        assert all(c.close_time <= NOW and c.open_time.tzinfo is UTC for c in candles)
        await market.close()

    run(scenario())


def test_request_never_reaches_the_max_bars_setting(
    terminal: Any, credentials: Credentials
) -> None:
    terminal.max_bars_setting = 1_000

    async def scenario() -> None:
        market = client(terminal, credentials)
        await market.connect()
        await market.closed_candles("XAUUSD", Timeframe.M15, 5_000)
        await market.close()

    run(scenario())
    assert terminal.requested_counts[("XAUUSD", Timeframe.M15)] == [999]


def test_only_whitelisted_symbols_are_selected_and_a_failure_is_isolated(
    terminal: Any, credentials: Credentials
) -> None:
    terminal.unselectable = {"XAUUSD"}

    async def scenario() -> set[str]:
        market = client(terminal, credentials)
        await market.connect()
        selected = await market.select({"XAUUSD", "BTCUSD"})
        await market.close()
        return selected

    assert run(scenario()) == {"BTCUSD"}
    assert terminal.selected == ["BTCUSD"]


def test_malformed_bar_is_skipped_not_fatal(terminal: Any, credentials: Credentials) -> None:
    series = bars(m15_series(datetime(2026, 10, 4, 11, 45, tzinfo=UTC), 3))
    series[1] = replace(series[1], high=1.0)  # high below low
    terminal.bars[("XAUUSD", Timeframe.M15)] = series

    async def scenario() -> int:
        market = client(terminal, credentials)
        await market.connect()
        candles = await market.closed_candles("XAUUSD", Timeframe.M15, 10)
        await market.close()
        return len(candles)

    assert run(scenario()) == 2


def test_polling_publishes_each_new_closed_candle_once(
    terminal: Any, credentials: Credentials
) -> None:
    key = ("XAUUSD", Timeframe.M15)
    clock = Clock(NOW)
    subscription = Subscription("XAUUSD", Timeframe.M15)
    terminal.bars[key] = bars(m15_series(datetime(2026, 10, 4, 12, 0, tzinfo=UTC), 6))

    async def scenario() -> list[list[datetime]]:
        market = client(terminal, credentials, clock=clock)
        await market.connect()
        published = [[c.open_time for c in await market.poll_new([subscription])]]
        published.append([c.open_time for c in await market.poll_new([subscription])])
        clock.now = NOW + timedelta(minutes=15)  # the 12:00 bar has closed
        terminal.bars[key] = bars(m15_series(datetime(2026, 10, 4, 12, 15, tzinfo=UTC), 7))
        published.append([c.open_time for c in await market.poll_new([subscription])])
        published.append([c.open_time for c in await market.poll_new([subscription])])
        clock.now = NOW + timedelta(minutes=45)  # two more bars closed while away
        terminal.bars[key] = bars(m15_series(datetime(2026, 10, 4, 12, 45, tzinfo=UTC), 9))
        published.append([c.open_time for c in await market.poll_new([subscription])])
        await market.close()
        return published

    first, repeat, after_close, repeat_again, catch_up = run(scenario())
    assert first == []  # first poll sets the baseline
    assert repeat == []
    assert after_close == [datetime(2026, 10, 4, 12, 0, tzinfo=UTC)]
    assert repeat_again == []
    assert catch_up == [
        datetime(2026, 10, 4, 12, 15, tzinfo=UTC),
        datetime(2026, 10, 4, 12, 30, tzinfo=UTC),
    ]


def test_a_failing_symbol_does_not_stop_the_others_while_polling(
    terminal: Any, credentials: Credentials
) -> None:
    clock = Clock(NOW)
    gold, bitcoin = Subscription("XAUUSD", Timeframe.M15), Subscription("BTCUSD", Timeframe.M15)
    terminal.failing_rates = {"XAUUSD"}
    terminal.bars[("BTCUSD", Timeframe.M15)] = bars(
        m15_series(datetime(2026, 10, 4, 12, 0, tzinfo=UTC), 6)
    )

    async def scenario() -> list[str]:
        market = client(terminal, credentials, clock=clock)
        await market.connect()
        await market.poll_new([gold, bitcoin])
        clock.now = NOW + timedelta(minutes=15)
        terminal.bars[("BTCUSD", Timeframe.M15)] = bars(
            m15_series(datetime(2026, 10, 4, 12, 15, tzinfo=UTC), 7)
        )
        published = await market.poll_new([gold, bitcoin])
        await market.close()
        return [c.open_time.isoformat() for c in published]

    assert run(scenario()) == ["2026-10-04T12:00:00+00:00"]


def test_history_behind_the_live_tick_is_refused(terminal: Any, credentials: Credentials) -> None:
    # Found on the real terminal: right after selection it can serve stale cached bars.
    delays: list[float] = []
    terminal.bars[(PROBE, Timeframe.M15)] = bars(
        m15_series(datetime(2026, 10, 3, 18, 0, tzinfo=UTC), 5)
    )

    async def scenario() -> None:
        market = client(terminal, credentials, delays=delays)
        await market.connect()
        with pytest.raises(HistoryNotSyncedError):
            await market.closed_candles(PROBE, Timeframe.M15, 5)
        await market.close()

    run(scenario())
    assert delays  # it retried before giving up


def test_history_that_catches_up_after_a_retry_is_accepted(
    terminal: Any, credentials: Credentials
) -> None:
    terminal.stale_reads = 1
    terminal.stale_bars = bars(m15_series(datetime(2026, 10, 3, 18, 0, tzinfo=UTC), 5))
    terminal.bars[(PROBE, Timeframe.M15)] = bars(
        m15_series(datetime(2026, 10, 4, 12, 0, tzinfo=UTC), 5)
    )

    async def scenario() -> datetime:
        market = client(terminal, credentials, delays=[])
        await market.connect()
        candles = await market.closed_candles(PROBE, Timeframe.M15, 5)
        await market.close()
        return candles[-1].open_time

    assert run(scenario()) == datetime(2026, 10, 4, 11, 45, tzinfo=UTC)


def test_closed_market_with_an_old_tick_is_not_mistaken_for_stale(
    terminal: Any, credentials: Credentials
) -> None:
    friday_close = datetime(2026, 10, 2, 20, 30, tzinfo=UTC)
    terminal.bars[("XAUUSD", Timeframe.M15)] = bars(m15_series(friday_close, 5))
    terminal.ticks["XAUUSD"] = RawTick(
        server_epoch(friday_close + timedelta(minutes=14), timedelta(0)), 1.0, 1.0
    )

    async def scenario() -> int:
        market = client(terminal, credentials)
        await market.connect()
        candles = await market.closed_candles("XAUUSD", Timeframe.M15, 5)
        await market.close()
        return len(candles)

    assert run(scenario()) == 5


def test_every_terminal_call_runs_on_one_dedicated_thread(
    terminal: Any, credentials: Credentials
) -> None:
    terminal.bars[("XAUUSD", Timeframe.M15)] = bars(
        m15_series(datetime(2026, 10, 4, 11, 45, tzinfo=UTC), 3)
    )

    async def scenario() -> None:
        market = client(terminal, credentials)
        await market.connect()
        await market.select({"XAUUSD"})
        await asyncio.gather(*(market.closed_candles("XAUUSD", Timeframe.M15, 3) for _ in range(5)))
        await market.close()

    run(scenario())
    assert len(terminal.threads) == 1
    assert threading.main_thread().name not in terminal.threads


def test_reconnection_backs_off_then_restores_the_selection(
    terminal: Any, credentials: Credentials
) -> None:
    delays: list[float] = []

    async def scenario() -> bool:
        market = client(terminal, credentials, delays=delays)
        await market.connect()
        await market.select({"BTCUSD"})
        terminal.connected = False
        terminal.initialize_failures = 3
        terminal.selected.clear()
        restored = await market.ensure_connected()
        await market.close()
        return restored

    assert run(scenario()) is True
    assert delays == [1.0, 2.0, 4.0]
    assert terminal.selected == ["BTCUSD"]


def test_backoff_is_capped(terminal: Any, credentials: Credentials) -> None:
    delays: list[float] = []

    async def scenario() -> bool:
        market = client(terminal, credentials, delays=delays)
        await market.connect()
        terminal.connected = False
        terminal.initialize_failures = 100
        restored = await market.ensure_connected(max_attempts=9)
        await market.close()
        return restored

    assert run(scenario()) is False
    # Sleeps happen between attempts only: no pointless wait after the last failure.
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


def test_healthy_terminal_needs_no_reconnection(terminal: Any, credentials: Credentials) -> None:
    async def scenario() -> bool:
        market = client(terminal, credentials)
        await market.connect()
        restored = await market.ensure_connected()
        await market.close()
        return restored

    assert run(scenario()) is True
    assert terminal.calls.count("initialize") == 1
