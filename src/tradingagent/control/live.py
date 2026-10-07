"""Live-mode activation and the progressive ramp-up (TASK-091, TASK-092).

Two rules hold the whole module together.

*RM-000* — live mode can never be switched on from one side only: the server flag
`LIVE_TRADING_ENABLED=true` **and** an explicit, recorded operator confirmation are both
required. *RM-005* — the risk per trade is capped at 5 % in live mode, and the first
stage of the ramp stays well below that ceiling.

Every decision is persisted in `system_events` (append-only, section 11): the
authorisation keeps its author and its cap, each ramp step keeps the evidence it was
taken on, and an incident stays readable long after the process restarted. Reversibility
is explicit: a stage either holds, promotes, rolls back to the previous one, or stops —
and a stop halts trading through the same `HaltStore` the emergency stop uses.

The credential check never stores or logs a secret: only a salted-nowhere SHA-256 digest
is compared, in memory, to prove the live account is not the demo one (section 15.2).
"""

import hashlib
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Engine

from tradingagent.core.halt import GLOBAL
from tradingagent.core.states import HaltAction, HaltSource, Severity
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.halts import HaltCommand, HaltStore

log = logging.getLogger(__name__)

# RM-005: the absolute ceiling of the risk per trade in live mode.
LIVE_RISK_CEILING_PCT = Decimal("5")
# RM-005: the value applied by default on activation, before any ramp-up.
LIVE_RISK_DEFAULT_PCT = Decimal("2")

ACTIVATION_KIND = "live_activation"
STAGE_KIND = "live_ramp_stage"
INCIDENT_KIND = "live_incident"

AGENT = "agent"


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- credentials: compared, never stored ----------------------------------------


@dataclass(frozen=True)
class CredentialFingerprint:
    """What identifies a credential set without carrying the secret itself."""

    login: int
    server: str
    secret_digest: str


def fingerprint(login: int, server: str, secret: str) -> CredentialFingerprint:
    """A digest of the password, not the password: it is compared in memory and dropped."""
    if not secret.strip():
        raise ValueError("a credential secret must not be blank")
    if not server.strip():
        raise ValueError("a credential server must not be blank")
    return CredentialFingerprint(
        login=login,
        server=server.strip().casefold(),
        secret_digest=hashlib.sha256(secret.encode()).hexdigest(),
    )


def reuses_demo_credentials(demo: CredentialFingerprint, live: CredentialFingerprint) -> bool:
    """True when the live set is the demo one, or shares its password (section 15.2)."""
    if demo.login == live.login and demo.server == live.server:
        return True
    return demo.secret_digest == live.secret_digest


# --- TASK-091: activation --------------------------------------------------------


@dataclass(frozen=True)
class OperatorConfirmation:
    actor: str
    reason: str
    confirmed_at: datetime


@dataclass(frozen=True)
class LiveActivationRequest:
    server_flag: bool
    confirmation: OperatorConfirmation | None
    demo: CredentialFingerprint
    live: CredentialFingerprint
    requested_risk_per_trade_pct: Decimal = LIVE_RISK_DEFAULT_PCT
    emergency_stop_validated: bool = False
    legal_checklist_signed: bool = False


@dataclass(frozen=True)
class ActivationDecision:
    allowed: bool
    reasons: tuple[str, ...]
    risk_per_trade_pct: Decimal | None


@dataclass(frozen=True)
class LiveActivation:
    actor: str
    risk_per_trade_pct: Decimal
    confirmed_at: datetime
    reason: str
    stage_index: int = 0


