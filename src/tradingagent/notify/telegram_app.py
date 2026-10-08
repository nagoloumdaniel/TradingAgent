"""Thin adapter between Telegram and CommandService (TASK-020).

Long polling: the bot fetches its messages, so the server needs no public address.
Run alone for now with `uv run tradingagent-bot`; it joins the agent loop later.

This module is the only place that knows how Telegram spells a button. A command reply
goes out as plain text (no parse mode), while the messages the agent pushes on its own
keep their HTML: mixing the two would print raw tags on the operator's phone.
"""

import logging
import os
import sys
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import truststore
from sqlalchemy import make_url
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from tradingagent.config.errors import ConfigError
from tradingagent.config.redaction import install_secret_redaction
from tradingagent.config.settings import load_bot_settings
from tradingagent.notify.access import AccessGate
from tradingagent.notify.commands import CommandRouter, status_handler
from tradingagent.notify.read_commands import (
    gates_handler,
    market_handler,
    markets_handler,
    performance_handler,
    positions_handler,
    proposals_handler,
    report_handler,
    signals_handler,
)
from tradingagent.notify.replies import Keyboard
from tradingagent.notify.sensitive_commands import (
    CONTROL_FILE_ENV,
    close_all_handler,
    disable_handler,
    emergency_stop_handler,
    enable_handler,
    mode_handler,
    pause_handler,
    restart_handler,
    resume_handler,
    stack_handler,
)
from tradingagent.notify.service import CommandService
from tradingagent.storage.audit import AuditStore
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltStore

log = logging.getLogger(__name__)

# Buttons travel in callback queries: without them here, a tap would reach nobody.
ALLOWED_UPDATES = (Update.MESSAGE, Update.CALLBACK_QUERY)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Coroutine[Any, Any, None]]


def to_markup(keyboard: Keyboard | None) -> InlineKeyboardMarkup | None:
    """Our buttons, in Telegram's spelling — inline only, never a permanent keyboard."""
    if keyboard is None:
        return None
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(button.label, callback_data=button.data) for button in row]
            for row in keyboard.rows
        ]
    )


def message_handler(service: CommandService) -> Handler:
    async def on_message(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        message, user, chat = update.effective_message, update.effective_user, update.effective_chat
        if message is None or user is None or chat is None or not message.text:
            return
        reply = await service.handle(user.id, chat.type == chat.PRIVATE, message.text)
        if reply:
            await message.reply_text(
                reply, reply_markup=to_markup(reply.keyboard), parse_mode=reply.parse_mode
            )

    return on_message


def callback_handler(service: CommandService) -> Handler:
    """A tap, carried by the same service that carries a typed command."""

    async def on_callback(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        user, chat = update.effective_user, update.effective_chat
        if query is None or query.data is None or user is None or chat is None:
            return
        reply = await service.handle_callback(user.id, chat.type == chat.PRIVATE, query.data)
        # An empty acknowledgement releases the spinner and tells the clicker nothing:
        # a stranger gets silence from a button exactly as from a message.
        await query.answer()
        if reply is None:
            return
        markup = to_markup(reply.keyboard)
        message = update.effective_message
        if message is not None:
            try:
                # Editing in place retires the button that was just used, so it cannot be
                # tapped twice, and keeps the conversation to one message per choice.
                await message.edit_text(reply, reply_markup=markup, parse_mode=reply.parse_mode)
                return
            except TelegramError:
                log.info("could not edit the message a button came from, sending a new one")
        await chat.send_message(reply, reply_markup=markup, parse_mode=reply.parse_mode)

    return on_callback


def build_application(token: str, service: CommandService) -> Application:
    application = ApplicationBuilder().token(token).build()
    # Every text message, commands or not: strangers must be seen to be recorded.
    application.add_handler(MessageHandler(filters.TEXT, message_handler(service)))
    application.add_handler(CallbackQueryHandler(callback_handler(service)))
    return application


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its request lines carry the token
    # This machine's TLS chain is only trusted through the Windows certificate store.
    truststore.inject_into_ssl()
    try:
        settings = load_bot_settings(Path(".env"))
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    token = settings.telegram_bot_token.get_secret_value()
    database_url = settings.database_url.get_secret_value()
    install_secret_redaction([token, make_url(database_url).password or token])
    engine = create_database_engine(database_url)
    candles = CandleStore(engine)
    halts = HaltStore(engine)
    # The market list comes from the agent configuration when the loop is wired (TASK-034);
    # until then the read commands answer from the database alone.
    markets: tuple[tuple[str, bool], ...] = ()
    router = CommandRouter()
    router.register(
        "status",
        "mode, arrêt d'urgence et quarantaines",
        status_handler(halts, settings.trading_mode, markets, candles),
    )
    router.register(
        "markets", "marchés suivis et fraîcheur des données", markets_handler(markets, candles)
    )
    router.register(
        "marche",
        "état d'un marché : position, haltes, calendrier",
        market_handler(markets, candles, halts, engine),
    )
    router.register(
        "propositions", "propositions de l'IA et leur décision", proposals_handler(engine)
    )
    router.register("portes", "portes de promotion manquantes", gates_handler(engine))
    router.register("signals", "derniers signaux", signals_handler(engine))
    router.register("positions", "positions ouvertes", positions_handler(engine))
    router.register("performance", "trades clôturés", performance_handler(engine))
    router.register(
        "report", "rapport à la demande", report_handler(engine, lambda: datetime.now(UTC))
    )
    router.register("pause", "suspend les ordres", pause_handler(halts))
    router.register("resume", "reprend les ordres", resume_handler(halts))
    router.register("close_all", "ferme les positions", close_all_handler(halts))
    router.register("emergency_stop", "arrêt d'urgence", emergency_stop_handler(halts))
    router.register("disable", "désactive un marché", disable_handler(halts, markets))
    router.register("enable", "réactive un marché", enable_handler(halts, markets))
    router.register("mode", "change le mode", mode_handler(engine))
    # The standalone bot reads the same two orders, from the same file the supervisor watches.
    # It knows no EA directory — `BotSettings` carries no such field — so it cannot lift a
    # local halt; the command says so rather than pretending, and the agent does the rest.
    control = Path(os.environ[CONTROL_FILE_ENV]) if os.environ.get(CONTROL_FILE_ENV) else None
    router.register("restart", "redémarre l'agent", restart_handler(engine))
    router.register("shutdown", "arrête tout", stack_handler("shutdown", control))
    router.register("restart_all", "redémarre tout", stack_handler("restart_all", control))
    service = CommandService(
        AccessGate(settings.telegram_allowed_user_ids), router, AuditStore(engine)
    )
    log.info("bot started in %s mode", settings.trading_mode)
    try:
        build_application(token, service).run_polling(allowed_updates=list(ALLOWED_UPDATES))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
