"""The five pages that had not yet received the treatment: /scalping, /strategies, /ai-lab,
/risk and /system.

Three rules are stated here, once per page rather than once per table:

* a display shows **ten rows at most**, chosen by a SQL ``LIMIT``/``OFFSET`` behind a SQL
  ``COUNT`` — the defect a page-level test cannot see is a page that *renders* ten rows after
  loading four hundred, so the statements themselves are captured and inspected;
* the search is a **bound parameter** and an empty result **says why** it is empty;
* a market is a **query parameter**, filtered in SQL, and never a column the reader filters
  by eye.

Where a column does not exist yet (``system_events.symbol``), the page gets paging and search
only, and says so; nothing is invented to make a selector look complete.
"""

import json
import re
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, insert
from tests.web import seed

from tradingagent.core.states import (
    AnalysisKind,
    ExecutionEventKind,
    HaltAction,
    HaltSource,
    ProposalStatus,
    Severity,
    StrategyStatus,
    ValidationStage,
)
from tradingagent.ea.bridge import format_utc, report_path
from tradingagent.storage.models import (
    AiAnalysisRow,
    AiProposalRow,
    BacktestRunRow,
    ExecutionEventRow,
    HaltCommandRow,
    StrategyRegistryRow,
    SystemEventRow,
    ValidationRunRow,
)
from tradingagent.web import queries
from tradingagent.web.app import create_app

TEMPLATES = Path(__file__).resolve().parents[2] / "src" / "tradingagent" / "web" / "templates"
# The operator's rule, stated once: no display shows more than this.
MAX_ROWS = 10


# ---------------------------------------------------------------------------------------
# Reading a rendered table, and proving where a query was bounded.
# ---------------------------------------------------------------------------------------


def _tbody(body: str, table_id: str) -> str:
    match = re.search(rf'<tbody id="{table_id}">(.*?)</tbody>', body, re.S)
    assert match is not None, f"the table {table_id} must render a tbody"
    return match.group(1)


def _rows(body: str, table_id: str) -> list[str]:
    return re.findall(r"<tr\b", _tbody(body, table_id))


def _value(body: str, element_id: str) -> str:
    match = re.search(rf'id="{element_id}"[^>]*>([^<]*)<', body)
    assert match is not None, f"the page must carry #{element_id}"
    return match.group(1).strip()


def _sql(engine: Engine, call: Callable[[], object]) -> list[str]:
    """Every statement the engine executed during ``call``, whitespace-normalised."""
    captured: list[str] = []

    def record(*args: Any) -> None:
        captured.append(" ".join(str(args[2]).split()).upper())

    event.listen(engine, "before_cursor_execute", record)
    try:
        call()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return captured


def _assert_bounded_in_sql(engine: Engine, call: Callable[[], object]) -> None:
    """The count and the page must both be decided by the database."""
    statements = _sql(engine, call)

    assert any(statement.startswith("SELECT COUNT(*)") for statement in statements), statements
    assert any("LIMIT" in statement for statement in statements), statements


# ---------------------------------------------------------------------------------------
# Rows the representative fixture deliberately does not carry.
# ---------------------------------------------------------------------------------------


def _registry(engine: Engine, ref: str, market: str = seed.XAU) -> None:
    strategy_id, _, version = ref.partition("@")
    with engine.begin() as connection:
        connection.execute(
            insert(StrategyRegistryRow).values(
                market=market,
                ref=ref,
                strategy_id=strategy_id,
                version=version,
                status=StrategyStatus.EXPERIMENTAL,
                parent_ref=None,
                origin="human",
                parameters={},
                results=None,
                dataset_fingerprint=None,
                promotion_reason=None,
                created_at=seed.NOW - timedelta(days=2),
                promoted_at=None,
                updated_at=seed.NOW - timedelta(days=2),
            )
        )


