from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Dialect, Numeric, String
from sqlalchemy.types import TypeDecorator, TypeEngine


class UtcDateTime(TypeDecorator[datetime]):
    """Refuses naive datetimes on write and always reads back aware UTC.

    SQLite would otherwise drop the timezone silently and break the all-UTC invariant.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        return dialect.type_descriptor(DateTime(timezone=dialect.name != "sqlite"))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError(f"naive datetime refused, timezone required: {value!r}")
        utc = value.astimezone(UTC)
        return utc.replace(tzinfo=None) if dialect.name == "sqlite" else utc

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ExactDecimal(TypeDecorator[Decimal]):
    """Exact decimal amounts. Stored as text on SQLite, which would round through float.

    Floats are refused so binary rounding errors never enter money columns. SQL arithmetic
    or ordering on these columns is not meaningful on SQLite: aggregate in Python.
    """

    impl = String(48)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(38, 12))
        return dialect.type_descriptor(String(48))

    def process_bind_param(self, value: Decimal | int | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, Decimal | int):
            raise TypeError(f"use Decimal or int for exact amounts, got {type(value).__name__}")
        amount = Decimal(value)
        if not amount.is_finite():
            raise ValueError(f"amount must be finite, got {amount}")
        return amount if dialect.name == "postgresql" else str(amount)

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        return None if value is None else Decimal(str(value))
