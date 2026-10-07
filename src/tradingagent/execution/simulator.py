"""An in-memory terminal that behaves like MT5: injectable, scriptable, and never online.

The recovery harness (TASK-084/085) and the executor tests need a broker that can be told
to lose an answer, refuse a jurisdiction, drop a protection or disappear mid-cycle. This is
that broker: the same `Terminal` and `TradingTerminal` interfaces, backed by dictionaries.
It ships in production code because the recovery suite runs the real executors against it.
"""

import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from tradingagent.core.market import Direction
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.terminal import (
    MAGIC,
    AccountFunds,
    AccountSnapshot,
    Credentials,
    DealInfo,
    PositionInfo,
    RawBar,
    RawTick,
    SymbolSpec,
    TerminalError,
    TradeCheck,
    TradeRequest,
    TradeResult,
)

RETCODE_DONE = 10009
RETCODE_PLACED = 10008
RETCODE_BLOCKED = 10006  # "instruments blocked in France": order_check approves, the server refuses
RETCODE_REQUOTE = 10004
RETCODE_INVALID_STOPS = 10016
DEFAULT_EPOCH = 1_790_748_000  # 2026-10-04T00:00:00Z


def default_specs() -> dict[str, SymbolSpec]:
    return {
        "XAUUSD": SymbolSpec("XAUUSD", 100.0, 0.01, 0.01, 100.0, 0.01, 10, 3),
        "BTCUSD": SymbolSpec("BTCUSD", 1.0, 0.01, 0.01, 100.0, 0.01, 10, 2),
    }


@dataclass
class _SimPosition:
    ticket: int
    symbol: str
    direction: Direction
    volume: float
    price_open: float
    stop_loss: float
    take_profit: float
    magic: int
    comment: str


