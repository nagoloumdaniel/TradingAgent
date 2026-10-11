"""Le filtre de tendance de la regle reproduit-il l'effet mesure ?

**Pourquoi ce script, et pourquoi il doit exister avant toute activation.** Le 2026-10-11, une
mesure a trouve que les operations prises **dans le sens d'une tendance nommee** rendent
+0,2975 R (BTC) et +0,2198 R (or), contre -0,5045 R et -0,6707 R pour celles qui la
contredisent (`docs/research/2026-10-11-ou-est-l-avantage.md`). Mais cette tendance-la vient de
`indicators/features.py::entry_features` : **EMA 10/30, pente sur une barre, seuil 0,05 ATR**.

La regle, elle, porte deja `trend_filter`, qui refuse quand `trend_of` ne dit pas le sens de
l'operation. `trend_of` se calcule avec **les EMA de la regle** (20/50) et **son** seuil de pente
(`trend_slope_atr`). Deux mesures differentes de la meme idee, avec des parametres differents :
l'effet trouve sur l'une ne se transporte pas automatiquement sur l'autre. C'est exactement la
question que ce script tranche, et personne ne l'a posee.

**Ce qu'il compare, sur les memes fenetres et les memes couts :**

* `off` — la production actuelle ;
* `on` — la meme regle avec `trend_filter: true` et le seuil par defaut du module de regime.

Et pour chaque marche, il rapporte le nombre d'operations **refusees** par le filtre : un filtre
qui coupe 60 % des entrees et double le PF ne se lit pas comme un filtre qui coupe 5 %.

    uv run python scripts/backtest/measure_trend_filter.py
    uv run python scripts/backtest/measure_trend_filter.py --market BTCUSD --bars 20000

Lecture seule : rien n'est promu, rien n'est ecrit dans `config/`, le jeu scelle reste ferme.
"""

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from measure_strategy import _load_current_stats
from sweep_exit_geometry import (
    ROOT,
    _refs_from_agent_config,
)

DATASET_DIR = ROOT / "docs" / "research" / "datasets-volume"
OUTPUT_DIR = ROOT / "docs" / "research" / "measurements"


def _with_trend_filter(spec: Any, *, enabled: bool) -> Any:
    """The same candidate, with the trend filter flipped. In memory: no manifest is rewritten."""
    values = dict(spec.parameters)
    values["trend_filter"] = enabled
    return replace(spec, parameters=values)


def _performance(run: Any, stats: Any) -> dict[str, Any]:
    performance = run.performance
    return {
        "trades": performance.trades,
        "wins": performance.wins,
        "losses": performance.losses,
        "win_rate": stats.number(performance.win_rate),
        "net_profit": stats.number(performance.net_profit),
        "profit_factor": stats.number(performance.profit_factor),
        "expectancy": stats.number(performance.expectancy),
        "average_win": stats.number(performance.average_win),
        "average_loss": stats.number(performance.average_loss),
        "max_drawdown": stats.number(performance.max_drawdown),
    }


def main(argv: list[str] | None = None) -> int:
    stats = _load_current_stats()
    stats._use_utf8_when_redirected()

    from tradingagent.backtest.harness import run_backtest
    from tradingagent.research.protocol import split_dataset

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-config", type=Path, default=ROOT / "config" / "agent.yaml")
    parser.add_argument("--datasets", type=Path, default=DATASET_DIR)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--market", default=None)
    parser.add_argument("--bars", type=int, default=0)
    args = parser.parse_args(argv)

    refs = _refs_from_agent_config(args.agent_config)
    datasets = stats.DatasetStore(args.datasets).load_all()
    markets = [args.market] if args.market else sorted(datasets)
    for market in markets:
        if market not in refs:
            raise SystemExit(f"{market}: aucune version declaree dans {args.agent_config.name}")
    if args.bars > 0:
        datasets = {
            market: replace(
                dataset,
                dataset_id=f"{dataset.dataset_id}-tronque-{args.bars}",
                candles=dataset.candles[-args.bars :],
            )
            for market, dataset in datasets.items()
        }

    rows: list[dict[str, Any]] = []
    for market in markets:
        dataset = datasets[market]
        split = split_dataset(dataset, token=f"trend-filter:{market}", anchor=True)
        print(f"\n=== {market} — {refs[market]} ===")
        for enabled in (False, True):
            spec = _with_trend_filter(stats.spec_for(market, refs[market]), enabled=enabled)
            config = replace(stats.config_for(market, dataset), targets_at_first_only=True)
            label = "avec filtre" if enabled else "sans filtre (production)"
            window: dict[str, Any] = {}
            for name, candles in (("train", split.train), ("validation", split.validation)):
                run = run_backtest(
                    spec.factory(spec.parameters),
                    spec.manifest,
                    {dataset.timeframe: candles},
                    config,
                )
                window[name] = _performance(run, stats)
                window[f"{name}_refused"] = run.filtered_signals
            rows.append({"market": market, "ref": refs[market], "filter": enabled, **window})
            train, validation = window["train"], window["validation"]
            print(f"  {label}")
            print(
                f"    entrainement  {train['trades']:4} op.  PF {train['profit_factor']:.4f}"
                f"  net {train['net_profit']:>10.2f} EUR  refus {window['train_refused']}"
            )
            print(
                f"    validation    {validation['trades']:4} op.  PF "
                f"{validation['profit_factor']:.4f}  net {validation['net_profit']:>10.2f} EUR"
                f"  refus {window['validation_refused']}"
            )

    print("\n########## decision ##########")
    print("  Un filtre ne vaut que s'il ameliore l'entrainement ET la validation, sur les DEUX")
    print("  marches. Un gain obtenu en supprimant la plupart des operations n'est pas un gain.")
    verdicts: dict[str, Any] = {}
    for market in markets:
        off = next(r for r in rows if r["market"] == market and not r["filter"])
        on = next(r for r in rows if r["market"] == market and r["filter"])
        kept = (
            on["validation"]["trades"] / off["validation"]["trades"]
            if off["validation"]["trades"]
            else 0
        )
        improves = (
            on["train"]["profit_factor"] > off["train"]["profit_factor"]
            and on["validation"]["profit_factor"] > off["validation"]["profit_factor"]
        )
        verdicts[market] = {
            "train_pf_off": off["train"]["profit_factor"],
            "train_pf_on": on["train"]["profit_factor"],
            "validation_pf_off": off["validation"]["profit_factor"],
            "validation_pf_on": on["validation"]["profit_factor"],
            "trades_kept": round(kept, 4),
            "improves_both_windows": improves,
        }
        print(
            f"  {market:8} PF entrainement {off['train']['profit_factor']:.4f} -> "
            f"{on['train']['profit_factor']:.4f} | validation "
            f"{off['validation']['profit_factor']:.4f} -> "
            f"{on['validation']['profit_factor']:.4f} | operations conservees "
            f"{kept * 100:.1f} % | {'AMELIORE' if improves else 'N AMELIORE PAS'}"
        )

    payload = {
        "method": "trend_filter flipped in memory, same split and costs, first target only",
        "refs": refs,
        "bars": args.bars or None,
        "rows": rows,
        "verdicts": verdicts,
    }
    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        path = args.output / "trend-filter-effect.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\necrit : {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
