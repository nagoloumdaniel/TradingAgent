"""Rows written before the mode cleanup (OBSERVATION, SIGNAL, PAPER) still read back."""

from pathlib import Path

import pytest

from tradingagent.core.mode import TradingMode
from tradingagent.storage.models import TradingModeType


@pytest.mark.parametrize("legacy", ["OBSERVATION", "SIGNAL", "PAPER"])
def test_a_legacy_mode_reads_back_as_demo(legacy: str, tmp_path: Path) -> None:
    kind = TradingModeType()
    assert kind.process_result_value(legacy, None) is TradingMode.DEMO  # type: ignore[arg-type]


@pytest.mark.parametrize("current", [TradingMode.DEMO, TradingMode.LIVE])
def test_a_current_mode_is_unchanged(current: TradingMode) -> None:
    kind = TradingModeType()
    assert kind.process_result_value(current.value, None) is current  # type: ignore[arg-type]
