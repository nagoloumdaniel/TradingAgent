import csv
import io
import json
import xml.etree.ElementTree
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from defusedxml.ElementTree import fromstring as parse_xml_safe

from tradingagent.analytics import Trade, compute_performance
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.reporting.exports import (
    equity_and_drawdown_svg,
    export_performance_json,
    export_trades_csv,
    export_trades_json,
)

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def two_trades():
    return [
        Trade(
            symbol="XAUUSD",
            strategy_ref="witness@1.0.0",
            direction=Direction.BUY,
            timeframe=Timeframe.M15,
            mode=TradingMode.DEMO,
            opened_at=T0,
            closed_at=T0 + timedelta(hours=1),
            pnl_eur=Decimal("150.00"),
            risk_eur=Decimal("100"),
        ),
        Trade(
            symbol="BTCUSD",
            strategy_ref="witness@1.0.0",
            direction=Direction.SELL,
            timeframe=Timeframe.M15,
            mode=TradingMode.DEMO,
            opened_at=T0 + timedelta(hours=2),
            closed_at=T0 + timedelta(hours=3),
            pnl_eur=Decimal("-80.00"),
            risk_eur=Decimal("100"),
        ),
    ]


def parse_svg(svg: str) -> xml.etree.ElementTree.Element:
    """Bounded size, hardened parser: the exporter must produce harmless SVG."""
    assert len(svg) < 100_000
    assert "<!doctype" not in svg.lower() and "<!entity" not in svg.lower()
    element = parse_xml_safe(svg)
    return element


def test_the_csv_carries_the_same_figures_as_the_report() -> None:
    rows = list(csv.DictReader(io.StringIO(export_trades_csv(two_trades()))))

    assert len(rows) == 2
    assert rows[0]["symbol"] == "XAUUSD"
    assert rows[0]["pnl_eur"] == "150.00"
    assert rows[1]["direction"] == "SELL"
    assert rows[1]["risk_eur"] == "100"


def test_the_json_exports_round_trip() -> None:
    trades = json.loads(export_trades_json(two_trades()))
    assert trades[1]["pnl_eur"] == "-80.00"

    performance = compute_performance(two_trades())
    exported = json.loads(export_performance_json(performance))
    assert exported["net_profit"] == "70.00"
    assert exported["trades"] == 2


def test_the_chart_is_a_valid_small_svg() -> None:
    svg = equity_and_drawdown_svg(two_trades(), "Courbe de capital — octobre 2026")

    root = parse_svg(svg)
    assert root.get("width") == "720" and root.get("height") == "280"
    assert len(svg) < 8000  # small enough for any phone connection


def test_an_empty_book_produces_a_valid_placeholder() -> None:
    svg = equity_and_drawdown_svg([], "Courbe de capital")

    root = parse_svg(svg)
    assert root is not None
    assert len(svg) < 500
