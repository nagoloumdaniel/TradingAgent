"""Reconstruction hors base des décisions de risque de l'incident (brouillon de vérification).

Vérifie que les valeurs reconstruites depuis `risk_decisions.checks` reproduisent exactement
les décisions enregistrées, avant d'en faire un test.
"""

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tests.risk.builders import LOGIN, RISK  # noqa: E402

from tradingagent.core.halt import TRADING  # noqa: E402
from tradingagent.core.market import Direction  # noqa: E402
from tradingagent.core.mode import TradingMode  # noqa: E402
from tradingagent.data.market_calendar import SlotStatus  # noqa: E402
from tradingagent.risk.checks import RiskContext  # noqa: E402
from tradingagent.risk.engine import decide  # noqa: E402
from tradingagent.risk.model import (  # noqa: E402
    AccountState,
    InstrumentSpec,
    MarketQuote,
    PortfolioState,
    TradeIntent,
    limits_for,
)

EQUITY = D("5496.67")
PEAK = D("5496.96")
RATE = D("0.891")
BTC = InstrumentSpec("BTCUSD", D(1), D("0.01"), D("0.01"), D(5), D("0.001"), 20000)


def context(
    *,
    signal_id: int,
    mode: TradingMode,
    entry_low: str,
    entry_high: str,
    stop_loss: str,
    ask: str,
    spread: str,
    margin_one_lot: str,
    now: str,
) -> RiskContext:
    ask_d = D(ask)
    bid = ask_d - D(spread)
    distance = abs(ask_d - D(stop_loss))
    loss_one_lot = BTC.contract_size * distance * RATE
    limits = limits_for(mode, RISK)
    intent = TradeIntent(
        signal_id, "BTCUSD", Direction.BUY, D(entry_low), D(entry_high), D(stop_loss)
    )
    return RiskContext(
        intent=intent,
        account=AccountState(LOGIN, True, "EUR", EQUITY, EQUITY, EQUITY),
        spec=BTC,
        quote=MarketQuote(bid, ask_d, loss_one_lot, D(margin_one_lot), RATE),
        portfolio=PortfolioState((), 0, EQUITY, D(0), EQUITY, D(0), PEAK, 0, None),
        market=SlotStatus.OPEN,
        limits=limits,
        now=datetime.fromisoformat(now),
        halt=TRADING,
    )


SIGNALS = {
    12: dict(
        signal_id=12,
        mode=TradingMode.DEMO,
        entry_low="82339.9475893861",
        entry_high="82378.8984106139",
        stop_loss="81969.9147877213",
        ask="82373.657",
        spread="18.424",
        margin_one_lot="36695.98",
        now="2026-10-09T03:45:14.407657+00:00",
    ),
    13: dict(
        signal_id=13,
        mode=TradingMode.SIGNAL,
        entry_low="82365.4312758585",
        entry_high="82403.1287241415",
        stop_loss="82007.3055171698",
        ask="82404.176",
        spread="18.424",
        margin_one_lot="36701.0",
        now="2026-10-09T04:00:10.296382+00:00",
    ),
    14: dict(
        signal_id=14,
        mode=TradingMode.DEMO,
        entry_low="82365.4312758585",
        entry_high="82403.1287241415",
        stop_loss="82007.3055171698",
        ask="82404.19",
        spread="18.424",
        margin_one_lot="36701.0",
        now="2026-10-09T04:00:13.006055+00:00",
    ),
    15: dict(
        signal_id=15,
        mode=TradingMode.SIGNAL,
        entry_low="82454.1918490115",
        entry_high="82490.8041509886",
        stop_loss="82106.3749802291",
        ask="82491.162",
        spread="18.424",
        margin_one_lot="36743.0",
        now="2026-10-09T04:15:08.867533+00:00",
    ),
    16: dict(
        signal_id=16,
        mode=TradingMode.DEMO,
        entry_low="82454.1918490115",
        entry_high="82490.8041509886",
        stop_loss="82106.3749802291",
        ask="82491.164",
        spread="18.424",
        margin_one_lot="36743.0",
        now="2026-10-09T04:15:12.370823+00:00",
    ),
}

for signal_id, fields in SIGNALS.items():
    ctx = context(**fields)
    decision = decide(ctx, LOGIN)
    record = decision.checks_record()
    print(f"--- #{signal_id} {fields['mode']} ---")
    print("  outcome :", decision.outcome.value)
    print("  reason  :", decision.reason)
    print("  refusals:", [c.name for c in decision.refusals])
    print("  sizing  :", record["sizing"]["reason"])
    print(
        "  stop    :",
        record["stop_loss"]["reason"],
        "| attendu",
        "distance " + str(abs(ctx.exit_price - ctx.intent.stop_loss)),
    )
    print("  spread  :", record["spread"]["reason"])
    print("  margin  :", record["margin"]["reason"])
    print("  daily   :", record["daily_loss"]["reason"])
    print("  drawdown:", record["drawdown"]["reason"])
