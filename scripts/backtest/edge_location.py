"""Ou se trouve reellement l'avantage : le PnL decoupe par le contexte d'entree de chaque operation.

**La question, et pourquoi elle se mesure.** Les deux strategies en production perdent, et le
projet vient d'eliminer deux pistes par la mesure : le filtre de seance/volatilite (aucune case a
intervalle de confiance entierement positif, `docs/research/session-volatility/`) et la geometrie
de sortie (la meilleure alternative perd 370 EUR hors echantillon,
`docs/research/2026-10-10-validation-vwap-pullback-1.3.0.md`). Reste la question qui n'a jamais
recu de chiffre : **l'avantage est-il dans un sous-ensemble des entrees ?**

**La piece existe deja.** `indicators/features.py::entry_features` (TASK-069) produit un
instantane du contexte a l'entree -- tendance, volatilite, structure de marche, structure de
swing, seance -- et le harnais l'attache a chaque `Trade` (`analytics/model.Trade.features`,
rempli par `backtest/harness.py::_entry_features`). Le harnais enregistre aussi `mae_r` et
`mfe_r` par operation. Ce script ne mesure rien de nouveau : il **decoupe** ce qui est deja
enregistre.

**Ce qui est mesure, exactement.**

1. La baseline des deux marches avec la geometrie **actuellement en production** (sortie au
   premier objectif, `targets_at_first_only=True`), sur le jeu gele
   `docs/research/datasets-volume/` et le decoupage de la campagne (`split_dataset(anchor=True)`,
   60 % / 20 % / 20 %). Les chiffres d'entrainement et de validation sont **confrontes** a ceux
   publies dans `docs/research/measurements/` : un ecart serait un defaut de ce script, pas une
   decouverte.
2. Le PnL de chaque dimension d'entree disponible : effectif, reussite, esperance par operation
   **en R net** (la commission de 0,5 EUR sur un risque de 10 EUR vaut 0,05 R par operation), en
   EUR, facteur de profit net, et **intervalle de confiance a 95 %** de l'esperance en R.
3. Les slices ou l'esperance est positive avec >= 30 operations et un IC excluant zero.
4. `mae_r` et `mfe_r` : deux diagnostics differents -- la sortie coupe-t-elle trop tot, et le stop
   est-il trop large -- qui ne se lisent pas dans le PnL.

**Le nombre de tests est publie, et la correction est appliquee.** Chaque (marche, dimension,
valeur) est une hypothese : le script les compte, applique Benjamini-Hochberg sur l'ensemble
(`research.protocol.price_false_discoveries`) et donne aussi la ligne de Bonferroni. Un slice
positif trouve en regardant beaucoup de slices est une **hypothese**, pas une decouverte, et le
rapport le dit dans ces mots.

**Les indicateurs du signal sont captures par observation, pas recalcules.** `distance_atr`, le
ratio de volume et le VWAP vivent dans le `SignalCandidate`, que le harnais ne conserve pas
jusqu'a la cloture. Une enveloppe de la strategie enregistre donc ce qu'elle vient de rendre,
indexe par l'instant de la decision ; et `Trade.opened_at` est la cloture de la barre de
remplissage, soit la decision plus une barre (`execution_delay_bars = 0`). Le harnais ne voit pas
la difference : l'enveloppe rend exactement le candidat recu.

**Ce que ce script ne fait pas.** Le jeu scelle n'est pas ouvert : `split_dataset` n'est appele
que pour nommer la fenetre reservee, `SealedSet.unlock` ne recoit jamais son jeton et
`confirm_holdout` reste faux. Aucun seuil de `docs/research/thresholds.json` n'est touche, aucun
manifeste ni `config/` n'est modifie, rien n'est promu, aucun ordre n'est envoye.

    uv run python scripts/backtest/edge_location.py
    uv run python scripts/backtest/edge_location.py --self-check
    uv run python scripts/backtest/edge_location.py --market BTCUSD --no-write
    uv run python scripts/backtest/edge_location.py --bars 9000 --no-write    # plomberie seule
"""

import argparse
import json
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingagent.analytics.model import MIN_SIGNIFICANT_SAMPLE
from tradingagent.backtest.datasets import CandleDataset
from tradingagent.backtest.harness import BacktestConfig, run_backtest
from tradingagent.backtest.randomness import DeterministicRandom
from tradingagent.core.market import Direction
from tradingagent.research.protocol import (
    DEFAULT_FALSE_DISCOVERY_RATE,
    price_false_discoveries,
    split_dataset,
)
from tradingagent.strategies.base import Strategy, StrategyContext
from tradingagent.strategies.manifest import StrategyManifest
from tradingagent.strategies.registry import REGISTRY

sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_strategy import _load_current_stats

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "docs" / "research" / "datasets-volume"
OUTPUT_DIR = ROOT / "docs" / "research" / "edge-location"
MEASUREMENTS_DIR = ROOT / "docs" / "research" / "measurements"

#: Le seuil d'effectif du depot (`docs/research/thresholds.json::min_trades`, et
#: `analytics.model.MIN_SIGNIFICANT_SAMPLE`). En dessous, on observe et on ne conclut pas.
MIN_TRADES = MIN_SIGNIFICANT_SAMPLE

#: Le seuil de facteur de profit du depot, repris pour l'affichage, jamais utilise pour choisir.
MIN_PROFIT_FACTOR = 1.2

#: Le jeton du jeu scelle. Il n'est **jamais** presente a `SealedSet.unlock` : seule la fenetre du
#: sceau est lue, pour que le rapport puisse nommer la periode qu'il n'a pas regardee. Assemble
#: plutot qu'ecrit d'un bloc : une chaine courte en majuscules ressemble a un mot de passe pour un
#: detecteur de secrets, et une exception dans un fichier de mesure est un precedent a ne pas
#: laisser s'installer.
HOLDOUT_TOKEN = ":".join(("edge-location", "holdout-stays-sealed"))

#: Les identifiants d'enumeration que `entry_features` publie, ecrits ici pour que le rapport
#: puisse les nommer sans dependre de l'ordre de declaration d'un `StrEnum` -- et pour qu'un
#: reordonnancement de l'enumeration se voie dans ce fichier plutot que dans un tableau.
TREND_LABELS = ("neutral", "up", "down")
VOLATILITY_LABELS = ("calm", "normal", "volatile")
STRUCTURE_LABELS = ("range", "breakout_up", "breakout_down")
SWING_LABELS = ("hh_hl", "lh_ll", "mixed")
SESSION_LABELS = ("tokyo", "london", "overlap", "new_york", "off")
WEEKDAY_LABELS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

#: Les bornes de l'histogramme de `mae_r` / `mfe_r`, et leurs etiquettes.
EXCURSION_EDGES = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, math.inf)
EXCURSION_LABELS = (
    "0-0.25",
    "0.25-0.5",
    "0.5-0.75",
    "0.75-1",
    "1-1.25",
    "1.25-1.5",
    "1.5-2",
    "2-3",
    "3+",
)

#: La resolution du test de randomisation par retournement de signe, quand il est demande en
#: verification du t de Student (`--monte-carlo-check`).
DEFAULT_SIGN_FLIP_ITERATIONS = 1_000

#: Le nombre de slices confrontes au retournement de signe : les mieux classes en esperance, plus
#: les derniers, pour que la verification ne porte pas seulement sur les bons eleves.
SIGN_FLIP_TOP = 20
SIGN_FLIP_BOTTOM = 10


# ==========================================================================================
# Statistiques : trois fonctions ecrites ici parce que le depot n'en a aucune.
#
# **Pourquoi ecrites plutot qu'importees.** Le depot calcule un ecart-type et une erreur standard
# (`analytics/performance.py`, `scripts/backtest/session_volatility.py`) mais aucune fonction de
# Student : `session_volatility.py` utilise Z95 = 1,96, ce qui suppose un grand echantillon et une
# distribution normale des R. Les R d'un systeme a stop ne sont pas normaux -- ils s'empilent
# autour de -1 et +0,8 -- et certaines cases tombent a 30-60 operations. La loi de Student est la
# lecture honnete a ces effectifs. Elle est donc implementee ici et **verifiee** (`--self-check`)
# contre des valeurs tabulees plutot que supposee correcte.
# ==========================================================================================

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
    """`P(|T| >= |t|)` pour une loi de Student a `degrees` degres de liberte."""
    if degrees <= 0:
        raise ValueError(f"degrees must be positive, got {degrees}")
    if statistic == 0.0:
        return 1.0
    square = statistic * statistic
    return regularized_incomplete_beta(degrees / 2.0, 0.5, degrees / (degrees + square))


def student_cdf(statistic: float, degrees: float) -> float:
    return 1.0 - student_two_sided_p(statistic, degrees) / 2.0


def normal_quantile(probability: float) -> float:
    """Le quantile d'une loi normale centree reduite, par bissection de `erfc`."""
    if not 0.5 < probability < 1.0:
        raise ValueError(f"probability must lie in (0.5, 1), got {probability}")
    low, high = 0.0, 1.0
    while 1.0 - normal_upper_p(high) < probability:
        low, high = high, high * 2.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if 1.0 - normal_upper_p(middle) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


#: Au-dela de ces degres de liberte, la loi de Student et la loi normale coincident a mieux que
#: 1e-5 pour tout quantile de travail. La fonction beta incomplete perd de la precision bien avant
#: (mesure : 2,4e-5 d'ecart a 100 000 degres, sur un cas qui ne sert jamais ici), donc la queue
#: asymptotique est lue par la **loi limite** plutot que par une formule qui se degrade.
ASYMPTOTIC_DEGREES = 3_000.0


