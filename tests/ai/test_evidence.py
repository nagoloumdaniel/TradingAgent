"""The bridge between a measured result and the evidence the researcher reads.

`ai/daily.py` looks for proof in `backtest_runs`; until this module existed, nothing ever
wrote there, so the AI researcher proposed nothing, ever. The tests below pin the two ends
of that bridge: what is written when a campaign measures something, and what is read back.

The strictness is the point. A row in `backtest_runs` is a *measurement*, so it may not
carry a default, a zero standing for "unknown", or a metric that was never measured. A
reader that finds nothing must find nothing — not an empty row dressed as evidence.
"""

import math
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, insert, select
from sqlalchemy.orm import Session

from tradingagent.ai.evidence import (
    MeasuredRun,
    evidence_for,
    parameters_of,
    record_gate,
    record_run,
)
from tradingagent.core.states import ValidationStage
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import (
    BacktestRunRow,
    OrderRow,
    PositionRow,
    SignalRow,
    StrategyVersionRow,
    TradeRow,
    ValidationRunRow,
)

T0 = datetime(2026, 10, 7, 6, 0, tzinfo=UTC)
MARKET = "frxXAUUSD"
REF = "witness@1.1.0"


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'evidence.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def a_run(**overrides: object) -> MeasuredRun:
    values: dict[str, object] = {
        "market": MARKET,
        "ref": REF,
        "dataset_id": "xau-m15-2026-01",
        "fingerprint": "a" * 64,
        "window_start": T0 - timedelta(days=30),
        "window_end": T0,
        "objective": 1250.0,
        "metrics": {"max_drawdown_eur": 90.0, "trades": 120.0},
        "costs": {"spread": 0.3, "commission": 0.5},
    }
    values.update(overrides)
    return MeasuredRun(**values)  # type: ignore[arg-type]


def snapshot(engine: Engine, ref: str, parameters: dict[str, object]) -> None:
    """The manifest snapshot `strategy_versions` keeps the first time a ref is used."""
    with engine.begin() as connection:
        connection.execute(
            insert(StrategyVersionRow).values(
                ref=ref,
                strategy_id=ref.partition("@")[0],
                version=ref.partition("@")[2],
                manifest={
                    "strategy_id": ref.partition("@")[0],
                    "version": ref.partition("@")[2],
                    "parameters": parameters,
                },
                content_hash="b" * 64,
                first_seen_at=T0 - timedelta(days=60),
            )
        )


def rows(engine: Engine) -> list[BacktestRunRow]:
    with Session(engine) as session:
        return list(session.scalars(select(BacktestRunRow).order_by(BacktestRunRow.id)).all())


def trading_counts(engine: Engine) -> tuple[int, int, int, int]:
    with Session(engine) as session:
        return (
            int(session.scalar(select(func.count()).select_from(SignalRow)) or 0),
            int(session.scalar(select(func.count()).select_from(OrderRow)) or 0),
            int(session.scalar(select(func.count()).select_from(PositionRow)) or 0),
            int(session.scalar(select(func.count()).select_from(TradeRow)) or 0),
        )


# --- writing a measurement ---------------------------------------------------------------


def test_a_recorded_run_is_read_back_as_evidence(engine: Engine) -> None:
    record_run(engine, a_run(comparisons=7), at=T0)

    evidence = evidence_for(engine, MARKET)

    assert evidence is not None
    assert evidence.ref == REF
    assert evidence.market == MARKET
    assert evidence.dataset_id == "xau-m15-2026-01"
    assert evidence.objective == 1250.0
    assert evidence.metrics["max_drawdown_eur"] == 90.0
    assert evidence.comparisons == 7


def test_the_objective_and_the_comparison_count_travel_with_the_measurement(engine: Engine) -> None:
    """The correction of the multiple test needs the count; nobody can rebuild it later."""
    record_run(engine, a_run(comparisons=4), at=T0)

    stored = rows(engine)[0].metrics

    assert stored["objective"] == 1250.0
    assert stored["comparisons"] == 4


def test_nothing_is_invented_when_the_count_is_unknown(engine: Engine) -> None:
    record_run(engine, a_run(), at=T0)

    assert "comparisons" not in rows(engine)[0].metrics
    assert evidence_for(engine, MARKET).comparisons is None  # type: ignore[union-attr]


def test_the_latest_run_is_what_the_reader_finds(engine: Engine) -> None:
    """`_evidence()` has always read the newest row: the bridge must honour that."""
    record_run(engine, a_run(ref="witness@1.1.0", objective=10.0), at=T0 - timedelta(days=2))
    record_run(
        engine,
        a_run(ref="witness@1.1.1", objective=99.0, comparisons=3),
        at=T0,
    )

    evidence = evidence_for(engine, MARKET)

    assert evidence is not None
    assert evidence.ref == "witness@1.1.1"
    assert evidence.objective == 99.0


