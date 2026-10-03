"""Candle persistence (F-004, TASK-013).

Duplicates are refused by the database's unique constraint, not by a prior read, so two
writers racing on the same bar or a catch-up overlapping stored history stay harmless. The
first stored version of a bar wins: a closed bar never changes.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Engine, Insert, Select, select
from sqlalchemy.dialects import postgresql, sqlite

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.models import CandleRow

UNIQUE_KEY = ("symbol", "timeframe", "open_time")


class CandleStore:
    def __init__(self, engine: Engine, source: str = "mt5") -> None:
        self._engine = engine
        self._source = source

    def save(self, symbol: str, candles: Sequence[Candle], ingested_at: datetime) -> int:
        """Insert the candles not stored yet and return how many were new."""
        if not candles:
            return 0
        rows = [
            {
                "symbol": symbol,
                "timeframe": candle.timeframe,
                "open_time": candle.open_time,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "source": self._source,
                "ingested_at": ingested_at,
            }
            for candle in candles
        ]
        statement = self._insert_ignoring_duplicates().returning(CandleRow.id)
        with self._engine.begin() as connection:
            return len(connection.execute(statement, rows).all())

    def _insert_ignoring_duplicates(self) -> Insert:
        dialect = self._engine.dialect.name
        if dialect == "postgresql":
            return postgresql.insert(CandleRow).on_conflict_do_nothing(index_elements=UNIQUE_KEY)
        if dialect == "sqlite":
            return sqlite.insert(CandleRow).on_conflict_do_nothing(index_elements=UNIQUE_KEY)
        raise NotImplementedError(f"no duplicate-safe insert for {dialect}")

    def latest(self, symbol: str, timeframe: Timeframe, count: int) -> list[Candle]:
        """The `count` most recent candles, oldest first."""
        query = _series(symbol, timeframe).order_by(CandleRow.open_time.desc()).limit(count)
        return list(reversed(self._candles(query)))

    def between(
        self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[Candle]:
        """Candles opening in [start, end), oldest first."""
        query = (
            _series(symbol, timeframe)
            .where(CandleRow.open_time >= start, CandleRow.open_time < end)
            .order_by(CandleRow.open_time)
        )
        return self._candles(query)

    def last_open_time(self, symbol: str, timeframe: Timeframe) -> datetime | None:
        latest = self.latest(symbol, timeframe, 1)
        return latest[0].open_time if latest else None

    def _candles(self, query: "_CandleSelect") -> list[Candle]:
        with self._engine.connect() as connection:
            rows = connection.execute(query).all()
        return [Candle(*row) for row in rows]


_CandleSelect = Select[Timeframe, datetime, float, float, float, float]


def _series(symbol: str, timeframe: Timeframe) -> _CandleSelect:
    return select(
        CandleRow.timeframe,
        CandleRow.open_time,
        CandleRow.open,
        CandleRow.high,
        CandleRow.low,
        CandleRow.close,
    ).where(CandleRow.symbol == symbol, CandleRow.timeframe == timeframe)
