from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.news.market_state import MarketStateReader, state_from_candles
from tradingagent.news.model import BTCUSD, XAUUSD

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def series(
    count: int, step: float, *, end: datetime = NOW, start_price: float = 100.0
) -> list[Candle]:
    first = end - timedelta(minutes=15 * count)
    candles = []
    price = start_price
    for index in range(count):
        close = price + step
        candles.append(
            Candle(
                Timeframe.M15,
                first + timedelta(minutes=15 * index),
                price,
                max(price, close) + 0.2,
                min(price, close) - 0.2,
                close,
            )
        )
        price = close
    return candles


def test_a_steady_rise_reads_as_an_uptrend() -> None:
    state = state_from_candles(XAUUSD, series(120, 0.5), NOW)
    assert state.known and state.trend == "up"
    assert state.last_price == pytest.approx(160.0)
    assert state.as_of == NOW


def test_a_steady_fall_reads_as_a_downtrend() -> None:
    assert state_from_candles(XAUUSD, series(120, -0.5, start_price=500.0), NOW).trend == "down"


def test_too_few_candles_is_unknown_not_neutral() -> None:
    state = state_from_candles(XAUUSD, series(10, 0.5), NOW)
    assert not state.known and state.last_price is None


def test_stale_candles_are_unknown() -> None:
    old = series(120, 0.5, end=NOW - timedelta(hours=12))
    assert not state_from_candles(XAUUSD, old, NOW).known


def test_a_naive_clock_is_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        state_from_candles(XAUUSD, [], datetime(2026, 10, 30, 12, 0))  # noqa: DTZ001


class FakeCandles:
    def __init__(self, by_symbol: dict[str, list[Candle] | Exception]) -> None:
        self._by_symbol = by_symbol

    def latest(
        self, symbol: str, timeframe: Timeframe, count: int, closed_by: datetime | None = None
    ) -> list[Candle]:
        outcome = self._by_symbol[symbol]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_the_reader_covers_both_markets_and_survives_a_broken_store() -> None:
    reader = MarketStateReader(
        FakeCandles({XAUUSD: series(120, 0.5), BTCUSD: RuntimeError("database is locked")})
    )
    states = reader.read(NOW)
    assert states[XAUUSD].trend == "up"
    assert not states[BTCUSD].known
