"""Обработка «Свин, найди в интернете …» в групповом чате."""
from __future__ import annotations

import logging

from aiogram.types import Message

import ai_quota
from web_search import (
    extract_http_url,
    extract_search_query,
    fetch_page_preview,
    fetch_top_pages,
    format_page_markdown,
    format_search_markdown,
    search_web,
    wants_search_links,
    wants_url_read,
)
from web_search_synthesis import synthesize_search_answer

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

    uid = message.from_user.id if message.from_user else 0
    show_links = wants_search_links(text)

    logger.info(
        "web_search chat=%s query=%r links=%s",
        message.chat.id,
        query[:120],
        show_links,
    )

    results = await search_web(query, max_results=6)
    if not results:
        return format_search_markdown(query, [])

    if show_links:
        return format_search_markdown(query, results, max_show=3)

    if uid and not ai_quota.can_ask(uid):
        return (
            "🐷 Лимит вопросов «Свин» на час выбран.\n\n"
            "💬 Попробуй позже или напиши **«со ссылками»** — "
            "дам короткий список без ИИ."
        )

    pages = await fetch_top_pages(results, limit=3)
    answer = await synthesize_search_answer(query, results, pages)
    if uid:
        ai_quota.record(uid)
    return answer
