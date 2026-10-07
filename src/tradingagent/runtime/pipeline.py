"""From a recorded signal to a notified, optionally executed order (F-009 to F-017).

Order of operations, none of them optional:

1. the AI filter sees the candidate and can only block it (C-002, RM-010);
2. the risk engine judges the situation and writes its decision, refusals included
   (TASK-035: it moves the signal to VALIDATED or RISK_REJECTED itself);
3. the operator is told, and the signal reaches SENT only once the message left;
4. in an executing mode, the order is sent and the broker's answer is verified — an
   accepted order without its stop-loss is closed immediately and alerted (EF-015).

An account that contradicts the mode is never a refusal: it halts the agent (RM-017).
"""

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Engine

from tradingagent.ai.layer import ReviewContext, ReviewOutcome
from tradingagent.config.agent import RiskConfig
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import ExecutionEventKind, RiskOutcome, Severity, SignalState
from tradingagent.data.market_calendar import MarketCalendar, SlotStatus
from tradingagent.notify.signal_template import (
    DIRECTION_LABELS,
    SignalNotice,
    render_signal_message,
)
from tradingagent.notify.trade_messages import (
    PositionOpened,
    render_position_opened,
)
from tradingagent.risk.checks import RiskContext
from tradingagent.risk.engine import AccountModeMismatchError, RiskDecision, decide
from tradingagent.risk.model import (
    OpenPosition,
    OrderRequest,
    OrderResult,
    TradeIntent,
    limits_for,
)
from tradingagent.runtime.portfolio import PortfolioBuilder
from tradingagent.runtime.ports import AiReviewerPort, BrokerPort, NotifierPort
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.risk_decisions import RiskDecisionStore
from tradingagent.storage.signals import (
    SignalDetail,
    SignalRepository,
    get_signal,
    pending_notifications,
    transition,
)
from tradingagent.storage.telemetry import ExecutionEventStore

log = logging.getLogger(__name__)

# Refusals worth waking the operator for: the ones that protect capital or scope.
NOTABLE_REFUSALS = frozenset(
    {"daily_loss", "weekly_loss", "drawdown", "live_eligibility", "margin", "trading_hours"}
)
SEND_ATTEMPTS = 3
SEND_BACKOFF_SECONDS = 2.0
EXECUTING_MODES = frozenset({TradingMode.PAPER, TradingMode.DEMO, TradingMode.LIVE})


@dataclass(frozen=True)
class ProcessOutcome:
    signal_id: int
    kind: str
    detail: str = ""


def order_comment(idempotency_key: str) -> str:
    """Broker comments are limited to 31 characters: keep a digest of the unique key."""
    return "ta-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]


