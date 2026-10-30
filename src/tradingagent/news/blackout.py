"""The news blackout: refuse a signal while a market-moving announcement is about to land.

Pure: events and the clock are arguments. The gate sits BEFORE the risk engine (in the
pipeline, not in `risk/`) and can only refuse -- it never creates, sizes or loosens anything
(C-002). The reason is a sentence the operator can read in the signal's history.

Why a blackout rather than a wider stop: the minutes around a major release are whipsaw. The
spread widens, the first move reverses, and a position opened inside the window is stopped out
by noise that says nothing about the strategy. Staying out is the cheapest way to cut that
back-and-forth ("battement").
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from tradingagent.news.config import BlackoutConfig
from tradingagent.news.model import (
    EconomicEvent,
    EventKind,
    Impact,
    currency_relevance,
    is_major,
)


@dataclass(frozen=True)
class BlackoutDecision:
    blocked: bool
    reason: str = ""
    event: EconomicEvent | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    #: Set when the gate could not do its job (stale or empty calendar): it then fails open
    #: and says so, rather than pretending the calendar was empty.
    degraded: str = ""


CLEAR = BlackoutDecision(blocked=False)


def blackout_window(
    event: EconomicEvent, symbol: str, config: BlackoutConfig
) -> tuple[datetime, datetime] | None:
    """The [start, end] interval during which `event` forbids new signals on `symbol`."""
    if not config.enabled:
        return None
    # An unscheduled headline has no "before": it is already out when we read it.
    if event.kind is EventKind.HEADLINE:
        return None
    if currency_relevance(symbol, event.currency) < config.min_relevance:
        return None
    if event.impact is Impact.HIGH:
        if is_major(event.title):
            before, after = config.major_before_minutes, config.major_after_minutes
        else:
            before, after = config.high_before_minutes, config.high_after_minutes
    elif event.impact is Impact.MEDIUM and config.medium_enabled:
        before, after = config.medium_before_minutes, config.medium_after_minutes
    else:
        return None
    return event.starts_at - timedelta(minutes=before), event.starts_at + timedelta(minutes=after)


def news_blackout(
    symbol: str, now: datetime, events: Iterable[EconomicEvent], config: BlackoutConfig
) -> BlackoutDecision:
    """Is a new signal on `symbol` forbidden at `now`? Both ends of the window are inside it."""
    if now.utcoffset() is None:
        raise ValueError(f"naive datetime refused, timezone required: {now!r}")
    hits: list[tuple[EconomicEvent, datetime, datetime]] = []
    for event in events:
        window = blackout_window(event, symbol, config)
        if window is not None and window[0] <= now <= window[1]:
            hits.append((event, window[0], window[1]))
    if not hits:
        return CLEAR
    # The most serious first (impact, then the one furthest from being over).
    event, start, end = max(hits, key=lambda hit: (hit[0].impact.rank, hit[2]))
    when = "publiée" if now >= event.starts_at else "imminente"
    reason = (
        f"news_blackout: {event.title} ({event.currency}, {event.impact.value}) "
        f"à {event.starts_at:%H:%M} UTC, annonce {when}; "
        f"fenêtre {start:%H:%M}-{end:%H:%M} UTC"
    )
    return BlackoutDecision(
        blocked=True, reason=reason, event=event, window_start=start, window_end=end
    )