class SimulatedTerminal:
    """A deterministic broker. Every fault is a field a test can set."""

    def __init__(
        self,
        *,
        login: int = 40123456,
        currency: str = "EUR",
        balance: float = 10_000.0,
        leverage: int = 30,
        profit_to_eur: float = 1.0,
        magic: int = MAGIC,
    ) -> None:
        self.login = login
        self.currency = currency
        self.balance = balance
        self.leverage = leverage
        self.profit_to_eur = profit_to_eur
        self.magic = magic

        self.specs: dict[str, SymbolSpec] = default_specs()
        self.ticks: dict[str, RawTick] = {}
        self.bars: dict[tuple[str, Timeframe], list[RawBar]] = {}
        self.unselectable: set[str] = set()

        self.is_demo = True
        self.trade_allowed = True
        self.connected = True
        self.initialized = False
        self.fail_initialize = False
        self.fail_funds = False
        self.clock_epoch = DEFAULT_EPOCH
        self.drop_protection = False  # the server accepts the order but keeps no stop
        self.fill_slippage = 0.0  # the price the fill really gets, in quote currency
        self.reject_retcode: int | None = None  # server-side refusal, regardless of order_check
        self.check_retcode = RETCODE_DONE
        self.fail_sends = 0  # the next N sends raise, the broker never reached the server
        self.lost_answers = 0  # the next N sends execute but return no answer
        self.calls: list[str] = []

        self._positions: dict[int, _SimPosition] = {}
        self._deals: list[DealInfo] = []
        self._next_ticket = 1000
        self._lock = threading.Lock()

    # -- data surface ------------------------------------------------------------------

    def initialize(self, credentials: Credentials) -> None:
        self._record("initialize")
        if self.fail_initialize:
            raise TerminalError("simulated terminal not reachable")
        self.login = credentials.login
        self.connected = True
        self.initialized = True

    def shutdown(self) -> None:
        self._record("shutdown")
        self.connected = False

    def account(self) -> AccountSnapshot:
        self._record("account")
        if not self.connected:
            raise TerminalError("simulated terminal disconnected")
        return AccountSnapshot(
            login=self.login,
            is_demo=self.is_demo,
            currency=self.currency,
            leverage=self.leverage,
            trade_allowed=self.trade_allowed,
            server="Simulated-Demo" if self.is_demo else "Simulated-Real",
        )

    def max_bars(self) -> int:
        self._record("max_bars")
        return 100_000

    def select(self, symbol: str) -> bool:
        self._record("select")
        return symbol not in self.unselectable

    def rates(self, symbol: str, timeframe: Timeframe, count: int) -> list[RawBar]:
        self._record("rates")
        return list(self.bars.get((symbol, timeframe), []))[-count:]

    def last_tick(self, symbol: str) -> RawTick | None:
        self._record("last_tick")
        if not self.connected:
            return None
        return self.ticks.get(symbol)

    def is_connected(self) -> bool:
        self._record("is_connected")
        return self.connected

    # -- trading surface ---------------------------------------------------------------

    def funds(self) -> AccountFunds:
        self._record("funds")
        if self.fail_funds or not self.connected:
            raise TerminalError("simulated funds unavailable")
        equity = self.balance + self._floating()
        free_margin = equity - self._margin()
        return AccountFunds(balance=self.balance, equity=equity, free_margin=free_margin)

    def symbol_spec(self, symbol: str) -> SymbolSpec | None:
        self._record("symbol_spec")
        return self.specs.get(symbol)

    def order_check(self, request: TradeRequest) -> TradeCheck:
        self._record("order_check")
        return TradeCheck(
            retcode=self.check_retcode,
            margin=self._margin_for(request),
            comment="simulated check",
        )

    def order_send(self, request: TradeRequest) -> TradeResult | None:
        self._record("order_send")
        if not self.connected:
            raise TerminalError("simulated terminal disconnected")
        if self.fail_sends > 0:
            self.fail_sends -= 1
            raise TerminalError("simulated order_send outage")
        if self.reject_retcode is not None:
            return TradeResult(
                retcode=self.reject_retcode,
                order_ticket=0,
                deal_ticket=0,
                price=0.0,
                volume=0.0,
                comment="blocked in France",
            )
        with self._lock:
            result = self._fill(request)
        if self.lost_answers > 0:
            self.lost_answers -= 1
            return None  # executed, answer lost: the caller must reconcile by comment
        return result

    def positions(self, symbol: str | None = None) -> tuple[PositionInfo, ...]:
        self._record("positions")
        if not self.connected:
            return ()
        with self._lock:
            return tuple(
                PositionInfo(
                    ticket=p.ticket,
                    symbol=p.symbol,
                    direction=p.direction,
                    volume=p.volume,
                    price_open=p.price_open,
                    stop_loss=p.stop_loss,
                    take_profit=p.take_profit,
                    profit=self._profit(p),
                    magic=p.magic,
                    comment=p.comment,
                )
                for p in self._positions.values()
                if p.magic == self.magic and (symbol is None or p.symbol == symbol)
            )

    def deals_since(self, server_epoch: int) -> tuple[DealInfo, ...]:
        self._record("deals_since")
        with self._lock:
            return tuple(d for d in self._deals if d.server_epoch >= server_epoch)

    def calc_profit(
        self,
        direction: Direction,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        self._record("calc_profit")
        spec = self.specs.get(symbol)
        if spec is None:
            return None
        sign = 1.0 if direction is Direction.BUY else -1.0
        return (price_close - price_open) * sign * volume * spec.contract_size * self.profit_to_eur

    def calc_margin(
        self, direction: Direction, symbol: str, volume: float, price: float
    ) -> float | None:
        self._record("calc_margin")
        spec = self.specs.get(symbol)
        if spec is None:
            return None
        return volume * spec.contract_size * price / self.leverage

    def last_error(self) -> tuple[int, str]:
        return -1, "simulated terminal"

    # -- test controls -----------------------------------------------------------------

    def set_tick(self, symbol: str, bid: float, ask: float, epoch: int | None = None) -> None:
        self.ticks[symbol] = RawTick(
            server_epoch=self.clock_epoch if epoch is None else epoch, bid=bid, ask=ask
        )

    def set_bar(self, symbol: str, bar: RawBar, timeframe: Timeframe = Timeframe.M15) -> None:
        self.bars.setdefault((symbol, timeframe), []).append(bar)

    def position_tickets(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(self._positions)

    def _record(self, name: str) -> None:
        self.calls.append(name)

    def _fill(self, request: TradeRequest) -> TradeResult:
        price = request.price if request.price is not None else self._market_price(request)
        if request.position_ticket is not None:
            return self._close(request, price)
        price += self.fill_slippage
        self._next_ticket += 1
        ticket = self._next_ticket
        position = _SimPosition(
            ticket=ticket,
            symbol=request.symbol,
            direction=request.direction,
            volume=request.volume,
            price_open=price,
            stop_loss=0.0 if self.drop_protection else (request.stop_loss or 0.0),
            take_profit=0.0 if self.drop_protection else (request.take_profit or 0.0),
            magic=request.magic,
            comment=request.comment,
        )
        self._positions[ticket] = position
        self._deals.append(
            DealInfo(
                ticket=ticket * 10,
                position_ticket=ticket,
                order_ticket=ticket,
                symbol=request.symbol,
                is_entry=True,
                is_buy=request.direction is Direction.BUY,
                volume=request.volume,
                price=price,
                profit=0.0,
                comment=request.comment,
                server_epoch=self.clock_epoch,
            )
        )
        return TradeResult(
            retcode=RETCODE_DONE,
            order_ticket=ticket,
            deal_ticket=ticket * 10,
            price=price,
            volume=request.volume,
            comment="simulated fill",
        )

    def _close(self, request: TradeRequest, price: float) -> TradeResult:
        position = self._positions.pop(request.position_ticket or 0)
        profit = self._profit_at(position, price)
        self.balance += profit
        self._deals.append(
            DealInfo(
                ticket=position.ticket * 10 + 1,
                position_ticket=position.ticket,
                order_ticket=position.ticket,
                symbol=position.symbol,
                is_entry=False,
                is_buy=request.direction is Direction.BUY,
                volume=request.volume,
                price=price,
                profit=profit,
                comment=request.comment,
                server_epoch=self.clock_epoch,
            )
        )
        return TradeResult(
            retcode=RETCODE_DONE,
            order_ticket=position.ticket,
            deal_ticket=position.ticket * 10 + 1,
            price=price,
            volume=request.volume,
            comment="simulated close",
        )

    def _market_price(self, request: TradeRequest) -> float:
        tick = self.ticks.get(request.symbol)
        if tick is None:
            raise TerminalError(f"no tick for {request.symbol}")
        if request.direction is Direction.BUY:
            return tick.ask
        return tick.bid

    def _margin_for(self, request: TradeRequest) -> float:
        price = self._market_price(request)
        spec = self.specs[request.symbol]
        return request.volume * spec.contract_size * price / self.leverage

    def _profit(self, position: _SimPosition) -> float:
        tick = self.ticks.get(position.symbol)
        if tick is None:
            return 0.0
        price = tick.bid if position.direction is Direction.BUY else tick.ask
        return self._profit_at(position, price)

    def _profit_at(self, position: _SimPosition, price: float) -> float:
        sign = 1.0 if position.direction is Direction.BUY else -1.0
        spec = self.specs[position.symbol]
        return (
            (price - position.price_open)
            * sign
            * position.volume
            * spec.contract_size
            * self.profit_to_eur
        )

    def _margin(self) -> float:
        return sum(self._margin_at(p) for p in self._positions.values())

    def _margin_at(self, position: _SimPosition) -> float:
        spec = self.specs[position.symbol]
        return position.volume * spec.contract_size * position.price_open / self.leverage

    def _floating(self) -> float:
        return sum(self._profit(p) for p in self._positions.values())


def utc_now() -> datetime:
    return datetime.now(UTC)


def decimal(value: float) -> Decimal:
    return Decimal(str(value))


__all__ = [
    "DEFAULT_EPOCH",
    "RETCODE_BLOCKED",
    "RETCODE_DONE",
    "RETCODE_INVALID_STOPS",
    "RETCODE_PLACED",
    "RETCODE_REQUOTE",
    "SimulatedTerminal",
    "decimal",
    "default_specs",
    "utc_now",
]
