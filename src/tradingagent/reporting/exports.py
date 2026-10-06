"""Exports and charts (F-023, EF-028, TASK-043).

CSV and JSON exports carry exactly the figures the analytics package computed — the
export and the report share one source. The equity curve and drawdown chart are drawn
as plain SVG: no raster dependency, small files, and the phone screen — Telegram's main
target — renders them crisply at any zoom.
"""

import csv
import io
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from tradingagent.analytics import Performance, Trade


def export_trades_csv(trades: list[Trade]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "symbol",
            "strategy_ref",
            "direction",
            "timeframe",
            "mode",
            "opened_at",
            "closed_at",
            "pnl_eur",
            "risk_eur",
        ]
    )
    for trade in trades:
        writer.writerow(
            [
                trade.symbol,
                trade.strategy_ref,
                trade.direction.value,
                trade.timeframe.value,
                trade.mode.value,
                trade.opened_at.isoformat(),
                trade.closed_at.isoformat(),
                str(trade.pnl_eur),
                str(trade.risk_eur),
            ]
        )
    return buffer.getvalue()


def export_trades_json(trades: list[Trade]) -> str:
    return json.dumps([_trade_dict(trade) for trade in trades], indent=2)


def export_performance_json(performance: Performance) -> str:
    return json.dumps(asdict(performance), indent=2, default=str)


def _trade_dict(trade: Trade) -> dict[str, str]:
    return {
        "symbol": trade.symbol,
        "strategy_ref": trade.strategy_ref,
        "direction": trade.direction.value,
        "timeframe": trade.timeframe.value,
        "mode": trade.mode.value,
        "opened_at": trade.opened_at.isoformat(),
        "closed_at": trade.closed_at.isoformat(),
        "pnl_eur": str(trade.pnl_eur),
        "risk_eur": str(trade.risk_eur),
    }


def _equity_series(trades: list[Trade]) -> list[tuple[str, Decimal]]:
    ordered = sorted(trades, key=lambda trade: trade.closed_at)
    points: list[tuple[str, Decimal]] = []
    cumulative = Decimal(0)
    for trade in ordered:
        cumulative += trade.pnl_eur
        points.append((f"{trade.closed_at:%m-%d %H:%M}", cumulative))
    return points


WIDTH, HEIGHT = 720, 280
MARGIN = 44


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def equity_and_drawdown_svg(trades: list[Trade], title: str) -> str:
    """One SVG with the equity curve on top and the drawdown area below it."""
    series = _equity_series(trades)
    if not series:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}">'
            f'<text x="{MARGIN}" y="{HEIGHT // 2}">aucune donnee</text></svg>'
        )
    values = [value for _, value in series]
    low, high = min(values), max(values)
    span = high - low or Decimal(1)
    draw = HEIGHT - 2 * MARGIN
    step_x = (WIDTH - 2 * MARGIN) / max(len(series) - 1, 1)

    def xy(index: int, value: Decimal) -> tuple[float, float]:
        return MARGIN + index * step_x, MARGIN + draw * float((high - value) / span)

    curve = " ".join(
        f"{x:.1f},{y:.1f}" for index, (_, value) in enumerate(series) for x, y in [xy(index, value)]
    )
    # Drawdown: distance below the running peak, drawn as a filled area under the curve.
    peak = values[0]
    dd_points: list[str] = []
    for index, (_, value) in enumerate(series):
        peak = max(peak, value)
        _, y_equity = xy(index, value)
        y_peak = xy(index, peak)[1]
        dd_points.append(f"{xy(index, value)[0]:.1f},{y_peak:.1f}")
        dd_points.append(f"{xy(index, value)[0]:.1f},{max(y_equity, y_peak):.1f}")
    labels = series[0][0] + " … " + series[-1][0]

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" font-family="sans-serif" font-size="13">'
        f"<title>{_escape(title)}</title>"
        f'<text x="{MARGIN}" y="24">{_escape(title)} — {low:.2f} à {high:.2f} EUR, {labels}</text>'
        f'<polyline fill="none" stroke="#1a6fb5" stroke-width="2" points="{curve}"/>'
        f'<polygon fill="#d94f3d" fill-opacity="0.25" points="{" ".join(dd_points)}"/>'
        f'<line x1="{MARGIN}" y1="{HEIGHT - MARGIN}" x2="{WIDTH - MARGIN}" '
        f'y2="{HEIGHT - MARGIN}" stroke="#666"/>'
        "</svg>"
    )


def write_export(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
