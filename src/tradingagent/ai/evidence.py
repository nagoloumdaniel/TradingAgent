"""The bridge between a measured result and the evidence the researcher reasons about.

`ai/daily.py` looks for proof in `backtest_runs` and `validation_runs`; the AI researcher
refuses to propose anything without it. Nothing used to write those tables, so the researcher
proposed nothing, ever — a whole feature wired to a table that stayed empty. This module is
that missing writer, and the reader its consumer uses.

Two rules shape it, and both are the reason it is not a one-line `insert`:

* **A row is a measurement, so it carries only measured numbers.** No default, no metric
  filled with `0` to mean "unknown": a run with nothing measured is refused rather than
  stored empty, and a number that cannot be measured (a `bool`, a `NaN`, an infinity) is not
  a measurement either.
* **The count travels with the number.** A result obtained by comparing twelve variants is
  not the same evidence as one obtained by comparing two, and the multiple-testing correction
  needs that count. `comparisons` is stored with the measurement, never reconstructed later.

Reading back gives a `MarketEvidence`: the newest run for a market, the parameters of the
version it measured, and the gate verdicts recorded for it. That is exactly what
`DailyLab._evidence` used to look for, and what the improvement cycle compares against.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Engine, insert, select
from sqlalchemy.orm import Session

from tradingagent.ai.researcher import BacktestEvidence, ValidationEvidence
from tradingagent.analytics.model import Performance
from tradingagent.core.improvement import Measurement
from tradingagent.core.states import Severity, ValidationStage
from tradingagent.storage.models import (
    BacktestRunRow,
    StrategyRegistryRow,
    StrategyVersionRow,
    SystemEventRow,
    ValidationRunRow,
)

#: The metric key holding the figure the search maximises. Choosing *which* figure that is
#: stays a business decision (`research.improvement` says so); the bridge only guarantees
#: that whichever one was chosen travels with the measurement instead of being recomputed.
OBJECTIVE_METRIC = "objective"

#: The metric key holding the number of comparisons that produced the measurement.
COMPARISONS_METRIC = "comparisons"


def _require_utc(moment: datetime, name: str) -> datetime:
    if moment.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be UTC")
    return moment


def measured_number(value: object) -> float | None:
    """A measured number, or `None` when this is not one.

    `bool` is refused on purpose: it is an `int` in Python, so `True` would silently become
    the metric `1.0` — a value nobody measured. `NaN` and the infinities are refused for the
    same reason: they are the shapes of a missing measurement, not of a result.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _measured_metrics(metrics: Mapping[str, object]) -> dict[str, float]:
    measured: dict[str, float] = {}
    for key, value in metrics.items():
        number = measured_number(value)
        if number is not None:
            measured[str(key)] = number
    return measured


def performance_metrics(performance: Performance) -> dict[str, float]:
    """The figures a `Performance` really holds, under the names the lab already reads.

    Only the fields that were actually computed are returned: a `None` profit factor means
    the trades did not define one, and writing a zero there would turn "undefined" into
    "measured, and terrible". `max_drawdown` keeps its EUR unit in its key because that is
    what `research.promotion` compares it against.
    """
    measured: dict[str, float] = {
        "net_profit": float(performance.net_profit),
        "max_drawdown_eur": float(performance.max_drawdown),
        "trades": float(performance.trades),
    }
    optional = {
        "profit_factor_net": performance.profit_factor,
        "win_rate": performance.win_rate,
        "expectancy": performance.expectancy,
        "sharpe": performance.sharpe,
        "sortino": performance.sortino,
    }
    for key, value in optional.items():
        number = measured_number(value)
        if number is not None:
            measured[key] = number
    return measured


@dataclass(frozen=True)
class MeasuredRun:
    """One reproducible backtest, exactly as it will be stored and read back."""

    market: str
    ref: str
    dataset_id: str
    fingerprint: str
    window_start: datetime
    window_end: datetime
    objective: float
    metrics: Mapping[str, float]
    costs: Mapping[str, Any] = field(default_factory=dict)
    comparisons: int | None = None
    report_path: str | None = None


