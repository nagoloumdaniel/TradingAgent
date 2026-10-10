"""The menu Telegram shows by itself: what « / » proposes, and the button that opens it.

The operator asked for two things that live in the same place: typing « / » in the chat
should propose the commands, and a persistent button at the bottom left of the input field
should open the palette — instead of having to know `/help` by heart.

Written before the implementation. Four promises are checked here.

  * *The menu is the router, never a second list.* A command registered in `CommandRouter`
    appears in the menu, a command that is not registered cannot; adding a command to the
    production router without reviewing the menu fails a test right here.
  * *Telegram's grammar is enforced before the call.* One name Telegram refuses makes it
    refuse the whole list, silently for the operator: nothing invalid is ever sent, and the
    valid commands are still published.
  * *Nothing reaches the network while the `Application` is built.* The 2649 tests of this
    repository build Telegram applications offline; registering the menu is a startup step
    (python-telegram-bot's `post_init`), never a side effect of construction.
  * *A failure is loud and never fatal.* The hook runs before the trading loop starts: a
    menu that cannot be published must leave a reason in the journal, and leave the agent
    trading.

No test in this file opens a socket: the menu is built from the router and handed to a bot
double, and the real `Application` built here has its two API calls replaced.
"""

import asyncio
import logging
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from telegram import (
    Bot,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
    MenuButtonCommands,
)
from telegram.error import TelegramError

from tradingagent import app as app_module
from tradingagent.app import _build_command_service, _build_telegram
from tradingagent.config.settings import Settings
from tradingagent.notify.bot_menu import (
    MAX_DESCRIPTION,
    bot_commands,
    command_names,
    publish_menu,
)
from tradingagent.notify.commands import CommandRequest, CommandRouter
from tradingagent.notify.service import CommandService
from tradingagent.notify.telegram_app import build_application
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltStore

TOKEN = "123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawE"  # pragma: allowlist secret
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

#: What the agent registers today (`app._build_command_service`), `aide` excluded: this is
#: the exact text of the list Telegram shows under « / », `help` first, then alphabetical.
PRODUCTION_MENU = (
    "help",
    "close_all",
    "disable",
    "emergency_stop",
    "enable",
    "marche",
    "markets",
    "mode",
    "pause",
    "performance",
    "portes",
    "positions",
    "propositions",
    "report",
    "restart",
    "restart_all",
    "resume",
    "shutdown",
    "signals",
    "status",
)

#: Telegram's own grammar, restated here rather than imported: a test that borrows the
#: implementation's regex would pass whatever the implementation decides to accept.
TELEGRAM_NAME = re.compile(r"[a-z0-9_]{1,32}\Z")


async def _noop(_: CommandRequest) -> str:
    return "ok"


class _SpyBot:
    """A bot that records instead of sending: enough to prove the two calls."""

    def __init__(self, *, failing: str | None = None) -> None:
        self.calls: list[str] = []
        self.commands: tuple[BotCommand, ...] | None = None
        self.scope: Any = "unset"
        self.scopes: list[Any] = []
        self.language_code: Any = "unset"
        self.menu_button: Any = "unset"
        self._failing = failing

    async def set_my_commands(self, commands: Any, scope: Any = None, language_code: Any = None):
        self.calls.append("set_my_commands")
        self.scopes.append(scope)
        if self._failing == "commands":
            raise TelegramError("Bad Request: command list refused")
        self.commands = tuple(commands)
        self.scope, self.language_code = scope, language_code
        return True

    async def set_chat_menu_button(self, chat_id: Any = None, menu_button: Any = None) -> bool:
        self.calls.append("set_chat_menu_button")
        if self._failing == "button":
            raise TelegramError("Bad Request: menu button refused")
        self.menu_button = menu_button
        return True


def _router(*names: str) -> CommandRouter:
    router = CommandRouter()
    for name in names:
        router.register(name, f"ce que fait /{name}", _noop)
    return router


def _settings(tmp_path: Any) -> Settings:
    return Settings(
        mt5_login=40123456,
        mt5_server="Deriv-Demo",
        mt5_password="investor-secret",  # pragma: allowlist secret
        telegram_bot_token=TOKEN,
        telegram_allowed_user_ids=(42,),
        database_url=f"sqlite:///{tmp_path / 'menu.db'}",  # pragma: allowlist secret
    )


def _production_service(settings: Settings) -> CommandService:
    engine = create_database_engine(settings.database_url.get_secret_value())
    return _build_command_service(
        engine,
        settings,
        HaltStore(engine),
        CandleStore(engine),
        (("XAUUSD", True), ("BTCUSD", True)),
        lambda: NOW,
    )


# --- 1. the menu is the router ------------------------------------------------


