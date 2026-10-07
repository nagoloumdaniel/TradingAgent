"""Backtest and research workshop (F-025, TASK-060 to TASK-065).

Never loaded in production: `tests/test_architecture.py` enforces that no production
module imports `tradingagent.backtest`. The harness reuses the production decision path
and the production analytics, so a backtest number and a live number are the same number.
"""

from tradingagent.backtest.costs import CostComparison, CostModel, compare_costs, observed_spread
from tradingagent.backtest.datasets import (
    DATASET_FORMAT,
    CandleDataset,
    DatasetError,
    DatasetImmutableError,
    DatasetIntegrityError,
    DatasetManifest,
    DatasetStore,
    SyntheticRegime,
    load_dataset,
    save_dataset,
    synthetic_dataset,
    write_dataset,
)
from tradingagent.backtest.harness import (
    BacktestConfig,
    BacktestResult,
    TradingSession,
    decision_prefix,
    run_backtest,
)

__all__ = [
    "DATASET_FORMAT",
    "BacktestConfig",
    "BacktestResult",
    "CandleDataset",
    "CostComparison",
    "CostModel",
    "DatasetError",
    "DatasetImmutableError",
    "DatasetIntegrityError",
    "DatasetManifest",
    "DatasetStore",
    "SyntheticRegime",
    "TradingSession",
    "compare_costs",
    "decision_prefix",
    "load_dataset",
    "observed_spread",
    "run_backtest",
    "save_dataset",
    "synthetic_dataset",
    "write_dataset",
]
