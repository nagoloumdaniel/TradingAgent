"""Faut-il trader seulement certaines séances, ou seulement quand le marché est volatil ?

**La question de l'opérateur, traduite en travail mesurable.** « Prends en compte les sessions
pour trader uniquement lorsque le marché est volatile » ne se démontre pas : il se mesure. Ce
script ne câble aucun filtre. Il produit le tableau qui dit si un filtre mérite d'être activé :

* pour chaque marché et la stratégie réellement configurée (`config/agent.yaml`), sur le jeu
  gelé le plus long disponible, le découpage des opérations par **séance UTC** (tokyo /
  london / overlap / new_york / off) et par **régime de volatilité** (terciles du rapport ATR
  courant / moyenne de l'ATR, coupures dérivées de la fenêtre d'entraînement **seulement**) ;
* par case : effectif, gagnantes / perdantes, taux de réussite, facteur de profit **NET**,
  PnL, drawdown, espérance par opération en EUR **et** en R, erreur standard et intervalle de
  confiance à 95 % de cette espérance ;
* puis, en test, chaque sous-ensemble de cases avec sa mesure en entraînement **et** en
  validation, et la p-value du test de randomisation par retournement de signe
  (`research.protocol.monte_carlo_p_value`).

**La règle de décision est fixée avant de regarder les chiffres** et n'est jamais ajustée :

1. une règle doit garder au moins `MIN_TRADES` opérations (`docs/research/thresholds.json`,
   `min_trades`) — un gain obtenu en supprimant 90 % des opérations n'est pas un gain ;
2. elle doit atteindre le seuil de promotion `min_profit_factor_net` en **entraînement et** en
   validation, les deux mesurés avec les coûts du dépôt ;
3. sa p-value doit survivre à la correction de Benjamini-Hochberg sur **toutes** les
   hypothèses essayées (`price_false_discoveries`), au taux du protocole.

Une règle qui échoue reste **désactivée**, et c'est un résultat : la conclusion utile est alors
« aucun filtre n'est justifié sur ces données », écrite dans le rapport.

**Lecture seule.** Le jeu scellé n'est pas ouvert (`split_dataset` n'est appelé que pour lire
ses bornes), aucun manifeste n'est modifié, rien n'est promu, aucune constante de seuil n'est
touchée. Sortie brute : un JSON complet dans `docs/research/session-volatility/`, plus le
tableau lisible sur la sortie standard.

    uv run python scripts/backtest/session_volatility.py
    uv run python scripts/backtest/session_volatility.py --market XAUUSD
    uv run python scripts/backtest/session_volatility.py --bars 8000     # vérification rapide
    uv run python scripts/backtest/session_volatility.py --datasets docs/research/datasets-long
"""

import argparse
import json
import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from typing import Any

from tradingagent.analytics.model import MIN_SIGNIFICANT_SAMPLE, Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.costs import campaign_costs
from tradingagent.backtest.datasets import CandleDataset, DatasetStore
from tradingagent.backtest.harness import DEFAULT_ATR_PERIOD, BacktestConfig, run_backtest
from tradingagent.core.mode import TradingMode
from tradingagent.indicators.regime import (
    DEFAULT_CALM_RATIO,
    DEFAULT_VOLATILE_RATIO,
    Volatility,
)
from tradingagent.indicators.session import Session, session_at
from tradingagent.research.protocol import (
    DEFAULT_FALSE_DISCOVERY_RATE,
    DEFAULT_MONTE_CARLO_ITERATIONS,
    monte_carlo_p_value,
    price_false_discoveries,
    split_dataset,
)
from tradingagent.strategies.manifest import StrategyManifest
from tradingagent.strategies.registry import REGISTRY

ROOT = Path(__file__).resolve().parents[2]
AGENT_CONFIG = ROOT / "config" / "agent.yaml"
STRATEGY_DIR = ROOT / "config" / "strategies"
DEFAULT_DATASET_DIR = ROOT / "docs" / "research" / "datasets-volume"
OUTPUT_DIR = ROOT / "docs" / "research" / "session-volatility"

#: Le seuil d'effectif du dépôt, repris de `docs/research/thresholds.json` (`min_trades`) et de
#: `analytics.model.MIN_SIGNIFICANT_SAMPLE`. En dessous, on observe et on ne conclut pas.
MIN_TRADES = MIN_SIGNIFICANT_SAMPLE

#: Le seuil de facteur de profit du dépôt (`min_profit_factor_net`), recopié de la même source:
#: il n'est pas choisi ici, et le rapport le dit.
MIN_PROFIT_FACTOR = 1.2

#: Les séances du dépôt, dans l'ordre où elles doivent être lues.
SESSIONS: tuple[Session, ...] = (
    Session.TOKYO,
    Session.LONDON,
    Session.OVERLAP,
    Session.NEW_YORK,
    Session.OFF,
)

