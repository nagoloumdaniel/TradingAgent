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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import truststore
from sqlalchemy import Engine
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes, MessageHandler, filters

from tradingagent.ai.analyst import TradeAnalyst
from tradingagent.ai.daily import DailyLab
from tradingagent.ai.escalation import Escalation
from tradingagent.ai.evidence import MarketEvidence, baseline_of, evidence_for, performance_metrics
from tradingagent.ai.improvement_cycle import (
    CycleOutcome,
    ImprovementCycle,
    Variant,
    propose_from_lab,
)
from tradingagent.ai.improvement_cycle import (
    Measurement as CycleMeasurement,
)
from tradingagent.ai.lab_store import LabStore
from tradingagent.ai.layer import AiFilterLayer
from tradingagent.ai.model_client import ModelClient
from tradingagent.ai.provider import ModelTarget, resolve_target
from tradingagent.ai.researcher import StrategyResearcher
from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import CandleDataset, DatasetStore
from tradingagent.backtest.harness import BacktestConfig
from tradingagent.config._yaml import read_yaml
from tradingagent.config.agent import AgentConfig, load_agent_config
from tradingagent.config.errors import ConfigError
from tradingagent.config.settings import Settings, load_settings
from tradingagent.config.strategy_catalog import StrategyCatalog
from tradingagent.console import (
    ConsoleNotifier,
    configure_console_logging,
    enable_ansi,
    use_utf8_console,
)
from tradingagent.control.guardian import Guardian
from tradingagent.control.quarantine import PersistentQuarantine
from tradingagent.core.improvement import Candidate, Measurement
from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.states import StrategyStatus
from tradingagent.data.history import HistorySync
from tradingagent.data.market_calendar import MarketCalendar
from tradingagent.data.market_data import MarketDataClient, Subscription
from tradingagent.data.mt5_terminal import Mt5Terminal
from tradingagent.data.terminal import MAGIC as BROKER_MAGIC
from tradingagent.execution import MT5Broker, PaperBroker, PositionTracker, TradeLog
from tradingagent.notify.access import AccessGate
from tradingagent.notify.commands import CommandRouter, status_handler
from tradingagent.notify.health_alerts import HealthAlerter
from tradingagent.notify.read_commands import (
    gates_handler,
    market_handler,
    markets_handler,
    performance_handler,
    positions_handler,
    proposals_handler,
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
from tradingagent.research.campaign import CandidateReport, CandidateSpec, run_campaign
from tradingagent.research.improvement import ImprovementOutcome, search_improvement
from tradingagent.research.versioning import CandidateVersion, build_candidate, write_candidate
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
from tradingagent.strategies.manifest import StrategyManifest
from tradingagent.strategies.registry import REGISTRY

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
AGENT_CONFIG = ROOT / "config" / "agent.yaml"
STRATEGY_DIR = ROOT / "config" / "strategies"
PAPER_STARTING_CAPITAL = Decimal(1000)
# Section 14: the ladder gates the mode, so a strategy cannot trade a rung it never climbed.
# OBSERVATION and SIGNAL are absent on purpose: they execute nothing, and that is where a
# candidate earns its evidence.
#
# PAPER and DEMO ask for the same rung, and that is deliberate. Both are venues without real
# money; what separates them is the fill — simulated against a demo account, real against a
# live account. Requiring `live` for DEMO, as an earlier version did, inverted the
# environment ladder of §44 (DEV → BACKTEST → PAPER → DEMO → LIVE_SMALL → PROD): it made the
# rehearsal available only once the decision it exists to inform had already been taken.
# LIVE keeps the top rung: real money is the one thing that must never be spent on a
# version nobody validated.
MODE_REQUIRED_STATUS: dict[TradingMode, frozenset[StrategyStatus]] = {
    TradingMode.PAPER: frozenset(
        {StrategyStatus.PAPER, StrategyStatus.CANDIDATE, StrategyStatus.LIVE}
    ),
    TradingMode.DEMO: frozenset(
        {StrategyStatus.PAPER, StrategyStatus.CANDIDATE, StrategyStatus.LIVE}
    ),
    TradingMode.LIVE: frozenset({StrategyStatus.LIVE}),
}
# The demo server is measured at UTC with no daylight saving (TASK-003); the live server
# is not, and `verify_clock` stops the agent the moment the offset differs.
DEMO_SERVER_OFFSET = timedelta(0)
# What the daily improvement chain measures on, and where an accepted candidate is written.
# Both are the research directories the campaign scripts already use: the daily pass replays
# what was measured, it never fetches anything, and a candidate never lands anywhere the
# agent loads from (`research.versioning.write_candidate` refuses `config/strategies/`).
DATASET_DIR = ROOT / "docs" / "research" / "datasets"
CANDIDATE_DIR = ROOT / "docs" / "research" / "candidates"
# What a comparable measurement needs the recorded run to have written down (F-025, EF-026).
COST_KEYS = (
    "spread",
    "slippage_atr_fraction",
    "slippage_fixed",
    "commission_per_trade",
    "execution_delay_bars",
    "multiplier",
)


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
    calendars: Mapping[str, MarketCalendar] | None = None,
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
    # The per-market control centre: one market per answer, never a blended view. The
    # calendars mapping is shared with the agent loop, which fills it in place, so the
    # answers follow the learned trading hours without being rebuilt at every command.
    live_calendars = calendars if calendars is not None else {}
    router.register(
        "marche",
        "état d'un marché : position, haltes, calendrier",
        market_handler(markets, candles, halts, engine, calendar_for=live_calendars.get),
    )
    router.register(
        "propositions",
        "propositions de l'IA et leur décision",
        proposals_handler(engine, markets=markets),
    )
    router.register(
        "portes",
        "portes de promotion manquantes",
        gates_handler(engine, markets=markets),
    )
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


def _model_target(settings: Settings) -> ModelTarget | None:
    """Which provider answers, or None. Everything downstream works without one (RM-011).

    The decision itself lives in `ai.provider`, as a pure function: it is asked here, by
    the diagnostic and by the tests, and none of them should have to build a client to know
    the answer.
    """
    deepseek = settings.deepseek_api_key
    anthropic = settings.anthropic_api_key
    target = resolve_target(
        provider=settings.ai_provider,
        deepseek_key=deepseek.get_secret_value() if deepseek is not None else None,
        anthropic_key=anthropic.get_secret_value() if anthropic is not None else None,
        deepseek_model=settings.deepseek_model,
        anthropic_model=settings.anthropic_model,
    )
    if target is None:
        log.warning(
            "no model key for provider %s: AI commentary disabled, deterministic analysis kept",
            settings.ai_provider.value,
        )
    else:
        log.info("AI provider: %s (%s)", target.provider.value, target.model)
    return target


def _build_ai(engine: Engine, settings: Settings) -> AiFilterLayer | None:
    target = _model_target(settings)
    if target is None:
        return None
    return AiFilterLayer(
        ModelClient(target.api_key, target.model, base_url=target.base_url),
        AiCallStore(engine),
        SystemEventStore(engine),
        model=target.model,
        ai_filter=AiFilter.SHADOW,
    )


def _build_lab(engine: Engine, settings: Settings, notifier: Any, now: Any) -> DailyLab:
    """The daily AI Lab pass. Without a model it still runs, deterministically.

    That is the point of the design: the long-term improvement loop does not depend on an
    API key, and the model only ever adds commentary to a verdict already computed.

    The improvement chain is wired here, with the real search and the real version builder.
    Without it the pass analysed, escalated and proposed — and nothing ever compared: the
    escalation the operator asked for ended in a message. `ai/daily.py` imports neither
    `research` nor `backtest`: it receives a runner whose dependencies were chosen below.
    """
    target = _model_target(settings)
    store = LabStore(engine)
    client = (
        ModelClient(target.api_key, target.model, base_url=target.base_url)
        if target is not None
        else None
    )
    model = target.model if target is not None else "deterministic"
    return DailyLab(
        engine,
        analyst=TradeAnalyst(store, client, model=model),
        researcher=StrategyResearcher(store, client, model=model),
        notifier=notifier,
        cycle=DailyImprovement(engine),
        now=now,
    )


class DailyImprovement:
    """The improvement chain, composed for each market the daily pass escalates.

    `ImprovementCycle` cannot be built once for the whole agent: its search has to know which
    version it compares against — `research.improvement.search_improvement` demands the
    incumbent's label and its objective — and that is a property of the market, not of the
    process. So the chain is assembled per escalation, the way the campaign script assembles
    it, with every dependency chosen here and injected: `ai/improvement_cycle.py` imports
    neither `research` nor `backtest`, and `ai/daily.py` only knows an `ImprovementRunner`.

    This class is what the architecture exception is for (`tests/test_architecture.py`): a
    composition root that is not allowed to compose leaves the chain wired to nothing.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        datasets: Path = DATASET_DIR,
        candidates: Path = CANDIDATE_DIR,
    ) -> None:
        self._engine = engine
        self._datasets = datasets
        self._candidates = candidates

    def run(
        self,
        escalation: Escalation,
        *,
        at: datetime,
        incumbent: CycleMeasurement | None = None,
        evidence: MarketEvidence | None = None,
    ) -> CycleOutcome:
        """Run the whole sequence for one escalation, stopping wherever it must.

        With no recorded run for the market, the chain is still built and still run: it stops
        on its own and *says so* (`skipped_no_evidence`), because "nothing has been measured
        yet" is a result the operator has to read, not an error that stops the daily pass.
        """
        market = escalation.market
        context = evidence if evidence is not None else evidence_for(self._engine, market)
        baseline: Measurement | None = None
        if incumbent is not None:
            baseline = _measured(incumbent)
        elif context is not None:
            baseline = baseline_of(context)
        cycle = ImprovementCycle(
            self._engine,
            propose=propose_from_lab(LabStore(self._engine)),
            measure=lambda variant, proven: _measure_variant(variant, proven, self._datasets),
            search=lambda candidates, evaluate: _search_against(
                market, context, baseline, candidates, evaluate
            ),
            build=_build_version,
            write=lambda candidate: _write_version(candidate, self._candidates),
        )
        return cycle.run(escalation, at=at, incumbent=incumbent, evidence=context)


def _search_against(
    market: str,
    context: MarketEvidence | None,
    baseline: Measurement | None,
    candidates: Sequence[Variant],
    evaluate: Callable[[Variant], CycleMeasurement],
) -> ImprovementOutcome:
    """The real acceptance rule, on the variants the chain measured for one market.

    The search is closed over the version in place, which is why it is built per market. It
    is never reached without a baseline either: `ImprovementCycle` stops before searching
    when no run is recorded, or when the run it found declares no objective — so the stop the
    operator reads comes from the chain, not from an exception raised here.
    """
    if context is None or baseline is None:
        raise ValueError(f"{market}: no measured version to compare against")
    by_label = {candidate.label: candidate for candidate in candidates}
    return search_improvement(
        market=market,
        incumbent=baseline,
        incumbent_label=context.ref,
        candidates=[
            Candidate(
                label=candidate.label,
                parameters=dict(candidate.parameters),
                rationale=candidate.rationale,
                payload=candidate.payload,
            )
            for candidate in candidates
        ],
        evaluate=lambda candidate: _measured(evaluate(by_label[candidate.label])),
    )


def _measured(measured: CycleMeasurement) -> Measurement:
    """The chain's measurement, in the canonical shape the search compares.

    `ai/improvement_cycle.py` declares its own `Measurement` — it may not import `research`,
    so it states the shape it receives — and `research.improvement` re-exports the one from
    `core/improvement.py`. This is the single place where the two meet, and it *constructs*
    the canonical value instead of casting one class into another: a cast would make the
    boundary silent, while this call fails loudly the day the chain's measurement stops
    carrying an objective, rather than comparing nothing with a number.
    """
    return Measurement(objective=measured.objective, metrics=dict(measured.metrics))


def _measure_variant(
    variant: Variant, evidence: MarketEvidence, directory: Path
) -> CycleMeasurement:
    """Measure one variant the way the version in place was measured.

    Same frozen dataset, same costs, same campaign. The recorded objective is `cost_net` —
    the validation window replayed under stressed costs — so a variant measured any other
    way would be compared with a number produced by a different method, and the method would
    win the comparison instead of the strategy.

    Anything missing raises: no dataset for the market, a dataset that is not the one the run
    used, costs the record never named, parameters the strategy refuses. The chain journals
    each failure and the search counts the variant as unmeasurable — a variant that vanished
    quietly would understate the number of comparisons the multiple-testing correction needs.
    """
    dataset = _frozen_dataset(evidence, directory)
    campaign = run_campaign(
        {evidence.market: dataset},
        [_candidate_spec(evidence.ref, variant)],
        config_for=lambda market, _dataset: _replay_config(market, evidence),
    )
    return _measurement_of(campaign.markets[0].candidates[0])


def _frozen_dataset(evidence: MarketEvidence, directory: Path) -> CandleDataset:
    """The frozen dataset the recorded run was measured on, or a refusal to measure at all.

    The dataset is identified, not merely located. Replaying a variant on newer candles would
    compare it with a baseline taken on other data, and the series that happened to move the
    right way would win. `DatasetStore` keys by symbol, so the id has to be checked.
    """
    found = DatasetStore(directory).load_all().get(evidence.market)
    if found is None:
        raise FileNotFoundError(
            f"no frozen dataset for {evidence.market} in {directory}: a variant cannot be "
            "measured on data that is not there"
        )
    if found.dataset_id != evidence.dataset_id:
        raise ValueError(
            f"{evidence.market}: the dataset in place is {found.dataset_id}, the recorded run "
            f"measured {evidence.dataset_id}: refusing to compare across datasets"
        )
    return found


def _candidate_spec(ref: str, variant: Variant) -> CandidateSpec:
    """One variant as a campaign candidate: the strategy that builds it, and its manifest.

    The manifest is the production one, read from the file the agent loads; the class comes
    from code (`REGISTRY`), never from configuration, so no file can name what runs.
    Parameters the strategy's own model refuses raise here, and the caller counts the variant
    as unmeasurable instead of measuring a strategy nobody asked for.
    """
    document = read_yaml(STRATEGY_DIR / f"{ref}.yaml")
    manifest = StrategyManifest.model_validate(document.data)
    builder = REGISTRY.get(manifest.strategy_id)
    if builder is None:
        raise LookupError(f"{ref}: {manifest.strategy_id!r} is not a runnable strategy")
    return CandidateSpec(
        label=variant.label,
        manifest=manifest,
        factory=lambda parameters: builder(builder.parameters_model(**parameters)),
        parameters=variant.parameters,
    )


def _replay_config(market: str, evidence: MarketEvidence) -> BacktestConfig:
    """The simulation a variant is replayed in: SIGNAL mode, and the recorded costs.

    `mode=SIGNAL` because a candidate executes nothing: it earns its evidence in research,
    and the environment ladder of §14 decides when it may touch an account.
    """
    return BacktestConfig(
        symbol=market,
        costs=_recorded_costs(evidence),
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
    )


def _recorded_costs(evidence: MarketEvidence) -> CostModel:
    """The costs the recorded run was measured under — or a refusal to measure at all.

    Costs are the difference between a strategy and a strategy that pays for itself, so a
    variant measured under other assumptions is not the same measurement as the baseline.
    Defaulting to zero would manufacture an improvement out of the fee schedule.
    """
    missing = [key for key in COST_KEYS if key not in evidence.costs]
    if missing:
        raise ValueError(
            f"the recorded run for {evidence.market} does not name its costs "
            f"({', '.join(missing)}): a variant measured under others would not be comparable"
        )
    return CostModel(
        spread=float(evidence.costs["spread"]),
        slippage_atr_fraction=float(evidence.costs["slippage_atr_fraction"]),
        slippage_fixed=float(evidence.costs["slippage_fixed"]),
        commission_per_trade=Decimal(str(evidence.costs["commission_per_trade"])),
        execution_delay_bars=int(evidence.costs["execution_delay_bars"]),
        multiplier=float(evidence.costs["multiplier"]),
    )


def _measurement_of(report: CandidateReport) -> CycleMeasurement:
    """A measured candidate, in the terms the chain and the search compare it by.

    The objective is the net profit after costs and stress — the figure the operator is paid
    in, not a robustness score — and the metrics are the ones the AI Lab reads by name,
    including the stability figures when the campaign measured them. Only measured numbers
    are put in: `performance_metrics` leaves out a ratio the trades did not define.
    """
    performance = report.cost_net
    metrics = performance_metrics(performance)
    stability = report.stability_report
    if stability is not None:
        metrics["stability_score"] = float(stability.score)
        metrics["parameter_dispersion"] = float(stability.parameter_dispersion)
        metrics["out_of_sample_retention"] = float(stability.out_of_sample_retention)
        metrics["profitable_regime_ratio"] = float(stability.profitable_regime_ratio)
    return CycleMeasurement(objective=float(performance.net_profit), metrics=metrics)


def _build_version(
    market: str, supersedes: str, parameters: Mapping[str, float]
) -> CandidateVersion:
    """The next version of a strategy, from the manifest the agent actually loads.

    Every field but the version and the parameters is copied from the production manifest:
    building it from anything else would validate one strategy and approve another. It is
    still only a candidate, and `write_candidate` refuses `config/strategies/` by design.
    """
    document = read_yaml(STRATEGY_DIR / f"{supersedes}.yaml")
    return build_candidate(
        market=market,
        supersedes=supersedes,
        parameters=parameters,
        incumbent_manifest=document.data,
    )


def _write_version(candidate: object, directory: Path) -> Path:
    """Write the version this root built, and only that one.

    The chain hands the version over through the structural protocol it declares — the shape
    it may state without importing `research` — and that shape carries no `to_yaml`, because
    rendering a manifest is the business of the class that models one. So the root checks
    what it received *is* a version of its own making, and refuses anything else. Checking is
    the point: `typing.cast` would assert the same thing without ever looking, and a writer
    that guessed would be the one place where an unvalidated version could reach the disk.
    """
    if not isinstance(candidate, CandidateVersion):
        raise TypeError(f"not a version this root can write: {type(candidate).__name__}")
    return write_candidate(candidate, directory)


async def build(
    settings: Settings, *, verbose: bool = False, json_logs: bool | None = None
) -> Components:
    """Construct every component and warm the agent up. Never trades before returning."""
    _configure_output(settings, verbose=verbose, json_logs=json_logs)
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
    _register_configured_strategies(engine, config, mode, now)

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
            calendars,
        )
        application = _build_telegram(settings, service)
        operator_id = settings.telegram_allowed_user_ids[0]
        # The console is a second reader of the same text, never a second source: the
        # signal still goes to Telegram exactly as before. A human watching the terminal
        # gets the message the operator was always meant to receive.
        notifier = ConsoleNotifier(TelegramNotifier(application, operator_id))

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
        ea_directory=settings.ea_files_dir,
        ea_magic=BROKER_MAGIC,
        lab=_build_lab(engine, settings, notifier, now),
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


def _register_configured_strategies(
    engine: Engine, config: AgentConfig, mode: TradingMode, now: Any
) -> None:
    """Make every configured market visible to the registry, and enforce §14.

    The registry is the deployment record. Two rules are checked before anything connects:
    a deprecated ref stops the start-up, and an executing mode requires the strategy to have
    climbed far enough up the ladder — PAPER accepts `paper`, `candidate` or `live`, while
    DEMO and LIVE accept only `live`. In OBSERVATION and SIGNAL nothing is executed, so any
    non-deprecated status is accepted: that is how a candidate earns its evidence.
    """
    registry = StrategyRegistry(engine, clock=now)
    refused: list[str] = []
    allowed = MODE_REQUIRED_STATUS.get(mode)
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
            entry = registry.get(market.symbol, market.strategy)
            log.info(
                "registry: %s on %s registered as %s",
                market.strategy,
                market.symbol,
                entry.status.value,
            )
        log.info(
            "registry: %s on %s is %s (promoted %s)",
            market.strategy,
            market.symbol,
            entry.status.value,
            entry.promoted_at.isoformat() if entry.promoted_at else "never",
        )
        if entry.status is StrategyStatus.DEPRECATED:
            refused.append(f"{market.strategy} on {market.symbol} is DEPRECATED")
        elif allowed is not None and entry.status not in allowed:
            refused.append(
                f"{market.strategy} on {market.symbol} is {entry.status.value}, "
                f"which cannot execute in {mode.value} (required: "
                + ", ".join(sorted(status.value for status in allowed))
                + ")"
            )
    if refused:
        raise ConfigError(
            "strategy governance refused the start-up: "
            + "; ".join(sorted(refused))
            + ". Promote the version through the validation gates, or point agent.yaml at "
            "one that is already promoted."
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


def _configure_output(
    settings: Settings, *, verbose: bool = False, json_logs: bool | None = None
) -> None:
    """Decide what the operator sees: signals and errors, or a machine-readable journal.

    Three cases, and the default is the one a person wants:

    * a terminal, unless told otherwise → framed signals, readable warnings, colour;
    * `--verbose` → the same, at INFO, for a diagnosis;
    * `--json-logs`, or a redirected stream (the scheduled task, a journal) → JSON.

    A redirected stream implies JSON because that is what reads it: making a service
    default to colour frames would trade a parseable journal for escape codes nobody sees.
    """
    secrets = settings.secret_values()
    interactive = getattr(sys.stderr, "isatty", lambda: False)()
    machine = json_logs if json_logs is not None else not interactive
    if machine:
        configure_json_logging(logging.INFO if verbose else logging.WARNING, secrets)
        return
    use_utf8_console()
    enable_ansi()
    configure_console_logging(logging.INFO if verbose else logging.WARNING)


async def run(
    settings: Settings,
    *,
    cycles: int | None = None,
    verbose: bool = False,
    json_logs: bool | None = None,
) -> int:
    components = await build(settings, verbose=verbose, json_logs=json_logs)
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
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show every log line, not only warnings, errors and signals",
    )
    parser.add_argument(
        "--json-logs",
        action="store_true",
        default=None,
        help="machine-readable journal (implied when the output is redirected)",
    )
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
        return asyncio.run(
            run(settings, cycles=cycles, verbose=args.verbose, json_logs=args.json_logs)
        )
    except KeyboardInterrupt:
        log.info("interrupted, shutting down")
        return 0
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
