"""Envoie un message de test a l'operateur: confirme que les alertes Telegram arrivent vraiment.

    uv run python scripts/telegram_check.py

Utilise le meme jeton et le meme destinataire que l'agent (premier identifiant autorise).
Ne lit ni n'affiche le jeton. Code de sortie 0 si Telegram a accepte le message, 1 sinon.
"""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from telegram import Bot

from tradingagent.config.settings import load_settings

ROOT = Path(__file__).resolve().parents[1]


async def main() -> int:
    settings = load_settings(ROOT / ".env")
    chat_id = settings.telegram_allowed_user_ids[0]
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    async with Bot(settings.telegram_bot_token.get_secret_value()) as bot:
        try:
            sent = await bot.send_message(
                chat_id=chat_id, text=f"TradingAgent: test d'envoi ({stamp})"
            )
        except Exception as error:  # reseau, jeton revoque, bot bloque
            print(f"ECHEC: {type(error).__name__}: {error}", file=sys.stderr)
            return 1
    print(f"OK: message {sent.message_id} accepte par Telegram")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
