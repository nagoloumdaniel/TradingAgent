"""Parité du filtre entre le backtest et la production, et la trace qu'il laisse.

Deux exigences, et chacune a son test :

* **parité structurelle** — le harnais de backtest et le générateur de production appellent tous
  deux `strategies.evaluation.evaluate`, donc la même porte. Le harnais compte les refus dans
  `filtered_signals`, et le générateur écrit un événement `entry_filtered` : un backtest qui
  accepterait ce que la production refuse ne mesurerait plus la stratégie exécutée ;
* **invisibilité quand rien n'est configuré** — sans `entry_filter` dans le manifeste, le
  résultat du backtest est exactement ce qu'il était, et le compteur de refus reste à zéro.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, select

from tradingagent.backtest.harness import (
    DEFAULT_ATR_PERIOD,
    BacktestConfig,
    BacktestResult,
    run_backtest,
)
from tradingagent.config.strategy_catalog import LoadedStrategy
from tradingagent.core.market import Candle, Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.signal import SignalCandidate
from tradingagent.core.states import Severity
from tradingagent.core.timeframe import Timeframe
from tradingagent.data.market_calendar import MarketCalendar, is_open
from tradingagent.signals.generator import GenerationStatus, SignalGenerator
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import SignalRow, SystemEventRow
from tradingagent.storage.signals import SignalRepository
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.manifest import DEFAULT_ATR_PERIOD as MANIFEST_ATR_PERIOD
from tradingagent.strategies.manifest import StrategyManifest

GOLD = "XAUUSD"
STEP = timedelta(minutes=15)
#: Lundi 05/10/2026, 00:00 UTC : la première bougie. 448 bougies couvrent quatre jours ouvrés.
START = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
COUNT = 448
LAST_OPEN = START + STEP * (COUNT - 1)  # 2026-10-08 15:45 UTC, séance overlap
CLOSE = LAST_OPEN + STEP  # 16:00 UTC, séance new_york (l'overlap s'arrête à 16:00)
HISTORY_BARS = 20


class NoParameters(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Always(Strategy[NoParameters]):
    """Signale à chaque bougie : toute différence entre deux exécutions vient du filtre."""

    strategy_id: ClassVar[str] = "always"
    parameters_model: ClassVar[type[BaseModel]] = NoParameters

    def evaluate(self, context: StrategyContext) -> SignalCandidate | None:
        close = context.closes(context.primary_timeframe)[-1]
        return SignalCandidate(
            direction=Direction.BUY,
            entry_low=close - 1.0,
            entry_high=close + 1.0,
            stop_loss=close - 10.0,
            take_profits=(close + 10.0,),
            reason="always",
            indicators={},
        )


def series(count: int = COUNT) -> tuple[Candle, ...]:
    """Des bougies calmes, une par quart d'heure, sans trou."""
    return tuple(
        Candle(
            timeframe=Timeframe.M15,
            open_time=START + STEP * index,
            open=100.0,
            high=100.5,
            low=99.5,
            close=100.0,
        )
        for index in range(count)
    )


def manifest(**overrides: Any) -> StrategyManifest:
    base: dict[str, Any] = {
        "strategy_id": "always",
        "version": "1.0.0",
        "max_mode": "SIGNAL",
        "allowed_symbols": [GOLD],
        "timeframes": ["M15"],
        "history_bars": HISTORY_BARS,
    }
    return StrategyManifest.model_validate({**base, **overrides})


def loaded(**overrides: Any) -> LoadedStrategy:
    return LoadedStrategy(manifest(**overrides), Always(NoParameters()))


def config() -> BacktestConfig:
    return BacktestConfig(symbol=GOLD, mode=TradingMode.SIGNAL, max_concurrent_positions=1)


def replay(**overrides: Any) -> BacktestResult:
    return run_backtest(
        Always(NoParameters()), manifest(**overrides), {Timeframe.M15: series()}, config()
    )


#: Les 672 créneaux d'une semaine : la définition d'un marché qui ne ferme jamais.
SLOTS_PER_WEEK_SET: set[tuple[int, int]] = {
    (weekday, quarter) for weekday in range(7) for quarter in range(96)
}


