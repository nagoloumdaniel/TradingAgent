"""L'accord ou le refus d'une entrée, et rien d'autre.

L'opérateur a demandé de « trader uniquement lorsque le marché est volatile », et de tenir
compte des sessions. Une règle de stratégie qui répond à cette demande a **une** forme
acceptable dans ce dépôt : un filtre qui refuse des entrées. Les trois propriétés testées ici
sont celles qui décident si ce filtre est un outil ou un incident :

* il ne peut que **passer** ou **refuser** — il ne fabrique jamais de signal, ne touche ni la
  taille ni le stop (C-002) : sa sortie n'a aucun champ pour en porter un ;
* il est **fail-open** : quand la mesure manque (pas assez de barres pour l'ATR), il laisse
  passer. Bloquer sur « je ne sais pas » éteindrait l'agent en silence, ce que TASK-069 a
  précisément documenté comme le risque à éviter ;
* il est **inerte quand rien n'est configuré** : pas de clé de filtre dans le manifeste, pas de
  changement de comportement. C'est ce qui rend son activation une décision, pas un effet de
  bord.
"""

import dataclasses
import math
from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.indicators.entry_filter import (
    EntryFilter,
    EntryVerdict,
    FilterReason,
    SessionFilter,
    VolatilityFilter,
)
from tradingagent.indicators.regime import (
    DEFAULT_CALM_RATIO,
    DEFAULT_VOLATILE_RATIO,
    Volatility,
    atr_ratio,
)
from tradingagent.indicators.session import Session

TOKYO_OPEN = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)  # lundi 00:00 UTC
LONDON_OPEN = datetime(2026, 10, 5, 7, 0, tzinfo=UTC)
OVERLAP_OPEN = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)
NEW_YORK_OPEN = datetime(2026, 10, 5, 16, 30, tzinfo=UTC)
OFF_OPEN = datetime(2026, 10, 5, 22, 30, tzinfo=UTC)  # 22:30 : aucune fenêtre ne le contient

LOOKBACK = 100
ATR_PERIOD = 14


def flat(value: float, count: int) -> list[float]:
    return [value] * count


def series(true_ranges: list[float]) -> tuple[Candle, ...]:
    """Une série dont chaque barre a le true range demandé.

    Le true range de la barre `i` vaut `max(high - low, |high - close[i-1]|, |low -
    close[i-1]|)`. En centrant le corps de chaque barre dans son range, la distance pertinente
    devient `range_i` : la série fabrique donc des ATR **choisis**, ce qui est la seule façon
    de tester un seuil sans dépendre du hasard d'un marché réel.
    """
    step = timedelta(seconds=Timeframe.M15.seconds)
    candles: list[Candle] = []
    low = 1000.0
    for index, true_range in enumerate(true_ranges):
        middle = low + true_range / 2
        candles.append(
            Candle(
                timeframe=Timeframe.M15,
                open_time=TOKYO_OPEN + step * index,
                open=middle,
                high=low + true_range,
                low=low,
                close=middle,
            )
        )
    return tuple(candles)


def ratio_of(true_ranges: list[float], *, lookback: int = LOOKBACK) -> float | None:
    """L'ATR courant rapporté à sa moyenne, tel que le filtre le lira."""
    candles = series(true_ranges)
    return atr_ratio(
        [candle.high for candle in candles],
        [candle.low for candle in candles],
        [candle.close for candle in candles],
        period=lookback,
    )


#: Le marché s'agite : 400 barres calmes, puis 60 au true range six fois plus grand.
VOLATILE = flat(1.0, 400) + flat(6.0, 60)
#: Le marché s'endort après une longue agitation : le rapport passe sous son seuil bas.
CALM = flat(6.0, 400) + flat(0.5, 60)
#: Une agitation modérée : le rapport monte sans atteindre le seuil haut du dépôt.
MODERATE = flat(1.0, 400) + flat(6.0, 5)
#: Un marché parfaitement régulier : le rapport vaut exactement 1.
STEADY = flat(1.0, 400)


