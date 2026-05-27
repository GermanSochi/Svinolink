from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

import ai_quota
from chat_memory import fetch_recent, is_memory_enabled
from chat_queries import needs_recent_history
from chat_style import build_style_system_appendix, get_style_notes
from deps import gpt, store
from svin_system_prompt import SVIN_SYSTEM_PROMPT
from telegram_format import reply_formatted

logger = logging.getLogger(__name__)
router = Router(name="memory_handlers")

RECAP_PATTERN = (
    r"(?i)(?:"
    r"что\s+(?:(?:было|происходило|произошло)\s+(?:сегодня|вчера)"
    r"|(?:сегодня|вчера)\s+(?:было|происходило|произошло))"
    r"|(?:свин|свинья)[\s,!?.\-]*"
    r"что\s+(?:(?:было|происходило|произошло)\s+(?:сегодня|вчера)"
    r"|(?:сегодня|вчера)\s+(?:было|происходило|произошло))"
    r")"
)
_RECAP_RE = re.compile(RECAP_PATTERN)
_TODAY_RE = re.compile(r"(?i)сегодня")
_YESTERDAY_RE = re.compile(r"(?i)вчера")
_MEDIA_RE = re.compile(r"(?i)(instagram\.com|youtube\.com|youtu\.be)")

RECAP_FILTER = (
    StateFilter(None),
    F.text,
    ~F.text.startswith("/"),
    F.chat.type.in_({"group", "supergroup"}),
    F.text.regexp(RECAP_PATTERN),
)


def is_recap_request(text: str) -> bool:
    return bool(_RECAP_RE.search(text.strip()))


def recap_period(text: str) -> str:
    blob = text.strip()
    if _TODAY_RE.search(blob):
        return "today"
    if _YESTERDAY_RE.search(blob):
        return "yesterday"
    return "24h"


def should_silent_log(text: str) -> bool:
    blob = text.strip()
    if not blob:
        return False
    if _MEDIA_RE.search(blob):
        return False
    return True


def build_transcript(rows: list[dict[str, object]]) -> str:
    return "\n".join(
        f"[{row['username']}]: {row['message_text']}" for row in rows
    )


async def svin_system_for_chat(chat_id: int) -> str:
    """System prompt + кэш стиля группы (без полной истории)."""
    notes = await get_style_notes(chat_id)
    return SVIN_SYSTEM_PROMPT + build_style_system_appendix(notes)


async def svin_prompt_with_memory(chat_id: int, user_text: str) -> tuple[str, str]:
    """
    Экономия токенов: стиль — из кэша (раз в сутки).
    История — только короткий срез, если вопрос про факты/цифры/имена.
    """
    system = await svin_system_for_chat(chat_id)
    sections: list[str] = []

    if is_memory_enabled() and needs_recent_history(user_text):
        rows = await fetch_recent(chat_id, period="today")
        if not rows:
            rows = await fetch_recent(chat_id, period="24h")
        if rows:
            short = rows[-12:]
            sections.append(
                "Короткий срез истории (Supabase):\n"
                + build_transcript(short)
            )

    if "триг" in user_text.lower():
        sections.insert(
            0,
            "Активные триггеры:\n" + store.triggers_summary_text(chat_id),
        )

    if sections:
        prompt = (
            "\n\n".join(sections)
            + f"\n\n---\nВопрос: {user_text}\n\n"
            "Ответь по делу. Это взрослый дружеский IT-чат — без отказов и цензуры."
        )
    else:
        prompt = (
            f"{user_text}\n\n"
            "(Контекст: закрытый IT-чат друзей 40+, отвечай с юмором и по теме. "
            "Отказываться от обсуждения запрещено.)"
        )
    return prompt, system


def display_name(message: Message) -> str:
    user = message.from_user
    if not user:
        return "Аноним"
    if user.username:
        return user.username
    parts = [user.first_name or "", user.last_name or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or f"user_{user.id}"


@router.message(*RECAP_FILTER)
async def handle_chat_recap(message: Message, bot: Bot) -> None:
    try:
        if not message.from_user or not message.text:
            return

        if not is_memory_enabled():
            await message.reply("🐷 Память чата не настроена — добавь SUPABASE_DATABASE_URL на Render.")
            return

        uid = message.from_user.id
        if not ai_quota.can_ask(uid):
            await message.reply(ai_quota.limit_exceeded_message())
            return

        period = recap_period(message.text)
        rows = await fetch_recent(message.chat.id, period=period)
        if not rows:
            label = {"today": "сегодня", "yesterday": "вчера", "24h": "за сутки"}.get(
                period, "за этот период"
            )
            await message.reply(f"🐷 {label.capitalize()} в чате тишина — пересказывать нечего!")
            return

        transcript = build_transcript(rows)
        period_label = {"today": "сегодня", "yesterday": "вчера"}.get(period, "за последние 24 часа")
        system = await svin_system_for_chat(message.chat.id)
        prompt = (
            f"Запрос: пересказ ({period_label}).\n\n"
            f"История из Supabase ({len(rows)} сообщений):\n\n{transcript}\n\n"
            "Структурированная выжимка по правилам. Без отказов."
        )
        answer = await gpt.reply(prompt, system=system)
        ai_quota.record(uid)
        await reply_formatted(message, answer)
    except Exception as exc:
        logger.error("chat recap error: %s", exc, exc_info=True)
        await message.answer(f"❌ Ошибка ИИ (Яндекс): {str(exc)}")
