import math
from datetime import UTC, datetime, timedelta, timezone

import pytest

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe

OPEN = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def candle(**overrides: object) -> Candle:
    fields: dict[str, object] = {
        "timeframe": Timeframe.M15,
        "open_time": OPEN,
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
    }
    fields.update(overrides)
    return Candle(**fields)  # type: ignore[arg-type]


def test_close_time_is_open_time_plus_timeframe() -> None:
    assert candle().close_time == OPEN + timedelta(minutes=15)


def test_zero_offset_timezone_is_accepted_as_utc() -> None:
    assert candle(open_time=OPEN.replace(tzinfo=timezone(timedelta(0)))).open == 10.0


@pytest.mark.parametrize(
    "open_time",
    [OPEN.replace(tzinfo=None), OPEN.replace(tzinfo=timezone(timedelta(hours=2)))],
)
def test_non_utc_open_time_is_rejected(open_time: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        candle(open_time=open_time)


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_non_finite_price_is_rejected(field: str, bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        candle(**{field: bad})


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": 8.0},
        {"open": 13.0},
        {"close": 8.5},
        {"low": 11.5},
    ],
)
def test_incoherent_range_is_rejected(overrides: dict[str, float]) -> None:
    with pytest.raises(ValueError, match="range"):
        candle(**overrides)


def test_candle_is_immutable() -> None:
    with pytest.raises(AttributeError):
        candle().close = 1.0  # type: ignore[misc]