def test_each_fixture_is_in_the_regime_it_claims() -> None:
    """Les jeux d'essai tombent dans les régimes annoncés — vérifié, pas supposé.

    C'est le test qui protège tous les autres : si un jeu d'essai dérive, les assertions de
    régime qui suivent mesureraient autre chose que ce qu'elles nomment.
    """
    volatile, calm, steady, moderate = (
        ratio_of(VOLATILE),
        ratio_of(CALM),
        ratio_of(STEADY),
        ratio_of(MODERATE),
    )
    assert volatile is not None and calm is not None
    assert steady is not None and moderate is not None
    assert volatile > DEFAULT_VOLATILE_RATIO
    assert calm < DEFAULT_CALM_RATIO
    assert math.isclose(steady, 1.0, rel_tol=1e-9)
    assert moderate > 1.1


def test_a_session_is_read_from_the_decision_time() -> None:
    policy = EntryFilter(
        session=SessionFilter(allowed=(Session.LONDON, Session.OVERLAP)),
        atr_period=ATR_PERIOD,
    )
    assert policy.decide(time=LONDON_OPEN, candles=series(flat(1.0, 10))).verdict is (
        EntryVerdict.PASS
    )
    assert policy.decide(time=OVERLAP_OPEN, candles=series(flat(1.0, 10))).verdict is (
        EntryVerdict.PASS
    )
    refused = policy.decide(time=TOKYO_OPEN, candles=series(flat(1.0, 10)))
    assert refused.verdict is EntryVerdict.REFUSED
    assert refused.reason is FilterReason.SESSION
    assert "tokyo" in refused.detail


def test_a_session_outside_every_window_is_refused_when_the_filter_names_sessions() -> None:
    policy = EntryFilter(session=SessionFilter(allowed=(Session.NEW_YORK,)), atr_period=ATR_PERIOD)
    decision = policy.decide(time=OFF_OPEN, candles=series(flat(1.0, 10)))
    assert decision.verdict is EntryVerdict.REFUSED
    assert decision.reason is FilterReason.SESSION
    assert "off" in decision.detail


def test_every_session_is_named_in_the_manifest_not_at_runtime() -> None:
    """Une session non listée est refusée : le filtre ne devine aucune fenêtre."""
    policy = EntryFilter(session=SessionFilter(allowed=(Session.TOKYO,)), atr_period=ATR_PERIOD)
    allowed = policy.decide(time=TOKYO_OPEN, candles=series(flat(1.0, 10)))
    assert allowed.verdict is EntryVerdict.PASS
    for moment in (LONDON_OPEN, OVERLAP_OPEN, NEW_YORK_OPEN, OFF_OPEN):
        assert policy.decide(time=moment, candles=series(flat(1.0, 10))).verdict is (
            EntryVerdict.REFUSED
        )


def test_volatility_keeps_the_named_regime_and_refuses_the_others() -> None:
    policy = EntryFilter(
        volatility=VolatilityFilter(allowed=(Volatility.VOLATILE,)),
        atr_period=ATR_PERIOD,
    )
    assert policy.decide(time=LONDON_OPEN, candles=series(VOLATILE)).verdict is EntryVerdict.PASS

    calm_decision = policy.decide(time=LONDON_OPEN, candles=series(CALM))
    assert calm_decision.verdict is EntryVerdict.REFUSED
    assert calm_decision.reason is FilterReason.VOLATILITY
    assert "calm" in calm_decision.detail

    steady_decision = policy.decide(time=LONDON_OPEN, candles=series(STEADY))
    assert steady_decision.verdict is EntryVerdict.REFUSED
    assert steady_decision.reason is FilterReason.VOLATILITY
    assert "normal" in steady_decision.detail


