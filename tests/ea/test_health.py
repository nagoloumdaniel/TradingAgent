"""`ea_health` is what the dashboard reads: one status per Guardian (F-024, phase 7).

The four cases that matter operationally: a live EA, a silent EA, a report the terminal
died while writing, and no EA installed at all. None of them may raise.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tradingagent.ea.bridge import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    REPORT_SUFFIX,
    EaStatus,
    format_utc,
    report_path,
)
from tradingagent.ea.health import ea_health

NOW = datetime(2026, 10, 7, 6, 0, 0, tzinfo=UTC)


def frozen(moment: datetime) -> Callable[[], datetime]:
    return lambda: moment


def write_report(reports_dir: Path, symbol: str, payload: object) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = report_path(reports_dir, symbol)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")
    return path


def heartbeat_report(symbol: str, heartbeat: datetime) -> dict:
    return {
        "protocol_version": 1,
        "symbol": symbol,
        "magic": 3031,
        "updated_at": format_utc(heartbeat),
    }


def test_a_missing_directory_reports_no_ea_at_all(tmp_path: Path) -> None:
    assert ea_health(tmp_path / "absent", now=frozen(NOW)) == {}


def test_an_empty_directory_reports_no_ea_at_all(tmp_path: Path) -> None:
    assert ea_health(tmp_path, now=frozen(NOW)) == {}


def test_a_fresh_heartbeat_is_online(tmp_path: Path) -> None:
    write_report(tmp_path, "BTCUSD", heartbeat_report("BTCUSD", NOW))
    assert ea_health(tmp_path, now=frozen(NOW + timedelta(seconds=2))) == {
        "BTCUSD": EaStatus.ONLINE
    }


def test_a_stale_heartbeat_is_offline(tmp_path: Path) -> None:
    write_report(tmp_path, "XAUUSD", heartbeat_report("XAUUSD", NOW))
    late = NOW + timedelta(seconds=DEFAULT_HEARTBEAT_TIMEOUT_SECONDS)
    assert ea_health(tmp_path, now=frozen(late)) == {"XAUUSD": EaStatus.OFFLINE}


def test_the_timeout_is_configurable(tmp_path: Path) -> None:
    write_report(tmp_path, "BTCUSD", heartbeat_report("BTCUSD", NOW))
    late = NOW + timedelta(seconds=30)
    assert ea_health(tmp_path, now=frozen(late), timeout_seconds=60) == {"BTCUSD": EaStatus.ONLINE}
    assert ea_health(tmp_path, now=frozen(late), timeout_seconds=5) == {"BTCUSD": EaStatus.OFFLINE}


def test_a_corrupt_report_is_offline_without_raising(tmp_path: Path) -> None:
    write_report(tmp_path, "BTCUSD", '{"symbol": "BTCUSD", "updated_at": "2026-10')
    assert ea_health(tmp_path, now=frozen(NOW)) == {"BTCUSD": EaStatus.OFFLINE}


def test_a_report_missing_its_heartbeat_is_offline(tmp_path: Path) -> None:
    write_report(tmp_path, "BTCUSD", {"symbol": "BTCUSD", "positions": []})
    assert ea_health(tmp_path, now=frozen(NOW)) == {"BTCUSD": EaStatus.OFFLINE}


def test_a_report_that_is_not_an_object_is_offline(tmp_path: Path) -> None:
    write_report(tmp_path, "BTCUSD", ["not", "an", "object"])
    assert ea_health(tmp_path, now=frozen(NOW)) == {"BTCUSD": EaStatus.OFFLINE}


def test_the_symbol_comes_from_the_report_and_falls_back_to_the_file_name(
    tmp_path: Path,
) -> None:
    write_report(tmp_path, "BTCUSD", heartbeat_report("BTCUSD.micro", NOW))
    write_report(tmp_path, "XAUUSD", "{{{")
    assert ea_health(tmp_path, now=frozen(NOW)) == {
        "BTCUSD.micro": EaStatus.ONLINE,
        "XAUUSD": EaStatus.OFFLINE,
    }


def test_two_eas_are_reported_independently(tmp_path: Path) -> None:
    write_report(tmp_path, "BTCUSD", heartbeat_report("BTCUSD", NOW))
    write_report(tmp_path, "XAUUSD", heartbeat_report("XAUUSD", NOW - timedelta(minutes=5)))
    assert ea_health(tmp_path, now=frozen(NOW)) == {
        "BTCUSD": EaStatus.ONLINE,
        "XAUUSD": EaStatus.OFFLINE,
    }


def test_only_report_files_are_considered(tmp_path: Path) -> None:
    write_report(tmp_path, "BTCUSD", heartbeat_report("BTCUSD", NOW))
    (tmp_path / "BTCUSD_events.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "BTCUSD_state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    assert list(ea_health(tmp_path, now=frozen(NOW))) == ["BTCUSD"]


def test_the_suffix_is_the_published_contract() -> None:
    assert REPORT_SUFFIX == "_report.json"
