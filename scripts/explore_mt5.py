"""TASK-003: measure what Deriv MT5 really offers. Read-only: never sends an order.

Writes docs/reports/<date>-mt5-capabilities.md and its raw .json annex. The account number
is masked and no local path is written, so both files can be committed.
"""

import itertools
import json
import logging
import statistics
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import MetaTrader5 as mt5
from dotenv import dotenv_values

from tradingagent.config.redaction import install_secret_redaction
from tradingagent.indicators.volatility import atr

REPORT_DIR = Path("docs/reports")
REFERENCE_CAPITAL = 100.0  # EUR, C-009
LIVE_RISK_CAP_PCT = 5.0  # RM-005
STOP_ATR_MULTIPLIER = 1.5  # witness default, a typical stop
ATR_PERIOD = 14
# Discovery on 2026-10-03 found 2 gold symbols and 46 cryptos: measure gold and the most liquid
# cryptos in depth, and a few synthetic indices for their specification only.
GOLD = ("XAUUSD", "XAUEUR")
CRYPTO = ("BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "LTCUSD", "ADAUSD")
SYNTHETIC_GROUPS = ("Volatility Indices", "Step Indices")
SYNTHETIC_PER_GROUP = 2
TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}
ACCOUNT_MODES = {0: "DEMO", 1: "CONTEST", 2: "REAL"}

log = logging.getLogger("tradingagent.explore")


def connect(env: dict[str, str | None]) -> None:
    kwargs: dict[str, Any] = {
        "login": int(env["MT5_LOGIN"] or 0),
        "password": env["MT5_PASSWORD"],
        "server": (env["MT5_SERVER"] or "").strip(),
        "timeout": 60_000,
    }
    path = (env.get("MT5_TERMINAL_PATH") or "").strip()
    ok = mt5.initialize(path, **kwargs) if path else mt5.initialize(**kwargs)
    if not ok:
        raise SystemExit(f"initialize failed: {mt5.last_error()}")


def stable_rates(symbol: str, timeframe: int, start: datetime, end: datetime) -> Any:
    """The terminal downloads history lazily: repeat until two reads agree."""
    previous = -1
    rates = None
    for _ in range(4):
        rates = mt5.copy_rates_range(symbol, timeframe, start, end)
        count = 0 if rates is None else len(rates)
        if count == previous:
            break
        previous = count
        time.sleep(1.0)
    return rates


def latest_rates(symbol: str, timeframe: int, max_bars: int) -> Any:
    """Most recent bars, one fewer than the terminal's max-bars setting.

    Measured on 2026-10-03: any request for max_bars bars or more, by count or by a date
    range holding that many, fails whole with (-2, 'Terminal: Invalid params') instead of
    returning a partial answer.
    """
    previous = -1
    rates = None
    for _ in range(6):
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, max_bars - 1)
        size = 0 if rates is None else len(rates)
        if size == previous:
            break
        previous = size
        time.sleep(1.5)
    return rates


def server_time(epoch: int) -> datetime:
    # MT5 epochs are the broker server's wall clock written as if it were UTC.
    return datetime.fromtimestamp(int(epoch), tz=UTC)


def specification(info: Any) -> dict[str, Any]:
    fields = (
        "name",
        "path",
        "description",
        "currency_base",
        "currency_profit",
        "currency_margin",
        "digits",
        "point",
        "trade_contract_size",
        "volume_min",
        "volume_step",
        "volume_max",
        "trade_tick_size",
        "trade_tick_value",
        "trade_stops_level",
        "trade_freeze_level",
        "trade_mode",
        "trade_exemode",
        "filling_mode",
        "trade_calc_mode",
        "swap_long",
        "swap_short",
        "spread",
        "spread_float",
    )
    return {field: getattr(info, field) for field in fields}


