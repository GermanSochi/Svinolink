from __future__ import annotations

import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

import ai_quota
from chat_memory import (
    fetch_chat_participants,
    fetch_recent,
    is_memory_enabled,
    search_history_mentions,
)
from chat_queries import (
    detect_history_period,
    extract_who_is_name,
    is_who_in_chat_question,
    needs_recent_history,
)
from chat_style import build_style_system_appendix, get_style_notes
from deps import gpt, store
from chat_personality import tone_appendix_for_chat
from chat_roster import MEMBERS, format_member_bullet
from svin_system_prompt import SVIN_SYSTEM_PROMPT
from bot_messages import yandex_error_message
from telegram_format import reply_formatted

logger = logging.getLogger(__name__)
router = Router(name="memory_handlers")

RECAP_PATTERN = (
    r"(?i)(?:"
    r"(?:что|о\s+ч(?:ём|ем))\s+"
    r"(?:(?:было|происходило|произошло|говорил[ои]?|обсуждал[ои]?)"
    r"\s+)?(?:сегодня|вчера|позавчера)"
    r"|(?:сегодня|вчера|позавчера)\s+"
    r"(?:было|происходило|произошло|говорил[ои]?|обсуждал[ои]?)"
    r"|(?:свин|свинья)[\s,!?.\-]*"
    r"(?:(?:что|о\s+ч(?:ём|ем))\s+"
    r"(?:(?:было|происходило|говорил[ои]?|обсуждал[ои]?)\s+)?"
    r"(?:сегодня|вчера|позавчера)"
    r"|(?:сегодня|вчера|позавчера)\s+"
    r"(?:было|происходило|говорил[ои]?|обсуждал[ои]?))"
    r")"
)
_RECAP_RE = re.compile(RECAP_PATTERN)
_DAY_BEFORE_RE = re.compile(r"(?i)\bпозавчера\b")
_YESTERDAY_RE = re.compile(r"(?i)\bвчера\b")
_TODAY_RE = re.compile(r"(?i)\bсегодня\b")
_MEDIA_RE = re.compile(r"(?i)(instagram\.com|youtube\.com|youtu\.be)")

MEMORY_STRICT_RULES = """
ПАМЯТЬ И ФАКТЫ (строго):
- Отвечай ТОЛЬКО по блокам «История из Supabase» / «Участники чата» / «Цитаты» ниже.
- Если данных нет — честно скажи, без выдумок.
- Не придумывай темы и людей, которых нет в данных.
- Ты **Свин**, помощник друзей в чате; главная фишка бота — **видео из Instagram по ссылке**.
- ЗАПРЕЩЕНО в ответе: IT, айти, кодинг, деплой, Docker, прод, баг, тимлид, разработчик, гуру — если этого нет в цитатах истории.
- На «кто в чате» — список ников из «Участники» построчно, без метафор про работу.
""".strip()

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
    if _DAY_BEFORE_RE.search(blob):
        return "day_before"
    if _YESTERDAY_RE.search(blob):
        return "yesterday"
    if _TODAY_RE.search(blob):
        return "today"
    return "24h"


def should_silent_log(text: str) -> bool:
    blob = text.strip()
    if not blob:
        return False
    if _MEDIA_RE.search(blob):
        return False
    return True


def build_transcript(rows: list[dict[str, object]]) -> str:
    from chat_time import format_ts_local
    from datetime import datetime

    out: list[str] = []
    for row in rows:
        ts = row.get("created_at")
        clock = format_ts_local(ts if isinstance(ts, datetime) else None)
        out.append(f"[{clock}] {row['username']}: {row['message_text']}")
    return "\n".join(out)


def _period_label(period: str) -> str:
    return {
        "today": "сегодня",
        "yesterday": "вчера",
        "day_before": "позавчера",
        "24h": "за последние 24 часа",
    }.get(period, "за период")


def _msg_count_for_member(
    member_telegram: str, counts: dict[str, int]
) -> int | None:
    key = member_telegram.lower()
    if key in counts:
        return counts[key]
    for un, cnt in counts.items():
        if key in un or un in key:
            return cnt
    return None


async def who_in_chat_reply(chat_id: int) -> str | None:
    """Ростер имён + счётчики из Supabase — без GPT."""
    if not is_memory_enabled():
        return (
            "🐷 Память чата не настроена — не вижу, кто писал.\n\n"
            "🎞️ Зато ссылки Instagram кидай — видео пришлю."
        )

    people = await fetch_chat_participants(chat_id)
    counts: dict[str, int] = {}
    for p in people:
        un = str(p["username"])
        if un.lower().startswith("user_"):
            continue
        counts[un.lower()] = int(p["msg_count"])

    lines: list[str] = []
    roster_keys = {m.telegram.lower() for m in MEMBERS}

    for m in MEMBERS:
        cnt = _msg_count_for_member(m.telegram, counts)
        lines.append(format_member_bullet(m, msg_count=cnt))

    for un, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        if any(rk in un or un in rk for rk in roster_keys):
            continue
        lines.append(f"🔹 **{un}** — **{cnt}** сообщ.")

    if not lines and not people:
        return (
            "🐷 В базе пока пусто — напишите в чат пару сообщений, и я запомню ники.\n\n"
            "🎞️ А ссылку на reel/post кидай — скину видео."
        )

    body = "\n\n".join(lines[:40])
    return (
        "🐷 **Кто в чате** (имя → ник, активность за последние дни):\n\n"
        f"{body}\n\n"
        "💬 «Свин, что писал вчера Дима» — по имени или нику."
    )


