"""One function per control of the specification (TASK-035, action 1).

Every check sees the same snapshot and returns a verdict with its reason; none raises.
The engine runs them all, so a refusal lists every limit it hit, not only the first.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tradingagent.core.halt import HaltStatus
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
from tradingagent.risk.sizing import SizingError, size_position

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
    halt: HaltStatus  # no default: forgetting the halt state must not mean "trading"

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


def check_not_halted(ctx: RiskContext) -> CheckResult:
    """RM-015: no new order while a halt is active, whatever asked for it."""
    halt = ctx.halt
    if not halt.halted:
        return _verdict("not_halted", True, "no halt active")
    return _verdict("not_halted", False, "; ".join(halt.reasons) or "halt active")


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


def check_slippage(ctx: RiskContext) -> CheckResult:
    """RM-012: expected slippage, expressed in multiples of the observed spread.

    A spread-relative ceiling fits gold and bitcoin alike, for the same reason
    `max_spread_stop_pct` does. When the caller supplies no estimate the check passes
    and says so: the unconditional guard remains `check_entry_zone`, which refuses any
    executable price outside the signal's zone. Observed post-fill slippage is recorded
    by execution and analysed by `analytics`, not gated here.
    """
    expected = ctx.quote.expected_slippage
    multiple = ctx.limits.max_slippage_to_spread
    spread = ctx.quote.spread
    if expected is None:
        return _verdict("slippage", True, "no slippage estimate; entry zone still guards the fill")
    # A favourable estimate (a negative one) can never breach an adverse ceiling.
    limit = multiple * spread
    return _verdict(
        "slippage",
        expected <= limit,
        f"expected slippage {expected}, limit {limit} ({multiple} x spread {spread})",
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


def _planned_volume(ctx: RiskContext) -> Decimal:
    """The volume sizing would authorize for this order.

    The exposure ceiling must count what is really about to be added, not the minimum
    lot. Sizing is pure, so the check can run it on the same snapshot; when it cannot
    produce a size the order is refused elsewhere, and the smallest executable size is
    counted here so the ceiling is never understated.
    """
    distance = ctx.stop_distance
    if distance is None or distance <= 0:
        return ctx.spec.volume_min
    limits = ctx.limits
    try:
        sizing = size_position(
            capital=limits.capital(ctx.account.equity),
            risk_per_trade=limits.risk_per_trade,
            stop_distance=distance,
            spec=ctx.spec,
            quote=ctx.quote,
            free_margin=ctx.account.free_margin,
            margin_usage=limits.margin_usage,
            max_volume=limits.max_volume,
        )
    except SizingError:
        return ctx.spec.volume_min
    return sizing.volume


def _notional_eur(ctx: RiskContext, volume: Decimal) -> Decimal | None:
    """Notional of `volume` lots on the intended instrument, in EUR, or None if the
    broker gave no conversion rate — the same refusal sizing would already produce."""
    rate = ctx.quote.profit_to_eur
    contract = ctx.spec.contract_size
    if rate is None or contract is None or contract <= 0:
        return None
    return volume * contract * ctx.entry_price * rate


def check_total_exposure(ctx: RiskContext) -> CheckResult:
    """RM-008: notional of every open position, all markets, plus this order, against a
    fraction of the capital in use.

    Gold and bitcoin are summed before the comparison: their correlation makes two
    separate market ceilings an illusion of safety. The current exposure is read from
    `PortfolioState.open_exposure_eur`, a caller-computed aggregate; when a position is
    open and the caller did not measure it, the check fails closed rather than let the
    ceiling be skipped.
    """
    portfolio, limits = ctx.portfolio, ctx.limits
    limit = limits.max_total_exposure * limits.capital(ctx.account.equity)
    planned = _notional_eur(ctx, _planned_volume(ctx))
    if planned is None:
        return CheckResult(
            "total_exposure",
            False,
            "notional unavailable: no profit-to-EUR rate (RM-008)",
            blocked_by="sizing",
        )
    current = portfolio.open_exposure_eur
    if current is None:
        if not portfolio.open_positions:
            current = Decimal(0)
        else:
            return CheckResult(
                "total_exposure",
                False,
                "existing exposure unknown: the caller must supply "
                "PortfolioState.open_exposure_eur (RM-008)",
            )
    total = current + planned
    return _verdict(
        "total_exposure",
        total <= limit,
        f"exposure {total:.2f} EUR (open {current:.2f} + order {planned:.2f}), "
        f"limit {limit:.2f} EUR",
    )


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
    check_not_halted,
    check_stop_loss,
    check_entry_zone,
    check_slippage,
    check_daily_loss,
    check_weekly_loss,
    check_drawdown,
    check_open_positions,
    check_market_positions,
    check_total_exposure,
    check_trades_today,
    check_spread,
    check_margin,
    check_account_currency,
    check_trading_hours,
    check_cooldown,
    check_live_eligibility,
)
