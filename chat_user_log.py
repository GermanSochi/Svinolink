"""Прямой ответ «что писал X вчера» — без Yandex GPT."""
from __future__ import annotations

from chat_memory import fetch_messages_by_user, is_memory_enabled
from chat_queries import parse_user_log_request

_PERIOD_LABEL = {
    "today": "сегодня",
    "yesterday": "вчера",
    "day_before": "позавчера",
    "24h": "за последние 24 часа",
}


async def user_messages_markdown(chat_id: int, text: str) -> str | None:
    parsed = parse_user_log_request(text)
    if not parsed:
        return None

    username, period = parsed
    if not is_memory_enabled():
        return (
            "🐷 Память чата не настроена — не вижу, кто что писал.\n\n"
            "🎞️ Ссылки Instagram кидай — видео пришлю."
        )

    rows = await fetch_messages_by_user(chat_id, username, period=period, limit=40)
    label = _PERIOD_LABEL.get(period, _PERIOD_LABEL["24h"])

    if not rows:
        return (
            f"🐷 За период **{label}** у **{username}** в базе **нет сообщений**.\n\n"
            "💬 Либо молчал, либо ник записан иначе — спроси **«кто в чате»**."
        )

    lines = [
        f"🐷 **{username}** — что писал **{label}** ({len(rows)} сообщ.)\n"
    ]
    emojis = ("💬", "📝", "🗨️", "📌", "🔹", "✨", "🎯", "📎", "🧩", "🐽")
    for i, row in enumerate(rows):
        mark = emojis[i % len(emojis)]
        body = str(row["message_text"]).replace("\n", " ")
        if len(body) > 500:
            body = body[:500] + "…"
        lines.append(f"{mark} {body}\n")

    lines.append("\n✅ Цитаты из **Supabase** — без GPT.")
    return "\n".join(lines)
