"""Le filtre de tendance 10/30 (entrer seulement dans le sens de la tendance), mesure d abord.

**D'ou vient l'hypothese.** Le diagnostic des pertes reelles de la demo (voir
`docs/research/2026-10-28-diagnostic-pertes-demo.md`) retombe sur la decouverte du 2026-10-11
(`docs/research/2026-10-11-ou-est-l-avantage.md`) : la regle `vwap_pullback` perd en moyenne
-0,15 R par operation sur 59 999 bougies, mais les operations qui vont DANS le sens de la
tendance nommee (EMA 10/30, pente d'une barre > 0,05 ATR : les valeurs par defaut de
`indicators.features.entry_features`) rendent +0,30 R (BTC) et +0,22 R (or), celles qui vont
CONTRE rendent -0,50 R et -0,67 R. Le filtre existant de la regle lisait le meme concept avec
EMA 20/50 et ne gardait que 12 a 15 % des operations sans reproduire l'effet.

**Aucun parametre n'est ajuste ici.** Le candidat est UN seul point, fixe avant la mesure :
`trend_filter=true, trend_ema_fast=10, trend_ema_slow=30, trend_slope_atr=0.05`, c'est-a-dire la
lecture qui a produit la decouverte, sans balayage. Surajuster demanderait de choisir parmi des
valeurs ; il n'y en a pas.

**La regle de decision, ecrite AVANT la mesure, par marche.** Le candidat est RETENU si et
seulement si :

(a) AMELIORATION — son profit factor net est superieur a celui de la version deployee sur
    l'entrainement ET sur la validation ;
(b) EFFECTIF — au moins 60 operations sur entrainement + validation (le double de `min_trades`) ;
(c) STABILITE — son R moyen par operation est superieur a celui de la version deployee dans la
    majorite des trimestres civils de l'entrainement + validation qui portent au moins 5
    operations des deux cotes ;
(d) HORS ECHANTILLON TEMPOREL — sur la fenetre posterieure aux jeux geles (la vie reelle de
    l'agent, bougies lues en LECTURE SEULE dans la base), le candidat ne fait pas moins bien que la
    version deployee en R moyen. Avec moins de 10 operations la fenetre est declaree NON
    CONCLUANTE : elle ne retient pas, elle ne rejette pas.

**Ce que cette mesure ne dit pas.** (1) La tranche « dans la tendance » a ete reperee en
regardant 164 tranches sur l'entrainement + validation : ces deux fenetres ne sont donc PAS
independantes de la decouverte. Seule la fenetre posterieure l'est, et elle est courte. (2) Le
jeu scelle n'est PAS ouvert : l'ouvrir ecrit dans le registre `holdout_unlocks` de la base de
production, ce que cette session n'a pas le droit de faire. (3) Un PF au-dessus de 1 en demo
n'est pas une validation : le seuil du projet est 1,20 et les neuf portes du §49.

    uv run python scripts/backtest/measure_trend_agreement.py --forward-from-db
    uv run python scripts/backtest/measure_trend_agreement.py --bars 9000 --no-write   # plomberie
"""

import argparse
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tradingagent.core.market import Candle
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.protocol import split_dataset

sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_strategy import _load_current_stats

ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT / "docs" / "research" / "datasets-volume"
OUTPUT = ROOT / "docs" / "research" / "measurements" / "trend-agreement-1030.json"

#: Le candidat, fixe avant la mesure. Les deux premiers sont les valeurs par defaut de
#: `entry_features` (fast=10, slow=30) ; le troisieme celle de `DEFAULT_TREND_THRESHOLD`.
CANDIDATE_OVERRIDES: dict[str, Any] = {
    "trend_filter": True,
    "trend_ema_fast": 10,
    "trend_ema_slow": 30,
    "trend_slope_atr": 0.05,
}

MIN_TRADES_TOTAL = 60
MIN_QUARTER_TRADES = 5
MIN_FORWARD_TRADES = 10
HISTORY_BARS = 400


def _variant_parameters(base: dict[str, Any], variant: str) -> dict[str, Any]:
    parameters = dict(base)
    if variant == "candidate":
        parameters.update(CANDIDATE_OVERRIDES)
    elif variant != "baseline":
        raise ValueError(variant)
    return parameters


