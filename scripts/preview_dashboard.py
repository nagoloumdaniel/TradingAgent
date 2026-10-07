"""Serve the dashboard against a disposable, seeded database — for design review only.

It never touches `.env` or the production database: it builds a temporary SQLite file,
applies the migrations, fills it with the same fixtures the web tests use, and serves the
read-only app on a local port.

    uv run python scripts/preview_dashboard.py [--port 8799] [--host 127.0.0.1] [--demo]

`--demo` adds a realistic history — a wandering equity curve and a few hundred candles —
so the design can be judged on the data it will actually carry. The test fixtures alone
hold a handful of points, which is enough to exercise the code but not to look at a chart.
"""

import argparse
import math
import random
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402
from sqlalchemy import Engine, delete, insert  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from tests.web.seed import seed  # noqa: E402

from tradingagent.core.timeframe import Timeframe  # noqa: E402
from tradingagent.storage.engine import create_database_engine  # noqa: E402
from tradingagent.storage.migrate import upgrade  # noqa: E402
from tradingagent.storage.models import AccountSnapshotRow, CandleRow  # noqa: E402
from tradingagent.web.app import create_app  # noqa: E402

DEMO_SNAPSHOTS = 220
DEMO_CANDLES_PER_MARKET = 130
DEMO_SEED = 20261007
# The fixtures' signals live on 2026-10-06 and 2026-10-07; the candles must surround them,
# with enough history on both sides for the replay window and the watermark.
CANDLE_START = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)


def _demo(engine: Engine) -> None:
    """A plausible history, so the watermark, the curve and the replay charts have something
    to say.

    The candles have to sit *around the seeded signals*, not at some arbitrary date: the
    replay page draws the candles surrounding `signals.generated_at`, and a demo whose
    candles are a month early shows an empty chart and looks broken.
    """
    # Reproducible demo data in a throwaway database; nothing here is cryptographic.
    rng = random.Random(DEMO_SEED)  # noqa: S311
    start = datetime(2026, 9, 1, tzinfo=UTC)
    with Session(engine) as session:
        session.execute(delete(AccountSnapshotRow))
        equity_rows = []
        for index in range(DEMO_SNAPSHOTS):
            # A slow drift with a weekly wave and noise: what a scalping curve looks like.
            drift = index * 0.42
            wave = math.sin(index / 11.0) * 26.0
            noise = rng.gauss(0, 7.5)
            equity = 1000.0 + drift + wave + noise
            equity_rows.append(
                {
                    "equity": Decimal(f"{equity:.2f}"),
                    "balance": Decimal(f"{equity:.2f}"),
                    "at": start + timedelta(hours=6 * index),
                }
            )
        session.execute(insert(AccountSnapshotRow), equity_rows)
        session.execute(delete(CandleRow))

        # H1, covering the week the seeded signals live in, for both markets.
        candle_rows = []
        for symbol, base in (("XAUUSD", 2650.0), ("BTCUSD", 62000.0)):
            price = base
            for index in range(DEMO_CANDLES_PER_MARKET):
                moment = CANDLE_START + timedelta(hours=index)
                open_price = price
                move = rng.gauss(0.0, base * 0.0006)
                close_price = open_price + move
                candle_rows.append(
                    {
                        "symbol": symbol,
                        "timeframe": Timeframe.H1,
                        "open_time": moment,
                        "open": round(open_price, 3),
                        "high": round(max(open_price, close_price) + abs(move) * 0.4, 3),
                        "low": round(min(open_price, close_price) - abs(move) * 0.4, 3),
                        "close": round(close_price, 3),
                        "source": "preview",
                        "ingested_at": moment + timedelta(minutes=1),
                    }
                )
                price = close_price
        session.execute(insert(CandleRow), candle_rows)
        session.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="preview_dashboard", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="ajoute une courbe d'équité et des bougies réalistes, pour juger le design",
    )
    args = parser.parse_args(argv)

    database = Path(tempfile.mkdtemp(prefix="tradingagent-preview-")) / "preview.db"
    url = f"sqlite:///{database}"
    upgrade(url)
    engine = create_database_engine(url)
    seed(engine)
    if args.demo:
        _demo(engine)
    print(f"preview database: {database}")
    print(f"dashboard: http://{args.host}:{args.port}/")
    uvicorn.run(create_app(engine), host=args.host, port=args.port, log_level="warning")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
