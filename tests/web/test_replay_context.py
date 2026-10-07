"""The market conditions at the moment of a replayed signal (§28).

The brief's remaining limit was that the replay showed *what happened* and not *what the
market looked like* when the signal fired. These tests cover the read
(:func:`tradingagent.web.queries.market_context`) and the page: the candles really stored
around the signal, the volatility regime taken from the daily pass's own ``regime_of``, the
UTC session from its own ``session_of``, the observed-versus-executed price, and the two
cases that must never lie — a database without candles, and a signal that never recorded an
ATR. Every value that the database does not hold stays ``None`` and is read ``n/a``.

The geometry is asserted on numbers computed by hand from the module's own constants, not by
calling the mapping function again: a test that repeats the formula proves nothing.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, insert, select, update
from tests.web import seed

from tradingagent.core.timeframe import Timeframe
from tradingagent.storage.models import CandleRow, SignalRow
from tradingagent.web import queries

# A moment no seeded chain uses, so an appended signal keeps its own idempotency key.
APPENDED_AT = seed.NOW - timedelta(days=4, minutes=11)


def _context(engine: Engine, signal_id: int) -> queries.MarketContext:
    replay = queries.trade_replay(engine, signal_id)
    assert replay is not None, f"signal {signal_id} must exist in the fixture"
    return queries.market_context(engine, replay)


def _set_indicators(engine: Engine, signal_id: int, indicators: dict[str, float]) -> None:
    """The signal's stored indicator JSON is the only thing this test rewrites."""
    with engine.begin() as connection:
        connection.execute(
            update(SignalRow).where(SignalRow.id == signal_id).values(indicators=indicators)
        )


# ---------------------------------------------------------------------------------------
# What the read gathers.
# ---------------------------------------------------------------------------------------


def test_the_candles_around_the_signal_are_the_stored_ones(
    engine: Engine, seeded: seed.Seeded
) -> None:
    seed.add_candles(engine, at=seeded.signal_generated_at, before=14, after=6)
    context = _context(engine, seeded.xau_signal_id)
    assert len(context.candles) == 20
    moments = [candle.open_time for candle in context.candles]
    assert moments == sorted(moments)
    # 14 at or before the signal, 6 strictly after it.
    assert sum(1 for moment in moments if moment <= seeded.signal_generated_at) == 14
    assert context.candles[0].open_time == seeded.signal_generated_at - timedelta(hours=13)
    assert context.candles[13].open_time == seeded.signal_generated_at


def test_the_recorded_indicators_are_read_verbatim(engine: Engine, seeded: seed.Seeded) -> None:
    context = _context(engine, seeded.xau_signal_id)
    assert context.indicators == {"ema_fast": 2649.5, "ema_slow": 2645.1}
    assert context.atr is None
    assert context.median_atr is None
    assert context.regime is None


@pytest.mark.parametrize(
    ("atr", "expected"),
    [
        (20.0, "volatilite_haute"),  # 20 / 10 = 2.0, above the 1.5 threshold
        (4.0, "volatilite_basse"),  #  4 / 10 = 0.4, below the 0.6 threshold
        (10.0, "volatilite_normale"),
    ],
)
def test_the_regime_compares_the_atr_to_the_markets_own_median(
    engine: Engine, seeded: seed.Seeded, atr: float, expected: str
) -> None:
    """Three markets so the median of one never leaks into another.

    The recorded ATRs are 10, 10, 10 then the signal's own: the upper median of
    ``[10, 10, 10, atr]`` is the third value, 10 — the daily pass's own definition.
    """
    del seeded
    market = f"TEST{expected.upper()}"
    for index in range(3):
        earlier = seed.add_chain(
            engine,
            market=market,
            generated_at=seed.NOW - timedelta(days=5 - index),
        )
        _set_indicators(engine, earlier, {"atr": 10.0})
    target = seed.add_chain(engine, market=market, generated_at=seed.NOW - timedelta(days=1))
    _set_indicators(engine, target, {"atr": atr})

    context = _context(engine, target)
    assert context.atr == atr
    assert context.median_atr == 10.0
    assert context.regime == expected


def test_a_signal_that_did_not_record_an_atr_has_no_regime(
    engine: Engine, seeded: seed.Seeded
) -> None:
    """No ATR is not "normal volatility": it is an absence, and it stays one."""
    signal_id = seed.add_chain(engine, market="EURUSD", generated_at=seed.NOW - timedelta(days=2))
    _set_indicators(engine, signal_id, {})
    context = _context(engine, signal_id)
    assert context.indicators == {}
    assert context.atr is None
    assert context.median_atr is None
    assert context.regime is None


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(2, "asie"), (9, "londres"), (15, "new_york"), (23, "apres_cloture")],
)
def test_the_session_is_the_utc_hour_of_the_signal(
    engine: Engine, seeded: seed.Seeded, hour: int, expected: str
) -> None:
    del seeded
    signal_id = seed.add_chain(
        engine,
        market="AUDUSD",
        generated_at=datetime(2026, 10, 7, hour, 0, tzinfo=UTC),
    )
    assert _context(engine, signal_id).session == expected


