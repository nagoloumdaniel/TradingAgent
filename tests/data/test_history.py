import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.history import HistorySync
from tradingagent.data.market_calendar import learn_calendar
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade

STEP = timedelta(minutes=15)
NOW = datetime(2026, 10, 6, 12, 7, tzinfo=UTC)  # a Tuesday
LAST_CLOSED = datetime(2026, 10, 6, 11, 45, tzinfo=UTC)
ALWAYS_OPEN = learn_calendar(
    "BTCUSD",
    [Candle(Timeframe.M15, NOW - STEP * (i + 1), 1.0, 1.0, 1.0, 1.0) for i in range(8 * 7 * 96)],
    NOW,
)


def candle(open_time: datetime) -> Candle:
    return Candle(Timeframe.M15, open_time, 100.0, 101.0, 99.0, 100.5)


def series(last_open: datetime, count: int) -> list[Candle]:
    return [candle(last_open - STEP * (count - 1 - i)) for i in range(count)]


class FakeSource:
    """The broker: serves the latest closed candles, at most `limit` per request."""

    def __init__(self, history: list[Candle], limit: int = 99_999) -> None:
        self.history = history
        self.limit = limit
        self.requests: list[int] = []

    async def closed_candles(self, symbol: str, timeframe: Timeframe, count: int) -> list[Candle]:
        self.requests.append(count)
        return self.history[-min(count, self.limit) :]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[CandleStore]:
    url = f"sqlite:///{tmp_path / 'history.db'}"
    upgrade(url)
    engine: Engine = create_database_engine(url)
    yield CandleStore(engine)
    engine.dispose()


def sync(source: FakeSource, store: CandleStore, warmup: int = 50) -> int:
    history = HistorySync(source, store, now=lambda: NOW)
    return asyncio.run(history.sync("BTCUSD", Timeframe.M15, warmup)).inserted


def test_first_start_downloads_the_warmup_history(store: CandleStore) -> None:
    source = FakeSource(series(LAST_CLOSED, 500))
    assert sync(source, store, warmup=200) == 200
    assert source.requests == [200]
    assert store.latest("BTCUSD", Timeframe.M15, 500) == series(LAST_CLOSED, 200)


def test_catch_up_after_an_outage_creates_no_duplicate(store: CandleStore) -> None:
    outage_start = LAST_CLOSED - STEP * 30
    store.save("BTCUSD", series(outage_start, 100), NOW)
    source = FakeSource(series(LAST_CLOSED, 500))
    assert sync(source, store) == 30
    assert source.requests[0] >= 31
    assert store.latest("BTCUSD", Timeframe.M15, 1000) == series(LAST_CLOSED, 130)


def test_restart_right_after_a_sync_inserts_nothing(store: CandleStore) -> None:
    source = FakeSource(series(LAST_CLOSED, 500))
    sync(source, store)
    assert sync(source, store) == 0


def test_outage_longer_than_the_broker_serves_leaves_a_recorded_hole(store: CandleStore) -> None:
    store.save("BTCUSD", series(LAST_CLOSED - STEP * 300, 10), NOW)
    source = FakeSource(series(LAST_CLOSED, 1000), limit=100)
    sync(source, store)
    history = HistorySync(source, store, now=lambda: NOW)
    start = LAST_CLOSED - STEP * 309
    holes = history.missing("BTCUSD", Timeframe.M15, ALWAYS_OPEN, start, NOW)
    assert holes[0] == LAST_CLOSED - STEP * 299
    assert holes[-1] == LAST_CLOSED - STEP * 100
    assert len(holes) == 200


def test_a_continuous_day_reports_no_hole(store: CandleStore) -> None:
    source = FakeSource(series(LAST_CLOSED, 500))
    sync(source, store, warmup=200)
    history = HistorySync(source, store, now=lambda: NOW)
    day_ago = NOW - timedelta(hours=24)
    assert history.missing("BTCUSD", Timeframe.M15, ALWAYS_OPEN, day_ago, NOW) == []


def test_the_bar_still_forming_is_never_reported_missing(store: CandleStore) -> None:
    sync(FakeSource(series(LAST_CLOSED, 100)), store)
    history = HistorySync(FakeSource([]), store, now=lambda: NOW)
    assert history.missing("BTCUSD", Timeframe.M15, ALWAYS_OPEN, LAST_CLOSED, NOW) == []