def _summary(trades: Sequence[Any], run: Any | None = None) -> dict[str, Any]:
    """PF net, net, win rate et R moyen d'une liste d'operations (R = PnL net / risque)."""
    rs = [float(t.pnl_eur) / float(t.risk_eur) for t in trades if float(t.risk_eur)]
    gains = math.fsum(float(t.pnl_eur) for t in trades if t.pnl_eur > 0)
    losses = -math.fsum(float(t.pnl_eur) for t in trades if t.pnl_eur <= 0)
    n = len(rs)
    stderr = statistics.stdev(rs) / math.sqrt(n) if n > 1 else None
    mean = math.fsum(rs) / n if n else None
    return {
        "trades": n,
        "wins": sum(1 for t in trades if t.pnl_eur > 0),
        "win_rate": (sum(1 for t in trades if t.pnl_eur > 0) / n) if n else None,
        "net_profit": round(gains - losses, 4),
        "profit_factor": round(gains / losses, 4) if losses else None,
        "mean_r": round(mean, 4) if mean is not None else None,
        "ci95_r": [round(mean - 1.96 * stderr, 4), round(mean + 1.96 * stderr, 4)]
        if (stderr is not None and mean is not None)
        else None,
        "max_drawdown_eur": round(_drawdown(trades), 4),
        "signals": getattr(run, "signals", None),
    }


def _drawdown(trades: Sequence[Any]) -> float:
    peak = equity = worst = 0.0
    for trade in sorted(trades, key=lambda t: t.closed_at):
        equity += float(trade.pnl_eur)
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _quarter(moment: datetime) -> str:
    return f"{moment.year}-Q{(moment.month - 1) // 3 + 1}"


def _db_candles(market: str, since: datetime) -> list[Candle]:
    """Les bougies M15 de la base, SELECT seulement, depuis la derniere serie continue de volume.

    Lecture seule : un `SELECT` et rien d'autre. Le secret n'est lu que dans l'environnement ou
    dans `.env` du depot principal, jamais affiche.
    """
    from sqlalchemy import create_engine, text

    url = os.environ.get("DATABASE_URL")
    if not url:
        env = Path(os.environ.get("TRADINGAGENT_ENV_FILE", ""))
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip("\"'")
    if not url:
        raise SystemExit("DATABASE_URL absent (environnement ou TRADINGAGENT_ENV_FILE)")
    engine = create_engine(url, connect_args={"connect_timeout": 20})
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "select open_time, open, high, low, close, tick_volume from candles "
                "where symbol = :s and timeframe = 'M15' and open_time >= :t order by open_time"
            ),
            {"s": market, "t": since},
        ).all()
    engine.dispose()
    candles: list[Candle] = []
    for open_time, o, h, low, c, volume in rows:
        if volume is None:
            candles = []  # une barre sans volume casse le VWAP : on repart apres elle
            continue
        candles.append(
            Candle(
                timeframe=Timeframe.M15,
                open_time=open_time.astimezone(UTC),
                open=float(o),
                high=float(h),
                low=float(low),
                close=float(c),
                volume=float(volume),
            )
        )
    return candles


def _measure(payload: tuple[str, str, str, int, bool]) -> dict[str, Any]:
    """Un marche, une variante : train, validation, et la fenetre posterieure si demandee."""
    market, variant, ref, bars, forward = payload

    from tradingagent.backtest.harness import run_backtest

    stats = _load_current_stats()
    dataset = stats.DatasetStore(DATASET_DIR).load_all()[market]
    if bars > 0:
        dataset = replace(dataset, candles=dataset.candles[-bars:])
    split = split_dataset(dataset, token=f"measure-trend-agreement:{market}", anchor=True)
    spec = stats.spec_for(market, ref)
    spec = replace(spec, parameters=_variant_parameters(dict(spec.parameters), variant))
    config = replace(stats.config_for(market, dataset), targets_at_first_only=True)

    out: dict[str, Any] = {
        "market": market,
        "variant": variant,
        "ref": ref,
        "dataset_fingerprint": dataset.fingerprint,
        "sealed_window_not_read": [
            split.holdout.window().start.isoformat(),
            split.holdout.window().end.isoformat(),
        ],
    }
    all_trades: list[Any] = []
    for name, candles in (("train", split.train), ("validation", split.validation)):
        run = run_backtest(
            spec.factory(spec.parameters), spec.manifest, {dataset.timeframe: candles}, config
        )
        out[name] = _summary(run.trades, run)
        out[f"{name}_window"] = [
            candles[0].open_time.isoformat(),
            candles[-1].close_time.isoformat(),
        ]
        all_trades.extend(run.trades)
    out["train_plus_validation"] = _summary(all_trades)
    per_quarter: dict[str, list[Any]] = defaultdict(list)
    for trade in all_trades:
        per_quarter[_quarter(trade.opened_at)].append(trade)
    out["quarters"] = {key: _summary(value) for key, value in sorted(per_quarter.items())}

    if forward:
        end = dataset.candles[-1].close_time
        since = end - timedelta(days=10)
        series = _db_candles(market, since)
        forward_config = replace(
            config, costs=stats.campaign_costs(series[0].close) if series else config.costs
        )
        if len(series) > HISTORY_BARS:
            run = run_backtest(
                spec.factory(spec.parameters),
                spec.manifest,
                {Timeframe.M15: series},
                forward_config,
            )
            kept = [t for t in run.trades if t.opened_at >= end]
            out["forward"] = _summary(kept, run)
            out["forward_window"] = [
                max(series[0].open_time, end).isoformat(),
                series[-1].close_time.isoformat(),
            ]
            out["forward_trades"] = [
                {
                    "opened_at": t.opened_at.isoformat(),
                    "closed_at": t.closed_at.isoformat(),
                    "direction": str(t.direction),
                    "r": round(float(t.pnl_eur) / float(t.risk_eur), 4),
                    "mae_r": t.mae_r,
                    "mfe_r": t.mfe_r,
                }
                for t in kept
            ]
        else:
            out["forward"] = {"trades": 0, "note": "serie continue avec volume trop courte"}
    return out


