"""The operator's control centre: one market at a time, the AI's proposals, the gates.

Written before the handlers: each new command has its nominal case, its "nothing to show"
case, its unknown-market case, and the refusal a stranger gets. A read reply is plain text:
no HTML tag, at most 80 characters per line, no URL and no file path.
"""

import asyncio
import re
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.core.halt import GLOBAL, market_scope
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import (
    HaltAction,
    HaltSource,
    OrderState,
    PositionState,
    ProposalStatus,
    SignalState,
    StrategyStatus,
    ValidationStage,
)
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar
from tradingagent.notify.access import AccessGate
from tradingagent.notify.commands import CommandRequest, CommandRouter
from tradingagent.notify.read_commands import (
    gates_handler,
    market_handler,
    proposals_handler,
)
from tradingagent.notify.replies import Reply
from tradingagent.notify.sensitive_commands import (
    disable_handler,
    emergency_stop_handler,
    enable_handler,
    pause_handler,
    resume_handler,
)
from tradingagent.notify.service import CommandService
from tradingagent.storage.audit import AuditStore
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltCommand, HaltStore
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import (
    AiProposalRow,
    AuditLogRow,
    CandleRow,
    OrderRow,
    PositionRow,
    SignalRow,
    StrategyRegistryRow,
    StrategyVersionRow,
    ValidationRunRow,
)

OPERATOR, STRANGER = 111, 999
T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)  # a Tuesday
MARKETS: tuple[tuple[str, bool], ...] = (("XAUUSD", True), ("BTCUSD", True))

HTML_TAG = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")
URL = re.compile(r"https?://")
WINDOWS_PATH = re.compile(r"[A-Za-z]:\\")


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'control.db'}"
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


def a_request(command: str, *args: str) -> CommandRequest:
    return CommandRequest(user_id=OPERATOR, command=command, args=tuple(args), at=T0)


def run(handler: object, request: CommandRequest) -> str:
    return asyncio.run(handler(request))  # type: ignore[operator]


def assert_readable(answer: str) -> None:
    """The invariants every plain-text answer of the control centre must keep."""
    assert answer.strip(), "a command never answers with a blank page"
    assert len(answer) < 4096
    assert max(len(line) for line in answer.splitlines()) <= 80, answer
    assert HTML_TAG.search(answer) is None, f"a read reply is plain text, got {answer!r}"
    assert URL.search(answer) is None
    assert WINDOWS_PATH.search(answer) is None


# --- seeding ----------------------------------------------------------------


def seed_version(session: Session) -> StrategyVersionRow:
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
    return version


def seed_signal(
    session: Session,
    version: StrategyVersionRow,
    key: str,
    *,
    symbol: str = "XAUUSD",
    generated_at: datetime = T0,
    state: SignalState = SignalState.VALIDATED,
) -> SignalRow:
    row = SignalRow(
        idempotency_key=key,
        strategy_version_id=version.id,
        symbol=symbol,
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
        generated_at=generated_at,
        expires_at=generated_at + timedelta(minutes=45),
        state=state,
    )
    session.add(row)
    session.flush()
    return row


def seed_position(
    session: Session, version: StrategyVersionRow, ticket: int, symbol: str
) -> PositionRow:
    signal = seed_signal(
        session,
        version,
        f"witness@1.0.0:{symbol}:M15:2026-10-06T0{ticket}:00Z",
        symbol=symbol,
        generated_at=T0 - timedelta(hours=ticket),
    )
    order = OrderRow(
        idempotency_key=f"order-{ticket}",
        signal_id=signal.id,
        symbol=symbol,
        direction=Direction.BUY,
        volume=Decimal("0.01"),
        requested_price=2650.0,
        stop_loss=2647.5,
        take_profit=2652.5,
        mode=TradingMode.DEMO,
        state=OrderState.FILLED,
        created_at=T0 - timedelta(hours=ticket),
        updated_at=T0 - timedelta(hours=ticket),
    )
    session.add(order)
    session.flush()
    position = PositionRow(
        broker_position_ticket=ticket,
        order_id=order.id,
        symbol=symbol,
        direction=Direction.BUY,
        volume=Decimal("0.01"),
        open_price=2650.0,
        stop_loss=2647.5,
        take_profit=2652.5,
        mode=TradingMode.DEMO,
        state=PositionState.OPEN,
        opened_at=T0 - timedelta(hours=ticket),
        updated_at=T0 - timedelta(hours=ticket),
    )
    session.add(position)
    session.flush()
    return position


