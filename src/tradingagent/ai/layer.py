"""The AI signal filter: useful, and structurally unable to do harm (F-010, C-002, TASK-037).

The veto is asymmetric: a verdict can only degrade a decision, never improve one. The
reviewer's power ends at approve/reject plus text — a response carrying levels, sizes or
new signals has those fields ignored and journalled as an overrun attempt. Every call,
failure and verdict is persisted in full; the behaviour on failure follows the
strategy's declared `ai_filter` state.
"""

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from tradingagent.core.market import Direction
from tradingagent.core.mode import AiFilter
from tradingagent.core.states import Severity
from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.ai_calls import AiCall, AiCallStore, AiReply
from tradingagent.storage.events import SystemEventStore

log = logging.getLogger(__name__)

# EUR per million tokens; a model outside the table is billed at the default rate.
PRICES_EUR_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {}
DEFAULT_INPUT_PRICE = Decimal("0.80")
DEFAULT_OUTPUT_PRICE = Decimal("4.00")

CONFORM_SCHEMA = (
    '{"decision": "approve" | "reject", "reason": "<court motif>", '
    '"text": "<explication en français>"}'
)

SYSTEM_PROMPT = f"""Tu es le filtre d'une couche d'analyse pour un agent de signaux de trading.
Ton pouvoir est STRICTEMENT borné : approuver ou rejeter un signal candidat déjà produit
par les règles déterministes. Tu ne peux jamais créer un signal, modifier un prix d'entrée,
un stop-loss, un objectif, une taille de position ou un mode d'exécution : toute réponse
tentant ces actions est ignorée et journalisée comme tentative de dépassement.
Réponds uniquement par un JSON valide, sans texte autour, de la forme exacte :
{CONFORM_SCHEMA}
`decision` vaut "approve" si le candidat est cohérent, "reject" sinon. `text` est une
explication en français, en une à trois phrases, destinée à l'opérateur."""


@dataclass(frozen=True)
class ReviewContext:
    """The strictly bounded context the model may see (F-010): market data and levels,
    nothing about the account, the capital or the risk configuration."""

    symbol: str
    timeframe: Timeframe
    strategy_ref: str
    direction: Direction
    observed_price: float
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profits: tuple[float, ...]
    indicators: Mapping[str, float]
    market_state: str


@dataclass(frozen=True)
class ReviewOutcome:
    verdict: str  # "approved" | "rejected" | "unavailable"
    ai_filter: AiFilter
    applied: bool  # the verdict changes the signal's fate (always False in shadow)
    blocks_signal: bool  # the final word: whether the signal must not be emitted
    degraded: bool  # the signal continues despite a model problem
    reason: str
    text: str
    overrun_attempts: tuple[str, ...] = ()
    latency_ms: int | None = None
    cost_eur: Decimal | None = None
    model: str = "unknown"


class AiClient(Protocol):
    async def complete(self, system: str, user: str) -> AiReply: ...


def _user_prompt(context: ReviewContext) -> str:
    indicators = ", ".join(
        f"{name}={value:.5g}" for name, value in sorted(context.indicators.items())
    )
    targets = ", ".join(f"{level:.2f}" for level in context.take_profits)
    return (
        f"Marché : {context.symbol} ({context.timeframe.value})\n"
        f"Stratégie : {context.strategy_ref}\n"
        f"Sens du candidat : {'ACHAT' if context.direction is Direction.BUY else 'VENTE'}\n"
        f"Prix observé : {context.observed_price:.2f}\n"
        f"Zone d'entrée : {context.entry_low:.2f} à {context.entry_high:.2f}\n"
        f"Stop-loss : {context.stop_loss:.2f}\n"
        f"Objectifs : {targets}\n"
        f"Indicateurs : {indicators}\n"
        f"État du marché : {context.market_state}\n"
        "Faut-il approuver ou rejeter ce signal candidat ?"
    )


def _prices(model: str) -> tuple[Decimal, Decimal]:
    return PRICES_EUR_PER_MTOK.get(model, (DEFAULT_INPUT_PRICE, DEFAULT_OUTPUT_PRICE))


def _cost_eur(reply: AiReply) -> Decimal:
    input_price, output_price = _prices(reply.model)
    tokens_cost = (
        Decimal(reply.input_tokens) * input_price + Decimal(reply.output_tokens) * output_price
    )
    return (tokens_cost / Decimal(1_000_000)).quantize(Decimal("0.000001"))


def _parse(response: str) -> tuple[str | None, str, str, tuple[str, ...]]:
    """Returns (decision, reason, text, overrun attempts). A non-conforming body yields
    a None decision, which the caller treats as model unavailability."""
    body = response.strip()
    if body.startswith("```"):
        body = body.strip("`")
        if body.startswith("json"):
            body = body.removeprefix("json")
    try:
        payload: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        return None, "réponse non conforme au schéma attendu", "", ()
    if not isinstance(payload, dict):
        return None, "réponse non conforme au schéma attendu", "", ()
    allowed = {"decision", "reason", "text"}
    overruns = tuple(sorted(str(key) for key in payload if key not in allowed))
    decision = payload.get("decision")
    if decision not in ("approve", "reject"):
        return None, "décision absente ou invalide", "", overruns
    reason = str(payload.get("reason", ""))
    text = str(payload.get("text", ""))
    return str(decision), reason, text, overruns


