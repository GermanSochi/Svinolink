"""Определение вопросов про список триггеров (без GPT)."""
from __future__ import annotations

import re

TRIGGER_LIST_RE = re.compile(
    r"(?i)(?:"
    r"триг[а-яё]*"
    r"|trigger"
    r"|производн[а-яё]*"
    r"|ключев[а-яё]*\s+слов[а-яё]*"
    r"|что\s+настро[а-яё]*"
    r"|список\s+(?:[а-яёa-z0-9_-]+\s+){0,3}(?:триг[а-яё]*|слов[а-яё]*|производн[а-яё]*)"
    r"|сколько\s+триг[а-яё]*"
    r"|активн[а-яё]*\s+триг[а-яё]*"
    r")"
)


def is_trigger_list_question(text: str) -> bool:
    blob = text.strip()
    if not blob:
        return False
    if TRIGGER_LIST_RE.search(blob):
        return True
    lower = blob.lower()
    if "какие" in lower and any(
        w in lower for w in ("триг", "слов", "производ", "актив", "настро")
    ):
        return True
    return False
