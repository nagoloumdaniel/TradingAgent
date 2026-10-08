"""The file protocol an EA and the agent share (phase 7, F-016, F-017, RM-013).

Nothing here touches MetaTrader: the bridge is the whole contract, and the contract is
what these tests pin down — atomic writes, UTC stamps, and a reader that never raises
because an EA died mid-write.
"""

import itertools
import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tradingagent.core.market import Direction
from tradingagent.ea import bridge
from tradingagent.ea.bridge import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    PROTOCOL_VERSION,
    STATE_REPLACE_ATTEMPTS,
    STATE_REPLACE_RETRY_SECONDS,
    AuthorisedOrder,
    EaStatus,
    ExpectedPosition,
    OrderAction,
    events_path,
    format_utc,
    newest_events,
    parse_utc,
    publish_state,
    read_events,
    read_json,
    read_reports,
    report_path,
    state_path,
    status_of,
)

NOW = datetime(2026, 10, 7, 6, 0, 0, tzinfo=UTC)


def frozen(moment: datetime) -> Callable[[], datetime]:
    return lambda: moment


def expected_position(ticket: int = 5001, stop: float | None = 62_000.0) -> ExpectedPosition:
    return ExpectedPosition(
        ticket=ticket,
        direction=Direction.BUY,
        volume=0.01,
        stop_loss=stop,
        take_profit=65_000.0,
        comment="ta-0123456789abcdef",
    )


def authorised_order() -> AuthorisedOrder:
    return AuthorisedOrder(
        order_id="open-1",
        action=OrderAction.OPEN,
        direction=Direction.BUY,
        volume=0.01,
        stop_loss=62_000.0,
        take_profit=65_000.0,
        comment="ta-0123456789abcdef",
    )


def write_report(reports_dir: Path, symbol: str, payload: dict) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = report_path(reports_dir, symbol)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def report_payload(symbol: str = "BTCUSD", heartbeat: datetime = NOW) -> dict:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "ea_version": "1.0.0",
        "symbol": symbol,
        "magic": 3031,
        "updated_at": format_utc(heartbeat),
        "applied_revision": 7,
        "connected": True,
        "trade_allowed": True,
        "kill_switch": False,
        "local_halt": False,
        "halt_reason": "",
        "positions": [
            {
                "ticket": 5001,
                "symbol": symbol,
                "direction": "BUY",
                "volume": 0.01,
                "price_open": 63_000.0,
                "stop_loss": 62_000.0,
                "take_profit": 65_000.0,
                "profit": 1.5,
                "comment": "ta-0123456789abcdef",
            }
        ],
        "counters": {"orders_sent": 3},
        "events": [
            {
                "seq": 12,
                "at": format_utc(NOW - timedelta(seconds=1)),
                "kind": "EXECUTION",
                "severity": "INFO",
                "ticket": 5001,
                "message": "order 5001 filled",
                "data": {"requested_price": 63_000.0, "executed_price": 63_002.5, "slippage": 2.5},
            }
        ],
    }


# -- publishing ------------------------------------------------------------------------


def test_publish_round_trips_through_read_reports(tmp_path: Path) -> None:
    directory = tmp_path / "bridge"
    publish_state(
        directory,
        "BTCUSD",
        magic=3031,
        positions=[expected_position()],
        orders=[authorised_order()],
        max_total_volume=0.05,
        now=frozen(NOW),
    )

    state = read_json(state_path(directory, "BTCUSD"))
    assert state is not None
    assert state["protocol_version"] == PROTOCOL_VERSION
    assert state["symbol"] == "BTCUSD"
    assert state["revision"] == 1
    assert state["published_at"] == "2026-10-07T06:00:00+00:00"
    assert state["positions"][0]["stop_loss"] == 62_000.0
    assert state["orders"][0]["id"] == "open-1"
    assert state["orders"][0]["action"] == "OPEN"
    assert state["limits"] == {"max_positions": 1, "max_total_volume": 0.05}
    assert state["kill_switch"] is False