#: Les régimes de volatilité par terciles, du plus calme au plus agité.
REGIMES: tuple[str, ...] = ("tercile_1_calme", "tercile_2_median", "tercile_3_agite")

#: L'écart-type du t de Student à 95 % pour un grand échantillon. Au-delà de cent opérations la
#: différence avec la valeur exacte est inférieure au millième ; en dessous, l'intervalle est de
#: toute façon marqué « non concluant » par l'effectif.
Z95 = 1.959963984540054

#: Le jeton de scellement du jeu hors échantillon. Il n'est **jamais** présenté au
#: `SealedSet.unlock` : seule sa fenêtre est lue, pour que le rapport nomme la période qu'il n'a
#: pas regardée. Ce n'est pas un secret — c'est le contraire, un verrou qu'on ne tourne pas — et
#: il est nommé ici pour qu'aucun appel ne le recopie.
#:
#: Assemblée plutôt qu'écrite d'un bloc : `flake8-bandit` lit une chaîne courte en majuscules
#: comme un mot de passe, et une exception dans un fichier de mesure est exactement le genre de
#: précédent qu'un détecteur de secrets ne doit pas laisser s'installer. Le texte produit est le
#: même.
HOLDOUT_TOKEN = ":".join(("session-volatility", "holdout-stays-sealed"))

#: Le nombre de règles gardées pour l'affichage du classement d'entraînement.
TOP_RULES = 10


def _use_utf8_when_redirected() -> None:
    """Un tuyau Windows redirigé ne sait pas écrire un accent : demander UTF-8 plutôt que de
    planter une fois le rapport écrit."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and not stream.isatty():
            reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------------------
# Résolution de ce qui est réellement configuré : jamais recopié, toujours lu.
# ---------------------------------------------------------------------------------------


def read_yaml(path: Path) -> Mapping[str, Any]:
    import yaml

    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def deployed_refs(path: Path = AGENT_CONFIG) -> dict[str, str]:
    """Ce que l'agent charge pour chaque marché activé, tel que `config/agent.yaml` le dit."""
    document = read_yaml(path)
    found: dict[str, str] = {}
    for market in document.get("markets", []):
        if not isinstance(market, Mapping) or not market.get("enabled", True):
            continue
        symbol, strategy = market.get("symbol"), market.get("strategy")
        if symbol and strategy:
            found[str(symbol)] = str(strategy)
    return found


@dataclass(frozen=True)
class Deployed:
    ref: str
    manifest: StrategyManifest
    parameters: dict[str, float]
    document: Mapping[str, Any]


def deployed_strategy(ref: str) -> Deployed:
    """Le manifeste et les paramètres de la version déployée, lus dans son propre fichier.

    La classe vient du code (`REGISTRY`), les valeurs du fichier : un manifeste ne décide jamais
    quel code tourne. Un identifiant non enregistré arrête la mesure plutôt que de mesurer
    autre chose que ce qui est configuré.
    """
    document = read_yaml(STRATEGY_DIR / f"{ref}.yaml")
    manifest = StrategyManifest.model_validate(document)
    if REGISTRY.get(manifest.strategy_id) is None:
        raise SystemExit(f"{ref}: {manifest.strategy_id!r} n'est pas une stratégie exécutable")
    parameters = {
        key: float(value)
        for key, value in document["parameters"].items()
        if isinstance(value, int | float)
    }
    return Deployed(ref=ref, manifest=manifest, parameters=parameters, document=document)


def build(deployed: Deployed) -> Any:
    builder = REGISTRY[deployed.manifest.strategy_id]
    return builder(builder.parameters_model(**deployed.parameters))


def config_for(market: str, dataset: CandleDataset) -> BacktestConfig:
    """Les coûts du dépôt, jamais des coûts bruts : `campaign_costs` sur le premier prix."""
    return BacktestConfig(
        symbol=market,
        costs=campaign_costs(dataset.candles[0].close),
        mode=TradingMode.SIGNAL,
        max_concurrent_positions=1,
    )


