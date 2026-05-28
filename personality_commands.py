"""Команды «Свин уровень юмора/токсичности» из чата."""
from __future__ import annotations

from aiogram.types import Message

from admin_auth import is_admin_user
from chat_personality import (
    get_personality,
    parse_personality_command,
    personality_status_markdown,
    set_personality,
)
from chat_roster import roster_summary_markdown


def is_roster_request(text: str) -> bool:
    low = text.lower()
    return any(
        x in low
        for x in (
            "наши в чате",
            "кто мы",
            "состав чата",
            "ростер",
            "участники и имена",
            "имена участников",
        )
    )


async def try_personality_or_roster(message: Message) -> str | None:
    text = (message.text or message.caption or "").strip()
    if not text:
        return None

    if is_roster_request(text):
        return roster_summary_markdown()

    cmd = parse_personality_command(text)
    if not cmd:
        return None

    if cmd == "show":
        return personality_status_markdown(message.chat.id)

    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return (
            "🐷 Уровни тона меняет **админ**.\n\n"
            + personality_status_markdown(message.chat.id)
        )

    if cmd.startswith("set_toxic:"):
        val = int(cmd.split(":", 1)[1])
        p = set_personality(message.chat.id, toxicity=val)
        return (
            f"🐷 **Токсичность (подкол): {p.toxicity}%** — сохранено.\n\n"
            f"😏 Юмор: **{p.humor}%**\n\n"
            "💬 Проверка: задай один и тот же вопрос при **10%** и **90%** — "
            "подача должна отличаться."
        )

    if cmd.startswith("set_humor:"):
        val = int(cmd.split(":", 1)[1])
        p = set_personality(message.chat.id, humor=val)
        return (
            f"🐷 **Юмор: {p.humor}%** — сохранено.\n\n"
            f"🔥 Подкол: **{p.toxicity}%**\n\n"
            "💬 Проверка: задай один и тот же вопрос при **10%** и **90%** — "
            "подача должна отличаться."
        )

    return None
