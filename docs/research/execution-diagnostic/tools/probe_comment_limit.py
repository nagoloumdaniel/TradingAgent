"""Sonde de validation des arguments de `MetaTrader5.order_check` — AUCUN ordre n'est envoyé.

`order_check` est une vérification de marge : la documentation MQL5 dit explicitement qu'elle
n'envoie aucun ordre. C'est aussi la fonction que le dépôt utilise déjà en production comme
« informative only ». On ne touche jamais à `order_send`.

Le module MetaTrader5 C-extension valide les champs du dictionnaire AVANT toute IPC ; rejouer
`order_check` avec des commentaires de longueurs différentes révèle donc la borne exacte que le
module applique, sans rien transmettre au courtier.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import MetaTrader5 as mt5  # noqa: E402

from tradingagent.execution.mt5_broker import key_comment  # noqa: E402
from tradingagent.runtime.pipeline import order_comment  # noqa: E402

KEY = "trend_breakout@1.0.1:BTCUSD:M15:2026-10-09T03:45Z"
HEX = hashlib.sha256(KEY.encode()).hexdigest()
RUNTIME = key_comment(KEY, order_comment(KEY))  # exactly what MT5Broker.place built


def request(comment: object) -> dict[str, object]:
    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": "BTCUSD",
        "volume": 0.07,
        "type": mt5.ORDER_TYPE_BUY,
        "deviation": 50,
        "magic": 20261009,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
        "price": 82373.659,
        "sl": 81969.9147877213,
        "tp": 83138.4394245574,
    }


def main() -> int:
    print("terminal_info  :", mt5.terminal_info())
    print("last_error     :", mt5.last_error())
    print("version        :", mt5.__version__)
    print("comment runtime:", repr(RUNTIME), "longueur", len(RUNTIME))
    print()
    accepted, rejected = [], []
    cases: list[tuple[str, object]] = [("None (mauvais type)", None), ("vide", "")]
    for length in range(0, 34):
        cases.append((f"longueur {length}", (RUNTIME + "x" * 33)[:length]))
    for label, comment in cases:
        mt5.last_error()  # purge
        mt5.order_check(request(comment))
        error = mt5.last_error()
        if error[0] == -2:
            rejected.append(comment if isinstance(comment, str) else None)
            verdict = "REFUSE par le module"
        else:
            accepted.append(comment)
            verdict = "accepte (passe a l'IPC)"
        shown = "n/a" if comment is None else f"len={len(comment)}"
        print(f"{label:<20} {shown:<8} -> {verdict}  last_error={error}")
    print()
    print("longueurs acceptees :", sorted(len(c) for c in accepted if isinstance(c, str)))
    print("longueurs refusees  :", sorted(len(c) for c in rejected if isinstance(c, str)))
    print("terminal_info apres :", mt5.terminal_info())
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
