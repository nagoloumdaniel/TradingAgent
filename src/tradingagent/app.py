"""Composition root: the only place that picks concrete implementations and wires them.

It is also the one module besides `risk` allowed to import `execution` (the architecture
test enforces it). Everything here is construction and lifecycle: build the terminal, the
market client, the strategy catalog, the risk engine, the executor, the Telegram surface
and the agent loop, then run the loop until it is stopped.

Run it with:

    uv run tradingagent-run              # continuous
    uv run tradingagent-run --once       # exactly one cycle, for verification
    uv run tradingagent-run --cycles 3   # a bounded number of cycles
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import truststore
from sqlalchemy import Engine
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes, MessageHandler, filters

from tradingagent.ai.anthropic_client import AnthropicClient
from tradingagent.ai.layer import AiFilterLayer
from tradingagent.config._yaml import read_yaml
from tradingagent.config.agent import AgentConfig, load_agent_config
from tradingagent.config.errors import ConfigError
from tradingagent.config.settings import Settings, load_settings
from tradingagent.config.strategy_catalog import StrategyCatalog
from tradingagent.control.guardian import Guardian
from tradingagent.control.quarantine import PersistentQuarantine
from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.states import StrategyStatus
from tradingagent.data.history import HistorySync
from tradingagent.data.market_calendar import MarketCalendar
from tradingagent.data.market_data import MarketDataClient, Subscription
from tradingagent.data.mt5_terminal import Mt5Terminal
from tradingagent.execution import MT5Broker, PaperBroker, PositionTracker, TradeLog
from tradingagent.notify.access import AccessGate
from tradingagent.notify.commands import CommandRouter, status_handler
from tradingagent.notify.health_alerts import HealthAlerter
from tradingagent.notify.read_commands import (
    markets_handler,
    performance_handler,
    positions_handler,
    report_handler,
    signals_handler,
)
from tradingagent.notify.sensitive_commands import (
    close_all_handler,
    disable_handler,
    emergency_stop_handler,
    enable_handler,
    mode_handler,
    pause_handler,
    resume_handler,
)
from tradingagent.notify.service import CommandService
from tradingagent.observability import Metrics, ResourceMonitor, configure_json_logging
from tradingagent.registry.store import StrategyRegistry, UnknownStrategyRef
from tradingagent.reporting.service import ReportService
from tradingagent.risk.model import AccountState, InstrumentSpec
from tradingagent.runtime.loop import AgentLoop
from tradingagent.runtime.pipeline import SignalPipeline
from tradingagent.runtime.portfolio import PortfolioBuilder
from tradingagent.signals.generator import SignalGenerator
from tradingagent.storage.ai_calls import AiCallStore
from tradingagent.storage.audit import AuditStore
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.signals import SignalRepository
from tradingagent.strategies.registry import REGISTRY

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
AGENT_CONFIG = ROOT / "config" / "agent.yaml"
STRATEGY_DIR = ROOT / "config" / "strategies"
# Chosen in TASK-037 and to confirm with the operator (Q-18): the model is never
# authoritative, so a wrong pick costs money, never correctness.
DEFAULT_MODEL = "claude-sonnet-4-5"
PAPER_STARTING_CAPITAL = Decimal(1000)
# The demo server is measured at UTC with no daylight saving (TASK-003); the live server
# is not, and `verify_clock` stops the agent the moment the offset differs.
DEMO_SERVER_OFFSET = timedelta(0)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class TelegramNotifier:
    """Best-effort sender: a Telegram outage must never stop the engine (F-013)."""

    def __init__(self, application: Application, chat_id: int) -> None:
        self._application = application
        self._chat_id = chat_id

    async def send(self, text: str, *, parse_mode: str | None = None) -> bool:
        try:
            await self._application.bot.send_message(
                chat_id=self._chat_id, text=text[:4096], parse_mode=parse_mode
            )
            return True
        except Exception as error:  # network, revoked token, blocked bot
            log.warning("Telegram send failed: %s", type(error).__name__)
            return False


class SilentNotifier:
    """Used when no bot is wired, so the pipeline keeps a single code path."""

    async def send(self, text: str, *, parse_mode: str | None = None) -> bool:
        del text, parse_mode
        return True


@dataclass
class Components:
    engine: Engine
    market: MarketDataClient
    loop: AgentLoop
    application: Application | None
    notifier: Any
    metrics: Metrics
    resources: ResourceMonitor
    alerts: HealthAlerter


def _declared_symbols() -> list[str]:
    """The raw symbols of agent.yaml, before validation: they are what we verify."""
    document = read_yaml(AGENT_CONFIG)
    markets = document.data.get("markets", [])
    return [str(market["symbol"]) for market in markets if isinstance(market, dict)]


def _subscriptions(config: AgentConfig, catalog: StrategyCatalog) -> tuple[Subscription, ...]:
    loaded = catalog.current()
    subscriptions: list[Subscription] = []
    for market in config.markets:
        if not market.enabled:
            continue
        manifest = loaded[market.strategy].manifest
        subscriptions += [
            Subscription(market.symbol, timeframe) for timeframe in manifest.timeframes
        ]
    return tuple(subscriptions)


async def _known_symbols(terminal: Mt5Terminal, declared: Sequence[str]) -> set[str]:
    """Verify each configured symbol against the terminal before trusting the config.

    `symbol_spec` returns None for a symbol this account cannot trade: a typo or a symbol
    missing from the account stops the start-up instead of silently narrowing the scope.
    """
    verified: set[str] = set()
    unknown: list[str] = []
    for symbol in declared:
        spec = await asyncio.to_thread(terminal.symbol_spec, symbol)
        if spec is None:
            unknown.append(symbol)
        else:
            verified.add(symbol)
    if unknown:
        raise ConfigError(
            "these configured symbols are not offered by this account: "
            + ", ".join(sorted(unknown))
        )
    return verified


def _instrument(spec: Any, symbol: str) -> InstrumentSpec:
    return InstrumentSpec(
        symbol=symbol,
        contract_size=Decimal(str(spec.contract_size)),
        volume_min=Decimal(str(spec.volume_min)),
        volume_step=Decimal(str(spec.volume_step)),
        volume_max=Decimal(str(spec.volume_max)),
        point=Decimal(str(spec.point)),
        stops_level=int(spec.stops_level),
    )


def _build_command_service(
    engine: Engine,
    settings: Settings,
    halts: HaltStore,
    candles: CandleStore,
    markets: Sequence[tuple[str, bool]],
    now: Any,
) -> CommandService:
    router = CommandRouter()
    router.register(
        "status",
        "mode, arrêt d'urgence et quarantaines",
        status_handler(halts, settings.trading_mode, markets, candles),
    )
    router.register(
        "markets", "marchés suivis et fraîcheur des données", markets_handler(markets, candles)
    )
    router.register("signals", "derniers signaux", signals_handler(engine))
    router.register("positions", "positions ouvertes", positions_handler(engine))
    router.register("performance", "trades clôturés", performance_handler(engine))
    router.register("report", "rapport à la demande", report_handler(engine, now))
    router.register("pause", "suspend les ordres", pause_handler(halts))
    router.register("resume", "reprend les ordres", resume_handler(halts))
    router.register("close_all", "ferme les positions", close_all_handler(halts))
    router.register("emergency_stop", "arrêt d'urgence", emergency_stop_handler(halts))
    router.register("disable", "désactive un marché", disable_handler(halts))
    router.register("enable", "réactive un marché", enable_handler(halts))
    router.register("mode", "change le mode", mode_handler(engine))
    return CommandService(
        AccessGate(settings.telegram_allowed_user_ids), router, AuditStore(engine)
    )


def _build_telegram(settings: Settings, service: CommandService) -> Application:
    application = ApplicationBuilder().token(settings.telegram_bot_token.get_secret_value()).build()

    async def on_message(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        message, user, chat = update.effective_message, update.effective_user, update.effective_chat
        if message is None or user is None or chat is None or not message.text:
            return
        reply = await service.handle(user.id, chat.type == chat.PRIVATE, message.text)
        if reply:
            await message.reply_text(reply)

    application.add_handler(MessageHandler(filters.TEXT, on_message))
    return application


def _build_ai(engine: Engine, settings: Settings) -> AiFilterLayer | None:
    key = settings.anthropic_api_key
    if key is None:
        log.warning("no ANTHROPIC_API_KEY: the AI filter is disabled (RM-011, shadow only)")
        return None
    client = AnthropicClient(key.get_secret_value(), DEFAULT_MODEL)
    return AiFilterLayer(
        client,
        AiCallStore(engine),
        SystemEventStore(engine),
        model=DEFAULT_MODEL,
        ai_filter=AiFilter.SHADOW,
    )


async def build(settings: Settings) -> Components:
    """Construct every component and warm the agent up. Never trades before returning."""
    secrets = settings.secret_values()
    configure_json_logging(logging.INFO, secrets)
    metrics = Metrics()
    resources = ResourceMonitor(ROOT)

    database_url = settings.database_url.get_secret_value()
    upgrade(database_url)
    engine = create_database_engine(database_url)
    events = SystemEventStore(engine)
    halts = HaltStore(engine)
    candles = CandleStore(engine)
    signals = SignalRepository(engine)

    now = _utc_now
    catalog = StrategyCatalog(STRATEGY_DIR, REGISTRY)
    terminal = Mt5Terminal()
    mode = settings.trading_mode
    market = MarketDataClient(
        terminal,
        _credentials(settings),
        mode,
        expected_server_offset=DEMO_SERVER_OFFSET,
        clock_probe_symbol="BTCUSD",
    )
    account_snapshot = await market.connect()
    if account_snapshot.currency != "EUR":
        raise ConfigError(f"the account is in {account_snapshot.currency}, every amount is in EUR")

    verified = await _known_symbols(terminal, _declared_symbols())
    config = load_agent_config(
        AGENT_CONFIG,
        known_symbols=verified,
        strategies={ref: loaded.manifest for ref, loaded in catalog.current().items()},
        mode=mode,
    )
    subscriptions = _subscriptions(config, catalog)
    if not subscriptions:
        raise ConfigError("no enabled market in agent.yaml: nothing to watch")
    _register_configured_strategies(engine, config, now)

    tracker = PositionTracker(engine, now=now)
    broker = await _build_broker(terminal, tracker, settings, config, mode, halts)
    await broker.initialize()

    calendars: dict[str, MarketCalendar] = {}
    notifier: Any
    application: Application | None = None
    if mode is TradingMode.OBSERVATION:
        notifier = SilentNotifier()
    else:
        service = _build_command_service(
            engine,
            settings,
            halts,
            candles,
            tuple((market_.symbol, market_.enabled) for market_ in config.markets),
            now,
        )
        application = _build_telegram(settings, service)
        operator_id = settings.telegram_allowed_user_ids[0]
        notifier = TelegramNotifier(application, operator_id)

    async def alert_sender(text: str) -> None:
        await notifier.send(text)

    alerts = HealthAlerter(alert_sender, events, disk_path=ROOT)
    guardian = Guardian(halts, now=now)
    report_service = ReportService(engine, sender=alert_sender)

    generator = SignalGenerator(
        catalog.current().values(),
        candles,
        signals,
        mode,
        quarantine=PersistentQuarantine(halts, now=now),
    )
    history = HistorySync(market, candles, now=now)
    pipeline = SignalPipeline(
        engine=engine,
        halts=halts,
        broker=broker,
        notifier=notifier,
        portfolio=PortfolioBuilder(engine),
        risk_config=config.risk,
        expected_login=settings.mt5_login,
        mode=mode,
        calendar_for=calendars.get,
        ai=_build_ai(engine, settings),
        now=now,
    )
    loop = AgentLoop(
        engine=engine,
        market=market,
        store=candles,
        history=history,
        generator=generator,
        pipeline=pipeline,
        broker=broker,
        halts=halts,
        notifier=notifier,
        guardian=guardian,
        alerts=alerts,
        reports=report_service,
        portfolio=PortfolioBuilder(engine),
        config=config,
        mode=mode,
        expected_login=settings.mt5_login,
        subscriptions=subscriptions,
        calendars=calendars,
        now=now,
    )
    del account_snapshot
    return Components(
        engine=engine,
        market=market,
        loop=loop,
        application=application,
        notifier=notifier,
        metrics=metrics,
        resources=resources,
        alerts=alerts,
    )


def _register_configured_strategies(engine: Engine, config: AgentConfig, now: Any) -> None:
    """Make every configured market visible to the registry and refuse a deprecated ref.

    The registry is the deployment record (cahier v3 §14). A market whose configured
    strategy was deprecated must not silently keep trading on it: the start-up stops and
    says which ref to replace.
    """
    registry = StrategyRegistry(engine, clock=now)
    deprecated: list[str] = []
    for market in config.markets:
        if not market.enabled:
            continue
        try:
            entry = registry.get(market.symbol, market.strategy)
        except UnknownStrategyRef:
            registry.register(
                market.symbol,
                market.strategy,
                origin="human",
                parameters={"source": "config/agent.yaml"},
            )
            continue
        if entry.status is StrategyStatus.DEPRECATED:
            deprecated.append(f"{market.strategy} on {market.symbol}")
    if deprecated:
        raise ConfigError(
            "these configured strategies are deprecated in the registry: "
            + ", ".join(sorted(deprecated))
            + ". Point agent.yaml at a promoted version."
        )


async def _build_broker(
    terminal: Mt5Terminal,
    log_: TradeLog,
    settings: Settings,
    config: AgentConfig,
    mode: TradingMode,
    halts: HaltStore,
) -> Any:
    if mode is TradingMode.PAPER:
        specs = {}
        for market in config.markets:
            raw = await asyncio.to_thread(terminal.symbol_spec, market.symbol)
            if raw is not None:
                specs[market.symbol] = _instrument(raw, market.symbol)
        account = AccountState(
            settings.mt5_login,
            True,
            "EUR",
            PAPER_STARTING_CAPITAL,
            PAPER_STARTING_CAPITAL,
        )
        return PaperBroker(
            terminal,
            log_,
            specs=specs,
            account=account,
            mode=TradingMode.PAPER,
            status=halts.status,
            alert=lambda message: log.critical("paper: %s", message),
        )
    return MT5Broker(
        terminal,
        log_,
        login=settings.mt5_login,
        mode=mode,
        status=halts.status,
        alert=lambda message: log.critical("broker: %s", message),
    )


def _credentials(settings: Settings) -> Any:
    from tradingagent.data.terminal import Credentials

    return Credentials(
        login=settings.mt5_login,
        password=settings.mt5_password.get_secret_value(),
        server=settings.mt5_server,
        terminal_path=settings.mt5_terminal_path,
    )


async def run(settings: Settings, *, cycles: int | None = None) -> int:
    components = await build(settings)
    loop = components.loop
    application = components.application
    try:
        selected = await loop.startup()
        log.info(
            "agent started in %s mode on %s",
            settings.trading_mode,
            ", ".join(sorted(selected)),
        )
        if application is not None:
            await application.initialize()
            await application.start()
            if application.updater is not None:
                await application.updater.start_polling(allowed_updates=[Update.MESSAGE])
        if cycles is not None:
            for _ in range(cycles):
                report = await loop.run_once()
                log.info(
                    "cycle: %d publication(s), %d signal(s), %d close(s), %d divergence(s)",
                    report.publications,
                    report.signals,
                    report.closed_positions,
                    report.divergences,
                )
        else:
            await loop.run_forever()
        return 0
    finally:
        if application is not None:
            if application.updater is not None:
                await application.updater.stop()
            await application.stop()
            await application.shutdown()
        await components.market.close()
        components.engine.dispose()


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="tradingagent-run", description="Trading agent runtime")
    parser.add_argument("--once", action="store_true", help="run exactly one cycle and exit")
    parser.add_argument("--cycles", type=int, default=None, help="run a bounded number of cycles")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    truststore.inject_into_ssl()
    try:
        settings = load_settings(ENV_FILE)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    cycles = 1 if args.once else args.cycles
    try:
        return asyncio.run(run(settings, cycles=cycles))
    except KeyboardInterrupt:
        log.info("interrupted, shutting down")
        return 0
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
