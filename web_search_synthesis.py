"""Сводка результатов поиска через Yandex GPT."""
from __future__ import annotations

import logging

from web_search import (
    KNOWLEDGE_SYNTHESIS_SYSTEM,
    SEARCH_SYNTHESIS_SYSTEM,
    build_search_evidence,
    build_snippet_fallback,
    build_wiki_fallback,
    is_meta_refusal_answer,
    wrap_short_answer,
)

logger = logging.getLogger(__name__)


async def synthesize_search_answer(
    query: str,
    results: list[dict],
    pages: list[tuple[str, dict[str, str]]],
    *,
    wiki_extra: dict[str, str] | None = None,
    detailed: bool = False,
) -> str:
    from deps import gpt

    evidence = build_search_evidence(query, results, pages, wiki_extra=wiki_extra)
    if detailed:
        system = KNOWLEDGE_SYNTHESIS_SYSTEM
        user_tail = (
            "Дай развёрнутый, но компактный ответ на запрос. "
            "Только текст, без ссылок. Сразу по сути, без общих определений."
        )
        max_len = 1700
    else:
        system = SEARCH_SYNTHESIS_SYSTEM
        user_tail = "Дай один короткий ответ на запрос. Только текст ответа, без ссылок."
        max_len = 1100

    prompt = f"{evidence}\n\n{user_tail}"
    try:
        answer = await gpt.reply(prompt, system=system)
    except Exception as exc:
        logger.error("search synthesis failed: %s", exc)
        if wiki_extra:
            wiki_fb = build_wiki_fallback(query, wiki_extra)
            if wiki_fb:
                return wiki_fb
        if results:
            return build_snippet_fallback(query, results)
        from bot_messages import gpt_glitch_message

        return (
            f"🔎 По **«{query}»** нашёл в сети, но свести не вышло.\n\n"
            f"{gpt_glitch_message()}"
        )

    if is_meta_refusal_answer(answer):
        logger.warning(
            "search synthesis meta-refusal for %r",
            query[:80],
        )
        if wiki_extra:
            wiki_fb = build_wiki_fallback(query, wiki_extra)
            if wiki_fb:
                return wiki_fb
        if results:
            return build_snippet_fallback(query, results)

    return wrap_short_answer(query, answer, max_len=max_len)
