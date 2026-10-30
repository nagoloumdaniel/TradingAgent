from datetime import UTC, datetime

import pytest

from tradingagent.news.model import (
    BTCUSD,
    XAUUSD,
    EconomicEvent,
    EventKind,
    Impact,
    currency_relevance,
    event_key,
    influence_score,
    input_hash,
    is_major,
    is_relevant,
    parse_figure,
    surprise_bonus,
    type_boost,
)

AT = datetime(2026, 10, 30, 12, 30, tzinfo=UTC)


def make(
    title: str = "Core PCE Price Index m/m",
    *,
    currency: str = "USD",
    impact: Impact = Impact.HIGH,
    kind: EventKind = EventKind.RELEASE,
    forecast: str | None = None,
    previous: str | None = None,
    actual: str | None = None,
) -> EconomicEvent:
    return EconomicEvent(
        key=event_key("test", currency, title, AT),
        starts_at=AT,
        currency=currency,
        title=title,
        impact=impact,
        kind=kind,
        forecast=forecast,
        previous=previous,
        actual=actual,
    )


def test_a_naive_start_is_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        EconomicEvent(
            key="k",
            starts_at=datetime(2026, 10, 30, 12, 30),  # noqa: DTZ001
            currency="USD",
            title="x",
            impact=Impact.LOW,
        )


def test_the_key_is_stable_and_blind_to_case_and_spacing() -> None:
    first = event_key("ff", "usd", "Core  PCE Price Index m/m", AT)
    assert first == event_key("ff", "USD", "core pce price index m/m", AT)
    assert first != event_key("ff", "USD", "core pce price index m/m", AT.replace(hour=13))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4.00%", 4.0),
        ("198K", 198_000.0),
        ("-47", -47.0),
        ("1,5", 1.5),
        ("1.2M", 1_200_000.0),
        ("", None),
        (None, None),
        ("3.2|1.5", None),
        ("n/a", None),
    ],
)
def test_figures_are_parsed_or_refused(text: str | None, expected: float | None) -> None:
    assert parse_figure(text) == expected


def test_a_high_impact_dollar_release_outranks_a_low_impact_one() -> None:
    high = influence_score(make(impact=Impact.HIGH), XAUUSD)
    low = influence_score(make("Wholesale Inventories m/m", impact=Impact.LOW), XAUUSD)
    assert high > low
    assert 0 <= low < high <= 100


def test_the_major_releases_are_boosted() -> None:
    plain = make("Chicago PMI", impact=Impact.HIGH)
    for title in ("Non-Farm Employment Change", "CPI m/m", "FOMC Statement", "Federal Funds Rate"):
        assert influence_score(make(title), XAUUSD) > influence_score(plain, XAUUSD)
        assert is_major(title)
    assert not is_major("Chicago PMI")


def test_the_dollar_matters_more_than_a_minor_currency() -> None:
    usd = influence_score(make("Interest Rate Decision", currency="USD"), XAUUSD)
    nzd = influence_score(make("Interest Rate Decision", currency="NZD"), XAUUSD)
    assert usd > nzd


def test_each_market_weighs_its_own_catalysts() -> None:
    etf = make("Bitcoin ETF approved by SEC", currency="BTC", kind=EventKind.HEADLINE)
    assert influence_score(etf, BTCUSD) > influence_score(etf, XAUUSD)
    gold = make("Gold hits record as bullion rallies", currency="XAU", kind=EventKind.HEADLINE)
    assert influence_score(gold, XAUUSD) > influence_score(gold, BTCUSD)


def test_a_surprise_adds_points_only_when_the_actual_is_known() -> None:
    forecast = make("CPI m/m", forecast="0.3%", previous="0.2%")
    printed = make("CPI m/m", forecast="0.3%", previous="0.2%", actual="0.6%")
    assert surprise_bonus(forecast) == 0
    assert surprise_bonus(printed) > 0
    assert influence_score(printed, XAUUSD) > influence_score(forecast, XAUUSD)
    # In line: no surprise, no bonus.
    assert surprise_bonus(make("CPI m/m", forecast="0.3%", actual="0.3%")) == 0


def test_the_surprise_is_capped_and_a_zero_forecast_does_not_explode() -> None:
    assert surprise_bonus(make("CPI m/m", forecast="0.0", actual="5.0")) == 15


def test_the_score_never_leaves_zero_to_one_hundred() -> None:
    huge = make("Non-Farm Employment Change", forecast="100K", previous="100K", actual="900K")
    assert influence_score(huge, XAUUSD) == 100
    assert influence_score(make(currency="XYZ", impact=Impact.LOW), BTCUSD) >= 0


def test_a_minor_headline_is_discounted() -> None:
    event = make("Gold steady", currency="XAU", impact=Impact.MEDIUM, kind=EventKind.RELEASE)
    headline = make("Gold steady", currency="XAU", impact=Impact.MEDIUM, kind=EventKind.HEADLINE)
    assert influence_score(headline, XAUUSD) < influence_score(event, XAUUSD)


def test_relevance_defaults_for_an_unknown_currency() -> None:
    assert currency_relevance(XAUUSD, "usd") == 1.0
    assert currency_relevance(XAUUSD, "ZZZ") < 0.1


def test_the_calendar_keeps_only_what_can_move_our_markets() -> None:
    assert is_relevant(make(currency="USD", impact=Impact.LOW))
    assert is_relevant(make(currency="EUR", impact=Impact.LOW))
    assert is_relevant(make(currency="JPY", impact=Impact.HIGH))
    assert not is_relevant(make(currency="JPY", impact=Impact.LOW))
    assert not is_relevant(make(currency="NZD", impact=Impact.HIGH))


def test_the_input_hash_follows_the_figures() -> None:
    base = make("CPI m/m", forecast="0.3%")
    assert input_hash(base) == input_hash(make("CPI m/m", forecast="0.3%"))
    assert input_hash(base) != input_hash(make("CPI m/m", forecast="0.4%"))
    assert input_hash(base) != input_hash(make("CPI m/m", forecast="0.3%", actual="0.5%"))


def test_type_boost_is_the_strongest_match_not_the_sum() -> None:
    assert type_boost("FOMC Press Conference", XAUUSD) == 30
    assert type_boost("Retail Sales m/m", XAUUSD) == 9
    assert type_boost("Nothing in particular", XAUUSD) == 0
