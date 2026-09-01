from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, Update

from chat_memory import is_memory_enabled, is_pool_ready, log_message
from deps import store
from memory_handlers import display_name, should_silent_log

logger = logging.getLogger("svinolink.updates")

_pool_warned = False


class LogUpdatesMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        global _pool_warned
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
            if (
                is_memory_enabled()
                and m.chat.type in {"group", "supergroup"}
                and m.text
                and m.from_user
                and not m.from_user.is_bot
                and should_silent_log(m.text)
            ):
                if not is_pool_ready() and not _pool_warned:
                    _pool_warned = True
                    logger.error(
                        "chat_history: пул Supabase не поднят — сообщения НЕ пишутся в БД. "
                        "Проверь /health/db и SUPABASE_DATABASE_URL на Render."
                    )
                try:
                    await log_message(
                        chat_id=m.chat.id,
                        user_id=m.from_user.id,
                        username=display_name(m),
                        message_text=m.text.strip(),
                    )
                except Exception as exc:
                    logger.warning("chat_history log failed chat=%s: %s", m.chat.id, exc)
        return await handler(event, data)
