"""The composition root: wiring decisions that can be checked without a terminal."""

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert, select
from sqlalchemy.orm import Session

from tradingagent.ai.escalation import Escalation, FailurePattern
from tradingagent.ai.evidence import MeasuredRun, record_run
from tradingagent.ai.improvement_cycle import CYCLE_EVENT, MEASURE_FAILED_EVENT, CycleStatus
from tradingagent.ai.lab_store import AnalysisRecord, LabStore, ProposalRecord
from tradingagent.app import (
    DailyImprovement,
    _build_command_service,
    _build_lab,
    _declared_symbols,
    _register_configured_strategies,
    _subscriptions,
)
from tradingagent.config.agent import load_agent_config
from tradingagent.config.errors import ConfigError
from tradingagent.config.settings import Settings
from tradingagent.config.strategy_catalog import StrategyCatalog
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import AnalysisKind, StrategyStatus
from tradingagent.notify.service import CommandService
from tradingagent.registry.store import StrategyRegistry
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import StrategyVersionRow, SystemEventRow
from tradingagent.strategies.registry import REGISTRY

ROOT = Path(__file__).resolve().parents[1]
SHIPPED_STRATEGIES = ROOT / "config" / "strategies"
SHIPPED_AGENT = ROOT / "config" / "agent.yaml"
BROKER_SYMBOLS = {"XAUUSD", "BTCUSD"}

COMMANDS = (
    "status",
    "markets",
    "signals",
    "positions",
    "performance",
    "report",
    "pause",
    "resume",
    "close_all",
    "emergency_stop",
    "disable",
    "enable",
    "mode",
)


def test_the_shipped_markets_are_the_verified_broker_symbols() -> None:
    assert set(_declared_symbols()) == BROKER_SYMBOLS


def test_each_market_gets_its_own_strategy() -> None:
    catalog = StrategyCatalog(SHIPPED_STRATEGIES, REGISTRY)
    config = load_agent_config(
        SHIPPED_AGENT,
        known_symbols=BROKER_SYMBOLS,
        strategies={ref: loaded.manifest for ref, loaded in catalog.current().items()},
        mode=TradingMode.SIGNAL,
    )
    subscriptions = _subscriptions(config, catalog)
    assert {subscription.symbol for subscription in subscriptions} == BROKER_SYMBOLS
    assert all(subscription.timeframe.value == "M15" for subscription in subscriptions)
    assert len({market.strategy for market in config.markets}) == 2


def _settings() -> Settings:
    return Settings(
        mt5_login=40123456,
        mt5_server="Deriv-Demo",
        mt5_password="investor-secret",  # pragma: allowlist secret
        telegram_bot_token="123456:ABCDEF",  # pragma: allowlist secret
        telegram_allowed_user_ids=(42,),
        database_url="postgresql://user:pw@host/db",  # pragma: allowlist secret
    )


def test_the_bot_registers_every_documented_command(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'bot.db'}"
    upgrade(url)
    engine = create_database_engine(url)
    service: CommandService = _build_command_service(
        engine,
        _settings(),
        HaltStore(engine),
        CandleStore(engine),
        (("XAUUSD", True), ("BTCUSD", True)),
        lambda: datetime(2026, 10, 6, tzinfo=UTC),
    )
    help_text = asyncio.run(service.handle(42, True, "/help"))
    assert help_text is not None
    for command in COMMANDS:
        assert f"/{command}" in help_text, command
    engine.dispose()


def test_the_ai_key_stays_optional() -> None:
    # RM-011: a missing or blank model key must not stop the agent (the AI is not wired).
    assert _settings().anthropic_api_key is None


def _configured(tmp_path):
    url = f"sqlite:///{tmp_path / 'registry.db'}"
    upgrade(url)
    engine = create_database_engine(url)
    catalog = StrategyCatalog(SHIPPED_STRATEGIES, REGISTRY)
    config = load_agent_config(
        SHIPPED_AGENT,
        known_symbols=BROKER_SYMBOLS,
        strategies={ref: loaded.manifest for ref, loaded in catalog.current().items()},
        mode=TradingMode.SIGNAL,
    )
    return engine, config


def _refs(config) -> dict[str, str]:
    """The strategy each market is configured with, read from the shipped config.

    Read rather than spelled out: the version moves when a candidate is put on the demo
    account, and a test that hardcodes one then fails for a reason that has nothing to do
    with the rule it verifies.
    """
    return {market.symbol: market.strategy for market in config.markets}


