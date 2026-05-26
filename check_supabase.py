"""Аудит Supabase: подключение + последние 10 строк chat_history."""
from __future__ import annotations

import asyncio
import os
import sys


async def main() -> int:
    url = os.getenv("SUPABASE_DATABASE_URL", "").strip()
    if not url:
        try:
            from config import settings

            url = settings.supabase_database_url.strip()
        except Exception:
            pass

    if not url:
        print("FAIL: SUPABASE_DATABASE_URL не задан (ни в env, ни в .env)")
        return 1

    masked = url.split("@")[-1] if "@" in url else "(hidden)"
    print(f"target: ...@{masked}")

    from chat_memory import (
        CREATE_INDEX_SQL,
        CREATE_TABLE_SQL,
        check_connection,
        normalize_database_url,
        url_hint,
    )

    normalized = normalize_database_url(url)
    if normalized != url:
        print("info: URL нормализован (https:// в хосте, кавычки и т.п.)")
    print(f"hint: {url_hint(normalized)}")

    ok, detail = await check_connection(normalized)
    if not ok:
        print("FAIL: подключение к Supabase не установлено")
        print(f"detail: {detail}")
        return 1

    print(f"OK: подключение успешно ({detail})")

    from chat_memory import _connect_once

    conn = await _connect_once(normalized)
    try:
        await conn.execute(CREATE_TABLE_SQL)
        await conn.execute(CREATE_INDEX_SQL)

        total = await conn.fetchval("SELECT COUNT(*) FROM chat_history")
        print(f"rows_total: {total}")

        rows = await conn.fetch(
            """
            SELECT chat_id, user_id, username, message_text, created_at
            FROM chat_history
            ORDER BY created_at DESC
            LIMIT 10
            """
        )
    finally:
        await conn.close()

    if not rows:
        print("WARN: таблица chat_history пуста — логирование не пишет или группа не активна")
        return 2

    print(f"last_{len(rows)}_messages:")
    for row in reversed(rows):
        name = row["username"] or f"user_{row['user_id']}"
        text = (row["message_text"] or "").replace("\n", " ")
        print(f"  Юзер [{name}]: {text}")
        print(f"    chat_id={row['chat_id']} at={row['created_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