class LiveActivationRefused(Exception):
    """The double condition of RM-000, or one of its guards, is not met."""

    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def evaluate_activation(request: LiveActivationRequest) -> ActivationDecision:
    """The pure gate of RM-000, F-018 and section 22.3: every missing condition is named."""
    reasons: list[str] = []
    if not request.server_flag:
        reasons.append(
            "the server flag LIVE_TRADING_ENABLED is not true: live mode wants it and an "
            "operator confirmation (RM-000)"
        )
    confirmation = request.confirmation
    if confirmation is None:
        reasons.append("no explicit operator confirmation was recorded (RM-000)")
    elif not confirmation.actor.strip():
        reasons.append("the operator confirmation carries no actor (RM-000)")

    risk = request.requested_risk_per_trade_pct
    if risk <= 0:
        reasons.append("the live risk per trade must be positive (RM-005)")
    elif risk > LIVE_RISK_CEILING_PCT:
        reasons.append(
            f"the live risk per trade exceeds the RM-005 ceiling of {LIVE_RISK_CEILING_PCT}%"
        )

    if reuses_demo_credentials(request.demo, request.live):
        reasons.append(
            "the live credentials reuse the demo ones: real and demo stay strictly "
            "separate (section 15.2)"
        )
    if not request.emergency_stop_validated:
        reasons.append(
            "the emergency stop has not been validated in demo before activation (F-018)"
        )
    if not request.legal_checklist_signed:
        reasons.append("the TASK-090 legal checklist is not signed: phase 9 must not start")

    allowed = not reasons
    return ActivationDecision(
        allowed=allowed,
        reasons=tuple(reasons),
        risk_per_trade_pct=risk if allowed else None,
    )


# --- TASK-092: the progressive ramp ----------------------------------------------


class StageAction(StrEnum):
    HOLD = "hold"
    PROMOTE = "promote"
    ROLLBACK = "rollback"
    STOP = "stop"


@dataclass(frozen=True)
class RampStage:
    index: int
    name: str
    risk_per_trade_pct: Decimal
    min_duration_days: int
    min_trades: int
    max_drawdown_pct: Decimal

    @property
    def min_duration(self) -> timedelta:
        return timedelta(days=self.min_duration_days)


@dataclass(frozen=True)
class RampPlan:
    stages: tuple[RampStage, ...]

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("a ramp plan needs at least one stage")
        expected = list(range(len(self.stages)))
        if [stage.index for stage in self.stages] != expected:
            raise ValueError("ramp stages must be indexed from 0 without a gap")
        for stage in self.stages:
            if not 0 < stage.risk_per_trade_pct <= LIVE_RISK_CEILING_PCT:
                raise ValueError(
                    f"stage {stage.index} risk {stage.risk_per_trade_pct}% leaves "
                    f"(0, {LIVE_RISK_CEILING_PCT}%] (RM-005)"
                )
            if stage.min_duration_days < 1 or stage.min_trades < 1:
                raise ValueError(f"stage {stage.index} has no meaningful threshold")

    def stage(self, index: int) -> RampStage:
        if not 0 <= index < len(self.stages):
            raise ValueError(f"unknown ramp stage {index}")
        return self.stages[index]

    def previous(self, index: int) -> int:
        return max(index - 1, 0)

    def has_next(self, index: int) -> bool:
        return index + 1 < len(self.stages)

    @classmethod
    def default(cls) -> "RampPlan":
        """Start small, prove it, then widen — never past the RM-005 ceiling."""
        return cls(
            stages=(
                RampStage(0, "amorce", Decimal("1"), 7, 10, Decimal("3")),
                RampStage(1, "renforce", Decimal("2"), 14, 30, Decimal("5")),
                RampStage(2, "croisiere", Decimal("5"), 30, 60, Decimal("8")),
            )
        )


@dataclass(frozen=True)
class StageEvidence:
    """What the daily check reads to decide whether the current stage still holds."""

    elapsed: timedelta
    trades: int
    max_drawdown_pct: Decimal
    daily_loss_breaches: int
    incidents_open: int
    reconciliation_clean: bool
    supervised_today: bool


@dataclass(frozen=True)
class RampDecision:
    action: StageAction
    stage_index: int
    target_index: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SupervisionReport:
    supervised: bool
    alerts: tuple[str, ...]


