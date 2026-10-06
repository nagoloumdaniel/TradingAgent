import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.ai.layer import AiFilterLayer, ReviewContext
from tradingagent.core.market import Direction
from tradingagent.core.mode import AiFilter
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.ai_calls import AiCallStore, AiReply
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import AiCallRow, SystemEventRow

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'ai.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


class FakeClient:
    """A model under test: canned reply, counted calls, optional latency."""

    def __init__(self, reply: str | Exception = "", seconds: float = 0.0) -> None:
        self.reply = reply
        self.seconds = seconds
        self.calls = 0

    async def complete(self, system: str, user: str) -> AiReply:
        self.calls += 1
        assert "Zone d'entrée" in user  # the bounded context is in the prompt
        if self.seconds:
            await asyncio.sleep(self.seconds)
        if isinstance(self.reply, Exception):
            raise self.reply
        return AiReply(text=self.reply, model="fake-model", input_tokens=100, output_tokens=50)


def a_context() -> ReviewContext:
    return ReviewContext(
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        strategy_ref="witness@1.0.0",
        direction=Direction.BUY,
        observed_price=2650.10,
        entry_low=2649.50,
        entry_high=2650.50,
        stop_loss=2647.50,
        take_profits=(2652.50, 2655.50),
        indicators={"ema_fast": 2649.8, "ema_slow": 2648.1, "atr": 3.2},
        market_state="HEALTHY",
    )


def a_layer(
    engine: Engine, client: FakeClient, ai_filter: AiFilter = AiFilter.SHADOW, **config: object
) -> AiFilterLayer:
    return AiFilterLayer(
        client,
        AiCallStore(engine),
        SystemEventStore(engine),
        model="fake-model",
        ai_filter=ai_filter,
        **config,  # type: ignore[arg-type]
    )


def answer(**overrides: str) -> str:
    payload = {
        "decision": "approve",
        "reason": "crossover coherent with the trend",
        "text": "Le croisement d'EMA confirme la tendance haussière.",
    }
    payload.update(overrides)
    return json.dumps(payload)


def outcome_rows(engine: Engine) -> list[AiCallRow]:
    with Session(engine) as session:
        return list(session.scalars(select(AiCallRow)).all())


def overrun_events(engine: Engine) -> list:
    with Session(engine) as session:
        return list(
            session.scalars(select(SystemEventRow).where(SystemEventRow.kind == "ai_overrun")).all()
        )


# --- the decisive test (EF-018) ---------------------------------------------


def test_a_malicious_response_cannot_create_a_signal_or_change_any_level(engine: Engine) -> None:
    malicious = answer()[:-1] + (
        ', "take_profit": 99999.0, "stop_loss": 0.0, "volume": 100.0, '
        '"new_signal": "BUY BTCUSD now", "max_mode": "LIVE"}'
    )
    client = FakeClient(malicious)

    outcome = asyncio.run(a_layer(engine, client).review(a_context(), T0))

    # The model said approve; every out-of-scope instruction was ignored and journalled.
    assert outcome.verdict == "approved"
    assert outcome.blocks_signal is False  # shadow mode: no effect either way
    assert set(outcome.overrun_attempts) == {
        "take_profit",
        "stop_loss",
        "volume",
        "new_signal",
        "max_mode",
    }
    assert len(overrun_events(engine)) == 1


def test_a_malicious_rejection_still_cannot_touch_the_levels(engine: Engine) -> None:
    malicious = answer(
        decision="reject",
        reason="reject and widen the stop and double the size on the next one",
    )
    client = FakeClient(malicious)
    outcome = asyncio.run(a_layer(engine, client, AiFilter.REQUIRED).review(a_context(), T0))

    # In required mode the rejection IS applied, but it can only degrade the decision:
    # nothing in the outcome carries levels, sizes or any positive power.
    assert outcome.verdict == "rejected"
    assert outcome.blocks_signal is True
    assert not outcome.overrun_attempts
    context_fields = set(outcome.__dataclass_fields__)
    assert not context_fields & {"stop_loss", "take_profits", "volume", "entry_low", "entry_high"}


# --- the three states of C-002 -----------------------------------------------


