"""Live-mode hardening and progressive ramp-up (TASK-091, TASK-092).

Every refusal is asserted by its reason, the credentials are only ever compared as
digests, and the ramp decisions are pure: the same evidence always yields the same
action, whatever is stored next to it.
"""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tradingagent.control.live import (
    ACTIVATION_KIND,
    INCIDENT_KIND,
    STAGE_KIND,
    CredentialFingerprint,
    LiveActivationRefused,
    LiveActivationRequest,
    LiveController,
    OperatorConfirmation,
    RampPlan,
    StageAction,
    StageEvidence,
    evaluate_activation,
    evaluate_ramp,
    fingerprint,
    reuses_demo_credentials,
    supervise,
)
from tradingagent.core.halt import GLOBAL
from tradingagent.core.states import Severity
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import SystemEventRow

NOW = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)

DEMO_SECRET = "demo-investor-password"
LIVE_SECRET = "live-principal-password"


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'live.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


def demo() -> CredentialFingerprint:
    return fingerprint(5012345, "Deriv-Demo", DEMO_SECRET)


def live() -> CredentialFingerprint:
    return fingerprint(6098765, "Deriv-Server", LIVE_SECRET)


def confirmation(actor: str = "daniel") -> OperatorConfirmation:
    return OperatorConfirmation(actor=actor, reason="checklist signed", confirmed_at=NOW)


def request(**overrides: object) -> LiveActivationRequest:
    base: dict[str, object] = {
        "server_flag": True,
        "confirmation": confirmation(),
        "demo": demo(),
        "live": live(),
        "requested_risk_per_trade_pct": Decimal("2"),
        "emergency_stop_validated": True,
        "legal_checklist_signed": True,
    }
    base.update(overrides)
    return LiveActivationRequest(**base)  # type: ignore[arg-type]


def evidence(stage_index: int = 1) -> StageEvidence:
    """Results that conform to `stage_index`: duration and trade count exactly at the bar."""
    stage = RampPlan.default().stage(stage_index)
    return StageEvidence(
        elapsed=stage.min_duration,
        trades=stage.min_trades,
        max_drawdown_pct=Decimal("0.5"),
        daily_loss_breaches=0,
        incidents_open=0,
        reconciliation_clean=True,
        supervised_today=True,
    )


# --- credentials: never stored, only compared ----------------------------------


def test_a_fingerprint_never_carries_the_secret() -> None:
    mark = fingerprint(1, "Deriv-Demo", DEMO_SECRET)
    assert DEMO_SECRET not in repr(mark)
    assert DEMO_SECRET not in mark.secret_digest
    assert mark.login == 1
    assert mark.server == "deriv-demo"  # normalised, so a case change is not a new account


def test_the_same_credential_material_is_detected_as_reuse() -> None:
    assert reuses_demo_credentials(demo(), fingerprint(5012345, "Deriv-Demo", DEMO_SECRET))
    assert reuses_demo_credentials(demo(), fingerprint(5012345, "deriv-demo", DEMO_SECRET))
    assert not reuses_demo_credentials(demo(), live())


def test_a_shared_password_on_another_account_is_still_reuse() -> None:
    shared = fingerprint(6098765, "Deriv-Server", DEMO_SECRET)
    assert reuses_demo_credentials(demo(), shared)


# --- TASK-091: the double condition and the reduced cap -------------------------


def test_activation_is_refused_without_the_server_flag() -> None:
    decision = evaluate_activation(request(server_flag=False))
    assert not decision.allowed
    assert any("LIVE_TRADING_ENABLED" in reason for reason in decision.reasons)
    assert decision.risk_per_trade_pct is None


def test_activation_is_refused_without_the_operator_confirmation() -> None:
    decision = evaluate_activation(request(confirmation=None))
    assert not decision.allowed
    assert any("operator" in reason for reason in decision.reasons)


def test_activation_is_refused_with_a_blank_actor() -> None:
    decision = evaluate_activation(request(confirmation=confirmation(actor="   ")))
    assert not decision.allowed
    assert any("actor" in reason for reason in decision.reasons)


def test_activation_is_refused_when_live_reuses_demo_credentials() -> None:
    decision = evaluate_activation(request(live=demo()))
    assert not decision.allowed
    assert any("credentials" in reason for reason in decision.reasons)


def test_the_reduced_cap_cannot_exceed_five_percent() -> None:
    decision = evaluate_activation(request(requested_risk_per_trade_pct=Decimal("5.1")))
    assert not decision.allowed
    assert any("5" in reason and "RM-005" in reason for reason in decision.reasons)


