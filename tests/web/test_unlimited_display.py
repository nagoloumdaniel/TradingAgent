"""The dashboard shows an absent limit as "illimité" and the daily stop as its own rule.

``config/agent.yaml`` ships ``max_open_positions``, ``max_positions_per_market`` and
``max_trades_per_day`` as ``null``: no cap. The pages must never print ``None``, and a gauge
without a limit must never claim a ratio (so it can never turn amber or red).
"""

import re
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.web.seed import NOW, Seeded

from tradingagent.web import queries
from tradingagent.web.app import AGENT_CONFIG, create_app


def _gauges(engine: Engine) -> dict[str, dict[str, object]]:
    view = queries.risk_view(engine, NOW, AGENT_CONFIG)
    account = queries.account_figures(engine, NOW)
    return {gauge["name"]: gauge for gauge in queries.risk_gauges(engine, NOW, view, account)}


def test_an_absent_cap_is_an_unlimited_gauge_without_a_ratio(
    engine: Engine, populated: Seeded
) -> None:
    gauges = _gauges(engine)

    for name in ("Positions ouvertes", "Positions par marché", "Trades du jour"):
        assert gauges[name]["unlimited"] is True
        assert gauges[name]["ratio"] is None
        assert "illimité" in str(gauges[name]["text"])


def test_the_per_market_gauge_carries_its_breakdown(engine: Engine, populated: Seeded) -> None:
    parts = _gauges(engine)["Positions par marché"]["parts"]

    assert isinstance(parts, list)
    assert all({"name", "value"} <= set(part) for part in parts)


def test_a_configured_cap_stays_an_ordinary_gauge() -> None:
    gauge = queries._count_gauge("Positions ouvertes", 2, 5)

    assert gauge["unlimited"] is False
    assert gauge["ratio"] == 0.4
    assert gauge["text"] == "2 sur 5"


def test_the_daily_stop_rule_is_a_gauge_when_it_is_on(engine: Engine, populated: Seeded) -> None:
    gauges = _gauges(engine)

    stop = gauges["Pertes consécutives du jour"]
    assert stop["unlimited"] is False
    assert stop["limit"] == 3.0


def test_the_risk_page_prints_illimite_never_none(seeded_client: TestClient) -> None:
    body = seeded_client.get("/risk").text

    assert "illimité" in body
    assert not re.search(r">\s*None\s*<", body)


def test_the_risk_page_shows_the_daily_stop_rule(seeded_client: TestClient) -> None:
    body = seeded_client.get("/risk").text

    assert "Arrêt après pertes" in body
    assert re.search(r"Arrêt après pertes</div>\s*<div class=\"value\">\d+ / 3<", body)
    assert "règle active" in body


def test_the_daily_stop_counts_only_todays_losing_streak(engine: Engine, populated: Seeded) -> None:
    view = queries.risk_view(engine, NOW, AGENT_CONFIG)

    stop = queries.daily_stop(engine, NOW, view)

    assert stop is not None
    assert stop.limit == 3
    assert stop.triggered is (stop.losses >= 3)


def test_the_halt_banner_is_drawn_once_per_page(engine: Engine, seed_halt: None) -> None:
    del seed_halt
    with TestClient(create_app(engine, now=lambda: NOW)) as client:
        overview = client.get("/").text
        risk = client.get("/risk").text

    # The halt banner is one shared macro: once per page, whichever page shows it.
    for body in (overview, risk):
        assert body.count("Trading arrêté.") == 1


def test_the_risk_limit_decimal_still_parses() -> None:
    limits = queries.risk_limits(AGENT_CONFIG)

    assert limits[0].risk_per_trade_pct == Decimal("0.5")
    assert limits[0].max_open_positions is None
