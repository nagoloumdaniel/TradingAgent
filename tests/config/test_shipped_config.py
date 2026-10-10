"""The shipped configuration must load as-is, with the real symbols of the demo account."""

from pathlib import Path

from tradingagent.config.agent import load_agent_config
from tradingagent.config.strategy_catalog import load_strategy_catalog
from tradingagent.core.mode import TradingMode
from tradingagent.strategies.registry import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
SHIPPED_STRATEGIES = ROOT / "config" / "strategies"
SHIPPED_AGENT = ROOT / "config" / "agent.yaml"
BROKER_SYMBOLS = {"XAUUSD", "BTCUSD"}


def test_shipped_strategy_catalog_loads() -> None:
    catalog = load_strategy_catalog(SHIPPED_STRATEGIES, REGISTRY)
    assert "witness@1.1.0" in catalog
    assert "trend_breakout@1.0.0" in catalog


def test_shipped_agent_config_loads_in_signal_mode() -> None:
    strategies = {
        ref: loaded.manifest
        for ref, loaded in load_strategy_catalog(SHIPPED_STRATEGIES, REGISTRY).items()
    }
    config = load_agent_config(
        SHIPPED_AGENT,
        known_symbols=BROKER_SYMBOLS,
        strategies=strategies,
        mode=TradingMode.SIGNAL,
    )
    assert [market.symbol for market in config.markets] == ["XAUUSD", "BTCUSD"]
    refs = [market.strategy for market in config.markets]
    # EF-003: "two markets run two different strategies". Since 2026-10-10 both markets run the
    # same *rule* (`vwap_pullback`), which the operator chose over `witness` + `trend_breakout`;
    # each market therefore carries its own **version**. The requirement is about a distinct
    # strategy per market, and a distinct version is what makes each symbol tunable on its own
    # -- gold and bitcoin share neither spread nor volatility. Comparing the full refs, and not
    # just the strategy ids, is what pins that: two markets on the very same manifest would
    # fail here.
    assert len(set(refs)) == 2, "each market runs a different strategy version (EF-003)"
    assert {ref.split("@")[0] for ref in refs} == {"vwap_pullback"}
    # RM-019: with 100 EUR neither market is eligible in live mode; the profile is still
    # declared so the refusal is a decision, not a crash.
    assert config.risk.live.reference_capital == 100
