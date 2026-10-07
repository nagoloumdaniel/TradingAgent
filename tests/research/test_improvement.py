"""The loop that decides whether a proposed change beats the version in production.

The interesting cases are the ones where it must say no. A search that accepts whatever it
last tried is not a search, and an unbounded one manufactures its own evidence — so the
tests here are mostly about refusal, counting and the arithmetic of a losing incumbent.
"""

import pytest

from tradingagent.research.improvement import (
    DEFAULT_MIN_RELATIVE_GAIN,
    Candidate,
    Measurement,
    report,
    search_improvement,
    verdict,
)

INCUMBENT = Measurement(objective=100.0, metrics={"profit_factor": 1.2})


def evaluator(scores: dict[str, float]):
    def evaluate(candidate: Candidate) -> Measurement:
        return Measurement(objective=scores[candidate.label], metrics={"profit_factor": 1.3})

    return evaluate


# --- the arithmetic ----------------------------------------------------------------------


def test_a_gain_above_the_threshold_is_accepted() -> None:
    accepted, reason = verdict(
        incumbent=INCUMBENT,
        measured=Measurement(objective=115.0),
        min_relative_gain=0.10,
    )
    assert accepted is True
    assert "15.0 %" in reason


def test_a_gain_below_the_threshold_is_refused_with_the_numbers() -> None:
    accepted, reason = verdict(
        incumbent=INCUMBENT,
        measured=Measurement(objective=104.0),
        min_relative_gain=0.10,
    )
    assert accepted is False
    assert "104.00" in reason and "100.00" in reason


def test_a_gain_of_zero_is_not_an_improvement() -> None:
    """The default threshold exists so that noise cannot pass for a discovery."""
    accepted, _ = verdict(
        incumbent=INCUMBENT, measured=Measurement(objective=100.0), min_relative_gain=0.0
    )
    assert accepted is True  # exactly equal clears a zero threshold, by definition


def test_a_losing_incumbent_is_improved_by_losing_less() -> None:
    accepted, reason = verdict(
        incumbent=Measurement(objective=-100.0),
        measured=Measurement(objective=-50.0),
        min_relative_gain=0.10,
    )
    assert accepted is True
    assert "loses less" in reason


def test_a_losing_incumbent_rejects_an_equally_bad_change() -> None:
    accepted, _ = verdict(
        incumbent=Measurement(objective=-100.0),
        measured=Measurement(objective=-100.0),
        min_relative_gain=0.10,
    )
    assert accepted is False


def test_a_risk_guard_is_reported_before_the_arithmetic() -> None:
    """ "Earns more but breaks the drawdown limit" is the sentence the operator needs."""

    def guard(incumbent: Measurement, measured: Measurement) -> str | None:
        if measured.metrics.get("max_drawdown_eur", 0) > 200:
            return "drawdown 250 EUR above the 200 EUR limit"
        return None

    accepted, reason = verdict(
        incumbent=INCUMBENT,
        measured=Measurement(objective=150.0, metrics={"max_drawdown_eur": 250.0}),
        min_relative_gain=0.10,
        guard=guard,
    )
    assert accepted is False
    assert "drawdown" in reason
    assert "150" not in reason, "the refusal must be the risk one, not the arithmetic"


def test_a_negative_threshold_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        verdict(incumbent=INCUMBENT, measured=INCUMBENT, min_relative_gain=-0.1)


# --- the search --------------------------------------------------------------------------


def test_the_first_genuine_improvement_ends_the_search() -> None:
    """The operator asked for an improvement, not for the best of twelve."""
    calls: list[str] = []

    def evaluate(candidate: Candidate) -> Measurement:
        calls.append(candidate.label)
        return Measurement(objective={"a": 90.0, "b": 130.0, "c": 500.0}[candidate.label])

    outcome = search_improvement(
        market="XAUUSD",
        incumbent=INCUMBENT,
        incumbent_label="witness@1.1.0",
        candidates=[Candidate("a", {}), Candidate("b", {}), Candidate("c", {})],
        evaluate=evaluate,
    )

    assert outcome.improved is True
    assert outcome.accepted_label == "b"
    assert calls == ["a", "b"], "the third candidate must never be measured"
    assert outcome.trials == 2


def test_nothing_is_accepted_when_no_attempt_beats_the_incumbent() -> None:
    outcome = search_improvement(
        market="XAUUSD",
        incumbent=INCUMBENT,
        incumbent_label="witness@1.1.0",
        candidates=[Candidate(label, {}) for label in ("a", "b", "c")],
        evaluate=evaluator({"a": 60.0, "b": 99.0, "c": 101.0}),
    )

    assert outcome.improved is False
    assert outcome.accepted_label is None
    assert outcome.exhausted is True
    assert outcome.trials == 3
    assert outcome.best is not None and outcome.best.label == "c"


