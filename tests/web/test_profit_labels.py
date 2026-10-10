"""The labels the operator reads must match the figures behind them.

Two ways a dashboard lies without a single wrong computation: it calls a gross figure "net",
or it presents a convention as a fact — "session de Londres" when what it computed is a range
of UTC hours that has nothing to do with the broker's schedule. A third way is subtler: an
empty screen that reads like a fault. On 2026-10-10 the demonstration account held **zero**
closed trades, so every profitability screen was empty — correctly, since nothing had been
traded yet.

These are regression guards, not new figures: nothing here recomputes a performance number.
`trades.pnl_eur` is what it is (for an MT5 trade it already includes the broker's commission
and swap — `data/mt5_terminal.py` sums `d.profit + d.swap + d.commission`), and the cuts come
from `analytics.scalping`, the same pure module the reports use.
"""

from collections import Counter
from decimal import Decimal
from html import unescape

from fastapi.testclient import TestClient
from sqlalchemy import Engine
from tests.web.seed import XAU, Seeded

from tradingagent.analytics.model import Trade
from tradingagent.web import queries

#: What "net" is allowed to mean, stated once and pinned on both pages.
NET_MEANING = "commission et swap du courtier déjà déduits"


def _text(client: TestClient, path: str, **params: str) -> str:
    """The page as the reader sees it.

    Jinja escapes a value it substitutes — a note passed as a macro argument comes back as
    ``l&#39;heure`` — while literal template text is left alone. Whitespace is collapsed for
    the same reason: the browser collapses it, and a sentence wrapped in the template must
    read as one sentence here too.
    """
    body = unescape(client.get(path, params=params or None).text)
    return " ".join(body.split())


def _identity(trade: Trade) -> tuple[object, ...]:
    return (trade.symbol, trade.opened_at, trade.closed_at, trade.strategy_ref)


def test_the_net_sentence_says_what_net_includes(seeded_client: TestClient) -> None:
    """A reader must be able to check the label instead of trusting it."""
    for path in ("/scalping", "/strategies"):
        assert NET_MEANING in _text(seeded_client, path), path


def test_the_scalping_session_cut_is_declared_a_convention(seeded_client: TestClient) -> None:
    """The session cut is a UTC convention. The page says so where the cut is, not in a doc."""
    body = _text(seeded_client, "/scalping")

    assert (
        "Sessions conventionnelles déduites de l'heure UTC, pas les horaires du courtier." in body
    )


def test_the_overview_labels_the_gross_figures_as_gross(seeded_client: TestClient) -> None:
    """`net_profit` is labelled "Résultat net"; the two gross aggregates are labelled "bruts"."""
    body = _text(seeded_client, "/")

    assert "Résultat net" in body
    assert "bruts" in body  # gross_profit / gross_loss, never presented as a result


def test_every_bucket_of_a_cut_holds_real_trades_once_each(
    engine: Engine, populated: Seeded
) -> None:
    """No axis invents a trade, drops one silently into two buckets, or double-counts one."""
    view = queries.scalping_view(engine, market=XAU)
    known = Counter(_identity(trade) for trade in queries.market_trades(engine, XAU))
    assert known, "the seed must hold closed XAU trades for this test to mean anything"

    axes = {
        "hours": view.hours.rows,
        "sessions": view.sessions,
        "weekdays": view.weekdays,
        "spreads": view.spreads,
        "durations": view.durations,
        "sizes": view.sizes,
    }
    for name, buckets in axes.items():
        seen: Counter[tuple[object, ...]] = Counter()
        for bucket in buckets:
            held = Counter(_identity(trade) for trade in bucket.trades)
            seen.update(held)
            # The figure printed for the bucket is the sum of the trades it says it holds.
            expected = sum((trade.pnl_eur for trade in bucket.trades), Decimal(0))
            assert Decimal(bucket.net_profit) == expected, (name, bucket.key)
        # An axis may leave a trade out (a spread it never recorded), it may never duplicate
        # one, and everything it counted must be a trade of this market.
        assert all(count == 1 for count in seen.values()), name
        assert set(seen) <= set(known), name


def test_the_market_total_is_the_sum_of_what_the_cuts_show(
    engine: Engine, populated: Seeded
) -> None:
    """The three axes that cannot drop a trade must add up to the market's own total."""
    view = queries.scalping_view(engine, market=XAU)
    total = sum((trade.pnl_eur for trade in queries.market_trades(engine, XAU)), Decimal(0))

    for buckets in (view.hours.rows, view.sessions, view.weekdays, view.durations):
        assert sum((bucket.net_profit for bucket in buckets), Decimal(0)) == total


def test_the_scalping_page_counts_the_trades_it_cut(seeded_client: TestClient) -> None:
    """The "Trades analysés" card is the market's sample, not the page's ten rows."""
    body = _text(seeded_client, "/scalping", market=XAU)

    assert "Trades analysés" in body
    assert "toutes stratégies de XAUUSD" in body


# --- an empty screen is not a fault -----------------------------------------------------


def test_an_empty_market_that_never_traded_says_no_trade_yet(client: TestClient) -> None:
    """The demonstration account really held zero trades on 2026-10-10. "Nothing yet" is
    what the pages must say — a blank table or a failure notice would both be wrong."""
    assert "0 trade(s) clôturé(s) pour cette sélection" in _text(client, "/trades")
    assert "Aucun trade clôturé" in _text(client, "/scalping")
    assert "0 trade(s) clôturé(s)" in _text(client, "/")


def test_an_empty_screen_never_borrows_the_vocabulary_of_a_failure(client: TestClient) -> None:
    """No "erreur", no "indisponible" on a page that is merely empty: the two are different
    facts and an operator acts differently on each."""
    for path in ("/", "/trades", "/scalping", "/positions", "/strategies", "/ai-lab"):
        body = _text(client, path)
        assert "Erreur interne" not in body, path
        assert "Base de données indisponible" not in body, path
        assert "n'a pas pu" not in body, path


def test_an_empty_history_says_it_is_empty_rather_than_broken(client: TestClient) -> None:
    body = _text(client, "/positions")

    assert "Aucune position enregistrée." in body
    assert "0 sur 0" in body


def test_a_ratio_without_trades_is_n_a_and_never_an_invented_zero(client: TestClient) -> None:
    """A profit factor needs trades. `n/a` says "not measurable"; `0.00` would be a figure
    nobody computed, and the operator would read it as a real result."""
    body = _text(client, "/trades")

    assert "Facteur de profit" in body
    assert "n/a" in body
    assert "Taux de réussite" in body
