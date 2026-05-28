"""Проверка админов бота (без импорта из chat_handlers)."""
from __future__ import annotations

from config import settings


def is_admin_user(user_id: int, username: str | None) -> bool:
    if user_id in settings.admin_ids:
        return True
    if username and username.lower().lstrip("@") in settings.admin_usernames:
        return True
    return False
