"""The scalping statistics of §32, as pure functions.

Every expected figure below is computed by hand from the trades literals, so a change in the
code that changes a number fails here instead of silently moving the dashboard.

Conventions under test:
  * every axis splits on `closed_at` (the closing time, like `axes.group`);
  * sessions are UTC, half-open: asie [0,7[, londres [7,13[, new_york [13,21[,
    apres_cloture [21,24[;
  * a band is `[edge_i, edge_i+1[`: the lower edge is inclusive, the upper exclusive;
  * a trade missing the figure an axis needs (no spread, no duration reading) is left out of
    that axis, never counted as a zero;
  * only non-empty buckets are returned: an empty series yields an empty tuple, and a band
    no trade falls into is omitted rather than filled with zeros.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradingagent.analytics.model import Trade
from tradingagent.analytics.scalping import (
    Bucket,
    CostSummary,
    Session,
    by_duration,
    by_hour,
    by_session,
    by_size,
    by_spread,
    by_volatility,
    by_weekday,
    cost_summary,
    session_of,
)
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)  # a Tuesday


def a_trade(
    *,
    closed_at: datetime = T0,
    opened_at: datetime | None = None,
    pnl: str = "10",
    slippage: float | None = None,
    spread: float | None = None,
) -> Trade:
    return Trade(
        symbol="XAUUSD",
        strategy_ref="witness@1.0.0",
        direction=Direction.BUY,
        timeframe=Timeframe.M15,
        mode=TradingMode.DEMO,
        opened_at=opened_at if opened_at is not None else closed_at - timedelta(minutes=5),
        closed_at=closed_at,
        pnl_eur=Decimal(pnl),
        risk_eur=Decimal("20"),
        slippage=slippage,
        spread=spread,
    )


def at(hour: int, minute: int = 0, day: int = 6) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------------------------------
# by_hour: chronological order of the 24 hours.
#   09:00 -> +10 and 09:30 -> -4 : net 6, 1 win of 2, expectancy 6 / 2 = 3
#   13:00 -> +6                  : net 6, 1 win of 1, expectancy 6 / 1 = 6
# ---------------------------------------------------------------------------------------
def test_hours_are_ordered_and_hand_computed() -> None:
    trades = [
        a_trade(closed_at=at(13), pnl="6"),
        a_trade(closed_at=at(9, 30), pnl="-4"),
        a_trade(closed_at=at(9), pnl="10"),
    ]

    buckets = by_hour(trades)

    assert [bucket.key for bucket in buckets] == ["09", "13"]
    assert buckets[0].net_profit == Decimal("6")
    assert buckets[0].win_rate == pytest.approx(0.5)
    assert buckets[0].expectancy == Decimal("3")
    assert buckets[0].sample == 2
    assert buckets[1].net_profit == Decimal("6")
    assert buckets[1].win_rate == pytest.approx(1.0)
    assert buckets[1].expectancy == Decimal("6")


def test_an_hour_without_trades_is_absent_rather_than_zero_filled() -> None:
    buckets = by_hour([a_trade(closed_at=at(3))])

    assert [bucket.key for bucket in buckets] == ["03"]


# ---------------------------------------------------------------------------------------
# by_session: the four UTC sessions, in display order.
# ---------------------------------------------------------------------------------------
def test_sessions_are_utc_and_half_open() -> None:
    assert session_of(a_trade(closed_at=at(0))) is Session.ASIA
    assert session_of(a_trade(closed_at=at(6, 59))) is Session.ASIA
    assert session_of(a_trade(closed_at=at(7))) is Session.LONDON
    assert session_of(a_trade(closed_at=at(12, 59))) is Session.LONDON
    assert session_of(a_trade(closed_at=at(13))) is Session.NEW_YORK
    assert session_of(a_trade(closed_at=at(20, 59))) is Session.NEW_YORK
    assert session_of(a_trade(closed_at=at(21))) is Session.AFTER_HOURS
    assert session_of(a_trade(closed_at=at(23, 59))) is Session.AFTER_HOURS


def test_sessions_keep_their_display_order_whatever_the_input_order() -> None:
    trades = [
        a_trade(closed_at=at(22), pnl="3"),  # apres_cloture
        a_trade(closed_at=at(5), pnl="1"),  # asie
        a_trade(closed_at=at(14), pnl="2"),  # new_york
        a_trade(closed_at=at(12), pnl="-1"),  # londres
    ]

    buckets = by_session(trades)

    assert [bucket.key for bucket in buckets] == ["asie", "londres", "new_york", "apres_cloture"]
    assert [bucket.net_profit for bucket in buckets] == [
        Decimal("1"),
        Decimal("-1"),
        Decimal("2"),
        Decimal("3"),
    ]
    assert [bucket.win_rate for bucket in buckets] == [1.0, 0.0, 1.0, 1.0]


# ---------------------------------------------------------------------------------------
# by_weekday: Monday first, then the calendar order.
# ---------------------------------------------------------------------------------------
def test_weekdays_start_on_monday() -> None:
    monday = datetime(2026, 10, 5, 12, tzinfo=UTC)
    sunday = datetime(2026, 10, 11, 12, tzinfo=UTC)
    trades = [
        a_trade(closed_at=sunday, pnl="-3"),
        a_trade(closed_at=monday, pnl="7"),
        a_trade(closed_at=monday + timedelta(hours=1), pnl="1"),
    ]

    buckets = by_weekday(trades)

    assert [bucket.key for bucket in buckets] == ["Monday", "Sunday"]
    assert buckets[0].net_profit == Decimal("8")
    assert buckets[0].win_rate == pytest.approx(1.0)
    assert buckets[0].expectancy == Decimal("4")  # 8 / 2
    assert buckets[1].net_profit == Decimal("-3")
    assert buckets[1].win_rate == pytest.approx(0.0)


# ---------------------------------------------------------------------------------------
# by_spread: bands of [edge_i, edge_i+1[, edges in observed spread units.
#   0.25 -> <0.5        : net 10
#   0.5  -> [0.5,1[     : net 20
#   0.75 -> [0.5,1[     : net -5   -> band net 15, 1 win of 2, expectancy 7.5
#   1.0  -> >=1         : net 2
#   None -> excluded from the axis entirely
# ---------------------------------------------------------------------------------------
def test_spread_bands_are_lower_inclusive_and_upper_exclusive() -> None:
    trades = [
        a_trade(spread=1.0, pnl="2"),
        a_trade(spread=0.25, pnl="10"),
        a_trade(spread=0.75, pnl="-5"),
        a_trade(spread=0.5, pnl="20"),
        a_trade(spread=None, pnl="100"),
    ]

    buckets = by_spread(trades, (0.5, 1.0))

    assert [bucket.key for bucket in buckets] == ["<0.5", "[0.5,1[", ">=1"]
    assert [bucket.net_profit for bucket in buckets] == [
        Decimal("10"),
        Decimal("15"),
        Decimal("2"),
    ]
    assert buckets[1].win_rate == pytest.approx(0.5)
    assert buckets[1].expectancy == Decimal("7.5")  # 15 / 2
    assert sum(bucket.sample for bucket in buckets) == 4  # the spread-less trade is out


def test_no_trade_means_no_band_even_with_edges() -> None:
    assert by_spread([a_trade(spread=None)], (0.5, 1.0)) == ()


# ---------------------------------------------------------------------------------------
# by_duration: bands in seconds, [edge_i, edge_i+1[.
#   30 s  -> <60       : net 1
#   60 s  -> [60,300[  : net 2
#   299 s -> [60,300[  : net -1  -> band net 1, 1 win of 2, expectancy 0.5
#   300 s -> >=300     : net 4
# ---------------------------------------------------------------------------------------
def test_duration_bands_are_in_seconds() -> None:
    opened = at(12)
    trades = [
        a_trade(opened_at=opened, closed_at=opened + timedelta(seconds=300), pnl="4"),
        a_trade(opened_at=opened, closed_at=opened + timedelta(seconds=30), pnl="1"),
        a_trade(opened_at=opened, closed_at=opened + timedelta(seconds=299), pnl="-1"),
        a_trade(opened_at=opened, closed_at=opened + timedelta(seconds=60), pnl="2"),
    ]

    buckets = by_duration(trades, (60.0, 300.0))

    assert [bucket.key for bucket in buckets] == ["<60", "[60,300[", ">=300"]
    assert [bucket.net_profit for bucket in buckets] == [
        Decimal("1"),
        Decimal("1"),
        Decimal("4"),
    ]
    assert buckets[1].win_rate == pytest.approx(0.5)
    assert buckets[1].expectancy == Decimal("0.5")


# ---------------------------------------------------------------------------------------
# by_volatility: the reading is supplied by the caller; a trade it cannot read is out.
# ---------------------------------------------------------------------------------------
def test_volatility_is_read_through_the_caller() -> None:
    low, mid, high, unknown = (a_trade(pnl=str(n)) for n in range(4))
    readings = {id(low): 0.5, id(mid): 1.0, id(high): 3.0, id(unknown): None}
    trades = [high, low, unknown, mid]

    buckets = by_volatility(trades, lambda trade: readings[id(trade)], (1.0, 2.0))

    assert [bucket.key for bucket in buckets] == ["<1", "[1,2[", ">=2"]
    assert [bucket.sample for bucket in buckets] == [1, 1, 1]
    assert sum(bucket.sample for bucket in buckets) == 3  # the unreadable one is out


# ---------------------------------------------------------------------------------------
# by_size: decimal edges, so a 0.005 lot is not rounded out of its band.
# ---------------------------------------------------------------------------------------
def test_size_bands_use_the_decimal_edges() -> None:
    small, exact, big, unknown = (a_trade(pnl=str(n)) for n in range(4))
    sizes: dict[int, Decimal | None] = {
        id(small): Decimal("0.005"),
        id(exact): Decimal("0.01"),
        id(big): Decimal("0.05"),
        id(unknown): None,
    }

    buckets = by_size(
        [big, small, unknown, exact],
        lambda trade: sizes[id(trade)],
        (Decimal("0.01"), Decimal("0.05")),
    )

    assert [bucket.key for bucket in buckets] == ["<0.01", "[0.01,0.05[", ">=0.05"]
    assert [bucket.sample for bucket in buckets] == [1, 1, 1]


def test_no_edges_means_one_band_holding_everything_readable() -> None:
    trades = [a_trade(spread=0.2, pnl="4"), a_trade(spread=9.0, pnl="6")]

    buckets = by_spread(trades, ())

    assert [bucket.key for bucket in buckets] == ["all"]
    assert buckets[0].net_profit == Decimal("10")
    assert buckets[0].sample == 2


def test_edges_must_be_strictly_increasing() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        by_spread([a_trade(spread=0.5)], (1.0, 0.5))
    with pytest.raises(ValueError, match="strictly increasing"):
        by_spread([a_trade(spread=0.5)], (0.5, 0.5))


# ---------------------------------------------------------------------------------------
# Determinism: same trades, any input order, same buckets; chronological inside a bucket.
# ---------------------------------------------------------------------------------------
def test_buckets_are_deterministic_and_chronological_inside() -> None:
    first = a_trade(closed_at=at(9), pnl="1")
    second = a_trade(closed_at=at(9, 30), pnl="2")
    third = a_trade(closed_at=at(9, 45), pnl="3")

    forward = by_hour([first, second, third])
    backward = by_hour([third, first, second])

    assert forward == backward
    assert forward[0].trades == (first, second, third)


def test_an_empty_series_has_no_buckets_at_all() -> None:
    assert by_hour([]) == ()
    assert by_session([]) == ()
    assert by_weekday([]) == ()
    assert by_spread([], (0.5, 1.0)) == ()
    assert by_duration([], (60.0,)) == ()
    assert by_volatility([], lambda trade: 1.0, (0.5,)) == ()
    assert by_size([], lambda trade: Decimal(1), (Decimal(5),)) == ()


def test_every_readable_trade_lands_in_exactly_one_bucket() -> None:
    trades = [
        a_trade(closed_at=at(9), spread=0.2, pnl="1"),
        a_trade(closed_at=at(13), spread=0.7, pnl="-1"),
        a_trade(closed_at=at(22), spread=None, pnl="2"),
        a_trade(closed_at=at(14), spread=0.9, pnl="3"),
    ]
    edges = (0.5, 0.75)

    buckets = by_spread(trades, edges)

    assert sum(bucket.sample for bucket in buckets) == 3
    assert sum(bucket.sample for bucket in by_hour(trades)) == 4
    assert all(isinstance(bucket, Bucket) for bucket in buckets)


# ---------------------------------------------------------------------------------------
# cost_summary, hand computed:
#   c1: slippage 1.5, spread 0.3, cost 1.00 EUR
#   c2: slippage 0.5, spread None, cost 0.50 EUR
#   c3: slippage None, spread None, cost 0.25 EUR
#   c4: slippage None, spread None, cost None
#   slippage: (1.5 + 0.5) / 2 = 1.0, worst 1.5, sample 2
#   spread:   0.3, sample 1
#   cost:     (1.00 + 0.50 + 0.25) / 3 = 1.75 / 3, sample 3
#   sample:   trades carrying at least one reading = 3
# ---------------------------------------------------------------------------------------
def test_cost_summary_reports_each_sample_and_never_zero_fills() -> None:
    trades = [
        a_trade(slippage=1.5, spread=0.3),
        a_trade(slippage=0.5),
        a_trade(),
        a_trade(),
    ]
    costs = {
        id(trades[0]): Decimal("1.00"),
        id(trades[1]): Decimal("0.50"),
        id(trades[2]): Decimal("0.25"),
        id(trades[3]): None,
    }

    summary = cost_summary(trades, lambda trade: costs[id(trade)])

    assert summary == CostSummary(
        sample=3,
        slippage_sample=2,
        average_slippage=1.0,
        max_slippage=1.5,
        spread_sample=1,
        average_spread=0.3,
        cost_sample=3,
        average_cost_eur=Decimal("1.75") / Decimal(3),
    )


def test_cost_summary_without_a_source_of_euro_costs_leaves_it_none() -> None:
    summary = cost_summary([a_trade(slippage=1.5, spread=0.3), a_trade()])

    assert summary.cost_sample == 0
    assert summary.average_cost_eur is None
    assert summary.sample == 1
    assert summary.slippage_sample == 1
    assert summary.max_slippage == 1.5


def test_cost_summary_on_an_empty_series_is_all_none() -> None:
    summary = cost_summary([])

    assert summary == CostSummary(
        sample=0,
        slippage_sample=0,
        average_slippage=None,
        max_slippage=None,
        spread_sample=0,
        average_spread=None,
        cost_sample=0,
        average_cost_eur=None,
    )
