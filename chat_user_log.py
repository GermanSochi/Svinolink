"""Прямой ответ «что писал X вчера» / «во сколько» — без Yandex GPT."""
from __future__ import annotations

from datetime import datetime

from chat_memory import fetch_messages_by_user, is_memory_enabled
from chat_queries import parse_user_log_request
from chat_query_models import UserLogQuery
from chat_time import format_ts_local

_PERIOD_LABEL = {
    "today": "сегодня",
    "yesterday": "вчера",
    "day_before": "позавчера",
    "24h": "за последние 24 часа",
}


def _time_window_label(q: UserLogQuery) -> str:
    label = _PERIOD_LABEL.get(q.period, _PERIOD_LABEL["24h"])
    if q.hour_from is not None and q.hour_to is not None:
        return f"{label}, **{q.hour_from:02d}:{q.minute_from:02d}–{q.hour_to:02d}:{q.minute_to:02d}** (МСК)"
    if q.hour_from is not None:
        return f"{label}, с **{q.hour_from:02d}:00** (МСК)"
    return label


def _format_row(row: dict[str, object], *, when_only: bool) -> str:
    ts = row.get("created_at")
    clock = format_ts_local(ts if isinstance(ts, datetime) else None)
    body = str(row["message_text"]).replace("\n", " ")
    if len(body) > 500:
        body = body[:500] + "…"
    if when_only:
        return f"🕐 **{clock}** — {body}"
    return f"🕐 **{clock}** — {body}"


async def user_messages_markdown(chat_id: int, text: str) -> str | None:
    q = parse_user_log_request(text)
    if not q:
        return None

    if not is_memory_enabled():
        return (
            "🐷 Память чата не настроена — не вижу, кто что писал.\n\n"
            "🎞️ Ссылки Instagram кидай — видео пришлю."
        )

    rows = await fetch_messages_by_user(
        chat_id,
        q.username,
        period=q.period,
        hour_from=q.hour_from,
        hour_to=q.hour_to,
        minute_from=q.minute_from,
        minute_to=q.minute_to,
        phrase=q.phrase,
        limit=50,
    )
    window = _time_window_label(q)

    if not rows:
        extra = ""
        if q.phrase:
            extra = f"\n\n🔍 Фраза «{q.phrase}» за этот интервал не найдена."
        return (
            f"🐷 У **{q.username}** за **{window}** в базе **нет сообщений**.{extra}\n\n"
            "💬 Спроси **«кто в чате»** — сверь ник."
        )

    if q.when_only and q.phrase:
        header = (
            f"🐷 **{q.username}** — **во сколько** писал про «{q.phrase}» "
            f"({window}, {len(rows)} совпад.)\n"
        )
    else:
        header = (
            f"🐷 **{q.username}** — сообщения **{window}** "
            f"({len(rows)} шт.)\n"
        )

    lines = [header]
    emojis = ("💬", "📝", "🗨️", "📌", "🔹", "✨", "🎯", "📎", "🧩", "🐽")
    for i, row in enumerate(rows):
        mark = emojis[i % len(emojis)]
        lines.append(f"{mark} {_format_row(row, when_only=q.when_only)}\n")

    lines.append("\n✅ Время и текст из **Supabase** (часовой пояс из CHAT_TIMEZONE).")
    return "\n".join(lines)
