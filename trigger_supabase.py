from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from typing import TYPE_CHECKING

from chat_memory import _connect_once, _pool, database_url, is_memory_enabled

if TYPE_CHECKING:
    from store import TriggerRule

logger = logging.getLogger(__name__)

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

CREATE_TRIGGERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS chat_triggers (
    chat_id BIGINT NOT NULL,
    trigger_id TEXT NOT NULL,
    words JSONB NOT NULL,
    response TEXT NOT NULL,
    once_per_day BOOLEAN NOT NULL DEFAULT FALSE,
    match_mode TEXT NOT NULL DEFAULT 'exact',
    added_by_user_id BIGINT,
    added_by_username TEXT,
    sort_order INT NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, trigger_id)
);
"""

CREATE_TRIGGER_DAILY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS trigger_daily (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    trigger_id TEXT NOT NULL,
    used_on DATE NOT NULL DEFAULT CURRENT_DATE,
    PRIMARY KEY (chat_id, user_id, trigger_id, used_on)
);
"""

CREATE_TRIGGERS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_chat_triggers_chat
ON chat_triggers (chat_id, sort_order);
"""


def run_async(coro):
    """Вызов async Supabase из sync-кода (store, sqlite-пути)."""
    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False
    if not in_loop:
        return asyncio.run(coro)
    return _executor.submit(asyncio.run, coro).result(timeout=60)


def _row_to_rule(row) -> TriggerRule:
    from store import TriggerRule

    words_raw = row["words"]
    if isinstance(words_raw, str):
        words = json.loads(words_raw)
    else:
        words = list(words_raw)
    return TriggerRule(
        id=str(row["trigger_id"]),
        words=[str(w).lower() for w in words],
        response=str(row["response"]),
        once_per_day=bool(row["once_per_day"]),
        match=str(row["match_mode"] or "exact"),
        builtin=False,
        added_by_user_id=row["added_by_user_id"],
        added_by_username=row["added_by_username"],
    )


async def ensure_trigger_tables(conn) -> None:
    await conn.execute(CREATE_TRIGGERS_TABLE_SQL)
    await conn.execute(CREATE_TRIGGER_DAILY_TABLE_SQL)
    await conn.execute(CREATE_TRIGGERS_INDEX_SQL)


async def load_custom_triggers(chat_id: int) -> list[TriggerRule]:
    if not is_memory_enabled():
        return []

    query = """
        SELECT trigger_id, words, response, once_per_day, match_mode,
               added_by_user_id, added_by_username
        FROM chat_triggers
        WHERE chat_id = $1
        ORDER BY sort_order ASC, trigger_id ASC
    """
    if _pool is not None:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(query, chat_id)
    else:
        url = database_url()
        if not url:
            return []
        conn = await _connect_once(url)
        try:
            rows = await conn.fetch(query, chat_id)
        finally:
            await conn.close()

    return [_row_to_rule(row) for row in rows]


async def save_custom_triggers(chat_id: int, rules: list[TriggerRule]) -> None:
    if not is_memory_enabled():
        return

    async def _save(conn) -> None:
        async with conn.transaction():
            await conn.execute("DELETE FROM chat_triggers WHERE chat_id = $1", chat_id)
            for idx, rule in enumerate(rules):
                await conn.execute(
                    """
                    INSERT INTO chat_triggers (
                        chat_id, trigger_id, words, response, once_per_day,
                        match_mode, added_by_user_id, added_by_username, sort_order
                    )
                    VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7, $8, $9)
                    """,
                    chat_id,
                    rule.id,
                    json.dumps(rule.words, ensure_ascii=False),
                    rule.response,
                    rule.once_per_day,
                    rule.match,
                    rule.added_by_user_id,
                    rule.added_by_username,
                    idx,
                )

    if _pool is not None:
        async with _pool.acquire() as conn:
            await _save(conn)
    else:
        url = database_url()
        if not url:
            return
        conn = await _connect_once(url)
        try:
            await _save(conn)
        finally:
            await conn.close()

    logger.info("chat_triggers saved chat_id=%s count=%s", chat_id, len(rules))


async def was_trigger_used_today(
    chat_id: int, user_id: int, trigger_id: str
) -> bool:
    if not is_memory_enabled():
        return False

    query = """
        SELECT 1 FROM trigger_daily
        WHERE chat_id = $1 AND user_id = $2 AND trigger_id = $3
          AND used_on = CURRENT_DATE
        LIMIT 1
    """
    if _pool is not None:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(query, chat_id, user_id, trigger_id)
    else:
        url = database_url()
        if not url:
            return False
        conn = await _connect_once(url)
        try:
            row = await conn.fetchrow(query, chat_id, user_id, trigger_id)
        finally:
            await conn.close()
    return row is not None


async def mark_trigger_used_today(
    chat_id: int, user_id: int, trigger_id: str
) -> None:
    if not is_memory_enabled():
        return

    query = """
        INSERT INTO trigger_daily (chat_id, user_id, trigger_id, used_on)
        VALUES ($1, $2, $3, CURRENT_DATE)
        ON CONFLICT DO NOTHING
    """
    if _pool is not None:
        async with _pool.acquire() as conn:
            await conn.execute(query, chat_id, user_id, trigger_id)
    else:
        url = database_url()
        if not url:
            return
        conn = await _connect_once(url)
        try:
            await conn.execute(query, chat_id, user_id, trigger_id)
        finally:
            await conn.close()


async def delete_custom_triggers(chat_id: int) -> None:
    if not is_memory_enabled():
        return

    async def _delete(conn) -> None:
        async with conn.transaction():
            await conn.execute("DELETE FROM chat_triggers WHERE chat_id = $1", chat_id)
            await conn.execute("DELETE FROM trigger_daily WHERE chat_id = $1", chat_id)

    if _pool is not None:
        async with _pool.acquire() as conn:
            await _delete(conn)
    else:
        url = database_url()
        if not url:
            return
        conn = await _connect_once(url)
        try:
            await _delete(conn)
        finally:
            await conn.close()
