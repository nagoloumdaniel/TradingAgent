import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tradingagent.notify.health_alerts import HealthAlerter
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.events import SystemEventStore
from tradingagent.storage.migrate import upgrade
from tradingagent.storage.models import SystemEventRow

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
COOLDOWN = timedelta(minutes=30)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    url = f"sqlite:///{tmp_path / 'alerts.db'}"
    upgrade(url)
    built = create_database_engine(url)
    yield built
    built.dispose()


class Sent:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def __call__(self, text: str) -> None:
        self.texts.append(text)


def alerter(engine: Engine, sent: Sent) -> HealthAlerter:
    return HealthAlerter(
        sent,
        SystemEventStore(engine),
        repeat_after=COOLDOWN,
        failure_threshold=3,
        degraded_threshold=2,
        disk_alert_percent=90.0,
    )


def alert_rows(engine: Engine) -> list[SystemEventRow]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(SystemEventRow).where(SystemEventRow.kind == "health_alert")
            ).all()
        )


def test_the_process_start_is_announced_once(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.process_started(T0, mode="DEMO"))
    asyncio.run(watcher.process_started(T0 + timedelta(seconds=5), mode="DEMO"))

    assert len(sent.texts) == 1
    assert "DEMO" in sent.texts[0]
    assert len(alert_rows(engine)) == 1


def test_the_stop_is_announced(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.process_stopping(T0))

    assert len(sent.texts) == 1
    assert len(alert_rows(engine)) == 1


def test_an_outage_alerts_once_and_not_in_a_loop(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.connection_lost("mt5", T0))
    asyncio.run(watcher.connection_lost("mt5", T0 + timedelta(seconds=1)))
    asyncio.run(watcher.connection_lost("mt5", T0 + timedelta(minutes=10)))

    assert len(sent.texts) == 1  # one outage, one alert


def test_the_recovery_resets_the_outage_and_is_notified(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.connection_lost("mt5", T0))
    asyncio.run(watcher.connection_restored("mt5", T0 + timedelta(minutes=5)))
    asyncio.run(watcher.connection_lost("mt5", T0 + timedelta(minutes=6)))

    assert len(sent.texts) == 3
    assert "rétablie" in sent.texts[1]


def test_repeated_component_failures_alert_once_then_again_after_the_cooldown(
    engine: Engine,
) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    for step in range(3):
        asyncio.run(watcher.component_failure("generator", "boom", T0 + timedelta(seconds=step)))
    for step in range(3, 10):
        asyncio.run(watcher.component_failure("generator", "boom", T0 + timedelta(seconds=step)))

    assert len(sent.texts) == 1  # the repeated failures stay silent

    asyncio.run(
        watcher.component_failure("generator", "boom", T0 + COOLDOWN + timedelta(seconds=5))
    )

    assert len(sent.texts) == 2  # after the cooldown the operator is reminded


def test_a_recovered_component_does_not_alert_anymore(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    for step in range(3):
        asyncio.run(watcher.component_failure("generator", "boom", T0 + timedelta(seconds=step)))
    asyncio.run(watcher.component_recovered("generator", T0 + timedelta(minutes=1)))
    asyncio.run(watcher.component_failure("generator", "boom", T0 + timedelta(minutes=2)))

    assert len(sent.texts) == 1  # the new failure is below the threshold again


def test_a_persistently_degraded_series_alerts_per_episode(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.series_check("XAUUSD", "STALE", T0))
    assert len(sent.texts) == 0  # one anomaly is not persistence yet
    asyncio.run(watcher.series_check("XAUUSD", "STALE", T0 + timedelta(minutes=1)))
    assert len(sent.texts) == 1
    asyncio.run(watcher.series_check("XAUUSD", "STALE", T0 + timedelta(minutes=2)))
    assert len(sent.texts) == 1  # same episode, no loop

    asyncio.run(watcher.series_check("XAUUSD", "HEALTHY", T0 + timedelta(minutes=3)))
    asyncio.run(watcher.series_check("XAUUSD", "STALE", T0 + timedelta(minutes=4)))
    asyncio.run(watcher.series_check("XAUUSD", "STALE", T0 + timedelta(minutes=5)))
    assert len(sent.texts) == 2  # a new episode alerts again


def test_disk_pressure_alerts_only_above_the_threshold(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.disk_pressure("C:", 89.9, T0))
    assert len(sent.texts) == 0

    asyncio.run(watcher.disk_pressure("C:", 95.0, T0 + timedelta(minutes=1)))
    asyncio.run(watcher.disk_pressure("C:", 96.0, T0 + timedelta(minutes=2)))
    assert len(sent.texts) == 1
