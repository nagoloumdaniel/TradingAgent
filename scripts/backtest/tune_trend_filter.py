"""Un reglage moins selectif du filtre de tendance conserve-t-il l'effet mesure ?

**La situation, et la contrainte qui la definit.** `vwap_pullback` porte un filtre de tendance
eteint par defaut (`trend_filter: false`). Allume au seuil par defaut de `trend_of`, il ameliore
le bitcoin sur les deux fenetres (PF net 0,7148 -> 0,9643 en entrainement, 0,6818 -> 1,5376 en
validation, net -327,71 EUR -> +47,34 EUR) et l'or sur la validation seule (0,5454 -> 0,9630),
mais il ne garde que **14,6 %** et **14,3 %** des operations
(`docs/research/2026-10-11-filtre-de-tendance-mesure.md`). Ce qui laisse 30 et 33 operations en
validation, soit exactement `min_trades` (30) : le seuil du projet, pas une marge.

L'effet trouve le 2026-10-11 portait sur 157 et 133 operations, mesure avec les EMA 10/30 de
`indicators/features.py::entry_features`. Le filtre de la regle lit `trend_of` avec les EMA 20/50
de la strategie, plus lentes : la tendance y est plus difficile a nommer, `NEUTRAL` y est plus
frequent, et le filtre refuse donc davantage. C'est la difference entre les deux lectures.

**La seule chose qui est balayee, et pourquoi rien d'autre ne l'est.** `trend_filter` est un
booleen et `trend_slope_atr` est le seuil de pente sur UNE barre qui fait dire a `trend_of` que le
marche tend encore : c'est lui qui determine la frequence de `NEUTRAL`, donc la selectivite. Les
EMA passees a `trend_of` sont `ema_fast`/`ema_slow`, qui sont **aussi** les EMA de la strategie :
les changer changerait la regle entiere, pas son filtre, et ce serait une autre strategie. Ce
script ne touche donc ni `ema_fast`, ni `ema_slow`, ni aucun autre parametre : un seul nombre
bouge, et il est bascule **en memoire** dans un `CandidateSpec`.

**La plage balayee, et sa justification.** Neuf valeurs :
0,001 / 0,002 / 0,005 / 0,01 / 0,02 / 0,03 / **0,05** / 0,08 / 0,12.

* `0,05` est le seuil par defaut du module de regime (`indicators.regime.DEFAULT_TREND_THRESHOLD`),
  lu ici et non recopie : c'est la reference a battre, deja mesuree et publiee ;
* `0,005` est la pente par barre que la regle exige **deja** d'elle-meme (`min_slope_atr`,
  mesuree sur 10 barres) : c'est un point de comparaison nomme par le manifeste, pas un nombre
  invente pour l'occasion ;
* sous `0,001`, la pente d'une barre d'EMA(50) est indistinguable de son propre bruit ;
* au-dessus de `0,12`, le filtre ne garde presque plus rien ;
* la plage couvre les **deux cotes** du seuil : un balayage a sens unique ne montrerait pas que la
  direction de la selectivite est bien celle qu'on croit.

**LA REGLE DE DECISION, ECRITE AVANT TOUTE MESURE, ET QUI NE SERA PAS AJUSTEE.** Un reglage `t`
est RETENU si et seulement si les quatre conditions tiennent ensemble :

(a) SELECTIVITE — le filtre garde **plus** d'operations qu'au seuil par defaut, sur chacun des
    quatre blocs (marche x fenetre) ;
(b) AMELIORATION — le PF net est **superieur** a celui de la meme regle **sans filtre** sur
    l'entrainement ET la validation, pour les DEUX marches ;
(c) PLANCHER D'OPERATIONS — le filtre garde **au moins 30 %** des operations sur chacun des quatre
    blocs ;
(d) MARGE SUR LE SEUIL — **au moins 60 operations** en validation pour chaque marche, soit le
    double de `min_trades`, pour que le resultat ne tienne pas sur le seuil.

Le plancher (c) est fixe sur les QUATRE blocs et pas seulement sur la validation : un reglage qui
tiendrait 30 % en validation et 15 % en entrainement serait un effet de regime, pas une
selectivite.

**Si plusieurs reglages sont retenus, l'ordre est fixe d'avance** : celui dont le PF net minimal
sur les quatre blocs est le plus haut. Aucun autre critere n'est regarde apres coup.

**Si aucun reglage ne satisfait les quatre conditions, c'est le resultat** : le rapport l'ecrit et
rien n'est adopte. Le balayage a une direction triviale sur la selectivite — un seuil plus bas
rend `NEUTRAL` plus rare, donc garde plus d'operations — et ce n'est pas cette direction qui est
interessante : ce qui n'est pas trivial, et ce que ce script mesure, est de savoir **si l'effet
survit quand on garde plus d'operations**. C'est precisement ce qui peut echouer.

**Le compte des hypotheses est publie avant les chiffres : 9 seuils x 2 marches = 18.** La
correction de tests multiples du depot (`research.protocol.price_false_discoveries`,
Benjamini-Hochberg) est appliquee sur ces 18 p-values, chacune etant la p-value unilaterale du t
de Student sur le R net par operation des deux fenetres reunies, d'un reglage sur un marche. La
loi de Student vient de `edge_location.py` (deja ecrite et verifiee la-bas) et est **re-verifiee
ici** contre des valeurs tabulees : `--self-check` refuse de publier si un controle echoue.

**Ce qui n'entre PAS dans la regle, et qui est publie quand meme** (lu, jamais utilise pour
retenir) : la fraction conservee en validation seule, la fraction de **signaux** conservee (et non
d'operations — le harnais n'ouvre qu'une position a la fois, donc moins de signaux ne veut pas
dire moins d'operations), les p-values par fenetre, et l'intervalle de confiance a 95 % de
l'esperance en R de chaque reglage.

**Verification avant de conclure.** Le script n'est comparable a la mesure publiee que s'il la
reproduit : la baseline sans filtre **et** le seuil par defaut sont confrontes, bloc par bloc, a
`docs/research/measurements/trend-filter-effect.json`, et le script refuse de publier si un champ
ne coincide pas. Avec `--bars`, le jeu est tronque et cette confrontation est desactivee (ce n'est
alors pas une mesure).

**Lecture seule.** Le jeu scelle n'est pas ouvert : `split_dataset` n'est appele que pour nommer
la fenetre reservee, `SealedSet.unlock` ne recoit jamais son jeton et `confirm_holdout` reste
faux. Aucun manifeste n'est reecrit, `config/` n'est pas touche, aucun seuil de
`docs/research/thresholds.json` n'est lu ni ecrit, aucun ordre n'est envoye, aucune ecriture en
base de production.

    uv run python scripts/backtest/tune_trend_filter.py --self-check
    uv run python scripts/backtest/tune_trend_filter.py --bars 9000 --no-write   # plomberie
    uv run python scripts/backtest/tune_trend_filter.py --jobs 4
"""

