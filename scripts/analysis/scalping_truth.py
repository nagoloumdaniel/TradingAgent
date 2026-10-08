"""T3 — « petits gains très répétitifs » : ce que la mesure décide.

Recalcule, à partir des jeux gelés du dépôt et du **modèle de coûts réel** du projet, les
trois chiffres qui décident de la demande « scalping avec le moindre gain, peu importe la
stratégie » :

1. l'espérance **brute** par opération qu'il faudrait atteindre pour que l'espérance nette
   soit positive, par unité de temps et par marché — en R, en euros, en points de prix, et
   en multiple de ce qui a été mesuré ;
2. ce que fait une suite d'opérations à espérance négative sur 100 / 500 / 1000 opérations,
   et ce qu'un doublement de mise après perte produit exactement (nombre de pertes
   consécutives qui ruine, probabilité de l'atteindre) ;
3. au-delà de quelle fréquence (opérations par jour) le compte démo réel perd de l'argent,
   en euros par jour, sur le solde réel lu dans `account_snapshots`.

Ce script **ne modifie rien** : ni seuil, ni stratégie, ni manifeste. Il relit les jeux
gelés de `docs/research/datasets/`, rejoue le backtest du dépôt hors coûts et au modèle de
coûts chargé, et imprime le rapport. Ses seules écritures sont `--json` et
`--refresh-account`.

Le modèle de coûts est celui que la campagne a réellement utilisé
(`scripts/backtest/run_campaign.py::config_for`) : spread = 0,5 bp du prix, slippage
fixe = 0,2 bp du prix, commission = 0,50 € par opération. Le risque nominal du backtest est
`harness.DEFAULT_RISK_EUR` (10 €). Le compte démo réel est dimensionné par
`config/agent.yaml` → `risk.simulated.risk_per_trade_pct`.

Exemples
--------
    uv run python scripts/analysis/scalping_truth.py
    uv run python scripts/analysis/scalping_truth.py --json scripts/analysis/petits-gains-facts.json
    uv run python scripts/analysis/scalping_truth.py --refresh-account

`--refresh-account` interroge `account_snapshots` en lecture seule et réécrit
`scripts/analysis/account-snapshot.json`. Sans lui, le script tourne hors ligne sur la
copie gelée de ce fichier, donc chaque euro affiché reste reproductible sans réseau.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import statistics
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
# `python scripts/analysis/<ce fichier>.py` met le dossier du script sur `sys.path`, pas la
# racine : sans ces deux lignes ni `tradingagent` ni `scripts.backtest.run_campaign` ne se
# résolvent. Elles ne font que rétablir ce que `uv run pytest` obtient de `pythonpath = ["."]`.
for entry in (str(ROOT / "src"), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from tradingagent.analytics.model import Trade  # noqa: E402
from tradingagent.analytics.performance import compute_performance  # noqa: E402
from tradingagent.backtest.datasets import CandleDataset, DatasetStore  # noqa: E402
from tradingagent.backtest.harness import DEFAULT_RISK_EUR, run_backtest  # noqa: E402
from tradingagent.core.timeframe import Timeframe  # noqa: E402
from tradingagent.indicators.volatility import atr  # noqa: E402
from tradingagent.research.protocol import split_dataset  # noqa: E402

RUNNER_PATH = ROOT / "scripts" / "backtest" / "run_campaign.py"
DATASETS = {
    "M15": ROOT / "docs" / "research" / "datasets",
    "M5": ROOT / "docs" / "research" / "datasets" / "M5",
    "M1": ROOT / "docs" / "research" / "datasets" / "M1",
}
CAMPAIGNS = {name: ROOT / "docs" / "research" / f"campaign-{name}" for name in DATASETS}
AGENT_CONFIG = ROOT / "config" / "agent.yaml"
ACCOUNT_SNAPSHOT = ROOT / "scripts" / "analysis" / "account-snapshot.json"

SEED = 20_261_008
DRIFT_PATHS = 4_000
WALK_STEPS = 1_000
CHECKPOINTS = (100, 500, 1000)
MARTINGALE_PATHS = 1_000


# ---------------------------------------------------------------------------------------
# Le modèle de coûts, nommé une fois. Chaque constante est relue depuis le dépôt, jamais
# recopiée : si `config_for` change, ce script change avec lui.
# ---------------------------------------------------------------------------------------
def load_runner() -> Any:
    """Load `scripts/backtest/run_campaign.py` as a module: the campaign's own cost model."""
    return importlib.import_module("scripts.backtest.run_campaign")


RISK_NOMINAL: Decimal = DEFAULT_RISK_EUR


@dataclass(frozen=True)
class Account:
    """The real demo account, read from `account_snapshots` and frozen next to this script."""

    balance_eur: float
    equity_eur: float
    captured_at: str
    rows: int
    closed_trades: int
    risk_per_trade_pct: float

    @property
    def risk_per_trade_eur(self) -> float:
        return self.balance_eur * self.risk_per_trade_pct / 100.0


@dataclass(frozen=True)
class Cell:
    """One (unité de temps, marché) cell, measured on its frozen validation window."""

    timeframe: str
    symbol: str
    dataset_id: str
    price: float
    spread_price: float
    slippage_price: float
    commission_eur: float
    bars: int
    days: float
    trades: int
    gross_eur: float
    net_eur: float
    cost_eur: float
    spread_eur: float
    slippage_eur: float
    commission_total_eur: float
    atr_median: float
    gross_multiples: tuple[float, ...]
    net_multiples: tuple[float, ...]
    candidates: tuple[dict[str, Any], ...] = ()

    # -- les six nombres par opération, en R -------------------------------------------------
    @property
    def ops_per_day(self) -> float:
        return self.trades / self.days if self.days > 0 else 0.0

    @property
    def gross_r(self) -> float:
        """Espérance brute par opération, en R, telle que mesurée."""
        return self.gross_eur / (self.trades * float(RISK_NOMINAL))

    @property
    def cost_r(self) -> float:
        """Coût par opération en R, au risque nominal du backtest."""
        return self.cost_eur / (self.trades * float(RISK_NOMINAL))

    @property
    def net_r(self) -> float:
        return self.gross_r - self.cost_r

    @property
    def price_part_r(self) -> float:
        """Part du coût qui ne dépend pas de la taille du compte : spread + slippage."""
        return (self.spread_eur + self.slippage_eur) / (self.trades * float(RISK_NOMINAL))

    @property
    def stop_effective(self) -> float:
        """Distance de stop effective (points de prix), moyenne harmonique pondérée par opération.

        Elle est déduite de la mesure, pas postulée : le coût spread+slippage d'une opération
        vaut `risque x (spread + 2 x slippage) / distance_de_stop`, donc la moyenne harmonique
        des distances est `(spread + 2 x slippage) / (coût spread+slippage en R)`.
        """
        price_cost = self.spread_price + 2.0 * self.slippage_price
        return price_cost / self.price_part_r if self.price_part_r > 0 else math.inf

    def cost_r_at(self, risk_eur: float) -> float:
        """Coût par opération en R quand le risque par opération n'est plus 10 €.

        Le spread et le slippage sont des distances de prix : leur part en R ne bouge pas.
        La commission est un forfait en euros : sa part en R vaut `0,50 / risque`.
        """
        return self.price_part_r + self.commission_eur / risk_eur

    def required_gross_r(self, risk_eur: float) -> float:
        """Espérance brute par opération qui annule exactement l'espérance nette."""
        return self.cost_r_at(risk_eur)

    def required_gross_price(self, risk_eur: float) -> float:
        """La même exigence, en points de prix par opération."""
        return self.required_gross_r(risk_eur) * self.stop_effective


