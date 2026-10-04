from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from tests.risk.builders import EQUITY, RISK, context

from tradingagent.control.guardian import Guardian
from tradingagent.control.quarantine import PersistentQuarantine
from tradingagent.core.account import AccountModeMismatchError
from tradingagent.core.halt import CONNECTION, GLOBAL
from tradingagent.core.mode import TradingMode
from tradingagent.core.states import HaltAction, HaltSource
from tradingagent.risk.model import PortfolioState, limits_for
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltCommand, HaltStore, OperatorRequiredError
from tradingagent.storage.migrate import upgrade

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
DEMO = limits_for(TradingMode.DEMO, RISK)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'control.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


@pytest.fixture
def store(engine: Engine) -> HaltStore:
    return HaltStore(engine)


def guardian(store: HaltStore, now: datetime = NOW) -> Guardian:
    return Guardian(store, now=lambda: now)


def portfolio(**changes: Any) -> PortfolioState:
    return replace(context().portfolio, **changes)


def test_nothing_halts_a_healthy_account(store: HaltStore) -> None:
    assert guardian(store).on_portfolio(EQUITY, portfolio(), DEMO) is False
    assert not guardian(store).trading_status().halted


def test_reaching_the_weekly_loss_halts_the_agent(store: HaltStore) -> None:
    lost = portfolio(week_pnl=-EQUITY * D("0.06"))
    assert guardian(store).on_portfolio(EQUITY, lost, DEMO) is True
    status = guardian(store).trading_status()
    assert status.halted
    assert "weekly loss" in status.reasons[0]
    assert not status.close_positions  # never implied by an automatic halt


def test_reaching_the_drawdown_limit_halts_the_agent(store: HaltStore) -> None:
    assert guardian(store).on_portfolio(EQUITY, portfolio(equity_peak=D(6200)), DEMO)
    assert "drawdown" in guardian(store).trading_status().reasons[0]


def test_a_breach_is_halted_once_not_on_every_check(store: HaltStore) -> None:
    lost = portfolio(week_pnl=-EQUITY * D("0.07"))
    for _ in range(3):
        guardian(store).on_portfolio(EQUITY, lost, DEMO)
    assert len(store.history(GLOBAL)) == 1


def test_only_the_operator_lifts_a_limit_halt(store: HaltStore) -> None:
    guardian(store).on_portfolio(EQUITY, portfolio(week_pnl=-EQUITY), DEMO)
    with pytest.raises(OperatorRequiredError):
        store.issue(HaltCommand(GLOBAL, HaltAction.RESUME, HaltSource.AUTOMATIC, "x", "agent", NOW))
    store.issue(HaltCommand(GLOBAL, HaltAction.RESUME, HaltSource.SERVER, "checked", "me", NOW))
    assert not guardian(store).trading_status().halted


def test_an_account_mismatch_halts_everything(store: HaltStore) -> None:
    guardian(store).on_account_mismatch(AccountModeMismatchError("real account in DEMO"))
    assert "RM-017" in guardian(store).trading_status().reasons[0]


def test_a_divergence_halts_everything(store: HaltStore) -> None:
    guardian(store).on_divergence("position 123 unknown locally")
    assert "RM-014" in guardian(store).trading_status().reasons[0]


def test_a_short_disconnection_does_not_halt(store: HaltStore) -> None:
    lost_since = NOW - timedelta(seconds=30)
    assert guardian(store).on_connection_lost(lost_since, timedelta(minutes=2)) is False
    assert not store.is_halted(CONNECTION)


def test_a_long_disconnection_suspends_then_healthy_data_resumes(store: HaltStore) -> None:
    lost_since = NOW - timedelta(minutes=5)
    assert guardian(store).on_connection_lost(lost_since, timedelta(minutes=2)) is True
    guardian(store).on_connection_lost(lost_since, timedelta(minutes=2))
    assert len(store.history(CONNECTION)) == 1
    assert guardian(store).trading_status().halted
    guardian(store).on_data_healthy()
    assert not guardian(store).trading_status().halted


def test_healthy_data_never_lifts_an_operator_halt(store: HaltStore) -> None:
    store.issue(HaltCommand(GLOBAL, HaltAction.HALT, HaltSource.SERVER, "pause", "me", NOW))
    guardian(store).on_connection_lost(NOW - timedelta(minutes=5), timedelta(minutes=2))
    guardian(store).on_data_healthy()
    status = guardian(store).trading_status()
    assert status.halted
    assert status.reasons[0].startswith("global")


def test_a_quarantine_survives_a_restart(engine: Engine, tmp_path: Path) -> None:
    PersistentQuarantine(HaltStore(engine)).quarantine("flaky@1.0.0", "XAUUSD", "3 errors", NOW)
    restarted = PersistentQuarantine(HaltStore(engine))
    assert restarted.is_quarantined("flaky@1.0.0", "XAUUSD")
    assert not restarted.is_quarantined("flaky@1.0.0", "BTCUSD")
    assert restarted.quarantined() == {("flaky@1.0.0", "XAUUSD")}
    restarted.rearm("flaky@1.0.0", "XAUUSD", actor="me")
    assert restarted.quarantined() == set()


def test_an_unreadable_quarantine_keeps_the_pair_stopped(tmp_path: Path) -> None:
    broken = create_database_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    try:
        assert PersistentQuarantine(HaltStore(broken)).is_quarantined("any@1.0.0", "XAUUSD")
    finally:
        broken.dispose()