def test_the_configured_strategies_are_registered_once(tmp_path) -> None:
    engine, config = _configured(tmp_path)
    now = lambda: datetime(2026, 10, 7, tzinfo=UTC)  # noqa: E731
    _register_configured_strategies(engine, config, TradingMode.SIGNAL, now)
    _register_configured_strategies(engine, config, TradingMode.SIGNAL, now)  # idempotent

    refs = _refs(config)
    registry = StrategyRegistry(engine, clock=now)
    gold = registry.get("XAUUSD", refs["XAUUSD"])
    btc = registry.get("BTCUSD", refs["BTCUSD"])
    assert gold.status is StrategyStatus.DISCOVERED
    assert btc.status is StrategyStatus.DISCOVERED
    assert registry.active("XAUUSD") is None  # nothing is LIVE yet, so nothing trades
    engine.dispose()


def test_a_deprecated_strategy_stops_the_start_up(tmp_path) -> None:
    engine, config = _configured(tmp_path)
    now = lambda: datetime(2026, 10, 7, tzinfo=UTC)  # noqa: E731
    _register_configured_strategies(engine, config, TradingMode.SIGNAL, now)
    refs = _refs(config)
    registry = StrategyRegistry(engine, clock=now)
    registry.transition(
        "XAUUSD", refs["XAUUSD"], StrategyStatus.DEPRECATED, "operator", "retired", now()
    )
    with pytest.raises(ConfigError) as caught:
        _register_configured_strategies(engine, config, TradingMode.SIGNAL, now)
    assert "DEPRECATED" in str(caught.value)
    assert f"{refs['XAUUSD']} on XAUUSD" in str(caught.value)
    engine.dispose()


def test_an_executing_mode_requires_the_matching_rung(tmp_path) -> None:
    """§14: DEMO executes only a LIVE version, and a DISCOVERED one is refused with why."""
    engine, config = _configured(tmp_path)
    now = lambda: datetime(2026, 10, 7, tzinfo=UTC)  # noqa: E731
    with pytest.raises(ConfigError) as caught:
        _register_configured_strategies(engine, config, TradingMode.DEMO, now)
    message = str(caught.value)
    assert "cannot execute in DEMO" in message
    assert "live" in message
    engine.dispose()


def test_paper_mode_only_requires_the_paper_rung(tmp_path) -> None:
    engine, config = _configured(tmp_path)
    now = lambda: datetime(2026, 10, 7, tzinfo=UTC)  # noqa: E731
    refs = _refs(config)
    registry = StrategyRegistry(engine, clock=now)
    _register_configured_strategies(engine, config, TradingMode.SIGNAL, now)
    for status, reason in (
        (StrategyStatus.EXPERIMENTAL, "candidate"),
        (StrategyStatus.BACKTESTING, "measured"),
        (StrategyStatus.VALIDATING, "gates"),
        (StrategyStatus.PAPER, "paper"),
    ):
        registry.transition("XAUUSD", refs["XAUUSD"], status, "operator", reason, now())
        registry.transition("BTCUSD", refs["BTCUSD"], status, "operator", reason, now())
    with pytest.raises(ConfigError):
        _register_configured_strategies(engine, config, TradingMode.LIVE, now)
    # PAPER and DEMO accept the same rung: both are venues without real money, and the
    # environment ladder of §44 puts the demo rehearsal before real trading, not after it.
    _register_configured_strategies(engine, config, TradingMode.PAPER, now)
    _register_configured_strategies(engine, config, TradingMode.DEMO, now)
    engine.dispose()


def test_demo_and_paper_accept_the_same_rung_but_live_does_not(tmp_path) -> None:
    """The one distinction that matters: real money needs the top rung, a demo account does
    not. Requiring it there would make the rehearsal available only after the decision it
    exists to inform."""
    engine, config = _configured(tmp_path)
    now = lambda: datetime(2026, 10, 7, tzinfo=UTC)  # noqa: E731
    registry = StrategyRegistry(engine, clock=now)
    _register_configured_strategies(engine, config, TradingMode.SIGNAL, now)
    for market, ref in _refs(config).items():
        for status in (
            StrategyStatus.EXPERIMENTAL,
            StrategyStatus.BACKTESTING,
            StrategyStatus.VALIDATING,
            StrategyStatus.PAPER,
        ):
            registry.transition(market, ref, status, "operator", "campaign", now())

    _register_configured_strategies(engine, config, TradingMode.DEMO, now)  # accepted
    with pytest.raises(ConfigError) as caught:
        _register_configured_strategies(engine, config, TradingMode.LIVE, now)
    assert "cannot execute in LIVE" in str(caught.value)
    engine.dispose()


# --- the daily improvement chain, composed here and nowhere else ---------------------------------

MARKET = "XAUUSD"
REF = "witness@1.1.0"
PARAMETERS: Mapping[str, float] = {"ema_fast": 20.0, "ema_slow": 50.0, "take_profit_rr": 2.0}
T0 = datetime(2026, 10, 8, 6, 0, tzinfo=UTC)


def _lab_engine(tmp_path: Path) -> Engine:
    url = f"sqlite:///{tmp_path / 'lab.db'}"
    upgrade(url)
    return create_database_engine(url)


