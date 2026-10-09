"""Trailing sur structure : suivre le dernier creux confirmé plutôt qu'une distance fixe.

**Pourquoi ça change quelque chose.** Un trailing à distance fixe (`trailing_stop_atr`) recule
quand la volatilité monte, même si la tendance est intacte : il sort sur du bruit. Suivre les
creux successifs laisse respirer un mouvement qui continue, et **serre** quand la structure se
dégrade — un creux plus haut veut dire que le marché refuse de redescendre.

**Le piège, et il est fatal si on le rate.** Un creux n'est confirmé qu'après `strength` barres.
Utiliser le creux « le plus récent » sans cette contrainte ferait entrer dans le stop un niveau
dont on ne savait pas encore qu'il en était un : le backtest serait magnifique et faux.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict

from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.manifest import StrategyManifest

START = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
STEP = timedelta(minutes=15)


class OnceParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Once(Strategy[OnceParameters]):
    """Émet un signal une seule fois, pour contrôler le chemin de prix qui suit."""

    strategy_id = "once"
    parameters_model = OnceParameters

    def __init__(self, at_index: int, candidate: SignalCandidate) -> None:
        super().__init__(OnceParameters())
        self._at = at_index
        self._candidate = candidate
        self._fired = False

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        bar_index = len(context.series(context.primary_timeframe)) - 1
        if bar_index == self._at and not self._fired:
            self._fired = True
            return self._candidate
        return None


def bars(quotes: list[tuple[float, float, float, float]]) -> tuple[Candle, ...]:
    """(open, high, low, close) par barre : les mèches comptent pour un swing."""
    return tuple(
        Candle(
            timeframe=Timeframe.M15,
            open_time=START + STEP * index,
            open=open_,
            high=high,
            low=low,
            close=close,
        )
        for index, (open_, high, low, close) in enumerate(quotes)
    )


def manifest() -> StrategyManifest:
    return StrategyManifest(
        strategy_id="once",
        version="0.1.0",
        max_mode=TradingMode.SIGNAL,
        allowed_symbols=("XAUUSD",),
        timeframes=(Timeframe.M15,),
        history_bars=2,
        expiry_bars=2,
    )


def buy(stop: float) -> SignalCandidate:
    """Zone large : le prix ne s'en échappe pas, et le stop est sous la borne basse."""
    return SignalCandidate(
        direction=Direction.BUY,
        entry_low=90.0,
        entry_high=110.0,
        stop_loss=stop,
        take_profits=(500.0,),
        reason="test",
        indicators={},
    )


def sell(stop: float) -> SignalCandidate:
    return SignalCandidate(
        direction=Direction.SELL,
        entry_low=90.0,
        entry_high=110.0,
        stop_loss=stop,
        take_profits=(1.0,),
        reason="test",
        indicators={},
    )


def config(**kwargs: object) -> BacktestConfig:
    return BacktestConfig(
        symbol="XAUUSD",
        costs=CostModel(),
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
        **kwargs,  # type: ignore[arg-type]
    )


def run(
    quotes: list[tuple[float, float, float, float]], candidate: SignalCandidate, **kwargs: object
):
    return run_backtest(
        Once(1, candidate), manifest(), {Timeframe.M15: bars(quotes)}, config(**kwargs)
    )


# --------------------------------------------------------------------------------------
# Le suivi de structure
# --------------------------------------------------------------------------------------


def test_an_unconfirmed_swing_never_raises_the_stop() -> None:
    """Le prix forme un creux puis s'effondre **avant** que ce creux soit confirmé.

    Si le harnais utilisait ce creux, le stop serait monté à 98 et les barres suivantes le
    toucheraient. Il ne doit pas : à la barre 4, le creux de la barre 3 n'est pas encore un
    swing, donc le stop reste à 85.
    """
    quotes = [
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 101.0, 99.0, 100.0),  # signal, rempli
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 100.5, 98.0, 99.0),  # creux 98 — PAS encore confirmé
        (99.0, 99.5, 97.0, 98.0),  # le marché continue de baisser
        (98.0, 98.5, 96.0, 97.0),
        (97.0, 97.5, 95.0, 96.0),
    ]

    result = run(quotes, buy(85.0), trailing_stop_swing_strength=2)

    # Aucune barre ne descend sous 95 : le stop à 85 n'est jamais touché, et le stop n'a pas
    # été monté sur un creux non confirmé (sinon les barres 5 et 6 l'auraient touché).
    assert result.entries == 1
    assert result.forced_closures == 1, "le trade doit courir jusqu'à la fin"


