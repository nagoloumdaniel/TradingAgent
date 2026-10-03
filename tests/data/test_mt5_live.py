"""Smoke test against the real terminal. Off by default: RUN_MT5_LIVE=1 uv run pytest -m mt5_live"""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.mt5_live,
    pytest.mark.skipif(os.environ.get("RUN_MT5_LIVE") != "1", reason="set RUN_MT5_LIVE=1"),
]


def test_reads_closed_utc_candles_from_the_demo_account() -> None:
    pytest.importorskip("MetaTrader5")
    from dotenv import dotenv_values

    from tradingagent.config.redaction import install_secret_redaction
    from tradingagent.core.mode import TradingMode
    from tradingagent.core.timeframe import Timeframe
    from tradingagent.data.market_data import MarketDataClient
    from tradingagent.data.mt5_terminal import Mt5Terminal
    from tradingagent.data.terminal import Credentials

    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    install_secret_redaction([env["MT5_PASSWORD"] or ""])
    path = (env.get("MT5_TERMINAL_PATH") or "").strip()
    credentials = Credentials(
        login=int(env["MT5_LOGIN"] or 0),
        password=env["MT5_PASSWORD"] or "",
        server=(env["MT5_SERVER"] or "").strip(),
        terminal_path=Path(path) if path else None,
    )

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
            gold = await market.closed_candles("XAUUSD", Timeframe.M15, 10)
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
