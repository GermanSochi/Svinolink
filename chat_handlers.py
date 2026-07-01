from __future__ import annotations

import asyncio
import logging
import os
import re
import io

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, StateFilter
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

import ai_quota
from config import settings
from deps import gpt, store
from chat_examples import chat_examples_markdown
from chat_user_log import user_messages_markdown
from telegram_format import reply_formatted, reply_photo_then_text
from chat_queries import is_chat_examples_request
from capabilities import capabilities_markdown, is_capabilities_question
# Мемы/видосы отключены — оставляем импорты закомментированными на будущее.
from trigger_manage_requests import TriggerAdd, TriggerDelete, TriggerUpdate, parse_trigger_manage
from doc_extract import extract_docx_text, extract_pdf_text, extract_xlsx_preview, extract_plain_text
from chat_queries import is_who_in_chat_question
from memory_handlers import RECAP_PATTERN, svin_prompt_with_memory, who_in_chat_reply
from bot_messages import (
    instagram_timeout_message,
    map_instagram_error,
    video_too_heavy_message,
    yandex_error_message,
)
from personality_commands import try_personality_or_roster
from web_search_handlers import try_web_search_reply
from message_urls import message_has_instagram_link, url_from_message
from trigger_queries import is_trigger_list_question
from yandex_router import route_intent
from games import execute_game_action
from games.responses import render_game_response

logger = logging.getLogger(__name__)
router = Router(name="chat_handlers")

_SECRET_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), "sk-***"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "ghp_***"),
    (re.compile(r"\bhf_[A-Za-z0-9]{10,}\b"), "hf_***"),
    (re.compile(r"\bvcp_[A-Za-z0-9]{10,}\b"), "vcp_***"),
    (re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"), "xox***"),
]


def _redact_secrets(text: str) -> str:
    out = text
    for pat, repl in _SECRET_REDACTIONS:
        out = pat.sub(repl, out)
    return out

TELEGRAM_MAX_BYTES = 52_428_800

_bot_id: int | None = None


class SvinInvokeFilter(BaseFilter):
    """Срабатывает на «свин» в тексте или reply на сообщение бота (только при AI)."""

    async def __call__(self, message: Message, bot: Bot) -> bool:
        global _bot_id
        text = message.text or message.caption
        if not text:
            return False
        if re.search(r"(?i)(свин|свинья)", text):
            return True
        # Reply на бота ловим ТОЛЬКО когда AI включён
        if not settings.ai_enabled:
            return False
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


from admin_auth import is_admin_user  # noqa: F401 — re-export для старых импортов


_ig_caption_cache: dict[str, str] = {}


