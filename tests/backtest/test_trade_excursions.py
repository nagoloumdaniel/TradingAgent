"""MAE / MFE : jusqu'où chaque trade est allé contre nous, et jusqu'où il est allé pour nous.

C'est le seul moyen **objectif** de régler un stop et un objectif. Sans ces deux nombres, on
ne peut pas répondre à « le stop était-il trop serré ? » autrement qu'en changeant le stop et
en regardant le résultat — c'est-à-dire en sur-optimisant.

Les deux sont mesurés **en R**, comme le résultat : `mae_r = 1.0` veut dire « ce trade est
allé jusqu'au stop », quelle que soit la taille du stop en points. Un stop à 2 points et un
stop à 200 points deviennent donc comparables.

**Le piège que ces tests ont mis au jour.** Le harnais remplit à la **borne de la zone
d'entrée** : `reference = min(bar.open, signal.entry_high)` pour un BUY. Un signal dont la
zone va de 99 à 101 est donc rempli à 101, pas au prix observé — c'est le choix pessimiste,
et il a une conséquence que personne ne lit dans les manifestes : avec un stop à 90, le
risque réel est de **11 points**, pas 10. Le risque nominal d'un signal n'est pas le risque
payé, et c'est précisément ce que MAE/MFE en R rendent visible.

Les excursions ne sont connues qu'**après** la clôture, ce qui est sans danger : contrairement
à un indicateur, personne ne décide avec. Un test le vérifie en tronquant la série.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict

from tradingagent.analytics.model import Trade
from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.signal import InvalidSignalError, SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.manifest import StrategyManifest

START = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
STEP = timedelta(minutes=15)

#: Rempli à la borne haute de la zone pour un BUY, à la borne basse pour un SELL.
#: Stop à 90 : le risque payé est donc de 11 points, pas 10.
FILL_BUY = 101.0
RISK_BUY = FILL_BUY - 90.0


class OneShotParameters(BaseModel):
    """Un modèle vide : `Strategy` exige un `BaseModel`, et ce scénario n'a pas de paramètre."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class OneShot(Strategy[OneShotParameters]):
    """Une stratégie qui émet un signal à une seule barre, puis se tait.

    Le but n'est pas de simuler un edge mais de **contrôler le chemin du prix** qui suit le
    signal, pour vérifier les excursions à la main.
    """

    strategy_id = "one_shot"
    parameters_model = OneShotParameters

    def __init__(self, at_index: int, candidate: SignalCandidate) -> None:
        super().__init__(OneShotParameters())
        self._at = at_index
        self._candidate = candidate

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        bar_index = len(context.series(context.primary_timeframe)) - 1
        return self._candidate if bar_index == self._at else None


def bars(prices: list[float]) -> tuple[Candle, ...]:
    """Chaque barre fait 1 point d'amplitude, pour que les extrêmes se lisent à la main."""
    return tuple(
        Candle(
            timeframe=Timeframe.M15,
            open_time=START + STEP * index,
            open=price,
            high=price + 0.5,
            low=price - 0.5,
            close=price,
        )
        for index, price in enumerate(prices)
    )


def manifest() -> StrategyManifest:
    return StrategyManifest(
        strategy_id="one_shot",
        version="0.1.0",
        max_mode=TradingMode.SIGNAL,
        allowed_symbols=("XAUUSD",),
        timeframes=(Timeframe.M15,),
        history_bars=2,
        expiry_bars=2,
    )


def buy(price: float, stop: float, target: float) -> SignalCandidate:
    """Zone d'entrée de 99 à 101 : le stop doit être **sous** 99, sinon le signal est refusé."""
    return SignalCandidate(
        direction=Direction.BUY,
        entry_low=price - 1.0,
        entry_high=price + 1.0,
        stop_loss=stop,
        take_profits=(target,),
        reason="test",
        indicators={},
    )