def test_another_market_is_not_evidence(engine: Engine) -> None:
    record_run(engine, a_run(market="XAUUSD"), at=T0)

    assert evidence_for(engine, "cryBTCUSD") is None
    assert evidence_for(engine, "XAUUSD") is not None


def test_the_bridge_touches_no_trading_table(engine: Engine) -> None:
    """§39: a measurement is a note. It can never move a signal, an order or a position."""
    before = trading_counts(engine)

    record_run(engine, a_run(comparisons=2), at=T0)
    record_gate(
        engine,
        ref=REF,
        market=MARKET,
        stage=ValidationStage.PARAMETER_ROBUSTNESS,
        passed=False,
        detail={"most_sensitive": "atr_stop_multiple"},
        at=T0,
    )

    assert trading_counts(engine) == before


# --- what may never be written ------------------------------------------------------------


def test_a_measurement_without_a_single_number_is_refused(engine: Engine) -> None:
    with pytest.raises(ValueError, match="metric"):
        record_run(engine, a_run(metrics={}), at=T0)
    assert rows(engine) == []


def test_a_boolean_is_not_a_metric(engine: Engine) -> None:
    with pytest.raises(ValueError, match="trades"):
        record_run(engine, a_run(metrics={"trades": True}), at=T0)


def test_a_nan_is_not_a_measurement(engine: Engine) -> None:
    with pytest.raises(ValueError, match="sharpe"):
        record_run(engine, a_run(metrics={"sharpe": math.nan}), at=T0)


def test_a_missing_identity_is_refused(engine: Engine) -> None:
    with pytest.raises(ValueError, match="ref"):
        record_run(engine, a_run(ref="  "), at=T0)
    with pytest.raises(ValueError, match="dataset_id"):
        record_run(engine, a_run(dataset_id=""), at=T0)
    with pytest.raises(ValueError, match="fingerprint"):
        record_run(engine, a_run(fingerprint=""), at=T0)


def test_a_naive_datetime_is_refused(engine: Engine) -> None:
    with pytest.raises(ValueError, match="UTC"):
        record_run(engine, a_run(), at=T0.replace(tzinfo=None))
    with pytest.raises(ValueError, match="UTC"):
        record_run(engine, a_run(window_start=T0.replace(tzinfo=None)), at=T0)


def test_a_window_that_ends_before_it_starts_is_refused(engine: Engine) -> None:
    with pytest.raises(ValueError, match="window"):
        record_run(engine, a_run(window_end=T0 - timedelta(days=60)), at=T0)


def test_a_comparison_count_below_one_is_refused(engine: Engine) -> None:
    """A search that compared nothing found nothing; the count is not a decoration."""
    with pytest.raises(ValueError, match="comparisons"):
        record_run(engine, a_run(comparisons=0), at=T0)


# --- the parameters of the version in place -----------------------------------------------


def test_the_parameters_come_from_the_manifest_of_the_reference(engine: Engine) -> None:
    """Without them the researcher cannot name what to change, and proposes nothing."""
    snapshot(engine, REF, {"ema_fast": 20, "take_profit_rr": 2.0, "note": "texte"})
    record_run(engine, a_run(), at=T0)

    evidence = evidence_for(engine, MARKET)

    assert evidence is not None
    assert evidence.parameters == {"ema_fast": 20.0, "take_profit_rr": 2.0}


def test_parameters_are_not_invented_without_a_manifest(engine: Engine) -> None:
    record_run(engine, a_run(), at=T0)

    assert parameters_of(engine, REF) == {}
    assert evidence_for(engine, MARKET).parameters == {}  # type: ignore[union-attr]


# --- the gates that were cleared ------------------------------------------------------------


def test_a_gate_verdict_is_recorded_and_read_back(engine: Engine) -> None:
    record_run(engine, a_run(), at=T0)
    record_gate(
        engine,
        ref=REF,
        market=MARKET,
        stage=ValidationStage.PARAMETER_ROBUSTNESS,
        passed=False,
        detail={"most_sensitive": "atr_stop_multiple"},
        at=T0,
    )

    evidence = evidence_for(engine, MARKET)

    assert evidence is not None
    assert len(evidence.validations) == 1
    assert evidence.validations[0].stage == ValidationStage.PARAMETER_ROBUSTNESS.value
    assert evidence.validations[0].passed is False
    assert evidence.validations[0].detail == {"most_sensitive": "atr_stop_multiple"}


def test_a_gate_verdict_without_evidence_is_refused(engine: Engine) -> None:
    with pytest.raises(ValueError, match="detail"):
        record_gate(
            engine,
            ref=REF,
            market=MARKET,
            stage=ValidationStage.WALK_FORWARD,
            passed=True,
            detail={},
            at=T0,
        )
    with Session(engine) as session:
        assert session.scalars(select(ValidationRunRow)).all() == []
