"""The composition root: wiring decisions that can be checked without a terminal."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from tradingagent.app import (
    _build_command_service,
    _declared_symbols,
    _register_configured_strategies,
    _subscriptions,
)
from tradingagent.config.agent import load_agent_config
from tradingagent.config.errors import ConfigError
from tradingagent.config.settings import Settings
from tradingagent.config.strategy_catalog import StrategyCatalog
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import StrategyStatus
from tradingagent.notify.service import CommandService
from tradingagent.registry.store import StrategyRegistry
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.migrate import upgrade
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


def test_the_configured_strategies_are_registered_once(tmp_path) -> None:
    engine, config = _configured(tmp_path)
    now = lambda: datetime(2026, 10, 7, tzinfo=UTC)  # noqa: E731
    _register_configured_strategies(engine, config, now)
    _register_configured_strategies(engine, config, now)  # idempotent across restarts

    registry = StrategyRegistry(engine, clock=now)
    gold = registry.get("XAUUSD", "witness@1.1.0")
    btc = registry.get("BTCUSD", "trend_breakout@1.0.0")
    assert gold.status is StrategyStatus.DISCOVERED
    assert btc.status is StrategyStatus.DISCOVERED
    assert registry.active("XAUUSD") is None  # nothing is LIVE yet, so nothing trades
    engine.dispose()


def test_a_deprecated_strategy_stops_the_start_up(tmp_path) -> None:
    engine, config = _configured(tmp_path)
    now = lambda: datetime(2026, 10, 7, tzinfo=UTC)  # noqa: E731
    _register_configured_strategies(engine, config, now)
    registry = StrategyRegistry(engine, clock=now)
    registry.transition(
        "XAUUSD", "witness@1.1.0", StrategyStatus.DEPRECATED, "operator", "retired", now()
    )
    try:
        _register_configured_strategies(engine, config, now)
    except ConfigError as error:
        assert "deprecated" in str(error)
        assert "witness@1.1.0 on XAUUSD" in str(error)
    else:  # pragma: no cover - the refusal is the point
        raise AssertionError("a deprecated strategy must stop the start-up")
    engine.dispose()
