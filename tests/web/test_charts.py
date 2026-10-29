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
