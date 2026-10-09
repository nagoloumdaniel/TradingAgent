"""Lecture seule de l'incident d'exécution DEMO du 2026-10-09 (task-8, axe D).

Aucune écriture : uniquement des SELECT. Usage :

    uv run python docs/research/execution-diagnostic/tools/dump_incident.py
    uv run python docs/research/execution-diagnostic/tools/dump_incident.py --signals 11,12,13
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tradingagent.config.settings import load_settings  # noqa: E402


def engine():
    settings = load_settings(ROOT / ".env")
    url = settings.database_url.get_secret_value()
    print(f"# database: {make_url(url).render_as_string(hide_password=True)}")
    return create_engine(url)


def dump(connection, label: str, sql: str, params: dict | None = None) -> None:
    print(f"\n=== {label} ===")
    rows = connection.execute(text(sql), params or {}).mappings().all()
    if not rows:
        print("(aucune ligne)")
        return
    for row in rows:
        parts = []
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            parts.append(f"{key}={value}")
        print(" | ".join(parts))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--signals",
        default="11,12,13,14,15,16",
        help="identifiants de signaux à détailler",
    )
    args = parser.parse_args()
    ids = [int(part) for part in args.signals.split(",") if part.strip()]

    connection_engine = engine()
    with connection_engine.connect() as connection:
        dump(
            connection,
            "signals (6 derniers)",
            """
            SELECT id, symbol, timeframe, direction, mode, state, observed_price,
                   entry_low, entry_high, stop_loss, take_profits, generated_at, expires_at,
                   idempotency_key, reason, indicators
            FROM signals ORDER BY id DESC LIMIT 6
            """,
        )
        dump(
            connection,
            "risk_decisions (toutes)",
            """
            SELECT id, signal_id, outcome, decided_at, volume, risk_eur, margin_eur,
                   reason, checks
            FROM risk_decisions ORDER BY id
            """,
        )
        dump(
            connection,
            "signal_events",
            """
            SELECT id, signal_id, state, occurred_at, detail
            FROM signal_events WHERE signal_id = ANY(:ids) ORDER BY id
            """,
            {"ids": ids},
        )
        dump(
            connection,
            "orders",
            """
            SELECT id, signal_id, symbol, direction, volume, requested_price, stop_loss,
                   take_profit, mode, state, broker_order_ticket, retcode, broker_comment,
                   created_at, updated_at
            FROM orders ORDER BY id
            """,
        )
        dump(
            connection,
            "positions",
            """
            SELECT id, order_id, broker_position_ticket, symbol, direction, volume,
                   open_price, stop_loss, take_profit, mode, state, opened_at, updated_at
            FROM positions ORDER BY id
            """,
        )
        dump(
            connection,
            "trades",
            """
            SELECT id, position_id, mode, closed_at, close_price, pnl_eur, risk_eur,
                   exit_reason
            FROM trades ORDER BY id
            """,
        )
        dump(
            connection,
            "executions",
            """
            SELECT id, order_id, broker_deal_ticket, price, volume, slippage, executed_at
            FROM executions ORDER BY id
            """,
        )
        dump(
            connection,
            "execution_events",
            """
            SELECT id, order_id, signal_id, symbol, kind, occurred_at, detail
            FROM execution_events WHERE signal_id = ANY(:ids) OR signal_id IS NULL
            ORDER BY id
            """,
            {"ids": ids},
        )
        dump(
            connection,
            "system_events (2026-10-08 -> 2026-10-09)",
            """
            SELECT id, kind, severity, symbol, occurred_at, detail
            FROM system_events
            WHERE occurred_at >= '2026-10-08T00:00:00+00:00' ORDER BY id
            """,
        )
        dump(
            connection,
            "halt_commands",
            """
            SELECT id, scope, action, close_positions, source, reason, actor, occurred_at
            FROM halt_commands ORDER BY id
            """,
        )
        dump(
            connection,
            "account_snapshots (2026-10-08 -> 2026-10-09)",
            """
            SELECT id, equity, balance, at FROM account_snapshots
            WHERE at >= '2026-10-08T00:00:00+00:00' ORDER BY at
            """,
        )
        dump(
            connection,
            "strategy_registry",
            """
            SELECT id, market, ref, strategy_id, version, status, origin, created_at,
                   promoted_at, parameters
            FROM strategy_registry ORDER BY id
            """,
        )
        dump(
            connection,
            "candles BTCUSD M15 (2026-10-09 02:00 -> 05:00)",
            """
            SELECT symbol, timeframe, open_time, open, high, low, close, tick_volume,
                   source, ingested_at
            FROM candles
            WHERE symbol='BTCUSD' AND timeframe='M15'
              AND open_time >= '2026-10-09T02:00:00+00:00'
              AND open_time <= '2026-10-09T05:00:00+00:00'
            ORDER BY open_time
            """,
        )
        dump(
            connection,
            "counts",
            """
            SELECT (SELECT count(*) FROM signals) AS signals,
                   (SELECT count(*) FROM risk_decisions) AS risk_decisions,
                   (SELECT count(*) FROM orders) AS orders,
                   (SELECT count(*) FROM positions) AS positions,
                   (SELECT count(*) FROM trades) AS trades,
                   (SELECT count(*) FROM executions) AS executions
            """,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