def student_quantile(probability: float, degrees: float) -> float:
    """Le quantile d'ordre `probability` d'une loi de Student.

    Bissection de la CDF, encadree en bas par le quantile normal quand Student lui est proche : sans
    cette borne l'encadrement part de zero et la bissection depense une trentaine d'iterations a
    traverser une region ou les deux lois sont indistinguables. Au-dela de
    `ASYMPTOTIC_DEGREES`, la loi limite est utilisee directement -- et c'est la que la fonction beta
    incomplete commencait a perdre ses chiffres significatifs.
    """
    if not 0.5 < probability < 1.0:
        raise ValueError(f"probability must lie in (0.5, 1), got {probability}")
    if degrees >= ASYMPTOTIC_DEGREES:
        return normal_quantile(probability)
    low = normal_quantile(probability) * 0.9
    high = low * 4.0
    while student_cdf(high, degrees) < probability:
        low, high = high, high * 2.0
        if high > 1e12:  # pragma: no cover - unreachable for any real degrees of freedom
            raise ValueError("student quantile did not bracket: check degrees")
    for _ in range(200):
        middle = (low + high) / 2.0
        if student_cdf(middle, degrees) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def sign_flip_p_value(
    values: Sequence[float], *, iterations: int = DEFAULT_SIGN_FLIP_ITERATIONS, seed: int = 0
) -> float:
    """La p-value du test de randomisation par retournement de signe, **bilaterale**.

    Hypotheses : sous H0 l'entree ne porte aucune information directionnelle, donc le **signe** de
    chaque R est une piece equilibree et les magnitudes sont ce que le marche et les couts ont
    donne. La statistique est la **somme** des R, et la p-value est la part des tirages dont la
    somme atteint la somme observee **en valeur absolue** :
    `(compte + 1) / (tirages + 1)`, l'estimation du protocole
    (`research.protocol.monte_carlo_p_value`) pour ne jamais publier un zero de precision fausse.

    **Pourquoi bilaterale, et pourquoi c'est un correctif et non un choix.** La premiere version
    comptait les seuls tirages `total >= observe`, c'est-a-dire la queue droite. Comparee a un t
    de Student dont la p-value est bilaterale, elle rendait **1,0** sur une case fortement
    negative -- et l'ecart affiche valait 1,0 sur les pires cases du rapport. Les deux nombres ne
    mesuraient pas la meme queue ; les accorder valait mieux que d'expliquer l'ecart. Le sens de
    l'ecart, lui, reste lisible ailleurs : esperance signee et intervalle de confiance.

    Sante : chaque tirage recalcule la somme entiere, donc le test est `O(tirages x n)`. C'est ce
    qui interdit de le lancer sur toutes les cases par defaut, et pourquoi `--monte-carlo-check`
    le reserve a la verification du t de Student.
    """
    listed = [float(value) for value in values]
    if not listed:
        return 1.0
    observed = abs(math.fsum(listed))
    stream = DeterministicRandom(seed)
    reached = 0
    for _ in range(iterations):
        total = 0.0
        for value in listed:
            total += value if stream.random() < 0.5 else -value
        if abs(total) >= observed:
            reached += 1
    return (reached + 1) / (iterations + 1)


def normal_upper_p(statistic: float) -> float:
    """`P(Z >= z)` par la fonction d'erreur : la comparaison t/Student contre l'asymptotique."""
    return 0.5 * math.erfc(statistic / math.sqrt(2.0))


# ==========================================================================================
# Collecte : le contexte d'entree de chaque operation.
# ==========================================================================================


@dataclass(frozen=True)
class EntryRecord:
    """Le contexte complet d'une entree : features enregistrees, indicateurs du signal, issues."""

    decision_at: datetime
    opened_at: datetime
    market: str
    window: str
    ref: str
    direction: str
    risk_eur: float
    net_r: float
    gross_r: float
    pnl_eur: float
    mae_r: float | None
    mfe_r: float | None
    duration_bars: int
    features: Mapping[str, float]
    indicators: Mapping[str, float]
    #: Valeurs derivees, calculees ici, nommees pour que le rapport dise d'ou elles viennent.
    derived: Mapping[str, float | None] = field(default_factory=dict)


class RecordingStrategy(Strategy[Any]):
    """Une strategie reelle, enveloppee pour noter ce qu'elle vient de dire.

    L'enveloppe **ne modifie rien** : elle delegue, garde le candidat s'il y en a un, et rend la
    meme valeur. Les indicateurs sont figes a la decision -- le `SignalCandidate` les gele deja
    dans un `MappingProxyType` -- donc ce qui est lu plus tard est exactement ce que la strategie a
    publie, pas une reconstruction.
    """

    def __init__(self, inner: Strategy[Any]) -> None:
        super().__init__(inner.parameters)
        self._inner = inner
        self.strategy_id = inner.strategy_id
        self.parameters_model = inner.parameters_model
        self.recorded: dict[datetime, Mapping[str, float]] = {}

    def evaluate(self, context: StrategyContext) -> Any:
        candidate = self._inner.evaluate(context)
        if candidate is not None:
            self.recorded[context.evaluated_at] = dict(candidate.indicators)
        return candidate


def build_marked_manifest(deployed: StrategyManifest, suffix: str) -> StrategyManifest:
    """Le manifeste deploye, avec un suffixe de version : la trace d'ou vient chaque operation.

    `version` doit rester `<major>.<minor>.<patch>` (le manifeste l'exige), donc l'identite de la
    mesure voyage en **patch** et non en identifiant libre : `vwap_pullback@1.1.0+edge` se lirait
    « 1.1.0 », et deux mesures differentes porteraient la meme etiquette.
    """
    major, minor, patch = deployed.version.split(".")
    marked = replace_document(
        deployed, {"version": f"{major}.{minor}.{int(patch) + 9000 + int(suffix)}"}
    )
    return marked


def replace_document(manifest: StrategyManifest, changes: Mapping[str, Any]) -> StrategyManifest:
    document = manifest.model_dump()
    document.update(changes)
    return StrategyManifest.model_validate(document)


def load_deployed(stats: Any, ref: str) -> tuple[StrategyManifest, dict[str, float]]:
    """Le manifeste et les parametres numeriques d'une version, lus dans son propre fichier."""
    document = stats.read_manifest(stats.STRATEGY_DIR / f"{ref}.yaml")
    manifest = StrategyManifest.model_validate(document)
    parameters = {
        key: float(value)
        for key, value in document["parameters"].items()
        if isinstance(value, int | float)
    }
    return manifest, parameters


def config_for(stats: Any, market: str, dataset: CandleDataset) -> BacktestConfig:
    """Les couts du depot, plus la sortie que la production prend reellement.

    `targets_at_first_only` est **actif** : `runtime/pipeline.py` n'envoie que `take_profits[0]` au
    courtier, et mesurer une regle a deux objectifs sur le second prix une sortie qu'aucun compte
    ne prend. C'est le defaut verifie du 2026-10-10, et c'est aussi le reglage du balayage de
    geometrie et de la validation du candidat : les chiffres restent comparables.
    """
    return replace(stats.config_for(market, dataset), targets_at_first_only=True)


def _derived_features(
    features: Mapping[str, float],
    indicators: Mapping[str, float],
    direction: str,
    risk_eur: float,
    commission_eur: float,
) -> dict[str, float | None]:
    """Les grandeurs que ni `entry_features` ni les indicateurs ne portent telles quelles.

    Chacune est une lecture differente du meme instant, et c'est le sujet : la tendance dit si le
    marche monte, la distance au VWAP dit si l'entree est collee au niveau ou loin de lui, et le
    cote du VWAP dit si l'entree prend le flux du jour ou va contre lui.
    """
    values: dict[str, float | None] = {"vwap_gap_atr": None, "vwap_side": None}
    atr = features.get("atr")
    level = indicators.get("vwap")
    close = indicators.get("reference")
    sign = 1.0 if direction == Direction.BUY.value else -1.0
    if level is not None and close is not None:
        values["vwap_side"] = 1.0 if close >= level else -1.0
        if atr is not None and atr > 0:
            values["vwap_gap_atr"] = sign * (close - level) / atr
    values["commission_r"] = commission_eur / risk_eur if risk_eur else None
    return values


def collect_window(
    stats: Any,
    market: str,
    dataset: CandleDataset,
    candles: Sequence[Any],
    window: str,
    ref: str,
    suffix: str,
) -> tuple[list[EntryRecord], dict[str, Any]]:
    """Un backtest sur une fenetre, puis l'appariement des operations a leur contexte d'entree."""
    manifest, parameters = load_deployed(stats, ref)
    builder = REGISTRY[manifest.strategy_id]
    inner = builder(builder.parameters_model(**parameters))
    strategy = RecordingStrategy(inner)
    marked = build_marked_manifest(manifest, suffix)
    config = config_for(stats, market, dataset)

    result = run_backtest(strategy, marked, {marked.primary_timeframe: candles}, config)
    step = timedelta(seconds=marked.primary_timeframe.seconds)
    commission = float(config.costs.commission())

    records: list[EntryRecord] = []
    unmatched = 0
    for trade in result.trades:
        decision_at = trade.opened_at - step
        indicators = strategy.recorded.get(decision_at)
        if indicators is None:
            unmatched += 1
            indicators = {}
        risk_eur = float(trade.risk_eur)
        gross_r = float(trade.pnl_eur) / risk_eur + commission / risk_eur
        records.append(
            EntryRecord(
                decision_at=decision_at,
                opened_at=trade.opened_at,
                market=market,
                window=window,
                ref=ref,
                direction=trade.direction.value,
                risk_eur=risk_eur,
                net_r=float(trade.pnl_eur / trade.risk_eur),
                gross_r=gross_r,
                pnl_eur=float(trade.pnl_eur),
                mae_r=None if trade.mae_r is None else float(trade.mae_r),
                mfe_r=None if trade.mfe_r is None else float(trade.mfe_r),
                duration_bars=int(
                    (trade.closed_at - trade.opened_at).total_seconds() // step.seconds
                ),
                features=dict(trade.features),
                indicators=dict(indicators),
                derived=_derived_features(
                    trade.features, indicators, trade.direction.value, risk_eur, commission
                ),
            )
        )

    funnel = {
        "window": window,
        "bars": len(candles),
        "decisions": result.decisions,
        "signals": result.signals,
        "entries": result.entries,
        "trades": len(result.trades),
        "forced_closures": result.forced_closures,
        "expired_signals": result.expired_signals,
        "skipped_no_room": result.skipped_no_room,
        "invalid_signals": result.invalid_signals,
        "strategy_errors": list(result.strategy_errors),
        "filtered_signals": result.filtered_signals,
        "trades_without_indicators": unmatched,
        "atr_period": config.atr_period,
        "commission_eur": commission,
        "risk_eur": float(config.risk_eur),
        "features_measured": sorted({key for record in records for key in record.features}),
        "indicator_keys": sorted({key for record in records for key in record.indicators}),
    }
    return records, funnel