def test_the_menu_is_derived_from_the_router() -> None:
    """Every registered command shows up, in a stable order, and `help` comes first."""
    router = _router("status", "mode")

    assert command_names(router) == ("help", "mode", "status")
    assert [command.command for command in bot_commands(router)] == ["help", "mode", "status"]


def test_a_command_added_to_the_router_appears_without_touching_the_menu() -> None:
    """The menu has no list of its own: the router is the only source, in both directions.

    The same wiring, with one command registered and without it: the line appears and
    disappears with the registration. Nothing here knows the name of a command.
    """
    router = _router("status")
    before = command_names(router)

    router.register("nouvelle", "une commande ajoutée", _noop)

    assert before == ("help", "status")
    assert command_names(router) == ("help", "nouvelle", "status")
    assert "nouvelle" in [command.command for command in bot_commands(router)]
    # And a router that never registered it cannot show it.
    assert "nouvelle" not in command_names(_router("status"))


def test_the_menu_carries_the_registered_description() -> None:
    """What the operator reads under « / » is what the router registered, not a second text."""
    router = _router("status")
    router.register("bavarde", "un texte enregistré avec la commande", _noop)

    shown = {command.command: command.description for command in bot_commands(router)}

    assert shown["bavarde"] == "un texte enregistré avec la commande"
    assert shown["status"] == "ce que fait /status"


def test_aide_is_not_a_command_the_operator_types() -> None:
    """`aide` is what a palette button opens; `help` is the one that must stay typable."""
    names = command_names(_router("status"))

    assert "aide" not in names
    assert "help" in names


def test_help_is_first_because_it_is_what_opens_the_palette() -> None:
    names = command_names(_router("zzz", "aaa"))

    assert names[0] == "help"
    assert names[1:] == tuple(sorted(("aaa", "zzz")))


def test_the_production_menu_lists_exactly_the_public_commands(tmp_path: Any) -> None:
    """A command added to the production router must be acknowledged here.

    This is the test that fails when someone registers a command in
    `app._build_command_service` without reviewing the menu: the operator would see the
    palette grow a command that « / » never proposes.
    """
    service = _production_service(_settings(tmp_path))

    assert command_names(service.router) == PRODUCTION_MENU


# --- 2. Telegram's grammar, enforced before the call --------------------------


def test_every_published_name_and_description_fits_telegram_grammar() -> None:
    """Telegram accepts `[a-z0-9_]`, 1 to 32 characters, and descriptions of 3 to 256."""
    commands = bot_commands(_router("status", "close_all", "emergency_stop", "restart_all"))

    for command in commands:
        assert TELEGRAM_NAME.fullmatch(command.command), command.command
        assert 3 <= len(command.description) <= MAX_DESCRIPTION


def test_the_names_telegram_accepts_are_accepted() -> None:
    """The four shapes in production: short, underscored, long, and with a digit."""
    names = tuple(command.command for command in bot_commands(_router("marche", "mode")))

    assert names == ("help", "marche", "mode")
    assert all(TELEGRAM_NAME.fullmatch(name) for name in names)


@pytest.mark.parametrize("bad", ["Close_All", "close all", "closé", "x" * 33, "close-all", ""])
def test_a_name_telegram_would_refuse_raises_instead_of_being_sent(bad: str) -> None:
    """One bad name makes Telegram refuse the whole list, and the menu disappears silently."""
    router = _router("status")
    router.register(bad, "une commande mal nommée", _noop)

    with pytest.raises(ValueError, match="Telegram"):
        bot_commands(router)


def test_a_description_telegram_would_refuse_raises_instead_of_being_sent() -> None:
    """Telegram refuses a description under three characters; the whole list goes with it."""
    router = _router()
    router.register("court", "ok", _noop)

    with pytest.raises(ValueError, match="description"):
        bot_commands(router)


def test_a_long_description_is_cleaned_and_truncated() -> None:
    """A description is one line, and never longer than what Telegram accepts."""
    router = _router()
    router.register("bavarde", "  deux\nlignes   et\tun très long texte  " * 12, _noop)

    (bavarde,) = [command for command in bot_commands(router) if command.command == "bavarde"]

    assert "\n" not in bavarde.description
    assert "\t" not in bavarde.description
    assert "  " not in bavarde.description
    assert bavarde.description == bavarde.description.strip()
    # The cut fills the room Telegram allows; a space left at the edge is trimmed, hence -1.
    assert MAX_DESCRIPTION - 1 <= len(bavarde.description) <= MAX_DESCRIPTION


