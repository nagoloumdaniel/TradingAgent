"""Operator decision 2026-10-30: position and trade counts are unlimited unless a value is
given, and three consecutive losses on one UTC day stop the day."""

from pathlib import Path

import pytest
from tests.config.test_agent_config import VALID, error_of, load

from tradingagent.config._yaml import read_yaml
from tradingagent.config.agent import AgentConfig
from tradingagent.core.mode import TradingMode
from tradingagent.risk.model import limits_for

CAPS = ("max_open_positions", "max_positions_per_market", "max_trades_per_day")


def with_keys(text: str, **values: str) -> str:
    """VALID without its caps, plus `values` on both profiles.

    The live profile has no default for the three caps, so the ones not given are written there
    as an explicit `null` (the simulated one may omit them).
    """
    return _with_keys(text, values, live_defaults=True)


def with_keys_unset_in_live(text: str, **values: str) -> str:
    """Same, but the live profile only gets `values`: what a forgotten key looks like."""
    return _with_keys(text, values, live_defaults=False)


def _with_keys(text: str, values: dict[str, str], *, live_defaults: bool) -> str:
    for key in (
        "    max_open_positions: 2\n",
        "    max_positions_per_market: 1\n",
        "    max_trades_per_day: null\n",
    ):
        text = text.replace(key, "")
    simulated = "".join(f"    {key}: {value}\n" for key, value in values.items())
    live_values = dict(values)
    if live_defaults:
        for key in CAPS:
            live_values.setdefault(key, "null")
    live = "".join(f"    {key}: {value}\n" for key, value in live_values.items())
    drawdown = "    max_drawdown_pct: {}\n"
    return text.replace(drawdown.format(10), drawdown.format(10) + simulated).replace(
        drawdown.format(20), drawdown.format(20) + live
    )


def test_null_caps_mean_unlimited(tmp_path: Path) -> None:
    config = load(tmp_path, with_keys(VALID, **dict.fromkeys(CAPS, "null")))
    for profile in (config.risk.simulated, config.risk.live):
        assert profile.max_open_positions is None
        assert profile.max_positions_per_market is None
        assert profile.max_trades_per_day is None


def test_omitted_caps_default_to_unlimited_in_the_simulated_profile_only(tmp_path: Path) -> None:
    config = load(tmp_path, with_keys(VALID))  # live written explicitly, simulated omitted
    assert config.risk.simulated.max_open_positions is None
    assert config.risk.simulated.max_positions_per_market is None
    assert config.risk.simulated.max_trades_per_day is None


def test_omitting_the_caps_in_live_is_a_config_error_naming_the_keys(tmp_path: Path) -> None:
    message = error_of(tmp_path, with_keys_unset_in_live(VALID))
    for key in CAPS:
        assert key in message
    assert "live" in message and "null" in message  # says how to ask for "unlimited"


@pytest.mark.parametrize("missing", CAPS)
def test_each_live_cap_is_required_on_its_own(tmp_path: Path, missing: str) -> None:
    given = {key: "null" for key in CAPS if key != missing}
    message = error_of(tmp_path, with_keys_unset_in_live(VALID, **given))
    assert missing in message


def test_null_is_an_explicit_unlimited_in_live(tmp_path: Path) -> None:
    config = load(tmp_path, with_keys(VALID, **dict.fromkeys(CAPS, "null")))
    assert config.risk.live.max_open_positions is None


def test_given_caps_are_kept(tmp_path: Path) -> None:
    text = VALID.replace(
        "    max_positions_per_market: 1\n  live",
        "    max_positions_per_market: 1\n    max_trades_per_day: 5\n  live",
    )
    config = load(tmp_path, text)
    assert config.risk.simulated.max_open_positions == 2
    assert config.risk.simulated.max_trades_per_day == 5


@pytest.mark.parametrize("key", CAPS)
def test_a_cap_below_one_is_rejected(tmp_path: Path, key: str) -> None:
    assert key in error_of(tmp_path, with_keys(VALID, **{key: "0"}))


def test_per_market_cap_above_the_open_cap_is_rejected_when_both_are_set(tmp_path: Path) -> None:
    error_of(tmp_path, with_keys(VALID, max_open_positions="1", max_positions_per_market="3"))


def test_the_coherence_rule_only_applies_when_both_caps_are_set(tmp_path: Path) -> None:
    only_market = load(tmp_path, with_keys(VALID, max_positions_per_market="3"))
    assert only_market.risk.simulated.max_positions_per_market == 3
    assert only_market.risk.simulated.max_open_positions is None
    only_open = load(tmp_path, with_keys(VALID, max_open_positions="1"))
    assert only_open.risk.simulated.max_open_positions == 1
    assert only_open.risk.simulated.max_positions_per_market is None


def test_the_daily_loss_streak_rule_defaults_to_three(tmp_path: Path) -> None:
    config = load(tmp_path, VALID)
    assert config.risk.simulated.stop_after_consecutive_losses_per_day == 3
    assert config.risk.live.stop_after_consecutive_losses_per_day == 3


def test_the_daily_loss_streak_rule_can_be_disabled_or_tuned(tmp_path: Path) -> None:
    text = VALID.replace(
        "    max_drawdown_pct: 10\n",
        "    max_drawdown_pct: 10\n    stop_after_consecutive_losses_per_day: null\n",
    ).replace(
        "    max_drawdown_pct: 20\n",
        "    max_drawdown_pct: 20\n    stop_after_consecutive_losses_per_day: 2\n",
    )
    config = load(tmp_path, text)
    assert config.risk.simulated.stop_after_consecutive_losses_per_day is None
    assert config.risk.live.stop_after_consecutive_losses_per_day == 2


def test_the_daily_loss_streak_rule_below_one_is_rejected(tmp_path: Path) -> None:
    text = VALID.replace(
        "    max_drawdown_pct: 10\n",
        "    max_drawdown_pct: 10\n    stop_after_consecutive_losses_per_day: 0\n",
    )
    assert "stop_after_consecutive_losses_per_day" in error_of(tmp_path, text)


def test_the_shipped_agent_yaml_is_unlimited_with_the_daily_stop() -> None:
    root = Path(__file__).resolve().parents[2]
    config = AgentConfig.model_validate(read_yaml(root / "config" / "agent.yaml").data)
    for profile in (config.risk.simulated, config.risk.live):
        assert profile.max_open_positions is None
        assert profile.max_positions_per_market is None
        assert profile.max_trades_per_day is None
        assert profile.stop_after_consecutive_losses_per_day == 3


@pytest.mark.parametrize("mode", [TradingMode.DEMO, TradingMode.LIVE])
def test_the_risk_limits_carry_the_unlimited_values_and_the_daily_stop(mode: TradingMode) -> None:
    root = Path(__file__).resolve().parents[2]
    config = AgentConfig.model_validate(read_yaml(root / "config" / "agent.yaml").data)
    limits = limits_for(mode, config.risk)
    assert limits.max_open_positions is None
    assert limits.max_positions_per_market is None
    assert limits.max_trades_per_day is None
    assert limits.stop_after_losses_per_day == 3
