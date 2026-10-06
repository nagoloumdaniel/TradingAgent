from pathlib import Path

import pytest
from tests.config.test_strategy_catalog import REGISTRY, VALID, write

from tradingagent.config.errors import ConfigError
from tradingagent.config.strategy_catalog import StrategyCatalog
from tradingagent.core.mode import TradingMode


def a_catalog(tmp_path: Path) -> StrategyCatalog:
    write(tmp_path, "trend@1.0.0.yaml", VALID)
    return StrategyCatalog(tmp_path, REGISTRY)


def test_construction_validates_and_indexes_by_reference(tmp_path: Path) -> None:
    catalog = a_catalog(tmp_path)

    assert "trend@1.0.0" in catalog.current()
    assert catalog.current()["trend@1.0.0"].manifest.max_mode is TradingMode.SIGNAL


def test_a_new_version_is_picked_up_without_touching_the_running_one(tmp_path: Path) -> None:
    catalog = a_catalog(tmp_path)
    before = catalog.current()["trend@1.0.0"]

    write(tmp_path, "trend@1.1.0.yaml", VALID.replace("1.0.0", "1.1.0"))
    catalog.reload()

    current = catalog.current()
    assert set(current) == {"trend@1.0.0", "trend@1.1.0"}
    # The previously loaded strategy is the very same object: no interruption of service.
    assert current["trend@1.0.0"] is before


def test_an_invalid_file_refuses_the_reload_and_keeps_serving(tmp_path: Path) -> None:
    catalog = a_catalog(tmp_path)
    before = catalog.current()

    write(tmp_path, "trend@2.0.0.yaml", VALID.replace("ema_slow: 50", "ema_slow: 5"))
    with pytest.raises(ConfigError):
        catalog.reload()

    assert catalog.current() is before
    assert "trend@1.0.0" in catalog.current()

    # The operator fixes the file and reloads again: it goes through.
    write(tmp_path, "trend@2.0.0.yaml", VALID.replace("1.0.0", "2.0.0"))
    catalog.reload()
    assert "trend@2.0.0" in catalog.current()


def test_a_removed_file_leaves_the_catalog_on_the_next_reload(tmp_path: Path) -> None:
    write(tmp_path, "trend@1.0.0.yaml", VALID)
    write(tmp_path, "trend@1.1.0.yaml", VALID.replace("1.0.0", "1.1.0"))
    catalog = StrategyCatalog(tmp_path, REGISTRY)

    (tmp_path / "trend@1.1.0.yaml").unlink()
    catalog.reload()

    assert set(catalog.current()) == {"trend@1.0.0"}


def test_reload_never_changes_a_state_the_files_do_not_carry(tmp_path: Path) -> None:
    """RM-016: only the files on disk decide; nothing is promoted automatically."""
    catalog = a_catalog(tmp_path)

    manifest = catalog.current()["trend@1.0.0"].manifest
    assert manifest.max_mode is TradingMode.SIGNAL
    write(tmp_path, "trend@2.0.0.yaml", VALID.replace("1.0.0", "2.0.0"))
    catalog.reload()

    assert catalog.current()["trend@1.0.0"].manifest.max_mode is TradingMode.SIGNAL
    assert catalog.current()["trend@2.0.0"].manifest.max_mode is TradingMode.SIGNAL
