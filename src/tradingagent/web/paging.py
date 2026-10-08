"""Server-side paging, search and market selection — one primitive for every list.

The dashboard never sends a whole table to the browser to display ten rows of it. Each list
page asks this module for one page, built in SQL (``LIMIT``/``OFFSET``) from a filtered
count, and renders the links that carry the filter forward. Nothing here filters in the
browser: a page link is a URL, it can be copied, reloaded and bookmarked.

Two rules come from the operator's brief and are enforced here rather than in each template:

* **ten rows per display**, and the rest behind a link;
* **one market at a time** — ``market`` is a bound query parameter, never a client-side
  hiding trick, so the logs, the strategies, the reports and the positions can never mix
  XAUUSD and BTCUSD in the same table.

Amounts are ``Decimal`` and every figure comes from :mod:`tradingagent.analytics`; this
module moves rows around and words the empty states, it computes no figure (§34).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import String, cast, or_
from sqlalchemy.sql.elements import ColumnElement

PAGE_SIZE = 10
"""How many rows one display shows. The operator asked for ten, everywhere, without exception."""

# The order markets are offered in. XAUUSD is the instrument the agent was built for, so a
# page opens on it rather than on BTCUSD; anything else follows, sorted, discovered from the
# database — this list is a preference, never a whitelist.
MARKET_PREFERENCE: tuple[str, ...] = ("XAUUSD", "BTCUSD")
ALL_MARKETS = ""
"""The value of ``market`` meaning "no filter". Only ever set when nothing else is available."""


def page_number(value: str | None) -> int:
    """``?page=`` read defensively: a stale or garbled bookmark degrades, it never raises.

    The upper bound is not checked here — only the query that counted the rows knows how many
    pages exist, and it clamps to the last one.
    """
    if not value:
        return 1
    try:
        number = int(value.strip())
    except (AttributeError, ValueError):
        return 1
    return number if number > 0 else 1


def page_bounds(total: int, page: int, size: int) -> tuple[int, int]:
    """``(page, pages)`` clamped so a page is always a real page, even with zero rows."""
    pages = max(1, -(-max(0, total) // max(1, size)))
    return min(max(page, 1), pages), pages


def like_pattern(needle: str) -> str:
    """A case-insensitive ``%…%`` pattern whose wildcards are the literal text typed.

    ``%`` and ``_`` are escaped: an operator searching for ``take_profit`` must find it, and
    an operator typing ``__`` must not accidentally match the whole table.
    """
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def text_search(needle: str, columns: Sequence[Any]) -> list[ColumnElement[bool]]:
    """One ``OR`` over text columns, every branch a bound parameter.

    The columns are cast to text first: a LIKE pattern cannot travel through an enum or an
    integer column, whose own type validates its values.
    """
    if not needle.strip():
        return []
    pattern = like_pattern(needle.strip())
    return [or_(*[cast(column, String).ilike(pattern, escape="\\") for column in columns])]


def default_market(markets: Sequence[str]) -> str:
    """Which market a page opens on when the URL does not name one.

    Never a mixture: the first preferred market that exists, else the first one sorted. An
    empty database has no market, and therefore no filter — there is nothing to separate.
    """
    if not markets:
        return ALL_MARKETS
    for preferred in MARKET_PREFERENCE:
        if preferred in markets:
            return preferred
    return sorted(markets)[0]


def resolve_market(requested: str | None, markets: Sequence[str]) -> str:
    """The market a request asks for, constrained to the ones that exist.

    An unknown ``?market=`` falls back to the default rather than to "no filter": a stale
    bookmark must never be the thing that silently mixes two markets in one table.
    """
    cleaned = (requested or "").strip()
    if cleaned in markets:
        return cleaned
    return default_market(markets)


@dataclass(frozen=True)
class Page[T]:
    """One page of rows plus everything the pager and the empty state need.

    ``rows`` holds the rows themselves, already limited by SQL; ``total`` counts the whole
    filtered selection, not the page. ``noun`` is the word the empty message uses, in the
    operator's language.
    """

    rows: tuple[T, ...]
    total: int
    page: int
    pages: int
    page_size: int = PAGE_SIZE
    query: str = ""
    market: str = ALL_MARKETS
    path: str = "/"
    filters: tuple[tuple[str, str], ...] = ()
    noun: str = "ligne"
    feminine: bool = False

    @property
    def first_row(self) -> int:
        return 0 if not self.rows else (self.page - 1) * self.page_size + 1

    @property
    def last_row(self) -> int:
        return 0 if not self.rows else self.first_row + len(self.rows) - 1

    @property
    def showing(self) -> str:
        """What this display is showing, so the count is never guessed from the row count."""
        if not self.rows:
            return "0 sur 0"
        return f"{self.first_row}\u2013{self.last_row} sur {self.total}"

    @property
    def empty_message(self) -> str:
        """An empty table must say why it is empty, not just sit there mute."""
        article = "Aucune" if self.feminine else "Aucun"
        past = "enregistrée" if self.feminine else "enregistré"
        if self.query:
            return f"{article} {self.noun} pour « {self.query} »."
        if self.market:
            return f"{article} {self.noun} pour {self.market}."
        return f"{article} {self.noun} {past}."

    @property
    def has_multiple_pages(self) -> bool:
        return self.pages > 1

    def filter_value(self, name: str) -> str:
        """One carried filter, by name — what a ``<select>`` needs to mark its own choice."""
        for key, value in self.filters:
            if key == name:
                return value
        return ""

    def url(self, number: int) -> str:
        """The link to another page, carrying the search, the market and every filter."""
        parameters: list[tuple[str, str]] = [("page", str(number))]
        if self.query:
            parameters.append(("q", self.query))
        if self.market:
            parameters.append(("market", self.market))
        parameters.extend((name, value) for name, value in self.filters if value)
        return f"{self.path}?{urlencode(parameters)}"


__all__ = [
    "ALL_MARKETS",
    "MARKET_PREFERENCE",
    "PAGE_SIZE",
    "Page",
    "default_market",
    "like_pattern",
    "page_bounds",
    "page_number",
    "resolve_market",
    "text_search",
]
