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


def test_every_page_with_charts_embeds_its_payload_and_icons(seeded_client: TestClient) -> None:
    expected = {
        "/positions": {"exposure", "open"},
        "/risk": {"gauges", "refusals"},
        "/strategies": {"strategies"},
        "/scalping": {"buckets"},
        "/ai-lab": {"calls", "proposals", "validations", "cost"},
        "/system": {"heartbeats", "latencies"},
    }
    for path, keys in expected.items():
        body = seeded_client.get(path).text
        assert keys <= set(_payload(body)), path
        assert '<use href="#i-' in body, path
        assert not re.search(r'(?:src|href)="https?://', body), path


def test_replay_embeds_levels_for_the_interactive_chart(seeded_client: TestClient) -> None:
    body = seeded_client.get("/trades/1").text
    replay = _payload(body)["replay"]
    assert replay is None or {"candles", "levels", "signal"} <= set(replay)


def test_heavy_modules_are_native_folds_with_a_key_value(seeded_client: TestClient) -> None:
    body = seeded_client.get("/").text
    assert body.count('<details class="mod fold') >= 4
    assert 'class="fold-val"' in body


def test_sprite_symbols_cover_every_icon_used(seeded_client: TestClient) -> None:
    for path in ("/", "/risk", "/system", "/ai-lab", "/reports", "/strategies", "/trades/1"):
        body = seeded_client.get(path).text
        used = set(re.findall(r'<use href="#(i-[a-z-]+)"', body))
        defined = set(re.findall(r'<symbol id="(i-[a-z-]+)"', body))
        assert used <= defined, (path, used - defined)
