"""Thin adapter between Telegram and CommandService (TASK-020).

Long polling: the bot fetches its messages, so the server needs no public address.
Run alone for now with `uv run tradingagent-bot`; it joins the agent loop later.
"""

import logging
import sys
from pathlib import Path

import truststore
from sqlalchemy import make_url
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes, MessageHandler, filters

from tradingagent.config.errors import ConfigError
from tradingagent.config.redaction import install_secret_redaction
from tradingagent.config.settings import load_bot_settings
from tradingagent.notify.access import AccessGate
from tradingagent.notify.commands import CommandRouter, status_handler
from tradingagent.notify.read_commands import (
    markets_handler,
    performance_handler,
    positions_handler,
    signals_handler,
)
from tradingagent.notify.service import CommandService
from tradingagent.storage.audit import AuditStore
from tradingagent.storage.candles import CandleStore
from tradingagent.storage.engine import create_database_engine
from tradingagent.storage.halts import HaltStore

log = logging.getLogger(__name__)


def build_application(token: str, service: CommandService) -> Application:
    application = ApplicationBuilder().token(token).build()

    async def on_message(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        message, user, chat = update.effective_message, update.effective_user, update.effective_chat
        if message is None or user is None or chat is None or not message.text:
            return
        reply = await service.handle(user.id, chat.type == chat.PRIVATE, message.text)
        if reply:
            await message.reply_text(reply)

    # Every text message, commands or not: strangers must be seen to be recorded.
    application.add_handler(MessageHandler(filters.TEXT, on_message))
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
    # The market list comes from the agent configuration when the loop is wired (TASK-034);
    # until then the read commands answer from the database alone.
    markets: tuple[tuple[str, bool], ...] = ()
    router = CommandRouter()
    router.register(
        "status",
        "mode, arrêt d'urgence et quarantaines",
        status_handler(HaltStore(engine), settings.trading_mode, markets, candles),
    )
    router.register(
        "markets", "marchés suivis et fraîcheur des données", markets_handler(markets, candles)
    )
    router.register("signals", "derniers signaux", signals_handler(engine))
    router.register("positions", "positions ouvertes", positions_handler(engine))
    router.register("performance", "trades clôturés", performance_handler(engine))
    service = CommandService(
        AccessGate(settings.telegram_allowed_user_ids), router, AuditStore(engine)
    )
    log.info("bot started in %s mode", settings.trading_mode)
    try:
        build_application(token, service).run_polling(allowed_updates=[Update.MESSAGE])
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
