"""La retention hors echantillon ne doit pas valoir 1.00 quand elle n'a pas ete calculee.

**Le piege que ce fichier verrouille, et je m'y suis pris.** Le 2026-10-10, `vwap_pullback@1.3.0`
est passe devant les neuf portes. La porte hors echantillon a refuse, et le rapport a affiche pour
elle :

    out_of_sample   failed   retention 1.00   threshold 0.5

`1.00` se lit comme « l'avantage est integralement conserve ». J'en ai conclu, et je l'ai ecrit
dans un premier rapport, que le candidat traversait le holdout intact. C'etait l'inverse : la
fenetre scellee donnait **PF 0,69 et -370 EUR**.

Le mecanisme, dans `protocol.out_of_sample_retention` :

    gross = float(in_sample.net_profit)
    if gross > 0:
        return float(out_of_sample.net_profit) / gross
    return 1.0 if out_of_sample.net_profit > 0 else 0.0

Quand la fenetre d'entree n'est pas positive, **aucun rapport n'est calcule** : la fonction ne
mesure rien et rend un drapeau. Le protocole lui-meme ne s'y trompe pas — la porte refuse sur le
net negatif, controle avant la retention — mais l'affichage, si, et l'affichage est ce que lit un
operateur.

Une valeur qui signifie « indetermine » ne doit pas etre un nombre. Elle doit etre `None`, et le
rapport doit ecrire `n/a`.
"""

from dataclasses import dataclass
from decimal import Decimal

from tradingagent.research.protocol import out_of_sample_retention


@dataclass(frozen=True)
class _OnlyNetProfit:
    """The single attribute the function reads. Anything else would be a fixture I do not need."""

    net_profit: Decimal


def _at(net: str) -> _OnlyNetProfit:
    return _OnlyNetProfit(net_profit=Decimal(net))


def test_a_negative_holdout_behind_a_negative_in_sample_is_not_a_ratio_of_one() -> None:
    """The exact shape of the trap: no ratio can be computed, so none is published."""
    retention = out_of_sample_retention(_at("-100"), _at("-370"))  # type: ignore[arg-type]

    assert retention is None, (
        "returning 1.0 here told a reader the edge survived, when the holdout lost 370 EUR"
    )


def test_a_positive_holdout_behind_a_losing_in_sample_is_not_zero() -> None:
    """0.0 was the other flag, and it reads as a measurement too. It is not one."""
    retention = out_of_sample_retention(_at("-100"), _at("50"))  # type: ignore[arg-type]

    assert retention is None, (
        "the ratio does not exist here either: two flags pretending to be numbers is the defect"
    )


def test_a_real_ratio_is_still_computed() -> None:
    """The guard must not swallow the normal case."""
    assert out_of_sample_retention(_at("100"), _at("60")) == 0.6  # type: ignore[arg-type]


def test_a_holdout_that_loses_behind_a_profitable_in_sample_is_negative() -> None:
    """A losing holdout must read as a loss, never as a flag."""
    retention = out_of_sample_retention(_at("100"), _at("-370"))  # type: ignore[arg-type]

    assert retention is not None
    assert retention < 0, "the ratio exists here, and it is negative: publish it"