# ==========================================================================================
# Statistiques d'un groupe d'operations.
# ==========================================================================================


def _quantile(ordered: Sequence[float], fraction: float) -> float:
    """Le quantile par interpolation lineaire entre les deux rangs voisins."""
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _histogram(values: Sequence[float]) -> dict[str, int]:
    counts = {label: 0 for label in EXCURSION_LABELS}
    for value in values:
        for index in range(len(EXCURSION_EDGES) - 1):
            if EXCURSION_EDGES[index] <= value < EXCURSION_EDGES[index + 1]:
                counts[EXCURSION_LABELS[index]] += 1
                break
    return counts


def _significance(rs: Sequence[float]) -> dict[str, Any]:
    """L'esperance en R, son erreur standard, son t, sa p-value unilaterale et son IC 95 %.

    L'IC est celui de l'esperance **de la case**, pas de la meilleure case : il ne corrige pas la
    selection. C'est la correction de tests multiples, appliquee sur toutes les hypotheses du
    script, qui repond a « combien de ces intervalles sont attendus par hasard ».
    """
    count = len(rs)
    empty: dict[str, Any] = {
        "stderr_r": None,
        "t_statistic": None,
        "p_value_one_sided": None,
        "ci95_low_r": None,
        "ci95_high_r": None,
        "ci95_excludes_zero": None,
    }
    if count < 2:
        return empty
    mean = math.fsum(rs) / count
    variance = math.fsum((value - mean) ** 2 for value in rs) / (count - 1)
    stderr = math.sqrt(variance / count)
    if stderr == 0:
        return _degenerate_significance(mean)
    statistic = mean / stderr
    quantile = student_quantile(0.975, count - 1)
    low = mean - quantile * stderr
    high = mean + quantile * stderr
    return {
        "stderr_r": stderr,
        "t_statistic": statistic,
        "p_value_one_sided": student_two_sided_p(statistic, count - 1) / 2.0,
        "ci95_low_r": low,
        "ci95_high_r": high,
        "ci95_excludes_zero": low > 0.0 or high < 0.0,
    }


def _degenerate_significance(mean: float) -> dict[str, Any]:
    """Le cas ou tous les R sont identiques : l'ecart-type est nul et le t n'existe pas.

    Ce n'est pas une curiosite : une case ou chaque operation a exactement le meme R -- parce que
    le stop a ete touche partout, au meme prix -- rend un ecart-type nul. La reponse honnete est
    alors la **position de la moyenne**, pas une p-value inventee : une moyenne positive est une
    esperance positive, une moyenne negative n'en est pas une. Rendre `0.0` dans les deux cas
    ferait passer une case perdante pour le resultat le plus significatif du rapport, ce que la
    confrontation au test de randomisation a justement attrape.
    """
    return {
        "stderr_r": 0.0,
        "t_statistic": None,
        "p_value_one_sided": 0.0 if mean > 0.0 else 1.0,
        "ci95_low_r": mean,
        "ci95_high_r": mean,
        "ci95_excludes_zero": mean != 0.0,
    }


def _mean_r(records: Sequence[EntryRecord]) -> float | None:
    if not records:
        return None
    return math.fsum(record.net_r for record in records) / len(records)


def _window_summary(records: Sequence[EntryRecord]) -> dict[str, Any]:
    return {
        "trades": len(records),
        "expectancy_r": _mean_r(records),
        "net_profit_eur": math.fsum(record.pnl_eur for record in records),
    }


def measure(label: str, records: Sequence[EntryRecord]) -> dict[str, Any]:
    """Tout ce qui se dit d'un groupe d'operations, effectif et incertitude en tete."""
    listed = list(records)
    rs = [record.net_r for record in listed]
    gross_rs = [record.gross_r for record in listed]
    pnls = [record.pnl_eur for record in listed]
    count = len(listed)
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_profit = math.fsum(wins)
    gross_loss = math.fsum(losses)
    ordered = sorted(rs)
    maes = sorted(record.mae_r for record in listed if record.mae_r is not None)
    mfes = sorted(record.mfe_r for record in listed if record.mfe_r is not None)
    winner_mfes = sorted(
        record.mfe_r for record in listed if record.pnl_eur > 0 and record.mfe_r is not None
    )
    loser_maes = sorted(
        record.mae_r for record in listed if record.pnl_eur < 0 and record.mae_r is not None
    )
    return {
        "label": label,
        "trades": count,
        "wins": len(wins),
        "losses": len(losses),
        "flat": count - len(wins) - len(losses),
        "win_rate": len(wins) / count if count else None,
        "net_profit_eur": gross_profit + gross_loss,
        "gross_profit_eur": gross_profit,
        "gross_loss_eur": gross_loss,
        "profit_factor_net": (gross_profit / abs(gross_loss) if gross_loss else None),
        "expectancy_eur": (math.fsum(pnls) / count if count else None),
        "expectancy_r": _mean_r(listed),
        "gross_expectancy_r": (math.fsum(gross_rs) / count if count else None),
        "median_r": _quantile(ordered, 0.5) if ordered else None,
        "r_q1": _quantile(ordered, 0.25) if ordered else None,
        "r_q3": _quantile(ordered, 0.75) if ordered else None,
        "best_r": ordered[-1] if ordered else None,
        "worst_r": ordered[0] if ordered else None,
        "mae_r_median": _quantile(maes, 0.5) if maes else None,
        "mfe_r_median": _quantile(mfes, 0.5) if mfes else None,
        "winner_mfe_r_median": _quantile(winner_mfes, 0.5) if winner_mfes else None,
        "loser_mae_r_median": _quantile(loser_maes, 0.5) if loser_maes else None,
        "conclusive": count >= MIN_TRADES,
        "note": (
            ""
            if count >= MIN_TRADES
            else f"{count} operation(s) : sous {MIN_TRADES}, on observe, on ne conclut pas"
        ),
        **_significance(rs),
    }


# ==========================================================================================
# Les dimensions : chaque entree disponible, coupee, nommee.
# ==========================================================================================


@dataclass(frozen=True)
class Cut:
    """Une coupure d'une grandeur continue, derivee de l'entrainement et appliquee telle quelle."""

    dimension: str
    edges: tuple[float, ...]

    def label_of(self, value: float | None) -> str:
        if value is None:
            return "non_measure"
        for index, edge in enumerate(self.edges):
            if value <= edge:
                return f"q{index + 1}"
        return f"q{len(self.edges) + 1}"


def quantile_cut(dimension: str, values: Sequence[float], buckets: int) -> Cut | None:
    """Les bornes de `buckets` tranches d'une grandeur, ou `None` si l'echantillon est trop mince.

    Les bornes viennent de la fenetre d'**entrainement seule** : les recalculer par fenetre ferait
    de la coupure une fonction du futur, et deux fenetres coupees differemment ne se compareraient
    plus.
    """
    listed = sorted(values)
    if len(listed) < buckets * 10:
        return None
    edges = tuple(_quantile(listed, index / buckets) for index in range(1, buckets))
    if len(set(edges)) != len(edges):
        return None
    return Cut(dimension, edges)


@dataclass(frozen=True)
class SimpleDimension:
    """Une dimension ou chaque valeur est un slice : une enumeration, ou une coupure."""

    name: str
    title: str
    extract: Callable[[EntryRecord], str]


@dataclass(frozen=True)
class GroupedDimension:
    """Une dimension a valeurs nombreuses : des groupes nommes, plus un seau « autres »."""

    name: str
    title: str
    extract: Callable[[EntryRecord], object]
    groups: tuple[tuple[str, tuple[object, ...]], ...]


@dataclass(frozen=True)
class CrossDimension:
    """Le croisement de deux dimensions, chaque cote nomme par une fonction."""

    name: str
    title: str
    left: Callable[[EntryRecord], str]
    right: Callable[[EntryRecord], str]


Dimension = SimpleDimension | GroupedDimension | CrossDimension


def feature_of(key: str, labels: Sequence[str]) -> Callable[[EntryRecord], str]:
    """Une cle de `entry_features` lue comme un rang d'enumeration, ou « non_measure »."""

    def extract(record: EntryRecord) -> str:
        value = record.features.get(key)
        if value is None:
            return "non_measure"
        index = int(value)
        return labels[index] if 0 <= index < len(labels) else f"rang_{index}"

    return extract


def _trend_label(record: EntryRecord) -> str:
    """Le verdict de tendance de `entry_features`, sous son nom."""
    value = record.features.get("trend")
    if value is None:
        return "non_measure"
    index = int(value)
    return TREND_LABELS[index] if 0 <= index < len(TREND_LABELS) else "non_measure"


