from __future__ import annotations

import logging
from typing import Any

import asyncpg

from config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chat_history (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    username TEXT,
    message_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chat_history_chat_created
ON chat_history (chat_id, created_at DESC);
"""


def is_memory_enabled() -> bool:
    return bool(settings.supabase_database_url.strip())


async def init_chat_memory() -> None:
    """Создаёт таблицу chat_history и пул подключений к Supabase PostgreSQL."""
    global _pool
    url = settings.supabase_database_url.strip()
    if not url:
        logger.warning("SUPABASE_DATABASE_URL не задан — память чата отключена")
        return

    _pool = await asyncpg.create_pool(
        url,
        min_size=1,
        max_size=4,
        command_timeout=30,
        statement_cache_size=0,
    )
    async with _pool.acquire() as conn:
        await conn.execute(CREATE_TABLE_SQL)
        await conn.execute(CREATE_INDEX_SQL)
    logger.info("Supabase chat_history ready")


async def close_chat_memory() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def log_message(
    *,
    chat_id: int,
    user_id: int,
    username: str,
    message_text: str,
) -> None:
    if _pool is None:
        return
    text = message_text[:4000]
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO chat_history (chat_id, user_id, username, message_text)
                VALUES ($1, $2, $3, $4)
                """,
                chat_id,
                user_id,
                username,
                text,
            )
            await conn.execute(
                """
                DELETE FROM chat_history
                WHERE created_at < NOW() - INTERVAL '3 days'
                """
            )


async def fetch_last_24h(chat_id: int) -> list[dict[str, Any]]:
    if _pool is None:
        return []
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT username, message_text, created_at
            FROM chat_history
            WHERE chat_id = $1
              AND created_at >= NOW() - INTERVAL '24 hours'
            ORDER BY created_at ASC
            """,
            chat_id,
        )
    return [
        {
            "username": row["username"] or "Аноним",
            "message_text": row["message_text"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