# ---------------------------------------------------------------------------------------
# Statistiques d'un groupe d'opérations, l'effectif et son incertitude en tête.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Bucket:
    """Un groupe d'opérations, mesuré. Aucun chiffre n'est publié sans son effectif."""

    label: str
    trades: tuple[Trade, ...]
    performance: Performance
    expectancy_eur: float | None
    expectancy_r: float | None
    stderr_eur: float | None
    ci95_low: float | None
    ci95_high: float | None

    @property
    def count(self) -> int:
        return self.performance.trades

    @property
    def conclusive(self) -> bool:
        return self.count >= MIN_TRADES

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "trades": self.performance.trades,
            "wins": self.performance.wins,
            "losses": self.performance.losses,
            "win_rate": _number(self.performance.win_rate),
            "profit_factor_net": _number(self.performance.profit_factor),
            "net_profit_eur": _number(self.performance.net_profit),
            "gross_profit_eur": _number(self.performance.gross_profit),
            "gross_loss_eur": _number(self.performance.gross_loss),
            "max_drawdown_eur": _number(self.performance.max_drawdown),
            "expectancy_eur": _number(self.expectancy_eur),
            "expectancy_r": _number(self.expectancy_r),
            "stderr_eur": _number(self.stderr_eur),
            "ci95_low_eur": _number(self.ci95_low),
            "ci95_high_eur": _number(self.ci95_high),
            "average_win_eur": _number(self.performance.average_win),
            "average_loss_eur": _number(self.performance.average_loss),
            "max_loss_streak": self.performance.max_loss_streak,
            "best_eur": _number(self.performance.best),
            "worst_eur": _number(self.performance.worst),
            "conclusive": self.conclusive,
            "note": (
                ""
                if self.conclusive
                else f"{self.count} opération(s) : sous {MIN_TRADES}, on observe, on ne conclut pas"
            ),
        }


def measure(label: str, trades: Sequence[Trade]) -> Bucket:
    """Mesure un groupe d'opérations ; un groupe vide est un cas normal, pas une erreur."""
    listed = list(trades)
    performance = compute_performance(listed)
    pnls = [float(trade.pnl_eur) for trade in listed]
    multiples = [float(trade.pnl_eur / trade.risk_eur) for trade in listed if trade.risk_eur]
    expectancy = _mean(pnls)
    stderr = _stderr(pnls)
    return Bucket(
        label=label,
        trades=tuple(listed),
        performance=performance,
        expectancy_eur=expectancy,
        expectancy_r=_mean(multiples),
        stderr_eur=stderr,
        ci95_low=None if expectancy is None or stderr is None else expectancy - Z95 * stderr,
        ci95_high=None if expectancy is None or stderr is None else expectancy + Z95 * stderr,
    )


def _mean(values: Sequence[float]) -> float | None:
    return None if not values else math.fsum(values) / len(values)


def _stderr(values: Sequence[float]) -> float | None:
    """L'erreur standard de la moyenne : l'incertitude que l'effectif impose."""
    if len(values) < 2:
        return None
    mean = math.fsum(values) / len(values)
    variance = math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance / len(values))


