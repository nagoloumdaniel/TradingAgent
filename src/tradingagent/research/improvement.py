"""The improvement loop: try a change, measure it, keep it only if it helps.

The operator's requirement is a loop — analyse what failed, propose a change, test it,
compare it with what runs today, and if it does not improve, propose another one until one
does. This module is that loop, and nothing else: it reads no clock, opens no connection and
knows nothing about backtests. The caller supplies the candidates and the measurement, which
is what makes the decision logic testable without a market.

**One thing here is not a formality.** "Try until something improves" is multiple testing.
Test enough variants and the best one will beat the incumbent by luck alone; keep it and the
account pays for a coincidence. That is not a hypothetical: the project's discovery lab
rejected every family it examined once the false-discovery rate was corrected, and it was
right to. So the loop does three things about it:

* every attempt is kept, accepted or not, with its reason — the search is auditable;
* the outcome reports `trials`, the number of comparisons made, because that is the input
  the correction needs and nobody can reconstruct it afterwards;
* acceptance demands a gain *outside* the noise band the caller declares, and the incumbent
  stays in place when the attempts run out. Failing to improve is a normal result, not an
  error: it means the version in production is still the best one measured so far.

`Measurement` and `Candidate` are **not** defined here: they live in
`core/improvement.py`, the one layer `ai` and `research` may both import, and they are
re-exported below so every caller keeps importing them from here. The improvement chain
(`ai/improvement_cycle.py`) measures a variant and this module decides whether it beats the
incumbent — two modules handing each other values must agree on the value, and two classes
that merely look alike do not agree, they drift.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from tradingagent.core.improvement import Candidate, Measurement

# A change has to beat the incumbent by this much before it is worth promoting. A threshold
# of zero would accept a gain indistinguishable from rounding.
DEFAULT_MIN_RELATIVE_GAIN = 0.10
# Bounded on purpose. An unbounded search is a promise nobody can keep, and the later
# attempts are the ones most likely to be noise wearing the costume of a discovery.
DEFAULT_MAX_ATTEMPTS = 12


@dataclass(frozen=True)
class Attempt:
    """One comparison, kept whatever its outcome."""

    number: int
    label: str
    parameters: Mapping[str, float]
    objective: float
    metrics: Mapping[str, float]
    accepted: bool
    reason: str
    rationale: str = ""


@dataclass(frozen=True)
class ImprovementOutcome:
    """The whole search, and what it concluded."""

    market: str
    incumbent_label: str
    incumbent_objective: float
    attempts: tuple[Attempt, ...]
    accepted_label: str | None
    accepted_parameters: Mapping[str, float] | None
    accepted_payload: Any = None
    min_relative_gain: float = DEFAULT_MIN_RELATIVE_GAIN

    @property
    def trials(self) -> int:
        """How many candidates were measured.

        The number the multiple-testing correction needs. Reconstructing it after the fact
        is impossible — a search that forgets how hard it looked reports its luck as skill.
        """
        return len(self.attempts)

    @property
    def improved(self) -> bool:
        return self.accepted_label is not None

    @property
    def relative_gain(self) -> float:
        """The accepted gain over the incumbent; 0.0 when nothing was accepted."""
        if self.accepted_label is None:
            return 0.0
        final = next(attempt for attempt in self.attempts if attempt.accepted)
        if self.incumbent_objective == 0:
            return 0.0
        return (final.objective - self.incumbent_objective) / abs(self.incumbent_objective)

    @property
    def best(self) -> Attempt | None:
        """The best attempt made, accepted or not — what to look at when nothing improved."""
        if not self.attempts:
            return None
        return max(self.attempts, key=lambda attempt: attempt.objective)

    @property
    def exhausted(self) -> bool:
        """True when every attempt was spent without a usable improvement."""
        return not self.improved


def verdict(
    *,
    incumbent: Measurement,
    measured: Measurement,
    min_relative_gain: float,
    guard: Callable[[Measurement, Measurement], str | None] | None = None,
) -> tuple[bool, str]:
    """Whether `measured` may replace `incumbent`, and why not when it may not.

    Order matters. A guard that refuses on risk grounds is reported before the arithmetic,
    because "this would have earned more but breaks the drawdown limit" is the sentence an
    operator needs, and "the gain was too small" would bury it.
    """
    if guard is not None:
        refused = guard(incumbent, measured)
        if refused:
            return False, refused
    if min_relative_gain < 0:
        raise ValueError("min_relative_gain cannot be negative")
    required = abs(incumbent.objective) * (1.0 + min_relative_gain)
    if incumbent.objective < 0:
        # A losing incumbent is improved by simply losing less: compare magnitudes.
        required = -abs(incumbent.objective) * (1.0 - min_relative_gain)
        if measured.objective > required:
            return (
                True,
                f"loses less than the incumbent ({measured.objective:.2f} > {required:.2f})",
            )
        return False, (
            f"no real improvement: {measured.objective:.2f} against {incumbent.objective:.2f} "
            f"and {required:.2f} required"
        )
    if measured.objective >= required:
        gain = (
            (measured.objective - incumbent.objective) / abs(incumbent.objective)
            if incumbent.objective
            else 0.0
        )
        return (
            True,
            f"improves by {gain * 100:.1f} %, above the {min_relative_gain * 100:.0f} % required",
        )
    return False, (
        f"below the {min_relative_gain * 100:.0f} % required: "
        f"{measured.objective:.2f} against {incumbent.objective:.2f}"
    )


def search_improvement(
    *,
    market: str,
    incumbent: Measurement,
    incumbent_label: str,
    candidates: Iterable[Candidate],
    evaluate: Callable[[Candidate], Measurement],
    min_relative_gain: float = DEFAULT_MIN_RELATIVE_GAIN,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    guard: Callable[[Measurement, Measurement], str | None] | None = None,
) -> ImprovementOutcome:
    """Measure candidates in order and accept the first that genuinely improves.

    The first acceptance ends the search: the operator asked for an improvement, not for the
    best of twelve. Continuing would spend compute to raise the number of comparisons, and
    every extra comparison makes the accepted one less trustworthy.

    `evaluate` is injected, and may raise: a candidate that cannot be measured is recorded
    as a refusal with the error rather than silently skipped. A search that hides its broken
    attempts reports a smaller `trials` than it made — exactly the count that must not be
    understated.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1, or there is nothing to search")
    attempts: list[Attempt] = []
    for number, candidate in enumerate(candidates, start=1):
        if number > max_attempts:
            break
        try:
            measured = evaluate(candidate)
        except Exception as error:
            attempts.append(
                Attempt(
                    number=number,
                    label=candidate.label,
                    parameters=dict(candidate.parameters),
                    objective=float("-inf"),
                    metrics={},
                    accepted=False,
                    reason=f"could not be measured: {type(error).__name__}: {error}",
                    rationale=candidate.rationale,
                )
            )
            continue
        accepted, reason = verdict(
            incumbent=incumbent,
            measured=measured,
            min_relative_gain=min_relative_gain,
            guard=guard,
        )
        attempts.append(
            Attempt(
                number=number,
                label=candidate.label,
                parameters=dict(candidate.parameters),
                objective=measured.objective,
                metrics=dict(measured.metrics),
                accepted=accepted,
                reason=reason,
                rationale=candidate.rationale,
            )
        )
        if accepted:
            return ImprovementOutcome(
                market=market,
                incumbent_label=incumbent_label,
                incumbent_objective=incumbent.objective,
                attempts=tuple(attempts),
                accepted_label=candidate.label,
                accepted_parameters=dict(candidate.parameters),
                accepted_payload=candidate.payload,
                min_relative_gain=min_relative_gain,
            )
    return ImprovementOutcome(
        market=market,
        incumbent_label=incumbent_label,
        incumbent_objective=incumbent.objective,
        attempts=tuple(attempts),
        accepted_label=None,
        accepted_parameters=None,
        min_relative_gain=min_relative_gain,
    )


