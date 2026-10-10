"""Le filtre d'entrée tel que les manifestes et la voie d'évaluation partagée l'appliquent.

Ces tests portent sur le contrat qui rend le filtre acceptable : **il ne change rien tant que
le manifeste ne le configure pas**, il refuse une entrée en séance exclue et laisse passer une
entrée en séance retenue, il laisse passer quand la mesure manque (fail-open), et il ne peut
jamais créer de signal. Ils valident aussi la clé de manifeste elle-même : nommer une séance
inconnue ou un régime inconnu doit échouer à la lecture du fichier, pas en production.
"""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from tradingagent.core.market import Candle, Direction
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.evaluation import OutcomeKind, evaluate
from tradingagent.strategies.manifest import (
    DEFAULT_ATR_PERIOD,
    EntryFilterRule,
    SessionRule,
    StrategyManifest,
    VolatilityRule,
)

GOLD = "XAUUSD"
TIMEFRAME = Timeframe.M15
STEP = timedelta(seconds=TIMEFRAME.seconds)
#: Lundi 05/10/2026. 00:00 UTC est tokyo, 07:00 london, 13:00 overlap, 16:30 new_york, 22:30 off.
MONDAY = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)


class NoParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Always(Strategy[NoParameters]):
    """Une règle qui produit un candidat à chaque évaluation : le filtre est le seul juge."""

    strategy_id: ClassVar[str] = "always"
    parameters_model: ClassVar[type[BaseModel]] = NoParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        close = context.closes(context.primary_timeframe)[-1]
        return SignalCandidate(
            direction=Direction.BUY,
            entry_low=close - 1,
            entry_high=close + 1,
            stop_loss=close - 10,
            take_profits=(close + 10,),
            reason="always",
            indicators={},
        )


def candles(count: int, *, start: datetime = MONDAY, span: float = 1.0) -> tuple[Candle, ...]:
    """Une série calme, terminant exactement `count` pas de temps après `start`."""
    return tuple(
        Candle(
            timeframe=TIMEFRAME,
            open_time=start + STEP * index,
            open=100.0,
            high=100.0 + span,
            low=100.0,
            close=100.0 + span / 2,
        )
        for index in range(count)
    )


def close_time(index: int, *, start: datetime = MONDAY) -> datetime:
    """La clôture de la bougie `index` : c'est l'instant de décision du harnais."""
    return start + STEP * (index + 1)


def manifest(**overrides: Any) -> StrategyManifest:
    base: dict[str, Any] = {
        "strategy_id": "always",
        "version": "1.0.0",
        "max_mode": "SIGNAL",
        "allowed_symbols": [GOLD],
        "timeframes": ["M15"],
        "history_bars": 10,
    }
    return StrategyManifest.model_validate({**base, **overrides})


# ---------------------------------------------------------------------------------------
# Le manifeste : une clé absente ne change rien, une clé impossible ne passe pas.
# ---------------------------------------------------------------------------------------


def test_a_manifest_without_the_key_is_exactly_what_it_was() -> None:
    loaded = manifest()
    assert loaded.entry_filter is None
    assert loaded.entry_policy().active is False
    assert loaded.atr_period == DEFAULT_ATR_PERIOD


def test_a_filter_cannot_be_empty_by_accident() -> None:
    """`entry_filter:` sans règle est une faute de frappe, pas un no-op silencieux."""
    with pytest.raises(ValidationError, match="at least one"):
        manifest(entry_filter={})