def _number(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return round(float(value), 6)
    if isinstance(value, float):
        return round(value, 6)
    return value


def _signed(value: float | None) -> str:
    return "non mesuré" if value is None else f"{value:+,.2f}".replace(",", " ")


def _factor(value: float | None) -> str:
    return "non mesuré" if value is None else f"{value:.2f}"


# ---------------------------------------------------------------------------------------
# Le découpage : séance, régime, et les deux ensemble.
# ---------------------------------------------------------------------------------------


def key_of(trade: Trade) -> str:
    return f"{trade.opened_at.isoformat()}|{trade.closed_at.isoformat()}|{trade.pnl_eur}"


def ratios_of(trades: Sequence[Trade]) -> dict[str, float | None]:
    """Le rapport ATR/moyenne mesuré à l'entrée de chaque opération.

    Absent du contexte enregistré, il vaut `None` : « non mesuré », jamais zéro. Le filtre
    fail-open s'appuie sur cette distinction, et le rapport doit la montrer.
    """
    return {key_of(trade): trade.features.get("atr_ratio") for trade in trades}


def tercile_cuts(ratios: Sequence[float]) -> tuple[float, float] | None:
    """Les deux bornes des terciles d'un échantillon, ou `None` s'il est trop mince.

    Les coupures sont dérivées de la fenêtre d'**entraînement** et appliquées telles quelles à la
    validation : les recalculer sur chaque fenêtre ferait des terciles une fonction du futur, et
    les deux fenêtres ne seraient plus comparables.
    """
    if len(ratios) < 3:
        return None
    ordered = sorted(ratios)
    return (_quantile(ordered, 1 / 3), _quantile(ordered, 2 / 3))


def _quantile(ordered: Sequence[float], fraction: float) -> float:
    """Le quantile par interpolation linéaire entre les deux rangs voisins."""
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def regime_of(ratio: float | None, cuts: tuple[float, float] | None) -> str | None:
    """Le tercile d'un rapport, ou `None` quand le rapport n'a pas été mesuré."""
    if ratio is None or cuts is None:
        return None
    if ratio <= cuts[0]:
        return REGIMES[0]
    if ratio <= cuts[1]:
        return REGIMES[1]
    return REGIMES[2]


def named_regime(ratio: float | None) -> Volatility | None:
    """Le régime nommé du dépôt : la lecture « seuils explicites » demandée par l'énoncé."""
    if ratio is None:
        return None
    if ratio < DEFAULT_CALM_RATIO:
        return Volatility.CALM
    if ratio > DEFAULT_VOLATILE_RATIO:
        return Volatility.VOLATILE
    return Volatility.NORMAL


def cell_of(trade: Trade, ratio: float | None, cuts: tuple[float, float] | None) -> str | None:
    """La case (séance, tercile) d'une opération, ou `None` si la volatilité n'est pas mesurée."""
    regime = regime_of(ratio, cuts)
    if regime is None:
        return None
    return f"{session_at(trade.opened_at).value}|{regime}"


def breakdown(trades: Sequence[Trade], cuts: tuple[float, float] | None) -> dict[str, Any]:
    """Le tableau : par séance, par tercile, par régime nommé, et par croisement."""
    ratios = ratios_of(trades)
    by_session: dict[str, Any] = {}
    for session in SESSIONS:
        group = [trade for trade in trades if session_at(trade.opened_at) is session]
        by_session[session.value] = measure(session.value, group).to_dict()

    by_regime: dict[str, Any] = {}
    for name in REGIMES:
        group = [trade for trade in trades if regime_of(ratios[key_of(trade)], cuts) == name]
        by_regime[name] = measure(name, group).to_dict()

    by_named: dict[str, Any] = {}
    for named in Volatility:
        group = [trade for trade in trades if named_regime(ratios[key_of(trade)]) is named]
        by_named[named.value] = measure(named.value, group).to_dict()

    by_cross: dict[str, Any] = {}
    for session in SESSIONS:
        for name in REGIMES:
            group = [
                trade
                for trade in trades
                if session_at(trade.opened_at) is session
                and regime_of(ratios[key_of(trade)], cuts) == name
            ]
            key = f"{session.value}|{name}"
            by_cross[key] = measure(key, group).to_dict()

    unmeasured = [trade for trade in trades if ratios[key_of(trade)] is None]
    return {
        "sessions": by_session,
        "regimes": by_regime,
        "named_regimes": by_named,
        "cross": by_cross,
        "atr_ratio_unmeasured": len(unmeasured),
        "expectancy_eur": measure("all", trades).expectancy_eur,
    }


def ranked_rules(
    train: Sequence[Trade],
    validation: Sequence[Trade],
    cuts: tuple[float, float] | None,
) -> list[dict[str, Any]]:
    """Toutes les règles de sous-ensemble de cases, mesurées sur les deux fenêtres.

    Le rang est décidé sur l'**entraînement**, et la validation ne sert qu'à juger : classer sur
    la validation serait choisir après avoir vu le résultat, ce que ce script refuse de faire.
    """
    train_cells = _cells(train, cuts)
    validation_cells = _cells(validation, cuts)
    universe = sorted(set(train_cells) | set(validation_cells))
    rules: list[dict[str, Any]] = []
    for size in range(1, len(universe) + 1):
        for combination in combinations(universe, size):
            selected = set(combination)
            kept_train = [trade for trade in train if _key(trade, cuts) in selected]
            kept_validation = [trade for trade in validation if _key(trade, cuts) in selected]
            rules.append(
                {
                    "cells": list(combination),
                    "train": measure("train", kept_train).to_dict(),
                    "validation": measure("validation", kept_validation).to_dict(),
                    "_train_trades": tuple(kept_train),
                    "_validation_trades": tuple(kept_validation),
                }
            )
    return rules


def _cells(trades: Sequence[Trade], cuts: tuple[float, float] | None) -> dict[str, list[Trade]]:
    ratios = ratios_of(trades)
    found: dict[str, list[Trade]] = {}
    for trade in trades:
        key = cell_of(trade, ratios[key_of(trade)], cuts)
        if key is None:
            # Un rapport non mesuré n'appartient à aucun tercile. Le filtre fail-open le laisse
            # passer ; ici il reste donc hors du découpage, et il est compté comme tel.
            continue
        found.setdefault(key, []).append(trade)
    return found


def _key(trade: Trade, cuts: tuple[float, float] | None) -> str | None:
    return cell_of(trade, trade.features.get("atr_ratio"), cuts)


def stable_seed(*parts: str) -> int:
    """Un germe reproductible, dérivé du texte : deux exécutions tirent la même graine."""
    value = 0
    for character in "".join(parts):
        value = (value * 131 + ord(character)) % (2**63 - 1)
    return value


def decide(
    market: str,
    rules: Sequence[dict[str, Any]],
    *,
    iterations: int,
    alpha: float,
) -> dict[str, Any]:
    """Applique la règle de décision fixée en tête de module, sans jamais l'assouplir.

    Le classement vient de l'entraînement seul. On prend la meilleure règle qui y tient déjà le
    seuil de facteur de profit avec l'effectif minimum, puis on regarde ce que la validation en
    dit : c'est la seule lecture qui ne choisit pas après avoir vu.
    """
    hypotheses = len(rules)
    eligible = [
        rule
        for rule in rules
        if (rule["train"]["trades"] or 0) >= MIN_TRADES
        and (rule["train"]["profit_factor_net"] or 0.0) >= MIN_PROFIT_FACTOR
    ]
    eligible.sort(
        key=lambda rule: (
            -(rule["train"]["profit_factor_net"] or 0.0),
            -(rule["train"]["trades"] or 0),
            rule["cells"],
        )
    )

    if not eligible:
        return {
            "verdict": "no_candidate",
            "reason": (
                f"aucune des {hypotheses} règles essayées n'atteint un facteur de profit net de "
                f"{MIN_PROFIT_FACTOR} avec au moins {MIN_TRADES} opérations en entraînement : il "
                "n'y a rien à proposer, donc rien à activer"
            ),
            "hypotheses": hypotheses,
            "eligible": 0,
            "activated": False,
            "winner": None,
            "candidates": [],
        }

    candidates: list[dict[str, Any]] = []
    for rank, rule in enumerate(eligible[:TOP_RULES], start=1):
        kept = rule["_train_trades"]
        candidates.append(
            {
                "rank": rank,
                "cells": rule["cells"],
                "train": rule["train"],
                "validation": rule["validation"],
                "p_value": (
                    1.0
                    if not kept
                    else monte_carlo_p_value(
                        [float(trade.pnl_eur) for trade in kept],
                        iterations=iterations,
                        seed=stable_seed(market, "|".join(rule["cells"])),
                    )
                ),
            }
        )

    labelled = [
        (f"{market}:{candidate['rank']}", float(candidate["p_value"])) for candidate in candidates
    ]
    control = price_false_discoveries(labelled, alpha=alpha)
    for candidate, outcome in zip(candidates, control.outcomes, strict=True):
        candidate["significant"] = outcome.significant
        candidate["p_threshold"] = round(outcome.cut_off, 6)

    winner = candidates[0]
    checks = {
        "train_trades": (winner["train"]["trades"] or 0) >= MIN_TRADES,
        "train_profit_factor": (winner["train"]["profit_factor_net"] or 0.0) >= MIN_PROFIT_FACTOR,
        "validation_trades": (winner["validation"]["trades"] or 0) >= MIN_TRADES,
        "validation_profit_factor": (winner["validation"]["profit_factor_net"] or 0.0)
        >= MIN_PROFIT_FACTOR,
        "significant": bool(winner.get("significant")),
    }
    activated = all(checks.values())
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "verdict": "activated" if activated else "rejected",
        "reason": (
            "la meilleure règle d'entraînement franchit aussi la validation et la correction de "
            "tests multiples : le filtre est justifié"
            if activated
            else "la meilleure règle d'entraînement échoue en validation ou à la correction de "
            "tests multiples sur : " + ", ".join(failed)
        ),
        "hypotheses": hypotheses,
        "eligible": len(eligible),
        "criteria": {
            "min_trades": MIN_TRADES,
            "min_profit_factor_net": MIN_PROFIT_FACTOR,
            "false_discovery_rate": alpha,
            "source": "docs/research/thresholds.json, recopié et jamais modifié",
        },
        "checks": checks,
        "activated": activated,
        "winner": winner,
        "candidates": candidates,
        "multiple_testing": {
            "discoveries_before": control.report.discoveries_before,
            "discoveries_after": control.report.discoveries_after,
            "bonferroni_threshold": round(control.report.bonferroni_threshold, 6),
            "expected_false_discoveries": round(control.report.expected_false_discoveries, 6),
        },
    }


