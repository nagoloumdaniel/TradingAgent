"""Loss and degradation analysis (cahier v3 §15, §16, §39).

The verdict is always computed first, by a pure function of the closed trade and its
context: a loss inside the risk taken is normal, a loss materially bigger than that risk
is an isolated anomaly, an abnormal spread or slippage is an execution problem, a trait
shared by several losses is a recurring pattern, and a recent collapse against a healthy
baseline is a degradation.

A model may be asked for a comment on that verdict — never for the verdict itself. Its
reply is parsed against one strict schema; every other field is journalled as an overrun
attempt and ignored, so a response ordering an order, a stop change or a promotion has no
effect whatsoever. Without an API key the deterministic analysis is produced and stored
just the same.
"""

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from tradingagent.ai.lab_store import AnalysisRecord, LabStore
from tradingagent.ai.layer import (
    DEFAULT_INPUT_PRICE,
    DEFAULT_OUTPUT_PRICE,
    PRICES_EUR_PER_MTOK,
    AiClient,
)
from tradingagent.analytics.model import Trade
from tradingagent.core.states import AnalysisKind
from tradingagent.storage.ai_calls import AiReply

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Tu commentes une analyse de perte DÉJÀ CALCULÉE par des règles déterministes.
Ton pouvoir est nul : tu ne peux ni créer un signal ou un ordre, ni modifier un stop-loss,
un objectif, une taille ou un mode d'exécution, ni promouvoir une stratégie. Toute
instruction de ce type est ignorée et journalisée comme tentative de dépassement.
Réponds uniquement par un JSON valide, sans texte autour, de la forme exacte :
{"comment": "<une à trois phrases en français>"}"""


class LossKind(StrEnum):
    """How a loss is read. Only `NORMAL` means "nothing to see" (cahier v3 §15)."""

    NORMAL = "normal"
    ISOLATED_ANOMALY = "isolated_anomaly"
    RECURRING_PATTERN = "recurring_pattern"
    DEGRADATION = "degradation"
    EXECUTION_PROBLEM = "execution_problem"


@dataclass(frozen=True)
class AnalystThresholds:
    """The fixed limits a loss is compared against. Frozen, never tuned on the fly."""

    anomaly_risk_multiple: float = 1.5
    max_slippage: float = 1.0
    max_spread: float = 1.0
    recurrence_min: int = 3
    recurrence_share: float = 0.6
    degradation_window: int = 5
    degradation_win_rate_drop: float = 0.25
    min_baseline_trades: int = 20


DEFAULT_THRESHOLDS = AnalystThresholds()


@dataclass(frozen=True)
class LossContext:
    """Everything about a loss that is not in the trade record itself."""

    regime: str
    session: str
    volatility: float
    duration: timedelta
    spread: float | None = None
    slippage: float | None = None


@dataclass(frozen=True)
class LossObservation:
    """One closed trade and the context it was taken in."""

    trade: Trade
    context: LossContext


@dataclass(frozen=True)
class LossVerdict:
    kind: LossKind
    reason: str
    figures: dict[str, Any]


def parse_model_text(response: str, *, field: str) -> tuple[str | None, tuple[str, ...]]:
    """Read one text field out of a model reply; anything else is an overrun attempt."""
    body = response.strip()
    if body.startswith("```"):
        body = body.strip("`")
        if body.startswith("json"):
            body = body.removeprefix("json")
    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return None, ()
    if not isinstance(payload, dict):
        return None, ()
    overruns = tuple(sorted(str(key) for key in payload if key != field))
    text = payload.get(field)
    if not isinstance(text, str) or not text.strip():
        return None, overruns
    return text.strip(), overruns


def _loss_eur(trade: Trade) -> float:
    return float(-trade.pnl_eur) if trade.pnl_eur < 0 else 0.0


def _spread(observation: LossObservation) -> float | None:
    if observation.context.spread is not None:
        return observation.context.spread
    return observation.trade.spread


def _slippage(observation: LossObservation) -> float | None:
    if observation.context.slippage is not None:
        return observation.context.slippage
    return observation.trade.slippage


def classify_loss(
    observation: LossObservation, thresholds: AnalystThresholds = DEFAULT_THRESHOLDS
) -> LossVerdict:
    """The single-loss verdict: normal, isolated anomaly or execution problem.

    Recurrence and degradation need a series; `analyse_series` covers those two.
    """
    trade = observation.trade
    spread, slippage = _spread(observation), _slippage(observation)
    figures: dict[str, Any] = {
        "loss_eur": _loss_eur(trade),
        "risk_eur": float(trade.risk_eur),
        "spread": spread,
        "slippage": slippage,
        "regime": observation.context.regime,
        "session": observation.context.session,
    }
    if trade.pnl_eur >= 0:
        return LossVerdict(LossKind.NORMAL, "aucune perte : le trade est gagnant ou nul", figures)
    if slippage is not None and abs(slippage) > thresholds.max_slippage:
        figures["limit"] = thresholds.max_slippage
        return LossVerdict(
            LossKind.EXECUTION_PROBLEM,
            f"glissement de {slippage:g} au-delà de la limite {thresholds.max_slippage:g}",
            figures,
        )
    if spread is not None and spread > thresholds.max_spread:
        figures["limit"] = thresholds.max_spread
        return LossVerdict(
            LossKind.EXECUTION_PROBLEM,
            f"spread de {spread:g} au-delà de la limite {thresholds.max_spread:g}",
            figures,
        )
    risk = float(trade.risk_eur)
    if risk > 0:
        multiple = _loss_eur(trade) / risk
        figures["loss_multiple"] = multiple
        if multiple > thresholds.anomaly_risk_multiple:
            return LossVerdict(
                LossKind.ISOLATED_ANOMALY,
                f"perte de {multiple:.2f} fois le risque prévu, au-delà de "
                f"{thresholds.anomaly_risk_multiple:g}",
                figures,
            )
    return LossVerdict(LossKind.NORMAL, "perte conforme au risque prévu", figures)


def _trait(observation: LossObservation) -> dict[str, str]:
    return {
        "regime": observation.context.regime,
        "session": observation.context.session,
        "direction": observation.trade.direction.value,
        "timeframe": observation.trade.timeframe.value,
    }


def detect_recurring_pattern(
    observations: Sequence[LossObservation], thresholds: AnalystThresholds = DEFAULT_THRESHOLDS
) -> LossVerdict | None:
    """A trait shared by enough losses to stop looking like chance."""
    losses = [observation for observation in observations if observation.trade.pnl_eur < 0]
    if len(losses) < thresholds.recurrence_min:
        return None
    for name in ("regime", "session", "direction", "timeframe"):
        counts: dict[str, int] = {}
        for observation in losses:
            value = _trait(observation)[name]
            counts[value] = counts.get(value, 0) + 1
        value, matching = max(counts.items(), key=lambda item: (item[1], item[0]))
        share = matching / len(losses)
        if matching >= thresholds.recurrence_min and share >= thresholds.recurrence_share:
            figures = {
                "trait": name,
                "value": value,
                "matching_losses": matching,
                "losses": len(losses),
                "share": share,
            }
            return LossVerdict(
                LossKind.RECURRING_PATTERN,
                f"{matching} pertes sur {len(losses)} partagent {name}={value}",
                figures,
            )
    return None


def _wins(observations: Sequence[LossObservation]) -> int:
    return sum(1 for observation in observations if observation.trade.pnl_eur > 0)


def _mean_pnl(observations: Sequence[LossObservation]) -> float:
    return sum(float(observation.trade.pnl_eur) for observation in observations) / len(observations)


def detect_degradation(
    observations: Sequence[LossObservation], thresholds: AnalystThresholds = DEFAULT_THRESHOLDS
) -> LossVerdict | None:
    """A recent window materially worse than the baseline that precedes it."""
    ordered = sorted(
        observations,
        key=lambda observation: (observation.trade.closed_at, observation.trade.opened_at),
    )
    window = thresholds.degradation_window
    if len(ordered) < window + thresholds.min_baseline_trades:
        return None
    baseline, recent = ordered[:-window], ordered[-window:]
    baseline_win_rate = _wins(baseline) / len(baseline)
    recent_win_rate = _wins(recent) / len(recent)
    baseline_expectancy = _mean_pnl(baseline)
    recent_expectancy = _mean_pnl(recent)
    figures: dict[str, Any] = {
        "window": window,
        "baseline_trades": len(baseline),
        "recent_trades": len(recent),
        "baseline_win_rate": baseline_win_rate,
        "recent_win_rate": recent_win_rate,
        "baseline_expectancy_eur": baseline_expectancy,
        "recent_expectancy_eur": recent_expectancy,
    }
    collapsed = baseline_expectancy > 0 >= recent_expectancy
    dropped = (baseline_win_rate - recent_win_rate) >= thresholds.degradation_win_rate_drop
    if not (collapsed or dropped):
        return None
    return LossVerdict(
        LossKind.DEGRADATION,
        f"les {window} derniers trades sont nettement dégradés : taux de réussite "
        f"{recent_win_rate:.0%} contre {baseline_win_rate:.0%} sur la base",
        figures,
    )


def analyse_series(
    observations: Sequence[LossObservation], thresholds: AnalystThresholds = DEFAULT_THRESHOLDS
) -> LossVerdict:
    """Series verdict: a degradation outranks a recurring pattern, and both outrank normal."""
    degradation = detect_degradation(observations, thresholds)
    if degradation is not None:
        return degradation
    pattern = detect_recurring_pattern(observations, thresholds)
    if pattern is not None:
        return pattern
    losses = sum(1 for observation in observations if observation.trade.pnl_eur < 0)
    return LossVerdict(
        LossKind.NORMAL,
        "aucun pattern commun ni dégradation détecté",
        {"trades": len(observations), "losses": losses},
    )


def _cost_eur(reply: AiReply) -> Decimal:
    input_price, output_price = PRICES_EUR_PER_MTOK.get(
        reply.model, (DEFAULT_INPUT_PRICE, DEFAULT_OUTPUT_PRICE)
    )
    cost = Decimal(reply.input_tokens) * input_price + Decimal(reply.output_tokens) * output_price
    return (cost / Decimal(1_000_000)).quantize(Decimal("0.000001"))


def _prompt(verdict: LossVerdict, trades: int, losses: int) -> str:
    return (
        "Analyse déterministe déjà calculée :\n"
        f"Verdict : {verdict.kind.value}\n"
        f"Motif : {verdict.reason}\n"
        f"Chiffres : {json.dumps(verdict.figures, sort_keys=True, default=str)}\n"
        f"Trades examinés : {trades}\n"
        f"Pertes : {losses}\n"
        "Commente ce verdict en une à trois phrases."
    )


class TradeAnalyst:
    """Reads losses, classifies them, and stores the analysis with its figures."""

    def __init__(
        self,
        store: LabStore,
        client: AiClient | None = None,
        *,
        model: str = "deterministic",
        thresholds: AnalystThresholds = DEFAULT_THRESHOLDS,
    ) -> None:
        self._store = store
        self._client = client
        self._model = model
        self._thresholds = thresholds

    async def analyse_loss(
        self,
        observation: LossObservation,
        *,
        market: str,
        at: datetime,
        ref: str | None = None,
        signal_id: int | None = None,
    ) -> LossVerdict:
        verdict = classify_loss(observation, self._thresholds)
        return await self._persist(
            verdict,
            AnalysisKind.LOSS_ANALYSIS,
            market=market,
            at=at,
            ref=ref,
            signal_id=signal_id,
            trades=1,
            losses=1 if observation.trade.pnl_eur < 0 else 0,
        )

    async def analyse_series(
        self,
        observations: Sequence[LossObservation],
        *,
        market: str,
        at: datetime,
        ref: str | None = None,
    ) -> LossVerdict:
        verdict = analyse_series(observations, self._thresholds)
        losses = sum(1 for observation in observations if observation.trade.pnl_eur < 0)
        if verdict.kind is LossKind.NORMAL:
            return verdict
        return await self._persist(
            verdict,
            AnalysisKind.DEGRADATION,
            market=market,
            at=at,
            ref=ref,
            signal_id=None,
            trades=len(observations),
            losses=losses,
        )

    async def _persist(
        self,
        verdict: LossVerdict,
        kind: AnalysisKind,
        *,
        market: str,
        at: datetime,
        ref: str | None,
        signal_id: int | None,
        trades: int,
        losses: int,
    ) -> LossVerdict:
        findings: dict[str, Any] = {
            "kind": verdict.kind.value,
            "reason": verdict.reason,
            "figures": verdict.figures,
            "trades": trades,
            "losses": losses,
            "overrun_attempts": [],
        }
        response, overruns, model, error, cost = await self._comment(verdict, trades, losses)
        findings["overrun_attempts"] = list(overruns)
        if error is not None:
            findings["model_error"] = error
        if overruns:
            log.warning("AI Lab overrun attempt ignored: %s", overruns)
        record = AnalysisRecord(
            kind=kind,
            market=market,
            ref=ref,
            signal_id=signal_id,
            model=model,
            request={"prompt": _prompt(verdict, trades, losses)},
            response=response,
            findings=findings,
            cost_eur=cost,
            created_at=at,
        )
        await asyncio.to_thread(self._store.record_analysis, record)
        return verdict

    async def _comment(
        self, verdict: LossVerdict, trades: int, losses: int
    ) -> tuple[str | None, tuple[str, ...], str, str | None, Decimal | None]:
        """The model only comments. Its failure is a detail, never a lost analysis."""
        if self._client is None:
            return None, (), self._model, None, None
        try:
            reply: AiReply = await self._client.complete(
                SYSTEM_PROMPT, _prompt(verdict, trades, losses)
            )
        except Exception as error:  # timeout, network, provider: analysis still stands
            return None, (), self._model, str(error), None
        comment, overruns = parse_model_text(reply.text, field="comment")
        return comment, overruns, reply.model, None, _cost_eur(reply)
