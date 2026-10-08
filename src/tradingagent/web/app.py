"""The dashboard application: FastAPI + Jinja2 + SSE, read-only by construction (§23-§34).

``create_app`` builds every route from a single SQLAlchemy :class:`Engine`. Nothing here
opens an order, changes a mode, lifts a halt or writes a row: the whole surface is GET, the
queries live in :mod:`tradingagent.web.queries`, and the figures come from
:mod:`tradingagent.analytics`. A test walks the route table *and* every page to prove it
(``tests/web/test_read_only.py``).

Run it with::

    uv run tradingagent-web                     # http://127.0.0.1:8787
    uv run tradingagent-web --port 9000
"""

import argparse
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import dotenv_values
from fastapi import FastAPI, Query, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from tradingagent.analytics import Trade, compute_performance
from tradingagent.config.errors import ConfigError
from tradingagent.config.settings import load_database_settings
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import Severity
from tradingagent.reporting.exports import (
    equity_and_drawdown_svg,
    export_performance_json,
    export_trades_csv,
    export_trades_json,
)
from tradingagent.storage.engine import create_database_engine
from tradingagent.web import format as display
from tradingagent.web import paging, queries
from tradingagent.web.auth import ACCESS_ENV_VAR, SESSION_COOKIE, AccessControl, configured_token
from tradingagent.web.sse import SSE_HEADERS, EventStream
from tradingagent.web.views import (
    ALERT_COLUMNS,
    POSITION_COLUMNS,
    POSITION_NUMERIC_COLUMNS,
    POSITION_TABLE_COLUMNS,
    describe,
    position_list_cells,
    replay_checks,
    replay_timeline,
    state_label,
)

ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = ROOT / ".env"
AGENT_CONFIG = ROOT / "config" / "agent.yaml"

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
# Served read-only. Its only file is the brand mark: a local mount means the browser fetches
# it once and the dashboard keeps working with no CDN and no network.
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Error pages. A monitoring tool that says "Internal Server Error" in JSON has told the
# operator nothing they can act on: each of these says what happened, what it does NOT mean,
# and where to go next. The 401 is deliberately absent — it is rendered by `auth.py` as a
# standalone page that reveals neither the navigation nor the existence of any figure.
ERROR_TEXT: dict[int, tuple[str, str]] = {
    400: (
        "Requête invalide",
        "Les paramètres de l'adresse sont incomplets ou mal formés.",
    ),
    403: (
        "Accès refusé",
        "Cette ressource n'est pas accessible depuis cette session.",
    ),
    404: (
        "Page introuvable",
        "Cette adresse ne correspond à aucune page du tableau de bord.",
    ),
    405: (
        "Lecture seule",
        "Le tableau de bord ne répond qu'à GET et HEAD. Aucune écriture n'est possible, "
        "même par erreur.",
    ),
    500: (
        "Erreur interne",
        "Le tableau de bord n'a pas pu afficher cette page.",
    ),
    503: (
        "Base de données indisponible",
        "Le tableau de bord n'a pas pu lire la base de données.",
    ),
}
# Where an error page may send the reader. Never a figure, never a mutation.
ERROR_LINKS: tuple[tuple[str, str], ...] = (
    ("Vue d'ensemble", "/"),
    ("Positions", "/positions"),
    ("Trades", "/trades"),
    ("Système", "/system"),
    ("Sonde de santé", "/healthz"),
)
ERROR_TEMPLATES = {404: "404.html", 500: "500.html", 503: "unavailable.html"}
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
# The dashboard is a reader: this flag is part of its contract, not a configuration knob.
READ_ONLY = True
# Optional: where the EA bridge writes its JSON reports. No secret, no default guess.
EA_REPORTS_ENV_VAR = "TRADINGAGENT_EA_REPORTS_DIR"

