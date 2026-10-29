"""Interactive charts: data embedded read-only, local scripts only, no external request."""

import json
import re

from fastapi.testclient import TestClient


def _payload(body: str) -> dict:
    match = re.search(r'<script type="application/json" id="ta-charts">(.*?)</script>', body, re.S)
    assert match, "chart data block missing"
    return json.loads(match.group(1))


def test_overview_embeds_chart_data_and_local_scripts(seeded_client: TestClient) -> None:
    body = seeded_client.get("/").text
    data = _payload(body)
    assert {"equity", "trades", "markets", "drawdown", "now"} <= set(data)
    for kind in ("equity", "pnl-bars", "markets"):
        assert f'data-chart="{kind}"' in body
    assert "/static/js/charts.js" in body and "/static/js/ux.js" in body
    assert not re.search(r'(?:src|href)="https?://', body)


def test_trades_page_embeds_the_selection_points(seeded_client: TestClient) -> None:
    body = seeded_client.get("/trades").text
    assert 'data-chart="pnl-bars"' in body
    assert isinstance(_payload(body)["trades"], list)


def test_chart_scripts_are_served_and_read_only(client: TestClient) -> None:
    for name in ("charts", "ux"):
        script = client.get(f"/static/js/{name}.js")
        assert script.status_code == 200
        assert "fetch(" not in script.text and "XMLHttpRequest" not in script.text
        assert "http://" not in script.text.replace("http://www.w3.org/2000/svg", "")


def test_a_snapshot_stranded_months_before_the_rest_is_not_drawn() -> None:
    from datetime import UTC, datetime
    from decimal import Decimal

    from tradingagent.web.queries import without_stranded_head

    series = [
        (datetime(2026, 4, 27, tzinfo=UTC), Decimal("5558.63")),
        (datetime(2026, 10, 8, tzinfo=UTC), Decimal("5496.67")),
        (datetime(2026, 10, 9, tzinfo=UTC), Decimal("5496.70")),
    ]
    assert without_stranded_head(series) == series[1:]
    assert without_stranded_head(series[1:]) == series[1:]
    assert without_stranded_head([]) == []