def test_the_observed_and_executed_prices_come_from_the_stored_rows(
    engine: Engine, seeded: seed.Seeded
) -> None:
    context = _context(engine, seeded.xau_signal_id)
    assert context.observed_price == 2650.0  # signals.observed_price
    assert context.executed_price == 2650.0  # executions.price
    assert context.slippage == 0.15  # executions.slippage, as stored


def test_a_signal_without_a_fill_has_no_executed_price_and_no_slippage(
    engine: Engine, seeded: seed.Seeded
) -> None:
    del seeded
    signal_id = seed.add_chain(engine, generated_at=APPENDED_AT, with_execution=False)
    context = _context(engine, signal_id)
    assert context.executed_price is None
    assert context.slippage is None


def test_a_signal_refused_by_risk_still_has_its_market_conditions(
    engine: Engine, seeded: seed.Seeded
) -> None:
    context = _context(engine, seeded.refused_signal_id)
    assert context.executed_price is None
    assert context.slippage is None
    assert context.session == "londres"  # 09:00 UTC
    assert context.observed_price == 2650.0


# ---------------------------------------------------------------------------------------
# The chart geometry, computed by hand from the module's constants.
# ---------------------------------------------------------------------------------------


def test_the_chart_geometry_is_hand_computed_from_the_stored_candles(
    engine: Engine, seeded: seed.Seeded
) -> None:
    """Ten identical candles, one signal on the first of them.

    With ``REPLAY_CHART_WIDTH = 960``, ``REPLAY_CHART_HEIGHT = 240``, top padding 18 and
    bottom padding 26: the plot runs from y=18 to y=214, the span is 101 - 99 = 2, each slot
    is 96 wide, and a body goes from y(100) = 116 to y(100.5) = 67.
    """
    del seeded
    at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    signal_id = seed.add_chain(engine, market="GBPUSD", generated_at=at)
    with engine.begin() as connection:
        for index in range(10):
            connection.execute(
                insert(CandleRow).values(
                    symbol="GBPUSD",
                    timeframe=Timeframe.H1,
                    open_time=at + timedelta(hours=index),
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.5,
                    source="test",
                    ingested_at=at,
                )
            )

    context = _context(engine, signal_id)
    chart = context.chart
    assert chart is not None
    assert (chart.width, chart.height) == (960, 240)
    assert (chart.plot_top, chart.plot_bottom) == (18.0, 214.0)
    assert (chart.high, chart.low) == (101.0, 99.0)
    assert chart.timeframe is Timeframe.H1
    assert len(chart.candles) == 10

    first = chart.candles[0]
    assert first.x == pytest.approx(48.0)  # slot / 2
    assert first.width == pytest.approx(40.32)  # 96 * 0.42
    assert first.wick_top == pytest.approx(18.0)  # y(101)
    assert first.wick_bottom == pytest.approx(214.0)  # y(99)
    assert first.body_top == pytest.approx(67.0)  # y(100.5)
    assert first.body_bottom == pytest.approx(116.0)  # y(100)
    assert first.up is True  # close 100.5 >= open 100.0

    assert chart.signal_x == pytest.approx(96.0)  # one candle at or before the signal
    assert chart.first_at == at
    assert chart.last_at == at + timedelta(hours=9)


def test_a_signal_outside_the_drawn_window_gets_no_marker(
    engine: Engine, seeded: seed.Seeded
) -> None:
    """Every candle sits after the signal: the axis holds no position for it, so none is
    invented."""
    del seeded
    at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    signal_id = seed.add_chain(engine, market="USDCHF", generated_at=at)
    with engine.begin() as connection:
        for index in range(12):
            connection.execute(
                insert(CandleRow).values(
                    symbol="USDCHF",
                    timeframe=Timeframe.H1,
                    open_time=at + timedelta(hours=index + 1),
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.5,
                    source="test",
                    ingested_at=at,
                )
            )
    chart = _context(engine, signal_id).chart
    assert chart is not None
    assert chart.signal_x is None


def test_a_market_without_the_signals_timeframe_uses_the_one_it_has(
    engine: Engine, seeded: seed.Seeded
) -> None:
    """A chart never mixes two units: the traced one is returned, and the page says which."""
    del seeded
    at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    signal_id = seed.add_chain(engine, market="NZDUSD", generated_at=at)
    seed.add_candles(
        engine,
        symbol="NZDUSD",
        at=at,
        before=12,
        after=0,
        timeframe=Timeframe.M15,
    )
    chart = _context(engine, signal_id).chart
    assert chart is not None
    assert chart.timeframe is Timeframe.M15
    assert len(chart.candles) == 12


