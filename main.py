from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from contextlib import suppress
from pathlib import Path

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from yt_dlp import YoutubeDL

from config import settings
from store import TriggerStore
from yandex_gpt import YandexGPT, YandexGPTError

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

store = TriggerStore()
gpt = YandexGPT()


def _is_private(message: Message) -> bool:
    return message.chat.type == "private"


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


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
        return
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
    # В личке триггеры тоже для теста; в группе — основной режим
    if message.text.startswith("/"):
        return

    rules = store.load_triggers()
    rule = store.find_match(message.text, rules)
    if not rule:
        return

    uid = message.from_user.id
    cid = message.chat.id

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
    if not _is_private(message):
        return
    text = (
        "🐷 <b>Svinolink</b>\n\n"
        "<b>Группа:</b> кидай ссылку IG Reels/пост или YouTube Shorts — пришлю видео (молча).\n"
        "Триггеры: <code>да</code> → пизда (1 раз/сутки), "
        "<code>300</code> / <code>триста</code> / <code>стристо</code> → отсоси у тракториста.\n\n"
        "<b>Личка (тест):</b> всё то же + команды:\n"
        "/triggers — список\n"
        "/addtrigger слово ответ [daily] — добавить (админ)\n"
        "/deltrigger id — удалить\n"
        "/ai текст — проверка Yandex GPT\n\n"
        "⚠️ В группе включи у @BotFather: /setprivacy → Disable "
        "(иначе бот не видит «да» без упоминания)."
    )
    await bot.send_message(message.chat.id, text, parse_mode="HTML")


async def cmd_triggers(message: Message, bot: Bot) -> None:
    if not _is_private(message):
        return
    rules = store.load_triggers()
    if not rules:
        await message.answer("Триггеров нет. triggers.json пуст.")
        return
    lines = ["<b>Триггеры:</b>"]
    for r in rules:
        daily = "1/день" if r.once_per_day else "без лимита"
        words = ", ".join(r.words)
        lines.append(f"• <code>{r.id}</code>: [{words}] → {r.response} ({daily})")
    await message.answer("\n".join(lines), parse_mode="HTML")


async def cmd_addtrigger(message: Message, bot: Bot) -> None:
    if not message.from_user or not _is_private(message):
        return
    if not _is_admin(message.from_user.id):
        await message.answer("Только для админа. Задай ADMIN_IDS в .env")
        return
    parts = (message.text or "").split(maxsplit=3)
    if len(parts) < 3:
        await message.answer(
            "Формат: /addtrigger слово ответ\n"
            "Или: /addtrigger слово ответ daily"
        )
        return
    word = parts[1]
    if len(parts) >= 4 and parts[-1].lower() == "daily":
        response = parts[2]
        daily = True
    else:
        response = " ".join(parts[2:])
        daily = False
    rid = store.add_rule(word, response, once_per_day=daily)
    await message.answer(f"Ок: <code>{rid}</code>", parse_mode="HTML")


async def cmd_deltrigger(message: Message, bot: Bot) -> None:
    if not message.from_user or not _is_private(message):
        return
    if not _is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("/deltrigger id")
        return
    tid = parts[1].strip()
    before = store.load_triggers()
    rules = [r for r in before if r.id != tid]
    store.save_triggers(rules)
    await message.answer("Удалено." if len(rules) < len(before) else "Не найдено.")


async def cmd_myid(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return
    await message.answer(f"Твой ID: <code>{message.from_user.id}</code>", parse_mode="HTML")


async def cmd_ai(message: Message, bot: Bot) -> None:
    if not _is_private(message) or not message.text:
        return
    prompt = message.text.split(maxsplit=1)
    if len(prompt) < 2:
        await message.answer("/ai ваш вопрос")
        return
    try:
        text = await gpt.reply(
            prompt[1],
            system="Ты короткий дерзкий бот Svinolink для друзей. Отвечай 1-2 предложения.",
        )
        await message.answer(text)
    except YandexGPTError as exc:
        await message.answer(str(exc))


def _build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_triggers, Command("triggers"))
    dp.message.register(cmd_addtrigger, Command("addtrigger"))
    dp.message.register(cmd_deltrigger, Command("deltrigger"))
    dp.message.register(cmd_ai, Command("ai"))
    dp.message.register(cmd_myid, Command("myid"))
    dp.message.register(handle_triggers, F.text)
    dp.message.register(handle_link, F.text)
    return dp


async def _run_polling(bot: Bot, dp: Dispatcher) -> None:
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    logger.info("Polling as @%s", me.username)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


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
            allowed_updates=dp.resolve_used_update_types(),
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