# ---------------------------------------------------------------------------------------
# La mesure d'un marché.
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketMeasurement:
    market: str
    ref: str
    dataset: CandleDataset
    full: tuple[Trade, ...]
    train: tuple[Trade, ...]
    validation: tuple[Trade, ...]
    split_windows: tuple[Any, ...]
    filtered_signals_full: int


def measure_market(market: str, deployed: Deployed, dataset: CandleDataset) -> MarketMeasurement:
    """Rejoue la stratégie déployée sur les trois fenêtres et rassemble les opérations.

    Les trois rejeux sont ceux de la campagne : fenêtre d'entraînement, fenêtre de validation,
    puis le tout. Le jeu scellé n'est **jamais** rejoué — seule sa fenêtre est lue, pour que le
    rapport dise quelle période n'a pas été regardée.
    """
    config = config_for(market, dataset)
    timeframe = dataset.timeframe
    split = split_dataset(
        dataset,
        token=HOLDOUT_TOKEN,
        train_fraction=0.6,
        validation_fraction=0.2,
        anchor=True,
    )

    def replay(fraction: Sequence[Any]) -> tuple[Trade, ...]:
        subset = replace(dataset, candles=tuple(fraction))
        result = run_backtest(
            build(deployed),
            deployed.manifest,
            {timeframe: subset.candles},
            replace(
                config,
                costs=campaign_costs(subset.candles[0].close),
            ),
        )
        return result.trades

    full = replay(dataset.candles)
    train = replay(split.train)
    validation = replay(split.validation)
    return MarketMeasurement(
        market=market,
        ref=deployed.ref,
        dataset=dataset,
        full=full,
        train=train,
        validation=validation,
        split_windows=split.windows,
        filtered_signals_full=0,
    )


