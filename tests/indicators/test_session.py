"""Les séances de marché : chaque instant tombe dans une seule, et la plus étroite gagne.

Le point délicat n'est pas de classer midi. C'est que l'overlap Londres/New York contient
Londres et New York à la fois, donc l'ordre des fenêtres décide du résultat — et un ordre
implicite produirait une étiquette qui change selon la façon dont on a écrit la liste.
"""

from datetime import UTC, datetime, time, timedelta, timezone

import pytest

from tradingagent.indicators.session import (
    DEFAULT_WINDOWS,
    Session,
    Window,
    session_at,
    sessions_for,
)


def at(hour: int, minute: int = 0, day: int = 25) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (0, Session.TOKYO),
        (3, Session.TOKYO),
        (6, Session.TOKYO),
        (7, Session.LONDON),
        (9, Session.LONDON),
        (12, Session.LONDON),
        (13, Session.OVERLAP),
        (15, Session.OVERLAP),
        (16, Session.NEW_YORK),
        (20, Session.NEW_YORK),
        (21, Session.NEW_YORK),
        (22, Session.OFF),
        (23, Session.OFF),
    ],
)
def test_every_hour_of_the_day_has_one_session(hour: int, expected: Session) -> None:
    assert session_at(at(hour)) is expected


def test_the_overlap_wins_over_the_sessions_that_contain_it() -> None:
    """13:00 appartient à Londres, à New York et à l'overlap : la fenêtre la plus étroite
    décide, et c'est celle qui porte l'information utile."""
    contained = [window for window in DEFAULT_WINDOWS if window.contains(at(13))]

    assert len(contained) == 3
    assert session_at(at(13)) is Session.OVERLAP


def test_the_boundaries_are_half_open() -> None:
    """07:00 ouvre Londres, 16:00 ferme l'overlap et Londres, 22:00 ferme New York."""
    assert session_at(at(6, 59)) is Session.TOKYO
    assert session_at(at(7, 0)) is Session.LONDON
    assert session_at(at(15, 59)) is Session.OVERLAP
    assert session_at(at(16, 0)) is Session.NEW_YORK
    assert session_at(at(21, 59)) is Session.NEW_YORK
    assert session_at(at(22, 0)) is Session.OFF


def test_a_window_crossing_midnight_contains_both_sides() -> None:
    """Tokyo vue de l'Europe franchit minuit : 23:00 et 02:00 sont dans la même fenêtre."""
    window = Window(Session.TOKYO, time(23, 0), time(7, 0))

    assert window.contains(at(23, 30))
    assert window.contains(at(2, 0))
    assert not window.contains(at(12, 0))


def test_a_timezone_offset_does_not_change_the_session() -> None:
    """15:00Z et 17:00+02:00 sont le même instant : ils doivent tomber dans la même séance."""
    utc = at(15)
    shifted = utc.astimezone(timezone(timedelta(hours=2)))

    assert session_at(shifted) is Session.OVERLAP
    assert session_at(shifted) is session_at(utc)


def test_a_naive_datetime_is_refused() -> None:
    """Un horodatage naïf n'est pas un instant : le deviner décalerait toutes les séances."""
    with pytest.raises(ValueError, match="timezone-aware"):
        session_at(datetime(2026, 9, 25, 14, 0))  # noqa: DTZ001


def test_an_empty_window_list_means_no_session() -> None:
    assert session_at(at(14), windows=()) is Session.OFF


def test_sessions_for_keeps_the_order_and_does_no_clock_read() -> None:
    moments = [at(2), at(8), at(14), at(20), at(23)]

    assert sessions_for(moments) == [
        Session.TOKYO,
        Session.LONDON,
        Session.OVERLAP,
        Session.NEW_YORK,
        Session.OFF,
    ]


def test_the_classification_is_deterministic() -> None:
    """Fonction pure : deux appels identiques donnent le même résultat."""
    assert sessions_for([at(14)]) == sessions_for([at(14)])
