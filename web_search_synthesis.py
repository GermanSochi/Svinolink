"""Сводка результатов поиска через Yandex GPT — один короткий ответ."""
from __future__ import annotations

import logging

from web_search import (
    SEARCH_SYNTHESIS_SYSTEM,
    build_search_evidence,
    wrap_short_answer,
)

logger = logging.getLogger(__name__)


async def synthesize_search_answer(
    query: str,
    results: list[dict],
    pages: list[tuple[str, dict[str, str]]],
) -> str:
    from deps import gpt

    evidence = build_search_evidence(query, results, pages)
    prompt = (
        f"{evidence}\n\n"
        "Дай один короткий ответ на запрос. Только текст ответа, без ссылок."
    )
    try:
        answer = await gpt.reply(prompt, system=SEARCH_SYNTHESIS_SYSTEM)
    except Exception as exc:
        logger.error("search synthesis failed: %s", exc)
        from bot_messages import gpt_glitch_message

        return (
            f"🔎 По **«{query}»** нашёл в сети, но свести не вышло.\n\n"
            f"{gpt_glitch_message()}"
        )
    return wrap_short_answer(query, answer)
