"""The economic calendar page: what the stored calendar says, shaped for one template.

Read-only and honest. Everything shown was either stored by the news service (events, the
model's readings, the morning brief, the feed log) or is computed here from those rows by the
same pure rules the agent uses (`tradingagent.news.fallback`), and then labelled "règles". A
source that is down, a calendar never refreshed, a model that never answered: each is said, in
words, on the page. Nothing is invented to fill a gap.

The page never decides anything for the agent. The blackout windows drawn here are computed by
the very function the pipeline uses (`blackout_window`), with the default windows, so what the
operator reads is what the gate does unless the operator tuned `news.blackout` in the config.
"""

import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import Engine

from tradingagent.news.blackout import blackout_window
from tradingagent.news.brief import fallback_brief
from tradingagent.news.config import DEFAULT_NEWS_CONFIG
from tradingagent.news.fallback import fallback_analysis
from tradingagent.news.market_state import MarketStateReader
from tradingagent.news.model import (
    BTCUSD,
    XAUUSD,
    Bias,
    DailyBrief,
    EconomicEvent,
    EventAnalysis,
    EventKind,
    Impact,
    MarketState,
    MarketVerdict,
    Stance,
    is_major,
)
from tradingagent.news.ports import EMPTY_CALENDAR, SourceReport
from tradingagent.news.stance import market_phrase
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.news import NewsStore
from tradingagent.trusted_clock import paris_offset
from tradingagent.trusted_clock import status as clock_status

log = logging.getLogger(__name__)

#: The strip: the week of the chosen day (the current one by default), Monday to Sunday.
STRIP_DAYS = 7
#: How far ahead announcements are looked up for the "next major" card and the day lookup.
LOOKAHEAD_DAYS = 8
#: The press headlines listed for one day.
HEADLINES_SHOWN = 40
#: An announcement this close is "imminent".
SOON = timedelta(minutes=60)
#: The week-ahead feed is published by the host later in the week: past this many days ahead
#: the page says the sources do not cover the day yet, instead of reading it as "nothing planned".
COVERED_DAYS_AHEAD = LOOKAHEAD_DAYS

DAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

IMPACT_LABELS = {Impact.HIGH: "Fort", Impact.MEDIUM: "Moyen", Impact.LOW: "Faible"}
KIND_LABELS = {
    EventKind.RELEASE: "Publication",
    EventKind.SPEECH: "Discours",
    EventKind.HEADLINE: "Titre de presse",
}
BIAS_LABELS = {Bias.BULLISH: "Haussier", Bias.BEARISH: "Baissier", Bias.NEUTRAL: "Neutre"}
STANCE_LABELS = {Stance.BUY: "Acheter", Stance.SELL: "Vendre", Stance.WAIT: "Attendre"}
WEEKDAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
WEEKDAYS_LONG = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)
MONTHS_SHORT = (
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
)
TREND_LABELS = {"up": "haussière", "down": "baissière", "neutral": "sans direction nette"}
VOLATILITY_LABELS = {"calm": "calme", "normal": "normale", "volatile": "forte"}

#: Country mark of a currency: a two-letter code, drawn by the stylesheet. Emoji flags are not
#: drawn by Windows fonts, so a chip with the ISO code is the one mark that shows everywhere.
COUNTRY_CODES = {
    "USD": "US",
    "EUR": "EU",
    "GBP": "GB",
    "JPY": "JP",
    "CHF": "CH",
    "CAD": "CA",
    "AUD": "AU",
    "NZD": "NZ",
    "CNY": "CN",
    "BTC": "₿",
    "XAU": "Au",
}

SOURCE_LABELS = {
    "faireconomy": "Calendrier de la semaine (Faireconomy)",
    "faireconomy_next": "Calendrier de la semaine suivante (Faireconomy)",
    "fed_press": "Réserve fédérale : communiqués",
    "fed_monetary": "Réserve fédérale : politique monétaire",
    "fed_speeches": "Réserve fédérale : discours",
    "ecb_press": "BCE : communiqués",
    "fxstreet": "FXStreet : actualités",
    "coindesk": "CoinDesk",
    "cointelegraph": "Cointelegraph",
}
CALENDAR_SOURCE = "faireconomy"

