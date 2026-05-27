from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

import asyncpg

from config import settings

logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None
_pool_init_failed: bool = False

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


@dataclass(frozen=True)
class PgParams:
    user: str
    password: str
    host: str
    port: int
    database: str


def normalize_database_url(raw: str) -> str:
    """Чистит URL: кавычки, https:// в части хоста после @."""
    url = raw.strip()
    if len(url) >= 2 and url[0] == url[-1] and url[0] in "\"'":
        url = url[1:-1].strip()

    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            scheme = prefix
            rest = url[len(prefix) :]
            break
    else:
        return url

    if "?" in rest:
        rest, query = rest.split("?", 1)
        query_suffix = f"?{query}"
    else:
        query_suffix = ""

    at = rest.rfind("@")
    if at == -1:
        return url

    userinfo = rest[:at]
    hostdb = rest[at + 1 :]
    if hostdb.startswith("https://"):
        hostdb = hostdb[len("https://") :]
    elif hostdb.startswith("http://"):
        hostdb = hostdb[len("http://") :]

    return f"{scheme}{userinfo}@{hostdb}{query_suffix}"


def parse_postgres_url(raw: str) -> PgParams:
    """Парсит URI через rfind('@') — пароль может содержать @, :, /, %."""
    url = normalize_database_url(raw)
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            rest = url[len(prefix) :]
            break
    else:
        raise ValueError("ожидается postgres:// или postgresql://")

    if "?" in rest:
        rest = rest.split("?", 1)[0]

    at = rest.rfind("@")
    if at == -1:
        raise ValueError("в URL нет @ между паролем и хостом")

    userinfo = rest[:at]
    hostdb = rest[at + 1 :]

    if "/" in hostdb:
        hostport, dbname = hostdb.split("/", 1)
        database = dbname or "postgres"
    else:
        hostport = hostdb
        database = "postgres"

    colon = userinfo.find(":")
    if colon == -1:
        raise ValueError("в URL нет : между логином и паролем")

    user = unquote(userinfo[:colon])
    password = unquote(userinfo[colon + 1 :])

    if ":" in hostport:
        host, port_raw = hostport.rsplit(":", 1)
        if not port_raw:
            port = 6543 if "supabase.com" in host else 5432
        elif not port_raw.isdigit():
            raise ValueError(f"неверный порт в URL: {hostport!r}")
        else:
            port = int(port_raw)
    else:
        host = hostport
        port = 6543 if "supabase.com" in hostport else 5432

    if not host:
        raise ValueError("хост пустой — проверьте SUPABASE_DATABASE_URL на Render")

    return PgParams(user=user, password=password, host=host, port=port, database=database)


def url_hint(raw: str) -> str:
    """Безопасная диагностика URL (без пароля) для /health/db."""
    try:
        params = parse_postgres_url(raw)
        return f"user={params.user} host={params.host}:{params.port} db={params.database}"
    except Exception as exc:
        url = raw.strip()
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                rest = url[len(prefix) :]
                break
        else:
            return f"bad scheme: {exc}"
        at = rest.rfind("@")
        if at == -1:
            return f"no @ before host: {exc}"
        tail = rest[at + 1 :]
        return f"tail after @={tail[:48]!r}… ({exc})"


def password_attempts(password: str) -> list[str]:
    """Пробуем decoded пароль и вариант без двойного %25 (Render иногда кодирует дважды)."""
    attempts = [password]
    if "%25" in password:
        attempts.append(password.replace("%25", "%"))
    if password != unquote(password):
        attempts.append(unquote(password))
    out: list[str] = []
    for item in attempts:
        if item not in out:
            out.append(item)
    return out


def _connect_kwargs(params: PgParams, password: str) -> dict[str, Any]:
    return {
        "host": params.host,
        "port": params.port,
        "user": params.user,
        "password": password,
        "database": params.database,
        "ssl": "require",
        "command_timeout": 30,
        "statement_cache_size": 0,
    }


async def _open_pool(url: str) -> asyncpg.Pool:
    params = parse_postgres_url(url)
    last_exc: Exception | None = None
    for pwd in password_attempts(params.password):
        try:
            return await asyncpg.create_pool(
                **_connect_kwargs(params, pwd),
                min_size=1,
                max_size=4,
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "asyncpg pool failed user=%s host=%s:%s: %s",
                params.user,
                params.host,
                params.port,
                exc,
            )
    assert last_exc is not None
    raise last_exc


async def _connect_once(url: str) -> asyncpg.Connection:
    params = parse_postgres_url(url)
    last_exc: Exception | None = None
    for pwd in password_attempts(params.password):
        try:
            return await asyncpg.connect(
                **_connect_kwargs(params, pwd),
                timeout=20,
            )
        except Exception as exc:
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def database_url() -> str:
    return normalize_database_url(settings.supabase_database_url.strip())


