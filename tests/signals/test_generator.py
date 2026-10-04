from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, select

from tradingagent.config.strategy_catalog import LoadedStrategy
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.states import Severity
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar, learn_calendar
from tradingagent.signals.generator import GenerationStatus, SignalGenerator
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import SignalRow, SystemEventRow
from tradingagent.storage.signals import SignalRepository
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.manifest import StrategyManifest

STEP = timedelta(minutes=15)
LAST_OPEN = datetime(2026, 10, 6, 11, 45, tzinfo=UTC)  # a Tuesday
CLOSE = LAST_OPEN + STEP
NOW = CLOSE + timedelta(seconds=20)
TICK = NOW - timedelta(seconds=3)


def always_open_calendar() -> MarketCalendar:
    quarters = [
        Candle(Timeframe.M15, CLOSE - STEP * (i + 1), 1.0, 1.0, 1.0, 1.0) for i in range(8 * 7 * 96)
    ]
    return learn_calendar("ANY", quarters, NOW)


ALWAYS_OPEN = always_open_calendar()
CLOSED_ON_TUESDAY_NOON = MarketCalendar(
    "XAUUSD",
    ALWAYS_OPEN.open_slots - {(1, 48)},
    frozenset(),
)


class NoParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Buyer(Strategy[NoParameters]):
    strategy_id: ClassVar[str] = "buyer"
    parameters_model: ClassVar[type[BaseModel]] = NoParameters
    calls = 0

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        type(self).calls += 1
        close = context.closes(context.primary_timeframe)[-1]
        return SignalCandidate(
            direction=Direction.BUY,
            entry_low=close - 1,
            entry_high=close,
            stop_loss=close - 10,
            take_profits=(close + 10,),
            reason="test buy",
            indicators={"last_close": close},
        )


class Silent(Buyer):
    strategy_id: ClassVar[str] = "silent"

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        return None


class Flaky(Buyer):
    """Crashes while `failing` is set; counts its calls."""

    strategy_id: ClassVar[str] = "flaky"
    failing = True

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        type(self).calls += 1
        if type(self).failing:
            raise ZeroDivisionError("division by zero")
        return super().evaluate(context)


def loaded(
    strategy_class: type[Buyer],
    symbols: tuple[str, ...] = ("XAUUSD", "BTCUSD"),
    max_mode: TradingMode = TradingMode.SIGNAL,
    history_bars: int = 20,
) -> LoadedStrategy:
    manifest = StrategyManifest(
        strategy_id=strategy_class.strategy_id,
        version="1.0.0",
        max_mode=max_mode,
        allowed_symbols=symbols,
        timeframes=(Timeframe.M15,),
        history_bars=history_bars,
        expiry_bars=2,
    )
    return LoadedStrategy(manifest, strategy_class(NoParameters()))


def candles(last_open: datetime, count: int, skip: datetime | None = None) -> list[Candle]:
    times = [last_open - STEP * (count - 1 - i) for i in range(count)]
    return [
        Candle(Timeframe.M15, at, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i)
        for i, at in enumerate(times)
        if at != skip
    ]


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'signals.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


@pytest.fixture(autouse=True)
def reset_counters() -> None:
    Buyer.calls = 0
    Flaky.calls = 0
    Flaky.failing = True


def generator(
    engine: Engine, *strategies: LoadedStrategy, mode: TradingMode = TradingMode.SIGNAL
) -> SignalGenerator:
    return SignalGenerator(
        strategies, CandleStore(engine), SignalRepository(engine), agent_mode=mode
    )


def stored(engine: Engine, symbol: str = "XAUUSD", count: int = 40) -> Candle:
    series = candles(LAST_OPEN, count)
    CandleStore(engine).save(symbol, series, NOW)
    return series[-1]


def close(
    gen: SignalGenerator,
    trigger: Candle,
    symbol: str = "XAUUSD",
    calendar: MarketCalendar = ALWAYS_OPEN,
    now: datetime = NOW,
) -> dict[str, GenerationStatus]:
    results = gen.on_candle_closed(symbol, trigger, calendar, now, now - timedelta(seconds=3))
    return {result.ref: result.status for result in results}


def signals(engine: Engine) -> list[SignalRow]:
    with engine.connect() as connection:
        return list(connection.execute(select(SignalRow)).all())  # type: ignore[arg-type]