NOT_ADVICE = (
    "Lecture automatique, pas un conseil en investissement. L'agent ne s'en sert que pour "
    "refuser ou temporiser un signal (fenêtre de blocage autour des annonces fortes), jamais "
    "pour en créer, augmenter une taille ou desserrer un stop."
)


class BadCalendarRequest(ValueError):
    """A `day` parameter that is not a real date: the route answers 400."""


# --- time ---------------------------------------------------------------------------------


def to_paris(moment: datetime) -> datetime:
    """The Paris wall clock of `moment`. Caveat: the result is tagged UTC but carries the
    Paris wall-clock fields; it is only ever formatted, never compared or stored."""
    return moment.astimezone(UTC) + paris_offset(moment)


# --- the view model -----------------------------------------------------------------------


@dataclass(frozen=True)
class VerdictView:
    symbol: str
    bias: str
    bias_label: str
    stance: str
    stance_label: str
    confidence: int
    influence: int
    timeframes: str
    window: str
    rationale: str
    score: int  # signed, -100..100: direction x confidence x influence
    score_text: str
    score_abs: int


@dataclass(frozen=True)
class EventView:
    key: str
    time_utc: str
    time_paris: str
    date_label: str
    at_iso: str
    country: str
    currency: str
    title: str
    impact: str
    impact_label: str
    kind: str
    kind_label: str
    forecast: str
    previous: str
    actual: str
    has_actual: bool
    state: str  # past | soon | upcoming
    state_label: str
    influence: int
    major: bool
    blackout: str  # "14:15 à 14:45 UTC" or ""
    reading: str  # ai | rules | live
    reading_label: str
    verdicts: tuple[VerdictView, ...]
    url: str
    host: str
    source_label: str
    summary: str
    top_stance: str


@dataclass(frozen=True)
class DayCell:
    iso: str
    number: int
    weekday: str
    month_label: str
    is_today: bool
    is_selected: bool
    is_weekend: bool
    high: int
    medium: int
    low: int
    total: int
    href: str
    aria: str
    top_titles: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketBriefView:
    symbol: str
    bias: str
    bias_label: str
    stance: str
    stance_label: str
    summary: str
    state_phrase: str
    last_price: str


@dataclass(frozen=True)
class BriefView:
    headline: str
    markets: tuple[MarketBriefView, ...]
    origin: str  # ai | rules | live
    origin_label: str
    generated: str


@dataclass(frozen=True)
class SourceView:
    key: str
    label: str
    ok: bool
    text: str
    age: str


@dataclass(frozen=True)
class Freshness:
    level: str  # ok | warn | bad
    headline: str
    details: tuple[str, ...]
    last_refresh: str


@dataclass(frozen=True)
class MarketOutlook:
    """One market for the day: the candles' trend, the announcements' lean, and the two combined."""

    symbol: str
    last_price: str
    state_known: bool
    trend: str
    trend_label: str
    volatility_label: str
    technical: int
    technical_text: str
    fundamental: int
    fundamental_text: str
    has_fundamental: bool
    score: int
    score_text: str
    score_abs: int
    bias: str
    bias_label: str
    stance: str
    stance_label: str
    prediction: str
    summary: str  # the morning brief's sentence for this market, "" when none is stored


@dataclass(frozen=True)
class Outlook:
    markets: tuple[MarketOutlook, ...]
    score: int
    score_text: str
    score_abs: int
    bias: str
    bias_label: str
    heat: int
    heat_label: str
    headline: str
    method: str


@dataclass(frozen=True)
class CalendarView:
    now_label: str
    paris_label: str
    clock_synced: bool
    clock_label: str
    freshness: Freshness
    brief: BriefView | None
    outlook: Outlook
    next_major: EventView | None
    strip: tuple[DayCell, ...]
    selected: str
    selected_label: str
    selected_is_today: bool
    selected_events: tuple[EventView, ...]
    selected_headlines: tuple[EventView, ...]
    selected_high: int
    selected_medium: int
    selected_note: str
    sources: tuple[SourceView, ...]
    ai_note: str
    total_events: int
    notice: str = NOT_ADVICE
    errors: tuple[str, ...] = field(default_factory=tuple)


