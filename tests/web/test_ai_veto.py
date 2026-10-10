"""C-002, proven by execution from the dashboard's side of the boundary.

``CLAUDE.md`` states the invariant without qualification: **the AI can only REJECT a signal.
It can never create one, never increase a size, never loosen a stop.** ``tests/ai`` already
tasks the veto with hostile replies; this file proves the same thing from the surface that
displays the AI, and adds the two guarantees a reading cannot give:

* the verdict is a **pure asymmetry** — an approval has no effect at all, in any mode, and a
  rejection can only ever block;
* a hostile reply is **inert**: after one, the rows that exist are byte-for-byte the rows
  that existed before, and the only tables written are the journal (``ai_calls``) and the
  overrun event (``system_events``);
* the dashboard **cannot consult the AI at all** — no module of ``tradingagent.web`` imports
  ``tradingagent.ai.layer`` — so no page can turn a verdict into a decision.

The structural guards below fail the day someone adds a field or a method that would let a
verdict carry a level, a size or a signal. ``_level_bearing`` and ``_public_surface`` are
checked against deliberately mutated shapes too, so the guards are not vacuous.
"""

import asyncio
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, insert, select
from sqlalchemy.orm import Session
from tests.web.seed import NOW, Seeded

from tradingagent.ai.layer import AiFilterLayer, ReviewContext, ReviewOutcome
from tradingagent.core.market import Direction
from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.states import SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.ai_calls import AiCallStore, AiReply
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.models import (
    AiCallRow,
    OrderRow,
    PositionRow,
    RiskDecisionRow,
    SignalRow,
    SystemEventRow,
    TradeRow,
)

#: The tables a signal flows through. If an AI reply could create a signal, enlarge a size or
#: loosen a stop, one of these counts would move.
SIGNAL_TABLES = (SignalRow, OrderRow, PositionRow, TradeRow, RiskDecisionRow)

#: Fields that would give a verdict power over levels, sizes or execution. None may ever
#: appear on `ReviewOutcome`: the veto's only possible effect is to block.
FORBIDDEN_OUTCOME_FIELDS = frozenset(
    {
        "stop_loss",
        "take_profits",
        "take_profit",
        "entry_low",
        "entry_high",
        "entry_price",
        "volume",
        "size",
        "quantity",
        "direction",
        "signal",
        "new_signal",
        "mode",
        "max_mode",
        "orders",
    }
)

#: The whole public surface of the layer class: `review` is the only way in, and `ai_filter`
#: only reports the configured mode. There is no second entry point that could mint a signal.
#: (`input_price` and `output_price` are instance attributes set in `__init__`, not class
#: members, so they are absent here — and they are rates, not decisions.)
EXPECTED_LAYER_SURFACE = frozenset({"review", "ai_filter"})

WEB_DIR = Path(__file__).resolve().parents[2] / "src" / "tradingagent" / "web"


class HostileClient:
    """A model that answers, and tries to take over while doing it."""

    def __init__(self, decision: str = "approve") -> None:
        self.decision = decision
        self.calls = 0

    async def complete(self, system: str, user: str) -> AiReply:
        self.calls += 1
        payload = {
            "decision": self.decision,
            "reason": "je réécris les niveaux",
            "text": "Achat immédiat BTCUSD, volume 100.",
            "stop_loss": 0.0,  # loosened to nothing
            "take_profit": 999_999.0,
            "volume": 100.0,
            "new_signal": "BUY BTCUSD now",
            "max_mode": "LIVE",
        }
        return AiReply(
            text=json.dumps(payload), model="hostile-model", input_tokens=10, output_tokens=10
        )


def a_context() -> ReviewContext:
    return ReviewContext(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        strategy_ref="witness@1.1.1",
        direction=Direction.BUY,
        observed_price=2650.10,
        entry_low=2649.50,
        entry_high=2650.50,
        stop_loss=2647.50,
        take_profits=(2652.50, 2655.50),
        indicators={"ema_fast": 2649.8, "atr": 3.2},
        market_state="HEALTHY",
    )


