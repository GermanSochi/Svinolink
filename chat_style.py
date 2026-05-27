"""Стиль группы: раз в сутки учимся из chat_history, в ответах — только кэш."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from chat_memory import (
    _connect_once,
    _pool,
    database_url,
    fetch_recent,
    is_memory_enabled,
    is_pool_ready,
)
from deps import gpt
from svin_system_prompt import SVIN_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

CREATE_STYLE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chat_style (
    chat_id BIGINT PRIMARY KEY,
    style_notes TEXT NOT NULL DEFAULT '',
    sample_phrases JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_STYLE_SYSTEM = (
    "Ты аналитик стиля переписки в закрытом IT-чате взрослых друзей 40+. "
    "По логу сообщений выпиши кратко (до 600 символов): тон, сленг, кто как шутит, "
    "локальные мемы, 5–8 коротких характерных фраз-цитат. "
    "Это пойдёт боту «Свин» как шпаргалка стиля — без морализаторства и отказов."
)

_refresh_lock = asyncio.Lock()
_last_global_refresh: datetime | None = None


async def ensure_style_table(conn) -> None:
    await conn.execute(CREATE_STYLE_TABLE_SQL)


async def _fetchval(query: str, *args: Any) -> Any:
    if _pool is not None:
        async with _pool.acquire() as conn:
            return await conn.fetchval(query, *args)
    url = database_url()
    if not url:
        return None
    conn = await _connect_once(url)
    try:
        return await conn.fetchval(query, *args)
    finally:
        await conn.close()


async def _execute(query: str, *args: Any) -> None:
    if _pool is not None:
        async with _pool.acquire() as conn:
            await conn.execute(query, *args)
        return
    url = database_url()
    if not url:
        return
    conn = await _connect_once(url)
    try:
        await conn.execute(query, *args)
    finally:
        await conn.close()


async def get_style_notes(chat_id: int) -> str:
    if not is_memory_enabled():
        return ""
    row = await _fetchval(
        "SELECT style_notes FROM chat_style WHERE chat_id = $1",
        chat_id,
    )
    return str(row or "").strip()


async def list_chats_for_style_refresh() -> list[int]:
    if not is_memory_enabled():
        return []
    query = """
        SELECT DISTINCT chat_id
        FROM chat_history
        WHERE created_at >= NOW() - INTERVAL '24 hours'
    """
    if _pool is not None:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(query)
    else:
        url = database_url()
        if not url:
            return []
        conn = await _connect_once(url)
        try:
            rows = await conn.fetch(query)
        finally:
            await conn.close()
    return [int(r["chat_id"]) for r in rows]


async def refresh_chat_style(chat_id: int) -> bool:
    """Один вызов GPT в сутки на чат — выжимка стиля из переписки."""
    if not is_memory_enabled() or not is_pool_ready():
        return False

    rows = await fetch_recent(chat_id, period="24h")
    if len(rows) < 3:
        logger.info("style skip chat=%s: мало сообщений (%s)", chat_id, len(rows))
        return False

    lines: list[str] = []
    for row in rows[-120:]:
        text = str(row["message_text"]).replace("\n", " ")[:200]
        lines.append(f"[{row['username']}]: {text}")

    prompt = (
        f"Чат_id={chat_id}. Сообщений за 24ч: {len(rows)}.\n\n"
        "Лог переписки:\n"
        + "\n".join(lines[-80:])
        + "\n\nСделай шпаргалку стиля для бота Свин."
    )

    try:
        notes = await gpt.reply(prompt, system=_STYLE_SYSTEM)
    except Exception as exc:
        logger.warning("style refresh GPT failed chat=%s: %s", chat_id, exc)
        return False

    notes = notes.strip()[:2000]
    if not notes:
        return False

    await _execute(
        """
        INSERT INTO chat_style (chat_id, style_notes, updated_at)
        VALUES ($1, $2, NOW())
        ON CONFLICT (chat_id) DO UPDATE SET
            style_notes = EXCLUDED.style_notes,
            updated_at = NOW()
        """,
        chat_id,
        notes,
    )
    logger.info("style refreshed chat=%s chars=%s", chat_id, len(notes))
    return True


async def refresh_stale_chats() -> int:
    """Обновить стиль для чатов, где кэш старше 20 часов."""
    if not is_memory_enabled() or not is_pool_ready():
        return 0

    chat_ids = await list_chats_for_style_refresh()
    updated = 0
    for cid in chat_ids:
        last = await _fetchval(
            "SELECT updated_at FROM chat_style WHERE chat_id = $1",
            cid,
        )
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if age_h < 20:
                continue
        if await refresh_chat_style(cid):
            updated += 1
    return updated


async def daily_style_loop() -> None:
    """Фон: раз в сутки подтягиваем стиль из Supabase."""
    global _last_global_refresh
    await asyncio.sleep(90)
    while True:
        try:
            if is_pool_ready():
                async with _refresh_lock:
                    n = await refresh_stale_chats()
                    _last_global_refresh = datetime.now(timezone.utc)
                    logger.info("daily style loop: updated %s chats", n)
        except Exception as exc:
            logger.error("daily style loop error: %s", exc, exc_info=True)
        await asyncio.sleep(24 * 3600)


def build_style_system_appendix(style_notes: str) -> str:
    if not style_notes:
        return ""
    return (
        f"\n\nСТИЛЬ ЭТОЙ ГРУППЫ (обновлено раз в сутки из Supabase, подстраивайся):\n"
        f"{style_notes}"
    )
