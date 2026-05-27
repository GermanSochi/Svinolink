"""Определение вопросов про список триггеров (без GPT)."""
from __future__ import annotations

import re

TRIGGER_LIST_RE = re.compile(
    r"(?i)(?:"
    r"тригger?\w*"
    r"|trigger"
    r"|производн"
    r"|ключев\w*\s+слов"
    r"|что\s+настро"
    r"|список\s+(?:\w+\s+){0,3}(?:триг|слов|производ)"
    r"|сколько\s+триг"
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
