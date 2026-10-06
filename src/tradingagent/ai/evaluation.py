"""Does the AI filter actually help? (C-002, EF-030, TASK-044)

The shadow mode records verdicts nobody exploits; this module turns them into a
measurement. Both series are built over the SAME set of signals — those that carry an
AI verdict and produced a closed trade — so the comparison is never between two
different periods. All figures come from the shared analytics package, and the
recommendation is an advice for the operator, never a decision (RM-016).

This module is pure decision logic: every database read lives in the storage layer
(`AiCallStore.verdicts_between`, `ReportData.trades_between`).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Engine

from tradingagent.analytics import Performance, Trade, compute_performance
from tradingagent.storage.account import ReportData
from tradingagent.storage.ai_calls import AiCallStore

MIN_EVALUATED_SIGNALS = 100
TOP_EXCLUDED = 5
PURPOSE = "signal_filter"


@dataclass(frozen=True)
class ShadowEvaluation:
    evaluated_signals: int
    actual: Performance
    counterfactual: Performance
    excluded_actual: Performance
    excluded_counterfactual: Performance
    full_sample_conclusion: str
    robust_conclusion: str
    cost_eur: Decimal
    insufficient_sample: bool
    inconclusive: bool
    recommendation: str


def evaluate_shadow_filter(engine: Engine, start: datetime, end: datetime) -> ShadowEvaluation:
    rejected_ids, evaluated_count, cost = AiCallStore(engine).verdicts_between(start, end, PURPOSE)
    actual_trades = ReportData(engine).trades_between(start, end)
    counterfactual_trades = [pair for pair in actual_trades if pair[0] not in rejected_ids]

    actual = compute_performance([trade for _, trade in actual_trades])
    counterfactual = compute_performance([trade for _, trade in counterfactual_trades])
    excluded_actual = compute_performance(_without_top(actual_trades))
    excluded_counterfactual = compute_performance(_without_top(counterfactual_trades))

    full_better = counterfactual.net_profit > actual.net_profit
    robust_better = excluded_counterfactual.net_profit > excluded_actual.net_profit
    insufficient = evaluated_count < MIN_EVALUATED_SIGNALS
    inconclusive = full_better != robust_better

    if insufficient:
        recommendation = (
            f"GARDER SHADOW : échantillon insuffisant ({evaluated_count} signaux "
            f"évalués, {MIN_EVALUATED_SIGNALS} requis avant toute conclusion)."
        )
    elif inconclusive:
        recommendation = (
            "NON CONCLUE : l'avantage du filtre ne résiste pas à l'exclusion des cinq "
            "meilleures opérations — quelques coups de chance, pas un apport réel."
        )
    elif full_better:
        recommendation = (
            "PROPOSER LA PROMOTION EN ADVISORY : appliquer les verdicts du modèle "
            "aurait amélioré le résultat, y compris hors des meilleures opérations. "
            "La promotion reste une décision de l'opérateur (RM-016)."
        )
    else:
        recommendation = (
            "GARDER SHADOW : appliquer les verdicts du modèle n'aurait pas amélioré le résultat."
        )

    return ShadowEvaluation(
        evaluated_signals=evaluated_count,
        actual=actual,
        counterfactual=counterfactual,
        excluded_actual=excluded_actual,
        excluded_counterfactual=excluded_counterfactual,
        full_sample_conclusion="le filtre améliore" if full_better else "le filtre dégrade",
        robust_conclusion="le filtre améliore" if robust_better else "le filtre dégrade",
        cost_eur=cost,
        insufficient_sample=insufficient,
        inconclusive=inconclusive,
        recommendation=recommendation,
    )


def _without_top(trades: list[tuple[int, Trade]]) -> list[Trade]:
    """The series minus its five best wins: an edge that lives only there is luck."""
    best = sorted(
        (pair for pair in trades if pair[1].pnl_eur > 0),
        key=lambda pair: pair[1].pnl_eur,
        reverse=True,
    )[:TOP_EXCLUDED]
    excluded = {id(pair) for pair in best}
    return [pair[1] for pair in trades if id(pair) not in excluded]


def render(evaluation: ShadowEvaluation) -> str:
    return (
        "Évaluation du filtre IA (mode shadow)\n"
        f"Signaux évalués : {evaluation.evaluated_signals}\n"
        f"Série réelle : net {evaluation.actual.net_profit} EUR sur "
        f"{evaluation.actual.trades} trades\n"
        f"Série contrefactuelle (verdicts appliqués) : net "
        f"{evaluation.counterfactual.net_profit} EUR sur "
        f"{evaluation.counterfactual.trades} trades\n"
        f"Hors des cinq meilleures opérations : "
        f"{evaluation.excluded_actual.net_profit} EUR contre "
        f"{evaluation.excluded_counterfactual.net_profit} EUR\n"
        f"Échantillon complet : {evaluation.full_sample_conclusion} ; "
        f"échantillon réduit : {evaluation.robust_conclusion}\n"
        f"Coût des appels IA sur la période : {evaluation.cost_eur} EUR\n"
        f"Recommandation : {evaluation.recommendation}"
    )