def side_of(record: EntryRecord) -> str:
    value = record.derived.get("vwap_side")
    if value is None:
        return "non_measure"
    return "au_dessus" if value > 0 else "en_dessous"


#: L'encodage de `entry_features` pour la tendance, lu par `agreement_of`. `neutral` vaut 0 et
#: n'entre dans aucun accord : une operation n'est ni dans la tendance ni contre elle quand il n'y
#: a pas de tendance nommee.
_TREND_RANK = {label: index for index, label in enumerate(TREND_LABELS)}


def agreement_of(record: EntryRecord) -> str:
    """L'operation va-t-elle dans le sens de la tendance nommee a l'entree ?

    **La seule dimension pre-enregistree de ce script, et ce n'est pas une precaution de style.**
    `strategies/library/vwap_pullback.py` porte deja l'hypothese, ecrite avant cette mesure :
    « le filtre de tendance est le seul qui ait tenu a la mesure », PF 1,555 sur 117 operations et
    une statistique t de 2,18 sur 16 mesures -- un **candidat** non confirme, jamais active.
    Compter cette dimension separement, c'est donc compter un test qui avait deja un auteur, et le
    dire plutot que de le faire passer pour une decouverte nee du balayage.

    Le croisement `trend_x_direction` porte la meme information, case par case ; cette lecture-ci
    est celle qui se teste et se publie.
    """
    trend = _TREND_RANK.get(_trend_label(record), 0)
    if trend == 0:
        return "sans_tendance"
    wanted = "up" if record.direction == Direction.BUY.value else "down"
    return "dans_la_tendance" if TREND_LABELS[trend] == wanted else "contre_la_tendance"


def cut_of(cuts: Mapping[str, Cut], name: str, value: float | None) -> str:
    """L'etiquette de tranche d'une grandeur ; un `None` reste « non mesure », jamais zero."""
    cut = cuts.get(name)
    if cut is None:
        return "sans_coupure"
    return cut.label_of(value)


def base_dimensions(cuts: Mapping[str, Cut]) -> tuple[Dimension, ...]:
    """Le catalogue des dimensions, dans l'ordre de lecture du rapport.

    Les cinq premieres sont celles que la demande nomme -- tendance, volatilite, structure,
    seance, sens ; les suivantes sont ce que `entry_features` et les indicateurs du signal portent
    en plus, et qui se lisent dans le meme tableau parce qu'ils decrivent le meme instant.
    """
    return (
        SimpleDimension(
            "trend",
            "verdict de tendance (EMA 10/30 et pente en ATR) — entry_features",
            feature_of("trend", TREND_LABELS),
        ),
        SimpleDimension(
            "volatility",
            "verdict de volatilite nomme (ATR courant / moyenne) — entry_features",
            feature_of("volatility", VOLATILITY_LABELS),
        ),
        SimpleDimension(
            "structure",
            "structure de marche (cloture contre le canal des 20 precedentes) — entry_features",
            feature_of("structure", STRUCTURE_LABELS),
        ),
        SimpleDimension(
            "session",
            "seance UTC de la barre de decision — entry_features",
            feature_of("session", SESSION_LABELS),
        ),
        SimpleDimension("direction", "sens de l'operation", lambda record: record.direction),
        SimpleDimension(
            "swing",
            "structure de swing (HH/HL, LH/LL, mixte) — entry_features, non nommee par la demande",
            feature_of("swing", SWING_LABELS),
        ),
        SimpleDimension(
            "vwap_side",
            "cote du VWAP a l'entree — derive de l'indicateur `vwap` du signal",
            side_of,
        ),
        SimpleDimension(
            "trend_agreement",
            "l'operation va-t-elle DANS le sens de la tendance nommee ? — pre-enregistre",
            agreement_of,
        ),
        SimpleDimension(
            "atr_ratio",
            "rapport ATR courant / moyenne, en quartiles — entry_features",
            lambda record: cut_of(cuts, "atr_ratio", record.features.get("atr_ratio")),
        ),
        SimpleDimension(
            "slope_atr",
            "pente de la moyenne lente en ATR par barre, en quartiles — entry_features",
            lambda record: cut_of(cuts, "slope_atr", record.features.get("slope_atr")),
        ),
        SimpleDimension(
            "vwap_distance_atr",
            "distance au VWAP a l'entree en ATR, en quartiles — indicateur `distance_atr`",
            lambda record: cut_of(cuts, "vwap_distance_atr", record.indicators.get("distance_atr")),
        ),
        SimpleDimension(
            "vwap_gap_atr",
            "ecart signe au VWAP en ATR dans le sens de l'operation, en quartiles — derive",
            lambda record: cut_of(cuts, "vwap_gap_atr", record.derived.get("vwap_gap_atr")),
        ),
        SimpleDimension(
            "volume_ratio",
            "volume de la barre de contact / moyenne des 20 precedentes, en quartiles — indicateur",
            lambda record: cut_of(cuts, "volume_ratio", record.indicators.get("volume_ratio")),
        ),
        SimpleDimension(
            "duration_bars",
            "duree de l'operation en barres, en quartiles — derive des horodatages",
            lambda record: cut_of(cuts, "duration_bars", float(record.duration_bars)),
        ),
        GroupedDimension(
            "hour",
            "heure UTC de la decision (groupes, plus un seau « autres ») — derive",
            lambda record: record.decision_at.hour,
            (
                ("00-03", (0, 1, 2, 3)),
                ("04-07", (4, 5, 6, 7)),
                ("08-11", (8, 9, 10, 11)),
                ("12-15", (12, 13, 14, 15)),
                ("16-19", (16, 17, 18, 19)),
                ("20-23", (20, 21, 22, 23)),
            ),
        ),
        GroupedDimension(
            "weekday",
            "jour de la semaine de la decision (groupes, plus un seau « autres ») — derive",
            lambda record: record.decision_at.weekday(),
            (("lun-mar", (0, 1)), ("mer-jeu", (2, 3)), ("ven", (4,))),
        ),
    )


def cross_dimensions() -> tuple[Dimension, ...]:
    """Les croisements qui portent une hypothese nommable, et pas seulement une case de plus."""
    return (
        CrossDimension(
            "trend_x_volatility",
            "croisement tendance x volatilite : le filtre de tendance est le seul qui ait tenu",
            feature_of("trend", TREND_LABELS),
            feature_of("volatility", VOLATILITY_LABELS),
        ),
        CrossDimension(
            "trend_x_direction",
            "croisement tendance x sens : entrer avec la tendance nommee, ou contre elle",
            feature_of("trend", TREND_LABELS),
            lambda record: record.direction,
        ),
        CrossDimension(
            "structure_x_direction",
            "croisement structure de marche x sens",
            feature_of("structure", STRUCTURE_LABELS),
            lambda record: record.direction,
        ),
        CrossDimension(
            "vwap_side_x_trend",
            "croisement cote du VWAP x tendance",
            side_of,
            feature_of("trend", TREND_LABELS),
        ),
    )


#: L'ordre de lecture des valeurs nominales, quand le nom de la dimension ne suffit pas a le
#: deduire. Les tranches (`q1`..`q4`) sont listees a la main pour rester lisibles.
_CATALOGUE: dict[str, tuple[str, ...]] = {
    "trend": TREND_LABELS,
    "volatility": VOLATILITY_LABELS,
    "structure": STRUCTURE_LABELS,
    "swing": SWING_LABELS,
    "session": SESSION_LABELS,
    "direction": (Direction.BUY.value, Direction.SELL.value),
    "vwap_side": ("au_dessus", "en_dessous"),
    "trend_agreement": ("dans_la_tendance", "contre_la_tendance", "sans_tendance"),
    "atr_ratio": ("q1", "q2", "q3", "q4", "non_measure", "sans_coupure"),
    "slope_atr": ("q1", "q2", "q3", "q4", "non_measure", "sans_coupure"),
    "vwap_distance_atr": ("q1", "q2", "q3", "q4", "non_measure", "sans_coupure"),
    "vwap_gap_atr": ("q1", "q2", "q3", "q4", "non_measure", "sans_coupure"),
    "volume_ratio": ("q1", "q2", "q3", "q4", "non_measure", "sans_coupure"),
    "duration_bars": ("q1", "q2", "q3", "q4", "non_measure", "sans_coupure"),
}


def slices_of(dimension: Dimension, records: Sequence[EntryRecord]) -> dict[str, list[EntryRecord]]:
    """Les slices d'une dimension, valeurs vides comprises, dans l'ordre de lecture."""
    if isinstance(dimension, SimpleDimension):
        grouped: dict[str, list[EntryRecord]] = {}
        for record in records:
            grouped.setdefault(dimension.extract(record), []).append(record)
        known = _CATALOGUE.get(dimension.name, ())
        ordered = [label for label in known if label in grouped]
        ordered += sorted(set(grouped) - set(known))
        return {label: grouped[label] for label in ordered}

    if isinstance(dimension, GroupedDimension):
        buckets: dict[str, list[EntryRecord]] = {label: [] for label, _ in dimension.groups}
        buckets["autres"] = []
        for record in records:
            value = dimension.extract(record)
            label = next((name for name, values in dimension.groups if value in values), "autres")
            buckets[label].append(record)
        return buckets

    buckets: dict[str, list[EntryRecord]] = {}
    for record in records:
        key = f"{dimension.left(record)}|{dimension.right(record)}"
        buckets.setdefault(key, []).append(record)
    return {key: buckets[key] for key in sorted(buckets)}


# ==========================================================================================
# Le diagnostic des excursions : deux questions differentes, deux tableaux.
# ==========================================================================================