def evaluate_ramp(plan: RampPlan, stage_index: int, evidence: StageEvidence) -> RampDecision:
    """One pure decision per daily check: hold, promote, roll back, or stop (TASK-092)."""
    stage = plan.stage(stage_index)

    if evidence.incidents_open > 0:
        return RampDecision(
            StageAction.STOP,
            stage_index,
            stage_index,
            (
                f"{evidence.incidents_open} incident(s) open: the ramp stops until the "
                "operator closes them (RM-014, RM-017)",
            ),
        )
    if not evidence.reconciliation_clean:
        return _degrade(
            plan,
            stage_index,
            "local state diverges from the broker account (RM-014): never resolved alone",
        )
    if evidence.max_drawdown_pct > stage.max_drawdown_pct:
        return _degrade(
            plan,
            stage_index,
            f"drawdown {evidence.max_drawdown_pct}% past the stage ceiling "
            f"{stage.max_drawdown_pct}%",
        )
    if evidence.daily_loss_breaches > 0:
        return _degrade(
            plan,
            stage_index,
            f"{evidence.daily_loss_breaches} daily loss limit(s) breached (RM-006)",
        )
    if not evidence.supervised_today:
        return RampDecision(
            StageAction.HOLD,
            stage_index,
            stage_index,
            ("surveillance quotidienne non effectuée: no promotion without the daily check",),
        )

    missing: list[str] = []
    if evidence.elapsed < stage.min_duration:
        missing.append(f"durée du palier insuffisante: {evidence.elapsed} < {stage.min_duration}")
    if evidence.trades < stage.min_trades:
        missing.append(f"opérations insuffisantes: {evidence.trades} < {stage.min_trades}")
    if missing:
        return RampDecision(StageAction.HOLD, stage_index, stage_index, tuple(missing))

    if not plan.has_next(stage_index):
        return RampDecision(
            StageAction.HOLD,
            stage_index,
            stage_index,
            ("palier maximal atteint: the RM-005 ceiling is not a target to cross",),
        )
    return RampDecision(StageAction.PROMOTE, stage_index, stage_index + 1, ())


def _degrade(plan: RampPlan, stage_index: int, reason: str) -> RampDecision:
    """Roll back one stage, or stop outright when there is no lower stage to fall to."""
    if stage_index == 0:
        return RampDecision(StageAction.STOP, stage_index, 0, (reason,))
    return RampDecision(StageAction.ROLLBACK, stage_index, plan.previous(stage_index), (reason,))


def supervise(stage: RampStage, evidence: StageEvidence) -> SupervisionReport:
    """The daily check, as a list of concrete alerts instead of one opaque boolean."""
    alerts: list[str] = []
    if not evidence.supervised_today:
        alerts.append("surveillance quotidienne non effectuée")
    if not evidence.reconciliation_clean:
        alerts.append("réconciliation en écart (RM-014)")
    if evidence.incidents_open:
        alerts.append(f"{evidence.incidents_open} incident(s) ouvert(s)")
    if evidence.max_drawdown_pct > stage.max_drawdown_pct:
        alerts.append(
            f"drawdown {evidence.max_drawdown_pct}% au-delà du palier {stage.max_drawdown_pct}%"
        )
    if evidence.daily_loss_breaches:
        alerts.append(f"{evidence.daily_loss_breaches} limite(s) de perte quotidienne franchie(s)")
    return SupervisionReport(supervised=not alerts, alerts=tuple(alerts))


# --- the controller: persistence and reversibility -------------------------------


