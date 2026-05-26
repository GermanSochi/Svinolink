from __future__ import annotations

import asyncio
import logging
import os
import re

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.types import FSInputFile, Message

from config import settings
from deps import gpt, store
from message_urls import message_has_instagram_link, url_from_message
from game_store import GameStore
from riddle_ai import start_riddle_flow, try_solve_riddle
from yandex_gpt import YandexGPTError

logger = logging.getLogger(__name__)
router = Router(name="chat_handlers")

CAPTION = "Svinolink любит донаты"
TELEGRAM_MAX_BYTES = 52_428_800
_WAKE_RE = re.compile(
    r"(?i)(@svinolink_bot|svinolink|свинолинк|свино\s*линк|свин\b|свино\b)"
)


class InstagramAnyFilter(BaseFilter):
    """Любое сообщение с instagram.com в тексте, подписи или entity."""

    async def __call__(self, message: Message) -> bool:
        blob = (message.text or "") + " " + (message.caption or "")
        if "instagram.com" in blob.lower():
            return True
        return message_has_instagram_link(message)


IG_LINK_FILTER = InstagramAnyFilter()

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


async def handle_instagram_link(message: Message, bot: Bot) -> None:
    await message.answer("Сек...")

    file_path = None
    try:
        text = message.text or message.caption or ""
        print(f"ПОЛУЧЕНО СООБЩЕНИЕ ИЗ ГРУППЫ: {text}")
        logger.info(
            "instagram_handler chat=%s type=%s text=%r",
            message.chat.id,
            message.chat.type,
            text[:200],
        )

        store.register_chat(
            message.chat.id,
            title=message.chat.title,
            chat_type=message.chat.type,
        )

        from instagram_download import download_instagram_video, remove_file
        from instagram_urls import is_instagram_media_url

        clean_url = url_from_message(message)
        if not clean_url:
            raise ValueError("не удалось вытащить ссылку Instagram из сообщения")
        if not is_instagram_media_url(clean_url):
            raise ValueError(
                "нужна ссылка на Reel или пост (/reel/ или /p/), а не просто instagram.com"
            )

        logger.info("IG clean_url=%s", clean_url)
        file_path = await asyncio.to_thread(download_instagram_video, clean_url)

        size = os.path.getsize(file_path)
        if size > TELEGRAM_MAX_BYTES:
            remove_file(file_path)
            file_path = None
            await message.answer(
                "❌ Ошибка: Видео весит более 50 МБ. "
                "Telegram запрещает ботам отправлять такие тяжелые файлы."
            )
            return

        await message.answer_video(
            video=FSInputFile(file_path),
            caption=CAPTION,
            reply_to_message_id=message.message_id,
            supports_streaming=True,
        )
    except Exception as e:
        logger.error("instagram handler error: %s", e, exc_info=True)
        err_text = str(e)
        if not err_text.startswith("❌"):
            err_text = f"❌ Ошибка в коде бота: {err_text}"
        await message.answer(err_text)
    finally:
        if file_path is not None:
            try:
                from instagram_download import remove_file

                remove_file(file_path)
            except Exception:
                pass


@router.message(StateFilter(None), F.text, ~Command())
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


@router.message(StateFilter(None), F.text, ~Command())
async def handle_riddle_answer(message: Message, bot: Bot) -> None:
    if not message.from_user or not message.text:
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
