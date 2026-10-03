import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.timeframe import Timeframe

STRATEGY_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
VERSION_PATTERN = r"^\d+\.\d+\.\d+$"
_REF = re.compile(rf"^({STRATEGY_ID_PATTERN[1:-1]})@({VERSION_PATTERN[1:-1]})$")


class StrategyManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_id: str = Field(pattern=STRATEGY_ID_PATTERN)
    version: str = Field(pattern=VERSION_PATTERN)
    max_mode: TradingMode
    allowed_symbols: tuple[str, ...] = Field(min_length=1)
    timeframes: tuple[Timeframe, ...] = Field(min_length=1)
    history_bars: int = Field(ge=1, le=10_000)
    expiry_bars: int = Field(default=1, ge=1)
    ai_filter: AiFilter = AiFilter.SHADOW
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("allowed_symbols", "timeframes")
    @classmethod
    def _no_duplicates(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if len(set(value)) != len(value):
            raise ValueError("entries must be unique")
        return value

    @property
    def ref(self) -> str:
        return f"{self.strategy_id}@{self.version}"

    @property
    def primary_timeframe(self) -> Timeframe:
        return self.timeframes[0]


def parse_ref(ref: str) -> tuple[str, str]:
    match = _REF.match(ref)
    if match is None:
        raise ValueError(f"malformed strategy reference {ref!r}, expected <id>@<major.minor.patch>")
    return match.group(1), match.group(2)