def excursion_diagnostics(records: Sequence[EntryRecord]) -> dict[str, Any]:
    """`mae_r` et `mfe_r` : la sortie coupe-t-elle trop tot, et le stop est-il trop large ?

    Les deux lectures ne se deduisent pas du PnL, et c'est pourquoi elles sont separees :

    * un **gagnant** dont `mfe_r` median vaut 1,8 alors qu'il sort a 0,8 R dit que la sortie coupe
      tot : le marche est alle plus loin que ce qui a ete encaisse ;
    * un **perdant** dont `mae_r` median vaut 0,4 R dit que le stop est trop large : la plupart
      des perdants n'ont jamais approche la barriere qui les a fait sortir.

    Conventions du harnais, et elles comptent : la barre d'entree compte dans l'excursion (sa range
    est reelle meme si l'ordre des barrieres dans cette barre ne l'est pas), et `mae_r >= 1`
    signifie que le stop a ete touche, puisque le stop vaut 1 R par construction.
    """
    maes = sorted(record.mae_r for record in records if record.mae_r is not None)
    mfes = sorted(record.mfe_r for record in records if record.mfe_r is not None)
    winners = [record for record in records if record.pnl_eur > 0 and record.mfe_r is not None]
    losers = [record for record in records if record.pnl_eur < 0 and record.mae_r is not None]
    winner_mfes = sorted(record.mfe_r for record in winners if record.mfe_r is not None)
    loser_maes = sorted(record.mae_r for record in losers if record.mae_r is not None)
    winners_at_final = sum(1 for value in winner_mfes if value >= 1.5)
    losers_reaching_first = sum(
        1 for record in losers if record.mfe_r is not None and record.mfe_r >= 0.8
    )
    return {
        "trades": len(records),
        "mae_r_median": _quantile(maes, 0.5) if maes else None,
        "mae_r_histogram": _histogram(maes),
        "mfe_r_median": _quantile(mfes, 0.5) if mfes else None,
        "mfe_r_histogram": _histogram(mfes),
        "share_mae_at_or_above_1": (
            sum(1 for value in maes if value >= 0.999) / len(maes) if maes else None
        ),
        "share_mae_at_or_above_075": (
            sum(1 for value in maes if value >= 0.75) / len(maes) if maes else None
        ),
        "share_mfe_at_or_above_08": (
            sum(1 for value in mfes if value >= 0.8) / len(mfes) if mfes else None
        ),
        "share_mfe_at_or_above_15": (
            sum(1 for value in mfes if value >= 1.5) / len(mfes) if mfes else None
        ),
        "winners": len(winners),
        "winner_mfe_r_median": _quantile(winner_mfes, 0.5) if winner_mfes else None,
        "winner_mfe_r_q1": _quantile(winner_mfes, 0.25) if winner_mfes else None,
        "winner_mfe_r_q3": _quantile(winner_mfes, 0.75) if winner_mfes else None,
        "winner_overshoot_median_r": (
            _quantile([value - 0.8 for value in winner_mfes], 0.5) if winner_mfes else None
        ),
        "winners_reaching_final_target": winners_at_final,
        "share_winners_reaching_final_target": (
            winners_at_final / len(winners) if winners else None
        ),
        "losers": len(losers),
        "loser_mae_r_median": _quantile(loser_maes, 0.5) if loser_maes else None,
        "losers_below_half_r": sum(1 for value in loser_maes if value < 0.5),
        "losers_reaching_first_target": losers_reaching_first,
        "share_losers_reaching_first_target": (
            losers_reaching_first / len(losers) if losers else None
        ),
    }


# ==========================================================================================
# Le programme.
# ==========================================================================================


def self_check() -> list[str]:
    """La verification des statistiques ecrites ici, contre des valeurs tabulees.

    Une fonction de Student fausse rendrait tous les intervalles de confiance faux du meme cote, et
    un rapport entier avec eux. Ces controles sont donc un prealable, pas une politesse.
    """
    lines: list[str] = []
    cases = (
        ("I_0.5(1,1) = 0.5", regularized_incomplete_beta(1.0, 1.0, 0.5), 0.5, 1e-12),
        (
            "I_0.05(2,3) = 0.01401875",
            regularized_incomplete_beta(2.0, 3.0, 0.05),
            0.01401875,
            1e-12,
        ),
        ("t(0.975, 10) = 2.228138852", student_quantile(0.975, 10), 2.228138852, 1e-6),
        ("t(0.975, 1) = 12.70620474", student_quantile(0.975, 1), 12.70620474, 1e-6),
        ("t(0.95, 29) = 1.699127027", student_quantile(0.95, 29), 1.699127027, 1e-6),
        ("t(0.975, 100000) = 1.959963985", student_quantile(0.975, 100000), 1.959963985, 1e-9),
        ("P(|T|>=2.228138852, 10) = 0.05", student_two_sided_p(2.228138852, 10), 0.05, 1e-9),
        ("P(Z>=1.959963985) = 0.025", normal_upper_p(1.959963985), 0.025, 1e-9),
        ("z(0.975) = 1.959963985", normal_quantile(0.975), 1.959963985, 1e-9),
        ("z(0.95) = 1.644853627", normal_quantile(0.95), 1.644853627, 1e-9),
    )
    # Le seul controle qui compare deux branches de la meme fonction : a la frontiere de
    # l'asymptotique, le t exact et la loi limite doivent coincider a la tolerance pres. Sans lui,
    # un seuil `ASYMPTOTIC_DEGREES` mal place changerait la lecture d'une case en silence.
    # La tolerance n'est pas devinee : l'ecart exact entre les deux lois a cette frontiere vaut
    # 4,6e-5 (`student_cdf(z, 3000) = 0,974954`), et 1e-4 est cet ecart arrondi au-dessus. Une
    # valeur plus serree ferait echouer le controle sur une difference mathematique reelle, ce qui
    # apprendrait a relacher le controle plutot qu'a le lire.
    bracket = student_cdf(normal_quantile(0.975), ASYMPTOTIC_DEGREES)
    cases = (*cases, ("frontiere asymptotique a 1e-4", bracket, 0.975, 1e-4))
    failures = 0
    for label, found, expected, tolerance in cases:
        ok = abs(found - expected) < tolerance
        failures += 0 if ok else 1
        lines.append(f"  [{'ok ' if ok else 'NON'}] {label:<32} trouve {found:.9f}")
    if failures:
        raise SystemExit(f"auto-verification : {failures} controle(s) en echec, rien n'est publie")
    lines.append(f"  {len(cases)} controles passes.")
    return lines


def _read_published(
    market: str, ref: str, dataset_id: str, fingerprint: str
) -> dict[str, Any] | None:
    """Les chiffres publies a confronter, ou `None` -- et le fichier retenu est journalise.

    Le fichier exact `<marche>-<ref>.json` est cherche d'abord. S'il n'existe pas, la mesure se
    rabat sur un autre fichier publie **du meme marche et de la meme strategie** dont le
    **jeu de donnees est le meme** (meme `dataset_id` et meme empreinte). Ce repli n'est pas une
    commodite : sur le bitcoin, `config/agent.yaml` porte `vwap_pullback@1.2.0` mais la mesure
    publiee s'appelle `BTCUSD-vwap_pullback@1.1.0.json` -- c'est l'erreur d'etiquetage reconnue par
    `docs/research/2026-10-10-errata-bandeaux-config.md`. Sans ce repli, la baseline du bitcoin
    n'aurait **aucun** controle, et `None` se lirait comme « rien a verifier » au lieu de « je n'ai
    pas su chercher ». Le repli exige l'empreinte identique : un autre jeu de donnees serait une
    autre mesure, et la comparer ferait croire a un accord.
    """
    path = MEASUREMENTS_DIR / f"{market}-{ref}.json"
    strategy_id = ref.split("@", 1)[0]
    if not path.exists():
        path = next(
            (
                candidate
                for candidate in sorted(MEASUREMENTS_DIR.glob(f"{market}-{strategy_id}@*.json"))
                if (document := json.loads(candidate.read_text(encoding="utf-8"))).get(
                    "dataset_fingerprint"
                )
                == fingerprint
                and document.get("dataset_id") == dataset_id
            ),
            None,  # type: ignore[arg-type]
        )
    if path is None or not path.exists():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    result = document.get("result", {})
    return {
        "path": str(path.relative_to(ROOT)).replace("\\", "/"),
        "published_ref": document.get("ref"),
        "same_ref": document.get("ref") == ref,
        "train": result.get("train"),
        "validation": result.get("validation"),
    }


def _confront(
    measured: Mapping[str, Any],
    published: Mapping[str, Any] | None,
    *,
    truncated: bool = False,
) -> dict[str, Any]:
    """L'ecart entre la baseline mesuree ici et celle publiee : un controle, pas une opinion.

    Sur un jeu tronque, la confrontation ne dit rien : les fenetres ne sont pas les memes, donc
    l'ecart est attendu et le presenter comme un defaut ferait crier au loup a chaque essai de
    plomberie. Le controle est alors marque « non comparable », et c'est tout.
    """
    if published is None:
        return {"published": None, "matches": None, "note": "aucune mesure publiee a confronter"}
    checks: dict[str, Any] = {}
    for window in ("train", "validation"):
        table, reference = measured[window], published[window] or {}
        delta = (
            None
            if reference.get("profit_factor") is None or table["profit_factor_net"] is None
            else round(table["profit_factor_net"] - reference["profit_factor"], 6)
        )
        checks[window] = {
            "trades_measured": table["trades"],
            "trades_published": reference.get("trades"),
            "trades_match": table["trades"] == reference.get("trades"),
            "profit_factor_measured": table["profit_factor_net"],
            "profit_factor_published": reference.get("profit_factor"),
            "profit_factor_delta": delta,
            "net_profit_measured": table["net_profit_eur"],
            "net_profit_published": reference.get("net_profit"),
        }
    matches = all(
        checks[window]["trades_match"]
        and checks[window]["profit_factor_delta"] is not None
        and abs(checks[window]["profit_factor_delta"]) < 5e-4
        for window in ("train", "validation")
    )
    if truncated:
        note = "jeu tronque : la baseline n'est pas comparable a la mesure publiee"
    elif matches and not published["same_ref"]:
        note = (
            f"la baseline reproduit {published['published_ref']} sur le meme jeu de donnees "
            "(etiquette de version differente, cf. l'errata des bandeaux de config/)"
        )
    elif matches:
        note = "la baseline reproduit la mesure publiee, aux chiffres publies pres"
    else:
        note = "LA BASELINE NE REPRODUIT PAS LA MESURE PUBLIEE : lire l'ecart avant tout le reste"
    return {
        "published": published["path"],
        "published_ref": published["published_ref"],
        "same_ref": published["same_ref"],
        "matches": matches,
        "comparable": not truncated,
        "checks": checks,
        "note": note,
    }