def measure_symbol(
    name: str, account_currency: str, max_bars: int, with_history: bool = True
) -> dict[str, Any]:
    mt5.symbol_select(name, True)
    info = mt5.symbol_info(name)
    tick = mt5.symbol_info_tick(name)
    result: dict[str, Any] = {"specification": specification(info)}
    if tick is None or info is None:
        result["error"] = f"no tick: {mt5.last_error()}"
        return result
    result["last_tick_server_time"] = server_time(tick.time).isoformat()
    result["bid"], result["ask"] = tick.bid, tick.ask
    result["has_current_quote"] = bool(tick.time and tick.bid and tick.ask)

    rates = mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_M15, 0, 300)
    if rates is not None and len(rates) > ATR_PERIOD + 1:
        highs = [float(r["high"]) for r in rates]
        lows = [float(r["low"]) for r in rates]
        closes = [float(r["close"]) for r in rates]
        volatility = atr(highs, lows, closes, ATR_PERIOD)[-1]
        median_spread = statistics.median(float(r["spread"]) for r in rates) * info.point
    else:
        volatility, median_spread = None, None
    result["atr14_m15"] = volatility
    result["median_spread_m15"] = median_spread

    price = tick.ask or closes[-1]
    margin = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, name, info.volume_min, price)
    result["margin_min_lot"] = margin
    if volatility:
        stop = max(STOP_ATR_MULTIPLIER * volatility, info.trade_stops_level * info.point)
        formula = info.volume_min * (stop / info.trade_tick_size) * info.trade_tick_value
        terminal = mt5.order_calc_profit(
            mt5.ORDER_TYPE_BUY, name, info.volume_min, price, price - stop
        )
        result["typical_stop_distance"] = stop
        result["risk_min_lot_formula"] = formula
        result["risk_min_lot_terminal"] = None if terminal is None else -terminal
        risk = result["risk_min_lot_terminal"] or formula
        cap = REFERENCE_CAPITAL * LIVE_RISK_CAP_PCT / 100
        result["live_eligible"] = bool(
            risk <= cap and margin is not None and margin <= REFERENCE_CAPITAL
        )
        shown_margin = None if margin is None else round(margin, 2)
        result["live_eligibility_reason"] = (
            f"risk {risk:.2f} {account_currency} vs cap {cap:.2f}; "
            f"margin {shown_margin} vs capital {REFERENCE_CAPITAL}"
        )

    if not with_history:
        return result
    history: dict[str, Any] = {}
    for label, timeframe in TIMEFRAMES.items():
        log.info("  %s history %s", name, label)
        bars = latest_rates(name, timeframe, max_bars)
        if bars is None or len(bars) == 0:
            history[label] = {"bars": 0}
            continue
        history[label] = {
            "bars": len(bars),
            "first": server_time(bars[0]["time"]).isoformat(),
            "last": server_time(bars[-1]["time"]).isoformat(),
            "capped_by_max_bars_setting": len(bars) >= max_bars - 1,
        }
    result["history"] = history

    first_tick = mt5.copy_ticks_from(name, datetime(2010, 1, 1, tzinfo=UTC), 1, mt5.COPY_TICKS_ALL)
    result["first_tick_server_time"] = (
        None
        if first_tick is None or len(first_tick) == 0
        else server_time(first_tick[0]["time"]).isoformat()
    )
    return result


def server_offset(names: list[str]) -> dict[str, Any]:
    """Offset of the broker clock, read on a 24/7 market with a fresh tick."""
    for name in names:
        tick = mt5.symbol_info_tick(name)
        if tick is None:
            continue
        raw = int(tick.time) - int(time.time())
        rounded_hours = round(raw / 1800) / 2
        residual = raw - rounded_hours * 3600
        if abs(residual) <= 120:
            return {
                "symbol": name,
                "raw_seconds": raw,
                "offset_hours": rounded_hours,
                "tick_age_seconds": -residual,
            }
    return {"error": "no fresh 24/7 tick to measure the offset"}


