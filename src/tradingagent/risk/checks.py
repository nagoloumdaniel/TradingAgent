"""One function per control of the specification (TASK-035, action 1).

Every check sees the same snapshot and returns a verdict with its reason; none raises.
The engine runs them all, so a refusal lists every limit it hit, not only the first.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tradingagent.core.market import Direction
from tradingagent.data.market_calendar import SlotStatus
from tradingagent.risk.eligibility import Eligibility, live_eligibility
from tradingagent.risk.model import (
    AccountState,
    InstrumentSpec,
    MarketQuote,
    PortfolioState,
    RiskLimits,
    TradeIntent,
)

ACCOUNT_CURRENCY = "EUR"


@dataclass(frozen=True)
class RiskContext:
    intent: TradeIntent
    account: AccountState
    spec: InstrumentSpec
    quote: MarketQuote
    portfolio: PortfolioState
    market: SlotStatus
    limits: RiskLimits
    now: datetime

    @property
    def entry_price(self) -> Decimal:
        return self.quote.entry_price(self.intent.direction)

    @property
    def exit_price(self) -> Decimal:
        """The price that triggers the stop: bid for a buy, ask for a sell, as MT5 does."""
        quote = self.quote
        return quote.bid if self.intent.direction is Direction.BUY else quote.ask

    @property
    def stop_distance(self) -> Decimal | None:
        """From the fill price: what is lost if the stop is hit, spread included."""
        stop = self.intent.stop_loss
        return None if stop is None else abs(self.entry_price - stop)


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    reason: str
    # Set when the check could not be assessed because another one failed first: the
    # refusal still counts, but reports attribute it to its root cause only.
    blocked_by: str | None = None


def _verdict(name: str, passed: bool, reason: str) -> CheckResult:
    return CheckResult(name, passed, reason)


def check_stop_loss(ctx: RiskContext) -> CheckResult:
    """RM-004: present, on the protective side, beyond the broker's minimum distance.
    Both are measured from the price that would trigger the stop, as the broker does.
    A stop too close is refused, never widened: widening changes the decided risk."""
    stop = ctx.intent.stop_loss
    if stop is None:
        return _verdict("stop_loss", False, "no stop-loss")
    trigger = ctx.exit_price
    protective = stop < trigger if ctx.intent.direction is Direction.BUY else stop > trigger
    if not protective:
        return _verdict(
            "stop_loss",
            False,
            f"stop {stop} is not protective for a {ctx.intent.direction} quoted at {trigger}",
        )
    minimum = ctx.spec.stops_level * ctx.spec.point
    distance = abs(trigger - stop)
    if distance < minimum:
        return _verdict(
            "stop_loss", False, f"stop distance {distance} below the broker minimum {minimum}"
        )
    return _verdict("stop_loss", True, f"stop distance {distance}")


def check_entry_zone(ctx: RiskContext) -> CheckResult:
    """RM-012 slippage: the executable price must still be inside the signal's zone."""
    price, intent = ctx.entry_price, ctx.intent
    inside = intent.entry_low <= price <= intent.entry_high
    zone = f"[{intent.entry_low}, {intent.entry_high}]"
    return _verdict(
        "entry_zone", inside, f"price {price} {'inside' if inside else 'outside'} {zone}"
    )


def _loss_check(name: str, loss: Decimal, limit: Decimal, base: Decimal) -> CheckResult:
    threshold = limit * base
    passed = loss < threshold
    return _verdict(name, passed, f"loss {loss:.2f} EUR, limit {threshold:.2f} EUR")


def _prospective_loss_check(
    ctx: RiskContext, name: str, pnl: Decimal, limit: Decimal, start_equity: Decimal
) -> CheckResult:
    """The loss so far plus this trade's full risk budget must stay within the limit:
    a trade whose stop would breach it is refused before it is taken."""
    limits = ctx.limits
    loss = -pnl
    at_stake = limits.risk_per_trade * limits.capital(ctx.account.equity)
    threshold = limit * limits.capital(start_equity)
    passed = loss + at_stake <= threshold
    return _verdict(
        name,
        passed,
        f"loss {loss:.2f} EUR + {at_stake:.2f} EUR at stake, limit {threshold:.2f} EUR",
    )


def check_daily_loss(ctx: RiskContext) -> CheckResult:
    """RM-006: realized and floating loss of the day, plus what this trade could lose."""
    portfolio = ctx.portfolio
    return _prospective_loss_check(
        ctx, "daily_loss", portfolio.day_pnl, ctx.limits.daily_loss, portfolio.day_start_equity
    )


def check_weekly_loss(ctx: RiskContext) -> CheckResult:
    """RM-007: loss of the week, plus what this trade could lose."""
    portfolio = ctx.portfolio
    return _prospective_loss_check(
        ctx, "weekly_loss", portfolio.week_pnl, ctx.limits.weekly_loss, portfolio.week_start_equity
    )


