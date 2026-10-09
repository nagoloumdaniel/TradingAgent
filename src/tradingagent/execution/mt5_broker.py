"""Order entry on the real MT5 terminal (TASK-081, F-016, RM-012, RM-017).

The rules this module enforces, in order, for every single order:

1. RM-017 — the account the terminal is logged into is checked against the configured
   login and mode *before* the order. A mismatch raises, and the guardian halts the whole
   agent: this is never an ordinary refusal.
2. The idempotency key is looked up first. If the order already exists — accepted, refused
   or of unknown outcome — nothing is ever sent again. A lost answer is recovered by the
   hash of the key carried in the order comment, never by resending.
3. `order_check` runs for information only. It approves orders the server later refuses for
   jurisdiction (measured on this account, section 12.1): only the `order_send` return code
   counts.
4. The volume is validated against the broker's own step, minimum and maximum before the
   send: the terminal is never asked to refuse an order this layer could refuse itself.
5. The stop and the target are sent natively, then **read back from the position** with a
   bounded retry (a terminal can answer the fill before its position list shows it); once the
   retries are spent, a stop that is still not there triggers an immediate close and an alert.
6. Requested price, executed price and the gap between them are recorded, and the order,
   the fill and the position are journalled by the executor (the single writer of `orders`,
   `positions`, `executions` and `trades`, PAPER and DEMO alike). `reconcile` collects the
   closures the broker made on its own before comparing states, so a stop-out is a trade to
   journal rather than a divergence to halt on.
"""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from typing import TypeVar

from tradingagent.core.account import AccountModeMismatchError, verify_account_mode
from tradingagent.core.halt import HaltStatus
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.data.terminal import (
    MAGIC,
    DealInfo,
    PositionInfo,
    TerminalError,
    TradeRequest,
    TradingTerminal,
)
from tradingagent.execution.ports import (
    Alert,
    BrokerUnavailableError,
    LocalPosition,
    OrderRefusedError,
    TradeLog,
)
from tradingagent.execution.reconciliation import reconcile_state
from tradingagent.execution.tracking import BROKER, STOP_LOSS, STOP_MISSING, TAKE_PROFIT
from tradingagent.risk.model import (
    AccountState,
    BrokerPosition,
    ClosedPosition,
    CloseResult,
    InstrumentSpec,
    MarketQuote,
    OpenPosition,
    OrderRequest,
    OrderResult,
)

log = logging.getLogger(__name__)

# The longest comment the MetaTrader5 *Python* module accepts, measured on 2026-10-09 against
# version 5.0.6231 with `order_check` (which routes nothing to the market): 0 to 29 characters
# pass, 30 and above come back as (-2, 'Invalid "comment" argument') before any IPC. An order
# carrying 31 characters therefore never reached the broker. The MQL5 EA bridge truncates at 31
# instead (`docs/ea/protocole-pont.md`), so 29 is the narrower of the two and the one this
# module must respect. Raw output: tools/evidence-comment-probe.txt in
# `docs/research/execution-diagnostic/`.
COMMENT_LIMIT = 29
# 64 bits of SHA-256. Out of reach of this project's order volume (a few thousand a year), and
# short enough that a "ta-" + digest comment survives the EA's 31-character truncation whole.
HASH_LENGTH = 16
# What a prefix may take: the digest is never shortened to make room for one.
PREFIX_LIMIT = COMMENT_LIMIT - HASH_LENGTH - 1
DONE_RETCODES = (10008, 10009)  # placed, done
STOP_TOLERANCE = Decimal("0.01")
# The terminal can publish the fill before its position list shows it. A single stale read is
# not evidence, so the stop read-back is retried before "not confirmed" becomes "not protected".
STOP_READBACK_ATTEMPTS = 3
STOP_READBACK_PAUSE = 0.25
T = TypeVar("T")