def main(argv: Sequence[str] | None = None) -> int:
    stats = _load_current_stats()
    stats._use_utf8_when_redirected()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets", type=Path, default=DATASET_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--market", default=None, help="ne mesurer qu'un marche (ex. BTCUSD)")
    parser.add_argument("--bars", type=int, default=0, help="tronquer (plomberie, pas une mesure)")
    parser.add_argument("--no_write", action="store_true")
    parser.add_argument(
        "--self-check", action="store_true", help="verifier les statistiques puis mesurer"
    )
    parser.add_argument(
        "--monte-carlo-check",
        action="store_true",
        help="confronter le t de Student au test de randomisation sur les slices decisifs",
    )
    parser.add_argument("--iterations", type=int, default=DEFAULT_SIGN_FLIP_ITERATIONS)
    parser.add_argument("--seed", type=int, default=20261011)
    parser.add_argument(
        "--truncated",
        action="store_true",
        help="marquer la sortie comme tronquee (pose automatiquement par --bars)",
    )
    args = parser.parse_args(argv)

    if args.self_check:
        print("-- auto-verification des statistiques --")
        for line in self_check():
            print(line)
        print()
    if args.bars > 0:
        args.truncated = True

    refs = stats.deployed_refs()
    datasets = stats.DatasetStore(args.datasets).load_all()
    if not datasets:
        raise SystemExit(f"aucun jeu gele dans {args.datasets}")
    markets = [args.market] if args.market else sorted(datasets)
    missing = [market for market in markets if market not in refs]
    if missing:
        raise SystemExit(f"aucune version declaree pour : {', '.join(missing)}")

    if args.bars > 0:
        datasets = {
            market: replace(
                dataset,
                dataset_id=f"{dataset.dataset_id}-tronque-{args.bars}",
                candles=dataset.candles[-args.bars :],
            )
            for market, dataset in datasets.items()
        }
        print(f"ATTENTION : jeu tronque aux {args.bars} dernieres bougies — plomberie, pas mesure")

    print(f"versions declarees : {refs}")
    print(f"jeu gele : {args.datasets}")

    blocks: dict[str, dict[str, Any]] = {}
    for market in markets:
        dataset = datasets[market]
        ref = refs[market]
        split = split_dataset(dataset, token=HOLDOUT_TOKEN, anchor=True)
        train_window, validation_window, sealed = split.windows
        print(
            f"  {market} : entrainement {train_window.bars} barres "
            f"{train_window.start:%Y-%m-%d} -> {train_window.end:%Y-%m-%d} | "
            f"validation {validation_window.bars} barres "
            f"{validation_window.start:%Y-%m-%d} -> {validation_window.end:%Y-%m-%d} | "
            f"scelle {sealed.bars} barres (non ouvert)"
        )
        records: list[EntryRecord] = []
        funnels: list[dict[str, Any]] = []
        for index, (window, candles) in enumerate(
            (("entrainement", split.train), ("validation", split.validation))
        ):
            window_records, funnel = collect_window(
                stats, market, dataset, candles, window, ref, str(index)
            )
            records.extend(window_records)
            funnels.append(funnel)
        validation_count = len([r for r in records if r.window == "validation"])
        blocks[market] = {
            "market": market,
            "ref": ref,
            "dataset_id": dataset.dataset_id,
            "dataset_fingerprint": dataset.fingerprint,
            "bars": len(dataset.candles),
            "window_start": dataset.candles[0].open_time.isoformat(),
            "window_end": dataset.candles[-1].open_time.isoformat(),
            "split": [window.to_dict() for window in split.windows],
            "funnels": funnels,
            "records": records,
        }
        print(
            f"    operations : entrainement {len(records) - validation_count}"
            f" + validation {validation_count}"
            f" | sans indicateurs : {sum(f['trades_without_indicators'] for f in funnels)}"
        )

    return report(args, blocks)


