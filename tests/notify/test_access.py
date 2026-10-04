from datetime import UTC, datetime, timedelta

from tradingagent.notify.access import AccessGate, Verdict

OPERATOR, STRANGER = 111, 999
T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def gate(**overrides: object) -> AccessGate:
    return AccessGate({OPERATOR}, **overrides)  # type: ignore[arg-type]


def test_the_operator_is_allowed_in_a_private_chat() -> None:
    decision = gate().check(OPERATOR, private_chat=True, at=T0)
    assert decision.verdict is Verdict.ALLOWED
    assert decision.record


def test_an_unknown_identifier_is_refused() -> None:
    decision = gate().check(STRANGER, private_chat=True, at=T0)
    assert decision.verdict is Verdict.UNAUTHORIZED
    assert decision.record


def test_the_operator_is_refused_outside_a_private_chat() -> None:
    assert gate().check(OPERATOR, private_chat=False, at=T0).verdict is Verdict.NOT_PRIVATE


def test_too_many_attempts_trigger_the_limit_once() -> None:
    g = gate(max_attempts=3)
    verdicts = [g.check(OPERATOR, True, T0 + timedelta(seconds=i)).verdict for i in range(5)]
    assert verdicts == [Verdict.ALLOWED] * 3 + [Verdict.RATE_LIMITED] * 2


def test_only_the_first_limited_attempt_is_recorded() -> None:
    g = gate(max_attempts=2)
    records = [g.check(STRANGER, True, T0 + timedelta(seconds=i)).record for i in range(10)]
    assert records == [True, True, True] + [False] * 7


def test_the_limit_rearms_after_its_cooldown() -> None:
    g = gate(max_attempts=2, cooldown=timedelta(minutes=5))
    for i in range(3):
        g.check(OPERATOR, True, T0 + timedelta(seconds=i))
    assert g.check(OPERATOR, True, T0 + timedelta(minutes=4)).verdict is Verdict.RATE_LIMITED
    assert g.check(OPERATOR, True, T0 + timedelta(minutes=6)).verdict is Verdict.ALLOWED


def test_attempts_spread_over_time_are_not_limited() -> None:
    g = gate(max_attempts=3, window=timedelta(minutes=1))
    verdicts = [g.check(OPERATOR, True, T0 + timedelta(seconds=30 * i)).verdict for i in range(10)]
    assert set(verdicts) == {Verdict.ALLOWED}


def test_one_identifier_being_limited_does_not_affect_another() -> None:
    g = gate(max_attempts=1)
    g.check(STRANGER, True, T0)
    g.check(STRANGER, True, T0)
    assert g.check(OPERATOR, True, T0).verdict is Verdict.ALLOWED


def test_records_of_unknown_identifiers_are_capped_overall() -> None:
    # Many different strangers must not be able to fill the database.
    g = gate(max_unauthorized_records_per_hour=5)
    records = [g.check(1000 + i, True, T0 + timedelta(seconds=i)).record for i in range(8)]
    assert records == [True] * 5 + [False] * 3
    assert g.check(5000, True, T0 + timedelta(hours=1, seconds=10)).record
