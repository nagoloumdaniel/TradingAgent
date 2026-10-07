"""`python -m tradingagent.registry.cli`: status, validate and promote against the database."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tradingagent.config.errors import ConfigError
from tradingagent.core.states import StrategyStatus, ValidationStage
from tradingagent.registry.cli import main
from tradingagent.registry.store import StrategyRegistry
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade

GOLD = "XAUUSD"
REF = "witness@1.1.0"
PARAMETERS = {"ema_fast": 20, "ema_slow": 50}
NOW = datetime(2026, 10, 7, 6, 30, tzinfo=UTC)
CANDIDATE_PATH = (
    StrategyStatus.EXPERIMENTAL,
    StrategyStatus.BACKTESTING,
    StrategyStatus.VALIDATING,
    StrategyStatus.PAPER,
    StrategyStatus.CANDIDATE,
)


@pytest.fixture
def url(tmp_path: Path) -> str:
    database = f"sqlite:///{tmp_path / 'cli.db'}"
    upgrade(database)
    return database


def factory(url: str) -> Callable[[], Engine]:
    return lambda: create_database_engine(url)


def run(url: str, *argv: str) -> int:
    return main(list(argv), engine_factory=factory(url), clock=lambda: NOW)


def store_of(url: str) -> StrategyRegistry:
    return StrategyRegistry(create_database_engine(url), clock=lambda: NOW)


def seed_candidate(url: str, *, ref: str = REF) -> None:
    engine = create_database_engine(url)
    try:
        store = StrategyRegistry(engine, clock=lambda: NOW)
        store.register(GOLD, ref, "human", PARAMETERS)
        for target in CANDIDATE_PATH:
            store.transition(GOLD, ref, target, "operator", "advance", NOW)
    finally:
        engine.dispose()


def test_status_lists_the_registry(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    seed_candidate(url)
    assert run(url, "registry-status") == 0
    out = capsys.readouterr().out
    assert REF in out
    assert GOLD in out
    assert "candidate" in out


def test_status_on_an_empty_registry_says_so(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(url, "registry-status") == 0
    assert "no strategy registered" in capsys.readouterr().out


def test_status_can_be_filtered_by_market(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    seed_candidate(url)
    engine = create_database_engine(url)
    try:
        StrategyRegistry(engine, clock=lambda: NOW).register(
            "BTCUSD", "trend_breakout@1.0.0", "human", PARAMETERS
        )
    finally:
        engine.dispose()

    assert run(url, "registry-status", "--market", "BTCUSD") == 0
    out = capsys.readouterr().out
    assert "trend_breakout@1.0.0" in out
    assert REF not in out


def test_validate_records_a_gate(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    seed_candidate(url)
    code = run(
        url,
        "registry-validate",
        "--market",
        GOLD,
        "--ref",
        REF,
        "--stage",
        "risk",
        "--detail",
        '{"max_drawdown": 0.2}',
        "--actor",
        "daniel",
    )
    assert code == 0
    assert "risk" in capsys.readouterr().out
    store = store_of(url)
    validations = store.history(REF, GOLD).validations
    assert [row.stage for row in validations] == [ValidationStage.RISK]
    assert validations[0].passed is True
    assert validations[0].detail == {"max_drawdown": 0.2}


def test_validate_can_record_a_failure(url: str) -> None:
    seed_candidate(url)
    code = run(
        url, "registry-validate", "--market", GOLD, "--ref", REF, "--stage", "stress", "--fail"
    )
    assert code == 0
    validations = store_of(url).history(REF, GOLD).validations
    assert validations[0].passed is False


def test_validate_rejects_an_unknown_stage(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    seed_candidate(url)
    assert run(url, "registry-validate", "--market", GOLD, "--ref", REF, "--stage", "vibes") == 2
    assert "vibes" in capsys.readouterr().err


def test_promote_refuses_while_a_gate_is_missing(
    url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_candidate(url)
    assert run(url, "registry-promote", "--market", GOLD, "--ref", REF, "--reason", "ship it") == 1
    assert "risk" in capsys.readouterr().err
    assert store_of(url).get(GOLD, REF).status is StrategyStatus.CANDIDATE


def test_promote_after_the_nine_gates(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    seed_candidate(url)
    engine = create_database_engine(url)
    try:
        store = StrategyRegistry(engine, clock=lambda: NOW)
        for stage in ValidationStage:
            store.record_validation(REF, GOLD, stage, True, {}, NOW)
    finally:
        engine.dispose()

    code = run(
        url,
        "registry-promote",
        "--market",
        GOLD,
        "--ref",
        REF,
        "--reason",
        "nine gates cleared",
        "--actor",
        "operator",
    )
    assert code == 0
    assert "live" in capsys.readouterr().out.lower()
    row = store_of(url).get(GOLD, REF)
    assert row.status is StrategyStatus.LIVE
    assert row.promotion_reason == "nine gates cleared"
    assert store_of(url).active(GOLD) == REF


def test_promote_reports_an_unknown_ref(url: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        run(url, "registry-promote", "--market", GOLD, "--ref", "ghost@1.0.0", "--reason", "why")
        == 1
    )
    assert "ghost@1.0.0" in capsys.readouterr().err


def test_a_missing_database_url_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    def no_database() -> Engine:
        raise ConfigError("Invalid environment configuration:\n  DATABASE_URL: Field required")

    assert main(["registry-status"], engine_factory=no_database) == 2
    assert "DATABASE_URL" in capsys.readouterr().err
