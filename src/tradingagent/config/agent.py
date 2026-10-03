from collections.abc import Collection, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tradingagent.config.errors import ConfigError
from tradingagent.core.timeframe import Timeframe

LIVE_RISK_PER_TRADE_CAP = Decimal(5)

Location = tuple[int | str, ...]
Problem = tuple[Location, str]
Percent = Annotated[Decimal, Field(gt=0, le=100)]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MarketConfig(_Strict):
    symbol: str = Field(pattern=r"^[A-Za-z0-9_]+$")
    enabled: bool = True
    strategy: str = Field(min_length=1)
    timeframes: tuple[Timeframe, ...] = Field(min_length=1)

    @field_validator("timeframes")
    @classmethod
    def _unique_timeframes(cls, value: tuple[Timeframe, ...]) -> tuple[Timeframe, ...]:
        if len(set(value)) != len(value):
            raise ValueError("timeframes must be unique")
        return value


class RiskProfile(_Strict):
    risk_per_trade_pct: Percent
    daily_loss_pct: Percent
    weekly_loss_pct: Percent
    max_drawdown_pct: Percent
    max_open_positions: int = Field(ge=1)
    max_positions_per_market: int = Field(ge=1)

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
    path: Path, *, known_symbols: Collection[str], known_strategies: Collection[str]
) -> AgentConfig:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"{path}: cannot read configuration ({error.strerror})") from None

    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        line = mark.line + 1 if mark is not None else "?"
        raise ConfigError(f"{path}:{line}: invalid YAML: {error}") from None
    if root is None:
        raise ConfigError(f"{path}: configuration is empty")

    problems = list(_reference_problems(data, known_symbols, known_strategies))
    config: AgentConfig | None = None
    try:
        config = AgentConfig.model_validate(data)
    except ValidationError as error:
        problems.extend(
            (tuple(detail["loc"]), detail["msg"])
            for detail in error.errors(include_input=False, include_url=False)
        )
    if problems or config is None:
        raise ConfigError(_render(path, root, problems))
    return config


def _reference_problems(
    data: Any, known_symbols: Collection[str], known_strategies: Collection[str]
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
        strategy = market.get("strategy")
        if isinstance(strategy, str) and strategy not in known_strategies:
            yield ("markets", index, "strategy"), f"unknown strategy {strategy!r}"


def _render(path: Path, root: yaml.Node, problems: list[Problem]) -> str:
    located = sorted(
        {(_line_of(root, location), _dotted(location), message) for location, message in problems}
    )
    body = "\n".join(f"  {path}:{line}: {where}: {message}" for line, where, message in located)
    return "Invalid agent configuration:\n" + body


def _line_of(root: yaml.Node, location: Location) -> int:
    node = root
    for key in location:
        child: yaml.Node | None = None
        if isinstance(node, yaml.MappingNode):
            child = next(
                (
                    value
                    for name, value in node.value
                    if isinstance(name, yaml.ScalarNode) and name.value == str(key)
                ),
                None,
            )
        elif (
            isinstance(node, yaml.SequenceNode)
            and isinstance(key, int)
            and 0 <= key < len(node.value)
        ):
            child = node.value[key]
        if child is None:
            break
        node = child
    return int(node.start_mark.line) + 1


def _dotted(location: Location) -> str:
    rendered = ""
    for key in location:
        rendered += f"[{key}]" if isinstance(key, int) else (f".{key}" if rendered else key)
    return rendered or "(root)"
