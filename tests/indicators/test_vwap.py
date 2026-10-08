"""Le VWAP : ce qu'il vaut, et surtout ce qu'il refuse de valoir.

Ces tests portent sur les deux propriétés qui décident de son usage dans une stratégie :
l'ancre est lue (donc deux ancres donnent deux résultats), et une série sans volume rend
`None` au lieu d'un nombre inventé. Le reste est de l'arithmétique vérifiée à la main.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from tradingagent.indicators.vwap import typical_price, vwap

#: 2026-09-25 00:00 UTC — le début d'une session avec l'ancre par défaut.
SESSION_START = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)


def minutes(count: int, start: datetime = SESSION_START) -> list[datetime]:
    return [start + timedelta(minutes=index) for index in range(count)]


def flat(count: int, price: float) -> list[float]:
    return [price] * count


def test_typical_price_is_the_mean_of_high_low_and_close() -> None:
    assert typical_price(high=12.0, low=9.0, close=10.5) == pytest.approx(10.5)


def test_a_single_bar_has_its_own_typical_price_as_vwap() -> None:
    """Le premier VWAP d'une session est le prix de la barre : rien avant lui ne pèse."""
    values = vwap(
        minutes(1),
        highs=[12.0],
        lows=[9.0],
        closes=[10.0],
        volumes=[5.0],
    )

    assert values == [pytest.approx((12.0 + 9.0 + 10.0) / 3)]


def test_the_weighted_mean_is_computed_by_hand() -> None:
    """Deux barres : (100 x 2 + 200 x 6) / 8 = 175, vérifié à la main.

    Barre 1 : high 101, low 99, close 100 -> typical 100, volume 2
    Barre 2 : high 202, low 198, close 200 -> typical 200, volume 6
    """
    values = vwap(
        minutes(2),
        highs=[101.0, 202.0],
        lows=[99.0, 198.0],
        closes=[100.0, 200.0],
        volumes=[2.0, 6.0],
    )

    assert values[0] == pytest.approx(100.0)
    assert values[1] == pytest.approx(175.0)


def test_a_zero_volume_bar_does_not_move_the_average_nor_reset_it() -> None:
    """Une minute sans échange ne pèse rien : la valeur précédente reste vraie."""
    values = vwap(
        minutes(3),
        highs=[101.0, 999.0, 101.0],
        lows=[99.0, 1.0, 99.0],
        closes=[100.0, 500.0, 100.0],
        volumes=[4.0, 0.0, 4.0],
    )

    assert values[0] == pytest.approx(100.0)
    assert values[1] == pytest.approx(100.0)  # le volume nul ne déplace pas la moyenne
    assert values[2] == pytest.approx(100.0)


def test_a_leading_zero_volume_bar_has_no_defined_vwap() -> None:
    """Aucun volume cumulé : il n'y a rien à pondérer, donc rien à afficher."""
    values = vwap(
        minutes(2),
        highs=[101.0, 101.0],
        lows=[99.0, 99.0],
        closes=[100.0, 100.0],
        volumes=[0.0, 4.0],
    )

    assert values[0] is None
    assert values[1] == pytest.approx(100.0)


def test_a_series_without_volume_has_no_vwap_at_all() -> None:
    """`volume is None` veut dire « non enregistré » : pondérer serait inventer.

    C'est le cas des huit jeux gelés avant le 2026-10-08, et d'un `Candle` relu de la base,
    qui n'a pas de colonne volume.
    """
    values = vwap(
        minutes(4),
        highs=flat(4, 101.0),
        lows=flat(4, 99.0),
        closes=flat(4, 100.0),
        volumes=[None, None, None, None],
    )

    assert values == [None, None, None, None]


def test_one_missing_volume_poisons_only_its_own_session() -> None:
    """Un trou dans une session ne doit pas contaminer la session suivante."""
    start = SESSION_START
    times = minutes(4, start=start)
    # +24 h place les deux dernières barres dans la session du lendemain.
    times[2] = times[2] + timedelta(days=1)
    times[3] = times[3] + timedelta(days=1)

    values = vwap(
        times,
        highs=flat(4, 101.0),
        lows=flat(4, 99.0),
        closes=flat(4, 100.0),
        volumes=[2.0, None, 3.0, 3.0],
    )

    assert values[0] == pytest.approx(100.0)
    assert values[1] is None  # la barre sans volume rend la session entière indéfinie
    assert values[2] == pytest.approx(100.0)  # nouvelle session : nouveau cumul
    assert values[3] == pytest.approx(100.0)


