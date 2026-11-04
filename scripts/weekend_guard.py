"""Garde du week-end, appelee par le superviseur et par la tache planifiee.

    uv run python scripts/weekend_guard.py check    # code 0 = week-end (tout doit rester eteint)
    uv run python scripts/weekend_guard.py notify   # envoie l'avis Telegram « marche ferme »

`check` n'ecrit rien et ne touche pas au reseau: code 10 = ouvert, code 0 = ferme.
`notify` n'envoie qu'un avis par week-end (marqueur dans logs/), code 0 meme s'il est deja parti.
"""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from tradingagent.data.weekend import WeekendWindow

ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / "logs" / "weekend_notified.txt"
OPEN_CODE = 10


async def _send(text: str) -> None:
    from telegram import Bot

    from tradingagent.config.settings import load_settings

    settings = load_settings(ROOT / ".env")
    async with Bot(settings.telegram_bot_token.get_secret_value()) as bot:
        for chat_id in settings.telegram_allowed_user_ids[:1]:
            await bot.send_message(chat_id=chat_id, text=text)


def main(argv: list[str], now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    window = WeekendWindow()
    command = argv[0] if argv else "check"
    if command == "check":
        return 0 if window.contains(now) else OPEN_CODE
    if command != "notify":
        print(f"commande inconnue: {command}", file=sys.stderr)
        return 2
    key = window.week_key(now)
    if MARKER.exists() and MARKER.read_text(encoding="utf-8").strip() == key:
        return 0
    reopening = window.reopening(now).strftime("%d/%m %H:%M UTC")
    text = (
        "Marché fermé pour le week-end : l'application et tous ses processus "
        f"(agent, tableau de bord, MetaTrader 5) sont arrêtés. Reprise dimanche, {reopening}."
    )
    try:
        asyncio.run(_send(text))
    except Exception as error:  # reseau, jeton revoque: l'arret ne doit pas en dependre
        print(f"ECHEC: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(key, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