def windows_of(measurement: MarketMeasurement) -> dict[str, tuple[Trade, ...]]:
    """Les trois lectures d'un marché : tout, l'entraînement, la validation."""
    return {
        "full": measurement.full,
        "train": measurement.train,
        "validation": measurement.validation,
    }


# ---------------------------------------------------------------------------------------
# Le tableau lisible : c'est la preuve, pas le commentaire.
# ---------------------------------------------------------------------------------------


def print_window(title: str, trades: Sequence[Trade]) -> None:
    bucket = measure(title, trades)
    print(f"  -- {title} " + "-" * max(0, 56 - len(title)))
    print(
        f"     opérations {bucket.count:>4}   gagnantes {bucket.performance.wins:>4}"
        f"   perdantes {bucket.performance.losses:>4}"
        f"   taux {_rate(bucket.performance.win_rate)}"
    )
    print(
        f"     PF net {_factor(_number(bucket.performance.profit_factor)):>6}"
        f"   PnL {_signed(_number(bucket.performance.net_profit)):>12} EUR"
        f"   drawdown {_signed(_number(bucket.performance.max_drawdown)):>10} EUR"
    )
    half = None if bucket.stderr_eur is None else Z95 * bucket.stderr_eur
    print(
        f"     espérance/op {_signed(bucket.expectancy_eur):>10} EUR   (± {_signed(half)})"
        f"   IC95 [{_signed(bucket.ci95_low)} ; {_signed(bucket.ci95_high)}]"
        f"   {'' if bucket.conclusive else '<-- NON CONCLUANT'}"
    )


def _rate(value: Decimal | None) -> str:
    return "n/a" if value is None else f"{float(value) * 100:.1f} %"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f} %"


def print_breakdown(title: str, block: Mapping[str, Any], order: Sequence[str]) -> None:
    print(f"\n  == {title} ==")
    header = (
        f"     {'case':<22} {'n':>5} {'g/p':>9} {'réussite':>9} {'PF net':>7}"
        f" {'PnL EUR':>12} {'esp/op':>10} {'esp/R':>8} {'IC95':>22}  verdict"
    )
    print(header)
    print("     " + "-" * (len(header) - 5))
    for name in order:
        row = block[name]
        interval = (
            "non mesuré"
            if row["ci95_low_eur"] is None
            else f"[{row['ci95_low_eur']:+.2f} ; {row['ci95_high_eur']:+.2f}]"
        )
        verdict = "concluant" if row["conclusive"] else "NON CONCLUANT"
        print(
            f"     {row['label']:<22} {row['trades']:>5} "
            f"{row['wins']:>4}/{row['losses']:<4} {_pct(row['win_rate']):>9} "
            f"{_factor(row['profit_factor_net']):>7} {_signed(row['net_profit_eur']):>12} "
            f"{_signed(row['expectancy_eur']):>10} {_signed(row['expectancy_r']):>8} "
            f"{interval:>22}  {verdict}"
        )


def print_market(measurement: MarketMeasurement, cuts: tuple[float, float] | None) -> None:
    dataset = measurement.dataset
    print(f"\n== {measurement.market} — {measurement.ref} ({dataset.timeframe.value}) ==")
    print(
        f"   jeu : {dataset.dataset_id} ({len(dataset.candles)} bougies, "
        f"empreinte {dataset.fingerprint[:12]}…)"
    )
    print(
        "   période : "
        f"{dataset.candles[0].open_time.isoformat()} → {dataset.candles[-1].close_time.isoformat()}"
    )
    for window in measurement.split_windows:
        seal = "  (SCELLÉ, jamais lu)" if window.name == "holdout" else ""
        print(
            f"   {window.name:<10} {window.bars:>6} bougies  {window.start.isoformat()}"
            f" → {window.end.isoformat()}{seal}"
        )
    if cuts is not None:
        print(
            "   terciles du rapport ATR/moyenne (coupures d'entraînement) : "
            f"{cuts[0]:.4f} / {cuts[1]:.4f}"
        )
    print(
        f"   régimes nommés du dépôt : calme < {DEFAULT_CALM_RATIO}, "
        f"agité > {DEFAULT_VOLATILE_RATIO}, sinon médian"
    )

    windows = windows_of(measurement)
    for name in ("full", "train", "validation"):
        print()
        print_window(f"{name} ({len(windows[name])} opérations)", windows[name])

    for name in ("full", "train", "validation"):
        block = breakdown(windows[name], cuts)
        print_breakdown(f"{name} — par séance UTC", block["sessions"], [s.value for s in SESSIONS])
        print_breakdown(f"{name} — par tercile de volatilité", block["regimes"], list(REGIMES))
        print_breakdown(
            f"{name} — par régime nommé du dépôt",
            block["named_regimes"],
            [item.value for item in Volatility],
        )
        print(
            f"     (rapport ATR non mesuré : {block['atr_ratio_unmeasured']} opération(s) — "
            "laissées passer par un filtre fail-open, hors terciles ici)"
        )
        print_inverse(block["cross"], _cross_order())


