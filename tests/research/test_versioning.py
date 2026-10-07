"""Turning an accepted improvement into a version — and stopping it at the door.

The interesting test here is the refusal: this module is the one place where a good-looking
backtest could reach production without passing a single gate. Everything else is arithmetic.
"""

import pytest

from tradingagent.research.versioning import (
    CandidateVersion,
    Version,
    build_candidate,
    next_ref,
    write_candidate,
)

INCUMBENT = {
    "strategy_id": "witness",
    "max_mode": "SIGNAL",
    "allowed_symbols": ["XAUUSD"],
    "timeframes": ["M15"],
    "history_bars": 300,
    "expiry_bars": 1,
    "ai_filter": "shadow",
}


# --- the version number ------------------------------------------------------------------


def test_a_patch_bump_is_the_same_idea_with_other_parameters() -> None:
    """A parameter improvement is a patch: a minor bump would mean a different idea."""
    assert next_ref("witness@1.1.0") == "witness@1.1.1"


def test_the_bump_does_not_touch_the_major_or_the_minor() -> None:
    assert next_ref("trend_breakout@1.0.0") == "trend_breakout@1.0.1"
    assert next_ref("witness@2.7.9") == "witness@2.7.10"


def test_the_strategy_id_survives_the_bump() -> None:
    assert next_ref("witness@1.1.0").partition("@")[0] == "witness"


def test_a_reference_without_a_version_is_refused() -> None:
    """A ref the protocol cannot parse would be stored and never promotable."""
    with pytest.raises(ValueError):
        next_ref("witness")


def test_a_reference_without_an_id_is_refused() -> None:
    with pytest.raises(ValueError):
        next_ref("@1.1.0")


def test_a_version_that_is_not_three_numbers_is_refused() -> None:
    for bad in ("1.1", "1.1.0.2", "v1.1.0", "1.a.0"):
        with pytest.raises(ValueError):
            Version.parse(bad)


def test_the_version_round_trips() -> None:
    assert str(Version.parse("1.10.3")) == "1.10.3"
    assert str(Version.parse("1.10.3").patched()) == "1.10.4"


# --- the candidate -------------------------------------------------------------------------


def test_a_candidate_copies_everything_but_the_version_and_the_parameters() -> None:
    """Otherwise the comparison would validate one strategy and approve another."""
    candidate = build_candidate(
        market="XAUUSD",
        supersedes="witness@1.1.0",
        parameters={"ema_fast": 20, "atr_period": 18},
        incumbent_manifest=INCUMBENT,
    )

    assert candidate.ref == "witness@1.1.1"
    assert candidate.supersedes == "witness@1.1.0"
    assert candidate.manifest["max_mode"] == "SIGNAL"
    assert candidate.manifest["timeframes"] == ["M15"]
    assert candidate.manifest["history_bars"] == 300
    assert candidate.parameters == {"ema_fast": 20, "atr_period": 18}


def test_the_candidate_is_restricted_to_its_market() -> None:
    candidate = build_candidate(
        market="XAUUSD",
        supersedes="witness@1.1.0",
        parameters={},
        incumbent_manifest=INCUMBENT,
    )
    assert candidate.manifest["allowed_symbols"] == ["XAUUSD"]


def test_building_a_version_for_a_market_the_incumbent_never_traded_is_refused() -> None:
    with pytest.raises(ValueError) as caught:
        build_candidate(
            market="BTCUSD",
            supersedes="witness@1.1.0",
            parameters={},
            incumbent_manifest=INCUMBENT,
        )
    assert "never traded" in str(caught.value)


def test_the_manifest_is_written_as_a_readable_yaml_document() -> None:
    candidate = build_candidate(
        market="XAUUSD",
        supersedes="witness@1.1.0",
        parameters={"atr_period": 18, "ema_fast": 20},
        incumbent_manifest=INCUMBENT,
    )
    text = candidate.to_yaml()

    assert "strategy_id: witness" in text
    assert "version: 1.1.1" in text
    assert "allowed_symbols: [XAUUSD]" in text
    assert "  atr_period: 18" in text
    assert text.endswith("\n")
    # The warning is the point: a human will read this file before the protocol does.
    assert "NE PAS copier dans config/strategies/" in text


# --- the door --------------------------------------------------------------------------------


def test_a_candidate_is_written_under_the_research_directory(tmp_path) -> None:
    candidate = build_candidate(
        market="XAUUSD",
        supersedes="witness@1.1.0",
        parameters={"atr_period": 18},
        incumbent_manifest=INCUMBENT,
    )

    path = write_candidate(candidate, tmp_path / "candidates")

    assert path.is_file()
    assert path.name == "witness-1.1.1.yaml"
    assert "atr_period: 18" in path.read_text(encoding="utf-8")


def test_writing_a_candidate_into_the_production_catalog_is_refused() -> None:
    """The one place a good backtest could reach the agent without passing a gate."""
    from pathlib import Path

    candidate = build_candidate(
        market="XAUUSD",
        supersedes="witness@1.1.0",
        parameters={},
        incumbent_manifest=INCUMBENT,
    )

    with pytest.raises(ValueError) as caught:
        write_candidate(candidate, Path("config") / "strategies")

    assert "promotion gates" in str(caught.value)


def test_the_default_directory_is_the_research_one() -> None:
    from tradingagent.research.versioning import CANDIDATES_DIR

    assert "config" not in str(CANDIDATES_DIR)
    assert "candidates" in str(CANDIDATES_DIR)


def test_two_accepted_improvements_in_a_row_produce_two_versions() -> None:
    """A candidate that passes the gates becomes the incumbent of the next comparison."""
    first = next_ref("witness@1.1.0")
    second = next_ref(first)
    assert (first, second) == ("witness@1.1.1", "witness@1.1.2")


def test_an_empty_manifest_still_produces_a_valid_document() -> None:
    candidate = CandidateVersion(
        ref="x@1.0.1",
        supersedes="x@1.0.0",
        strategy_id="x",
        parameters={},
        manifest={
            "max_mode": "SIGNAL",
            "timeframes": ["M15"],
            "history_bars": 300,
            "expiry_bars": 1,
        },
        market="XAUUSD",
    )
    assert "parameters:" in candidate.to_yaml()