def _analysis(engine: Engine, index: int, market: str = seed.XAU) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(AiAnalysisRow).values(
                kind=AnalysisKind.REGIME,
                market=market,
                ref=None,
                signal_id=None,
                model="sonde",
                request={},
                response="observation de sonde",
                findings={"index": index},
                cost_eur=Decimal("0.001"),
                created_at=seed.NOW - timedelta(minutes=index),
            )
        )


def _proposal(engine: Engine, index: int, market: str = seed.XAU) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(AiProposalRow).values(
                market=market,
                ref=None,
                analysis_id=None,
                hypothesis=f"hypothèse de sonde {index}",
                proposed_change={"index": index},
                status=ProposalStatus.PROPOSED,
                decided_by=None,
                decision_reason=None,
                created_at=seed.NOW - timedelta(minutes=index),
                decided_at=None,
            )
        )


def _validation(engine: Engine, index: int, market: str = seed.XAU) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(ValidationRunRow).values(
                ref=f"sonde_{index}@1.0.0",
                market=market,
                stage=ValidationStage.WALK_FORWARD,
                passed=True,
                detail={"index": index},
                created_at=seed.NOW - timedelta(minutes=index),
            )
        )


def _backtest(engine: Engine, index: int, market: str = seed.XAU) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(BacktestRunRow).values(
                ref=f"sonde_{index}@1.0.0",
                market=market,
                dataset_id=f"jeu-{index}",
                fingerprint="e" * 64,
                window_start=seed.NOW - timedelta(days=30),
                window_end=seed.NOW,
                metrics={"sharpe": 0.5},
                costs={},
                report_path=None,
                created_at=seed.NOW - timedelta(minutes=index),
            )
        )


def _event(
    engine: Engine,
    kind: str,
    at: Any,
    severity: Severity = Severity.WARNING,
    symbol: str | None = None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(SystemEventRow).values(
                kind=kind,
                severity=severity,
                detail={"sonde": kind},
                occurred_at=at,
                symbol=symbol,
            )
        )


def _halt(engine: Engine, scope: str, at: Any) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(HaltCommandRow).values(
                scope=scope,
                action=HaltAction.HALT,
                close_positions=False,
                source=HaltSource.SERVER,
                reason="sonde",
                actor="sonde",
                occurred_at=at,
            )
        )


def _telemetry(engine: Engine, index: int) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(ExecutionEventRow).values(
                order_id=None,
                signal_id=None,
                symbol=seed.XAU,
                kind=ExecutionEventKind.ORDER_SENT,
                detail={"sonde": index},
                occurred_at=seed.NOW - timedelta(minutes=index),
            )
        )


# ---------------------------------------------------------------------------------------
# /strategies — one market at a time, ten rows, searched in SQL.
# ---------------------------------------------------------------------------------------


def test_the_strategies_page_opens_on_one_market(seeded_client: TestClient) -> None:
    body = seeded_client.get("/strategies").text

    assert seed.WITNESS in body
    assert seed.BREAKOUT not in body
    assert seed.BTC in body  # offered by the selector, never displayed


def test_the_strategies_page_can_be_asked_for_the_other_market(seeded_client: TestClient) -> None:
    body = seeded_client.get("/strategies", params={"market": seed.BTC}).text

    assert seed.BREAKOUT in body
    assert seed.WITNESS not in body


def test_the_strategies_page_shows_ten_at_most_and_pages_the_rest(
    seeded_client: TestClient, engine: Engine
) -> None:
    for index in range(12):
        _registry(engine, f"sonde_{index:02d}@1.0.0")

    first = seeded_client.get("/strategies").text
    second = seeded_client.get("/strategies", params={"page": 2}).text

    assert len(_rows(first, "strategies-body")) == MAX_ROWS
    assert "13 au total" in first
    assert "page=2" in first
    assert len(_rows(second, "strategies-body")) == 3


def test_the_strategies_page_says_when_a_search_finds_nothing(seeded_client: TestClient) -> None:
    body = seeded_client.get("/strategies", params={"q": "zzz-introuvable"}).text

    assert "Aucune stratégie pour « zzz-introuvable »." in body
    assert len(_rows(body, "strategies-body")) == 1  # the empty row, and only it


