from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from tradingagent.news.blackout import blackout_window, news_blackout
from tradingagent.news.config import DEFAULT_NEWS_CONFIG, BlackoutConfig, NewsConfig
from tradingagent.news.model import (
    BTCUSD,
    XAUUSD,
    EconomicEvent,
    EventKind,
    Impact,
    event_key,
)

RELEASE = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)
CONFIG = BlackoutConfig()


def event(
    title: str = "Unemployment Claims",
    *,
    at: datetime = RELEASE,
    currency: str = "USD",
    impact: Impact = Impact.HIGH,
    kind: EventKind = EventKind.RELEASE,
) -> EconomicEvent:
    return EconomicEvent(
        key=event_key("t", currency, title, at),
        starts_at=at,
        currency=currency,
        title=title,
        impact=impact,
        kind=kind,
    )


def minutes(value: int) -> timedelta:
    return timedelta(minutes=value)


def test_defaults_are_the_operators_windows() -> None:
    assert (CONFIG.high_before_minutes, CONFIG.high_after_minutes) == (15, 15)
    assert (CONFIG.major_before_minutes, CONFIG.major_after_minutes) == (30, 30)
    assert CONFIG.medium_enabled is False
    assert DEFAULT_NEWS_CONFIG.blackout == CONFIG


def test_a_high_impact_event_blocks_fifteen_minutes_either_side_inclusive() -> None:
    claims = [event()]
    for offset, blocked in [
        (-16, False),
        (-15, True),
        (-1, True),
        (0, True),
        (15, True),
        (16, False),
    ]:
        decision = news_blackout(XAUUSD, RELEASE + minutes(offset), claims, CONFIG)
        assert decision.blocked is blocked, offset


@pytest.mark.parametrize("title", ["FOMC Statement", "Non-Farm Employment Change", "CPI m/m"])
def test_the_major_releases_block_thirty_minutes_either_side(title: str) -> None:
    major = [event(title)]
    assert news_blackout(XAUUSD, RELEASE - minutes(30), major, CONFIG).blocked
    assert not news_blackout(XAUUSD, RELEASE - minutes(31), major, CONFIG).blocked
    assert news_blackout(XAUUSD, RELEASE + minutes(30), major, CONFIG).blocked
    assert not news_blackout(XAUUSD, RELEASE + minutes(31), major, CONFIG).blocked


def test_windows_follow_the_configuration() -> None:
    wide = BlackoutConfig(high_before_minutes=60, high_after_minutes=5)
    assert news_blackout(XAUUSD, RELEASE - minutes(60), [event()], wide).blocked
    assert not news_blackout(XAUUSD, RELEASE + minutes(6), [event()], wide).blocked


def test_medium_impact_is_ignored_unless_enabled() -> None:
    medium = [event("CB Consumer Confidence", impact=Impact.MEDIUM)]
    assert not news_blackout(XAUUSD, RELEASE, medium, CONFIG).blocked
    enabled = BlackoutConfig(medium_enabled=True, medium_before_minutes=5, medium_after_minutes=5)
    assert news_blackout(XAUUSD, RELEASE - minutes(5), medium, enabled).blocked
    assert not news_blackout(XAUUSD, RELEASE - minutes(6), medium, enabled).blocked


def test_low_impact_never_blocks() -> None:
    low = [event(impact=Impact.LOW)]
    assert not news_blackout(XAUUSD, RELEASE, low, BlackoutConfig(medium_enabled=True)).blocked


def test_only_the_concerned_markets_are_blocked() -> None:
    dollar = [event()]
    assert news_blackout(XAUUSD, RELEASE, dollar, CONFIG).blocked
    assert news_blackout(BTCUSD, RELEASE, dollar, CONFIG).blocked
    # A New Zealand release says nothing about gold or bitcoin.
    assert not news_blackout(XAUUSD, RELEASE, [event(currency="NZD")], CONFIG).blocked
    # A euro decision is relevant (0.5) but under the 0.8 default threshold: not a blackout.
    assert not news_blackout(XAUUSD, RELEASE, [event(currency="EUR")], CONFIG).blocked
    loose = BlackoutConfig(min_relevance=0.4)
    assert news_blackout(XAUUSD, RELEASE, [event(currency="EUR")], loose).blocked


def test_a_crypto_headline_event_concerns_bitcoin_only() -> None:
    crypto = event("SEC ETF decision", currency="BTC", kind=EventKind.SPEECH)
    assert news_blackout(BTCUSD, RELEASE, [crypto], CONFIG).blocked
    assert not news_blackout(XAUUSD, RELEASE, [crypto], CONFIG).blocked


def test_an_unscheduled_headline_never_blocks() -> None:
    headline = event("Fed signals pause", kind=EventKind.HEADLINE)
    assert not news_blackout(XAUUSD, RELEASE, [headline], CONFIG).blocked


def test_a_scheduled_speech_blocks_like_a_release() -> None:
    speech = event("Fed Chair Powell Speaks", kind=EventKind.SPEECH)
    assert news_blackout(XAUUSD, RELEASE + minutes(10), [speech], CONFIG).blocked


def test_the_gate_can_be_switched_off() -> None:
    off = BlackoutConfig(enabled=False)
    assert not news_blackout(XAUUSD, RELEASE, [event("FOMC Statement")], off).blocked
    assert blackout_window(event(), XAUUSD, off) is None


def test_the_reason_names_the_event_and_its_window() -> None:
    decision = news_blackout(XAUUSD, RELEASE - minutes(10), [event("FOMC Statement")], CONFIG)
    assert decision.blocked
    assert decision.reason.startswith("news_blackout: FOMC Statement (USD, high)")
    assert "12:30 UTC" in decision.reason
    assert "imminente" in decision.reason
    assert decision.window_start == RELEASE - minutes(30)
    assert decision.window_end == RELEASE + minutes(30)
    after = news_blackout(XAUUSD, RELEASE + minutes(5), [event("FOMC Statement")], CONFIG)
    assert "publiée" in after.reason


def test_the_most_serious_overlapping_event_is_named() -> None:
    quiet = event("ISM Services PMI", impact=Impact.MEDIUM)
    serious = event("FOMC Statement")
    enabled = BlackoutConfig(medium_enabled=True)
    decision = news_blackout(XAUUSD, RELEASE, [quiet, serious], enabled)
    assert decision.event == serious


def test_no_event_is_a_clear_gate() -> None:
    assert not news_blackout(XAUUSD, RELEASE, [], CONFIG).blocked


def test_a_naive_clock_is_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        news_blackout(XAUUSD, datetime(2026, 10, 30, 12, 30), [], CONFIG)  # noqa: DTZ001


def test_the_config_rejects_unknown_keys_and_absurd_windows() -> None:
    with pytest.raises(ValidationError):
        NewsConfig.model_validate({"blackoutt": {}})
    with pytest.raises(ValidationError):
        BlackoutConfig(high_before_minutes=-1)
    assert NewsConfig.model_validate({}) == DEFAULT_NEWS_CONFIG