def test_an_invalid_entry_never_reaches_telegram_and_the_menu_survives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Telegram refuses the whole list for one bad name: the bad line is dropped first.

    Losing one line is a nuisance; losing the menu because of a line nobody can see is the
    defect. The journal names the command that stayed out.
    """
    router = _router("status", "mode")
    router.register("Close_All", "une commande mal nommée", _noop)
    spy = _SpyBot()

    with caplog.at_level(logging.ERROR, logger="tradingagent.notify.bot_menu"):
        asyncio.run(publish_menu(cast(Any, spy), router))

    assert spy.commands is not None, "the menu must still be published"
    assert [command.command for command in spy.commands] == ["help", "mode", "status"]
    assert any(
        record.levelno >= logging.ERROR and "Close_All" in record.getMessage()
        for record in caplog.records
    )


# --- 3. the two calls, off the network ----------------------------------------


def test_publishing_asks_for_both_scopes_and_the_commands_button() -> None:
    """The three calls, with the right arguments, and nothing else."""
    spy = _SpyBot()
    router = _router("status")

    asyncio.run(publish_menu(cast(Any, spy), router))

    assert spy.calls == ["set_my_commands", "set_my_commands", "set_chat_menu_button"]
    # Two scopes, not one: Telegram keeps a separate list per scope and a client that asks
    # without one reads only the default — measured on the real API on 2026-10-10, where
    # `AllPrivateChats` alone answered 20 entries and `Default` alone answered 0.
    assert [type(scope) for scope in spy.scopes] == [
        BotCommandScopeDefault,
        BotCommandScopeAllPrivateChats,
    ]
    assert isinstance(spy.menu_button, MenuButtonCommands)
    # No language is pinned: the operator's client picks its own, and the default applies.
    assert spy.language_code is None
    assert [command.command for command in spy.commands or ()] == list(command_names(router))


def test_publishing_uses_the_sendable_list_not_the_strict_one() -> None:
    """The startup path never raises: a bad entry costs a line, not the whole menu."""
    spy = _SpyBot()
    router = _router("status")
    router.register("court", "ok", _noop)  # a two-character description Telegram refuses

    asyncio.run(publish_menu(cast(Any, spy), router))

    assert spy.calls == ["set_my_commands", "set_my_commands", "set_chat_menu_button"]
    assert [command.command for command in spy.commands or ()] == ["help", "status"]


@pytest.mark.parametrize("broken", ["commands", "button"])
def test_a_publish_failure_is_loud_and_does_not_weigh_on_the_agent(
    broken: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A bot that starts without its menu must say why, and must not stop the agent.

    The hook runs before the trading loop starts, so letting the error out would stop an
    agent that trades perfectly well — for a missing autocompletion list.
    """
    spy = _SpyBot(failing=broken)

    with caplog.at_level(logging.ERROR, logger="tradingagent.notify.bot_menu"):
        asyncio.run(publish_menu(cast(Any, spy), _router("status")))

    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors, "a menu that could not be published must leave a trace"
    # What the journal says is what the operator can act on, not just the technical cause.
    said = " ".join(record.getMessage() for record in errors)
    assert "menu" in said and "/help" in said
    assert any(record.exc_info is not None for record in errors), "and the cause with it"


# --- 4. wired at startup, never at construction -------------------------------


def test_building_the_agent_application_touches_no_network(tmp_path: Any, monkeypatch: Any) -> None:
    """The menu is a startup step, never a side effect of building the application.

    Thousands of tests build this application offline: a call here would break every one of
    them, and in production it would make the agent's own construction depend on Telegram.
    """
    calls: list[str] = []

    async def forbidden(*_: Any, **__: Any) -> bool:
        calls.append("called")
        raise AssertionError("the menu must not be published while the application is built")

    monkeypatch.setattr(Bot, "set_my_commands", forbidden)
    monkeypatch.setattr(Bot, "set_chat_menu_button", forbidden)

    settings = _settings(tmp_path)
    application = _build_telegram(settings, _production_service(settings))

    assert calls == []
    assert application.post_init is not None, "the agent's application installs no menu hook"


def test_building_the_standalone_bot_touches_no_network(tmp_path: Any, monkeypatch: Any) -> None:
    """`tradingagent-bot` builds the same application: same rule, same hook."""

    async def forbidden(*_: Any, **__: Any) -> bool:
        raise AssertionError("the menu must not be published while the application is built")

    monkeypatch.setattr(Bot, "set_my_commands", forbidden)
    monkeypatch.setattr(Bot, "set_chat_menu_button", forbidden)

    settings = _settings(tmp_path)
    application = build_application(TOKEN, _production_service(settings))

    assert application.post_init is not None