# ---------------------------------------------------------------------------------------
# Mesure
# ---------------------------------------------------------------------------------------
def measure_cell(runner: Any, timeframe: str, directory: Path) -> list[Cell]:
    """Replay the campaign's three candidates on their validation window, costs on and off."""
    runner.TIMEFRAME = Timeframe(timeframe)
    store = DatasetStore(directory)
    datasets: dict[str, CandleDataset] = {item.symbol: item for item in store.load_all().values()}
    cells: list[Cell] = []
    for symbol, dataset in sorted(datasets.items()):
        split = split_dataset(
            dataset,
            token=f"campaign:{symbol}:holdout",
            train_fraction=runner.TRAIN_FRACTION,
            validation_fraction=runner.VALIDATION_FRACTION,
            anchor=runner.ANCHOR_SPLIT,
        )
        validation = split.validation
        charged = runner.config_for(symbol, dataset)
        free = replace(
            charged,
            costs=replace(
                charged.costs,
                spread=0.0,
                slippage_fixed=0.0,
                commission_per_trade=Decimal("0"),
            ),
        )
        # La décomposition est séquentielle, comme dans `docs/research/cost-attribution.txt` :
        # chaque poste est la différence entre deux runs qui ne diffèrent que par lui.
        ladder = {
            "free": free.costs,
            "spread": replace(free.costs, spread=charged.costs.spread),
            "spread+slippage": replace(
                free.costs,
                spread=charged.costs.spread,
                slippage_fixed=charged.costs.slippage_fixed,
            ),
            "charged": charged.costs,
        }
        totals = dict.fromkeys(ladder, 0.0)
        net_trades: list[Trade] = []
        gross_trades: list[Trade] = []
        per_candidate: list[dict[str, Any]] = []
        for spec in runner.candidate_specs([symbol]):
            runs: dict[str, tuple[Trade, ...]] = {}
            for name, costs in ladder.items():
                result = run_backtest(
                    runner.witness_factory(dict(spec.parameters)),
                    runner.witness_manifest([symbol]),
                    {dataset.timeframe: validation},
                    replace(charged, costs=costs),
                )
                runs[name] = result.trades
                totals[name] += float(result.performance.net_profit)
            gross_trades.extend(runs["free"])
            net_trades.extend(runs["charged"])
            free = compute_performance(list(runs["free"]))
            charged_run = compute_performance(list(runs["charged"]))
            per_candidate.append(
                {
                    "label": spec.label,
                    "operations": charged_run.trades,
                    "brut_eur": float(free.net_profit),
                    "net_eur": float(charged_run.net_profit),
                    "pf_brut": free.profit_factor,
                    "pf_net_1x": charged_run.profit_factor,
                }
            )
        gross = totals["free"]
        net = totals["charged"]
        spread_cost = gross - totals["spread"]
        slippage_cost = totals["spread"] - totals["spread+slippage"]
        commission_cost = totals["spread+slippage"] - net
        volatility = [
            value
            for value in atr(
                [bar.high for bar in validation],
                [bar.low for bar in validation],
                [bar.close for bar in validation],
                int(runner.candidate_specs([symbol])[0].parameters["atr_period"]),
            )
            if value is not None
        ]
        cells.append(
            Cell(
                timeframe=timeframe,
                symbol=symbol,
                dataset_id=dataset.dataset_id,
                price=float(dataset.candles[0].close),
                spread_price=float(charged.costs.spread),
                slippage_price=float(charged.costs.slippage_fixed),
                commission_eur=float(charged.costs.commission_per_trade),
                bars=len(validation),
                days=(validation[-1].close_time - validation[0].close_time).total_seconds()
                / 86_400.0,
                trades=len(net_trades),
                gross_eur=gross,
                net_eur=net,
                cost_eur=gross - net,
                spread_eur=spread_cost,
                slippage_eur=slippage_cost,
                commission_total_eur=commission_cost,
                atr_median=statistics.median(volatility),
                gross_multiples=tuple(
                    float(trade.pnl_eur / trade.risk_eur) for trade in gross_trades
                ),
                net_multiples=tuple(float(trade.pnl_eur / trade.risk_eur) for trade in net_trades),
                candidates=tuple(per_candidate),
            )
        )
    return cells


# ---------------------------------------------------------------------------------------
# Question 1 — ce qu'il faudrait pour que ça marche
# ---------------------------------------------------------------------------------------
def breakeven_rows(cells: list[Cell], account: Account) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for cell in cells:
        for label, risk in (
            ("backtest 10 €", float(RISK_NOMINAL)),
            ("démo réel", account.risk_per_trade_eur),
        ):
            required = cell.required_gross_r(risk)
            measured = cell.gross_r
            rows.append(
                {
                    "timeframe": cell.timeframe,
                    "symbol": cell.symbol,
                    "repere": label,
                    "risque_eur": risk,
                    "brut_mesure_r": measured,
                    "cout_par_op_r": cell.cost_r_at(risk),
                    "brut_requis_r": required,
                    "brut_requis_eur": required * risk,
                    "brut_requis_prix": cell.required_gross_price(risk),
                    "brut_mesure_prix": measured * cell.stop_effective,
                    "multiple": (required / measured if measured > 0 else None),
                    "signe_a_inverser": measured <= 0,
                }
            )
    return rows


