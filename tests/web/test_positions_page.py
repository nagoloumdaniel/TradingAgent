"""The positions page: the ten most recent positions, one page at a time, filtered in SQL.

These tests state the contract the operator asked for: never more than ten rows, the rest
behind a page link that keeps the search, a search that runs in the database (case-folded,
bound as a parameter), and an empty result that says so instead of showing a mute table.
"""

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.web import seed

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.web import queries
from tradingagent.web.app import create_app

PAGE_SIZE = 10
TOKEN = "jeton-de-test-0123456789abcdef"  # pragma: allowlist secret


def _stack(engine: Engine, count: int = 13) -> list[int]:
    """Positions newer than every seeded one, newest first; returns their signal ids.

    One market only — the positions page shows a single instrument at a time, so a stack that
    alternated XAUUSD and BTCUSD would be testing a page that no longer exists. Index 0 is the
    most recent position overall and the only DEMO of the stack; index 1 is the only position
    whose exit reason is ``stop_loss``. The search tests lean on those two unique rows and
    never on a hard-coded global count.
    """
    signals: list[int] = []
    for index in range(count):
        signals.append(
            seed.add_chain(
                engine,
                market=seed.XAU,
                direction=Direction.SELL if index % 4 == 1 else Direction.BUY,
                generated_at=seed.NOW - timedelta(minutes=index + 1),
                closed=True,
                mode=TradingMode.DEMO if index == 0 else TradingMode.PAPER,
                exit_reason="stop_loss" if index == 1 else "take_profit",
            )
        )
    return signals


def _ticket(signal_id: int) -> str:
    """The broker ticket ``_chain`` writes for a given signal (800000 + signal id)."""
    return str(800_000 + signal_id)


def _body_rows(body: str) -> list[str]:
    match = re.search(r'<tbody id="positions-body">(.*?)</tbody>', body, re.S)
    assert match is not None, "the positions table must render a tbody"
    return re.findall(r"<tr\b", match.group(1))


def _hrefs(body: str) -> list[str]:
    return re.findall(r'href="([^"]*)"', body)


def _summary(body: str) -> str:
    match = re.search(r"(\d+) position\(s\) pour « ([^»]*) »", body)
    assert match is not None, "the search must summarise what it found"
    return match.group(1)


def _tickets(body: str) -> set[str]:
    return set(re.findall(r"\b(80\d{4})\b", body))


# ---------------------------------------------------------------------------------------
# Ten rows, then the next ten.
# ---------------------------------------------------------------------------------------


def test_the_page_shows_ten_of_the_most_recent_positions(
    seeded_client: TestClient, engine: Engine
) -> None:
    signals = _stack(engine)
    body = seeded_client.get("/positions").text

    assert len(_body_rows(body)) == PAGE_SIZE
    assert _ticket(signals[0]) in body  # the newest position
    assert _ticket(signals[9]) in body  # the tenth newest
    assert _ticket(signals[10]) not in body  # the eleventh waits on page 2


def test_the_second_page_shows_the_next_positions(
    seeded_client: TestClient, engine: Engine
) -> None:
    signals = _stack(engine)
    body = seeded_client.get("/positions", params={"page": 2}).text

    assert _ticket(signals[10]) in body
    assert _ticket(signals[0]) not in body
    assert "Page 2 sur 2" in body


def test_the_two_pages_do_not_overlap_and_cover_every_position(
    seeded_client: TestClient, engine: Engine
) -> None:
    _stack(engine)
    first = seeded_client.get("/positions").text
    second = seeded_client.get("/positions", params={"page": 2}).text
    first_tickets, second_tickets = _tickets(first), _tickets(second)

    # Seventeen XAUUSD positions: the thirteen appended plus the four seeded ones. The two
    # BTCUSD rows of the dataset are on the other market, and this page never mixes them in.
    assert len(first_tickets | second_tickets) == 17
    assert not first_tickets & second_tickets


