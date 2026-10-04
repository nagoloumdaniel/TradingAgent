from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tradingagent.core.halt import CONNECTION, GLOBAL, pair_scope
from tradingagent.core.states import HaltAction, HaltSource
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltCommand, HaltStore, OperatorRequiredError

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def command(
    action: HaltAction = HaltAction.HALT,
    scope: str = GLOBAL,
    source: HaltSource = HaltSource.SERVER,
    close_positions: bool = False,
    minutes: int = 0,
) -> HaltCommand:
    return HaltCommand(
        scope=scope,
        action=action,
        source=source,
        reason=f"{action} for the test",
        actor="operator",
        occurred_at=NOW + timedelta(minutes=minutes),
        close_positions=close_positions,
    )


def test_a_fresh_database_lets_trading_run(engine: Engine) -> None:
    status = HaltStore(engine).status()
    assert not status.halted
    assert status.reasons == ()


def test_a_halt_blocks_trading_with_its_reason(engine: Engine) -> None:
    store = HaltStore(engine)
    store.issue(command())
    status = store.status()
    assert status.halted
    assert "halt for the test" in status.reasons[0]
    assert "operator" in status.reasons[0]


def test_an_operator_resume_lifts_the_halt(engine: Engine) -> None:
    store = HaltStore(engine)
    store.issue(command())
    store.issue(command(HaltAction.RESUME, source=HaltSource.TELEGRAM, minutes=1))
    assert not store.status().halted


def test_the_agent_cannot_lift_a_global_halt_by_itself(engine: Engine) -> None:
    store = HaltStore(engine)
    store.issue(command(source=HaltSource.AUTOMATIC))
    with pytest.raises(OperatorRequiredError):
        store.issue(command(HaltAction.RESUME, source=HaltSource.AUTOMATIC, minutes=1))
    assert store.status().halted


def test_a_connection_halt_can_be_lifted_automatically(engine: Engine) -> None:
    store = HaltStore(engine)
    store.issue(command(scope=CONNECTION, source=HaltSource.AUTOMATIC))
    assert store.status().halted
    store.issue(command(HaltAction.RESUME, CONNECTION, HaltSource.AUTOMATIC, minutes=1))
    assert not store.status().halted


def test_any_halted_scope_halts_trading(engine: Engine) -> None:
    store = HaltStore(engine)
    store.issue(command(scope=CONNECTION, source=HaltSource.AUTOMATIC))
    store.issue(command(minutes=1))
    store.issue(command(HaltAction.RESUME, CONNECTION, HaltSource.AUTOMATIC, minutes=2))
    status = store.status()
    assert status.halted
    assert len(status.reasons) == 1


def test_positions_are_closed_only_when_explicitly_asked(engine: Engine) -> None:
    store = HaltStore(engine)
    store.issue(command())
    assert store.status().close_positions is False
    store.issue(command(close_positions=True, minutes=1))
    assert store.status().close_positions is True
    store.issue(command(HaltAction.RESUME, minutes=2))
    assert store.status().close_positions is False


def test_a_resume_cannot_ask_to_close_positions(engine: Engine) -> None:
    with pytest.raises(ValueError, match="only a halt"):
        HaltStore(engine).issue(command(HaltAction.RESUME, close_positions=True))


def test_a_command_needs_a_reason(engine: Engine) -> None:
    blank = HaltCommand(GLOBAL, HaltAction.HALT, HaltSource.SERVER, "  ", "operator", NOW)
    with pytest.raises(ValueError, match="reason"):
        HaltStore(engine).issue(blank)


def test_the_halt_survives_a_restart(engine: Engine, database_url: str) -> None:
    HaltStore(engine).issue(command())
    restarted = create_database_engine(database_url)
    try:
        assert HaltStore(restarted).status().halted
    finally:
        restarted.dispose()


def test_an_unreadable_state_halts_trading(tmp_path: Path) -> None:
    # A database without the table, like a corrupted or unreachable one: fail closed.
    broken = create_database_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    try:
        status = HaltStore(broken).status()
    finally:
        broken.dispose()
    assert status.halted
    assert "unreadable" in status.reasons[0]


def test_quarantined_pairs_are_the_halted_pair_scopes(engine: Engine) -> None:
    store = HaltStore(engine)
    gold, btc = pair_scope("witness@1.0.0", "XAUUSD"), pair_scope("witness@1.0.0", "BTCUSD")
    store.issue(command(scope=gold, source=HaltSource.AUTOMATIC))
    store.issue(command(scope=btc, source=HaltSource.AUTOMATIC, minutes=1))
    store.issue(command(HaltAction.RESUME, scope=btc, minutes=2))
    assert store.halted_pairs() == {("witness@1.0.0", "XAUUSD")}
    assert not store.status().halted  # a quarantine does not halt the whole agent


def test_history_lists_the_newest_commands_first(engine: Engine) -> None:
    store = HaltStore(engine)
    store.issue(command())
    store.issue(command(HaltAction.RESUME, minutes=1))
    assert [row.action for row in store.history(GLOBAL)] == [HaltAction.RESUME, HaltAction.HALT]