# --- parsing ------------------------------------------------------------------------------


def parse_day(text: str | None) -> date | None:
    if text is None or text == "":
        return None
    if not DAY_PATTERN.fullmatch(text):
        raise BadCalendarRequest("jour invalide : attendu AAAA-MM-JJ")
    try:
        day = date.fromisoformat(text)
    except ValueError:
        raise BadCalendarRequest("jour invalide : cette date n'existe pas") from None
    if not 1900 <= day.year <= 2200:
        # Day arithmetic on year 9999 overflows into a 500.
        raise BadCalendarRequest("jour invalide : cette date n'existe pas")
    return day


# --- small formatters ---------------------------------------------------------------------


def _day_label(day: date) -> str:
    return f"{WEEKDAYS_LONG[day.weekday()]} {day.day} {MONTHS[day.month - 1]} {day.year}"


def _hm(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%H:%M")


def _figure(text: str | None) -> str:
    return text if text else "—"


def _age(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 90:
        return "à l'instant"
    minutes = seconds // 60
    if minutes < 90:
        return f"il y a {minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"il y a {hours} h"
    return f"il y a {hours // 24} j"


def _host(url: str | None) -> str:
    """The host of a feed link, shown so the operator sees where an item comes from.

    Only an http(s) link is kept: a feed is data, and a `javascript:` URL in an anchor is not.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:  # e.g. an unbalanced IPv6 bracket: stored data must never break the page
        return ""
    return parts.netloc if parts.scheme in ("http", "https") and parts.netloc else ""


def _safe_url(url: str | None) -> str:
    return url if url and _host(url) else ""


#: Public page behind a feed that carries no link per item (the weekly calendar export).
#: A link to the page the data comes from, never a deep link made up for one event.
SOURCE_PAGES: Mapping[str, tuple[str, str]] = {
    "faireconomy": ("Forex Factory", "https://www.forexfactory.com/calendar"),
    "faireconomy_next": ("Forex Factory", "https://www.forexfactory.com/calendar"),
}


def _source_link(event: EconomicEvent) -> tuple[str, str]:
    """(url, label) of where an announcement can be checked: its own link, else its feed's page."""
    own = _safe_url(event.url)
    if own:
        return own, _host(own).removeprefix("www.")
    label, page = SOURCE_PAGES.get(event.source, ("", ""))
    return page, label


def _day_href(day: date, today: date) -> str:
    # No `day` for today: the bare URL is the default, and it is the one a bookmark keeps.
    base = "/calendar" if day == today else f"/calendar?day={day.isoformat()}"
    return f"{base}#day-panel"


# --- verdicts and events ------------------------------------------------------------------


BIAS_SIGN = {Bias.BULLISH: 1, Bias.BEARISH: -1, Bias.NEUTRAL: 0}
#: A combined score past this (either way) is a lean, below it the picture is mixed.
LEAN_THRESHOLD = 15
#: A trend the candles show counts for this much of the technical half of the score.
TREND_WEIGHT = 50
#: Busiest announcement of the day (max influence): below `CALM`, calm; from `BUSY`, loaded.
HEAT_CALM, HEAT_BUSY = 35, 65


def _signed(value: int) -> str:
    return f"+{value}" if value > 0 else (f"\N{MINUS SIGN}{-value}" if value < 0 else "0")


def signed_score(verdict: MarketVerdict) -> int:
    """Direction x confidence x influence, as a whole number from -100 to 100."""
    return round(BIAS_SIGN[verdict.bias] * verdict.confidence * verdict.influence)


def _verdict_view(verdict: MarketVerdict) -> VerdictView:
    score = signed_score(verdict)
    start, end = verdict.window_start, verdict.window_end
    window = f"{_hm(start)} à {_hm(end)} UTC"
    if start.date() != end.date():
        window = f"{start:%d/%m %H:%M} à {end:%d/%m %H:%M} UTC"
    return VerdictView(
        symbol=verdict.symbol,
        bias=verdict.bias.value,
        bias_label=BIAS_LABELS[verdict.bias],
        stance=verdict.stance.value,
        stance_label=STANCE_LABELS[verdict.stance],
        confidence=round(verdict.confidence * 100),
        influence=verdict.influence,
        timeframes=" · ".join(verdict.timeframes),
        window=window,
        rationale=verdict.rationale,
        score=score,
        score_text=_signed(score),
        score_abs=abs(score),
    )


def _event_view(
    event: EconomicEvent,
    analysis: EventAnalysis,
    reading: str,
    at: datetime,
) -> EventView:
    verdicts = tuple(_verdict_view(v) for v in analysis.verdicts)
    influence = max((v.influence for v in verdicts), default=0)
    paris = to_paris(event.starts_at)
    delta = event.starts_at - at
    if delta <= timedelta(0):
        state, state_label = "past", "Publiée" if event.kind is EventKind.RELEASE else "Passée"
    elif delta <= SOON:
        state, state_label = "soon", "Imminente"
    else:
        state, state_label = "upcoming", "À venir"
    window = blackout_window(event, XAUUSD, DEFAULT_NEWS_CONFIG.blackout)
    labels = {
        "ai": "Lecture IA",
        "rules": "Lecture par règles",
        "live": "Règles, calculées à l'affichage",
    }
    first = next((v for v in verdicts if v.symbol == XAUUSD), None)
    source_url, source_label = _source_link(event)
    return EventView(
        key=event.key,
        time_utc=_hm(event.starts_at),
        time_paris=paris.strftime("%H:%M"),
        date_label=f"{WEEKDAYS[event.starts_at.weekday()]} {event.starts_at:%d/%m}",
        at_iso=event.starts_at.astimezone(UTC).isoformat(),
        country=COUNTRY_CODES.get(event.currency, event.currency[:2]),
        currency=event.currency,
        title=event.title,
        impact=event.impact.value,
        impact_label=IMPACT_LABELS[event.impact],
        kind=event.kind.value,
        kind_label=KIND_LABELS[event.kind],
        forecast=_figure(event.forecast),
        previous=_figure(event.previous),
        actual=_figure(event.actual),
        has_actual=bool(event.actual),
        state=state,
        state_label=state_label,
        influence=influence,
        major=is_major(event.title),
        blackout=(f"{_hm(window[0])} à {_hm(window[1])} UTC" if window is not None else ""),
        reading=reading,
        reading_label=labels[reading],
        verdicts=verdicts,
        url=source_url,
        host=_host(source_url),
        source_label=source_label,
        summary=(event.summary or "")[:400],
        top_stance=first.stance if first is not None else "wait",
    )


def _views(
    events: Iterable[EconomicEvent],
    analyses: Mapping[str, EventAnalysis],
    states: Mapping[str, MarketState],
    at: datetime,
) -> list[EventView]:
    views: list[EventView] = []
    for event in events:
        stored = analyses.get(event.key)
        if stored is not None:
            views.append(_event_view(event, stored, "ai" if stored.source == "ai" else "rules", at))
        else:
            views.append(_event_view(event, fallback_analysis(event, states, at), "live", at))
    return views


# --- the grid -----------------------------------------------------------------------------


def _cell(
    day: date,
    *,
    today: date,
    selected: date,
    by_day: Mapping[date, list[EconomicEvent]],
) -> DayCell:
    events = by_day.get(day, [])
    high = sum(1 for e in events if e.impact is Impact.HIGH)
    medium = sum(1 for e in events if e.impact is Impact.MEDIUM)
    low = len(events) - high - medium
    parts = [_day_label(day)]
    if day == today:
        parts.append("aujourd'hui")
    parts.append(
        f"{len(events)} annonce(s) : {high} fort impact, {medium} moyen"
        if events
        else "aucune annonce enregistrée"
    )
    strongest = sorted(events, key=lambda e: (-e.impact.rank, e.starts_at))[:2]
    titles = tuple(f"{_hm(e.starts_at)} {e.currency} {e.title}" for e in strongest)
    return DayCell(
        iso=day.isoformat(),
        number=day.day,
        weekday=WEEKDAYS[day.weekday()],
        month_label=MONTHS_SHORT[day.month - 1],
        is_today=day == today,
        is_selected=day == selected,
        is_weekend=day.weekday() >= 5,
        high=high,
        medium=medium,
        low=low,
        total=len(events),
        href=_day_href(day, today),
        aria=", ".join(parts),
        top_titles=titles,
    )


# --- freshness, sources, brief ------------------------------------------------------------


def _source_text(report: SourceReport) -> str:
    if report.ok:
        text = f"{report.items} élément(s)"
        return f"{text} ; {report.note}" if report.note else text
    if report.status == 429:
        wait = f", réessai dans {report.retry_after // 60 + 1} min" if report.retry_after else ""
        return f"limite de débit de l'hôte (HTTP 429){wait}"
    if report.status is not None and report.status >= 400:
        return f"indisponible (HTTP {report.status})"
    return f"indisponible : {report.error or 'aucune réponse'}"


def _sources(reports: Sequence[SourceReport], at: datetime) -> tuple[SourceView, ...]:
    return tuple(
        SourceView(
            key=report.source,
            label=SOURCE_LABELS.get(report.source, report.source),
            ok=report.ok,
            text=_source_text(report),
            age=_age(at - report.fetched_at),
        )
        for report in reports
    )


def _freshness(
    reports: Sequence[SourceReport],
    last_calendar_success: datetime | None,
    at: datetime,
    stale_after: timedelta,
    stored_events: int,
) -> Freshness:
    details: list[str] = []
    failing = [r for r in reports if not r.ok]
    for report in failing:
        details.append(
            f"{SOURCE_LABELS.get(report.source, report.source)} : {_source_text(report)}"
        )
    if not reports:
        return Freshness(
            level="bad",
            headline="Le calendrier n'a jamais été rafraîchi.",
            details=(
                "Aucune récupération n'est enregistrée : l'agent n'a pas encore tourné avec la "
                "section « news » active, ou la migration vient d'être appliquée. Aucune "
                "annonce n'est affichée parce qu'aucune n'a été lue, pas parce qu'il n'y en a "
                "pas. Le filtre d'annonces laisse passer les signaux tant que c'est le cas.",
            ),
            last_refresh="jamais",
        )
    if last_calendar_success is None:
        return Freshness(
            level="bad",
            headline="Le calendrier économique est injoignable.",
            details=(
                *details,
                "Aucune récupération du calendrier n'a réussi : les annonces affichées ne sont "
                "pas fiables. Le filtre d'annonces laisse passer les signaux.",
            ),
            last_refresh="jamais réussi",
        )
    age = at - last_calendar_success
    last = f"{last_calendar_success:%Y-%m-%d %H:%M} UTC ({_age(age)})"
    if age > stale_after:
        return Freshness(
            level="bad",
            headline=f"Calendrier périmé : dernière mise à jour réussie {_age(age)}.",
            details=(
                *details,
                "Au-delà de ce délai le filtre d'annonces laisse passer les signaux et le dit "
                "dans le journal : les annonces ci-dessous peuvent être incomplètes ou déplacées.",
            ),
            last_refresh=last,
        )
    empty = next(
        (
            r
            for r in reports
            if r.source == CALENDAR_SOURCE and (r.error or "").startswith(EMPTY_CALENDAR)
        ),
        None,
    )
    if empty is not None:
        return Freshness(
            level="bad",
            headline="Calendrier vide : le dernier export ne listait aucune annonce.",
            details=(
                *details,
                "Un jour de semaine, une réponse sans aucune annonce pertinente est un export "
                "cassé, pas une journée calme : le filtre d'annonces ne voit rien et laisse "
                "passer les signaux (ou les refuse si news.fail_closed_when_blind est actif "
                "en mode réel).",
            ),
            last_refresh=last,
        )
    if failing:
        return Freshness(
            level="warn",
            headline=f"Calendrier à jour, {len(failing)} source(s) secondaire(s) en échec.",
            details=tuple(details),
            last_refresh=last,
        )
    note = () if stored_events else ("Les sources répondent mais ne listent aucune annonce.",)
    return Freshness(level="ok", headline="Calendrier à jour.", details=note, last_refresh=last)


def _brief_view(brief: DailyBrief, origin: str, states: Mapping[str, MarketState]) -> BriefView:
    labels = {
        "ai": "Synthèse du matin, rédigée par le modèle",
        "rules": "Synthèse du matin, rédigée par les règles",
        "live": "Synthèse calculée à l'affichage par les règles (aucune synthèse stockée)",
    }
    markets: list[MarketBriefView] = []
    for market in brief.markets:
        state = states.get(market.symbol, MarketState(market.symbol))
        price = f"{state.last_price:,.2f}".replace(",", " ") if state.last_price else "—"
        markets.append(
            MarketBriefView(
                symbol=market.symbol,
                bias=market.bias.value,
                bias_label=BIAS_LABELS[market.bias],
                stance=market.stance.value,
                stance_label=STANCE_LABELS[market.stance],
                summary=market.summary,
                state_phrase=market_phrase(state),
                last_price=price,
            )
        )
    return BriefView(
        headline=brief.headline,
        markets=tuple(markets),
        origin=origin,
        origin_label=labels[origin],
        generated=f"{brief.generated_at:%Y-%m-%d %H:%M} UTC",
    )


# --- the page -----------------------------------------------------------------------------


def _heat_label(heat: int) -> str:
    return "Calme" if heat < HEAT_CALM else ("Actif" if heat < HEAT_BUSY else "Chargé")


def _bias_of(score: int) -> Bias:
    if score >= LEAN_THRESHOLD:
        return Bias.BULLISH
    return Bias.BEARISH if score <= -LEAN_THRESHOLD else Bias.NEUTRAL


def _market_outlook(
    symbol: str,
    state: MarketState,
    items: Sequence[EventView],
    imminent: EventView | None,
    brief_summary: str,
) -> MarketOutlook:
    """Technical (the candles' trend) and fundamental (the day's announcements) in one reading.

    Fundamental = mean of the signed verdict scores of the day. Each score already carries its
    influence, so one payrolls outweighs ten minor releases. Both halves count equally when
    both are known.
    Pure rules over data already shown on the page: it creates no signal and moves no stop.
    """
    scores = [v.score for item in items for v in item.verdicts if v.symbol == symbol]
    fundamental = round(sum(scores) / len(scores)) if scores else 0
    has_fundamental = bool(scores)
    technical = (
        {"up": TREND_WEIGHT, "down": -TREND_WEIGHT}.get(state.trend, 0) if state.known else 0
    )
    # A flat trend is a real reading (technical 0), not a missing one: it still weighs half and
    # pulls the score toward neutral. Only an unknown trend drops the technical half.
    if state.known and has_fundamental:
        score = round((technical + fundamental) / 2)
    elif state.known:
        score = technical
    else:
        score = fundamental
    bias = _bias_of(score)
    stance = Stance.WAIT
    if imminent is None:
        if bias is Bias.BULLISH and state.trend != "down":
            stance = Stance.BUY
        elif bias is Bias.BEARISH and state.trend != "up":
            stance = Stance.SELL

    parts: list[str] = []
    if state.known and has_fundamental and technical and _bias_of(fundamental) is not Bias.NEUTRAL:
        agree = (technical > 0) == (fundamental > 0)
        parts.append("Analyses concordantes." if agree else "Analyses divergentes : prudence.")
    if imminent is not None:
        parts.append(f"Annonce forte à {imminent.time_utc} UTC : attendre.")
    elif stance is Stance.WAIT and bias is not Bias.NEUTRAL:
        parts.append("Tendance contraire à la lecture : on n'agit pas.")
    parts.append(f"Biais {BIAS_LABELS[bias].lower()}, {STANCE_LABELS[stance].lower()}.")

    price = f"{state.last_price:,.2f}".replace(",", " ") if state.last_price else "—"
    return MarketOutlook(
        symbol=symbol,
        last_price=price,
        state_known=state.known,
        trend=state.trend,
        trend_label=TREND_LABELS[state.trend] if state.known else "inconnue",
        volatility_label=VOLATILITY_LABELS[state.volatility] if state.known else "inconnue",
        technical=technical,
        technical_text=_signed(technical) if state.known else "—",
        fundamental=fundamental,
        fundamental_text=_signed(fundamental) if has_fundamental else "—",
        has_fundamental=has_fundamental,
        score=score,
        score_text=_signed(score),
        score_abs=abs(score),
        bias=bias.value,
        bias_label=BIAS_LABELS[bias],
        stance=stance.value,
        stance_label=STANCE_LABELS[stance],
        prediction=" ".join(parts),
        summary=brief_summary,
    )


def _outlook(
    symbols: Sequence[str],
    states: Mapping[str, MarketState],
    items: Sequence[EventView],
    brief: DailyBrief | None,
) -> Outlook:
    imminent = next((i for i in items if i.state == "soon" and i.impact == "high"), None)
    summaries = {m.symbol: m.summary for m in brief.markets} if brief is not None else {}
    markets = tuple(
        _market_outlook(
            symbol,
            states.get(symbol, MarketState(symbol)),
            items,
            imminent,
            summaries.get(symbol, ""),
        )
        for symbol in symbols
    )
    score = round(sum(m.score for m in markets) / len(markets)) if markets else 0
    heat = max((i.influence for i in items), default=0)
    strong = sum(1 for i in items if i.impact == "high")
    return Outlook(
        markets=markets,
        score=score,
        score_text=_signed(score),
        score_abs=abs(score),
        bias=_bias_of(score).value,
        bias_label=BIAS_LABELS[_bias_of(score)],
        heat=heat,
        heat_label=_heat_label(heat),
        headline=(
            f"Journée {_heat_label(heat).lower()} : {strong} annonce(s) à fort impact "
            f"sur {len(items)} élément(s)."
            if items
            else "Aucune annonce enregistrée : la lecture repose sur la technique seule."
        ),
        method=(
            "Score = moyenne de la tendance des bougies (±50) et de la lecture des annonces du "
            "jour pondérée par leur influence. Calcul par règles sur les lectures affichées "
            "(modèle ou règles) : un repère, pas une prévision."
        ),
    )


def _clock_label() -> tuple[bool, str]:
    state = clock_status()
    if not state.synced:
        return False, "Heure du PC (horloge en ligne injoignable, non vérifiée)"
    gap = state.offset_seconds or 0.0
    return True, f"Heure en ligne ({state.source_count} source(s)), PC décalé de {gap:+.0f} s"


def build_calendar(
    engine: Engine,
    at: datetime,
    *,
    day: str | None = None,
    stale_after: timedelta | None = None,
) -> CalendarView:
    """Read the calendar for the page. Raises `BadCalendarRequest` on a malformed parameter."""
    chosen = parse_day(day)
    at = at.astimezone(UTC)
    today = at.date()
    selected = chosen or today
    stale = stale_after or timedelta(minutes=DEFAULT_NEWS_CONFIG.max_staleness_minutes)

    store = NewsStore(engine)
    week_start = selected - timedelta(days=selected.weekday())
    lo = min(today, week_start)
    hi = max(
        week_start + timedelta(days=STRIP_DAYS + 1),
        today + timedelta(days=LOOKAHEAD_DAYS + 1),
        selected + timedelta(days=1),
    )
    stored = store.events_between(
        datetime.combine(lo, datetime.min.time(), UTC),
        datetime.combine(hi, datetime.min.time(), UTC),
    )

    scheduled = [e for e in stored if e.kind is not EventKind.HEADLINE]
    by_day: dict[date, list[EconomicEvent]] = {}
    for event in scheduled:
        by_day.setdefault(event.starts_at.astimezone(UTC).date(), []).append(event)

    states = MarketStateReader(CandleStore(engine)).read(at)

    day_events = sorted(by_day.get(selected, []), key=lambda e: (e.starts_at, e.title))
    day_headlines = sorted(
        (
            e
            for e in stored
            if e.kind is EventKind.HEADLINE and e.starts_at.astimezone(UTC).date() == selected
        ),
        key=lambda e: e.starts_at,
        reverse=True,
    )[:HEADLINES_SHOWN]
    upcoming_majors = sorted(
        (
            e
            for e in scheduled
            if e.impact is Impact.HIGH and at <= e.starts_at <= at + timedelta(days=LOOKAHEAD_DAYS)
        ),
        key=lambda e: e.starts_at,
    )

    shown = {e.key: e for e in (*day_events, *day_headlines, *upcoming_majors[:1])}
    analyses = store.latest_analyses(shown)

    selected_views = _views(day_events, analyses, states, at)
    day_headline_views = _views(day_headlines, analyses, states, at)
    next_major = _views(upcoming_majors[:1], analyses, states, at)[0] if upcoming_majors else None

    # The morning brief: stored if the service wrote one for today, otherwise the rules' own.
    today_iso = today.isoformat()
    stored_brief = store.latest_brief(today_iso)
    brief: BriefView | None
    brief_model: DailyBrief | None = None
    if stored_brief is not None:
        brief_model = stored_brief
        brief = _brief_view(stored_brief, "ai" if stored_brief.source == "ai" else "rules", states)
    elif stored:
        brief_model = fallback_brief(today_iso, by_day.get(today, []), analyses, states, at)
        brief = _brief_view(brief_model, "live", states)
    else:
        brief = None

    outlook = _outlook(
        (XAUUSD, BTCUSD),
        states,
        [*selected_views, *day_headline_views],
        brief_model if selected == today else None,
    )

    reports = store.latest_fetches()
    # Freshness is the primary calendar's alone: the optional next-week feed answers on its own.
    freshness = _freshness(reports, store.last_success(CALENDAR_SOURCE), at, stale, len(scheduled))

    ai_seen = any(a.source == "ai" for a in analyses.values())
    ai_calls = store.ai_calls_between(
        datetime.combine(today, datetime.min.time(), UTC),
        datetime.combine(today + timedelta(days=1), datetime.min.time(), UTC),
    )
    if ai_seen or ai_calls:
        ai_note = (
            f"{ai_calls} appel(s) au modèle aujourd'hui pour lire les annonces ; les annonces "
            "sans lecture IA sont lues par les règles (étiquette « règles »)."
        )
    else:
        ai_note = (
            "Aucune lecture par le modèle n'est enregistrée aujourd'hui : clé absente, budget "
            "du jour épuisé, ou aucune annonce nouvelle à lire. Les lectures affichées sont "
            "celles des règles, étiquetées comme telles."
        )

    # The note under the day panel says what an empty day means.
    note = ""
    if not day_events:
        if selected > today + timedelta(days=COVERED_DAYS_AHEAD - 1):
            note = (
                "Les sources ne publient pas encore ce jour : l'absence d'annonce ici "
                "ne veut pas dire qu'il n'y en aura pas."
            )
        elif not scheduled and not reports:
            note = "Le calendrier n'a jamais été rafraîchi : aucun jour n'est renseigné."
        else:
            note = "Aucune annonce enregistrée pour ce jour."

    synced, clock_label = _clock_label()
    return CalendarView(
        now_label=f"{at:%H:%M} UTC",
        paris_label=f"{to_paris(at):%H:%M} Paris",
        clock_synced=synced,
        clock_label=clock_label,
        freshness=freshness,
        brief=brief,
        outlook=outlook,
        next_major=next_major,
        strip=tuple(
            _cell(
                week_start + timedelta(days=offset), today=today, selected=selected, by_day=by_day
            )
            for offset in range(STRIP_DAYS)
        ),
        selected=selected.isoformat(),
        selected_label=_day_label(selected),
        selected_is_today=selected == today,
        selected_events=tuple(selected_views),
        selected_headlines=tuple(day_headline_views),
        selected_high=sum(1 for e in day_events if e.impact is Impact.HIGH),
        selected_medium=sum(1 for e in day_events if e.impact is Impact.MEDIUM),
        selected_note=note,
        sources=_sources(reports, at),
        ai_note=ai_note,
        total_events=len(scheduled),
    )


__all__ = [
    "BadCalendarRequest",
    "CalendarView",
    "build_calendar",
    "paris_offset",
    "parse_day",
    "to_paris",
]