def _decide(base: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    """La regle ecrite en tete de fichier, appliquee mot pour mot."""

    def pf(block: dict[str, Any]) -> float:
        value = block["profit_factor"]
        return math.inf if value is None else float(value)

    a = all(pf(cand[w]) > pf(base[w]) for w in ("train", "validation"))
    b = cand["train_plus_validation"]["trades"] >= MIN_TRADES_TOTAL
    better = comparable = 0
    for quarter, block in cand["quarters"].items():
        reference = base["quarters"].get(quarter)
        if (
            reference
            and block["trades"] >= MIN_QUARTER_TRADES
            and reference["trades"] >= MIN_QUARTER_TRADES
        ):
            comparable += 1
            better += int(block["mean_r"] > reference["mean_r"])
    c = comparable > 0 and better > comparable / 2
    forward_c = cand.get("forward", {})
    forward_b = base.get("forward", {})
    if forward_c.get("trades", 0) < MIN_FORWARD_TRADES:
        d: bool | None = None
    else:
        d = (forward_c["mean_r"] or 0.0) >= (forward_b.get("mean_r") or 0.0)
    return {
        "a_improves_pf_train_and_validation": a,
        "b_at_least_60_trades": b,
        "c_better_in_majority_of_quarters": c,
        "quarters_better": better,
        "quarters_comparable": comparable,
        "d_forward_not_worse": d,
        "retained": bool(a and b and c and d is not False),
        "forward_conclusive": d is not None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default=None)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--bars", type=int, default=0, help="tronque (plomberie, pas une mesure)")
    parser.add_argument("--forward-from-db", action="store_true")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)

    stats = _load_current_stats()
    refs = stats.deployed_refs()
    markets = [args.market] if args.market else sorted(refs)
    payloads = [
        (market, variant, refs[market], args.bars, args.forward_from_db)
        for market in markets
        for variant in ("baseline", "candidate")
    ]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(_measure, payloads))
    by_key = {(r["market"], r["variant"]): r for r in results}

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "candidate_overrides": CANDIDATE_OVERRIDES,
        "rule": {
            "min_trades_total": MIN_TRADES_TOTAL,
            "min_quarter_trades": MIN_QUARTER_TRADES,
            "min_forward_trades": MIN_FORWARD_TRADES,
        },
        "markets": {},
    }
    for market in markets:
        base, cand = by_key[(market, "baseline")], by_key[(market, "candidate")]
        verdict = _decide(base, cand)
        report["markets"][market] = {"baseline": base, "candidate": cand, "verdict": verdict}
        print(f"=== {market}  (base {base['ref']}) ===")
        for window in ("train", "validation", "train_plus_validation", "forward"):
            for label, block in (("base", base), ("cand", cand)):
                row = block.get(window)
                if not row or not row.get("trades"):
                    print(f"  {window:<22}{label}  0 operations")
                    continue
                print(
                    f"  {window:<22}{label}  n={row['trades']:>4}  PF={row['profit_factor']}"
                    f"  net={row['net_profit']:>9}  win={row['win_rate']:.3f}  R={row['mean_r']}"
                    f"  DD={row['max_drawdown_eur']}"
                )
        print(f"  verdict: {json.dumps(verdict)}")
    if not args.no_write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"ecrit : {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
