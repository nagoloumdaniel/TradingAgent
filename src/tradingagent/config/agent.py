from collections.abc import Collection, Iterator, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tradingagent.config._yaml import Problem, read_yaml, render, validation_problems
from tradingagent.config.errors import ConfigError
from tradingagent.core.mode import TradingMode, mode_rank
from tradingagent.news.config import NewsConfig
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
    # Operator decision 2026-10-30: these three counts are no longer caps by default. None
    # (YAML `null`, or the key omitted) means unlimited; the risk engine still counts and
    # reports them. A value, when given, stays a hard limit and must be at least 1.
    max_open_positions: int | None = Field(default=None, ge=1)
    max_positions_per_market: int | None = Field(default=None, ge=1)
    max_trades_per_day: int | None = Field(default=None, ge=1)
    cooldown_after_losses: int = Field(default=3, ge=1)
    cooldown_hours: Decimal = Field(default=Decimal(4), ge=0)
    # Operator decision 2026-10-30: after this many consecutive losing trades closed within
    # the same UTC day, no new trade until the next 00:00 UTC. None disables the rule.
    stop_after_consecutive_losses_per_day: int | None = Field(default=3, ge=1)
    max_spread_stop_pct: Percent = Decimal(10)
    margin_usage_pct: Percent = Decimal(50)
    max_volume: Decimal | None = Field(default=None, gt=0)
    # §21: total notional across every market, as a percentage of the capital. Gold and
    # bitcoin are treated as fully correlated, so their exposures simply add up.
    max_total_exposure_pct: Percent = Decimal(200)
    # §21: tolerated slippage, in multiples of the observed spread. 1 means "never worse
    # than the spread".
    max_slippage_to_spread: Decimal = Field(default=Decimal(1), gt=0)

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
        if (
            self.max_positions_per_market is not None
            and self.max_open_positions is not None
            and self.max_positions_per_market > self.max_open_positions
        ):
            raise ValueError("max_positions_per_market cannot exceed max_open_positions")
        return self


#: The three counts that are unlimited when null. In live they must be written down.
EXPLICIT_LIVE_CAPS = ("max_open_positions", "max_positions_per_market", "max_trades_per_day")


class LiveRiskProfile(RiskProfile):
    reference_capital: Decimal = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # Real money: "unlimited" is a decision, not an oversight. The simulated profile defaults
    # these to None (unlimited); the live one has no default, so a forgotten key (a typo, a
    # copy-paste from an older file) is a config error instead of silently uncapped trading.
    # Write an integer >= 1, or `null` to say unlimited on purpose.
    max_open_positions: int | None = Field(ge=1)
    max_positions_per_market: int | None = Field(ge=1)
    max_trades_per_day: int | None = Field(ge=1)

    @model_validator(mode="before")
    @classmethod
    def _caps_are_explicit(cls, data: Any) -> Any:
        if isinstance(data, dict):
            missing = [key for key in EXPLICIT_LIVE_CAPS if key not in data]
            if missing:
                raise ValueError(
                    f"risk.live : {', '.join(missing)} obligatoire(s) en mode réel : écrire un "
                    "entier >= 1, ou null pour « illimité » (choix explicite, jamais un oubli)"
                )
        return data

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
    # Optional: economic-calendar blackout and AI reading. Defaults apply without the section.
    news: NewsConfig = NewsConfig()


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
