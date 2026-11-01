"""The trusted clock follows online time and ignores edits of the PC clock."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from tradingagent.trusted_clock import TrustedClock, paris_offset

ONLINE = datetime(2026, 11, 1, 18, 0, tzinfo=UTC)


class Mono:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


class Pc:
    """A PC clock the operator can change at will."""

    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def _clock(answers: Mapping[str, datetime | Exception], mono: Mono, pc: Pc) -> TrustedClock:
    def fetch(url: str, timeout: float) -> datetime:
        answer = answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return TrustedClock(tuple(answers), fetch, monotonic=mono, system_now=pc)


def test_before_any_sync_the_pc_clock_is_used_and_reported() -> None:
    pc = Pc(ONLINE)
    clock = _clock({"a": ONLINE}, Mono(), pc)
    assert clock.now() == ONLINE
    assert not clock.status().synced


def test_changing_the_pc_clock_does_not_move_trusted_time() -> None:
    mono, pc = Mono(), Pc(ONLINE + timedelta(hours=5))  # the PC is 5 h ahead
    clock = _clock({"a": ONLINE, "b": ONLINE, "c": ONLINE}, mono, pc)
    assert clock.sync()
    assert clock.now() == ONLINE
    assert clock.status().offset_seconds == -5 * 3600
    pc.at = ONLINE - timedelta(days=3)  # the operator sets it back: no effect
    mono.value += 90
    assert clock.now() == ONLINE + timedelta(seconds=90)


def test_an_outlier_source_is_ignored() -> None:
    answers = {
        "a": ONLINE,
        "b": ONLINE + timedelta(seconds=1),
        "c": ONLINE + timedelta(hours=2),
    }
    clock = _clock(answers, Mono(), Pc(ONLINE))
    assert clock.sync()
    assert abs((clock.now() - ONLINE).total_seconds()) <= 1
    assert clock.status().source_count == 2


def test_a_failing_host_does_not_prevent_sync() -> None:
    clock = _clock({"a": OSError("down"), "b": ONLINE, "c": ONLINE}, Mono(), Pc(ONLINE))
    assert clock.sync()
    assert clock.status().source_count == 2


def test_a_single_answer_is_not_a_quorum() -> None:
    clock = _clock({"a": OSError("down"), "b": ONLINE}, Mono(), Pc(ONLINE))
    assert not clock.sync()
    assert not clock.status().synced and "désaccord" in clock.status().detail


def test_sources_that_disagree_are_rejected_not_averaged() -> None:
    answers = {"a": ONLINE, "b": ONLINE + timedelta(hours=1)}
    clock = _clock(answers, Mono(), Pc(ONLINE))
    assert not clock.sync()
    assert not clock.status().synced


def test_a_sudden_jump_waits_for_a_second_round() -> None:
    mono, pc = Mono(), Pc(ONLINE)
    answers: dict[str, datetime | Exception] = {"a": ONLINE, "b": ONLINE}
    clock = _clock(answers, mono, pc)
    assert clock.sync()
    later = ONLINE + timedelta(minutes=10)
    answers.update(a=later, b=later)
    mono.value += 1
    assert not clock.sync()  # held back: could be a forged or stale answer
    assert abs((clock.now() - ONLINE).total_seconds() - 1) < 1
    assert clock.sync()  # the same reading again: accepted
    assert abs((clock.now() - later).total_seconds()) < 1


def test_an_http_date_without_zone_is_read_as_utc() -> None:
    from tradingagent import trusted_clock

    class Response:
        def __init__(self) -> None:
            self.headers = {"Date": "Sun, 01 Nov 2026 18:00:00 -0000"}

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    original = trusted_clock.urllib.request.urlopen
    trusted_clock.urllib.request.urlopen = lambda *a, **k: Response()
    try:
        stamp = trusted_clock._http_date("https://example.test", 1.0)
    finally:
        trusted_clock.urllib.request.urlopen = original
    assert abs((stamp - ONLINE).total_seconds()) < 1


def test_when_every_host_is_down_the_clock_stays_unsynced_and_says_why() -> None:
    clock = _clock({"a": OSError("down"), "b": TimeoutError()}, Mono(), Pc(ONLINE))
    assert not clock.sync()
    status = clock.status()
    assert not status.synced and "aucune source" in status.detail


def test_paris_offset_follows_the_eu_rule() -> None:
    assert paris_offset(datetime(2026, 7, 1, 12, tzinfo=UTC)) == timedelta(hours=2)
    assert paris_offset(datetime(2026, 11, 1, 12, tzinfo=UTC)) == timedelta(hours=1)
    assert paris_offset(datetime(2026, 10, 25, 0, 59, tzinfo=UTC)) == timedelta(hours=2)
    assert paris_offset(datetime(2026, 10, 25, 1, 0, tzinfo=UTC)) == timedelta(hours=1)