class LiveController:
    """Records the authorisation, the ramp and the incidents, and halts when told to."""

    def __init__(
        self,
        engine: Engine,
        halts: HaltStore | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._events = SystemEventStore(engine)
        self._halts = halts
        self._now = now

    def activate(self, request: LiveActivationRequest) -> LiveActivation:
        decision = evaluate_activation(request)
        if not decision.allowed:
            log.warning("live activation refused: %s", "; ".join(decision.reasons))
            raise LiveActivationRefused(decision.reasons)

        confirmation = request.confirmation
        risk = decision.risk_per_trade_pct
        if confirmation is None or risk is None:
            # Unreachable through the public gate, kept so the invariant never depends on it.
            raise LiveActivationRefused(decision.reasons or ("incomplete activation request",))
        at = self._now()
        activation = LiveActivation(
            actor=confirmation.actor.strip(),
            risk_per_trade_pct=risk,
            confirmed_at=confirmation.confirmed_at,
            reason=confirmation.reason,
        )
        self._events.record(
            ACTIVATION_KIND,
            Severity.INFO,
            {
                "actor": activation.actor,
                "reason": activation.reason,
                "risk_per_trade_pct": str(activation.risk_per_trade_pct),
                "confirmed_at": activation.confirmed_at.isoformat(),
                "occurred_at": at.isoformat(),
                "server_flag": True,
                "credentials_distinct": True,
                "emergency_stop_validated": request.emergency_stop_validated,
                "legal_checklist_signed": request.legal_checklist_signed,
                "ramp_stage": activation.stage_index,
            },
            at,
        )
        log.warning(
            "live mode activated by %s, risk per trade capped at %s%% (RM-000, RM-005)",
            activation.actor,
            activation.risk_per_trade_pct,
        )
        return activation

    def start_ramp(self, plan: RampPlan) -> RampDecision:
        if self._events.latest(ACTIVATION_KIND) is None:
            raise LiveActivationRefused(
                ("no recorded live activation: the ramp starts after the operator does (RM-000)",)
            )
        stage = plan.stage(0)
        decision = RampDecision(StageAction.HOLD, 0, 0, (f"palier d'amorce: {stage.name}",))
        self._record_stage(decision, None)
        return decision

    def advance(self, plan: RampPlan, evidence: StageEvidence, actor: str = AGENT) -> RampDecision:
        current = self.current_stage_index()
        decision = evaluate_ramp(plan, current, evidence)
        self._record_stage(decision, evidence)
        if decision.action is StageAction.STOP:
            self._halt(decision, actor)
        return decision

    def current_stage_index(self) -> int:
        event = self._events.latest(STAGE_KIND)
        if event is None:
            return 0
        return int(event.detail["to"])

    def record_incident(self, kind: str, detail: Mapping[str, Any], at: datetime) -> None:
        log.critical("live incident %s: %s", kind, detail)
        self._events.record(
            INCIDENT_KIND,
            Severity.CRITICAL,
            {"incident": kind, **detail},
            at,
        )

    def _record_stage(self, decision: RampDecision, evidence: StageEvidence | None) -> None:
        at = self._now()
        detail: dict[str, Any] = {
            "action": decision.action.value,
            "from": decision.stage_index,
            "to": decision.target_index,
            "reasons": list(decision.reasons),
            "occurred_at": at.isoformat(),
        }
        if evidence is not None:
            detail["evidence"] = {
                "elapsed_seconds": evidence.elapsed.total_seconds(),
                "trades": evidence.trades,
                "max_drawdown_pct": str(evidence.max_drawdown_pct),
                "daily_loss_breaches": evidence.daily_loss_breaches,
                "incidents_open": evidence.incidents_open,
                "reconciliation_clean": evidence.reconciliation_clean,
                "supervised_today": evidence.supervised_today,
            }
        severity = Severity.WARNING if decision.action in _DEGRADING else Severity.INFO
        self._events.record(STAGE_KIND, severity, detail, at)

    def _halt(self, decision: RampDecision, actor: str) -> None:
        if self._halts is None:
            log.critical("ramp stop without a halt store: stop the execution manually")
            return
        reason = "; ".join(decision.reasons) or "ramp stopped"
        self._halts.issue(
            HaltCommand(
                GLOBAL,
                HaltAction.HALT,
                HaltSource.AUTOMATIC,
                f"progressive activation stopped: {reason}",
                actor,
                self._now(),
            )
        )


_DEGRADING = frozenset({StageAction.ROLLBACK, StageAction.STOP})
