"""The morning summary written by the rules, when the model cannot be asked.

Pure. It aggregates the readings already made for the day (the model's or the rules'), weighted by
how much each event matters to the market, and names what is still to come. It is deliberately
conservative: with a high-impact announcement still ahead the day's stance is to wait for it, and
without a trend that agrees with the reading it is to wait as well.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from tradingagent.news import fallback
from tradingagent.news.model import (
    MARKETS,
    Bias,
    DailyBrief,
    EconomicEvent,
    EventAnalysis,
    Impact,
    MarketBrief,
    MarketState,
    Stance,
    influence_score,
)
from tradingagent.news.stance import market_phrase

#: A day's net lean must clear this to be called a direction.
LEAN_THRESHOLD = 0.15
#: Below this influence an event is background noise for the summary.
MIN_INFLUENCE = 30
#: A still-to-come announcement this strong holds the day's stance at "wait".
HOLD_INFLUENCE = 50

_BIAS_SIGN = {Bias.BULLISH: 1.0, Bias.BEARISH: -1.0, Bias.NEUTRAL: 0.0}
_BIAS_FR = {
    Bias.BULLISH: "plutôt haussière",
    Bias.BEARISH: "plutôt baissière",
    Bias.NEUTRAL: "neutre",
}


def _verdict_for(
    event: EconomicEvent,
    market: str,
    analyses: Mapping[str, EventAnalysis],
    states: Mapping[str, MarketState],
    now: datetime,
) -> tuple[Bias, float]:
    analysis = analyses.get(event.key)
    if analysis is not None:
        for verdict in analysis.verdicts:
            if verdict.symbol == market:
                return verdict.bias, verdict.confidence
    verdict = fallback.fallback_verdict(event, market, states.get(market, MarketState(market)), now)
    return verdict.bias, verdict.confidence


def fallback_brief(
    day: str,
    events: Sequence[EconomicEvent],
    analyses: Mapping[str, EventAnalysis],
    states: Mapping[str, MarketState],
    now: datetime,
) -> DailyBrief:
    markets = tuple(_market_brief(market, events, analyses, states, now) for market in MARKETS)
    return DailyBrief(
        day=day,
        headline=_headline(events, now),
        markets=markets,
        source=fallback.SOURCE,
        model=fallback.MODEL,
        generated_at=now,
    )


def _headline(events: Sequence[EconomicEvent], now: datetime) -> str:
    strong = [
        e
        for e in events
        if e.impact is Impact.HIGH and max(influence_score(e, m) for m in MARKETS) >= HOLD_INFLUENCE
    ]
    if not events:
        return "Aucune annonce au calendrier pour cette journée."
    if not strong:
        return (
            f"{len(events)} annonce(s) au calendrier, aucune de fort impact : journée plutôt calme."
        )
    names = ", ".join(f"{e.title} ({e.starts_at.strftime('%H:%M')} UTC)" for e in strong[:3])
    more = f" et {len(strong) - 3} autre(s)" if len(strong) > 3 else ""
    return f"{len(strong)} annonce(s) à fort impact : {names}{more}."


def _market_brief(
    market: str,
    events: Sequence[EconomicEvent],
    analyses: Mapping[str, EventAnalysis],
    states: Mapping[str, MarketState],
    now: datetime,
) -> MarketBrief:
    state = states.get(market, MarketState(market))
    weighted = 0.0
    total = 0.0
    ahead: list[EconomicEvent] = []
    for event in events:
        influence = influence_score(event, market)
        if influence < MIN_INFLUENCE:
            continue
        bias, confidence = _verdict_for(event, market, analyses, states, now)
        weight = influence / 100.0 * confidence
        weighted += _BIAS_SIGN[bias] * weight
        total += weight
        if event.starts_at >= now and influence >= HOLD_INFLUENCE and event.impact is Impact.HIGH:
            ahead.append(event)
    lean = weighted / total if total else 0.0
    bias = (
        Bias.BULLISH
        if lean > LEAN_THRESHOLD
        else Bias.BEARISH
        if lean < -LEAN_THRESHOLD
        else Bias.NEUTRAL
    )
    stance = Stance.WAIT
    if not ahead and state.known:
        if bias is Bias.BULLISH and state.trend == "up":
            stance = Stance.BUY
        elif bias is Bias.BEARISH and state.trend == "down":
            stance = Stance.SELL
    if ahead:
        first = min(ahead, key=lambda e: e.starts_at)
        at = first.starts_at.strftime("%H:%M")
        advice = f"attendre {first.title} ({at} UTC) avant de prendre position"
    elif stance is Stance.WAIT:
        advice = "attendre : pas de lecture assez nette ou tendance qui ne la confirme pas"
    else:
        advice = "la tendance confirme la lecture"
    summary = (
        f"Lecture automatique, {_BIAS_FR[bias]} sur la base des annonces du jour ; "
        f"marché : {market_phrase(state)} ; conduite : {advice}."
    )
    return MarketBrief(market, bias, stance, summary)
