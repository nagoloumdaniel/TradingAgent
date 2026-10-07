"""Promotion gates, as pure functions (cahier v3 §10, §49).

`missing_gates` answers one question: given the stages a candidate has cleared, which ones
are still open? It reads nothing, measures nothing and trusts nothing: the caller passes the
stages it has actually recorded. The nine gates are exactly `ValidationStage`, and the
protocol order is the declaration order, so a refusal is always listed the same way.
"""

from collections.abc import Iterable

from tradingagent.core.states import ValidationStage

PROMOTION_GATES: tuple[ValidationStage, ...] = tuple(ValidationStage)


def missing_gates(passed_stages: Iterable[ValidationStage]) -> tuple[ValidationStage, ...]:
    """The gates of §49 that are not in `passed_stages`, in protocol order.

    Repeating a stage, or passing a stage twice, counts once: a gate is cleared or it is not.
    """
    passed = set(passed_stages)
    return tuple(stage for stage in PROMOTION_GATES if stage not in passed)


def can_promote(passed_stages: Iterable[ValidationStage]) -> bool:
    """True only when all nine gates are cleared; no threshold and no partial credit."""
    return not missing_gates(passed_stages)
