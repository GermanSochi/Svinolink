"""Поиск в интернете и «энциклопедия» в групповом чате."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram.types import Message

import ai_quota
from web_search import (
    extract_http_url,
    fetch_knowledge_pages,
    fetch_page_preview,
    format_page_markdown,
    format_search_markdown,
    resolve_search_query,
    search_web,
    wants_search_links,
    wants_url_read,
)
from web_search_synthesis import synthesize_search_answer

logger = logging.getLogger(__name__)


@dataclass
class WebSearchReply:
    text: str
    photo_bytes: bytes | None = None


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


async def try_web_search_reply(message: Message) -> WebSearchReply | None:
    text = (message.text or message.caption or "").strip()

    url_reply = await try_url_read_reply(message)
    if url_reply:
        return WebSearchReply(text=url_reply)

    query, knowledge_mode = resolve_search_query(text)
    if not query:
        return None

    uid = message.from_user.id if message.from_user else 0
    show_links = wants_search_links(text)

    logger.info(
        "web_search chat=%s query=%r knowledge=%s links=%s",
        message.chat.id,
        query[:120],
        knowledge_mode,
        show_links,
    )

    results = await search_web(query, max_results=8)
    if not results:
        return WebSearchReply(text=format_search_markdown(query, []))

    if show_links:
        return WebSearchReply(text=format_search_markdown(query, results, max_show=3))

    if uid and not ai_quota.can_ask(uid):
        return WebSearchReply(
            text=(
                "🐷 Лимит вопросов «Свин» на час выбран.\n\n"
                "💬 Попробуй позже или напиши **«со ссылками»** — "
                "дам короткий список без ИИ."
            )
        )

    pages, photo_bytes, wiki_extra = await fetch_knowledge_pages(query, results)
    answer = await synthesize_search_answer(
        query,
        results,
        pages,
        wiki_extra=wiki_extra or None,
        detailed=knowledge_mode,
        chat_id=message.chat.id,
    )
    if uid:
        ai_quota.record(uid)
    return WebSearchReply(text=answer, photo_bytes=photo_bytes)