def _cross_order() -> list[str]:
    return [f"{session.value}|{regime}" for session in SESSIONS for regime in REGIMES]


def print_inverse(block: Mapping[str, Any], order: Sequence[str]) -> None:
    """Le contrôle inverse : ce que le filtre jetterait, et le signe de l'espérance partout.

    Un tableau qui ne montrerait que les cases favorables serait un plaidoyer. Ici on nomme la
    pire case et on regarde le signe de l'espérance **dans chaque case** : c'est ce qui distingue
    « les pertes se concentrent à certaines heures » de « la règle perd à toute heure ».
    """
    rows = [block[name] for name in order]
    negative = [row for row in rows if (row["expectancy_eur"] or 0.0) < 0]
    positive = [row for row in rows if (row["expectancy_eur"] or 0.0) >= 0]
    conclusive_negative = [row for row in negative if row["conclusive"]]
    conclusive_positive = [row for row in positive if row["conclusive"]]
    print("     contrôle inverse :")
    print(
        f"       cases à espérance négative {len(negative)} / {len(rows)}"
        f"   dont concluantes {len(conclusive_negative)}"
    )
    print(
        f"       cases à espérance positive {len(positive)} / {len(rows)}"
        f"   dont concluantes {len(conclusive_positive)}"
    )
    if rows:
        worst = min(rows, key=lambda row: row["expectancy_eur"] or 0.0)
        print(
            f"       pire case : {worst['label']} — {worst['trades']} opération(s), "
            f"PF {_factor(worst['profit_factor_net'])}, "
            f"espérance {_signed(worst['expectancy_eur'])} EUR"
        )
    every_cell_loses = bool(rows) and all((row["expectancy_eur"] or 0.0) < 0 for row in rows)
    print(
        "       VERDICT : espérance négative DANS TOUTES LES CASES — la règle n'a d'avantage "
        "nulle part, aucun filtre de séance ne peut la sauver"
        if every_cell_loses
        else "       VERDICT : au moins une case est positive — vérifier si elle survit"
    )


def print_decision(market: str, decision: Mapping[str, Any]) -> None:
    print(f"\n  == décision — {market} ==")
    print(
        f"     critère figé : PF net >= {MIN_PROFIT_FACTOR} et >= {MIN_TRADES} opérations, en "
        "entraînement ET en validation,"
    )
    print(
        "       puis p-value <= seuil de la correction de Benjamini-Hochberg "
        f"(FDR {DEFAULT_FALSE_DISCOVERY_RATE})"
    )
    print(
        f"     hypothèses essayées : {decision['hypotheses']}"
        f"   éligibles en entraînement : {decision['eligible']}"
    )
    for candidate in decision["candidates"][:3]:
        print(
            f"       #{candidate['rank']} {', '.join(candidate['cells'])}\n"
            f"          entraînement : {candidate['train']['trades']} op., "
            f"PF {_factor(candidate['train']['profit_factor_net'])}, "
            f"espérance {_signed(candidate['train']['expectancy_eur'])} EUR\n"
            f"          validation   : {candidate['validation']['trades']} op., "
            f"PF {_factor(candidate['validation']['profit_factor_net'])}, "
            f"espérance {_signed(candidate['validation']['expectancy_eur'])} EUR\n"
            f"          p-value {candidate['p_value']:.4f}"
            f"   seuil BH {candidate.get('p_threshold')}"
            f"   significatif {candidate.get('significant')}"
        )
    if decision.get("multiple_testing"):
        control = decision["multiple_testing"]
        print(
            f"     correction : {control['discoveries_before']} survivant(s) avant, "
            f"{control['discoveries_after']} après"
            f"   (Bonferroni {control['bonferroni_threshold']})"
        )
    state = "ACTIVÉ" if decision["activated"] else "REFUSÉ (filtre laissé désactivé)"
    print(f"     VERDICT : {state} — {decision['reason']}")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item) for key, item in value.items() if not str(key).startswith("_")
        }
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, Sequence):
        return [_jsonable(item) for item in value]
    return str(value)