def an_escalation(market: str = MARKET) -> Escalation:
    """The escalation the analyst raises when a failure motif repeats."""
    pattern = FailurePattern(
        market=market,
        kind="recurring_pattern",
        occurrences=3,
        first_seen=T0 - timedelta(days=5),
        last_seen=T0,
        reasons=("le motif se répète",),
    )
    return Escalation(market=market, pattern=pattern, trigger=3, reason="le motif se répète 3 fois")


def record_incumbent(engine: Engine) -> None:
    """One market that really was measured, with the parameters of the version in place."""
    with engine.begin() as connection:
        connection.execute(
            insert(StrategyVersionRow).values(
                ref=REF,
                strategy_id="witness",
                version="1.1.0",
                manifest={"parameters": dict(PARAMETERS)},
                content_hash="c" * 64,
                first_seen_at=T0 - timedelta(days=60),
            )
        )
    record_run(
        engine,
        MeasuredRun(
            market=MARKET,
            ref=REF,
            dataset_id="xau-m15-2026-10-08",
            fingerprint="a" * 64,
            window_start=T0 - timedelta(days=30),
            window_end=T0,
            objective=1000.0,
            metrics={"max_drawdown_eur": 90.0, "trades": 120.0},
            costs={"spread": 0.3, "commission_per_trade": 0.5},
            comparisons=2,
        ),
        at=T0 - timedelta(days=1),
    )


def record_proposal(engine: Engine) -> None:
    """One open AI proposal: the variant the daily chain would measure."""
    store = LabStore(engine)
    store.record_analysis(
        AnalysisRecord(
            kind=AnalysisKind.HYPOTHESIS,
            market=MARKET,
            ref=REF,
            model="deterministic",
            request={},
            findings={"hypotheses": [{"parameter": "take_profit_rr"}]},
            created_at=T0,
        )
    )
    store.record_proposal(
        ProposalRecord(
            market=MARKET,
            hypothesis="un take profit plus proche encaisse plus souvent",
            proposed_change={"parameter": "take_profit_rr", "proposed_value": 1.5},
            created_at=T0,
            ref=REF,
        )
    )


def journal(engine: Engine, kind: str) -> list[SystemEventRow]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(SystemEventRow)
                .where(SystemEventRow.kind == kind)
                .order_by(SystemEventRow.id)
            ).all()
        )


def test_the_daily_pass_is_wired_to_the_improvement_chain(tmp_path: Path) -> None:
    """`ai/daily.py` accepts a chain nobody was handing it: the pass must now receive one."""
    engine = _lab_engine(tmp_path)
    lab = _build_lab(engine, _settings(), None, lambda: T0)
    assert isinstance(lab.cycle, DailyImprovement)
    engine.dispose()


def test_the_chain_stops_without_proof_and_says_so(tmp_path: Path) -> None:
    """No recorded run is a result to report, not a failure to swallow.

    It is the state every market starts in: the chain must stop, say why, write nothing and
    journal the stop — never invent a baseline to compare against, never raise into the
    daily pass.
    """
    engine = _lab_engine(tmp_path)
    chain = DailyImprovement(engine, datasets=tmp_path, candidates=tmp_path)

    outcome = chain.run(an_escalation(), at=T0)

    assert outcome.status is CycleStatus.SKIPPED_NO_EVIDENCE
    assert outcome.improved is False
    assert outcome.comparisons == 0
    assert "aucune preuve mesurée" in outcome.message()
    assert not list(tmp_path.glob("*.yaml")), "nothing was written for an unmeasured market"
    assert [entry.detail["status"] for entry in journal(engine, CYCLE_EVENT)] == [
        CycleStatus.SKIPPED_NO_EVIDENCE.value
    ]
    engine.dispose()


def test_the_composed_chain_measures_with_the_real_search(tmp_path: Path) -> None:
    """The chain does not re-implement the acceptance rule, it calls it.

    With a recorded baseline and an open proposal, the real `search_improvement` runs: the
    variant is measured by the real campaign machinery — which finds no frozen dataset here
    — and the failure is *counted* as a refused comparison and journalled, rather than the
    variant quietly disappearing from the count the multiple-testing correction needs.
    """
    engine = _lab_engine(tmp_path)
    record_incumbent(engine)
    record_proposal(engine)
    chain = DailyImprovement(engine, datasets=tmp_path, candidates=tmp_path)

    outcome = chain.run(an_escalation(), at=T0)

    assert outcome.status is CycleStatus.NO_IMPROVEMENT
    assert outcome.comparisons == 1
    refused = [attempt for attempt in outcome.attempts if not attempt.accepted]
    assert any("could not be measured" in attempt.reason for attempt in refused)
    assert len(journal(engine, MEASURE_FAILED_EVENT)) == 1
    assert not list(tmp_path.glob("*.yaml")), "an unmeasurable variant writes no version"
    engine.dispose()
