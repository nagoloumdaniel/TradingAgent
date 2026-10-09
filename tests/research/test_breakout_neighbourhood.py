"""Le voisinage du seul candidat qui n'échoue qu'à une porte — et sa taille annoncée.

La taille de la grille n'est pas un détail d'implémentation : c'est le **diviseur de la
correction de Bonferroni**. Une grille qui grossit en silence rend la correction plus sévère
sans que personne ne le voie, et une grille annoncée trop petite la rend complaisante. Ces
tests fixent donc les deux chiffres.
"""

from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.discovery import (
    FAMILIES,
    TemplateScope,
    breakout_only_neighbourhood_template,
)


def scope() -> TemplateScope:
    return TemplateScope(symbols=("BTCUSD",), timeframe=Timeframe.M15)


def proposals() -> list:
    return list(breakout_only_neighbourhood_template(scope(), {}))


def test_the_family_is_registered_and_discoverable() -> None:
    registered = [template for template in FAMILIES if template.family == "breakout_neighbourhood"]

    assert len(registered) == 1
    assert registered[0].description.strip()


def test_the_grid_is_exactly_twenty_seven_combinations() -> None:
    """3 vitesses rapides, 3 lentes et 3 objectifs : 27 combinaisons, et le filtre
    `lente > rapide` n'en retire aucune. C'est ce nombre qui entre dans Bonferroni."""
    result = proposals()

    assert len(result) == 27
    parameters = [tuple(sorted(item.parameters.items())) for item in result]
    assert len(set(parameters)) == 27, "aucune combinaison ne doit être proposée deux fois"


def test_the_grid_is_never_wider_than_announced() -> None:
    """Le plafond est vérifié : une grille élargie par mégarde ferait échouer ce test."""
    result = proposals()

    assert len(result) <= 27
    assert {item.parameters["ema_fast"] for item in result} == {15, 20, 25}
    assert {item.parameters["ema_slow"] for item in result} == {50, 60, 70}
    assert {item.parameters["take_profit_rr"] for item in result} == {1.3, 1.5, 1.7}


def test_the_slow_average_is_always_slower_than_the_fast_one() -> None:
    """Un croisement inversé n'a pas de sens : le gabarit doit le refuser."""
    for item in proposals():
        assert item.parameters["ema_slow"] > item.parameters["ema_fast"]


def test_every_proposal_declares_the_breakout_strategy_and_a_signal_ceiling() -> None:
    """Aucun candidat de recherche ne doit pouvoir prétendre à un plafond supérieur."""
    for item in proposals():
        assert item.strategy_id == "breakout_only"
        assert item.manifest.max_mode is TradingMode.SIGNAL
        assert item.manifest.timeframes == (Timeframe.M15,)
        assert item.manifest.allowed_symbols == ("BTCUSD",)


def test_the_history_covers_the_slow_average_and_the_atr() -> None:
    """Trop peu d'historique, et l'indicateur récursif n'a pas fini sa chauffe : le candidat
    ouvrirait alors des trades sur des valeurs non définies."""
    for item in proposals():
        needed = max(item.parameters["ema_slow"], item.parameters["atr_period"])
        assert item.manifest.history_bars > needed


def test_the_family_does_not_touch_the_production_registry() -> None:
    """Un gabarit de recherche n'enregistre rien : le registre de production est intact."""
    from tradingagent.strategies.registry import REGISTRY

    assert "breakout_neighbourhood" not in REGISTRY
    assert "breakout_only" not in REGISTRY