def always_open() -> MarketCalendar:
    """Un calendrier ouvert en permanence.

    Construit plutôt qu'appris : `learn_calendar` a besoin de huit semaines d'historique pour
    décider qu'un créneau ouvre, et une série de quatre jours laisserait un calendrier vide — la
    porte serait alors jugée sur un marché fermé, ce qui n'est pas le sujet de ces tests.
    """
    return MarketCalendar(GOLD, frozenset(SLOTS_PER_WEEK_SET), frozenset())


def test_the_two_atr_defaults_are_the_same_number() -> None:
    """Le manifeste et le harnais doivent mesurer la volatilité avec le même ATR."""
    assert MANIFEST_ATR_PERIOD == DEFAULT_ATR_PERIOD


def test_the_calendar_fixture_covers_the_series() -> None:
    """Le calendrier d'essai est ouvert partout : aucune décision n'est « marché fermé »."""
    calendar = always_open()
    for candle in series():
        assert is_open(calendar, candle.close_time)


def test_a_manifest_without_a_filter_changes_nothing_in_the_harness() -> None:
    """La garantie qui protège les campagnes déjà publiées : pas de clé, pas d'effet."""
    result = replay()
    assert result.filtered_signals == 0
    assert result.signals == result.decisions
    assert result.entries == len(result.trades)


def test_the_harness_counts_the_refusals_and_takes_no_foreign_trade() -> None:
    """Un filtre qui n'autorise que tokyo laisse entrer les signaux de tokyo, et rien d'autre.

    Les refus sont comptés **avec les décisions**, pas avec « pas de signal » : un signal refusé
    par la porte n'est jamais un signal au sens du harnais. C'est la distinction que rend
    possible `OutcomeKind.FILTERED`, et elle est vérifiée ici sur les deux moitiés.
    """
    result = replay(entry_filter={"session": {"allowed": ["tokyo"]}})
    assert result.filtered_signals > 0
    assert result.signals > 0
    assert result.filtered_signals + result.signals == result.decisions
    assert result.entries == len(result.trades) > 0
    # Aucune entrée n'a été prise hors de la séance autorisée. Le harnais range dans les
    # caractéristiques de l'opération l'index de la séance du remplissage
    # (`indicators/features.py`) : `Session.TOKYO` vaut 0, donc cette vérification ne recopie
    # aucune fenêtre.
    assert {trade.features["session"] for trade in result.trades} == {0.0}


def test_an_allowed_session_trades_exactly_like_no_filter() -> None:
    """Autoriser toutes les séances rend le même backtest que ne rien filtrer."""
    unfiltered = replay()
    filtered = replay(
        entry_filter={"session": {"allowed": ["tokyo", "london", "overlap", "new_york", "off"]}}
    )
    assert filtered.filtered_signals == 0
    assert len(filtered.trades) == len(unfiltered.trades)
    assert [trade.pnl_eur for trade in filtered.trades] == [
        trade.pnl_eur for trade in unfiltered.trades
    ]


def test_a_retained_session_still_trades() -> None:
    """Un filtre qui garde une séance réelle doit produire des opérations, pas zéro.

    Sans ce test, un filtre qui refuse tout passerait pour un filtre qui marche.
    """
    result = replay(entry_filter={"session": {"allowed": ["tokyo"]}})
    tokyo_closes = [
        candle.close_time
        for candle in series()
        if candle.close_time.hour < 7 or candle.close_time.hour >= 22
    ]
    assert tokyo_closes
    assert result.entries > 0
    assert result.filtered_signals > 0


def test_the_volatility_half_of_the_filter_is_the_same_in_both_paths() -> None:
    """Même mesure des deux côtés : le rapport ATR lu par le harnais et par la porte.

    Une série parfaitement régulière a un rapport ATR/moyenne de 1 : le régime est « médian »,
    donc une porte qui n'autorise que « agité » refuse chaque décision, et le compteur le dit
    sans qu'aucune opération n'existe.

    **La fenêtre doit être assez longue pour mesurer.** Une porte de volatilité a besoin de
    l'historique que son ATR demande (ici `lookback=100` valeurs, chacune sur `atr_period=14`
    barres) : en dessous, le filtre est fail-open et laisse tout passer — c'est le test suivant
    qui le prouve, et c'est un prérequis de déploiement, pas un détail.
    """
    result = replay(history_bars=200, entry_filter={"volatility": {"allowed": ["volatile"]}})
    assert result.filtered_signals == result.decisions > 0
    assert result.signals == 0
    assert result.entries == 0
    assert result.trades == ()


