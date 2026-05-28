"""Прямые ответы «примеры из чата» — без Yandex GPT."""
from __future__ import annotations

from datetime import datetime

from chat_memory import fetch_recent, is_memory_enabled
from chat_time import format_ts_local


async def chat_examples_markdown(chat_id: int, *, limit: int = 12) -> str:
    if not is_memory_enabled():
        return "🐷 Supabase не подключён — **истории чата** нет."

    rows = await fetch_recent(chat_id, period="today")
    if not rows:
        rows = await fetch_recent(chat_id, period="24h")
    if not rows:
        return (
            "🐷 В Supabase за сутки **тишина** — пока нечего показать.\n\n"
            "Напишите в чат пару сообщений (не только «Свин»), и я запомню."
        )

    pick = rows[-limit:]
    lines = [
        f"🐷 **Примеры из Supabase** — последние {len(pick)} из {len(rows)} за сутки\n"
    ]
    emojis = ("💬", "📝", "🗨️", "📌", "🔹", "✨", "🛠️", "⚡", "🎯", "📎", "🧩", "🐽")
    for i, row in enumerate(pick):
        name = str(row["username"] or "Аноним")
        text = str(row["message_text"]).replace("\n", " ")
        mark = emojis[i % len(emojis)]
        ts = row.get("created_at")
        clock = format_ts_local(ts if isinstance(ts, datetime) else None)
        lines.append(f"{mark} 🕐 **{clock}** · **{name}**: {text}\n")

    lines.append("\n✅ Это **реальные строки** из chat_history — без выдумок GPT.")
    return "\n".join(lines)


async def chat_examples_html(chat_id: int, *, limit: int = 12) -> str:
    return await chat_examples_markdown(chat_id, limit=limit)
