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


def parse_init_data(init_data: str, *, fallback_chat_id: int | None = None) -> WebAppSession:
    if not init_data:
        raise ValueError("нет initData")
    parsed = safe_parse_webapp_init_data(settings.bot_token, init_data)
    if not parsed.user:
        raise ValueError("нет пользователя")
    chat_id = fallback_chat_id
    if parsed.chat:
        chat_id = parsed.chat.id
    if chat_id is None and parsed.start_param:
        raw = parsed.start_param.strip()
        if raw.startswith("chat_") and raw[5:].lstrip("-").isdigit():
            chat_id = int(raw[5:])
    if chat_id is None:
        raise ValueError("открой Mini App из группового чата")
    return WebAppSession(
        user_id=parsed.user.id,
        chat_id=chat_id,
        username=parsed.user.username,
    )