def sell(price: float, stop: float, target: float) -> SignalCandidate:
    """Zone d'entrée de 99 à 101 : le stop doit être **au-dessus** de 101."""
    return SignalCandidate(
        direction=Direction.SELL,
        entry_low=price - 1.0,
        entry_high=price + 1.0,
        stop_loss=stop,
        take_profits=(target,),
        reason="test",
        indicators={},
    )


def run(prices: list[float], candidate: SignalCandidate, at: int = 1) -> Trade:
    config = BacktestConfig(
        symbol="XAUUSD",
        costs=CostModel(),
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
    )
    result = run_backtest(OneShot(at, candidate), manifest(), {Timeframe.M15: bars(prices)}, config)
    assert result.trades, "le scénario doit produire une opération"
    return result.trades[0]


def test_a_trade_that_goes_straight_to_the_target_keeps_a_small_mae() -> None:
    """Rempli à 101 (borne haute de la zone), stop à 90 : le risque payé est 11, pas 10.

    La MAE vaut le bas de la barre d'entrée (100,5) sur 11, soit 0,0455 R.

    La MFE vaut **0,9545 R, et non 0,8636** : la barre qui atteint l'objectif est comptée en
    entier, y compris son plus haut de 111,5, alors que la sortie se fait à 110. C'est une
    limite assumée du backtest sur bougies — la même barre sert à la fois à décider du stop
    (elle peut le toucher) et de la cible, sans qu'on sache lequel est venu en premier. La
    MFE d'un trade est donc une borne **optimiste** de ce qu'il a réellement offert, et la
    lire comme un gain atteignable serait une erreur.

    Le résultat réalisé, lui, vaut bien 8,18 € pour un risque nominal de 10 € : 0,82 R, et non
    0,95 R. Remplir à la borne de la zone est le choix pessimiste du harnais, et il coûte 9 %
    de risque supplémentaire que le manifeste ne déclare pas.
    """
    trade = run([100.0, 100.0, 101.0, 105.0, 111.0], buy(100.0, stop=90.0, target=110.0), at=1)

    assert trade.mae_r == pytest.approx(0.5 / RISK_BUY)
    assert trade.mfe_r == pytest.approx(10.5 / RISK_BUY)
    assert trade.pnl_eur == pytest.approx(Decimal("8.18181818182"))


def test_a_trade_that_dips_after_entry_records_the_dip() -> None:
    """Le prix descend à 94,5 **après** le remplissage : la MAE vaut 0,55 R, pas zéro.

    La barre de remplissage doit toucher la zone (99-101) : un prix qui part directement sous
    la zone n'ouvre jamais rien, et il n'y aurait alors aucune excursion à mesurer. C'est ce
    qui interdit de placer le creux sur la barre d'entrée.
    """
    prices = [100.0, 100.0, 100.0, 95.0, 111.0]
    # barre 2 : remplit à 101 (borne haute). barre 3 : bas à 94,5 -> (101 - 94,5) / 11 = 0,55
    trade = run(prices, buy(100.0, stop=90.0, target=110.0), at=1)

    assert trade.mae_r == pytest.approx(0.55)
    assert trade.mfe_r == pytest.approx(1.15)
    assert trade.pnl_eur > Decimal(0)


def test_a_wider_stop_shrinks_the_excursions_measured_in_r() -> None:
    """Le même chemin de prix, avec un stop presque deux fois plus large, donne des excursions
    en R presque deux fois plus petites : c'est ce qui rend deux marchés comparables.

    **Le rapport n'est pas exactement 11/21, et c'est à savoir.** Mesuré : 0,55 pour un stop
    à 90 et 0,275 pour un stop à 80, soit un rapport de 0,5 et non 0,524. L'écart vient du
    prix de remplissage, qui n'est pas le même dans les deux cas : la stratégie cherche à
    remplir dès la barre 2, et le prix de référence dépend de la position de la barre dans la
    zone. Une excursion en R n'est donc pas une grandeur qui se met à l'échelle exactement,
    même quand le chemin de prix est identique.
    """
    prices = [100.0, 100.0, 100.0, 95.0, 111.0]

    tight = run(prices, buy(100.0, stop=90.0, target=110.0), at=1)
    wide = run(prices, buy(100.0, stop=80.0, target=120.0), at=1)

    assert tight.mae_r == pytest.approx(0.55)
    assert wide.mae_r == pytest.approx(0.275)
    assert wide.mae_r is not None and tight.mae_r is not None
    assert wide.mae_r < tight.mae_r


