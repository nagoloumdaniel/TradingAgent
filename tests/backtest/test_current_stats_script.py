"""The instrument that reports the *deployed* strategies' backtest statistics.

`run_campaign.py` measures candidates; `scripts/backtest/current_stats.py` measures what
`config/agent.yaml` designates, and its whole value rests on four resolutions that must not
drift: the version, the parameters, the timeframe and the costs. These tests pin the three that
can be checked without running a campaign. The fourth — the campaign itself — is verified by
`docs/reports/2026-10-08-verification-stats-backtest.md`, where a second code path reproduced
the published figures exactly.
"""

import importlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.costs import (
    DEFAULT_COMMISSION_EUR,
    DEFAULT_SLIPPAGE_FRACTION,
    DEFAULT_SPREAD_FRACTION,
)
from tradingagent.backtest.datasets import CandleDataset, SyntheticRegime, synthetic_dataset
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe

ROOT = Path(__file__).resolve().parents[2]
AGENT_CONFIG = ROOT / "config" / "agent.yaml"

stats = importlib.import_module("scripts.backtest.current_stats")


def declared_markets(path: Path) -> dict[str, str]:
    """What `agent.yaml` declares, read here without going through the module under test."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        str(market["symbol"]): str(market["strategy"])
        for market in document["markets"]
        if market.get("enabled", True)
    }


def a_dataset(timeframe: Timeframe = Timeframe.M15, bars: int = 40) -> CandleDataset:
    return synthetic_dataset(
        "stats-test",
        "XAUUSD",
        timeframe,
        datetime(2026, 1, 5, tzinfo=UTC),
        (SyntheticRegime(bars=bars, drift=0.00002, volatility=0.0012),),
        seed=11,
        start_price=2000.0,
        decimals=2,
    )


def test_the_measured_version_is_the_deployed_one() -> None:
    assert stats.deployed_refs(AGENT_CONFIG) == declared_markets(AGENT_CONFIG)


def test_a_disabled_market_is_not_measured(tmp_path: Path) -> None:
    path = tmp_path / "agent.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "markets": [
                    {"symbol": "XAUUSD", "enabled": True, "strategy": "witness@9.9.9"},
                    {"symbol": "BTCUSD", "enabled": False, "strategy": "trend_breakout@9.9.9"},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert stats.deployed_refs(path) == {"XAUUSD": "witness@9.9.9"}


@pytest.mark.parametrize("market", ["XAUUSD", "BTCUSD"])
def test_the_candidate_carries_the_manifest_parameters(market: str) -> None:
    """The values come from the manifest, the class from code: nothing is restated here."""
    ref = declared_markets(AGENT_CONFIG)[market]
    document = yaml.safe_load((ROOT / "config" / "strategies" / f"{ref}.yaml").read_text("utf-8"))

    spec = stats.spec_for(market, ref)

    assert spec.label == ref
    assert dict(spec.parameters) == document["parameters"]
    assert spec.manifest.version == document["version"]
    assert spec.manifest.timeframes == tuple(document["timeframes"])


def test_the_charged_costs_are_the_documented_ones() -> None:
    """Same numbers as `run_campaign.py`: a report on other costs would not be comparable.

    Les valeurs sont lues sur les constantes du module de coûts, pas recopiées ici : c'est ce
    qui fait échouer ce test le jour où le modèle change, au lieu de le laisser passer. Il a
    d'ailleurs échoué quand le spread est passé du modèle (0,5 point de base) au spread mesuré
    sur le courtier (2,24 points de base), ce qui est exactement son rôle.
    """
    dataset = a_dataset()
    price = dataset.candles[0].close

    config = stats.config_for("XAUUSD", dataset)

    assert config.costs.spread == round(price * DEFAULT_SPREAD_FRACTION, 8)
    assert config.costs.slippage_fixed == round(price * DEFAULT_SLIPPAGE_FRACTION, 8)
    assert config.costs.commission_per_trade == DEFAULT_COMMISSION_EUR
    assert config.mode is TradingMode.SIGNAL, "a statistics report never executes"
    assert config.max_concurrent_positions == 1


def test_the_reported_figures_are_the_analytics_ones() -> None:
    """One empty sample, to pin the field names: a rename must break this, not the report."""
    figures = stats.performance_stats(compute_performance([]))

    assert figures["trades"] == 0
    assert figures["net_profit"] == 0.0
    assert figures["profit_factor"] is None
    assert figures["win_rate"] is None
    assert "max_drawdown" in figures
    assert "realized_rr" in figures


def test_a_dataset_in_another_timeframe_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The manifest declares M15; measuring M5 would answer a different question.

    The refusal is the point: a report that silently measured the wrong unit of time would be
    believed, and the timeframe is exactly what the operator asked to see named.
    """

    class Store:
        def __init__(self, directory: Path) -> None:
            self.directory = directory

        def load_all(self) -> dict[str, CandleDataset]:
            return {"XAUUSD": a_dataset(Timeframe.M5)}

    monkeypatch.setattr(stats, "DatasetStore", Store)

    with pytest.raises(SystemExit, match="M5"):
        stats.main(["--market", "XAUUSD", "--no-write"])


def test_a_market_without_a_frozen_dataset_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    class Store:
        def __init__(self, directory: Path) -> None:
            self.directory = directory

        def load_all(self) -> dict[str, CandleDataset]:
            return {"XAUUSD": a_dataset()}

    monkeypatch.setattr(stats, "DatasetStore", Store)

    with pytest.raises(SystemExit, match="BTCUSD"):
        stats.main(["--market", "BTCUSD", "--no-write"])
