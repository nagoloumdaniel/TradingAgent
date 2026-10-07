"""Every page, filter and export, rendered through ``TestClient`` on real HTML."""

import json

import pytest
from fastapi.testclient import TestClient
from tests.web import seed

PAGES = (
    "/",
    "/positions",
    "/trades",
    "/strategies",
    "/ai-lab",
    "/risk",
    "/system",
    "/reports",
)


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_on_an_empty_database(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "TradingAgent" in response.text


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_on_a_populated_database(seeded_client: TestClient, path: str) -> None:
    response = seeded_client.get(path)
    assert response.status_code == 200
    assert "lecture seule" in response.text


def test_navigation_lists_every_page(seeded_client: TestClient) -> None:
    body = seeded_client.get("/").text
    for path in PAGES:
        assert f'href="{path}"' in body


def test_overview_shows_the_dataset_and_the_total(seeded_client: TestClient) -> None:
    body = seeded_client.get("/").text
    assert "16.75" in body  # lifetime net profit
    assert "3.25" in body  # day
    assert "15.75" in body  # week
    assert "13.75" in body  # month
    assert "1012.00" in body  # equity
    assert "38.00" in body  # current drawdown
    assert "4.00" in body  # max drawdown from analytics
    assert "TOTAL" in body
    assert "60.0 %" in body  # win rate: 3 of 5
    assert "3.79" in body  # profit factor 22.75 / 6.00


def test_overview_separates_each_market(seeded_client: TestClient) -> None:
    body = seeded_client.get("/").text
    assert seed.XAU in body
    assert seed.BTC in body
    assert "11.50" in body  # XAUUSD net profit
    assert "5.25" in body  # BTCUSD net profit
    assert "53.00" in body  # XAUUSD notional
    assert "620.00" in body  # BTCUSD notional


def test_overview_states_the_market_and_data_status(seeded_client: TestClient) -> None:
    body = seeded_client.get("/").text
    assert "battement de cœur" in body
    assert "données récentes" in body  # the XAUUSD candle is one hour old
    assert "données anciennes" in body  # the BTCUSD candle is thirty hours old


def test_overview_shows_the_strategy_status(seeded_client: TestClient) -> None:
    body = seeded_client.get("/").text
    assert seed.WITNESS in body
    assert seed.BREAKOUT in body
    assert ">live<" in body
    assert ">paper<" in body


def test_overview_warns_when_a_global_halt_is_active(
    seeded_client: TestClient, seed_halt: None
) -> None:
    body = seeded_client.get("/").text
    assert "Trading arrêté" in body
    assert "perte quotidienne dépassée" in body


def test_trades_page_lists_every_closed_trade(seeded_client: TestClient) -> None:
    body = seeded_client.get("/trades").text
    assert "5 trade(s) affiché(s) sur 5 au total" in body
    assert "witness@1.0.0" in body
    assert "trend_breakout@1.0.0" in body
    assert "Achat" in body
    assert "Vente" in body


def test_trades_page_filters_by_market(seeded_client: TestClient) -> None:
    body = seeded_client.get("/trades", params={"market": seed.XAU}).text
    assert "3 trade(s) affiché(s) sur 5 au total" in body
    assert f"market={seed.XAU}" in body  # the export links carry the active filter


def test_trades_page_filters_by_strategy_and_mode(seeded_client: TestClient) -> None:
    by_strategy = seeded_client.get("/trades", params={"strategy": seed.WITNESS}).text
    assert "3 trade(s) affiché(s) sur 5 au total" in by_strategy
    by_mode = seeded_client.get("/trades", params={"mode": "DEMO"}).text
    assert "1 trade(s) affiché(s) sur 5 au total" in by_mode


def test_an_unknown_filter_value_degrades_to_no_filter(seeded_client: TestClient) -> None:
    response = seeded_client.get("/trades", params={"mode": "NOPE"})
    assert response.status_code == 200
    assert "5 trade(s) affiché(s) sur 5 au total" in response.text


def test_trades_page_opens_the_detail_of_one_signal(
    seeded_client: TestClient, seeded: seed.Seeded
) -> None:
    body = seeded_client.get("/trades", params={"trade_id": seeded.losing_signal_id}).text
    assert "Détail du trade" in body
    assert "croisement de moyennes confirmé par le volume" in body
    assert "ema_fast" in body
    assert "dans les limites" in body


def test_strategies_page_shows_the_registry_and_the_evidence(seeded_client: TestClient) -> None:
    body = seeded_client.get("/strategies").text
    assert seed.WITNESS in body
    assert "validé sur 12 mois" in body
    assert "walk_forward" in body
    assert "monte_carlo" in body
    assert "réussi" in body
    assert "échoué" in body
    assert "xau-h1-2024" in body
    assert "1.4" in body  # the stored backtest metric, displayed as recorded


def test_ai_lab_page_shows_analyses_and_proposals(seeded_client: TestClient) -> None:
    body = seeded_client.get("/ai-lab").text
    assert "loss_analysis" in body
    assert "claude-sonnet-4-5" in body
    assert "Allonger la fenêtre de tendance réduit les faux signaux." in body
    assert "proposed" in body
    assert "Le régime de marché a changé." in body


def test_risk_page_shows_limits_exposure_and_the_halt_history(seeded_client: TestClient) -> None:
    body = seeded_client.get("/risk").text
    assert "0.5 %" in body  # simulated risk per trade, as written in the YAML
    assert "2.0 %" in body  # live risk per trade
    assert "Aucun arrêt actif" in body
    assert "BTCUSD" in body  # the disabled market
    assert "volatilité excessive" in body
    assert "broker_disconnected" in body
    assert "clock_mismatch" in body
    assert "53.00" in body  # XAUUSD exposure


def test_risk_page_reports_a_global_halt(seeded_client: TestClient, seed_halt: None) -> None:
    body = seeded_client.get("/risk").text
    assert "Trading arrêté" in body
    assert "perte quotidienne dépassée" in body


def test_system_page_shows_health_latency_and_errors(seeded_client: TestClient) -> None:
    body = seeded_client.get("/system").text
    assert "joignable" in body
    assert "sqlite" in body
    assert "1000.0" in body  # signal → order sent
    assert "2000.0" in body  # signal → filled
    assert "broker_disconnected" in body
    assert "signal_generated" in body


def test_reports_page_renders_the_stored_report(seeded_client: TestClient) -> None:
    body = seeded_client.get("/reports").text
    assert "Rapport quotidien" in body
    assert "+15.75 EUR" in body
    assert "non envoyé" in body
    assert "/export/trades.csv" in body


def test_reports_page_opens_a_chosen_report(seeded_client: TestClient, seeded: seed.Seeded) -> None:
    body = seeded_client.get("/reports", params={"report_id": seeded.report_id}).text
    assert "Rapport quotidien" in body


def test_healthz_reports_read_only_and_the_database(seeded_client: TestClient) -> None:
    payload = seeded_client.get("/healthz").json()
    assert payload["status"] == "ok"
    assert payload["read_only"] is True
    assert payload["database"] == {"ok": True, "dialect": "sqlite"}
    assert payload["halted"] is False


def test_openapi_and_docs_are_disabled(seeded_client: TestClient) -> None:
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert seeded_client.get(path).status_code == 404


def test_trades_export_is_the_reporting_export(seeded_client: TestClient) -> None:
    response = seeded_client.get("/export/trades.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0].startswith("symbol,strategy_ref,direction")
    assert len(lines) == 6  # header + five trades


def test_trades_json_export_carries_every_trade(seeded_client: TestClient) -> None:
    response = seeded_client.get("/export/trades.json")
    assert response.status_code == 200
    payload = json.loads(response.text)
    assert len(payload) == 5
    assert {entry["symbol"] for entry in payload} == {seed.XAU, seed.BTC}


def test_trades_json_export_honours_the_filters(seeded_client: TestClient) -> None:
    response = seeded_client.get("/export/trades.json", params={"market": seed.BTC})
    assert len(json.loads(response.text)) == 2


def test_performance_export_is_the_analytics_summary(seeded_client: TestClient) -> None:
    payload = json.loads(seeded_client.get("/export/performance.json").text)
    assert payload["trades"] == 5
    assert payload["wins"] == 3
    assert payload["net_profit"] == "16.75"
    assert payload["insufficient_sample"] is True  # below MIN_SIGNIFICANT_SAMPLE


def test_equity_svg_is_the_reporting_chart(seeded_client: TestClient) -> None:
    response = seeded_client.get("/export/equity.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.text.startswith("<svg")
    assert "polyline" in response.text
    assert "drawdown" in response.text


def test_equity_svg_handles_an_empty_history(client: TestClient) -> None:
    body = client.get("/export/equity.svg").text
    assert body.startswith("<svg")
    assert "aucune donnee" in body


def test_a_report_can_be_downloaded(seeded_client: TestClient, seeded: seed.Seeded) -> None:
    response = seeded_client.get(f"/export/reports/{seeded.report_id}.txt")
    assert response.status_code == 200
    assert response.text == seed.REPORT_CONTENT


def test_downloading_an_unknown_report_is_a_404(seeded_client: TestClient) -> None:
    assert seeded_client.get("/export/reports/9999.txt").status_code == 404


def test_positions_page_carries_the_live_wiring(seeded_client: TestClient) -> None:
    body = seeded_client.get("/positions").text
    assert 'new EventSource("/events")' in body
    assert "stream-status" in body
    assert "positions-body" in body
    assert "alertes" in body.lower()