def check_drawdown(ctx: RiskContext) -> CheckResult:
    """RM-007: drop from the equity peak."""
    portfolio, limits = ctx.portfolio, ctx.limits
    drop = portfolio.equity_peak - ctx.account.equity
    return _loss_check("drawdown", drop, limits.max_drawdown, limits.capital(portfolio.equity_peak))


def check_open_positions(ctx: RiskContext) -> CheckResult:
    """RM-008: simultaneous positions, all markets."""
    count, limit = len(ctx.portfolio.open_positions), ctx.limits.max_open_positions
    return _verdict("open_positions", count < limit, f"{count} open, limit {limit}")


def check_market_positions(ctx: RiskContext) -> CheckResult:
    """RM-008: one position per market, even though the account allows hedging."""
    symbol = ctx.intent.symbol
    count = sum(1 for position in ctx.portfolio.open_positions if position.symbol == symbol)
    limit = ctx.limits.max_positions_per_market
    return _verdict("market_positions", count < limit, f"{count} open on {symbol}, limit {limit}")


def check_trades_today(ctx: RiskContext) -> CheckResult:
    """RM-008: trades opened today, all markets."""
    count, limit = ctx.portfolio.trades_today, ctx.limits.max_trades_per_day
    return _verdict("trades_today", count < limit, f"{count} today, limit {limit}")


def check_spread(ctx: RiskContext) -> CheckResult:
    """RM-012: spread relative to the stop distance, so one rule fits gold and bitcoin."""
    distance = ctx.stop_distance
    if distance is None or distance <= 0:
        return CheckResult(
            "spread", False, "no valid stop to compare the spread with", blocked_by="stop_loss"
        )
    limit = ctx.limits.max_spread_to_stop * distance
    spread = ctx.quote.spread
    return _verdict("spread", spread <= limit, f"spread {spread}, limit {limit}")


def check_margin(ctx: RiskContext) -> CheckResult:
    """RM-012: the free margin, under its usage cap, must carry at least the minimum lot."""
    margin_one_lot = ctx.quote.margin_one_lot
    if margin_one_lot is None or margin_one_lot <= 0:
        return _verdict("margin", False, "margin for one lot unavailable")
    needed = ctx.spec.volume_min * margin_one_lot
    available = ctx.account.free_margin * ctx.limits.margin_usage
    return _verdict(
        "margin", needed <= available, f"minimum lot needs {needed:.2f} EUR, {available:.2f} usable"
    )


def check_account_currency(ctx: RiskContext) -> CheckResult:
    """Every amount here is in EUR: equity and margin in another currency would mix units."""
    currency = ctx.account.currency
    return _verdict(
        "account_currency", currency == ACCOUNT_CURRENCY, f"account in {currency}, expected EUR"
    )


def check_trading_hours(ctx: RiskContext) -> CheckResult:
    """F-005: only while the learned calendar says the market is open."""
    return _verdict("trading_hours", ctx.market is SlotStatus.OPEN, f"market {ctx.market}")


def check_cooldown(ctx: RiskContext) -> CheckResult:
    """Pause after a losing streak."""
    portfolio, limits = ctx.portfolio, ctx.limits
    streak = portfolio.consecutive_losses
    if streak < limits.cooldown_after_losses:
        return _verdict("cooldown", True, f"{streak} consecutive loss(es)")
    if portfolio.last_loss_at is None:
        return _verdict("cooldown", False, f"{streak} consecutive losses, last loss time unknown")
    resumes = portfolio.last_loss_at + limits.cooldown
    passed = ctx.now >= resumes
    return _verdict(
        "cooldown",
        passed,
        f"{streak} consecutive losses, {'resumed' if passed else 'paused until'} "
        f"{resumes.isoformat()}",
    )


def check_live_eligibility(ctx: RiskContext) -> CheckResult:
    """RM-019: in LIVE, the minimum lot alone must fit the risk budget."""
    if not ctx.limits.is_live:
        return _verdict("live_eligibility", True, f"not applicable in {ctx.limits.mode}")
    if ctx.stop_distance is None:
        return CheckResult(
            "live_eligibility", False, "no stop to assess the minimum risk", blocked_by="stop_loss"
        )
    verdict = live_eligibility(
        ctx.spec, ctx.quote, ctx.stop_distance, ctx.limits, ctx.account.equity
    )
    return _verdict("live_eligibility", verdict.status is Eligibility.ELIGIBLE, verdict.reason)


CHECKS: tuple[Callable[[RiskContext], CheckResult], ...] = (
    check_stop_loss,
    check_entry_zone,
    check_daily_loss,
    check_weekly_loss,
    check_drawdown,
    check_open_positions,
    check_market_positions,
    check_trades_today,
    check_spread,
    check_margin,
    check_account_currency,
    check_trading_hours,
    check_cooldown,
    check_live_eligibility,
)
