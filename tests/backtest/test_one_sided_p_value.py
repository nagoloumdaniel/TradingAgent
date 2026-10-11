"""La p-value unilaterale d'une case qui perd ne doit pas etre celle d'une case qui gagne.

**Le defaut, trouve le 2026-10-11 par un equipier qui travaillait sur autre chose.**
Le balayage publiait :

    "p_value_one_sided": student_two_sided_p(statistic, count - 1) / 2.0

Or `student_two_sided_p(t)` vaut `P(|T| >= |t|)`. Le diviser par deux ne donne pas `P(T >= t)` :
la fonction est **symetrique en signe**, alors qu'une p-value unilaterale ne l'est pas. Elle rend
`P(T <= t)` pour un `t` positif, et `P(T >= t)` pour un `t` negatif.

**La consequence, et elle est exactement a l'envers.** Pour une case dont l'esperance est
**negative** et le t fortement negatif, la formule rend une p-value minuscule. Le premier passage
du balayage a donc publie **onze fausses decouvertes, toutes sur des reglages qui perdaient de
l'argent**. Une case qui perd n'est pas une decouverte : c'est une case qui perd.

**Pourquoi ces tests importent du paquet et non du script.** Le calcul vivait dans
`scripts/backtest/edge_location.py`, ou `mypy` ne va pas — sa configuration couvre `src`, `tests`
et `tools`. Un premier jet de ce fichier importait donc de la, et **le hook de typage l'a refuse**,
a raison : un test qui depend d'un module non verifie n'est pas verifiable. Le calcul est
maintenant dans `tradingagent.research.statistics`, et ces tests portent sur la **fonction de
production** plutot que sur une reimplementation locale — un premier jet faisait exactement cela
et passait sans rien prouver.
"""

import math

import pytest

from tradingagent.research.statistics import (
    student_cdf,
    student_one_sided_p,
    student_two_sided_p,
)


def test_the_two_sided_helper_is_symmetric_in_sign() -> None:
    """It was always right; that symmetry is why it could not be halved."""
    assert student_two_sided_p(0.0, 10) == 1.0
    assert student_two_sided_p(2.228138852, 10) == pytest.approx(0.05, abs=1e-6)
    assert student_two_sided_p(-2.228138852, 10) == pytest.approx(0.05, abs=1e-6)


def test_a_losing_slice_is_not_published_as_a_discovery() -> None:
    """The defect itself: a strongly negative t must give a p-value near 1, not near 0."""
    assert student_one_sided_p(-10.0, 100) > 0.99
    assert student_one_sided_p(10.0, 100) < 0.01
    assert student_one_sided_p(0.0, 100) == pytest.approx(0.5)


def test_the_two_sides_sum_to_one() -> None:
    """A one-sided p-value and its mirror sum to one. The old formula gave twice that."""
    for statistic in (-5.0, -2.0, -0.5, 0.5, 2.0, 5.0):
        assert student_one_sided_p(statistic, 40) + student_one_sided_p(-statistic, 40) == (
            pytest.approx(1.0)
        )


def test_the_cdf_is_a_cdf() -> None:
    """`P(T <= t)` must run from 0 to 1, not from 0.5 to 0.5.

    The version committed on 2026-10-11 computed `1 - P(|T| >= |t|) / 2` unconditionally, which
    caps at 0.5: it was a survival function wearing the name of a CDF.
    """
    assert student_cdf(-10.0, 100) < 0.001
    assert student_cdf(0.0, 100) == pytest.approx(0.5)
    assert student_cdf(10.0, 100) > 0.999
    for statistic in (-3.0, -1.0, 0.0, 1.0, 3.0):
        assert student_cdf(statistic, 40) + student_cdf(-statistic, 40) == pytest.approx(1.0)


def test_the_quantile_still_matches_the_published_table() -> None:
    """The CDF change had to leave the quantiles alone: they were right."""
    from tradingagent.research.statistics import student_quantile

    assert student_quantile(0.975, 10) == pytest.approx(2.228138852, abs=1e-6)
    assert student_quantile(0.95, 29) == pytest.approx(1.699127027, abs=1e-6)
    assert student_quantile(0.975, 1) == pytest.approx(12.70620474, abs=1e-5)
    assert student_quantile(0.5, 10) == 0.0
    assert student_quantile(0.025, 10) == pytest.approx(-2.228138852, abs=1e-6)


def test_the_significance_reader_of_the_script_agrees() -> None:
    """End to end on what actually published the eleven false discoveries.

    Loaded by path: the script lives outside mypy's perimeter, so it is exercised as a script
    rather than imported as a package module.
    """
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "backtest" / "edge_location.py"
    spec = importlib.util.spec_from_file_location("edge_location_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["edge_location_probe"] = module
    spec.loader.exec_module(module)

    losing = module._significance([-1.0] * 40 + [-0.5] * 10)
    assert losing["t_statistic"] < 0
    assert losing["p_value_one_sided"] > 0.99, (
        "a slice whose expectancy is negative must never come back significant; this is the "
        "reading that published eleven false discoveries before the fix"
    )

    winning = module._significance([0.8] * 35 + [-1.0] * 5)
    assert winning["t_statistic"] > 0
    assert winning["p_value_one_sided"] < 0.01
    assert math.isclose(
        winning["p_value_one_sided"],
        student_two_sided_p(winning["t_statistic"], 39) / 2.0,
        rel_tol=1e-12,
    ), "for a positive statistic the two readings coincide: only the negative side changed"