def test_the_strategies_query_counts_and_limits_in_sql(engine: Engine, populated: object) -> None:
    del populated

    _assert_bounded_in_sql(engine, lambda: queries.strategy_page(engine, market=seed.XAU))


def test_a_strategy_with_no_trade_is_written_in_the_text_colour(
    seeded_client: TestClient, engine: Engine
) -> None:
    """Zero is not a gain: it carries no colour at all (``format.ratio_class``)."""
    _registry(engine, "plate@1.0.0")

    body = seeded_client.get("/strategies", params={"q": "plate"}).text
    row = re.search(r"<tr>(?:(?!</tr>).)*plate@1\.0\.0(?:(?!</tr>).)*</tr>", body, re.S)

    assert row is not None
    assert "0.00" in row.group(0)
    assert "pos" not in row.group(0)
    assert "neg" not in row.group(0)


# ---------------------------------------------------------------------------------------
# /ai-lab — four lists, four page parameters, one market filter.
# ---------------------------------------------------------------------------------------


def test_the_ai_lab_tables_show_ten_at_most_and_page_independently(
    seeded_client: TestClient, engine: Engine
) -> None:
    for index in range(12):
        _analysis(engine, index)
        _proposal(engine, index)

    first = seeded_client.get("/ai-lab").text

    assert len(_rows(first, "analyses-body")) == MAX_ROWS
    assert len(_rows(first, "proposals-body")) == MAX_ROWS
    assert "page=2" in first
    assert "proposals=2" in first

    moved = seeded_client.get("/ai-lab", params={"proposals": 2}).text

    assert len(_rows(moved, "proposals-body")) == 3
    assert len(_rows(moved, "analyses-body")) == MAX_ROWS  # the other list did not move

    second = seeded_client.get("/ai-lab", params={"page": 2, "proposals": 2}).text

    assert len(_rows(second, "analyses-body")) == 3
    assert len(_rows(second, "proposals-body")) == 3


def test_the_ai_lab_separates_the_markets(seeded_client: TestClient) -> None:
    xau = seeded_client.get("/ai-lab").text

    assert "walk_forward" in xau  # the XAU validation
    assert "monte_carlo" not in xau  # the BTC one stays out

    btc = seeded_client.get("/ai-lab", params={"market": seed.BTC}).text

    assert "monte_carlo" in btc
    assert "walk_forward" not in btc


def test_the_ai_lab_search_says_when_it_finds_nothing(seeded_client: TestClient) -> None:
    body = seeded_client.get("/ai-lab", params={"q": "zzz-introuvable"}).text

    assert "Aucune analyse pour « zzz-introuvable »." in body
    assert "Aucune proposition pour « zzz-introuvable »." in body
    assert "Aucune validation pour « zzz-introuvable »." in body
    assert "Aucun backtest pour « zzz-introuvable »." in body


def test_the_ai_lab_queries_count_and_limit_in_sql(engine: Engine, populated: object) -> None:
    """Four lists, so four counts and four limits — one page each, decided by the database."""
    del populated

    statements = _sql(engine, lambda: queries.ai_lab_pages(engine, market=seed.XAU))

    assert sum(1 for statement in statements if statement.startswith("SELECT COUNT(*)")) == 4
    assert sum(1 for statement in statements if "LIMIT" in statement) == 4


# ---------------------------------------------------------------------------------------
# /scalping — the market is derivable, so it is filtered in SQL.
# ---------------------------------------------------------------------------------------


def test_the_scalping_page_filters_the_trades_on_the_market(seeded_client: TestClient) -> None:
    """Three closed XAU trades and two BTC ones: the market is a query parameter."""
    assert _value(seeded_client.get("/scalping").text, "scalping-trades") == "3"
    assert (
        _value(seeded_client.get("/scalping", params={"market": seed.BTC}).text, "scalping-trades")
        == "2"
    )


