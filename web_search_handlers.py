"""Обработка «Свин, найди в интернете …» в групповом чате."""
from __future__ import annotations

import logging

from aiogram.types import Message

from web_search import (
    extract_http_url,
    extract_search_query,
    fetch_page_preview,
    format_page_markdown,
    format_search_markdown,
    search_web,
    wants_url_read,
)

logger = logging.getLogger(__name__)


async def try_url_read_reply(message: Message) -> str | None:
    text = (message.text or message.caption or "").strip()
    if not wants_url_read(text):
        return None
    url = extract_http_url(text)
    if not url:
        return None
    logger.info("url_read chat=%s url=%s", message.chat.id, url[:120])
    data = await fetch_page_preview(url)
    return format_page_markdown(url, data)


async def try_web_search_reply(message: Message) -> str | None:
    text = (message.text or message.caption or "").strip()

    url_reply = await try_url_read_reply(message)
    if url_reply:
        return url_reply

    query = extract_search_query(text)
    if not query:
        return None

    logger.info("web_search chat=%s query=%r", message.chat.id, query[:120])
    results = await search_web(query, max_results=5)
    return format_search_markdown(query, results)
