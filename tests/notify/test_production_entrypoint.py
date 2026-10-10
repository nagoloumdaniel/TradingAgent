"""The production entry point (`tradingagent-run`) must show the buttons too.

The palette speaks through `notify.telegram_app`: an inline keyboard travels in a callback
query, and a callback query only reaches the bot if a `CallbackQueryHandler` is registered
*and* polling asks Telegram for `callback_query` updates. The adapter is wired in two
places — the bot and the agent loop — so these tests pin the agent loop's copy, the one an
operator actually meets in production.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from telegram import Update

from tradingagent import app as app_module
from tradingagent.app import _build_command_service, _build_telegram
from tradingagent.config.settings import Settings
from tradingagent.notify.service import CommandService
from tradingagent.notify.telegram_app import ALLOWED_UPDATES
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltStore
from tradingagent.storage.migrate import upgrade

TOKEN = "123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawE"  # pragma: allowlist secret


def _settings(tmp_path: Any) -> Settings:
    return Settings(
        mt5_login=40123456,
        mt5_server="Deriv-Demo",
        mt5_password="investor-secret",  # pragma: allowlist secret
        telegram_bot_token=TOKEN,
        telegram_allowed_user_ids=(42,),
        database_url=f"sqlite:///{tmp_path / 'entrypoint.db'}",  # pragma: allowlist secret
    )


def _service(settings: Settings) -> CommandService:
    engine = create_database_engine(settings.database_url.get_secret_value())
    return _build_command_service(
        engine,
        settings,
        HaltStore(engine),
        CandleStore(engine),
        (("XAUUSD", True), ("BTCUSD", True)),
        lambda: datetime(2026, 10, 8, tzinfo=UTC),
    )


def test_the_agent_loop_registers_the_click_handler(tmp_path: Any) -> None:
    """Exactly the adapter's pair: one text handler, one button handler, no local copy."""
    settings = _settings(tmp_path)
    upgrade(settings.database_url.get_secret_value())
    service = _service(settings)

    application = _build_telegram(settings, service)
    handlers = application.handlers[0]
    kinds = [type(handler).__name__ for handler in handlers]

    assert len(handlers) == 2, kinds
    assert "MessageHandler" in kinds
    assert "CallbackQueryHandler" in kinds
    # A tap must land on the adapter that knows how to spell a button and edit the message
    # it came from; a re-implemented closure here is the bug this test exists to catch.
    assert {handler.callback.__module__ for handler in handlers} == {
        "tradingagent.notify.telegram_app"
    }


def test_the_agent_loop_asks_telegram_for_button_clicks(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`start_polling` is called with the adapter's update list, callback queries included."""
    application = _FakeApplication()
    components = SimpleNamespace(
        loop=_FakeLoop(),
        application=application,
        market=_FakeMarket(),
        engine=_FakeEngine(),
    )

    async def fake_build(*_: Any, **__: Any) -> Any:
        return components

    # Only the agent's construction is replaced: `run` itself, including its polling call,
    # is the code under test.
    monkeypatch.setattr(app_module, "build", fake_build)
    exit_code = asyncio.run(app_module.run(_settings(tmp_path), cycles=1))

    assert exit_code == 0
    allowed_updates = application.updater.polling["allowed_updates"]
    assert Update.CALLBACK_QUERY in allowed_updates
    assert Update.MESSAGE in allowed_updates
    assert set(allowed_updates) == set(ALLOWED_UPDATES)


class _FakeUpdater:
    def __init__(self) -> None:
        self.polling: dict[str, Any] = {}
        self.stopped = False

    async def start_polling(self, **kwargs: Any) -> None:
        self.polling = kwargs

    async def stop(self) -> None:
        self.stopped = True


class _FakeApplication:
    #: `Application.post_init` carries the menu hook (see `tests/notify/test_bot_menu.py`):
    #: `app.run` runs it between `initialize` and `start`, because PTB only runs it from
    #: `run_polling`. This double has no menu to publish, so it mirrors an empty hook.
    post_init: Any = None

    def __init__(self) -> None:
        self.updater = _FakeUpdater()

    async def initialize(self) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass


class _FakeLoop:
    #: `app.run` reads this to choose its exit code; the double mirrors the real loop.
    restart_requested = False

    async def startup(self) -> tuple[str, ...]:
        return ("XAUUSD",)

    async def run_once(self) -> Any:
        return SimpleNamespace(publications=0, signals=0, closed_positions=0, divergences=0)


class _FakeMarket:
    async def close(self) -> None:
        pass


class _FakeEngine:
    def dispose(self) -> None:
        pass
