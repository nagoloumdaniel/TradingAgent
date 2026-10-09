"""The 2026-10-09 BTCUSD refusals, replayed from the values the production database recorded.

Four BTCUSD BUY signals were refused between 03:45 and 04:15 UTC; the DEMO twin of the same
bar was authorized and then lost its order to a terminal argument error. This module rebuilds
the risk snapshot of each one from `signals` and `risk_decisions.checks` — the exact decimals
the engine printed that night — and pins what the engine decided, so the diagnosis in
`docs/research/execution-diagnostic/2026-10-09-rejets-risque.md` stays reproducible.

The replay is exact: every reason asserted below is byte-for-byte the string stored in
`risk_decisions.checks` for that signal (see the report's section 3).

What these tests prove, and what they do not:

* they prove each refusal comes from `entry_zone` alone, and that its cause is the quoted
  spread (18.424 USD) measured against a band half-width of 0.1 x ATR (18.306 USD on #15/#16):
  not a market move, and not the EUR/USD position sizing;
* they do not decide whether the band or the spread guard should change. That is an operator
  decision, which is why `src/tradingagent/risk/` is left untouched.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from decimal import Decimal as D

import pytest
from tests.risk.builders import LOGIN, RISK

from tradingagent.core.halt import TRADING
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import RiskOutcome
from tradingagent.data.market_calendar import SlotStatus
from tradingagent.risk.checks import RiskContext
from tradingagent.risk.engine import decide
from tradingagent.risk.model import (
    AccountState,
    InstrumentSpec,
    MarketQuote,
    PortfolioState,
    TradeIntent,
    limits_for,
)

# The demo account on 2026-10-09 04:00 UTC: `account_snapshots` and the stored checks agree.
EQUITY = D("5496.67")  # 0.5 % in play = 27.48 EUR, 2 % daily = 109.93 EUR in the stored reasons
PEAK = D("5496.96")  # the stored drawdown reason says "loss 0.29 EUR, limit 549.70 EUR"
USD_TO_EUR = D("0.891")  # BTCUSD profit currency to the account currency, read from the terminal
BTCUSD = InstrumentSpec("BTCUSD", D(1), D("0.01"), D("0.01"), D(5), D("0.001"), 20000)
SPREAD = D("18.424")  # `quote.ask - quote.bid`, printed by all five BTCUSD decisions


@dataclass(frozen=True)
class Stored:
    """One decision exactly as production recorded it: signal levels plus quote fields."""

    signal_id: int
    mode: TradingMode
    at: str
    band_low: str
    band_high: str
    stop_loss: str
    ask: str
    margin_one_lot: str
    reason: str  # the stored `risk_decisions.reason`, "" when the decision was authorized

    def context(self, *, bid: str | None = None, spread: Decimal = SPREAD) -> RiskContext:
        """The snapshot `decide` saw. Pass `bid` to hold the traded price and vary the spread."""
        ask_price = D(self.ask) if bid is None else D(bid) + spread
        bid_price = ask_price - spread if bid is None else D(bid)
        distance = abs(ask_price - D(self.stop_loss))
        # The broker's own order_calc_profit agreed with this within CROSS_CHECK_TOLERANCE that
        # night (the sizing check passed), so the independent estimate stands in for it here.
        loss_one_lot = BTCUSD.contract_size * distance * USD_TO_EUR
        return RiskContext(
            intent=TradeIntent(
                self.signal_id,
                "BTCUSD",
                Direction.BUY,
                D(self.band_low),
                D(self.band_high),
                D(self.stop_loss),
            ),
            account=AccountState(LOGIN, True, "EUR", EQUITY, EQUITY, EQUITY),
            spec=BTCUSD,
            quote=MarketQuote(
                bid_price, ask_price, loss_one_lot, D(self.margin_one_lot), USD_TO_EUR
            ),
            portfolio=PortfolioState(
                open_positions=(),
                trades_today=0,
                day_start_equity=EQUITY,
                day_pnl=D(0),
                week_start_equity=EQUITY,
                week_pnl=D(0),
                equity_peak=PEAK,
                consecutive_losses=0,
                last_loss_at=None,
            ),
            market=SlotStatus.OPEN,
            limits=limits_for(self.mode, RISK),
            now=datetime.fromisoformat(self.at),
            halt=TRADING,
        )

    @property
    def half_width(self) -> Decimal:
        return (D(self.band_high) - D(self.band_low)) / 2


# `signals.entry_low`/`entry_high`/`stop_loss` and the quote fields printed in
# `risk_decisions.checks` / `risk_decisions.reason`, verbatim.
REFUSED = (
    Stored(
        signal_id=13,
        mode=TradingMode.SIGNAL,
        at="2026-10-09T04:00:10.296382+00:00",
        band_low="82365.4312758585",
        band_high="82403.1287241415",
        stop_loss="82007.3055171698",
        ask="82404.176",
        margin_one_lot="36701.0",
        reason="entry_zone: price 82404.176 outside [82365.4312758585, 82403.1287241415]",
    ),
    Stored(
        signal_id=14,
        mode=TradingMode.DEMO,
        at="2026-10-09T04:00:13.006055+00:00",
        band_low="82365.4312758585",
        band_high="82403.1287241415",
        stop_loss="82007.3055171698",
        ask="82404.19",
        margin_one_lot="36701.0",
        reason="entry_zone: price 82404.19 outside [82365.4312758585, 82403.1287241415]",
    ),
    Stored(
        signal_id=15,
        mode=TradingMode.SIGNAL,
        at="2026-10-09T04:15:08.867533+00:00",
        band_low="82454.1918490115",
        band_high="82490.8041509886",
        stop_loss="82106.3749802291",
        ask="82491.162",
        margin_one_lot="36743.0",
        reason="entry_zone: price 82491.162 outside [82454.1918490115, 82490.8041509886]",
    ),
    Stored(
        signal_id=16,
        mode=TradingMode.DEMO,
        at="2026-10-09T04:15:12.370823+00:00",
        band_low="82454.1918490115",
        band_high="82490.8041509886",
        stop_loss="82106.3749802291",
        ask="82491.164",
        margin_one_lot="36743.0",
        reason="entry_zone: price 82491.164 outside [82454.1918490115, 82490.8041509886]",
    ),
)

# Signal #12, the same bar as #13 but in DEMO from the manifest capped at DEMO: authorized,
# 0.07 lot, and then lost to the terminal (see the report's section 4).
AUTHORIZED = Stored(
    signal_id=12,
    mode=TradingMode.DEMO,
    at="2026-10-09T03:45:14.407657+00:00",
    band_low="82339.9475893861",
    band_high="82378.8984106139",
    stop_loss="81969.9147877213",
    ask="82373.657",
    margin_one_lot="36695.98",
    reason="0.07 lot, 25.18 EUR at risk",
)

REFUSED_IDS = [f"signal {stored.signal_id} at {stored.at[11:19]} UTC" for stored in REFUSED]


@pytest.mark.parametrize("stored", REFUSED, ids=REFUSED_IDS)
def test_each_stored_refusal_is_the_entry_zone_and_nothing_else(stored: Stored) -> None:
    decision = decide(stored.context(), LOGIN)
    assert decision.outcome is RiskOutcome.REFUSED
    assert [check.name for check in decision.refusals] == ["entry_zone"]
    assert decision.reason == stored.reason


@pytest.mark.parametrize("stored", REFUSED, ids=REFUSED_IDS)
def test_the_other_seventeen_controls_passed_on_those_signals(stored: Stored) -> None:
    """No limit, no halt, no cooldown, no margin shortage: only the entry price was refused."""
    record = decide(stored.context(), LOGIN).checks_record()
    assert len(record) == 18
    assert all(entry["passed"] for name, entry in record.items() if name != "entry_zone")
    assert record["sizing"]["passed"], record["sizing"]["reason"]


@pytest.mark.parametrize("stored", REFUSED, ids=REFUSED_IDS)
def test_the_refusal_reason_quotes_the_ask_not_the_bid(stored: Stored) -> None:
    """RM-012 measures the price that would be paid: bid + spread, outside the band on all
    four, while the bid itself (ask - 18.424) stayed at or inside the band's upper bound."""
    context = stored.context()
    assert context.entry_price == D(stored.ask)
    assert "price " + str(context.entry_price) in stored.reason
    assert context.quote.bid <= D(stored.band_high)


