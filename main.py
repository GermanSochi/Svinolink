from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message

from admin_panel import router as admin_router
from chat_handlers import router as chat_router
from config import settings
from deps import gpt, store
from middleware_log import LogUpdatesMiddleware
from server_runner import run_polling_with_http, run_webhook_mode
from trigger_fsm import PRIVATE_GREET, router as trigger_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("svinolink")


async def handle_triggers(message: Message, bot: Bot) -> None:
    if not message.text or not message.from_user:
        return
    if message.text.startswith("/"):
        return

    cid = message.chat.id
    rules = store.load_triggers(cid)
    rule = store.find_match(message.text, rules)
    if not rule:
        return

    uid = message.from_user.id
    if rule.once_per_day and store.was_used_today(cid, uid, rule.id):
        return

    try:
        await bot.send_message(
            cid,
            rule.response,
            reply_to_message_id=message.message_id,
        )
        if rule.once_per_day:
            store.mark_used_today(cid, uid, rule.id)
    except Exception as exc:
        logger.warning("trigger send failed: %s", exc)


async def cmd_start(message: Message, bot: Bot) -> None:
    from bot_miniapp import miniapp_keyboard
    from trigger_fsm import GROUP_GREET

    if message.chat.type == "private":
        await bot.send_message(message.chat.id, PRIVATE_GREET)
        return
    kb = miniapp_keyboard(message.chat.id) if settings.miniapp_url else None
    short = GROUP_GREET.split("\n\n@BotFather")[0]
    await bot.send_message(message.chat.id, short, reply_markup=kb)


def _build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.middleware(LogUpdatesMiddleware())
    dp.include_router(admin_router)
    dp.include_router(trigger_router)
    dp.include_router(chat_router)

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(handle_triggers, StateFilter(None), F.text)
    return dp


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("Задай BOT_TOKEN в .env")

    if settings.is_render and not settings.webhook_base_url.strip():
        logger.error("На Render нужен WEBHOOK_BASE_URL=https://<service>.onrender.com (без /)")
        sys.exit(1)

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
        await gpt.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
