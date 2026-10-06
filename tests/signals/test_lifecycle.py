from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.signals.lifecycle import TRANSITIONS, IllegalTransitionError, validate_transition
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import SignalEventRow, SignalRow, StrategyVersionRow
from tradingagent.storage.signals import history, transition

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)

TERMINAL = {
    SignalState.RISK_REJECTED,
    SignalState.EXPIRED,
    SignalState.IGNORED,
    SignalState.ORDER_REJECTED,
    SignalState.CLOSED,
    SignalState.CANCELLED,
    SignalState.ERROR,
}


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'lifecycle.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def seed_signal(engine: Engine, state: SignalState = SignalState.CANDIDATE) -> int:
    with Session(engine) as session:
        version = session.scalars(select(StrategyVersionRow)).first()
        if version is None:
            version = StrategyVersionRow(
                ref="witness@1.0.0",
                strategy_id="witness",
                version="1.0.0",
                manifest={},
                content_hash="0" * 64,
                first_seen_at=T0,
            )
            session.add(version)
            session.flush()
        signal = SignalRow(
            idempotency_key="witness@1.0.0:XAUUSD:M15:2026-10-06T12:00Z",
            strategy_version_id=version.id,
            symbol="XAUUSD",
            timeframe=Timeframe.M15,
            direction=Direction.BUY,
            mode=TradingMode.DEMO,
            observed_price=2650.0,
            entry_low=2649.5,
            entry_high=2650.5,
            stop_loss=2647.5,
            take_profits=[2652.5],
            reason="witness crossover",
            indicators={},
            generated_at=T0,
            expires_at=T0 + timedelta(minutes=45),
            state=state,
        )
        session.add(signal)
        session.commit()
        return int(signal.id)


# --- the pure state machine ---------------------------------------------------


def test_every_non_terminal_state_has_an_outgoing_transition() -> None:
    for state in SignalState:
        if state in TERMINAL:
            assert state not in TRANSITIONS, f"{state} is terminal and must have no exits"
        else:
            assert TRANSITIONS.get(state), f"{state} is live and must have at least one exit"


def test_the_happy_path_to_a_closed_position_is_legal() -> None:
    path = [
        SignalState.CANDIDATE,
        SignalState.VALIDATED,
        SignalState.SENT,
        SignalState.ACCEPTED,
        SignalState.ORDER_SENT,
        SignalState.ORDER_ACCEPTED,
        SignalState.POSITION_OPEN,
        SignalState.PARTIALLY_CLOSED,
        SignalState.CLOSED,
    ]
    for current, target in pairwise(path):
        validate_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (SignalState.CLOSED, SignalState.VALIDATED),
        (SignalState.CANDIDATE, SignalState.POSITION_OPEN),
        (SignalState.EXPIRED, SignalState.SENT),
        (SignalState.SENT, SignalState.ORDER_SENT),
        (SignalState.RISK_REJECTED, SignalState.VALIDATED),
        (SignalState.ERROR, SignalState.CANDIDATE),
    ],
)
def test_an_illegal_transition_is_refused(current: SignalState, target: SignalState) -> None:
    with pytest.raises(IllegalTransitionError):
        validate_transition(current, target)


# --- persistence ---------------------------------------------------------------


def test_a_full_history_is_reconstructable(engine: Engine) -> None:
    signal_id = seed_signal(engine)
    path = [
        SignalState.VALIDATED,
        SignalState.SENT,
        SignalState.ACCEPTED,
        SignalState.ORDER_SENT,
        SignalState.ORDER_ACCEPTED,
        SignalState.POSITION_OPEN,
        SignalState.CLOSED,
    ]
    for step, state in enumerate(path):
        transition(engine, signal_id, state, T0 + timedelta(minutes=step + 1))

    events = history(engine, signal_id)

    assert [event.state for event in events] == path
    assert [event.occurred_at for event in events] == sorted(event.occurred_at for event in events)
    with Session(engine) as session:
        current = session.get(SignalRow, signal_id)
    assert current is not None and current.state is SignalState.CLOSED


def test_an_illegal_transition_is_persisted_nowhere(engine: Engine) -> None:
    signal_id = seed_signal(engine, SignalState.VALIDATED)

    with pytest.raises(IllegalTransitionError):
        transition(engine, signal_id, SignalState.CLOSED, T0 + timedelta(minutes=1))

    with Session(engine) as session:
        events = session.scalars(
            select(SignalEventRow).where(SignalEventRow.signal_id == signal_id)
        ).all()
        current = session.get(SignalRow, signal_id)
    assert events == []
    assert current is not None and current.state is SignalState.VALIDATED


def test_two_racing_transitions_from_the_same_state_produce_one_winner(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        pytest.skip("the row lock that serializes racers is a PostgreSQL guarantee")
    signal_id = seed_signal(engine)
    at = T0 + timedelta(minutes=1)

    results: list[str] = []

    def move(target: SignalState) -> None:
        try:
            transition(engine, signal_id, target, at)
            results.append("ok")
        except IllegalTransitionError:
            results.append("refused")

    import threading

    threads = [
        threading.Thread(target=move, args=(SignalState.VALIDATED,)),
        threading.Thread(target=move, args=(SignalState.EXPIRED,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count("ok") == 1
    assert results.count("refused") == 1


def test_a_second_transition_is_judged_against_the_moved_state(engine: Engine) -> None:
    signal_id = seed_signal(engine)
    transition(engine, signal_id, SignalState.VALIDATED, T0 + timedelta(minutes=1))

    # From VALIDATED, EXPIRED is legal; but from SENT it is not: the state machine
    # judges every request against the current state, never against the request order.
    with pytest.raises(IllegalTransitionError):
        transition(engine, signal_id, SignalState.ACCEPTED, T0 + timedelta(minutes=2))


def test_history_is_empty_for_a_fresh_candidate(engine: Engine) -> None:
    signal_id = seed_signal(engine)

    assert history(engine, signal_id) == []
