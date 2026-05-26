from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update

from deps import store

logger = logging.getLogger("svinolink.updates")


class LogUpdatesMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Update) and event.message:
            m: Message = event.message
            if m.chat.type in {"group", "supergroup"}:
                store.register_chat(
                    m.chat.id,
                    title=m.chat.title,
                    chat_type=m.chat.type,
                )
            text = (m.text or m.caption or "")[:200]
            logger.info(
                "IN chat_id=%s chat_type=%s user=%s text=%r",
                m.chat.id,
                m.chat.type,
                m.from_user.id if m.from_user else None,
                text,
            )
        return await handler(event, data)
