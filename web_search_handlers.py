"""Обработка «Свин, найди в интернете …» в групповом чате."""
from __future__ import annotations

import logging

from aiogram.types import Message

from web_search import extract_search_query, format_search_markdown, search_web

logger = logging.getLogger(__name__)


async def try_web_search_reply(message: Message) -> str | None:
    text = (message.text or message.caption or "").strip()
    query = extract_search_query(text)
    if not query:
        return None

    logger.info("web_search chat=%s query=%r", message.chat.id, query[:120])
    results = await search_web(query, max_results=5)
    return format_search_markdown(query, results)
