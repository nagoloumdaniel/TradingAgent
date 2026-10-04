"""Hard limits whose breach halts the agent until an operator decides (RM-007).

Unlike the per-order checks, these look at what has happened, not at what a trade could
add: reaching the weekly loss or the drawdown limit stops trading altogether.
"""

from decimal import Decimal

from tradingagent.risk.model import PortfolioState, RiskLimits


def hard_limit_breaches(
    equity: Decimal, portfolio: PortfolioState, limits: RiskLimits
) -> list[str]:
    breaches = []
    weekly_loss = -portfolio.week_pnl
    weekly_limit = limits.weekly_loss * limits.capital(portfolio.week_start_equity)
    if weekly_loss >= weekly_limit:
        breaches.append(
            f"weekly loss {weekly_loss:.2f} EUR reached its limit {weekly_limit:.2f} EUR (RM-007)"
        )
    drawdown = portfolio.equity_peak - equity
    drawdown_limit = limits.max_drawdown * limits.capital(portfolio.equity_peak)
    if drawdown >= drawdown_limit:
        breaches.append(
            f"drawdown {drawdown:.2f} EUR reached its limit {drawdown_limit:.2f} EUR (RM-007)"
        )
    return breaches