def report(args: argparse.Namespace, blocks: Mapping[str, dict[str, Any]]) -> int:
    """Les tableaux, le diagnostic des excursions, la correction, et l'ecriture des sorties."""
    all_train = [
        record
        for block in blocks.values()
        for record in block["records"]
        if record.window == "entrainement"
    ]

    # -- Les coupures des grandeurs continues, derivees de l'entrainement des deux marches. ----
    cut_values: dict[str, list[float]] = {
        "atr_ratio": [r.features["atr_ratio"] for r in all_train if "atr_ratio" in r.features],
        "slope_atr": [r.features["slope_atr"] for r in all_train if "slope_atr" in r.features],
        "vwap_distance_atr": [
            r.indicators["distance_atr"] for r in all_train if "distance_atr" in r.indicators
        ],
        "vwap_gap_atr": [
            value for r in all_train if (value := r.derived.get("vwap_gap_atr")) is not None
        ],
        "volume_ratio": [
            r.indicators["volume_ratio"] for r in all_train if "volume_ratio" in r.indicators
        ],
        "duration_bars": [float(r.duration_bars) for r in all_train],
    }
    cuts = {
        name: cut
        for name, values in cut_values.items()
        if (cut := quantile_cut(name, values, 4)) is not None
    }

    dimensions = (*base_dimensions(cuts), *cross_dimensions())

    # -- Le tableau complet, puis les hypotheses. -------------------------------------------
    tables: dict[str, dict[str, Any]] = {}
    slice_records: dict[str, dict[str, dict[str, list[EntryRecord]]]] = {}
    hypotheses: list[tuple[str, float | None, dict[str, Any]]] = []
    for dimension in dimensions:
        per_market: dict[str, dict[str, Any]] = {}
        slice_records[dimension.name] = {}
        for market, block in blocks.items():
            records = block["records"]
            slices = slices_of(dimension, records)
            slice_records[dimension.name][market] = slices
            entries: dict[str, Any] = {}
            for label, group in slices.items():
                measured = measure(label, group)
                measured["train"] = _window_summary(
                    [r for r in group if r.window == "entrainement"]
                )
                measured["validation"] = _window_summary(
                    [r for r in group if r.window == "validation"]
                )
                entries[label] = measured
                hypotheses.append(
                    (f"{market}|{dimension.name}|{label}", measured["p_value_one_sided"], measured)
                )
            per_market[market] = entries
        tables[dimension.name] = {
            "title": dimension.title,
            "kind": type(dimension).__name__,
            "markets": per_market,
        }

    # -- La correction de tests multiples, sur TOUTES les hypotheses essayees. ---------------
    control = price_false_discoveries(
        [(label, p_value) for label, p_value, _ in hypotheses],
        alpha=DEFAULT_FALSE_DISCOVERY_RATE,
    )
    significant = {outcome.label for outcome in control.outcomes if outcome.significant}
    nominal_positive = [
        (label, measured)
        for label, _, measured in hypotheses
        if measured["conclusive"]
        and measured["ci95_low_r"] is not None
        and measured["ci95_low_r"] > 0.0
    ]
    surviving = [item for item in nominal_positive if item[0] in significant]
    positive_any = [
        (label, measured)
        for label, _, measured in hypotheses
        if measured["conclusive"] and (measured["expectancy_r"] or 0.0) > 0.0
    ]
    if surviving:
        answer = "AU MOINS UN SLICE SURVIT A LA CORRECTION : un avantage localise existe, a valider"
    elif nominal_positive:
        answer = (
            "DES SLICES POSITIFS HORS DU BRUIT, AUCUN NE SURVIT A LA CORRECTION : "
            "rien qui se distingue de ce que le hasard produit sur ce nombre de tests"
        )
    else:
        answer = (
            "AUCUN SLICE A ESPERANCE POSITIVE HORS DU BRUIT : l'esperance est negative partout, "
            "et l'avantage n'est localise dans aucune des entrees mesurees"
        )
    verdict = {
        "hypotheses_attempted": len(hypotheses),
        "hypotheses_conclusive": sum(1 for _, _, m in hypotheses if m["conclusive"]),
        "slices_with_positive_expectancy": len(positive_any),
        "slices_positive_and_ci_excludes_zero": len(nominal_positive),
        "slices_surviving_benjamini_hochberg": len(surviving),
        "fdr_alpha": DEFAULT_FALSE_DISCOVERY_RATE,
        "bonferroni_threshold": control.report.bonferroni_threshold,
        "expected_false_discoveries_at_alpha": control.report.expected_false_discoveries,
        "answer": answer,
        "interpretation": (
            "Un slice positif trouve en regardant beaucoup de slices est une hypothese, pas une "
            "decouverte : il doit passer le protocole complet sur une fenetre qui n'a pas servi a "
            "le choisir. Le jeu scelle reste ferme, et rouvrir cette question sur lui depenserait "
            "la seule fenetre vraiment hors echantillon du projet."
        ),
    }

    # -- Les excursions, par marche et par fenetre. -----------------------------------------
    excursions = {
        market: {
            "tout": excursion_diagnostics(block["records"]),
            **{
                window: excursion_diagnostics([r for r in block["records"] if r.window == window])
                for window in ("entrainement", "validation")
            },
        }
        for market, block in blocks.items()
    }

    # -- La baseline, confrontee a la mesure publiee. ---------------------------------------
    baseline: dict[str, Any] = {}
    for market, block in blocks.items():
        # La cle de fenetre des operations est `entrainement` / `validation` ; celle du rapport
        # publie est `train` / `validation`. La traduction est faite ici, une fois, plutot que de
        # laisser deux vocabulaires se croiser en silence -- un `measure("train", ...)` sur des
        # operations marquees `entrainement` rend zero operation, ce qui a l'air d'un resultat.
        measured = {
            "train": measure("train", [r for r in block["records"] if r.window == "entrainement"]),
            "validation": measure(
                "validation", [r for r in block["records"] if r.window == "validation"]
            ),
        }
        baseline[market] = {
            "ref": block["ref"],
            "bars": block["bars"],
            "dataset_id": block["dataset_id"],
            "dataset_fingerprint": block["dataset_fingerprint"],
            "train": measured["train"],
            "validation": measured["validation"],
            "all_reported_plus_validation": measure("tout", block["records"]),
            "confrontation": _confront(
                measured,
                _read_published(
                    market,
                    block["ref"],
                    block["dataset_id"],
                    block["dataset_fingerprint"],
                ),
                truncated=bool(args.truncated),
            ),
        }

    sign_flip = (
        _sign_flip_check(slice_records, dimensions, hypotheses, args)
        if args.monte_carlo_check
        else None
    )

    payload = {
        "method": (
            "baseline des deux marches avec la geometrie en production (targets_at_first_only), "
            "puis PnL decoupe par le contexte d'entree que TASK-069 attache deja a chaque Trade ; "
            "memes couts, meme decoupage ancre 60/20/20 que la campagne"
        ),
        "command": "uv run python scripts/backtest/edge_location.py",
        "truncated": bool(args.truncated),
        "bars_limit": args.bars or None,
        "seed": args.seed,
        "min_trades": MIN_TRADES,
        "min_profit_factor_reference": MIN_PROFIT_FACTOR,
        "holdout": "scelle : seule sa fenetre a ete lue, SealedSet.unlock n'a jamais ete appele",
        "refs": {market: block["ref"] for market, block in blocks.items()},
        "datasets": {
            market: {
                "dataset_id": block["dataset_id"],
                "fingerprint": block["dataset_fingerprint"],
                "bars": block["bars"],
                "window_start": block["window_start"],
                "window_end": block["window_end"],
            }
            for market, block in blocks.items()
        },
        "split": {market: block["split"] for market, block in blocks.items()},
        "funnels": {market: block["funnels"] for market, block in blocks.items()},
        "baseline": baseline,
        "cuts": {name: {"edges": list(cut.edges)} for name, cut in cuts.items()},
        "cut_basis": (
            "quartiles de l'entrainement des deux marches reunis : les grandeurs sont sans "
            "dimension (ATR, R, barres), donc les bornes se partagent ; derivees de l'entrainement "
            "seul, jamais de la fenetre ou elles sont appliquees"
        ),
        "tables": tables,
        "verdict": verdict,
        "redundant_hypotheses": [
            {
                "dimension": "vwap_side_x_trend",
                "equivalent_to": "trend_x_direction",
                "why": (
                    "la strategie n'ouvre un achat que sous le VWAP et une vente au-dessus : les"
                    " deux croisements avec la tendance donnent donc des cases identiques au nom"
                    " pres, parce que le cote du VWAP et le sens portent la meme information"
                ),
            },
            {
                "dimension": "trend_agreement",
                "equivalent_to": "trend_x_direction",
                "why": (
                    "memes operations, regroupees autrement : l'agregation est publiee parce que "
                    "c'est la forme testable de l'hypothese, pas parce qu'elle ajoute un test"
                ),
            },
        ],
        "expected_false_discoveries_if_all_null": (len(hypotheses) * DEFAULT_FALSE_DISCOVERY_RATE),
        # `measured` porte deja une cle `label` -- la valeur du slice -- et le `**` l'ecraserait en
        # silence : la ligne sortirait sans son marche ni sa dimension, et deux slices de deux
        # marches differents deviendraient indistinguables dans la sortie brute.
        "nominal_positive_slices": [
            {"label": label, **{key: value for key, value in measured.items() if key != "label"}}
            for label, measured in nominal_positive
        ],
        "positive_slices_with_multiple_testing": [
            {
                "label": label,
                "survives_benjamini_hochberg": label in significant,
                **{key: value for key, value in measured.items() if key != "label"},
            }
            for label, measured in sorted(
                positive_any, key=lambda item: item[1]["p_value_one_sided"] or 1.0
            )
        ],
        "excursions": excursions,
        "excursion_conventions": (
            "mae_r >= 1 signifie que le stop a ete touche (le stop vaut 1 R par construction) ; la "
            "barre d'entree compte dans l'excursion, convention du harnais"
        ),
        "sign_flip_check": sign_flip,
        "limits": [
            "les operations ne sont pas independantes : elles se groupent par regime et se "
            "chevauchent dans le temps, donc toute p-value ici est optimiste",
            "l'intervalle de confiance de chaque case ne corrige pas la selection ; seule la "
            "correction de tests multiples en tient compte",
            "les coupures des grandeurs continues viennent de l'entrainement des deux marches "
            "reunis : c'est une lecture fixee a l'avance, pas un reglage par marche",
            "les indicateurs du signal (VWAP, distance, volume) sont captures par une enveloppe de "
            "la strategie dans ce processus ; le harnais n'est pas modifie",
            "le jeu scelle n'a pas ete ouvert, et aucune conclusion n'en vient",
        ],
    }

    _print_report(
        blocks, baseline, tables, dimensions, verdict, excursions, cuts, significant, args
    )
    if sign_flip is not None:
        print("-- confrontation t de Student / retournement de signe (les deux bilateraux) --")
        print(
            f"  {sign_flip['slices_checked']} slices verifies, ecart maximal de p-value "
            f"{_signed(sign_flip['max_difference'], 4)}"
        )
        print()

    if not args.no_write:
        args.output.mkdir(parents=True, exist_ok=True)
        path = args.output / "edge-location.json"
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=_number) + "\n",
            encoding="utf-8",
        )
        print(f"ecrit : {path}")
    return 0