def events(engine: Engine) -> list[tuple[str, Severity]]:
    with engine.connect() as connection:
        rows = connection.execute(select(SystemEventRow.kind, SystemEventRow.severity)).all()
    return [(kind, severity) for kind, severity in rows]


def test_a_healthy_close_produces_a_recorded_signal(engine: Engine) -> None:
    trigger = stored(engine)
    gen = generator(engine, loaded(Buyer))
    results = gen.on_candle_closed("XAUUSD", trigger, ALWAYS_OPEN, NOW, TICK)
    assert [r.status for r in results] == [GenerationStatus.RECORDED]
    assert results[0].signal_id is not None
    [signal] = signals(engine)
    assert signal.idempotency_key == "buyer@1.0.0:XAUUSD:M15:2026-10-06T12:00Z"
    assert signal.indicators == {"last_close": trigger.close}
    assert signal.observed_price == trigger.close
    assert signal.generated_at == CLOSE
    assert signal.expires_at == CLOSE + 2 * STEP
    assert signal.mode is TradingMode.SIGNAL


def test_the_same_close_evaluated_twice_records_one_signal(engine: Engine) -> None:
    trigger = stored(engine)
    gen = generator(engine, loaded(Buyer))
    close(gen, trigger)
    assert close(gen, trigger) == {"buyer@1.0.0": GenerationStatus.DUPLICATE}
    assert len(signals(engine)) == 1


def test_a_restart_mid_candle_cannot_duplicate_the_signal(engine: Engine) -> None:
    trigger = stored(engine)
    close(generator(engine, loaded(Buyer)), trigger)
    restarted = generator(engine, loaded(Buyer))
    assert close(restarted, trigger) == {"buyer@1.0.0": GenerationStatus.DUPLICATE}
    assert len(signals(engine)) == 1


@pytest.mark.parametrize(
    ("agent_mode", "max_mode", "expected"),
    [
        (TradingMode.DEMO, TradingMode.SIGNAL, TradingMode.SIGNAL),
        (TradingMode.OBSERVATION, TradingMode.SIGNAL, TradingMode.OBSERVATION),
        (TradingMode.PAPER, TradingMode.LIVE, TradingMode.PAPER),
    ],
)
def test_signal_mode_never_exceeds_the_strategy_promotion(
    engine: Engine, agent_mode: TradingMode, max_mode: TradingMode, expected: TradingMode
) -> None:
    trigger = stored(engine)
    close(generator(engine, loaded(Buyer, max_mode=max_mode), mode=agent_mode), trigger)
    assert signals(engine)[0].mode is expected


def test_a_closed_market_skips_evaluation_and_keeps_the_reason(engine: Engine) -> None:
    trigger = stored(engine)
    gen = generator(engine, loaded(Buyer))
    status = close(gen, trigger, calendar=CLOSED_ON_TUESDAY_NOON, now=NOW)
    assert status == {"buyer@1.0.0": GenerationStatus.SKIPPED}
    assert Buyer.calls == 0
    assert signals(engine) == []
    assert events(engine) == [("evaluation_skipped", Severity.INFO)]


def test_a_gap_in_the_series_skips_evaluation_with_a_warning(engine: Engine) -> None:
    series = candles(LAST_OPEN, 40, skip=LAST_OPEN - STEP * 5)
    CandleStore(engine).save("XAUUSD", series, NOW)
    status = close(generator(engine, loaded(Buyer)), series[-1])
    assert status == {"buyer@1.0.0": GenerationStatus.SKIPPED}
    assert Buyer.calls == 0
    assert events(engine) == [("evaluation_skipped", Severity.WARNING)]


def test_an_old_candle_replayed_after_a_restart_gives_no_signal(engine: Engine) -> None:
    trigger = stored(engine)
    an_hour_later = NOW + timedelta(hours=1)
    assert close(generator(engine, loaded(Buyer)), trigger, now=an_hour_later) == {
        "buyer@1.0.0": GenerationStatus.SKIPPED
    }
    assert signals(engine) == []


