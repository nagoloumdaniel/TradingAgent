"""The vocabulary of the calendar, and the deterministic score of how much an event matters.

`influence_score` is the one number the dashboard, the blackout gate and the AI prompt share.
It is a heuristic, and it says so: an impact weight, the relevance of the event's currency to
the market, a boost for the releases that historically move gold and bitcoin, and a bonus when
the published figure is known and departs from the forecast. It is never a prediction of
direction -- direction is `fallback` (deterministic) or the model (journalled).
"""

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit

XAUUSD = "XAUUSD"
BTCUSD = "BTCUSD"
#: The markets the agent trades; every score, verdict and gate is computed per market.
MARKETS: tuple[str, ...] = (XAUUSD, BTCUSD)


class Impact(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return _IMPACT_RANK[self]


_IMPACT_RANK = {Impact.LOW: 1, Impact.MEDIUM: 2, Impact.HIGH: 3}


class EventKind(StrEnum):
    RELEASE = "release"  # a scheduled figure or decision
    SPEECH = "speech"  # a scheduled speech, press conference or testimony
    HEADLINE = "headline"  # an unscheduled item from a news feed


class Bias(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class Stance(StrEnum):
    BUY = "buy"
    SELL = "sell"
    WAIT = "wait"


@dataclass(frozen=True)
class EconomicEvent:
    key: str
    starts_at: datetime
    currency: str
    title: str
    impact: Impact
    kind: EventKind = EventKind.RELEASE
    country: str = ""
    forecast: str | None = None
    previous: str | None = None
    actual: str | None = None
    source: str = ""
    url: str | None = None
    summary: str = ""

    def __post_init__(self) -> None:
        if self.starts_at.utcoffset() is None:
            raise ValueError(f"naive datetime refused, timezone required: {self.starts_at!r}")


CURRENCY_PATTERN = re.compile(r"[A-Z]{2,8}")
MAX_TITLE, MAX_COUNTRY, MAX_FIGURE, MAX_URL = 240, 8, 32, 500


def _safe_https_url(url: str | None) -> str | None:
    """A feed link kept only if it is a well-formed https URL that fits its column whole."""
    if not url or len(url) > MAX_URL or not url.startswith("https://"):
        return None
    try:
        return url if urlsplit(url).netloc else None
    except ValueError:
        return None


def sanitize_event(event: EconomicEvent) -> EconomicEvent | None:
    """The event as it may be stored, or None when it is not one worth storing.

    A feed is data we do not control: a currency that is not a code, a figure longer than its
    column or a `javascript:` link must cost that one event (or that one field), never the whole
    batch. Cut, never wrapped: a URL longer than the column is dropped rather than truncated
    into a different address.
    """
    currency = event.currency.strip().upper()
    if CURRENCY_PATTERN.fullmatch(currency) is None or not event.title.strip():
        return None

    def figure(value: str | None) -> str | None:
        return value[:MAX_FIGURE] if value else None

    return replace(
        event,
        currency=currency,
        title=event.title[:MAX_TITLE],
        country=event.country[:MAX_COUNTRY],
        forecast=figure(event.forecast),
        previous=figure(event.previous),
        actual=figure(event.actual),
        url=_safe_https_url(event.url),
    )


def event_key(source: str, currency: str, title: str, starts_at: datetime) -> str:
    """A stable identity: the same announcement refreshed twice is one row, not two."""
    if starts_at.utcoffset() is None:
        raise ValueError(f"naive datetime refused, timezone required: {starts_at!r}")
    stamp = starts_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")
    raw = "|".join((source, currency.upper(), " ".join(title.lower().split()), stamp))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


@dataclass(frozen=True)
class MarketState:
    """What the market is doing right now, from the existing indicators and latest candles."""

    symbol: str
    trend: str = "neutral"  # up | down | neutral
    volatility: str = "normal"  # calm | normal | volatile
    last_price: float | None = None
    as_of: datetime | None = None

    @property
    def known(self) -> bool:
        return self.as_of is not None


@dataclass(frozen=True)
class MarketVerdict:
    symbol: str
    bias: Bias
    confidence: float
    influence: int
    stance: Stance
    timeframes: tuple[str, ...]
    window_start: datetime
    window_end: datetime
    rationale: str


@dataclass(frozen=True)
class EventAnalysis:
    event_key: str
    verdicts: tuple[MarketVerdict, ...]
    source: str  # "ai" | "fallback"
    model: str
    analysed_at: datetime
    input_hash: str


@dataclass(frozen=True)
class MarketBrief:
    """The day's reading of one market, in a sentence."""

    symbol: str
    bias: Bias
    stance: Stance
    summary: str


@dataclass(frozen=True)
class DailyBrief:
    """The morning summary: what the day's calendar and headlines mean for gold and bitcoin."""

    day: str  # YYYY-MM-DD, UTC
    headline: str
    markets: tuple[MarketBrief, ...]
    source: str  # "ai" | "fallback"
    model: str
    generated_at: datetime


def input_hash(event: EconomicEvent) -> str:
    """Changes when the figures do: a new `actual` or a revised forecast earns a new reading."""
    raw = "|".join(
        (
            event.starts_at.isoformat(),
            event.impact.value,
            event.forecast or "",
            event.previous or "",
            event.actual or "",
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# --- relevance ---------------------------------------------------------------------------

# How much an event in a given currency matters to each market. USD dominates both: gold is
# priced in dollars and trades against real yields; bitcoin follows dollar liquidity. The other
# currencies matter in proportion to their weight in global liquidity and in gold demand.
_RELEVANCE: Mapping[str, Mapping[str, float]] = {
    XAUUSD: {
        "USD": 1.0,
        "XAU": 1.0,
        "EUR": 0.5,
        "CNY": 0.45,
        "JPY": 0.35,
        "GBP": 0.35,
        "CHF": 0.3,
        "CAD": 0.2,
        "AUD": 0.2,
        "NZD": 0.15,
        "BTC": 0.1,
    },
    BTCUSD: {
        "USD": 1.0,
        "BTC": 1.0,
        "CRYPTO": 1.0,
        "EUR": 0.35,
        "CNY": 0.35,
        "JPY": 0.3,
        "GBP": 0.25,
        "CHF": 0.15,
        "CAD": 0.15,
        "AUD": 0.15,
        "NZD": 0.1,
        "XAU": 0.1,
    },
}
DEFAULT_RELEVANCE = 0.05


def currency_relevance(market: str, currency: str) -> float:
    return _RELEVANCE.get(market, {}).get(currency.upper(), DEFAULT_RELEVANCE)


def is_relevant(event: EconomicEvent) -> bool:
    """Kept in the calendar: it matters to at least one of our two markets.

    Majors and the market's own currencies always qualify; the minor currencies only when the
    event is a medium or high impact one (a BoJ decision moves gold, a NZ trade balance does not).
    """
    best = max(currency_relevance(market, event.currency) for market in MARKETS)
    if best >= 0.45:
        return True
    return best >= 0.3 and event.impact.rank >= Impact.MEDIUM.rank


# --- type boosts -------------------------------------------------------------------------

#: (pattern on the lower-cased title, boost for gold, boost for bitcoin).
_RAW_BOOSTS: tuple[tuple[str, int, int], ...] = (
    (r"non[- ]?farm|nfp|payrolls", 30, 26),
    (r"\bcpi\b|consumer price", 28, 26),
    (
        r"fomc|federal funds rate|interest rate|rate (decision|statement)|monetary policy"
        r"|refinancing|cash rate|bank rate",
        30,
        26,
    ),
    (r"press conference|powell|fed chair", 22, 20),
    (r"\bpce\b", 22, 20),
    (r"\bgdp\b", 14, 12),
    (r"\bppi\b|producer price", 10, 9),
    (r"retail sales", 9, 8),
    (r"unemployment|jobless|\badp\b|jolts|employment cost", 8, 8),
    (r"\bism\b|\bpmi\b", 8, 6),
    (r"treasury|yield|auction", 8, 6),
    (r"\betf\b|\bsec\b|regulat|\bban\b", 2, 20),
    (r"\bgold\b|bullion|xau", 12, 0),
    (r"bitcoin|\bbtc\b|crypto", 0, 14),
    (r"speaks|speech|testimony", 6, 6),
)
_TYPE_BOOSTS: tuple[tuple[re.Pattern[str], int, int], ...] = tuple(
    (re.compile(pattern), gold, bitcoin) for pattern, gold, bitcoin in _RAW_BOOSTS
)

#: Titles that name a major, market-moving announcement: wider blackout, bigger score.
MAJOR_PATTERN = re.compile(
    r"fomc|federal funds rate|interest rate|rate (decision|statement)|non[- ]?farm|nfp|payrolls"
    r"|\bcpi\b|consumer price|monetary policy|refinancing|cash rate|bank rate",
    re.IGNORECASE,
)


def is_major(title: str) -> bool:
    return MAJOR_PATTERN.search(title) is not None


def type_boost(title: str, market: str) -> int:
    """The strongest boost any pattern grants this title for this market (never summed)."""
    lowered = title.lower()
    boosts = [
        gold if market == XAUUSD else bitcoin
        for pattern, gold, bitcoin in _TYPE_BOOSTS
        if pattern.search(lowered)
    ]
    return max(boosts, default=0)


_IMPACT_BASE = {Impact.LOW: 12, Impact.MEDIUM: 32, Impact.HIGH: 55}
_NUMBER = re.compile(r"^\s*[<>~]?\s*(-?\d+(?:[.,]\d+)?)\s*([%KMBT]?)\s*$", re.IGNORECASE)
_SCALE = {"": 1.0, "%": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def parse_figure(text: str | None) -> float | None:
    """A published figure as a number ("198K" is 198000, "4.00%" is 4.0), or None."""
    if not text:
        return None
    match = _NUMBER.match(text)
    if match is None:
        return None
    return float(match.group(1).replace(",", ".")) * _SCALE[match.group(2).upper()]


def surprise_bonus(event: EconomicEvent) -> int:
    """0 to 15 points when the actual figure is known and departs from the forecast.

    Relative to the larger of forecast and previous, floored so a forecast of 0.0 does not make
    every print an infinite surprise. Zero when any of the figures is missing: the absence of an
    `actual` is a fact about the source, not a zero surprise to be scored.
    """
    actual = parse_figure(event.actual)
    forecast = parse_figure(event.forecast)
    if actual is None or forecast is None:
        return 0
    previous = parse_figure(event.previous)
    scale = max(abs(forecast), abs(previous) if previous is not None else 0.0, 0.1)
    return min(15, round(abs(actual - forecast) / scale * 30))


def influence_score(event: EconomicEvent, market: str) -> int:
    """How much this event should matter to `market`, 0 (nothing) to 100 (the day's event)."""
    relevance = currency_relevance(market, event.currency)
    raw: float = (
        _IMPACT_BASE[event.impact] + type_boost(event.title, market) + surprise_bonus(event)
    )
    if event.kind is EventKind.HEADLINE and event.impact is not Impact.HIGH:
        raw *= 0.85
    return max(0, min(100, round(raw * relevance)))