async def handle_instagram_link(message: Message, bot: Bot) -> None:
    from config import settings
    from instagram_download import instagram_user_message
    from bot_stats import bot_stats
    bot_stats.record_message()

    if not settings.instagram_is_active():
        await message.answer(instagram_user_message())
        return

    file_path = None
    clean_url: str | None = None
    try:
        text = message.text or message.caption or ""
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

        max_retries = 2
        sent_msg = None
        for attempt in range(max_retries):
            try:
                if caption.strip():
                    # Кэшируем caption по message_id видео чтобы callback мог достать
                    sent_msg = await message.answer_video(
                        video=FSInputFile(file_path),
                        reply_to_message_id=message.message_id,
                        supports_streaming=True,
                    )
                    # Сохраняем caption с привязкой к chat+video message_id
                    cache_key = f"{sent_msg.chat.id}:{sent_msg.message_id}"
                    _ig_caption_cache[cache_key] = caption
                    # Чистим старые записи (макс 100)
                    if len(_ig_caption_cache) > 100:
                        old_keys = list(_ig_caption_cache.keys())[:50]
                        for k in old_keys:
                            _ig_caption_cache.pop(k, None)
                    # Добавляем inline кнопку
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📝 Получить текст", callback_data=f"igtxt:{cache_key}")]
                    ])
                    await sent_msg.edit_reply_markup(reply_markup=kb)
                else:
                    await message.answer_video(
                        video=FSInputFile(file_path),
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
        bot_stats.record_error(f"IG timeout {DOWNLOAD_TOTAL_TIMEOUT_SEC}s: {clean_url or '?'}")
        await message.answer(instagram_timeout_message())
    except Exception as e:
        logger.error("instagram handler error: %s", e, exc_info=True)
        bot_stats.record_error(f"IG error: {str(e)[:100]}")
        await message.answer(map_instagram_error(e, clean_url))
    finally:
        if file_path is not None:
            try:
                from instagram_download import remove_file

                remove_file(file_path)
            except Exception:
                pass


async def handle_ig_text_callback(callback: CallbackQuery) -> None:
    data = callback.data or ""
    if not data.startswith("igtxt:"):
        return
    cache_key = data[6:]
    caption = _ig_caption_cache.pop(cache_key, "")
    if not caption:
        await callback.answer("Текст не найден (кэш истёк)", show_alert=True)
        return
    await callback.answer()
    # Отправляем текст отдельным сообщением
    await callback.message.answer(caption)


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

        # Управление триггерами из чата (добавить/удалить/править) — раньше списка,
        # чтобы фраза "добавь триггер" не перехватывалась "какие триггеры".
        action = parse_trigger_manage(text)
        if action:
            if isinstance(action, TriggerAdd):
                rule_id = store.add_custom_rule(
                    message.chat.id,
                    action.word,
                    action.response,
                    once_per_day=action.once_per_day,
                    added_by_user_id=uid,
                    added_by_username=message.from_user.username,
                    match=action.match,
                )
                await reply_formatted(
                    message,
                    "✅ **Триггер добавлен**\n\n"
                    f"🎯 **Слово**: `{action.word}`\n\n"
                    f"💬 **Ответ**: **{action.response}**\n\n"
                    f"🧷 **ID**: `{rule_id}`",
                )
                return
            if isinstance(action, TriggerDelete):
                removed = store.delete_custom_by_indices(
                    message.chat.id, [i - 1 for i in action.indices_1based]
                )
                if removed:
                    await reply_formatted(
                        message,
                        "🗑️ **Триггеры удалены**\n\n"
                        f"🔢 Кол-во: **{removed}**",
                    )
                else:
                    await reply_formatted(
                        message,
                        "🗑️ Не нашёл такие номера.\n\n"
                        "🧷 Спроси **«какие триггеры»** и удали по номеру.",
                    )
                return
            if isinstance(action, TriggerUpdate):
                ok = store.update_custom_rule(
                    message.chat.id,
                    action.index_1based - 1,
                    word=action.word,
                    response=action.response,
                    match=action.match,
                )
                if ok:
                    await reply_formatted(
                        message,
                        "✏️ **Триггер обновлён**\n\n"
                        f"🔢 Номер: **{action.index_1based}**",
                    )
                else:
                    await reply_formatted(
                        message,
                        "✏️ Не нашёл такой номер.\n\n"
                        "🧷 Спроси **«какие триггеры»** и выбери номер.",
                    )
                return

        # Достать текст из документа — текстом (reply на файл ИЛИ файл с подписью)
        doc_msg = None
        if message.reply_to_message and message.reply_to_message.document:
            doc_msg = message.reply_to_message
        elif message.document:
            doc_msg = message

        if doc_msg and doc_msg.document:
            low = text.lower()
            wants_text = any(
                x in low
                for x in (
                    "достань текст",
                    "вытащи текст",
                    "достать текст",
                    "извлеки текст",
                    "вытяни текст",
                    "прочитай документ",
                    "прочитай файл",
                    "текст из документа",
                    "текст из файла",
                    "расшифруй текст",
                    "расшифровать текст",
                    "распознай текст",
                    "распознать текст",
                    "покажи текст",
                    "покажи содержимое",
                )
            )
            if wants_text:
                doc = doc_msg.document
                buf = io.BytesIO()
                await bot.download(doc.file_id, destination=buf)
                data = buf.getvalue()

                name = (doc.file_name or "").lower()
                extracted = ""
                kind = ""
                if name.endswith(".pdf") or (doc.mime_type or "").lower().endswith("pdf"):
                    kind = "PDF"
                    extracted = extract_pdf_text(data)
                elif name.endswith(".docx"):
                    kind = "DOCX"
                    extracted = extract_docx_text(data)
                elif name.endswith(".xlsx"):
                    kind = "XLSX"
                    extracted = extract_xlsx_preview(data)
                elif name.endswith(".txt") or (doc.mime_type or "").lower().startswith("text/"):
                    kind = "TXT"
                    extracted = extract_plain_text(data)

                if not kind:
                    await reply_formatted(
                        message,
                        "📎 Понимаю **PDF/DOCX/XLSX/TXT**.\n\n"
                        "🧷 Пришли файл с подписью **«Свин, достань текст из файла»** "
                        "или ответь реплаем на файл.",
                    )
                    return

                if not extracted:
                    await reply_formatted(
                        message,
                        f"📎 **{kind}** пустой или текст не извлёкся.\n\n"
                        "Если это сканы — нужна OCR.",
                    )
                    return

                cleaned = _redact_secrets(extracted).strip()
                # XLSX уже форматируется построчно — сохраняем переносы строк.
                snippet = cleaned if kind == "XLSX" else cleaned.replace("\n", " ")
                if len(snippet) > 2000:
                    snippet = snippet[:2000] + "…"
                await reply_formatted(
                    message,
                    f"📎 **{kind} → текст**\n\n🧾 {snippet}",
                )
                return

        if is_trigger_list_question(text):
            reply = store.triggers_list_markdown(message.chat.id)
            logger.info(
                "trigger_list chat=%s reply_chars=%s",
                message.chat.id,
                len(reply),
            )
            await reply_formatted(message, reply)
            return

        if is_capabilities_question(text):
            await reply_formatted(message, capabilities_markdown())
            return

        if settings.web_search_enabled:
            web_reply = await try_web_search_reply(message)
            if web_reply:
                await reply_photo_then_text(
                    message, web_reply.text, web_reply.photo_bytes
                )
                return

        if settings.ai_enabled:
            if is_chat_examples_request(text):
                reply = await chat_examples_markdown(message.chat.id)
                logger.info("chat_examples chat=%s", message.chat.id)
                await reply_formatted(message, reply)
                return

            tone_reply = await try_personality_or_roster(message)
            if tone_reply:
                await reply_formatted(message, tone_reply)
                return

            if is_who_in_chat_question(text):
                reply = await who_in_chat_reply(message.chat.id)
                if reply:
                    await reply_formatted(message, reply)
                    return

            user_log = await user_messages_markdown(message.chat.id, text)
            if user_log:
                await reply_formatted(message, user_log)
                return

            if settings.games_enabled:
                routed = await route_intent(text)
                if routed["is_game_action"] and routed["game_id"] != "none":
                    data = await execute_game_action(
                        chat_id=message.chat.id,
                        telegram_user_id=uid,
                        username=message.from_user.username,
                        game_id=routed["game_id"],
                        action_type=routed["action_type"],
                        payload=routed["payload"],
                    )
                    resp = render_game_response(routed["game_id"], routed["action_type"], data)
                    await reply_formatted(message, resp)
                    return

            if not ai_quota.can_ask(uid):
                await message.reply(ai_quota.limit_exceeded_message())
                return

            prompt, system = await svin_prompt_with_memory(message.chat.id, text)
            answer = await gpt.reply(prompt, system=system)
            ai_quota.record(uid)
            await reply_formatted(message, answer)
        else:
            # AI выключен — отвечаем заглушкой
            await reply_formatted(
                message,
                "🐷 ИИ-режим выключен. Могу скачать видео по ссылке Instagram.",
            )
    except Exception as e:
        logger.error("svin_ai error: %s", e, exc_info=True)
        await reply_formatted(message, yandex_error_message())