def is_memory_enabled() -> bool:
    return bool(settings.supabase_database_url.strip())


def is_pool_ready() -> bool:
    return _pool is not None


async def init_chat_memory() -> None:
    """Создаёт таблицу chat_history и пул подключений к Supabase PostgreSQL."""
    global _pool, _pool_init_failed
    url = database_url()
    if not url:
        logger.warning("SUPABASE_DATABASE_URL не задан — память чата отключена")
        return

    _pool_init_failed = False
    try:
        _pool = await _open_pool(url)
        async with _pool.acquire() as conn:
            await conn.execute(CREATE_TABLE_SQL)
            await conn.execute(CREATE_INDEX_SQL)
            from trigger_supabase import ensure_trigger_tables

            await ensure_trigger_tables(conn)
        logger.info("Supabase chat_history + chat_triggers ready")
    except Exception as exc:
        _pool = None
        _pool_init_failed = True
        logger.error(
            "Supabase init failed — бот стартует без памяти чата: %s; hint: %s",
            exc,
            url_hint(url),
            exc_info=True,
        )


async def _ping_connection(conn: asyncpg.Connection) -> tuple[bool, str]:
    ping = await conn.fetchval("SELECT 1")
    if ping != 1:
        return False, f"unexpected SELECT 1 result: {ping!r}"

    table = await conn.fetchval("SELECT to_regclass('public.chat_history')")
    if table is None:
        await conn.execute(CREATE_TABLE_SQL)
        await conn.execute(CREATE_INDEX_SQL)
        table = await conn.fetchval("SELECT to_regclass('public.chat_history')")

    return True, f"chat_history={table}"


async def check_connection(url: str | None = None) -> tuple[bool, str]:
    """SELECT 1 + проверка таблицы chat_history. Для health/test."""
    raw = normalize_database_url((url or settings.supabase_database_url).strip())
    if not raw:
        return False, "SUPABASE_DATABASE_URL not set"

    errors: list[str] = []

    try:
        conn = await _connect_once(raw)
        try:
            return await _ping_connection(conn)
        finally:
            await conn.close()
    except Exception as exc:
        errors.append(f"parsed: {exc}; {url_hint(raw)}")

    return False, " | ".join(errors)


async def close_chat_memory() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def _insert_message(
    conn: asyncpg.Connection,
    *,
    chat_id: int,
    user_id: int,
    username: str,
    message_text: str,
) -> None:
    text = message_text[:4000]
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


async def log_message(
    *,
    chat_id: int,
    user_id: int,
    username: str,
    message_text: str,
) -> None:
    if _pool is not None:
        async with _pool.acquire() as conn:
            await _insert_message(
                conn,
                chat_id=chat_id,
                user_id=user_id,
                username=username,
                message_text=message_text,
            )
        return

    url = database_url()
    if not url:
        return

    conn = await _connect_once(url)
    try:
        await _insert_message(
            conn,
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            message_text=message_text,
        )
    finally:
        await conn.close()


async def fetch_recent(
    chat_id: int,
    *,
    period: str = "24h",
) -> list[dict[str, Any]]:
    if period == "today":
        where = "created_at >= CURRENT_DATE"
    elif period == "yesterday":
        where = (
            "created_at >= CURRENT_DATE - INTERVAL '1 day' "
            "AND created_at < CURRENT_DATE"
        )
    else:
        where = "created_at >= NOW() - INTERVAL '24 hours'"

    query = f"""
        SELECT username, message_text, created_at
        FROM chat_history
        WHERE chat_id = $1
          AND {where}
        ORDER BY created_at ASC
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

    return [
        {
            "username": row["username"] or "Аноним",
            "message_text": row["message_text"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


async def fetch_last_24h(chat_id: int) -> list[dict[str, Any]]:
    return await fetch_recent(chat_id, period="24h")


async def fetch_audit_rows(limit: int = 10) -> list[dict[str, Any]]:
    """Последние N строк для аудита (check_supabase / health)."""
    query = """
        SELECT chat_id, user_id, username, message_text, created_at
        FROM chat_history
        ORDER BY created_at DESC
        LIMIT $1
    """
    if _pool is not None:
        async with _pool.acquire() as conn:
            rows = await conn.fetch(query, limit)
    else:
        url = database_url()
        if not url:
            return []
        conn = await _connect_once(url)
        try:
            rows = await conn.fetch(query, limit)
        finally:
            await conn.close()

    return [
        {
            "chat_id": row["chat_id"],
            "user_id": row["user_id"],
            "username": row["username"] or "Аноним",
            "message_text": row["message_text"],
            "created_at": row["created_at"].isoformat()
            if row["created_at"]
            else None,
        }
        for row in rows
    ]