def weekly_open_hours(name: str) -> dict[str, Any]:
    """Server hour of the first bar after each weekend gap, by month.

    Constant across the year: the server shifts with daylight saving time.
    One hour apart between winter and summer: the server keeps a fixed offset.
    """
    end = datetime.now(UTC) + timedelta(days=2)
    bars = stable_rates(name, mt5.TIMEFRAME_H1, end - timedelta(days=800), end)
    if bars is None or len(bars) < 2:
        return {"error": "no H1 history"}
    by_month: dict[int, Counter[int]] = defaultdict(Counter)
    daily_gap_hours: Counter[int] = Counter()
    for previous, current in itertools.pairwise(bars):
        gap_hours = (int(current["time"]) - int(previous["time"])) / 3600
        opened = server_time(current["time"])
        if gap_hours > 24:
            by_month[opened.month][opened.hour] += 1
        elif 1 < gap_hours <= 3:
            daily_gap_hours[server_time(previous["time"]).hour + 1] += 1
    return {
        "symbol": name,
        "weekly_open_hour_by_month": {m: dict(c) for m, c in sorted(by_month.items())},
        "daily_break_start_hour_counts": dict(daily_gap_hours.most_common(5)),
    }


def latency(name: str) -> dict[str, float]:
    tick_ms, rates_ms = [], []
    for _ in range(30):
        started = time.perf_counter()
        mt5.symbol_info_tick(name)
        tick_ms.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        mt5.copy_rates_from_pos(name, mt5.TIMEFRAME_M15, 0, 300)
        rates_ms.append((time.perf_counter() - started) * 1000)
    return {
        "tick_median_ms": statistics.median(tick_ms),
        "tick_max_ms": max(tick_ms),
        "rates300_median_ms": statistics.median(rates_ms),
        "rates300_max_ms": max(rates_ms),
    }


def main() -> None:
    env = dotenv_values(".env")
    install_secret_redaction([env["MT5_PASSWORD"] or ""])
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    connect(env)
    try:
        account, terminal = mt5.account_info(), mt5.terminal_info()
        symbols = mt5.symbols_get()
        available = {s.name for s in symbols}
        groups = Counter(s.path.split("\\")[0] for s in symbols)
        gold = [name for name in GOLD if name in available]
        crypto = [name for name in CRYPTO if name in available]
        all_crypto = sorted(s.name for s in symbols if s.path.startswith("Crypto\\"))
        synthetic_names = {
            group: sorted(s.name for s in symbols if s.path.startswith(group + "\\"))
            for group in groups
            if "Indices" in group and group not in ("Stock Indices",)
        }
        synthetic = [
            name
            for group in SYNTHETIC_GROUPS
            for name in synthetic_names.get(group, [])[:SYNTHETIC_PER_GROUP]
        ]

        measured = {}
        for name in gold + crypto:
            log.info("measuring %s", name)
            measured[name] = measure_symbol(name, account.currency, terminal.maxbars)
        for name in synthetic:
            log.info("measuring %s (specification only)", name)
            measured[name] = measure_symbol(name, account.currency, terminal.maxbars, False)

        report = {
            "measured_at_utc": datetime.now(UTC).isoformat(),
            "account": {
                "login_masked": f"...{str(account.login)[-2:]}",
                "mode": ACCOUNT_MODES.get(account.trade_mode, account.trade_mode),
                "currency": account.currency,
                "leverage": account.leverage,
                "trade_allowed": account.trade_allowed,
                "company": account.company,
                "server": account.server,
                "margin_mode": account.margin_mode,
            },
            "terminal": {
                "build": terminal.build,
                "connected": terminal.connected,
                "max_bars_setting": terminal.maxbars,
            },
            "symbol_groups": dict(groups),
            "gold_measured": gold,
            "crypto_measured": crypto,
            "crypto_available": all_crypto,
            "synthetic_available": synthetic_names,
            "synthetic_measured_specification_only": synthetic,
            "symbols": measured,
            "server_clock": server_offset(crypto),
            "session_inference": weekly_open_hours(gold[0]) if gold else None,
            "latency": latency(crypto[0] if crypto else gold[0]),
        }
    finally:
        mt5.shutdown()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stem = REPORT_DIR / f"{datetime.now(UTC).date()}-mt5-capabilities"
    stem.with_suffix(".json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    log.info("raw report written to %s", stem.with_suffix(".json"))


if __name__ == "__main__":
    main()