def test_a_volatility_filter_that_cannot_measure_lets_everything_through() -> None:
    """Fail-open, à l'autre bout de la chaîne : une fenêtre trop courte ne mesure rien.

    Vingt barres ne donnent aucune valeur d'ATR sur cent : le rapport est indéfini, donc le
    filtre **laisse passer** au lieu d'éteindre l'agent en silence. Le harnais le prouve en
    produisant exactement le même backtest que sans filtre.
    """
    unfiltered = replay()
    unmeasurable = replay(entry_filter={"volatility": {"allowed": ["volatile"]}})
    assert unmeasurable.filtered_signals == 0
    assert unmeasurable.signals == unfiltered.signals > 0
    assert [trade.pnl_eur for trade in unmeasurable.trades] == [
        trade.pnl_eur for trade in unfiltered.trades
    ]


def test_the_volatility_rule_accepts_a_regime_that_can_occur() -> None:
    """Le pendant du test précédent : un filtre qui ne refuse rien ne prouve rien.

    Une porte qui autorise le régime réellement mesuré — « médian » sur une série régulière —
    laisse passer les mêmes opérations que l'absence de filtre.
    """
    unfiltered = replay(history_bars=200)
    accepted = replay(history_bars=200, entry_filter={"volatility": {"allowed": ["normal"]}})
    assert accepted.filtered_signals == 0
    assert len(accepted.trades) == len(unfiltered.trades) > 0


# ---------------------------------------------------------------------------------------
# La trace en production : un refus doit être lisible, un accord ne doit rien écrire.
# ---------------------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'entry-filter.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def events(engine: Engine) -> list[tuple[str, Severity]]:
    with engine.connect() as connection:
        rows = connection.execute(select(SystemEventRow.kind, SystemEventRow.severity)).all()
    return [(str(kind), severity) for kind, severity in rows]


def signals(engine: Engine) -> list[SignalRow]:
    with engine.connect() as connection:
        return list(connection.execute(select(SignalRow)).all())  # type: ignore[arg-type]


def generate(engine: Engine, **overrides: Any) -> GenerationStatus:
    """Range la série, fait fermer sa dernière bougie, et rend le statut de la génération."""
    candles = series()
    CandleStore(engine).save(GOLD, candles, CLOSE + timedelta(seconds=30))
    trigger = candles[-1]
    # La dernière bougie ferme à 16:00 UTC, en séance new_york : la décision est bien prise,
    # c'est la porte qui parle.
    assert trigger.close_time == CLOSE
    generator = SignalGenerator(
        [loaded(**overrides)],
        CandleStore(engine),
        SignalRepository(engine),
        agent_mode=TradingMode.SIGNAL,
    )
    [result] = generator.on_candle_closed(
        GOLD, trigger, always_open(), CLOSE + timedelta(seconds=30), CLOSE
    )
    return result.status


def test_a_refused_signal_leaves_a_readable_event_and_records_nothing(engine: Engine) -> None:
    """Le refus est traçable : c'est ce qui le distingue d'un marché sans signal."""
    status = generate(engine, entry_filter={"session": {"allowed": ["tokyo"]}})
    assert status is GenerationStatus.FILTERED
    assert signals(engine) == []
    assert "entry_filtered" in [kind for kind, _ in events(engine)]


def test_an_allowed_signal_writes_a_signal_and_no_filter_event(engine: Engine) -> None:
    status = generate(engine, entry_filter={"session": {"allowed": ["new_york"]}})
    assert status is GenerationStatus.RECORDED
    assert len(signals(engine)) == 1
    assert "entry_filtered" not in [kind for kind, _ in events(engine)]


def test_a_manifest_without_the_filter_traces_nothing_new(engine: Engine) -> None:
    status = generate(engine)
    assert status is GenerationStatus.RECORDED
    assert "entry_filtered" not in [kind for kind, _ in events(engine)]
