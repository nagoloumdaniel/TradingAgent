import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tradingagent.core.halt import GLOBAL
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import HaltAction, HaltSource
from tradingagent.notify.access import AccessGate
from tradingagent.notify.commands import CommandRequest, CommandRouter, status_handler
from tradingagent.notify.service import CommandService
from tradingagent.storage.audit import AuditStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltCommand, HaltStore
from tradingagent.storage.migrate import upgrade

OPERATOR, STRANGER = 111, 999
T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'bot.db'}"
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


def service(engine: Engine, mode: TradingMode = TradingMode.DEMO, **gate: object) -> CommandService:
    router = CommandRouter()
    router.register("status", "état de l'agent", status_handler(HaltStore(engine), mode))

    async def boom(_: CommandRequest) -> str:
        raise RuntimeError("handler bug")

    router.register("boom", "plante", boom)
    return CommandService(
        AccessGate({OPERATOR}, **gate),  # type: ignore[arg-type]
        router,
        AuditStore(engine),
        now=Clock(),
    )


def send(svc: CommandService, text: str, user: int = OPERATOR, private: bool = True) -> str | None:
    return asyncio.run(svc.handle(user, private, text))


def test_the_operator_gets_the_status(engine: Engine) -> None:
    reply = send(service(engine), "/status")
    assert reply is not None
    assert "DÉMO" in reply
    assert "aucun arrêt" in reply


def test_live_mode_is_unmistakable(engine: Engine) -> None:
    reply = send(service(engine, TradingMode.LIVE), "/status")
    assert reply is not None
    assert "RÉEL" in reply
    assert "DÉMO" not in reply


def test_status_shows_an_active_halt_and_its_reason(engine: Engine) -> None:
    HaltStore(engine).issue(
        HaltCommand(GLOBAL, HaltAction.HALT, HaltSource.SERVER, "news risk", "daniel", T0)
    )
    reply = send(service(engine), "/status")
    assert reply is not None
    assert "ARRÊT" in reply
    assert "news risk" in reply


def test_a_stranger_gets_no_answer_at_all(engine: Engine) -> None:
    assert send(service(engine), "/status", user=STRANGER) is None


def test_a_stranger_is_recorded_in_the_audit_log(engine: Engine) -> None:
    send(service(engine), "/status", user=STRANGER)
    [entry] = AuditStore(engine).recent()
    assert entry.actor == f"telegram:{STRANGER}"
    assert entry.action == "command_refused"
    assert entry.detail["verdict"] == "unauthorized"
    assert entry.detail["command"] == "status"


def test_every_authorized_command_is_recorded_with_its_arguments(engine: Engine) -> None:
    send(service(engine), "/status now please")
    [entry] = AuditStore(engine).recent()
    assert entry.actor == f"telegram:{OPERATOR}"
    assert entry.action == "command"
    assert entry.detail == {"command": "status", "args": ["now", "please"]}


def test_a_group_chat_gets_no_answer(engine: Engine) -> None:
    assert send(service(engine), "/status", private=False) is None
    assert AuditStore(engine).recent()[0].detail["verdict"] == "not_private"


def test_the_operator_is_told_once_when_rate_limited(engine: Engine) -> None:
    svc = service(engine, max_attempts=2)
    replies = [send(svc, "/status") for _ in range(5)]
    assert replies[2] is not None and "trop de commandes" in replies[2].lower()
    assert replies[3:] == [None, None]


def test_a_stranger_is_never_told_about_the_rate_limit(engine: Engine) -> None:
    svc = service(engine, max_attempts=1)
    assert [send(svc, "/status", user=STRANGER) for _ in range(4)] == [None] * 4


def test_unknown_command_points_to_help(engine: Engine) -> None:
    reply = send(service(engine), "/fly")
    assert reply is not None and "/help" in reply


def test_help_lists_the_commands(engine: Engine) -> None:
    reply = send(service(engine), "/help")
    assert reply is not None
    assert "/status" in reply and "/help" in reply


def test_the_bot_username_suffix_is_ignored(engine: Engine) -> None:
    reply = send(service(engine), "/status@Nagos_XauUsdFx_Bot")
    assert reply is not None and "DÉMO" in reply


def test_plain_text_gets_a_hint(engine: Engine) -> None:
    reply = send(service(engine), "bonjour")
    assert reply is not None and "/help" in reply


def test_a_failing_handler_answers_without_crashing(engine: Engine) -> None:
    reply = send(service(engine), "/boom")
    assert reply is not None and "erreur" in reply


def test_no_command_runs_if_it_cannot_be_recorded(tmp_path: Path) -> None:
    broken = create_database_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    try:
        router = CommandRouter()
        ran: list[str] = []

        async def act(_: CommandRequest) -> str:
            ran.append("ran")
            return "done"

        router.register("act", "agit", act)
        svc = CommandService(AccessGate({OPERATOR}), router, AuditStore(broken), now=Clock())
        reply = send(svc, "/act")
    finally:
        broken.dispose()
    assert ran == []
    assert reply is not None and "journal" in reply
