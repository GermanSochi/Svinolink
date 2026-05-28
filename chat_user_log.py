"""Прямой ответ «что писал X вчера» / «кто что писал вчера» — без Yandex GPT."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from chat_memory import (
    fetch_chat_participants,
    fetch_messages_by_user,
    fetch_period_messages,
    is_memory_enabled,
)
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


def _format_row(row: dict[str, object]) -> str:
    ts = row.get("created_at")
    clock = format_ts_local(ts if isinstance(ts, datetime) else None)
    body = str(row["message_text"]).replace("\n", " ")
    if len(body) > 400:
        body = body[:400] + "…"
    return f"🕐 **{clock}** — {body}"


async def _all_users_markdown(chat_id: int, q: UserLogQuery) -> str:
    window = _time_window_label(q)
    rows = await fetch_period_messages(
        chat_id,
        period=q.period,
        hour_from=q.hour_from,
        hour_to=q.hour_to,
        minute_from=q.minute_from,
        minute_to=q.minute_to,
        limit=300,
    )

    by_user: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        name = str(row["username"] or "Аноним")
        if name.lower().startswith("user_"):
            continue
        by_user[name].append(row)

    people = await fetch_chat_participants(chat_id)
    names: list[str] = []
    for p in people:
        n = str(p["username"])
        if n.lower().startswith("user_"):
            continue
        if n not in names:
            names.append(n)
    for n in by_user:
        if n not in names:
            names.append(n)

    if not names and not rows:
        return (
            f"🐷 За **{window}** в базе **тишина** — никто не писал.\n\n"
            "💬 Напишите в чат пару сообщений, чтобы я запомнил."
        )

    lines = [
        f"🐷 **Кто что писал** — **{window}**\n",
        f"👥 Участников в базе: **{len(names)}**\n",
    ]

    for name in names:
        msgs = by_user.get(name, [])
        if not msgs:
            lines.append(f"\n🔹 **{name}** — **молчал**\n")
            continue
        lines.append(f"\n🔹 **{name}** — **{len(msgs)}** сообщ.\n")
        for row in msgs[:12]:
            lines.append(f"   {_format_row(row)}\n")
        if len(msgs) > 12:
            pw = _PERIOD_LABEL.get(q.period, "вчера")
            lines.append(
                f"   💬 … ещё **{len(msgs) - 12}** — **Свин, что писал {pw} {name}**\n"
            )

    lines.append(
        "\n✅ По одному: **Свин, что писал вчера Имя** — подставь ник из списка."
    )
    return "\n".join(lines)


async def _one_user_markdown(chat_id: int, q: UserLogQuery) -> str:
    assert q.username
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
            "💬 Спроси **«кто в чате»** — сверь ник.\n\n"
            "👥 Или **«кто что писал вчера»** — сводка по всем."
        )

    if q.when_only and q.phrase:
        header = (
            f"🐷 **{q.username}** — **во сколько** про «{q.phrase}» "
            f"({window}, {len(rows)} совпад.)\n"
        )
    else:
        header = (
            f"🐷 **{q.username}** — сообщения **{window}** ({len(rows)} шт.)\n"
        )

    lines = [header]
    emojis = ("💬", "📝", "🗨️", "📌", "🔹", "✨", "🎯", "📎", "🧩", "🐽")
    for i, row in enumerate(rows):
        mark = emojis[i % len(emojis)]
        lines.append(f"{mark} {_format_row(row)}\n")

    lines.append("\n✅ Время и текст из **Supabase**.")
    return "\n".join(lines)


async def user_messages_markdown(chat_id: int, text: str) -> str | None:
    q = parse_user_log_request(text)
    if not q:
        return None

    if not is_memory_enabled():
        return (
            "🐷 Память чата не настроена — не вижу, кто что писал.\n\n"
            "🎞️ Ссылки Instagram кидай — видео пришлю."
        )

    if q.username is None:
        return await _all_users_markdown(chat_id, q)
    return await _one_user_markdown(chat_id, q)
