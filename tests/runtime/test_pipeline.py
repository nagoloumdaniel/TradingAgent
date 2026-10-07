"""The pipeline: AI can only block, risk decides, notification then execution (F-009..F-017)."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from tests.runtime.fakes import (
    BTC,
    GOLD,
    LOGIN,
    NOW,
    RISK,
    FakeAi,
    FakeBroker,
    FakeNotifier,
    open_calendar,
    weekend_calendar,
)

from tradingagent.core.market import Direction
from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.states import HaltAction, HaltSource, RiskOutcome, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar
from tradingagent.risk.model import BrokerPosition, OpenPosition
from tradingagent.runtime.pipeline import SignalPipeline
from tradingagent.runtime.portfolio import PortfolioBuilder
from tradingagent.storage.account import AccountStore
from tradingagent.storage.halts import HaltCommand, HaltStore
from tradingagent.storage.models import RiskDecisionRow, SignalRow, SystemEventRow
from tradingagent.storage.signals import SignalRecord, SignalRepository, idempotency_key
from tradingagent.strategies.manifest import StrategyManifest

MANIFEST = StrategyManifest.model_validate(
    {
        "strategy_id": "witness",
        "version": "1.1.0",
        "max_mode": "SIGNAL",
        "allowed_symbols": [GOLD],
        "timeframes": ["M15"],
        "history_bars": 300,
    }
)


def manifest_with(ai_filter: AiFilter) -> StrategyManifest:
    return MANIFEST.model_copy(update={"ai_filter": ai_filter})


async def _no_sleep(_: float) -> None:
    return None


class Parts:
    def __init__(
        self,
        engine: Engine,
        mode: TradingMode = TradingMode.SIGNAL,
        calendar_for: Callable[[str], MarketCalendar | None] = open_calendar,
        now: Callable[[], datetime] = lambda: NOW,
    ) -> None:
        self.engine = engine
        self.broker = FakeBroker()
        self.notifier = FakeNotifier()
        self.ai = FakeAi()
        self.pipeline = SignalPipeline(
            engine=engine,
            halts=HaltStore(engine),
            broker=self.broker,
            notifier=self.notifier,
            portfolio=PortfolioBuilder(engine),
            risk_config=RISK,
            expected_login=LOGIN,
            mode=mode,
            calendar_for=calendar_for,
            ai=self.ai,
            now=now,
            sleep=_no_sleep,
        )

    def run(self, signal_id: int):
        return asyncio.run(self.pipeline.process(signal_id))


def record_signal(
    engine: Engine,
    *,
    mode: TradingMode = TradingMode.SIGNAL,
    symbol: str = GOLD,
    direction: Direction = Direction.BUY,
    stop_loss: float = 2387.8927,
    entry_low: float = 2399.0,
    entry_high: float = 2401.0,
    take_profits: tuple[float, ...] = (2424.0,),
    ai_filter: AiFilter = AiFilter.SHADOW,
) -> int:
    signal_id = SignalRepository(engine).record(
        SignalRecord(
            idempotency_key=idempotency_key(MANIFEST.ref, symbol, Timeframe.M15, NOW),
            manifest=manifest_with(ai_filter),
            symbol=symbol,
            timeframe=Timeframe.M15,
            direction=direction,
            mode=mode,
            observed_price=2400.0,
            entry_low=entry_low,
            entry_high=entry_high,
            stop_loss=stop_loss,
            take_profits=take_profits,
            reason="crossover de test",
            indicators={"atr": 12.0},
            generated_at=NOW,
            expires_at=NOW + timedelta(minutes=15),
        )
    )
    assert signal_id is not None
    return signal_id


def state_of(engine: Engine, signal_id: int) -> SignalState:
    with Session(engine) as session:
        return session.scalars(select(SignalRow.state).where(SignalRow.id == signal_id)).one()


def events_of(engine: Engine, kind: str) -> list[SystemEventRow]:
    with Session(engine) as session:
        return list(
            session.scalars(select(SystemEventRow).where(SystemEventRow.kind == kind)).all()
        )


def test_a_validated_signal_is_sent_once_with_every_required_field(engine: Engine) -> None:
    parts = Parts(engine)
    signal_id = record_signal(engine)
    outcome = parts.run(signal_id)

    assert outcome.kind == "notified"
    assert len(parts.notifier.messages) == 1
    message = parts.notifier.messages[0]
    for required in (
        "XAUUSD",
        "ACHAT",
        "Stop-loss",
        "Objectifs",
        "Risque/rendement",
        "Stratégie",
        "Expire le",
        "Mode",
    ):
        assert required in message, required
    assert state_of(engine, signal_id) is SignalState.SENT
    with Session(engine) as session:
        decisions = session.scalars(
            select(RiskDecisionRow).where(RiskDecisionRow.signal_id == signal_id)
        ).all()
    assert len(decisions) == 1
    assert decisions[0].outcome is RiskOutcome.AUTHORIZED
    assert parts.broker.placed == []


def test_a_halt_refuses_the_signal_and_persists_the_reason(engine: Engine) -> None:
    parts = Parts(engine)
    HaltStore(engine).issue(
        HaltCommand("global", HaltAction.HALT, HaltSource.SERVER, "maintenance", "operator", NOW)
    )
    signal_id = record_signal(engine)
    outcome = parts.run(signal_id)

    assert outcome.kind == "refused"
    assert "not_halted" in outcome.detail
    assert parts.notifier.messages == []
    assert state_of(engine, signal_id) is SignalState.RISK_REJECTED


def test_a_notable_refusal_is_sent_to_the_operator(engine: Engine) -> None:
    parts = Parts(engine)
    AccountStore(engine).record(Decimal(10000), Decimal(10000), NOW.replace(hour=0, minute=0))
    signal_id = record_signal(engine)
    outcome = parts.run(signal_id)

    assert outcome.kind == "refused"
    assert len(parts.notifier.messages) == 1
    assert "Refus risque" in parts.notifier.messages[0]
    assert "daily_loss" in parts.notifier.messages[0]
    assert "Aucun ordre n'a été envoyé" in parts.notifier.messages[0]


def test_a_shadow_rejection_does_not_block_the_signal(engine: Engine) -> None:
    parts = Parts(engine)
    parts.ai.verdict = "rejected"
    signal_id = record_signal(engine, ai_filter=AiFilter.SHADOW)
    outcome = parts.run(signal_id)

    assert outcome.kind == "notified"
    assert parts.ai.calls == 1
    assert parts.ai.contexts[0].stop_loss == pytest.approx(2387.8927)
    assert len(parts.notifier.messages) == 1
    assert state_of(engine, signal_id) is SignalState.SENT


def test_an_advisory_rejection_blocks_the_signal(engine: Engine) -> None:
    parts = Parts(engine)
    parts.ai.verdict = "rejected"
    signal_id = record_signal(engine, ai_filter=AiFilter.ADVISORY)
    outcome = parts.run(signal_id)

    assert outcome.kind == "ai_blocked"
    assert parts.notifier.messages == []
    assert state_of(engine, signal_id) is SignalState.EXPIRED


def test_the_ai_can_only_reject_and_never_reach_the_risk_levels(engine: Engine) -> None:
    parts = Parts(engine)
    parts.ai.overruns = ("volume", "stop_loss", "new_signal", "mode")
    signal_id = record_signal(engine)
    outcome = parts.run(signal_id)

    assert outcome.kind == "notified"
    assert state_of(engine, signal_id) is SignalState.SENT
    with Session(engine) as session:
        decision = session.scalars(
            select(RiskDecisionRow).where(RiskDecisionRow.signal_id == signal_id)
        ).one()
    assert decision.volume == Decimal("0.02")
    with Session(engine) as session:
        stored = session.scalars(select(SignalRow).where(SignalRow.id == signal_id)).one()
    assert stored.stop_loss == pytest.approx(2387.8927)


def test_the_telegram_outage_delays_the_signal_then_the_retry_sends_it(engine: Engine) -> None:
    parts = Parts(engine)
    parts.notifier.deliver = False
    signal_id = record_signal(engine)
    outcome = parts.run(signal_id)

    assert outcome.kind == "notification_pending"
    assert state_of(engine, signal_id) is SignalState.VALIDATED

    parts.notifier.deliver = True
    assert asyncio.run(parts.pipeline.retry_pending()) == 1
    assert state_of(engine, signal_id) is SignalState.SENT
    assert len(parts.notifier.messages) == 4  # three failed attempts, then the successful one


def test_paper_mode_places_the_order_and_opens_the_position(engine: Engine) -> None:
    parts = Parts(engine, mode=TradingMode.PAPER)
    signal_id = record_signal(engine, mode=TradingMode.PAPER)
    outcome = parts.run(signal_id)

    assert outcome.kind == "executed"
    assert len(parts.notifier.messages) == 2  # the signal, then the compact position notice
    assert parts.notifier.messages[1].splitlines()[0] == "📈 OUVERT · XAUUSD · ACHAT"
    assert len(parts.broker.placed) == 1
    assert parts.broker.placed[0].volume == Decimal("0.02")
    assert parts.broker.placed[0].idempotency_key.startswith("witness@1.1.0:XAUUSD")
    assert parts.broker.placed[0].stop_loss == Decimal("2387.8927")
    assert parts.broker.placed[0].take_profit == Decimal("2424.0")
    assert state_of(engine, signal_id) is SignalState.POSITION_OPEN


def test_a_position_without_its_stop_is_closed_immediately(engine: Engine) -> None:
    parts = Parts(engine, mode=TradingMode.PAPER)
    parts.broker.stop_present = False
    signal_id = record_signal(engine, mode=TradingMode.PAPER)
    outcome = parts.run(signal_id)

    assert outcome.kind == "stop_missing"
    assert parts.broker.closed == [(555001, "stop missing after execution")]
    assert state_of(engine, signal_id) is SignalState.ERROR
    assert len(events_of(engine, "stop_missing")) == 1


def test_an_account_that_contradicts_the_mode_never_trades(engine: Engine) -> None:
    parts = Parts(engine)
    parts.broker.login = 99999999
    signal_id = record_signal(engine)
    outcome = parts.run(signal_id)

    assert outcome.kind == "account_mismatch"
    assert parts.notifier.messages == []
    assert parts.broker.placed == []
    assert len(events_of(engine, "account_mismatch")) == 1


def test_a_known_signal_is_never_processed_twice(engine: Engine) -> None:
    parts = Parts(engine)
    signal_id = record_signal(engine)
    assert parts.run(signal_id).kind == "notified"
    second = parts.run(signal_id)
    assert second.kind == "already_processed"
    assert len(parts.notifier.messages) == 1


def test_an_unknown_signal_is_reported_not_crashed(engine: Engine) -> None:
    parts = Parts(engine)
    assert parts.run(999).kind == "missing"


def test_the_open_exposure_is_measured_from_the_broker(engine: Engine) -> None:
    """§21: volume * contract size * price, converted to the account currency."""
    parts = Parts(engine)
    empty = asyncio.run(parts.pipeline._open_exposure(()))
    assert empty == Decimal(0)

    one = asyncio.run(parts.pipeline._open_exposure((OpenPosition("XAUUSD", Decimal("0.02")),)))
    expected = (Decimal("0.02") * Decimal(100) * Decimal("2400.2") * Decimal("0.888786")).quantize(
        Decimal("0.000001")
    )
    assert one is not None
    assert one.quantize(Decimal("0.000001")) == expected


def test_an_unmeasurable_exposure_is_none_and_blocks_the_order(engine: Engine) -> None:
    """Failing closed: the risk engine refuses what it cannot measure."""
    parts = Parts(engine)
    parts.broker.fail_quote_symbols = {"BTCUSD"}

    measured = asyncio.run(
        parts.pipeline._open_exposure((OpenPosition("BTCUSD", Decimal("0.01")),))
    )
    assert measured is None

    signal_id = record_signal(engine)
    parts.broker._positions[999] = BrokerPosition(
        ticket=999,
        symbol="BTCUSD",
        direction=Direction.BUY,
        volume=Decimal("0.01"),
        open_price=Decimal("62000"),
        stop_loss=Decimal("61000"),
        take_profit=None,
        mode=TradingMode.SIGNAL,
    )
    outcome = parts.run(signal_id)
    assert outcome.kind == "refused"
    assert "total_exposure" in outcome.detail


def test_a_closed_gold_market_never_stops_bitcoin(engine: Engine) -> None:
    """F-005, the operator's requirement: gold is off, the crypto keeps producing signals."""
    saturday = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    gold, bitcoin = weekend_calendar(GOLD), open_calendar(BTC)
    parts = Parts(
        engine,
        calendar_for=lambda symbol: gold if symbol == GOLD else bitcoin,
        now=lambda: saturday,
    )
    gold_id = record_signal(engine, symbol=GOLD)
    bitcoin_id = record_signal(engine, symbol=BTC)

    gold_outcome = parts.run(gold_id)
    bitcoin_outcome = parts.run(bitcoin_id)

    assert gold_outcome.kind == "refused"
    assert "trading_hours" in gold_outcome.detail
    assert state_of(engine, gold_id) is SignalState.RISK_REJECTED
    assert bitcoin_outcome.kind == "notified"
    assert state_of(engine, bitcoin_id) is SignalState.SENT
    assert any(BTC in message for message in parts.notifier.messages)
