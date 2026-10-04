"""Position size, formula settled in TASK-004 (specification section 9).

Any impossibility raises SizingError: an order is refused, a size is never guessed. The
volume is rounded down to the lot step, so the realized risk never exceeds the budget.
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal

from tradingagent.risk.model import CROSS_CHECK_TOLERANCE, InstrumentSpec, MarketQuote


class SizingError(Exception):
    """No safe size exists: the order must be refused."""


@dataclass(frozen=True)
class Sizing:
    volume: Decimal
    risk_eur: Decimal
    margin_eur: Decimal
    risk_volume: Decimal  # volume the risk budget alone would allow
    margin_volume: Decimal  # volume the free margin alone would allow
    limited_by: str  # risk, margin, volume_max or max_volume


def size_position(
    *,
    capital: Decimal,
    risk_per_trade: Decimal,
    stop_distance: Decimal,
    spec: InstrumentSpec,
    quote: MarketQuote,
    free_margin: Decimal,
    margin_usage: Decimal,
    max_volume: Decimal | None,
) -> Sizing:
    if stop_distance <= 0:
        raise SizingError(f"stop distance must be positive, got {stop_distance}")
    if capital <= 0 or free_margin <= 0:
        raise SizingError("no capital or free margin to size against")
    broker_loss = _positive(quote.loss_one_lot, "loss for one lot (order_calc_profit)")
    margin_one_lot = _positive(quote.margin_one_lot, "margin for one lot (order_calc_margin)")
    rate = _positive(quote.profit_to_eur, "profit currency to EUR rate")

    independent_loss = spec.contract_size * stop_distance * rate
    gap = abs(broker_loss - independent_loss) / independent_loss
    if gap > CROSS_CHECK_TOLERANCE:
        raise SizingError(
            f"loss estimates diverge by {gap:.2%}: broker {broker_loss:.2f} EUR, "
            f"independent {independent_loss:.2f} EUR (tolerance {CROSS_CHECK_TOLERANCE:.0%})"
        )
    # The larger estimate: sizing on the smaller one would risk more than intended.
    loss_one_lot = max(broker_loss, independent_loss)

    caps = {
        "risk": capital * risk_per_trade / loss_one_lot,
        "margin": free_margin * margin_usage / margin_one_lot,
        "volume_max": spec.volume_max,
    }
    if max_volume is not None:
        caps["max_volume"] = max_volume
    limited_by = min(caps, key=lambda name: caps[name])
    steps = (caps[limited_by] / spec.volume_step).to_integral_value(rounding=ROUND_FLOOR)
    volume = steps * spec.volume_step
    if volume < spec.volume_min:
        raise SizingError(
            f"size {volume} is below the minimum lot {spec.volume_min} "
            f"(risk allows {caps['risk']:.5f}, margin {caps['margin']:.5f})"
        )
    return Sizing(
        volume=volume,
        risk_eur=volume * loss_one_lot,
        margin_eur=volume * margin_one_lot,
        risk_volume=caps["risk"],
        margin_volume=caps["margin"],
        limited_by=limited_by,
    )


def _positive(value: Decimal | None, label: str) -> Decimal:
    if value is None:
        raise SizingError(f"{label} unavailable")
    if not value.is_finite() or value <= 0:
        raise SizingError(f"{label} must be positive, got {value}")
    return value
