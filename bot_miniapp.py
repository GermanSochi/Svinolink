from __future__ import annotations

from config import settings


def miniapp_url_for_chat(chat_id: int | None = None) -> str | None:
    base = settings.miniapp_url
    if not base:
        return None
    if chat_id is not None:
        return f"{base}?chat_id={chat_id}"
    return base
