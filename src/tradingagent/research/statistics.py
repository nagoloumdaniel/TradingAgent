"""Le calcul des distributions dont un test de signification a besoin, et une lecon a ce sujet.

**Pourquoi ce module vit dans le paquet et pas dans un script.** Il a d'abord ete ecrit dans
`scripts/backtest/edge_location.py`, ou `mypy` ne va pas : sa configuration couvre `src`, `tests`
et `tools`. Un test qui l'importait de la etait donc **intestable**, et le premier jet de ce test
importait en fait une reimplementation locale — il passait sans rien prouver. Le calcul est ici,
couvert, et les scripts l'importent.

**Le defaut qui a motive le deplacement, et il vaut d'etre raconte.** Le balayage publiait :

    "p_value_one_sided": student_two_sided_p(statistic, count - 1) / 2.0

`student_two_sided_p(t)` vaut `P(|T| >= |t|)`, qui est **symetrique en signe**. Le diviser par
deux ne donne `P(T >= t)` que pour `t > 0` : pour un `t` negatif il rend `P(T <= t)`, c'est-a-dire
la probabilite que la statistique soit *plus petite*. Une case dont l'esperance etait **negative**
et le `t` fortement negatif recevait donc une p-value minuscule. Le premier passage a publie
**onze fausses decouvertes, toutes sur des reglages qui perdaient de l'argent**.

La lecon n'est pas « attention aux signes » : c'est qu'une fonction symetrique et une fonction de
queue se ressemblent assez pour qu'on les confonde, et que le seul remede est de nommer les deux
et de verifier qu'elles somment a un.
"""

import math

#: Au-dela de ce nombre de degres de liberte, la queue est lue par la **loi normale** plutot que
#: par la fonction beta incomplete : celle-ci perd ses chiffres significatifs bien avant
#: (mesure : 2,4e-5 d'ecart a 100 000 degres), et Student y est indistinguable de la limite.
#:
#: La frontiere est nommee parce qu'un seuil mal place changerait la lecture d'une case en
#: silence. `edge_location` la lit pour verifier que les deux branches se rejoignent.
ASYMPTOTIC_DEGREES = 3_000.0

_LANCZOS = (
    676.5203681218851,
    -1259.1392167224028,
    771.32342877765313,
    -176.61502916214059,
    12.507343278686905,
    -0.13857109526572012,
    9.9843695780195716e-6,
    1.5056327351493116e-7,
)


def log_gamma(value: float) -> float:
    """Logarithme de la fonction Gamma, par l'approximation de Lanczos (erreur relative ~1e-15)."""
    if value < 0.5:
        return math.log(math.pi / math.sin(math.pi * value)) - log_gamma(1.0 - value)
    shifted = value - 1.0
    series = 0.99999999999980993
    for index, coefficient in enumerate(_LANCZOS):
        series += coefficient / (shifted + index + 1.0)
    base = shifted + len(_LANCZOS) - 0.5
    return (
        0.5 * math.log(2.0 * math.pi) + (shifted + 0.5) * math.log(base) + math.log(series) - base
    )


