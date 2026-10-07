"""TASK-060 — freeze a small real MT5 demo dataset with the read-only market data path.

    uv run python scripts/backtest/fetch_mt5_dataset.py --symbol frxXAUUSD --bars 4000

Reads closed M15 candles through `tradingagent.data.market_data.MarketDataClient`
(the same adapter production uses), verifies the broker clock, then writes an immutable
`docs/research/datasets/<symbol>-M15-<id>.jsonl` plus its manifest. No order is ever sent.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from tradingagent.backtest.datasets import CandleDataset, DatasetStore
from tradingagent.config.settings import load_settings
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import learn_calendar
from tradingagent.data.market_data import MarketDataClient
from tradingagent.data.mt5_terminal import Mt5Terminal
from tradingagent.data.server_clock import measure_offset
from tradingagent.data.terminal import Credentials

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "docs" / "research" / "datasets"
CLOCK_PROBE = "BTCUSD"  # 24/7 market: a fresh tick is always available on the MT5 side
DEFAULT_SYMBOL = "XAUUSD"  # MT5 name; the Deriv API calls the same market frxXAUUSD


def credentials() -> Credentials:
    settings = load_settings(ROOT / ".env")
    return Credentials(
        login=settings.mt5_login,
        password=settings.mt5_password.get_secret_value(),
        server=settings.mt5_server,
        terminal_path=settings.mt5_terminal_path,
    )


def connected_client() -> MarketDataClient:
    creds = credentials()
    terminal = Mt5Terminal()
    terminal.initialize(creds)
    terminal.select(CLOCK_PROBE)
    tick = terminal.last_tick(CLOCK_PROBE)
    if tick is None:
        terminal.shutdown()
        raise SystemExit(f"no tick on {CLOCK_PROBE} to verify the broker clock")
    offset = measure_offset(tick.server_epoch, datetime.now(UTC))
    print(f"broker clock offset to UTC: {offset}")
    return MarketDataClient(
        terminal,
        creds,
        TradingMode.OBSERVATION,
        expected_server_offset=offset,
        clock_probe_symbol=CLOCK_PROBE,
    )


async def fetch(client: MarketDataClient, symbol: str, bars: int) -> CandleDataset:
    await client.connect()
    try:
        await client.select([symbol, CLOCK_PROBE])
        candles = await client.closed_candles(symbol, Timeframe.M15, bars)
    finally:
        await client.close()
    if len(candles) < 100:
        raise SystemExit(f"only {len(candles)} candle(s) returned for {symbol}")
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return CandleDataset(
        # The symbol belongs in the id: two markets fetched the same day are two datasets,
        # and an id made of the date alone says they are one.
        dataset_id=f"mt5-{symbol}-{stamp}",
        symbol=symbol,
        timeframe=Timeframe.M15,
        source=f"deriv-mt5:{datetime.now(UTC).isoformat(timespec='seconds')}",
        candles=tuple(candles),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--bars", type=int, default=4000)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    dataset = asyncio.run(fetch(connected_client(), args.symbol, args.bars))
    path = DatasetStore(args.output).save(dataset)
    print(f"written {path}")
    print(f"bars={dataset.bars} start={dataset.start.isoformat()} end={dataset.end.isoformat()}")
    print(f"fingerprint={dataset.fingerprint}")
    try:
        calendar = learn_calendar(args.symbol, dataset.candles, dataset.end, weeks=4)
        manifest = dataset.manifest(calendar)
        manifest_path = path.with_suffix(".manifest.json")
        manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2) + "\n", encoding="utf-8")
        print(f"calendar learned; {manifest.gap_count} hole(s); manifest {manifest_path}")
    except ValueError as error:
        print(f"calendar not learned, gap census skipped: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
