"""Order feasibility check on the DEMO account: open the minimum lot, then close it at once.

Refuses to run unless the terminal reports a demo account. Asked by the operator on
2026-10-04 to learn whether synthetic indices are actually tradable on this account.
"""

import logging
import sys

import MetaTrader5 as mt5
from dotenv import dotenv_values

from tradingagent.config.redaction import install_secret_redaction

DEMO = 0
MAGIC = 3031
log = logging.getLogger("tradingagent.feasibility")


def connect(env: dict[str, str | None]) -> None:
    kwargs = {
        "login": int(env["MT5_LOGIN"] or 0),
        "password": env["MT5_PASSWORD"],
        "server": (env["MT5_SERVER"] or "").strip(),
        "timeout": 60_000,
    }
    path = (env.get("MT5_TERMINAL_PATH") or "").strip()
    if not (mt5.initialize(path, **kwargs) if path else mt5.initialize(**kwargs)):
        raise SystemExit(f"initialize failed: {mt5.last_error()}")


def send(request: dict[str, object]) -> object:
    check = mt5.order_check(request)
    log.info(
        "  order_check: retcode=%s comment=%s",
        getattr(check, "retcode", None),
        getattr(check, "comment", mt5.last_error()),
    )
    result = mt5.order_send(request)
    if result is None:
        log.info("  order_send: None, last_error=%s", mt5.last_error())
    else:
        log.info(
            "  order_send: retcode=%s comment=%s price=%s volume=%s order=%s deal=%s",
            result.retcode,
            result.comment,
            result.price,
            result.volume,
            result.order,
            result.deal,
        )
    return result


def try_symbol(symbol: str) -> bool:
    if not mt5.symbol_select(symbol, True):
        log.info("%s: cannot select (%s)", symbol, mt5.last_error())
        return False
    info, tick = mt5.symbol_info(symbol), mt5.symbol_info_tick(symbol)
    if info is None or tick is None or not tick.ask:
        log.info("%s: no quote", symbol)
        return False
    stop = max(20 * info.trade_stops_level * info.point, 50 * info.point)
    log.info("%s: buy %s at ~%s, stop distance %s", symbol, info.volume_min, tick.ask, stop)
    opened = send(
        {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": info.volume_min,
            "type": mt5.ORDER_TYPE_BUY,
            "price": tick.ask,
            "sl": round(tick.ask - stop, info.digits),
            "tp": round(tick.ask + 2 * stop, info.digits),
            "deviation": 50,
            "magic": MAGIC,
            "comment": "feasibility",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_FOK,
        }
    )
    if opened is None or opened.retcode != mt5.TRADE_RETCODE_DONE:
        return False

    positions = [p for p in mt5.positions_get(symbol=symbol) or () if p.magic == MAGIC]
    for position in positions:
        log.info(
            "  position %s: sl=%s tp=%s (native protections set: %s)",
            position.ticket,
            position.sl,
            position.tp,
            bool(position.sl and position.tp),
        )
        bid = mt5.symbol_info_tick(symbol).bid
        send(
            {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": position.volume,
                "type": mt5.ORDER_TYPE_SELL,
                "position": position.ticket,
                "price": bid,
                "deviation": 50,
                "magic": MAGIC,
                "comment": "feasibility close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_FOK,
            }
        )
    left = [p for p in mt5.positions_get(symbol=symbol) or () if p.magic == MAGIC]
    log.info("  positions left open by this test: %d", len(left))
    return not left


def main() -> None:
    env = dotenv_values(".env")
    install_secret_redaction([env["MT5_PASSWORD"] or ""])
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    connect(env)
    try:
        account = mt5.account_info()
        if account.trade_mode != DEMO:
            raise SystemExit("REFUSED: the terminal does not report a demo account")
        log.info(
            "account: DEMO, currency %s, balance %s, trade_allowed %s",
            account.currency,
            account.balance,
            account.trade_allowed,
        )
        symbols = sys.argv[1:] or ["Volatility 100 Index"]
        for symbol in symbols:
            log.info("%s -> %s", symbol, "TRADABLE" if try_symbol(symbol) else "NOT TRADABLE")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
