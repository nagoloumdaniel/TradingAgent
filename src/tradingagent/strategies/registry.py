import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from tradingagent.strategies.base import Strategy
from tradingagent.strategies.library.witness import Witness
from tradingagent.strategies.manifest import STRATEGY_ID_PATTERN

StrategyClass = type[Strategy[Any]]


def build_registry(*classes: StrategyClass) -> Mapping[str, StrategyClass]:
    """Explicit list of runnable strategies.

    Classes are never resolved from a name read in configuration: a YAML file must not
    decide which code runs.
    """
    registry: dict[str, StrategyClass] = {}
    for strategy_class in classes:
        strategy_id = strategy_class.strategy_id
        if not re.match(STRATEGY_ID_PATTERN, strategy_id):
            raise ValueError(f"{strategy_class.__name__}: malformed strategy_id {strategy_id!r}")
        model_config = strategy_class.parameters_model.model_config
        if model_config.get("frozen") is not True:
            raise ValueError(f"{strategy_class.__name__}: parameters_model must be frozen")
        if model_config.get("extra") != "forbid":
            raise ValueError(
                f"{strategy_class.__name__}: parameters_model must reject unknown keys "
                "(extra='forbid'), or a misspelled parameter silently falls back to its default"
            )
        if strategy_id in registry:
            raise ValueError(f"duplicate strategy_id {strategy_id!r}")
        registry[strategy_id] = strategy_class
    return MappingProxyType(registry)


REGISTRY = build_registry(Witness)