# ---------------------------------------------------------------------------------------
# Bounds: a stale bookmark is clamped, never an error.
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["999", "-3", "0", "abc", ""])
def test_an_out_of_range_page_is_clamped_to_a_valid_one(
    seeded_client: TestClient, engine: Engine, value: str
) -> None:
    signals = _stack(engine)
    response = seeded_client.get("/positions", params={"page": value})

    assert response.status_code == 200
    if value == "999":
        assert "Page 2 sur 2" in response.text
        assert _ticket(signals[10]) in response.text
    else:
        assert "Page 1 sur 2" in response.text
        assert _ticket(signals[0]) in response.text


# ---------------------------------------------------------------------------------------
# Search: bound parameters, case-folded, database-side.
# ---------------------------------------------------------------------------------------


def test_the_search_filters_on_the_exit_reason(seeded_client: TestClient, engine: Engine) -> None:
    signals = _stack(engine)
    body = seeded_client.get("/positions", params={"q": "stop_loss"}).text

    assert len(_body_rows(body)) == 1
    assert "stop_loss" in body
    assert "1 position(s) pour" in body
    assert _ticket(signals[1]) in body
    assert _ticket(signals[0]) not in body


@pytest.mark.parametrize("needle", ["XAUUSD", "xauusd", "BUY", "buy", "DEMO", "demo"])
def test_the_search_finds_the_stored_value(
    seeded_client: TestClient, engine: Engine, needle: str
) -> None:
    _stack(engine)
    body = seeded_client.get("/positions", params={"q": needle}).text

    assert len(_body_rows(body)) > 0
    assert f"pour « {needle} »" in body


def test_a_case_only_change_returns_the_same_count(
    seeded_client: TestClient, engine: Engine
) -> None:
    _stack(engine)
    upper = seeded_client.get("/positions", params={"q": "XAUUSD"}).text
    lower = seeded_client.get("/positions", params={"q": "xauusd"}).text

    assert _summary(upper) == _summary(lower) == "17"


def test_the_search_accepts_the_label_the_page_displays(
    seeded_client: TestClient, engine: Engine
) -> None:
    _stack(engine)
    raw_direction = seeded_client.get("/positions", params={"q": "BUY"}).text
    label_direction = seeded_client.get("/positions", params={"q": "Achat"}).text
    raw_mode = seeded_client.get("/positions", params={"q": "DEMO"}).text
    label_mode = seeded_client.get("/positions", params={"q": "Démo"}).text

    assert _summary(raw_direction) == _summary(label_direction)
    assert _summary(raw_mode) == _summary(label_mode) == "1"


def test_the_search_finds_a_position_by_its_ticket(
    seeded_client: TestClient, engine: Engine
) -> None:
    signals = _stack(engine)
    body = seeded_client.get("/positions", params={"q": _ticket(signals[0])}).text

    assert len(_body_rows(body)) == 1
    assert _ticket(signals[0]) in body


def test_a_search_that_matches_nothing_says_so(seeded_client: TestClient, engine: Engine) -> None:
    _stack(engine)
    body = seeded_client.get("/positions", params={"q": "zzz-introuvable"}).text

    assert "Aucune position pour" in body
    assert "zzz-introuvable" in body
    assert "0 position(s) pour" in body


def test_an_empty_database_keeps_its_own_message(client: TestClient) -> None:
    body = client.get("/positions").text

    assert "Aucune position enregistrée." in body


@pytest.mark.parametrize("needle", ["'", "%", "__", "'; DROP TABLE positions; --"])
def test_the_query_is_a_bound_parameter_not_sql(
    seeded_client: TestClient, engine: Engine, needle: str
) -> None:
    """A quote and a wildcard are search text, not SQL: no 500, no accidental match-all.

    ``__`` is the interesting one: unescaped it would match every position, because every
    string is at least two characters long.
    """
    _stack(engine)
    response = seeded_client.get("/positions", params={"q": needle})

    assert response.status_code == 200
    assert "0 position(s) pour" in response.text


