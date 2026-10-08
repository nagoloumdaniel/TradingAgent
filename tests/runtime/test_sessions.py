"""The gold weekend, end to end in the runtime (F-005).

What the operator asked for, in order: on a weekend gold produces nothing; when the agent is
already running (or starts) then, it says once on Telegram that the gold market is closed; and
it stops gold for the weekend, resuming by itself when the market reopens. Bitcoin, which
quotes seven days a week, must never be caught by any of it.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from tests.runtime.fakes import BTC, GOLD, open_calendar, weekend_calendar

from tradingagent.core.halt import session_scope
from tradingagent.data.market_calendar import MarketCalendar
from tradingagent.notify.health_alerts import HealthAlerter
from tradingagent.runtime.sessions import MarketSessionGuard
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.migrate import upgrade

# 2026-10-02 is a Friday, 10-03 a Saturday, 10-04 a Sunday and 10-05 a Monday.
FRIDAY_CLOSE = datetime(2026, 10, 2, 20, 50, tzinfo=UTC)
SATURDAY = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
SUNDAY = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
SUNDAY_REOPEN = datetime(2026, 10, 4, 22, 15, tzinfo=UTC)
MONDAY = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
NEXT_FRIDAY_CLOSE = datetime(2026, 10, 9, 20, 50, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'sessions.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


class Sent:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def __call__(self, text: str) -> None:
        self.texts.append(text)


def build(
    engine: Engine,
    calendars: dict[str, MarketCalendar],
    sent: Sent | None = None,
) -> tuple[MarketSessionGuard, Sent]:
    collector = sent if sent is not None else Sent()
    alerts = HealthAlerter(collector, SystemEventStore(engine))
    guard = MarketSessionGuard(halts=HaltStore(engine), alerts=alerts, calendar_for=calendars.get)
    return guard, collector


def test_the_weekend_closes_gold_and_announces_it_once(engine: Engine) -> None:
    """Two days of cycles, one message: this is the defect the operator named explicitly."""
    guard, sent = build(engine, {GOLD: weekend_calendar(GOLD)})

    for step in range(100):
        asyncio.run(guard.guard(GOLD, SATURDAY.replace(second=step % 60)))

    assert len(sent.texts) == 1
    assert "fermé" in sent.texts[0]
    assert HaltStore(engine).is_halted(session_scope(GOLD)) is True
    assert len(HaltStore(engine).history(session_scope(GOLD))) == 1  # halted once, not 100 times


def test_the_friday_close_is_detected_as_well(engine: Engine) -> None:
    guard, sent = build(engine, {GOLD: weekend_calendar(GOLD)})

    outcome = asyncio.run(guard.guard(GOLD, FRIDAY_CLOSE))

    assert outcome is not None and outcome.closed is True
    # The closure notice carries no clock stamp: Telegram dates what it delivers.
    assert "Marché fermé" in sent.texts[0] and "UTC" not in sent.texts[0]


def test_sunday_is_the_same_closure_and_stays_silent(engine: Engine) -> None:
    guard, sent = build(engine, {GOLD: weekend_calendar(GOLD)})

    asyncio.run(guard.guard(GOLD, SATURDAY))
    asyncio.run(guard.guard(GOLD, SUNDAY))

    assert len(sent.texts) == 1
    assert len(HaltStore(engine).history(session_scope(GOLD))) == 1


def test_gold_reopens_by_itself_on_monday(engine: Engine) -> None:
    """No operator action: the calendar says the market trades again, so gold resumes."""
    guard, sent = build(engine, {GOLD: weekend_calendar(GOLD)})
    asyncio.run(guard.guard(GOLD, SATURDAY))

    asyncio.run(guard.guard(GOLD, MONDAY))

    assert HaltStore(engine).is_halted(session_scope(GOLD)) is False
    history = HaltStore(engine).history(session_scope(GOLD))
    assert [row.action.value for row in history] == ["resume", "halt"]
    assert "rouvert" in sent.texts[1]


def test_gold_reopens_at_the_sunday_evening_session(engine: Engine) -> None:
    guard, _ = build(engine, {GOLD: weekend_calendar(GOLD)})
    asyncio.run(guard.guard(GOLD, SUNDAY))

    asyncio.run(guard.guard(GOLD, SUNDAY_REOPEN))

    assert HaltStore(engine).is_halted(session_scope(GOLD)) is False


def test_the_gold_stop_leaves_bitcoin_trading(engine: Engine) -> None:
    calendars = {GOLD: weekend_calendar(GOLD), BTC: open_calendar(BTC)}
    guard, sent = build(engine, calendars)

    gold = asyncio.run(guard.guard(GOLD, SATURDAY))
    bitcoin = asyncio.run(guard.guard(BTC, SATURDAY))

    assert gold is not None and gold.closed is True
    assert bitcoin is None  # a seven-day market has no session to stop
    assert HaltStore(engine).is_halted(session_scope(BTC)) is False
    # Only `global` and `connection` suspend the whole agent: gold's weekend is neither.
    assert HaltStore(engine).status().halted is False
    assert len(sent.texts) == 1


def test_a_restart_during_the_weekend_does_not_repeat_the_alert(engine: Engine) -> None:
    """A new process reads the persisted halt: the same closure is not announced twice."""
    first, first_sent = build(engine, {GOLD: weekend_calendar(GOLD)})
    asyncio.run(first.guard(GOLD, SATURDAY))

    restarted, restarted_sent = build(engine, {GOLD: weekend_calendar(GOLD)})
    asyncio.run(restarted.guard(GOLD, SATURDAY))

    assert len(first_sent.texts) == 1
    assert restarted_sent.texts == []
    assert len(HaltStore(engine).history(session_scope(GOLD))) == 1


def test_a_restart_mid_weekend_still_announces_the_monday_reopening(engine: Engine) -> None:
    first, _ = build(engine, {GOLD: weekend_calendar(GOLD)})
    asyncio.run(first.guard(GOLD, SATURDAY))

    restarted, restarted_sent = build(engine, {GOLD: weekend_calendar(GOLD)})
    asyncio.run(restarted.guard(GOLD, MONDAY))

    assert HaltStore(engine).is_halted(session_scope(GOLD)) is False
    assert any("rouvert" in text for text in restarted_sent.texts)


def test_the_next_closure_is_announced_again(engine: Engine) -> None:
    guard, sent = build(engine, {GOLD: weekend_calendar(GOLD)})

    asyncio.run(guard.guard(GOLD, SATURDAY))
    asyncio.run(guard.guard(GOLD, MONDAY))
    asyncio.run(guard.guard(GOLD, NEXT_FRIDAY_CLOSE))

    assert len(sent.texts) == 3
    assert "fermé" in sent.texts[2]


def test_an_unknown_calendar_is_left_alone(engine: Engine) -> None:
    guard, sent = build(engine, {})

    assert asyncio.run(guard.guard(GOLD, SATURDAY)) is None
    assert sent.texts == []
    assert HaltStore(engine).history(session_scope(GOLD)) == []


def test_an_operator_disable_is_not_lifted_by_the_weekend_resume(engine: Engine) -> None:
    """The session scope is separate from `market:<symbol>`, which belongs to `/disable`."""
    from tradingagent.core.halt import market_scope
    from tradingagent.core.states import HaltAction, HaltSource
    from tradingagent.storage.halts import HaltCommand

    halts = HaltStore(engine)
    halts.issue(
        HaltCommand(
            market_scope(GOLD),
            HaltAction.HALT,
            HaltSource.TELEGRAM,
            "XAUUSD disabled by the operator",
            "operator",
            SATURDAY,
        )
    )
    guard, _ = build(engine, {GOLD: weekend_calendar(GOLD)})

    asyncio.run(guard.guard(GOLD, SATURDAY))
    asyncio.run(guard.guard(GOLD, MONDAY))

    # The weekend halt came and went; the operator's disable is still in force.
    assert halts.halted_markets() == {GOLD}
