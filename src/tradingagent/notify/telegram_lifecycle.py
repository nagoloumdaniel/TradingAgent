"""Start and stop the Telegram application without ever depending on the network.

The agent trades whether or not Telegram answers: a notification is best-effort and the
signals not delivered are retried by the pipeline. So Telegram is brought up in the
background, step by step, each step remembered, and a step that fails is retried later
instead of ending the process. Stopping only undoes what was done.

Two defects of 2026-10-23..28 are the reason this exists. `Application.initialize` timed out
on a network that was down, and the `finally` that followed called `Updater.stop()` on an
updater that had never started — `RuntimeError: This Updater is not running!` — which
replaced the real error and ended the process, 38 to 54 seconds after each restart.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from tradingagent.resilience import backoff_delay, one_line, should_log_attempt

log = logging.getLogger(__name__)


class TelegramLifecycle:
    def __init__(
        self,
        application: Any,
        allowed_updates: Sequence[str],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        base: float = 5.0,
        cap: float = 300.0,
    ) -> None:
        self._application = application
        self._allowed_updates = list(allowed_updates)
        self._sleep = sleep
        self._base = base
        self._cap = cap
        self._initialized = False
        self._menu_published = False
        self._started = False
        self._polling = False

    @property
    def ready(self) -> bool:
        """Whether the bot receives commands: every step is done."""
        return self._polling or (self._started and self._application.updater is None)

    async def start_once(self) -> None:
        """Do the steps that are not done yet. Raises at the first one that fails.

        Each step is remembered on success, so a retry resumes where the last attempt
        stopped: starting an application twice is an error, and so is polling twice.
        """
        application = self._application
        if not self._initialized:
            await application.initialize()
            self._initialized = True
        # The menu Telegram shows is published between the two: python-telegram-bot only
        # runs `post_init` from `run_polling`/`run_webhook`, and this code starts the
        # application by hand. `publish_menu` logs a failure rather than raising.
        if not self._menu_published:
            if application.post_init is not None:
                await application.post_init(application)
            self._menu_published = True
        if not self._started:
            await application.start()
            self._started = True
        updater = application.updater
        if updater is not None and not self._polling:
            await updater.start_polling(allowed_updates=self._allowed_updates)
            self._polling = True

    async def run(self) -> None:
        """Retry `start_once` until it works. Cancelled by the caller on shutdown."""
        failures = 0
        while True:
            try:
                await self.start_once()
            except Exception as error:
                failures += 1
                if should_log_attempt(failures):
                    log.warning(
                        "Telegram not started (attempt %d), retrying: %s", failures, one_line(error)
                    )
                await self._sleep(backoff_delay(failures, self._base, self._cap))
                continue
            if failures:
                log.warning("Telegram started after %d failed attempt(s)", failures)
            return

    async def stop(self) -> None:
        """Undo what was done, newest first. Never raises: this runs while the agent exits."""
        application = self._application
        updater = application.updater
        if self._polling and updater is not None:
            await self._quietly("stop polling", updater.stop())
            self._polling = False
        if self._started:
            await self._quietly("stop the application", application.stop())
            self._started = False
        if self._initialized:
            await self._quietly("shut the application down", application.shutdown())
            self._initialized = False
            self._menu_published = False

    @staticmethod
    async def _quietly(what: str, call: Awaitable[Any]) -> None:
        try:
            await call
        except Exception as error:
            log.warning("could not %s: %s", what, one_line(error))
