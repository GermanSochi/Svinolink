"""Установить webhook вручную (если Render env уже настроен)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Bot

from config import settings
from server_runner import apply_webhook


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан")
    if not settings.webhook_base_url:
        raise SystemExit("WEBHOOK_BASE_URL не задан (без / на конце)")
    bot = Bot(token=settings.bot_token)
    try:
        url = await apply_webhook(bot)
        print("OK:", url)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