@dataclass(frozen=True)
class MarketEvidence:
    """What the lab knows about one market's version in place: its newest measured run."""

    market: str
    ref: str
    dataset_id: str
    fingerprint: str
    window_start: datetime
    window_end: datetime
    parameters: Mapping[str, float]
    metrics: Mapping[str, float]
    costs: Mapping[str, Any]
    objective: float | None
    comparisons: int | None
    validations: tuple[ValidationEvidence, ...] = ()

    def to_backtest_evidence(self) -> BacktestEvidence:
        """The shape `StrategyResearcher.research` consumes."""
        return BacktestEvidence(
            market=self.market,
            ref=self.ref,
            parameters=dict(self.parameters),
            metrics=dict(self.metrics),
            dataset_id=self.dataset_id,
            validations=self.validations,
        )


def record_run(engine: Engine, run: MeasuredRun, *, at: datetime) -> int:
    """Append one measured run to `backtest_runs` and return its id.

    Refuses an empty or non-numeric measurement: the table is read as evidence by the
    researcher, and an empty row would be read as "measured, and nothing to report".
    """
    _require_utc(at, "at")
    _require_utc(run.window_start, "window_start")
    _require_utc(run.window_end, "window_end")
    if run.window_end <= run.window_start:
        raise ValueError(
            f"window_end ({run.window_end.isoformat()}) must come after window_start "
            f"({run.window_start.isoformat()})"
        )
    for name in ("market", "ref", "dataset_id", "fingerprint"):
        if not str(getattr(run, name)).strip():
            raise ValueError(f"a measured run needs a {name}")
    if run.comparisons is not None and run.comparisons < 1:
        raise ValueError(
            "comparisons must be at least 1 when it is given: a search that compared nothing "
            "found nothing, and the multiple-testing correction cannot use a zero"
        )
    objective = measured_number(run.objective)
    if objective is None:
        raise ValueError("objective must be a measured, finite number")
    metrics = _measured_metrics(run.metrics)
    for key, value in run.metrics.items():
        if measured_number(value) is None:
            raise ValueError(f"metric {key!r} was not measured as a number: {value!r}")
    if not metrics:
        raise ValueError("a measured run needs at least one metric that was measured")
    metrics[OBJECTIVE_METRIC] = objective
    if run.comparisons is not None:
        metrics[COMPARISONS_METRIC] = float(run.comparisons)
    with engine.begin() as connection:
        result = connection.execute(
            insert(BacktestRunRow).values(
                ref=run.ref.strip(),
                market=run.market.strip(),
                dataset_id=run.dataset_id.strip(),
                fingerprint=run.fingerprint.strip(),
                window_start=run.window_start,
                window_end=run.window_end,
                metrics=metrics,
                costs=dict(run.costs),
                report_path=run.report_path,
                created_at=at,
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("insert returned no primary key")
        return int(primary_key[0])


def baseline_of(evidence: MarketEvidence) -> Measurement | None:
    """The measurement a recorded run already holds, in the shape a search compares.

    `None` when the run declares no objective: a run that measured something else is not a
    baseline, and reading it as zero would make every variant look like an improvement. The
    two figures the bridge stores *alongside* the objective are dropped — they describe the
    search that produced the number (how many comparisons it cost, which figure was
    maximised), not what the strategy achieved.
    """
    if evidence.objective is None:
        return None
    return Measurement(
        objective=evidence.objective,
        metrics={
            key: value
            for key, value in evidence.metrics.items()
            if key not in (OBJECTIVE_METRIC, COMPARISONS_METRIC)
        },
    )


def record_gate(
    engine: Engine,
    *,
    ref: str,
    market: str,
    stage: ValidationStage,
    passed: bool,
    detail: Mapping[str, Any],
    at: datetime,
) -> int:
    """Append one gate verdict to `validation_runs`.

    A verdict is a decision, so it carries its evidence: `detail` may not be empty. An empty
    detail would let a gate look cleared while nothing supports it, which is the one thing
    the nine gates exist to prevent.
    """
    _require_utc(at, "at")
    stage = ValidationStage(stage)
    if not ref.strip() or not market.strip():
        raise ValueError("a gate verdict needs a ref and a market")
    if not detail:
        raise ValueError(f"a {stage.value} verdict needs its evidence in detail")
    with engine.begin() as connection:
        result = connection.execute(
            insert(ValidationRunRow).values(
                ref=ref.strip(),
                market=market.strip(),
                stage=stage,
                passed=bool(passed),
                detail=dict(detail),
                created_at=at,
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("insert returned no primary key")
        return int(primary_key[0])


def parameters_of(engine: Engine, ref: str) -> dict[str, float]:
    """The numeric parameters of a reference, from its manifest snapshot or its registry row.

    Two sources, newest knowledge first, and no third one: the registry row is what the
    operator declared, the `strategy_versions` snapshot is the manifest the strategy really
    ran with. Only numbers are returned — a non-numeric entry would reach
    `StrategyResearcher`, which formats parameters as figures, and a string there is a crash
    dressed as a hypothesis.
    """
    with Session(engine) as session:
        snapshot = session.scalars(
            select(StrategyVersionRow)
            .where(StrategyVersionRow.ref == ref)
            .order_by(StrategyVersionRow.id.desc())
            .limit(1)
        ).first()
        if snapshot is not None:
            declared = snapshot.manifest.get("parameters")
            if isinstance(declared, Mapping):
                measured = _measured_metrics(declared)
                if measured:
                    return measured
        registered = session.scalars(
            select(StrategyRegistryRow)
            .where(StrategyRegistryRow.ref == ref)
            .order_by(StrategyRegistryRow.id.desc())
            .limit(1)
        ).first()
        if registered is not None and isinstance(registered.parameters, Mapping):
            return _measured_metrics(registered.parameters)
    return {}


def evidence_for(engine: Engine, market: str) -> MarketEvidence | None:
    """The newest measured run for a market, with its parameters and its gate verdicts.

    `None` when nothing was ever measured. That is the case the improvement cycle must report
    as "no proof yet" instead of inventing a baseline: an absent measurement and a zero
    measurement are not the same statement.
    """
    with Session(engine) as session:
        run = session.scalars(
            select(BacktestRunRow)
            .where(BacktestRunRow.market == market.strip())
            .order_by(BacktestRunRow.id.desc())
            .limit(1)
        ).first()
        if run is None:
            return None
        validations = session.scalars(
            select(ValidationRunRow)
            .where(ValidationRunRow.ref == run.ref, ValidationRunRow.market == market.strip())
            .order_by(ValidationRunRow.id)
        ).all()
    metrics = _measured_metrics(run.metrics)
    comparisons = metrics.get(COMPARISONS_METRIC)
    return MarketEvidence(
        market=str(run.market),
        ref=str(run.ref),
        dataset_id=str(run.dataset_id),
        fingerprint=str(run.fingerprint),
        window_start=run.window_start,
        window_end=run.window_end,
        parameters=parameters_of(engine, str(run.ref)),
        metrics=metrics,
        costs=dict(run.costs),
        objective=metrics.get(OBJECTIVE_METRIC),
        comparisons=None if comparisons is None else int(comparisons),
        validations=tuple(
            ValidationEvidence(
                stage=str(row.stage),
                passed=bool(row.passed),
                detail=dict(row.detail),
            )
            for row in validations
        ),
    )


def journal(
    engine: Engine,
    *,
    kind: str,
    detail: Mapping[str, Any],
    at: datetime,
    severity: Severity = Severity.INFO,
    symbol: str | None = None,
) -> int:
    """Append one machine-readable entry to the operator journal (`system_events`).

    The improvement cycle uses it for what `backtest_runs` cannot hold: the attempts that
    were refused, the variants that could not be measured, and the comparison count of a
    search that improved nothing. A refusal nobody wrote down is a refusal nobody can audit.
    """
    _require_utc(at, "at")
    with engine.begin() as connection:
        result = connection.execute(
            insert(SystemEventRow).values(
                kind=kind,
                severity=severity,
                detail=dict(detail),
                occurred_at=at,
                symbol=symbol,
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("insert returned no primary key")
        return int(primary_key[0])


__all__ = [
    "COMPARISONS_METRIC",
    "OBJECTIVE_METRIC",
    "MarketEvidence",
    "MeasuredRun",
    "baseline_of",
    "evidence_for",
    "journal",
    "measured_number",
    "parameters_of",
    "performance_metrics",
    "record_gate",
    "record_run",
]