def test_the_scalping_read_filters_on_the_market_in_sql(engine: Engine, populated: object) -> None:
    del populated

    statements = _sql(engine, lambda: queries.scalping_view(engine, market=seed.BTC))

    assert any("SYMBOL = ?" in statement for statement in statements), statements


def test_the_scalping_hourly_cut_shows_ten_rows_at_most(
    seeded_client: TestClient, engine: Engine
) -> None:
    """The only cut that can outgrow a display: the trades close on thirteen UTC hours."""
    for hour in range(12):
        seed.add_chain(engine, generated_at=_at_hour(hour))

    first = seeded_client.get("/scalping").text

    assert len(_rows(first, "hours-body")) == MAX_ROWS
    assert "page=2" in first
    assert len(_rows(seeded_client.get("/scalping", params={"page": 2}).text, "hours-body")) >= 1


def _at_hour(hour: int) -> Any:
    """A signal closing at ``hour`` UTC, on a day the representative fixture never uses.

    ``add_chain`` keys its signal on the instant, so a day of its own keeps these rows from
    colliding with the dataset's — the test appends, it does not replace.
    """
    day = seed.NOW.replace(month=8, day=15, hour=0, minute=0, second=0, microsecond=0)
    return day.replace(hour=hour) - timedelta(hours=1)


def test_every_scalping_cut_stays_within_a_display(
    seeded_client: TestClient, engine: Engine
) -> None:
    for hour in range(20):
        seed.add_chain(engine, generated_at=_at_hour(hour))

    body = seeded_client.get("/scalping").text
    for table_id in (
        "hours-body",
        "sessions-body",
        "weekdays-body",
        "spreads-body",
        "durations-body",
        "sizes-body",
    ):
        assert len(_rows(body, table_id)) <= MAX_ROWS, table_id


# ---------------------------------------------------------------------------------------
# /risk — paging and search only: `system_events` carries no market column yet.
# ---------------------------------------------------------------------------------------


def test_the_risk_events_are_separated_by_market(seeded_client: TestClient, engine: Engine) -> None:
    """One market at a time — plus the account-wide events, which belong to every market."""
    _event(engine, "xau_only", seed.NOW - timedelta(minutes=1), symbol=seed.XAU)
    _event(engine, "btc_only", seed.NOW - timedelta(minutes=2), symbol=seed.BTC)
    _event(engine, "compte", seed.NOW - timedelta(minutes=3), symbol=None)

    xau = _tbody(seeded_client.get("/risk").text, "risk-events-body")
    btc = _tbody(seeded_client.get("/risk", params={"market": seed.BTC}).text, "risk-events-body")

    assert "xau_only" in xau and "compte" in xau
    assert "btc_only" not in xau  # two instruments never share the table
    assert "btc_only" in btc and "compte" in btc
    assert "xau_only" not in btc


def test_the_risk_page_offers_the_markets_the_events_name(seeded_client: TestClient) -> None:
    body = seeded_client.get("/risk").text

    assert re.search(r'<select id="market".*?</select>', body, re.S) is not None
    assert f'value="{seed.XAU}"' in body


def test_the_risk_events_show_ten_at_most_and_page(
    seeded_client: TestClient, engine: Engine
) -> None:
    for index in range(15):
        _event(engine, f"sonde_{index:02d}", seed.NOW - timedelta(minutes=index))

    first = seeded_client.get("/risk").text
    second = seeded_client.get("/risk", params={"page": 2}).text

    assert len(_rows(first, "risk-events-body")) == MAX_ROWS
    assert "page=2" in first
    assert len(_rows(second, "risk-events-body")) == 8  # 18 events, ten then eight


