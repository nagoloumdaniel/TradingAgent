"""From a direction to something an operator can act on: a stance, a window, timeframes.

Pure. A direction ("bullish for gold") is not an instruction: whether to buy depends on what the
market is doing right now (trend, volatility) and on how close the announcement is. The model may
propose a stance, but `constrain_stance` has the last word, so an inconsistent or reckless answer
("buy" on a bearish reading, "buy" two minutes before the payrolls) cannot reach the screen.

The stance is advice for the operator's reading, never an order: the agent's signals still come
from the strategies and still pass through the risk engine.
"""

from datetime import datetime, timedelta

from tradingagent.news.config import BlackoutConfig
from tradingagent.news.model import (
    Bias,
    EconomicEvent,
    EventKind,
    Impact,
    MarketState,
    Stance,
    is_major,
)

#: Below this confidence a reading is a guess, and a guess is not a reason to take a position.
MIN_ACTIONABLE_CONFIDENCE = 0.45
#: Against the trend, only a decisive reading would justify going the other way: we never do.
COUNTER_TREND_BLOCKED = True


def timeframes_for(event: EconomicEvent) -> tuple[str, ...]:
    """The chart timeframes the announcement concerns: the bigger the event, the higher they go."""
    if event.kind is EventKind.HEADLINE:
        return ("M15", "H1")
    if event.kind is EventKind.SPEECH:
        return ("M15", "H1")
    if event.impact is Impact.HIGH:
        return ("M15", "H1", "H4") if is_major(event.title) else ("M5", "M15", "H1")
    if event.impact is Impact.MEDIUM:
        return ("M5", "M15")
    return ("M5",)


def effect_window(event: EconomicEvent) -> tuple[datetime, datetime]:
    """When the announcement is expected to move the price: from the release to a few hours."""
    if event.kind is EventKind.SPEECH:
        minutes = 90
    elif event.kind is EventKind.HEADLINE:
        minutes = 180
    elif event.impact is Impact.HIGH:
        minutes = 240 if is_major(event.title) else 120
    elif event.impact is Impact.MEDIUM:
        minutes = 60
    else:
        minutes = 30
    return event.starts_at, event.starts_at + timedelta(minutes=minutes)


def constrain_stance(
    proposed: Stance,
    bias: Bias,
    confidence: float,
    state: MarketState,
    event: EconomicEvent,
    now: datetime,
    blackout: BlackoutConfig,
) -> Stance:
    """The stance the dashboard may show: the proposal, reduced to what the rules allow.

    Only ever reduces to `wait`: a proposal is kept when consistent, otherwise it becomes `wait`,
    and a neutral or weak reading is `wait` whatever the proposal says. A buy is never allowed on
    a bearish reading, nor a sell on a bullish one.
    """
    if now.utcoffset() is None:
        raise ValueError(f"naive datetime refused, timezone required: {now!r}")
    if bias is Bias.NEUTRAL or confidence < MIN_ACTIONABLE_CONFIDENCE:
        return Stance.WAIT
    if (proposed is Stance.BUY and bias is not Bias.BULLISH) or (
        proposed is Stance.SELL and bias is not Bias.BEARISH
    ):
        return Stance.WAIT
    if proposed is Stance.WAIT:
        return Stance.WAIT
    if _in_pre_release_window(event, now, blackout):
        return Stance.WAIT
    if not state.known:
        return Stance.WAIT
    if state.volatility == "volatile" and confidence < 0.7:
        return Stance.WAIT
    if COUNTER_TREND_BLOCKED and (
        (proposed is Stance.BUY and state.trend == "down")
        or (proposed is Stance.SELL and state.trend == "up")
    ):
        return Stance.WAIT
    return proposed


def derive_stance(
    bias: Bias,
    confidence: float,
    state: MarketState,
    event: EconomicEvent,
    now: datetime,
    blackout: BlackoutConfig,
) -> Stance:
    """The deterministic stance: follow the reading when the trend agrees, otherwise wait."""
    proposed = {Bias.BULLISH: Stance.BUY, Bias.BEARISH: Stance.SELL, Bias.NEUTRAL: Stance.WAIT}[
        bias
    ]
    # With no clear trend, only a confident reading is worth acting on.
    if state.known and state.trend == "neutral" and confidence < 0.6:
        return Stance.WAIT
    return constrain_stance(proposed, bias, confidence, state, event, now, blackout)


def _in_pre_release_window(event: EconomicEvent, now: datetime, blackout: BlackoutConfig) -> bool:
    """Before the print (or just after it) the first move is whipsaw: the stance is to wait."""
    if event.kind is EventKind.HEADLINE or event.impact is not Impact.HIGH:
        return False
    if is_major(event.title):
        before, after = blackout.major_before_minutes, blackout.major_after_minutes
    else:
        before, after = blackout.high_before_minutes, blackout.high_after_minutes
    return (
        event.starts_at - timedelta(minutes=before)
        <= now
        <= event.starts_at + timedelta(minutes=after)
    )


_STANCE_FR = {Stance.BUY: "acheter", Stance.SELL: "vendre", Stance.WAIT: "attendre"}
_TREND_FR = {"up": "haussière", "down": "baissière", "neutral": "neutre"}
_VOL_FR = {"calm": "calme", "normal": "normale", "volatile": "agitée"}


def market_phrase(state: MarketState) -> str:
    """One French clause describing the market the stance was judged against."""
    if not state.known:
        return "état du marché inconnu (pas de bougies)"
    return (
        f"tendance {_TREND_FR.get(state.trend, state.trend)}, "
        f"volatilité {_VOL_FR.get(state.volatility, state.volatility)}"
    )


def stance_phrase(stance: Stance) -> str:
    return _STANCE_FR[stance]
