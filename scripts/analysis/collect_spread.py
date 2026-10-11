"""Le spread du courtier, heure par heure, releve sur le compte reel.

**Le trou que ce script comble, et pourquoi il est le plus couteux du projet.** Le diagnostic du
2026-10-09 a mesure un spread BTCUSD de **18,424 $** et en a tire la conclusion qui a fait elargir
la bande d'entree. Mais ce chiffre venait de **onze decisions d'une seule journee**. La recherche
externe du 2026-10-10 l'a classe comme le trou le plus couteux, parce que trois autres questions
en dependent : le choix de la fenetre horaire, le filtre de cout, et le choix du marche lui-meme.
Si le spread s'elargit durablement de 18 a 30 $, aucune bande fondee sur l'ATR ne suffira.

**Ce qu'il fait.** Il interroge le terminal toutes les `--interval` secondes et enregistre, pour
chaque symbole, le bid, l'ask et le spread exprime **en fraction d'ATR M15** — parce que c'est
cette unite-la qui decide si une porte d'entree est calibree, pas le dollar. Il tourne jusqu'a
`--seconds`, ecrit un JSONL, puis rend le tableau par heure UTC.

**Sortie honnete par construction.** Un releve de N minutes ne dit rien du week-end ni des
publications macro. Le rapport ecrit la couverture reelle (premier et dernier instant, nombre de
releves par heure, heures vides) et **refuse de conclure sur une heure qui n'a pas assez de
points**. Un tableau horaire bati sur trois points est du bruit, et ce projet en a deja produit.

    uv run python scripts/analysis/collect_spread.py --seconds 3600
    uv run python scripts/analysis/collect_spread.py --seconds 600 --interval 20

Lecture seule sur le courtier : aucun ordre, aucune ecriture en base. Le terminal est partage avec
l'agent, qui n'envoie d'ordres que sur signal ; lire des cotations ne le perturbe pas.
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

OUTPUT_DIR = ROOT / "docs" / "research" / "spread"
DEFAULT_SYMBOLS = ("XAUUSD", "BTCUSD")
#: En dessous, une heure n'a pas assez de points pour qu'une mediane veuille dire quelque chose.
MIN_SAMPLES_PER_HOUR = 10


def _connect():
    import os

    import MetaTrader5 as mt5
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    ok = mt5.initialize(
        login=int(os.environ["MT5_LOGIN"]),
        password=os.environ["MT5_PASSWORD"],
        server=os.environ["MT5_SERVER"],
    )
    if not ok:
        raise SystemExit(f"terminal indisponible : {mt5.last_error()}")
    account = mt5.account_info()
    return mt5, account


def _atr_m15(mt5, symbol: str, period: int = 14) -> float | None:
    """L'ATR M15 courant, pour exprimer le spread dans l'unite qui decide vraiment."""
    from tradingagent.core.timeframe import Timeframe
    from tradingagent.data.mt5_terminal import Mt5Terminal

    del mt5
    try:
        rows = Mt5Terminal().rates(symbol, Timeframe.M15, period + 2)
    except Exception:
        return None
    if len(rows) < period + 1:
        return None
    ranges = [
        max(row.high - row.low, abs(row.high - previous.close), abs(row.low - previous.close))
        for previous, row in pairwise(rows)
    ]
    if not ranges:
        return None
    return sum(ranges[-period:]) / period


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=3600, help="duree du releve")
    parser.add_argument("--interval", type=int, default=30, help="secondes entre deux releves")
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args(argv)

    mt5, account = _connect()
    started = datetime.now(UTC)
    ends_at = started + timedelta(seconds=args.seconds)
    print(f"releve du {started.isoformat()} pendant {args.seconds} s, toutes les {args.interval} s")
    print(
        f"compte {account.login} ({'demo' if account.trade_mode == 0 else 'REEL'}), "
        f"devise {account.currency}"
    )
    print(f"symboles : {', '.join(args.symbols)}")

    for symbol in args.symbols:
        mt5.symbol_select(symbol, True)

    samples: list[dict[str, object]] = []
    try:
        while datetime.now(UTC) < ends_at:
            at = datetime.now(UTC)
            for symbol in args.symbols:
                tick = mt5.symbol_info_tick(symbol)
                info = mt5.symbol_info(symbol)
                if tick is None or info is None or not tick.ask or not tick.bid:
                    continue
                spread = float(tick.ask) - float(tick.bid)
                atr = _atr_m15(mt5, symbol)
                samples.append(
                    {
                        "at": at.isoformat(),
                        "symbol": symbol,
                        "bid": float(tick.bid),
                        "ask": float(tick.ask),
                        "spread": spread,
                        "atr_m15": atr,
                        "spread_over_atr": (spread / atr) if atr else None,
                    }
                )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\ninterrompu par l'operateur : le releve partiel est conserve")
    finally:
        mt5.shutdown()

    if not samples:
        raise SystemExit("aucun releve : rien a analyser")

    args.output.mkdir(parents=True, exist_ok=True)
    stamp = started.strftime("%Y-%m-%dT%H%M")
    jsonl = args.output / f"spread-{stamp}.jsonl"
    jsonl.write_text(
        "\n".join(json.dumps(sample, ensure_ascii=False) for sample in samples) + "\n",
        encoding="utf-8",
    )

    first = datetime.fromisoformat(str(samples[0]["at"]))
    last = datetime.fromisoformat(str(samples[-1]["at"]))
    covered = (last - first).total_seconds()
    print(f"\n{len(samples)} releve(s) sur {covered / 60:.1f} minutes")
    print(f"du {first.isoformat()} au {last.isoformat()}")
    print(f"ecrit : {jsonl}")

    for symbol in args.symbols:
        rows = [s for s in samples if s["symbol"] == symbol]
        if not rows:
            print(f"\n== {symbol} : aucun releve ==")
            continue
        spreads = sorted(float(s["spread"]) for s in rows)  # type: ignore[arg-type]
        ratios = sorted(
            float(s["spread_over_atr"])  # type: ignore[arg-type]
            for s in rows
            if s["spread_over_atr"] is not None
        )
        print(f"\n== {symbol} : {len(rows)} releve(s) ==")
        print(f"  spread median  {spreads[len(spreads) // 2]:.4f}")
        print(f"  spread min/max {spreads[0]:.4f} / {spreads[-1]:.4f}")
        if ratios:
            print(f"  spread/ATR M15 median {ratios[len(ratios) // 2]:.4f}")

        by_hour: dict[int, list[float]] = defaultdict(list)
        for row in rows:
            by_hour[datetime.fromisoformat(str(row["at"])).hour].append(float(row["spread"]))  # type: ignore[arg-type]
        print("  par heure UTC (seules les heures assez garnies sont concluantes) :")
        for hour in range(24):
            values = sorted(by_hour.get(hour, []))
            if not values:
                continue
            median = values[len(values) // 2]
            mark = "" if len(values) >= MIN_SAMPLES_PER_HOUR else "  (trop peu de points)"
            print(f"    {hour:02d}h  n={len(values):4}  median {median:>10.4f}{mark}")

    print(
        "\nRappel de lecture : un releve de quelques minutes ne dit rien du week-end, des "
        "publications macro ni de la stabilite du courtier. Le tableau horaire n'est "
        "concluant que pour les heures garnies, et la couverture ci-dessus est la seule "
        "que ce fichier peut revendiquer."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
