"""The daily AI Lab pass, wired into the agent loop (cahier v3 §5, §15, §16, §17, §52).

Once per UTC day, the analyst reads the trades that closed and the researcher turns what it
learned into falsifiable hypotheses. Nothing here can trade: the only tables this module
writes are `ai_analyses`, `ai_proposals` and the `system_events` marker that makes the pass
idempotent.

The pass is deliberately dumb about *when*: the loop calls `run_once` every cycle and the
day marker makes it a no-op until midnight UTC, which keeps a scheduler out of the loop.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Engine, insert, select
from sqlalchemy.orm import Session

from tradingagent.ai.analyst import LossContext, LossObservation, LossVerdict, TradeAnalyst
from tradingagent.ai.escalation import DEFAULT_TRIGGER, Escalation, escalations, failure_patterns
from tradingagent.ai.lab_store import LabStore
from tradingagent.ai.researcher import (
    BacktestEvidence,
    Hypothesis,
    StrategyResearcher,
    ValidationEvidence,
)
from tradingagent.analytics.model import Trade
from tradingagent.core.states import Severity
from tradingagent.runtime.ports import NotifierPort
from tradingagent.storage.models import (
    BacktestRunRow,
    ExecutionEventRow,
    OrderRow,
    PositionRow,
    SignalRow,
    StrategyVersionRow,
    SystemEventRow,
    TradeRow,
    ValidationRunRow,
)

log = logging.getLogger(__name__)

MARKER = "ai_lab_ran"
# How much history the trigger reads. Two weeks of analyses is far more than a fortnight of
# trading produces, and the window — not this limit — is what bounds the count.
ESCALATION_HISTORY = 200
SESSIONS = (
    (0, "asie"),
    (7, "londres"),
    (13, "new_york"),
    (21, "apres_cloture"),
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def day_floor(at: datetime) -> datetime:
    return at.replace(hour=0, minute=0, second=0, microsecond=0)


def session_of(moment: datetime) -> str:
    """Coarse session label from the UTC hour; the analyst groups losses by it."""
    hour = moment.astimezone(UTC).hour
    label = SESSIONS[0][1]
    for start, name in SESSIONS:
        if hour >= start:
            label = name
    return label


def regime_of(atr: float | None, median: float | None) -> str:
    """Coarse volatility regime, relative to the market's own recent median."""
    if atr is None or median is None or median <= 0:
        return "inconnu"
    ratio = atr / median
    if ratio >= 1.5:
        return "volatilite_haute"
    if ratio <= 0.6:
        return "volatilite_basse"
    return "volatilite_normale"


@dataclass(frozen=True)
class ClosedTradeFact:
    """One closed trade plus the little that is still knowable about its conditions."""

    trade: Trade
    signal_id: int | None
    atr: float | None


@dataclass(frozen=True)
class LabRun:
    day: datetime
    trades: int
    verdicts: tuple[LossVerdict, ...]
    proposals: tuple[Hypothesis, ...]
    escalations: tuple[Escalation, ...] = ()
    skipped: bool = False


