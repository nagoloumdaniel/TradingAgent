"""The only module allowed to import MetaTrader5 (tests/test_architecture.py)."""

import time
from datetime import UTC, datetime

import MetaTrader5 as mt5

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

_TIMEFRAMES = {
    Timeframe.M1: mt5.TIMEFRAME_M1,
    Timeframe.M5: mt5.TIMEFRAME_M5,
    Timeframe.M15: mt5.TIMEFRAME_M15,
    Timeframe.M30: mt5.TIMEFRAME_M30,
    Timeframe.H1: mt5.TIMEFRAME_H1,
    Timeframe.H4: mt5.TIMEFRAME_H4,
    Timeframe.D1: mt5.TIMEFRAME_D1,
}
_HISTORY_SETTLE_SECONDS = 0.5
_HISTORY_READS = 4


class Mt5Terminal:
    def __init__(self, magic: int = MAGIC) -> None:
        self._magic = magic

    def initialize(self, credentials: Credentials) -> None:
        kwargs = {
            "login": credentials.login,
            "password": credentials.password,
            "server": credentials.server,
            "timeout": 60_000,
        }
        path = credentials.terminal_path
        ok = mt5.initialize(str(path), **kwargs) if path else mt5.initialize(**kwargs)
        if not ok:
            raise TerminalError(f"initialize failed: {mt5.last_error()}")

    def shutdown(self) -> None:
        mt5.shutdown()

    def account(self) -> AccountSnapshot:
        info = mt5.account_info()
        if info is None:
            raise TerminalError(f"account_info failed: {mt5.last_error()}")
        return AccountSnapshot(
            login=info.login,
            # A contest account counts as not demo, so it is refused outside LIVE mode.
            is_demo=info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO,
            currency=info.currency,
            leverage=info.leverage,
            trade_allowed=info.trade_allowed,
            server=info.server,
        )

    def max_bars(self) -> int:
        info = mt5.terminal_info()
        if info is None:
            raise TerminalError(f"terminal_info failed: {mt5.last_error()}")
        return int(info.maxbars)

    def select(self, symbol: str) -> bool:
        return bool(mt5.symbol_select(symbol, True))

    def rates(self, symbol: str, timeframe: Timeframe, count: int) -> list[RawBar]:
        # History downloads lazily: a short answer is re-read until it stops growing.
        data = None
        previous = -1
        for _ in range(_HISTORY_READS):
            data = mt5.copy_rates_from_pos(symbol, _TIMEFRAMES[timeframe], 0, count)
            size = 0 if data is None else len(data)
            if size >= count or size == previous:
                break
            previous = size
            time.sleep(_HISTORY_SETTLE_SECONDS)
        if data is None:
            raise TerminalError(f"rates {symbol} {timeframe} failed: {mt5.last_error()}")
        return [
            RawBar(
                int(r["time"]),
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
            )
            for r in data
        ]

    def last_tick(self, symbol: str) -> RawTick | None:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None or not tick.time:
            return None
        return RawTick(int(tick.time), float(tick.bid), float(tick.ask))

    def is_connected(self) -> bool:
        info = mt5.terminal_info()
        return bool(info is not None and info.connected)

    # -- Trading operations (TASK-081) -------------------------------------------------

    def funds(self) -> AccountFunds:
        info = mt5.account_info()
        if info is None:
            raise TerminalError(f"account_info failed: {mt5.last_error()}")
        return AccountFunds(
            balance=float(info.balance),
            equity=float(info.equity),
            free_margin=float(info.margin_free),
        )

    def symbol_spec(self, symbol: str) -> SymbolSpec | None:
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        return SymbolSpec(
            symbol=symbol,
            contract_size=float(info.trade_contract_size),
            volume_min=float(info.volume_min),
            volume_step=float(info.volume_step),
            volume_max=float(info.volume_max),
            point=float(info.point),
            stops_level=int(info.trade_stops_level),
            digits=int(info.digits),
        )

    def order_check(self, request: TradeRequest) -> TradeCheck:
        raw = mt5.order_check(self._order_fields(request))
        if raw is None:
            return TradeCheck(retcode=-1, margin=0.0, comment=f"order_check: {mt5.last_error()}")
        return TradeCheck(
            retcode=int(raw.retcode), margin=float(raw.margin), comment=str(raw.comment)
        )

    def order_send(self, request: TradeRequest) -> TradeResult | None:
        raw = mt5.order_send(self._order_fields(request))
        if raw is None:
            return None
        return TradeResult(
            retcode=int(raw.retcode),
            order_ticket=int(raw.order),
            deal_ticket=int(raw.deal),
            price=float(raw.price),
            volume=float(raw.volume),
            comment=str(raw.comment),
        )

    def positions(self, symbol: str | None = None) -> tuple[PositionInfo, ...]:
        raw = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if raw is None:
            return ()
        return tuple(
            PositionInfo(
                ticket=int(p.ticket),
                symbol=str(p.symbol),
                direction=Direction.BUY if p.type == mt5.POSITION_TYPE_BUY else Direction.SELL,
                volume=float(p.volume),
                price_open=float(p.price_open),
                stop_loss=float(p.sl),
                take_profit=float(p.tp),
                profit=float(p.profit),
                magic=int(p.magic),
                comment=str(p.comment),
            )
            for p in raw
            if int(p.magic) == self._magic
        )

    def deals_since(self, server_epoch: int) -> tuple[DealInfo, ...]:
        date_from = datetime.fromtimestamp(server_epoch, tz=UTC)
        raw = mt5.history_deals_get(date_from, datetime.now(UTC))
        if raw is None:
            return ()
        return tuple(
            DealInfo(
                ticket=int(d.ticket),
                position_ticket=int(d.position_id),
                order_ticket=int(d.order),
                symbol=str(d.symbol),
                is_entry=d.entry == mt5.DEAL_ENTRY_IN,
                is_buy=d.type == mt5.DEAL_TYPE_BUY,
                volume=float(d.volume),
                price=float(d.price),
                profit=float(d.profit + d.swap + d.commission),
                comment=str(d.comment),
                server_epoch=int(d.time),
            )
            for d in raw
            if int(d.magic) == self._magic
        )

    def calc_profit(
        self,
        direction: Direction,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        value = mt5.order_calc_profit(
            self._order_type(direction), symbol, volume, price_open, price_close
        )
        return None if value is None else float(value)

    def calc_margin(
        self, direction: Direction, symbol: str, volume: float, price: float
    ) -> float | None:
        value = mt5.order_calc_margin(self._order_type(direction), symbol, volume, price)
        return None if value is None else float(value)

    def last_error(self) -> tuple[int, str]:
        code, message = mt5.last_error()
        return int(code), str(message)

    @staticmethod
    def _order_type(direction: Direction) -> int:
        return mt5.ORDER_TYPE_BUY if direction is Direction.BUY else mt5.ORDER_TYPE_SELL

    def _order_fields(self, request: TradeRequest) -> dict[str, object]:
        fields: dict[str, object] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": request.symbol,
            "volume": request.volume,
            "type": self._order_type(request.direction),
            "deviation": request.deviation,
            "magic": request.magic,
            "comment": request.comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }
        if request.price is not None:
            fields["price"] = request.price
        if request.stop_loss is not None:
            fields["sl"] = request.stop_loss
        if request.take_profit is not None:
            fields["tp"] = request.take_profit
        if request.position_ticket is not None:
            fields["position"] = request.position_ticket
        return fields
