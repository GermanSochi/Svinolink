from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

import ai_quota
from chat_memory import fetch_recent, is_memory_enabled
from deps import gpt

logger = logging.getLogger(__name__)
router = Router(name="memory_handlers")

_RECAP_RE = re.compile(
    r"(?i)(?:"
    r"что\s+(?:было|произошло)\s+(?:сегодня|вчера)"
    r"|свин[\s,!?.\-]*что\s+(?:было|произошло)\s+(?:сегодня|вчера)"
    r")"
)
_TODAY_RE = re.compile(r"(?i)сегодня")
_YESTERDAY_RE = re.compile(r"(?i)вчера")
_MEDIA_RE = re.compile(r"(?i)(instagram\.com|youtube\.com|youtu\.be)")
_SVIN_START_RE = re.compile(r"(?i)^свин\b")

RECAP_FILTER = (
    StateFilter(None),
    F.text,
    ~F.text.startswith("/"),
    F.chat.type.in_({"group", "supergroup"}),
    F.text.regexp(_RECAP_RE),
)

_RECAP_SYSTEM = (
    "Ты веселый бот-свинья в чате пацанов. Перед тобой РЕАЛЬНАЯ история переписки "
    "из базы данных — только эти сообщения, без выдумок. Сделай короткий смешной "
    "пересказ: кто что говорил, о чём спорили, какие шутки были. "
    "Если в переписке упоминают имена (Игорь и т.д.) — используй их. "
    "Не придумывай желуди, лужи и другие детали, которых нет в тексте."
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
    if _SVIN_START_RE.match(blob):
        return False
    if _MEDIA_RE.search(blob):
        return False
    if is_recap_request(blob):
        return False
    return True


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

        transcript = "\n".join(
            f"[{row['username']}]: {row['message_text']}" for row in rows
        )
        period_label = {"today": "сегодня", "yesterday": "вчера"}.get(period, "за последние 24 часа")
        prompt = (
            f"Вот переписка в группе {period_label} (из базы данных, {len(rows)} сообщений):\n\n"
            f"{transcript}\n\n"
            "Сделай короткий смешной пересказ строго по этим сообщениям."
        )
        answer = await gpt.reply(prompt, system=_RECAP_SYSTEM)
        ai_quota.record(uid)
        left = ai_quota.remaining(uid)
        await message.reply(f"{answer}\n\n(Осталось вопросов: {left} в час)")
    except Exception as exc:
        logger.error("chat recap error: %s", exc, exc_info=True)
        await message.answer(f"❌ Ошибка ИИ (Яндекс): {str(exc)}")
