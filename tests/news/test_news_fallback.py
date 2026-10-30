from datetime import UTC, datetime, timedelta

from tradingagent.news.fallback import fallback_analysis, fallback_verdict
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
    input_hash,
)

AT = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)
NOW = AT - timedelta(hours=6)  # far from any blackout window
SEEN = datetime(2026, 10, 30, 6, 0, tzinfo=UTC)


def make(
    title: str,
    *,
    currency: str = "USD",
    impact: Impact = Impact.HIGH,
    kind: EventKind = EventKind.RELEASE,
    forecast: str | None = None,
    previous: str | None = None,
    actual: str | None = None,
    summary: str = "",
) -> EconomicEvent:
    return EconomicEvent(
        key=event_key("t", currency, title, AT),
        starts_at=AT,
        currency=currency,
        title=title,
        impact=impact,
        kind=kind,
        forecast=forecast,
        previous=previous,
        actual=actual,
        summary=summary,
    )


def state(symbol: str, trend: str = "down") -> MarketState:
    return MarketState(symbol, trend=trend, volatility="normal", as_of=SEEN)


def test_a_hotter_inflation_forecast_is_bearish_for_gold() -> None:
    event = make("CPI m/m", forecast="0.4%", previous="0.2%")
    verdict = fallback_verdict(event, XAUUSD, state(XAUUSD), NOW)
    assert verdict.bias is Bias.BEARISH
    assert 0 < verdict.confidence <= 0.4
    assert "dollar soutenu" in verdict.rationale


def test_a_higher_unemployment_forecast_is_bullish_for_gold() -> None:
    event = make("Unemployment Rate", forecast="4.5%", previous="4.2%")
    verdict = fallback_verdict(event, XAUUSD, state(XAUUSD, "up"), NOW)
    assert verdict.bias is Bias.BULLISH


def test_a_published_figure_beats_the_forecast_reading() -> None:
    # The forecast was cooler than the previous (bullish for gold) but the print came in hot.
    event = make("CPI m/m", forecast="0.1%", previous="0.2%", actual="0.5%")
    verdict = fallback_verdict(event, XAUUSD, state(XAUUSD), NOW)
    assert verdict.bias is Bias.BEARISH
    assert verdict.confidence >= 0.5


def test_bitcoin_follows_the_dollar_with_less_conviction() -> None:
    event = make("Retail Sales m/m", forecast="0.6%", previous="0.2%")
    gold = fallback_verdict(event, XAUUSD, state(XAUUSD), NOW)
    bitcoin = fallback_verdict(event, BTCUSD, state(BTCUSD), NOW)
    assert gold.bias is bitcoin.bias is Bias.BEARISH
    assert bitcoin.confidence < gold.confidence


def test_a_decision_without_figures_is_neutral_and_waits() -> None:
    event = make("FOMC Statement")
    verdict = fallback_verdict(event, XAUUSD, state(XAUUSD), NOW)
    assert verdict.bias is Bias.NEUTRAL
    assert verdict.stance is Stance.WAIT


def test_a_foreign_release_does_not_commit_to_a_side() -> None:
    event = make("German CPI m/m", currency="EUR", forecast="0.5%", previous="0.1%")
    assert fallback_verdict(event, XAUUSD, state(XAUUSD), NOW).bias is Bias.NEUTRAL


def test_equal_forecast_and_previous_is_neutral() -> None:
    event = make("Retail Sales m/m", forecast="0.2%", previous="0.2%")
    assert fallback_verdict(event, XAUUSD, state(XAUUSD), NOW).bias is Bias.NEUTRAL


def test_the_stance_follows_the_market_state() -> None:
    event = make("Retail Sales m/m", forecast="0.9%", previous="0.2%", actual="1.4%")
    with_trend = fallback_verdict(event, XAUUSD, state(XAUUSD, "down"), NOW)
    against_trend = fallback_verdict(event, XAUUSD, state(XAUUSD, "up"), NOW)
    unknown = fallback_verdict(event, XAUUSD, MarketState(XAUUSD), NOW)
    assert with_trend.bias is Bias.BEARISH
    assert with_trend.stance is Stance.SELL
    assert against_trend.stance is Stance.WAIT
    assert unknown.stance is Stance.WAIT
    assert "inconnu" in unknown.rationale


def test_a_crypto_headline_is_read_for_bitcoin_only() -> None:
    event = make(
        "SEC approves spot ether ETF as inflows surge",
        currency="BTC",
        impact=Impact.MEDIUM,
        kind=EventKind.HEADLINE,
    )
    assert fallback_verdict(event, BTCUSD, state(BTCUSD, "up"), NOW).bias is Bias.BULLISH
    assert fallback_verdict(event, XAUUSD, state(XAUUSD, "up"), NOW).bias is Bias.NEUTRAL


def test_a_mixed_headline_is_neutral() -> None:
    event = make(
        "Bitcoin rallies then crashes",
        currency="BTC",
        impact=Impact.MEDIUM,
        kind=EventKind.HEADLINE,
    )
    assert fallback_verdict(event, BTCUSD, state(BTCUSD), NOW).bias is Bias.NEUTRAL


def test_a_hawkish_fed_speech_is_bearish_for_gold() -> None:
    event = make(
        "Fed Governor on inflation",
        kind=EventKind.SPEECH,
        summary="Inflation remains elevated; further tightening may be needed.",
    )
    assert fallback_verdict(event, XAUUSD, state(XAUUSD), NOW).bias is Bias.BEARISH


def test_the_analysis_covers_both_markets_and_carries_the_hash() -> None:
    event = make("CPI m/m", forecast="0.4%", previous="0.2%")
    analysis = fallback_analysis(event, {XAUUSD: state(XAUUSD)}, NOW)
    assert [v.symbol for v in analysis.verdicts] == [XAUUSD, BTCUSD]
    assert analysis.source == "fallback"
    assert analysis.input_hash == input_hash(event)
    assert analysis.event_key == event.key
    # A market with no state reading is "unknown", not an error.
    assert analysis.verdicts[1].stance is Stance.WAIT
    assert all(v.window_end > v.window_start for v in analysis.verdicts)
    assert all(0 <= v.influence <= 100 for v in analysis.verdicts)
