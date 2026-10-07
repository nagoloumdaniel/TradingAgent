"""Versioned strategy registry (cahier v3 §14, §30, §35, §49).

`StrategyRegistry` owns the lifecycle of a strategy version, `gates` holds the pure rules of
the nine-stage promotion protocol, and `cli` is the operator surface. A version is created
once under a `ref`, and only a `LIVE` version may trade.
"""

from tradingagent.registry.gates import PROMOTION_GATES, can_promote, missing_gates
from tradingagent.registry.store import (
    DuplicateStrategyRef,
    IllegalStrategyTransition,
    ImmutableLiveStrategy,
    MarketAlreadyLive,
    MissingValidationGates,
    RegistryError,
    StrategyHistory,
    StrategyRegistry,
    UnknownStrategyRef,
    parse_ref,
)

__all__ = [
    "PROMOTION_GATES",
    "DuplicateStrategyRef",
    "IllegalStrategyTransition",
    "ImmutableLiveStrategy",
    "MarketAlreadyLive",
    "MissingValidationGates",
    "RegistryError",
    "StrategyHistory",
    "StrategyRegistry",
    "UnknownStrategyRef",
    "can_promote",
    "missing_gates",
    "parse_ref",
]
