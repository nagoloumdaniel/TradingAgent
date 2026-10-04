from dataclasses import replace
from decimal import Decimal as D

import pytest

from tradingagent.risk.model import InstrumentSpec, MarketQuote
from tradingagent.risk.sizing import SizingError, size_position

# Reference examples of the specification (formula, TASK-004), 1 USD = 0.888786 EUR.
USD = D("0.888786")
GOLD = InstrumentSpec("XAUUSD", D(100), D("0.01"), D("0.01"), D(100), D("0.01"), 0)
BTC = InstrumentSpec("BTCUSD", D(1), D("0.01"), D("0.01"), D(100), D("0.01"), 0)
GOLD_QUOTE = MarketQuote(D(2400), D("2400.2"), D("1094.00"), D(18393), USD)
BTC_QUOTE = MarketQuote(D(84000), D(84010), D("92.00"), D(37634), USD)
GOLD_STOP = D("12.3073")
BTC_STOP = D("104.1262")
DEMO_EQUITY = D("5497.74")


def gold(capital: D = DEMO_EQUITY, risk: D = D("0.005"), free: D = DEMO_EQUITY, **kw: object):
    return size_position(
        capital=capital,
        risk_per_trade=risk,
        stop_distance=kw.pop("stop", GOLD_STOP),  # type: ignore[arg-type]
        spec=kw.pop("spec", GOLD),  # type: ignore[arg-type]
        quote=kw.pop("quote", GOLD_QUOTE),  # type: ignore[arg-type]
        free_margin=free,
        margin_usage=D("0.5"),
        max_volume=kw.pop("max_volume", None),  # type: ignore[arg-type]
    )


def test_example_1_gold_demo() -> None:
    sizing = gold()
    assert sizing.volume == D("0.02")
    assert sizing.risk_eur == D("21.88")
    assert sizing.margin_eur == D("367.86")
    assert sizing.limited_by == "risk"
    assert round(sizing.risk_volume, 5) == D("0.02513")
    assert round(sizing.margin_volume, 5) == D("0.14945")


def test_example_2_gold_live_is_refused_below_the_minimum_lot() -> None:
    with pytest.raises(SizingError, match="minimum lot"):
        gold(capital=D(100), risk=D("0.02"), free=D(100))


def test_example_3_btc_demo_is_bounded_by_margin() -> None:
    sizing = size_position(
        capital=DEMO_EQUITY,
        risk_per_trade=D("0.005"),
        stop_distance=BTC_STOP,
        spec=BTC,
        quote=BTC_QUOTE,
        free_margin=DEMO_EQUITY,
        margin_usage=D("0.5"),
        max_volume=None,
    )
    assert sizing.volume == D("0.07")
    assert sizing.limited_by == "margin"
    assert round(sizing.risk_eur, 2) == D("6.48")
    assert sizing.margin_eur == D("2634.38")
    assert round(sizing.risk_volume, 5) == D("0.29703")


def test_the_larger_of_the_two_loss_estimates_is_used() -> None:
    # BTC: broker 92.00, independent 92.55: sizing on 92.00 would risk more than intended.
    sizing = size_position(
        capital=DEMO_EQUITY,
        risk_per_trade=D("0.005"),
        stop_distance=BTC_STOP,
        spec=BTC,
        quote=BTC_QUOTE,
        free_margin=D(10**9),
        margin_usage=D("0.5"),
        max_volume=None,
    )
    assert sizing.volume == D("0.29")
    assert sizing.risk_eur <= DEMO_EQUITY * D("0.005")


def test_divergent_loss_estimates_refuse_the_order() -> None:
    with pytest.raises(SizingError, match="diverge"):
        gold(quote=replace(GOLD_QUOTE, loss_one_lot=D(1200)))


@pytest.mark.parametrize("field", ["loss_one_lot", "margin_one_lot", "profit_to_eur"])
def test_a_missing_broker_value_refuses_never_falls_back(field: str) -> None:
    with pytest.raises(SizingError):
        gold(quote=replace(GOLD_QUOTE, **{field: None}))  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["loss_one_lot", "margin_one_lot", "profit_to_eur"])
def test_a_non_positive_broker_value_refuses(field: str) -> None:
    with pytest.raises(SizingError):
        gold(quote=replace(GOLD_QUOTE, **{field: D(0)}))


def test_a_zero_stop_distance_refuses() -> None:
    with pytest.raises(SizingError, match="stop"):
        gold(stop=D(0))


def test_volume_is_always_rounded_down() -> None:
    # Risk allows 0.0299...: rounding to nearest would give 0.03 and overshoot the risk.
    capital = D("1094.00") * D("0.03") / D("0.005") - D("0.01")
    assert gold(capital=capital, free=D(10**9)).volume == D("0.02")


def test_configured_max_volume_caps_the_size() -> None:
    sizing = gold(max_volume=D("0.01"))
    assert (sizing.volume, sizing.limited_by) == (D("0.01"), "max_volume")


def test_broker_volume_max_caps_the_size() -> None:
    sizing = gold(capital=D(10**9), free=D(10**12), spec=replace(GOLD, volume_max=D(5)))
    assert (sizing.volume, sizing.limited_by) == (D(5), "volume_max")


@pytest.mark.parametrize("capital", [D(100), D("777.77"), D(5000), D("123456.78")])
@pytest.mark.parametrize("stop", [D("0.5"), D("3.3"), D("12.3073"), D(40)])
def test_realized_risk_never_exceeds_the_budget(capital: D, stop: D) -> None:
    loss = GOLD.contract_size * stop * USD
    quote = replace(GOLD_QUOTE, loss_one_lot=loss)
    try:
        sizing = gold(capital=capital, free=D(10**12), stop=stop, quote=quote)
    except SizingError:
        return
    assert sizing.risk_eur <= capital * D("0.005")


def test_no_capital_refuses() -> None:
    with pytest.raises(SizingError, match="capital"):
        gold(capital=D(0))
