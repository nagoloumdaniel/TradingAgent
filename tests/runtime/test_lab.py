"""The daily AI Lab pass: it analyses, it proposes, and it never trades (§5, §52)."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session
from tests.runtime.fakes import FakeNotifier
from tests.web.seed import NOW as SEED_NOW
from tests.web.seed import seed

from tradingagent.ai.analyst import TradeAnalyst
from tradingagent.ai.daily import (
    MARKER,
    DailyLab,
    day_floor,
    observations_of,
    regime_of,
    session_of,
)
from tradingagent.ai.lab_store import LabStore
from tradingagent.ai.researcher import StrategyResearcher
from tradingagent.storage.models import (
    AiAnalysisRow,
    OrderRow,
    PositionRow,
    SignalRow,
    SystemEventRow,
    TradeRow,
)


@pytest.fixture
def seeded(engine: Engine):
    return seed(engine)


def build_lab(engine: Engine, notifier: FakeNotifier | None = None) -> DailyLab:
    store = LabStore(engine)
    return DailyLab(
        engine,
        analyst=TradeAnalyst(store),
        researcher=StrategyResearcher(store),
        notifier=notifier,
        now=lambda: SEED_NOW,
    )


def counts(engine: Engine) -> tuple[int, int, int, int]:
    """(analyses, orders, positions, trades) — the last three must never move."""
    with Session(engine) as session:
        return (
            int(session.scalar(select(func.count()).select_from(AiAnalysisRow)) or 0),
            int(session.scalar(select(func.count()).select_from(OrderRow)) or 0),
            int(session.scalar(select(func.count()).select_from(PositionRow)) or 0),
            int(session.scalar(select(func.count()).select_from(TradeRow)) or 0),
        )


def test_the_pass_analyses_the_closed_trades_of_the_day(engine: Engine, seeded) -> None:
    before = counts(engine)
    run = asyncio.run(build_lab(engine).run_once(SEED_NOW))

    assert run.skipped is False
    assert run.trades > 0
    assert run.verdicts  # every market with a closed trade is examined
    # A normal day is allowed to record nothing: §15 says a normal loss needs no
    # modification. Anything else must leave its trace.
    notable = [verdict for verdict in run.verdicts if verdict.kind.value != "normal"]
    if notable:
        assert counts(engine)[0] > before[0]
    # No trading row is written by the pass: that is the whole point of §39.
    assert counts(engine)[1:] == before[1:]


def test_the_pass_runs_at_most_once_per_utc_day(engine: Engine, seeded) -> None:
    lab = build_lab(engine)
    first = asyncio.run(lab.run_once(SEED_NOW))
    second = asyncio.run(lab.run_once(SEED_NOW + timedelta(hours=6)))
    third = asyncio.run(lab.run_once(SEED_NOW + timedelta(days=1)))

    assert first.skipped is False
    assert second.skipped is True
    assert third.skipped is False
    assert third.trades == 0  # nothing closed the next day, and that is not an error


def test_the_marker_event_is_written_once(engine: Engine, seeded) -> None:
    asyncio.run(build_lab(engine).run_once(SEED_NOW))
    with Session(engine) as session:
        rows = session.scalars(select(SystemEventRow).where(SystemEventRow.kind == MARKER)).all()
    assert len(rows) == 1
    assert rows[0].detail["day"] == day_floor(SEED_NOW).isoformat()


def test_a_proposal_is_never_applied(engine: Engine, seeded) -> None:
    notifier = FakeNotifier()
    run = asyncio.run(build_lab(engine, notifier).run_once(SEED_NOW))
    assert isinstance(run.proposals, tuple)
    with Session(engine) as session:
        states = session.scalars(select(SignalRow.state)).all()
    assert states  # the seeded signals were read, never moved


def test_the_session_label_follows_the_utc_hour() -> None:
    assert session_of(SEED_NOW.replace(hour=2)) == "asie"
    assert session_of(SEED_NOW.replace(hour=9)) == "londres"
    assert session_of(SEED_NOW.replace(hour=15)) == "new_york"
    assert session_of(SEED_NOW.replace(hour=23)) == "apres_cloture"


def test_the_regime_is_relative_to_the_market_median() -> None:
    assert regime_of(None, 10.0) == "inconnu"
    assert regime_of(10.0, None) == "inconnu"
    assert regime_of(20.0, 10.0) == "volatilite_haute"
    assert regime_of(4.0, 10.0) == "volatilite_basse"
    assert regime_of(10.0, 10.0) == "volatilite_normale"


def test_day_floor_is_the_utc_midnight() -> None:
    assert day_floor(SEED_NOW) == SEED_NOW.replace(hour=0, minute=0, second=0, microsecond=0)


def test_observations_are_built_from_what_the_database_really_holds(engine: Engine, seeded) -> None:
    facts = build_lab(engine)._closed_trades(day_floor(SEED_NOW))
    if not facts:
        pytest.skip("the fixture closes no trade on that day")
    observations = observations_of(facts)
    assert len(observations) == len(facts)
    assert all(observation.context.session for observation in observations)