def test_the_anchor_resets_the_accumulation() -> None:
    """Deux ancres différentes sur la même série donnent deux VWAP différents.

    C'est le test qui prouve que l'ancre est réellement lue. Il faut des volumes **inégaux** :
    à volume uniforme, remettre le cumul à zéro redonne exactement la même moyenne, et le test
    ne prouverait rien.

    Quatre barres de 11:00 à 11:03, prix typiques 100, 101, 102, 103, volumes 1, 1, 1, 5.
    Ancre 00:00 : une seule session, donc la dernière valeur couvre les quatre barres.
    Ancre 11:02 : la session repart à cette barre, les deux premières sortent du cumul.
    """
    count = 4
    times = minutes(count, start=datetime(2026, 9, 25, 11, 0, tzinfo=UTC))
    closes = [100.0, 101.0, 102.0, 103.0]
    volumes = [1.0, 1.0, 1.0, 5.0]

    one_session = vwap(
        times,
        highs=[c + 1 for c in closes],
        lows=[c - 1 for c in closes],
        closes=closes,
        volumes=volumes,
    )
    # Ancre à 11:02 : la session repart à cette barre, donc les deux premières sortent du cumul.
    reset_midway = vwap(
        times,
        highs=[c + 1 for c in closes],
        lows=[c - 1 for c in closes],
        closes=closes,
        volumes=volumes,
        session_anchor_minutes=11 * 60 + 2,
    )

    # Une seule session : (100x1 + 101x1 + 102x1 + 103x5) / 8 = 818 / 8 = 102,25
    assert one_session[3] == pytest.approx(818.0 / 8.0)
    # Remise à zéro à 11:02 : la barre 11:02 ouvre la session, (102x1 + 103x5) / 6 = 617 / 6
    assert reset_midway[2] == pytest.approx(102.0)
    assert reset_midway[3] == pytest.approx(617.0 / 6.0)
    assert one_session[3] != pytest.approx(reset_midway[3])


def test_the_timezone_offset_is_honoured() -> None:
    """Un horodatage décalé n'est pas la même session : l'UTC est la règle, pas un détail."""
    aware = minutes(3)
    utc_series = vwap(
        aware, highs=flat(3, 101.0), lows=flat(3, 99.0), closes=flat(3, 100.0), volumes=flat(3, 1.0)
    )
    shifted = [moment.astimezone(timezone(timedelta(hours=2))) for moment in aware]
    shifted_series = vwap(
        shifted,
        highs=flat(3, 101.0),
        lows=flat(3, 99.0),
        closes=flat(3, 100.0),
        volumes=flat(3, 1.0),
    )

    # Les mêmes instants, écrits autrement, doivent donner le même VWAP.
    assert shifted_series == pytest.approx(utc_series)


def test_inputs_are_validated() -> None:
    with pytest.raises(ValueError, match="same length"):
        vwap(minutes(3), [1.0], [1.0], [1.0], [1.0])
    with pytest.raises(ValueError, match="timezone-aware"):
        # Naïf volontairement : c'est exactement ce que la fonction doit refuser, et le
        # deviner ferait glisser toutes les frontières de session en silence.
        vwap(
            [datetime(2026, 9, 25, 0, 0)],  # noqa: DTZ001
            [101.0],
            [99.0],
            [100.0],
            [1.0],
        )
    with pytest.raises(ValueError, match="not finite"):
        vwap(minutes(1), [float("nan")], [99.0], [100.0], [1.0])
    with pytest.raises(ValueError, match="negative"):
        vwap(minutes(1), [101.0], [99.0], [100.0], [-1.0])


def test_the_vwap_is_deterministic() -> None:
    """Fonction pure : deux appels identiques donnent le même résultat."""
    arguments = (minutes(5), flat(5, 101.0), flat(5, 99.0), flat(5, 100.0), flat(5, 2.0))

    assert vwap(*arguments) == vwap(*arguments)
