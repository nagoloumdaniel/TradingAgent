"""The shipped configuration must load as-is, with the real symbols of the demo account."""

from pathlib import Path

from tradingagent.config.agent import load_agent_config
from tradingagent.config.strategy_catalog import load_strategy_catalog
from tradingagent.core.mode import TradingMode
from tradingagent.strategies.registry import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_STRATEGIES = ROOT / "config" / "strategies"
SHIPPED_AGENT = ROOT / "config" / "agent.yaml"
BROKER_SYMBOLS = {"XAUUSD"}


def test_shipped_strategy_catalog_loads() -> None:
    catalog = load_strategy_catalog(SHIPPED_STRATEGIES, REGISTRY)
    assert "witness@1.1.0" in catalog
    assert "vwap_pullback@1.1.0" in catalog


def test_shipped_agent_config_loads_in_signal_mode() -> None:
    strategies = {
        ref: loaded.manifest
        for ref, loaded in load_strategy_catalog(SHIPPED_STRATEGIES, REGISTRY).items()
    }
    config = load_agent_config(
        SHIPPED_AGENT,
        known_symbols=BROKER_SYMBOLS,
        strategies=strategies,
        mode=TradingMode.DEMO,
    )
    assert [market.symbol for market in config.markets] == ["XAUUSD"]
    refs = [market.strategy for market in config.markets]
    # The gold market runs one pinned `vwap_pullback` version (EF-003 asked for a distinct
    # strategy per market back when there were two; with gold alone the pin is what remains).
    assert refs == ["vwap_pullback@1.2.0"]
    # RM-019: with 100 EUR the market is not eligible in live mode; the profile is still
    # declared so the refusal is a decision, not a crash.
    assert config.risk.live.reference_capital == 100


def test_shipped_cost_ceiling_keeps_a_net_margin_at_the_first_target() -> None:
    """TASK-067: costs may eat at most half the distance to TP1, so a trade that reaches its
    first target and nothing more still ends in profit. Shipped, not left at None."""
    from tradingagent.risk.model import limits_for

    strategies = {
        ref: loaded.manifest
        for ref, loaded in load_strategy_catalog(SHIPPED_STRATEGIES, REGISTRY).items()
    }
    config = load_agent_config(
        SHIPPED_AGENT, known_symbols=BROKER_SYMBOLS, strategies=strategies, mode=TradingMode.DEMO
    )
    for mode in (TradingMode.DEMO, TradingMode.LIVE):
        ceiling = limits_for(mode, config.risk).max_cost_to_target
        assert ceiling is not None and 0 < ceiling <= 1
        assert ceiling == 0.5
