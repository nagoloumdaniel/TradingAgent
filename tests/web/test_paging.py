"""The shared paging primitive: the contract every list page builds on.

One place decides how many rows a display shows, how a garbled ``?page=`` degrades, and
how a page link keeps the filters that produced it. Stating it here means the trades page,
the positions page and the reports page cannot drift apart.
"""

from tradingagent.web.paging import PAGE_SIZE, Page, page_bounds, page_number


def test_a_display_shows_at_most_ten_rows() -> None:
    assert PAGE_SIZE == 10


def test_a_garbled_page_number_degrades_to_the_first_page() -> None:
    for value in (None, "", "abc", "0", "-3", "  ", "1.5"):
        assert page_number(value) == 1


def test_a_valid_page_number_is_kept() -> None:
    assert page_number("1") == 1
    assert page_number("7") == 7
    assert page_number(" 12 ") == 12


def test_the_bounds_clamp_and_always_offer_a_page() -> None:
    assert page_bounds(total=0, page=1, size=10) == (1, 1)
    assert page_bounds(total=25, page=1, size=10) == (1, 3)
    assert page_bounds(total=25, page=99, size=10) == (3, 3)
    assert page_bounds(total=10, page=2, size=10) == (1, 1)


def _page(**overrides: object) -> Page[str]:
    values: dict[str, object] = {
        "rows": ("a", "b"),
        "total": 12,
        "page": 1,
        "pages": 2,
        "page_size": 10,
        "query": "",
        "market": "XAUUSD",
        "path": "/trades",
        "filters": (("mode", "PAPER"),),
        "noun": "trade",
    }
    values.update(overrides)
    return Page(**values)  # type: ignore[arg-type]


def test_a_page_link_keeps_every_filter() -> None:
    url = _page().url(2)

    assert url.startswith("/trades?")
    assert "page=2" in url
    assert "market=XAUUSD" in url
    assert "mode=PAPER" in url


def test_a_page_link_keeps_the_search() -> None:
    assert "q=stop_loss" in _page(query="stop_loss").url(3)


def test_a_page_link_escapes_what_the_operator_typed() -> None:
    url = _page(query="a&b=c d").url(2)

    assert "a%26b%3Dc+d" in url or "a%26b%3Dc%20d" in url
    assert "&b=" not in url


def test_an_empty_page_says_why_it_is_empty() -> None:
    assert _page(total=0, pages=1, rows=(), market="", query="").empty_message == (
        "Aucun trade enregistré."
    )
    assert _page(total=0, pages=1, rows=(), query="zzz").empty_message == (
        "Aucun trade pour « zzz »."
    )
    assert _page(total=0, pages=1, rows=(), query="", market="BTCUSD").empty_message == (
        "Aucun trade pour BTCUSD."
    )


def test_the_page_reports_the_rows_it_is_showing() -> None:
    assert _page().showing == "1\u20132 sur 12"


def test_an_empty_page_reports_no_range() -> None:
    assert _page(total=0, pages=1, rows=()).showing == "0 sur 0"