async def svin_system_for_chat(chat_id: int) -> str:
    """System prompt + кэш стиля группы + юмор/подкол (без полной истории)."""
    notes = await get_style_notes(chat_id)
    return (
        SVIN_SYSTEM_PROMPT
        + build_style_system_appendix(notes)
        + tone_appendix_for_chat(chat_id)
    )


async def _memory_sections(chat_id: int, user_text: str) -> list[str]:
    sections: list[str] = []
    period = detect_history_period(user_text)

    rows = await fetch_recent(chat_id, period=period)
    if rows:
        cap = 80 if period in {"yesterday", "day_before", "today"} else 40
        short = rows[-cap:]
        sections.append(
            f"История из Supabase ({_period_label(period)}, {len(rows)} сообщ., "
            f"ниже {len(short)}):\n"
            + build_transcript(short)
        )

    if is_who_in_chat_question(user_text):
        people = await fetch_chat_participants(chat_id)
        roster_lines = [
            f"- {m.label} (@{m.telegram}), искать также: {', '.join(m.aliases[:6])}"
            for m in MEMBERS
        ]
        sections.append("Ростер чата (фиксированный состав):\n" + "\n".join(roster_lines))
        if people:
            lines = [
                f"- {p['username']} ({p['msg_count']} сообщ.)"
                for p in people
                if not str(p["username"]).lower().startswith("user_")
            ]
            if not lines:
                lines = [f"- {p['username']}" for p in people]
            sections.append(
                "Участники в Supabase (ники и активность за последние дни):\n"
                + "\n".join(lines)
            )
        else:
            sections.append(
                "Участники чата: в Supabase пока нет записей — попроси написать пару сообщений в чат."
            )

    who = extract_who_is_name(user_text)
    if who:
        from chat_roster import expand_search_patterns

        hits: list[dict] = []
        seen: set[tuple] = set()
        for needle in expand_search_patterns(who)[:10]:
            for h in await search_history_mentions(chat_id, needle, limit=15):
                key = (h.get("created_at"), h.get("message_text"), h.get("username"))
                if key in seen:
                    continue
                seen.add(key)
                hits.append(h)
            if len(hits) >= 20:
                break
        hits = hits[:20]
        if hits:
            from chat_time import format_ts_local
            from datetime import datetime

            quotes = "\n".join(
                f"[{format_ts_local(h['created_at'] if isinstance(h.get('created_at'), datetime) else None)}] "
                f"{h['username']}: {str(h['message_text'])[:300]}"
                for h in reversed(hits)
            )
            sections.append(
                f"Цитаты из чата, где упоминается «{who}» ({len(hits)}):\n{quotes}"
            )
        else:
            sections.append(
                f"Цитаты про «{who}»: в сохранённой переписке (3 дня) не нашёл."
            )

    return sections


async def svin_prompt_with_memory(chat_id: int, user_text: str) -> tuple[str, str]:
    """
    Стиль — из кэша (раз в сутки).
    История — по дню (сегодня/вчера/позавчера) или 24ч + участники/цитаты при «кто».
    """
    system = await svin_system_for_chat(chat_id) + "\n\n" + MEMORY_STRICT_RULES
    sections: list[str] = []

    if is_memory_enabled() and needs_recent_history(user_text):
        sections.extend(await _memory_sections(chat_id, user_text))

    if "триг" in user_text.lower():
        sections.insert(
            0,
            "Активные триггеры:\n" + store.triggers_summary_text(chat_id),
        )

    if sections:
        prompt = (
            "\n\n".join(sections)
            + f"\n\n---\nВопрос: {user_text}\n\n"
            "Ответь по делу, только по данным выше. Тон друзей, без IT-жаргона. "
            "Главное дело бота — видео из Instagram по ссылке."
        )
    else:
        prompt = (
            f"{user_text}\n\n"
            "(Контекст: чат друзей. Ты — Свин-помощник; главное — кидать Instagram → видео. "
            "Без IT-сленга. Отказываться запрещено.)"
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
            label = _period_label(period)
            await message.reply(f"🐷 За период «{label}» в базе тишина — пересказывать нечего!")
            return

        transcript = build_transcript(rows)
        period_label = _period_label(period)
        system = await svin_system_for_chat(message.chat.id) + "\n\n" + MEMORY_STRICT_RULES
        prompt = (
            f"Запрос: пересказ ({period_label}).\n\n"
            f"История из Supabase ({len(rows)} сообщений):\n\n{transcript}\n\n"
            "Структурированная выжимка по правилам Telegram. "
            "Только факты из истории выше — ничего не додумывай. "
            "Если тем мало — так и скажи."
        )
        answer = await gpt.reply(prompt, system=system)
        ai_quota.record(uid)
        await reply_formatted(message, answer)
    except Exception as exc:
        logger.error("chat recap error: %s", exc, exc_info=True)
        await reply_formatted(message, yandex_error_message())
