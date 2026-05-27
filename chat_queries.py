"""Запросы к истории чата / Supabase (без GPT)."""
from __future__ import annotations

import re

CHAT_EXAMPLES_RE = re.compile(
    r"(?i)(?:"
    r"примеры?\s+(?:из\s+)?(?:чат|баз|переписк|истори|супер)"
    r"|(?:из|со)\s+(?:чат|баз|супер\s*баз|переписк|истори)"
    r"|(?:дай|покаж|приведи|скинь|вытащи|накидай)\s+.{0,40}(?:пример|цитат|сообщен)"
    r"|(?:видишь|видишь\s+ли)\s+.{0,30}(?:истори|чат|переписк)"
    r"|что\s+(?:было|писали)\s+.{0,20}(?:в\s+)?чат"
    r")"
)


def is_chat_examples_request(text: str) -> bool:
    blob = text.strip()
    if not blob:
        return False
    if CHAT_EXAMPLES_RE.search(blob):
        return True
    lower = blob.lower()
    if "пример" in lower and any(
        w in lower for w in ("чат", "баз", "истори", "переписк", "супер")
    ):
        return True
    if any(w in lower for w in ("супер баз", "супербаз", "supabase")) and any(
        w in lower for w in ("пример", "истори", "чат", "дай", "покаж")
    ):
        return True
    return False


def needs_recent_history(text: str) -> bool:
    """Нужна ли короткая выборка истории для ответа (не полный дайджест)."""
    if is_chat_examples_request(text):
        return False
    lower = text.lower()
    if any(
        w in lower
        for w in (
            "номер",
            "цифр",
            "как зовут",
            "имя",
            "кто сказал",
            "что писал",
            "сколько",
            "когда",
            "вчера",
            "сегодня",
            "помнишь",
            "говорил",
        )
    ):
        return True
    return False
