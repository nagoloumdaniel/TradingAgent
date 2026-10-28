"""The Telegram application comes up in the background and goes down without a crash."""

import asyncio
from typing import Any

import pytest
from telegram.error import NetworkError, TimedOut

from tradingagent.notify.telegram_lifecycle import TelegramLifecycle

UPDATES = ("message", "callback_query")


class FakeUpdater:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.running = False
        self.polling_failures: list[Exception] = []

    async def start_polling(self, *, allowed_updates: list[str]) -> None:
        self.calls.append("start_polling")
        if self.polling_failures:
            raise self.polling_failures.pop(0)
        self.running = True

    async def stop(self) -> None:
        self.calls.append("updater.stop")
        if not self.running:
            raise RuntimeError("This Updater is not running!")
        self.running = False


class FakeApplication:
    def __init__(self, with_updater: bool = True) -> None:
        self.calls: list[str] = []
        self.initialize_failures: list[Exception] = []
        self.updater = FakeUpdater(self.calls) if with_updater else None
        self.post_init: Any = self._post_init
        self.menu_published = 0

    async def _post_init(self, application: object) -> None:
        self.menu_published += 1

    async def initialize(self) -> None:
        self.calls.append("initialize")
        if self.initialize_failures:
            raise self.initialize_failures.pop(0)

    async def start(self) -> None:
        self.calls.append("start")

    async def stop(self) -> None:
        self.calls.append("stop")

    async def shutdown(self) -> None:
        self.calls.append("shutdown")


def lifecycle(application: FakeApplication, delays: list[float] | None = None) -> TelegramLifecycle:
    async def fake_sleep(seconds: float) -> None:
        if delays is not None:
            delays.append(seconds)

    return TelegramLifecycle(application, UPDATES, sleep=fake_sleep, base=5.0, cap=300.0)


def test_a_healthy_start_runs_every_step_once_and_publishes_the_menu() -> None:
    application = FakeApplication()
    bot = lifecycle(application)

    asyncio.run(bot.start_once())

    assert application.calls == ["initialize", "start", "start_polling"]
    assert application.menu_published == 1
    assert bot.ready


def test_a_network_that_is_down_is_retried_with_backoff_and_never_raises() -> None:
    application = FakeApplication()
    application.initialize_failures = [TimedOut("Timed out"), NetworkError("down"), TimedOut("t")]
    delays: list[float] = []
    bot = lifecycle(application, delays)

    asyncio.run(bot.run())

    assert delays == [5.0, 10.0, 20.0]
    assert application.calls[-3:] == ["initialize", "start", "start_polling"]
    assert bot.ready


def test_a_failure_after_the_application_started_does_not_start_it_twice() -> None:
    application = FakeApplication()
    assert application.updater is not None
    application.updater.polling_failures = [TimedOut("Timed out")]
    bot = lifecycle(application)

    asyncio.run(bot.run())

    assert application.calls.count("start") == 1
    assert application.calls.count("initialize") == 1
    assert application.calls.count("start_polling") == 2
    assert application.menu_published == 1


def test_stopping_an_updater_that_never_ran_is_not_an_error() -> None:
    """The 2026-10-28 crash: "RuntimeError: This Updater is not running!" from `finally`."""
    application = FakeApplication()
    application.initialize_failures = [TimedOut("Timed out")]
    bot = lifecycle(application)
    with pytest.raises(TimedOut):
        asyncio.run(bot.start_once())

    asyncio.run(bot.stop())  # does not raise

    assert "updater.stop" not in application.calls
    assert "stop" not in application.calls
    assert "shutdown" not in application.calls


def test_stop_undoes_what_was_done_in_reverse_order() -> None:
    application = FakeApplication()
    bot = lifecycle(application)
    asyncio.run(bot.start_once())
    application.calls.clear()

    asyncio.run(bot.stop())

    assert application.calls == ["updater.stop", "stop", "shutdown"]
    assert not bot.ready


def test_a_step_that_fails_on_the_way_down_does_not_stop_the_others() -> None:
    application = FakeApplication()
    bot = lifecycle(application)
    asyncio.run(bot.start_once())
    assert application.updater is not None
    application.updater.running = False  # polling died by itself: stop() will refuse
    application.calls.clear()

    asyncio.run(bot.stop())

    assert application.calls == ["updater.stop", "stop", "shutdown"]


def test_an_application_without_an_updater_is_ready_once_started() -> None:
    application = FakeApplication(with_updater=False)
    bot = lifecycle(application)

    asyncio.run(bot.start_once())

    assert application.calls == ["initialize", "start"]
    assert bot.ready