# (path, label) of the navigation bar, in reading order.
PAGES: tuple[tuple[str, str], ...] = (
    ("/", "Vue d'ensemble"),
    ("/positions", "Positions"),
    ("/trades", "Trades"),
    ("/scalping", "Scalping"),
    ("/strategies", "Stratégies"),
    ("/ai-lab", "Labo IA"),
    ("/risk", "Risque"),
    ("/system", "Système"),
    ("/reports", "Rapports"),
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(
    engine: Engine,
    *,
    interval_seconds: float = 2.0,
    now: Callable[[], datetime] | None = None,
    templates_dir: Path | None = None,
    agent_config_path: Path | None = None,
    ea_reports_dir: Path | None = None,
    stream_cycles: int | None = None,
    access_token: str | None = None,
) -> FastAPI:
    """Build the read-only dashboard around an already-migrated database engine.

    ``now`` is injected so every page is reproducible in a test; the production entry point
    passes the real UTC clock. ``ea_reports_dir`` points at the bridge's ``reports/``
    directory — when it is absent, the EA sections say so instead of inventing a status.
    ``access_token`` is the optional access protection of §43: ``None`` reads
    ``TRADINGAGENT_WEB_TOKEN`` from the environment, and an absent or blank variable leaves
    the dashboard exactly as it was — local, unauthenticated, read-only.
    """
    clock = now if now is not None else _utc_now
    limits_path = agent_config_path if agent_config_path is not None else AGENT_CONFIG
    reports_dir = ea_reports_dir if ea_reports_dir is not None else _env_reports_dir()
    credential = configured_token(access_token)
    access = None if credential is None else AccessControl(credential)
    templates = Jinja2Templates(directory=str(templates_dir or TEMPLATES_DIR))
    templates.env.globals.update(display=display, pages=PAGES, state_label=state_label)
    templates.env.filters["describe"] = describe

    app = FastAPI(
        title="TradingAgent — tableau de bord",
        description="Monitoring en lecture seule. Aucun ordre, aucune écriture (§34).",
        # The interactive docs are a builder's tool; on a monitoring host they are surface
        # with no reader. The routes stay discoverable through the navigation.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.read_only = READ_ONLY
    # StaticFiles answers GET and HEAD only, which keeps the dashboard's "nothing but
    # reads" promise true even for the brand mark.
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.state.engine = engine
    app.state.access_protected = access is not None

    stream = EventStream(engine, interval_seconds=interval_seconds, now=clock)

    def page(request: Request, name: str, **context: Any) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            f"{name}.html",
            {
                "request": request,
                "page": name,
                "render_at": clock(),
                # Real charts, drawn behind the content (§51). Read-only, and every page
                # still renders without them if the database holds nothing yet.
                "watermark": queries.watermark(engine),
                **context,
            },
        )

    if access is not None:

        @app.middleware("http")
        async def require_access(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            """Every page and the SSE feed sit behind the token (§43).

            The refusal is a standalone 401 with no navigation, no figure and no echo of
            what was presented: a request without a valid credential learns nothing about
            the dashboard. The token itself is never logged, here or anywhere else.
            """
            if not access.granted(request):
                return access.denial()
            return await call_next(request)

        @app.get("/session")
        def open_session() -> RedirectResponse:
            """Trade a valid bearer token for the signed session cookie.

            A browser cannot set a header on a top-level navigation, and ``EventSource``
            cannot set one at all, so the cookie exists for those two flows. It is minted
            only from an already-authorized request; the middleware has answered 401
            otherwise. The cookie carries ``HMAC-SHA256(token, …)``, never the token.
            """
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                SESSION_COOKIE,
                access.session_value,
                httponly=True,
                samesite="strict",
                path="/",
            )
            return response

    def error_response(
        status: int,
        request: Request,
        *,
        headline: str | None = None,
        explanation: str | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> HTMLResponse:
        """One shape for every failure the dashboard can answer itself.

        The error pages never read the database: that is the only way to be sure a database
        failure cannot turn into a rendering failure. They carry no watermark for the same
        reason — absence by construction, not by oversight.
        """
        default_headline, default_explanation = ERROR_TEXT.get(
            status, (f"Erreur {status}", "La requête n'a pas pu être servie.")
        )
        context: dict[str, Any] = {
            "request": request,
            "page": ERROR_TEMPLATES.get(status, "error"),
            "render_at": clock(),
            "status": status,
            "headline": headline or default_headline,
            "explanation": explanation or default_explanation,
            "links": ERROR_LINKS,
            "allow": (headers or {}).get("allow"),
        }
        context.update(extra or {})
        return templates.TemplateResponse(
            request,
            ERROR_TEMPLATES.get(status, "error.html"),
            context,
            status_code=status,
            headers=headers,
        )

    def wants_html(request: Request) -> bool:
        """Browsers get a page; a script that asked for JSON gets JSON.

        `*/*` counts as HTML: this is a web application, and a plain `curl` that forgot its
        Accept header is better served by the readable answer.
        """
        accept = request.headers.get("accept", "")
        return not ("application/json" in accept and "text/html" not in accept)

    @app.exception_handler(StarletteHTTPException)
    def http_error(request: Request, error: StarletteHTTPException) -> Response:
        """404, 405, 400… — answered in the dashboard's own voice."""
        headers = dict(error.headers or {})
        if not wants_html(request):
            return JSONResponse(
                {"detail": error.detail}, status_code=error.status_code, headers=headers
            )
        # A 405 must keep its `Allow`: dropping it would lie about the surface.
        extra = (
            # Echoing the path back is what makes a stale bookmark debuggable. It is
            # escaped by Jinja and truncated: the dashboard echoes nothing else, and the
            # 401 page echoes nothing at all, on purpose.
            {"requested": request.url.path[:120]} if error.status_code == 404 else None
        )
        return error_response(error.status_code, request, headers=headers, extra=extra)

    @app.exception_handler(Exception)
    def unexpected_error(request: Request, error: Exception) -> Response:
        """Last line of defence: an unhandled exception still renders something readable.

        The trace goes to the log, where it belongs; the page says nothing about internals.
        `SQLAlchemyError` never reaches here — it has its own handler, deeper in the stack.
        """
        log.exception("unhandled dashboard error on %s: %s", request.url.path, error)
        return error_response(500, request)

    @app.exception_handler(SQLAlchemyError)
    def database_error(request: Request, error: SQLAlchemyError) -> HTMLResponse:
        """A monitoring page must state the failure, not vanish behind a stack trace."""
        log.warning("database unavailable while rendering %s: %s", request.url.path, error)
        return error_response(503, request, extra={"reason": type(error).__name__})

    @app.get("/", response_class=HTMLResponse)
    def overview_page(request: Request) -> HTMLResponse:
        at = clock()
        data = queries.overview(engine, at, reports_dir)
        return page(
            request,
            "overview",
            data=data,
            health=queries.market_health(engine, at),
            ea_halted=queries.ea_halted(data.ea),
        )

    @app.get("/positions", response_class=HTMLResponse)
    def positions_page(
        request: Request,
        q: str | None = None,
        market: str | None = None,
        page_number: str | None = Query(default=None, alias="page"),
    ) -> HTMLResponse:
        """The positions history, ten rows at a time, one market at a time.

        Paging, searching and the market all happen in SQL (:func:`queries.position_page`):
        the table grows all year, and sending it whole to display ten rows is the defect this
        avoids. A page outside the bounds is clamped rather than refused, and a garbled one
        falls back to the first — a stale bookmark degrades, it does not raise. An unknown
        ``?market=`` falls back to the default market: a stale link must never be the thing
        that mixes two instruments in one table.
        """
        at = clock()
        markets = queries.available_markets(engine)
        positions = queries.position_page(
            engine,
            at,
            query=q or "",
            market=paging.resolve_market(market, markets),
            page=paging.page_number(page_number),
        )
        return page(
            request,
            "positions",
            at=at,
            positions=positions,
            markets=markets,
            open_count=len(queries.open_positions(engine, at, market=positions.market)),
            rows=[position_list_cells(position) for position in positions.rows],
            columns=POSITION_TABLE_COLUMNS,
            numeric=POSITION_NUMERIC_COLUMNS,
            alerts=queries.recent_events(engine, minimum=Severity.WARNING, limit=20),
            alert_columns=ALERT_COLUMNS,
            interval=stream.interval_seconds,
        )

    @app.get("/trades", response_class=HTMLResponse)
    def trades_page(
        request: Request,
        market: str | None = None,
        strategy: str | None = None,
        mode: str | None = None,
        q: str | None = None,
        page_number: str | None = Query(default=None, alias="page"),
        trade_id: int | None = None,
    ) -> HTMLResponse:
        """The closed trades, ten rows at a time, one market at a time.

        The listing is filtered and bounded in SQL (:func:`queries.trade_page`); the KPI
        cards describe the whole filtered selection, because a card computed from the ten
        visible rows would be a lie.
        """
        listing = queries.trade_page(
            engine,
            market=paging.resolve_market(market, queries.available_markets(engine)),
            strategy=strategy or "",
            mode=_mode(mode),
            query=q or "",
            page=paging.page_number(page_number),
        )
        return page(
            request,
            "trades",
            listing=listing,
            modes=list(TradingMode),
            detail=queries.trade_detail(engine, trade_id) if trade_id is not None else None,
        )

    @app.get("/trades/{trade_id}", response_class=HTMLResponse)
    def trade_replay_page(request: Request, trade_id: int) -> HTMLResponse:
        """The replay of one trade (§28), keyed on the signal that produced it.

        The signal id is the identity the trades list carries; a signal that never became a
        position still replays — its verdict is the answer to "why was this executed?".
        An id the database does not hold is a 404, never a broken page.
        """
        replay = queries.trade_replay(engine, trade_id)
        if replay is None:
            return templates.TemplateResponse(
                request,
                "trade_replay.html",
                {
                    "request": request,
                    "page": "trade_replay",
                    "render_at": clock(),
                    "trade_id": trade_id,
                    "replay": None,
                    "context": None,
                    "timeline": (),
                    "checks": (),
                },
                status_code=404,
            )
        return page(
            request,
            "trade_replay",
            trade_id=trade_id,
            replay=replay,
            # The market conditions at the signal's own moment: read-only, and every missing
            # value stays missing so the page can write `n/a` instead of inventing one (§28).
            context=queries.market_context(engine, replay),
            min_candles=queries.REPLAY_MIN_CANDLES,
            timeline=replay_timeline(replay),
            checks=replay_checks(replay),
        )

    @app.get("/scalping", response_class=HTMLResponse)
    def scalping_page(
        request: Request,
        market: str | None = None,
        page_number: str | None = Query(default=None, alias="page"),
    ) -> HTMLResponse:
        """The §32 cuts of one market.

        Every trade carries its symbol, so the market is *derivable* here and is filtered in
        SQL like everywhere else: the cuts, the costs and the latencies all describe the same
        instrument. The hourly cut is the only one that can outgrow a display, and it carries
        its page in the URL.
        """
        markets = queries.available_markets(engine)
        return page(
            request,
            "scalping",
            view=queries.scalping_view(
                engine,
                market=paging.resolve_market(market, markets),
                page=paging.page_number(page_number),
            ),
            markets=markets,
        )

    @app.get("/strategies", response_class=HTMLResponse)
    def strategies_page(
        request: Request,
        q: str | None = None,
        market: str | None = None,
        page_number: str | None = Query(default=None, alias="page"),
    ) -> HTMLResponse:
        """The registry and its evidence, ten rows at a time, one market at a time.

        ``strategy_registry`` carries the market, so the filter is mechanical and the count,
        the search and the page all happen in SQL.
        """
        markets = queries.strategy_markets(engine)
        return page(
            request,
            "strategies",
            listing=queries.strategy_page(
                engine,
                market=paging.resolve_market(market, markets),
                query=q or "",
                page=paging.page_number(page_number),
            ),
        )

    @app.get("/ai-lab", response_class=HTMLResponse)
    def ai_lab_page(
        request: Request,
        q: str | None = None,
        market: str | None = None,
        page_number: str | None = Query(default=None, alias="page"),
        proposals: str | None = None,
        validations: str | None = None,
        backtests: str | None = None,
    ) -> HTMLResponse:
        """The four lists of the laboratory, each with its own page and a shared market.

        Four tables carry a market column, so the filter is mechanical; each list is counted
        and bounded on its own, and its own query parameter keeps the other three still.
        """
        markets = queries.ai_markets(engine)
        return page(
            request,
            "ai_lab",
            lab=queries.ai_lab_pages(
                engine,
                market=paging.resolve_market(market, markets),
                query=q or "",
                analyses_page=paging.page_number(page_number),
                proposals_page=paging.page_number(proposals),
                validations_page=paging.page_number(validations),
                backtests_page=paging.page_number(backtests),
            ),
        )

    @app.get("/risk", response_class=HTMLResponse)
    def risk_page(
        request: Request,
        q: str | None = None,
        market: str | None = None,
        page_number: str | None = Query(default=None, alias="page"),
        hpage: str | None = None,
    ) -> HTMLResponse:
        """The risk state, and the two append-only logs it carries.

        ``system_events`` names the instrument of an event, or nothing when the event
        concerns the account as a whole: the selector shows one market's events plus those,
        and never another instrument's. ``halt_commands`` has no market column, so its
        history is paged and searched only.
        """
        data = queries.risk_view(
            engine,
            clock(),
            limits_path,
            reports_dir,
            market=paging.resolve_market(market, queries.event_markets(engine)),
            query=q or "",
            events_page=paging.page_number(page_number),
            history_page=paging.page_number(hpage),
        )
        return page(
            request,
            "risk",
            data=data,
            markets=queries.event_markets(engine),
            ea_halted=queries.ea_halted(data.ea),
        )

    @app.get("/system", response_class=HTMLResponse)
    def system_page(
        request: Request,
        q: str | None = None,
        market: str | None = None,
        page_number: str | None = Query(default=None, alias="page"),
        epage: str | None = None,
    ) -> HTMLResponse:
        """The health page: the probes, the paged logs, and a bounded EA journal.

        The market selector scopes the two logs (telemetry and errors); the probes above
        stay whole, because a health page that hid a market would hide the failure it exists
        to report. ``execution_events`` always names its instrument; the account-wide events
        of ``system_events`` stay visible whatever is selected. The EA journal comes from the
        bridge's JSON reports rather than from a table: it is bounded to one display and
        says how many lines were read.
        """
        data = queries.system_view(
            engine,
            clock(),
            ea_reports_dir=reports_dir,
            market=paging.resolve_market(market, queries.event_markets(engine)),
            query=q or "",
            page=paging.page_number(page_number),
            errors_page=paging.page_number(epage),
        )
        return page(
            request,
            "system",
            data=data,
            markets=queries.event_markets(engine),
            ea_halted=queries.ea_halted(data.ea),
        )

    @app.get("/reports", response_class=HTMLResponse)
    def reports_page(
        request: Request,
        q: str | None = None,
        report_id: int | None = None,
        page_number: str | None = Query(default=None, alias="page"),
    ) -> HTMLResponse:
        """The stored reports, ten at a time, searched in SQL.

        A report row carries no market: the period, the window and the text. There is
        therefore no market selector here — one that filtered nothing would be worse than no
        selector at all — and the page states what it does hold.
        """
        stored = queries.report_page(engine, query=q or "", page=paging.page_number(page_number))
        opened = (
            queries.report_by_id(engine, report_id)
            if report_id is not None
            else (stored.rows[0] if stored.rows else None)
        )
        return page(request, "reports", reports=stored, opened=opened)

    @app.get("/events")
    def events(
        market: str | None = None,
        cycles: int | None = Query(
            default=None,
            ge=1,
            le=100,
            description="Borne le flux à N cycles puis ferme ; absent, il reste ouvert.",
        ),
    ) -> StreamingResponse:
        """The live feed: the open positions of one market, and warnings, polled from the DB.

        The market is the page's own parameter, resolved against the markets the database
        holds: the count pushed by the stream and the table rendered by the page describe the
        same market, or they would contradict each other.
        """
        selected = paging.resolve_market(market, queries.available_markets(engine))
        return StreamingResponse(
            stream.frames(cycles=stream_cycles if cycles is None else cycles, market=selected),
            media_type="text/event-stream",
            headers=dict(SSE_HEADERS),
        )

    @app.get("/export/trades.csv", response_class=PlainTextResponse)
    def export_trades(
        market: str | None = None, strategy: str | None = None, mode: str | None = None
    ) -> PlainTextResponse:
        trades = _filtered(engine, market, strategy, mode)
        return _download(export_trades_csv(trades), "text/csv", f"trades-{_stamp(clock())}.csv")

    @app.get("/export/trades.json", response_class=PlainTextResponse)
    def export_trades_json_route(
        market: str | None = None, strategy: str | None = None, mode: str | None = None
    ) -> PlainTextResponse:
        trades = _filtered(engine, market, strategy, mode)
        return _download(
            export_trades_json(trades), "application/json", f"trades-{_stamp(clock())}.json"
        )

    @app.get("/export/performance.json", response_class=PlainTextResponse)
    def export_performance() -> PlainTextResponse:
        trades = queries.all_trades(engine)
        return _download(
            export_performance_json(compute_performance(trades)),
            "application/json",
            f"performance-{_stamp(clock())}.json",
        )

    @app.get("/export/equity.svg")
    def export_equity() -> Response:
        """The chart drawn by the reporting package, served as-is (§34, EF-028)."""
        trades = queries.all_trades(engine)
        return Response(
            content=equity_and_drawdown_svg(trades, "Courbe d'équité et drawdown"),
            media_type="image/svg+xml",
            headers={"Content-Disposition": 'inline; filename="equity.svg"'},
        )

    @app.get("/export/reports/{report_id}.txt", response_class=PlainTextResponse)
    def export_report(report_id: int) -> PlainTextResponse:
        stored = queries.report_by_id(engine, report_id)
        if stored is None:
            return PlainTextResponse("rapport inconnu", status_code=404)
        return _download(stored.content, "text/plain; charset=utf-8", f"rapport-{stored.id}.txt")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        """Liveness for a supervisor: database reachable, nothing else invented."""
        database = queries.database_health(engine)
        halt = queries.halt_status(engine)
        return {
            "status": "ok" if database.ok else "degraded",
            "read_only": READ_ONLY,
            "database": {"ok": database.ok, "dialect": database.dialect},
            "halted": halt.halted,
        }

    return app


def _filtered(
    engine: Engine, market: str | None, strategy: str | None, mode: str | None
) -> list[Trade]:
    filters = queries.TradeFilters(
        market=market or None, strategy=strategy or None, mode=_mode(mode)
    )
    return filters.apply(queries.all_trades(engine))


def _mode(value: str | None) -> TradingMode | None:
    """A stale bookmark must degrade to "no filter", not to a 422."""
    if not value:
        return None
    try:
        return TradingMode(value)
    except ValueError:
        return None


def _stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y%m%d-%H%M%S")


def _env_reports_dir() -> Path | None:
    """Where the EA bridge writes its reports, when the operator declared it.

    The variable is optional and carries no secret: without it the EA sections state that
    no bridge is configured rather than showing an empty table as if it were a status.
    """
    raw = os.environ.get(EA_REPORTS_ENV_VAR, "").strip()
    return Path(raw) if raw else None


#: The name the bridge gives its reports directory, and the suffix of one report file.
_REPORTS_DIR_NAME = "reports"
_REPORT_SUFFIX = "_report.json"


def _is_reports_directory(path: Path) -> bool:
    if not path.is_dir():
        return False
    if path.name == _REPORTS_DIR_NAME:
        return True
    try:
        return next(path.glob(f"*{_REPORT_SUFFIX}"), None) is not None
    except OSError:
        return False


def _bridge_reports(declared: Path) -> Path | None:
    """`reports/` under a declared bridge root, or the declared path when it already is one.

    Both spellings are accepted on purpose. `EA_FILES_DIR` names the bridge root, while
    `TRADINGAGENT_EA_REPORTS_DIR` has always been documented as the reports directory
    itself, and an operator who configured the latter must not be broken by a fallback
    added for the former. A path that resolves nowhere returns `None`: the dashboard then
    says the bridge is absent instead of showing an empty table that reads like a status.
    """
    if _is_reports_directory(declared):
        return declared
    candidate = declared / _REPORTS_DIR_NAME
    return candidate if candidate.is_dir() else None


def _declared_bridge_root(env_file: Path | None) -> Path | None:
    """`EA_FILES_DIR` from `.env`, read without requiring any broker credential.

    Read with `dotenv_values` rather than `load_settings` because `tradingagent-web` must
    start on a machine without MT5 secrets: reading a path is not trading. This is the same
    distinction `DatabaseSettings` already makes for the database URL.
    """
    if env_file is None or not env_file.is_file():
        return None
    raw = (dotenv_values(env_file).get("EA_FILES_DIR") or "").strip()
    return Path(raw) if raw else None


def resolve_ea_reports_dir(
    explicit: Path | None = None, *, env_file: Path | None = None
) -> Path | None:
    """The bridge's reports directory, from the argument, the environment, or `.env`.

    The operator declares the bridge once, in `EA_FILES_DIR`, and the agent already reads it
    there. The dashboard demanded the same information a second time through another
    variable, and on 2026-10-08 it was missing exactly that second copy: the page said "no
    bridge configured" while the Expert Advisor carried a local halt, so the one screen that
    exists to reveal a stopped EA was the screen that could not see it.

    An explicit `--ea-reports-dir` still wins and is trusted as given: an operator pointing
    at a directory is making a statement about it, and the dashboard still reports an empty
    read honestly.
    """
    if explicit is not None:
        return explicit
    from_env = _env_reports_dir()
    if from_env is not None:
        return _bridge_reports(from_env)
    declared = _declared_bridge_root(env_file)
    return None if declared is None else _bridge_reports(declared)


def _download(content: str, media_type: str, filename: str) -> PlainTextResponse:
    return PlainTextResponse(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tradingagent-web", description="Tableau de bord de monitoring (lecture seule)"
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="adresse d'écoute (locale par défaut)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port d'écoute")
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="intervalle de rafraîchissement SSE, en secondes",
    )
    parser.add_argument(
        "--ea-reports-dir",
        default=None,
        help=f"répertoire des rapports EA (défaut : ${EA_REPORTS_ENV_VAR})",
    )
    parser.add_argument("--log-level", default="info", help="niveau de journalisation uvicorn")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of ``uv run tradingagent-web``. Reads the database, never the broker."""
    args = _parse(argv)
    try:
        settings = load_database_settings(ENV_FILE)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    engine = create_database_engine(settings.database_url.get_secret_value())
    reports_dir = resolve_ea_reports_dir(
        Path(args.ea_reports_dir) if args.ea_reports_dir else None, env_file=ENV_FILE
    )
    # The token comes from the same `.env` as everything else. Reading it from `os.environ`
    # alone would have left the dashboard unprotected while the operator believed otherwise:
    # pydantic-settings parses `.env` into a model, it does not export it to the process.
    declared = settings.tradingagent_web_token
    access_token = declared.get_secret_value() if declared is not None else None
    try:
        uvicorn.run(
            create_app(
                engine,
                interval_seconds=args.interval,
                ea_reports_dir=reports_dir,
                access_token=access_token,
            ),
            host=args.host,
            port=args.port,
            log_level=args.log_level,
        )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ACCESS_ENV_VAR",
    "ALERT_COLUMNS",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "EA_REPORTS_ENV_VAR",
    "PAGES",
    "POSITION_COLUMNS",
    "READ_ONLY",
    "SESSION_COOKIE",
    "create_app",
    "main",
]
