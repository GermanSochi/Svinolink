from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from contextlib import suppress
from pathlib import Path

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from yt_dlp import YoutubeDL

from config import settings
from deps import gpt, store
from trigger_fsm import PRIVATE_GREET, router as trigger_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("svinolink")

CAPTION = "Svinolink любит донаты"

_IG_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|p)/[A-Za-z0-9_-]+",
    re.IGNORECASE,
)
_YT_SHORTS_RE = re.compile(
    r"https?://(?:www\.)?(?:m\.)?youtube\.com/shorts/[A-Za-z0-9_-]+",
    re.IGNORECASE,
)


def _extract_supported_url(text: str) -> str | None:
    for m in re.finditer(r"https?://\S+", text):
        url = m.group(0).strip("()[]<>.,!?:;\"'")
        if _IG_RE.search(url) or _YT_SHORTS_RE.search(url):
            return url
    return None


def _download_to_temp_mp4(url: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
    tmp_path = Path(tmp.name)
    tmp.close()

    ydl_opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "outtmpl": str(tmp_path.with_suffix(".%(ext)s")),
        "noplaylist": True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = ydl.prepare_filename(info)
        final_path = Path(downloaded)

    if final_path.suffix.lower() != ".mp4":
        merged = final_path.with_suffix(".mp4")
        if merged.is_file():
            final_path = merged
        elif tmp_path.with_suffix(".mp4").is_file():
            final_path = tmp_path.with_suffix(".mp4")

    return final_path if final_path.is_file() else tmp_path


async def handle_link(message: Message, bot: Bot) -> None:
    if not message.text or not message.from_user:
        return
    url = _extract_supported_url(message.text)
    if not url:
        return

    file_path: Path | None = None
    try:
        file_path = await asyncio.to_thread(_download_to_temp_mp4, url)
        if not file_path or not file_path.exists():
            return
        await bot.send_video(
            chat_id=message.chat.id,
            video=FSInputFile(file_path),
            caption=CAPTION,
            reply_to_message_id=message.message_id,
            supports_streaming=True,
        )
    except Exception as exc:
        logger.warning("download failed: %s", exc)
    finally:
        if file_path:
            with suppress(Exception):
                file_path.unlink(missing_ok=True)
                for p in file_path.parent.glob(f"{file_path.stem}*"):
                    if p.is_file():
                        p.unlink(missing_ok=True)


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
    dp.include_router(trigger_router)

    dp.message.register(cmd_start, CommandStart())
    # FSM и команды тригеров — в trigger_router (раньше общего текста)
    dp.message.register(handle_triggers, StateFilter(None), F.text)
    dp.message.register(handle_link, StateFilter(None), F.text)
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
