from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, FSInputFile, Message

from config import settings
from bot_messages import (
    instagram_timeout_message,
    map_instagram_error,
    video_too_heavy_message,
)
from message_urls import message_has_instagram_link, url_from_message

logger = logging.getLogger(__name__)


class InstagramAnyFilter(BaseFilter):
    """\u041b\u044e\u0431\u043e\u0435 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435 \u0441 instagram.com \u0432 \u0442\u0435\u043a\u0441\u0442\u0435, \u043f\u043e\u0434\u043f\u0438\u0441\u0438 \u0438\u043b\u0432 entity."""

    async def __call__(self, message: Message) -> bool:
        blob = (message.text or "") + " " + (message.caption or "")
        if "instagram.com" in blob.lower():
            return True
        return message_has_instagram_link(message)


IG_LINK_FILTER = InstagramAnyFilter()


_ig_caption_cache: dict[str, str] = {}
TELEGRAM_MAX_BYTES = 52_428_800

async def handle_instagram_link(message: Message, bot: Bot) -> None:
    from instagram_download import instagram_user_message
    from bot_stats import bot_stats
    bot_stats.record_message()

    if not settings.instagram_is_active():
        await message.answer(instagram_user_message())
        return

    clean_url: str | None = None
    text = message.text or message.caption or ""
    logger.info(
        "instagram_handler chat=%s type=%s text=%r",
        message.chat.id,
        message.chat.type,
        text[:200],
    )

    from store import TriggerStore
    TriggerStore().register_chat(
        message.chat.id,
        title=message.chat.title,
        chat_type=message.chat.type,
    )

    from instagram_download import DOWNLOAD_TOTAL_TIMEOUT_SEC, download_instagram_video, remove_file
    from instagram_urls import is_instagram_media_url

    clean_url = url_from_message(message)
    if not clean_url:
        await message.answer("рџђ· РќРµ РІС‹С‚Р°С‰РёР» СЃСЃС‹Р»РєСѓ РёР· СЃРѕРѕР±С‰РµРЅРёСЏ.")
        return
    if not is_instagram_media_url(clean_url):
        await message.answer(
            "рџђ· РќСѓР¶РЅР° СЃСЃС‹Р»РєР° РЅР° Reel, РїРѕСЃС‚, СЃС‚РѕСЂРёСЃ РёР»Рё Р°РєС‚СѓР°Р»СЊРЅРѕРµ (/reel/, /p/, /stories/, /s/)"
        )
        return

    logger.info("IG clean_url=%s", clean_url)

    MAX_DOWNLOAD_RETRIES = 3
    RETRY_DELAY_SEC = 5
    last_error: Exception | None = None

    for download_attempt in range(MAX_DOWNLOAD_RETRIES):
        file_path = None
        try:
            from instagram_download import _download_semaphore
            async with _download_semaphore:
                file_path, caption = await asyncio.wait_for(
                    asyncio.to_thread(download_instagram_video, clean_url),
                    timeout=DOWNLOAD_TOTAL_TIMEOUT_SEC,
                )

            size = os.path.getsize(file_path)
            if size > TELEGRAM_MAX_BYTES:
                remove_file(file_path)
                file_path = None
                await message.answer(video_too_heavy_message(clean_url))
                return

            # РћС‚РїСЂР°РІРєР° РІ Telegram вЂ” РґРѕ 2 РїРѕРїС‹С‚РѕРє РїСЂРё timeout
            sent_msg = None
            for attempt in range(2):
                try:
                    sent_msg = await message.answer_video(
                        video=FSInputFile(file_path),
                        reply_to_message_id=message.message_id,
                        supports_streaming=True,
                    )
                    if caption.strip():
                        cache_key = f"{sent_msg.chat.id}:{sent_msg.message_id}"
                        _ig_caption_cache[cache_key] = caption
                        if len(_ig_caption_cache) > 100:
                            old_keys = list(_ig_caption_cache.keys())[:50]
                            for k in old_keys:
                                _ig_caption_cache.pop(k, None)
                    break
                except Exception as e:
                    if "timeout" in str(e).lower() and attempt < 1:
                        logger.warning(
                            "telegram upload timeout attempt %s/2: %s",
                            attempt + 1,
                            e,
                        )
                        await asyncio.sleep(2)
                        continue
                    raise

            # РЈСЃРїРµС… вЂ” РІС‹С…РѕРґРёРј
            last_error = None
            break

        except asyncio.TimeoutError:
            logger.error("instagram download total timeout (%ss)", DOWNLOAD_TOTAL_TIMEOUT_SEC)
            last_error = RuntimeError("timeout")
            if download_attempt < MAX_DOWNLOAD_RETRIES - 1:
                logger.info("retry %s/%s after %ss", download_attempt + 2, MAX_DOWNLOAD_RETRIES, RETRY_DELAY_SEC)
                await asyncio.sleep(RETRY_DELAY_SEC)
                continue
        except Exception as e:
            last_error = e
            logger.warning(
                "instagram download attempt %s/%s failed: %s",
                download_attempt + 1,
                MAX_DOWNLOAD_RETRIES,
                e,
            )
            if download_attempt < MAX_DOWNLOAD_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY_SEC)
                continue
        finally:
            if file_path is not None:
                try:
                    remove_file(file_path)
                except Exception:
                    pass

    # Р’СЃРµ РїРѕРїС‹С‚РєРё РёСЃС‡РµСЂРїР°РЅС‹
    if last_error is not None:
        error_text = str(last_error).lower()
        # РЈРІРµРґРѕРјР»СЏРµРј Р°РґРјРёРЅР° РїСЂРё РїСЂРѕС‚СѓС…Р°РЅРёРё cookies
        if "cookie" in error_text or "СЃРµСЃСЃРёСЏ" in error_text or "login" in error_text:
            from instagram_download import _notify_admin_cookies_expired
            await _notify_admin_cookies_expired(bot)
            await message.answer(
                "🐷 Instagram протухла сессия на сервере. "
                "Админу уже написал. "
                "Попробуй позже или открой по ссылке."
            )
            return
        if isinstance(last_error, RuntimeError) and str(last_error) == "timeout":
            bot_stats.record_error(f"IG timeout {DOWNLOAD_TOTAL_TIMEOUT_SEC}s: {clean_url}")
            await message.answer(instagram_timeout_message())
        else:
            bot_stats.record_error(f"IG error: {str(last_error)[:100]}")
            await message.answer(map_instagram_error(last_error, clean_url))




async def handle_ig_text_callback(callback: CallbackQuery) -> None:
    data = callback.data or ""
    if not data.startswith("igtxt:"):
        return
    cache_key = data[6:]
    caption = _ig_caption_cache.pop(cache_key, "")
    if not caption:
        await callback.answer("РўРµРєСЃС‚ РЅРµ РЅР°Р№РґРµРЅ (РєСЌС€ РёСЃС‚С‘Рє)", show_alert=True)
        return
    await callback.answer()
    # РћС‚РїСЂР°РІР»СЏРµРј С‚РµРєСЃС‚ РѕС‚РґРµР»СЊРЅС‹Рј СЃРѕРѕР±С‰РµРЅРёРµРј
    await callback.message.answer(caption)
