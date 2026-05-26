from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from admin_panel import router as admin_router
from chat_handlers import router as chat_router
from config import settings
from deps import gpt, store
from trigger_fsm import PRIVATE_GREET, router as trigger_router

logging.basicConfig(level=logging.INFO)
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
    if message.chat.type != "private":
        return
    await bot.send_message(message.chat.id, PRIVATE_GREET)


def _build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)
    dp.include_router(trigger_router)
    dp.include_router(chat_router)

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(handle_triggers, StateFilter(None), F.text)
    return dp


async def _run_polling(bot: Bot, dp: Dispatcher) -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    logger.info("Polling as @%s", me.username)
    await dp.start_polling(
        bot,
        allowed_updates=["message", "my_chat_member"],
    )


async def _run_webhook(bot: Bot, dp: Dispatcher) -> None:
    base_url = settings.webhook_base_url.rstrip("/")
    path_secret = settings.webhook_path.strip().lstrip("/") or bot.token
    webhook_path = f"/{path_secret}"

    app = web.Application()

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=webhook_path)
    setup_application(app, dp, bot=bot)

    async def on_startup(_: web.Application) -> None:
        await bot.set_webhook(
            url=f"{base_url}{webhook_path}",
            drop_pending_updates=True,
            allowed_updates=["message", "my_chat_member"],
        )
        logger.info("Webhook: %s%s", base_url, webhook_path)

    async def on_shutdown(_: web.Application) -> None:
        with suppress(Exception):
            await bot.delete_webhook(drop_pending_updates=False)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()
    while True:
        await asyncio.sleep(3600)


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("Задай BOT_TOKEN в .env")

    bot = Bot(token=settings.bot_token)
    dp = _build_dispatcher()

    try:
        if settings.webhook_base_url.strip():
            await _run_webhook(bot, dp)
        else:
            await _run_polling(bot, dp)
    finally:
        await gpt.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