def test_a_literal_underscore_in_the_data_is_still_found(
    seeded_client: TestClient, engine: Engine
) -> None:
    """The escape must not make the data unreachable: ``take_profit`` is searchable."""
    _stack(engine)
    body = seeded_client.get("/positions", params={"q": "take_profit"}).text

    assert "15 position(s) pour" in body  # twelve appended plus the three seeded XAUUSD ones


# ---------------------------------------------------------------------------------------
# State survives the URL.
# ---------------------------------------------------------------------------------------


def test_the_page_links_keep_the_search(seeded_client: TestClient, engine: Engine) -> None:
    _stack(engine)
    body = seeded_client.get("/positions", params={"q": "XAUUSD"}).text
    pager = [href for href in _hrefs(body) if "page=2" in href]

    assert pager, "a filter that spans two pages must offer the next one"
    assert any("q=XAUUSD" in href for href in pager)


def test_the_search_box_remembers_what_was_typed(seeded_client: TestClient) -> None:
    body = seeded_client.get("/positions", params={"q": "xau"}).text

    assert 'action="/positions"' in body
    assert 'method="get"' in body
    assert 'name="q"' in body
    assert 'value="xau"' in body


def test_the_search_text_is_escaped(seeded_client: TestClient) -> None:
    response = seeded_client.get("/positions", params={"q": "<script>alert(1)</script>"})

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_a_protected_page_stays_protected_when_it_is_paginated(
    engine: Engine, populated: seed.Seeded
) -> None:
    del populated
    with TestClient(
        create_app(engine, now=lambda: seed.NOW, access_token=TOKEN), follow_redirects=False
    ) as client:
        response = client.get("/positions", params={"page": 2, "q": "xau"})

    assert response.status_code == 401


# ---------------------------------------------------------------------------------------
# The alerts section: aligned columns rather than a list of running text.
# ---------------------------------------------------------------------------------------


def test_the_alerts_section_aligns_its_columns(seeded_client: TestClient) -> None:
    body = seeded_client.get("/positions").text

    assert '<ul class="alerts"' not in body
    for column in ("Horodatage (UTC)", "Gravité", "Type", "Détail"):
        assert f"<th>{column}</th>" in body
    # The seeded critical event carries its severity badge, in its own aligned cell.
    assert "clock_mismatch" in body
    assert 'class="badge bad"' in body


def test_the_alerts_list_is_still_the_live_feed_target(seeded_client: TestClient) -> None:
    body = seeded_client.get("/positions").text

    assert 'id="alerts-list"' in body
    assert 'new EventSource("/events?market="' in body
    # The first stream cycle repeats what the server already rendered: the page must key
    # the rows so the feed completes the list instead of duplicating it.
    assert "alertKey" in body


# ---------------------------------------------------------------------------------------
# The query layer, on its own.
# ---------------------------------------------------------------------------------------


def test_the_query_clamps_and_reports_the_page_metadata(
    populated: seed.Seeded, engine: Engine
) -> None:
    del populated
    page = queries.position_page(engine, seed.NOW, page=99)

    assert page.page == page.pages == 1
    assert page.total == 7  # five closed chains and two open positions
    assert len(page.rows) == 7
    assert page.page_size == queries.POSITION_PAGE_SIZE


def test_the_query_orders_the_newest_first(populated: seed.Seeded, engine: Engine) -> None:
    del populated
    page = queries.position_page(engine, seed.NOW)

    assert page.rows[0].symbol == seed.BTC  # opened one hour before NOW
    assert [row.symbol for row in page.rows[:2]] == [seed.BTC, seed.XAU]


def test_the_query_strips_the_search_before_echoing_it(
    populated: seed.Seeded, engine: Engine
) -> None:
    del populated
    page = queries.position_page(engine, seed.NOW, query="  xauusd  ")

    assert page.query == "xauusd"
    assert page.total == 4  # three closed XAU chains and the open one


def test_the_query_carries_the_exit_reason_of_a_closed_position(
    populated: seed.Seeded, engine: Engine
) -> None:
    del populated
    page = queries.position_page(engine, seed.NOW)

    assert {row.exit_reason for row in page.rows} == {None, "take_profit"}
