from decimal import Decimal
from pathlib import Path

import pytest

from tradingagent.config.agent import AgentConfig, load_agent_config
from tradingagent.config.errors import ConfigError
from tradingagent.core.mode import TradingMode
from tradingagent.strategies.manifest import StrategyManifest

KNOWN_SYMBOLS = {"frxXAUUSD", "cryBTCUSD", "cryETHUSD"}


def manifest(strategy_id: str, symbols: list[str], max_mode: str) -> StrategyManifest:
    return StrategyManifest.model_validate(
        {
            "strategy_id": strategy_id,
            "version": "1.0.0",
            "max_mode": max_mode,
            "allowed_symbols": symbols,
            "timeframes": ["M15"],
            "history_bars": 100,
        }
    )


GOLD_TREND = manifest("gold_trend", ["frxXAUUSD"], "PAPER")
CRYPTO_BREAKOUT = manifest("crypto_breakout", ["cryBTCUSD", "cryETHUSD"], "SIGNAL")
STRATEGIES = {m.ref: m for m in (GOLD_TREND, CRYPTO_BREAKOUT)}

VALID = """\
markets:
  - symbol: frxXAUUSD
    strategy: gold_trend@1.0.0
  - symbol: cryBTCUSD
    strategy: crypto_breakout@1.0.0
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


def load(tmp_path: Path, text: str, mode: TradingMode = TradingMode.SIGNAL) -> AgentConfig:
    return load_agent_config(
        write(tmp_path, text), known_symbols=KNOWN_SYMBOLS, strategies=STRATEGIES, mode=mode
    )


def error_of(tmp_path: Path, text: str, mode: TradingMode = TradingMode.SIGNAL) -> str:
    with pytest.raises(ConfigError) as caught:
        load(tmp_path, text, mode)
    return str(caught.value)


def test_valid_file_loads(tmp_path: Path) -> None:
    config = load(tmp_path, VALID)
    assert [market.symbol for market in config.markets] == ["frxXAUUSD", "cryBTCUSD"]
    assert [market.strategy for market in config.markets] == [
        "gold_trend@1.0.0",
        "crypto_breakout@1.0.0",
    ]
    assert config.markets[0].enabled is True
    assert config.risk.simulated.risk_per_trade_pct == Decimal("0.5")
    assert config.risk.live.reference_capital == Decimal("100")


def test_decimal_values_are_exact(tmp_path: Path) -> None:
    config = load(tmp_path, VALID.replace("risk_per_trade_pct: 0.5", "risk_per_trade_pct: 0.1"))
    assert config.risk.simulated.risk_per_trade_pct == Decimal("0.1")


def test_unknown_symbol_blocks_startup_with_its_line(tmp_path: Path) -> None:
    message = error_of(tmp_path, VALID.replace("symbol: cryBTCUSD", "symbol: cryDOGEUSD"))
    assert "agent.yaml:4" in message
    assert "cryDOGEUSD" in message


def test_disabled_market_with_unknown_symbol_still_blocks_startup(tmp_path: Path) -> None:
    text = VALID.replace("  - symbol: cryBTCUSD\n", "  - symbol: cryTYPO\n    enabled: false\n")
    assert "cryTYPO" in error_of(tmp_path, text)


def test_unknown_strategy_reference_blocks_startup_with_its_line(tmp_path: Path) -> None:
    message = error_of(tmp_path, VALID.replace("crypto_breakout@1.0.0", "ghost@1.0.0"))
    assert "agent.yaml:5" in message
    assert "ghost@1.0.0" in message


def test_unpinned_strategy_version_is_rejected(tmp_path: Path) -> None:
    message = error_of(tmp_path, VALID.replace("crypto_breakout@1.0.0", "crypto_breakout"))
    assert "agent.yaml:5" in message
    assert "reference" in message


def test_duplicate_symbol_is_rejected_with_its_line(tmp_path: Path) -> None:
    text = VALID.replace("symbol: cryBTCUSD", "symbol: frxXAUUSD").replace(
        "crypto_breakout@1.0.0", "gold_trend@1.0.0"
    )
    message = error_of(tmp_path, text)
    assert "agent.yaml:4" in message
    assert "duplicate" in message.lower()


def test_strategy_is_refused_on_a_symbol_it_does_not_allow(tmp_path: Path) -> None:
    # RM-003: a gold strategy cannot be assigned to a crypto pair without a new manifest.
    message = error_of(tmp_path, VALID.replace("crypto_breakout@1.0.0", "gold_trend@1.0.0"))
    assert "agent.yaml:5" in message
    assert "cryBTCUSD" in message
    assert "RM-003" in message


@pytest.mark.parametrize("mode", [TradingMode.OBSERVATION, TradingMode.SIGNAL])
def test_mode_within_every_strategy_ceiling_is_accepted(tmp_path: Path, mode: TradingMode) -> None:
    assert len(load(tmp_path, VALID, mode).markets) == 2


@pytest.mark.parametrize("mode", [TradingMode.PAPER, TradingMode.DEMO, TradingMode.LIVE])
def test_mode_above_a_strategy_ceiling_blocks_startup(tmp_path: Path, mode: TradingMode) -> None:
    # RM-016: crypto_breakout is capped at SIGNAL; promotion needs a new manifest version.
    message = error_of(tmp_path, VALID, mode)
    assert "agent.yaml:5" in message
    assert "RM-016" in message


def test_every_market_above_its_ceiling_is_reported(tmp_path: Path) -> None:
    message = error_of(tmp_path, VALID, TradingMode.DEMO)
    assert "agent.yaml:3" in message
    assert "agent.yaml:5" in message


def test_disabled_market_above_its_ceiling_still_blocks_startup(tmp_path: Path) -> None:
    # It could be re-enabled from Telegram later, running above its validated mode.
    text = VALID.replace("  - symbol: cryBTCUSD\n", "  - symbol: cryBTCUSD\n    enabled: false\n")
    assert "RM-016" in error_of(tmp_path, text, TradingMode.PAPER)


def test_timeframes_are_no_longer_declared_per_market(tmp_path: Path) -> None:
    text = VALID.replace(
        "    strategy: gold_trend@1.0.0\n",
        "    strategy: gold_trend@1.0.0\n    timeframes: [M15]\n",
    )
    message = error_of(tmp_path, text)
    assert "agent.yaml:4" in message
    assert "timeframes" in message


def test_misspelled_key_is_rejected_with_its_line(tmp_path: Path) -> None:
    message = error_of(tmp_path, VALID.replace("strategy: gold_trend", "strategie: gold_trend"))
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
    message = error_of(tmp_path, VALID.replace("symbol: frxXAUUSD", "symbol: [frxXAUUSD"))
    assert "agent.yaml:" in message


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    error_of(tmp_path, "")


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"agent\.yaml"):
        load_agent_config(
            tmp_path / "agent.yaml",
            known_symbols=KNOWN_SYMBOLS,
            strategies=STRATEGIES,
            mode=TradingMode.SIGNAL,
        )


def test_every_problem_is_reported_at_once(tmp_path: Path) -> None:
    text = VALID.replace("symbol: cryBTCUSD", "symbol: cryDOGEUSD").replace(
        "strategy: gold_trend", "strategie: gold_trend"
    )
    message = error_of(tmp_path, text)
    assert "agent.yaml:3" in message
    assert "agent.yaml:4" in message
