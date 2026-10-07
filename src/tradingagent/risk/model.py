"""What the risk engine decides on: one immutable snapshot, exact decimals throughout.

The caller gathers the snapshot (terminal, store, executor); the engine never reads the
clock, the network or the database, so every decision can be replayed from its inputs.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from tradingagent.config.agent import LiveRiskProfile, RiskConfig, RiskProfile
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode

HUNDRED = Decimal(100)
CROSS_CHECK_TOLERANCE = Decimal("0.02")

# RM-008 total exposure ceiling, as a fraction of the capital in use (see
# `RiskLimits.capital`). Two means the notional of every open position plus the intended
# one never exceeds twice the capital; with the two-position ceiling of RM-008 that lets
# a full-size gold position and a full-size bitcoin position coexist, while refusing a
# single position whose tight stop makes the margin cap allow huge leverage.
# The operator declares it as `max_total_exposure_pct` in agent.yaml; this is the fallback
# for callers that build `RiskLimits` directly.
DEFAULT_MAX_TOTAL_EXPOSURE = Decimal("2")

# RM-012 slippage ceiling, in multiples of the observed spread: a spread-relative cap
# fits gold and bitcoin alike, exactly as `max_spread_stop_pct` does. One means the
# expected adverse slippage may not exceed the spread itself.
DEFAULT_MAX_SLIPPAGE_TO_SPREAD = Decimal("1")


@dataclass(frozen=True)
class TradeIntent:
    signal_id: int
    symbol: str
    direction: Direction
    entry_low: Decimal
    entry_high: Decimal
    stop_loss: Decimal | None


@dataclass(frozen=True)
class AccountState:
    login: int
    is_demo: bool
    currency: str
    equity: Decimal
    free_margin: Decimal
    # Optional so existing callers stay valid; the operator's close message shows it.
    balance: Decimal | None = None


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    contract_size: Decimal
    volume_min: Decimal
    volume_step: Decimal
    volume_max: Decimal
    point: Decimal
    stops_level: int  # minimum stop distance, in points


@dataclass(frozen=True)
class MarketQuote:
    """Prices and broker calculations taken at decision time, for the intended stop."""

    bid: Decimal
    ask: Decimal
    loss_one_lot: Decimal | None  # order_calc_profit on 1 lot to the stop, in EUR, positive
    margin_one_lot: Decimal | None  # order_calc_margin on 1 lot, in EUR
    profit_to_eur: Decimal | None  # converts the profit currency to EUR, 1 if already EUR
    # RM-012: adverse slippage the caller expects at fill, in quote-currency price units,
    # from recent fills, broker latency or the paper broker's hypothesis. None means no
    # estimate is available: the unconditional guard is then `check_entry_zone`, which
    # already requires the executable price to stay inside the signal's zone.
    expected_slippage: Decimal | None = None

    def entry_price(self, direction: Direction) -> Decimal:
        return self.ask if direction is Direction.BUY else self.bid

    @property
    def spread(self) -> Decimal:
        return self.ask - self.bid


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    volume: Decimal


@dataclass(frozen=True)
class OrderRequest:
    """What the executor is allowed to send once risk has authorized a signal.

    The idempotency key travels to the broker (hashed into the order comment), so a lost
    answer can be reconciled by looking the ticket up instead of resending an order.
    """

    signal_id: int
    idempotency_key: str
    symbol: str
    direction: Direction
    volume: Decimal
    stop_loss: Decimal
    take_profit: Decimal | None
    mode: TradingMode
    comment: str


@dataclass(frozen=True)
class OrderResult:
    accepted: bool
    ticket: int | None
    retcode: int | None
    requested_price: Decimal
    executed_price: Decimal | None
    slippage: Decimal | None
    stop_present: bool  # read back from the position, not assumed from the request
    message: str


@dataclass(frozen=True)
class BrokerPosition:
    ticket: int
    symbol: str
    direction: Direction
    volume: Decimal
    open_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    mode: TradingMode


@dataclass(frozen=True)
class ClosedPosition:
    """A position that ended, reported by the tracking layer (F-017, TASK-082).

    `signal_id` travels back so the agent loop can move the signal's lifecycle to CLOSED
    without reading the executor's tables.
    """

    ticket: int
    symbol: str
    exit_price: Decimal
    pnl_eur: Decimal
    exit_reason: str
    closed_at: datetime
    signal_id: int | None = None


@dataclass(frozen=True)
class CloseResult:
    closed: bool
    position_ticket: int
    exit_price: Decimal | None
    pnl_eur: Decimal | None
    exit_reason: str
    message: str


@dataclass(frozen=True)
class PortfolioState:
    open_positions: tuple[OpenPosition, ...]
    trades_today: int
    day_start_equity: Decimal
    day_pnl: Decimal  # realized + floating since the daily reset, negative when losing
    week_start_equity: Decimal
    week_pnl: Decimal
    equity_peak: Decimal
    consecutive_losses: int
    last_loss_at: datetime | None
    # RM-008: current notional exposure of every open position, all markets summed, in
    # EUR. Computed by the caller, because `OpenPosition` carries neither the contract
    # size nor a price — only the caller holds the instrument specs and the quotes of
    # the markets it is not currently reviewing. Gold and bitcoin must be added together:
    # their correlation makes separate ceilings an illusion of safety.
    # None means the caller did not measure it. `check_total_exposure` then fails closed
    # as soon as a position is open, so the ceiling is never silently skipped.
    open_exposure_eur: Decimal | None = None


@dataclass(frozen=True)
class RiskLimits:
    """Thresholds for one mode, as fractions. RM-005 to RM-007 differ between modes."""

    mode: TradingMode
    risk_per_trade: Decimal
    daily_loss: Decimal
    weekly_loss: Decimal
    max_drawdown: Decimal
    max_open_positions: int
    max_positions_per_market: int
    max_trades_per_day: int
    cooldown_after_losses: int
    cooldown: timedelta
    max_spread_to_stop: Decimal
    margin_usage: Decimal
    max_volume: Decimal | None
    reference_capital: Decimal | None  # LIVE only: sizing never exceeds declared capital
    # RM-008 and RM-012 ceilings; see the module constants for why they default here.
    max_total_exposure: Decimal = DEFAULT_MAX_TOTAL_EXPOSURE
    max_slippage_to_spread: Decimal = DEFAULT_MAX_SLIPPAGE_TO_SPREAD

    @property
    def is_live(self) -> bool:
        return self.mode is TradingMode.LIVE

    def capital(self, equity: Decimal) -> Decimal:
        """Capital that percentages apply to: in LIVE, never more than the declared one."""
        if self.reference_capital is None:
            return equity
        return min(equity, self.reference_capital)


def limits_for(mode: TradingMode, config: RiskConfig) -> RiskLimits:
    """Live thresholds in LIVE; every other mode, notified signals included, uses the
    simulated ones, so a signal shows the size the demo account would take."""
    profile: RiskProfile = config.live if mode is TradingMode.LIVE else config.simulated
    reference = profile.reference_capital if isinstance(profile, LiveRiskProfile) else None
    return RiskLimits(
        mode=mode,
        risk_per_trade=profile.risk_per_trade_pct / HUNDRED,
        daily_loss=profile.daily_loss_pct / HUNDRED,
        weekly_loss=profile.weekly_loss_pct / HUNDRED,
        max_drawdown=profile.max_drawdown_pct / HUNDRED,
        max_open_positions=profile.max_open_positions,
        max_positions_per_market=profile.max_positions_per_market,
        max_trades_per_day=profile.max_trades_per_day,
        cooldown_after_losses=profile.cooldown_after_losses,
        cooldown=timedelta(hours=float(profile.cooldown_hours)),
        max_spread_to_stop=profile.max_spread_stop_pct / HUNDRED,
        margin_usage=profile.margin_usage_pct / HUNDRED,
        max_volume=profile.max_volume,
        reference_capital=reference,
        # §21 ceilings, now declared by the operator in agent.yaml like every other limit.
        max_total_exposure=profile.max_total_exposure_pct / HUNDRED,
        max_slippage_to_spread=profile.max_slippage_to_spread,
    )