class DailyLab:
    """One pass a day: analyse the closed trades, then research the backtest evidence."""

    def __init__(
        self,
        engine: Engine,
        *,
        analyst: TradeAnalyst,
        researcher: StrategyResearcher | None = None,
        notifier: NotifierPort | None = None,
        trigger: int = DEFAULT_TRIGGER,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._engine = engine
        self._analyst = analyst
        self._researcher = researcher
        self._notifier = notifier
        self._trigger = trigger
        self._now = now

    async def run_once(self, now: datetime) -> LabRun:
        day = day_floor(now)
        if self._already_ran(day):
            return LabRun(day=day, trades=0, verdicts=(), proposals=(), skipped=True)

        facts = self._closed_trades(day)
        verdicts = await self._analyse(facts, now)
        escalated = await self._escalate(now)
        proposals = await self._research(facts, now)
        self._mark(day, len(facts), len(proposals), now)
        log.info(
            "AI Lab: %d trade(s) examined, %d verdict(s), %d escalation(s), %d proposal(s)",
            len(facts),
            len(verdicts),
            len(escalated),
            len(proposals),
        )
        return LabRun(
            day=day,
            trades=len(facts),
            verdicts=verdicts,
            proposals=proposals,
            escalations=escalated,
        )

    # -- the trigger ----------------------------------------------------------------------

    async def _escalate(self, now: datetime) -> tuple[Escalation, ...]:
        """Ask whether any market has failed often enough to be worth rewriting.

        Read from the persisted history, not from today's trades: a motif that shows up twice
        a day for a week never looks repetitive inside one day, which is the case worth
        catching. Nothing is proposed here — the trigger only decides that a rewrite is
        justified, and says so to the operator.
        """
        history = LabStore(self._engine).recent_analyses(limit=ESCALATION_HISTORY)
        patterns = failure_patterns(history, trigger=self._trigger, now=now)
        decided = escalations(patterns, trigger=self._trigger)
        for escalation in decided:
            await self._alert(escalation.message())
        return decided

    # -- analysis -------------------------------------------------------------------------

    async def _analyse(
        self, facts: Sequence[ClosedTradeFact], now: datetime
    ) -> tuple[LossVerdict, ...]:
        verdicts: list[LossVerdict] = []
        for market in sorted({fact.trade.symbol for fact in facts}):
            market_facts = [fact for fact in facts if fact.trade.symbol == market]
            observations = observations_of(market_facts)
            if not observations:
                continue
            verdict = await self._analyst.analyse_series(
                observations,
                market=market,
                at=now,
                ref=market_facts[0].trade.strategy_ref,
            )
            verdicts.append(verdict)
            if verdict.kind.value != "normal":
                await self._alert(
                    f"🔎 AI Lab — {market} : {verdict.reason}\n"
                    "Analyse enregistrée, aucune modification automatique."
                )
        return tuple(verdicts)

    # -- research -------------------------------------------------------------------------

    async def _research(
        self, facts: Sequence[ClosedTradeFact], now: datetime
    ) -> tuple[Hypothesis, ...]:
        if self._researcher is None:
            return ()
        proposals: list[Hypothesis] = []
        for market in sorted({fact.trade.symbol for fact in facts}):
            evidence = self._evidence(market)
            if evidence is None:
                continue
            found = await self._researcher.research(evidence, at=now)
            proposals.extend(found)
            for hypothesis in found:
                await self._alert(
                    f"🧪 Nouvelle hypothèse IA — {market}\n"
                    f"{hypothesis.hypothesis}\n"
                    "Statut : proposée, à valider avant toute promotion."
                )
        return tuple(proposals)

    def _evidence(self, market: str) -> BacktestEvidence | None:
        """The latest recorded backtest for this market, with its validation history."""
        with Session(self._engine) as session:
            run = session.scalars(
                select(BacktestRunRow)
                .where(BacktestRunRow.market == market)
                .order_by(BacktestRunRow.id.desc())
                .limit(1)
            ).first()
            if run is None:
                return None
            validations = session.scalars(
                select(ValidationRunRow).where(
                    ValidationRunRow.ref == run.ref, ValidationRunRow.market == market
                )
            ).all()
        return BacktestEvidence(
            market=market,
            ref=run.ref,
            parameters={},
            metrics={key: float(value) for key, value in run.metrics.items()},
            dataset_id=run.dataset_id,
            validations=tuple(
                ValidationEvidence(stage=str(row.stage), passed=bool(row.passed))
                for row in validations
            ),
        )

    # -- persistence helpers --------------------------------------------------------------

    def _closed_trades(self, day: datetime) -> list[ClosedTradeFact]:
        end = day + timedelta(days=1)
        statement = (
            select(TradeRow, PositionRow, SignalRow, StrategyVersionRow.ref)
            .join(PositionRow, TradeRow.position_id == PositionRow.id)
            .join(OrderRow, PositionRow.order_id == OrderRow.id)
            .join(SignalRow, OrderRow.signal_id == SignalRow.id)
            .join(StrategyVersionRow, SignalRow.strategy_version_id == StrategyVersionRow.id)
            .where(TradeRow.closed_at >= day, TradeRow.closed_at < end)
            .order_by(TradeRow.closed_at)
        )
        with Session(self._engine) as session:
            rows = session.execute(statement).all()
        return [
            ClosedTradeFact(
                trade=Trade(
                    symbol=position.symbol,
                    strategy_ref=str(ref),
                    direction=position.direction,
                    timeframe=signal.timeframe,
                    mode=trade.mode,
                    opened_at=position.opened_at,
                    closed_at=trade.closed_at,
                    pnl_eur=Decimal(trade.pnl_eur),
                    risk_eur=Decimal(trade.risk_eur),
                    slippage=self._slippage(int(trade.id)),
                    spread=None,
                ),
                signal_id=int(signal.id),
                atr=_atr(signal.indicators),
            )
            for trade, position, signal, ref in rows
        ]

    def _slippage(self, trade_id: int) -> float | None:
        """The recorded fill slippage of the order that opened the trade, if any."""
        statement = (
            select(ExecutionEventRow.detail)
            .join(OrderRow, ExecutionEventRow.order_id == OrderRow.id)
            .join(PositionRow, PositionRow.order_id == OrderRow.id)
            .join(TradeRow, TradeRow.position_id == PositionRow.id)
            .where(TradeRow.id == trade_id, ExecutionEventRow.kind == "filled")
            .limit(1)
        )
        with Session(self._engine) as session:
            detail = session.scalars(statement).first()
        if not detail:
            return None
        raw = detail.get("slippage")
        try:
            return None if raw is None else float(raw)
        except (TypeError, ValueError):
            return None

    def _already_ran(self, day: datetime) -> bool:
        statement = (
            select(SystemEventRow)
            .where(SystemEventRow.kind == MARKER)
            .order_by(SystemEventRow.id.desc())
            .limit(1)
        )
        with Session(self._engine) as session:
            row = session.scalars(statement).first()
        return row is not None and row.detail.get("day") == day.isoformat()

    def _mark(self, day: datetime, trades: int, proposals: int, now: datetime) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                insert(SystemEventRow).values(
                    kind=MARKER,
                    severity=Severity.INFO,
                    detail={"day": day.isoformat(), "trades": trades, "proposals": proposals},
                    occurred_at=now,
                )
            )

    async def _alert(self, text: str) -> None:
        if self._notifier is None:
            return
        try:
            await self._notifier.send(text)
        except Exception as error:  # a notification failure never blocks the pass
            log.warning("AI Lab notice failed: %s", error)


def observations_of(facts: Sequence[ClosedTradeFact]) -> tuple[LossObservation, ...]:
    """Turn the day's closed trades into the observations the analyst reasons about."""
    known = sorted(fact.atr for fact in facts if fact.atr is not None)
    median_atr = known[len(known) // 2] if known else None
    return tuple(
        LossObservation(
            trade=fact.trade,
            context=LossContext(
                regime=regime_of(fact.atr, median_atr),
                session=session_of(fact.trade.opened_at),
                volatility=fact.atr or 0.0,
                duration=fact.trade.closed_at - fact.trade.opened_at,
                spread=fact.trade.spread,
                slippage=fact.trade.slippage,
            ),
        )
        for fact in facts
    )


def _atr(indicators: dict[str, float] | None) -> float | None:
    if not indicators:
        return None
    for key in ("atr", "ATR", "atr14"):
        value = indicators.get(key)
        if isinstance(value, int | float):
            return float(value)
    return None


__all__ = [
    "ClosedTradeFact",
    "DailyLab",
    "LabRun",
    "day_floor",
    "observations_of",
    "regime_of",
    "session_of",
]
