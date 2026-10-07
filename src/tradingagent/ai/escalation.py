"""When a failure stops being noise: the trigger that asks for a new strategy.

The operator's rule, in his words: after a number of failures, or when the same motif
repeats, the AI should propose a strategy — which is then measured against the one in
production before anything changes.

This module is that trigger and only that trigger. It answers one question — *has this
market failed often enough, in the same way, to justify disturbing a version that works?* —
and the answer is a value, not an action. Probing whether a change is worth making is not
the same decision as making it; `research.improvement` owns the second one.

Two ways to fire, because the operator named two:

* **Repeated motif** — the same kind of failure, `trigger` times inside the window. One bad
  trade is variance; five identical ones are a property of the strategy.
* **Proven fault** — an execution problem, or a documented degradation. Those are not bad
  luck, they are the system misbehaving, and waiting for a count before reacting would mean
  deliberately losing more of them.

The count spans days, and it is read from the persisted analyses rather than from today's
trades: a motif that shows up twice a day for a week never looks repetitive inside a single
day, which is exactly the case worth catching.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from tradingagent.ai.analyst import LossKind
from tradingagent.ai.lab_store import StoredAnalysis
from tradingagent.core.states import AnalysisKind

# Three identical failures: the smallest number that cannot be a coincidence and can still
# be seen inside a week of normal trading.
DEFAULT_TRIGGER = 3
# A fortnight: long enough to see a weekly pattern, short enough that a defect fixed last
# month does not keep asking for a rewrite.
DEFAULT_WINDOW_DAYS = 14

# Kinds that do not need a count: they describe the system failing, not the market moving.
IMMEDIATE_KINDS: frozenset[str] = frozenset(
    {LossKind.EXECUTION_PROBLEM.value, LossKind.DEGRADATION.value}
)

# `normal` is not a failure, and an isolated anomaly is by definition not a pattern.
IGNORED_KINDS: frozenset[str] = frozenset({LossKind.NORMAL.value, LossKind.ISOLATED_ANOMALY.value})


@dataclass(frozen=True)
class FailurePattern:
    """One kind of failure, counted over the window, for one market."""

    market: str
    kind: str
    occurrences: int
    first_seen: datetime
    last_seen: datetime
    reasons: tuple[str, ...]

    @property
    def repeated(self) -> bool:
        return self.occurrences > 1

    def describe(self) -> str:
        return f"{self.kind} — {self.occurrences} fois ({self.span()})"

    def span(self) -> str:
        return f"{self.first_seen:%Y-%m-%d} → {self.last_seen:%Y-%m-%d}"


@dataclass(frozen=True)
class Escalation:
    """A pattern that justifies proposing a change, and the sentence that explains why."""

    market: str
    pattern: FailurePattern
    trigger: int
    reason: str

    def message(self) -> str:
        return (
            f"🧪 {self.market} — {self.reason}\n"
            f"Motif : {self.pattern.kind} ({self.pattern.occurrences} fois)\n"
            "Une variante va être backtestée et comparée à la version en place."
        )


def loss_kind_of(analysis: StoredAnalysis) -> str | None:
    """The motif a persisted loss analysis recorded, if it was one.

    The row's own `kind` says *what the analysis was about* (`loss_analysis`); the motif
    lives in `findings`. Reading the wrong one would group every analysis together and the
    trigger would fire on a healthy strategy.
    """
    if analysis.kind is not AnalysisKind.LOSS_ANALYSIS:
        return None
    value = analysis.findings.get("kind")
    return str(value) if value is not None else None


def failure_patterns(
    analyses: Iterable[StoredAnalysis],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    trigger: int = DEFAULT_TRIGGER,
    now: datetime | None = None,
) -> tuple[FailurePattern, ...]:
    """Every repeated or immediately-serious failure motif, per market, most seen first."""
    if trigger < 1:
        raise ValueError("a trigger below one would fire on every trade")
    cutoff = now - timedelta(days=window_days) if now is not None and window_days > 0 else None

    buckets: dict[tuple[str, str], list[StoredAnalysis]] = {}
    for analysis in analyses:
        kind = loss_kind_of(analysis)
        if kind is None or kind in IGNORED_KINDS:
            continue
        if cutoff is not None and analysis.created_at < cutoff:
            continue
        buckets.setdefault((analysis.market, kind), []).append(analysis)

    found: list[FailurePattern] = []
    for (market, kind), rows in buckets.items():
        if kind not in IMMEDIATE_KINDS and len(rows) < trigger:
            continue
        ordered = sorted(rows, key=lambda row: row.created_at)
        found.append(
            FailurePattern(
                market=market,
                kind=kind,
                occurrences=len(ordered),
                first_seen=ordered[0].created_at,
                last_seen=ordered[-1].created_at,
                reasons=tuple(dict.fromkeys(reason_of(row) for row in ordered))[:3],
            )
        )
    return tuple(
        sorted(found, key=lambda pattern: (-pattern.occurrences, pattern.market, pattern.kind))
    )


def reason_of(analysis: StoredAnalysis) -> str:
    """The human sentence the analyst stored, when it stored one."""
    value = analysis.findings.get("reason")
    if isinstance(value, str) and value.strip():
        return value.strip()
    kind = loss_kind_of(analysis)
    return kind or "motif non précisé"


def escalations(
    patterns: Sequence[FailurePattern], *, trigger: int = DEFAULT_TRIGGER
) -> tuple[Escalation, ...]:
    """The patterns worth acting on now, with the reason phrased for the operator."""
    decided: list[Escalation] = []
    for pattern in patterns:
        if pattern.kind in IMMEDIATE_KINDS:
            reason = f"défaillance avérée ({pattern.kind}), vue {pattern.occurrences} fois"
        elif pattern.occurrences >= trigger:
            reason = (
                f"le motif se répète {pattern.occurrences} fois (seuil {trigger}) — "
                "ce n'est plus de la variance"
            )
        else:
            continue
        decided.append(
            Escalation(market=pattern.market, pattern=pattern, trigger=trigger, reason=reason)
        )
    return tuple(decided)


def describe(escalation: Escalation) -> str:
    """A short line for a terminal or a log; the operator message is `message()`."""
    details = " | ".join(escalation.pattern.reasons)
    return f"{escalation.market}: {escalation.reason} [{escalation.pattern.span()}] {details}"


__all__ = [
    "DEFAULT_TRIGGER",
    "DEFAULT_WINDOW_DAYS",
    "IMMEDIATE_KINDS",
    "Escalation",
    "FailurePattern",
    "describe",
    "escalations",
    "failure_patterns",
    "loss_kind_of",
    "reason_of",
]