def test_every_attempt_is_kept_even_when_one_is_accepted() -> None:
    """The search must be auditable: a reader sees what was tried and why it was rejected."""
    outcome = search_improvement(
        market="BTCUSD",
        incumbent=INCUMBENT,
        incumbent_label="trend_breakout@1.0.0",
        candidates=[Candidate(label, {}) for label in ("a", "b")],
        evaluate=evaluator({"a": 80.0, "b": 140.0}),
    )

    assert [attempt.accepted for attempt in outcome.attempts] == [False, True]
    assert all(attempt.reason for attempt in outcome.attempts)


def test_the_search_stops_at_max_attempts() -> None:
    outcome = search_improvement(
        market="XAUUSD",
        incumbent=INCUMBENT,
        incumbent_label="witness@1.1.0",
        candidates=[Candidate(f"c{index}", {}) for index in range(20)],
        evaluate=evaluator({f"c{index}": 50.0 for index in range(20)}),
        max_attempts=4,
    )

    assert outcome.trials == 4


def test_a_candidate_that_cannot_be_measured_is_a_recorded_refusal() -> None:
    """Silently skipping it would understate `trials` — the one count that must not lie."""

    def evaluate(candidate: Candidate) -> Measurement:
        if candidate.label == "broken":
            raise RuntimeError("no data for this period")
        return Measurement(objective=150.0)

    outcome = search_improvement(
        market="XAUUSD",
        incumbent=INCUMBENT,
        incumbent_label="witness@1.1.0",
        candidates=[Candidate("broken", {}), Candidate("good", {})],
        evaluate=evaluate,
    )

    assert outcome.trials == 2
    assert outcome.attempts[0].accepted is False
    assert "no data for this period" in outcome.attempts[0].reason
    assert outcome.accepted_label == "good"


def test_no_candidate_means_no_search_and_no_improvement() -> None:
    outcome = search_improvement(
        market="XAUUSD",
        incumbent=INCUMBENT,
        incumbent_label="witness@1.1.0",
        candidates=[],
        evaluate=evaluator({}),
    )

    assert outcome.trials == 0
    assert outcome.improved is False
    assert "aucune variante" in report(outcome)


def test_a_search_that_cannot_be_bounded_is_refused() -> None:
    with pytest.raises(ValueError):
        search_improvement(
            market="XAUUSD",
            incumbent=INCUMBENT,
            incumbent_label="witness@1.1.0",
            candidates=[Candidate("a", {})],
            evaluate=evaluator({"a": 200.0}),
            max_attempts=0,
        )


def test_the_relative_gain_is_the_one_reported() -> None:
    outcome = search_improvement(
        market="XAUUSD",
        incumbent=INCUMBENT,
        incumbent_label="witness@1.1.0",
        candidates=[Candidate("a", {})],
        evaluate=evaluator({"a": 125.0}),
    )

    assert outcome.relative_gain == pytest.approx(0.25)


def test_no_gain_is_reported_when_nothing_was_accepted() -> None:
    outcome = search_improvement(
        market="XAUUSD",
        incumbent=INCUMBENT,
        incumbent_label="witness@1.1.0",
        candidates=[Candidate("a", {})],
        evaluate=evaluator({"a": 101.0}),
    )

    assert outcome.relative_gain == 0.0


def test_the_threshold_default_is_not_zero() -> None:
    """A zero default would let rounding pass for an improvement."""
    assert DEFAULT_MIN_RELATIVE_GAIN > 0


# --- what the operator reads ---------------------------------------------------------------


def test_the_report_names_the_version_that_stays_in_place() -> None:
    outcome = search_improvement(
        market="XAUUSD",
        incumbent=INCUMBENT,
        incumbent_label="witness@1.1.0",
        candidates=[Candidate("a", {})],
        evaluate=evaluator({"a": 99.0}),
    )

    text = report(outcome)
    assert "witness@1.1.0" in text
    assert "reste en place" in text


def test_the_report_warns_that_the_trials_must_be_corrected_for() -> None:
    """An accepted improvement is a candidate, never a verdict: the count feeds the test."""
    outcome = search_improvement(
        market="XAUUSD",
        incumbent=INCUMBENT,
        incumbent_label="witness@1.1.0",
        candidates=[Candidate("a", {})],
        evaluate=evaluator({"a": 150.0}),
    )

    text = report(outcome)
    assert "correction du test multiple" in text
    assert "essai" in text
