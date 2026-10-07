"""Acceptance thresholds and strategy promotion (RM-016, TASK-065).

Two things are frozen before any result is looked at: the threshold set, identified by a
SHA-256 digest, and the evidence describing the candidate. `evaluate_promotion` refuses to
decide when the evidence was not measured against the same threshold digest, so a threshold
can not be quietly relaxed after seeing the numbers. A promotion also requires a named
operator and a UTC timestamp: RM-016 forbids automatic promotion.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from tradingagent.analytics.model import Performance
from tradingagent.core.mode import AiFilter, TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.protocol import StabilityReport
from tradingagent.strategies.manifest import StrategyManifest

THRESHOLDS_FORMAT = "tradingagent.acceptance-thresholds/1"
DECISION_FORMAT = "tradingagent.promotion-decision/1"


class PromotionError(Exception):
    """A promotion cannot be recorded as asked."""


class ThresholdsChangedError(PromotionError):
    """The evidence was produced against a different threshold set."""


@dataclass(frozen=True)
class AcceptanceThresholds:
    """The numbers fixed before the final results are examined."""

    version: str
    min_out_of_sample_retention: float = 0.5
    max_parameter_dispersion: float = 0.5
    min_profitable_regime_ratio: float = 0.5
    min_stability_score: float = 0.5
    min_trades: int = 30
    min_profit_factor_net: float = 1.2
    max_drawdown_eur: Decimal = Decimal("200")
    max_correlation: float = 0.7
    min_monte_carlo_probability: float = 0.5

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("thresholds need a version")
        if not 0.0 <= self.min_profitable_regime_ratio <= 1.0:
            raise ValueError("min_profitable_regime_ratio must be in [0, 1]")
        if not 0.0 <= self.min_stability_score <= 1.0:
            raise ValueError("min_stability_score must be in [0, 1]")
        if not 0.0 <= self.max_correlation <= 1.0:
            raise ValueError("max_correlation must be in [0, 1]")
        if self.min_trades < 1:
            raise ValueError("min_trades must be positive")
        if self.max_drawdown_eur < 0:
            raise ValueError("max_drawdown_eur must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": THRESHOLDS_FORMAT,
            "version": self.version,
            "min_out_of_sample_retention": self.min_out_of_sample_retention,
            "max_parameter_dispersion": self.max_parameter_dispersion,
            "min_profitable_regime_ratio": self.min_profitable_regime_ratio,
            "min_stability_score": self.min_stability_score,
            "min_trades": self.min_trades,
            "min_profit_factor_net": self.min_profit_factor_net,
            "max_drawdown_eur": str(self.max_drawdown_eur),
            "max_correlation": self.max_correlation,
            "min_monte_carlo_probability": self.min_monte_carlo_probability,
        }

    @property
    def digest(self) -> str:
        return _digest(self.to_dict())


def write_thresholds(path: Path, thresholds: AcceptanceThresholds) -> Path:
    """Commit the thresholds to disk, digest included, before any result is inspected."""
    document = thresholds.to_dict()
    document["digest"] = thresholds.digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_thresholds(path: Path) -> AcceptanceThresholds:
    document = json.loads(path.read_text(encoding="utf-8"))
    stored = document.pop("digest", None)
    document.pop("format", None)
    document["max_drawdown_eur"] = Decimal(str(document["max_drawdown_eur"]))
    thresholds = AcceptanceThresholds(**document)
    if stored != thresholds.digest:
        raise ThresholdsChangedError(f"{path}: thresholds were edited after being committed")
    return thresholds


@dataclass(frozen=True)
class PromotionEvidence:
    """Everything a promotion decision may invoke, measured against one threshold digest."""

    strategy_ref: str
    parameters: Mapping[str, Any]
    in_sample: Performance
    out_of_sample: Performance
    cost_net: Performance
    stability: StabilityReport
    thresholds_digest: str
    correlation_with_existing: float | None = None
    monte_carlo_probability_of_profit: float | None = None

    @property
    def digest(self) -> str:
        return _digest(
            {
                "strategy_ref": self.strategy_ref,
                "parameters": {key: str(value) for key, value in sorted(self.parameters.items())},
                "in_sample_net": str(self.in_sample.net_profit),
                "out_of_sample_net": str(self.out_of_sample.net_profit),
                "cost_net_net": str(self.cost_net.net_profit),
                "stability_score": self.stability.score,
                "stability_trades": self.stability.trades,
                "thresholds_digest": self.thresholds_digest,
                "correlation_with_existing": self.correlation_with_existing,
                "monte_carlo_probability_of_profit": self.monte_carlo_probability_of_profit,
            }
        )


@dataclass(frozen=True)
class PromotionDecision:
    strategy_ref: str
    promoted: bool
    reasons: tuple[str, ...]
    thresholds_digest: str
    evidence_digest: str
    decided_at: datetime
    decided_by: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": DECISION_FORMAT,
            "strategy_ref": self.strategy_ref,
            "promoted": self.promoted,
            "reasons": list(self.reasons),
            "thresholds_digest": self.thresholds_digest,
            "evidence_digest": self.evidence_digest,
            "decided_at": self.decided_at.isoformat(),
            "decided_by": self.decided_by,
        }


def evaluate_promotion(
    evidence: PromotionEvidence,
    thresholds: AcceptanceThresholds,
    *,
    decided_at: datetime,
    decided_by: str,
) -> PromotionDecision:
    if decided_at.utcoffset() != timedelta(0):
        raise ValueError("decided_at must be UTC")
    if not decided_by.strip():
        raise ValueError("RM-016 requires the identity of the deciding operator")
    if evidence.thresholds_digest != thresholds.digest:
        raise ThresholdsChangedError(
            "the evidence was produced against different thresholds: "
            "thresholds are written before the final results, never after"
        )

    reasons: list[str] = []
    stability = evidence.stability
    if stability.trades < thresholds.min_trades:
        reasons.append(f"{stability.trades} out-of-sample trade(s), need {thresholds.min_trades}")
    if stability.out_of_sample_retention < thresholds.min_out_of_sample_retention:
        reasons.append(
            f"out-of-sample retention {stability.out_of_sample_retention:.2f} below "
            f"{thresholds.min_out_of_sample_retention:.2f}"
        )
    if stability.parameter_dispersion > thresholds.max_parameter_dispersion:
        reasons.append(
            f"parameter dispersion {stability.parameter_dispersion:.2f} above "
            f"{thresholds.max_parameter_dispersion:.2f}"
        )
    if stability.profitable_regime_ratio < thresholds.min_profitable_regime_ratio:
        reasons.append(
            f"profitable regimes {stability.profitable_regime_ratio:.0%} below "
            f"{thresholds.min_profitable_regime_ratio:.0%}"
        )
    if stability.score < thresholds.min_stability_score:
        reasons.append(
            f"stability score {stability.score:.2f} below {thresholds.min_stability_score:.2f}"
        )
    factor = evidence.cost_net.profit_factor
    if factor is None or factor < thresholds.min_profit_factor_net:
        shown = "undefined" if factor is None else f"{factor:.2f}"
        reasons.append(f"net profit factor {shown} below {thresholds.min_profit_factor_net:.2f}")
    if evidence.cost_net.max_drawdown > thresholds.max_drawdown_eur:
        reasons.append(
            f"drawdown {evidence.cost_net.max_drawdown} above {thresholds.max_drawdown_eur}"
        )
    if (
        evidence.correlation_with_existing is not None
        and abs(evidence.correlation_with_existing) > thresholds.max_correlation
    ):
        reasons.append(
            f"correlation {evidence.correlation_with_existing:.2f} above "
            f"{thresholds.max_correlation:.2f}: the same risk would be duplicated"
        )
    if (
        evidence.monte_carlo_probability_of_profit is not None
        and evidence.monte_carlo_probability_of_profit < thresholds.min_monte_carlo_probability
    ):
        reasons.append(
            f"Monte-Carlo probability of profit "
            f"{evidence.monte_carlo_probability_of_profit:.2f} below "
            f"{thresholds.min_monte_carlo_probability:.2f}"
        )
    return PromotionDecision(
        strategy_ref=evidence.strategy_ref,
        promoted=not reasons,
        reasons=tuple(reasons),
        thresholds_digest=thresholds.digest,
        evidence_digest=evidence.digest,
        decided_at=decided_at,
        decided_by=decided_by,
    )


def record_decision(path: Path, decision: PromotionDecision) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(decision.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


@dataclass(frozen=True)
class ManifestSpec:
    """The production-facing identity of a promoted strategy."""

    strategy_id: str
    version: str
    allowed_symbols: tuple[str, ...]
    timeframes: tuple[Timeframe, ...]
    history_bars: int
    parameters: Mapping[str, Any]
    expiry_bars: int = 1
    ai_filter: AiFilter = AiFilter.SHADOW
    max_mode: TradingMode = TradingMode.SIGNAL


def write_manifest(target_dir: Path, spec: ManifestSpec, *, provenance: str = "") -> Path:
    """Emit `<id>@<version>.yaml` in the exact format `config.strategy_catalog` loads."""
    manifest = StrategyManifest(
        strategy_id=spec.strategy_id,
        version=spec.version,
        max_mode=spec.max_mode,
        allowed_symbols=spec.allowed_symbols,
        timeframes=spec.timeframes,
        history_bars=spec.history_bars,
        expiry_bars=spec.expiry_bars,
        ai_filter=spec.ai_filter,
        parameters=dict(spec.parameters),
    )
    path = target_dir / f"{manifest.ref}.yaml"
    header = [
        "# Generated by tradingagent.research.promotion (TASK-065). Do not edit by hand.",
        f"# Strategy: {manifest.ref}",
    ]
    if provenance:
        header.append(f"# {provenance}")
    body = yaml.safe_dump(manifest.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    content = "\n".join(header) + "\n" + body
    if path.exists():
        if path.read_text(encoding="utf-8") == content:
            return path
        raise PromotionError(
            f"{path} already exists with different content; versions are immutable"
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@dataclass(frozen=True)
class PromotionOutcome:
    decision: PromotionDecision
    manifest_path: Path | None
    decision_path: Path


def promote(
    evidence: PromotionEvidence,
    thresholds: AcceptanceThresholds,
    spec: ManifestSpec,
    *,
    manifest_dir: Path,
    decision_dir: Path,
    decided_at: datetime,
    decided_by: str,
) -> PromotionOutcome:
    """Decide, then materialise the manifest only if the decision is a promotion."""
    decision = evaluate_promotion(
        evidence, thresholds, decided_at=decided_at, decided_by=decided_by
    )
    manifest_path: Path | None = None
    if decision.promoted:
        manifest_path = write_manifest(
            manifest_dir,
            spec,
            provenance=f"promoted on {decided_at.isoformat()} by {decided_by}",
        )
    decision_path = record_decision(
        decision_dir / f"{spec.strategy_id}@{spec.version}.json", decision
    )
    return PromotionOutcome(
        decision=decision, manifest_path=manifest_path, decision_path=decision_path
    )


def strategy_ref(strategy_id: str, version: str) -> str:
    return f"{strategy_id}@{version}"


def _digest(document: Mapping[str, Any]) -> str:
    canonical = json.dumps(document, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evidence_from_campaign(
    strategy_ref_value: str,
    parameters: Mapping[str, Any],
    in_sample: Performance,
    out_of_sample: Performance,
    cost_net: Performance,
    stability: StabilityReport,
    thresholds: AcceptanceThresholds,
    *,
    correlation_with_existing: float | None = None,
    monte_carlo_probability_of_profit: float | None = None,
) -> PromotionEvidence:
    return PromotionEvidence(
        strategy_ref=strategy_ref_value,
        parameters=parameters,
        in_sample=in_sample,
        out_of_sample=out_of_sample,
        cost_net=cost_net,
        stability=stability,
        thresholds_digest=thresholds.digest,
        correlation_with_existing=correlation_with_existing,
        monte_carlo_probability_of_profit=monte_carlo_probability_of_profit,
    )


def candidate_manifests(outcome: PromotionOutcome) -> Sequence[Path]:
    return () if outcome.manifest_path is None else (outcome.manifest_path,)
