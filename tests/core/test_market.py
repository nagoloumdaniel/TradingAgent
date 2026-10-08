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


# -- volume : le tick volume du courtier, absent des series gelees avant le 2026-10-08 ----


def test_a_candle_without_volume_is_valid() -> None:
    """Les jeux gelés avant l'ajout n'ont pas de volume : `None` doit rester légitime.

    Les rejeter obligerait à re-geler tout l'historique avant de pouvoir lire une seule
    bougie, et ferait échouer la reconstruction depuis la base, qui ne stocke pas ce champ.
    """
    assert candle().volume is None
    assert candle(volume=12.5).volume == 12.5


def test_volume_is_keyword_only_so_positional_callers_keep_working() -> None:
    """`storage.candles` reconstruit par `Candle(*row)` : six positionnels, rien de plus.

    Un champ positionnel inséré en fin de signature casserait cet appel en silence, en
    glissant un volume dans un prix.
    """
    with pytest.raises(TypeError):
        Candle(Timeframe.M15, OPEN, 10.0, 12.0, 9.0, 11.0, 5.0)  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_volume_is_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="volume"):
        candle(volume=bad)


def test_negative_volume_is_rejected() -> None:
    with pytest.raises(ValueError, match="volume"):
        candle(volume=-1.0)


def test_a_zero_volume_is_accepted() -> None:
    """Une bougie sans échange existe, et vaut zéro : ce n'est pas une donnée manquante."""
    assert candle(volume=0.0).volume == 0.0
