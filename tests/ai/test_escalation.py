"""The trigger that decides a strategy is worth rewriting.

The interesting cases are all refusals: a healthy strategy must not be sent back to the
drawing board, and a motif that appears twice a day for a week — never twice in one day —
must still be caught. Both are about reading the right field in the history.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.ai.escalation import (
    DEFAULT_TRIGGER,
    LossKind,
    escalations,
    failure_patterns,
    loss_kind_of,
    reason_of,
)
from tradingagent.ai.lab_store import StoredAnalysis
from tradingagent.core.states import AnalysisKind

NOW = datetime(2026, 10, 7, 22, 0, tzinfo=UTC)


def analysis(
    *,
    market: str = "XAUUSD",
    motif: str = LossKind.RECURRING_PATTERN.value,
    at: datetime | None = None,
    reason: str = "le motif se répète",
    kind: AnalysisKind = AnalysisKind.LOSS_ANALYSIS,
    findings: dict | None = None,
) -> StoredAnalysis:
    payload = {"kind": motif, "reason": reason} if findings is None else findings
    return StoredAnalysis(
        id=1,
        kind=kind,
        market=market,
        ref="witness@1.1.0",
        signal_id=None,
        model="deterministic",
        request={},
        response=None,
        findings=payload,
        cost_eur=None,
        created_at=at if at is not None else NOW,
    )


# --- reading the right field --------------------------------------------------------------


def test_the_motif_is_read_from_findings_not_from_the_row_kind() -> None:
    """The row's `kind` says the analysis was about losses; the motif is inside `findings`.

    Confusing the two would group every analysis together and fire the trigger on a healthy
    strategy — the one mistake this module cannot make.
    """
    row = analysis(motif=LossKind.DEGRADATION.value)
    assert row.kind is AnalysisKind.LOSS_ANALYSIS
    assert loss_kind_of(row) == LossKind.DEGRADATION.value


def test_a_non_loss_analysis_carries_no_motif() -> None:
    row = analysis(kind=AnalysisKind.REGIME, findings={"regime": "volatilite_haute"})
    assert loss_kind_of(row) is None


def test_a_missing_motif_is_not_invented() -> None:
    assert loss_kind_of(analysis(findings={"reason": "sans motif"})) is None


def test_the_reason_is_the_sentence_the_analyst_wrote() -> None:
    assert reason_of(analysis(reason="le R/R réel s'effondre")) == "le R/R réel s'effondre"


def test_a_missing_reason_falls_back_to_the_motif() -> None:
    assert reason_of(analysis(findings={"kind": LossKind.EXECUTION_PROBLEM.value})) == (
        LossKind.EXECUTION_PROBLEM.value
    )


# --- what must NOT fire ---------------------------------------------------------------------


def test_a_healthy_strategy_never_reaches_the_trigger() -> None:
    """`normal` is not a failure: counting it would ask for a rewrite of something working."""
    rows = [
        analysis(motif=LossKind.NORMAL.value, at=NOW - timedelta(days=index)) for index in range(9)
    ]
    assert failure_patterns(rows, trigger=3, now=NOW) == ()


def test_an_isolated_anomaly_is_not_a_pattern() -> None:
    """By definition: one occurrence, however odd, is what `isolated` means."""
    rows = [analysis(motif=LossKind.ISOLATED_ANOMALY.value) for _ in range(8)]
    assert failure_patterns(rows, trigger=3, now=NOW) == ()


def test_two_failures_are_still_variance() -> None:
    rows = [analysis(at=NOW - timedelta(days=index)) for index in range(2)]
    assert failure_patterns(rows, trigger=3, now=NOW) == ()


def test_a_fixed_defect_from_last_month_stops_asking() -> None:
    """A motif older than the window is history, not a present problem."""
    rows = [analysis(at=NOW - timedelta(days=40)) for _ in range(6)]
    assert failure_patterns(rows, trigger=3, window_days=14, now=NOW) == ()


# --- what must fire --------------------------------------------------------------------------


def test_the_same_motif_three_times_fires() -> None:
    rows = [analysis(at=NOW - timedelta(days=index)) for index in range(DEFAULT_TRIGGER)]

    patterns = failure_patterns(rows, trigger=DEFAULT_TRIGGER, now=NOW)

    assert len(patterns) == 1
    assert patterns[0].occurrences == 3
    assert patterns[0].repeated is True


def test_a_motif_spread_over_days_is_still_caught() -> None:
    """Twice a day for a week never looks repetitive inside a single day."""
    rows = [
        analysis(at=NOW - timedelta(days=day, hours=hour)) for day in range(7) for hour in (6, 18)
    ]

    patterns = failure_patterns(rows, trigger=3, window_days=14, now=NOW)

    assert len(patterns) == 1
    assert patterns[0].occurrences == 14
    assert patterns[0].span().count("→") == 1


def test_a_proven_execution_fault_fires_without_waiting_for_a_count() -> None:
    """Waiting for three execution problems means deliberately losing two more."""
    rows = [analysis(motif=LossKind.EXECUTION_PROBLEM.value)]

    patterns = failure_patterns(rows, trigger=3, now=NOW)

    assert len(patterns) == 1
    assert patterns[0].occurrences == 1


def test_a_degradation_fires_on_its_own() -> None:
    rows = [analysis(motif=LossKind.DEGRADATION.value)]
    assert len(failure_patterns(rows, trigger=5, now=NOW)) == 1


def test_markets_are_counted_apart() -> None:
    """A motif repeating on gold says nothing about bitcoin."""
    rows = [
        analysis(market="XAUUSD", at=NOW),
        analysis(market="XAUUSD", at=NOW - timedelta(days=1)),
        analysis(market="BTCUSD", at=NOW),
    ]

    patterns = failure_patterns(rows, trigger=3, now=NOW)

    assert patterns == (), "neither market reached three on its own"


def test_the_most_repeated_pattern_comes_first() -> None:
    rows = [
        *[analysis(market="XAUUSD", at=NOW - timedelta(days=index)) for index in range(3)],
        *[analysis(market="BTCUSD", at=NOW - timedelta(days=index)) for index in range(6)],
    ]

    patterns = failure_patterns(rows, trigger=3, now=NOW)

    assert [pattern.market for pattern in patterns] == ["BTCUSD", "XAUUSD"]


def test_distinct_reasons_are_kept_but_bounded() -> None:
    rows = [analysis(at=NOW - timedelta(days=index), reason=f"motif {index}") for index in range(5)]

    patterns = failure_patterns(rows, trigger=3, now=NOW)

    assert 1 <= len(patterns[0].reasons) <= 3


def test_a_trigger_below_one_is_refused() -> None:
    with pytest.raises(ValueError):
        failure_patterns([analysis()], trigger=0, now=NOW)


# --- the decision and its wording ---------------------------------------------------------------


def test_an_escalation_explains_why_in_the_operator_s_words() -> None:
    rows = [analysis(at=NOW - timedelta(days=index)) for index in range(4)]
    patterns = failure_patterns(rows, trigger=3, now=NOW)

    decided = escalations(patterns, trigger=3)

    assert len(decided) == 1
    assert "se répète 4 fois" in decided[0].reason
    assert "variance" in decided[0].reason


def test_the_message_says_a_variant_will_be_compared_not_applied() -> None:
    """Nothing is adopted on the strength of a pattern: it is proposed, then measured."""
    rows = [analysis(at=NOW - timedelta(days=index)) for index in range(3)]
    message = escalations(failure_patterns(rows, trigger=3, now=NOW), trigger=3)[0].message()

    assert "backtestée" in message
    assert "comparée" in message
    assert "adopt" not in message.lower()


def test_a_pattern_below_the_trigger_produces_no_escalation() -> None:
    pattern = failure_patterns([analysis(), analysis()], trigger=DEFAULT_TRIGGER, now=NOW)
    assert escalations(pattern, trigger=DEFAULT_TRIGGER) == ()


def test_describe_is_one_line() -> None:
    from tradingagent.ai.escalation import describe

    rows = [analysis(at=NOW - timedelta(days=index)) for index in range(3)]
    line = describe(escalations(failure_patterns(rows, trigger=3, now=NOW), trigger=3)[0])

    assert "\n" not in line
    assert "XAUUSD" in line
