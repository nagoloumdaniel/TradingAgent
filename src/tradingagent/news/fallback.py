"""The deterministic reading of an event: what the agent says when the model cannot be asked.

No model, no network, no clock but the one passed in. The reading is deliberately modest: it
turns a *figure* into a direction (is it better or worse than expected, and does that favour the
dollar), and then the dollar into gold and bitcoin (both trade inversely to it, bitcoin less
reliably). When there is no figure -- a speech, a rate decision whose outcome is unknown -- it
says "neutral" rather than invent a direction. Confidence is low by construction: a forecast
against the previous print is an expectation, not a result.
"""

import re
from collections.abc import Mapping
from datetime import datetime

from tradingagent.news.config import DEFAULT_NEWS_CONFIG, BlackoutConfig
from tradingagent.news.model import (
    BTCUSD,
    MARKETS,
    XAUUSD,
    Bias,
    EconomicEvent,
    EventAnalysis,
    EventKind,
    MarketState,
    MarketVerdict,
    influence_score,
    input_hash,
    parse_figure,
)
from tradingagent.news.stance import (
    derive_stance,
    effect_window,
    market_phrase,
    stance_phrase,
    timeframes_for,
)

SOURCE = "fallback"
MODEL = "deterministic"

#: (pattern on the lower-cased title, sign). +1: a higher figure is good for the dollar.
#: -1: a higher figure is bad for it (unemployment, claims).
_USD_POLARITY: tuple[tuple[re.Pattern[str], int], ...] = tuple(
    (re.compile(pattern), sign)
    for pattern, sign in (
        (r"unemployment|jobless|claims", -1),
        (r"non[- ]?farm|payrolls|employment change|\badp\b|jolts|job openings", 1),
        (r"\bcpi\b|consumer price|\bppi\b|producer price|\bpce\b|inflation", 1),
        (r"retail sales|\bgdp\b|\bism\b|\bpmi\b|industrial production|durable goods", 1),
        (r"confidence|sentiment|housing starts|building permits|new home|existing home", 1),
        (r"interest rate|federal funds rate|rate decision|rate statement", 1),
        (r"trade balance|deficit", -1),
    )
)

# Crude lexicons for unscheduled items. They only fire on a clear word; a headline that matches
# both sides, or neither, is neutral.
_BULLISH_WORDS = re.compile(
    r"\b(approv\w+|inflows?|record high|all-time high|rall(y|ies|ied)|surg\w+|adopt\w+|"
    r"soar\w+|jump\w+|rebound\w+|safe[- ]haven demand|rate cuts?|dovish|easing)\b"
)
_BEARISH_WORDS = re.compile(
    r"\b(ban\w*|hack\w*|lawsuit|sues?|crash\w*|outflows?|reject\w+|sell[- ]?off|plung\w+|"
    r"slump\w*|tumbl\w+|crackdown|liquidat\w+|rate hikes?|hawkish|tightening)\b"
)
_HAWKISH = re.compile(r"hawkish|rate hike|higher for longer|further tightening|inflation remains")
_DOVISH = re.compile(r"dovish|rate cut|easing|downside risks?|pause|lower rates")


def _usd_sign(title: str) -> int:
    lowered = title.lower()
    for pattern, sign in _USD_POLARITY:
        if pattern.search(lowered):
            return sign
    return 0


def _figure_direction(event: EconomicEvent) -> tuple[int, float, str]:
    """(+1 higher/-1 lower/0 flat, confidence, wording) for the figure the event carries."""
    actual = parse_figure(event.actual)
    forecast = parse_figure(event.forecast)
    previous = parse_figure(event.previous)
    if actual is not None and forecast is not None:
        delta = actual - forecast
        if delta == 0:
            return 0, 0.4, f"publié {event.actual}, conforme au consensus {event.forecast}"
        wording = f"publié {event.actual} contre {event.forecast} attendu"
        return (1 if delta > 0 else -1), 0.6, wording
    if forecast is not None and previous is not None:
        delta = forecast - previous
        if delta == 0:
            return 0, 0.2, f"consensus {event.forecast} identique au précédent {event.previous}"
        wording = f"consensus {event.forecast} contre {event.previous} précédemment"
        return (1 if delta > 0 else -1), 0.35, wording
    return 0, 0.0, "aucun chiffre publié pour trancher"


