"""Errors are read one UTC day at a time: today by default, any other day on request.

The operator's brief: clear the errors so the dashboard starts from zero, then show each
day's new errors only, unless the previous ones are searched for or sorted. The stored rows
are never touched (the journal is append-only); only the *window* the pages read is narrowed,
and the window is one primitive shared by every page that lists an error.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from tradingagent.web import paging
from tradingagent.web.paging import DayScope, resolve_day

AT = datetime(2026, 10, 7, 15, 42, tzinfo=UTC)


def test_no_request_means_today() -> None:
    scope = resolve_day(None, AT)

    assert scope.is_today is True
    assert scope.is_history is False
    assert scope.start == datetime(2026, 10, 7, tzinfo=UTC)
    assert scope.end == datetime(2026, 10, 8, tzinfo=UTC)
    assert scope.param == ""
    assert scope.label == "aujourd'hui"


def test_a_past_day_is_reachable_by_its_date() -> None:
    scope = resolve_day("2026-10-05", AT)

    assert scope.is_today is False
    assert scope.day == date(2026, 10, 5)
    assert scope.start == datetime(2026, 10, 5, tzinfo=UTC)
    assert scope.end == datetime(2026, 10, 6, tzinfo=UTC)
    assert scope.param == "2026-10-05"
    assert scope.label == "le 05/10/2026"


def test_the_whole_history_is_one_explicit_choice() -> None:
    scope = resolve_day("all", AT)

    assert scope.is_history is True
    assert scope.start is None
    assert scope.end is None
    assert scope.param == "all"
    assert scope.label == "tout l'historique"


def test_a_search_reaches_the_history_without_any_other_gesture() -> None:
    assert resolve_day(None, AT, searching=True).is_history is True
    # ...but an explicit day still wins over the search: the operator narrowed it themselves.
    assert resolve_day("2026-10-05", AT, searching=True).day == date(2026, 10, 5)


@pytest.mark.parametrize("garbage", ["", "  ", "hier", "2026-13-40", "05/10/2026", "2026-10-7x"])
def test_a_garbled_day_degrades_to_today(garbage: str) -> None:
    scope = resolve_day(garbage, AT)

    assert scope.is_today is True


def test_a_future_day_is_clamped_to_today() -> None:
    assert resolve_day("2026-12-01", AT).is_today is True


def test_the_neighbouring_days_stop_at_today() -> None:
    today = resolve_day(None, AT)
    yesterday = resolve_day("2026-10-06", AT)

    assert today.previous == date(2026, 10, 6)
    assert today.following is None  # tomorrow does not exist yet
    assert yesterday.following == date(2026, 10, 7)
    assert resolve_day("all", AT).previous is None


def test_contains_is_half_open_so_midnight_belongs_to_the_new_day() -> None:
    scope = resolve_day(None, AT)

    assert scope.contains(datetime(2026, 10, 7, 0, 0, tzinfo=UTC)) is True
    assert scope.contains(datetime(2026, 10, 6, 23, 59, 59, tzinfo=UTC)) is False
    assert scope.contains(datetime(2026, 10, 8, 0, 0, tzinfo=UTC)) is False
    assert resolve_day("all", AT).contains(datetime(2001, 1, 1, tzinfo=UTC)) is True


def test_the_day_travels_in_page_links_only_when_it_is_not_the_default() -> None:
    page: paging.Page[None] = paging.Page(
        rows=(),
        total=0,
        page=1,
        pages=3,
        path="/system",
        filters=(("day", resolve_day(None, AT).param),),
    )
    assert "day=" not in page.url(2)

    past: paging.Page[None] = paging.Page(
        rows=(),
        total=0,
        page=1,
        pages=3,
        path="/system",
        filters=(("day", resolve_day("2026-10-05", AT).param),),
    )
    assert "day=2026-10-05" in past.url(2)


def test_an_empty_page_names_its_window() -> None:
    page: paging.Page[None] = paging.Page(
        rows=(), total=0, page=1, pages=1, noun="erreur", feminine=True, scope="aujourd'hui"
    )

    assert page.empty_message == "Aucune erreur aujourd'hui."


def test_the_scope_is_a_frozen_value() -> None:
    scope = resolve_day(None, AT)
    with pytest.raises(AttributeError):
        scope.start = None  # type: ignore[misc]
    assert isinstance(scope, DayScope)
    assert scope.today == AT.date()
    assert (scope.end - scope.start) == timedelta(days=1)  # type: ignore[operator]
