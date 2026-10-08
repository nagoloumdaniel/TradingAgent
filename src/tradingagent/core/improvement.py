"""The values an improvement compares, in the one layer both sides may import.

`ai/improvement_cycle.py` chains the steps of the daily improvement (escalate → measure →
compare → write a candidate) and `research/improvement.py` owns the acceptance rule. The two
have to hand each other the *same* measurement: the cycle measures a variant, the search
decides whether it beats the version in place. Each of them used to declare its own
`Measurement`, deliberately — `ai` may not import `research`, so the cycle had to define the
shape it received structurally.

Two classes for one concept is not a shape problem, it is a drift waiting to happen: the day
one of them gains a field, the numbers cross the boundary unchanged and nothing says a word.
They also do not type-check against each other, so the composition root that ties them
together was reduced to casting — which is exactly how the type system stops protecting the
one place where the wrong number becomes a strategy version.

So the values live here. `core` is imported by every package and imports none of them
(`tests/test_architecture.py`), which makes it the only layer `ai` and `research` may share.
`research/improvement.py` re-exports these names, so every existing caller — the campaign
script, the research tests — keeps its import and its behaviour.

The chain-facing side reads them through `ai.evidence`, which is the module that knows how a
recorded run becomes a measurement again (`baseline_of`): the composition root then compares
one `Measurement` with another, and no cast stands between the two.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Measurement:
    """What one version of a strategy achieved on one market.

    `objective` is the number the search maximises — the caller chooses it, and choosing it
    is a business decision, not a technical one. `metrics` carries the rest so a refusal can
    be explained with figures rather than adjectives.
    """

    objective: float
    metrics: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Candidate:
    """One proposed change, before it is measured.

    `payload` carries whatever the caller attached — for the daily chain, the id of the AI
    proposal the candidate came from, so an accepted improvement can be traced back to the
    hypothesis that produced it.
    """

    label: str
    parameters: Mapping[str, float]
    rationale: str = ""
    payload: Any = None


__all__ = ["Candidate", "Measurement"]
