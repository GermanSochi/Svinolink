from __future__ import annotations

import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def _database_url() -> str:
    """
    По умолчанию SQLite в data/game.db.
    Если задан DATABASE_URL (Postgres), используем его.
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        # ожидаем postgresql://... -> asyncpg
        if url.startswith("postgresql://") and "+asyncpg" not in url:
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url
    # sqlite file
    return "sqlite+aiosqlite:///./data/game_ecosystem.db"


ENGINE: AsyncEngine = create_async_engine(
    _database_url(),
    future=True,
    pool_pre_ping=True,
)

SessionMaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
    ENGINE, expire_on_commit=False, autoflush=False, autocommit=False
)


@asynccontextmanager
async def session_scope() -> AsyncSession:
    async with SessionMaker() as session:
        yield session

