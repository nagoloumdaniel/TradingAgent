"""The only module allowed to import MetaTrader5 (tests/test_architecture.py)."""

import time

import MetaTrader5 as mt5

from tradingagent.core.timeframe import Timeframe
from tradingagent.data.terminal import AccountSnapshot, Credentials, RawBar, RawTick, TerminalError

_TIMEFRAMES = {
    Timeframe.M1: mt5.TIMEFRAME_M1,
    Timeframe.M5: mt5.TIMEFRAME_M5,
    Timeframe.M15: mt5.TIMEFRAME_M15,
    Timeframe.M30: mt5.TIMEFRAME_M30,
    Timeframe.H1: mt5.TIMEFRAME_H1,
    Timeframe.H4: mt5.TIMEFRAME_H4,
    Timeframe.D1: mt5.TIMEFRAME_D1,
}
_HISTORY_SETTLE_SECONDS = 0.5
_HISTORY_READS = 4


class Mt5Terminal:
    def initialize(self, credentials: Credentials) -> None:
        kwargs = {
            "login": credentials.login,
            "password": credentials.password,
            "server": credentials.server,
            "timeout": 60_000,
        }
        path = credentials.terminal_path
        ok = mt5.initialize(str(path), **kwargs) if path else mt5.initialize(**kwargs)
        if not ok:
            raise TerminalError(f"initialize failed: {mt5.last_error()}")

    def shutdown(self) -> None:
        mt5.shutdown()

    def account(self) -> AccountSnapshot:
        info = mt5.account_info()
        if info is None:
            raise TerminalError(f"account_info failed: {mt5.last_error()}")
        return AccountSnapshot(
            login=info.login,
            # A contest account counts as not demo, so it is refused outside LIVE mode.
            is_demo=info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
            currency=info.currency,
            leverage=info.leverage,
            trade_allowed=info.trade_allowed,
            server=info.server,
        )

    def max_bars(self) -> int:
        info = mt5.terminal_info()
        if info is None:
            raise TerminalError(f"terminal_info failed: {mt5.last_error()}")
        return int(info.maxbars)

    def select(self, symbol: str) -> bool:
        return bool(mt5.symbol_select(symbol, True))

    def rates(self, symbol: str, timeframe: Timeframe, count: int) -> list[RawBar]:
        # History downloads lazily: a short answer is re-read until it stops growing.
        data = None
        previous = -1
        for _ in range(_HISTORY_READS):
            data = mt5.copy_rates_from_pos(symbol, _TIMEFRAMES[timeframe], 0, count)
            size = 0 if data is None else len(data)
            if size >= count or size == previous:
                break
            previous = size
            time.sleep(_HISTORY_SETTLE_SECONDS)
        if data is None:
            raise TerminalError(f"rates {symbol} {timeframe} failed: {mt5.last_error()}")
        return [
            RawBar(
                int(r["time"]),
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
            )
            for r in data
        ]

    def last_tick(self, symbol: str) -> RawTick | None:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None or not tick.time:
            return None
        return RawTick(int(tick.time), float(tick.bid), float(tick.ask))

    def is_connected(self) -> bool:
        info = mt5.terminal_info()
        return bool(info is not None and info.connected)