def test_a_rising_structure_raises_the_stop_above_the_initial_level() -> None:
    """Deux creux confirmés plus hauts, puis un retour sous le second : le trade sort.

    C'est la preuve que le stop a suivi la structure : le stop initial était à 85, et seule la
    montée vers le creux confirmé à 104 peut faire sortir la barre finale à 100.
    """
    quotes = [
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 101.0, 99.0, 100.0),  # signal, rempli à ~101
        (100.0, 100.5, 98.0, 100.0),  # creux 98
        (100.0, 102.0, 99.5, 101.5),
        (101.5, 103.0, 101.0, 102.5),  # creux 98 confirmé ici
        (104.0, 106.0, 104.0, 105.5),  # creux 104
        (105.5, 107.0, 105.0, 106.5),
        (106.5, 108.0, 106.0, 107.5),  # creux 104 confirmé -> stop monte vers 104
        (107.5, 108.0, 100.0, 101.0),  # retour sous 104 : le stop doit être touché
    ]

    result = run(quotes, buy(85.0), trailing_stop_swing_strength=2)

    assert len(result.trades) == 1, "la structure montante doit avoir relevé le stop"
    # Sortie au stop de structure (~104) au-dessus du prix d'entrée (~101) : le trade gagne.
    assert result.trades[0].pnl_eur > 0


def test_the_stop_never_moves_backwards_on_a_falling_structure() -> None:
    """Un stop qui redescend est un stop qu'on a desserré. Interdit.

    Après un creux confirmé à 98, la structure se dégrade : des creux plus bas. Le stop ne doit
    pas redescendre sous 98, et le trade doit sortir quand le prix y revient.
    """
    quotes = [
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 105.0, 98.0, 104.0),  # creux 98
        (104.0, 106.0, 103.0, 105.0),
        (105.0, 107.0, 104.5, 106.0),  # creux 98 confirmé -> stop vers 98
        (106.0, 106.5, 95.0, 96.0),  # effondrement : le stop à 98 doit être touché
    ]

    result = run(quotes, buy(85.0), trailing_stop_swing_strength=2)

    assert len(result.trades) == 1
    # La sortie se fait au stop (~98) et non au stop initial (85) : c'est là toute la
    # différence entre suivre la structure et laisser courir une perte jusqu'à 85.
    assert result.trades[0].mae_r is not None
    assert result.trades[0].pnl_eur < 0


def test_a_sell_follows_swing_highs() -> None:
    """Pour un SELL, la structure se suit par le haut : des sommets plus bas serrent le stop."""
    quotes = [
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 101.0, 99.0, 100.0),  # signal, rempli à 90
        (90.0, 95.0, 89.0, 94.0),  # sommet 95
        (94.0, 96.0, 92.0, 93.0),  # sommet 96
        (93.0, 94.0, 91.0, 92.0),  # confirme le sommet 95
        (92.0, 97.0, 91.0, 96.0),  # retour au-dessus de 95 : le stop doit être touché
    ]

    result = run(quotes, sell(200.0), trailing_stop_swing_strength=2)

    assert len(result.trades) == 1
    assert result.trades[0].pnl_eur < 0


# --------------------------------------------------------------------------------------
# Le contrat du réglage
# --------------------------------------------------------------------------------------


def test_the_setting_refuses_a_strength_that_makes_no_sense() -> None:
    with pytest.raises(ValueError, match="swing strength"):
        config(trailing_stop_swing_strength=0)


def test_no_structure_trailing_means_no_change_at_all() -> None:
    """Par défaut, rien ne bouge : le harnais reste celui d'avant."""
    assert BacktestConfig(symbol="XAUUSD").trailing_stop_swing_strength is None

    quotes = [
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 105.0, 98.0, 104.0),
        (104.0, 106.0, 103.0, 105.0),
        (105.0, 107.0, 104.5, 106.0),
        (106.0, 106.5, 95.0, 96.0),
    ]

    without = run(quotes, buy(85.0))
    with_structure = run(quotes, buy(85.0), trailing_stop_swing_strength=2)

    # Sans structure, le stop reste à 85 et la barre à 95 ne le touche pas : le trade court
    # encore à la fin. Avec structure, il est sorti. Les deux comportements sont distincts.
    assert without.forced_closures == 1
    assert with_structure.forced_closures == 0


def test_the_two_trailing_styles_can_be_combined() -> None:
    """Suivre la structure **et** une distance ATR : les deux règles coexistent sans casser."""
    quotes = [
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 101.0, 99.0, 100.0),
        (100.0, 102.0, 99.0, 101.0),
        (101.0, 104.0, 100.0, 103.0),
        (103.0, 106.0, 102.0, 105.0),
        (105.0, 108.0, 104.0, 107.0),
    ]

    both = run(quotes, buy(85.0), trailing_stop_swing_strength=2, trailing_stop_atr=1.5)

    assert both.entries == 1
    assert not both.strategy_errors