def _beta_continued_fraction(a: float, b: float, x: float) -> float:
    """La fraction continue de Lentz pour la fonction beta incomplete (Numerical Recipes)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d
    for step in range(1, 300):
        twice = 2 * step
        coefficient = step * (b - step) * x / ((qam + twice) * (a + twice))
        d = 1.0 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c
        coefficient = -(a + step) * (qab + step) * x / ((a + twice) * (qap + twice))
        d = 1.0 + coefficient * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return result


def regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    """`I_x(a, b)`, la fonction beta incomplete regularisee, par fraction continue."""
    if not 0.0 <= x <= 1.0:
        raise ValueError(f"x must lie in [0, 1], got {x}")
    if x == 0.0:
        return 0.0
    if x == 1.0:
        return 1.0
    front = math.exp(
        log_gamma(a + b) - log_gamma(a) - log_gamma(b) + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_continued_fraction(a, b, x) / a
    return 1.0 - front * _beta_continued_fraction(b, a, 1.0 - x) / b


def student_two_sided_p(statistic: float, degrees: float) -> float:
    """`P(|T| >= |t|)`, une queue bilaterale — donc **symetrique en signe**.

    C'est cette symetrie qui interdit de la diviser par deux pour obtenir une p-value
    unilaterale : voir `student_one_sided_p`.
    """
    if degrees <= 0:
        raise ValueError(f"degrees must be positive, got {degrees}")
    if statistic == 0.0:
        return 1.0
    square = statistic * statistic
    return regularized_incomplete_beta(degrees / 2.0, 0.5, degrees / (degrees + square))


def student_one_sided_p(statistic: float, degrees: float) -> float:
    """`P(T >= t)` — la seule lecture unilaterale qui ait un sens, quel que soit le signe.

    `1 - P(|T| >= |t|) / 2` pour `t >= 0`, et son miroir pour `t < 0`, de sorte que
    `student_one_sided_p(t) + student_one_sided_p(-t) = 1`.

    **Le defaut qu'elle repare, trouve le 2026-10-11.** La formule publiee etait
    `student_two_sided_p(t) / 2`, qui vaut `P(T <= t)` pour un `t` positif et `P(T >= t)` pour un
    `t` negatif : une valeur symetrique en signe. Consequence exactement a l'envers — une case
    dont l'esperance etait negative et le `t` fortement negatif recevait une p-value minuscule, et
    le premier passage du balayage a publie **onze fausses decouvertes, toutes perdantes**.
    """
    two_sided = student_two_sided_p(statistic, degrees)
    if statistic >= 0.0:
        return two_sided / 2.0
    return 1.0 - two_sided / 2.0


def student_cdf(statistic: float, degrees: float) -> float:
    """`P(T <= t)`. Rend `student_two_sided_p(t) / 2` pour `t <= 0` et son complement au-dela."""
    two_sided = student_two_sided_p(statistic, degrees)
    if statistic <= 0.0:
        return two_sided / 2.0
    return 1.0 - two_sided / 2.0


def normal_quantile(probability: float) -> float:
    """Le quantile d'une loi normale centree reduite, par bissection de `erfc`."""
    if not 0.5 < probability < 1.0:
        raise ValueError(f"probability must lie in (0.5, 1), got {probability}")
    low, high = 0.0, 40.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if 0.5 * math.erfc(middle / math.sqrt(2.0)) > 1.0 - probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def student_quantile(probability: float, degrees: float) -> float:
    """Le quantile d'une loi de Student, par bissection.

    Sous 0,5 la distribution est symetrique, donc `t(p) = -t(1-p)` : c'est ce qui evite d'avoir a
    inverser la queue basse separement.
    """
    if not 0.0 < probability < 1.0:
        raise ValueError(f"probability must lie in (0, 1), got {probability}")
    if probability == 0.5:
        return 0.0
    if probability < 0.5:
        return -student_quantile(1.0 - probability, degrees)
    if degrees >= ASYMPTOTIC_DEGREES:
        # Garde-fou documente : au-dela, la fonction beta incomplete perd ses chiffres
        # significatifs et la bissection derive. La loi normale est la limite, a 1e-4 pres.
        return normal_quantile(probability)
    low, high = 0.0, 1.0
    while student_cdf(high, degrees) < probability:
        high *= 2.0
        if high > 1e6:  # pragma: no cover - a Student quantile never needs to go this far
            raise ValueError(f"cannot bracket the quantile for p={probability}, df={degrees}")
    for _ in range(200):
        middle = (low + high) / 2.0
        if student_cdf(middle, degrees) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


__all__ = [
    "ASYMPTOTIC_DEGREES",
    "log_gamma",
    "normal_quantile",
    "regularized_incomplete_beta",
    "student_cdf",
    "student_one_sided_p",
    "student_quantile",
    "student_two_sided_p",
]
