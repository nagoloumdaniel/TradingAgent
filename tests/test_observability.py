"""Tests for TASK-054 observability: JSON logs, metrics, resources (ENF-006, F-024)."""

import io
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tradingagent.observability import (
    JsonFormatter,
    Metrics,
    ResourceMonitor,
    configure_json_logging,
    default_rss_bytes,
    format_snapshot,
    upgrade_level,
)

# Assembled at runtime so the source carries no complete credential-shaped literal;
# the string the redaction code sees is unchanged (same convention as test_secret_detector).
FAKE_CREDENTIAL = "deriv-" + "token-0123456789" + "abcdef"


@pytest.fixture
def restore_logging() -> Any:
    """Leave the process logging state as we found it: notify/tests share the root logger."""
    root = logging.getLogger()
    factory = logging.getLogRecordFactory()
    handlers = list(root.handlers)
    level = root.level
    yield
    root.handlers = handlers
    root.setLevel(level)
    logging.setLogRecordFactory(factory)


def record(message: str = "hello", **extra: Any) -> logging.LogRecord:
    built = logging.LogRecord(
        name="tradingagent.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(built, key, value)
    return built


def test_json_formatter_emits_one_parseable_object() -> None:
    payload = json.loads(JsonFormatter().format(record("signal envoyé")))
    assert payload["level"] == "INFO"
    assert payload["logger"] == "tradingagent.test"
    assert payload["message"] == "signal envoyé"
    assert payload["timestamp"].endswith("+00:00")


def test_json_formatter_uses_the_injected_clock() -> None:
    frozen = datetime(2026, 10, 7, 4, 30, tzinfo=UTC)
    formatter = JsonFormatter(now=lambda _created: frozen)
    payload = json.loads(formatter.format(record()))
    assert payload["timestamp"] == frozen.isoformat()


def test_json_formatter_keeps_structured_extras() -> None:
    built = record("latence", symbol="frxXAUUSD", latency_ms=12.5)
    payload = json.loads(JsonFormatter().format(built))
    assert payload["extra"] == {"symbol": "frxXAUUSD", "latency_ms": 12.5}


def test_json_formatter_renders_the_exception() -> None:
    try:
        raise ValueError("connexion perdue")
    except ValueError:
        info = sys.exc_info()
    built = record("échec", exc_info=info)
    payload = json.loads(JsonFormatter().format(built))
    assert "ValueError" in payload["exception"]
    assert "connexion perdue" in payload["exception"]


def test_json_formatter_redacts_extra_values_too() -> None:
    built = record("échec", detail=f"token={FAKE_CREDENTIAL}")
    line = JsonFormatter(secrets=[FAKE_CREDENTIAL]).format(built)
    assert FAKE_CREDENTIAL not in line
    assert "***" in line


def test_configure_json_logging_installs_one_redacting_handler(restore_logging: Any) -> None:
    logger = logging.getLogger("tradingagent.test.json")
    logger.propagate = False
    stream = io.StringIO()
    configure_json_logging(logging.DEBUG, [FAKE_CREDENTIAL], stream=stream, logger=logger)
    try:
        logger.setLevel(logging.DEBUG)
        logger.info("jeton %s refusé", FAKE_CREDENTIAL)
        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    finally:
        logger.propagate = True
        logger.handlers.clear()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["message"] == "jeton *** refusé"
    assert FAKE_CREDENTIAL not in lines[0]


def test_configure_json_logging_is_idempotent(restore_logging: Any) -> None:
    logger = logging.getLogger("tradingagent.test.idempotent")
    logger.propagate = False
    try:
        first = configure_json_logging(
            "INFO", [FAKE_CREDENTIAL], stream=io.StringIO(), logger=logger
        )
        second = configure_json_logging(
            "INFO", [FAKE_CREDENTIAL], stream=io.StringIO(), logger=logger
        )
        assert first not in logger.handlers
        assert logger.handlers == [second]
        assert logger.level == logging.INFO
    finally:
        logger.propagate = True
        logger.handlers.clear()


def test_configure_json_logging_rejects_an_unknown_level(restore_logging: Any) -> None:
    with pytest.raises(ValueError, match="unknown log level"):
        configure_json_logging("VERBOSE", [], logger=logging.getLogger("tradingagent.test.bad"))


def test_upgrade_level_accepts_names_and_numbers() -> None:
    assert upgrade_level("warning") == logging.WARNING
    assert upgrade_level(logging.ERROR) == logging.ERROR


def test_counters_accumulate() -> None:
    metrics = Metrics()
    metrics.incr("api_errors")
    metrics.incr("api_errors", 4)
    assert metrics.snapshot()["counters"] == {"api_errors": 5}


def test_latencies_aggregate() -> None:
    metrics = Metrics()
    for seconds in (0.2, 0.4, 0.3):
        metrics.observe_latency("deriv_quote", seconds)
    entry = metrics.snapshot()["latencies"]["deriv_quote"]
    assert entry["count"] == 3
    assert entry["min_seconds"] == pytest.approx(0.2)
    assert entry["max_seconds"] == pytest.approx(0.4)
    assert entry["last_seconds"] == pytest.approx(0.3)
    assert entry["mean_seconds"] == pytest.approx(0.3)
    assert entry["total_seconds"] == pytest.approx(0.9)


def test_a_negative_latency_is_refused() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        Metrics().observe_latency("bad", -1.0)


def test_metrics_never_read_the_clock_implicitly() -> None:
    current = datetime(2026, 10, 7, 4, 30, tzinfo=UTC)
    metrics = Metrics(now=lambda: current)
    metrics.set_connection("frxXAUUSD", True)
    current = current + timedelta(seconds=90)
    metrics.set_connection("BTCUSD", False)
    snapshot = metrics.snapshot()
    assert snapshot["uptime_seconds"] == pytest.approx(90.0)
    assert snapshot["available"] is False
    assert snapshot["connections"]["frxXAUUSD"] == {
        "ok": True,
        "since": datetime(2026, 10, 7, 4, 30, tzinfo=UTC).isoformat(),
        "seconds": 90.0,
    }
    assert snapshot["connections"]["BTCUSD"]["seconds"] == 0.0


def test_availability_is_true_when_every_connection_is_up() -> None:
    metrics = Metrics()
    metrics.set_connection("frxXAUUSD", True)
    assert metrics.snapshot()["available"] is True


def test_a_repeated_connection_state_does_not_move_the_timestamp() -> None:
    current = datetime(2026, 10, 7, 4, 30, tzinfo=UTC)
    metrics = Metrics(now=lambda: current)
    metrics.set_connection("frxXAUUSD", True)
    current = current + timedelta(seconds=5)
    metrics.set_connection("frxXAUUSD", True)
    connections = metrics.snapshot()["connections"]["frxXAUUSD"]
    assert connections["since"] == datetime(2026, 10, 7, 4, 30, tzinfo=UTC).isoformat()
    assert connections["seconds"] == 5.0


def test_resource_snapshot_reports_disk_and_injected_rss(tmp_path: Path) -> None:
    monitor = ResourceMonitor(tmp_path, rss=lambda: 123_456_789)
    snapshot = monitor.snapshot()
    assert snapshot["rss_bytes"] == 123_456_789
    disk = snapshot["disk"]
    assert disk["path"] == str(tmp_path)
    assert disk["total_bytes"] > 0
    assert 0.0 <= disk["used_percent"] <= 100.0
    assert disk["used_bytes"] + disk["free_bytes"] == disk["total_bytes"]


def test_an_unavailable_rss_reader_reports_none(tmp_path: Path) -> None:
    def broken() -> int:
        raise OSError("no such API")

    assert ResourceMonitor(tmp_path, rss=broken).snapshot()["rss_bytes"] is None


def test_default_rss_reader_is_positive_on_supported_platforms() -> None:
    assert default_rss_bytes() > 0


def test_snapshot_serialises_to_json() -> None:
    metrics = Metrics()
    metrics.incr("signals")
    metrics.observe_latency("notify", 0.1)
    line = format_snapshot(metrics.snapshot())
    assert json.loads(line)["counters"] == {"signals": 1}