# ---------------------------------------------------------------------------------------
# What the page shows.
# ---------------------------------------------------------------------------------------


def test_the_replay_page_draws_the_stored_candles(
    seeded_client: TestClient, engine: Engine, seeded: seed.Seeded
) -> None:
    seed.add_candles(engine, at=seeded.signal_generated_at, before=14, after=6)
    response = seeded_client.get(f"/trades/{seeded.xau_signal_id}")
    assert response.status_code == 200
    body = response.text
    assert "Conditions de marché au moment du signal" in body
    assert 'class="replay-chart"' in body
    assert 'class="wick up"' in body and 'class="wick down"' in body
    assert 'class="signal"' in body
    assert "20 bougies H1 réellement stockées" in body
    assert "Londres" in body  # 08:00 UTC
    assert "2650.00000 → 2650.00000" in body
    assert "glissement enregistré : 0.1500" in body


def test_a_database_without_candles_says_so_instead_of_drawing(
    seeded_client: TestClient, engine: Engine, seeded: seed.Seeded
) -> None:
    """The default dataset holds one candle per market, nowhere near the signal: the page
    states it and draws nothing — no 500, no straight line standing in for a chart."""
    response = seeded_client.get(f"/trades/{seeded.xau_signal_id}")
    assert response.status_code == 200
    assert "Aucune bougie stockée pour cet instant" in response.text
    assert 'class="replay-chart"' not in response.text
    assert _context(engine, seeded.xau_signal_id).chart is None


def test_under_the_threshold_the_page_draws_nothing(
    seeded_client: TestClient, engine: Engine, seeded: seed.Seeded
) -> None:
    written = seed.add_candles(engine, at=seeded.signal_generated_at, before=5, after=4)
    assert written == 9 < queries.REPLAY_MIN_CANDLES
    response = seeded_client.get(f"/trades/{seeded.xau_signal_id}")
    assert response.status_code == 200
    assert "Aucune bougie stockée pour cet instant" in response.text
    assert 'class="replay-chart"' not in response.text


def test_the_page_never_invents_a_missing_value(
    seeded_client: TestClient, engine: Engine, seeded: seed.Seeded
) -> None:
    del seeded
    signal_id = seed.add_chain(engine, generated_at=APPENDED_AT, with_execution=False)
    _set_indicators(engine, signal_id, {})
    response = seeded_client.get(f"/trades/{signal_id}")
    assert response.status_code == 200
    body = response.text
    assert "ATR n/a · médiane du marché n/a" in body
    assert "2650.00000 → n/a" in body
    assert "glissement enregistré : n/a" in body
    assert "Aucun indicateur enregistré pour ce signal" in body
    assert "moins de 10 autour du signal" in body


def test_the_page_says_which_unit_it_traced(seeded_client: TestClient, engine: Engine) -> None:
    at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    signal_id = seed.add_chain(engine, market="NZDUSD", generated_at=at)
    seed.add_candles(
        engine,
        symbol="NZDUSD",
        at=at,
        before=12,
        after=0,
        timeframe=Timeframe.M15,
    )
    body = seeded_client.get(f"/trades/{signal_id}").text
    assert "L'unité du signal (H1) n'a pas assez de bougies" in body
    assert "12 bougies M15 réellement stockées" in body


# ---------------------------------------------------------------------------------------
# Read-only: the new read adds no write, and the page still changes no row.
# ---------------------------------------------------------------------------------------


def _row_counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            "candles": int(
                connection.execute(select(func.count()).select_from(CandleRow)).scalar_one()
            ),
            "signals": int(
                connection.execute(select(func.count()).select_from(SignalRow)).scalar_one()
            ),
        }


def test_reading_the_market_conditions_changes_no_row(
    seeded_client: TestClient, engine: Engine, seeded: seed.Seeded
) -> None:
    """§34 applied to the new read: a chart is drawn, and the database is untouched."""
    seed.add_candles(engine, at=seeded.signal_generated_at, before=14, after=6)
    before = _row_counts(engine)
    response = seeded_client.get(f"/trades/{seeded.xau_signal_id}")
    assert response.status_code == 200
    assert 'class="replay-chart"' in response.text
    assert _row_counts(engine) == before


def test_the_market_context_also_answers_for_an_appended_signal(
    engine: Engine, seeded: seed.Seeded
) -> None:
    del seeded
    signal_id = seed.add_chain(engine, generated_at=APPENDED_AT)
    context = _context(engine, signal_id)
    assert isinstance(context, queries.MarketContext)
    assert context.symbol == seed.XAU
    assert context.session == "londres"  # 11:49 UTC