def test_the_risk_events_are_searched_in_sql(seeded_client: TestClient) -> None:
    body = seeded_client.get("/risk", params={"q": "cycle"}).text

    assert "cycle" in _tbody(body, "risk-events-body")
    assert "broker_disconnected" not in _tbody(body, "risk-events-body")

    empty = seeded_client.get("/risk", params={"q": "zzz-introuvable"}).text

    assert "Aucun événement pour « zzz-introuvable »." in empty
    assert len(_rows(empty, "risk-events-body")) == 1


def test_the_halt_history_shows_ten_at_most_and_pages(
    seeded_client: TestClient, engine: Engine
) -> None:
    for index in range(12):
        _halt(engine, f"market:SONDE{index:02d}", seed.NOW - timedelta(minutes=index))

    first = seeded_client.get("/risk").text
    second = seeded_client.get("/risk", params={"hpage": 2}).text

    assert len(_rows(first, "halt-history-body")) == MAX_ROWS
    assert "hpage=2" in first
    assert len(_rows(second, "halt-history-body")) == 3  # 13 commands
    # The apostrophe is escaped on the way out: Jinja's autoescaping is never turned off, so
    # the page carries the entity, not the raw quote.
    assert "Aucune commande d&#39;arrêt pour « zzz-introuvable »." in (
        seeded_client.get("/risk", params={"q": "zzz-introuvable"}).text
    )


def test_the_risk_queries_count_and_limit_in_sql(engine: Engine, populated: object) -> None:
    del populated

    _assert_bounded_in_sql(engine, lambda: queries.event_page(engine))
    _assert_bounded_in_sql(engine, lambda: queries.halt_page(engine))


# ---------------------------------------------------------------------------------------
# /system — paging, search, and a bounded EA journal.
# ---------------------------------------------------------------------------------------


def test_the_system_telemetry_shows_ten_at_most_and_pages(
    seeded_client: TestClient, engine: Engine
) -> None:
    for index in range(20):
        _telemetry(engine, index)

    first = seeded_client.get("/system").text
    second = seeded_client.get("/system", params={"page": 2}).text

    assert len(_rows(first, "telemetry-body")) == MAX_ROWS
    assert "page=2" in first
    assert len(_rows(second, "telemetry-body")) == 10  # 24 events in all


def test_the_system_errors_show_ten_at_most_and_are_searched(
    seeded_client: TestClient, engine: Engine
) -> None:
    for index in range(15):
        _event(engine, f"sonde_{index:02d}", seed.NOW - timedelta(minutes=index))

    first = seeded_client.get("/system").text

    assert len(_rows(first, "errors-body")) == MAX_ROWS
    assert "epage=2" in first
    assert "Aucune erreur pour « zzz-introuvable »." in (
        seeded_client.get("/system", params={"q": "zzz-introuvable"}).text
    )


def test_the_system_logs_are_separated_by_market(seeded_client: TestClient, engine: Engine) -> None:
    """The telemetry is one instrument's; the errors keep the account-wide events visible."""
    _telemetry(engine, 0)  # XAUUSD, from the helper
    with engine.begin() as connection:
        connection.execute(
            insert(ExecutionEventRow).values(
                order_id=None,
                signal_id=None,
                symbol=seed.BTC,
                kind=ExecutionEventKind.ORDER_SENT,
                detail={"sonde": "btc"},
                occurred_at=seed.NOW - timedelta(minutes=1),
            )
        )
    _event(engine, "btc_alerte", seed.NOW - timedelta(minutes=1), symbol=seed.BTC)

    body = seeded_client.get("/system").text

    assert "btc_alerte" not in _tbody(body, "errors-body")  # XAUUSD is the default
    assert "BTCUSD" not in _tbody(body, "telemetry-body")

    btc = seeded_client.get("/system", params={"market": seed.BTC}).text

    assert "btc_alerte" in _tbody(btc, "errors-body")
    assert "BTCUSD" in _tbody(btc, "telemetry-body")
    assert "XAUUSD" not in _tbody(btc, "telemetry-body")


