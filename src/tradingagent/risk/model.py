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
    )