import argparse
import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

from tradingagent.analytics.model import MIN_SIGNIFICANT_SAMPLE
from tradingagent.indicators.regime import DEFAULT_TREND_THRESHOLD
from tradingagent.research.protocol import (
    DEFAULT_FALSE_DISCOVERY_RATE,
    price_false_discoveries,
    split_dataset,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from edge_location import _significance, student_quantile, student_two_sided_p
from measure_strategy import _load_current_stats
from sweep_exit_geometry import _refs_from_agent_config

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "docs" / "research" / "datasets-volume"
OUTPUT_DIR = ROOT / "docs" / "research" / "tune-trend-filter"
PUBLISHED_MEASUREMENT = ROOT / "docs" / "research" / "measurements" / "trend-filter-effect.json"

#: Les neuf seuils balayes, justifies en tete de fichier. Le seuil par defaut doit y figurer :
#: `_thresholds()` refuse de mesurer une plage qui ne contiendrait pas la reference a battre.
THRESHOLDS: tuple[float, ...] = (0.001, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12)

#: (c) — plancher d'operations conservees, sur chacun des quatre blocs. Fixe avant la mesure.
KEPT_FLOOR = 0.30

#: (d) — operations minimales en validation, par marche. Fixe avant la mesure.
MIN_VALIDATION_TRADES = 60

WINDOWS = ("train", "validation")

#: Les identifiants de `OutcomeKind` ne sont pas utilises ici : `filtered_signals` compte le
#: filtre d'entree du **manifeste** (`EntryFilterRule`), inerte dans les deux manifestes de
#: `vwap_pullback`. Le filtre de tendance sort par `evaluate` -> `None`, donc en `NO_SIGNAL` : le
#: harnais ne peut pas distinguer les deux, et c'est pourquoi la selectivite est mesuree sur les
#: **operations** et sur les **signaux**, jamais sur `filtered_signals`.
SELF_CHECKS: tuple[tuple[str, float, float, float], ...] = (
    ("t(0.975, 10) = 2.228138852", student_quantile(0.975, 10), 2.228138852, 1e-6),
    ("t(0.975, 1) = 12.70620474", student_quantile(0.975, 1), 12.70620474, 1e-6),
    ("t(0.95, 29) = 1.699127027", student_quantile(0.95, 29), 1.699127027, 1e-6),
    ("t(0.975, 100000) = 1.959963985", student_quantile(0.975, 100000), 1.959963985, 1e-9),
    ("P(|T| >= 2.228138852, 10) = 0.05", student_two_sided_p(2.228138852, 10), 0.05, 1e-9),
)

#: Les quatre conditions, dans l'ordre ou elles sont ecrites en tete de fichier.
CRITERIA = (
    "a_more_kept_than_default",
    "b_pf_above_baseline_both_windows_both_markets",
    "c_kept_at_least_floor",
    "d_validation_trades_at_least_minimum",
)


class _Log:
    """La sortie standard, gardee en memoire pour etre ecrite telle quelle a cote du JSON."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, text: str = "") -> None:
        print(text)
        self.lines.append(text)

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _thresholds() -> tuple[float, ...]:
    """Les seuils balayes, avec le defaut du module **dans** la liste et non a cote d'elle."""
    if DEFAULT_TREND_THRESHOLD not in THRESHOLDS:
        raise SystemExit(
            f"DEFAULT_TREND_THRESHOLD={DEFAULT_TREND_THRESHOLD} n'est pas dans la plage balayee : "
            "la reference a battre doit etre mesuree comme les autres"
        )
    return THRESHOLDS


def _self_check(emit: Any) -> dict[str, Any]:
    """La loi de Student confrontee a des valeurs tabulees, avant toute mesure."""
    emit("--- self-check : loi de Student contre valeurs tabulees ---")
    results: list[dict[str, Any]] = []
    for label, measured, expected, tolerance in SELF_CHECKS:
        error = abs(measured - expected)
        ok = error <= tolerance
        results.append(
            {
                "check": label,
                "measured": measured,
                "expected": expected,
                "tolerance": tolerance,
                "error": error,
                "ok": ok,
            }
        )
        emit(
            f"  [{'ok ' if ok else 'NON'}] {label:<34} mesure {measured:.9f}"
            f"  erreur {error:.2e} (tolerance {tolerance:.0e})"
        )
    failed = [item for item in results if not item["ok"]]
    if failed:
        raise SystemExit(f"self-check refuse : {len(failed)} controle(s) hors tolerance")
    emit(f"  les {len(results)} controles passent : la loi de Student est utilisable")
    return {"checks": results, "passed": True}


def _variant(spec: Any, *, threshold: float | None) -> Any:
    """La meme regle, avec le filtre bascule **en memoire**. Aucun manifeste n'est reecrit.

    `threshold=None` rend la production telle qu'elle tourne (`trend_filter: false`). Sinon le
    filtre est allume et `trend_slope_atr` prend la valeur balayee. Rien d'autre ne bouge :
    `ema_fast`, `ema_slow`, la geometrie et les seuils de la regle restent ceux du manifeste.
    """
    parameters = dict(spec.parameters)
    if threshold is None:
        parameters["trend_filter"] = False
    else:
        parameters["trend_filter"] = True
        parameters["trend_slope_atr"] = float(threshold)
    return replace(spec, parameters=parameters)


def _r_values(run: Any) -> list[float]:
    """Le R **net** de chaque operation : le PnL realise divise par le risque de l'operation."""
    values: list[float] = []
    for trade in run.trades:
        risk = float(trade.risk_eur)
        values.append(float(trade.pnl_eur) / risk if risk else 0.0)
    return values


def _summary(run: Any, stats: Any) -> dict[str, Any]:
    """Un bloc mesure : les chiffres du rapport, plus les R par operation pour les tests."""
    performance = run.performance
    values = _r_values(run)
    significance = _significance(values)
    return {
        "trades": performance.trades,
        "wins": performance.wins,
        "losses": performance.losses,
        "win_rate": stats.number(performance.win_rate),
        "net_profit": stats.number(performance.net_profit),
        "profit_factor": stats.number(performance.profit_factor),
        "expectancy": stats.number(performance.expectancy),
        "max_drawdown": stats.number(performance.max_drawdown),
        "signals": run.signals,
        "skipped_no_room": run.skipped_no_room,
        "decisions": run.decisions,
        "mean_r": round(math.fsum(values) / len(values), 6) if values else None,
        "stderr_r": significance["stderr_r"],
        "t_statistic": significance["t_statistic"],
        "p_value_one_sided": significance["p_value_one_sided"],
        "ci95_low_r": significance["ci95_low_r"],
        "ci95_high_r": significance["ci95_high_r"],
        "r_values": values,
    }


def _measure(payload: tuple[str, float | None, int, str, str]) -> dict[str, Any]:
    """Un bloc de mesure : un marche, un seuil, les deux fenetres. Execute par un processus.

    `threshold=None` mesure la production (filtre eteint). Le jeu scelle n'est jamais lu : seuls
    son effectif et sa **fenetre** sont nommes, pour que le rapport puisse dire quelle periode n'a
    pas ete regardee.
    """
    market, threshold, bars, dataset_dir, agent_config = payload

    from tradingagent.backtest.harness import run_backtest

    stats = _load_current_stats()
    refs = _refs_from_agent_config(Path(agent_config))
    dataset = stats.DatasetStore(Path(dataset_dir)).load_all()[market]
    if bars > 0:
        dataset = replace(
            dataset,
            dataset_id=f"{dataset.dataset_id}-tronque-{bars}",
            candles=dataset.candles[-bars:],
        )
    split = split_dataset(dataset, token=f"tune-trend-filter:{market}", anchor=True)
    spec = _variant(stats.spec_for(market, refs[market]), threshold=threshold)
    config = replace(stats.config_for(market, dataset), targets_at_first_only=True)

    measured: dict[str, Any] = {
        "market": market,
        "ref": refs[market],
        "threshold": threshold,
        "filter": threshold is not None,
        "timeframe": dataset.timeframe.value,
        "dataset_id": dataset.dataset_id,
        "dataset_fingerprint": dataset.fingerprint,
        "bars": len(dataset.candles),
        "sealed_bars_not_read": split.holdout.size,
        "sealed_window_not_read": [
            split.holdout.window().start.isoformat(),
            split.holdout.window().end.isoformat(),
        ],
    }
    for name, candles in (("train", split.train), ("validation", split.validation)):
        run = run_backtest(
            spec.factory(spec.parameters),
            spec.manifest,
            {dataset.timeframe: candles},
            config,
        )
        measured[name] = _summary(run, stats)
        measured[f"{name}_window"] = [
            candles[0].open_time.isoformat(),
            candles[-1].close_time.isoformat(),
        ]
    return measured


def _block(market: str, window: str) -> str:
    return f"{market}|{window}"


def _kept(on: dict[str, Any], off: dict[str, Any], key: str) -> float | None:
    """La part conservee par le filtre, en operations (`trades`) ou en signaux (`signals`)."""
    reference = off[key]
    return on[key] / reference if reference else None


def _row(
    threshold: float,
    measured: dict[str, dict[float, dict[str, Any]]],
    baseline: dict[str, dict[str, Any]],
    markets: list[str],
    default_kept: dict[str, float | None],
) -> dict[str, Any]:
    """Un reglage, ses quatre blocs, ses quatre conditions, et les lectures secondaires."""
    blocks = [_block(market, window) for market in markets for window in WINDOWS]
    kept: dict[str, float | None] = {}
    kept_signals: dict[str, float | None] = {}
    improves: dict[str, bool] = {}
    profit_factors: dict[str, float | None] = {}
    for market in markets:
        for window in WINDOWS:
            block = _block(market, window)
            on = measured[market][threshold][window]
            off = baseline[market][window]
            kept[block] = _kept(on, off, "trades")
            kept_signals[block] = _kept(on, off, "signals")
            profit_factors[block] = on["profit_factor"]
            improves[block] = (
                on["profit_factor"] is not None
                and off["profit_factor"] is not None
                and on["profit_factor"] > off["profit_factor"]
            )
    more_kept = {}
    for block in blocks:
        reference = default_kept.get(block)
        value = kept[block]
        more_kept[block] = value is not None and reference is not None and value > reference
    return {
        "threshold": threshold,
        "kept_trades": kept,
        "kept_signals": kept_signals,
        "profit_factors": profit_factors,
        "improves_above_baseline": improves,
        "criteria": {
            "a_more_kept_than_default": all(more_kept[block] for block in blocks),
            "b_pf_above_baseline_both_windows_both_markets": all(
                improves[block] for block in blocks
            ),
            "c_kept_at_least_floor": all(
                kept[block] is not None and kept[block] >= KEPT_FLOOR for block in blocks
            ),
            "d_validation_trades_at_least_minimum": all(
                measured[market][threshold]["validation"]["trades"] >= MIN_VALIDATION_TRADES
                for market in markets
            ),
        },
        "worst_profit_factor": (
            min(value for value in profit_factors.values() if value is not None)
            if all(value is not None for value in profit_factors.values())
            else None
        ),
        "retained": False,
    }


def _reproduction(
    baseline: dict[str, dict[str, Any]],
    measured: dict[str, dict[float, dict[str, Any]]],
    markets: list[str],
    path: Path,
) -> dict[str, Any]:
    """La mesure publiee du 2026-10-11, reproduite bloc par bloc avant toute conclusion.

    Sans cette confrontation, un balayage ne sait pas s'il mesure la meme chose que le document
    qui l'a motive. Elle porte sur les deux lignes publiees : la production sans filtre, et le
    filtre au seuil par defaut.
    """
    published = json.loads(path.read_text(encoding="utf-8"))
    fields = ("trades", "wins", "losses", "net_profit", "profit_factor", "expectancy")
    deltas: list[dict[str, Any]] = []
    for row in published["rows"]:
        market = str(row["market"])
        if market not in markets:
            continue
        found_row = measured[market][DEFAULT_TREND_THRESHOLD] if row["filter"] else baseline[market]
        for window in WINDOWS:
            for field in fields:
                if field not in row[window] or field not in found_row[window]:
                    deltas.append(
                        {
                            "market": market,
                            "filter": bool(row["filter"]),
                            "window": window,
                            "field": field,
                            "published": row[window].get(field),
                            "measured": found_row[window].get(field),
                            "error": math.inf,
                        }
                    )
                    continue
                expected = row[window][field]
                found = found_row[window][field]
                if expected is None or found is None:
                    error = 0.0 if expected == found else math.inf
                elif isinstance(expected, int) and isinstance(found, int):
                    error = float(abs(expected - found))
                else:
                    error = abs(float(expected) - float(found))
                    tolerance = max(1e-3, 1e-6 * abs(float(expected)))
                    error = 0.0 if error <= tolerance else error
                deltas.append(
                    {
                        "market": market,
                        "filter": bool(row["filter"]),
                        "window": window,
                        "field": field,
                        "published": expected,
                        "measured": found,
                        "error": error,
                    }
                )
    worst = max((item["error"] for item in deltas), default=0.0)
    return {
        "source": str(path.relative_to(ROOT)),
        "compared_fields": list(fields),
        "checks": len(deltas),
        "max_absolute_error": worst,
        "reproduced": worst == 0.0,
        "deltas": [item for item in deltas if item["error"] != 0.0],
    }


def _correct(
    rows: dict[float, dict[str, Any]],
    measured: dict[str, dict[float, dict[str, Any]]],
    markets: list[str],
) -> dict[str, Any]:
    """La correction du depot sur les 18 hypotheses, et ce qu'elle laisse debout.

    Une hypothese est un couple (reglage, marche) : 9 seuils x 2 marches. La p-value est celle du
    t de Student unilateral sur le R net par operation des deux fenetres reunies -- la lecture du
    depot (`edge_location.py`, `research.protocol.price_false_discoveries`). Un reglage dont la
    validation porte moins de `min_trades` operations est compte avec `p = 1` plutot que retire :
    le nombre de tests doit egaler le nombre de tentatives.
    """
    labelled: list[tuple[str, float | None]] = []
    significance: dict[str, Any] = {}
    retained_by_label: dict[str, bool] = {}
    for threshold, row in rows.items():
        for market in markets:
            values = [
                value
                for window in WINDOWS
                for value in measured[market][threshold][window]["r_values"]
            ]
            test = _significance(values)
            enough = measured[market][threshold]["validation"]["trades"] >= MIN_SIGNIFICANT_SAMPLE
            label = f"{market}|trend_slope_atr={threshold:g}"
            significance[label] = {
                "threshold": threshold,
                "market": market,
                "observations": len(values),
                "validation_trades": measured[market][threshold]["validation"]["trades"],
                "conclusive": enough,
                **test,
            }
            retained_by_label[label] = bool(row["retained"])
            labelled.append((label, test["p_value_one_sided"] if enough else None))

    control = price_false_discoveries(labelled, alpha=DEFAULT_FALSE_DISCOVERY_RATE)
    survivors = [outcome for outcome in control.outcomes if outcome.significant]
    return {
        "method": control.report.method,
        "alpha": control.alpha,
        "hypotheses": control.report.hypotheses,
        "conclusive_hypotheses": sum(1 for item in significance.values() if item["conclusive"]),
        "bonferroni_threshold": control.report.bonferroni_threshold,
        "expected_false_discoveries": control.report.expected_false_discoveries,
        "discoveries_before": control.report.discoveries_before,
        "discoveries_after": control.report.discoveries_after,
        "survivors": [
            {
                "label": outcome.label,
                "p_value": outcome.p_value,
                "rank": outcome.rank,
                "cut_off": outcome.cut_off,
                "retained_by_rule": retained_by_label.get(outcome.label, False),
            }
            for outcome in survivors
        ],
        "hypothesis_p_values": significance,
    }


def _pf(value: float | None) -> str:
    return "  non defini" if value is None else f"{value:10.4f}"


def _percent(value: float | None) -> str:
    return "   --  " if value is None else f"{value * 100:6.1f} %"


def _emit_windows(emit: Any, measured: dict[str, dict[float, dict[str, Any]]], market: str) -> None:
    any_row = next(iter(measured[market].values()))
    emit(f"\n  jeu gele  : {any_row['dataset_id']}")
    emit(f"  empreinte : {any_row['dataset_fingerprint']}")
    emit(f"  {any_row['bars']} bougies {any_row['timeframe']} mesurees")
    for name in WINDOWS:
        window = any_row[f"{name}_window"]
        emit(f"  {name:<12} {window[0][:16]} -> {window[1][:16]} UTC")
    emit(
        f"  {'scelle':<12} {any_row['sealed_window_not_read'][0][:16]} -> "
        f"{any_row['sealed_window_not_read'][1][:16]} UTC"
        f"  ({any_row['sealed_bars_not_read']} bougies NON LUES)"
    )


def _emit_baseline(
    emit: Any,
    baseline: dict[str, dict[str, Any]],
    measured: dict[str, dict[float, dict[str, Any]]],
) -> None:
    emit("\n########## la reference : la regle sans filtre ##########")
    for market, blocks in baseline.items():
        ref = next(iter(measured[market].values()))["ref"]
        emit(f"  {market} ({ref})")
        for name in WINDOWS:
            block = blocks[name]
            emit(
                f"    {name:<12} {block['trades']:5} op.  PF {_pf(block['profit_factor'])}"
                f"  net {block['net_profit']:>10.2f} EUR  signaux {block['signals']:5}"
                f"  esperance {block['mean_r']:+.4f} R"
            )


def _emit_sweep(emit: Any, rows: dict[float, dict[str, Any]], markets: list[str]) -> None:
    emit("\n########## le balayage : selectivite contre effet ##########")
    emit("  un seuil plus bas rend NEUTRAL plus rare : la colonne 'conserve' MONTE vers le bas.")
    emit("  c'est attendu, et ce n'est pas le resultat. Le resultat est la colonne PF et 'retenu'.")
    header = f"  {'seuil':>6} "
    for market in markets:
        header += f"| {'conserve ' + market + ' tr / va':>28} "
    for market in markets:
        header += f"| {'PF net ' + market + ' tr / va':>26} "
    header += "| a b c d  verdict"
    emit(header)
    for threshold, row in rows.items():
        line = f"  {threshold:>6g} "
        for market in markets:
            line += (
                f"| {_percent(row['kept_trades'][_block(market, 'train')])} /"
                f"{_percent(row['kept_trades'][_block(market, 'validation')])} "
            )
        for market in markets:
            line += (
                f"| {_pf(row['profit_factors'][_block(market, 'train')])} /"
                f"{_pf(row['profit_factors'][_block(market, 'validation')])} "
            )
        marks = " ".join("oui" if row["criteria"][key] else "NON" for key in CRITERIA)
        line += f"| {marks}  {'RETENU' if row['retained'] else 'refuse'}"
        emit(line)


def _emit_verdict(emit: Any, rows: dict[float, dict[str, Any]]) -> None:
    retained = [row for row in rows.values() if row["retained"]]
    emit("\n########## la regle de decision, appliquee telle qu'elle a ete ecrite ##########")
    emit(
        "  (a) plus d'operations qu'au seuil par defaut sur les 4 blocs ; (b) PF net au-dessus de "
        "la regle sans filtre sur l'entrainement ET la validation, 2 marches ;"
    )
    emit(
        f"  (c) au moins {KEPT_FLOOR:.0%} des operations sur les 4 blocs ; "
        f"(d) au moins {MIN_VALIDATION_TRADES} operations en validation par marche."
    )
    if not retained:
        emit("  AUCUN reglage ne satisfait les quatre conditions.")
        emit("  Consequence : rien n'est adopte, et le filtre reste eteint par defaut.")
        return
    ranked = sorted(
        retained,
        key=lambda row: (
            row["worst_profit_factor"] if row["worst_profit_factor"] is not None else -math.inf
        ),
        reverse=True,
    )
    listed = ", ".join(f"{row['threshold']:g}" for row in ranked)
    emit(f"  {len(retained)} reglage(s) retenu(s) : {listed}")
    best = ranked[0]
    smallest = min(value for value in best["kept_trades"].values() if value is not None)
    emit(
        "  ordre fixe d'avance (PF net minimal le plus haut sur les 4 blocs) : "
        f"seuil {best['threshold']:g}"
        f" (PF minimal {best['worst_profit_factor']:.4f},"
        f" operations conservees {smallest * 100:.1f} % au minimum)"
    )


def _emit_secondary(
    emit: Any,
    rows: dict[float, dict[str, Any]],
    correction: dict[str, Any],
    markets: list[str],
) -> None:
    emit("\n########## lectures secondaires (jamais utilisees pour retenir) ##########")
    emit("  signaux conserves, en entrainement / validation :")
    for threshold, row in rows.items():
        parts = []
        for market in markets:
            values = " / ".join(
                _percent(row["kept_signals"][_block(market, window)]) for window in WINDOWS
            )
            parts.append(f"{market} {values}")
        emit(f"  {threshold:>6g}  " + "   ".join(parts))
    emit("  p-value unilaterale du t de Student sur le R net, deux fenetres reunies :")
    for threshold in rows:
        parts = []
        for market in markets:
            label = f"{market}|trend_slope_atr={threshold:g}"
            item = correction["hypothesis_p_values"][label]
            value = item["p_value_one_sided"]
            parts.append(
                f"{market} p={value:.3e}" if value is not None else f"{market} p=non concluant"
            )
        emit(f"  {threshold:>6g}  " + "   ".join(parts))


def _verdict(rows: dict[float, dict[str, Any]]) -> dict[str, Any]:
    retained = [row for row in rows.values() if row["retained"]]
    chosen = None
    if retained:
        chosen = max(
            retained,
            key=lambda row: (
                row["worst_profit_factor"] if row["worst_profit_factor"] is not None else -math.inf
            ),
        )
    return {
        "question": (
            "existe-t-il un reglage moins selectif de trend_slope_atr qui conserve l'effet du "
            "filtre de tendance sur les deux fenetres et les deux marches ?"
        ),
        "answer": "oui" if retained else "non",
        "retained_thresholds": sorted(row["threshold"] for row in retained),
        "chosen_threshold": None if chosen is None else chosen["threshold"],
        "chosen_worst_profit_factor": None if chosen is None else chosen["worst_profit_factor"],
        "chosen_kept_trades": None if chosen is None else chosen["kept_trades"],
        "adopted": False,
        "note": (
            "aucune activation : ce script decide d'un reglage de mesure, pas d'une mise en "
            "production. L'activation est une decision du Lead apres les neuf portes."
        ),
    }


def _strip(measured: dict[str, Any]) -> dict[str, Any]:
    """Le meme bloc sans les R par operation : le JSON reste lisible, les tests sont ailleurs."""
    stripped: dict[str, Any] = {}
    for key, value in measured.items():
        if key in WINDOWS:
            stripped[key] = {name: item for name, item in value.items() if name != "r_values"}
        else:
            stripped[key] = value
    return stripped


def _assemble(
    results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    markets: list[str],
    thresholds: tuple[float, ...],
) -> tuple[
    dict[str, dict[str, Any]], dict[str, dict[float, dict[str, Any]]], bool, list[dict[str, Any]]
]:
    """Range les blocs mesures, et dit si la mesure est complete."""
    baseline: dict[str, dict[str, Any]] = {}
    measured: dict[str, dict[float, dict[str, Any]]] = {market: {} for market in markets}
    for outcome in results:
        if outcome["threshold"] is None:
            baseline[outcome["market"]] = outcome
        else:
            measured[outcome["market"]][outcome["threshold"]] = outcome
    complete = (
        not failures
        and all(market in baseline for market in markets)
        and all(len(measured[market]) == len(thresholds) for market in markets)
    )
    return baseline, measured, complete, failures


def main(argv: list[str] | None = None) -> int:
    stats = _load_current_stats()
    stats._use_utf8_when_redirected()
    emit = _Log()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-config", type=Path, default=ROOT / "config" / "agent.yaml")
    parser.add_argument("--datasets", type=Path, default=DATASET_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--market", default=None, help="ne mesurer qu'un marche")
    parser.add_argument("--bars", type=int, default=0, help="tronquer (plomberie, pas une mesure)")
    parser.add_argument("--jobs", type=int, default=4, help="processus de mesure en parallele")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--self-check", action="store_true", help="verifier Student, puis sortir")
    parser.add_argument(
        "--from-cache",
        action="store_true",
        help=(
            "relire la mesure brute mise en cache et refaire l'analyse seule : la mesure complete "
            "coute une vingtaine de minutes et ne doit pas etre relancee pour un tableau"
        ),
    )
    args = parser.parse_args(argv)

    self_check = _self_check(emit)
    if args.self_check:
        return 0

    refs = _refs_from_agent_config(args.agent_config)
    datasets = stats.DatasetStore(args.datasets).load_all()
    markets = [args.market] if args.market else sorted(datasets)
    for market in markets:
        if market not in refs:
            raise SystemExit(f"{market}: aucune version declaree dans {args.agent_config.name}")
    thresholds = _thresholds()

    emit(f"\nversions declarees : {refs}")
    emit(f"marches mesures    : {', '.join(markets)}")
    emit(f"seuils balayes ({len(thresholds)}) : {', '.join(f'{value:g}' for value in thresholds)}")
    emit(f"reference a battre : {DEFAULT_TREND_THRESHOLD:g} (lu dans indicators.regime)")
    emit(
        f"exigences fixees   : (c) >= {KEPT_FLOOR:.0%} des operations sur les 4 blocs, "
        f"(d) >= {MIN_VALIDATION_TRADES} operations en validation par marche"
    )
    emit(f"hypotheses         : {len(thresholds)} seuils x {len(markets)} marches")
    if args.bars > 0:
        emit(
            f"ATTENTION : jeu tronque aux {args.bars} dernieres bougies — plomberie, pas une mesure"
        )

    jobs: list[tuple[str, float | None, int, str, str]] = []
    for market in markets:
        jobs.append((market, None, args.bars, str(args.datasets), str(args.agent_config)))
        for threshold in thresholds:
            jobs.append((market, threshold, args.bars, str(args.datasets), str(args.agent_config)))

    cache_path = args.output / "measurements-cache.json"
    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    def _collect(payload: tuple[str, float | None, int, str, str], outcome: dict[str, Any]) -> None:
        results.append(outcome)
        label = "eteint" if outcome["threshold"] is None else f"{outcome['threshold']:g}"
        emit(
            f"  [{outcome['market']}] seuil {label:>7} : entrainement "
            f"{outcome['train']['trades']:4} op.  validation "
            f"{outcome['validation']['trades']:4} op."
        )

    if args.from_cache:
        if not cache_path.exists():
            raise SystemExit(f"{cache_path}: aucune mesure en cache a relire")
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached["bars"] != args.bars or cached["thresholds"] != list(thresholds):
            raise SystemExit(
                f"{cache_path}: le cache porte sur {cached['thresholds']} a {cached['bars']} "
                "bougies, pas sur la demande"
            )
        results = list(cached["results"])
        failures = list(cached["failures"])
        emit(f"\n{len(results)} blocs relus depuis {cache_path} : aucun backtest relance")
    else:
        emit(f"\n{len(jobs)} blocs de mesure, {args.jobs} processus...")
        if args.jobs > 1 and len(jobs) > 1:
            with ProcessPoolExecutor(max_workers=args.jobs) as pool:
                futures = [pool.submit(_measure, payload) for payload in jobs]
                for payload, future in zip(jobs, futures, strict=True):
                    try:
                        outcome = future.result()
                    except BaseException as error:  # un bloc perdu ne doit pas tout perdre
                        failures.append(
                            {"market": payload[0], "threshold": payload[1], "error": repr(error)}
                        )
                        emit(f"  ECHEC [{payload[0]}] seuil {payload[1]} : {error!r}")
                        continue
                    _collect(payload, outcome)
        else:
            for payload in jobs:
                try:
                    outcome = _measure(payload)
                except BaseException as error:  # un bloc perdu ne doit pas tout perdre
                    failures.append(
                        {"market": payload[0], "threshold": payload[1], "error": repr(error)}
                    )
                    emit(f"  ECHEC [{payload[0]}] seuil {payload[1]} : {error!r}")
                    continue
                _collect(payload, outcome)

        if failures:
            emit(
                f"\n{len(failures)} bloc(s) de mesure en echec : "
                "rien ne sera publie comme une mesure."
            )
        if not args.no_write:
            args.output.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(
                json.dumps(
                    {
                        "method": (
                            "mesure brute du balayage, ecrite avant toute analyse pour que "
                            "l'analyse puisse etre refaite sans relancer les backtests"
                        ),
                        "bars": args.bars,
                        "markets": markets,
                        "thresholds": list(thresholds),
                        "results": results,
                        "failures": failures,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            emit(f"\nmesure brute mise en cache : {cache_path}")

    baseline, measured, complete, _ = _assemble(results, failures, markets, thresholds)

    if not complete:
        emit("\nmesure incomplete : la regle de decision n'est pas appliquee.")
        if not args.no_write:
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / "run-datasets-volume.txt").write_text(emit.text(), encoding="utf-8")
        return 1

    for market in markets:
        emit(f"\n=== {market} — {baseline[market]['ref']} ===")
        _emit_windows(emit, measured, market)
    _emit_baseline(emit, baseline, measured)

    default_kept = {
        _block(market, window): _kept(
            measured[market][DEFAULT_TREND_THRESHOLD][window], baseline[market][window], "trades"
        )
        for market in markets
        for window in WINDOWS
    }
    rows: dict[float, dict[str, Any]] = {}
    for threshold in thresholds:
        row = _row(threshold, measured, baseline, markets, default_kept)
        row["retained"] = all(row["criteria"].values())
        rows[threshold] = row

    _emit_sweep(emit, rows, markets)
    _emit_verdict(emit, rows)

    reproduction = (
        {"skipped": "jeu tronque par --bars : ce n'est pas une mesure"}
        if args.bars > 0
        else _reproduction(baseline, measured, markets, PUBLISHED_MEASUREMENT)
    )
    emit("\n########## la mesure publiee est-elle reproduite ? ##########")
    if "skipped" in reproduction:
        emit(f"  {reproduction['skipped']}")
    else:
        emit(
            f"  {reproduction['checks']} champs confrontes a {reproduction['source']}"
            f" : ecart maximal {reproduction['max_absolute_error']:.3e}"
            f" -> {'REPRODUITE' if reproduction['reproduced'] else 'ECART'}"
        )
        for delta in reproduction["deltas"][:12]:
            emit(
                f"    {delta['market']} filtre={delta['filter']} {delta['window']}"
                f" {delta['field']} : publie {delta['published']} mesure {delta['measured']}"
            )

    correction = _correct(rows, measured, markets)
    emit("\n########## correction de tests multiples (Benjamini-Hochberg du depot) ##########")
    emit(f"  hypotheses essayees : {correction['hypotheses']}")
    emit(
        f"  dont concluantes (>= min_trades en validation) : {correction['conclusive_hypotheses']}"
    )
    emit(
        f"  alpha {correction['alpha']:.2f}, ligne de Bonferroni "
        f"{correction['bonferroni_threshold']:.3e}, fausses decouvertes attendues "
        f"{correction['expected_false_discoveries']:.1f}"
    )
    emit(f"  survivantes : {correction['discoveries_after']}")
    for survivor in correction["survivors"]:
        emit(
            f"    {survivor['label']:<34} p {survivor['p_value']:.3e}"
            f"  rang {survivor['rank']}  seuil {survivor['cut_off']:.3e}"
            f"  retenu par la regle : {'oui' if survivor['retained_by_rule'] else 'non'}"
        )
    _emit_secondary(emit, rows, correction, markets)

    verdict = _verdict(rows)
    emit("\n########## reponse a la question posee ##########")
    emit(f"  {verdict['question']}")
    emit(f"  REPONSE : {verdict['answer'].upper()}")
    if verdict["answer"] == "oui":
        emit(
            "  reglages retenus : "
            + ", ".join(f"{value:g}" for value in verdict["retained_thresholds"])
        )
        emit(
            f"  retenu par l'ordre fixe d'avance : seuil {verdict['chosen_threshold']:g},"
            f" PF net minimal {verdict['chosen_worst_profit_factor']:.4f}"
        )
    emit(f"  {verdict['note']}")

    payload = {
        "method": (
            "filtre de tendance bascule en memoire (CandidateSpec), meme decoupage"
            " (ancre 60/20/20), memes couts (campaign_costs, mode SIGNAL, une position a la fois),"
            " sortie au premier objectif comme la production l'execute"
        ),
        "question": verdict["question"],
        "rule": {
            "a": "plus d'operations gardees qu'au seuil par defaut, sur chacun des 4 blocs",
            "b": "PF net superieur a la regle sans filtre, entrainement ET validation, 2 marches",
            "c": f"au moins {KEPT_FLOOR:.2f} des operations gardees, sur chacun des 4 blocs",
            "d": f"au moins {MIN_VALIDATION_TRADES} operations en validation, par marche",
            "reference_threshold": DEFAULT_TREND_THRESHOLD,
            "tie_break": "PF net minimal le plus haut sur les 4 blocs",
            "thresholds": list(thresholds),
            "hypotheses": len(thresholds) * len(markets),
            "kept_floor": KEPT_FLOOR,
            "min_validation_trades": MIN_VALIDATION_TRADES,
        },
        "refs": {market: baseline[market]["ref"] for market in markets},
        "bars": args.bars or None,
        "baseline": {market: _strip(baseline[market]) for market in markets},
        "sweep": {
            f"{threshold:g}": {
                "threshold": threshold,
                "criteria": rows[threshold]["criteria"],
                "retained": rows[threshold]["retained"],
                "worst_profit_factor": rows[threshold]["worst_profit_factor"],
                "kept_trades": rows[threshold]["kept_trades"],
                "kept_signals": rows[threshold]["kept_signals"],
                "profit_factors": rows[threshold]["profit_factors"],
                "improves_above_baseline": rows[threshold]["improves_above_baseline"],
                "blocks": {market: _strip(measured[market][threshold]) for market in markets},
            }
            for threshold in thresholds
        },
        "default_threshold_kept_trades": default_kept,
        "multiple_testing": correction,
        "reproduction": reproduction,
        "self_check": self_check,
        "verdict": verdict,
    }

    if not args.no_write:
        args.output.mkdir(parents=True, exist_ok=True)
        json_path = args.output / "tune-trend-filter.json"
        json_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        text_path = args.output / "run-datasets-volume.txt"
        text_path.write_text(emit.text(), encoding="utf-8")
        emit(f"\necrit : {json_path}")
        emit(f"ecrit : {text_path}")

    if args.bars == 0 and not reproduction.get("reproduced", False):
        emit(
            "\nla mesure publiee n'est PAS reproduite : le balayage n'est pas publie comme mesure."
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
