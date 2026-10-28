"""Diagnostic des operations reelles du compte DEMO : d'ou viennent les pertes ?

**Lecture seule.** Uniquement des `SELECT` sur la base de production ; aucune ecriture, aucun
ordre, aucun secret affiche. `DATABASE_URL` est lu dans l'environnement, ou dans le fichier
designe par `TRADINGAGENT_ENV_FILE` (le `.env` du depot principal) ; il n'est jamais imprime.

Ce que le script calcule, pour chaque marche et pour l'ensemble :

* resultat : effectif, taux de reussite, profit factor net, gain moyen / perte moyenne, esperance
  en R, drawdown de la suite d'operations, plus longue serie perdante ;
* decoupes : sens, session UTC de la barre de decision, heure, jour, version de strategie ;
* sortie : la table `trades` ne distingue pas stop et objectif (`exit_reason = 'broker'`), donc
  la sortie est reclassee en comparant le prix de cloture au stop et a l'objectif de la position ;
* couts : slippage a l'execution, spread de l'entree (ecart ask/prix de decision), part du R
  consommee par le spread ;
* excursions : MFE/MAE en R, recalculees sur les bougies M15 de la base entre l'ouverture et la
  cloture (un trade n'a pas de chandelier plus fin que M15 ici : la MFE/MAE est donc une borne) ;
* tendance a l'entree : la lecture 10/30 et la lecture 20/50 de `trend_of` a la barre de decision ;
* entonnoir : signaux generes, refus du moteur de risque (motifs), ordres rejetes ;
* courbe d'equity : pic et creux des snapshots du compte.

    uv run python scripts/analysis/diagnose_demo_losses.py
    uv run python scripts/analysis/diagnose_demo_losses.py --output out.json
"""

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from tradingagent.indicators.regime import Trend, trend_of
from tradingagent.indicators.session import session_at

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "docs" / "research" / "measurements" / "demo-diagnostic-2026-10-28.json"

TRADES_SQL = """
select t.id, t.closed_at, t.close_price, t.pnl_eur, t.risk_eur, t.exit_reason,
       p.symbol, p.direction, p.volume, p.open_price, p.stop_loss, p.take_profit, p.opened_at,
       o.requested_price, s.id as signal_id, s.observed_price, s.generated_at, sv.ref as ref,
       e.slippage
from trades t
join positions p on p.id = t.position_id
join orders o on o.id = p.order_id
join signals s on s.id = o.signal_id
join strategy_versions sv on sv.id = s.strategy_version_id
left join executions e on e.order_id = o.id
order by t.closed_at
"""


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        env = Path(os.environ.get("TRADINGAGENT_ENV_FILE", ""))
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip("\"'")
    if not url:
        raise SystemExit("DATABASE_URL absent (environnement ou TRADINGAGENT_ENV_FILE)")
    return url