def seed_candle(session: Session, symbol: str, open_time: datetime) -> None:
    session.add(
        CandleRow(
            symbol=symbol,
            timeframe=Timeframe.M15,
            open_time=open_time,
            open=2649.0,
            high=2651.0,
            low=2648.0,
            close=2650.0,
            source="mt5",
            ingested_at=open_time,
        )
    )
    session.flush()


def seed_proposal(
    session: Session,
    *,
    market: str,
    hypothesis: str,
    status: ProposalStatus = ProposalStatus.PROPOSED,
    ref: str | None = "witness@1.1.0",
    analysis_id: int | None = None,
    created_at: datetime = T0 - timedelta(minutes=30),
    decided_by: str | None = None,
    decision_reason: str | None = None,
    decided_at: datetime | None = None,
) -> AiProposalRow:
    row = AiProposalRow(
        market=market,
        ref=ref,
        analysis_id=analysis_id,
        hypothesis=hypothesis,
        proposed_change={"stop_multiplier": "0.9"},
        status=status,
        decided_by=decided_by,
        decision_reason=decision_reason,
        created_at=created_at,
        decided_at=decided_at,
    )
    session.add(row)
    session.flush()
    return row


def seed_version_row(
    session: Session,
    *,
    market: str,
    ref: str,
    status: StrategyStatus = StrategyStatus.CANDIDATE,
) -> StrategyRegistryRow:
    strategy_id, _, version = ref.partition("@")
    row = StrategyRegistryRow(
        market=market,
        ref=ref,
        strategy_id=strategy_id,
        version=version,
        status=status,
        parent_ref=None,
        origin="human",
        parameters={},
        results=None,
        dataset_fingerprint=None,
        promotion_reason=None,
        created_at=T0 - timedelta(days=1),
        promoted_at=None,
        updated_at=T0 - timedelta(days=1),
    )
    session.add(row)
    session.flush()
    return row


def pass_gate(session: Session, ref: str, market: str, stage: ValidationStage) -> None:
    session.add(
        ValidationRunRow(
            ref=ref,
            market=market,
            stage=stage,
            passed=True,
            detail={},
            created_at=T0 - timedelta(hours=1),
        )
    )
    session.flush()