def test_a_volatility_rule_can_keep_two_regimes() -> None:
    policy = EntryFilter(
        volatility=VolatilityFilter(allowed=(Volatility.VOLATILE, Volatility.NORMAL)),
        atr_period=ATR_PERIOD,
    )
    assert policy.decide(time=LONDON_OPEN, candles=series(STEADY)).verdict is EntryVerdict.PASS
    assert policy.decide(time=LONDON_OPEN, candles=series(VOLATILE)).verdict is EntryVerdict.PASS
    assert policy.decide(time=LONDON_OPEN, candles=series(CALM)).verdict is (EntryVerdict.REFUSED)


def test_an_unmeasurable_volatility_lets_the_entry_through() -> None:
    """Fail-open : pas assez de barres pour l'ATR, le filtre laisse passer.

    C'est la décision de sécurité du filtre. Un filtre qui refuse quand il ne sait pas
    arrête l'agent sans que personne ne l'ait demandé, et aucune alerte ne le dit.
    """
    policy = EntryFilter(
        session=SessionFilter(allowed=(Session.LONDON,)),
        volatility=VolatilityFilter(allowed=(Volatility.VOLATILE,)),
        atr_period=ATR_PERIOD,
    )
    for bars in ([], flat(1.0, 1), flat(1.0, 5), flat(1.0, ATR_PERIOD), flat(1.0, 15)):
        decision = policy.decide(time=LONDON_OPEN, candles=series(list(bars)))
        assert decision.verdict is EntryVerdict.PASS, bars
        assert decision.reason is FilterReason.UNMEASURED, bars
        assert "fail-open" in decision.detail


def test_a_session_verdict_is_taken_even_when_volatility_is_unmeasurable() -> None:
    """Les deux règles sont indépendantes : une session refusée l'est, mesure ou pas."""
    policy = EntryFilter(
        session=SessionFilter(allowed=(Session.LONDON,)),
        volatility=VolatilityFilter(allowed=(Volatility.VOLATILE,)),
        atr_period=ATR_PERIOD,
    )
    decision = policy.decide(time=TOKYO_OPEN, candles=series(flat(1.0, 5)))
    assert decision.verdict is EntryVerdict.REFUSED
    assert decision.reason is FilterReason.SESSION


def test_an_inactive_filter_is_not_a_fail_open_it_is_a_no_op() -> None:
    """Sans règle, le filtre ne dit ni « mesuré » ni « non mesuré » : il ne dit rien."""
    policy = EntryFilter(atr_period=ATR_PERIOD)
    for moment in (TOKYO_OPEN, OFF_OPEN):
        decision = policy.decide(time=moment, candles=())
        assert decision.verdict is EntryVerdict.PASS
        assert decision.reason is FilterReason.NONE
        assert decision.detail == ""
    assert policy.active is False


def test_an_active_policy_says_so() -> None:
    assert EntryFilter(session=SessionFilter(allowed=(Session.TOKYO,)), atr_period=14).active
    assert EntryFilter(
        volatility=VolatilityFilter(allowed=(Volatility.VOLATILE,)), atr_period=14
    ).active
    assert EntryFilter(atr_period=14).active is False


def test_the_decision_has_no_room_for_a_signal() -> None:
    """Invariant C-002 : un filtre d'entrée n'a pas de champ pour porter un signal.

    Le test est volontairement grossier — il porte sur la **forme** de la décision. Un filtre
    qui pourrait rendre un candidat, une taille ou un stop serait une porte par laquelle une
    règle de recherche pourrait augmenter le risque au lieu de le réduire.
    """
    decision = EntryFilter(
        session=SessionFilter(allowed=(Session.LONDON,)), atr_period=ATR_PERIOD
    ).decide(time=TOKYO_OPEN, candles=())
    assert [field.name for field in dataclasses.fields(decision)] == [
        "verdict",
        "reason",
        "detail",
    ]
    assert decision.verdict in set(EntryVerdict)
    for forbidden in ("candidate", "signal", "size", "stop", "take_profit", "direction"):
        assert not hasattr(decision, forbidden)


