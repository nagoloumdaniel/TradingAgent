"""Axe A : balayage du stop et de la geometrie des sorties de `vwap_pullback`.

Le diagnostic MAE/MFE dit que les perdants touchent le stop a 1,192 R en mediane et que le
ratio gain/perte median vaut 0,673 : a 1,5 ATR le stop est trop large pour la geometrie
d'entree. Ce script mesure `stop_atr_multiplier` croise avec les paires TP1/TP2, avec et sans
sortie partielle + break-even, puis les briques de gestion jamais utilisees sur cette regle
(`trailing_stop_swing_strength`, `max_holding_bars`).

**Deux etages, et c'est une contrainte de temps, pas un choix.** Un backtest de 20 000 barres
coute environ 3 minutes ; 30 configurations y prendraient une heure et demie. L'etage 1 tourne
donc large sur 5 000 barres pour eliminer, l'etage 2 affine sur 20 000 barres les seules
configurations qui ont montre quelque chose. Chaque ligne ecrite porte le nombre de barres,
pour qu'aucune mesure de 5 000 ne soit lue comme une mesure de 20 000.

La sortie est un JSONL : chaque mesure est ecrite des qu'elle est terminee, donc une serie
interrompue garde ce qu'elle a deja mesure.

    uv run python scripts/backtest/tune_stop_sweep.py --group stage1 --bars 4999
    uv run python scripts/backtest/tune_stop_sweep.py --group stage2 --bars 19999
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.costs import CostModel
from tradingagent.backtest.datasets import DatasetStore
from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.strategies.manifest import StrategyManifest

ROOT = Path(__file__).resolve().parents[2]
DATASETS = ROOT / "docs" / "research" / "datasets-volume"
OUTPUT_DIR = ROOT / "docs" / "research" / "vwap-tuning"

#: La regle, **figee au commit `bfa0e65`**, et c'est une precaution de mesure, pas un gout.
#:
#: `src/tradingagent/strategies/library/vwap_pullback.py` est en cours d'edition par l'axe B
#: pendant que cet axe mesure (filtres optionnels, ajoutes apres la reference a PF 0,990). Un
#: balayage qui lit le fichier vivant mesure une cible mobile : le premier passage du 2026-10-09
#: a rendu 50 fois « 0 trade » avec `TypeError: must be real number, not str` sur **chaque**
#: barre, parce que la version en cours d'ecriture etait cassee a cet instant. Ces mesures-la ne
#: disent rien de la regle, elles disent que le fichier changeait.
#:
#: Le module charge ici est la copie byte pour byte du commit, deposee dans le perimetre
#: d'ecriture de cet axe avec son empreinte SHA-256. Toute mesure de ce fichier est donc
#: reproductible, et ne depend ni de l'ordre des commits des autres axes ni de leur etat
#: intermediaire. Quand `src/` se stabilise, le meme balayage se relance avec `--live` pour
#: verifier que la reference n'a pas bouge.
PINNED = OUTPUT_DIR / "vwap_pullback_pinned_bfa0e65.py"
PINNED_REVISION = "bfa0e65"


def load_strategy_module(live: bool = False) -> Any:
    """La regle a mesurer : la copie figee, ou la source vivante si on le demande."""
    if live:
        from tradingagent.strategies.library import vwap_pullback

        print("== regle lue depuis src/ (source vivante) ==")
        return vwap_pullback
    digest = hashlib.sha256(PINNED.read_bytes()).hexdigest()
    print(f"== regle figee sur {PINNED_REVISION} : {PINNED.name} sha256 {digest} ==")
    specification = importlib.util.spec_from_file_location("pinned_vwap_pullback", PINNED)
    if specification is None or specification.loader is None:
        raise SystemExit(f"{PINNED}: module chargeable introuvable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


SYMBOL = "BTCUSD"
TIMEFRAME = Timeframe.M15

#: Le manifeste est construit a la main : la regle ne declare que BTCUSD, et c'est exactement
#: le marche mesure. `history_bars=400` comme la campagne, `expiry_bars=2` comme la campagne.
MANIFEST = StrategyManifest(
    strategy_id="vwap_pullback",
    version="1.0.0",
    max_mode=TradingMode.SIGNAL,
    allowed_symbols=(SYMBOL,),
    timeframes=(TIMEFRAME,),
    history_bars=400,
    expiry_bars=2,
)

#: Les parametres de reference, ceux de la campagne : tout le reste du balayage n'en bouge
#: qu'une ou deux valeurs a la fois.
BASE: dict[str, float] = {
    "ema_fast": 20,
    "ema_slow": 50,
    "vwap_period": 20,
    "atr_period": 14,
    "stop_atr_multiplier": 1.5,
    "first_target_rr": 1.5,
    "final_target_rr": 3.0,
    "pullback_atr": 0.4,
    "entry_zone_atr": 0.1,
    "min_slope_atr": 0.005,
    "slope_window": 10,
}

TP_PAIRS: tuple[tuple[float, float], ...] = ((0.8, 1.5), (1.0, 2.0), (1.5, 3.0), (2.0, 4.0))
STOPS: tuple[float, ...] = (0.8, 1.0, 1.2, 1.5, 2.0)
PARTIAL = (0.5, 0.5)


@dataclass(frozen=True)
class Arm:
    """Une configuration mesuree : des parametres de regle et un harnais."""

    label: str
    parameters: dict[str, float]
    partial_exit_fractions: tuple[float, ...] = PARTIAL
    move_stop_to_breakeven_after_first_target: bool = True
    trailing_stop_swing_strength: int | None = None
    trailing_stop_atr: float | None = None
    max_holding_bars: int | None = None

    def config(self, costs: CostModel) -> BacktestConfig:
        return BacktestConfig(
            symbol=SYMBOL,
            costs=costs,
            mode=TradingMode.SIGNAL,
            max_concurrent_positions=1,
            partial_exit_fractions=self.partial_exit_fractions,
            move_stop_to_breakeven_after_first_target=(
                self.move_stop_to_breakeven_after_first_target
            ),
            trailing_stop_swing_strength=self.trailing_stop_swing_strength,
            trailing_stop_atr=self.trailing_stop_atr,
            max_holding_bars=self.max_holding_bars,
        )


def arm(label: str, **overrides: Any) -> Arm:
    """Une configuration derivee de la reference : seules les cles citees changent."""
    parameters = dict(BASE)
    harness: dict[str, Any] = {}
    for key, value in overrides.items():
        if key in parameters:
            parameters[key] = value
        else:
            harness[key] = value
    return Arm(label=label, parameters=parameters, **harness)


def stop_grid(partial: bool) -> list[Arm]:
    """Le croisement demande : 5 stops x 4 paires TP, avec ou sans partiel + break-even.

    Les 20 configurations portent le **meme** `pullback_atr` (0,4) et le **meme**
    `entry_zone_atr` (0,1) : c'est la seule facon de comparer des stops entre eux sans que la
    detection change en meme temps. Les deux valeurs restent valides a 0,8 ATR, le stop le plus
    serre du balayage.
    """
    suffix = "partiel" if partial else "sortie-unique"
    options: dict[str, Any] = {}
    if not partial:
        options = {
            "partial_exit_fractions": (),
            "move_stop_to_breakeven_after_first_target": False,
        }
    return [
        arm(
            f"stop{stop:.1f}_tp{first:.1f}-{final:.1f}_{suffix}",
            stop_atr_multiplier=stop,
            first_target_rr=first,
            final_target_rr=final,
            **options,
        )
        for stop in STOPS
        for first, final in TP_PAIRS
    ]


def stage1() -> list[Arm]:
    """Elimination large : le croisement, les briques de gestion, un objectif lointain."""
    arms = stop_grid(partial=True) + stop_grid(partial=False)
    for stop in (1.0, 1.2, 1.5):
        arms.append(
            arm(
                f"stop{stop:.1f}_tp1.5-3.0_swing2",
                stop_atr_multiplier=stop,
                first_target_rr=1.5,
                final_target_rr=3.0,
                trailing_stop_swing_strength=2,
            )
        )
        arms.append(
            arm(
                f"stop{stop:.1f}_tp1.5-3.0_swing3",
                stop_atr_multiplier=stop,
                first_target_rr=1.5,
                final_target_rr=3.0,
                trailing_stop_swing_strength=3,
            )
        )
    arms.append(
        arm(
            "stop1.2_tp1.5-3.0_swing3_partiel_off",
            stop_atr_multiplier=1.2,
            first_target_rr=1.5,
            final_target_rr=3.0,
            trailing_stop_swing_strength=3,
            partial_exit_fractions=(),
            move_stop_to_breakeven_after_first_target=False,
        )
    )
    for holding in (16, 48):
        arms.append(
            arm(
                f"stop1.2_tp1.5-3.0_holding{holding}",
                stop_atr_multiplier=1.2,
                first_target_rr=1.5,
                final_target_rr=3.0,
                max_holding_bars=holding,
            )
        )
    arms.append(
        arm(
            "stop1.5_tp3.0-6.0_partiel",
            stop_atr_multiplier=1.5,
            first_target_rr=3.0,
            final_target_rr=6.0,
        )
    )
    return arms


def stage2_stops() -> list[Arm]:
    """Affinage sur 20 000 barres : la reference, les stops serres, la paire 0,8/1,5."""
    return [
        arm(
            f"stop{stop:.1f}_tp{first:.1f}-{final:.1f}_partiel",
            stop_atr_multiplier=stop,
            first_target_rr=first,
            final_target_rr=final,
        )
        for stop in (0.8, 1.0, 1.2, 1.5, 2.0)
        for first, final in ((0.8, 1.5), (1.0, 2.0), (1.5, 3.0), (2.0, 4.0))
    ]


def stage2_roll() -> list[Arm]:
    """Les briques de gestion sur 20 000 barres : trailing structure, holding, partiel."""
    arms: list[Arm] = []
    for stop in (0.8, 1.0, 1.2, 1.5):
        arms.append(
            arm(
                f"stop{stop:.1f}_tp1.5-3.0_swing2",
                stop_atr_multiplier=stop,
                first_target_rr=1.5,
                final_target_rr=3.0,
                trailing_stop_swing_strength=2,
            )
        )
        arms.append(
            arm(
                f"stop{stop:.1f}_tp1.5-3.0_swing3",
                stop_atr_multiplier=stop,
                first_target_rr=1.5,
                final_target_rr=3.0,
                trailing_stop_swing_strength=3,
            )
        )
    for stop in (1.0, 1.2):
        for holding in (16, 48):
            arms.append(
                arm(
                    f"stop{stop:.1f}_tp1.5-3.0_holding{holding}",
                    stop_atr_multiplier=stop,
                    first_target_rr=1.5,
                    final_target_rr=3.0,
                    max_holding_bars=holding,
                )
            )
    for stop in (1.0, 1.2):
        arms.append(
            arm(
                f"stop{stop:.1f}_tp1.5-3.0_partiel70-30",
                stop_atr_multiplier=stop,
                first_target_rr=1.5,
                final_target_rr=3.0,
                partial_exit_fractions=(0.7, 0.3),
            )
        )
    return arms


def stage2_no_partial() -> list[Arm]:
    """Sortie unique (aucun partiel, aucun break-even) sur 20 000 barres, pour la comparaison."""
    return [
        a
        for a in stop_grid(partial=False)
        if a.parameters["stop_atr_multiplier"] in (0.8, 1.0, 1.2, 1.5)
    ]


def decision_core() -> list[Arm]:
    """Le premier lot de **decision**, sur le jeu complet : les stops serres croises aux TP.

    Toutes ces configurations gardent `pullback_atr=0,4` et `entry_zone_atr=0,1` : l'ensemble
    des signaux produits est donc **le meme** d'une ligne a l'autre, et seule la geometrie du
    stop et des objectifs change. C'est la seule facon d'attribuer un ecart de PF au stop plutot
    qu'a un changement de detection. Le plancher est 0,5 ATR : en dessous, `pullback_atr=0,4`
    violerait la contrainte du modele de parametres, et la detection changerait avec le stop.
    """
    arms = [
        arm(
            "ref_stop1.5_tp0.8-1.5_sortie-unique",
            stop_atr_multiplier=1.5,
            first_target_rr=0.8,
            final_target_rr=1.5,
            partial_exit_fractions=(),
            move_stop_to_breakeven_after_first_target=False,
        ),
        arm(
            "ref_stop1.5_tp0.8-1.5_partiel",
            stop_atr_multiplier=1.5,
            first_target_rr=0.8,
            final_target_rr=1.5,
        ),
        arm(
            "ref_stop1.5_tp1.5-3.0_partiel",
            stop_atr_multiplier=1.5,
            first_target_rr=1.5,
            final_target_rr=3.0,
        ),
        arm(
            "ref_stop2.0_tp1.5-3.0_partiel",
            stop_atr_multiplier=2.0,
            first_target_rr=1.5,
            final_target_rr=3.0,
        ),
    ]
    for stop in (0.5, 0.6, 0.8, 1.0, 1.2):
        arms.append(
            arm(
                f"stop{stop:.1f}_tp0.8-1.5_partiel",
                stop_atr_multiplier=stop,
                first_target_rr=0.8,
                final_target_rr=1.5,
            )
        )
        arms.append(
            arm(
                f"stop{stop:.1f}_tp1.5-3.0_partiel",
                stop_atr_multiplier=stop,
                first_target_rr=1.5,
                final_target_rr=3.0,
            )
        )
    for stop in (0.8, 1.0, 1.2):
        arms.append(
            arm(
                f"stop{stop:.1f}_tp1.0-2.0_partiel",
                stop_atr_multiplier=stop,
                first_target_rr=1.0,
                final_target_rr=2.0,
            )
        )
    return arms


def decision_second() -> list[Arm]:
    """Le second lot : les briques de gestion, dans la zone de stop que le premier lot designe.

    Chaque hypothese y est testee **a detection constante** (memes `pullback_atr`/`entry_zone_atr`)
    et sur le jeu complet. Les libelles portent le stop, donc ce lot se relance sans le reecrire
    quand le premier lot deplace le meilleur stop.
    """
    arms: list[Arm] = []
    for stop in (0.8, 1.0, 1.2):
        prefix = f"stop{stop:.1f}_tp1.5-3.0"
        arms.append(
            arm(
                f"{prefix}_swing3",
                stop_atr_multiplier=stop,
                first_target_rr=1.5,
                final_target_rr=3.0,
                trailing_stop_swing_strength=3,
            )
        )
        arms.append(
            arm(
                f"{prefix}_swing2",
                stop_atr_multiplier=stop,
                first_target_rr=1.5,
                final_target_rr=3.0,
                trailing_stop_swing_strength=2,
            )
        )
        arms.append(
            arm(
                f"{prefix}_sortie-unique",
                stop_atr_multiplier=stop,
                first_target_rr=1.5,
                final_target_rr=3.0,
                partial_exit_fractions=(),
                move_stop_to_breakeven_after_first_target=False,
            )
        )
        # Un objectif unique a 3 R, sans partiel ni break-even : la position vit ou meurt sur la
        # meme barriere, et c'est la seule config qui ne doit rien a la gestion de position.
        arms.append(
            arm(
                f"{prefix}_objectif-unique-3R",
                stop_atr_multiplier=stop,
                first_target_rr=3.0,
                final_target_rr=6.0,
                partial_exit_fractions=(),
                move_stop_to_breakeven_after_first_target=False,
            )
        )
        # Sortie minoritaire au premier objectif : 30 % encaisse tot, 70 % court apres TP2.
        arms.append(
            arm(
                f"{prefix}_partiel30-70",
                stop_atr_multiplier=stop,
                first_target_rr=1.5,
                final_target_rr=3.0,
                partial_exit_fractions=(0.3, 0.7),
            )
        )
    return arms


def decision_priority() -> list[Arm]:
    """Le lot de decision, **ordonne par valeur decisionnelle**, pas par commodite de boucle.

    Une mesure sur le jeu complet coute environ sept minutes de temps reel sur cette machine,
    et la machine est partagee avec trois autres axes : l'ordre dans lequel les configurations
    sont mesurees decide donc de ce qui sera su, si la serie doit s'arreter avant la fin.

    L'ordre repond, dans cet ordre, a quatre questions :

    1. la reference contractuelle de l'operateur (TP 0,8/1,5, stop 1,5) tient-elle sur le jeu
       complet, avec et sans gestion de position ?
    2. un stop plus serre, a objectifs larges (TP 1,5/3,0), franchit-il PF 1,0 ?
    3. la meme question a TP 0,8/1,5 — la geometrie que l'operateur a demandee ;
    4. le controle : la configuration a PF 0,990 sur 20 000 barres, sur le jeu complet.

    Toutes gardent `pullback_atr=0,4` et `entry_zone_atr=0,1` : le jeu de **signaux** est
    identique d'une ligne a l'autre, et seule la geometrie du stop et des objectifs change.
    """
    labels = [
        "ref_stop1.5_tp0.8-1.5_sortie-unique",
        "ref_stop1.5_tp0.8-1.5_partiel",
        "stop1.0_tp1.5-3.0_partiel",
        "stop0.8_tp1.5-3.0_partiel",
        "stop1.2_tp1.5-3.0_partiel",
        "ref_stop1.5_tp1.5-3.0_partiel",
        "stop0.8_tp0.8-1.5_partiel",
        "stop1.0_tp0.8-1.5_partiel",
        "stop0.6_tp1.5-3.0_partiel",
        "stop1.2_tp0.8-1.5_partiel",
        "stop0.5_tp1.5-3.0_partiel",
        "ref_stop2.0_tp1.5-3.0_partiel",
        "stop0.8_tp1.0-2.0_partiel",
        "stop1.0_tp1.0-2.0_partiel",
        "stop1.2_tp1.0-2.0_partiel",
        "stop0.5_tp0.8-1.5_partiel",
        "stop0.6_tp0.8-1.5_partiel",
    ]
    available = {a.label: a for a in decision_core()}
    missing = [label for label in labels if label not in available]
    if missing:
        raise SystemExit(f"libelles absents de decision_core : {missing}")
    return [available[label] for label in labels]


def decision_all() -> list[Arm]:
    """Le lot de decision complet, **ordonne par valeur decisionnelle**.

    Une mesure sur le jeu complet coute huit minutes de temps reel sur cette machine, partagee
    avec trois autres axes : l'ordre decide de ce qui sera su si la serie doit s'arreter.

    L'ordre arbitre entre deux signaux contradictoires, et c'est le but :

    * le diagnostic MAE/MFE (perdants a 1,192 R, gagnants a 0,978 R) dit « stop trop large » ;
    * l'exploration sur 4 999 barres dit l'inverse — a chaque paire de TP, le PF monte avec le
      stop, et la sortie unique bat le partiel + break-even sur 20 paires sur 20.

    Les deux ne peuvent pas avoir raison sur le jeu complet. Les configurations sont donc
    mesurees dans un ordre qui tranche tot : la reference contractuelle d'abord, puis le stop
    serre a objectifs larges, puis la famille « sortie unique » que l'exploration prefere.

    Toutes gardent `pullback_atr=0,4` et `entry_zone_atr=0,1` : le jeu de **signaux** est
    identique d'une ligne a l'autre, et seule la geometrie du stop et des objectifs change.
    """
    catalogue = {a.label: a for a in decision_core() + decision_second()}
    catalogue.update({a.label: a for a in decision_extra()})
    labels = [
        # 1. la reference contractuelle de l'operateur, ses deux variantes de gestion
        "ref_stop1.5_tp0.8-1.5_sortie-unique",
        "ref_stop1.5_tp0.8-1.5_partiel",
        # 2. le controle : la geometrie a PF 0,990 sur 20 000 barres, sur le jeu complet
        "ref_stop1.5_tp1.5-3.0_partiel",
        # 3. les geometries que l'exploration sur 4 999 barres place en tete (PF 1,09 a 1,34),
        #    et qui contredisent le diagnostic MAE/MFE : stop large et objectifs lointains
        "ref_stop2.0_tp2.0-4.0_partiel",
        "stop1.5_tp2.0-4.0_partiel",
        "ref_stop2.0_tp1.5-3.0_partiel",
        "stop1.5_tp3.0-6.0_partiel",
        # 4. le stop serre a objectifs larges — l'hypothese du diagnostic MAE/MFE
        "stop1.0_tp1.5-3.0_partiel",
        "stop0.8_tp1.5-3.0_partiel",
        "stop0.6_tp1.5-3.0_partiel",
        # 5. la famille « sortie unique », que l'exploration prefere sur 19 paires sur 20
        "stop1.5_tp1.5-3.0_sortie-unique",
        "stop1.2_tp1.0-2.0_sortie-unique",
        "stop1.2_tp2.0-4.0_partiel",
        "stop2.0_tp2.0-4.0_sortie-unique",
        "stop1.5_tp2.0-4.0_sortie-unique",
        "stop1.0_tp1.5-3.0_sortie-unique",
        # 6. le reste du balayage, si le temps le permet
        "stop1.2_tp1.5-3.0_partiel",
        "stop1.0_tp0.8-1.5_partiel",
        "stop0.8_tp0.8-1.5_partiel",
        "stop1.2_tp0.8-1.5_partiel",
        "stop1.0_tp1.0-2.0_partiel",
        "stop1.2_tp1.0-2.0_partiel",
    ]
    missing = [label for label in labels if label not in catalogue]
    if missing:
        raise SystemExit(f"libelles absents du catalogue : {missing}")
    seen: set[str] = set()
    ordered = [catalogue[label] for label in labels if not (label in seen or seen.add(label))]
    return ordered


def decision_extra() -> list[Arm]:
    """Les configurations que le lot initial n'avait pas prevues, et que l'exploration designe.

    Elles ne figurent pas dans `decision_core` parce qu'elles repondent a une observation
    posterieure : la famille « sortie unique » et les objectifs larges sortent en tete de
    l'exploration sur 4 999 barres, alors que le diagnostic MAE/MFE predisait l'inverse.
    """
    arms: list[Arm] = []
    for stop in (1.0, 1.2, 1.5, 2.0):
        arms.append(
            arm(
                f"stop{stop:.1f}_tp1.5-3.0_sortie-unique",
                stop_atr_multiplier=stop,
                first_target_rr=1.5,
                final_target_rr=3.0,
                partial_exit_fractions=(),
                move_stop_to_breakeven_after_first_target=False,
            )
        )
    for stop in (1.2, 1.5):
        arms.append(
            arm(
                f"stop{stop:.1f}_tp2.0-4.0_partiel",
                stop_atr_multiplier=stop,
                first_target_rr=2.0,
                final_target_rr=4.0,
            )
        )
        arms.append(
            arm(
                f"stop{stop:.1f}_tp2.0-4.0_sortie-unique",
                stop_atr_multiplier=stop,
                first_target_rr=2.0,
                final_target_rr=4.0,
                partial_exit_fractions=(),
                move_stop_to_breakeven_after_first_target=False,
            )
        )
    arms.append(
        arm(
            "stop2.0_tp2.0-4.0_sortie-unique",
            stop_atr_multiplier=2.0,
            first_target_rr=2.0,
            final_target_rr=4.0,
            partial_exit_fractions=(),
            move_stop_to_breakeven_after_first_target=False,
        )
    )
    arms.append(
        arm(
            "ref_stop2.0_tp2.0-4.0_partiel",
            stop_atr_multiplier=2.0,
            first_target_rr=2.0,
            final_target_rr=4.0,
        )
    )
    arms.append(
        arm(
            "stop1.5_tp1.0-2.0_sortie-unique",
            stop_atr_multiplier=1.5,
            first_target_rr=1.0,
            final_target_rr=2.0,
            partial_exit_fractions=(),
            move_stop_to_breakeven_after_first_target=False,
        )
    )
    arms.append(
        arm(
            "stop1.2_tp1.0-2.0_sortie-unique",
            stop_atr_multiplier=1.2,
            first_target_rr=1.0,
            final_target_rr=2.0,
            partial_exit_fractions=(),
            move_stop_to_breakeven_after_first_target=False,
        )
    )
    # Le meilleur PF de l'exploration a objectifs tres larges (1,1744 sur 4 999 barres, 49
    # trades) : il appartient a la meme famille que les deux precedents et doit etre tranche.
    arms.append(
        arm(
            "stop1.5_tp3.0-6.0_partiel",
            stop_atr_multiplier=1.5,
            first_target_rr=3.0,
            final_target_rr=6.0,
        )
    )
    return arms


GROUPS = {
    "stage1": stage1,
    "decision_core": decision_core,
    "decision_all": decision_all,
    "decision_priority": decision_priority,
    "decision_second": decision_second,
    "stage2_stops": stage2_stops,
    "stage2_roll": stage2_roll,
    "stage2_no_partial": stage2_no_partial,
    "stops": lambda: stop_grid(partial=True),
    "no_partial": lambda: stop_grid(partial=False),
}


def median(values: Sequence[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def measure(arm: Arm, candles: Sequence[Any], module: Any, costs: CostModel) -> dict[str, Any]:
    """Une mesure : les chiffres de la campagne, plus de quoi auditer le diagnostic MAE/MFE."""
    strategy = module.VwapPullback(module.VwapPullbackParameters(**arm.parameters))
    result = run_backtest(strategy, MANIFEST, {TIMEFRAME: candles}, arm.config(costs))
    performance = compute_performance(list(result.trades))
    wins = [t for t in result.trades if t.pnl_eur > 0]
    losses = [t for t in result.trades if t.pnl_eur < 0]
    return {
        "trades": performance.trades,
        "wins": performance.wins,
        "losses": performance.losses,
        "win_rate": None if performance.win_rate is None else round(float(performance.win_rate), 4),
        "net_eur": round(float(performance.net_profit), 2),
        "profit_factor": (
            None if performance.profit_factor is None else round(performance.profit_factor, 4)
        ),
        "max_drawdown_eur": round(float(performance.max_drawdown), 2),
        "signals": result.signals,
        "entries": result.entries,
        "expired_signals": result.expired_signals,
        "skipped_no_room": result.skipped_no_room,
        "forced_closures": result.forced_closures,
        "strategy_errors": list(result.strategy_errors[:3]),
        "winners_mfe_r_median": median([t.mfe_r for t in wins if t.mfe_r is not None]),
        "winners_mae_r_median": median([t.mae_r for t in wins if t.mae_r is not None]),
        "losers_mfe_r_median": median([t.mfe_r for t in losses if t.mfe_r is not None]),
        "losers_mae_r_median": median([t.mae_r for t in losses if t.mae_r is not None]),
        "average_win_eur": (
            None if performance.average_win is None else round(performance.average_win, 2)
        ),
        "average_loss_eur": (
            None if performance.average_loss is None else round(performance.average_loss, 2)
        ),
    }


def run_arm(arm: Arm, candles: Sequence[Any], module: Any, costs: CostModel) -> dict[str, Any]:
    started = time.perf_counter()
    row: dict[str, Any] = {
        "label": arm.label,
        "bars": len(candles),
        "parameters": arm.parameters,
        "harness": {
            "partial_exit_fractions": list(arm.partial_exit_fractions),
            "move_stop_to_breakeven_after_first_target": (
                arm.move_stop_to_breakeven_after_first_target
            ),
            "trailing_stop_swing_strength": arm.trailing_stop_swing_strength,
            "trailing_stop_atr": arm.trailing_stop_atr,
            "max_holding_bars": arm.max_holding_bars,
        },
    }
    try:
        row.update(measure(arm, candles, module, costs))
        row["status"] = "ok"
    except Exception as error:  # une configuration refusee est un resultat, pas un arret
        row["status"] = f"erreur: {type(error).__name__}: {error}"
    row["seconds"] = round(time.perf_counter() - started, 1)
    return row


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=sorted(GROUPS), default="stage1")
    parser.add_argument(
        "--bars",
        type=int,
        default=4999,
        help="nombre de barres prises en fin de jeu ; ignore si --slice est donne",
    )
    parser.add_argument(
        "--slice",
        dest="window",
        default=None,
        help="fenetre START:END en indices sur le jeu complet, ex. 0:30000 ou 30000:59999",
    )
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR / "axe-A-mesures.jsonl")
    parser.add_argument("--datasets", type=Path, default=DATASETS)
    parser.add_argument(
        "--only",
        default=None,
        help="filtre de sous-chaine sur le libelle, pour reprendre une serie interrompue",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="mesurer la source vivante au lieu de la copie figee (verification d'ecart)",
    )
    args = parser.parse_args()

    module = load_strategy_module(live=args.live)

    datasets = DatasetStore(args.datasets).load_all()
    if SYMBOL not in datasets:
        raise SystemExit(f"{SYMBOL} absent de {args.datasets} (trouves : {sorted(datasets)})")
    dataset = datasets[SYMBOL]
    if args.window:
        # Une fenetre explicite, pour trancher la question qui a coute le plus cher a cet axe :
        # un PF mesure sur un decoupage n'est pas un PF mesure sur le marche. Les moities de jeu
        # sont mesurees comme des marches differents, pas comme des confirmations.
        start_text, _, end_text = args.window.partition(":")
        window = slice(int(start_text), int(end_text) if end_text else None)
        candles = dataset.candles[window]
        window_label = f"indices {args.window} sur {dataset.bars}"
    else:
        candles = dataset.candles[-args.bars :]
        window_label = f"{len(candles)} dernieres barres sur {dataset.bars}"
    if len(candles) < 400:
        raise SystemExit(f"{len(candles)} bougie(s) : moins que les 400 barres d'historique")

    first, last = candles[0].open_time, candles[-1].open_time
    price = candles[0].close
    costs = CostModel(
        spread=round(price * 5e-5, 6),
        slippage_fixed=round(price * 2e-5, 6),
        commission_per_trade=Decimal("0.5"),
    )

    arms = [a for a in GROUPS[args.group]() if args.only is None or args.only in a.label]
    print(f"== {SYMBOL} {TIMEFRAME.value} : {len(candles)} barres, {window_label} ==")
    print(f"   {first.isoformat()} -> {last.isoformat()}")
    print(f"   prix de reference {price} ; spread {costs.spread} ; slippage {costs.slippage_fixed}")
    print(f"   {len(arms)} configuration(s) ; groupe {args.group}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if args.out.exists():
        for line in args.out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            previous = json.loads(line)
            # Une mesure dont la regle n'a produit aucun signal est une source cassee, pas un
            # resultat : elle est refaite plutot que comptee. La cle inclut la fenetre, sinon
            # deux decoupages de meme longueur se confondraient. Les lignes ecrites avant que la
            # fenetre ne soit nommee (`window` absent) sont lues sur leur seule longueur.
            same_window = previous.get("window") == window_label or (
                previous.get("window") is None and previous.get("bars") == len(candles)
            )
            if (
                same_window
                and previous.get("status") == "ok"
                and (previous.get("signals") or 0) > 0
            ):
                done.add(str(previous["label"]))
        if done:
            print(f"   deja mesure sur cette fenetre : {len(done)} configuration(s)")

    with args.out.open("a", encoding="utf-8") as handle:
        for number, candidate in enumerate(arms, start=1):
            if candidate.label in done:
                print(f"   [{number}/{len(arms)}] {candidate.label} : deja mesure, saute")
                continue
            row = run_arm(candidate, candles, module, costs)
            row["window"] = window_label
            row["window_first"] = first.isoformat()
            row["window_last"] = last.isoformat()
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(
                f"   [{number}/{len(arms)}] {candidate.label:<42} "
                f"trades {row.get('trades')} reussite {row.get('win_rate')} "
                f"net {row.get('net_eur')} PF {row.get('profit_factor')} "
                f"({row['seconds']}s) {row['status']}"
            )

    print(f"== mesures ecrites dans {args.out} ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__: Sequence[str] = [
    "BASE",
    "GROUPS",
    "MANIFEST",
    "PINNED",
    "Arm",
    "arm",
    "decision_core",
    "decision_second",
    "load_strategy_module",
    "main",
    "measure",
    "run_arm",
    "stage1",
    "stage2_no_partial",
    "stage2_roll",
    "stage2_stops",
    "stop_grid",
]
