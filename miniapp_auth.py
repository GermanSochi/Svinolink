from __future__ import annotations

import json
from dataclasses import dataclass

from aiogram.utils.web_app import safe_parse_webapp_init_data

from config import settings


@dataclass
class WebAppSession:
    user_id: int
    chat_id: int
    username: str | None


def parse_user_session(init_data: str) -> WebAppSession:
    if not init_data:
        raise ValueError("нет initData — открой Mini App из Telegram")
    parsed = safe_parse_webapp_init_data(settings.bot_token, init_data)
    if not parsed.user:
        raise ValueError("нет пользователя")
    chat_id = parsed.chat.id if parsed.chat else 0
    return WebAppSession(
        user_id=parsed.user.id,
        chat_id=chat_id,
        username=parsed.user.username,
    )


def parse_init_data(init_data: str, *, fallback_chat_id: int | None = None) -> WebAppSession:
    if not init_data:
        raise ValueError("нет initData — открой Mini App из Telegram")
    parsed = safe_parse_webapp_init_data(settings.bot_token, init_data)
    if not parsed.user:
        raise ValueError("нет пользователя")

    # URL ?chat_id= из группы — приоритетнее parsed.chat (иначе грузится не тот чат)
    chat_id: int | None = None
    if fallback_chat_id is not None:
        chat_id = fallback_chat_id
    elif parsed.chat:
        chat_id = parsed.chat.id
    elif parsed.start_param:
        raw = parsed.start_param.strip()
        if raw.startswith("chat_") and raw[5:].lstrip("-").isdigit():
            chat_id = int(raw[5:])

    if chat_id is None:
        raise ValueError("выбери группу в Mini App из лички бота")

    return WebAppSession(
        user_id=parsed.user.id,
        chat_id=chat_id,
        username=parsed.user.username,
    )