def _release_bias(event: EconomicEvent) -> tuple[Bias, float, str]:
    """Direction for a market priced against the dollar, from the figure and its polarity."""
    sign = _usd_sign(event.title)
    if event.currency.upper() != "USD":
        # A foreign print moves the dollar only indirectly: not enough to commit to a side.
        return Bias.NEUTRAL, 0.15, f"annonce {event.currency.upper()}, effet indirect sur le dollar"
    if sign == 0:
        return Bias.NEUTRAL, 0.15, "annonce sans lecture directionnelle fiable"
    direction, confidence, wording = _figure_direction(event)
    if direction == 0:
        return Bias.NEUTRAL, confidence, wording
    usd_stronger = direction * sign > 0
    # Dollar stronger -> gold and bitcoin weaker (and the other way round).
    bias = Bias.BEARISH if usd_stronger else Bias.BULLISH
    flavour = "dollar soutenu" if usd_stronger else "dollar pénalisé"
    return bias, confidence, f"{wording} : {flavour}"


def _text_bias(event: EconomicEvent, market: str) -> tuple[Bias, float, str]:
    """Direction for a headline or a speech, from a few unambiguous words."""
    text = f"{event.title} {event.summary}".lower()
    own_market = (market == BTCUSD and event.currency.upper() in {"BTC", "CRYPTO"}) or (
        market == XAUUSD and event.currency.upper() == "XAU"
    )
    if own_market:
        bullish = _BULLISH_WORDS.search(text) is not None
        bearish = _BEARISH_WORDS.search(text) is not None
        if bullish and not bearish:
            return Bias.BULLISH, 0.4, "titre favorable au marché concerné"
        if bearish and not bullish:
            return Bias.BEARISH, 0.4, "titre défavorable au marché concerné"
        return Bias.NEUTRAL, 0.2, "titre sans direction claire"
    if event.currency.upper() == "USD":
        hawkish = _HAWKISH.search(text) is not None
        dovish = _DOVISH.search(text) is not None
        if hawkish and not dovish:
            return Bias.BEARISH, 0.35, "ton restrictif de la Fed : dollar soutenu"
        if dovish and not hawkish:
            return Bias.BULLISH, 0.35, "ton accommodant de la Fed : dollar pénalisé"
    return Bias.NEUTRAL, 0.15, "propos sans direction claire avant lecture"


def _bias(event: EconomicEvent, market: str) -> tuple[Bias, float, str]:
    if event.kind is EventKind.RELEASE:
        bias, confidence, why = _release_bias(event)
        if market == BTCUSD:
            # Bitcoin follows the dollar less tightly than gold does.
            confidence = round(confidence * 0.8, 2)
        return bias, confidence, why
    return _text_bias(event, market)


def fallback_verdict(
    event: EconomicEvent,
    market: str,
    state: MarketState,
    now: datetime,
    blackout: BlackoutConfig = DEFAULT_NEWS_CONFIG.blackout,
) -> MarketVerdict:
    bias, confidence, why = _bias(event, market)
    stance = derive_stance(bias, confidence, state, event, now, blackout)
    start, end = effect_window(event)
    direction = {Bias.BULLISH: "haussier", Bias.BEARISH: "baissier", Bias.NEUTRAL: "neutre"}[bias]
    rationale = (
        f"Lecture automatique ({direction}) : {why}. "
        f"Marché : {market_phrase(state)} ; conduite : {stance_phrase(stance)}."
    )
    return MarketVerdict(
        symbol=market,
        bias=bias,
        confidence=round(confidence, 2),
        influence=influence_score(event, market),
        stance=stance,
        timeframes=timeframes_for(event),
        window_start=start,
        window_end=end,
        rationale=rationale,
    )


def fallback_analysis(
    event: EconomicEvent,
    states: Mapping[str, MarketState],
    now: datetime,
    blackout: BlackoutConfig = DEFAULT_NEWS_CONFIG.blackout,
) -> EventAnalysis:
    """Both markets read from the figures alone: always available, never invented."""
    verdicts = tuple(
        fallback_verdict(event, market, states.get(market, MarketState(market)), now, blackout)
        for market in MARKETS
    )
    return EventAnalysis(
        event_key=event.key,
        verdicts=verdicts,
        source=SOURCE,
        model=MODEL,
        analysed_at=now,
        input_hash=input_hash(event),
    )