def test_publish_state_writes_under_the_state_directory(tmp_path: Path) -> None:
    written = publish_state(tmp_path, "XAUUSD", magic=3031, now=frozen(NOW))
    assert written == tmp_path / "state" / "XAUUSD_state.json"
    assert written.is_file()
    assert state_path(tmp_path, "XAUUSD") == written


def test_revision_auto_increments_without_the_caller_tracking_it(tmp_path: Path) -> None:
    for expected in (1, 2, 3):
        publish_state(tmp_path, "BTCUSD", magic=3031, now=frozen(NOW))
        state = read_json(state_path(tmp_path, "BTCUSD"))
        assert state is not None
        assert state["revision"] == expected


def test_revision_can_be_forced_and_overrides_the_auto_counter(tmp_path: Path) -> None:
    publish_state(tmp_path, "BTCUSD", magic=3031, now=frozen(NOW))
    publish_state(tmp_path, "BTCUSD", magic=3031, revision=99, now=frozen(NOW))
    state = read_json(state_path(tmp_path, "BTCUSD"))
    assert state is not None
    assert state["revision"] == 99


def test_a_corrupt_previous_state_restarts_the_revision_at_one(tmp_path: Path) -> None:
    path = state_path(tmp_path, "BTCUSD")
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    publish_state(tmp_path, "BTCUSD", magic=3031, now=frozen(NOW))
    state = read_json(path)
    assert state is not None
    assert state["revision"] == 1


def test_kill_switch_is_published_and_read_back(tmp_path: Path) -> None:
    publish_state(tmp_path, "BTCUSD", magic=3031, kill_switch=True, now=frozen(NOW))
    state = read_json(state_path(tmp_path, "BTCUSD"))
    assert state is not None
    assert state["kill_switch"] is True


