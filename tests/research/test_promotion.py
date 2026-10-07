"""TASK-065 / RM-016: thresholds frozen first, manifest loads without a code change.

Hand rationale: a candidate keeping 80 % of its in-sample result out of sample, with a
0.1 parameter dispersion, every period profitable, a 12.0 net profit factor and a 20 EUR
drawdown clears every default threshold. A candidate that keeps nothing, scatters wildly
across perturbations and correlates 0.95 with an existing market clears none.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.config.strategy_catalog import load_strategy_catalog
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.promotion import (
    AcceptanceThresholds,
    ManifestSpec,
    PromotionError,
    ThresholdsChangedError,
    evaluate_promotion,
    evidence_from_campaign,
    load_thresholds,
    promote,
    record_decision,
    write_manifest,
    write_thresholds,
)
from tradingagent.research.protocol import StabilityReport
from tradingagent.strategies.library.witness import Witness
from tradingagent.strategies.registry import REGISTRY

START = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
DECIDED_AT = datetime(2026, 10, 7, 15, 30, tzinfo=UTC)
GOLD = "frxXAUUSD"


def trades_from(pnls, month: int = 1) -> list[Trade]:
    base = datetime(2026, month, 1, tzinfo=UTC)
    return [
        Trade(
            symbol=GOLD,
            strategy_ref="witness@1.0.0",
            direction=Direction.BUY,
            timeframe=Timeframe.M15,
            mode=TradingMode.SIGNAL,
            opened_at=base + timedelta(minutes=index),
            closed_at=base + timedelta(minutes=index + 1),
            pnl_eur=Decimal(str(pnl)),
            risk_eur=Decimal("10"),
        )
        for index, pnl in enumerate(pnls)
    ]


def performance_of(pnls, month: int = 1) -> Performance:
    return compute_performance(trades_from(pnls, month))


PARAMETERS = {
    "ema_fast": 20,
    "ema_slow": 50,
    "atr_period": 14,
    "stop_atr_multiplier": 1.5,
    "take_profit_rr": 2.0,
    "entry_zone_atr": 0.1,
}


def robust_evidence(thresholds: AcceptanceThresholds):
    stability = StabilityReport(
        score=0.8,
        out_of_sample_retention=0.8,
        parameter_dispersion=0.1,
        profitable_regime_ratio=1.0,
        trades=50,
        fragile=False,
        reasons=(),
    )
    return evidence_from_campaign(
        "witness@1.0.0",
        PARAMETERS,
        performance_of([10.0] * 40 + [-1.0] * 10),
        performance_of([8.0] * 40 + [-2.0] * 10, month=2),
        performance_of([6.0] * 40 + [-2.0] * 10, month=2),
        stability,
        thresholds,
        correlation_with_existing=0.3,
        monte_carlo_probability_of_profit=0.7,
    )


def overfit_evidence(thresholds: AcceptanceThresholds):
    stability = StabilityReport(
        score=0.2,
        out_of_sample_retention=-0.2,
        parameter_dispersion=1.2,
        profitable_regime_ratio=0.2,
        trades=50,
        fragile=True,
        reasons=("out-of-sample retention -0.20 below 0.50",),
    )
    return evidence_from_campaign(
        "witness@1.0.0",
        PARAMETERS,
        performance_of([12.0] * 40 + [-1.0] * 10),
        performance_of([-4.0] * 45 + [1.0] * 5, month=2),
        performance_of([1.0] * 20 + [-5.0] * 30, month=2),
        stability,
        thresholds,
        correlation_with_existing=0.95,
        monte_carlo_probability_of_profit=0.2,
    )


def spec() -> ManifestSpec:
    return ManifestSpec(
        strategy_id="witness",
        version="1.1.0",
        allowed_symbols=(GOLD,),
        timeframes=(Timeframe.M15,),
        history_bars=300,
        parameters=PARAMETERS,
    )


def test_thresholds_round_trip_and_keep_their_digest(tmp_path) -> None:
    thresholds = AcceptanceThresholds(version="2026-10-07-a")
    path = write_thresholds(tmp_path / "thresholds.json", thresholds)
    loaded = load_thresholds(path)
    assert loaded == thresholds
    assert loaded.digest == thresholds.digest


def test_edited_thresholds_are_detected(tmp_path) -> None:
    thresholds = AcceptanceThresholds(version="2026-10-07-a", min_trades=30)
    path = write_thresholds(tmp_path / "thresholds.json", thresholds)
    document = path.read_text(encoding="utf-8").replace('"min_trades": 30', '"min_trades": 5')
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ThresholdsChangedError):
        load_thresholds(path)


def test_evidence_produced_against_other_thresholds_is_refused() -> None:
    fixed = AcceptanceThresholds(version="2026-10-07-a")
    relaxed = AcceptanceThresholds(version="2026-10-07-a", min_trades=1)
    with pytest.raises(ThresholdsChangedError, match="written before"):
        evaluate_promotion(
            robust_evidence(fixed), relaxed, decided_at=DECIDED_AT, decided_by="operator"
        )


def test_a_promotion_needs_a_named_operator_and_a_utc_timestamp() -> None:
    thresholds = AcceptanceThresholds(version="2026-10-07-a")
    evidence = robust_evidence(thresholds)
    with pytest.raises(ValueError, match="operator"):
        evaluate_promotion(evidence, thresholds, decided_at=DECIDED_AT, decided_by="  ")
    with pytest.raises(ValueError, match="UTC"):
        evaluate_promotion(
            evidence, thresholds, decided_at=DECIDED_AT.replace(tzinfo=None), decided_by="op"
        )


def test_a_robust_candidate_is_promoted() -> None:
    thresholds = AcceptanceThresholds(version="2026-10-07-a")
    decision = evaluate_promotion(
        robust_evidence(thresholds), thresholds, decided_at=DECIDED_AT, decided_by="operator"
    )
    assert decision.promoted is True
    assert decision.reasons == ()
    assert decision.decided_at == DECIDED_AT
    assert decision.decided_by == "operator"
    assert decision.thresholds_digest == thresholds.digest
    assert len(decision.evidence_digest) == 64


def test_an_overfitted_candidate_is_rejected_with_reasons() -> None:
    thresholds = AcceptanceThresholds(version="2026-10-07-a")
    decision = evaluate_promotion(
        overfit_evidence(thresholds), thresholds, decided_at=DECIDED_AT, decided_by="operator"
    )
    assert decision.promoted is False
    assert len(decision.reasons) >= 5
    joined = " | ".join(decision.reasons)
    assert "retention" in joined
    assert "dispersion" in joined
    assert "profit factor" in joined
    assert "correlation" in joined
    assert "Monte-Carlo" in joined


def test_manifest_is_written_in_production_format_and_loads(tmp_path) -> None:
    target = tmp_path / "manifests"
    path = write_manifest(target, spec())
    assert path.name == "witness@1.1.0.yaml"
    catalog = load_strategy_catalog(target, REGISTRY)
    assert set(catalog) == {"witness@1.1.0"}
    loaded = catalog["witness@1.1.0"]
    assert isinstance(loaded.strategy, Witness)
    assert loaded.manifest.parameters == PARAMETERS
    assert loaded.manifest.history_bars == 300
    assert loaded.manifest.allowed_symbols == (GOLD,)


def test_manifest_version_is_immutable(tmp_path) -> None:
    target = tmp_path / "manifests"
    write_manifest(target, spec())
    other = ManifestSpec(
        strategy_id="witness",
        version="1.1.0",
        allowed_symbols=(GOLD,),
        timeframes=(Timeframe.M15,),
        history_bars=400,
        parameters=PARAMETERS,
    )
    with pytest.raises(PromotionError, match="immutable"):
        write_manifest(target, other)


def test_decision_is_recorded_with_identity_and_timestamp(tmp_path) -> None:
    thresholds = AcceptanceThresholds(version="2026-10-07-a")
    decision = evaluate_promotion(
        robust_evidence(thresholds), thresholds, decided_at=DECIDED_AT, decided_by="operator"
    )
    path = record_decision(tmp_path / "decision.json", decision)
    document = path.read_text(encoding="utf-8")
    assert "2026-10-07T15:30:00+00:00" in document
    assert '"decided_by": "operator"' in document
    assert '"promoted": true' in document


def test_promote_writes_a_manifest_only_when_the_candidate_passes(tmp_path) -> None:
    thresholds = AcceptanceThresholds(version="2026-10-07-a")
    rejected = promote(
        overfit_evidence(thresholds),
        thresholds,
        spec(),
        manifest_dir=tmp_path / "manifests",
        decision_dir=tmp_path / "decisions",
        decided_at=DECIDED_AT,
        decided_by="operator",
    )
    assert rejected.manifest_path is None
    assert rejected.decision.promoted is False
    assert not (tmp_path / "manifests").exists()
    assert rejected.decision_path.is_file()

    accepted = promote(
        robust_evidence(thresholds),
        thresholds,
        spec(),
        manifest_dir=tmp_path / "manifests",
        decision_dir=tmp_path / "decisions",
        decided_at=DECIDED_AT,
        decided_by="operator",
    )
    assert accepted.manifest_path is not None
    assert accepted.manifest_path.is_file()
    assert "promoted on 2026-10-07T15:30:00+00:00 by operator" in accepted.manifest_path.read_text(
        encoding="utf-8"
    )
    assert set(load_strategy_catalog(tmp_path / "manifests", REGISTRY)) == {"witness@1.1.0"}


def test_threshold_defaults_are_the_documented_ones() -> None:
    thresholds = AcceptanceThresholds(version="v")
    assert thresholds.min_trades == 30
    assert thresholds.min_out_of_sample_retention == 0.5
    assert thresholds.max_correlation == 0.7
    assert thresholds.max_drawdown_eur == Decimal("200")
    assert str(START.tzinfo) == "UTC"