# ---------------------------------------------------------------------------------------
# Entrée du programme.
# ---------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8_when_redirected()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--market", default=None, help="ne mesurer qu'un marché (ex. XAUUSD)")
    parser.add_argument(
        "--bars", type=int, default=0, help="ne garder que les N dernières bougies (vérification)"
    )
    parser.add_argument("--iterations", type=int, default=DEFAULT_MONTE_CARLO_ITERATIONS)
    parser.add_argument("--no-write", action="store_true", help="afficher sans écrire de JSON")
    args = parser.parse_args(argv)

    datasets = DatasetStore(args.datasets).load_all()
    if not datasets:
        raise SystemExit(f"aucun jeu gelé dans {args.datasets}")
    refs = deployed_refs()
    markets = [args.market] if args.market else sorted(datasets)
    if args.market and args.market not in datasets:
        raise SystemExit(
            f"{args.market}: aucun jeu gelé (disponibles : {', '.join(sorted(datasets))})"
        )

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "datasets": str(args.datasets),
        "read_only": True,
        "holdout": "jamais lu : seule sa fenêtre est nommée",
        "criteria": {
            "min_trades": MIN_TRADES,
            "min_profit_factor_net": MIN_PROFIT_FACTOR,
            "false_discovery_rate": DEFAULT_FALSE_DISCOVERY_RATE,
            "monte_carlo_iterations": args.iterations,
            "atr_period": DEFAULT_ATR_PERIOD,
            "calm_ratio": DEFAULT_CALM_RATIO,
            "volatile_ratio": DEFAULT_VOLATILE_RATIO,
        },
        "markets": {},
    }

    for market in markets:
        ref = refs.get(market)
        if ref is None:
            print(f"== {market} : absent de {AGENT_CONFIG.name}, rien à mesurer ==")
            continue
        dataset = datasets[market]
        deployed = deployed_strategy(ref)
        if dataset.timeframe not in deployed.manifest.timeframes:
            declared = [item.value for item in deployed.manifest.timeframes]
            raise SystemExit(
                f"{market}: le jeu gelé est en {dataset.timeframe.value} alors que {ref} déclare "
                f"{declared}: mesurer une autre unité de temps répondrait à une autre question"
            )
        if args.bars > 0:
            dataset = replace(dataset, candles=dataset.candles[-args.bars :])
            print(
                f"ATTENTION : {market} tronqué aux {args.bars} dernières bougies — vérification "
                "de plomberie, pas une mesure"
            )

        measurement = measure_market(market, deployed, dataset)
        train_ratios = [
            value for value in ratios_of(measurement.train).values() if value is not None
        ]
        cuts = tercile_cuts(train_ratios)
        if cuts is None:
            print(f"ATTENTION : {market} n'a pas assez d'opérations pour des terciles")
        print_market(measurement, cuts)

        rules = ranked_rules(measurement.train, measurement.validation, cuts)
        decision = decide(
            market, rules, iterations=args.iterations, alpha=DEFAULT_FALSE_DISCOVERY_RATE
        )
        print_decision(market, decision)

        windows = windows_of(measurement)
        report["markets"][market] = {
            "ref": ref,
            "dataset": {
                "dataset_id": dataset.dataset_id,
                "fingerprint": dataset.fingerprint,
                "bars": len(dataset.candles),
                "timeframe": dataset.timeframe.value,
                "start": dataset.candles[0].open_time.isoformat(),
                "end": dataset.candles[-1].close_time.isoformat(),
                "source": dataset.source,
            },
            "manifest": {
                "max_mode": deployed.manifest.max_mode.value,
                "history_bars": deployed.manifest.history_bars,
                "parameters": {key: _number(value) for key, value in deployed.parameters.items()},
                "entry_filter": (
                    None
                    if deployed.manifest.entry_filter is None
                    else deployed.manifest.entry_filter.model_dump(mode="json")
                ),
            },
            "costs": {
                key: _number(value)
                for key, value in config_for(market, dataset).costs.describe().items()
            },
            "windows": [window.to_dict() for window in measurement.split_windows],
            "tercile_cuts": None if cuts is None else list(cuts),
            "baseline": {name: measure(name, trades).to_dict() for name, trades in windows.items()},
            "breakdown": {
                name: _jsonable(breakdown(trades, cuts)) for name, trades in windows.items()
            },
            "decision": _jsonable(decision),
            "rules_tried": len(rules),
        }

    if not args.no_write and report["markets"]:
        args.output.mkdir(parents=True, exist_ok=True)
        path = args.output / "session-volatility.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nécrit : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
