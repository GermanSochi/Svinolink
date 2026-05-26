from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, StateFilter
from aiogram.types import FSInputFile, Message

import ai_quota
from config import settings
from deps import gpt, store
from message_urls import message_has_instagram_link, url_from_message

logger = logging.getLogger(__name__)
router = Router(name="chat_handlers")

CAPTION = "Svinolink любит донаты"
TELEGRAM_MAX_BYTES = 52_428_800
_SVIN_LIMIT_MSG = (
    "🐷 Хватит дрочить свинью! У тебя закончился лимит: "
    "доступно только 2 вопроса в час."
)

SVIN_AI_FILTER = (
    StateFilter(None),
    F.text,
    ~F.text.startswith("/"),
    F.chat.type.in_({"group", "supergroup"}),
    ~F.text.regexp(r"(?i)instagram\.com"),
    F.text.regexp(r"(?i)(свин|свинья)"),
)


class InstagramAnyFilter(BaseFilter):
    """Любое сообщение с instagram.com в тексте, подписи или entity."""

    async def __call__(self, message: Message) -> bool:
        blob = (message.text or "") + " " + (message.caption or "")
        if "instagram.com" in blob.lower():
            return True
        return message_has_instagram_link(message)


IG_LINK_FILTER = InstagramAnyFilter()


def is_admin_user(user_id: int, username: str | None) -> bool:
    if user_id in settings.admin_ids:
        return True
    if username and username.lower().lstrip("@") in settings.admin_usernames:
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

        from instagram_download import DOWNLOAD_TOTAL_TIMEOUT_SEC, download_instagram_video, remove_file
        from instagram_urls import is_instagram_media_url

        clean_url = url_from_message(message)
        if not clean_url:
            raise ValueError("не удалось вытащить ссылку Instagram из сообщения")
        if not is_instagram_media_url(clean_url):
            raise ValueError(
                "нужна ссылка на Reel или пост (/reel/ или /p/), а не просто instagram.com"
            )

        logger.info("IG clean_url=%s", clean_url)
        file_path = await asyncio.wait_for(
            asyncio.to_thread(download_instagram_video, clean_url),
            timeout=DOWNLOAD_TOTAL_TIMEOUT_SEC,
        )

        size = os.path.getsize(file_path)
        if size > TELEGRAM_MAX_BYTES:
            remove_file(file_path)
            file_path = None
            await message.answer(
                "❌ Ошибка: Видео весит более 50 МБ. "
                "Telegram запрещает ботам отправлять такие тяжелые файлы."
            )
            return

        max_retries = 2
        for attempt in range(max_retries):
            try:
                await message.answer_video(
                    video=FSInputFile(file_path),
                    caption=CAPTION,
                    reply_to_message_id=message.message_id,
                    supports_streaming=True,
                )
                break
            except Exception as e:
                if "timeout" in str(e).lower() and attempt < max_retries - 1:
                    logger.warning(
                        "telegram upload timeout attempt %s/%s: %s",
                        attempt + 1,
                        max_retries,
                        e,
                    )
                    await asyncio.sleep(2)
                    continue
                raise
    except asyncio.TimeoutError:
        logger.error("instagram download total timeout (%ss)", DOWNLOAD_TOTAL_TIMEOUT_SEC)
        await message.answer(
            "❌ Instagram слишком долго не отвечает. Отправь ссылку ещё раз."
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


async def handle_svin_ai(message: Message, bot: Bot) -> None:
    try:
        if not message.from_user or not message.text:
            return

        uid = message.from_user.id
        logger.info(
            "svin_ai chat=%s user=%s text=%r",
            message.chat.id,
            uid,
            message.text[:200],
        )

        if not ai_quota.can_ask(uid):
            await message.reply(_SVIN_LIMIT_MSG)
            return

        answer = await gpt.reply(message.text)
        ai_quota.record(uid)
        left = ai_quota.remaining(uid)
        await message.reply(f"{answer}\n\n(Осталось вопросов: {left} в час)")
    except Exception as e:
        logger.error("svin_ai error: %s", e, exc_info=True)
        await message.answer(f"❌ Ошибка ИИ (Яндекс): {str(e)}")