@pytest.mark.parametrize(
    "entry_filter",
    [
        {"session": {"allowed": ["narnia"]}},
        {"session": {"allowed": []}},
        {"session": {"allowed": ["tokyo", "tokyo"]}},
        {"volatility": {"allowed": ["explosive"]}},
        {"volatility": {"allowed": []}},
        {"volatility": {"allowed": ["volatile", "volatile"]}},
        {"volatility": {"allowed": ["calm"], "lookback": 0}},
        {"volatility": {"allowed": ["calm"], "calm_ratio": 1.5, "volatile_ratio": 1.0}},
        {"session": {"allowed": ["tokyo"]}, "volatilty": {"allowed": ["volatile"]}},
    ],
)
def test_an_impossible_manifest_key_is_refused_at_validation(entry_filter: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        manifest(entry_filter=entry_filter)


def test_a_named_filter_compiles_into_the_shared_policy() -> None:
    loaded = manifest(
        entry_filter={
            "session": {"allowed": ["london", "overlap"]},
            "volatility": {"allowed": ["volatile"], "lookback": 50},
        }
    )
    policy = loaded.entry_policy()
    assert policy.active is True
    assert policy.session is not None
    assert policy.volatility is not None
    assert policy.volatility.lookback == 50


def test_the_manifest_and_its_parameters_cannot_disagree_on_the_atr() -> None:
    """Deux ATR différents décriraient deux marchés : la lecture du fichier doit refuser."""
    with pytest.raises(ValidationError, match="contradicts"):
        manifest(atr_period=20, parameters={"atr_period": 14})
    assert manifest(atr_period=14, parameters={"atr_period": 14}).atr_period == 14


def test_the_rule_models_are_immutable_and_strict() -> None:
    rule = EntryFilterRule(session=SessionRule(allowed=("tokyo",)))
    with pytest.raises(ValidationError):
        rule.session = None  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SessionRule.model_validate({"allowed": ["tokyo"], "extra": 1})
    with pytest.raises(ValidationError):
        VolatilityRule.model_validate({"allowed": ["volatile"], "extra": 1})


# ---------------------------------------------------------------------------------------
# La voie d'évaluation partagée : refuser, laisser passer, et ne jamais créer.
# ---------------------------------------------------------------------------------------


def run(
    at: datetime,
    series: tuple[Candle, ...],
    **overrides: Any,
) -> Any:
    return evaluate(Always(NoParameters()), manifest(**overrides), GOLD, {TIMEFRAME: series}, at)


def test_a_signal_in_an_excluded_session_is_refused() -> None:
    series = candles(50)
    policy = {"session": {"allowed": ["tokyo"]}}
    # 50 bougies M15 depuis minuit : la dernière ferme à 12:15 UTC, donc en séance london
    # (`session.DEFAULT_WINDOWS`), qui n'est pas dans l'ensemble autorisé.
    refused = run(series[-1].close_time, series, entry_filter=policy)
    assert series[-1].close_time.hour == 12
    assert refused.kind is OutcomeKind.FILTERED
    assert refused.candidate is None
    assert "london" in refused.detail
    assert refused.filter_verdict is not None


def test_a_signal_in_a_retained_session_passes() -> None:
    # La 27e bougie ferme à 07:00 UTC, première bougie de london.
    series = candles(50)
    allowed = run(close_time(27), series, entry_filter={"session": {"allowed": ["london"]}})
    assert allowed.kind is OutcomeKind.SIGNAL
    assert allowed.candidate is not None


def test_the_filter_is_invisible_without_the_key() -> None:
    """Le seul contrat qui protège la production : pas de clé, pas de changement."""
    series = candles(50)
    before = run(series[-1].close_time, series)
    assert before.kind is OutcomeKind.SIGNAL
    assert before.filter_verdict is None
    assert before == run(series[-1].close_time, series)


def test_an_unmeasurable_volatility_lets_the_signal_through() -> None:
    """Fail-open : dix bougies ne donnent pas d'ATR, donc le filtre laisse passer."""
    series = candles(10)
    outcome = run(
        series[-1].close_time,
        series,
        history_bars=5,
        entry_filter={"volatility": {"allowed": ["volatile"]}},
    )
    assert outcome.kind is OutcomeKind.SIGNAL
    assert outcome.candidate is not None


def test_a_measurable_but_excluded_volatility_refuses_the_signal() -> None:
    """Une série parfaitement régulière a un rapport de 1 : le régime « agité » l'exclut."""
    series = candles(400)
    refused = run(
        series[-1].close_time,
        series,
        history_bars=400,
        entry_filter={"volatility": {"allowed": ["volatile"]}},
    )
    assert refused.kind is OutcomeKind.FILTERED
    assert "volatility" in refused.detail


def test_the_filter_can_only_remove_signals_never_add_one() -> None:
    """Invariant C-002, mesuré sur la voie partagée : le filtre est monotone.

    Sur une série où la stratégie signale à chaque évaluation, un filtre activé ne peut que
    produire moins de signaux que sans lui — jamais plus, jamais un autre.
    """
    series = candles(400)
    times = [close_time(index) for index in range(9, 399)]
    without = [run(at, series, history_bars=10) for at in times]
    with_filter = [
        run(
            at,
            series,
            history_bars=10,
            entry_filter={"session": {"allowed": ["tokyo"]}},
        )
        for at in times
    ]
    plain = [outcome.kind for outcome in without]
    filtered = [outcome.kind for outcome in with_filter]
    assert plain.count(OutcomeKind.SIGNAL) == len(times)
    assert filtered.count(OutcomeKind.SIGNAL) <= plain.count(OutcomeKind.SIGNAL)
    assert all(
        outcome.kind in {OutcomeKind.SIGNAL, OutcomeKind.FILTERED} for outcome in with_filter
    )
    # Chaque signal conservé est exactement un signal que la stratégie avait produit, dans le
    # même ordre : le filtre en retire, il n'en fabrique aucun et n'en modifie aucun.
    produced = [outcome.candidate for outcome in without]
    retained = [outcome.candidate for outcome in with_filter if outcome.kind is OutcomeKind.SIGNAL]
    position = 0
    for candidate in retained:
        while position < len(produced) and produced[position] != candidate:
            position += 1
        assert position < len(produced), "un signal retenu n'a jamais été produit par la règle"
        position += 1
    assert 0 < len(retained) < len(produced)