def test_the_decision_is_a_pure_function_of_its_arguments() -> None:
    """Même entrée, même sortie — et l'ordre des appels n'y change rien (ENF-008)."""
    policy = EntryFilter(
        session=SessionFilter(allowed=(Session.OVERLAP,)),
        volatility=VolatilityFilter(allowed=(Volatility.VOLATILE, Volatility.NORMAL)),
        atr_period=ATR_PERIOD,
    )
    candles = series(VOLATILE)
    assert policy.decide(time=OVERLAP_OPEN, candles=candles) == policy.decide(
        time=OVERLAP_OPEN, candles=candles
    )


def test_volatility_thresholds_default_to_the_regime_module() -> None:
    """Le filtre ne réinvente aucun seuil : il lit ceux de `regime`."""
    policy = VolatilityFilter(allowed=(Volatility.VOLATILE,))
    assert policy.calm_ratio == DEFAULT_CALM_RATIO
    assert policy.volatile_ratio == DEFAULT_VOLATILE_RATIO


def test_the_configuration_decides_the_regime_not_the_module() -> None:
    """Le même rapport peut être « normal » ou « volatile » selon les seuils du manifeste.

    C'est ce qui rend un seuil modifiable **sans toucher au code**, donc vérifiable dans un
    manifeste versionné : le module ne porte aucun seuil caché.
    """
    candles = series(MODERATE)
    measured = ratio_of(MODERATE)
    assert measured is not None
    assert DEFAULT_VOLATILE_RATIO > measured > 1.05

    default_policy = EntryFilter(
        volatility=VolatilityFilter(allowed=(Volatility.VOLATILE,)), atr_period=ATR_PERIOD
    )
    tight_policy = EntryFilter(
        volatility=VolatilityFilter(
            allowed=(Volatility.VOLATILE,), calm_ratio=0.9, volatile_ratio=1.05
        ),
        atr_period=ATR_PERIOD,
    )
    assert default_policy.decide(time=LONDON_OPEN, candles=candles).verdict is (
        EntryVerdict.REFUSED
    )
    assert tight_policy.decide(time=LONDON_OPEN, candles=candles).verdict is EntryVerdict.PASS


def test_the_decision_carries_a_readable_reason() -> None:
    policy = EntryFilter(
        session=SessionFilter(allowed=(Session.LONDON,)),
        volatility=VolatilityFilter(allowed=(Volatility.VOLATILE,)),
        atr_period=ATR_PERIOD,
    )
    refused = policy.decide(time=TOKYO_OPEN, candles=series(STEADY))
    assert refused.detail.startswith("entry filter:")
    passed = policy.decide(time=LONDON_OPEN, candles=series(VOLATILE))
    assert passed.detail == ""


@pytest.mark.parametrize(
    "build",
    [
        lambda: SessionFilter(allowed=()),
        lambda: SessionFilter(allowed=(Session.LONDON, Session.LONDON)),
        lambda: VolatilityFilter(allowed=()),
        lambda: VolatilityFilter(allowed=(Volatility.VOLATILE, Volatility.VOLATILE)),
        lambda: VolatilityFilter(allowed=(Volatility.CALM,), calm_ratio=1.5, volatile_ratio=1.0),
        lambda: VolatilityFilter(allowed=(Volatility.CALM,), lookback=0),
        lambda: VolatilityFilter(allowed=(Volatility.CALM,), calm_ratio=-0.1),
        lambda: EntryFilter(atr_period=0),
    ],
)
def test_an_impossible_filter_is_rejected_at_construction(build: object) -> None:
    """Une règle impossible doit échouer à la lecture du manifeste, pas en production."""
    with pytest.raises(ValueError):
        build()  # type: ignore[operator]


def test_a_named_regime_matches_the_measured_ratio() -> None:
    """`regime` est la seule lecture du rapport : pas de seuil ailleurs."""
    policy = VolatilityFilter(allowed=(Volatility.VOLATILE,))
    assert policy.regime(0.5) is Volatility.CALM
    assert policy.regime(1.0) is Volatility.NORMAL
    assert policy.regime(2.0) is Volatility.VOLATILE
    assert policy.regime(None) is None