def test_the_system_queries_count_and_limit_in_sql(engine: Engine, populated: object) -> None:
    del populated

    _assert_bounded_in_sql(engine, lambda: queries.telemetry_page(engine))


def _journal_client(engine: Engine, tmp_path: Path, events: int) -> TestClient:
    """A bridge report carrying ``events`` journal lines, the shape the EA writes."""
    directory = tmp_path / "bridge-journal" / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    moment = seed.NOW - timedelta(minutes=1)
    payload = {
        "protocol_version": 1,
        "ea_version": "1.0.0",
        "symbol": seed.XAU,
        "magic": 3031,
        "updated_at": format_utc(seed.NOW),
        "applied_revision": 7,
        "connected": True,
        "trade_allowed": True,
        "kill_switch": False,
        "local_halt": False,
        "halt_reason": "",
        "state_age_seconds": 1.0,
        "positions": [],
        "counters": {"errors": 0},
        "events": [
            {
                "seq": index,
                "at": format_utc(moment - timedelta(seconds=index)),
                "kind": "EXECUTION",
                "severity": "INFO",
                "ticket": None,
                "message": f"ligne de journal {index}",
                "data": {},
            }
            for index in range(events)
        ],
    }
    report_path(directory, seed.XAU).write_text(json.dumps(payload), encoding="utf-8")
    return TestClient(create_app(engine, now=lambda: seed.NOW, ea_reports_dir=directory))


def test_the_ea_journal_is_bounded(engine: Engine, populated: object, tmp_path: Path) -> None:
    """A report carrying sixty events must not put sixty rows on the page."""
    del populated

    with _journal_client(engine, tmp_path, events=60) as client:
        body = client.get("/system").text

    assert len(_rows(body, "ea-journal-body")) == MAX_ROWS
    # The truncation is stated, never silent.
    assert "10" in _value(body, "ea-journal-count")
    assert "60" in _value(body, "ea-journal-count")


def test_the_ea_journal_keeps_the_newest_events(
    engine: Engine, populated: object, tmp_path: Path
) -> None:
    del populated

    with _journal_client(engine, tmp_path, events=60) as client:
        body = client.get("/system").text

    assert "ligne de journal 0" in body
    assert "ligne de journal 59" not in body


# ---------------------------------------------------------------------------------------
# What every one of these pages owes the keyboard and the screen reader.
# ---------------------------------------------------------------------------------------

PAGINATED_TABLES = (
    ("/scalping", ("hours-body", "sizes-body")),
    ("/strategies", ("strategies-body",)),
    ("/ai-lab", ("analyses-body", "proposals-body", "validations-body", "backtests-body")),
    ("/risk", ("risk-events-body", "halt-history-body")),
    ("/system", ("telemetry-body", "errors-body", "ea-journal-body")),
)


@pytest.mark.parametrize(("path", "table_ids"), PAGINATED_TABLES)
def test_every_table_is_wrapped_in_a_named_scroll_region(
    seeded_client: TestClient, path: str, table_ids: tuple[str, ...]
) -> None:
    """``.table-wrap`` is the region the shell script names and makes focusable."""
    body = seeded_client.get(path).text

    for table_id in table_ids:
        before = body[: body.index(f'id="{table_id}"')]
        assert before.rindex('<div class="table-wrap">') > before.rindex("</div>"), table_id
    assert 'wrap.setAttribute("role", "region")' in body


def test_the_result_colours_come_from_the_shared_formatter() -> None:
    """Profit green, loss red, **zero in the text colour** — one rule, one function.

    Only the two pages that display a realized result carry the rule: the laboratory shows a
    *cost* and a proposal status, neither of which is a gain or a loss, so nothing there is
    coloured. What is checked everywhere is that no template re-implements the comparison.
    """
    for name in ("scalping.html", "strategies.html"):
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "display.ratio_class(" in source, name

    for name in ("scalping.html", "strategies.html", "ai_lab.html", "risk.html", "system.html"):
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "else 'neg'" not in source, name
        assert "> 0' if" not in source, name