def test_in_shadow_mode_a_rejection_is_recorded_and_the_signal_still_goes(
    engine: Engine,
) -> None:
    client = FakeClient(answer(decision="reject"))

    outcome = asyncio.run(a_layer(engine, client, AiFilter.SHADOW).review(a_context(), T0))

    assert outcome.verdict == "rejected"
    assert outcome.blocks_signal is False  # the signal goes out anyway
    rows = outcome_rows(engine)
    assert len(rows) == 1
    assert rows[0].verdict == "rejected"
    assert rows[0].ai_filter is AiFilter.SHADOW


def test_in_advisory_mode_a_rejection_blocks_but_is_flagged(engine: Engine) -> None:
    outcome = asyncio.run(
        a_layer(engine, FakeClient(answer(decision="reject")), AiFilter.ADVISORY).review(
            a_context(), T0
        )
    )

    assert outcome.blocks_signal is True
    assert outcome.degraded is False
    assert outcome.applied is True


def test_in_required_mode_a_rejection_blocks(engine: Engine) -> None:
    outcome = asyncio.run(
        a_layer(engine, FakeClient(answer(decision="reject")), AiFilter.REQUIRED).review(
            a_context(), T0
        )
    )

    assert outcome.blocks_signal is True
    assert outcome.applied is True


# --- failure behaviour per filter --------------------------------------------


def test_a_model_failure_in_shadow_emits_with_a_local_fallback(engine: Engine) -> None:
    outcome = asyncio.run(
        a_layer(engine, FakeClient(RuntimeError("api down")), AiFilter.SHADOW).review(
            a_context(), T0
        )
    )

    assert outcome.verdict == "unavailable"
    assert outcome.blocks_signal is False
    assert outcome.degraded is True
    assert "indisponible" in outcome.reason
    rows = outcome_rows(engine)
    assert rows[0].error is not None
    assert rows[0].verdict is None


def test_a_model_failure_in_advisory_degrades_the_signal(engine: Engine) -> None:
    outcome = asyncio.run(
        a_layer(engine, FakeClient(RuntimeError("api down")), AiFilter.ADVISORY).review(
            a_context(), T0
        )
    )

    assert outcome.blocks_signal is False
    assert outcome.degraded is True


def test_a_model_failure_in_required_refuses_fail_closed(engine: Engine) -> None:
    outcome = asyncio.run(
        a_layer(engine, FakeClient(RuntimeError("api down")), AiFilter.REQUIRED).review(
            a_context(), T0
        )
    )

    assert outcome.blocks_signal is True
    # Nothing continues: the signal is refused outright, not degraded (C-002).
    assert outcome.degraded is False


def test_a_non_conforming_response_behaves_like_a_failure(engine: Engine) -> None:
    outcome = asyncio.run(a_layer(engine, FakeClient("not json at all")).review(a_context(), T0))

    assert outcome.verdict == "unavailable"
    assert outcome.degraded is True


# --- latency cap and budget ---------------------------------------------------


def test_a_slow_model_does_not_stretch_the_processing_beyond_the_cap(engine: Engine) -> None:
    started = datetime.now(UTC)
    outcome = asyncio.run(
        a_layer(
            engine, FakeClient(answer(), seconds=2.0), timeout=timedelta(milliseconds=50)
        ).review(a_context(), T0)
    )
    elapsed = datetime.now(UTC) - started

    assert outcome.verdict == "unavailable"
    assert elapsed < timedelta(seconds=1)


def test_no_call_is_issued_once_the_budget_is_spent(engine: Engine) -> None:
    client = FakeClient(answer())
    # One call costs about 0.0003 EUR at the default rates; this cap blocks the second.
    layer = a_layer(engine, client, budget_eur=Decimal("0.0001"))
    asyncio.run(layer.review(a_context(), T0))  # the first call fits in the budget
    spent = AiCallStore(engine).total_cost_eur()
    assert spent > 0

    outcome = asyncio.run(layer.review(a_context(), T0 + timedelta(minutes=1)))

    assert client.calls == 1  # the second review never reached the model
    assert outcome.verdict == "unavailable"
    assert outcome.degraded is True


def test_the_cumulated_cost_is_consultable(engine: Engine) -> None:
    layer = a_layer(engine, FakeClient(answer()))
    asyncio.run(layer.review(a_context(), T0))
    asyncio.run(layer.review(a_context(), T0 + timedelta(minutes=1)))

    tokens_cost = (Decimal(100) * layer.input_price + Decimal(50) * layer.output_price) / Decimal(
        1_000_000
    )
    assert AiCallStore(engine).total_cost_eur() == 2 * tokens_cost
