from typing import Any

import pytest
from pydantic import ValidationError

from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.manifest import StrategyManifest, parse_ref

VALID: dict[str, Any] = {
    "strategy_id": "gold_trend",
    "version": "1.2.0",
    "max_mode": "DEMO",
    "allowed_symbols": ["frxXAUUSD"],
    "timeframes": ["M15", "H1"],
    "history_bars": 300,
}


def manifest(**overrides: Any) -> StrategyManifest:
    return StrategyManifest.model_validate({**VALID, **overrides})


def test_valid_manifest_and_defaults() -> None:
    loaded = manifest()
    assert loaded.max_mode is TradingMode.DEMO
    assert loaded.timeframes == (Timeframe.M15, Timeframe.H1)
    assert loaded.expiry_bars == 1
    assert loaded.ai_filter is AiFilter.SHADOW
    assert loaded.parameters == {}


def test_ref_and_primary_timeframe() -> None:
    loaded = manifest()
    assert loaded.ref == "gold_trend@1.2.0"
    assert loaded.primary_timeframe is Timeframe.M15


@pytest.mark.parametrize(
    "overrides",
    [
        {"strategy_id": "GoldTrend"},
        {"strategy_id": "1gold"},
        {"strategy_id": "gold-trend"},
        {"version": "1.2"},
        {"version": "v1.2.0"},
        {"version": 1.2},
        {"max_mode": "YOLO"},
        {"allowed_symbols": []},
        {"allowed_symbols": ["frxXAUUSD", "frxXAUUSD"]},
        {"timeframes": []},
        {"timeframes": ["M15", "M15"]},
        {"timeframes": ["M7"]},
        {"history_bars": 0},
        {"history_bars": 10_001},
        {"expiry_bars": 0},
        {"ai_filter": "maybe"},
        {"stratgy_id": "typo"},
    ],
)
def test_invalid_manifest_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        manifest(**overrides)


def test_manifest_is_immutable() -> None:
    with pytest.raises(ValidationError):
        manifest().history_bars = 1  # type: ignore[misc]


def test_parse_ref() -> None:
    assert parse_ref("gold_trend@1.2.0") == ("gold_trend", "1.2.0")


@pytest.mark.parametrize("ref", ["gold_trend", "gold_trend@1.2", "@1.2.0", "Gold@1.0.0", ""])
def test_parse_ref_rejects_malformed_references(ref: str) -> None:
    with pytest.raises(ValueError, match="reference"):
        parse_ref(ref)


def test_scan_timeframes_trigger_one_isolated_series_each() -> None:
    scalper = manifest(timeframes=["M15"], scan_timeframes=["M1", "M5", "M15", "M30"])
    assert scalper.trigger_timeframes == (
        Timeframe.M1,
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.M30,
    )
    one = scalper.for_timeframe(Timeframe.M5)
    assert one.timeframes == (Timeframe.M5,)
    assert one.primary_timeframe is Timeframe.M5
    assert one.scan_timeframes == ()
    with pytest.raises(ValueError, match="not a trigger timeframe"):
        scalper.for_timeframe(Timeframe.H1)


def test_without_scan_timeframes_only_the_primary_triggers() -> None:
    assert manifest().trigger_timeframes == (Timeframe.M15,)


def test_scan_timeframes_must_be_unique() -> None:
    with pytest.raises(ValidationError):
        manifest(scan_timeframes=["M1", "M1"])