def calendar_for_at(symbol: str, moment: datetime) -> MarketCalendar:
    slot = (moment.weekday(), (moment.hour * 60 + moment.minute) // 15)
    return MarketCalendar(symbol, frozenset({slot}), frozenset())


def market_command(engine: Engine, calendars: dict[str, MarketCalendar] | None = None) -> object:
    return market_handler(
        MARKETS,
        CandleStore(engine),
        HaltStore(engine),
        engine,
        calendar_for=(calendars or {}).get,
    )


# --- /marche : one market at a time -----------------------------------------


def test_one_market_shows_its_own_state(engine: Engine) -> None:
    with Session(engine) as session:
        version = seed_version(session)
        seed_candle(session, "XAUUSD", T0 - timedelta(minutes=15))
        seed_position(session, version, 1, "XAUUSD")
        session.commit()

    answer = run(
        market_command(engine, {"XAUUSD": calendar_for_at("XAUUSD", T0)}),
        a_request("marche", "XAUUSD"),
    )

    assert_readable(answer)
    assert "XAUUSD" in answer
    assert "BTCUSD" not in answer  # the other market is never mentioned
    assert "ouvert" in answer  # the calendar
    assert "  ACHAT 0.01 lot @ 2650.00 (DÉMO)," in answer  # its open position, indented
    assert "DEMO" not in answer  # the mode is never the ambiguous raw value
    assert "2026-10-06 11:45" in answer  # its last candle
    assert "/disable XAUUSD" in answer  # how to stop this market


def test_one_market_says_when_nothing_is_open(engine: Engine) -> None:
    answer = run(
        market_command(engine, {"XAUUSD": calendar_for_at("XAUUSD", T0)}),
        a_request("marche", "XAUUSD"),
    )

    assert_readable(answer)
    assert "aucune position" in answer.lower()
    assert "aucune bougie" in answer.lower()
    assert "aucun signal" in answer.lower()


def test_a_closed_market_is_reported_as_closed(engine: Engine) -> None:
    # An empty calendar knows no open slot: the market is closed at T0.
    closed = MarketCalendar("BTCUSD", frozenset(), frozenset())

    answer = run(
        market_command(engine, {"BTCUSD": closed}),
        a_request("marche", "BTCUSD"),
    )

    assert_readable(answer)
    assert "fermé" in answer
    assert "autorisés" in answer  # closed is not halted: the two are distinct


def test_a_halt_on_one_market_never_leaks_to_the_other(engine: Engine) -> None:
    HaltStore(engine).issue(
        HaltCommand(
            market_scope("XAUUSD"),
            HaltAction.HALT,
            HaltSource.TELEGRAM,
            "XAUUSD disabled by the operator",
            "telegram:111",
            T0,
        )
    )
    handler = market_command(engine, {"XAUUSD": calendar_for_at("XAUUSD", T0)})

    stopped = run(handler, a_request("marche", "XAUUSD"))
    running = run(handler, a_request("marche", "BTCUSD"))

    assert_readable(stopped)
    assert_readable(running)
    assert "suspendus sur ce marché" in stopped
    assert "suspendus" not in running
    assert "autorisés" in running


def test_a_global_halt_is_named_as_such(engine: Engine) -> None:
    HaltStore(engine).issue(
        HaltCommand(GLOBAL, HaltAction.HALT, HaltSource.SERVER, "news risk", "daniel", T0)
    )

    answer = run(market_command(engine), a_request("marche", "XAUUSD"))

    assert_readable(answer)
    assert "arrêt global" in answer


def test_an_unknown_market_is_named_with_the_ones_that_exist(engine: Engine) -> None:
    answer = run(market_command(engine), a_request("marche", "EURUSD"))

    assert_readable(answer)
    assert "inconnu" in answer.lower()
    assert "EURUSD" in answer
    assert "BTCUSD" in answer and "XAUUSD" in answer


def test_marche_without_a_symbol_says_what_to_type(engine: Engine) -> None:
    answer = run(market_command(engine), a_request("marche"))

    assert_readable(answer)
    assert "/marche" in answer
    assert "XAUUSD" in answer


def test_no_market_configured_says_so(engine: Engine) -> None:
    handler = market_handler((), CandleStore(engine), HaltStore(engine), engine)

    answer = run(handler, a_request("marche", "XAUUSD"))

    assert_readable(answer)
    assert "aucun marché" in answer.lower()


# --- /propositions : what the AI proposed -----------------------------------


def test_proposals_show_the_market_the_hypothesis_and_the_state(engine: Engine) -> None:
    with Session(engine) as session:
        seed_proposal(
            session,
            market="XAUUSD",
            hypothesis="réduire le stop de 10 pour cent sur les cassures",
        )
        session.commit()

    answer = run(proposals_handler(engine, markets=MARKETS), a_request("propositions", "XAUUSD"))

    assert_readable(answer)
    assert "XAUUSD" in answer
    assert "réduire le stop" in answer
    assert "proposée" in answer
    assert "witness@1.1.0" in answer  # the evidence: what it was derived from


def test_proposals_are_filtered_by_market(engine: Engine) -> None:
    with Session(engine) as session:
        seed_proposal(session, market="XAUUSD", hypothesis="hypothèse or")
        seed_proposal(session, market="BTCUSD", hypothesis="hypothèse bitcoin")
        session.commit()

    answer = run(proposals_handler(engine, markets=MARKETS), a_request("propositions", "XAUUSD"))

    assert_readable(answer)
    assert "hypothèse or" in answer
    assert "hypothèse bitcoin" not in answer


def test_proposals_show_the_decision_when_one_was_taken(engine: Engine) -> None:
    with Session(engine) as session:
        seed_proposal(
            session,
            market="XAUUSD",
            hypothesis="élargir les cibles",
            status=ProposalStatus.REJECTED,
            decided_by="validation",
            decision_reason="sur-ajustement",
            decided_at=T0 - timedelta(hours=2),
        )
        session.commit()

    answer = run(proposals_handler(engine, markets=MARKETS), a_request("propositions"))

    assert_readable(answer)
    assert "refusée" in answer
    assert "validation · sur-ajustement" in answer


def test_proposals_say_when_there_is_none_for_that_market(engine: Engine) -> None:
    answer = run(proposals_handler(engine, markets=MARKETS), a_request("propositions", "BTCUSD"))

    assert_readable(answer)
    assert "aucune proposition" in answer.lower()
    assert "BTCUSD" in answer


def test_proposals_refuse_an_unknown_market(engine: Engine) -> None:
    answer = run(proposals_handler(engine, markets=MARKETS), a_request("propositions", "EURUSD"))

    assert_readable(answer)
    assert "inconnu" in answer.lower()
    assert "EURUSD" in answer


def test_proposals_never_confuse_two_markets(engine: Engine) -> None:
    with Session(engine) as session:
        seed_proposal(session, market="BTCUSD", hypothesis="hypothèse bitcoin")
        session.commit()

    answer = run(proposals_handler(engine, markets=MARKETS), a_request("propositions", "XAUUSD"))

    assert "bitcoin" not in answer


# --- /portes : why a strategy is not promoted -------------------------------


def test_gates_list_the_missing_ones(engine: Engine) -> None:
    with Session(engine) as session:
        seed_version_row(session, market="XAUUSD", ref="witness@1.1.0")
        for stage in (ValidationStage.BACKTEST, ValidationStage.COSTS, ValidationStage.RISK):
            pass_gate(session, "witness@1.1.0", "XAUUSD", stage)
        session.commit()

    answer = run(gates_handler(engine, markets=MARKETS), a_request("portes", "XAUUSD"))

    assert_readable(answer)
    assert "witness@1.1.0" in answer
    assert "3/9" in answer
    assert "monte_carlo" in answer
    assert "backtest" not in answer  # a cleared gate is not listed as missing


def test_gates_say_when_everything_is_cleared(engine: Engine) -> None:
    with Session(engine) as session:
        seed_version_row(session, market="XAUUSD", ref="witness@2.0.0", status=StrategyStatus.LIVE)
        for stage in ValidationStage:
            pass_gate(session, "witness@2.0.0", "XAUUSD", stage)
        session.commit()

    answer = run(gates_handler(engine, markets=MARKETS), a_request("portes", "XAUUSD"))

    assert_readable(answer)
    assert "9/9" in answer
    assert "promotion possible" in answer


def test_gates_only_show_the_asked_market(engine: Engine) -> None:
    with Session(engine) as session:
        seed_version_row(session, market="XAUUSD", ref="witness@1.1.0")
        seed_version_row(session, market="BTCUSD", ref="bitcoin@1.0.0")
        session.commit()

    answer = run(gates_handler(engine, markets=MARKETS), a_request("portes", "XAUUSD"))

    assert_readable(answer)
    assert "witness@1.1.0" in answer
    assert "bitcoin@1.0.0" not in answer


def test_gates_say_when_no_strategy_is_registered_for_that_market(engine: Engine) -> None:
    answer = run(gates_handler(engine, markets=MARKETS), a_request("portes", "BTCUSD"))

    assert_readable(answer)
    assert "aucune stratégie" in answer.lower()
    assert "BTCUSD" in answer


def test_gates_refuse_an_unknown_ref(engine: Engine) -> None:
    with Session(engine) as session:
        seed_version_row(session, market="XAUUSD", ref="witness@1.1.0")
        session.commit()

    answer = run(
        gates_handler(engine, markets=MARKETS), a_request("portes", "XAUUSD", "ghost@9.9.9")
    )

    assert_readable(answer)
    assert "ghost@9.9.9" in answer
    assert "inconnu" in answer.lower()


def test_gates_refuse_an_unknown_market(engine: Engine) -> None:
    answer = run(gates_handler(engine, markets=MARKETS), a_request("portes", "EURUSD"))

    assert_readable(answer)
    assert "inconnu" in answer.lower()


def test_gates_without_a_symbol_say_what_to_type(engine: Engine) -> None:
    answer = run(gates_handler(engine, markets=MARKETS), a_request("portes"))

    assert_readable(answer)
    assert "/portes" in answer


# --- the control centre as the operator actually reaches it -----------------


def service(engine: Engine) -> CommandService:
    """The control centre wired like the bot: reads and the market-scoped controls."""
    halts = HaltStore(engine)
    router = CommandRouter()
    router.register(
        "marche",
        "état d'un marché",
        market_handler(MARKETS, CandleStore(engine), halts, engine),
    )
    router.register(
        "propositions", "propositions de l'IA", proposals_handler(engine, markets=MARKETS)
    )
    router.register("portes", "portes de promotion", gates_handler(engine, markets=MARKETS))
    router.register("pause", "suspend les ordres", pause_handler(halts))
    router.register("resume", "reprend les ordres", resume_handler(halts))
    router.register("emergency_stop", "arrêt d'urgence", emergency_stop_handler(halts))
    router.register("disable", "désactive un marché", disable_handler(halts))
    router.register("enable", "réactive un marché", enable_handler(halts))
    return CommandService(AccessGate({OPERATOR}), router, AuditStore(engine), now=Clock())


def send(svc: CommandService, text: str, user: int = OPERATOR) -> Reply | None:
    return asyncio.run(svc.handle(user, True, text))


def test_a_stranger_gets_no_answer_from_any_control_command(engine: Engine) -> None:
    svc = service(engine)

    for text in ("/marche XAUUSD", "/propositions", "/portes XAUUSD"):
        assert send(svc, text, user=STRANGER) is None

    with Session(engine) as session:
        refusals = session.scalars(
            select(AuditLogRow).where(AuditLogRow.action == "command_refused")
        ).all()
    assert len(refusals) == 3
    assert all(row.actor == f"telegram:{STRANGER}" for row in refusals)


def test_stopping_one_market_leaves_the_other_running_and_is_journalled(
    engine: Engine,
) -> None:
    svc = service(engine)

    send(svc, "/disable XAUUSD confirmer")

    halts = HaltStore(engine)
    assert halts.is_halted("market:XAUUSD")
    assert not halts.is_halted("market:BTCUSD")

    stopped = send(svc, "/marche XAUUSD")
    running = send(svc, "/marche BTCUSD")
    assert stopped is not None and "suspendus sur ce marché" in stopped
    assert running is not None and "autorisés" in running

    # Who, what, when: the audit row names the actor, the command and its arguments.
    with Session(engine) as session:
        rows = session.scalars(select(AuditLogRow).where(AuditLogRow.action == "command")).all()
    control = [row for row in rows if row.detail["command"] == "disable"]
    assert len(control) == 1
    assert control[0].actor == f"telegram:{OPERATOR}"
    assert control[0].detail["args"] == ["XAUUSD", "confirmer"]
    # Why: the halt row carries its own reason, its author and its UTC moment.
    [halt_row] = halts.history("market:XAUUSD", limit=1)
    assert "XAUUSD" in halt_row.reason
    assert halt_row.actor == f"telegram:{OPERATOR}"
    assert halt_row.occurred_at is not None


def test_resuming_one_market_never_reopens_the_other(engine: Engine) -> None:
    svc = service(engine)
    send(svc, "/disable XAUUSD confirmer")

    send(svc, "/enable XAUUSD confirmer")

    halts = HaltStore(engine)
    assert not halts.is_halted("market:XAUUSD")
    assert not halts.is_halted("market:BTCUSD")


def test_every_reply_fits_a_phone(engine: Engine) -> None:
    with Session(engine) as session:
        seed_proposal(session, market="XAUUSD", hypothesis="h" * 400)
        seed_version_row(session, market="XAUUSD", ref="witness@1.1.0")
        session.commit()
    svc = service(engine)

    for text in (
        "/marche XAUUSD",
        "/marche BTCUSD",
        "/propositions",
        "/propositions XAUUSD",
        "/portes XAUUSD",
    ):
        reply = send(svc, text)
        assert reply is not None
        assert_readable(reply)


def test_help_lists_the_new_commands(engine: Engine) -> None:
    """Each documented command gets a button, and that button opens its own page."""
    answer = send(service(engine), "/help")

    assert answer is not None and answer.keyboard is not None
    labels = [button.label for row in answer.keyboard.rows for button in row]
    for name in ("/marche", "/propositions", "/portes"):
        assert name in labels


def _lines(answer: str) -> Sequence[str]:
    return answer.splitlines()
