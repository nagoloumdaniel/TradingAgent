from decimal import Decimal
from pathlib import Path

import pytest

from tradingagent.config.agent import AgentConfig, load_agent_config
from tradingagent.config.errors import ConfigError
from tradingagent.core.timeframe import Timeframe

KNOWN_SYMBOLS = {"frxXAUUSD", "cryBTCUSD", "cryETHUSD"}
KNOWN_STRATEGIES = {"witness"}

VALID = """\
markets:
  - symbol: frxXAUUSD
    strategy: witness
    timeframes: [M5, M15]
  - symbol: cryBTCUSD
    strategy: witness
    timeframes: [M15]
risk:
  simulated:
    risk_per_trade_pct: 0.5
    daily_loss_pct: 2
    weekly_loss_pct: 6
    max_drawdown_pct: 10
    max_open_positions: 2
    max_positions_per_market: 1
  live:
    reference_capital: 100
    currency: EUR
    risk_per_trade_pct: 2
    daily_loss_pct: 5
    weekly_loss_pct: 10
    max_drawdown_pct: 20
    max_open_positions: 2
    max_positions_per_market: 1
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "agent.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def load(tmp_path: Path, text: str) -> AgentConfig:
    return load_agent_config(
        write(tmp_path, text), known_symbols=KNOWN_SYMBOLS, known_strategies=KNOWN_STRATEGIES
    )


def error_of(tmp_path: Path, text: str) -> str:
    with pytest.raises(ConfigError) as caught:
        load(tmp_path, text)
    return str(caught.value)


def test_valid_file_loads(tmp_path: Path) -> None:
    config = load(tmp_path, VALID)
    assert [market.symbol for market in config.markets] == ["frxXAUUSD", "cryBTCUSD"]
    assert config.markets[0].timeframes == (Timeframe.M5, Timeframe.M15)
    assert config.markets[0].enabled is True
    assert config.risk.simulated.risk_per_trade_pct == Decimal("0.5")
    assert config.risk.live.reference_capital == Decimal("100")


def test_decimal_values_are_exact(tmp_path: Path) -> None:
    config = load(tmp_path, VALID.replace("risk_per_trade_pct: 0.5", "risk_per_trade_pct: 0.1"))
    assert config.risk.simulated.risk_per_trade_pct == Decimal("0.1")


def test_unknown_symbol_blocks_startup_with_its_line(tmp_path: Path) -> None:
    message = error_of(tmp_path, VALID.replace("cryBTCUSD", "cryDOGEUSD"))
    assert "agent.yaml:5" in message
    assert "cryDOGEUSD" in message


def test_disabled_market_with_unknown_symbol_still_blocks_startup(tmp_path: Path) -> None:
    text = VALID.replace("  - symbol: cryBTCUSD\n", "  - symbol: cryTYPO\n    enabled: false\n")
    assert "cryTYPO" in error_of(tmp_path, text)


def test_unknown_strategy_blocks_startup_with_its_line(tmp_path: Path) -> None:
    text = VALID.replace(
        "    strategy: witness\n    timeframes: [M15]", "    strategy: ghost\n    timeframes: [M15]"
    )
    message = error_of(tmp_path, text)
    assert "agent.yaml:6" in message
    assert "ghost" in message


def test_duplicate_symbol_is_rejected_with_its_line(tmp_path: Path) -> None:
    message = error_of(tmp_path, VALID.replace("cryBTCUSD", "frxXAUUSD"))
    assert "agent.yaml:5" in message
    assert "duplicate" in message.lower()


def test_invalid_timeframe_points_to_its_line(tmp_path: Path) -> None:
    message = error_of(tmp_path, VALID.replace("[M5, M15]", "[M5, M7]"))
    assert "agent.yaml:4" in message


def test_duplicate_timeframe_is_rejected(tmp_path: Path) -> None:
    assert "timeframes" in error_of(tmp_path, VALID.replace("[M5, M15]", "[M5, M5]"))


def test_misspelled_key_is_rejected_with_its_line(tmp_path: Path) -> None:
    message = error_of(
        tmp_path,
        VALID.replace(
            "    strategy: witness\n    timeframes: [M5",
            "    strategie: witness\n    timeframes: [M5",
            1,
        ),
    )
    assert "agent.yaml:3" in message


def test_live_risk_per_trade_above_absolute_cap_is_rejected(tmp_path: Path) -> None:
    text = VALID.replace("risk_per_trade_pct: 2\n", "risk_per_trade_pct: 6\n")
    assert "risk_per_trade_pct" in error_of(tmp_path, text)


@pytest.mark.parametrize(
    ("original", "broken"),
    [
        ("daily_loss_pct: 2\n", "daily_loss_pct: 7\n"),
        ("weekly_loss_pct: 6\n", "weekly_loss_pct: 11\n"),
        ("risk_per_trade_pct: 0.5\n", "risk_per_trade_pct: 3\n"),
        ("max_positions_per_market: 1\n  live", "max_positions_per_market: 3\n  live"),
    ],
)
def test_incoherent_risk_profile_is_rejected(tmp_path: Path, original: str, broken: str) -> None:
    error_of(tmp_path, VALID.replace(original, broken, 1))


@pytest.mark.parametrize("value", ["0", "-1", "101"])
def test_percentage_out_of_range_is_rejected(tmp_path: Path, value: str) -> None:
    error_of(tmp_path, VALID.replace("max_drawdown_pct: 10\n", f"max_drawdown_pct: {value}\n"))


def test_invalid_currency_is_rejected(tmp_path: Path) -> None:
    error_of(tmp_path, VALID.replace("currency: EUR", "currency: euro"))


def test_empty_market_list_is_rejected(tmp_path: Path) -> None:
    _, _, tail = VALID.partition("risk:")
    error_of(tmp_path, "markets: []\nrisk:" + tail)


def test_yaml_syntax_error_reports_its_line(tmp_path: Path) -> None:
    message = error_of(tmp_path, VALID.replace("[M5, M15]", "[M5, M15"))
    assert "agent.yaml:" in message


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    error_of(tmp_path, "")


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"agent\.yaml"):
        load_agent_config(
            tmp_path / "agent.yaml",
            known_symbols=KNOWN_SYMBOLS,
            known_strategies=KNOWN_STRATEGIES,
        )


def test_every_problem_is_reported_at_once(tmp_path: Path) -> None:
    text = VALID.replace("cryBTCUSD", "cryDOGEUSD").replace("[M5, M15]", "[M5, M7]")
    message = error_of(tmp_path, text)
    assert "agent.yaml:4" in message
    assert "agent.yaml:5" in message
