"""Pipeline-level adversarial checks: the AI can only block, risk is never bypassed,
and one signal can never produce two orders (C-002, RM-014, RM-018).
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tradingagent.ai.layer import ReviewContext, ReviewOutcome
from tradingagent.config.agent import LiveRiskProfile, RiskConfig, RiskProfile
from tradingagent.core.account import AccountModeMismatchError
from tradingagent.core.halt import GLOBAL
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.states import HaltAction, HaltSource, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.execution.journal import OrderJournal
from tradingagent.execution.mt5_broker import MT5Broker
from tradingagent.execution.simulator import SimulatedTerminal
from tradingagent.risk.model import (
    AccountState,
    BrokerPosition,
    ClosedPosition,
    CloseResult,
    InstrumentSpec,
    MarketQuote,
    OpenPosition,
    OrderRequest,
    OrderResult,
)
from tradingagent.runtime.pipeline import SignalPipeline
from tradingagent.runtime.portfolio import PortfolioBuilder
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltCommand, HaltStore
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import OrderRow
from tradingagent.storage.signals import (
    SignalRecord,
    SignalRepository,
    get_signal,
    idempotency_key,
)
from tradingagent.strategies.manifest import StrategyManifest

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
LOGIN = 40123456
EQUITY = Decimal("5497.74")
MANIFEST = StrategyManifest(
    strategy_id="witness",
    version="1.1.0",
    max_mode=TradingMode.SIGNAL,
    allowed_symbols=("XAUUSD",),
    timeframes=(Timeframe.M15,),
    history_bars=300,
)
RISK = RiskConfig(
    simulated=RiskProfile(
        risk_per_trade_pct=Decimal("0.5"),
        daily_loss_pct=Decimal(2),
        weekly_loss_pct=Decimal(6),
        max_drawdown_pct=Decimal(10),
        max_open_positions=2,
        max_positions_per_market=1,
    ),
    live=LiveRiskProfile(
        risk_per_trade_pct=Decimal(2),
        daily_loss_pct=Decimal(5),
        weekly_loss_pct=Decimal(10),
        max_drawdown_pct=Decimal(20),
        max_open_positions=2,
        max_positions_per_market=1,
        reference_capital=Decimal(100),
        currency="EUR",
    ),
)

SPEC = InstrumentSpec(
    "XAUUSD", Decimal(100), Decimal("0.01"), Decimal("0.01"), Decimal(100), Decimal("0.01"), 10
)
QUOTE = MarketQuote(
    Decimal(2400), Decimal("2400.2"), Decimal("1094.00"), Decimal(18393), Decimal("0.888786")
)


class FakeBroker:
    def __init__(self) -> None:
        self.placed: list[OrderRequest] = []

    async def account(self) -> AccountState:
        return AccountState(LOGIN, True, "EUR", EQUITY, EQUITY)

    async def instrument(self, symbol: str) -> InstrumentSpec:
        return SPEC

    async def quote(
        self, symbol: str, direction: Direction, stop_loss: Decimal | None = None
    ) -> MarketQuote:
        return QUOTE

    async def open_positions(self) -> tuple[OpenPosition, ...]:
        return ()

    async def positions(self) -> tuple[BrokerPosition, ...]:
        return ()

    async def place(self, request: OrderRequest) -> OrderResult:
        self.placed.append(request)
        raise AssertionError("no order may reach the broker on this path")

    async def close(self, ticket: int, reason: str) -> CloseResult:
        raise AssertionError("nothing to close")

    async def on_candle(self, symbol: str, candle: Candle) -> tuple[ClosedPosition, ...]:
        return ()

    async def collect_closures(self) -> tuple[ClosedPosition, ...]:
        return ()

    async def reconcile(self) -> tuple[str, ...]:
        return ()


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, text: str, *, parse_mode: str | None = None) -> bool:
        self.messages.append(text)
        return True


class BlockingAi:
    async def review(
        self,
        context: ReviewContext,
        at: datetime,
        signal_id: int | None = None,
        ai_filter: AiFilter | None = None,
    ) -> ReviewOutcome:
        del context, at, signal_id, ai_filter
        return ReviewOutcome(
            verdict="rejected",
            ai_filter=AiFilter.ADVISORY,
            applied=True,
            blocks_signal=True,
            degraded=False,
            reason="verifier: blocked on purpose",
            text="verifier: blocked on purpose",
            overrun_attempts=("volume", "stop_loss"),
        )


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'pipeline.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def make_signal(engine: Engine) -> int:
    record = SignalRecord(
        idempotency_key=idempotency_key(MANIFEST.ref, "XAUUSD", Timeframe.M15, NOW),
        manifest=MANIFEST,
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        direction=Direction.BUY,
        mode=TradingMode.DEMO,
        observed_price=2400.5,
        entry_low=2400.0,
        entry_high=2401.0,
        stop_loss=2390.0,
        take_profits=(2420.0,),
        reason="verification signal",
        indicators={"ema_fast": 2401.0},
        generated_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    signal_id = SignalRepository(engine).record(record)
    assert signal_id is not None
    return signal_id


def build_pipeline(
    engine: Engine, halts: HaltStore, broker: FakeBroker, ai: Any = None
) -> SignalPipeline:
    return SignalPipeline(
        engine=engine,
        halts=halts,
        broker=broker,
        notifier=FakeNotifier(),
        portfolio=PortfolioBuilder(engine),
        risk_config=RISK,
        expected_login=LOGIN,
        mode=TradingMode.DEMO,
        calendar_for=lambda symbol: None,
        ai=ai,
        now=lambda: NOW,
    )


# --- risk cannot be bypassed ------------------------------------------------------


def test_a_halted_agent_never_reaches_the_broker(engine: Engine) -> None:
    halts = HaltStore(engine)
    halts.issue(
        HaltCommand(GLOBAL, HaltAction.HALT, HaltSource.AUTOMATIC, "test halt", "verifier", NOW)
    )
    broker = FakeBroker()
    signal_id = make_signal(engine)

    outcome = asyncio.run(build_pipeline(engine, halts, broker).process(signal_id))

    assert outcome.kind == "refused"
    assert broker.placed == []
    # The refusal is persisted on the signal: it can never be picked up as executable.
    detail = get_signal(engine, signal_id)
    assert detail is not None
    assert detail.state is SignalState.RISK_REJECTED


def test_an_uncertain_market_refuses_instead_of_trading(engine: Engine) -> None:
    broker = FakeBroker()
    signal_id = make_signal(engine)
    outcome = asyncio.run(build_pipeline(engine, HaltStore(engine), broker).process(signal_id))
    assert outcome.kind == "refused"
    assert broker.placed == []


# --- the AI can only block --------------------------------------------------------


def test_a_blocking_ai_expires_the_signal_without_touching_the_broker(engine: Engine) -> None:
    broker = FakeBroker()
    signal_id = make_signal(engine)

    outcome = asyncio.run(
        build_pipeline(engine, HaltStore(engine), broker, ai=BlockingAi()).process(signal_id)
    )

    assert outcome.kind == "ai_blocked"
    assert broker.placed == []
    detail = get_signal(engine, signal_id)
    assert detail is not None
    assert detail.state is SignalState.EXPIRED


def test_the_ai_verdict_carries_no_field_that_could_create_a_signal(engine: Engine) -> None:
    outcome = asyncio.run(
        BlockingAi().review(
            ReviewContext(
                symbol="XAUUSD",
                timeframe=Timeframe.M15,
                strategy_ref=MANIFEST.ref,
                direction=Direction.BUY,
                observed_price=2400.0,
                entry_low=2399.0,
                entry_high=2401.0,
                stop_loss=2390.0,
                take_profits=(2420.0,),
                indicators={},
                market_state="open",
            ),
            NOW,
        )
    )
    for field in ("volume", "stop_loss", "entry_low", "entry_high", "direction", "symbol"):
        assert not hasattr(outcome, field)


# --- one signal, one order --------------------------------------------------------


def test_two_records_of_the_same_candle_yield_one_signal(engine: Engine) -> None:
    repo = SignalRepository(engine)
    first = make_signal(engine)
    duplicate = SignalRecord(
        idempotency_key=idempotency_key(MANIFEST.ref, "XAUUSD", Timeframe.M15, NOW),
        manifest=MANIFEST,
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        direction=Direction.BUY,
        mode=TradingMode.DEMO,
        observed_price=2400.5,
        entry_low=2400.0,
        entry_high=2401.0,
        stop_loss=2390.0,
        take_profits=(2420.0,),
        reason="duplicate",
        indicators={},
        generated_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )
    assert repo.record(duplicate) is None
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(OrderRow)) == 0
    assert first > 0


def test_rm017_is_checked_before_any_order_leaves_the_process(engine: Engine) -> None:
    terminal = SimulatedTerminal()
    terminal.set_tick("XAUUSD", bid=2400.0, ask=2400.2)
    terminal.is_demo = False  # a real account while the mode is DEMO (RM-017)
    broker = MT5Broker(terminal, OrderJournal(engine), login=terminal.login, mode=TradingMode.DEMO)
    signal_id = make_signal(engine)
    request = OrderRequest(
        signal_id=signal_id,
        idempotency_key="rm017-before-order",
        symbol="XAUUSD",
        direction=Direction.BUY,
        volume=Decimal("0.01"),
        stop_loss=Decimal("2390"),
        take_profit=Decimal("2420"),
        mode=TradingMode.DEMO,
        comment="ta",
    )

    with pytest.raises(AccountModeMismatchError):
        asyncio.run(broker.place(request))

    assert terminal.calls.count("order_send") == 0


def test_the_same_idempotency_key_never_sends_a_second_order(engine: Engine) -> None:
    terminal = SimulatedTerminal()
    terminal.set_tick("XAUUSD", bid=2400.0, ask=2400.2)
    broker = MT5Broker(terminal, OrderJournal(engine), login=terminal.login, mode=TradingMode.DEMO)
    signal_id = make_signal(engine)
    request = OrderRequest(
        signal_id=signal_id,
        idempotency_key=f"{MANIFEST.ref}:XAUUSD:{Timeframe.M15}:{NOW:%Y-%m-%dT%H:%MZ}",
        symbol="XAUUSD",
        direction=Direction.BUY,
        volume=Decimal("0.01"),
        stop_loss=Decimal("2390"),
        take_profit=Decimal("2420"),
        mode=TradingMode.DEMO,
        comment="ta",
    )

    first = asyncio.run(broker.place(request))
    second = asyncio.run(broker.place(request))

    assert first.ticket is not None
    assert first.ticket == second.ticket
    assert terminal.calls.count("order_send") == 1
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(OrderRow)) == 1
    assert terminal.position_tickets() != ()


# --- the emergency stop survives a restart ----------------------------------------


def test_a_global_halt_survives_a_process_restart(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'restart.db'}"
    upgrade(url)
    first = create_database_engine(url)
    HaltStore(first).issue(
        HaltCommand(GLOBAL, HaltAction.HALT, HaltSource.TELEGRAM, "emergency stop", "operator", NOW)
    )
    first.dispose()

    rebuilt = create_database_engine(url)
    try:
        status = HaltStore(rebuilt).status()
        assert status.halted is True
        assert any("emergency stop" in reason for reason in status.reasons)
    finally:
        rebuilt.dispose()


def test_a_resume_command_lifts_the_halt_after_the_restart(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'restart2.db'}"
    upgrade(url)
    first = create_database_engine(url)
    halts = HaltStore(first)
    halts.issue(HaltCommand(GLOBAL, HaltAction.HALT, HaltSource.TELEGRAM, "stop", "operator", NOW))
    halts.issue(
        HaltCommand(GLOBAL, HaltAction.RESUME, HaltSource.TELEGRAM, "all clear", "operator", NOW)
    )
    first.dispose()

    rebuilt = create_database_engine(url)
    try:
        assert HaltStore(rebuilt).status().halted is False
    finally:
        rebuilt.dispose()


def test_an_automatic_agent_cannot_lift_a_global_halt(tmp_path: Path) -> None:
    from tradingagent.storage.halts import OperatorRequiredError

    url = f"sqlite:///{tmp_path / 'restart3.db'}"
    upgrade(url)
    engine = create_database_engine(url)
    try:
        with pytest.raises(OperatorRequiredError):
            HaltStore(engine).issue(
                HaltCommand(
                    GLOBAL, HaltAction.RESUME, HaltSource.AUTOMATIC, "self heal", "agent", NOW
                )
            )
    finally:
        engine.dispose()