def test_publish_refuses_a_naive_clock(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        publish_state(tmp_path, "BTCUSD", magic=3031, now=frozen(NOW.replace(tzinfo=None)))


def test_the_published_epoch_is_the_same_instant_as_the_iso_stamp(tmp_path: Path) -> None:
    """MQL5 reads the epoch because it has no date parser; both must agree."""
    publish_state(tmp_path, "BTCUSD", magic=3031, now=frozen(NOW))
    state = read_json(state_path(tmp_path, "BTCUSD"))
    assert state is not None
    assert state["published_epoch"] == int(NOW.timestamp())


def test_the_state_file_is_pure_ascii_so_mql5_can_read_it(tmp_path: Path) -> None:
    """MQL5 has no UTF-8 text mode: anything outside ASCII must travel as an escape."""
    position = ExpectedPosition(
        ticket=5001,
        direction=Direction.BUY,
        volume=0.01,
        stop_loss=62_000.0,
        comment="déclenchement à 62 000 €",
    )
    path = publish_state(tmp_path, "BTCUSD", magic=3031, positions=[position], now=frozen(NOW))
    raw = path.read_bytes()
    assert all(byte < 128 for byte in raw)
    assert b"\\u00e9" in raw


def test_an_absent_state_age_is_not_a_negative_age(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    payload = report_payload()
    payload["state_age_seconds"] = -1
    write_report(reports, "BTCUSD", payload)

    read = read_reports(reports, now=frozen(NOW))
    assert read[0].state_age_seconds is None


# -- atomicity -------------------------------------------------------------------------


def test_an_atomic_write_leaves_neither_temporary_nor_partial_file(tmp_path: Path) -> None:
    publish_state(tmp_path, "BTCUSD", magic=3031, positions=[expected_position()], now=frozen(NOW))
    state_dir = tmp_path / "state"
    assert [entry.name for entry in state_dir.iterdir()] == ["BTCUSD_state.json"]


def test_a_failed_replace_keeps_the_previous_document_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publish_state(tmp_path, "BTCUSD", magic=3031, revision=1, now=frozen(NOW))
    before = state_path(tmp_path, "BTCUSD").read_bytes()

    def boom(*_: object, **__: object) -> None:
        raise OSError("the disk said no")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="the disk said no"):
        publish_state(tmp_path, "BTCUSD", magic=3031, revision=2, now=frozen(NOW))

    assert state_path(tmp_path, "BTCUSD").read_bytes() == before
    assert [entry.name for entry in (tmp_path / "state").iterdir()] == ["BTCUSD_state.json"]


# -- Windows rename refusals -----------------------------------------------------------
#
# On Windows `os.replace` fails with `PermissionError` (WinError 5, "Accès refusé") while
# another process holds the destination open, and the EA reads the state file several
# times per second: a refusal is a matter of timing, not a broken disk. No real second
# process is started here — holding a file open from another process cannot be made
# reliable in a unit test — so every refusal below is *simulated* by a fake `os.replace`.


def refused(error: str = "the reader holds the state file") -> Callable[..., None]:
    """An `os.replace` the OS always refuses, as Windows does when the EA is reading."""

    def replace(*_: object, **__: object) -> None:
        raise PermissionError(5, error)

    return replace


def refusing(times: int, real: Callable[..., None]) -> Callable[..., None]:
    """An `os.replace` refused the first `times` calls, then the real one."""
    calls = itertools.count()

    def replace(source: object, destination: object) -> None:
        if next(calls) < times:
            raise PermissionError(5, "the reader holds the state file")
        real(source, destination)

    return replace


def test_a_rename_refused_by_a_reader_is_retried_until_it_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publish_state(tmp_path, "XAUUSD", magic=3031, revision=1, now=frozen(NOW))
    monkeypatch.setattr(os, "replace", refusing(2, os.replace))
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    path = publish_state(tmp_path, "XAUUSD", magic=3031, revision=2, now=frozen(NOW))

    assert json.loads(path.read_text(encoding="utf-8"))["revision"] == 2
    assert [entry.name for entry in path.parent.iterdir()] == ["XAUUSD_state.json"]


def test_the_retry_budget_is_bounded_and_short(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[int] = []
    delays: list[float] = []

    def counted(*_: object, **__: object) -> None:
        attempts.append(1)
        raise PermissionError(5, "the reader holds the state file")

    monkeypatch.setattr(os, "replace", counted)
    monkeypatch.setattr(time, "sleep", delays.append)

    with pytest.raises(PermissionError):
        publish_state(tmp_path, "XAUUSD", magic=3031, now=frozen(NOW))

    # The budget is spent and bounded: at least three tries, one pause between two of
    # them, and a total wait far below both the publish cadence and the EA's timeout.
    assert STATE_REPLACE_ATTEMPTS >= 3
    assert len(attempts) == STATE_REPLACE_ATTEMPTS
    assert len(delays) == STATE_REPLACE_ATTEMPTS - 1
    assert all(delay == STATE_REPLACE_RETRY_SECONDS for delay in delays)
    assert (STATE_REPLACE_ATTEMPTS - 1) * STATE_REPLACE_RETRY_SECONDS < 1.0


def test_a_persistent_rename_refusal_is_propagated_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publish_state(tmp_path, "XAUUSD", magic=3031, revision=1, now=frozen(NOW))
    before = state_path(tmp_path, "XAUUSD").read_bytes()
    monkeypatch.setattr(os, "replace", refused())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with pytest.raises(PermissionError, match="the reader holds the state file"):
        publish_state(tmp_path, "XAUUSD", magic=3031, revision=2, now=frozen(NOW))

    # A refusal that outlives the budget is a real failure: the EA keeps the document it
    # has, the previous revision is intact, and no debris is left in the state directory.
    assert state_path(tmp_path, "XAUUSD").read_bytes() == before
    assert [entry.name for entry in (tmp_path / "state").iterdir()] == ["XAUUSD_state.json"]


def test_a_non_retryable_replace_error_is_raised_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[int] = []

    def disk_full(*_: object, **__: object) -> None:
        attempts.append(1)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", disk_full)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with pytest.raises(OSError, match="No space left on device"):
        publish_state(tmp_path, "XAUUSD", magic=3031, now=frozen(NOW))

    # Only a lock is worth waiting for; a full disk would just be five times slower.
    assert len(attempts) == 1


def test_the_reader_never_sees_a_partial_document_while_the_rename_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publish_state(
        tmp_path, "XAUUSD", magic=3031, revision=1, positions=[expected_position()], now=frozen(NOW)
    )
    target = state_path(tmp_path, "XAUUSD")
    whole_previous = target.read_bytes()
    seen: list[bytes] = []
    real = os.replace

    def reading_ea(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        # What the EA would read at this very instant, mid-publish.
        seen.append(target.read_bytes())
        if len(seen) < 3:
            raise PermissionError(5, "the reader holds the state file")
        real(source, destination)

    monkeypatch.setattr(os, "replace", reading_ea)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    publish_state(
        tmp_path, "XAUUSD", magic=3031, revision=2, positions=[expected_position()], now=frozen(NOW)
    )

    assert len(seen) == 3  # the reader really did look while the write was still refused
    assert all(observed == whole_previous for observed in seen)
    final = json.loads(target.read_text(encoding="utf-8"))
    assert final["revision"] == 2
    assert final["positions"] and final["orders"] == []


def test_only_our_own_temporary_is_ever_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    removed: list[str] = []
    monkeypatch.setattr(os, "unlink", removed.append)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    target = state_path(tmp_path, "XAUUSD")

    publish_state(tmp_path, "XAUUSD", magic=3031, revision=1, now=frozen(NOW))
    assert removed == []  # a publish that works removes nothing at all

    monkeypatch.setattr(os, "replace", refusing(2, os.replace))
    publish_state(tmp_path, "XAUUSD", magic=3031, revision=2, now=frozen(NOW))
    assert removed == []  # retried and then renamed: the temporary is the new state

    monkeypatch.setattr(os, "replace", refused())
    with pytest.raises(PermissionError):
        publish_state(tmp_path, "XAUUSD", magic=3031, revision=3, now=frozen(NOW))

    assert len(removed) == 1
    temporary = Path(removed[0])
    # Only the uniquely named temporary this very call created is unlinked — never the
    # state file the EA may be reading, and never anything else in the directory.
    assert temporary.parent == target.parent
    assert temporary != target
    assert temporary.name.startswith(f".{target.name}.") and temporary.name.endswith(".tmp")


def test_the_temporary_is_left_alone_when_it_is_no_longer_ours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    removed: list[str] = []
    identities = itertools.count()
    monkeypatch.setattr(os, "unlink", removed.append)
    monkeypatch.setattr(os, "replace", refused())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    # Every stat answers a different file, as if another process had taken the temporary.
    monkeypatch.setattr(bridge, "_file_identity", lambda _path: (next(identities), 0))

    with pytest.raises(PermissionError, match="the reader holds the state file"):
        publish_state(tmp_path, "XAUUSD", magic=3031, now=frozen(NOW))

    assert removed == []
    # The temporary is not ours any more, so it is left where it is: one stray `.tmp` is
    # a mess to sweep later, deleting another writer's file is a bug.
    leftovers = [entry.name for entry in (tmp_path / "state").iterdir()]
    assert len(leftovers) == 1 and leftovers[0].endswith(".tmp")


def test_a_refused_cleanup_does_not_mask_the_real_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unlink_refused(*_: object, **__: object) -> None:
        raise PermissionError(5, "a scanner holds the temporary")

    monkeypatch.setattr(os, "replace", refused("the rename was refused"))
    monkeypatch.setattr(os, "unlink", unlink_refused)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    # The failure the caller must hear about is the rename, not the cleanup.
    with pytest.raises(PermissionError, match="the rename was refused"):
        publish_state(tmp_path, "XAUUSD", magic=3031, now=frozen(NOW))


# -- reading ---------------------------------------------------------------------------


def test_reading_a_directory_that_does_not_exist_is_empty_not_an_error(tmp_path: Path) -> None:
    assert read_reports(tmp_path / "nowhere") == ()
    assert read_events(tmp_path / "nowhere", "BTCUSD") == ()


def test_a_corrupt_report_is_skipped_without_raising(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "BTCUSD_report.json").write_text('{"symbol": "BTC', encoding="utf-8")
    write_report(reports, "XAUUSD", report_payload("XAUUSD"))

    assert [report.symbol for report in read_reports(reports)] == ["XAUUSD"]


def test_a_report_without_a_heartbeat_is_skipped(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    payload = report_payload()
    del payload["updated_at"]
    write_report(reports, "BTCUSD", payload)

    assert read_reports(reports) == ()


def test_reports_are_read_in_symbol_order_with_their_events(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    write_report(reports, "XAUUSD", report_payload("XAUUSD"))
    write_report(reports, "BTCUSD", report_payload("BTCUSD"))

    read = read_reports(reports, now=frozen(NOW + timedelta(seconds=1)))
    assert [report.symbol for report in read] == ["BTCUSD", "XAUUSD"]
    assert read[0].status is EaStatus.ONLINE
    assert read[0].magic == 3031
    assert read[0].applied_revision == 7
    assert read[0].connected is True
    assert read[0].positions[0].direction is Direction.BUY
    assert read[0].positions[0].stop_loss == 62_000.0
    assert read[0].events[0].kind == "EXECUTION"
    assert read[0].events[0].data["slippage"] == 2.5
    assert read[0].counters == {"orders_sent": 3}


def test_a_naive_heartbeat_is_read_as_utc(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    payload = report_payload()
    payload["updated_at"] = "2026-10-07T06:00:00"
    write_report(reports, "BTCUSD", payload)

    read = read_reports(reports, now=frozen(NOW + timedelta(seconds=1)))
    assert read[0].heartbeat_at == NOW
    assert read[0].status is EaStatus.ONLINE


def test_a_broken_position_entry_does_not_lose_the_rest_of_the_report(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    payload = report_payload()
    payload["positions"].append({"ticket": "not a number"})
    payload["events"].append({"kind": "NO_TIMESTAMP"})
    write_report(reports, "BTCUSD", payload)

    read = read_reports(reports, now=frozen(NOW))
    assert [position.ticket for position in read[0].positions] == [5001]
    assert len(read[0].events) == 1


def test_the_event_journal_skips_unreadable_lines(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir(parents=True)
    path = events_path(reports, "BTCUSD")
    good = json.dumps(
        {
            "seq": 1,
            "at": format_utc(NOW),
            "kind": "KILL_SWITCH",
            "severity": "CRITICAL",
            "message": "kill switch latched",
        }
    )
    path.write_text(f"{good}\n\nnot json at all\n", encoding="utf-8")

    events = read_events(reports, "BTCUSD")
    assert [event.kind for event in events] == ["KILL_SWITCH"]
    assert events[0].severity == "CRITICAL"


def test_newest_events_are_returned_most_recent_first(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    payload = report_payload()
    payload["events"] = [
        {
            "seq": seq,
            "at": format_utc(NOW - timedelta(seconds=10 - seq)),
            "kind": "EXECUTION",
            "severity": "INFO",
            "message": f"event {seq}",
        }
        for seq in range(1, 6)
    ]
    write_report(reports, "BTCUSD", payload)

    read = read_reports(reports, now=frozen(NOW))
    assert [event.seq for event in newest_events(read, limit=2)] == [5, 4]


# -- status ----------------------------------------------------------------------------


def test_a_fresh_heartbeat_is_online_and_a_stale_one_is_offline() -> None:
    assert status_of(NOW, now=frozen(NOW + timedelta(seconds=1))) is EaStatus.ONLINE
    assert (
        status_of(NOW, now=frozen(NOW + timedelta(seconds=DEFAULT_HEARTBEAT_TIMEOUT_SECONDS)))
        is EaStatus.OFFLINE
    )


def test_the_heartbeat_timeout_can_be_shortened() -> None:
    late = NOW + timedelta(seconds=5)
    assert status_of(NOW, now=frozen(late), timeout_seconds=10) is EaStatus.ONLINE
    assert status_of(NOW, now=frozen(late), timeout_seconds=2) is EaStatus.OFFLINE


def test_parse_utc_accepts_both_utc_spellings() -> None:
    assert parse_utc("2026-10-07T06:00:00Z") == NOW
    assert parse_utc("2026-10-07T06:00:00+00:00") == NOW


def test_parse_utc_refuses_a_non_timestamp() -> None:
    with pytest.raises(ValueError):
        parse_utc(None)
    with pytest.raises(ValueError):
        parse_utc("last tuesday")
