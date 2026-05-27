"""Прямые ответы «примеры из чата» — без Yandex GPT."""
from __future__ import annotations

from html import escape

from chat_memory import fetch_recent, is_memory_enabled


async def chat_examples_html(chat_id: int, *, limit: int = 12) -> str:
    if not is_memory_enabled():
        return "🐷 Supabase не подключён — истории чата нет."

    rows = await fetch_recent(chat_id, period="today")
    if not rows:
        rows = await fetch_recent(chat_id, period="24h")
    if not rows:
        return (
            "🐷 В Supabase за сутки пусто — пока нечего показать. "
            "Напишите в чат пару сообщений (не только «Свин»), и я запомню."
        )

    pick = rows[-limit:]
    lines = [
        f"🐷 <b>Примеры из Supabase</b> (последние {len(pick)} из {len(rows)} за сутки):\n"
    ]
    for row in pick:
        name = escape(str(row["username"] or "Аноним"))
        text = escape(str(row["message_text"]).replace("\n", " "))
        lines.append(f"• <b>{name}</b>: {text}")

    lines.append(
        "\n<i>Это реальные строки из chat_history — без выдумок GPT.</i>"
    )
    return "\n".join(lines)
