import asyncio
import codecs
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tradingagent.news.faireconomy import (
    NEXT_WEEK_URL,
    THIS_WEEK_URL,
    FaireconomySource,
)
from tradingagent.news.model import EventKind, Impact
from tradingagent.news.ports import EMPTY_CALENDAR, HttpError, HttpResponse
from tradingagent.news.rss import FEEDS, FeedSpec, RssSource

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 30, 15, 0, tzinfo=UTC)


class FakeHttp:
    def __init__(self, responses: dict[str, HttpResponse | Exception]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    async def get(self, url: str, *, timeout: float) -> HttpResponse:
        self.calls.append(url)
        outcome = self._responses[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def ok(body: bytes, status: int = 200, **headers: str) -> HttpResponse:
    return HttpResponse(status, body, headers)


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def spec(name: str) -> FeedSpec:
    return next(feed for feed in FEEDS if feed.name == name)


def fetch_rss(name: str, body: bytes, *, lookback_hours: int = 24 * 60, status: int = 200):
    http = FakeHttp({spec(name).url: ok(body, status)})
    source = RssSource(spec(name), http, lookback_hours=lookback_hours)
    return asyncio.run(source.fetch(NOW))


# --- Faireconomy -------------------------------------------------------------------------


def fetch_calendar(
    response: HttpResponse | Exception, *, optional: bool = False, url: str = THIS_WEEK_URL
):
    http = FakeHttp({url: response})
    source = FaireconomySource(http, url=url, optional=optional)
    return asyncio.run(source.fetch(NOW))


def test_the_weekly_calendar_is_parsed_into_utc_events() -> None:
    result = fetch_calendar(ok(fixture("faireconomy_thisweek.json")))
    assert result.report.ok and result.report.status == 200
    assert result.report.items == len(result.events) > 0
    assert all(e.starts_at.utcoffset().total_seconds() == 0 for e in result.events)
    fomc = next(e for e in result.events if e.title == "FOMC Statement")
    # 14:00 New York (EDT, UTC-4 on 2026-10-28) is 18:00 UTC.
    assert fomc.starts_at == datetime(2026, 10, 28, 18, 0, tzinfo=UTC)
    assert fomc.currency == "USD"
    assert fomc.impact is Impact.HIGH
    assert result.covers == (result.events[0].starts_at, result.events[-1].starts_at)


def test_only_what_matters_to_gold_and_bitcoin_is_kept() -> None:
    result = fetch_calendar(ok(fixture("faireconomy_thisweek.json")))
    raw = json.loads(fixture("faireconomy_thisweek.json"))
    assert len(result.events) < len(raw)
    # Minor-currency low-impact noise is gone, the dollar's calendar is whole.
    assert "NZD" not in {e.currency for e in result.events if e.impact is Impact.LOW}
    assert {"Federal Funds Rate", "FOMC Press Conference"} <= {e.title for e in result.events}


def test_a_press_conference_is_a_speech_and_a_release_is_a_release() -> None:
    result = fetch_calendar(ok(fixture("faireconomy_thisweek.json")))
    by_title = {e.title: e for e in result.events}
    assert by_title["FOMC Press Conference"].kind is EventKind.SPEECH
    assert by_title["Federal Funds Rate"].kind is EventKind.RELEASE


def test_forecast_and_previous_are_kept_and_actual_is_absent() -> None:
    result = fetch_calendar(ok(fixture("faireconomy_thisweek.json")))
    with_figures = [e for e in result.events if e.forecast and e.previous]
    assert with_figures
    assert all(e.actual is None for e in result.events)


def test_the_same_week_fetched_twice_gives_the_same_keys() -> None:
    first = fetch_calendar(ok(fixture("faireconomy_thisweek.json")))
    second = fetch_calendar(ok(fixture("faireconomy_thisweek.json")))
    assert [e.key for e in first.events] == [e.key for e in second.events]
    assert len({e.key for e in first.events}) == len(first.events)


def test_an_unpublished_next_week_is_a_note_not_a_failure() -> None:
    result = fetch_calendar(ok(b"Not Found", 404), optional=True, url=NEXT_WEEK_URL)
    assert result.report.ok
    assert result.report.note and "non encore publiée" in result.report.note
    assert result.events == ()


def test_a_404_on_the_required_week_is_a_failure() -> None:
    result = fetch_calendar(ok(b"Not Found", 404))
    assert not result.report.ok
    assert result.report.status == 404
    assert result.events == ()


def test_rate_limiting_is_reported_with_the_delay_the_host_asked_for() -> None:
    result = fetch_calendar(ok(b"slow down", 429, **{"Retry-After": "290"}))
    assert not result.report.ok
    assert result.report.status == 429
    assert result.report.retry_after == 290
    assert "290" in (result.report.error or "")


def test_the_retry_delay_is_bounded() -> None:
    assert fetch_calendar(ok(b"", 429, **{"retry-after": "5"})).report.retry_after == 60
    assert fetch_calendar(ok(b"", 429, **{"retry-after": "999999"})).report.retry_after == 3600
    assert fetch_calendar(ok(b"", 429, **{"retry-after": "soon"})).report.retry_after == 300
    assert fetch_calendar(ok(b"", 429)).report.retry_after == 300


def test_an_html_page_served_as_200_is_an_error_not_an_empty_calendar() -> None:
    result = fetch_calendar(ok(b"<html><body>Request denied</body></html>"))
    assert not result.report.ok
    assert "non JSON" in (result.report.error or "")


def test_a_json_object_instead_of_an_array_is_refused() -> None:
    result = fetch_calendar(ok(b'{"error": "nope"}'))
    assert not result.report.ok


def test_a_transport_failure_is_reported_not_raised() -> None:
    result = fetch_calendar(HttpError("TimeoutError: timed out"))
    assert not result.report.ok
    assert result.report.status is None
    assert "timed out" in (result.report.error or "")


def test_malformed_rows_are_skipped_individually() -> None:
    rows = [
        {"title": "ok", "country": "USD", "date": "2026-10-30T08:30:00-04:00", "impact": "High"},
        {"title": "no date", "country": "USD", "impact": "High"},
        {"title": "bad date", "country": "USD", "date": "yesterday", "impact": "High"},
        {"title": "naive", "country": "USD", "date": "2026-10-30T08:30:00", "impact": "High"},
        "not a row",
        {"country": "USD", "date": "2026-10-30T08:30:00-04:00"},
    ]
    result = fetch_calendar(ok(json.dumps(rows).encode()))
    assert [e.title for e in result.events] == ["ok"]


def test_a_weekday_200_without_any_relevant_event_is_a_degraded_calendar() -> None:
    # NOW is a Friday: an empty week then is a broken export, not a quiet calendar.
    result = fetch_calendar(ok(b"[]"))
    assert not result.report.ok
    assert result.report.status == 200
    assert EMPTY_CALENDAR in (result.report.error or "")
    assert result.report.retry_after is None  # no back-off: try again at the next poll
    assert result.events == ()


def test_only_irrelevant_rows_count_as_an_empty_calendar_too() -> None:
    rows = [{"title": "Bank Holiday", "country": "NZD", "date": "2026-10-30T08:30:00-04:00"}]
    result = fetch_calendar(ok(json.dumps(rows).encode()))
    assert not result.report.ok and EMPTY_CALENDAR in (result.report.error or "")


def test_an_empty_optional_week_or_a_weekend_is_not_degraded() -> None:
    assert fetch_calendar(ok(b"[]"), optional=True, url=NEXT_WEEK_URL).report.ok
    saturday = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    http = FakeHttp({THIS_WEEK_URL: ok(b"[]")})
    result = asyncio.run(FaireconomySource(http).fetch(saturday))
    assert result.report.ok and result.events == ()


def row(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "Core CPI m/m",
        "country": "USD",
        "date": "2026-10-30T08:30:00-04:00",
        "impact": "High",
        "forecast": "0.3%",
        "previous": "0.2%",
    }
    base.update(changes)
    return base


@pytest.mark.parametrize("country", ["", "usd", "U", "TOOLONGCODE", "US D", "U$D", "éu", "<b>"])
def test_a_currency_that_is_not_a_code_skips_that_event_only(country: str) -> None:
    rows = [row(country=country, title="bad"), row(title="good")]
    result = fetch_calendar(ok(json.dumps(rows).encode()))
    titles = [e.title for e in result.events]
    if country == "usd":  # lower case is normalised, not refused
        assert sorted(titles) == ["bad", "good"]
    else:
        assert titles == ["good"]
    assert result.report.ok


def test_figures_and_labels_are_cut_to_the_column_sizes() -> None:
    long = "9" * 400
    rows = [row(forecast=long, previous=long, actual=long, title="T" * 600)]
    [event] = fetch_calendar(ok(json.dumps(rows).encode())).events
    assert len(event.forecast or "") == 32
    assert len(event.previous or "") == 32
    assert len(event.actual or "") == 32
    assert len(event.title) <= 240
    assert len(event.country) <= 8


def test_a_non_https_event_url_is_dropped_and_an_https_one_kept() -> None:
    rows = [
        row(title="a", url="javascript:alert(1)"),
        row(title="b", url="http://example.com/x"),
        row(title="c", url="https://www.forexfactory.com/calendar"),
        row(title="d", url="https://" + "h" * 600),
    ]
    by_title = {e.title: e for e in fetch_calendar(ok(json.dumps(rows).encode())).events}
    assert by_title["a"].url is None and by_title["b"].url is None
    assert by_title["c"].url == "https://www.forexfactory.com/calendar"
    assert by_title["d"].url is None  # past the column: dropped, never cut into another URL


# --- RSS ---------------------------------------------------------------------------------


def test_fed_speeches_are_speeches_with_utc_dates() -> None:
    result = fetch_rss("fed_speeches", fixture("fed_speeches.xml"))
    assert result.report.ok
    assert result.events
    assert all(e.kind is EventKind.SPEECH and e.currency == "USD" for e in result.events)
    bowman = next(e for e in result.events if e.title.startswith("Bowman"))
    assert bowman.starts_at == datetime(2026, 10, 15, 2, 55, tzinfo=UTC)
    assert bowman.url and bowman.url.startswith("https://www.federalreserve.gov/")
    assert "&uuml;" not in "".join(e.summary for e in result.events)


def test_the_lookback_drops_old_items() -> None:
    recent = fetch_rss("fed_speeches", fixture("fed_speeches.xml"), lookback_hours=36)
    assert recent.events == ()
    assert recent.report.ok


def test_ecb_dates_with_an_offset_are_converted_to_utc() -> None:
    result = fetch_rss("ecb_press", fixture("ecb_press.xml"))
    decision = next(e for e in result.events if e.title == "Monetary policy decisions")
    assert decision.starts_at == datetime(2026, 10, 29, 13, 15, tzinfo=UTC)
    assert decision.currency == "EUR"
    assert decision.impact is Impact.HIGH


def test_the_keyword_filter_drops_the_irrelevant() -> None:
    result = fetch_rss("ecb_press", fixture("ecb_press.xml"))
    titles = {e.title for e in result.events}
    assert not any("tokenisation" in title for title in titles)


def test_fxstreet_items_are_filed_under_the_market_they_talk_about() -> None:
    result = fetch_rss("fxstreet", fixture("fxstreet_news.xml"))
    assert result.events
    gold = next(e for e in result.events if e.title.startswith("Gold slides"))
    assert gold.currency == "XAU"
    assert gold.kind is EventKind.HEADLINE
    assert gold.starts_at == datetime(2026, 10, 30, 11, 10, 2, tzinfo=UTC)


def test_crypto_feeds_keep_bitcoin_and_market_moving_items() -> None:
    for name in ("coindesk", "cointelegraph"):
        result = fetch_rss(name, fixture(f"{name}.xml"))
        assert result.report.ok
        assert all(e.currency == "BTC" for e in result.events)
        assert all(e.source == name for e in result.events)


def test_html_in_descriptions_is_stripped() -> None:
    body = b"""<rss><channel><item><title>Bitcoin ETF flows</title>
    <description>&lt;p&gt;Spot &lt;b&gt;bitcoin&lt;/b&gt; ETF inflows&lt;/p&gt;</description>
    <pubDate>Fri, 30 Oct 2026 14:00:00 +0000</pubDate></item></channel></rss>"""
    result = fetch_rss("coindesk", body)
    assert result.events[0].summary == "Spot bitcoin ETF inflows"


def test_a_feed_declaring_a_dtd_or_entity_is_refused() -> None:
    body = b"""<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaa">]>
    <rss><channel><item><title>&a;</title></item></channel></rss>"""
    result = fetch_rss("coindesk", body)
    assert not result.report.ok
    assert "DTD" in (result.report.error or "")
    assert result.events == ()


BOMB_TEXT = """<?xml version="1.0" encoding="{enc}"?><!DOCTYPE rss [<!ENTITY a "aaaa">]>
<rss><channel><item><title>Bitcoin &a;</title>
<pubDate>Fri, 30 Oct 2026 14:00:00 +0000</pubDate></item></channel></rss>"""


@pytest.mark.parametrize(
    ("encoding", "codec", "bom"),
    [
        ("utf-16", "utf-16-le", codecs.BOM_UTF16_LE),
        ("utf-16", "utf-16-be", codecs.BOM_UTF16_BE),
        ("utf-16", "utf-16-le", b""),  # no BOM: the declaration alone names the encoding
        ("utf-32", "utf-32-le", codecs.BOM_UTF32_LE),
        ("cp037", "cp037", b""),  # EBCDIC: the byte pattern of `<!DOCTYPE` never appears
    ],
)
def test_a_dtd_hidden_behind_a_non_utf8_encoding_is_refused_too(
    encoding: str, codec: str, bom: bytes
) -> None:
    body = bom + BOMB_TEXT.format(enc=encoding).encode(codec)
    result = fetch_rss("coindesk", body)
    assert not result.report.ok
    assert result.events == ()
    assert "UTF-8" in (result.report.error or "")


def test_a_utf8_feed_with_a_byte_order_mark_still_parses() -> None:
    body = codecs.BOM_UTF8 + (
        b"<rss><channel><item><title>Bitcoin ETF flows</title>"
        b"<pubDate>Fri, 30 Oct 2026 14:00:00 +0000</pubDate></item></channel></rss>"
    )
    result = fetch_rss("coindesk", body)
    assert result.report.ok
    assert [event.title for event in result.events] == ["Bitcoin ETF flows"]


def test_a_declared_encoding_cannot_change_how_the_checked_text_is_read() -> None:
    # Valid UTF-8 that merely *claims* another encoding: what was guarded is what is parsed.
    body = (
        b'<?xml version="1.0" encoding="cp037"?>'
        b"<rss><channel><item><title>Bitcoin ETF flows</title>"
        b"<pubDate>Fri, 30 Oct 2026 14:00:00 +0000</pubDate></item></channel></rss>"
    )
    result = fetch_rss("coindesk", body)
    assert result.report.ok
    assert [event.title for event in result.events] == ["Bitcoin ETF flows"]


def test_broken_xml_is_reported_not_raised() -> None:
    result = fetch_rss("coindesk", b"<rss><channel><item>")
    assert not result.report.ok
    assert "XML" in (result.report.error or "")


def test_an_rss_http_failure_is_reported() -> None:
    assert not fetch_rss("coindesk", b"", status=503).report.ok
    limited = fetch_rss("coindesk", b"", status=429)
    assert limited.report.retry_after == 600


def test_an_rss_transport_error_is_reported() -> None:
    http = FakeHttp({spec("coindesk").url: HttpError("ConnectionResetError")})
    result = asyncio.run(RssSource(spec("coindesk"), http).fetch(NOW))
    assert not result.report.ok


@pytest.mark.parametrize("feed", FEEDS, ids=lambda feed: feed.name)
def test_every_feed_is_https_and_named_uniquely(feed: FeedSpec) -> None:
    assert feed.url.startswith("https://")
    assert [f.name for f in FEEDS].count(feed.name) == 1
