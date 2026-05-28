"""Форматирование ответов для Telegram через telegramify-markdown."""
from __future__ import annotations

import logging
import re

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile, Message

logger = logging.getLogger(__name__)

try:
    from telegramify_markdown import convert, markdownify, split_markdownv2
except ImportError:  # pragma: no cover
    convert = None  # type: ignore
    markdownify = None  # type: ignore
    split_markdownv2 = None  # type: ignore

_INTRO_RE = re.compile(
    r"^(?:"
    r"(?:вот|итак|ну|смотри|короче)[,!\s—-]*"
    r"(?:несколько|главн\w*|основн\w*|важн\w*)\s+(?:причин|факт|пункт|момент)"
    r"|(?:фильтр|кофе|тема)[^.!?]{0,80}:\s*"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

_DISCLAIMER_RE = re.compile(
    r"\n+(?:но\s+)?(?:это\s+)?(?:всего\s+лишь\s+)?(?:мо[ёе]\s+)?мнени[ея][^.!?]*[.!?]?\s*$",
    re.IGNORECASE,
)


def polish_markdown(text: str) -> str:
    """Убираем типичные вводные и финальные отмазки."""
    out = text.strip()
    out = _INTRO_RE.sub("", out).lstrip()
    out = _DISCLAIMER_RE.sub("", out).rstrip()
    return out


def to_telegram_chunks(text: str, *, max_len: int = 4090) -> list[str]:
    """Markdown → безопасные куски MarkdownV2 для Telegram."""
    raw = polish_markdown(text)
    if not raw:
        return [""]

    if markdownify is None or convert is None or split_markdownv2 is None:
        return _plain_chunks(raw, max_len=max_len)

    try:
        plain, entities = convert(raw)
        chunks = list(split_markdownv2(plain, entities, max_utf16_len=max_len))
        if chunks:
            return chunks
        mdv2 = markdownify(raw)
        return [mdv2] if mdv2 else [raw]
    except Exception as exc:
        logger.warning("telegramify-markdown failed: %s", exc)
        return _plain_chunks(raw, max_len=max_len)


def _plain_chunks(text: str, *, max_len: int) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    rest = text
    while rest:
        parts.append(rest[:max_len])
        rest = rest[max_len:]
    return parts


async def reply_photo_then_text(
    message: Message,
    text: str,
    photo_bytes: bytes | None,
) -> None:
    """Сначала фото (файлом), потом текст с разметкой."""
    if photo_bytes:
        try:
            await message.reply_photo(
                BufferedInputFile(photo_bytes, filename="wiki.jpg"),
            )
        except Exception as exc:
            logger.warning("reply_photo failed: %s", exc)
    await reply_formatted(message, text)


async def reply_formatted(
    message: Message,
    text: str,
    *,
    html: bool = False,
) -> None:
    """Отправка с telegramify-markdown (MarkdownV2) или HTML для служебных ответов."""
    if html:
        await message.reply(text, parse_mode=ParseMode.HTML)
        return

    chunks = to_telegram_chunks(text)
    for idx, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        try:
            if idx == 0:
                await message.reply(chunk, parse_mode=ParseMode.MARKDOWN_V2)
            else:
                await message.answer(chunk, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as exc:
            logger.warning("MarkdownV2 send failed, plain fallback: %s", exc)
            if idx == 0:
                await message.reply(text)
            else:
                await message.answer(chunk)


async def send_formatted(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    reply_to_message_id: int | None = None,
) -> None:
    chunks = to_telegram_chunks(text)
    for idx, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        kwargs: dict = {"chat_id": chat_id, "text": chunk}
        if idx == 0 and reply_to_message_id is not None:
            kwargs["reply_to_message_id"] = reply_to_message_id
        try:
            await bot.send_message(**kwargs, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception:
            await bot.send_message(**kwargs)
