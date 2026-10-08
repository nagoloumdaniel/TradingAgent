"""La garde « cassure seulement » : elle doit refuser un range et laisser passer le reste.

Ces tests portent sur la **délégation**, pas sur la règle sous-jacente : le parent est déjà
testé ailleurs. Ce qui compte ici, c'est que la garde ne réécrive jamais le signal et qu'elle
n'ouvre rien quand le marché oscille.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tradingagent.core.market import Candle, Direction
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.timeframe import Timeframe
from tradingagent.indicators.regime import is_breakout
from tradingagent.research.discovery import BreakoutOnly
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.library.witness import WitnessParameters

START = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
STEP = timedelta(minutes=15)


class Spy(Strategy[WitnessParameters]):
    """Un parent qui rend toujours le même signal, et compte ses appels."""

    strategy_id = "spy"
    parameters_model = WitnessParameters

    def __init__(self, parameters: WitnessParameters, candidate: SignalCandidate) -> None:
        super().__init__(parameters)
        self._candidate = candidate
        self.calls = 0

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        del context
        self.calls += 1
        return self._candidate


def parameters() -> WitnessParameters:
    return WitnessParameters(
        ema_fast=10,
        ema_slow=30,
        atr_period=14,
        stop_atr_multiplier=1.5,
        take_profit_rr=2.0,
        entry_zone_atr=0.1,
    )


def candidate() -> SignalCandidate:
    return SignalCandidate(
        direction=Direction.BUY,
        entry_low=99.0,
        entry_high=101.0,
        stop_loss=90.0,
        take_profits=(110.0,),
        reason="parent",
        indicators={},
    )


def context(closes: list[float]) -> StrategyContext:
    candles = tuple(
        Candle(
            timeframe=Timeframe.M15,
            open_time=START + STEP * index,
            open=price,
            high=price + 1.0,
            low=price - 1.0,
            close=price,
        )
        for index, price in enumerate(closes)
    )
    return StrategyContext(
        symbol="XAUUSD",
        evaluated_at=candles[-1].close_time,
        primary_timeframe=Timeframe.M15,
        candles={Timeframe.M15: candles},
    )


def build(candidate_signal: SignalCandidate) -> tuple[BreakoutOnly, Spy]:
    inner = Spy(parameters(), candidate_signal)
    return BreakoutOnly(parameters(), inner), inner


def test_a_flat_market_is_a_range_and_the_gate_refuses() -> None:
    """Un marché plat est un range : la garde refuse, et le parent n'est même pas appelé."""
    strategy, inner = build(candidate())

    result = strategy.evaluate(context([100.0] * 40))

    assert result is None
    assert inner.calls == 0, "la garde doit refuser avant de déranger le parent"


def test_a_breakout_lets_the_parent_signal_through_unchanged() -> None:
    """Sur une cassure, le signal du parent passe **tel quel** : pas de réécriture."""
    expected = candidate()
    strategy, inner = build(expected)
    closes = [*([100.0] * 39), 120.0]

    result = strategy.evaluate(context(closes))

    assert result is expected, "la garde doit déléguer, pas reconstruire"
    assert inner.calls == 1


def test_the_channel_window_follows_the_slow_average() -> None:
    """Le canal s'adosse à la moyenne lente du parent au lieu d'être inventé."""
    strategy, _ = build(candidate())

    assert strategy.channel == 30


def test_the_gate_and_the_indicator_agree() -> None:
    """La garde ne doit pas réimplémenter la cassure : elle doit lire le même verdict.

    Le test compare donc deux contextes **cohérents** — un plat, un cassé — et vérifie que
    `is_breakout` dit la même chose que la garde, sur les mêmes séries.
    """
    flat = context([100.0] * 40)
    broken = context([*([100.0] * 39), 120.0])

    for ctx, expected in ((flat, False), (broken, True)):
        verdict = is_breakout(
            ctx.highs(Timeframe.M15),
            ctx.lows(Timeframe.M15),
            ctx.closes(Timeframe.M15),
            channel=30,
        )
        assert verdict is expected

    gate, _ = build(candidate())
    assert gate.evaluate(flat) is None
    assert gate.evaluate(broken) is not None


def test_a_short_series_cannot_prove_a_breakout() -> None:
    """Sans assez de barres pour le canal, il n'y a pas de cassure : la garde refuse."""
    strategy, inner = build(candidate())

    result = strategy.evaluate(context([100.0, 101.0, 102.0]))

    assert result is None
    assert inner.calls == 0


def test_the_parameters_are_the_parent_model() -> None:
    """Le wrapper expose exactement le modèle de paramètres qu'il utilise : pas de dérive."""
    assert BreakoutOnly.parameters_model is WitnessParameters


def test_the_strategy_is_not_registered_in_production() -> None:
    """Un candidat de recherche n'est pas une stratégie exécutable : il n'est pas au registre."""
    from tradingagent.strategies.registry import REGISTRY

    assert BreakoutOnly.strategy_id not in REGISTRY


def test_a_missing_parameters_model_is_refused() -> None:
    """Le modèle du parent valide la cohérence : un couple incohérent doit être rejeté."""
    with pytest.raises(ValueError, match="ema_slow"):
        WitnessParameters(
            ema_fast=30,
            ema_slow=10,
            atr_period=14,
            stop_atr_multiplier=1.5,
            take_profit_rr=2.0,
            entry_zone_atr=0.1,
        )