# ---------------------------------------------------------------------------------------
# Question 2 — la suite d'opérations et la martingale
# ---------------------------------------------------------------------------------------
def walk_probabilities(
    cell: Cell,
    cost_r: float,
    paths: int,
    seed: int,
) -> dict[str, dict[str, float]]:
    """P(encore positif) après 100 / 500 / 1000 opérations, coût prélevé à chaque pas.

    Chaque pas retire une issue brute **observée** (bootstrap sur les multiples R du run
    sans coûts) puis paie le coût mesuré. Aucune loi n'est postulée : la forme de la
    distribution est celle que la campagne a réellement produite.
    """
    stream = _stream(seed)
    steps = [value - cost_r for value in cell.gross_multiples]
    population = len(steps)
    totals = [0.0] * len(CHECKPOINTS)
    positive = [0] * len(CHECKPOINTS)
    never_negative = [0] * len(CHECKPOINTS)
    finals: list[float] = []
    for _ in range(paths):
        total = 0.0
        worst = 0.0
        index = 0
        for step in range(1, WALK_STEPS + 1):
            total += steps[stream.index(population)]
            worst = min(worst, total)
            if index < len(CHECKPOINTS) and step == CHECKPOINTS[index]:
                totals[index] += total
                positive[index] += 1 if total > 0 else 0
                never_negative[index] += 1 if worst >= 0 else 0
                index += 1
        finals.append(total)
    ordered = sorted(finals)
    mean_net = statistics.fmean(steps)
    deviation = statistics.pstdev(steps)
    return {
        "par_pas": {
            "esperance_nette_r": mean_net,
            "ecart_type_r": deviation,
        },
        "checkpoints": {
            str(steps_count): {
                "probabilite_positive": positive[position] / paths,
                "esperance_cumulee_r": totals[position] / paths,
                "probabilite_jamais_sous_zero": never_negative[position] / paths,
                "approximation_normale": _normal_positive(
                    mean_net * steps_count, deviation * math.sqrt(steps_count)
                ),
                "derive_seule_r": mean_net * steps_count,
            }
            for position, steps_count in enumerate(CHECKPOINTS)
        },
        "final": {
            "median_r": ordered[len(ordered) // 2],
            "p05_r": ordered[int(0.05 * (len(ordered) - 1))],
            "p95_r": ordered[int(0.95 * (len(ordered) - 1))],
        },
    }


def _normal_positive(mean: float, deviation: float) -> float:
    if deviation <= 0:
        return 1.0 if mean > 0 else 0.0
    return 0.5 * (1.0 + math.erf(mean / (deviation * math.sqrt(2.0))))


def _stream(seed: int) -> Any:
    from tradingagent.backtest.randomness import DeterministicRandom

    return DeterministicRandom(seed)


def losses_in_a_row(p_loss: float, streaks: int, operations: int) -> float:
    """P(au moins une suite de `streaks` pertes consécutives sur `operations` opérations).

    Exact, par récurrence sur la longueur de la série courante — pas d'approximation.
    """
    if p_loss <= 0:
        return 0.0
    if p_loss >= 1:
        return 1.0 if streaks <= operations else 0.0
    win = 1.0 - p_loss
    state = [0.0] * streaks
    state[0] = 1.0
    ruined = 0.0
    for _ in range(operations):
        following = [0.0] * streaks
        for run, probability in enumerate(state):
            if probability == 0.0:
                continue
            following[0] += probability * win
            if run + 1 >= streaks:
                ruined += probability * p_loss
            else:
                following[run + 1] += probability * p_loss
        state = following
    return ruined


def ruin_streak(stake: float, equity: float, *, can_continue: bool) -> int:
    """Nombre de pertes consécutives qui arrête la martingale.

    Après k pertes consécutives le cumul perdu vaut `stake x (2^k - 1)` et la mise suivante
    vaut `stake x 2^k`.

    - `can_continue=True`  : plus petite suite k telle que la mise suivante ne tient plus
      dans ce qui reste, soit `stake x 2^k > equity - stake x (2^k - 1)`.
    - `can_continue=False` : plus petite suite k telle que le cumul perdu dépasse le solde,
      soit `stake x (2^k - 1) > equity`.
    """
    streak = 1
    while streak < 64:
        if can_continue:
            if stake * 2**streak > equity - stake * (2**streak - 1):
                return streak
        elif stake * (2**streak - 1) > equity:
            return streak
        streak += 1
    return streak


def martingale_table(cell: Cell, account: Account, p_loss: float) -> dict[str, Any]:
    stake = account.risk_per_trade_eur
    equity = account.balance_eur
    blocked = ruin_streak(stake, equity, can_continue=True)
    wiped = ruin_streak(stake, equity, can_continue=False)
    losses = {
        str(operations): {
            "probabilite_mise_impossible": losses_in_a_row(p_loss, blocked, operations),
            "probabilite_solde_epuise": losses_in_a_row(p_loss, wiped, operations),
        }
        for operations in CHECKPOINTS
    }
    return {
        "mise_initiale_eur": stake,
        "solde_eur": equity,
        "pertes_consecutives_qui_bloquent": blocked,
        "mise_apres_blocage_eur": stake * 2**blocked,
        "solde_restant_apres_blocage_eur": equity - stake * (2**blocked - 1),
        "pertes_consecutives_qui_epuisent": wiped,
        "cumul_perdu_eur": stake * (2**wiped - 1),
        "probabilite_par_horizon": losses,
        "probabilite_perte_par_operation": p_loss,
    }


def loss_probability(cell: Cell, stake: float) -> float:
    """Fréquence de perte observée à cette mise : une opération est perdue si le net est < 0."""
    if stake <= 0:
        raise ValueError("a stake must be positive")
    cost_r = cell.cost_r_at(stake)
    return sum(1 for value in cell.gross_multiples if value - cost_r < 0) / len(
        cell.gross_multiples
    )


def martingale_paths(cell: Cell, account: Account, paths: int, seed: int) -> dict[str, float]:
    """La martingale jouée pour de vrai : mise doublée après chaque perte, reset après gain.

    La commission étant forfaitaire, elle s'allège en R quand la mise double : le modèle
    est donc *généreux* avec la martingale, jamais l'inverse. Deux issues sont comptées
    séparément : `solde_epuise` (le solde tombe à zéro ou moins) et `mise_impossible` (le
    solde reste positif mais la mise doublée ne tient plus dedans) — la seconde est la
    ruine opérationnelle : la martingale n'a plus de coup suivant à jouer.
    """
    stream = _stream(seed)
    population = len(cell.gross_multiples)
    stake0 = account.risk_per_trade_eur
    impossible = 0
    exhausted = 0
    finals: list[float] = []
    peak_stakes: list[float] = []
    for _ in range(paths):
        equity = account.balance_eur
        stake = stake0
        peak = stake
        for _ in range(WALK_STEPS):
            gross = cell.gross_multiples[stream.index(population)]
            net_r = gross - cell.price_part_r - cell.commission_eur / stake
            equity += stake * net_r
            if net_r > 0:
                stake = stake0
            else:
                stake *= 2.0
                peak = max(peak, stake)
                if stake > equity:
                    impossible += 1
                    break
        if equity <= 0:
            exhausted += 1
        finals.append(equity - account.balance_eur)
        peak_stakes.append(peak)
    ordered = sorted(finals)
    return {
        "probabilite_ruine": impossible / paths,
        "probabilite_solde_epuise": exhausted / paths,
        "gain_median_eur": ordered[len(ordered) // 2],
        "gain_moyen_eur": statistics.fmean(finals),
        "p05_eur": ordered[int(0.05 * (len(ordered) - 1))],
        "p95_eur": ordered[int(0.95 * (len(ordered) - 1))],
        "mise_maximale_mediane_eur": statistics.median(peak_stakes),
    }


def fixed_stake_paths(cell: Cell, account: Account, paths: int, seed: int) -> dict[str, float]:
    """Le même nombre d'opérations, mise constante : la comparaison qui isole la martingale."""
    stream = _stream(seed)
    population = len(cell.gross_multiples)
    stake = account.risk_per_trade_eur
    cost_r = cell.cost_r_at(stake)
    finals: list[float] = []
    for _ in range(paths):
        equity = account.balance_eur
        for _ in range(WALK_STEPS):
            gross = cell.gross_multiples[stream.index(population)]
            equity += stake * (gross - cost_r)
        finals.append(equity - account.balance_eur)
    ordered = sorted(finals)
    below = sum(1 for value in finals if value <= -account.balance_eur)
    return {
        "probabilite_ruine": below / paths,
        "gain_median_eur": ordered[len(ordered) // 2],
        "gain_moyen_eur": statistics.fmean(finals),
        "p05_eur": ordered[int(0.05 * (len(ordered) - 1))],
        "p95_eur": ordered[int(0.95 * (len(ordered) - 1))],
    }


# ---------------------------------------------------------------------------------------
# Question 3 — le seuil de fréquence
# ---------------------------------------------------------------------------------------
def frequency_rows(cells: list[Cell], account: Account) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    risk = account.risk_per_trade_eur
    for cell in cells:
        cost_r = cell.cost_r_at(risk)
        net_r = cell.gross_r - cost_r
        rows.append(
            {
                "timeframe": cell.timeframe,
                "symbol": cell.symbol,
                "ops_par_jour": cell.ops_per_day,
                "risque_eur": risk,
                "cout_par_op_r": cost_r,
                "cout_par_op_eur": cost_r * risk,
                "net_par_op_r": net_r,
                "net_par_op_eur": net_r * risk,
                "eur_par_jour": net_r * risk * cell.ops_per_day,
                "eur_par_jour_en_pct_du_solde": (
                    net_r * risk * cell.ops_per_day / account.balance_eur * 100.0
                ),
                "jours_pour_2pct": (
                    -0.02 * account.balance_eur / (net_r * risk * cell.ops_per_day)
                    if net_r < 0 and cell.ops_per_day > 0
                    else None
                ),
                "jours_pour_10pct": (
                    -0.10 * account.balance_eur / (net_r * risk * cell.ops_per_day)
                    if net_r < 0 and cell.ops_per_day > 0
                    else None
                ),
            }
        )
    return rows


def frequency_ceiling(cell: Cell, account: Account, budget_eur: float) -> float | None:
    """Opérations par jour au-delà desquelles la perte dépasse `budget_eur` par jour."""
    risk = account.risk_per_trade_eur
    loss_per_operation = (cell.cost_r_at(risk) - cell.gross_r) * risk
    if loss_per_operation <= 0:
        return None
    return budget_eur / loss_per_operation


def atr_sweep(
    price: float, commission: float, risk: float, seed_atr: float, gross_r: float
) -> list[dict[str, float]]:
    """Coût par opération en fonction de l'ATR, spread et slippage exprimés en bp du prix.

    La relation est analytique et exacte sous le modèle du projet : le coût prix d'un
    aller-retour vaut `prix x (0,5 bp + 2 x 0,2 bp)` = 0,9 bp du prix, indépendamment du
    marché et de l'unité de temps ; seule la distance de stop (1,5 x ATR) le convertit en R.
    """
    price_cost = price * (0.00005 + 2.0 * 0.00002)
    rows: list[dict[str, float]] = []
    for atr_value in (0.25, 0.5, 1.0, seed_atr, 2.0, 4.0, 8.0, 16.0):
        stop = 1.5 * atr_value
        cost_r = price_cost / stop + commission / risk
        rows.append(
            {
                "atr": atr_value,
                "distance_stop": stop,
                "spread_prix": price * 0.00005,
                "slippage_prix": price * 0.00002,
                "cout_prix_aller_retour": price_cost,
                "cout_par_op_r": cost_r,
                "brut_requis_prix": cost_r * stop,
                "marge_sur_brut_mesure_r": gross_r - cost_r,
            }
        )
    return rows


def breakeven_atr(price: float, commission: float, risk: float, gross_r: float) -> float | None:
    """ATR sous lequel la cellule ne peut plus payer ses coûts, à espérance brute mesurée."""
    price_cost = price * (0.00005 + 2.0 * 0.00002)
    margin = gross_r - commission / risk
    if margin <= 0:
        return None
    return price_cost / (1.5 * margin)


# ---------------------------------------------------------------------------------------
# Contexte déjà mesuré : les seuils et la correction de sélection multiple
# ---------------------------------------------------------------------------------------
def campaign_protocol() -> dict[str, Any]:
    thresholds = json.loads((CAMPAIGNS["M15"] / "thresholds.json").read_text(encoding="utf-8"))
    multiple: dict[str, Any] = {}
    for name, directory in CAMPAIGNS.items():
        report = json.loads(next(directory.glob("*-campaign.json")).read_text(encoding="utf-8"))
        block = report["multiple_testing"]
        multiple[name] = {
            "hypotheses": block["hypotheses"],
            "decouvertes_avant": block["discoveries_before"],
            "decouvertes_apres": block["discoveries_after"],
            "alpha": block["alpha"],
            "p_value_min": min(item["p_value"] for item in block["hypotheses_detail"]),
            "generated_at": report["generated_at"],
        }
    return {"seuils": thresholds, "selection_multiple": multiple}


def account_from_disk() -> Account:
    payload = json.loads(ACCOUNT_SNAPSHOT.read_text(encoding="utf-8"))
    return Account(
        balance_eur=float(payload["balance_eur"]),
        equity_eur=float(payload["equity_eur"]),
        captured_at=payload["captured_at"],
        rows=int(payload["rows"]),
        closed_trades=int(payload["closed_trades"]),
        risk_per_trade_pct=float(payload["risk_per_trade_pct"]),
    )


def refresh_account() -> Account:
    """Read `account_snapshots` once, read-only, and freeze what it says next to the script."""
    from sqlalchemy import text

    from tradingagent.config.doctor import read_env
    from tradingagent.storage.engine import create_database_engine

    url = read_env(ROOT / ".env").get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not configured: cannot read account_snapshots")
    engine = create_database_engine(url)
    with engine.connect() as connection:
        row = connection.execute(
            text("select at, equity, balance from account_snapshots order by at desc limit 1")
        ).one()
        stats = connection.execute(text("select count(*), min(at) from account_snapshots")).one()
        trades = connection.execute(text("select count(*) from trades")).scalar()
    risk_pct = float(_simulated_risk_pct())
    payload = {
        "source": "table account_snapshots (lecture seule), solde du compte démo Deriv/MT5",
        "captured_at": row[0].isoformat(),
        "fetched_at": datetime.now(UTC).isoformat(),
        "equity_eur": str(row[1]),
        "balance_eur": str(row[2]),
        "rows": int(stats[0]),
        "first_at": stats[1].isoformat(),
        "closed_trades": int(trades or 0),
        "risk_per_trade_pct": risk_pct,
        "sql": "select at, equity, balance from account_snapshots order by at desc limit 1",
    }
    ACCOUNT_SNAPSHOT.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return account_from_disk()


def _simulated_risk_pct() -> Decimal:
    """`config/agent.yaml` -> `risk.simulated.risk_per_trade_pct`, lu, jamais recopié.

    Le schéma du dépôt valide le document ; les contrôles de références croisées de
    `load_agent_config` ne portent pas sur ce champ, et DEMO n'est pas LIVE : c'est bien le
    profil `simulated` qui dimensionne le compte démo (`risk.model.limits_for`).
    """
    from tradingagent.config._yaml import read_yaml
    from tradingagent.config.agent import AgentConfig

    document = read_yaml(AGENT_CONFIG)
    return AgentConfig.model_validate(document.data).risk.simulated.risk_per_trade_pct


# ---------------------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------------------
def money(value: float) -> str:
    return f"{value:,.2f} €".replace(",", " ")


def number(value: float, digits: int = 3) -> str:
    return f"{value:.{digits}f}"


def print_measurement(cells: list[Cell]) -> None:
    print("=" * 100)
    print("A. CE QUI EST MESURÉ (reproduction de docs/research/cost-attribution.txt)")
    print("=" * 100)
    print(
        f"{'UT':4s} {'marché':8s} {'op.':>5s} {'jours':>7s} {'op/j':>7s} "
        f"{'brut €':>10s} {'net €':>10s} {'coût €':>9s} "
        f"{'coût/op R':>10s} {'brut/op R':>10s} {'net/op R':>10s}"
    )
    for cell in cells:
        print(
            f"{cell.timeframe:4s} {cell.symbol:8s} {cell.trades:5d} {cell.days:7.2f} "
            f"{cell.ops_per_day:7.2f} {cell.gross_eur:10.2f} {cell.net_eur:10.2f} "
            f"{cell.cost_eur:9.2f} {cell.cost_r:10.4f} {cell.gross_r:10.4f} "
            f"{cell.net_r:10.4f}"
        )
    print()
    print("Décomposition séquentielle du coût (EUR), et ATR médian de la fenêtre :")
    print(
        f"{'UT':4s} {'marché':8s} {'spread €':>10s} {'slippage €':>11s} {'commission €':>13s} "
        f"{'ATR médian':>11s} {'stop eff.':>10s} {'1,5xATR méd.':>13s}"
    )
    for cell in cells:
        print(
            f"{cell.timeframe:4s} {cell.symbol:8s} {cell.spread_eur:10.2f} "
            f"{cell.slippage_eur:11.2f} {cell.commission_total_eur:13.2f} "
            f"{cell.atr_median:11.4f} {cell.stop_effective:10.4f} "
            f"{1.5 * cell.atr_median:13.4f}"
        )
    print()


def print_candidates(cells: list[Cell], threshold: float) -> dict[str, Any]:
    print("A bis. LES 18 CELLULES CANDIDATES, PROFIT FACTOR NET A 1x (seuil de la porte : 1,20)")
    print(
        f"{'UT':4s} {'marché':8s} {'candidat':14s} {'op.':>5s} {'brut €':>9s} {'net €':>9s} "
        f"{'PF brut':>8s} {'PF net 1x':>10s} {'porte':>7s}"
    )
    detail: list[dict[str, Any]] = []
    passing = 0
    for cell in cells:
        for candidate in cell.candidates:
            pf = candidate["pf_net_1x"]
            cleared = pf is not None and pf >= threshold
            passing += 1 if cleared else 0
            detail.append({"timeframe": cell.timeframe, "symbol": cell.symbol, **candidate})
            gross_pf = candidate["pf_brut"]
            print(
                f"{cell.timeframe:4s} {cell.symbol:8s} {candidate['label']:14s} "
                f"{candidate['operations']:5d} {candidate['brut_eur']:9.2f} "
                f"{candidate['net_eur']:9.2f} "
                f"{(gross_pf if gross_pf is not None else float('nan')):8.3f} "
                f"{(pf if pf is not None else float('nan')):10.3f} "
                f"{'PASSE' if cleared else 'échoue':>7s}"
            )
    print(
        f"  → {passing} cellule(s) candidate sur {len(detail)} atteignent "
        f"PF net 1x >= {threshold:.2f}"
    )
    print()
    return {"seuil": threshold, "passantes": passing, "detail": detail}


def print_breakeven(rows: list[dict[str, Any]], account: Account) -> None:
    print("=" * 100)
    print("B. QUESTION 1 — ESPÉRANCE BRUTE QU'IL FAUDRAIT POUR ÊTRE NET-POSITIF")
    print("=" * 100)
    for label in ("backtest 10 €", "démo réel"):
        print(f"-- repère : {label}")
        print(
            f"{'UT':4s} {'marché':8s} {'brut/op R':>10s} {'coût/op R':>10s} {'brut requis R':>14s} "
            f"{'requis €/op':>12s} {'requis pts':>11s} {'mesuré pts':>11s} {'multiple':>9s}"
        )
        for row in rows:
            if row["repere"] != label:
                continue
            multiple = row["multiple"]
            shown = f"x{multiple:.2f}" if multiple is not None else "signe à inverser"
            print(
                f"{row['timeframe']:4s} {row['symbol']:8s} {row['brut_mesure_r']:10.4f} "
                f"{row['cout_par_op_r']:10.4f} {row['brut_requis_r']:14.4f} "
                f"{row['brut_requis_eur']:12.2f} {row['brut_requis_prix']:11.4f} "
                f"{row['brut_mesure_prix']:11.4f} {shown:>9s}"
            )
        print()
    print(f"solde démo retenu : {money(account.balance_eur)} au {account.captured_at}")
    print(
        f"risque par opération : {account.risk_per_trade_pct:g} % → "
        f"{money(account.risk_per_trade_eur)} (config/agent.yaml, risk.simulated)"
    )
    print()


def print_walk(cells: list[Cell], account: Account, paths: int, seed: int) -> dict[str, Any]:
    print("=" * 100)
    print("C. QUESTION 2 — UNE SUITE D'OPÉRATIONS À ESPÉRANCE NÉGATIVE")
    print("=" * 100)
    print(
        f"bootstrap de {paths} chemins, {WALK_STEPS} pas, graine {seed} ; "
        f"coût prélevé à chaque pas, aucun ordre imposé entre les pas"
    )
    detail: dict[str, Any] = {}
    for label, code, risk in (
        ("backtest 10 €", "10eur", float(RISK_NOMINAL)),
        ("démo réel 27,48 €", "demo", account.risk_per_trade_eur),
    ):
        print(f"-- repère : {label} de risque par opération")
        print(
            f"{'UT':4s} {'marché':8s} {'esp. nette R':>12s} {'sigma R':>8s} "
            f"{'P(>0) 100':>10s} {'P(>0) 500':>10s} {'P(>0) 1000':>11s} "
            f"{'E[1000] R':>10s} {'loi norm.':>10s}"
        )
        for cell in cells:
            key = f"{code}-{cell.timeframe}-{cell.symbol}"
            walk = walk_probabilities(cell, cell.cost_r_at(risk), paths, seed + len(detail))
            detail[key] = walk
            block = walk["checkpoints"]["1000"]
            print(
                f"{cell.timeframe:4s} {cell.symbol:8s} "
                f"{walk['par_pas']['esperance_nette_r']:12.4f} "
                f"{walk['par_pas']['ecart_type_r']:8.3f} "
                f"{walk['checkpoints']['100']['probabilite_positive']:10.3f} "
                f"{walk['checkpoints']['500']['probabilite_positive']:10.3f} "
                f"{block['probabilite_positive']:11.3f} "
                f"{block['derive_seule_r']:10.1f} {block['approximation_normale']:10.3f}"
            )
        print()
    print("P(jamais sous zéro) à 1000 pas, et intervalle des résultats finaux :")
    for key, walk in detail.items():
        block = walk["checkpoints"]["1000"]
        final = walk["final"]
        print(
            f"  {key:20s} P(jamais sous 0) {block['probabilite_jamais_sous_zero']:.3f} · "
            f"médiane {final['median_r']:9.1f} R · p05 {final['p05_r']:9.1f} R · "
            f"p95 {final['p95_r']:9.1f} R · E = {block['esperance_cumulee_r']:.1f} R"
        )
    print()
    return detail


def print_martingale(cells: list[Cell], account: Account, paths: int, seed: int) -> dict[str, Any]:
    print("-" * 100)
    print("C bis. MARTINGALE — MISE DOUBLÉE APRÈS CHAQUE PERTE")
    print("-" * 100)
    print(
        f"mise initiale {money(account.risk_per_trade_eur)} "
        f"({account.risk_per_trade_pct:g} % de {money(account.balance_eur)}), "
        f"doublée après chaque opération perdante, réinitialisée après une gagnante"
    )
    detail: dict[str, Any] = {}
    for cell in cells:
        p_loss = loss_probability(cell, account.risk_per_trade_eur)
        table = martingale_table(cell, account, p_loss)
        played = martingale_paths(cell, account, paths, seed + len(detail))
        fixed = fixed_stake_paths(cell, account, paths, seed + 100 + len(detail))
        key = f"{cell.timeframe}-{cell.symbol}"
        detail[key] = {
            "table": table,
            "simulation": played,
            "mise_constante": fixed,
            "p_loss": p_loss,
        }
        blocked = table["pertes_consecutives_qui_bloquent"]
        wiped = table["pertes_consecutives_qui_epuisent"]
        print(
            f"{cell.timeframe:4s} {cell.symbol:8s} P(perte/op)={p_loss:.3f} → "
            f"{blocked} pertes consécutives bloquent la martingale "
            f"(la mise suivante serait {money(table['mise_apres_blocage_eur'])} pour "
            f"{money(table['solde_restant_apres_blocage_eur'])} restants) ; "
            f"{wiped} l'effacent ({money(table['cumul_perdu_eur'])})"
        )
        probabilities = table["probabilite_par_horizon"]
        print(
            f"     P(une suite de {blocked}) = "
            + " · ".join(
                f"{operations} op. : {probabilities[operations]['probabilite_mise_impossible']:.4f}"
                for operations in ("100", "500", "1000")
            )
            + f"   |   P(une suite de {wiped}) = "
            + " · ".join(
                f"{operations} op. : {probabilities[operations]['probabilite_solde_epuise']:.4f}"
                for operations in ("100", "500", "1000")
            )
        )
        print(
            f"     1000 op. martingale    : ruine {played['probabilite_ruine']:.3f}, "
            f"solde épuisé {played['probabilite_solde_epuise']:.3f}, "
            f"gain médian {money(played['gain_median_eur'])}, "
            f"moyenne {money(played['gain_moyen_eur'])}, "
            f"p05 {money(played['p05_eur'])}, p95 {money(played['p95_eur'])}"
        )
        print(
            f"     1000 op. mise constante: ruine {fixed['probabilite_ruine']:.3f}, "
            f"gain médian {money(fixed['gain_median_eur'])}, "
            f"moyenne {money(fixed['gain_moyen_eur'])}, "
            f"p05 {money(fixed['p05_eur'])}, p95 {money(fixed['p95_eur'])}"
        )
    print()
    return detail


def print_frequency(cells: list[Cell], account: Account) -> dict[str, Any]:
    print("=" * 100)
    print("D. QUESTION 3 — LE SEUIL DE FRÉQUENCE, EN EUROS PAR JOUR SUR LE SOLDE RÉEL")
    print("=" * 100)
    print(
        f"solde démo {money(account.balance_eur)} · risque {account.risk_per_trade_pct:g} % "
        f"= {money(account.risk_per_trade_eur)}/op"
    )
    print(
        f"{'UT':4s} {'marché':8s} {'op/j mesuré':>12s} {'coût/op R':>10s} {'net/op R':>10s} "
        f"{'net €/op':>10s} {'€/jour':>11s} {'%/jour':>8s} {'j → -2 %':>9s} {'j → -10 %':>10s}"
    )
    rows = frequency_rows(cells, account)
    for row in rows:
        to_two = row["jours_pour_2pct"]
        to_ten = row["jours_pour_10pct"]
        two = f"{to_two:9.2f}" if to_two is not None else f"{'—':>9s}"
        ten = f"{to_ten:10.2f}" if to_ten is not None else f"{'—':>10s}"
        print(
            f"{row['timeframe']:4s} {row['symbol']:8s} {row['ops_par_jour']:12.2f} "
            f"{row['cout_par_op_r']:10.4f} {row['net_par_op_r']:10.4f} "
            f"{row['net_par_op_eur']:10.3f} {row['eur_par_jour']:11.2f} "
            f"{row['eur_par_jour_en_pct_du_solde']:8.3f} {two} {ten}"
        )
    print("  (— = l'espérance nette est positive : aucun délai de ruine à afficher)")
    print()
    print("Plafond de fréquence pour un budget de perte donné (opérations par jour) :")
    for cell in cells:
        ceilings = {budget: frequency_ceiling(cell, account, budget) for budget in (1.0, 5.0, 10.0)}
        if all(value is None for value in ceilings.values()):
            net = cell.gross_r - cell.cost_r_at(account.risk_per_trade_eur)
            print(
                f"  {cell.timeframe:4s} {cell.symbol:8s} aucun plafond : "
                f"l'espérance nette est positive ({net:+.4f} R/op)"
            )
            continue
        shown = " · ".join(
            f"{budget:.0f} €/j → {value:.1f} op/j"
            for budget, value in ceilings.items()
            if value is not None
        )
        print(f"  {cell.timeframe:4s} {cell.symbol:8s} {shown}")
    print()
    print("Seuil de rentabilité en ATR (espérance brute mesurée conservée, coûts du projet) :")
    gold_m1 = next(cell for cell in cells if cell.timeframe == "M1" and cell.symbol == "XAUUSD")
    gold_m15 = next(cell for cell in cells if cell.timeframe == "M15" and cell.symbol == "XAUUSD")
    for cell in (gold_m1, gold_m15):
        for label, risk in (
            ("10 €", float(RISK_NOMINAL)),
            ("démo", account.risk_per_trade_eur),
        ):
            threshold = breakeven_atr(cell.price, cell.commission_eur, risk, cell.gross_r)
            if threshold is None:
                shown = "aucun ATR ne suffit : la commission seule dépasse le brut mesuré"
            else:
                ratio = threshold / cell.atr_median
                shown = f"ATR ≥ {threshold:.3f} ({ratio:.2f} x l'ATR médian {cell.atr_median:.3f})"
            print(f"  {cell.timeframe} {cell.symbol} risque {label:5s} : {shown}")
    print()
    return {"lignes": rows}


def print_atr_sweep(cells: list[Cell], account: Account) -> dict[str, Any]:
    print("E. COÛT PAR OPÉRATION EN FONCTION DE L'ATR ET DU SPREAD")
    print(
        "   coût/op (R) = prix x 0,9 bp / (1,5 x ATR) + commission / risque"
        "   [0,9 bp = spread 0,5 bp + 2 x slippage 0,2 bp, aller-retour]"
    )
    detail: dict[str, Any] = {}
    targets = [cell for cell in cells if cell.timeframe == "M1"]
    targets.append(
        next(cell for cell in cells if cell.timeframe == "M15" and cell.symbol == "XAUUSD")
    )
    for cell in targets:
        for label, risk in (
            ("risque 10 €", float(RISK_NOMINAL)),
            ("risque démo", account.risk_per_trade_eur),
        ):
            rows = atr_sweep(cell.price, cell.commission_eur, risk, cell.atr_median, cell.gross_r)
            print(
                f"-- {cell.timeframe} {cell.symbol} (prix de référence {cell.price:.2f}), "
                f"{label} = {risk:.2f} €/op"
            )
            print(
                f"   {'ATR':>8s} {'stop 1,5xATR':>13s} {'spread pts':>11s} {'coût/op R':>10s} "
                f"{'brut requis pts':>16s} {'brut mesuré - coût':>19s}"
            )
            for row in rows:
                marker = " <- ATR mesuré" if abs(row["atr"] - cell.atr_median) < 1e-9 else ""
                print(
                    f"   {row['atr']:8.3f} {row['distance_stop']:13.3f} "
                    f"{row['spread_prix']:11.5f} {row['cout_par_op_r']:10.4f} "
                    f"{row['brut_requis_prix']:16.4f} "
                    f"{row['marge_sur_brut_mesure_r']:19.4f}{marker}"
                )
            detail[f"{cell.timeframe}-{cell.symbol}-{label}"] = rows
    print()
    return detail


def print_protocol(protocol: dict[str, Any]) -> None:
    print("=" * 100)
    print("F. LE CADRE DÉJÀ MESURÉ (rappel, jamais rouvert ici)")
    print("=" * 100)
    thresholds = protocol["seuils"]
    shown = ", ".join(
        f"{key}={value}"
        for key, value in sorted(thresholds.items())
        if key not in {"digest", "format", "version"}
    )
    print(f"seuils de campagne : {shown}")
    for name, block in sorted(protocol["selection_multiple"].items()):
        print(
            f"  {name} : {block['hypotheses']} hypothèses, p-value min {block['p_value_min']:.4f}, "
            f"découvertes {block['decouvertes_avant']} → {block['decouvertes_apres']} après "
            f"Benjamini-Hochberg (alpha={block['alpha']}) — {block['generated_at']}"
        )
    print()


def build(
    cells: list[Cell],
    account: Account,
    seed: int,
    paths: int,
    protocol: dict[str, Any],
    *,
    walk_detail: dict[str, Any],
    martingale_detail: dict[str, Any],
    atr_detail: dict[str, Any],
) -> dict[str, Any]:
    martingale: dict[str, Any] = {}
    for key, block in martingale_detail.items():
        martingale[key] = {
            "perte_par_operation": block["p_loss"],
            "table": block["table"],
            "simulation_1000_operations": block["simulation"],
            "mise_constante_1000_operations": block["mise_constante"],
        }
    return {
        "genere_le": datetime.now(UTC).isoformat(),
        "graine": seed,
        "chemins_monte_carlo": paths,
        "risque_nominal_backtest_eur": str(RISK_NOMINAL),
        "compte_demo": {
            "solde_eur": account.balance_eur,
            "equity_eur": account.equity_eur,
            "capture": account.captured_at,
            "lignes": account.rows,
            "operations_cloturees": account.closed_trades,
            "risque_par_operation_pct": account.risk_per_trade_pct,
            "risque_par_operation_eur": account.risk_per_trade_eur,
        },
        "cellules": [
            {
                "timeframe": cell.timeframe,
                "symbole": cell.symbol,
                "dataset_id": cell.dataset_id,
                "prix": cell.price,
                "spread_prix": cell.spread_price,
                "slippage_prix": cell.slippage_price,
                "commission_eur": cell.commission_eur,
                "bougies_validation": cell.bars,
                "jours": cell.days,
                "operations": cell.trades,
                "operations_par_jour": cell.ops_per_day,
                "brut_eur": cell.gross_eur,
                "net_eur": cell.net_eur,
                "cout_eur": cell.cost_eur,
                "spread_eur": cell.spread_eur,
                "slippage_eur": cell.slippage_eur,
                "commission_totale_eur": cell.commission_total_eur,
                "atr_median": cell.atr_median,
                "distance_stop_effective": cell.stop_effective,
                "brut_par_op_r": cell.gross_r,
                "cout_par_op_r": cell.cost_r,
                "net_par_op_r": cell.net_r,
                "cout_par_op_r_demo": cell.cost_r_at(account.risk_per_trade_eur),
                "net_par_op_r_demo": cell.gross_r - cell.cost_r_at(account.risk_per_trade_eur),
                "candidats": list(cell.candidates),
            }
            for cell in cells
        ],
        "question_1": breakeven_rows(cells, account),
        # Le JSON ne recalcule rien : il reprend les objets que le rapport vient d'imprimer, pour
        # qu'un chiffre du document et le chiffre du JSON soient le même tirage, pas deux voisins.
        "question_2": {
            "marche_aleatoire": walk_detail,
            "martingale": martingale,
            "seuil_atr_rentabilite": {
                f"{cell.timeframe}-{cell.symbol}": {
                    f"{label}": breakeven_atr(cell.price, cell.commission_eur, risk, cell.gross_r)
                    for label, risk in (
                        ("10eur", float(RISK_NOMINAL)),
                        ("demo", account.risk_per_trade_eur),
                    )
                }
                for cell in cells
            },
        },
        "question_3": frequency_rows(cells, account),
        "atr_et_spread": atr_detail,
        "seuils": protocol,
    }


def main(argv: list[str] | None = None) -> int:
    # La console Windows par défaut est en cp1252 et refuse « → » ou « ≥ ». Le rapport est en
    # français : il sort en UTF-8, et reste lisible redirigé dans un fichier.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--paths", type=int, default=DRIFT_PATHS, help="chemins Monte-Carlo de la marche aléatoire"
    )
    parser.add_argument(
        "--martingale-paths",
        type=int,
        default=MARTINGALE_PATHS,
        help="chemins Monte-Carlo de la martingale",
    )
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument(
        "--refresh-account",
        action="store_true",
        help="relire account_snapshots (lecture seule) et regeler le solde du compte démo",
    )
    args = parser.parse_args(argv)

    runner = load_runner()
    account = refresh_account() if args.refresh_account else account_from_disk()
    cells: list[Cell] = []
    for timeframe, directory in DATASETS.items():
        cells.extend(measure_cell(runner, timeframe, directory))
    cells.sort(key=lambda cell: (cell.timeframe, cell.symbol))

    protocol = campaign_protocol()
    print_measurement(cells)
    print_candidates(cells, float(protocol["seuils"]["min_profit_factor_net"]))
    print_breakeven(breakeven_rows(cells, account), account)
    walk_detail = print_walk(cells, account, args.paths, args.seed)
    martingale_detail = print_martingale(cells, account, args.martingale_paths, args.seed)
    print_frequency(cells, account)
    atr_detail = print_atr_sweep(cells, account)
    print_protocol(protocol)

    if args.json is not None:
        payload = build(
            cells,
            account,
            args.seed,
            args.paths,
            protocol,
            walk_detail=walk_detail,
            martingale_detail=martingale_detail,
            atr_detail=atr_detail,
        )
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"faits écrits dans {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