def test_the_authorized_twin_is_sized_at_007_lot_so_sizing_is_not_the_cause() -> None:
    """Signal #12 proves the EUR/USD conversion works: 0.07 lot, 25.18 EUR at risk, 0.46 %."""
    decision = decide(AUTHORIZED.context(), LOGIN)
    assert decision.outcome is RiskOutcome.AUTHORIZED
    assert decision.sizing is not None
    assert decision.sizing.volume == D("0.07")  # stored: volume = 0.070000000000
    assert decision.sizing.risk_eur == pytest.approx(D("25.1804"), abs=D("0.01"))
    assert decision.reason == AUTHORIZED.reason  # "0.07 lot, 25.18 EUR at risk", verbatim
    record = decision.checks_record()
    assert record["sizing"]["reason"] == "0.07 lot, limited by risk"
    assert record["account_currency"]["passed"]
    assert record["margin"]["reason"] == "minimum lot needs 366.96 EUR, 2748.34 usable"


def test_at_the_signal_price_itself_the_buy_is_already_out_of_its_own_band() -> None:
    """The decisive measurement: with the bid *exactly* at the close the signal was built on,
    the ask is bid + 18.424 = 82490.922, above entry_high 82490.8041509886. The market did not
    have to move at all for #15/#16 to be refused: the spread alone covers the whole band."""
    stored = REFUSED[3]
    context = stored.context(bid="82472.498")  # `signals.observed_price` of #15 and #16
    assert context.quote.bid == D("82472.498")
    assert context.entry_price == D("82490.922")
    decision = decide(context, LOGIN)
    assert decision.outcome is RiskOutcome.REFUSED
    assert [check.name for check in decision.refusals] == ["entry_zone"]
    assert "outside" in decision.reason


