from __future__ import annotations

import logging

from sqlalchemy import text

from game_db import ENGINE
from game_models import Base

logger = logging.getLogger(__name__)


async def init_game_db() -> None:
    async with ENGINE.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # небольшая sanity-проверка
        await conn.execute(text("SELECT 1"))
    logger.info("game_db initialized")

