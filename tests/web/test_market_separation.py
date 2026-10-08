"""XAUUSD and BTCUSD never share a table.

The operator's rule: a log, a strategy list, a report list, a risk view or a position list
shows *one* market, and the market is a parameter of the query — not a column the reader has
to filter by eye, and not a client-side hide. These tests state the new contract: the default
is a single market, the selector is visible, and a stale ``?market=`` falls back to the
default instead of silently mixing two instruments.
"""

import re
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.web import seed

from tradingagent.web import paging, queries


def _table(body: str, table_id: str = "positions-body") -> str:
    match = re.search(rf'<tbody id="{table_id}">(.*?)</tbody>', body, re.S)
    assert match is not None, f"the table {table_id} must render a tbody"
    return match.group(1)


def _body_rows(body: str, table_id: str = "positions-body") -> list[str]:
    return re.findall(r"<tr\b", _table(body, table_id))


def _symbols(body: str, table_id: str = "positions-body") -> list[str]:
    return re.findall(r"<td>([A-Z]{3}USD)</td>", _table(body, table_id))


def test_the_query_layer_knows_which_markets_exist(populated: seed.Seeded, engine: Engine) -> None:
    del populated

    assert queries.available_markets(engine) == (seed.BTC, seed.XAU)


def test_the_default_market_prefers_gold() -> None:
    assert paging.default_market([seed.BTC, seed.XAU]) == seed.XAU
    assert paging.default_market([seed.BTC]) == seed.BTC
    assert paging.default_market([]) == paging.ALL_MARKETS


def test_an_unknown_market_falls_back_to_the_default() -> None:
    assert paging.resolve_market("NOPE", [seed.BTC, seed.XAU]) == seed.XAU
    assert paging.resolve_market("", [seed.BTC, seed.XAU]) == seed.XAU
    assert paging.resolve_market(seed.BTC, [seed.BTC, seed.XAU]) == seed.BTC


def test_the_positions_query_filters_on_the_market(populated: seed.Seeded, engine: Engine) -> None:
    del populated

    page = queries.position_page(engine, seed.NOW, market=seed.BTC)

    assert page.market == seed.BTC
    assert page.rows, "the seeded BTC position must be found"
    assert {row.symbol for row in page.rows} == {seed.BTC}


def test_the_positions_page_opens_on_one_market(seeded_client: TestClient) -> None:
    body = seeded_client.get("/positions").text
    symbols = _symbols(body)

    assert symbols
    assert seed.XAU in symbols
    assert seed.BTC not in symbols
    assert 'name="market"' in body


def test_the_positions_page_can_be_asked_for_the_other_market(seeded_client: TestClient) -> None:
    symbols = _symbols(seeded_client.get("/positions", params={"market": seed.BTC}).text)

    assert symbols
    assert set(symbols) == {seed.BTC}


def test_a_stale_market_never_mixes_the_two(seeded_client: TestClient) -> None:
    symbols = _symbols(seeded_client.get("/positions", params={"market": "NOPE"}).text)

    assert symbols
    assert set(symbols) == {seed.XAU}


def test_the_positions_pager_keeps_the_market(seeded_client: TestClient, engine: Engine) -> None:
    for index in range(14):
        seed.add_chain(
            engine,
            market=seed.BTC,
            generated_at=seed.NOW - timedelta(minutes=index + 1),
            closed=True,
        )
    body = seeded_client.get("/positions", params={"market": seed.BTC}).text
    pager = [href for href in re.findall(r'href="([^"]*)"', body) if "page=2" in href]

    assert pager, "the BTC history must span two pages"
    assert all(f"market={seed.BTC}" in href for href in pager)


def _market_select(body: str) -> str:
    match = re.search(r'<select id="market".*?</select>', body, re.S)
    assert match is not None, "the market selector must be a native select"
    return match.group(0)


def test_the_trades_page_opens_on_one_market(seeded_client: TestClient) -> None:
    body = seeded_client.get("/trades").text
    symbols = _symbols(body, "trades-body")

    assert body  # the display is not empty
    assert symbols
    assert seed.XAU in symbols
    assert seed.BTC not in symbols
    assert seed.BTC in _market_select(body)  # the other market is offered, not displayed


def test_the_trades_page_can_be_asked_for_the_other_market(seeded_client: TestClient) -> None:
    body = seeded_client.get("/trades", params={"market": seed.BTC}).text
    symbols = _symbols(body, "trades-body")

    assert symbols
    assert set(symbols) == {seed.BTC}


def test_the_market_selector_lists_the_markets_the_database_holds(
    seeded_client: TestClient,
) -> None:
    selector = _market_select(seeded_client.get("/trades").text)

    assert f'value="{seed.XAU}"' in selector
    assert f'value="{seed.BTC}"' in selector
    # No "all markets" escape hatch: a table that mixes the two is the defect being removed.
    assert '<option value=""' not in selector
