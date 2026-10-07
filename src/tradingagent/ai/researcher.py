"""Falsifiable hypotheses from backtest, validation and loss evidence (cahier v3 §5, §17, §39).

Every hypothesis is a *proposal*: a parametric change, the data that motivated it, and the
measurement that would refute it. It is stored as PROPOSED and nothing else — the researcher
never writes a manifest, never changes a status and never touches a live strategy. The
validation system (walk-forward, out-of-sample, Monte-Carlo, paper) decides, and only an
operator may promote.

A model may add a commentary on the deterministic hypotheses; its reply is parsed against
one strict schema and every other field is journalled as an overrun attempt.
"""

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from typing import Any

from tradingagent.ai.analyst import parse_model_text
from tradingagent.ai.lab_store import AnalysisRecord, LabStore, ProposalRecord, StoredAnalysis
from tradingagent.ai.layer import (
    DEFAULT_INPUT_PRICE,
    DEFAULT_OUTPUT_PRICE,
    PRICES_EUR_PER_MTOK,
    AiClient,
)
from tradingagent.core.states import AnalysisKind
from tradingagent.storage.ai_calls import AiReply

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu commentes des hypothèses DÉJÀ CALCULÉES par des règles déterministes.
Ton pouvoir est nul : tu ne peux ni créer un signal ou un ordre, ni modifier un stop-loss,
un objectif, une taille ou un mode d'exécution, ni promouvoir une stratégie. Tu ne peux pas
non plus changer les hypothèses ni les valeurs proposées.
Réponds uniquement par un JSON valide, sans texte autour, de la forme exacte :
{"commentary": "<une à trois phrases en français>"}"""

# The gates any proposed change must clear before it can reach production (§10).
VALIDATION_PLAN = ("walk_forward", "out_of_sample", "monte_carlo", "paper")


@dataclass(frozen=True)
class ValidationEvidence:
    """One gate already run for the strategy, with its verdict."""

    stage: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestEvidence:
    """The measured material a hypothesis may be built on."""

    market: str
    ref: str
    parameters: Mapping[str, float]
    metrics: Mapping[str, float]
    dataset_id: str = ""
    validations: tuple[ValidationEvidence, ...] = ()


@dataclass(frozen=True)
class ResearchThresholds:
    """Fixed limits, mirroring the acceptance thresholds of the validation protocol."""

    max_drawdown_eur: float = 200.0
    max_parameter_dispersion: float = 0.5
    min_profit_factor_net: float = 1.2


DEFAULT_THRESHOLDS = ResearchThresholds()


@dataclass(frozen=True)
class Hypothesis:
    """A falsifiable proposal, with the data that motivated it."""

    market: str
    ref: str | None
    analysis_id: int | None
    hypothesis: str
    proposed_change: dict[str, Any]


def _change(
    *,
    parameter: str,
    current: Any,
    proposed: Any,
    action: str,
    citations: Sequence[str],
    falsification: str,
) -> dict[str, Any]:
    return {
        "parameter": parameter,
        "current_value": current,
        "proposed_value": proposed,
        "action": action,
        "evidence": list(citations),
        "falsification": falsification,
        "validation": list(VALIDATION_PLAN),
    }


def _drawdown_rule(evidence: BacktestEvidence, thresholds: ResearchThresholds) -> Hypothesis | None:
    drawdown = evidence.metrics.get("max_drawdown_eur")
    risk = evidence.parameters.get("risk_per_trade_pct")
    if drawdown is None or risk is None or risk <= 0:
        return None
    if drawdown <= thresholds.max_drawdown_eur:
        return None
    proposed = risk / 2
    return Hypothesis(
        market=evidence.market,
        ref=evidence.ref,
        analysis_id=None,
        hypothesis=(
            f"Réduire risk_per_trade_pct de {risk:g} à {proposed:g} ramènerait le drawdown "
            f"sous {thresholds.max_drawdown_eur:g} EUR sans changer les règles d'entrée."
        ),
        proposed_change=_change(
            parameter="risk_per_trade_pct",
            current=risk,
            proposed=proposed,
            action="decrease",
            citations=(
                f"backtest_runs:{evidence.ref}#max_drawdown_eur={drawdown:g}",
                f"seuil:max_drawdown_eur={thresholds.max_drawdown_eur:g}",
                f"dataset:{evidence.dataset_id}",
            ),
            falsification=(
                f"Sur {evidence.dataset_id}, le drawdown maximum doit retomber sous "
                f"{thresholds.max_drawdown_eur:g} EUR ; sinon l'hypothèse est réfutée."
            ),
        ),
    )


def _robustness_rule(
    evidence: BacktestEvidence, thresholds: ResearchThresholds
) -> Hypothesis | None:
    failed = [
        validation
        for validation in evidence.validations
        if validation.stage == "parameter_robustness" and not validation.passed
    ]
    dispersion = evidence.metrics.get("parameter_dispersion")
    if not failed and (dispersion is None or dispersion <= thresholds.max_parameter_dispersion):
        return None
    name: str | None = None
    if failed:
        sensitive = failed[0].detail.get("most_sensitive")
        name = str(sensitive) if sensitive else None
    if name is None:
        from_metrics = evidence.metrics.get("most_sensitive_parameter")
        name = str(from_metrics) if from_metrics else None
    if name is None and evidence.parameters:
        name = sorted(evidence.parameters)[0]
    if name is None or name not in evidence.parameters:
        return None
    current = evidence.parameters[name]
    citations = [f"backtest_runs:{evidence.ref}#parameter_dispersion={dispersion}"]
    if failed:
        citations.insert(0, f"validation_runs:{evidence.ref}#parameter_robustness=FAILED")
    return Hypothesis(
        market=evidence.market,
        ref=evidence.ref,
        analysis_id=None,
        hypothesis=(
            f"Geler {name} à {current:g} supprimerait la sensibilité du résultat aux "
            "paramètres, mesurée comme instable."
        ),
        proposed_change=_change(
            parameter=name,
            current=current,
            proposed=current,
            action="freeze",
            citations=citations,
            falsification=(
                f"Rejouer {evidence.dataset_id} avec {name} gelé doit ramener la dispersion "
                f"sous {thresholds.max_parameter_dispersion:g} ; sinon l'hypothèse est réfutée."
            ),
        ),
    )


def _degradation_rule(
    evidence: BacktestEvidence, analyses: Sequence[StoredAnalysis]
) -> Hypothesis | None:
    for analysis in analyses:
        if analysis.ref not in (None, evidence.ref):
            continue
        figures = analysis.findings.get("figures", {})
        return Hypothesis(
            market=evidence.market,
            ref=evidence.ref,
            analysis_id=None,
            hypothesis=(
                "Ajouter un filtre de régime désactiverait les prises de position pendant "
                "la dégradation observée, sans toucher au stop ni au risque par trade."
            ),
            proposed_change=_change(
                parameter="regime_filter",
                current=False,
                proposed=True,
                action="enable",
                citations=(
                    f"ai_analyses:{analysis.id}#kind=degradation",
                    f"chiffres:{json.dumps(figures, sort_keys=True, default=str)}",
                ),
                falsification=(
                    "Sur le prochain mois paper, la fenêtre récente doit retrouver une "
                    "espérance positive ; sinon l'hypothèse est réfutée."
                ),
            ),
        )
    return None


def _rules(
    evidence: BacktestEvidence,
    analyses: Sequence[StoredAnalysis],
    thresholds: ResearchThresholds,
) -> list[Hypothesis]:
    drafts = [
        _drawdown_rule(evidence, thresholds),
        _robustness_rule(evidence, thresholds),
        _degradation_rule(evidence, analyses),
    ]
    return [draft for draft in drafts if draft is not None]


def _cost_eur(reply: AiReply) -> Decimal:
    input_price, output_price = PRICES_EUR_PER_MTOK.get(
        reply.model, (DEFAULT_INPUT_PRICE, DEFAULT_OUTPUT_PRICE)
    )
    cost = Decimal(reply.input_tokens) * input_price + Decimal(reply.output_tokens) * output_price
    return (cost / Decimal(1_000_000)).quantize(Decimal("0.000001"))


def _prompt(evidence: BacktestEvidence, drafts: Sequence[Hypothesis]) -> str:
    return (
        "Hypothèses déterministes déjà calculées :\n"
        f"{json.dumps([draft.proposed_change for draft in drafts], sort_keys=True, default=str)}\n"
        f"Stratégie : {evidence.ref} sur {evidence.market}\n"
        f"Jeu de données : {evidence.dataset_id}\n"
        "Commente ces hypothèses en une à trois phrases."
    )


class StrategyResearcher:
    """Turns measured evidence into PROPOSED hypotheses, and never into anything more."""

    def __init__(
        self,
        store: LabStore,
        client: AiClient | None = None,
        *,
        model: str = "deterministic",
        thresholds: ResearchThresholds = DEFAULT_THRESHOLDS,
    ) -> None:
        self._store = store
        self._client = client
        self._model = model
        self._thresholds = thresholds

    async def research(self, evidence: BacktestEvidence, *, at: datetime) -> tuple[Hypothesis, ...]:
        analyses = self._store.recent_analyses(
            kind=AnalysisKind.DEGRADATION, market=evidence.market, limit=20
        )
        drafts = _rules(evidence, analyses, self._thresholds)
        if not drafts:
            return ()
        response, overruns, model, error, cost = await self._commentary(evidence, drafts)
        findings: dict[str, Any] = {
            "hypotheses": [draft.proposed_change for draft in drafts],
            "overrun_attempts": list(overruns),
            "evidence": {
                "market": evidence.market,
                "ref": evidence.ref,
                "dataset_id": evidence.dataset_id,
                "metrics": dict(evidence.metrics),
            },
        }
        if error is not None:
            findings["model_error"] = error
        if overruns:
            log.warning("AI Lab overrun attempt ignored: %s", overruns)
        analysis_id = await asyncio.to_thread(
            self._store.record_analysis,
            AnalysisRecord(
                kind=AnalysisKind.HYPOTHESIS,
                market=evidence.market,
                ref=evidence.ref,
                model=model,
                request={"prompt": _prompt(evidence, drafts)},
                response=response,
                findings=findings,
                cost_eur=cost,
                created_at=at,
            ),
        )
        stored: list[Hypothesis] = []
        for draft in drafts:
            proposal_id = await asyncio.to_thread(
                self._store.record_proposal,
                ProposalRecord(
                    market=draft.market,
                    ref=draft.ref,
                    analysis_id=analysis_id,
                    hypothesis=draft.hypothesis,
                    proposed_change=draft.proposed_change,
                    created_at=at,
                ),
            )
            log.info("proposal %s recorded as PROPOSED", proposal_id)
            stored.append(replace(draft, analysis_id=analysis_id))
        return tuple(stored)

    async def _commentary(
        self, evidence: BacktestEvidence, drafts: Sequence[Hypothesis]
    ) -> tuple[str | None, tuple[str, ...], str, str | None, Decimal | None]:
        """The model only comments; the hypotheses and their values stay deterministic."""
        if self._client is None:
            return None, (), self._model, None, None
        try:
            reply: AiReply = await self._client.complete(SYSTEM_PROMPT, _prompt(evidence, drafts))
        except Exception as error:  # timeout, network, provider: the proposals still stand
            return None, (), self._model, str(error), None
        commentary, overruns = parse_model_text(reply.text, field="commentary")
        return commentary, overruns, reply.model, None, _cost_eur(reply)