def test_the_narrow_spread_of_the_same_day_lets_the_very_same_order_through() -> None:
    """Only the spread changes: the bid stays where it was quoted, 2.424 instead of 18.424.
    The band is no longer the binding constraint, which names the spread as the cause rather
    than the 18.666 EUR the market had drifted between the close and the decision."""
    stored = REFUSED[3]
    context = stored.context(bid="82472.740", spread=D("2.424"))
    decision = decide(context, LOGIN)
    assert decision.checks_record()["entry_zone"]["passed"]
    assert decision.outcome is not RiskOutcome.REFUSED


@pytest.mark.parametrize(
    ("stored", "headroom"),
    [(REFUSED[0], D("0.4247241415")), (REFUSED[2], D("-0.11784901145"))],
    ids=["04:00 band barely wider than the spread", "04:15 band narrower than the spread"],
)
def test_the_headroom_left_to_the_ask_is_the_band_half_width_minus_the_spread(
    stored: Stored, headroom: Decimal
) -> None:
    """`half_width - spread`: the room a BUY really has before the gate closes. Negative on
    #15/#16, where the whole band is smaller than the spread the broker was quoting. Recorded
    constants, so widening the band upstream does not invalidate this incident measurement."""
    assert stored.half_width - SPREAD == headroom


def test_the_band_comes_from_the_strategy_and_its_half_width_is_a_tenth_of_the_atr() -> None:
    """`entry_zone_atr: 0.1` on the ATR14 stored in `signals.indicators` for #15 and #16."""
    atr = D("183.06150988546344")
    stored = REFUSED[2]
    assert stored.half_width == pytest.approx(atr / 10, abs=D("0.0000000001"))
    assert stored.half_width < SPREAD  # 18.306 < 18.424: nothing left to pay the spread


def test_the_spread_guard_still_called_that_spread_acceptable() -> None:
    """RM-012 carries two scales: `max_spread_stop_pct: 10` of a 384.5 EUR stop allows 38.45 EUR
    of spread, twice the entry band. The wide number passed while the narrow one refused."""
    stored = REFUSED[2]
    context = stored.context()
    record = decide(context, LOGIN).checks_record()
    assert record["spread"]["passed"]
    assert record["spread"]["reason"] == "spread 18.424, limit 38.47870197709"
    assert context.limits.max_spread_to_stop == D("0.1")


def test_the_stored_reasons_are_still_the_ones_this_code_produces() -> None:
    """A guard on the replay itself: the four stored reasons are the strings the code produces
    now, so a later edit that changes the message is noticed here rather than in production."""
    played = [decide(stored.context(), LOGIN).reason for stored in REFUSED]
    assert played == [stored.reason for stored in REFUSED]
