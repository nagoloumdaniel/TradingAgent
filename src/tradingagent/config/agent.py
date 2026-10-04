from collections.abc import Collection, Iterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tradingagent.config._yaml import Problem, read_yaml, render, validation_problems
from tradingagent.config.errors import ConfigError
from tradingagent.core.mode import TradingMode, mode_rank
from tradingagent.strategies.manifest import StrategyManifest, parse_ref

LIVE_RISK_PER_TRADE_CAP = Decimal(5)

Percent = Annotated[Decimal, Field(gt=0, le=100)]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MarketConfig(_Strict):
    """Timeframes are not declared here: they come from the strategy's manifest."""

    symbol: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    enabled: bool = True
    strategy: str

    @field_validator("strategy")
    @classmethod
    def _pinned_reference(cls, value: str) -> str:
        parse_ref(value)
        return value


class RiskProfile(_Strict):
    risk_per_trade_pct: Percent
    daily_loss_pct: Percent
    weekly_loss_pct: Percent
    max_drawdown_pct: Percent
    max_open_positions: int = Field(ge=1)
    max_positions_per_market: int = Field(ge=1)
    # Defaults chosen by the operator on 2026-10-04, to revisit after market analysis.
    max_trades_per_day: int = Field(default=4, ge=1)
    cooldown_after_losses: int = Field(default=3, ge=1)
    cooldown_hours: Decimal = Field(default=Decimal(4), ge=0)
    max_spread_stop_pct: Percent = Decimal(10)
    margin_usage_pct: Percent = Decimal(50)
    max_volume: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _coherent_limits(self) -> Self:
        if not (
            self.risk_per_trade_pct
            <= self.daily_loss_pct
            <= self.weekly_loss_pct
            <= self.max_drawdown_pct
        ):
            raise ValueError(
                "expected risk_per_trade_pct <= daily_loss_pct <= weekly_loss_pct"
                " <= max_drawdown_pct"
            )
        if self.max_positions_per_market > self.max_open_positions:
            raise ValueError("max_positions_per_market cannot exceed max_open_positions")
        return self


class LiveRiskProfile(RiskProfile):
    reference_capital: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("risk_per_trade_pct")
    @classmethod
    def _absolute_cap(cls, value: Decimal) -> Decimal:
        if value > LIVE_RISK_PER_TRADE_CAP:
            raise ValueError(
                f"risk_per_trade_pct is capped at {LIVE_RISK_PER_TRADE_CAP}% in live mode (RM-005)"
            )
        return value


class RiskConfig(_Strict):
    simulated: RiskProfile
    live: LiveRiskProfile


class AgentConfig(_Strict):
    markets: tuple[MarketConfig, ...] = Field(min_length=1)
    risk: RiskConfig


def load_agent_config(
    path: Path,
    *,
    known_symbols: Collection[str],
    strategies: Mapping[str, StrategyManifest],
    mode: TradingMode,
) -> AgentConfig:
    document = read_yaml(path)
    problems = list(_reference_problems(document.data, known_symbols, strategies, mode))
    config: AgentConfig | None = None
    try:
        config = AgentConfig.model_validate(document.data)
    except ValidationError as error:
        problems.extend(validation_problems(error))
    if problems or config is None:
        raise ConfigError(render("Invalid agent configuration", document.locate(problems)))
    return config


def _reference_problems(
    data: Any,
    known_symbols: Collection[str],
    strategies: Mapping[str, StrategyManifest],
    mode: TradingMode,
) -> Iterator[Problem]:
    # Runs on the raw document so these problems surface even when schema validation fails.
    markets = data.get("markets") if isinstance(data, dict) else None
    if not isinstance(markets, list):
        return
    seen: set[str] = set()
    for index, market in enumerate(markets):
        if not isinstance(market, dict):
            continue
        symbol = market.get("symbol")
        if isinstance(symbol, str):
            if symbol in seen:
                yield ("markets", index, "symbol"), f"duplicate symbol {symbol!r}"
            seen.add(symbol)
            if symbol not in known_symbols:
                yield (
                    ("markets", index, "symbol"),
                    f"unknown symbol {symbol!r}: not offered by the broker for this account",
                )
        reference = market.get("strategy")
        if not isinstance(reference, str) or not _well_formed(reference):
            continue  # malformed references are reported by schema validation
        location = ("markets", index, "strategy")
        manifest = strategies.get(reference)
        if manifest is None:
            yield location, f"unknown strategy {reference!r}: no such manifest"
            continue
        if isinstance(symbol, str) and symbol not in manifest.allowed_symbols:
            yield location, f"{reference} does not allow symbol {symbol!r} (RM-003)"
        # Checked even for disabled markets: one can be re-enabled from Telegram later.
        if mode_rank(manifest.max_mode) < mode_rank(mode):
            yield (
                location,
                f"{reference} is capped at max_mode {manifest.max_mode}, the agent runs in "
                f"{mode}: publish a new manifest version to promote it (RM-016)",
            )


def _well_formed(reference: str) -> bool:
    try:
        parse_ref(reference)
    except ValueError:
        return False
    return True
