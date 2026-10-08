import asyncio
import re
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


# The gold weekend lasts two days while the loop runs every twenty seconds: a closure is an
# episode, and the operator reads one message per episode, never one per cycle.


def test_a_closed_market_alerts_once_for_the_whole_closure(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    for step in range(100):
        asyncio.run(watcher.market_closed("XAUUSD", T0, T0 + timedelta(seconds=20 * step)))

    assert len(sent.texts) == 1
    assert len(alert_rows(engine)) == 1


def test_a_reopened_market_alerts_again_on_its_next_closure(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.market_closed("XAUUSD", T0, T0))
    asyncio.run(watcher.market_closed("XAUUSD", T0, T0 + timedelta(minutes=1)))
    asyncio.run(watcher.market_reopened("XAUUSD", T0 + timedelta(days=2)))
    asyncio.run(watcher.market_closed("XAUUSD", T0 + timedelta(weeks=1), T0 + timedelta(weeks=1)))

    assert len(sent.texts) == 3  # closed, reopened, the new closure
    assert "fermé" in sent.texts[0]
    assert "rouvert" in sent.texts[1]
    assert "fermé" in sent.texts[2]


def test_a_reopening_is_announced_so_a_restarted_process_still_reports_it(
    engine: Engine,
) -> None:
    """The closure latch is in memory, the halt is persisted: the reopening is not latched."""
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.market_reopened("XAUUSD", T0))

    assert len(sent.texts) == 1
    assert "rouvert" in sent.texts[0]


# --- what the operator actually reads (F-024) ----------------------------------------------
#
# These texts go out without a parse mode, so they are plain text: structure comes from
# blank lines, never from markup. Each one says what happened, where, and what happens now.


def test_an_outage_names_the_part_and_the_consequence(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.connection_lost("terminal", T0))

    [text] = sent.texts
    head, _, body = text.partition("\n\n")
    assert head == "🔌 Coupure · terminal MT5"
    assert "Aucun nouvel ordre" in body
    assert "UTC" not in text, "Telegram dates the message; the notice carries no clock stamp"
    assert "<" not in text and ">" not in text  # plain text: no markup can survive


def test_a_restoration_says_the_orders_resume(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.connection_restored("terminal", T0))

    [text] = sent.texts
    assert text.splitlines()[0] == "✅ Connexion rétablie · terminal MT5"
    assert "reprennent" in text


def test_a_repeated_failure_keeps_one_short_line_and_hides_the_file_path(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    for step in range(3):
        asyncio.run(
            watcher.component_failure(
                "market_data",
                r"timeout while reading D:\Projets\TradingAgent\src\feed.py:41 (see https://x/y)",
                T0 + timedelta(seconds=step),
            )
        )

    [text] = sent.texts
    assert text.splitlines()[0] == "⚠️ Échec répété · données de marché"
    assert "3 échecs de suite" in text
    assert "timeout while reading" in text  # the cause stays readable
    assert "D:\\Projets" not in text
    assert "https://x/y" not in text
    assert "(retiré)" in text
    assert all(len(line) <= 80 for line in text.splitlines())


def test_the_start_names_the_mode_and_the_stop_is_final(engine: Engine) -> None:
    """One line each, the action and nothing else — no clock stamp, Telegram dates it."""
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.process_started(T0, mode="DEMO"))
    asyncio.run(watcher.process_stopping(T0 + timedelta(minutes=1)))

    start, stop = sent.texts
    assert start == "🟢 Agent démarré · DEMO"
    assert stop == "🔴 Agent arrêté"
    assert len(start.splitlines()) == 1 and len(stop.splitlines()) == 1


def test_a_degraded_series_names_the_symbol_and_the_state(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.series_check("XAUUSD", "STALE", T0))
    asyncio.run(watcher.series_check("XAUUSD", "STALE", T0 + timedelta(minutes=1)))

    [text] = sent.texts
    assert text.splitlines()[0] == "⚠️ Série dégradée · XAUUSD"
    assert "STALE" in text
    assert "2 contrôles de suite" in text


def test_disk_pressure_names_the_drive_not_the_folders(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.disk_pressure(r"D:\Projets\TradingAgent", 95.0, T0))

    [text] = sent.texts
    assert text.splitlines()[0] == "🪫 Disque presque plein"
    assert "Disque D:" in text
    assert "Projets" not in text
    assert "95.0 %" in text


def test_every_alert_carries_at_most_one_emoji_at_the_head(engine: Engine) -> None:
    emoji = re.compile("[\U0001f300-\U0001faff\u2190-\u21ff\u2600-\u27bf\u2b00-\u2bff]")
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.process_started(T0, mode="DEMO"))
    asyncio.run(watcher.connection_lost("terminal", T0))
    asyncio.run(watcher.connection_lost("reconciliation", T0))
    asyncio.run(watcher.disk_pressure("C:", 95.0, T0))

    for text in sent.texts:
        found = emoji.findall(text)
        assert len(found) <= 1, text
        if found:
            assert emoji.match(text) is not None, text


def test_a_market_closure_names_what_closed_and_what_the_agent_does(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)
    since = datetime(2026, 10, 2, 20, 45, tzinfo=UTC)

    asyncio.run(watcher.market_closed("XAUUSD", since, T0))

    [text] = sent.texts
    head, _, body = text.partition("\n\n")
    assert head == "🌙 Marché fermé · XAUUSD"
    assert "Aucun signal ni ordre sur XAUUSD" in body
    assert "autres marchés" in body  # gold stops, the crypto keeps running
    assert "UTC" not in text, "Telegram dates the message; the notice carries no clock stamp"
    assert "<" not in text and ">" not in text  # plain text: no markup can survive


def test_a_market_reopening_says_the_signals_resume(engine: Engine) -> None:
    sent = Sent()
    watcher = alerter(engine, sent)

    asyncio.run(watcher.market_closed("XAUUSD", T0, T0))
    asyncio.run(watcher.market_reopened("XAUUSD", T0 + timedelta(days=2)))

    assert sent.texts[1].splitlines()[0] == "☀️ Marché rouvert · XAUUSD"
    assert "reprennent" in sent.texts[1]
