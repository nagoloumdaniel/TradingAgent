"""The `max_mode` ceiling of `config/strategies/`: never an escalation without a document.

The invariant of 2026-10-08. The rule it replaces -- "a shipped manifest never exceeds
SIGNAL" -- was a proxy: what it protects is that the ceiling of a strategy is never raised
*without the operator saying so and writing down what the validation did not show*. The
operator asked for a DEMO rehearsal, and `witness@1.1.1` / `trend_breakout@1.0.1` document
it in their header. That is allowed. A manifest that declares the same ceiling with no such
banner is refused by the catalog loader, which is the only thing standing between a comment
and an account.

`LIVE` is deliberately a different question. An operator derogation bounds the ceiling at
DEMO -- a demonstration account, where no real money can be lost -- and DEMO is below LIVE in
the exposure order. Raising the ceiling to LIVE takes the complete nine-gate validation of
§49, named in the same banner: a derogation is not a validation (RM-016).
"""

import re
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
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

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

#: A banner that satisfies the invariant: the marker, its date, and the figures of the
#: validation that is missing -- measured profit factor and the one required, the minimum
#: p-value and the Bonferroni line it is read against, and the survivors after correction.
BANNER = """\
# DEROGATION OPERATEUR du 2026-10-08 : plafond releve de SIGNAL a DEMO, sans validation.
# Chiffres de validation qui manquent : profit factor net mesure 0,81, exige 1,20 ;
# p-value minimale 0,3497, contre la ligne de Bonferroni 0,016667 ; 0 survivant sur 6.
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
# (a) a ceiling above SIGNAL without a documented derogation is refused
# --------------------------------------------------------------------------------------


def test_a_manifest_above_signal_without_a_derogation_is_refused(tmp_path: Path) -> None:
    write(tmp_path, "witness@1.0.0", manifest_text("DEMO"))
    with pytest.raises(ConfigError) as caught:
        load_strategy_catalog(tmp_path, REGISTRY)
    message = str(caught.value)
    # The refusal is located on the line that declares the ceiling.
    assert "witness@1.0.0.yaml:3" in message
    assert "max_mode DEMO is above the SIGNAL ceiling" in message
    assert "may not escalate silently" in message
    assert "DEROGATION OPERATEUR" in message


def test_the_refusal_names_every_piece_of_the_banner_that_is_missing(tmp_path: Path) -> None:
    # A banner that claims the derogation but names no figure is a claim, not a document.
    thin = "# DEROGATION OPERATEUR du 2026-10-08 : plafond releve de SIGNAL a DEMO.\n"
    write(tmp_path, "witness@1.0.0", manifest_text("DEMO", thin))
    with pytest.raises(ConfigError) as caught:
        load_strategy_catalog(tmp_path, REGISTRY)
    message = str(caught.value)
    assert "profit factor" in message
    assert "p-value" in message
    assert "Bonferroni" in message
    assert "survivors" in message


def test_a_manifest_at_or_below_the_ceiling_needs_no_derogation(tmp_path: Path) -> None:
    write(tmp_path, "witness@1.0.0", manifest_text("SIGNAL"))
    write(tmp_path, "witness@1.1.0", manifest_text("OBSERVATION").replace("1.0.0", "1.1.0"))
    catalog = load_strategy_catalog(tmp_path, REGISTRY)
    assert catalog["witness@1.0.0"].manifest.max_mode is TradingMode.SIGNAL
    assert catalog["witness@1.1.0"].manifest.max_mode is TradingMode.OBSERVATION


def test_a_documented_derogation_is_accepted_above_the_ceiling(tmp_path: Path) -> None:
    write(tmp_path, "witness@1.0.0", manifest_text("DEMO", BANNER))
    write(tmp_path, "witness@1.1.0", manifest_text("PAPER", BANNER).replace("1.0.0", "1.1.0"))
    catalog = load_strategy_catalog(tmp_path, REGISTRY)
    assert catalog["witness@1.0.0"].manifest.max_mode is TradingMode.DEMO
    assert catalog["witness@1.1.0"].manifest.max_mode is TradingMode.PAPER


# --------------------------------------------------------------------------------------
# (b) every shipped manifest above SIGNAL carries the derogation, figures included
# --------------------------------------------------------------------------------------


def test_every_shipped_manifest_above_signal_carries_its_derogation() -> None:
    catalog = load_strategy_catalog(SHIPPED, REGISTRY)
    above: list[tuple[str, str]] = []
    for ref, loaded in catalog.items():
        text = (SHIPPED / f"{ref}.yaml").read_text(encoding="utf-8")
        assert mode_ceiling_problems(text, loaded.manifest.max_mode) == [], ref
        if mode_rank(loaded.manifest.max_mode) > mode_rank(TradingMode.SIGNAL):
            above.append((ref, header_banner(text)))
    assert above, "no shipped manifest is above SIGNAL: this test would prove nothing"
    for ref, banner in above:
        assert ISO_DATE.search(banner), ref
        assert "profit factor" in banner, ref
        assert "p-value" in banner, ref
        assert "Bonferroni" in banner, ref
        assert re.search(r"\d+\s+survivant", banner), ref
        # The banner has to be *in front of* the keys, not buried under them.
        assert banner.strip().startswith("#") is False  # stripped of its '#' by header_banner
        raw = (SHIPPED / f"{ref}.yaml").read_text(encoding="utf-8")
        assert raw.index("DEROGATION OPERATEUR") < raw.index("strategy_id:"), ref


def test_no_shipped_manifest_is_above_the_ceiling_without_the_catalog_refusing_it() -> None:
    # Loading the shipped directory is itself the check: the loader is all-or-nothing, so a
    # manifest that lost its banner would raise here rather than reach a running agent.
    catalog = load_strategy_catalog(SHIPPED, REGISTRY)
    assert catalog
    for loaded in catalog.values():
        assert mode_rank(loaded.manifest.max_mode) <= mode_rank(TradingMode.LIVE)


# --------------------------------------------------------------------------------------
# The bounded ceiling: LIVE stays impossible without the complete validation
# --------------------------------------------------------------------------------------


def test_demo_is_a_bounded_ceiling_below_live() -> None:
    # The property that protects real money: a derogation cannot reach LIVE by itself.
    assert mode_rank(TradingMode.DEMO) < mode_rank(TradingMode.LIVE)
    assert mode_rank(TradingMode.SIGNAL) < mode_rank(TradingMode.DEMO)


def test_live_is_refused_when_only_a_derogation_raises_the_ceiling(tmp_path: Path) -> None:
    # The banner the operator wrote for the DEMO rehearsal, on a LIVE manifest: refused.
    write(tmp_path, "witness@1.0.0", manifest_text("LIVE", BANNER))
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
    for mode in (TradingMode.OBSERVATION, TradingMode.SIGNAL):
        assert mode_ceiling_problems("", mode) == []
