"""Backtest-versus-production comparison (TASK-093, F-026, EF-027, R-12, R-14).

Two rules shape this module.

*Every figure comes from the database.* Production trades are read from `trades` through
`ReportData`; the reference block is the one persisted in `strategy_versions.manifest`
under `parameters.backtest`, written once when the strategy version is first seen. The
comparison never imports the backtest package: production reports must not load it.

*Demo and live are never aggregated (R-14).* Each strategy is compared mode by mode, and a
strategy with too few production trades is shown as inconclusive instead of producing a
false alert. Only a gap past its configured threshold raises one.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.analytics import Axis, Performance, Trade, compute_performance, group
from tradingagent.core.mode import TradingMode
from tradingagent.storage.account import ReportData
from tradingagent.storage.models import StrategyVersionRow

BACKTEST_REFERENCE_KEY = "backtest"
DEFAULT_MIN_PRODUCTION_TRADES = 5

# An alert threshold can be widened or tightened without a code change.
ENV_PREFIX = "TA_COMPARISON_"


@dataclass(frozen=True)
class ComparisonThresholds:
    """How far production may drift from the reference before the operator is told."""

    net_profit_gap_pct: Decimal = Decimal("25")
    win_rate_gap_points: Decimal = Decimal("15")
    profit_factor_gap: Decimal = Decimal("0.5")
    max_drawdown_gap_pct: Decimal = Decimal("25")
    realized_rr_gap: Decimal = Decimal("0.5")
    min_production_trades: int = DEFAULT_MIN_PRODUCTION_TRADES

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ComparisonThresholds":
        source = os.environ if environ is None else environ

        def decimal(name: str, default: Decimal) -> Decimal:
            raw = source.get(f"{ENV_PREFIX}{name}")
            if raw is None or not raw.strip():
                return default
            try:
                return Decimal(raw.strip())
            except InvalidOperation as error:
                raise ValueError(f"{ENV_PREFIX}{name} is not a number: {raw!r}") from error

        def integer(name: str, default: int) -> int:
            raw = source.get(f"{ENV_PREFIX}{name}")
            if raw is None or not raw.strip():
                return default
            try:
                return int(raw.strip())
            except ValueError as error:
                raise ValueError(f"{ENV_PREFIX}{name} is not an integer: {raw!r}") from error

        minimum = integer("MIN_PRODUCTION_TRADES", cls.min_production_trades)
        if minimum < 1:
            raise ValueError(f"{ENV_PREFIX}MIN_PRODUCTION_TRADES must be at least 1")
        return cls(
            net_profit_gap_pct=decimal("NET_PROFIT_GAP_PCT", cls.net_profit_gap_pct),
            win_rate_gap_points=decimal("WIN_RATE_GAP_POINTS", cls.win_rate_gap_points),
            profit_factor_gap=decimal("PROFIT_FACTOR_GAP", cls.profit_factor_gap),
            max_drawdown_gap_pct=decimal("MAX_DRAWDOWN_GAP_PCT", cls.max_drawdown_gap_pct),
            realized_rr_gap=decimal("REALIZED_RR_GAP", cls.realized_rr_gap),
            min_production_trades=minimum,
        )


@dataclass(frozen=True)
class MetricGap:
    """One indicator, its reference, its production value, and the distance between them."""

    name: str
    label: str
    reference: float
    production: float
    gap: float
    threshold: float
    unit: str
    breached: bool


@dataclass(frozen=True)
class StrategyComparison:
    strategy_ref: str
    mode: TradingMode | None
    reference_trades: int | None
    production: Performance
    metrics: tuple[MetricGap, ...]
    sufficient_sample: bool
    missing_reference: bool
    min_production_trades: int

    @property
    def production_trades(self) -> int:
        return self.production.trades

    @property
    def breached(self) -> bool:
        if self.missing_reference or not self.sufficient_sample:
            return False
        return any(metric.breached for metric in self.metrics)

    def alerts(self) -> tuple[str, ...]:
        label = f"{self.strategy_ref} [{self.mode.value if self.mode else 'aucune opération'}]"
        if self.missing_reference:
            return (f"{label}: aucune référence de backtest enregistrée (F-026)",)
        if not self.sufficient_sample:
            return (
                f"{label}: échantillon de production insuffisant "
                f"({self.production_trades} < {self.min_production_trades}), "
                "aucune conclusion (R-14)",
            )
        return tuple(
            f"{label}: {metric.label} écart {metric.gap:.1f} ({metric.unit}) au-delà du seuil "
            f"{metric.threshold:g} (R-12)"
            for metric in self.metrics
            if metric.breached
        )


@dataclass(frozen=True)
class ComparisonReport:
    comparisons: tuple[StrategyComparison, ...]
    alerts: tuple[str, ...]

    def render(self) -> list[str]:
        lines = [
            "",
            "Comparaison backtest / production (F-026, EF-027) :",
            "  Démonstration et réel sont présentés séparément et jamais agrégés (R-14).",
        ]
        if not self.comparisons:
            lines.append("  aucune donnée")
            return lines
        for comparison in self.comparisons:
            mode = comparison.mode.value if comparison.mode else "aucune opération"
            lines.append(
                f"  {comparison.strategy_ref} [{mode}] : "
                f"{comparison.production_trades} opération(s)"
            )
            if comparison.missing_reference:
                lines.append("    aucune référence de backtest enregistrée")
            elif not comparison.sufficient_sample:
                lines.append(
                    f"    échantillon insuffisant ({comparison.production_trades} < "
                    f"{comparison.min_production_trades}) : aucune conclusion (R-14)"
                )
            else:
                for metric in comparison.metrics:
                    flag = " ALERTE" if metric.breached else ""
                    lines.append(
                        f"    {metric.label} : backtest {metric.reference:.2f} → production "
                        f"{metric.production:.2f} (écart {metric.gap:.1f} {metric.unit}, "
                        f"seuil {metric.threshold:g}){flag}"
                    )
        if self.alerts:
            lines.append("")
            lines.append("  Alertes de dérive (R-12) :")
            lines.extend(f"    {alert}" for alert in self.alerts)
        return lines


# name in the reference block == name of the analytics indicator.
@dataclass(frozen=True)
class _MetricSpec:
    name: str
    label: str
    unit: str
    threshold_field: str


_METRICS: tuple[_MetricSpec, ...] = (
    _MetricSpec("net_profit", "Résultat net", "%", "net_profit_gap_pct"),
    _MetricSpec("win_rate", "Taux de réussite", "points", "win_rate_gap_points"),
    _MetricSpec("profit_factor", "Profit factor", "absolu", "profit_factor_gap"),
    _MetricSpec("max_drawdown", "Drawdown maximal", "%", "max_drawdown_gap_pct"),
    _MetricSpec("realized_rr", "R multiple réalisé", "absolu", "realized_rr_gap"),
)


def compare_strategy(
    strategy_ref: str,
    mode: TradingMode | None,
    reference_block: Mapping[str, Any] | None,
    production: Performance,
    thresholds: ComparisonThresholds,
) -> StrategyComparison:
    """One comparison, pure: same inputs, same numbers, no clock and no I/O."""
    sufficient = production.trades >= thresholds.min_production_trades
    if reference_block is None:
        return StrategyComparison(
            strategy_ref=strategy_ref,
            mode=mode,
            reference_trades=None,
            production=production,
            metrics=(),
            sufficient_sample=sufficient,
            missing_reference=True,
            min_production_trades=thresholds.min_production_trades,
        )
    metrics = tuple(
        gap
        for spec in _METRICS
        if (gap := _metric_gap(spec, reference_block, production, thresholds)) is not None
    )
    return StrategyComparison(
        strategy_ref=strategy_ref,
        mode=mode,
        reference_trades=_reference_trades(reference_block),
        production=production,
        metrics=metrics,
        sufficient_sample=sufficient,
        missing_reference=False,
        min_production_trades=thresholds.min_production_trades,
    )


def _metric_gap(
    spec: _MetricSpec,
    reference_block: Mapping[str, Any],
    production: Performance,
    thresholds: ComparisonThresholds,
) -> MetricGap | None:
    reference = _number(reference_block.get(spec.name))
    produced = _produced_value(spec.name, production)
    if reference is None or produced is None:
        return None
    gap = _distance(spec.unit, reference, produced)
    threshold = float(getattr(thresholds, spec.threshold_field))
    return MetricGap(
        name=spec.name,
        label=spec.label,
        reference=reference,
        production=produced,
        gap=gap,
        threshold=threshold,
        unit=spec.unit,
        breached=gap > threshold,
    )


def _produced_value(name: str, production: Performance) -> float | None:
    if name == "net_profit":
        return float(production.net_profit)
    if name == "win_rate":
        return float(production.win_rate) if production.win_rate is not None else None
    if name == "profit_factor":
        return production.profit_factor
    if name == "max_drawdown":
        return float(production.max_drawdown)
    if name == "realized_rr":
        return production.realized_rr
    return None


def _distance(unit: str, reference: float, produced: float) -> float:
    if unit == "points":  # a fraction difference reads as percentage points
        return abs(produced - reference) * 100.0
    if unit == "%":
        if reference == 0:
            # A zero reference makes any non-zero production a full deviation rather than
            # an infinite ratio: the line stays readable and still alerts.
            return 0.0 if produced == 0 else 100.0
        return abs(produced - reference) / abs(reference) * 100.0
    return abs(produced - reference)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float | Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _reference_trades(reference_block: Mapping[str, Any]) -> int | None:
    value = _number(reference_block.get("trades"))
    return int(value) if value is not None else None


class ComparisonBuilder:
    """Reads the reference blocks and the production trades, then compares them."""

    def __init__(self, engine: Engine, thresholds: ComparisonThresholds | None = None) -> None:
        self._engine = engine
        self._thresholds = thresholds if thresholds is not None else ComparisonThresholds()

    def build(self, start: datetime, end: datetime) -> ComparisonReport:
        production: list[Trade] = [
            trade for _, trade in ReportData(self._engine).trades_between(start, end)
        ]
        references = self._references()
        by_strategy = group(production, Axis.STRATEGY)

        comparisons: list[StrategyComparison] = []
        for ref in sorted(set(by_strategy) | set(references)):
            reference = references.get(ref)
            if ref not in by_strategy:
                # Referenced but never traded in the window: no mode to separate, and the
                # report says so instead of silently dropping the strategy.
                comparisons.append(
                    compare_strategy(
                        ref, None, reference, compute_performance([]), self._thresholds
                    )
                )
                continue
            for mode_key, bucket in sorted(group(by_strategy[ref], Axis.MODE).items()):
                comparisons.append(
                    compare_strategy(
                        ref,
                        TradingMode(mode_key),
                        reference,
                        compute_performance(bucket),
                        self._thresholds,
                    )
                )
        alerts = tuple(alert for comparison in comparisons for alert in comparison.alerts())
        return ComparisonReport(comparisons=tuple(comparisons), alerts=alerts)

    def _references(self) -> dict[str, Mapping[str, Any]]:
        with Session(self._engine) as session:
            rows = session.execute(
                select(StrategyVersionRow.ref, StrategyVersionRow.manifest)
            ).all()
        references: dict[str, Mapping[str, Any]] = {}
        for ref, manifest in rows:
            block = _reference_block(manifest)
            if block is not None:
                references[str(ref)] = block
        return references


def _reference_block(manifest: Any) -> Mapping[str, Any] | None:
    if not isinstance(manifest, dict):
        return None
    parameters = manifest.get("parameters")
    if not isinstance(parameters, dict):
        return None
    block = parameters.get(BACKTEST_REFERENCE_KEY)
    return block if isinstance(block, dict) else None
