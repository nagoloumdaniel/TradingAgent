import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.indicators.entry_filter import (
    DEFAULT_VOLATILITY_LOOKBACK,
    EntryFilter,
    SessionFilter,
    VolatilityFilter,
)
from tradingagent.indicators.regime import DEFAULT_CALM_RATIO, DEFAULT_VOLATILE_RATIO, Volatility
from tradingagent.indicators.session import Session

STRATEGY_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
VERSION_PATTERN = r"^\d+\.\d+\.\d+$"
_REF = re.compile(rf"^({STRATEGY_ID_PATTERN[1:-1]})@({VERSION_PATTERN[1:-1]})$")

#: La période d'ATR par défaut du dépôt, celle du harnais (`backtest.harness`). Elle est écrite
#: ici parce que le manifeste en a besoin pour compiler un filtre de volatilité, et que le
#: harnais importe le manifeste et non l'inverse : la définir dans les deux sens ferait un
#: cycle. `tests/backtest/test_entry_filter_parity.py` vérifie que les deux valeurs coïncident.
DEFAULT_ATR_PERIOD = 14


class SessionRule(BaseModel):
    """`session_filter` : les séances UTC où la stratégie a le droit d'entrer.

    Un manifeste qui ne déclare pas cette clé garde exactement le comportement qu'il avait :
    aucune séance n'est filtrée. Les nommer est donc un acte explicite, et une séance absente
    de `allowed` est refusée — y compris `off`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: tuple[Session, ...] = Field(min_length=1)

    @field_validator("allowed")
    @classmethod
    def _no_duplicates(cls, value: tuple[Session, ...]) -> tuple[Session, ...]:
        if len(set(value)) != len(value):
            raise ValueError("entries must be unique")
        return value


class VolatilityRule(BaseModel):
    """`volatility_filter` : les régimes de volatilité où la stratégie a le droit d'entrer.

    Le régime vient du rapport ATR courant / moyenne de l'ATR (`indicators/regime.py`), jamais
    d'un niveau en points. Les seuils par défaut sont ceux du module `regime` ; les écrire ici
    les rend vérifiables dans un fichier versionné plutôt que dans le code.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: tuple[Volatility, ...] = Field(min_length=1)
    lookback: int = Field(default=DEFAULT_VOLATILITY_LOOKBACK, ge=1)
    calm_ratio: float = Field(default=DEFAULT_CALM_RATIO, ge=0)
    volatile_ratio: float = Field(default=DEFAULT_VOLATILE_RATIO, ge=0)

    @field_validator("allowed")
    @classmethod
    def _no_duplicates(cls, value: tuple[Volatility, ...]) -> tuple[Volatility, ...]:
        if len(set(value)) != len(value):
            raise ValueError("entries must be unique")
        return value

    @model_validator(mode="after")
    def _coherent_bands(self) -> "VolatilityRule":
        if self.calm_ratio > self.volatile_ratio:
            raise ValueError(
                f"calm_ratio ({self.calm_ratio}) must not exceed volatile_ratio "
                f"({self.volatile_ratio})"
            )
        return self


class EntryFilterRule(BaseModel):
    """`entry_filter` : la porte qui refuse une entrée, dans le backtest comme en production.

    Elle ne peut que **refuser** : `strategies.evaluation.evaluate` l'applique après que la
    stratégie a produit son candidat, et un refus rend un `OutcomeKind.FILTERED` sans candidat.
    Elle ne crée jamais de signal, ne change jamais une taille et ne touche jamais un stop
    (C-002).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    session: SessionRule | None = None
    volatility: VolatilityRule | None = None

    @model_validator(mode="after")
    def _at_least_one_rule(self) -> "EntryFilterRule":
        if self.session is None and self.volatility is None:
            raise ValueError(
                "entry_filter must declare at least one of `session` or `volatility`: an "
                "empty filter is a configuration mistake, not a no-op"
            )
        return self


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
    #: La période d'ATR avec laquelle un filtre de volatilité mesure le marché. Elle doit être
    #: celle dont la stratégie se sert pour son stop : deux ATR différents décriraient deux
    #: marchés différents, et le filtre jugerait une entrée sur une volatilité que la stratégie
    #: n'a jamais regardée. Le défaut est celui du harnais, donc un manifeste qui ne dit rien
    #: garde exactement le comportement qu'il avait.
    atr_period: int = Field(default=DEFAULT_ATR_PERIOD, ge=1)
    #: Absent, le filtre est inerte : `evaluate` rend exactement ce qu'il rendait avant.
    entry_filter: EntryFilterRule | None = None

    @field_validator("allowed_symbols", "timeframes")
    @classmethod
    def _no_duplicates(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if len(set(value)) != len(value):
            raise ValueError("entries must be unique")
        return value

    @model_validator(mode="after")
    def _atr_period_agrees_with_the_strategy_parameters(self) -> "StrategyManifest":
        """Un manifeste ne peut pas déclarer deux ATR différents pour le même marché.

        Les paramètres d'une stratégie sont libres (`parameters: dict[str, Any]`) parce que
        chaque règle a les siens, et `atr_period` y est le nom conventionnel. S'il est présent,
        il doit valoir la période du manifeste : sinon le filtre de volatilité mesurerait une
        autre volatilité que celle du stop, et rien ne le dirait.
        """
        declared = self.parameters.get("atr_period")
        if declared is None:
            return self
        if not isinstance(declared, int) or isinstance(declared, bool):
            raise ValueError(f"parameters.atr_period must be an integer, got {declared!r}")
        if declared != self.atr_period:
            raise ValueError(
                f"atr_period {self.atr_period} contradicts parameters.atr_period {declared}: "
                "the volatility filter and the strategy must measure the same ATR"
            )
        return self

    @property
    def ref(self) -> str:
        return f"{self.strategy_id}@{self.version}"

    @property
    def primary_timeframe(self) -> Timeframe:
        return self.timeframes[0]

    def entry_policy(self) -> EntryFilter:
        """La porte compilée, avec l'ATR que la stratégie utilise déjà.

        Un manifeste sans `entry_filter` rend un filtre **inerte** : `EntryFilter.active` est
        faux et toute entrée passe. C'est la garantie que l'ajout de cette clé ne change rien
        tant que personne ne l'écrit.
        """
        rule = self.entry_filter
        if rule is None:
            return EntryFilter(atr_period=self.atr_period)
        return EntryFilter(
            session=(None if rule.session is None else SessionFilter(allowed=rule.session.allowed)),
            volatility=(
                None
                if rule.volatility is None
                else VolatilityFilter(
                    allowed=rule.volatility.allowed,
                    lookback=rule.volatility.lookback,
                    calm_ratio=rule.volatility.calm_ratio,
                    volatile_ratio=rule.volatility.volatile_ratio,
                )
            ),
            atr_period=self.atr_period,
        )


def parse_ref(ref: str) -> tuple[str, str]:
    match = _REF.match(ref)
    if match is None:
        raise ValueError(f"malformed strategy reference {ref!r}, expected <id>@<major.minor.patch>")
    return match.group(1), match.group(2)
