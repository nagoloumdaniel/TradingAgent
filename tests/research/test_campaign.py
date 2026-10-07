"""TASK-064: per-market campaigns, robustness selection and cross-market correlation.

Selection check: candidate "greedy" wins 1000 EUR net out of sample but is fragile
(stability 0.2); candidate "steady" wins only 100 EUR but is stable (0.9). The campaign
must select "steady" — the whole point of TASK-064 and section 2.3.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradingagent.analytics.model import Performance, Trade
from tradingagent.analytics.performance import compute_performance
from tradingagent.backtest.datasets import SyntheticRegime, synthetic_dataset
from tradingagent.backtest.harness import BacktestConfig
from tradingagent.core.market import Direction
from tradingagent.core.mode import TradingMode
from tradingagent.core.timeframe import Timeframe
from tradingagent.research.campaign import (
    CandidateReport,
    CandidateSpec,
    correlate_markets,
    pearson,
    rank_candidates,
    run_campaign,
)
from tradingagent.research.protocol import StabilityReport
from tradingagent.strategies.library.witness import Witness, WitnessParameters
from tradingagent.strategies.manifest import StrategyManifest

START = datetime(2026, 1, 5, tzinfo=UTC)
M15 = Timeframe.M15
BARS = 600


def witness_manifest(history_bars: int = 40) -> StrategyManifest:
    return StrategyManifest.model_validate(
        {
            "strategy_id": "witness",
            "version": "1.0.0",
            "max_mode": "SIGNAL",
            "allowed_symbols": ["frxXAUUSD", "cryBTCUSD"],
            "timeframes": ["M15"],
            "history_bars": history_bars,
            "expiry_bars": 2,
        }
    )


def witness_factory(parameters):
    return Witness(WitnessParameters(**parameters))


def spec(label: str, fast: int, slow: int) -> CandidateSpec:
    parameters = {
        "ema_fast": fast,
        "ema_slow": slow,
        "atr_period": 5,
        "stop_atr_multiplier": 1.5,
        "take_profit_rr": 2.0,
        "entry_zone_atr": 0.1,
    }
    return CandidateSpec(
        label=label,
        manifest=witness_manifest(),
        factory=witness_factory,
        parameters=parameters,
        perturbation=0.1,
    )


def correlated_datasets(idio_volatility: float = 0.0005):
    from tradingagent.backtest.randomness import DeterministicRandom

    stream = DeterministicRandom(2026)
    factor = [stream.gauss() for _ in range(BARS)]
    # The shared factor (beta 0.01) dwarfs the per-market idio noise: the two return
    # series must move together, which is exactly what the campaign has to detect.
    gold = synthetic_dataset(
        "gold-correlated",
        "frxXAUUSD",
        M15,
        START,
        (SyntheticRegime(bars=BARS, drift=0.0, volatility=idio_volatility),),
        seed=1,
        common_returns=factor,
        beta=0.01,
    )
    btc = synthetic_dataset(
        "btc-correlated",
        "cryBTCUSD",
        M15,
        START,
        (SyntheticRegime(bars=BARS, drift=0.0, volatility=idio_volatility),),
        seed=2,
        common_returns=factor,
        beta=0.01,
    )
    return {"frxXAUUSD": gold, "cryBTCUSD": btc}


def independent_datasets():
    regimes = (SyntheticRegime(bars=BARS, drift=0.0, volatility=0.004),)
    return {
        "frxXAUUSD": synthetic_dataset("gold-ind", "frxXAUUSD", M15, START, regimes, seed=5),
        "cryBTCUSD": synthetic_dataset("btc-ind", "cryBTCUSD", M15, START, regimes, seed=6),
    }


def config_for(market: str, dataset) -> BacktestConfig:
    return BacktestConfig(symbol=market, max_concurrent_positions=2, session=None)


def trades_from(pnls, month: int = 1) -> list[Trade]:
    base = datetime(2026, month, 1, tzinfo=UTC)
    return [
        Trade(
            symbol="frxXAUUSD",
            strategy_ref="witness@1.0.0",
            direction=Direction.BUY,
            timeframe=M15,
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


def candidate_report(label: str, net: float, score: float) -> CandidateReport:
    return CandidateReport(
        market="frxXAUUSD",
        label=label,
        train=performance_of([1.0] * 40),
        validation=performance_of([net / 40] * 40, month=2),
        cost_net=performance_of([0.5] * 40, month=2),
        stability_score=score,
        fragile=score < 0.5,
        reasons=(),
        selected=False,
    )


def test_selection_ignores_raw_net_profit() -> None:
    greedy = candidate_report("greedy", net=1000.0, score=0.2)
    steady = candidate_report("steady", net=100.0, score=0.9)
    ranked = rank_candidates([greedy, steady])
    assert ranked[0].label == "steady"
    assert ranked[1].label == "greedy"


def test_campaign_runs_every_market_and_keeps_the_holdout_sealed() -> None:
    report = run_campaign(
        correlated_datasets(),
        [spec("fast", 5, 12), spec("slow", 10, 25)],
        config_for=config_for,
    )
    assert [market.market for market in report.markets] == ["cryBTCUSD", "frxXAUUSD"]
    for market in report.markets:
        assert market.holdout_still_sealed is True
        assert market.selected is not None
        selected = [c for c in market.candidates if c.selected]
        assert len(selected) == 1
        assert selected[0].label == market.selected
        assert "stability" in market.selection_basis
        assert market.candidates[0].stability_score >= 0.0
    assert set(report.selected_by_market()) == {"frxXAUUSD", "cryBTCUSD"}


def test_campaign_measures_the_gold_crypto_correlation() -> None:
    report = run_campaign(
        correlated_datasets(idio_volatility=0.0005),
        [spec("fast", 5, 12)],
        config_for=config_for,
    )
    assert len(report.correlations) == 1
    pair = report.correlations[0]
    assert {pair.market_a, pair.market_b} == {"frxXAUUSD", "cryBTCUSD"}
    assert pair.correlation is not None
    assert pair.correlation > 0.9
    assert pair.aligned_bars >= 400
    assert pair in report.high_correlations()


def test_independent_markets_are_not_flagged_as_correlated() -> None:
    pairs = correlate_markets(independent_datasets())
    pair = pairs[0]
    assert pair.correlation is not None
    assert abs(pair.correlation) < 0.7
    assert pair.high is False


def test_pearson_needs_a_significant_sample() -> None:
    assert pearson([0.1] * 10, [0.1] * 10) is None
    assert pearson([1.0, -1.0] * 40, [1.0, -1.0] * 40) == pytest.approx(1.0)
    assert pearson([1.0, -1.0] * 40, [-1.0, 1.0] * 40) == pytest.approx(-1.0)
    with pytest.raises(ValueError):
        pearson([0.1] * 40, [0.1] * 39)


def test_campaign_refuses_empty_inputs() -> None:
    with pytest.raises(ValueError, match="market"):
        run_campaign({}, [spec("fast", 5, 12)], config_for=config_for)
    with pytest.raises(ValueError, match="candidate"):
        run_campaign(correlated_datasets(), [], config_for=config_for)


def test_stability_report_is_not_a_profit_figure() -> None:
    report = StabilityReport(
        score=0.2,
        out_of_sample_retention=0.1,
        parameter_dispersion=1.0,
        profitable_regime_ratio=0.2,
        trades=50,
        fragile=True,
        reasons=("fragile",),
    )
    assert report.fragile is True
    assert not hasattr(report, "net_profit")