def _num(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Les chiffres d'une liste d'operations. Pur : aucune lecture de l'heure ni du reseau."""
    n = len(rows)
    if not n:
        return {"trades": 0}
    pnl = [r["pnl"] for r in rows]
    rs = [r["r"] for r in rows]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    gross_loss = -math.fsum(losses)
    peak = equity = drawdown = 0.0
    streak = longest = 0
    for r in rows:
        equity += r["pnl"]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        streak = streak + 1 if r["pnl"] <= 0 else 0
        longest = max(longest, streak)
    mean_r = math.fsum(rs) / n
    return {
        "trades": n,
        "wins": len(wins),
        "win_rate": round(len(wins) / n, 4),
        "net_eur": round(math.fsum(pnl), 2),
        "profit_factor": round(math.fsum(wins) / gross_loss, 4) if gross_loss else None,
        "avg_win_eur": round(math.fsum(wins) / len(wins), 2) if wins else None,
        "avg_loss_eur": round(gross_loss / len(losses), 2) if losses else None,
        "payoff": round((math.fsum(wins) / len(wins)) / (gross_loss / len(losses)), 3)
        if wins and losses and gross_loss
        else None,
        "mean_r": round(mean_r, 4),
        "avg_win_r": round(math.fsum(r for r in rs if r > 0) / max(1, sum(r > 0 for r in rs)), 3),
        "avg_loss_r": round(
            math.fsum(r for r in rs if r <= 0) / max(1, sum(r <= 0 for r in rs)), 3
        ),
        "max_drawdown_eur": round(drawdown, 2),
        "longest_losing_streak": longest,
    }


def classify_exit(row: dict[str, Any]) -> str:
    """Stop ou objectif, deduit du prix de cloture (la table ne le dit pas : 'broker')."""
    sign = 1 if row["direction"] == "BUY" else -1
    risk = abs(row["open_price"] - row["stop_loss"])
    result = sign * (row["close_price"] - row["open_price"])
    if row["pnl"] > 0:
        return "objectif" if row["take_profit"] is not None else "gain_autre"
    return "stop" if result <= -0.9 * risk else "perte_autre"


def _slice(rows: list[dict[str, Any]], key: Any) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key(row))].append(row)
    return {name: block(items) for name, items in sorted(groups.items())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine, text

    engine = create_engine(_database_url(), connect_args={"connect_timeout": 20})
    with engine.connect() as c:
        trade_rows = c.execute(text(TRADES_SQL)).mappings().all()
        candles: dict[str, list[tuple[Any, ...]]] = {}
        for symbol in sorted({r["symbol"] for r in trade_rows}):
            candles[symbol] = [
                (_utc(t), o, h, low, cl)
                for t, o, h, low, cl in c.execute(
                    text(
                        "select open_time, open, high, low, close from candles "
                        "where symbol = :s and timeframe = 'M15' order by open_time"
                    ),
                    {"s": symbol},
                ).all()
            ]
        funnel_states = Counter(r[0] for r in c.execute(text("select state from signals")).all())
        risk_rows = c.execute(text("select outcome, reason from risk_decisions")).all()
        snapshots = c.execute(text("select at, equity from account_snapshots order by at")).all()
    engine.dispose()

    rows: list[dict[str, Any]] = []
    for t in trade_rows:
        row = {k: _num(v) for k, v in dict(t).items()}
        row["opened_at"] = _utc(t["opened_at"])
        row["closed_at"] = _utc(t["closed_at"])
        row["generated_at"] = _utc(t["generated_at"])
        row["pnl"] = float(t["pnl_eur"])
        row["r"] = row["pnl"] / float(t["risk_eur"])
        row["exit"] = classify_exit(row)
        series = candles[row["symbol"]]
        index = max(
            (
                i
                for i, k in enumerate(series)
                if k[0] + timedelta(minutes=15) <= row["generated_at"]
            ),
            default=None,
        )
        sign = 1 if row["direction"] == "BUY" else -1
        risk_px = abs(row["open_price"] - row["stop_loss"])
        window = [
            k for k in series if row["opened_at"] - timedelta(minutes=15) <= k[0] < row["closed_at"]
        ]
        if window and risk_px:
            row["mfe_r"] = (
                max(sign * ((k[2] if sign > 0 else k[3]) - row["open_price"]) for k in window)
                / risk_px
            )
            row["mae_r"] = (
                max(-sign * ((k[3] if sign > 0 else k[2]) - row["open_price"]) for k in window)
                / risk_px
            )
        # Cout d'entree : prix paye contre prix de decision, en part du risque.
        row["entry_cost_r"] = (
            sign * (row["open_price"] - row["observed_price"]) / risk_px if risk_px else None
        )
        if index is not None:
            hist = series[max(0, index - 399) : index + 1]
            hs, ls, cs = [k[2] for k in hist], [k[3] for k in hist], [k[4] for k in hist]
            side = Trend.UP if row["direction"] == "BUY" else Trend.DOWN
            for name, (fast, slow) in {"trend_10_30": (10, 30), "trend_20_50": (20, 50)}.items():
                verdict = trend_of(hs, ls, cs, fast=fast, slow=slow, atr_period=14, threshold=0.05)
                row[name] = (
                    "dans"
                    if verdict is side
                    else ("neutre" if verdict is Trend.NEUTRAL else "contre")
                )
            row["session"] = str(session_at(series[index][0]))
        rows.append(row)

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": [rows[0]["opened_at"].isoformat(), rows[-1]["closed_at"].isoformat()]
        if rows
        else None,
        "all": block(rows),
        "by_market": _slice(rows, lambda r: r["symbol"]),
        "by_market_direction": _slice(rows, lambda r: f"{r['symbol']}|{r['direction']}"),
        "by_session": _slice(rows, lambda r: r.get("session", "?")),
        "by_hour_utc_bucket": _slice(
            rows,
            lambda r: (
                f"{(r['opened_at'].hour // 4) * 4:02d}-{(r['opened_at'].hour // 4) * 4 + 3:02d}"
            ),
        ),
        "by_weekday": _slice(rows, lambda r: r["opened_at"].strftime("%a")),
        "by_ref": _slice(rows, lambda r: r["ref"]),
        "by_exit": _slice(rows, lambda r: r["exit"]),
        "by_trend_10_30": _slice(rows, lambda r: r.get("trend_10_30", "?")),
        "by_trend_20_50": _slice(rows, lambda r: r.get("trend_20_50", "?")),
        "costs": {
            "mean_entry_cost_r": round(
                math.fsum(r["entry_cost_r"] for r in rows if r.get("entry_cost_r") is not None)
                / max(1, sum(r.get("entry_cost_r") is not None for r in rows)),
                4,
            ),
            "slippage_max": max((r["slippage"] or 0.0) for r in rows),
            "slippage_nonzero": sum(1 for r in rows if (r["slippage"] or 0.0) != 0.0),
        },
        "excursions": {
            "winners_mfe_r_median": _median(
                [r["mfe_r"] for r in rows if r["pnl"] > 0 and "mfe_r" in r]
            ),
            "winners_mae_r_median": _median(
                [r["mae_r"] for r in rows if r["pnl"] > 0 and "mae_r" in r]
            ),
            "losers_mfe_r_median": _median(
                [r["mfe_r"] for r in rows if r["pnl"] <= 0 and "mfe_r" in r]
            ),
            "losers_mae_r_median": _median(
                [r["mae_r"] for r in rows if r["pnl"] <= 0 and "mae_r" in r]
            ),
            "losers_that_reached_0p8r": sum(
                1 for r in rows if r["pnl"] <= 0 and r.get("mfe_r", 0) >= 0.8
            ),
            "winners_that_reached_1r_or_more": sum(
                1 for r in rows if r["pnl"] > 0 and r.get("mfe_r", 0) >= 1.0
            ),
        },
        "funnel": {
            "signals_by_state": dict(funnel_states),
            "risk_outcomes": dict(Counter(r[0] for r in risk_rows)),
            "risk_top_reasons": Counter(
                str(r[1]).split(":")[0].split(";")[0][:48] for r in risk_rows if r[0] == "refused"
            ).most_common(8),
        },
        "equity": _equity(snapshots),
        "trades": [
            {
                "id": r["id"],
                "symbol": r["symbol"],
                "direction": r["direction"],
                "ref": r["ref"],
                "opened_at": r["opened_at"].isoformat(),
                "closed_at": r["closed_at"].isoformat(),
                "minutes": round((r["closed_at"] - r["opened_at"]).total_seconds() / 60),
                "pnl_eur": round(r["pnl"], 2),
                "r": round(r["r"], 3),
                "exit": r["exit"],
                "mfe_r": round(r["mfe_r"], 2) if "mfe_r" in r else None,
                "mae_r": round(r["mae_r"], 2) if "mae_r" in r else None,
                "trend_10_30": r.get("trend_10_30"),
                "trend_20_50": r.get("trend_20_50"),
                "session": r.get("session"),
            }
            for r in rows
        ],
    }
    print(
        json.dumps({k: v for k, v in report.items() if k != "trades"}, indent=2, ensure_ascii=False)
    )
    if not args.no_write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"ecrit : {args.output}")
    return 0


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    value = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return round(value, 3)


def _equity(snapshots: list[Any]) -> dict[str, Any]:
    if not snapshots:
        return {}
    peak_value = float("-inf")
    worst, worst_at = 0.0, None
    for at, equity in snapshots:
        value = float(equity)
        if value > peak_value:
            peak_value = value
        if peak_value - value > worst:
            worst, worst_at = peak_value - value, at
    return {
        "first": [snapshots[0][0].isoformat(), float(snapshots[0][1])],
        "last": [snapshots[-1][0].isoformat(), float(snapshots[-1][1])],
        "max_drawdown_eur": round(worst, 2),
        "max_drawdown_at": worst_at.isoformat() if worst_at else None,
        "snapshots": len(snapshots),
    }


if __name__ == "__main__":
    sys.exit(main())
