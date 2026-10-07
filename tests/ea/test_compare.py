"""The agent's own cross-check of an EA report against the last state it published.

The EA already watches itself; this is the second pair of eyes RM-014 asks for, and like
the first one it only ever *describes* a difference.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from tradingagent.core.market import Direction
from tradingagent.ea.bridge import (
    EaReport,
    EaState,
    EaStatus,
    ExpectedPosition,
    ReportedPosition,
    compare_expected,
    publish_state,
    read_reports,
)

NOW = datetime(2026, 10, 7, 6, 0, 0, tzinfo=UTC)


def state(positions: tuple[ExpectedPosition, ...] = (), revision: int = 7) -> EaState:
    return EaState(
        symbol="BTCUSD",
        magic=3031,
        revision=revision,
        published_at=NOW,
        positions=positions,
    )


def report(positions: tuple[ReportedPosition, ...] = (), revision: int = 7) -> EaReport:
    return EaReport(
        symbol="BTCUSD",
        magic=3031,
        protocol_version=1,
        ea_version="1.0.0",
        heartbeat_at=NOW,
        status=EaStatus.ONLINE,
        connected=True,
        trade_allowed=True,
        applied_revision=revision,
        kill_switch=False,
        local_halt=False,
        halt_reason="",
        state_age_seconds=1.0,
        positions=positions,
        events=(),
    )


def expected(
    ticket: int = 5001, volume: float = 0.01, stop: float | None = 62_000.0
) -> ExpectedPosition:
    return ExpectedPosition(
        ticket=ticket,
        direction=Direction.BUY,
        volume=volume,
        stop_loss=stop,
        take_profit=None,
        comment="ta-0123456789abcdef",
    )


def seen(
    ticket: int = 5001,
    volume: float = 0.01,
    stop: float = 62_000.0,
    direction: Direction = Direction.BUY,
) -> ReportedPosition:
    return ReportedPosition(
        ticket=ticket,
        symbol="BTCUSD",
        direction=direction,
        volume=volume,
        price_open=63_000.0,
        stop_loss=stop,
        take_profit=0.0,
        profit=0.0,
        comment="ta-0123456789abcdef",
    )


def kinds(divergences: tuple) -> list[str]:
    return [divergence.kind for divergence in divergences]


def test_agreeing_states_produce_nothing() -> None:
    assert compare_expected(state((expected(),)), report((seen(),))) == ()


def test_an_expected_position_absent_from_the_terminal_is_a_divergence() -> None:
    found = compare_expected(state((expected(),)), report())
    assert kinds(found) == ["missing_in_terminal"]
    assert found[0].ticket == 5001
    assert "absent from the terminal" in found[0].detail


def test_a_terminal_position_the_backend_does_not_expect_is_a_divergence() -> None:
    found = compare_expected(state(), report((seen(),)))
    assert kinds(found) == ["unknown_in_terminal"]
    assert found[0].ticket == 5001


def test_a_volume_that_drifted_is_a_divergence() -> None:
    found = compare_expected(state((expected(),)), report((seen(volume=0.02),)))
    assert kinds(found) == ["volume"]


def test_a_missing_stop_is_a_divergence() -> None:
    found = compare_expected(state((expected(),)), report((seen(stop=0.0),)))
    assert kinds(found) == ["stop"]


def test_a_stop_that_drifted_beyond_the_tolerance_is_a_divergence() -> None:
    assert compare_expected(state((expected(),)), report((seen(stop=62_000.005),))) == ()
    assert kinds(compare_expected(state((expected(),)), report((seen(stop=61_900.0),)))) == ["stop"]


def test_an_expected_absence_of_stop_is_only_satisfied_by_no_stop() -> None:
    assert compare_expected(state((expected(stop=None),)), report((seen(stop=0.0),))) == ()
    assert kinds(compare_expected(state((expected(stop=None),)), report((seen(),)))) == ["stop"]


def test_a_side_that_flipped_is_a_divergence() -> None:
    found = compare_expected(state((expected(),)), report((seen(direction=Direction.SELL),)))
    assert kinds(found) == ["side"]


def test_a_revision_the_ea_never_applied_is_a_divergence() -> None:
    found = compare_expected(state(revision=9), report(revision=7))
    assert kinds(found) == ["revision"]
    assert "9" in found[0].detail


def test_a_symbol_or_magic_mismatch_is_a_divergence() -> None:
    other = EaReport(
        symbol="XAUUSD",
        magic=9999,
        protocol_version=1,
        ea_version="1.0.0",
        heartbeat_at=NOW,
        status=EaStatus.ONLINE,
        connected=True,
        trade_allowed=True,
        applied_revision=7,
        kill_switch=False,
        local_halt=False,
        halt_reason="",
        state_age_seconds=1.0,
        positions=(),
        events=(),
    )
    assert kinds(compare_expected(state(), other)) == ["symbol", "magic"]


def test_the_comparison_is_available_from_a_written_report(tmp_path: Path) -> None:
    """The end-to-end shape: publish, read back, compare — with no terminal anywhere."""
    publish_state(
        tmp_path,
        "BTCUSD",
        magic=3031,
        positions=[expected()],
        now=lambda: NOW,
    )
    reports = tmp_path / "reports"
    reports.mkdir()
    payload = {
        "symbol": "BTCUSD",
        "magic": 3031,
        "updated_at": "2026-10-07T06:00:00Z",
        "applied_revision": 1,
        "positions": [
            {
                "ticket": 5001,
                "symbol": "BTCUSD",
                "direction": "BUY",
                "volume": 0.01,
                "price_open": 63_000.0,
                "stop_loss": 62_000.0,
                "take_profit": 0.0,
                "profit": 0.0,
                "comment": "ta-0123456789abcdef",
            }
        ],
        "events": [],
    }
    (reports / "BTCUSD_report.json").write_text(json.dumps(payload), encoding="utf-8")

    read = read_reports(reports, now=lambda: NOW)
    assert compare_expected(state((expected(),), revision=1), read[0]) == ()
