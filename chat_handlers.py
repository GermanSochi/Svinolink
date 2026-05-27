from __future__ import annotations

import asyncio
import logging
import os
import re

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, StateFilter
from aiogram.types import FSInputFile, Message

import ai_quota
from config import settings
from deps import gpt, store
from chat_examples import chat_examples_markdown
from telegram_format import reply_formatted
from chat_queries import is_chat_examples_request
from capabilities import capabilities_markdown, is_capabilities_question
from media_requests import parse_meme_request, parse_video_request
from media_tools import image_to_bytes, make_meme_image, save_bytes
from memory_handlers import RECAP_PATTERN, svin_prompt_with_memory
from message_urls import message_has_instagram_link, url_from_message
from trigger_queries import is_trigger_list_question

logger = logging.getLogger(__name__)
router = Router(name="chat_handlers")

CAPTION = "Svinolink любит донаты"
TELEGRAM_MAX_BYTES = 52_428_800

_bot_id: int | None = None


class SvinInvokeFilter(BaseFilter):
    """Срабатывает на «свин» в тексте или reply на сообщение бота."""

    async def __call__(self, message: Message, bot: Bot) -> bool:
        global _bot_id
        text = message.text or message.caption
        if not text:
            return False
        if re.search(r"(?i)(свин|свинья)", text):
            return True
        replied = message.reply_to_message
        if not replied or not replied.from_user or not replied.from_user.is_bot:
            return False
        if _bot_id is None:
            me = await bot.get_me()
            _bot_id = me.id
        return replied.from_user.id == _bot_id


SVIN_AI_FILTER = (
    StateFilter(None),
    F.text,
    ~F.text.startswith("/"),
    F.chat.type.in_({"group", "supergroup"}),
    ~F.text.regexp(r"(?i)instagram\.com"),
    ~F.text.regexp(RECAP_PATTERN),
    SvinInvokeFilter(),
)

SVIN_CAPTION_FILTER = (
    StateFilter(None),
    F.caption,
    F.chat.type.in_({"group", "supergroup"}),
    ~F.caption.regexp(r"(?i)instagram\.com"),
    SvinInvokeFilter(),
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
        text = message.text or message.caption
        if not message.from_user or not text:
            return

        uid = message.from_user.id
        logger.info(
            "svin_ai chat=%s user=%s text=%r",
            message.chat.id,
            uid,
            text[:200],
        )

        if is_trigger_list_question(text):
            reply = store.triggers_list_markdown(message.chat.id)
            logger.info(
                "trigger_list chat=%s reply_chars=%s",
                message.chat.id,
                len(reply),
            )
            await reply_formatted(message, reply)
            return

        if is_chat_examples_request(text):
            reply = await chat_examples_markdown(message.chat.id)
            logger.info("chat_examples chat=%s", message.chat.id)
            await reply_formatted(message, reply)
            return

        if is_capabilities_question(text):
            await reply_formatted(message, capabilities_markdown())
            return

        meme_text = parse_meme_request(text)
        if meme_text:
            img = make_meme_image(meme_text)
            data = image_to_bytes(img, fmt="PNG")
            path = save_bytes(data, settings.downloads_dir, prefix="meme", ext="png")
            await message.reply_photo(FSInputFile(path))
            return

        video_text = parse_video_request(text)
        if video_text:
            # Без TextClip (нужен ImageMagick). Делаем картинку через PIL и
            # превращаем её в короткий mp4 через ImageClip + ffmpeg.
            from moviepy.editor import ImageClip

            img = make_meme_image(video_text)
            data = image_to_bytes(img, fmt="PNG")
            img_path = save_bytes(data, settings.downloads_dir, prefix="frame", ext="png")
            out_path = settings.downloads_dir / f"vid-{img_path.stem}.mp4"
            clip = ImageClip(str(img_path)).set_duration(3)
            clip.write_videofile(
                str(out_path),
                fps=24,
                codec="libx264",
                audio=False,
                verbose=False,
                logger=None,
            )
            await message.reply_video(FSInputFile(out_path), reply_to_message_id=message.message_id)
            return

        if not ai_quota.can_ask(uid):
            await message.reply(ai_quota.limit_exceeded_message())
            return

        prompt, system = await svin_prompt_with_memory(message.chat.id, text)
        answer = await gpt.reply(prompt, system=system)
        ai_quota.record(uid)
        await reply_formatted(message, answer)
    except Exception as e:
        logger.error("svin_ai error: %s", e, exc_info=True)
        await message.answer(f"❌ Ошибка ИИ (Яндекс): {str(e)}")
