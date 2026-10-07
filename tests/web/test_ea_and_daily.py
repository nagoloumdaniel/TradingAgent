"""The Guardian EAs and the stored daily aggregates, as the dashboard shows them.

The EA bridge writes JSON reports; the dashboard reads them and never invents a status. These
tests cover the three cases the operator must be able to tell apart: a live Guardian, a
silent one, and one whose report cannot be parsed at all.
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.web import seed

from tradingagent.core.mode import TradingMode
from tradingagent.web import queries


def test_ea_status_is_read_from_the_bridge_reports(
    populated: seed.Seeded, engine: Engine, ea_reports_dir: Path
) -> None:
    views = {view.symbol: view for view in queries.ea_views(ea_reports_dir, seed.NOW)}
    assert set(views) == {seed.XAU, seed.BTC}
    xau = views[seed.XAU]
    assert xau.online is True
    assert xau.readable is True
    assert xau.connected is True
    assert xau.trade_allowed is True
    assert xau.kill_switch is False
    assert xau.heartbeat_age is not None and xau.heartbeat_age.total_seconds() == 0
    assert xau.remote_positions == 1
    assert xau.events[0].kind == "EXECUTION"
    # The BTCUSD heartbeat is older than the bridge's tolerance.
    assert views[seed.BTC].online is False
    assert views[seed.BTC].status.value == "OFFLINE"


def test_ea_kill_switch_is_surfaced(
    populated: seed.Seeded, engine: Engine, ea_halted_reports_dir: Path
) -> None:
    views = queries.ea_views(ea_halted_reports_dir, seed.NOW)
    assert len(views) == 1
    guardian = views[0]
    assert guardian.halted is True
    assert guardian.kill_switch is True
    assert guardian.trade_allowed is False
    assert guardian.halt_reason == "perte journalière dépassée"
    assert queries.ea_halted(views) == views


def test_an_unparseable_report_is_offline_not_missing(
    populated: seed.Seeded, engine: Engine, unreadable_reports_dir: Path
) -> None:
    views = queries.ea_views(unreadable_reports_dir, seed.NOW)
    assert [view.symbol for view in views] == [seed.XAU]
    assert views[0].readable is False
    assert views[0].online is False
    assert views[0].heartbeat_at is None


def test_an_unconfigured_bridge_yields_nothing(populated: seed.Seeded, engine: Engine) -> None:
    assert queries.ea_views(None, seed.NOW) == ()
    assert queries.ea_views(Path("nowhere-at-all"), seed.NOW) == ()


def test_overview_shows_the_guardians(seeded_client: TestClient) -> None:
    """Without a bridge the page says so rather than showing an empty table as a status."""
    body = seeded_client.get("/").text
    assert "Gardiens EA" in body
    assert "Aucun rapport EA" in body
    assert "TRADINGAGENT_EA_REPORTS_DIR" in body


def test_overview_shows_a_live_and_a_silent_guardian(ea_client: TestClient) -> None:
    body = ea_client.get("/").text
    assert "EN LIGNE" in body
    assert "HORS LIGNE" in body
    assert "order 5001 filled" not in body  # the journal belongs to the system page
    assert "Aucun rapport EA" not in body


def test_overview_and_risk_raise_the_kill_switch(halted_ea_client: TestClient) -> None:
    overview = halted_ea_client.get("/").text
    assert "Garde EA en arrêt" in overview
    assert "perte journalière dépassée" in overview
    risk = halted_ea_client.get("/risk").text
    assert "Interrupteur d'urgence des EA" in risk
    assert "perte journalière dépassée" in risk


def test_system_page_lists_the_ea_journal(ea_client: TestClient) -> None:
    body = ea_client.get("/system").text
    assert "Gardiens EA" in body
    assert "order 5001 filled" in body
    assert "1.0.0" in body  # the reported EA version
    assert "12" in body  # the event sequence
    assert "actif" not in body  # no kill switch here


def test_system_page_still_lists_an_unreadable_guardian(corrupt_ea_client: TestClient) -> None:
    body = corrupt_ea_client.get("/system").text
    assert "XAUUSD" in body
    assert "HORS LIGNE" in body


def test_daily_aggregates_are_read_from_their_own_table(
    populated: seed.Seeded, engine: Engine
) -> None:
    start, end = queries.daily_window(seed.NOW)
    rows = queries.daily_performance(engine, start, end)
    assert len(rows) == 5
    days = [row.day for row in rows]
    assert days == sorted(days, reverse=True)
    # The fast path and the trades table tell the same story over the same window.
    assert sum((row.pnl for row in rows), Decimal(0)) == seed.TOTAL_PNL
    today = [row for row in rows if row.day == queries.day_start(seed.NOW)]
    assert sum(row.trades for row in today) == 2
    assert sum((row.pnl for row in today), Decimal(0)) == seed.DAY_PNL
    assert {row.market for row in today} == {seed.XAU, seed.BTC}
    assert all(row.mode in {TradingMode.PAPER, TradingMode.DEMO} for row in rows)


def test_daily_window_covers_whole_days(engine: Engine) -> None:
    start, end = queries.daily_window(seed.NOW, days=30)
    assert end - start == timedelta(days=30)
    assert start == queries.day_start(seed.NOW) - timedelta(days=29)


def test_overview_renders_the_daily_table(seeded_client: TestClient) -> None:
    body = seeded_client.get("/").text
    assert "Par jour (agrégats enregistrés)" in body
    assert "daily_performance" in body
    assert "12.50" in body
    assert "7.25" in body


def test_the_daily_table_is_omitted_when_empty(client: TestClient) -> None:
    body = client.get("/").text
    assert "Aucun agrégat journalier enregistré" in body
