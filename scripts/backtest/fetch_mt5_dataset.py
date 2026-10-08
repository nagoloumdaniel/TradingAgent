"""TASK-060 — freeze a small real MT5 demo dataset with the read-only market data path.

    uv run python scripts/backtest/fetch_mt5_dataset.py --symbol XAUUSD --bars 12000
    uv run python scripts/backtest/fetch_mt5_dataset.py --symbol XAUUSD \
        --timeframe M5 --output docs/research/datasets/M5

Reads closed candles of the requested timeframe through
`tradingagent.data.market_data.MarketDataClient` (the same adapter production uses),
verifies the broker clock, then writes an immutable
`<output>/<symbol>-<timeframe>-<id>.jsonl` plus its manifest. No order is ever sent.

`--timeframe` defaults to M15, so the historical invocation is unchanged. Each timeframe
carries its own dataset id: two series of the same symbol and day but different timeframes
are two datasets, and an id that named only the market and the day would say they are one —
the collision `DatasetStore.load_all` already refuses once the files share a directory.
One directory still holds one timeframe per symbol; archive or split by timeframe otherwise.
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


async def fetch(
    client: MarketDataClient, symbol: str, timeframe: Timeframe, bars: int
) -> CandleDataset:
    await client.connect()
    try:
        await client.select([symbol, CLOCK_PROBE])
        candles = await client.closed_candles(symbol, timeframe, bars)
    finally:
        await client.close()
    if len(candles) < 100:
        raise SystemExit(f"only {len(candles)} candle(s) returned for {symbol} {timeframe}")
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return CandleDataset(
        # The symbol belongs in the id: two markets fetched the same day are two datasets,
        # and an id made of the date alone says they are one. The timeframe belongs in it for
        # the same reason: the M15 and the M1 series of one market are not one series either.
        dataset_id=f"mt5-{symbol}-{timeframe.value}-{stamp}",
        symbol=symbol,
        timeframe=timeframe,
        source=f"deriv-mt5:{datetime.now(UTC).isoformat(timespec='seconds')}",
        candles=tuple(candles),
    )


def requested_timeframe(value: str) -> Timeframe:
    try:
        return Timeframe(value.upper())
    except ValueError:
        known = ", ".join(item.value for item in Timeframe)
        raise argparse.ArgumentTypeError(f"unknown timeframe {value!r}; known: {known}") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--bars", type=int, default=4000)
    parser.add_argument(
        "--timeframe",
        type=requested_timeframe,
        default=Timeframe.M15,
        help="candle unit to freeze; M15 unless asked otherwise",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    dataset = asyncio.run(fetch(connected_client(), args.symbol, args.timeframe, args.bars))
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
