"""RM-019: an instrument whose minimum lot alone exceeds the live risk budget is refused in
LIVE, and stays fully active in every other mode. Unknown counts as refused."""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from tradingagent.risk.model import InstrumentSpec, MarketQuote, RiskLimits


class Eligibility(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EligibilityVerdict:
    status: Eligibility
    reason: str
    minimum_risk: Decimal | None = None
    required_capital: Decimal | None = None


def live_eligibility(
    spec: InstrumentSpec,
    quote: MarketQuote,
    stop_distance: Decimal | None,
    limits: RiskLimits,
    equity: Decimal,
) -> EligibilityVerdict:
    """`stop_distance` is the order's stop, or at start-up the strategy's typical one."""
    if stop_distance is None or stop_distance <= 0:
        return EligibilityVerdict(Eligibility.UNKNOWN, "no stop distance to assess, refused")
    broker_loss, rate = quote.loss_one_lot, quote.profit_to_eur
    if broker_loss is None or rate is None or broker_loss <= 0 or rate <= 0:
        return EligibilityVerdict(Eligibility.UNKNOWN, "loss per lot unavailable, refused")
    loss_one_lot = max(broker_loss, spec.contract_size * stop_distance * rate)
    minimum_risk = spec.volume_min * loss_one_lot
    budget = limits.risk_per_trade * limits.capital(equity)
    required = minimum_risk / limits.risk_per_trade
    if minimum_risk > budget:
        return EligibilityVerdict(
            Eligibility.INELIGIBLE,
            f"minimum lot risks {minimum_risk:.2f} EUR, budget {budget:.2f} EUR: "
            f"needs {required:.2f} EUR of capital",
            minimum_risk,
            required,
        )
    return EligibilityVerdict(
        Eligibility.ELIGIBLE,
        f"minimum lot risks {minimum_risk:.2f} EUR, budget {budget:.2f} EUR",
        minimum_risk,
        required,
    )
