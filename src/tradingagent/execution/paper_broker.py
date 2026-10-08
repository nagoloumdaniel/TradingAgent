"""The paper executor (TASK-070, F-015): real market flow, no exposure, no order path.

Paper trading exists to produce comparable numbers, not to look plausible. So it fills on
the *observed* spread, applies an explicit slippage hypothesis, and its fills land in the
**same `executions` table** as the real ones, told apart by `mode=PAPER` alone; the runtime
writes `orders`, `positions` and `trades` for both executors. The analytics code therefore
reads one stream and cannot tell the difference.

It is wired to a `Terminal`, never to a `TradingTerminal`: the type carries only market-data
reads, so no execution call can be emitted even by accident. The test in
`tests/execution/test_paper_broker.py` proves it on a terminal that records every call.

Stops and targets are simulated the way a broker would honour them: stop first, then target,
and through the bar when a candle is given.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal

from tradingagent.core.halt import HaltStatus
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.data.terminal import Terminal
from tradingagent.execution.ports import (
    Alert,
    BrokerUnavailableError,
    OrderRefusedError,
    TradeLog,
)
from tradingagent.execution.reconciliation import reconcile_state
from tradingagent.execution.simulator import RETCODE_DONE
from tradingagent.execution.tracking import RULE, STOP_LOSS, TAKE_PROFIT
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
ONE = Decimal(1)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _PaperPosition:
    __slots__ = (
        "direction",
        "opened_at",
        "price_open",
        "signal_id",
        "stop_loss",
        "symbol",
        "take_profit",
        "ticket",
        "volume",
    )

    def __init__(
        self,
        ticket: int,
        symbol: str,
        direction: Direction,
        volume: Decimal,
        price_open: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal | None,
        signal_id: int,
        opened_at: datetime,
    ) -> None:
        self.ticket = ticket
        self.symbol = symbol
        self.direction = direction
        self.volume = volume
        self.price_open = price_open
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.signal_id = signal_id
        self.opened_at = opened_at


class PaperBroker:
    """Simulated executor over the production flow. Never sends anything anywhere."""

    def __init__(
        self,
        terminal: Terminal,
        log_: TradeLog,
        *,
        specs: Mapping[str, InstrumentSpec],
        account: AccountState,
        mode: TradingMode = TradingMode.PAPER,
        slippage_points: Decimal = Decimal(2),
        leverage: Decimal = Decimal(30),
        profit_to_eur: Decimal = ONE,
        now: Callable[[], datetime] = _utc_now,
        alert: Alert | None = None,
        status: Callable[[], HaltStatus] | None = None,
    ) -> None:
        if mode not in (TradingMode.OBSERVATION, TradingMode.SIGNAL, TradingMode.PAPER):
            raise ValueError(f"{mode} is a real-money mode: use MT5Broker")
        self._terminal = terminal
        self._log = log_
        self._specs = dict(specs)
        self._account = account
        self._mode = mode
        self._slippage_points = slippage_points
        self._leverage = leverage
        self._profit_to_eur = profit_to_eur
        self._now = now
        self._alert = alert
        self._status = status
        self._positions: dict[int, _PaperPosition] = {}
        self._next_ticket = 1

    # -- Broker surface ----------------------------------------------------------------

    async def initialize(self) -> None:
        """Restore the open positions the journal kept, so a restart resumes tracking.

        No market call and no order call: only `local_positions` on the ledger.
        """
        for position in self._log.local_positions(self._mode):
            self._positions[position.ticket] = _PaperPosition(
                ticket=position.ticket,
                symbol=position.symbol,
                direction=position.direction,
                volume=position.volume,
                price_open=position.open_price,
                stop_loss=position.stop_loss or Decimal(0),
                take_profit=position.take_profit,
                signal_id=position.signal_id,
                opened_at=self._now(),
            )
            self._next_ticket = max(self._next_ticket, position.ticket + 1)

    async def account(self) -> AccountState:
        realized = await asyncio.to_thread(self._log.realized_pnl, self._mode)
        floating = await asyncio.to_thread(self._floating)
        equity = self._account.equity + realized + floating
        margin = await asyncio.to_thread(self._margin)
        return AccountState(
            login=self._account.login,
            is_demo=True,
            currency=self._account.currency,
            equity=equity,
            free_margin=equity - margin,
            balance=self._account.equity + realized,
        )

    async def instrument(self, symbol: str) -> InstrumentSpec:
        spec = self._specs.get(symbol)
        if spec is None:
            raise BrokerUnavailableError(f"no paper contract specification for {symbol}")
        return spec

    async def quote(
        self, symbol: str, direction: Direction, stop_loss: Decimal | None
    ) -> MarketQuote:
        spec = await self.instrument(symbol)
        tick = await asyncio.to_thread(self._terminal.last_tick, symbol)
        if tick is None:
            raise BrokerUnavailableError(f"no quote for {symbol}")
        bid, ask = Decimal(str(tick.bid)), Decimal(str(tick.ask))
        entry = ask if direction is Direction.BUY else bid
        loss_one_lot = (
            None
            if stop_loss is None
            else spec.contract_size * abs(entry - stop_loss) * self._profit_to_eur
        )
        margin_one_lot = spec.contract_size * entry / self._leverage
        return MarketQuote(bid, ask, loss_one_lot, margin_one_lot, self._profit_to_eur)

    async def open_positions(self) -> tuple[OpenPosition, ...]:
        return tuple(
            OpenPosition(symbol=position.symbol, volume=position.volume)
            for position in self._positions.values()
        )

    async def positions(self) -> tuple[BrokerPosition, ...]:
        return tuple(
            BrokerPosition(
                ticket=position.ticket,
                symbol=position.symbol,
                direction=position.direction,
                volume=position.volume,
                open_price=position.price_open,
                stop_loss=position.stop_loss,
                take_profit=position.take_profit,
                mode=self._mode,
            )
            for position in self._positions.values()
        )

    async def reconcile(self) -> tuple[str, ...]:
        """RM-014: describe every difference between the paper ledger and its positions."""
        positions = await self.positions()
        return await asyncio.to_thread(reconcile_state, self._log, self._mode, positions)

    async def place(self, request: OrderRequest) -> OrderResult:
        self._check_mode(request.mode)
        self._ensure_trading()
        spec = await self.instrument(request.symbol)
        tick = await asyncio.to_thread(self._terminal.last_tick, request.symbol)
        if tick is None:
            raise BrokerUnavailableError(f"no quote for {request.symbol}")
        requested = (
            Decimal(str(tick.ask)) if request.direction is Direction.BUY else Decimal(str(tick.bid))
        )
        slip = self._slippage_points * spec.point
        executed = requested + slip if request.direction is Direction.BUY else requested - slip

        existing = self._log.find_order(request.idempotency_key)
        if existing is not None:
            return OrderResult(
                accepted=existing.ticket is not None,
                ticket=existing.ticket,
                retcode=existing.retcode,
                requested_price=requested,
                executed_price=None,
                slippage=None,
                stop_present=existing.ticket is not None,
                message="already recorded under this idempotency key",
            )
        order_id = self._log.record_request(request, requested, self._now())

        ticket = self._next_ticket
        self._next_ticket += 1
        result = OrderResult(
            accepted=True,
            ticket=ticket,
            retcode=RETCODE_DONE,
            requested_price=requested,
            executed_price=executed,
            slippage=abs(executed - requested),
            stop_present=True,
            message="paper fill",
        )
        self._log.record_result(order_id, result, self._now())
        self._log.record_fill(order_id, request, result, deal_ticket=ticket * 10, at=self._now())
        self._positions[ticket] = _PaperPosition(
            ticket=ticket,
            symbol=request.symbol,
            direction=request.direction,
            volume=request.volume,
            price_open=executed,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            signal_id=request.signal_id,
            opened_at=self._now(),
        )
        return result

    async def close(self, ticket: int, reason: str = RULE) -> CloseResult:
        position = self._positions.get(ticket)
        if position is None:
            return CloseResult(
                closed=False,
                position_ticket=ticket,
                exit_price=None,
                pnl_eur=None,
                exit_reason=reason,
                message="no such paper position",
            )
        spec = await self.instrument(position.symbol)
        exit_price = await self._exit_price(position, spec)
        closed = self._close_at(position, exit_price, reason)
        return CloseResult(
            closed=True,
            position_ticket=ticket,
            exit_price=closed.exit_price,
            pnl_eur=closed.pnl_eur,
            exit_reason=reason,
            message="paper close",
        )

    async def collect_closures(self) -> tuple[ClosedPosition, ...]:
        """Nothing is ever pending in paper mode: a fill only happens on a fed candle or tick.

        The loop drains closures before reconciling; here that drain is empty by
        construction, and saying so is better than an absent method that would make the
        paper broker fail the port.
        """
        return ()

    async def on_candle(self, symbol: str, candle: Candle) -> tuple[ClosedPosition, ...]:
        return await asyncio.to_thread(self._from_candle, symbol, candle)

    async def on_tick(self, symbol: str, bid: Decimal, ask: Decimal) -> tuple[ClosedPosition, ...]:
        return await asyncio.to_thread(self._from_tick, symbol, bid, ask)

    # -- simulation --------------------------------------------------------------------

    def _from_tick(self, symbol: str, bid: Decimal, ask: Decimal) -> tuple[ClosedPosition, ...]:
        closed: list[ClosedPosition] = []
        for position in tuple(self._positions.values()):
            if position.symbol != symbol:
                continue
            if position.direction is Direction.BUY:
                if position.stop_loss and bid <= position.stop_loss:
                    closed.append(self._close_at(position, position.stop_loss, STOP_LOSS))
                elif position.take_profit is not None and bid >= position.take_profit:
                    closed.append(self._close_at(position, position.take_profit, TAKE_PROFIT))
            else:
                if position.stop_loss and ask >= position.stop_loss:
                    closed.append(self._close_at(position, position.stop_loss, STOP_LOSS))
                elif position.take_profit is not None and ask <= position.take_profit:
                    closed.append(self._close_at(position, position.take_profit, TAKE_PROFIT))
        return tuple(closed)

    def _from_candle(self, symbol: str, candle: Candle) -> tuple[ClosedPosition, ...]:
        closed: list[ClosedPosition] = []
        high, low = Decimal(str(candle.high)), Decimal(str(candle.low))
        for position in tuple(self._positions.values()):
            if position.symbol != symbol:
                continue
            if position.direction is Direction.BUY:
                if position.stop_loss and low <= position.stop_loss:
                    closed.append(self._close_at(position, position.stop_loss, STOP_LOSS))
                elif position.take_profit is not None and high >= position.take_profit:
                    closed.append(self._close_at(position, position.take_profit, TAKE_PROFIT))
            else:
                if position.stop_loss and high >= position.stop_loss:
                    closed.append(self._close_at(position, position.stop_loss, STOP_LOSS))
                elif position.take_profit is not None and low <= position.take_profit:
                    closed.append(self._close_at(position, position.take_profit, TAKE_PROFIT))
        return tuple(closed)

    async def _exit_price(self, position: _PaperPosition, spec: InstrumentSpec) -> Decimal:
        tick = await asyncio.to_thread(self._terminal.last_tick, position.symbol)
        if tick is None:
            raise BrokerUnavailableError(f"no quote to close {position.symbol}")
        return (
            Decimal(str(tick.bid))
            if position.direction is Direction.BUY
            else Decimal(str(tick.ask))
        )

    def _close_at(
        self, position: _PaperPosition, exit_price: Decimal, reason: str
    ) -> ClosedPosition:
        spec = self._specs[position.symbol]
        sign = ONE if position.direction is Direction.BUY else -ONE
        pnl = (
            (exit_price - position.price_open)
            * sign
            * position.volume
            * spec.contract_size
            * self._profit_to_eur
        )
        closed = ClosedPosition(
            ticket=position.ticket,
            symbol=position.symbol,
            exit_price=exit_price,
            pnl_eur=pnl,
            exit_reason=reason,
            closed_at=self._now(),
            signal_id=position.signal_id,
        )
        self._positions.pop(position.ticket, None)
        self._log.record_closures((closed,), self._now())
        return closed

    def _floating(self) -> Decimal:
        total = Decimal(0)
        for position in self._positions.values():
            tick = self._terminal.last_tick(position.symbol)
            if tick is None:
                continue
            spec = self._specs[position.symbol]
            raw = tick.bid if position.direction is Direction.BUY else tick.ask
            sign = ONE if position.direction is Direction.BUY else -ONE
            total += (
                (Decimal(str(raw)) - position.price_open)
                * sign
                * position.volume
                * spec.contract_size
                * self._profit_to_eur
            )
        return total

    def _margin(self) -> Decimal:
        total = Decimal(0)
        for position in self._positions.values():
            spec = self._specs[position.symbol]
            total += position.volume * spec.contract_size * position.price_open / self._leverage
        return total

    def _check_mode(self, mode: TradingMode) -> None:
        if mode is not self._mode:
            raise OrderRefusedError(
                f"order carries mode {mode}, this executor runs in {self._mode}: refused"
            )

    def _ensure_trading(self) -> None:
        if self._status is None:
            return
        status = self._status()
        if status.halted:
            raise OrderRefusedError(f"trading is halted: {'; '.join(status.reasons)}")


__all__ = ["PaperBroker"]