def report(outcome: ImprovementOutcome) -> str:
    """A plain-text account of the search, for the operator's terminal and the journal."""
    lines = [
        f"== {outcome.market} : amélioration ==",
        f"  version en place : {outcome.incumbent_label} "
        f"(objectif {outcome.incumbent_objective:.2f})",
    ]
    if not outcome.attempts:
        lines.append("  aucune variante à essayer")
        return "\n".join(lines)
    for attempt in outcome.attempts:
        mark = "RETENUE" if attempt.accepted else "écartée"
        objective = (
            "non mesurable" if attempt.objective == float("-inf") else f"{attempt.objective:.2f}"
        )
        lines.append(f"  {attempt.number:>2}. [{mark}] {attempt.label} : {objective}")
        lines.append(f"      {attempt.reason}")
    lines.append("")
    if outcome.improved:
        lines.append(
            f"  -> {outcome.accepted_label} retenue, +{outcome.relative_gain * 100:.1f} % "
            f"après {outcome.trials} comparaison(s)"
        )
        lines.append(
            "     À valider par les portes avant toute promotion : "
            f"{outcome.trials} essai(s) doivent entrer dans la correction du test multiple."
        )
    else:
        lines.append(
            f"  -> aucune amélioration après {outcome.trials} comparaison(s). "
            f"La version {outcome.incumbent_label} reste en place."
        )
        best = outcome.best
        if best is not None and best.objective != float("-inf"):
            lines.append(
                f"     Meilleur essai : {best.label} ({best.objective:.2f}), "
                f"insuffisant — {best.reason}"
            )
    return "\n".join(lines)


__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MIN_RELATIVE_GAIN",
    "Attempt",
    "Candidate",
    "ImprovementOutcome",
    "Measurement",
    "report",
    "search_improvement",
    "verdict",
]
