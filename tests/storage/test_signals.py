import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select

from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import Severity, SignalState
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.models import (
    SignalEventRow,
    SignalRow,
    StrategyVersionRow,
    SystemEventRow,
)
from tradingagent.storage.signals import (
    ManifestChangedError,
    SignalRecord,
    SignalRepository,
    idempotency_key,
)
from tradingagent.strategies.manifest import StrategyManifest

CLOSE = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
MANIFEST = StrategyManifest(
    strategy_id="witness",
    version="1.0.0",
    max_mode=TradingMode.SIGNAL,
    allowed_symbols=("XAUUSD",),
    timeframes=(Timeframe.M15,),
    history_bars=20,
)


def record(**changes: object) -> SignalRecord:
    base = SignalRecord(
        idempotency_key=idempotency_key(MANIFEST.ref, "XAUUSD", Timeframe.M15, CLOSE),
        manifest=MANIFEST,
        symbol="XAUUSD",
        timeframe=Timeframe.M15,
        direction=Direction.BUY,
        mode=TradingMode.SIGNAL,
        observed_price=2400.5,
        entry_low=2400.0,
        entry_high=2401.0,
        stop_loss=2390.0,
        take_profits=(2410.0, 2420.0),
        reason="close above the moving average",
        indicators={"sma_20": 2395.25, "rsi_14": 61.5},
        generated_at=CLOSE,
        expires_at=CLOSE + timedelta(minutes=15),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def count(engine: Engine, table: type) -> int:
    with engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(table)).scalar_one()


def test_key_is_deterministic_and_readable() -> None:
    key = idempotency_key("witness@1.0.0", "XAUUSD", Timeframe.M15, CLOSE)
    assert key == "witness@1.0.0:XAUUSD:M15:2026-10-06T12:00Z"
    assert key == idempotency_key("witness@1.0.0", "XAUUSD", Timeframe.M15, CLOSE)


def test_key_refuses_a_non_utc_time() -> None:
    with pytest.raises(ValueError, match="UTC"):
        idempotency_key("witness@1.0.0", "XAUUSD", Timeframe.M15, CLOSE.replace(tzinfo=None))


def test_signal_is_stored_with_indicators_version_and_first_event(engine: Engine) -> None:
    signal_id = SignalRepository(engine).record(record())
    assert signal_id is not None
    with engine.connect() as connection:
        signal = connection.execute(select(SignalRow)).one()
        event = connection.execute(select(SignalEventRow)).one()
        version = connection.execute(select(StrategyVersionRow)).one()
    assert signal.indicators == {"sma_20": 2395.25, "rsi_14": 61.5}
    assert signal.take_profits == [2410.0, 2420.0]
    assert signal.state is SignalState.CANDIDATE
    assert (event.signal_id, event.state, event.occurred_at) == (
        signal_id,
        SignalState.CANDIDATE,
        CLOSE,
    )
    assert version.ref == "witness@1.0.0"
    assert version.manifest["history_bars"] == 20
    assert signal.strategy_version_id == version.id


def test_the_same_candle_recorded_twice_gives_one_signal(engine: Engine) -> None:
    repository = SignalRepository(engine)
    assert repository.record(record()) is not None
    assert repository.record(record()) is None
    assert count(engine, SignalRow) == 1
    assert count(engine, SignalEventRow) == 1


def test_a_restarted_agent_cannot_duplicate_a_signal(engine: Engine) -> None:
    SignalRepository(engine).record(record())
    assert SignalRepository(engine).record(record(direction=Direction.SELL)) is None
    assert count(engine, SignalRow) == 1


def test_concurrent_recording_of_the_same_candle_gives_one_signal(engine: Engine) -> None:
    repository = SignalRepository(engine)
    results: list[int | None] = []
    start = threading.Barrier(8)

    def worker() -> None:
        start.wait()
        results.append(repository.record(record()))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len([r for r in results if r is not None]) == 1
    assert count(engine, SignalRow) == 1
    assert count(engine, SignalEventRow) == 1


def test_a_crash_between_signal_and_event_leaves_nothing_behind(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = SignalRepository(engine)

    def crash(*_: object) -> None:
        raise RuntimeError("power cut")

    monkeypatch.setattr(repository, "_insert_first_event", crash)
    with pytest.raises(RuntimeError):
        repository.record(record())
    assert count(engine, SignalRow) == 0
    monkeypatch.undo()
    assert repository.record(record()) is not None
    assert count(engine, SignalEventRow) == 1


def test_the_strategy_version_is_stored_once(engine: Engine) -> None:
    repository = SignalRepository(engine)
    later = CLOSE + timedelta(minutes=15)
    repository.record(record())
    repository.record(
        record(
            idempotency_key=idempotency_key(MANIFEST.ref, "XAUUSD", Timeframe.M15, later),
            generated_at=later,
        )
    )
    assert count(engine, StrategyVersionRow) == 1
    assert count(engine, SignalRow) == 2


def test_a_manifest_edited_without_a_version_bump_is_refused(engine: Engine) -> None:
    repository = SignalRepository(engine)
    repository.record(record())
    edited = MANIFEST.model_copy(update={"history_bars": 50})
    later = CLOSE + timedelta(minutes=15)
    with pytest.raises(ManifestChangedError, match=r"witness@1\.0\.0"):
        repository.record(
            record(
                manifest=edited,
                idempotency_key=idempotency_key(MANIFEST.ref, "XAUUSD", Timeframe.M15, later),
            )
        )
    assert count(engine, SignalRow) == 1


def test_system_events_keep_the_reason(engine: Engine) -> None:
    SignalRepository(engine).record_system_event(
        "evaluation_skipped", Severity.INFO, {"symbol": "XAUUSD", "status": "market_closed"}, CLOSE
    )
    with engine.connect() as connection:
        event = connection.execute(select(SystemEventRow)).one()
    assert (event.kind, event.severity, event.detail["status"]) == (
        "evaluation_skipped",
        Severity.INFO,
        "market_closed",
    )
