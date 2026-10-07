"""Smoke tests against the real terminal. Off by default: RUN_MT5_LIVE=1 uv run pytest -m mt5_live

Everything here refuses to run unless the terminal reports a DEMO account. The order test
opens the minimum lot with a native stop and closes it immediately.
"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.mt5_live,
    pytest.mark.skipif(os.environ.get("RUN_MT5_LIVE") != "1", reason="set RUN_MT5_LIVE=1"),
]


def credentials_from_env():
    from dotenv import dotenv_values

    from tradingagent.config.redaction import install_secret_redaction
    from tradingagent.data.terminal import Credentials

    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    install_secret_redaction([env["MT5_PASSWORD"] or ""])
    path = (env.get("MT5_TERMINAL_PATH") or "").strip()
    return Credentials(
        login=int(env["MT5_LOGIN"] or 0),
        password=env["MT5_PASSWORD"] or "",  # pragma: allowlist secret
        server=(env["MT5_SERVER"] or "").strip(),
        terminal_path=Path(path) if path else None,
    )


def test_reads_closed_utc_candles_from_the_demo_account() -> None:
    pytest.importorskip("MetaTrader5")
    from tradingagent.core.mode import TradingMode
    from tradingagent.core.timeframe import Timeframe
    from tradingagent.data.market_data import MarketDataClient
    from tradingagent.data.mt5_terminal import Mt5Terminal

    credentials = credentials_from_env()

    async def scenario() -> None:
        market = MarketDataClient(
            Mt5Terminal(),
            credentials,
            TradingMode.SIGNAL,
            expected_server_offset=timedelta(0),
            clock_probe_symbol="BTCUSD",
        )
        try:
            account = await market.connect()
            assert account.is_demo
            assert await market.select({"XAUUSD", "BTCUSD"}) == {"XAUUSD", "BTCUSD"}
            now = datetime.now(UTC)
            # `closed_candles` drops the forming bar: asking for N returns N-1 closed candles
            # at most, so 10 closed candles are obtained by asking for 11 (TASK-081 fix).
            gold = await market.closed_candles("XAUUSD", Timeframe.M15, 11)
            assert len(gold) == 10
            assert [c.open_time for c in gold] == sorted(c.open_time for c in gold)
            assert all(c.close_time <= now and c.open_time.tzinfo is UTC for c in gold)
            bitcoin = await market.closed_candles("BTCUSD", Timeframe.M1, 3)
            # Bitcoin trades around the clock: its last closed minute must be recent.
            assert now - bitcoin[-1].close_time < timedelta(minutes=2)
            assert await market.ensure_connected() is True
        finally:
            await market.close()

    asyncio.run(scenario())


def test_places_a_demo_order_with_a_native_stop_read_back_then_closes_it() -> None:
    """TASK-081 end to end, on the demo account only: open, confirm the stop, close."""
    pytest.importorskip("MetaTrader5")
    from tradingagent.core.market import Direction
    from tradingagent.core.mode import TradingMode
    from tradingagent.data.mt5_terminal import Mt5Terminal
    from tradingagent.execution.mt5_broker import MT5Broker
    from tradingagent.risk.model import OrderRequest

    credentials = credentials_from_env()

    async def scenario() -> None:
        from tests.execution.conftest import FakeLog

        terminal = Mt5Terminal()
        terminal.initialize(credentials)
        try:
            snapshot = terminal.account()
            if not snapshot.is_demo:
                pytest.skip("the terminal is not logged into the demo account")
            if not snapshot.trade_allowed:
                pytest.skip("trading is disabled on this account")
            assert terminal.is_connected()
            assert terminal.select("XAUUSD") is True
            broker = MT5Broker(terminal, FakeLog(), login=credentials.login, mode=TradingMode.DEMO)
            await broker.initialize()
            spec = await broker.instrument("XAUUSD")
            tick = terminal.last_tick("XAUUSD")
            assert tick is not None
            distance = max(spec.point * Decimal(str(spec.stops_level)) * 5, Decimal("5"))
            ask = Decimal(str(tick.ask))
            request = OrderRequest(
                signal_id=1,
                idempotency_key="live-smoke-081",
                symbol="XAUUSD",
                direction=Direction.BUY,
                volume=spec.volume_min,
                stop_loss=ask - distance,
                take_profit=ask + distance * 2,
                mode=TradingMode.DEMO,
                comment="live",
            )

            result = await broker.place(request)

            assert result.accepted, result.message
            assert result.ticket is not None
            assert result.stop_present is True, "the stop must be present on the position"
            closed = await broker.close(result.ticket, "live smoke test cleanup")
            assert closed.closed is True
        finally:
            terminal.shutdown()

    asyncio.run(scenario())
