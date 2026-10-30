"""The AI-read calendar: only figures the page carries survive, nothing is invented."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from tradingagent.news.ports import HttpResponse
from tradingagent.news.webscan import SCAN_PAGES, WebScanSource, page_text, parse_events
from tradingagent.storage.ai_calls import AiCall, AiReply

NOW = datetime(2026, 10, 30, 8, 0, tzinfo=UTC)
PAGE = SCAN_PAGES[0]
HTML = (
    "<html><head><script>x=1</script></head><body>"
    + ("<tr><td>Nov 4 14:00 ET</td><td>FOMC statement</td><td>Previous 4.25</td></tr>" * 20)
    + "</body></html>"
)


class FakeHttp:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls = 0

    async def get(self, url: str, *, timeout: float) -> HttpResponse:
        self.calls += 1
        return HttpResponse(self.status, HTML.encode(), {})


class FakeModel:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def complete(self, system: str, user: str, *, max_tokens: int) -> AiReply:
        self.calls += 1
        return AiReply(text=self.text, model="deepseek-chat", input_tokens=10, output_tokens=10)


class Journal:
    def __init__(self) -> None:
        self.calls: list[AiCall] = []

    def record(self, call: AiCall) -> None:
        self.calls.append(call)


REPLY = (
    '{"events":[{"starts_at_utc":"2026-11-04T19:00:00Z","currency":"USD","title":"FOMC statement",'
    '"impact":"high","kind":"release","forecast":"4.00","previous":"4.25","actual":null},'
    '{"starts_at_utc":"2026-12-25T19:00:00Z","currency":"USD","title":"Too far","impact":"high"},'
    '{"starts_at_utc":"2026-11-04T10:00:00","currency":"USD","title":"Naive","impact":"high"}]}'
)


def _source(text: str = REPLY, status: int = 200) -> tuple[WebScanSource, Journal, FakeModel]:
    journal, model = Journal(), FakeModel(text)
    return (
        WebScanSource(PAGE, FakeHttp(status), model, journal, model="deepseek-chat"),
        journal,
        model,
    )


def test_page_text_drops_scripts() -> None:
    assert "x=1" not in page_text(HTML)


def test_invented_figure_is_dropped_and_real_one_kept() -> None:
    source, journal, _ = _source()
    result = asyncio.run(source.fetch(NOW))
    assert result.report.ok and len(result.events) == 1
    event = result.events[0]
    assert event.previous == "4.25"  # printed on the page
    assert event.forecast is None  # "4.00" is not on the page: invented, dropped
    assert event.actual is None
    assert journal.calls and journal.calls[0].purpose == "news_web_scan"
    assert isinstance(journal.calls[0].cost_eur, Decimal)


def test_scan_is_throttled() -> None:
    source, _, model = _source()
    asyncio.run(source.fetch(NOW))
    again = asyncio.run(source.fetch(NOW.replace(hour=9)))
    assert again.report.ok and not again.events and model.calls == 1


def test_bad_reply_is_a_failed_report_not_an_exception() -> None:
    source, journal, _ = _source("not json")
    result = asyncio.run(source.fetch(NOW))
    assert not result.report.ok and journal.calls[0].error


def test_http_error_status_reported() -> None:
    source, _, _ = _source(status=403)
    assert asyncio.run(source.fetch(NOW)).report.error == "HTTP 403"


def test_without_model_nothing_is_read() -> None:
    source = WebScanSource(PAGE, FakeHttp(), None, Journal(), model="x")
    assert asyncio.run(source.fetch(NOW)).report.ok


def test_parse_ignores_garbage() -> None:
    assert parse_events({"events": "x"}, PAGE, "", NOW, 7) == ()
    assert parse_events([], PAGE, "", NOW, 7) == ()