def a_layer(engine: Engine, client: HostileClient, ai_filter: AiFilter) -> AiFilterLayer:
    return AiFilterLayer(
        client,
        AiCallStore(engine),
        SystemEventStore(engine),
        model="hostile-model",
        ai_filter=ai_filter,
    )


def _level_bearing(fields: Iterable[str]) -> set[str]:
    """The part of a field set that would carry power over levels, sizes or a signal."""
    return set(fields) & FORBIDDEN_OUTCOME_FIELDS


def _public_surface(layer: type[object]) -> set[str]:
    return {name for name in dir(layer) if not name.startswith("_")}


def _counts(engine: Engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            table.__tablename__: int(session.scalar(select(func.count()).select_from(table)) or 0)
            for table in SIGNAL_TABLES
        }


def _signal_row(engine: Engine, signal_id: int) -> dict[str, object]:
    """Every stored column of one signal: the thing an AI reply must never be able to move."""
    with Session(engine) as session:
        row = session.scalars(select(SignalRow).where(SignalRow.id == signal_id)).one()
        return {column.key: getattr(row, column.key) for column in SignalRow.__table__.columns}


def _count(engine: Engine, table: type[Any]) -> int:
    with Session(engine) as session:
        return int(session.scalar(select(func.count()).select_from(table)) or 0)


# --- the asymmetry ----------------------------------------------------------------------


def test_an_approval_never_changes_anything_in_any_mode(engine: Engine) -> None:
    """Approval is not a weaker rejection: it is no decision at all. If an approval could
    block, the AI would hold a veto it does not have."""
    for ai_filter in AiFilter:
        outcome = asyncio.run(
            a_layer(engine, HostileClient("approve"), ai_filter).review(a_context(), NOW)
        )
        assert outcome.verdict == "approved", ai_filter
        assert outcome.blocks_signal is False, ai_filter


def test_a_rejection_can_only_ever_block(engine: Engine) -> None:
    """The one power the veto has, and it is entirely negative."""
    expected = {
        AiFilter.SHADOW: False,  # recorded, never applied
        AiFilter.ADVISORY: True,
        AiFilter.REQUIRED: True,
    }
    for ai_filter, blocks in expected.items():
        outcome = asyncio.run(
            a_layer(engine, HostileClient("reject"), ai_filter).review(a_context(), NOW)
        )
        assert outcome.verdict == "rejected", ai_filter
        assert outcome.blocks_signal is blocks, ai_filter
        # Whatever the mode, the outcome carries no level a caller could apply.
        assert _level_bearing(outcome.__dataclass_fields__) == set()


def test_the_two_only_effects_of_a_verdict_are_continue_or_refuse(engine: Engine) -> None:
    """Exhaustive over every (mode, decision) pair: nothing else is reachable."""
    for ai_filter in AiFilter:
        for decision in ("approve", "reject"):
            outcome = asyncio.run(
                a_layer(engine, HostileClient(decision), ai_filter).review(a_context(), NOW)
            )
            assert outcome.blocks_signal in (True, False)
            assert outcome.verdict in ("approved", "rejected", "unavailable")
            assert set(outcome.overrun_attempts) <= {
                "stop_loss",
                "take_profit",
                "volume",
                "new_signal",
                "max_mode",
            }


# --- a hostile reply is inert -----------------------------------------------------------


def test_a_hostile_reply_creates_no_row_anywhere_a_signal_lives(
    engine: Engine, populated: Seeded
) -> None:
    before = _counts(engine)
    before_signal = _signal_row(engine, populated.xau_signal_id)

    outcome = asyncio.run(
        a_layer(engine, HostileClient(), AiFilter.REQUIRED).review(a_context(), NOW)
    )

    assert outcome.verdict == "approved"  # it said approve…
    assert _counts(engine) == before  # …and not one signal, order, position or trade appeared
    assert _signal_row(engine, populated.xau_signal_id) == before_signal