def test_a_window_never_sees_bars_after_its_trigger(engine: Engine) -> None:
    series = candles(LAST_OPEN + STEP * 3, 43)
    CandleStore(engine).save("XAUUSD", series, NOW)
    trigger = series[-4]
    later = trigger.close_time + timedelta(seconds=20)
    close(generator(engine, loaded(Buyer)), trigger, now=later)
    assert signals(engine)[0].observed_price == trigger.close


def test_short_history_is_reported_not_evaluated(engine: Engine) -> None:
    trigger = stored(engine, count=5)
    assert close(generator(engine, loaded(Buyer)), trigger) == {
        "buyer@1.0.0": GenerationStatus.INSUFFICIENT_HISTORY
    }
    assert Buyer.calls == 0


def test_only_strategies_for_this_market_and_timeframe_run(engine: Engine) -> None:
    trigger = stored(engine)
    gold_only = loaded(Silent, symbols=("XAUUSD",))
    assert close(generator(engine, gold_only), trigger, symbol="BTCUSD") == {}
    hourly = Candle(Timeframe.H1, LAST_OPEN - timedelta(minutes=45), 1.0, 1.0, 1.0, 1.0)
    assert close(generator(engine, gold_only), hourly) == {}


def test_no_signal_is_not_an_error(engine: Engine) -> None:
    trigger = stored(engine)
    assert close(generator(engine, loaded(Silent)), trigger) == {
        "silent@1.0.0": GenerationStatus.NO_SIGNAL
    }
    assert events(engine) == []


def test_a_crashing_strategy_does_not_stop_the_others(engine: Engine) -> None:
    gold, bitcoin = stored(engine, "XAUUSD"), stored(engine, "BTCUSD")
    gen = generator(engine, loaded(Flaky), loaded(Buyer))
    assert close(gen, gold) == {
        "flaky@1.0.0": GenerationStatus.STRATEGY_ERROR,
        "buyer@1.0.0": GenerationStatus.RECORDED,
    }
    assert close(gen, bitcoin, symbol="BTCUSD")["buyer@1.0.0"] is GenerationStatus.RECORDED
    assert len(signals(engine)) == 2
    assert ("strategy_error", Severity.WARNING) in events(engine)


def replay(gen: SignalGenerator, engine: Engine, times: int) -> list[GenerationStatus]:
    """Evaluate `times` consecutive closes of gold, one per quarter."""
    statuses = []
    series = candles(LAST_OPEN + STEP * (times - 1), 40 + times)
    CandleStore(engine).save("XAUUSD", series, NOW)
    for trigger in series[-times:]:
        now = trigger.close_time + timedelta(seconds=20)
        statuses.append(close(gen, trigger, now=now)["flaky@1.0.0"])
    return statuses


def test_three_consecutive_crashes_quarantine_the_strategy_on_that_market(
    engine: Engine,
) -> None:
    gen = generator(engine, loaded(Flaky))
    assert (
        replay(gen, engine, 5)
        == [GenerationStatus.STRATEGY_ERROR] * 3 + [GenerationStatus.QUARANTINED] * 2
    )
    assert Flaky.calls == 3
    assert gen.quarantined == {("flaky@1.0.0", "XAUUSD")}
    assert events(engine).count(("strategy_quarantined", Severity.CRITICAL)) == 1


def test_rearming_lifts_the_quarantine(engine: Engine) -> None:
    gen = generator(engine, loaded(Flaky))
    replay(gen, engine, 3)
    Flaky.failing = False
    gen.rearm("flaky@1.0.0", "XAUUSD")
    assert gen.quarantined == set()
    assert close(gen, stored(engine)) == {"flaky@1.0.0": GenerationStatus.RECORDED}


def test_a_success_resets_the_crash_count(engine: Engine) -> None:
    gen = generator(engine, loaded(Flaky))
    series = candles(LAST_OPEN + STEP * 3, 44)
    CandleStore(engine).save("XAUUSD", series, NOW)
    statuses = []
    for index, trigger in enumerate(series[-4:]):
        Flaky.failing = index != 2
        statuses.append(close(gen, trigger, now=trigger.close_time + timedelta(seconds=20)))
    assert [s["flaky@1.0.0"] for s in statuses] == [
        GenerationStatus.STRATEGY_ERROR,
        GenerationStatus.STRATEGY_ERROR,
        GenerationStatus.RECORDED,
        GenerationStatus.STRATEGY_ERROR,
    ]
    assert gen.quarantined == set()
