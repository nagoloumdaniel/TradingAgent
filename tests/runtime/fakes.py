"""Deterministic fakes for the runtime: no network, no terminal, no wall clock."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from tradingagent.ai.layer import ReviewContext, ReviewOutcome
from tradingagent.config.agent import LiveRiskProfile, RiskConfig, RiskProfile
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import AiFilter
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import SLOTS_PER_WEEK, MarketCalendar
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

LOGIN = 40123456
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
EQUITY = Decimal("5497.74")
GOLD = "XAUUSD"
BTC = "BTCUSD"

GOLD_SPEC = InstrumentSpec(
    GOLD, Decimal(100), Decimal("0.01"), Decimal("0.01"), Decimal(100), Decimal("0.01"), 10
)
GOLD_QUOTE = MarketQuote(
    bid=Decimal(2400),
    ask=Decimal("2400.2"),
    loss_one_lot=Decimal("1094.00"),
    margin_one_lot=Decimal(18393),
    profit_to_eur=Decimal("0.888786"),
)

RISK = RiskConfig(
    simulated=RiskProfile(
        risk_per_trade_pct=Decimal("0.5"),
        daily_loss_pct=Decimal(2),
        weekly_loss_pct=Decimal(6),
        max_drawdown_pct=Decimal(10),
        max_open_positions=2,
        max_positions_per_market=1,
    ),
    live=LiveRiskProfile(
        risk_per_trade_pct=Decimal(2),
        daily_loss_pct=Decimal(5),
        weekly_loss_pct=Decimal(10),
        max_drawdown_pct=Decimal(20),
        max_open_positions=2,
        max_positions_per_market=1,
        reference_capital=Decimal(100),
        currency="EUR",
    ),
)


def open_calendar(symbol: str = GOLD) -> MarketCalendar:
    slots = frozenset(
        (weekday, quarter) for weekday in range(7) for quarter in range(SLOTS_PER_WEEK // 7)
    )
    return MarketCalendar(symbol, slots, frozenset())


class FakeNotifier:
    def __init__(self, *, deliver: bool = True) -> None:
        self.deliver = deliver
        self.messages: list[str] = []

    async def send(self, text: str, *, parse_mode: str | None = None) -> bool:
        self.messages.append(text)
        return self.deliver


class FakeBroker:
    def __init__(
        self,
        *,
        login: int = LOGIN,
        is_demo: bool = True,
        equity: Decimal = EQUITY,
        free_margin: Decimal = EQUITY,
        currency: str = "EUR",
        stop_present: bool = True,
        accepted: bool = True,
        ticket: int | None = 555001,
        executed_price: Decimal | None = Decimal("2400.25"),
    ) -> None:
        self.login = login
        self.is_demo = is_demo
        self.equity = equity
        self.free_margin = free_margin
        self.currency = currency
        self.stop_present = stop_present
        self.accepted = accepted
        self.ticket = ticket
        self.executed_price = executed_price
        self.placed: list[OrderRequest] = []
        self.closed: list[tuple[int, str]] = []
        self.divergences: tuple[str, ...] = ()
        # Symbols whose quote raises, so a test can make one measurement impossible
        # without breaking the quote the risk engine itself needs.
        self.fail_quote_symbols: set[str] = set()
        self._positions: dict[int, BrokerPosition] = {}
        self._closed_queue: list[ClosedPosition] = []

    async def initialize(self) -> None:
        return None

    async def account(self) -> AccountState:
        return AccountState(self.login, self.is_demo, self.currency, self.equity, self.free_margin)

    async def instrument(self, symbol: str) -> InstrumentSpec:
        return GOLD_SPEC

    async def quote(
        self, symbol: str, direction: Direction, stop_loss: Decimal | None = None
    ) -> MarketQuote:
        if symbol in self.fail_quote_symbols:
            raise RuntimeError(f"no quote for {symbol}")
        return GOLD_QUOTE

    async def open_positions(self) -> tuple[OpenPosition, ...]:
        return tuple(OpenPosition(p.symbol, p.volume) for p in self._positions.values())

    async def positions(self) -> tuple[BrokerPosition, ...]:
        return tuple(self._positions.values())

    async def place(self, request: OrderRequest) -> OrderResult:
        self.placed.append(request)
        if not self.accepted:
            return OrderResult(
                accepted=False,
                ticket=None,
                retcode=10006,
                requested_price=request.stop_loss,
                executed_price=None,
                slippage=None,
                stop_present=False,
                message="rejected by the broker",
            )
        assert self.ticket is not None
        self._positions[self.ticket] = BrokerPosition(
            ticket=self.ticket,
            symbol=request.symbol,
            direction=request.direction,
            volume=request.volume,
            open_price=self.executed_price or Decimal(0),
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            mode=request.mode,
        )
        return OrderResult(
            accepted=True,
            ticket=self.ticket,
            retcode=10009,
            requested_price=Decimal("2400.2"),
            executed_price=self.executed_price,
            slippage=Decimal("0.05"),
            stop_present=self.stop_present,
            message="done",
        )

    async def close(self, ticket: int, reason: str) -> CloseResult:
        self.closed.append((ticket, reason))
        self._positions.pop(ticket, None)
        return CloseResult(True, ticket, Decimal("2400.0"), Decimal("-2.00"), reason, "closed")

    async def on_candle(self, symbol: str, candle: Candle) -> tuple[ClosedPosition, ...]:
        queued, self._closed_queue = self._closed_queue, []
        return tuple(queued)

    async def reconcile(self) -> tuple[str, ...]:
        return self.divergences

    def queue_close(self, position: ClosedPosition) -> None:
        self._closed_queue.append(position)
        self._positions.pop(position.ticket, None)


@dataclass
class FakeAi:
    """A reviewer that returns a programmed verdict; never touches the network."""

    blocks_signal: bool = False
    verdict: str = "approved"
    applied: bool = True
    reason: str = "programmed"
    ai_filter: AiFilter = AiFilter.ADVISORY
    overruns: tuple[str, ...] = ()
    calls: int = 0
    contexts: list[ReviewContext] = field(default_factory=list)

    async def review(
        self,
        context: ReviewContext,
        at: datetime,
        signal_id: int | None = None,
        ai_filter: AiFilter | None = None,
    ) -> ReviewOutcome:
        self.calls += 1
        self.contexts.append(context)
        del at, signal_id
        effective = ai_filter if ai_filter is not None else self.ai_filter
        shadow = effective is AiFilter.SHADOW
        rejected = self.verdict == "rejected"
        return ReviewOutcome(
            verdict=self.verdict,
            ai_filter=effective,
            applied=not shadow,
            blocks_signal=not shadow and rejected,
            degraded=False,
            reason=self.reason,
            text=self.reason,
            overrun_attempts=self.overruns,
        )


def candle(open_time: datetime = NOW, close: float = 2400.0) -> Candle:
    return Candle(
        timeframe=Timeframe.M15,
        open_time=open_time,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
    )


__all__ = [
    "BTC",
    "EQUITY",
    "GOLD",
    "GOLD_QUOTE",
    "GOLD_SPEC",
    "LOGIN",
    "NOW",
    "RISK",
    "UTC",
    "FakeAi",
    "FakeBroker",
    "FakeNotifier",
    "candle",
    "open_calendar",
    "timedelta",
]
