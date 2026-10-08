"""Le protocole walk-forward doit juger la série, pas son premier centième.

Le défaut corrigé ici n'était pas un bug de code : c'était un **réglage**. `max_folds=6` avec
un pas de 200 barres joue six plis, soit 1 200 barres, sur une série roulante de 48 000 :
**3 % du jeu**. Deux campagnes ont rendu « 0 retenu sur 34 » et « 0 sur 16 » sur cette base,
et la docstring du plan prévenait déjà que les plis 0..k d'une longue série sont sa tranche la
plus ancienne, pas un échantillon de la série.

Ces tests fixent la garantie qui manquait : quand le plan n'impose pas de plafond, les plis
couvrent la série, et le **dernier** pli valide le bloc le plus récent.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.protocol import WalkForwardPlan, walk_forward

START = datetime(2026, 1, 1, tzinfo=UTC)
STEP = timedelta(minutes=15)


def candles(count: int) -> tuple[Candle, ...]:
    return tuple(
        Candle(
            timeframe=Timeframe.M15,
            open_time=START + STEP * index,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
        )
        for index in range(count)
    )


def test_without_a_ceiling_the_folds_cover_the_series() -> None:
    """C'est la garantie centrale : la fenêtre jouée va jusqu'au bout de la série.

    Avec un pas de 500 et une fenêtre de 3 000, le dernier pli commence à 21 000 et finit à
    24 000 : toute la série est couverte.
    """
    series = candles(24000)
    plan = WalkForwardPlan(train_bars=2000, validation_bars=1000, step_bars=500)

    folds = walk_forward(series, plan)

    played = plan.step_bars * (len(folds) - 1) + plan.train_bars + plan.validation_bars
    assert played == len(series), f"{played} barres jouées sur {len(series)}"
    assert folds[-1].validation[-1] is series[-1]


def test_a_ceiling_stops_the_walk_on_the_oldest_folds() -> None:
    """Le plafond est opt-in, et il a un prix qu'il faut nommer : il joue les plis anciens."""
    series = candles(24000)
    capped_plan = WalkForwardPlan(350, 250, 200, max_folds=6)

    capped = walk_forward(series, capped_plan)
    uncapped = walk_forward(series, WalkForwardPlan(350, 250, 200))

    assert len(capped) == 6
    assert len(uncapped) > 100
    # Six plis au pas de 200 couvrent les indices 0..1449 : le dernier bloc validé s'arrête
    # avant la barre 1450, et la version sans plafond va jusqu'à la dernière barre.
    played = capped_plan.step_bars * (len(capped) - 1) + 350 + 250
    assert capped[-1].validation[-1] is series[played - 1]
    assert played / len(series) < 0.07
    assert uncapped[-1].validation[-1] is series[-1]


def test_the_recommended_default_covers_the_series_and_keeps_the_fold_count_low() -> None:
    """Le compromis retenu : fenêtres plus grandes, donc moins de plis et une large couverture.

    Réglé sur la mesure du 2026-10-09 : 2 000/1 000 avec un pas de 1 000 donne 46 plis et
    97,9 % de couverture, contre 238 plis et 99,7 % pour des fenêtres de 350/250 sans plafond
    — cinq fois plus de calcul pour le même verdict.
    """
    series = candles(48000)
    plan = WalkForwardPlan(train_bars=2000, validation_bars=1000, step_bars=1000)

    folds = walk_forward(series, plan)

    played = plan.step_bars * (len(folds) - 1) + plan.train_bars + plan.validation_bars
    assert played / len(series) > 0.97
    assert 20 <= len(folds) <= 60


def test_every_fold_validates_on_a_block_it_never_trained_on() -> None:
    """Un pli dont la validation recoupe l'apprentissage ne vaut rien : ordre strict exigé."""
    series = candles(12000)
    plan = WalkForwardPlan(train_bars=1000, validation_bars=500, step_bars=500)

    for fold in walk_forward(series, plan):
        assert fold.train[-1].open_time < fold.validation[0].open_time


def test_a_series_shorter_than_one_fold_yields_nothing() -> None:
    """Trop court pour un pli : aucun pli, et c'est un résultat, pas une erreur."""
    assert walk_forward(candles(100), WalkForwardPlan(350, 250, 200)) == []


def test_the_plan_refuses_sizes_that_make_no_sense() -> None:
    with pytest.raises(ValueError, match="positive"):
        WalkForwardPlan(train_bars=0, validation_bars=250, step_bars=200)
    with pytest.raises(ValueError, match="max_folds"):
        WalkForwardPlan(train_bars=350, validation_bars=250, step_bars=200, max_folds=0)