def test_the_hook_publishes_the_production_menu_when_it_runs(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """`post_init` is the wiring point: when python-telegram-bot runs it, the menu goes out."""
    sent: dict[str, Any] = {}
    scopes: list[Any] = []

    async def record_commands(self: Any, commands: Any, scope: Any = None, **_kwargs: Any) -> bool:
        sent["commands"] = tuple(commands)
        sent["scope"] = scope
        scopes.append(scope)
        return True

    async def record_button(
        self: Any, chat_id: Any = None, menu_button: Any = None, **_kwargs: Any
    ) -> bool:
        sent["menu_button"] = menu_button
        return True

    monkeypatch.setattr(Bot, "set_my_commands", record_commands)
    monkeypatch.setattr(Bot, "set_chat_menu_button", record_button)

    settings = _settings(tmp_path)
    application = _build_telegram(settings, _production_service(settings))
    hook = application.post_init
    assert hook is not None

    asyncio.run(hook(application))

    assert [type(scope) for scope in scopes] == [
        BotCommandScopeDefault,
        BotCommandScopeAllPrivateChats,
    ]
    assert isinstance(sent["menu_button"], MenuButtonCommands)
    assert [command.command for command in sent["commands"]] == list(PRODUCTION_MENU)


def test_the_agent_runs_the_hook_between_initialize_and_start(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """PTB only runs `post_init` from `run_polling`; the agent starts the application by hand.

    Verified in python-telegram-bot 22.8: `Application.initialize` and `Application.start`
    never call `post_init` (`ext/_application.py`), so an agent that ignored the hook would
    run a bot with no menu at all. The order matters too: `initialize` prepares the bot.
    """
    order: list[str] = []

    class _Application:
        post_init: Any = None  # replaced once the hook is known, as the builder does

        def __init__(self) -> None:
            self.updater = SimpleNamespace(
                polling={}, start_polling=self._start_polling, stop=self._noop
            )

        async def _noop(self, **_kwargs: Any) -> None:
            order.append("updater.stop")

        async def _start_polling(self, **kwargs: Any) -> None:
            self.updater.polling = kwargs
            order.append("start_polling")

        async def initialize(self) -> None:
            order.append("initialize")

        async def start(self) -> None:
            order.append("start")

        async def stop(self) -> None:
            order.append("stop")

        async def shutdown(self) -> None:
            order.append("shutdown")

    application = _Application()

    async def hook(_application: Any) -> None:
        order.append("post_init")

    application.post_init = hook

    components = SimpleNamespace(
        loop=SimpleNamespace(restart_requested=False, startup=_startup, run_once=_run_once),
        application=application,
        market=SimpleNamespace(close=_close),
        engine=SimpleNamespace(dispose=lambda: None),
    )

    async def fake_build(*_: Any, **__: Any) -> Any:
        return components

    monkeypatch.setattr(app_module, "build", fake_build)

    assert asyncio.run(app_module.run(_settings(tmp_path), cycles=1)) == 0
    assert order[:3] == ["initialize", "post_init", "start"]


def test_the_agent_survives_a_hook_that_fails(tmp_path: Any, monkeypatch: Any) -> None:
    """The agent's startup must not depend on Telegram answering.

    `publish_menu` already swallows its own failures; this proves the agent does not add a
    second, fatal path of its own around the hook.
    """
    order: list[str] = []

    class _Application:
        post_init: Any = None

        def __init__(self) -> None:
            self.updater = SimpleNamespace(
                polling={}, start_polling=self._start_polling, stop=self._noop
            )

        async def _noop(self, **_kwargs: Any) -> None:
            pass

        async def _start_polling(self, **kwargs: Any) -> None:
            self.updater.polling = kwargs

        async def initialize(self) -> None:
            order.append("initialize")

        async def start(self) -> None:
            order.append("start")

        async def stop(self) -> None:
            pass

        async def shutdown(self) -> None:
            pass

    application = _Application()

    async def hook(_application: Any) -> None:
        # What `publish_menu` returns after logging: no exception, no menu.
        order.append("post_init")

    application.post_init = hook
    components = SimpleNamespace(
        loop=SimpleNamespace(restart_requested=False, startup=_startup, run_once=_run_once),
        application=application,
        market=SimpleNamespace(close=_close),
        engine=SimpleNamespace(dispose=lambda: None),
    )

    async def fake_build(*_: Any, **__: Any) -> Any:
        return components

    monkeypatch.setattr(app_module, "build", fake_build)

    assert asyncio.run(app_module.run(_settings(tmp_path), cycles=1)) == 0
    assert order == ["initialize", "post_init", "start"]


async def _startup() -> tuple[str, ...]:
    return ("XAUUSD",)


async def _run_once() -> Any:
    return SimpleNamespace(publications=0, signals=0, closed_positions=0, divergences=0)


async def _close() -> None:
    pass