def test_the_cap_must_be_positive() -> None:
    decision = evaluate_activation(request(requested_risk_per_trade_pct=Decimal("0")))
    assert not decision.allowed


def test_activation_is_refused_until_the_emergency_stop_is_validated() -> None:
    decision = evaluate_activation(request(emergency_stop_validated=False))
    assert not decision.allowed
    assert any("emergency stop" in reason for reason in decision.reasons)


def test_activation_is_refused_until_the_legal_checklist_is_signed() -> None:
    decision = evaluate_activation(request(legal_checklist_signed=False))
    assert not decision.allowed
    assert any("TASK-090" in reason for reason in decision.reasons)


def test_every_missing_condition_is_reported_at_once() -> None:
    decision = evaluate_activation(
        request(
            server_flag=False,
            confirmation=None,
            requested_risk_per_trade_pct=Decimal("9"),
            live=demo(),
            emergency_stop_validated=False,
            legal_checklist_signed=False,
        )
    )
    assert len(decision.reasons) == 6


def test_a_complete_request_is_allowed_with_the_reduced_cap() -> None:
    decision = evaluate_activation(request(requested_risk_per_trade_pct=Decimal("1.5")))
    assert decision.allowed
    assert decision.reasons == ()
    assert decision.risk_per_trade_pct == Decimal("1.5")


def test_the_maximum_cap_is_accepted() -> None:
    decision = evaluate_activation(request(requested_risk_per_trade_pct=Decimal("5")))
    assert decision.allowed


# --- persistence of the authorisation -------------------------------------------


def test_a_refusal_happens_before_anything_is_persisted(engine: Engine) -> None:
    controller = LiveController(engine)
    with pytest.raises(LiveActivationRefused):
        controller.activate(request(server_flag=False))
    with Session(engine) as session:
        assert session.query(SystemEventRow).all() == []


def test_activation_persists_the_author_actor_and_cap(engine: Engine) -> None:
    controller = LiveController(engine, now=lambda: NOW)
    activation = controller.activate(request(requested_risk_per_trade_pct=Decimal("2")))

    assert activation.actor == "daniel"
    assert activation.risk_per_trade_pct == Decimal("2")

    event = SystemEventStore(engine).latest(ACTIVATION_KIND)
    assert event is not None
    assert event.severity is Severity.INFO
    assert event.detail["actor"] == "daniel"
    assert event.detail["risk_per_trade_pct"] == "2"
    assert event.detail["server_flag"] is True
    assert event.detail["occurred_at"] == NOW.isoformat()
    assert DEMO_SECRET not in str(event.detail) and LIVE_SECRET not in str(event.detail)


def test_the_activation_record_is_append_only(engine: Engine) -> None:
    LiveController(engine).activate(request())
    LiveController(engine).activate(request(confirmation=confirmation(actor="sam")))
    with Session(engine) as session:
        rows = session.query(SystemEventRow).filter_by(kind=ACTIVATION_KIND).all()
    assert [row.detail["actor"] for row in rows] == ["daniel", "sam"]


# --- TASK-092: the progressive ramp ---------------------------------------------


def test_the_default_plan_starts_below_the_ceiling_and_never_exceeds_it() -> None:
    risks = [stage.risk_per_trade_pct for stage in RampPlan.default().stages]
    assert risks[0] < risks[-1]
    assert all(risk <= Decimal("5") for risk in risks)


def test_a_plan_is_ordered_and_knows_the_previous_stage() -> None:
    plan = RampPlan.default()
    assert [stage.index for stage in plan.stages] == list(range(len(plan.stages)))
    assert plan.previous(0) == 0
    assert plan.previous(2) == 1


def test_an_open_incident_stops_the_ramp() -> None:
    decision = evaluate_ramp(RampPlan.default(), 1, replace(evidence(), incidents_open=1))
    assert decision.action is StageAction.STOP
    assert decision.target_index == 1
    assert any("incident" in reason for reason in decision.reasons)


def test_a_dirty_reconciliation_rolls_back_to_the_previous_stage() -> None:
    decision = evaluate_ramp(RampPlan.default(), 2, replace(evidence(), reconciliation_clean=False))
    assert decision.action is StageAction.ROLLBACK
    assert decision.target_index == 1


def test_a_drawdown_breach_at_the_first_stage_stops_instead_of_rolling_back() -> None:
    decision = evaluate_ramp(
        RampPlan.default(), 0, replace(evidence(), max_drawdown_pct=Decimal("9"))
    )
    assert decision.action is StageAction.STOP
    assert decision.target_index == 0


