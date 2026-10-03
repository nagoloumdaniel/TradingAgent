import math
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import Numeric
from sqlalchemy.dialects import postgresql, sqlite

from tradingagent.storage.types import ExactDecimal, UtcDateTime

SQLITE = sqlite.dialect()
POSTGRES = postgresql.dialect()


NAIVE_NOON = datetime(2026, 10, 4, 12, 0)  # noqa: DTZ001 - deliberately naive, as SQLite stores it


def test_aware_datetime_is_stored_as_utc() -> None:
    paris = timezone(timedelta(hours=2))
    bound = UtcDateTime().process_bind_param(datetime(2026, 10, 4, 14, 0, tzinfo=paris), SQLITE)
    assert bound == NAIVE_NOON


def test_naive_datetime_is_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        UtcDateTime().process_bind_param(NAIVE_NOON, SQLITE)


def test_stored_datetime_is_read_back_as_aware_utc() -> None:
    loaded = UtcDateTime().process_result_value(NAIVE_NOON, SQLITE)
    assert loaded == datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    assert loaded is not None and loaded.tzinfo is UTC


def test_none_passes_through() -> None:
    assert UtcDateTime().process_bind_param(None, SQLITE) is None
    assert UtcDateTime().process_result_value(None, SQLITE) is None


def test_decimal_round_trips_exactly() -> None:
    column = ExactDecimal()
    stored = column.process_bind_param(Decimal("0.1"), SQLITE)
    assert column.process_result_value(stored, SQLITE) == Decimal("0.1")


def test_integer_is_accepted() -> None:
    column = ExactDecimal()
    assert column.process_result_value(column.process_bind_param(100, SQLITE), SQLITE) == 100


def test_float_is_refused_to_keep_binary_errors_out() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        ExactDecimal().process_bind_param(0.1, SQLITE)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_decimal_is_refused(value: Decimal) -> None:
    with pytest.raises(ValueError, match="finite"):
        ExactDecimal().process_bind_param(value, SQLITE)


def test_decimal_uses_numeric_on_postgres() -> None:
    assert isinstance(ExactDecimal().load_dialect_impl(POSTGRES), Numeric)


def test_float_nan_is_also_refused() -> None:
    with pytest.raises(TypeError):
        ExactDecimal().process_bind_param(math.nan, SQLITE)  # type: ignore[arg-type]
