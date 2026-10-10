"""The Telegram menu: the list « / » proposes, and the button that opens the palette.

Telegram shows two things by itself, from two API calls made once, at startup:

* `set_my_commands` fills the list a client offers as soon as the operator types « / »;
* `set_chat_menu_button(MenuButtonCommands())` puts the persistent button at the bottom left
  of the input field, which opens that very list — so the palette is one tap away instead of
  one command to remember.

Both are built from the `CommandRouter` and from nothing else: a command that is registered
is in the menu, a command that is not cannot be. A second, hand-written list would drift on
the first command added, and the operator would see the palette grow a command that « / »
never proposes.

**The network call is a startup step, never a construction step.** The two builders that
create the application (`telegram_app.build_application`, `app._build_telegram`) install the
hook and publish nothing: python-telegram-bot runs `post_init` after the bot is initialized —
`run_polling` does it for the standalone bot, and `app.run` calls the same hook explicitly for
the agent, because `Application.initialize` and `Application.start` never run it themselves
(verified in python-telegram-bot 22.8). Registering the menu while building the `Application`
would put a network call in the path of every offline test.

**A menu is a convenience; trading is not.** The hook runs before the agent's loop starts, so
a failure — Telegram refusing the list, a network error, a revoked token — is logged in
ERROR with its cause and never propagates: no autocompletion list is worth stopping an agent
that trades, or making its supervisor restart it in a loop. The journal also says what still
works, because a menu that silently does not exist is the defect this module exists to fix.

`aide` is not published: it is not a command to type, it is what a button of the palette
opens. `help` is published, and first: it is what a typed « / » should offer.
"""

import logging
import re
from collections.abc import Callable, Coroutine, Iterator
from typing import Any

from telegram import (
    Bot,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
    MenuButtonCommands,
)
from telegram.ext import Application

from tradingagent.notify.commands import CommandRouter

log = logging.getLogger(__name__)

#: Telegram's own grammar for a command name. It is also the reason a bad name is refused
#: here, before the call: one name Telegram dislikes makes it refuse the *whole* list, and
#: the operator would lose the menu without a word. (`replies._is_addressable` narrows the
#: same alphabet further, for the buttons: a name that is fine here must still be buttonable.)
VALID_NAME = re.compile(r"[a-z0-9_]{1,32}\Z")
MIN_DESCRIPTION = 3
MAX_DESCRIPTION = 256

#: Not a command the operator types: it is what a button of the palette opens.
NOT_TYPED = frozenset({"aide"})
#: The one command that comes first: it is what opens the palette.
FIRST = "help"


def _entries(router: CommandRouter) -> Iterator[tuple[str, str]]:
    """The router's public commands and their registered descriptions, in menu order.

    `help` first, then alphabetical. Registration order would be a trap: the two wiring
    sites (`app._build_command_service`, `tradingagent-bot`) register the same commands in
    different orders, and a list the operator reads must not depend on which binary wrote it.

    `_handlers` is read directly on purpose: `CommandRouter` exposes no public enumeration,
    and this module is its only reader outside the router itself. The alternative — a second
    list of names — is exactly the drift this module is built to make impossible.
    """
    names = sorted(name for name in router._handlers if name not in NOT_TYPED)
    if FIRST in names:
        names.remove(FIRST)
        names.insert(0, FIRST)
    return ((name, router._handlers[name][0]) for name in names)


def command_names(router: CommandRouter) -> tuple[str, ...]:
    """The public names, in the order Telegram will list them."""
    return tuple(name for name, _ in _entries(router))


def _clean(description: str) -> str:
    """One line, single-spaced, at most what Telegram accepts for a description."""
    return " ".join(description.split())[:MAX_DESCRIPTION].rstrip()


def _bot_command(name: str, description: str) -> BotCommand:
    """One entry, or the reason Telegram would refuse it — and the whole list with it."""
    if not VALID_NAME.fullmatch(name):
        raise ValueError(
            f"{name!r} is not a name Telegram accepts: lowercase letters, digits and "
            "underscores, one to 32 of them"
        )
    cleaned = _clean(description)
    if len(cleaned) < MIN_DESCRIPTION:
        raise ValueError(
            f"/{name}: a description Telegram accepts has at least {MIN_DESCRIPTION} characters"
        )
    return BotCommand(name, cleaned)


def bot_commands(router: CommandRouter) -> tuple[BotCommand, ...]:
    """Every public command, ready for `set_my_commands`, or the `ValueError` to fix.

    Strict on purpose: this is the contract, and the tests pin it. The startup path uses
    `_sendable` instead, so that one bad entry costs one line rather than the whole menu.
    """
    return tuple(_bot_command(name, text) for name, text in _entries(router))


def _sendable(router: CommandRouter) -> tuple[BotCommand, ...]:
    """What can be sent: an entry Telegram would refuse is dropped, one by one, loudly."""
    commands: list[BotCommand] = []
    for name, text in _entries(router):
        try:
            commands.append(_bot_command(name, text))
        except ValueError as error:
            log.error(
                "command /%s stays out of the Telegram menu (%s): the rest of the menu is "
                "published, and /help still lists the palette",
                name,
                error,
            )
    return tuple(commands)


async def publish_menu(bot: Bot, router: CommandRouter) -> None:
    """The two calls that make « / » and the menu button work, off the trading path.

    A failure is logged with its cause and never propagates: the hook runs before the
    agent's loop starts, so letting it out would stop an agent that trades for a missing
    autocompletion list. The message says what still works, because the operator reads it.
    """
    try:
        commands = _sendable(router)
        # Both scopes, and that is not belt-and-braces: Telegram keeps a *separate* list per
        # scope and picks the most specific one that applies. A client asking without a scope
        # — which is what `get_my_commands()` does by default — reads only the default scope,
        # and a list set solely on `AllPrivateChats` leaves it empty. Measured on the real API
        # on 2026-10-10: `AllPrivateChats` alone answered 20 entries, `Default` alone answered
        # 0. The operator's autocomplete must not depend on the scope a client happens to read.
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        # The native Menu button, bottom left of the input field. `MenuButtonCommands` is
        # the button that opens the command list above; a `MenuButtonWebApp` would need a
        # web app to host, and `MenuButtonDefault` is what we are replacing.
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as error:
        log.exception(
            "the Telegram menu could not be registered (%s): the « / » list and the menu "
            "button are missing, /help still lists the palette, and the agent keeps running",
            error,
        )


def menu_post_init(router: CommandRouter) -> Callable[[Application], Coroutine[Any, Any, None]]:
    """The hook `ApplicationBuilder.post_init` expects, holding the router it publishes.

    python-telegram-bot 22.8 runs it from `run_polling` and `run_webhook` only: neither
    `initialize` nor `start` calls it. The agent starts its application by hand, so it runs
    this same hook between the two — one definition of what to publish, two ways to reach it.
    """

    async def post_init(application: Application) -> None:
        await publish_menu(application.bot, router)

    return post_init