class SignalPipeline:
    def __init__(
        self,
        *,
        engine: Engine,
        halts: HaltStore,
        broker: BrokerPort,
        notifier: NotifierPort,
        portfolio: PortfolioBuilder,
        risk_config: RiskConfig,
        expected_login: int,
        mode: TradingMode,
        calendar_for: Callable[[str], MarketCalendar | None],
        ai: AiReviewerPort | None = None,
        send_attempts: int = SEND_ATTEMPTS,
        send_backoff: float = SEND_BACKOFF_SECONDS,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        now: Callable[[], datetime],
    ) -> None:
        self._engine = engine
        self._halts = halts
        self._broker = broker
        self._notifier = notifier
        self._portfolio = portfolio
        self._risk_config = risk_config
        self._expected_login = expected_login
        self._mode = mode
        self._calendar_for = calendar_for
        self._ai = ai
        self._send_attempts = send_attempts
        self._send_backoff = send_backoff
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._now = now
        self._decisions = RiskDecisionStore(engine)
        self._telemetry = ExecutionEventStore(engine)

    @property
    def execution_enabled(self) -> bool:
        return self._mode in EXECUTING_MODES

    async def process(self, signal_id: int) -> ProcessOutcome:
        detail = get_signal(self._engine, signal_id)
        if detail is None:
            return ProcessOutcome(signal_id, "missing", "unknown signal")
        if detail.state is not SignalState.CANDIDATE:
            return ProcessOutcome(signal_id, "already_processed", str(detail.state))
        now = self._now()

        review = await self._review(detail, now)
        if review is not None and review.blocks_signal:
            transition(
                self._engine,
                signal_id,
                SignalState.EXPIRED,
                now,
                f"ai_filter {review.ai_filter}: {review.reason}",
            )
            self._event(
                "ai_filter_blocked",
                Severity.WARNING,
                {"signal_id": signal_id, "ai_filter": str(review.ai_filter)},
                now,
            )
            return ProcessOutcome(signal_id, "ai_blocked", review.reason)

        decision = await self._judge(detail, now)
        if decision is None:
            return ProcessOutcome(signal_id, "account_mismatch", "RM-017 halt issued")
        if decision.outcome is RiskOutcome.REFUSED:
            if NOTABLE_REFUSALS & {check.name for check in decision.refusals}:
                await self._notifier.send(_refusal_message(detail, decision))
            return ProcessOutcome(signal_id, "refused", decision.reason)

        if not await self._deliver(detail, now):
            self._event(
                "notification_pending",
                Severity.CRITICAL,
                {"signal_id": signal_id, "symbol": detail.symbol},
                now,
            )
            return ProcessOutcome(signal_id, "notification_pending", "Telegram unreachable")
        transition(self._engine, signal_id, SignalState.SENT, now, "signal sent to Telegram")

        if not self.execution_enabled:
            return ProcessOutcome(signal_id, "notified")
        return await self._execute(detail, decision, now)

    async def retry_pending(self, limit: int = 50) -> int:
        """Re-send the signals whose message failed while Telegram was down (F-013)."""
        sent = 0
        for signal_id in pending_notifications(self._engine, limit):
            detail = get_signal(self._engine, signal_id)
            if detail is None or detail.state is not SignalState.VALIDATED:
                continue
            if await self._deliver(detail, self._now()):
                transition(
                    self._engine, signal_id, SignalState.SENT, self._now(), "signal sent on retry"
                )
                sent += 1
        return sent

    async def _review(self, detail: SignalDetail, now: datetime) -> ReviewOutcome | None:
        if self._ai is None:
            return None
        context = ReviewContext(
            symbol=detail.symbol,
            timeframe=detail.timeframe,
            strategy_ref=detail.strategy_ref,
            direction=detail.direction,
            observed_price=detail.observed_price,
            entry_low=detail.entry_low,
            entry_high=detail.entry_high,
            stop_loss=detail.stop_loss,
            take_profits=detail.take_profits,
            indicators=detail.indicators,
            market_state=str(self._market_state(detail.symbol, now)),
        )
        return await self._ai.review(context, now, detail.id, detail.ai_filter)

    async def _judge(self, detail: SignalDetail, now: datetime) -> RiskDecision | None:
        halt = self._halts.status()
        limits = limits_for(detail.mode, self._risk_config)
        account = await self._broker.account()
        spec = await self._broker.instrument(detail.symbol)
        stop = Decimal(str(detail.stop_loss))
        quote = await self._broker.quote(detail.symbol, detail.direction, stop)
        positions = await self._broker.open_positions()
        today = self._portfolio.trades_opened_since(
            now.replace(hour=0, minute=0, second=0, microsecond=0), now
        )
        state = self._portfolio.build(
            account=account,
            open_positions=positions,
            trades_today=today,
            at=now,
            open_exposure_eur=await self._open_exposure(positions),
        )
        context = RiskContext(
            intent=TradeIntent(
                signal_id=detail.id,
                symbol=detail.symbol,
                direction=detail.direction,
                entry_low=Decimal(str(detail.entry_low)),
                entry_high=Decimal(str(detail.entry_high)),
                stop_loss=stop,
            ),
            account=account,
            spec=spec,
            quote=quote,
            portfolio=state,
            market=self._market_state(detail.symbol, now),
            limits=limits,
            now=now,
            halt=halt,
        )
        try:
            decision = decide(context, self._expected_login)
        except AccountModeMismatchError as error:
            log.critical("account does not match the mode: %s", error)
            self._event("account_mismatch", Severity.CRITICAL, {"detail": str(error)}, now)
            return None
        self._decisions.record(detail.id, decision, now)
        return decision

    async def _deliver(self, detail: SignalDetail, now: datetime) -> bool:
        notice = SignalNotice(
            ref=detail.idempotency_key,
            symbol=detail.symbol,
            direction=detail.direction,
            observed_price=detail.observed_price,
            entry_low=detail.entry_low,
            entry_high=detail.entry_high,
            stop_loss=detail.stop_loss,
            take_profits=detail.take_profits,
            timeframe=detail.timeframe,
            strategy_ref=detail.strategy_ref,
            generated_at=detail.generated_at,
            expires_at=detail.expires_at,
            mode=detail.mode,
            market_state=str(self._market_state(detail.symbol, now)),
            reason=detail.reason,
        )
        message = render_signal_message(notice)
        for attempt in range(self._send_attempts):
            try:
                if await self._notifier.send(message, parse_mode="HTML"):
                    return True
            except Exception as error:  # a notifier must never take the loop down
                log.warning("notification attempt %d failed: %s", attempt + 1, error)
            if attempt < self._send_attempts - 1:
                await self._sleep(self._send_backoff * (attempt + 1))
        return False

    async def _execute(
        self, detail: SignalDetail, decision: RiskDecision, now: datetime
    ) -> ProcessOutcome:
        sizing = decision.sizing
        if sizing is None:
            return ProcessOutcome(detail.id, "refused", "no size to execute")
        request = OrderRequest(
            signal_id=detail.id,
            idempotency_key=detail.idempotency_key,
            symbol=detail.symbol,
            direction=detail.direction,
            volume=sizing.volume,
            stop_loss=Decimal(str(detail.stop_loss)),
            take_profit=(Decimal(str(detail.take_profits[0])) if detail.take_profits else None),
            mode=detail.mode,
            comment=order_comment(detail.idempotency_key),
        )
        transition(self._engine, detail.id, SignalState.ACCEPTED, now, "accepted automatically")
        transition(self._engine, detail.id, SignalState.ORDER_SENT, now, request.comment)
        started = time.monotonic()
        self._telemetry.record(
            ExecutionEventKind.ORDER_SENT,
            detail.symbol,
            {
                "volume": str(request.volume),
                "stop_loss": str(request.stop_loss),
                "take_profit": None if request.take_profit is None else str(request.take_profit),
                "mode": detail.mode.value,
                "comment": request.comment,
            },
            now,
            signal_id=detail.id,
        )
        # The executor owns the orders table: it records the request before reaching the
        # broker, so a lost answer is reconciled by idempotency key, never resent blindly.
        result = await self._broker.place(request)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if not result.accepted:
            transition(self._engine, detail.id, SignalState.ORDER_REJECTED, now, result.message)
            self._telemetry.record(
                ExecutionEventKind.ORDER_REJECTED,
                detail.symbol,
                {
                    "retcode": result.retcode,
                    "detail": result.message,
                    "elapsed_ms": elapsed_ms,
                },
                now,
                signal_id=detail.id,
            )
            self._event(
                "order_rejected",
                Severity.WARNING,
                {"signal_id": detail.id, "retcode": result.retcode, "detail": result.message},
                now,
            )
            return ProcessOutcome(detail.id, "order_rejected", result.message)

        transition(
            self._engine, detail.id, SignalState.ORDER_ACCEPTED, now, f"ticket {result.ticket}"
        )
        self._telemetry.record(
            ExecutionEventKind.FILLED,
            detail.symbol,
            {
                "ticket": result.ticket,
                "requested_price": str(result.requested_price),
                "executed_price": (
                    None if result.executed_price is None else str(result.executed_price)
                ),
                "slippage": None if result.slippage is None else str(result.slippage),
                "elapsed_ms": elapsed_ms,
            },
            now,
            signal_id=detail.id,
        )
        if result.ticket is None or result.executed_price is None:
            transition(self._engine, detail.id, SignalState.ERROR, now, "no ticket returned")
            return ProcessOutcome(detail.id, "order_error", "accepted without a ticket")
        if not result.stop_present:
            # EF-015: a position without its stop is closed at once, never left drifting.
            closed = await self._broker.close(result.ticket, "stop missing after execution")
            transition(
                self._engine, detail.id, SignalState.ERROR, now, "stop missing after execution"
            )
            self._telemetry.record(
                ExecutionEventKind.STOP_MISSING,
                detail.symbol,
                {"ticket": result.ticket, "closed": closed.closed, "elapsed_ms": elapsed_ms},
                now,
                signal_id=detail.id,
            )
            self._event(
                "stop_missing",
                Severity.CRITICAL,
                {"signal_id": detail.id, "ticket": result.ticket, "closed": closed.closed},
                now,
            )
            return ProcessOutcome(detail.id, "stop_missing", "position closed immediately")

        transition(self._engine, detail.id, SignalState.POSITION_OPEN, now, "position opened")
        self._telemetry.record(
            ExecutionEventKind.POSITION_OPENED,
            detail.symbol,
            {"ticket": result.ticket, "elapsed_ms": elapsed_ms},
            now,
            signal_id=detail.id,
        )
        await self._notify_open(detail, request, result)
        return ProcessOutcome(detail.id, "executed", f"ticket {result.ticket}")

    async def _notify_open(
        self, detail: SignalDetail, request: OrderRequest, result: OrderResult
    ) -> None:
        """The operator asked for the position's essentials only (cahier v3, §37)."""
        if result.ticket is None or result.executed_price is None:
            return
        notice = PositionOpened(
            symbol=detail.symbol,
            direction=detail.direction,
            volume=request.volume,
            entry_price=result.executed_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            strategy_ref=detail.strategy_ref,
            mode=detail.mode,
            ticket=result.ticket,
        )
        try:
            await self._notifier.send(render_position_opened(notice), parse_mode="HTML")
        except Exception as error:  # a notification failure never unwinds an opened position
            log.warning("position-opened notice failed: %s", error)

    async def _open_exposure(self, positions: tuple[OpenPosition, ...]) -> Decimal | None:
        """Total notional already committed, in EUR (§21).

        `OpenPosition` carries only a symbol and a volume, so the contract size and the
        price have to come from the broker. Returning None on any doubt is deliberate: the
        risk engine refuses an order it cannot measure, which is the safe direction.
        """
        if not positions:
            return Decimal(0)
        total = Decimal(0)
        try:
            for position in positions:
                spec = await self._broker.instrument(position.symbol)
                quote = await self._broker.quote(position.symbol, Direction.BUY, None)
                rate = quote.profit_to_eur if quote.profit_to_eur is not None else Decimal(1)
                total += (
                    position.volume * spec.contract_size * quote.entry_price(Direction.BUY) * rate
                )
        except Exception as error:
            log.warning("open exposure not measurable: %s", error)
            return None
        return total

    def _market_state(self, symbol: str, now: datetime) -> SlotStatus:
        calendar = self._calendar_for(symbol)
        return calendar.status_at(now) if calendar is not None else SlotStatus.UNCERTAIN

    def _event(
        self, kind: str, severity: Severity, detail: dict[str, object], at: datetime
    ) -> None:
        SignalRepository(self._engine).record_system_event(kind, severity, detail, at)


def _refusal_message(detail: SignalDetail, decision: RiskDecision) -> str:
    """Plain text (this one is sent without a parse mode): what was refused, why, and what
    the agent did about it, so the operator never reads a bare "erreur"."""
    causes = ", ".join(check.name for check in decision.refusals)
    return (
        f"⛔ Refus risque · {detail.symbol} {DIRECTION_LABELS[detail.direction]}\n"
        f"\n"
        f"Contrôles refusés : {causes}\n"
        f"Motif : {decision.reason}\n"
        f"\n"
        f"Réf : {detail.idempotency_key}\n"
        f"Aucun ordre n'a été envoyé."
    )
