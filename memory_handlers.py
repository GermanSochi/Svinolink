from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

import ai_quota
from chat_memory import fetch_last_24h, is_memory_enabled
from deps import gpt

logger = logging.getLogger(__name__)
router = Router(name="memory_handlers")

_YESTERDAY_RE = re.compile(r"(?i)(что было вчера|что произошло вчера)")
_MEDIA_RE = re.compile(r"(?i)(instagram\.com|youtube\.com|youtu\.be)")
_SVIN_START_RE = re.compile(r"(?i)^свин\b")

YESTERDAY_FILTER = (
    StateFilter(None),
    F.text,
    ~F.text.startswith("/"),
    F.chat.type.in_({"group", "supergroup"}),
    F.text.regexp(r"(?i)(что было вчера|что произошло вчера)"),
)

_YESTERDAY_SYSTEM = (
    "Ты веселый бот-свинья в чате пацанов. Перед тобой история их переписки "
    "за вчерашний день. Сделай из неё очень короткий, емкий и смешной пересказ: "
    "кто в чате был главным красавчиком, о чем больше всего спорили и какие "
    "важные темы обсудили. Пиши в стиле пацанского юмора, без официального тона."
)


def should_silent_log(text: str) -> bool:
    blob = text.strip()
    if not blob:
        return False
    if _SVIN_START_RE.match(blob):
        return False
    if _MEDIA_RE.search(blob):
        return False
    if _YESTERDAY_RE.search(blob):
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


@router.message(*YESTERDAY_FILTER)
async def handle_yesterday_recap(message: Message, bot: Bot) -> None:
    try:
        if not message.from_user:
            return

        if not is_memory_enabled():
            await message.reply("🐷 Память чата не настроена — добавь SUPABASE_DATABASE_URL на Render.")
            return

        uid = message.from_user.id
        if not ai_quota.can_ask(uid):
            await message.reply(ai_quota.limit_exceeded_message())
            return

        rows = await fetch_last_24h(message.chat.id)
        if not rows:
            await message.reply("🐷 Вчера все молчали, пересказывать нечего!")
            return

        transcript = "\n".join(
            f"[{row['username']}]: {row['message_text']}" for row in rows
        )
        prompt = (
            "Вот переписка в группе за последние 24 часа:\n\n"
            f"{transcript}\n\n"
            "Сделай короткий смешной пересказ для пацанов."
        )
        answer = await gpt.reply(prompt, system=_YESTERDAY_SYSTEM)
        ai_quota.record(uid)
        left = ai_quota.remaining(uid)
        await message.reply(f"{answer}\n\n(Осталось вопросов: {left} в час)")
    except Exception as exc:
        logger.error("yesterday recap error: %s", exc, exc_info=True)
        await message.answer(f"❌ Ошибка ИИ (Яндекс): {str(exc)}")