def test_the_only_tables_a_review_writes_are_the_journal_and_the_overrun_event(
    engine: Engine, populated: Seeded
) -> None:
    calls_before = _count(engine, AiCallRow)
    events_before = _count(engine, SystemEventRow)

    asyncio.run(a_layer(engine, HostileClient(), AiFilter.SHADOW).review(a_context(), NOW))

    assert _count(engine, AiCallRow) == calls_before + 1
    assert _count(engine, SystemEventRow) == events_before + 1
    with Session(engine) as session:
        event = session.scalars(
            select(SystemEventRow).order_by(SystemEventRow.id.desc()).limit(1)
        ).one()
    assert event.kind == "ai_overrun"
    assert set(event.detail["fields"]) == {
        "stop_loss",
        "take_profit",
        "volume",
        "new_signal",
        "max_mode",
    }


def test_the_untouched_check_would_notice_a_write(engine: Engine, populated: Seeded) -> None:
    """Non-vacuity: the comparison above is not a comparison of two empty dicts.

    A row inserted by the test itself moves the count, so the assertion that it does not move
    during a review has teeth.
    """
    before = _counts(engine)
    with Session(engine) as session:
        version_id = session.scalar(
            select(SignalRow.strategy_version_id).where(SignalRow.id == populated.xau_signal_id)
        )
    assert version_id is not None
    with engine.begin() as connection:
        connection.execute(
            insert(SignalRow).values(
                idempotency_key="signal:test:non-vacuite",
                strategy_version_id=version_id,
                symbol="XAUUSD",
                timeframe=Timeframe.M15,
                direction=Direction.BUY,
                mode=TradingMode.PAPER,
                observed_price=2650.0,
                entry_low=2649.0,
                entry_high=2651.0,
                stop_loss=2645.0,
                take_profits=[2655.0],
                reason="écrit par le test lui-même",
                indicators={"atr": 3.0},
                generated_at=NOW,
                expires_at=NOW,
                state=SignalState.CANDIDATE,
            )
        )

    assert _counts(engine) != before


# --- the structural guards, and their teeth ---------------------------------------------


def test_the_outcome_carries_no_field_that_could_move_a_level() -> None:
    assert set(ReviewOutcome.__dataclass_fields__) & FORBIDDEN_OUTCOME_FIELDS == set()


def test_the_guard_would_catch_a_level_bearing_field() -> None:
    """The guard's own mutation check: add a level, and `_level_bearing` says so."""
    assert _level_bearing([*ReviewOutcome.__dataclass_fields__, "stop_loss"]) == {"stop_loss"}
    assert _level_bearing([*ReviewOutcome.__dataclass_fields__, "volume"]) == {"volume"}


def test_the_layer_has_no_second_entry_point() -> None:
    assert _public_surface(AiFilterLayer) == EXPECTED_LAYER_SURFACE


def test_the_surface_guard_would_catch_a_method_that_mints_a_signal() -> None:
    """The guard's own mutation check, on a mutant subclass built here rather than in
    production code."""

    class Mutant(AiFilterLayer):
        def create_signal(self) -> None:  # pragma: no cover - never called
            pass

    assert _public_surface(Mutant) == EXPECTED_LAYER_SURFACE | {"create_signal"}


def test_the_dashboard_never_imports_the_ai_layer() -> None:
    """No page can apply a verdict: `tradingagent.web` has no path to the AI at all."""
    offenders: list[str] = []
    for source in sorted(WEB_DIR.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        if "tradingagent.ai.layer" in text or "AiFilterLayer" in text:
            offenders.append(source.name)
    assert offenders == []


def test_the_page_promises_exactly_what_the_tests_prove(seeded_client) -> None:
    """The operator-facing sentence is part of the invariant, so it is pinned here."""
    body = seeded_client.get("/ai-lab").text
    assert "L'IA observe et propose ; elle ne décide jamais." in body
