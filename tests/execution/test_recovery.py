"""TASK-084/085: the recovery suite really runs and really passes on a simulated terminal."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from tradingagent.execution.recovery import RecoveryReport, run_recovery_suite
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.migrate import upgrade


@pytest.fixture
def engine_factory(tmp_path: Path) -> Iterator[object]:
    counter = {"value": 0}

    def build() -> Engine:
        counter["value"] += 1
        url = f"sqlite:///{tmp_path / f'scenario-{counter["value"]}.db'}"
        upgrade(url)
        return create_database_engine(url)

    yield build


def test_every_recovery_scenario_passes(engine_factory: object) -> None:
    report = run_recovery_suite(engine_factory)  # type: ignore[arg-type]

    assert isinstance(report, RecoveryReport)
    assert report.failures == ()
    assert report.passed is True


def test_the_report_names_every_scenario_and_its_steps(engine_factory: object) -> None:
    report = run_recovery_suite(engine_factory)  # type: ignore[arg-type]

    names = [result.name for result in report.results]
    assert "arrêt brutal pendant l'envoi" in names
    assert "réponse perdue" in " ".join(names)
    assert "déconnexion du courtier" in names
    assert "base indisponible" in names
    assert "franchissement de la limite quotidienne" in names
    assert "périmètre de l'arrêt d'urgence" in names
    text = report.text()
    assert "[PASS]" in text
    assert "order_send" not in text  # no secret, no raw broker payload in the report


def test_a_lost_answer_never_produces_a_second_order(engine_factory: object) -> None:
    report = run_recovery_suite(engine_factory)  # type: ignore[arg-type]
    scenario = next(r for r in report.results if "réponse perdue" in r.name)
    labels = {step.label for step in scenario.steps}
    assert "no second order was sent" in labels
    assert all(step.passed for step in scenario.steps)


def test_the_daily_limit_blocks_new_orders(engine_factory: object) -> None:
    report = run_recovery_suite(engine_factory)  # type: ignore[arg-type]
    scenario = next(r for r in report.results if "limite quotidienne" in r.name)
    steps = {step.label: step.passed for step in scenario.steps}
    assert steps["the risk engine refuses a new order"] is True
    assert steps["the hard limit halted the agent"] is True
    assert steps["the executor refuses the order too"] is True
    assert steps["nothing reached the broker"] is True


def test_the_emergency_stop_never_closes_positions_by_default(engine_factory: object) -> None:
    report = run_recovery_suite(engine_factory)  # type: ignore[arg-type]
    scenario = next(r for r in report.results if "arrêt d'urgence" in r.name)
    steps = {step.label: step.passed for step in scenario.steps}
    assert steps["the halt carried no close option"] is True
    assert steps["no position was closed"] is True
    assert steps["the close option is honoured only when enabled"] is True
