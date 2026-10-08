"""The baseline the improvement script measures against must be the one the agent loads.

The script used to hard-code `witness@1.1.0` / `trend_breakout@1.0.0` while its own comment
claimed it read the production manifests. On 2026-10-08 the agent ran `witness@1.1.1`: every
recorded run would have named a version nobody was running, and the daily chain compares
against exactly that name. These tests hold the source of truth to `config/agent.yaml`.
"""

import importlib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
AGENT_CONFIG = ROOT / "config" / "agent.yaml"

improve = importlib.import_module("scripts.backtest.improve")


def declared_markets(path: Path) -> dict[str, str]:
    """What `agent.yaml` declares, read here without going through the module under test."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        str(market["symbol"]): str(market["strategy"])
        for market in document["markets"]
        if market.get("enabled", True)
    }


def write_config(path: Path, markets: list[dict[str, object]]) -> Path:
    path.write_text(yaml.safe_dump({"markets": markets}), encoding="utf-8")
    return path


def test_the_baseline_ref_is_the_one_the_agent_loads() -> None:
    assert improve.deployed_refs(AGENT_CONFIG) == declared_markets(AGENT_CONFIG)


def test_a_market_the_config_declares_is_the_one_measured(tmp_path: Path) -> None:
    path = write_config(
        tmp_path / "agent.yaml",
        [
            {"symbol": "XAUUSD", "enabled": True, "strategy": "witness@9.9.9"},
            {"symbol": "BTCUSD", "enabled": True, "strategy": "trend_breakout@9.9.9"},
        ],
    )

    assert improve.incumbent_ref("XAUUSD", path) == "witness@9.9.9"
    assert improve.incumbent_ref("BTCUSD", path) == "trend_breakout@9.9.9"


def test_a_disabled_market_is_not_measured(tmp_path: Path) -> None:
    path = write_config(
        tmp_path / "agent.yaml",
        [
            {"symbol": "XAUUSD", "enabled": True, "strategy": "witness@9.9.9"},
            {"symbol": "BTCUSD", "enabled": False, "strategy": "trend_breakout@9.9.9"},
        ],
    )

    assert improve.deployed_refs(path) == {"XAUUSD": "witness@9.9.9"}


def test_an_undeclared_market_is_refused_instead_of_guessed(tmp_path: Path) -> None:
    path = write_config(
        tmp_path / "agent.yaml",
        [{"symbol": "XAUUSD", "enabled": True, "strategy": "witness@9.9.9"}],
    )

    with pytest.raises(SystemExit, match="BTCUSD"):
        improve.incumbent_ref("BTCUSD", path)


def test_the_script_keeps_no_remembered_version() -> None:
    """A version baked into the source is the defect this file exists to prevent.

    Comments are stripped before looking: the module *explains* the drift it once had, and
    naming the versions in prose is the point. What must not come back is a version the code
    can execute.
    """
    lines = (ROOT / "scripts" / "backtest" / "improve.py").read_text(encoding="utf-8").splitlines()
    code = "\n".join(line.split("#", 1)[0] for line in lines)

    for remembered in ("witness@1.", "trend_breakout@1."):
        assert remembered not in code, f"{remembered} en dur dans le script"
