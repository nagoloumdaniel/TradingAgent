from datetime import UTC, datetime, timedelta

from tradingagent.news.brief import fallback_brief
from tradingagent.news.model import (
    BTCUSD,
    XAUUSD,
    Bias,
    EconomicEvent,
    EventKind,
    Impact,
    MarketState,
    Stance,
    event_key,
)

DAY = "2026-10-30"
MORNING = datetime(2026, 10, 30, 6, 0, tzinfo=UTC)
EVENING = datetime(2026, 10, 30, 20, 0, tzinfo=UTC)
AT = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)


def event(title: str = "Core CPI m/m", impact: Impact = Impact.HIGH) -> EconomicEvent:
    return EconomicEvent(
        key=event_key("t", "USD", title, AT),
        starts_at=AT,
        currency="USD",
        title=title,
        impact=impact,
        kind=EventKind.RELEASE,
        forecast="0.3%",
        previous="0.2%",
    )


def states(trend: str = "up") -> dict[str, MarketState]:
    return {
        XAUUSD: MarketState(XAUUSD, trend, "normal", 4000.0, MORNING),
        BTCUSD: MarketState(BTCUSD, trend, "normal", 80000.0, MORNING),
    }


def brief_for(market: str, events: list[EconomicEvent], now: datetime, trend: str = "up"):
    brief = fallback_brief(DAY, events, {}, states(trend), now)
    return brief, next(m for m in brief.markets if m.symbol == market)


def test_an_empty_day_says_so_and_waits() -> None:
    brief, gold = brief_for(XAUUSD, [], MORNING)
    assert "Aucune annonce" in brief.headline
    assert gold.stance is Stance.WAIT and gold.bias is Bias.NEUTRAL
    assert brief.source == "fallback" and brief.model == "deterministic"
    assert [m.symbol for m in brief.markets] == [XAUUSD, BTCUSD]


def test_a_major_release_still_ahead_holds_the_stance_at_wait() -> None:
    brief, gold = brief_for(XAUUSD, [event()], MORNING)
    assert gold.stance is Stance.WAIT
    assert "Core CPI" in gold.summary and "12:30" in gold.summary
    assert "fort impact" in brief.headline


def test_once_the_release_is_behind_a_confirming_trend_can_lead_somewhere() -> None:
    # The same day, evening: the print is out; the rules read it and the trend is up.
    _, up = brief_for(XAUUSD, [event()], EVENING, trend="up")
    _, flat = brief_for(XAUUSD, [event()], EVENING, trend="neutral")
    assert flat.stance is Stance.WAIT
    assert up.stance in (Stance.BUY, Stance.WAIT)
    if up.stance is Stance.BUY:
        assert up.bias is Bias.BULLISH


def test_low_impact_noise_does_not_move_the_summary() -> None:
    low = event("Some minor survey", Impact.LOW)
    brief, gold = brief_for(XAUUSD, [low], MORNING)
    assert gold.bias is Bias.NEUTRAL and gold.stance is Stance.WAIT
    assert "aucune de fort impact" in brief.headline


def test_the_brief_is_pure() -> None:
    first = fallback_brief(DAY, [event()], {}, states(), MORNING)
    second = fallback_brief(DAY, [event()], {}, states(), MORNING + timedelta(seconds=0))
    assert first == second
