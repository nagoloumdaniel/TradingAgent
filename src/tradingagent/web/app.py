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
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

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
from tradingagent.web import queries
from tradingagent.web.auth import ACCESS_ENV_VAR, SESSION_COOKIE, AccessControl, configured_token
from tradingagent.web.sse import SSE_HEADERS, EventStream
from tradingagent.web.views import (
    ALERT_COLUMNS,
    POSITION_COLUMNS,
    describe,
    position_cells,
    replay_checks,
    replay_timeline,
    state_label,
)

ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = ROOT / ".env"
AGENT_CONFIG = ROOT / "config" / "agent.yaml"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
# Served read-only. Its only file is the brand mark: a local mount means the browser fetches
# it once and the dashboard keeps working with no CDN and no network.
STATIC_DIR = Path(__file__).resolve().parent / "static"
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

    @app.exception_handler(SQLAlchemyError)
    def database_error(request: Request, error: SQLAlchemyError) -> HTMLResponse:
        """A monitoring page must state the failure, not vanish behind a stack trace."""
        return templates.TemplateResponse(
            request,
            "unavailable.html",
            {
                "request": request,
                "page": "unavailable",
                "render_at": clock(),
                "reason": type(error).__name__,
            },
            status_code=503,
        )

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
    def positions_page(request: Request) -> HTMLResponse:
        at = clock()
        positions = queries.open_positions(engine, at)
        return page(
            request,
            "positions",
            at=at,
            positions=positions,
            rows=[position_cells(position) for position in positions],
            columns=POSITION_COLUMNS,
            alerts=queries.recent_events(engine, minimum=Severity.WARNING, limit=20),
            interval=stream.interval_seconds,
        )

    @app.get("/trades", response_class=HTMLResponse)
    def trades_page(
        request: Request,
        market: str | None = None,
        strategy: str | None = None,
        mode: str | None = None,
        trade_id: int | None = None,
    ) -> HTMLResponse:
        entries = queries.all_trade_entries(engine)
        filters = queries.TradeFilters(
            market=market or None, strategy=strategy or None, mode=_mode(mode)
        )
        selected = filters.apply_entries(entries)
        return page(
            request,
            "trades",
            entries=selected,
            total_trades=len(entries),
            performance=compute_performance([entry.trade for entry in selected]),
            markets=queries.selectable_markets(entry.trade for entry in entries),
            strategies=queries.selectable_strategies(entry.trade for entry in entries),
            modes=list(TradingMode),
            filters=filters,
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
            timeline=replay_timeline(replay),
            checks=replay_checks(replay),
        )

    @app.get("/scalping", response_class=HTMLResponse)
    def scalping_page(request: Request) -> HTMLResponse:
        return page(request, "scalping", view=queries.scalping_view(engine))

    @app.get("/strategies", response_class=HTMLResponse)
    def strategies_page(request: Request) -> HTMLResponse:
        return page(request, "strategies", views=queries.strategies(engine))

    @app.get("/ai-lab", response_class=HTMLResponse)
    def ai_lab_page(request: Request) -> HTMLResponse:
        return page(request, "ai_lab", lab=queries.ai_lab(engine))

    @app.get("/risk", response_class=HTMLResponse)
    def risk_page(request: Request) -> HTMLResponse:
        data = queries.risk_view(engine, clock(), limits_path, reports_dir)
        return page(request, "risk", data=data, ea_halted=queries.ea_halted(data.ea))

    @app.get("/system", response_class=HTMLResponse)
    def system_page(request: Request) -> HTMLResponse:
        data = queries.system_view(engine, clock(), ea_reports_dir=reports_dir)
        return page(request, "system", data=data, ea_halted=queries.ea_halted(data.ea))

    @app.get("/reports", response_class=HTMLResponse)
    def reports_page(request: Request, report_id: int | None = None) -> HTMLResponse:
        stored = queries.reports(engine)
        opened = (
            queries.report_by_id(engine, report_id)
            if report_id is not None
            else (stored[0] if stored else None)
        )
        return page(request, "reports", reports=stored, opened=opened)

    @app.get("/events")
    def events(
        cycles: int | None = Query(
            default=None,
            ge=1,
            le=100,
            description="Borne le flux à N cycles puis ferme ; absent, il reste ouvert.",
        ),
    ) -> StreamingResponse:
        """The live feed: open positions and warnings, polled from the database."""
        return StreamingResponse(
            stream.frames(cycles=stream_cycles if cycles is None else cycles),
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
    reports_dir = Path(args.ea_reports_dir) if args.ea_reports_dir else None
    try:
        uvicorn.run(
            create_app(engine, interval_seconds=args.interval, ea_reports_dir=reports_dir),
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