@dataclass
class _LayerConfig:
    ai_filter: AiFilter = AiFilter.SHADOW
    timeout: timedelta = timedelta(seconds=10)
    budget_eur: Decimal = Decimal("10.00")
    purpose: str = "signal_filter"


def _fallback_reason(error: str) -> str:
    return f"Filtre IA indisponible ({error}) : décision prise par les règles déterministes."


class AiFilterLayer:
    """One entry point: `review`. Called only when a signal candidate exists."""

    def __init__(
        self,
        client: AiClient,
        calls: AiCallStore,
        events: SystemEventStore,
        *,
        model: str,
        input_price: Decimal = DEFAULT_INPUT_PRICE,
        output_price: Decimal = DEFAULT_OUTPUT_PRICE,
        **config: Any,
    ) -> None:
        self._client = client
        self._calls = calls
        self._events = events
        self._model = model
        self._config = _LayerConfig(**config)
        self.input_price = input_price
        self.output_price = output_price

    @property
    def ai_filter(self) -> AiFilter:
        return self._config.ai_filter

    async def review(
        self,
        context: ReviewContext,
        at: datetime,
        signal_id: int | None = None,
        ai_filter: AiFilter | None = None,
    ) -> ReviewOutcome:
        """`ai_filter` overrides the layer default for this call: the manifest of the
        strategy that produced the signal is the authority (RM-016, TASK-037)."""
        effective = ai_filter if ai_filter is not None else self._config.ai_filter
        if self._calls.total_cost_eur() >= self._config.budget_eur:
            return await self._unavailable(context, at, signal_id, "budget épuisé", effective)

        request = _user_prompt(context)
        started = time.monotonic()
        try:
            reply = await asyncio.wait_for(
                self._client.complete(SYSTEM_PROMPT, request), self._config.timeout.total_seconds()
            )
        except Exception as error:  # timeout, network, provider
            elapsed = int((time.monotonic() - started) * 1000)
            return await self._unavailable(context, at, signal_id, str(error), effective, elapsed)

        latency_ms = int((time.monotonic() - started) * 1000)
        decision, reason, text, overruns = _parse(reply.text)
        cost = _cost_eur(reply)
        if overruns:
            await asyncio.to_thread(
                self._events.record,
                "ai_overrun",
                Severity.WARNING,
                {"model": reply.model, "fields": list(overruns)},
                at,
            )
        outcome = self._outcome(
            decision, reason, text, overruns, latency_ms, cost, reply.model, effective
        )
        await asyncio.to_thread(
            self._calls.record,
            AiCall(
                signal_id=signal_id,
                purpose=self._config.purpose,
                ai_filter=effective,
                request={"prompt": request, "system": SYSTEM_PROMPT},
                response=reply.text,
                verdict=outcome.verdict if outcome.verdict != "unavailable" else None,
                error=None,
                latency_ms=latency_ms,
                cost_eur=cost,
                called_at=at,
                model=reply.model,
            ),
        )
        return outcome

    async def _unavailable(
        self,
        context: ReviewContext,
        at: datetime,
        signal_id: int | None,
        error: str,
        ai_filter: AiFilter,
        latency_ms: int | None = None,
    ) -> ReviewOutcome:
        del context
        log.warning("AI review unavailable (%s): %s", ai_filter, error)
        await asyncio.to_thread(
            self._calls.record,
            AiCall(
                signal_id=signal_id,
                purpose=self._config.purpose,
                ai_filter=ai_filter,
                request={"error": error},
                response=None,
                verdict=None,
                error=error,
                latency_ms=latency_ms,
                cost_eur=None,
                called_at=at,
                model=self._model,
            ),
        )
        return ReviewOutcome(
            verdict="unavailable",
            ai_filter=ai_filter,
            applied=False,
            blocks_signal=ai_filter is AiFilter.REQUIRED,
            degraded=ai_filter is not AiFilter.REQUIRED,
            reason=_fallback_reason(error),
            text=_fallback_reason(error),
            model=self._model,
            latency_ms=latency_ms,
        )

    def _outcome(
        self,
        decision: str | None,
        reason: str,
        text: str,
        overruns: tuple[str, ...],
        latency_ms: int,
        cost: Decimal,
        model: str,
        ai_filter: AiFilter,
    ) -> ReviewOutcome:
        if decision is None:
            return ReviewOutcome(
                verdict="unavailable",
                ai_filter=ai_filter,
                applied=False,
                blocks_signal=ai_filter is AiFilter.REQUIRED,
                degraded=True,
                reason=_fallback_reason(reason),
                text=_fallback_reason(reason),
                overrun_attempts=overruns,
                latency_ms=latency_ms,
                cost_eur=cost,
                model=model,
            )
        rejected = decision == "reject"
        shadow = ai_filter is AiFilter.SHADOW
        return ReviewOutcome(
            verdict="rejected" if rejected else "approved",
            ai_filter=ai_filter,
            applied=not shadow,
            blocks_signal=not shadow and rejected,
            degraded=False,
            reason=reason,
            text=text,
            overrun_attempts=overruns,
            latency_ms=latency_ms,
            cost_eur=cost,
            model=model,
        )
