import asyncio
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.core.mode import TradingMode
from tradingagent.notify.access import AccessGate
from tradingagent.notify.commands import CommandRouter
from tradingagent.notify.sensitive_commands import (
    RESTART_EVENT,
    close_all_handler,
    disable_handler,
    emergency_stop_handler,
    enable_handler,
    mode_handler,
    pause_handler,
    restart_handler,
    resume_handler,
    stack_handler,
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


def service(engine: Engine, *, supervised: bool = False) -> CommandService:
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
    router.register("restart", "redémarre l'agent", restart_handler(engine, supervised=supervised))
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


# --- /restart: the demand is written, the loop is what honours it ---------------


def restart_events(engine: Engine) -> list[SystemEventRow]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(SystemEventRow).where(SystemEventRow.kind == RESTART_EVENT)
            ).all()
        )


def test_restart_asks_first_and_writes_nothing(engine: Engine) -> None:
    """The convention of every sensitive command: nothing is persisted before the word."""
    svc = service(engine, supervised=True)

    prompt = send(svc, "/restart")

    assert prompt is not None
    assert "arrêt propre" in prompt and "confirmer" in prompt
    assert "revient seul" in prompt, "a supervised agent says it will come back"
    assert restart_events(engine) == []
    assert not HaltStore(engine).status().halted, "a restart suspends nothing"


def test_the_confirmed_restart_leaves_one_request_for_the_loop(engine: Engine) -> None:
    svc = service(engine, supervised=True)

    done = send(svc, "/restart confirmer")

    assert done is not None and "Redémarrage demandé" in done
    events = restart_events(engine)
    assert len(events) == 1
    assert events[0].detail["actor"] == f"telegram:{OPERATOR}"
    # Telegram cannot end the process that serves it: the request is what the loop reads.
    assert not HaltStore(engine).status().halted


def test_an_unsupervised_agent_says_the_restart_will_not_come_back_on_its_own(
    engine: Engine,
) -> None:
    """Better a sentence the operator can act on than a promise nothing will keep."""
    svc = service(engine, supervised=False)

    prompt = send(svc, "/restart")
    done = send(svc, "/restart confirmer")

    assert prompt is not None and "pas supervisé" in prompt
    assert "install_autostart.ps1" in prompt, "and it says how to bring it back"
    assert done is not None and "ne reviendra pas seul" in done
    assert len(restart_events(engine)) == 1


def test_a_restart_is_audited_like_any_other_sensitive_command(engine: Engine) -> None:
    svc = service(engine, supervised=True)

    send(svc, "/restart confirmer")

    with Session(engine) as session:
        rows = session.scalars(select(AuditLogRow).where(AuditLogRow.action == "command")).all()
    assert rows[-1].actor == f"telegram:{OPERATOR}"
    assert rows[-1].detail["command"] == "restart"


# --- /shutdown and /restart_all: the order the supervisor reads -----------------


def stack_service(
    engine: Engine,
    command: str,
    control: Path | None,
    *,
    ea_directory: Path | None = None,
    symbols: Sequence[str] = (),
) -> CommandService:
    """One stack command, wired as the composition root wires it."""
    router = CommandRouter()
    router.register(
        command,
        "toute la pile",
        stack_handler(command, control, ea_directory=ea_directory, symbols=symbols),
    )
    return CommandService(AccessGate({OPERATOR}), router, AuditStore(engine), now=Clock())


def test_shutdown_states_the_irreversible_part_then_writes_one_order(
    engine: Engine, tmp_path: Path
) -> None:
    control = tmp_path / "controle.txt"
    svc = stack_service(engine, "shutdown", control)

    prompt = send(svc, "/shutdown")

    assert prompt is not None
    assert "RIEN ne redémarrera seul" in prompt, "the operator is told what cannot be undone"
    assert not control.exists(), "nothing is written before the confirmation word"

    done = send(svc, "/shutdown confirmer")

    assert done is not None and "Arrêt de tout demandé" in done
    assert control.read_text(encoding="utf-8") == "arreter\n"


def test_restart_all_writes_the_other_order(engine: Engine, tmp_path: Path) -> None:
    control = tmp_path / "controle.txt"
    svc = stack_service(engine, "restart_all", control)

    send(svc, "/restart_all confirmer")

    assert control.read_text(encoding="utf-8") == "redemarrer\n"


def test_restart_all_lifts_the_local_halts_the_restart_would_trip(
    engine: Engine, tmp_path: Path
) -> None:
    """The trap this closes: a full restart silences the backend, the EAs halt themselves,
    and they re-read their halt file at the next start — so without this they never come back.
    """
    ea = tmp_path / "ea"
    (ea / "control").mkdir(parents=True)
    for symbol in ("XAUUSD", "BTCUSD"):
        (ea / "control" / f"{symbol}_halt.txt").write_text("backend silencieux", encoding="utf-8")
    svc = stack_service(
        engine,
        "restart_all",
        tmp_path / "controle.txt",
        ea_directory=ea,
        symbols=("XAUUSD", "BTCUSD"),
    )

    answer = send(svc, "/restart_all confirmer")

    assert answer is not None and "arrêts locaux levés" in answer
    assert "XAUUSD" in answer and "BTCUSD" in answer
    assert list((ea / "control").glob("*_halt.txt")) == []


def test_shutdown_leaves_the_local_halts_alone(engine: Engine, tmp_path: Path) -> None:
    """`/shutdown` promises everything stops, so it must not quietly re-arm anything."""
    ea = tmp_path / "ea"
    (ea / "control").mkdir(parents=True)
    (ea / "control" / "XAUUSD_halt.txt").write_text("backend silencieux", encoding="utf-8")
    svc = stack_service(
        engine, "shutdown", tmp_path / "controle.txt", ea_directory=ea, symbols=("XAUUSD",)
    )

    send(svc, "/shutdown confirmer")

    assert (ea / "control" / "XAUUSD_halt.txt").exists()


def test_without_a_supervisor_the_order_is_refused_not_faked(engine: Engine) -> None:
    """No supervisor means nobody reads the file: answering "c'est fait" would be a lie."""
    answer = send(stack_service(engine, "shutdown", None), "/shutdown confirmer")

    assert answer is not None
    assert "superviseur" in answer and "install_autostart" in answer