def test_a_drawdown_breach_rolls_back_one_stage() -> None:
    plan = RampPlan.default()
    stage = plan.stage(2)
    decision = evaluate_ramp(
        plan, 2, replace(evidence(), max_drawdown_pct=stage.max_drawdown_pct + 1)
    )
    assert decision.action is StageAction.ROLLBACK
    assert decision.target_index == 1


def test_a_daily_loss_breach_rolls_back() -> None:
    decision = evaluate_ramp(RampPlan.default(), 1, replace(evidence(), daily_loss_breaches=1))
    assert decision.action is StageAction.ROLLBACK


def test_a_missing_daily_supervision_holds_the_ramp() -> None:
    decision = evaluate_ramp(RampPlan.default(), 1, replace(evidence(), supervised_today=False))
    assert decision.action is StageAction.HOLD
    assert any("surveillance" in reason for reason in decision.reasons)


def test_a_short_duration_holds_the_ramp() -> None:
    stage = RampPlan.default().stage(1)
    decision = evaluate_ramp(
        RampPlan.default(), 1, replace(evidence(), elapsed=stage.min_duration - timedelta(days=1))
    )
    assert decision.action is StageAction.HOLD
    assert any("durée" in reason for reason in decision.reasons)


def test_too_few_trades_holds_the_ramp() -> None:
    stage = RampPlan.default().stage(1)
    decision = evaluate_ramp(
        RampPlan.default(), 1, replace(evidence(), trades=stage.min_trades - 1)
    )
    assert decision.action is StageAction.HOLD
    assert any("opérations" in reason for reason in decision.reasons)


def test_conforming_results_promote_to_the_next_stage() -> None:
    decision = evaluate_ramp(RampPlan.default(), 1, evidence(1))
    assert decision.action is StageAction.PROMOTE
    assert decision.target_index == 2
    assert decision.reasons == ()


def test_the_last_stage_holds_once_its_results_conform() -> None:
    plan = RampPlan.default()
    last = len(plan.stages) - 1
    decision = evaluate_ramp(plan, last, evidence(last))
    assert decision.action is StageAction.HOLD
    assert decision.target_index == last
    assert any("palier maximal" in reason for reason in decision.reasons)


def test_supervision_lists_every_reason_instead_of_a_boolean() -> None:
    report = supervise(
        RampPlan.default().stage(1),
        replace(evidence(), supervised_today=False, incidents_open=2),
    )
    assert not report.supervised
    assert len(report.alerts) == 2


def test_supervision_is_quiet_when_everything_conforms() -> None:
    report = supervise(RampPlan.default().stage(1), evidence(1))
    assert report.supervised
    assert report.alerts == ()


# --- the controller: persistence and reversibility -------------------------------


def test_the_ramp_cannot_start_without_a_recorded_activation(engine: Engine) -> None:
    controller = LiveController(engine)
    with pytest.raises(LiveActivationRefused):
        controller.start_ramp(RampPlan.default())


def test_the_ramp_starts_at_zero_and_persists_every_step(engine: Engine) -> None:
    controller = LiveController(engine, now=lambda: NOW)
    controller.activate(request())
    controller.start_ramp(RampPlan.default())

    assert controller.current_stage_index() == 0
    decision = controller.advance(RampPlan.default(), evidence(0))
    assert decision.action is StageAction.PROMOTE
    assert controller.current_stage_index() == 1

    with Session(engine) as session:
        stages = session.query(SystemEventRow).filter_by(kind=STAGE_KIND).all()
    assert [row.detail["to"] for row in stages] == [0, 1]


def test_a_stop_halts_trading_and_records_the_incident(engine: Engine) -> None:
    halts = HaltStore(engine)
    controller = LiveController(engine, halts=halts, now=lambda: NOW)
    controller.activate(request())
    controller.start_ramp(RampPlan.default())
    controller.record_incident("reconciliation_divergence", {"detail": "position 42 unknown"}, NOW)

    decision = controller.advance(
        RampPlan.default(),
        replace(evidence(), incidents_open=1),
    )

    assert decision.action is StageAction.STOP
    assert halts.is_halted(GLOBAL)
    assert controller.current_stage_index() == 0
    incident = SystemEventStore(engine).latest(INCIDENT_KIND)
    assert incident is not None
    assert incident.severity is Severity.CRITICAL


def test_a_divergence_at_the_first_stage_stops_instead_of_rolling_back(engine: Engine) -> None:
    controller = LiveController(engine, now=lambda: NOW)
    controller.activate(request())
    controller.start_ramp(RampPlan.default())
    decision = controller.advance(
        RampPlan.default(),
        replace(evidence(), reconciliation_clean=False),
    )
    assert decision.action is StageAction.STOP
    assert controller.current_stage_index() == 0