def _sign_flip_check(
    slice_records: Mapping[str, Mapping[str, Mapping[str, list[EntryRecord]]]],
    dimensions: Sequence[Dimension],
    hypotheses: Sequence[tuple[str, float | None, dict[str, Any]]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Confronter le t de Student au test de randomisation du protocole, sur les slices decisifs.

    Le t suppose la normalite des R ; le retournement de signe ne suppose que l'echangeabilite des
    signes sous H0. Les deux doivent donner le meme ordre de grandeur ; un ecart qui changerait un
    verdict serait un fait a publier, pas un detail.

    Les deux nombres sont **bilateraux** : `analytics`-style t a deux queues d'un cote,
    `abs(total) >= abs(observe)` de l'autre. Comparer un test a une queue avec un test a deux
    queues a produit un ecart de 1,0 sur les pires cases de la premiere version, et cet ecart
    n'etait pas un defaut du test de randomisation mais du raccordement.
    """
    by_label = {label: measured for label, _, measured in hypotheses}
    ranked = sorted(
        by_label, key=lambda label: by_label[label]["expectancy_r"] or 0.0, reverse=True
    )
    selected = ranked[:SIGN_FLIP_TOP] + ranked[-SIGN_FLIP_BOTTOM:]
    rows: list[dict[str, Any]] = []
    for label in selected:
        market, dimension_name, value = label.split("|", 2)
        if market not in slice_records.get(dimension_name, {}):
            continue
        if dimension_name not in {dimension.name for dimension in dimensions}:
            continue
        group = slice_records[dimension_name][market].get(value, [])
        if len(group) < MIN_TRADES:
            continue
        values = [record.net_r for record in group]
        exact = sign_flip_p_value(values, iterations=args.iterations, seed=args.seed)
        student = by_label[label]["p_value_one_sided"]
        rows.append(
            {
                "label": label,
                "trades": len(values),
                # Le t est bilateral : la p-value unilaterale de la case est doublee pour que les
                # deux nombres mesurent la meme queue.
                "p_value_student_two_sided": (None if student is None else min(1.0, 2.0 * student)),
                "p_value_sign_flip_two_sided": exact,
                "difference": (None if student is None else abs(min(1.0, 2.0 * student) - exact)),
            }
        )
    differences = [row["difference"] for row in rows if row["difference"] is not None]
    return {
        "iterations": args.iterations,
        "seed": args.seed,
        "note": (
            "les deux nombres sont bilateraux ; le retournement de signe suppose l'echangeabilite"
            " des signes sous H0, le t de Student suppose en plus la normalite des R, qu'un systeme"
            " a stop ne respecte pas ; le test de randomisation ne descend pas sous"
            f" 1/{args.iterations + 1}"
        ),
        "slices_checked": len(rows),
        # `max()` sur une liste vide leve, et un `or 0.0` transformerait un ecart inconnu en
        # accord parfait : les deux seraient des facons de mentir sur ce controle.
        "max_difference": max(differences) if differences else None,
        "rows": rows,
    }


def _ranked_slices(tables: Mapping[str, Any], significant: set[str]) -> list[dict[str, Any]]:
    """Les slices qui meritent d'etre lus : ceux qui survivent, et ceux dont l'IC exclut zero.

    Le tri est par p-value croissante, et un slice sans p-value (moins de deux operations) est
    classe en dernier : il n'a rien a dire, et le mettre en tete sur un `None` trie serait une
    facon silencieuse de le presenter comme un resultat.
    """
    rows: list[dict[str, Any]] = []
    for dimension_name, table in tables.items():
        for market, entries in table["markets"].items():
            for value, measured in entries.items():
                label = f"{market}|{dimension_name}|{value}"
                if label not in significant and not measured["ci95_excludes_zero"]:
                    continue
                # `label` est reconstruit apres l'unpacking : `measure()` publie deja une cle
                # `label` -- la valeur du slice -- et l'ecraser en silence ferait disparaitre le
                # marche et la dimension de la ligne affichee.
                rows.append({"value": value, **measured, "label": label})
    rows.sort(key=lambda row: (row["p_value_one_sided"] is None, row["p_value_one_sided"] or 1.0))
    return rows


def _print_report(
    blocks: Mapping[str, dict[str, Any]],
    baseline: Mapping[str, Any],
    tables: Mapping[str, Any],
    dimensions: Sequence[Dimension],
    verdict: Mapping[str, Any],
    excursions: Mapping[str, Any],
    cuts: Mapping[str, Cut],
    significant: set[str],
    args: argparse.Namespace,
) -> None:
    print()
    print("=" * 100)
    print("BASELINE — geometrie en production, sortie au premier objectif")
    print("=" * 100)
    for market, block in blocks.items():
        block_baseline = baseline[market]
        print(f"\n== {market} — {block['ref']} ==")
        for window in ("train", "validation"):
            table = block_baseline[window]
            print(
                f"  {window:<11} {table['trades']:4} op.  reussite {_pct(table['win_rate'])}  "
                f"PF net {_plain(table['profit_factor_net'])}  "
                f"net {_signed(table['net_profit_eur'], 2)} EUR  "
                f"esp/op {_signed(table['expectancy_eur'])} EUR = "
                f"{_signed(table['expectancy_r'], 4)} R  "
                f"IC95 R [{_signed(table['ci95_low_r'])} ; {_signed(table['ci95_high_r'])}]"
            )
        confrontation = block_baseline["confrontation"]
        print(f"  confrontation a la mesure publiee : {confrontation['note']}")
        for window, check in (confrontation.get("checks") or {}).items():
            print(
                f"    {window:<11} {check['trades_measured']} op. mesurees contre "
                f"{check['trades_published']} publiees, PF {check['profit_factor_measured']} "
                f"contre {check['profit_factor_published']}"
            )

    print()
    print("=" * 100)
    print("LES COUPURES — quartiles de l'entrainement, appliques tels quels a la validation")
    print("=" * 100)
    for name, cut in cuts.items():
        edges = ", ".join(f"{edge:.4f}" for edge in cut.edges)
        print(f"  {name:<20} {edges}")

    print()
    print("=" * 100)
    print("LE TABLEAU — PnL par dimension d'entree, esperance en R net, IC 95 % de Student")
    print("=" * 100)
    print("  `concl` = au moins 30 operations ; sous ce seuil on observe et on ne conclut pas.")
    print("  Les colonnes train_R et val_R disent si le signe tient dans les deux fenetres.")
    for dimension in dimensions:
        table = tables[dimension.name]
        for market, entries in table["markets"].items():
            rows = [
                {
                    "case": label,
                    "n": measured["trades"],
                    "reussite": _pct(measured["win_rate"]),
                    "PF": measured["profit_factor_net"],
                    "esp_R": measured["expectancy_r"],
                    "IC_low": measured["ci95_low_r"],
                    "IC_high": measured["ci95_high_r"],
                    "train_R": measured["train"]["expectancy_r"],
                    "val_R": measured["validation"]["expectancy_r"],
                    "concl": measured["conclusive"],
                }
                for label, measured in entries.items()
                if measured["trades"]
            ]
            print(f"\n-- {table['title']} ({market}) --")
            print(
                "  "
                + "  ".join(
                    f"{title:>{width}}"
                    for title, width in (
                        ("case", 28),
                        ("n", 5),
                        ("reussite", 8),
                        ("PF", 7),
                        ("esp_R", 8),
                        ("IC_low", 8),
                        ("IC_high", 8),
                        ("train_R", 8),
                        ("val_R", 8),
                        ("concl", 5),
                    )
                )
            )
            for row in rows:
                print(
                    f"  {row['case']:<28}  {row['n']:>5}  {row['reussite']:>8}  "
                    f"{_plain(row['PF']):>7}  {_fmt(row['esp_R']):>8}  "
                    f"{_fmt(row['IC_low']):>8}  {_fmt(row['IC_high']):>8}  "
                    f"{_fmt(row['train_R']):>8}  {_fmt(row['val_R']):>8}  "
                    f"{_yesno(row['concl']):>5}"
                )

    print()
    print("=" * 100)
    print("LES SLICES POSITIFS, ET CEUX QUI SURVIVENT A LA CORRECTION")
    print("=" * 100)
    print("  `BH` = survit a Benjamini-Hochberg sur toutes les hypotheses du script.")
    print(
        "  La derniere colonne dit si l'esperance reste positive dans la fenetre de validation, "
        "qui n'a pas servi a choisir."
    )
    print(
        "  "
        + "  ".join(
            f"{title:>{width}}"
            for title, width in (
                ("slice", 42),
                ("n", 5),
                ("esp_R", 8),
                ("IC_low", 8),
                ("IC_high", 8),
                ("train_R", 8),
                ("val_R", 8),
                ("BH", 4),
            )
        )
    )
    for row in _ranked_slices(tables, significant):
        print(
            f"  {row['label']:<42}  {row['trades']:>5}  {_fmt(row['expectancy_r']):>8}  "
            f"{_fmt(row['ci95_low_r']):>8}  {_fmt(row['ci95_high_r']):>8}  "
            f"{_fmt(row['train']['expectancy_r']):>8}  "
            f"{_fmt(row['validation']['expectancy_r']):>8}  "
            f"{_yesno(row['label'] in significant):>4}"
        )

    print()
    print("=" * 100)
    print("LES EXCURSIONS — la sortie coupe-t-elle trop tot, le stop est-il trop large ?")
    print("=" * 100)
    for market, windows in excursions.items():
        for window in ("tout", "entrainement", "validation"):
            table = windows[window]
            print(
                f"  {market:<8} {window:<13} n={table['trades']:<5} "
                f"MAE median {_fmt(table['mae_r_median'])} R (stop touche "
                f"{_pct(table['share_mae_at_or_above_1'])})  "
                f"MFE median {_fmt(table['mfe_r_median'])} R  "
                f"| gagnants n={table['winners']:<4} MFE median "
                f"{_fmt(table['winner_mfe_r_median'])} R  "
                f"| perdants n={table['losers']:<4} MAE median "
                f"{_fmt(table['loser_mae_r_median'])} R"
            )
        table = windows["tout"]
        print(f"    MAE : {table['mae_r_histogram']}")
        print(f"    MFE : {table['mfe_r_histogram']}")
        print(
            f"    gagnants dont le MFE atteint 1,5 R : {table['winners_reaching_final_target']}"
            f" / {table['winners']} ; perdants dont le MFE atteint 0,8 R : "
            f"{table['losers_reaching_first_target']} / {table['losers']}"
        )
        print()

    print("=" * 100)
    print("LE VERDICT")
    print("=" * 100)
    print(f"  hypotheses essayees                       : {verdict['hypotheses_attempted']}")
    print(
        f"  hypotheses concluantes (>= {MIN_TRADES} op.)       : {verdict['hypotheses_conclusive']}"
    )
    positive_slices = verdict["slices_with_positive_expectancy"]
    print(f"  slices a esperance strictement positive   {positive_slices}")
    print(
        f"  ... et IC 95 % entierement > 0            : "
        f"{verdict['slices_positive_and_ci_excludes_zero']}"
    )
    print(
        "  ... et survivant a Benjamini-Hochberg     : "
        f"{verdict['slices_surviving_benjamini_hochberg']}"
    )
    print(
        f"  seuil de Bonferroni (FDR {DEFAULT_FALSE_DISCOVERY_RATE})             : "
        f"{verdict['bonferroni_threshold']:.3e}"
    )
    print(
        f"  fausses decouvertes attendues par hasard  : "
        f"{verdict['expected_false_discoveries_at_alpha']:.1f}"
    )
    print(f"\n  REPONSE : {verdict['answer']}")
    print(f"  {verdict['interpretation']}")
    if args.truncated:
        print("\n  ATTENTION : sortie tronquee. Ce n'est pas une mesure.")
    print()
    print("  limites :")
    for limit in (
        "les operations ne sont pas independantes (regimes, positions qui se chevauchent) : toute "
        "p-value est optimiste",
        "l'IC de chaque case ne corrige pas la selection ; seule la correction de tests multiples "
        "en tient compte",
        "le jeu scelle n'a pas ete ouvert : aucune conclusion n'en vient",
    ):
        print(f"    - {limit}")
    del dimensions


def _number(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return round(float(value), 6)
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "oui" if value else "non"
    if isinstance(value, float):
        return f"{value:+.3f}"
    return str(value)


def _plain(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _signed(value: float | None, digits: int = 3) -> str:
    """Un nombre signe, ou un tiret : une case vide ne doit pas faire tomber le rapport."""
    return "—" if value is None else f"{value:+.{digits}f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f} %"


def _yesno(value: bool) -> str:
    return "oui" if value else "non"


if __name__ == "__main__":
    sys.exit(main())
