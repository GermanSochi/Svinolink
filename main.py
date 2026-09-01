from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage

from admin_panel import router as admin_router
from chat_handlers import (
    IG_LINK_FILTER,
    handle_ig_text_callback,
    handle_instagram_link,
)
from config import settings
from middleware_log import LogUpdatesMiddleware
from server_runner import run_polling_with_http, run_webhook_mode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("svinolink")


def _build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(LogUpdatesMiddleware())
    dp.message.register(handle_instagram_link, IG_LINK_FILTER)
    dp.callback_query.register(handle_ig_text_callback, F.data.startswith("igtxt:"))
    dp.include_router(admin_router)
    return dp


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("Задай BOT_TOKEN в .env")

    bot = Bot(token=settings.bot_token)
    dp = _build_dispatcher()
    me = await bot.get_me()
    logger.info("Bot @%s render=%s webhook_base=%s", me.username, settings.is_render, settings.webhook_base_url or "(empty)")

    try:
        if settings.webhook_base_url.strip():
            await run_webhook_mode(bot, dp)
        else:
            await run_polling_with_http(bot, dp)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
