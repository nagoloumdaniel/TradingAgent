from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.news.config import BlackoutConfig
from tradingagent.news.model import (
    XAUUSD,
    Bias,
    EconomicEvent,
    EventKind,
    Impact,
    MarketState,
    Stance,
    event_key,
)
from tradingagent.news.stance import (
    constrain_stance,
    derive_stance,
    effect_window,
    market_phrase,
    timeframes_for,
)

AT = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)
LATER = AT + timedelta(hours=2)  # well after any blackout window
NOW_KNOWN = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)
CONFIG = BlackoutConfig()


def make(
    title: str = "Retail Sales m/m",
    impact: Impact = Impact.HIGH,
    kind: EventKind = EventKind.RELEASE,
) -> EconomicEvent:
    return EconomicEvent(
        key=event_key("t", "USD", title, AT),
        starts_at=AT,
        currency="USD",
        title=title,
        impact=impact,
        kind=kind,
    )


def state(trend: str = "up", volatility: str = "normal") -> MarketState:
    return MarketState(XAUUSD, trend=trend, volatility=volatility, as_of=NOW_KNOWN)


def test_timeframes_grow_with_the_event() -> None:
    assert timeframes_for(make("Non-Farm Employment Change")) == ("M15", "H1", "H4")
    assert timeframes_for(make("Retail Sales m/m")) == ("M5", "M15", "H1")
    assert timeframes_for(make(impact=Impact.MEDIUM)) == ("M5", "M15")
    assert timeframes_for(make(impact=Impact.LOW)) == ("M5",)
    assert timeframes_for(make(kind=EventKind.SPEECH)) == ("M15", "H1")


def test_the_effect_window_starts_at_the_release_and_scales_with_impact() -> None:
    start, end = effect_window(make("FOMC Statement"))
    assert start == AT
    assert end - start == timedelta(hours=4)
    assert effect_window(make())[1] - AT == timedelta(hours=2)
    assert effect_window(make(impact=Impact.LOW))[1] - AT == timedelta(minutes=30)


def test_a_consistent_confident_reading_with_the_trend_is_kept() -> None:
    assert constrain_stance(Stance.BUY, Bias.BULLISH, 0.7, state("up"), make(), LATER, CONFIG) is (
        Stance.BUY
    )
    assert constrain_stance(
        Stance.SELL, Bias.BEARISH, 0.7, state("down"), make(), LATER, CONFIG
    ) is (Stance.SELL)


def test_a_neutral_or_weak_reading_is_always_wait() -> None:
    assert constrain_stance(Stance.BUY, Bias.NEUTRAL, 0.9, state(), make(), LATER, CONFIG) is (
        Stance.WAIT
    )
    assert constrain_stance(Stance.BUY, Bias.BULLISH, 0.3, state(), make(), LATER, CONFIG) is (
        Stance.WAIT
    )


def test_a_stance_contradicting_the_reading_is_refused() -> None:
    assert constrain_stance(Stance.BUY, Bias.BEARISH, 0.9, state("up"), make(), LATER, CONFIG) is (
        Stance.WAIT
    )
    assert constrain_stance(
        Stance.SELL, Bias.BULLISH, 0.9, state("down"), make(), LATER, CONFIG
    ) is (Stance.WAIT)


def test_it_never_goes_against_the_trend() -> None:
    assert constrain_stance(
        Stance.BUY, Bias.BULLISH, 0.9, state("down"), make(), LATER, CONFIG
    ) is (Stance.WAIT)
    assert constrain_stance(Stance.SELL, Bias.BEARISH, 0.9, state("up"), make(), LATER, CONFIG) is (
        Stance.WAIT
    )


def test_around_a_high_impact_release_the_stance_is_wait() -> None:
    before = AT - timedelta(minutes=10)
    assert constrain_stance(Stance.BUY, Bias.BULLISH, 0.9, state("up"), make(), before, CONFIG) is (
        Stance.WAIT
    )
    after = AT + timedelta(minutes=14)
    assert constrain_stance(Stance.BUY, Bias.BULLISH, 0.9, state("up"), make(), after, CONFIG) is (
        Stance.WAIT
    )


def test_an_unknown_market_cannot_justify_a_position() -> None:
    assert constrain_stance(
        Stance.BUY, Bias.BULLISH, 0.9, MarketState(XAUUSD), make(), LATER, CONFIG
    ) is (Stance.WAIT)


def test_a_volatile_market_needs_a_strong_reading() -> None:
    volatile = state("up", "volatile")
    assert constrain_stance(Stance.BUY, Bias.BULLISH, 0.6, volatile, make(), LATER, CONFIG) is (
        Stance.WAIT
    )
    assert constrain_stance(Stance.BUY, Bias.BULLISH, 0.8, volatile, make(), LATER, CONFIG) is (
        Stance.BUY
    )


def test_derive_follows_the_reading_when_the_trend_agrees() -> None:
    assert derive_stance(Bias.BULLISH, 0.6, state("up"), make(), LATER, CONFIG) is Stance.BUY
    assert derive_stance(Bias.BEARISH, 0.6, state("down"), make(), LATER, CONFIG) is Stance.SELL
    assert derive_stance(Bias.NEUTRAL, 0.9, state("up"), make(), LATER, CONFIG) is Stance.WAIT


def test_derive_with_no_trend_wants_a_confident_reading() -> None:
    assert derive_stance(Bias.BULLISH, 0.5, state("neutral"), make(), LATER, CONFIG) is Stance.WAIT
    assert derive_stance(Bias.BULLISH, 0.7, state("neutral"), make(), LATER, CONFIG) is Stance.BUY


def test_a_naive_clock_is_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        constrain_stance(
            Stance.BUY,
            Bias.BULLISH,
            0.9,
            state(),
            make(),
            datetime(2026, 10, 30, 14),  # noqa: DTZ001
            CONFIG,
        )


def test_the_market_phrase_is_honest_about_what_it_does_not_know() -> None:
    assert "inconnu" in market_phrase(MarketState(XAUUSD))
    assert "haussière" in market_phrase(state("up"))
