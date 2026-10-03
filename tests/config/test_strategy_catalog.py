from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from tradingagent.config.errors import ConfigError
from tradingagent.config.strategy_catalog import load_strategy_catalog
from tradingagent.core.signal import SignalCandidate
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.registry import build_registry


class TrendParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    ema_fast: int = Field(ge=2)
    ema_slow: int = Field(ge=3)


class Trend(Strategy[TrendParameters]):
    strategy_id = "trend"
    parameters_model = TrendParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        return None


REGISTRY = build_registry(Trend)

VALID = """\
strategy_id: trend
version: 1.0.0
max_mode: SIGNAL
allowed_symbols: [frxXAUUSD]
timeframes: [M15, H1]
history_bars: 300
parameters:
  ema_fast: 20
  ema_slow: 50
"""


def write(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


def error_of(directory: Path) -> str:
    with pytest.raises(ConfigError) as caught:
        load_strategy_catalog(directory, REGISTRY)
    return str(caught.value)


def test_valid_catalog_is_indexed_by_reference(tmp_path: Path) -> None:
    write(tmp_path, "trend@1.0.0.yaml", VALID)
    catalog = load_strategy_catalog(tmp_path, REGISTRY)
    loaded = catalog["trend@1.0.0"]
    assert isinstance(loaded.strategy, Trend)
    assert loaded.strategy.parameters == TrendParameters(ema_fast=20, ema_slow=50)
    assert loaded.manifest.history_bars == 300


def test_two_versions_of_a_strategy_coexist(tmp_path: Path) -> None:
    write(tmp_path, "trend@1.0.0.yaml", VALID)
    write(tmp_path, "trend@1.1.0.yaml", VALID.replace("1.0.0", "1.1.0").replace("20", "12"))
    catalog = load_strategy_catalog(tmp_path, REGISTRY)
    assert set(catalog) == {"trend@1.0.0", "trend@1.1.0"}


def test_file_name_must_match_the_reference(tmp_path: Path) -> None:
    write(tmp_path, "trend.yaml", VALID)
    message = error_of(tmp_path)
    assert "trend.yaml:1" in message
    assert "trend@1.0.0.yaml" in message


def test_unregistered_strategy_is_rejected(tmp_path: Path) -> None:
    write(tmp_path, "ghost@1.0.0.yaml", VALID.replace("strategy_id: trend", "strategy_id: ghost"))
    message = error_of(tmp_path)
    assert "ghost@1.0.0.yaml:1" in message
    assert "not registered" in message


def test_invalid_parameter_points_to_its_line(tmp_path: Path) -> None:
    write(tmp_path, "trend@1.0.0.yaml", VALID.replace("ema_fast: 20", "ema_fast: 1"))
    message = error_of(tmp_path)
    assert "trend@1.0.0.yaml:8" in message
    assert "parameters.ema_fast" in message


def test_misspelled_parameter_is_rejected_not_ignored(tmp_path: Path) -> None:
    write(tmp_path, "trend@1.0.0.yaml", VALID.replace("ema_fast: 20", "ema_fsat: 20"))
    message = error_of(tmp_path)
    assert "parameters.ema_fsat" in message


def test_schema_error_points_to_its_line(tmp_path: Path) -> None:
    write(tmp_path, "trend@1.0.0.yaml", VALID.replace("history_bars: 300", "history_bars: 0"))
    assert "trend@1.0.0.yaml:6" in error_of(tmp_path)


def test_every_problem_of_every_file_is_reported_at_once(tmp_path: Path) -> None:
    write(tmp_path, "trend@1.0.0.yaml", VALID.replace("ema_fast: 20", "ema_fast: 1"))
    write(
        tmp_path, "trend@2.0.0.yaml", VALID.replace("1.0.0", "2.0.0").replace("[M15, H1]", "[M7]")
    )
    write(tmp_path, "broken@1.0.0.yaml", "strategy_id: [unclosed\n")
    message = error_of(tmp_path)
    assert "trend@1.0.0.yaml:8" in message
    assert "trend@2.0.0.yaml:5" in message
    assert "broken@1.0.0.yaml" in message


def test_files_other_than_yaml_are_ignored(tmp_path: Path) -> None:
    write(tmp_path, "trend@1.0.0.yaml", VALID)
    write(tmp_path, "README.md", "notes")
    assert set(load_strategy_catalog(tmp_path, REGISTRY)) == {"trend@1.0.0"}


def test_empty_directory_gives_an_empty_catalog(tmp_path: Path) -> None:
    assert load_strategy_catalog(tmp_path, REGISTRY) == {}


def test_missing_directory_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_strategy_catalog(tmp_path / "absent", REGISTRY)
