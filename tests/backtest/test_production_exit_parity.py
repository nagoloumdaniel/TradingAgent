"""Le backtest doit sortir où la production sort, sinon il ne mesure pas la stratégie exécutée.

**Le défaut que ce fichier verrouille.** Une stratégie peut publier deux objectifs (TP1 puis
TP2). La production n'en envoie **qu'un** au courtier : `runtime/pipeline.py` passe
`detail.take_profits[0]`, et rien dans `execution/` ne sait clôturer une fraction de position.
Le backtest, lui, gardait les deux niveaux et — sans partiels configurés — sortait au
**dernier**. Mesuré le 2026-10-10 : sur une série où le prix franchit TP1 et TP2, le harnais
rendait +0,80 R pour une sortie que le code décrivait comme le dernier objectif.

Ce n'est pas un détail de simulation : c'est l'écart entre la règle mesurée et la règle
exécutée, et c'est exactement ce que la parité backtest/production existe pour empêcher. Un
TP1 à 0,8 R et un TP2 à 1,5 R ne donnent pas le même résultat, donc les deux lectures ne
mesurent pas la même stratégie.

`BacktestConfig.targets_at_first_only` nomme la fidélité : le harnais ne garde alors que le
premier objectif, comme le fait `pipeline._execute`. Le défaut reste `False` pour ne rien
changer aux mesures publiées, et la campagne de production des stratégies multi-objectifs le
passe à `True`.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel

from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.manifest import StrategyManifest

T = Timeframe.M15
START = datetime(2026, 1, 1, tzinfo=UTC)
#: Entree 100, stop 90 (risque 10 points = 1 R), TP1 a 0,8 R, TP2 a 1,5 R.
ENTRY, STOP, TP1, TP2 = 100.0, 90.0, 108.0, 115.0


class _NoParameters(BaseModel):
    """A strategy needs a pydantic model, even an empty one: that is the harness contract."""


class _EmitOnce(Strategy[_NoParameters]):
    """One signal and no more: the position is then managed to its exit, whatever it is."""

    strategy_id = "two_targets"
    parameters_model = _NoParameters

    def __init__(self, parameters: _NoParameters) -> None:
        super().__init__(parameters)
        self._sent = False

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        if self._sent:
            return None
        self._sent = True
        return SignalCandidate(
            direction=Direction.BUY,
            entry_low=ENTRY,
            entry_high=ENTRY,
            stop_loss=STOP,
            take_profits=(TP1, TP2),
            reason="parity fixture",
            indicators={},
        )


MANIFEST = StrategyManifest(
    strategy_id="two_targets",
    version="1.0.0",
    max_mode=TradingMode.SIGNAL,
    allowed_symbols=["XAUUSD"],
    timeframes=[T],
    history_bars=20,
)


def _series(high: float) -> list[Candle]:
    """Twenty flat bars to warm up, then bars whose high reaches `high`."""
    warm = [
        Candle(T, START + timedelta(minutes=15 * index), ENTRY, ENTRY + 0.5, ENTRY - 0.5, ENTRY)
        for index in range(20)
    ]
    live = [
        Candle(T, START + timedelta(minutes=15 * (20 + index)), ENTRY, high, 99.0, high - 2.0)
        for index in range(30)
    ]
    return warm + live


def _realized_r(high: float, **overrides: object) -> float | None:
    config = BacktestConfig(symbol="XAUUSD", mode=TradingMode.SIGNAL, **overrides)  # type: ignore[arg-type]
    result = run_backtest(_EmitOnce(_NoParameters()), MANIFEST, {T: _series(high)}, config)
    if not result.trades:
        return None
    trade = result.trades[0]
    return float(trade.pnl_eur) / float(trade.risk_eur)


@pytest.mark.parametrize("high", [109.0, 120.0])
def test_the_first_target_only_exits_at_the_first_target(high: float) -> None:
    """What production does: one order, at `take_profits[0]`, whatever the price does after.

    Both scenarios must give the same result. That equality *is* the property: once the first
    objective is placed, nothing later in the bar changes what was sold.
    """
    realized = _realized_r(high, targets_at_first_only=True)

    assert realized == pytest.approx(0.8, abs=0.02), (
        "the position must close at the first objective, the only level "
        "`pipeline._execute` ever sends to the broker"
    )


def test_the_default_still_keeps_every_target() -> None:
    """The flag defaults to off: no already-published measurement changes meaning."""
    config = BacktestConfig(symbol="XAUUSD", mode=TradingMode.SIGNAL)

    assert config.targets_at_first_only is False


def test_without_the_flag_a_break_even_stop_ends_the_trade_at_the_first_target() -> None:
    """The old behaviour, pinned: this is what the flag exists to stop measuring.

    A break-even stop after the first objective retires the rest of the position on the next
    bar, so the last target is never reached. A two-target rule measured this way is priced on
    an exit that cannot happen, which is why `targets_at_first_only` is the honest setting.
    """
    with_partials = _realized_r(
        120.0,
        partial_exit_fractions=(0.5, 0.5),
        move_stop_to_breakeven_after_first_target=True,
    )

    # Half at TP1, then the break-even stop retires the rest: 0,4 R, and TP2 is never reached.
    assert with_partials == pytest.approx(0.4, abs=0.02)
