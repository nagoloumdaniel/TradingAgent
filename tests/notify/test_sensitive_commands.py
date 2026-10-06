import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.core.mode import TradingMode
from tradingagent.notify.access import AccessGate
from tradingagent.notify.commands import CommandRouter
from tradingagent.notify.sensitive_commands import (
    close_all_handler,
    disable_handler,
    emergency_stop_handler,
    enable_handler,
    mode_handler,
    pause_handler,
    resume_handler,
)
from tradingagent.notify.service import CommandService
from tradingagent.storage.audit import AuditStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import AuditLogRow, SystemEventRow

OPERATOR = 111
T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'sensitive.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def service(engine: Engine) -> CommandService:
    """The full sensitive surface, wired exactly like the bot does."""
    halts = HaltStore(engine)
    router = CommandRouter()
    router.register("pause", "suspend les ordres", pause_handler(halts))
    router.register("resume", "reprend les ordres", resume_handler(halts))
    router.register("close_all", "ferme les positions", close_all_handler(halts))
    router.register("emergency_stop", "arrêt d'urgence", emergency_stop_handler(halts))
    router.register("disable", "désactive un marché", disable_handler(halts))
    router.register("enable", "réactive un marché", enable_handler(halts))
    router.register("mode", "change le mode", mode_handler(engine))
    return CommandService(AccessGate({OPERATOR}), router, AuditStore(engine), now=Clock())


def send(svc: CommandService, text: str) -> str | None:
    return asyncio.run(svc.handle(OPERATOR, True, text))


def test_pause_only_suspends_after_explicit_confirmation(engine: Engine) -> None:
    svc = service(engine)

    prompt = send(svc, "/pause")
    assert prompt is not None
    assert "nouveaux ordres" in prompt
    assert "conservées" in prompt
    assert "confirmer" in prompt

    assert send(svc, "/pause") == prompt  # asking twice does not change anything
    assert not HaltStore(engine).status().halted

    answer = send(svc, "/pause confirmer")
    assert answer is not None
    status = HaltStore(engine).status()
    assert status.halted
    assert not status.close_positions


def test_resume_lifts_the_pause(engine: Engine) -> None:
    svc = service(engine)
    send(svc, "/pause confirmer")

    send(svc, "/resume confirmer")

    assert not HaltStore(engine).status().halted


def test_close_all_announces_and_requires_the_closing(engine: Engine) -> None:
    svc = service(engine)

    prompt = send(svc, "/close_all")
    assert prompt is not None
    assert "clôturées" in prompt  # the operator is told positions WILL be closed

    send(svc, "/close_all confirmer")

    status = HaltStore(engine).status()
    assert status.halted
    assert status.close_positions


def test_emergency_stop_never_closes_positions_by_itself(engine: Engine) -> None:
    svc = service(engine)

    prompt = send(svc, "/emergency_stop")
    assert prompt is not None
    assert "pas" in prompt and "clôturées" in prompt  # no implicit closing (RM-015)

    send(svc, "/emergency_stop confirmer")

    status = HaltStore(engine).status()
    assert status.halted
    assert not status.close_positions


def test_mode_live_is_refused_with_the_server_condition(engine: Engine) -> None:
    answer = send(service(engine), "/mode LIVE")

    assert answer is not None
    assert "LIVE" in answer
    assert "serveur" in answer


def test_mode_refuses_an_unknown_mode(engine: Engine) -> None:
    answer = send(service(engine), "/mode TOMORROW")

    assert answer is not None
    assert "TOMORROW" in answer


def test_mode_records_a_valid_change_with_its_author(engine: Engine) -> None:
    send(service(engine), "/mode PAPER")

    with Session(engine) as session:
        events = session.scalars(
            select(SystemEventRow).where(SystemEventRow.kind == "mode_command")
        ).all()
    assert len(events) == 1
    assert events[0].detail["requested"] == TradingMode.PAPER.value
    assert events[0].detail["actor"] == f"telegram:{OPERATOR}"


def test_disable_needs_the_symbol_and_confirmation_then_survives_a_restart(
    engine: Engine,
) -> None:
    svc = service(engine)

    usage = send(svc, "/disable")
    assert usage is not None and "symbole" in usage
    assert not HaltStore(engine).is_halted("market:BTCUSD")

    prompt = send(svc, "/disable BTCUSD")
    assert prompt is not None and "BTCUSD" in prompt
    assert not HaltStore(engine).is_halted("market:BTCUSD")

    send(svc, "/disable BTCUSD confirmer")

    # A fresh store over the same database: the state survives a restart.
    assert HaltStore(engine).is_halted("market:BTCUSD")


def test_enable_lifts_a_disabled_market(engine: Engine) -> None:
    svc = service(engine)
    send(svc, "/disable BTCUSD confirmer")

    send(svc, "/enable BTCUSD confirmer")

    assert not HaltStore(engine).is_halted("market:BTCUSD")


def test_every_executed_sensitive_command_is_journalled_with_its_author(engine: Engine) -> None:
    svc = service(engine)
    send(svc, "/pause confirmer")

    with Session(engine) as session:
        rows = session.scalars(select(AuditLogRow).where(AuditLogRow.action == "command")).all()
    assert rows[-1].actor == f"telegram:{OPERATOR}"
    assert rows[-1].detail["command"] == "pause"
    assert rows[-1].detail["args"] == ["confirmer"]
