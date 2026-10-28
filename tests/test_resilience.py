"""Transient failures: classified, spaced out, and said once."""

import asyncio

import pytest
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError

from tradingagent.config.errors import ConfigError
from tradingagent.data.terminal import TerminalError
from tradingagent.resilience import (
    MAX_BACKOFF_SECONDS,
    backoff_delay,
    is_transient,
    one_line,
    retry_transient,
    should_log_attempt,
)


class FakeDriverError(Exception):
    pass


def operational(message: str = "failed to resolve host") -> OperationalError:
    return OperationalError("SELECT 1", {"open": "bound-value-1234"}, FakeDriverError(message))


def test_network_and_pool_failures_are_transient() -> None:
    assert is_transient(operational())
    assert is_transient(InterfaceError("SELECT 1", {}, FakeDriverError("closed")))
    assert is_transient(TerminalError("Authorization failed"))
    assert is_transient(TimeoutError())
    assert is_transient(ConnectionResetError())


def test_a_defect_or_a_refusal_is_not_transient() -> None:
    assert not is_transient(IntegrityError("INSERT", {}, FakeDriverError("duplicate")))
    assert not is_transient(ConfigError("no enabled market"))
    assert not is_transient(ValueError("bad"))


def test_a_missing_or_unreadable_file_is_not_transient() -> None:
    """OSError is transient, but a missing config or terminal path never heals by waiting."""
    assert not is_transient(FileNotFoundError("agent.yaml"))
    assert not is_transient(PermissionError(".env"))
    assert not is_transient(IsADirectoryError("config"))


def test_the_delay_doubles_and_is_capped() -> None:
    delays = [backoff_delay(n, 20.0) for n in range(1, 9)]
    assert delays[:4] == [20.0, 40.0, 80.0, 160.0]
    assert delays[-1] == MAX_BACKOFF_SECONDS
    assert delays == sorted(delays)


def test_the_first_failure_and_then_the_powers_of_two_are_logged() -> None:
    logged = [n for n in range(1, 40) if should_log_attempt(n)]
    assert logged == [1, 2, 4, 8, 16, 32]


def test_one_line_drops_the_statement_and_the_parameters() -> None:
    line = one_line(operational("failed to resolve host 'db.example'\nHINT: something"))
    assert line == "OperationalError: failed to resolve host 'db.example'"
    assert "bound-value-1234" not in line


def test_one_line_is_bounded() -> None:
    assert len(one_line(RuntimeError("x" * 5000))) <= 200


def test_retry_transient_waits_then_returns_the_result() -> None:
    attempts = {"count": 0}
    delays: list[float] = []

    async def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 4:
            raise operational()
        return "ready"

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    result = asyncio.run(retry_transient(flaky, "start-up", sleep=fake_sleep, base=5.0, cap=60.0))

    assert result == "ready"
    assert delays == [5.0, 10.0, 20.0]


def test_retry_transient_raises_a_defect_at_once() -> None:
    async def broken() -> None:
        raise ConfigError("no enabled market")

    async def never(seconds: float) -> None:
        raise AssertionError("a defect must not be waited on")

    with pytest.raises(ConfigError):
        asyncio.run(retry_transient(broken, "start-up", sleep=never))
