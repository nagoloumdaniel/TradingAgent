"""The economic-calendar gate sits before the AI and the risk engine, and fails open."""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import Engine
from tests.runtime.fakes import (
    LOGIN,
    NOW,
    RISK,
    FakeAi,
    FakeBroker,
    FakeNotifier,
    open_calendar,
)
from tests.runtime.test_pipeline import events_of, record_signal, state_of

from tradingagent.core.mode import TradingMode
from tradingagent.core.states import Severity, SignalState
from tradingagent.news.blackout import CLEAR, BlackoutDecision
from tradingagent.news.model import EconomicEvent, EventKind, Impact, event_key
from tradingagent.runtime.pipeline import SignalPipeline
from tradingagent.runtime.portfolio import PortfolioBuilder
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.signals import get_signal

CPI = EconomicEvent(
    key=event_key("t", "USD", "Core CPI m/m", NOW + timedelta(minutes=10)),
    starts_at=NOW + timedelta(minutes=10),
    currency="USD",
    title="Core CPI m/m",
    impact=Impact.HIGH,
    kind=EventKind.RELEASE,
)


class FakeNews:
    def __init__(self, decision: BlackoutDecision = CLEAR, *, broken: bool = False) -> None:
        self.decision = decision
        self.broken = broken
        self.checked: list[str] = []

    async def tick(self, now: datetime) -> object:
        return None

    def check(self, symbol: str, now: datetime) -> BlackoutDecision:
        self.checked.append(symbol)
        if self.broken:
            raise RuntimeError("calendar store unreadable")
        return self.decision

    def upcoming_summary(self, now: datetime, hours: int = 24, limit: int = 8) -> str:
        if self.broken:
            raise RuntimeError("calendar store unreadable")
        return "- 12:40 UTC (dans 10 min) USD Core CPI m/m [high] [majeure]"


class Rig:
    def __init__(self, engine: Engine, news: FakeNews) -> None:
        self.engine = engine
        self.notifier = FakeNotifier()
        self.ai = FakeAi()
        self.news = news
        self.pipeline = SignalPipeline(
            engine=engine,
            halts=HaltStore(engine),
            broker=FakeBroker(),
            notifier=self.notifier,
            portfolio=PortfolioBuilder(engine),
            risk_config=RISK,
            expected_login=LOGIN,
            mode=TradingMode.SIGNAL,
            calendar_for=open_calendar,
            ai=self.ai,
            news=news,
            now=lambda: NOW,
        )

    def run(self, signal_id: int):
        return asyncio.run(self.pipeline.process(signal_id))


def blocked() -> BlackoutDecision:
    return BlackoutDecision(
        blocked=True,
        reason="Core CPI m/m dans 10 min (fenêtre 30/30 min)",
        event=CPI,
        window_start=NOW - timedelta(minutes=20),
        window_end=NOW + timedelta(minutes=40),
    )


def test_a_candidate_inside_the_window_is_refused_with_its_motif(engine: Engine) -> None:
    rig = Rig(engine, FakeNews(blocked()))
    signal_id = record_signal(engine)
    outcome = rig.run(signal_id)

    assert outcome.kind == "news_blackout" and "Core CPI" in outcome.detail
    assert state_of(engine, signal_id) is SignalState.EXPIRED
    assert rig.notifier.messages == []
    assert rig.ai.calls == 0  # refused before the model was asked, and before the risk engine
    [event] = events_of(engine, "news_blackout")
    assert event.detail["event"] == "Core CPI m/m" and event.symbol == "XAUUSD"


def test_a_clear_calendar_lets_the_signal_through_and_feeds_the_model(engine: Engine) -> None:
    rig = Rig(engine, FakeNews())
    signal_id = record_signal(engine)
    assert rig.run(signal_id).kind == "notified"
    assert rig.news.checked == ["XAUUSD"]
    assert "Core CPI m/m" in rig.ai.contexts[0].upcoming_events


def test_a_blind_calendar_does_not_block_and_leaves_a_trace(engine: Engine) -> None:
    rig = Rig(
        engine, FakeNews(BlackoutDecision(blocked=False, degraded="calendrier jamais rafraîchi"))
    )
    signal_id = record_signal(engine)
    assert rig.run(signal_id).kind == "notified"
    [event] = events_of(engine, "news_gate_degraded")
    assert "jamais" in event.detail["reason"]


def test_a_broken_calendar_never_stops_the_pipeline(engine: Engine) -> None:
    rig = Rig(engine, FakeNews(broken=True))
    signal_id = record_signal(engine)
    assert rig.run(signal_id).kind == "notified"
    assert state_of(engine, signal_id) is SignalState.SENT
    assert rig.ai.contexts[0].upcoming_events == ""


# --- visibility: a blind gate is a warning, once per episode, and a gate fault is journalled ---


def gate_check(rig: Rig, signal_id: int) -> object:
    detail = get_signal(rig.engine, signal_id)
    assert detail is not None
    return asyncio.run(rig.pipeline._news_blackout(detail, NOW))


def test_a_blind_gate_is_a_warning_not_an_info(engine: Engine) -> None:
    rig = Rig(engine, FakeNews(BlackoutDecision(blocked=False, degraded="calendrier vide")))
    rig.run(record_signal(engine))
    [event] = events_of(engine, "news_gate_degraded")
    assert event.severity is Severity.WARNING


def test_a_blind_gate_is_told_once_per_episode_not_once_per_signal(engine: Engine) -> None:
    news = FakeNews(BlackoutDecision(blocked=False, degraded="calendrier jamais rafraîchi"))
    rig = Rig(engine, news)
    signal_id = record_signal(engine)
    for _ in range(5):
        gate_check(rig, signal_id)
    assert len(events_of(engine, "news_gate_degraded")) == 1
    # The calendar comes back, then goes blind again: that is a new episode, told again.
    news.decision = CLEAR
    gate_check(rig, signal_id)
    news.decision = BlackoutDecision(blocked=False, degraded="calendrier rafraîchi il y a 400 min")
    gate_check(rig, signal_id)
    gate_check(rig, signal_id)
    assert len(events_of(engine, "news_gate_degraded")) == 2


def test_a_gate_that_raises_is_journalled_once_per_episode(engine: Engine) -> None:
    news = FakeNews(broken=True)
    rig = Rig(engine, news)
    signal_id = record_signal(engine)
    for _ in range(3):
        assert gate_check(rig, signal_id) is None  # fail-open: the signal goes on
    [event] = events_of(engine, "news_gate_failed")
    assert event.severity is Severity.WARNING
    assert "RuntimeError" in event.detail["error"]
    assert event.symbol == "XAUUSD"
    news.broken = False
    gate_check(rig, signal_id)
    news.broken = True
    gate_check(rig, signal_id)
    assert len(events_of(engine, "news_gate_failed")) == 2


def test_a_fail_closed_refusal_carries_its_motif_and_is_reject_only(engine: Engine) -> None:
    motif = (
        "calendrier jamais rafraîchi : signal refusé "
        "(news.fail_closed_when_blind actif en mode réel)"
    )
    rig = Rig(
        engine,
        FakeNews(
            BlackoutDecision(blocked=True, reason=motif, degraded="calendrier jamais rafraîchi")
        ),
    )
    signal_id = record_signal(engine)
    outcome = rig.run(signal_id)
    assert outcome.kind == "news_blackout" and "fail_closed_when_blind" in outcome.detail
    assert state_of(engine, signal_id) is SignalState.EXPIRED
    assert rig.notifier.messages == []