def key_comment(idempotency_key: str, prefix: str = "ta") -> str:
    """A short, stable, non-reversible comment for one key, never longer than the terminal takes.

    The whole key goes through SHA-256 and the digest is **never truncated**: uniqueness lives
    in those 64 bits, so a shorter comment is paid for with the prefix, which is readable
    decoration. Cutting from the right instead — what this function used to do — pushed the
    comment to 31 characters whenever a caller passed a prefix (MetaTrader5 refuses 30 and
    above, so the order never left), and a longer prefix erased the key material altogether:
    every key sharing that prefix produced the very same comment.

    Two different keys share a comment only if their 64-bit digests collide. The prefix cannot
    create such a collision, it can only decorate the digest; the birthday bound sits around
    2^32 orders and this project mints a few thousand a year.
    """
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:HASH_LENGTH]
    stem = prefix[:PREFIX_LIMIT].rstrip("-")
    return f"{stem}-{digest}" if stem else digest


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MT5Broker:
    """The DEMO and LIVE executor. PAPER is refused here and handled by PaperBroker."""

    def __init__(
        self,
        terminal: TradingTerminal,
        log_: TradeLog,
        *,
        login: int,
        mode: TradingMode,
        magic: int = MAGIC,
        deviation: int = 50,
        now: Callable[[], datetime] = _utc_now,
        alert: Alert | None = None,
        status: Callable[[], HaltStatus] | None = None,
        guardian_alarm: Callable[[AccountModeMismatchError], None] | None = None,
        stop_readback_attempts: int = STOP_READBACK_ATTEMPTS,
        stop_readback_pause: float = STOP_READBACK_PAUSE,
    ) -> None:
        if mode is TradingMode.PAPER:
            raise ValueError("PAPER mode uses PaperBroker, never the terminal")
        self._terminal = terminal
        self._log = log_
        self._login = login
        self._mode = mode
        self._magic = magic
        self._deviation = deviation
        self._now = now
        self._alert = alert
        self._status = status
        self._guardian_alarm = guardian_alarm
        self._readback_attempts = stop_readback_attempts
        self._readback_pause = stop_readback_pause
        self._known: set[int] = set()
        self._deal_cursor = 0
        self._seeded = False

    # -- Broker surface ----------------------------------------------------------------

    async def initialize(self) -> None:
        """Start-up only: RM-017 is checked once so a wrong account stops the agent at once.

        The constructor performs no I/O. The terminal connection itself belongs to the
        market data client; this method only reads the account the terminal is already on.
        """
        await self._reverify()

    async def account(self) -> AccountState:
        snapshot = await self._call(self._terminal.account)
        self._verify(snapshot.login, snapshot.is_demo)
        funds = await self._call(self._terminal.funds)
        return AccountState(
            login=snapshot.login,
            is_demo=snapshot.is_demo,
            currency=snapshot.currency,
            equity=Decimal(str(funds.equity)),
            free_margin=Decimal(str(funds.free_margin)),
            balance=Decimal(str(funds.balance)),
        )

    async def instrument(self, symbol: str) -> InstrumentSpec:
        spec = await self._call(self._terminal.symbol_spec, symbol)
        if spec is None:
            raise BrokerUnavailableError(f"no contract specification for {symbol}")
        return InstrumentSpec(
            symbol=symbol,
            contract_size=Decimal(str(spec.contract_size)),
            volume_min=Decimal(str(spec.volume_min)),
            volume_step=Decimal(str(spec.volume_step)),
            volume_max=Decimal(str(spec.volume_max)),
            point=Decimal(str(spec.point)),
            stops_level=spec.stops_level,
        )

    async def quote(
        self, symbol: str, direction: Direction, stop_loss: Decimal | None
    ) -> MarketQuote:
        spec = await self.instrument(symbol)
        tick = await self._call(self._terminal.last_tick, symbol)
        if tick is None:
            raise BrokerUnavailableError(f"no quote for {symbol}")
        bid, ask = Decimal(str(tick.bid)), Decimal(str(tick.ask))
        entry = ask if direction is Direction.BUY else bid
        loss_one_lot: Decimal | None = None
        rate: Decimal | None = None
        if stop_loss is not None:
            raw = await self._call(
                self._terminal.calc_profit, direction, symbol, 1.0, float(entry), float(stop_loss)
            )
            distance = abs(entry - stop_loss)
            independent = spec.contract_size * distance
            if raw is not None and independent > 0:
                loss_one_lot = abs(Decimal(str(raw)))
                rate = loss_one_lot / independent
        margin = await self._call(self._terminal.calc_margin, direction, symbol, 1.0, float(entry))
        margin_one_lot = None if margin is None else Decimal(str(margin))
        return MarketQuote(bid, ask, loss_one_lot, margin_one_lot, rate)

    async def open_positions(self) -> tuple[OpenPosition, ...]:
        positions = await self.positions()
        return tuple(
            OpenPosition(symbol=position.symbol, volume=position.volume) for position in positions
        )

    async def positions(self) -> tuple[BrokerPosition, ...]:
        raw = await self._call(self._terminal.positions)
        self._known.update(item.ticket for item in raw)
        return tuple(self._position(item) for item in raw)

    async def place(self, request: OrderRequest) -> OrderResult:
        self._check_mode(request.mode)
        self._ensure_trading()
        await self._reverify()
        price = await self._quote_price(request.symbol, request.direction)

        existing = self._log.find_order(request.idempotency_key)
        if existing is not None:
            if existing.ticket is None and existing.state in ("sent", "error"):
                # The outcome of a previous attempt is unknown: look for our comment at the
                # broker before concluding anything. Never send a second order.
                recovered = await self._find_by_comment(request.idempotency_key)
                if recovered is not None:
                    return await self._recovered(existing.order_id, request, price, recovered)
                return OrderResult(
                    accepted=False,
                    ticket=None,
                    retcode=None,
                    requested_price=price,
                    executed_price=None,
                    slippage=None,
                    stop_present=False,
                    message="a previous attempt has an unknown outcome: reconcile, never resend",
                )
            return self._replay(existing.ticket, existing.state, existing.retcode, price)
        # The executor is the last gate before the terminal: a volume the broker cannot take
        # is a refusal here, never a request the server answers with 10014.
        problem = await self._volume_problem(request)
        if problem is not None:
            return OrderResult(
                accepted=False,
                ticket=None,
                retcode=None,
                requested_price=price,
                executed_price=None,
                slippage=None,
                stop_present=False,
                message=problem,
            )
        # The comment is the only thread back to an order whose answer was lost: `_find_by_comment`
        # looks for exactly this string. It is therefore derived from the idempotency key ALONE,
        # never from `request.comment`, which the caller fills and the search cannot recompute —
        # two different strings here and a lost answer becomes an orphaned position, or worse, a
        # second order. `key_comment(key)` is what both ends compute, by construction.
        comment = key_comment(request.idempotency_key)
        # The intent is journalled before anything leaves the process (R-05), carrying the comment
        # the broker will actually read: `orders.broker_comment` must not name a string nobody sent.
        order_id = self._log.record_request(replace(request, comment=comment), price, self._now())

        trade = TradeRequest(
            symbol=request.symbol,
            direction=request.direction,
            volume=float(request.volume),
            price=float(price),
            stop_loss=float(request.stop_loss),
            take_profit=None if request.take_profit is None else float(request.take_profit),
            comment=comment,
            deviation=self._deviation,
            magic=self._magic,
        )
        check = await self._call(self._terminal.order_check, trade)
        log.info(
            "order_check %s retcode=%s margin=%s (informative only)",
            request.symbol,
            check.retcode,
            check.margin,
        )

        try:
            raw = await self._call(self._terminal.order_send, trade)
        except TerminalError as error:
            return await self._after_send_failure(order_id, request, price, error)

        if raw is None:
            # The production adapter raises instead of returning None; the simulator still
            # returns it to stage a lost answer, and an unknown outcome is never an outcome.
            return await self._after_lost_answer(order_id, request, price)
        assert raw is not None  # noqa: S101 - narrows the simulator's optional for the type checker
        if raw.retcode not in DONE_RETCODES:
            refused = OrderResult(
                accepted=False,
                ticket=None,
                retcode=raw.retcode,
                requested_price=price,
                executed_price=None,
                slippage=None,
                stop_present=False,
                message=f"refused by the server: {raw.retcode} {raw.comment}",
            )
            self._log.record_result(order_id, refused, self._now())
            return refused

        executed = Decimal(str(raw.price)) if raw.price else price
        ticket = raw.order_ticket or raw.deal_ticket
        result = OrderResult(
            accepted=True,
            ticket=ticket,
            retcode=raw.retcode,
            requested_price=price,
            executed_price=executed,
            slippage=abs(executed - price),
            stop_present=True,
            message=raw.comment,
        )
        self._log.record_result(order_id, result, self._now())
        self._log.record_fill(
            order_id, request, result, deal_ticket=raw.deal_ticket, at=self._now()
        )
        self._known.add(ticket)
        if not await self._stop_present(ticket, request):
            return await self._close_unprotected(order_id, request, result)
        return result

    async def close(self, ticket: int, reason: str) -> CloseResult:
        await self._reverify()
        raw_positions = await self._call(self._terminal.positions)
        position = next((item for item in raw_positions if item.ticket == ticket), None)
        if position is None:
            return CloseResult(
                closed=False,
                position_ticket=ticket,
                exit_price=None,
                pnl_eur=None,
                exit_reason=reason,
                message="position is not open at the broker",
            )
        tick = await self._call(self._terminal.last_tick, position.symbol)
        if tick is None:
            raise BrokerUnavailableError(f"no quote to close {position.symbol}")
        closing = Direction.SELL if position.direction is Direction.BUY else Direction.BUY
        price = Decimal(str(tick.bid if position.direction is Direction.BUY else tick.ask))
        trade = TradeRequest(
            symbol=position.symbol,
            direction=closing,
            volume=position.volume,
            price=float(price),
            comment=key_comment(f"close:{ticket}:{reason}", "tc"),
            deviation=self._deviation,
            position_ticket=ticket,
            magic=self._magic,
        )
        try:
            raw = await self._call(self._terminal.order_send, trade)
        except TerminalError as error:
            raise BrokerUnavailableError(f"closing {ticket} failed: {error}") from error
        if raw is None or raw.retcode not in DONE_RETCODES:
            retcode = None if raw is None else raw.retcode
            return CloseResult(
                closed=False,
                position_ticket=ticket,
                exit_price=None,
                pnl_eur=None,
                exit_reason=reason,
                message=f"close refused: retcode={retcode}",
            )
        self._known.discard(ticket)
        closed = await self._closure_for(ticket, reason)
        if closed is None:
            closed = ClosedPosition(
                ticket=ticket,
                symbol=position.symbol,
                exit_price=price,
                pnl_eur=Decimal(0),
                exit_reason=reason,
                closed_at=self._now(),
            )
        self._log.record_closures((closed,), self._now())
        return CloseResult(
            closed=True,
            position_ticket=ticket,
            exit_price=closed.exit_price,
            pnl_eur=closed.pnl_eur,
            exit_reason=reason,
            message=f"closed at {closed.exit_price}",
        )

    async def collect_closures(self) -> tuple[ClosedPosition, ...]:
        """Whatever the broker closed since the last call, whatever the candles are doing.

        The candle poll notices a stop-out up to a bar late, and reconciliation notices it
        within twenty seconds. Called by the loop *before* it reconciles, this is what lets
        the closure reach the operator as a message, the telemetry as POSITION_CLOSED and
        the daily bucket as a trade — instead of existing only in the ledger.
        """
        return await self._collect_closures()

    async def on_candle(self, symbol: str, candle: Candle) -> tuple[ClosedPosition, ...]:
        return await self._collect_closures()

    async def on_tick(self, symbol: str, bid: Decimal, ask: Decimal) -> tuple[ClosedPosition, ...]:
        return await self._collect_closures()

    # -- internals ---------------------------------------------------------------------

    def _replay(
        self, ticket: int | None, state: str, retcode: int | None, price: Decimal
    ) -> OrderResult:
        """An order already recorded for this key: never a second one."""
        if ticket is not None:
            return OrderResult(
                accepted=True,
                ticket=ticket,
                retcode=retcode,
                requested_price=price,
                executed_price=None,
                slippage=None,
                stop_present=True,
                message="already sent under this idempotency key",
            )
        if state == "error":
            return OrderResult(
                accepted=False,
                ticket=None,
                retcode=None,
                requested_price=price,
                executed_price=None,
                slippage=None,
                stop_present=False,
                message="a previous attempt has an unknown outcome: reconcile before any retry",
            )
        return OrderResult(
            accepted=False,
            ticket=None,
            retcode=retcode,
            requested_price=price,
            executed_price=None,
            slippage=None,
            stop_present=False,
            message="already refused under this idempotency key",
        )

    async def _after_send_failure(
        self, order_id: int, request: OrderRequest, price: Decimal, error: TerminalError
    ) -> OrderResult:
        position = await self._find_by_comment(request.idempotency_key)
        if position is not None:
            return await self._recovered(order_id, request, price, position)
        result = OrderResult(
            accepted=False,
            ticket=None,
            retcode=None,
            requested_price=price,
            executed_price=None,
            slippage=None,
            stop_present=False,
            message=f"send failed with an unknown outcome: {error}",
        )
        self._log.record_result(order_id, result, self._now())
        return result

    async def _after_lost_answer(
        self, order_id: int, request: OrderRequest, price: Decimal
    ) -> OrderResult:
        position = await self._find_by_comment(request.idempotency_key)
        if position is not None:
            return await self._recovered(order_id, request, price, position)
        result = OrderResult(
            accepted=False,
            ticket=None,
            retcode=None,
            requested_price=price,
            executed_price=None,
            slippage=None,
            stop_present=False,
            message="answer lost: reconcile by comment before any retry, never resend",
        )
        self._log.record_result(order_id, result, self._now())
        return result

    async def _recovered(
        self, order_id: int, request: OrderRequest, price: Decimal, position: PositionInfo
    ) -> OrderResult:
        """The order did reach the server: adopt its ticket instead of sending another."""
        executed = Decimal(str(position.price_open))
        result = OrderResult(
            accepted=True,
            ticket=position.ticket,
            retcode=None,
            requested_price=price,
            executed_price=executed,
            slippage=abs(executed - price),
            stop_present=bool(position.stop_loss),
            message="recovered by idempotency comment, no second order sent",
        )
        self._log.record_result(order_id, result, self._now())
        self._log.record_fill(
            order_id,
            request,
            result,
            deal_ticket=await self._entry_deal_ticket(position.ticket),
            at=self._now(),
        )
        self._known.add(position.ticket)
        if not result.stop_present:
            return await self._close_unprotected(order_id, request, result)
        return result

    async def _close_unprotected(
        self, order_id: int, request: OrderRequest, result: OrderResult
    ) -> OrderResult:
        ticket = result.ticket
        if ticket is None:
            raise BrokerUnavailableError("cannot close an order without a ticket")
        self._alarm(
            f"position {ticket} {request.symbol} opened without its stop-loss: closing it now "
            "(TASK-081)"
        )
        closed = await self.close(ticket, STOP_MISSING)
        outcome = "ok" if closed.closed else "failed"
        final = OrderResult(
            accepted=result.accepted,
            ticket=ticket,
            retcode=result.retcode,
            requested_price=result.requested_price,
            executed_price=result.executed_price,
            slippage=result.slippage,
            stop_present=False,
            message=f"stop-loss absent after execution; close={outcome}",
        )
        self._log.record_result(order_id, final, self._now())
        return final

    async def _stop_present(self, ticket: int, request: OrderRequest) -> bool:
        """Read the stop back from the position, tolerating a terminal cache that lags.

        The terminal can answer the fill before its position list shows it, so one stale read
        is not evidence: `not confirmed` only becomes `not protected` after the bounded
        retries. A stop present at another price is a real difference and answers at once.
        """
        for attempt in range(self._readback_attempts):
            if attempt:
                await asyncio.sleep(self._readback_pause)
            positions = await self._call(self._terminal.positions, request.symbol)
            for position in positions:
                if position.ticket != ticket:
                    continue
                if not position.stop_loss:
                    break
                spec = await self.instrument(request.symbol)
                tolerance = max(spec.point * 2, STOP_TOLERANCE)
                return abs(Decimal(str(position.stop_loss)) - request.stop_loss) <= tolerance
        return False  # gone or unreadable: not confirmed is not confirmed

    async def _volume_problem(self, request: OrderRequest) -> str | None:
        """Why the broker cannot take that volume, or None when it can.

        The risk engine already rounds the size down to the lot step (`risk/sizing.py`), but
        this executor also answers paths that do not go through it, and MT5 rejects a volume
        that is not a multiple of `volume_step`. Refusing here keeps the order out of the
        terminal instead of collecting a 10014 and a phantom position.
        """
        spec = await self.instrument(request.symbol)
        volume = request.volume
        if volume < spec.volume_min or volume > spec.volume_max:
            return (
                f"volume {volume} is outside the broker limits "
                f"{spec.volume_min}..{spec.volume_max} for {request.symbol}: refused before sending"
            )
        whole = (volume / spec.volume_step).to_integral_value(
            rounding=ROUND_FLOOR
        ) * spec.volume_step
        if whole != volume:
            return (
                f"volume {volume} is not a multiple of the lot step {spec.volume_step} "
                f"for {request.symbol}: refused before sending"
            )
        return None

    async def _find_by_comment(self, idempotency_key: str) -> PositionInfo | None:
        """The position `place` opened for this key, matched by the comment it sent.

        This must compute the very string `place` sends — both call `key_comment(key)` with the
        default prefix for exactly that reason. The caller's `OrderRequest.comment` is not part
        of the formula: the search only ever holds the key, so anything else would be a comment
        that cannot be recomputed, and a lost answer that can never be reconciled.
        """
        comment = key_comment(idempotency_key)
        positions = await self._call(self._terminal.positions)
        for position in positions:
            if position.comment == comment:
                return position
        return None

    async def _collect_closures(self) -> tuple[ClosedPosition, ...]:
        await self._seed_known()
        raw_positions = await self._call(self._terminal.positions)
        live = {position.ticket for position in raw_positions}
        self._known.update(live)
        missing = {ticket for ticket in self._known if ticket not in live}
        if not missing:
            return ()
        # One read for the whole batch. Reading once per ticket advanced the cursor over the
        # deals of the tickets still waiting, and a closure skipped that way is lost for good:
        # the position stays open locally and the next reconciliation halts the agent.
        deals = await self._unconsumed_deals()
        closed: list[ClosedPosition] = []
        for ticket in sorted(missing):
            item = self._closed_from(deals, ticket, None)
            if item is not None:
                closed.append(item)
        self._consume(deals)
        if closed:
            self._log.record_closures(closed, self._now())
        return tuple(closed)

    async def _unconsumed_deals(self) -> tuple[DealInfo, ...]:
        """The deals the terminal has not handed over yet. Reading moves nothing."""
        return await self._call(self._terminal.deals_since, self._deal_cursor)

    def _consume(self, deals: tuple[DealInfo, ...]) -> None:
        """Move the cursor only once the whole batch has been looked at."""
        if deals:
            self._deal_cursor = max(self._deal_cursor, max(deal.server_epoch for deal in deals))

    async def _closure_for(self, ticket: int, reason: str | None) -> ClosedPosition | None:
        return self._closed_from(await self._unconsumed_deals(), ticket, reason)

    def _closed_from(
        self, deals: tuple[DealInfo, ...], ticket: int, reason: str | None
    ) -> ClosedPosition | None:
        closed_deals = [
            deal for deal in deals if not deal.is_entry and deal.position_ticket == ticket
        ]
        if not closed_deals:
            return None
        self._known.discard(ticket)
        last = closed_deals[-1]
        pnl = sum((Decimal(str(deal.profit)) for deal in closed_deals), Decimal(0))
        exit_price = Decimal(str(last.price))
        local = self._local(ticket)
        return ClosedPosition(
            ticket=ticket,
            symbol=last.symbol,
            exit_price=exit_price,
            pnl_eur=pnl,
            exit_reason=reason or self._reason_for(ticket, exit_price),
            closed_at=datetime.fromtimestamp(last.server_epoch, tz=UTC),
            signal_id=None if local is None else local.signal_id,
        )

    async def _entry_deal_ticket(self, ticket: int) -> int:
        """The deal that opened the position, when the terminal still reports it.

        `positions_get` answers a *position* ticket, which is not a deal ticket: writing it in
        `executions.broker_deal_ticket` would put a foreign identifier in the audit trail.
        """
        for deal in await self._unconsumed_deals():
            if deal.is_entry and deal.position_ticket == ticket:
                return deal.ticket
        log.warning(
            "no entry deal reported for position %s: the position ticket is recorded", ticket
        )
        return ticket

    def _reason_for(self, ticket: int, exit_price: Decimal) -> str:
        position = self._local(ticket)
        if position is None:
            return BROKER
        if position.stop_loss is not None and abs(position.stop_loss - exit_price) <= (
            STOP_TOLERANCE
        ):
            return STOP_LOSS
        if position.take_profit is not None and abs(position.take_profit - exit_price) <= (
            STOP_TOLERANCE
        ):
            return TAKE_PROFIT
        return BROKER

    def _local(self, ticket: int) -> LocalPosition | None:
        return self._log.position_for_ticket(ticket)

    async def _seed_known(self) -> None:
        if self._seeded:
            return
        self._seeded = True
        for position in self._log.local_positions(self._mode):
            self._known.add(position.ticket)

    async def reconcile(self) -> tuple[str, ...]:
        """RM-014: compare the local state to the account's, describe every difference.

        It never corrects a divergence: a divergence means the operator must look, so the
        caller halts the agent. Both directions are checked — a local position the broker
        does not have, and a broker position the base does not know.

        A position the broker closed on its own (its stop was hit) is *collected* from the
        deals before the comparison. F-017 asks for a transaction subscription; the candle
        poll is what exists, so without this the very first stop-out would be reported as a
        divergence and halt the whole agent — globally, until an operator resumes it — for a
        stop that simply did its job.
        """
        for closed in await self._collect_closures():
            self._alarm(
                f"position {closed.ticket} {closed.symbol} closed by the broker "
                f"({closed.exit_reason}): {closed.pnl_eur:+.2f} EUR — collected at reconciliation"
            )
        positions = await self.positions()
        return await asyncio.to_thread(reconcile_state, self._log, self._mode, positions)

    async def _quote_price(self, symbol: str, direction: Direction) -> Decimal:
        tick = await self._call(self._terminal.last_tick, symbol)
        if tick is None:
            raise BrokerUnavailableError(f"no quote for {symbol}")
        return Decimal(str(tick.ask if direction is Direction.BUY else tick.bid))

    async def _reverify(self) -> None:
        snapshot = await self._call(self._terminal.account)
        self._verify(snapshot.login, snapshot.is_demo)

    def _verify(self, login: int, is_demo: bool) -> None:
        try:
            verify_account_mode(login, is_demo, self._login, self._mode)
        except AccountModeMismatchError as error:
            if self._guardian_alarm is not None:
                self._guardian_alarm(error)
            self._alarm(f"RM-017 violation, execution stops: {error}")
            raise

    def _ensure_trading(self) -> None:
        if self._status is None:
            return
        status = self._status()
        if status.halted:
            raise OrderRefusedError(f"trading is halted: {'; '.join(status.reasons)}")

    def _check_mode(self, mode: TradingMode) -> None:
        if mode is not self._mode:
            raise OrderRefusedError(
                f"order carries mode {mode}, this executor runs in {self._mode}: refused"
            )

    def _alarm(self, message: str) -> None:
        log.critical("%s", message)
        if self._alert is None:
            return
        try:
            self._alert(message)
        except Exception:
            log.exception("alert could not be delivered")

    @staticmethod
    async def _call(function: Callable[..., T], *args: object) -> T:
        return await asyncio.to_thread(function, *args)

    def _position(self, item: PositionInfo) -> BrokerPosition:
        return BrokerPosition(
            ticket=item.ticket,
            symbol=item.symbol,
            direction=item.direction,
            volume=Decimal(str(item.volume)),
            open_price=Decimal(str(item.price_open)),
            stop_loss=Decimal(str(item.stop_loss)) if item.stop_loss else None,
            take_profit=Decimal(str(item.take_profit)) if item.take_profit else None,
            mode=self._mode,
        )


__all__ = ["BROKER", "COMMENT_LIMIT", "MT5Broker", "key_comment"]
