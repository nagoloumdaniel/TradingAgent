"""The `max_mode` ceiling of `config/strategies/`: DEMO freely, LIVE only with a document.

DEMO is a demonstration account where no real money can be lost, so a manifest may declare it
on its own. `LIVE` takes the complete nine-gate validation of §49, each gate named in the header
banner, and its date: a manifest that declares LIVE without it is refused by the catalog loader,
which is the only thing standing between a comment and the real account (RM-016, RM-017).
"""

from pathlib import Path

import pytest

from tradingagent.config.errors import ConfigError
from tradingagent.config.strategy_catalog import (
    header_banner,
    load_strategy_catalog,
    mode_ceiling_problems,
)
from tradingagent.core.mode import TradingMode, mode_rank
from tradingagent.core.states import ValidationStage
from tradingagent.strategies.registry import REGISTRY

SHIPPED = Path(__file__).resolve().parents[2] / "config" / "strategies"

BODY = """\
strategy_id: witness
version: 1.0.0
max_mode: {max_mode}
allowed_symbols: [XAUUSD]
timeframes: [M15]
history_bars: 300
parameters:
  ema_fast: 20
  ema_slow: 50
  atr_period: 14
  stop_atr_multiplier: 1.5
  take_profit_rr: 2.0
  entry_zone_atr: 0.1
"""


def manifest_text(max_mode: str, banner: str = "") -> str:
    return banner + BODY.format(max_mode=max_mode)


def write(directory: Path, ref: str, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ref}.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def live_validation_banner() -> str:
    """The complete validation a LIVE ceiling takes: every gate of §49 named, and dated."""
    stages = ", ".join(stage.value for stage in ValidationStage)
    return (
        f"# VALIDATION COMPLETE {len(tuple(ValidationStage))}/9 portes, franchies le 2026-10-08 "
        f"hors echantillon : {stages}.\n"
    )


# --------------------------------------------------------------------------------------
# DEMO needs no document
# --------------------------------------------------------------------------------------


def test_a_demo_manifest_needs_no_banner(tmp_path: Path) -> None:
    write(tmp_path, "witness@1.0.0", manifest_text("DEMO"))
    catalog = load_strategy_catalog(tmp_path, REGISTRY)
    assert catalog["witness@1.0.0"].manifest.max_mode is TradingMode.DEMO


def test_removed_modes_are_no_longer_valid_ceilings(tmp_path: Path) -> None:
    for removed in ("SIGNAL", "OBSERVATION", "PAPER"):
        write(tmp_path, "witness@1.0.0", manifest_text(removed))
        with pytest.raises(ConfigError):
            load_strategy_catalog(tmp_path, REGISTRY)


def test_every_shipped_manifest_loads_within_the_ceiling() -> None:
    # Loading the shipped directory is itself the check: the loader is all-or-nothing.
    catalog = load_strategy_catalog(SHIPPED, REGISTRY)
    assert catalog
    for ref, loaded in catalog.items():
        text = (SHIPPED / f"{ref}.yaml").read_text(encoding="utf-8")
        assert mode_ceiling_problems(text, loaded.manifest.max_mode) == [], ref


# --------------------------------------------------------------------------------------
# LIVE stays impossible without the complete validation
# --------------------------------------------------------------------------------------


def test_demo_is_a_bounded_ceiling_below_live() -> None:
    assert mode_rank(TradingMode.DEMO) < mode_rank(TradingMode.LIVE)


def test_live_is_refused_without_the_validation_banner(tmp_path: Path) -> None:
    write(tmp_path, "witness@1.0.0", manifest_text("LIVE"))
    with pytest.raises(ConfigError) as caught:
        load_strategy_catalog(tmp_path, REGISTRY)
    message = str(caught.value)
    assert "max_mode LIVE requires the complete validation of the nine gates" in message
    assert "missing:" in message


def test_live_is_refused_when_the_validation_names_only_some_gates(tmp_path: Path) -> None:
    partial = live_validation_banner().replace("monte_carlo, ", "")
    write(tmp_path, "witness@1.0.0", manifest_text("LIVE", partial))
    with pytest.raises(ConfigError) as caught:
        load_strategy_catalog(tmp_path, REGISTRY)
    assert "monte_carlo" in str(caught.value)


def test_live_is_accepted_only_with_the_complete_validation_named(tmp_path: Path) -> None:
    write(tmp_path, "witness@1.0.0", manifest_text("LIVE", live_validation_banner()))
    catalog = load_strategy_catalog(tmp_path, REGISTRY)
    assert catalog["witness@1.0.0"].manifest.max_mode is TradingMode.LIVE


# --------------------------------------------------------------------------------------
# The banner reader itself
# --------------------------------------------------------------------------------------


def test_the_banner_is_the_comment_block_above_the_first_key() -> None:
    text = "# first\n#\n#   indented\n\nstrategy_id: witness\n# not in the banner\n"
    assert header_banner(text) == "first\n\nindented"
    assert header_banner("strategy_id: witness\n") == ""


def test_a_manifest_at_the_ceiling_is_never_a_problem() -> None:
    assert mode_ceiling_problems("", TradingMode.DEMO) == []
