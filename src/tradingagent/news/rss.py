"""Unscheduled items from public RSS feeds: central-bank releases, speeches, market news.

Only what can move gold or bitcoin is kept: each feed carries a keyword filter, and an item that
does not match it is dropped here rather than stored and hidden later. Items are headlines (or
speeches), dated at their publication time -- they are *news the market has already seen or is
seeing*, not a schedule, so they never open a blackout window by themselves (see `blackout`).

Feeds verified 2026-10-30, all HTTP 200: federalreserve.gov/feeds/press_all.xml, speeches.xml,
press_monetary.xml; ecb.europa.eu/rss/press.html; fxstreet.com/rss/news; coindesk.com/arc/
outboundfeeds/rss/; cointelegraph.com/rss.

XML is parsed with the standard library after refusing any document that declares a DTD or an
entity: an RSS feed never needs one, and refusing them removes the entity-expansion class of
attacks without a new dependency.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from tradingagent.news.model import EconomicEvent, EventKind, Impact, event_key
from tradingagent.news.ports import FetchResult, HttpClient, HttpError, SourceReport

log = logging.getLogger(__name__)

_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_FORBIDDEN_XML = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
MAX_ITEMS_PER_FEED = 30
SUMMARY_CHARS = 400


@dataclass(frozen=True)
class FeedSpec:
    name: str
    url: str
    #: The currency code the items are filed under (USD, EUR, BTC, XAU) -- or "auto" to choose
    #: per item from its text (gold -> XAU, bitcoin -> BTC, otherwise USD).
    currency: str
    keywords: re.Pattern[str]
    kind: EventKind = EventKind.HEADLINE
    #: Items matching this title pattern are rated HIGH, the others MEDIUM (or LOW when
    #: `base_impact` says so).
    high_titles: re.Pattern[str] | None = None
    base_impact: Impact = Impact.MEDIUM
    #: Marks an item as a speech when its RSS category equals one of these.
    speech_categories: tuple[str, ...] = ()


def _pattern(words: str) -> re.Pattern[str]:
    return re.compile(words, re.IGNORECASE)


_MONETARY = (
    r"monetary|fomc|federal funds|interest rate|inflation|economic projections|balance sheet"
    r"|discount rate|minutes|rate decision|lagarde|powell|employment|labor market|dollar|gold"
)
_MARKET = (
    r"\bgold\b|\bxau|bullion|\bfed\b|fomc|powell|dollar|\busd\b|inflation|\bcpi\b|\bnfp\b|payroll"
    r"|treasury|yields?|rate (cut|hike|decision)|bitcoin|\bbtc\b|tariff|safe[- ]haven"
)
_CRYPTO = (
    r"bitcoin|\bbtc\b|\betf\b|\bsec\b|regulat|\bfed\b|fomc|stablecoin|liquidat|crypto market"
    r"|treasury|tariff"
)

FEEDS: tuple[FeedSpec, ...] = (
    FeedSpec(
        "fed_press",
        "https://www.federalreserve.gov/feeds/press_all.xml",
        "USD",
        _pattern(_MONETARY),
        high_titles=_pattern(r"fomc (statement|minutes)|federal open market committee"),
    ),
    FeedSpec(
        "fed_monetary",
        "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "USD",
        _pattern(r".*"),
        high_titles=_pattern(r"fomc|federal open market committee"),
    ),
    FeedSpec(
        "fed_speeches",
        "https://www.federalreserve.gov/feeds/speeches.xml",
        "USD",
        _pattern(r".*"),
        kind=EventKind.SPEECH,
        high_titles=_pattern(r"^powell\b|\bchair\b"),
        speech_categories=("speech", "testimony"),
    ),
    FeedSpec(
        "ecb_press",
        "https://www.ecb.europa.eu/rss/press.html",
        "EUR",
        _pattern(_MONETARY + r"|decision|statement"),
        high_titles=_pattern(r"monetary policy (decisions|statement)"),
    ),
    FeedSpec(
        "fxstreet",
        "https://www.fxstreet.com/rss/news",
        "auto",
        _pattern(_MARKET),
        high_titles=_pattern(r"\bfomc\b|\bnfp\b|non[- ]?farm|\bcpi\b|rate decision"),
        base_impact=Impact.LOW,
    ),
    FeedSpec(
        "coindesk",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "BTC",
        _pattern(_CRYPTO),
        high_titles=_pattern(r"bitcoin etf|\bsec\b|\bban\b|liquidat"),
        base_impact=Impact.LOW,
    ),
    FeedSpec(
        "cointelegraph",
        "https://cointelegraph.com/rss",
        "BTC",
        _pattern(_CRYPTO),
        high_titles=_pattern(r"bitcoin etf|\bsec\b|\bban\b|liquidat"),
        base_impact=Impact.LOW,
    ),
)


class RssSource:
    def __init__(
        self,
        spec: FeedSpec,
        http: HttpClient,
        *,
        lookback_hours: int = 36,
        timeout: float = 15.0,
    ) -> None:
        self._spec = spec
        self._http = http
        self._lookback = timedelta(hours=lookback_hours)
        self._timeout = timeout
        self.name = spec.name

    async def fetch(self, now: datetime) -> FetchResult:
        try:
            response = await self._http.get(self._spec.url, timeout=self._timeout)
        except HttpError as error:
            return self._failure(now, None, f"réseau : {error}")
        if response.status == 429:
            return self._failure(now, 429, "limite de débit atteinte", 600)
        if response.status != 200:
            return self._failure(now, response.status, f"statut HTTP {response.status}")
        # The DTD guard reads text, so the bytes are decoded first, and what is guarded is what
        # is parsed. A UTF-16/32 or EBCDIC document would otherwise hide `<!DOCTYPE` from a byte
        # search while expat still decodes it. Anything that is not UTF-8 is refused: no feed
        # listed above is anything else.
        try:
            text = response.body.decode("utf-8-sig")
        except UnicodeDecodeError:
            return self._failure(now, 200, "flux refusé : encodage autre que UTF-8")
        if "\x00" in text:  # UTF-16/32 without a BOM reads as UTF-8 with a NUL between letters
            return self._failure(now, 200, "flux refusé : encodage autre que UTF-8 (octets nuls)")
        if _FORBIDDEN_XML.search(text):
            return self._failure(now, 200, "flux refusé : DTD ou entité déclarée")
        try:
            # `encoding` overrides the prolog's declaration: the text was checked as UTF-8.
            parser = ElementTree.XMLParser(encoding="utf-8")  # noqa: S314 - DTD/entities refused
            parser.feed(text.encode("utf-8"))
            root = parser.close()
        except ElementTree.ParseError as error:
            return self._failure(now, 200, f"XML illisible : {error}")
        events = self.parse(root, now)
        return FetchResult(events, SourceReport(self.name, True, len(events), now, status=200))

    def _failure(
        self, now: datetime, status: int | None, error: str, retry_after: int | None = None
    ) -> FetchResult:
        log.warning("news source %s failed: %s", self.name, error)
        report = SourceReport(
            self.name, False, 0, now, status=status, error=error, retry_after=retry_after
        )
        return FetchResult((), report)

    def parse(self, root: ElementTree.Element, now: datetime) -> tuple[EconomicEvent, ...]:
        events: dict[str, EconomicEvent] = {}
        for item in root.iter("item"):
            event = self._event(item, now)
            if event is not None:
                events[event.key] = event
        newest_first = sorted(events.values(), key=lambda e: e.starts_at, reverse=True)
        return tuple(newest_first[:MAX_ITEMS_PER_FEED])

    def _event(self, item: ElementTree.Element, now: datetime) -> EconomicEvent | None:
        title = _clean(item.findtext("title"))
        if not title:
            return None
        published = _published(item.findtext("pubDate") or item.findtext("{*}date"))
        if published is None:
            return None
        if published < now - self._lookback or published > now + timedelta(hours=1):
            return None
        summary = _clean(item.findtext("description"))[:SUMMARY_CHARS]
        text = f"{title} {summary}"
        spec = self._spec
        if spec.keywords.search(text) is None:
            return None
        categories = {_clean(c.text).lower() for c in item.findall("category")}
        is_speech = bool(categories & set(spec.speech_categories))
        kind = (
            EventKind.SPEECH
            if is_speech
            else (EventKind.HEADLINE if spec.kind is EventKind.SPEECH else spec.kind)
        )
        currency = _auto_currency(text) if spec.currency == "auto" else spec.currency
        high = spec.high_titles is not None and spec.high_titles.search(title) is not None
        impact = Impact.HIGH if high else spec.base_impact
        link = _clean(item.findtext("link")) or None
        return EconomicEvent(
            key=event_key(self.name, currency, title, published),
            starts_at=published,
            currency=currency,
            title=title,
            impact=impact,
            kind=kind,
            source=self.name,
            url=link if link and link.startswith("https://") else None,
            summary=summary,
        )


_GOLD = re.compile(r"\bgold\b|\bxau|bullion", re.IGNORECASE)
_BITCOIN = re.compile(r"bitcoin|\bbtc\b", re.IGNORECASE)


def _auto_currency(text: str) -> str:
    if _GOLD.search(text):
        return "XAU"
    if _BITCOIN.search(text):
        return "BTC"
    return "USD"


def _clean(text: str | None) -> str:
    if not text:
        return ""
    no_tags = _TAGS.sub(" ", text)
    # Entities in feeds are mostly &amp; &nbsp; &uuml;: unescape the common ones, keep the rest.
    import html

    return _SPACES.sub(" ", html.unescape(no_tags)).strip()


def _published(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text.strip())
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(text.strip())
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
