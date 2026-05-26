from __future__ import annotations

import asyncio
import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, StateFilter
from aiogram.types import FSInputFile, Message

from config import settings
from deps import gpt, store
from downloader import cleanup_paths, download_to_temp_mp4
from message_urls import message_has_instagram_link, url_from_message
from game_store import GameStore
from riddle_ai import start_riddle_flow, try_solve_riddle
from yandex_gpt import YandexGPTError

logger = logging.getLogger(__name__)
router = Router(name="chat_handlers")

CAPTION = "Svinolink любит донаты"
_WAKE_RE = re.compile(
    r"(?i)(@svinolink_bot|svinolink|свинолинк|свино\s*линк|свин\b|свино\b)"
)


class InstagramLinkFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message_has_instagram_link(message)


game = GameStore()


def is_admin_user(user_id: int, username: str | None) -> bool:
    if user_id in settings.admin_ids:
        return True
    if username and username.lower().lstrip("@") in settings.admin_usernames:
        return True
    return False


def is_wake_message(message: Message) -> bool:
    text = message.text or message.caption or ""
    if _WAKE_RE.search(text):
        return True
    if message.entities and message.from_user:
        for ent in message.entities:
            if ent.type == "mention" and "svinolink" in text.lower():
                return True
    return False


@router.message(StateFilter(None), InstagramLinkFilter())
async def handle_instagram_link(message: Message, bot: Bot) -> None:
    text = message.text or message.caption or ""
    logger.info(
        "instagram_handler chat=%s type=%s text=%r",
        message.chat.id,
        message.chat.type,
        text[:200],
    )

    await message.answer("Сек...")

    url = url_from_message(message)
    if not url:
        err = "не удалось вытащить ссылку Instagram"
        logger.error("%s text=%r", err, text[:300])
        await message.answer(f"Ошибка при обработке ссылки: {err}")
        return

    logger.info("IG url=%s", url[:160])
    file_path = None
    try:
        file_path = await asyncio.to_thread(download_to_temp_mp4, url)
        await bot.send_video(
            chat_id=message.chat.id,
            video=FSInputFile(file_path),
            caption=CAPTION,
            reply_to_message_id=message.message_id,
            supports_streaming=True,
        )
    except Exception as e:
        logger.error("instagram download failed url=%s: %s", url, e, exc_info=True)
        await message.answer(f"Ошибка при обработке ссылки: {str(e)}")
    finally:
        if file_path:
            cleanup_paths(file_path)


@router.message(StateFilter(None), F.text)
async def handle_wake(message: Message, bot: Bot) -> None:
    if message_has_instagram_link(message):
        return
    if not is_wake_message(message):
        return
    if not message.from_user:
        return
    try:
        text = await start_riddle_flow(
            message.chat.id, message.from_user.id, game
        )
        await message.reply(text)
    except YandexGPTError:
        await message.reply(
            "На связи Svinolink. Кидай ссылку на Instagram Reel — пришлю видео."
        )


@router.message(StateFilter(None), F.text)
async def handle_riddle_answer(message: Message, bot: Bot) -> None:
    if not message.from_user or not message.text:
        return
    if message.text.startswith("/"):
        return
    if message_has_instagram_link(message):
        return
    if is_wake_message(message):
        return

    uid = message.from_user.id
    cid = message.chat.id

    reply = await try_solve_riddle(cid, uid, message.text, game)
    if reply:
        await message.reply(reply)
        return

    if game.is_unlocked(cid, uid) and not store.find_match(
        message.text, store.load_triggers(cid)
    ):
        if game.questions_left(cid, uid) <= 0:
            await message.reply("Лимит: 2 вопроса в час. Потом снова или кидай Reels.")
            return
        try:
            answer = await gpt.reply(
                message.text,
                system=(
                    "Ты Svinolink — дерзкий бот в мужском чате друзей. "
                    "Отвечай коротко, по делу, можно мат. Не упоминай код и админов."
                ),
            )
            game.use_question(cid, uid)
            left = game.questions_left(cid, uid)
            await message.reply(f"{answer}\n\n(осталось {left}/2 вопроса в час)")
        except YandexGPTError:
            await message.reply("Сейчас не могу ответить. Кидай Reels — видео скину.")
