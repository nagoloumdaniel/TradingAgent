import pytest

from tradingagent.core.timeframe import Timeframe


def test_every_timeframe_has_a_duration() -> None:
    assert all(timeframe.seconds > 0 for timeframe in Timeframe)


@pytest.mark.parametrize(
    ("timeframe", "seconds"),
    [(Timeframe.M1, 60), (Timeframe.M15, 900), (Timeframe.H1, 3600), (Timeframe.D1, 86400)],
)
def test_duration_in_seconds(timeframe: Timeframe, seconds: int) -> None:
    assert timeframe.seconds == seconds


def test_durations_are_strictly_increasing() -> None:
    durations = [timeframe.seconds for timeframe in Timeframe]
    assert durations == sorted(set(durations))