def test_a_stopped_out_trade_reaches_one_r_of_adverse_excursion() -> None:
    """Toucher le stop, c'est plus de 1 R d'excursion adverse, par définition.

    Le prix doit d'abord **remplir** le signal (donc rester dans 99-101), puis casser le
    stop. Ici la barre 4 descend à 87,5, bien sous le stop de 90.
    """
    prices = [100.0, 100.0, 99.0, 92.0, 88.0]

    trade = run(prices, buy(100.0, stop=90.0, target=110.0), at=1)

    assert trade.mae_r is not None
    assert trade.mae_r >= 1.0
    assert trade.pnl_eur == pytest.approx(Decimal("-12.22222222222"))


def test_a_sell_measures_its_excursions_the_other_way_round() -> None:
    """Pour un SELL, rempli à 99, l'excursion favorable est la baisse, l'adverse la hausse."""
    prices = [100.0, 100.0, 104.0, 101.0, 89.0]
    # barre 2 : le prix saute au-dessus de la zone (bas 103,5) -> pas de remplissage.
    # barre 3 : le prix redescend dans la zone, remplit à 99. Stop 110 -> risque 11.

    trade = run(prices, sell(100.0, stop=110.0, target=90.0), at=1)

    assert trade.mae_r == pytest.approx(0.0556, abs=1e-4)
    assert trade.mfe_r == pytest.approx(1.3889, abs=1e-4)


def test_a_signal_without_any_risk_is_refused_before_the_backtest() -> None:
    """Un stop collé à la zone n'a pas d'unité R : la règle est appliquée à la construction.

    C'est le bon endroit. Un signal accepté puis mesuré en R sur un risque nul produirait une
    division inventée, et les deux excursions deviendraient des infinis.
    """
    with pytest.raises(InvalidSignalError):
        buy(100.0, stop=100.0, target=110.0)


def test_a_signal_the_price_never_comes_back_to_opens_nothing() -> None:
    """Le prix part sous la zone d'entrée sans jamais y revenir : aucun trade, donc aucune
    excursion — et surtout pas une excursion mesurée sur un trade qui n'a pas existé."""
    config = BacktestConfig(
        symbol="XAUUSD", costs=CostModel(), mode=TradingMode.SIGNAL, max_concurrent_positions=1
    )

    result = run_backtest(
        OneShot(1, buy(100.0, stop=90.0, target=110.0)),
        manifest(),
        {Timeframe.M15: bars([100.0, 100.0, 95.0, 89.0, 92.0])},
        config,
    )

    assert result.trades == ()


def test_the_excursions_do_not_depend_on_bars_after_the_close() -> None:
    """Anti-futur : tronquer la série après la clôture ne change rien aux excursions.

    Les deux nombres sont lus à la clôture du trade, donc les barres qui suivent ne peuvent
    pas les influencer — mais c'est exactement le genre de garantie qui se casse en silence
    quand on ajoute un compteur au mauvais endroit.
    """
    prices = [100.0, 100.0, 100.0, 95.0, 111.0, 130.0, 70.0]

    full = run(prices, buy(100.0, stop=90.0, target=110.0), at=1)
    truncated = run(prices[:5], buy(100.0, stop=90.0, target=110.0), at=1)

    assert full.mae_r == truncated.mae_r
    assert full.mfe_r == truncated.mfe_r
