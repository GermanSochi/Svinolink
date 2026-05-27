from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerAdd:
    word: str
    response: str
    once_per_day: bool
    match: str  # exact | contains | word


@dataclass(frozen=True)
class TriggerDelete:
    indices_1based: list[int]


@dataclass(frozen=True)
class TriggerUpdate:
    index_1based: int
    word: str | None
    response: str | None
    match: str | None  # exact | contains | word


_ADD_RE = re.compile(
    r"(?is)\b(?:свин(?:ья)?\s*,?\s*)?"
    r"(?:добавь|добавить|создай|сделай)\s+триг+ер(?:\s+в\s+(?:базу|супабейс|supabase))?"
    r"\s*[:\-—]?\s*(.+)$"
)
_DEL_RE = re.compile(
    r"(?is)\b(?:свин(?:ья)?\s*,?\s*)?"
    r"(?:удали|удалить|сотри|снести|убери|убрать)\s+триг+ер(?:ы)?\s*[:\-—]?\s*(.+)$"
)
_UPD_RE = re.compile(
    r"(?is)\b(?:свин(?:ья)?\s*,?\s*)?"
    r"(?:измени|поменяй|правь|обнови|обновить)\s+триг+ер\s*[:\-—]?\s*(.+)$"
)

_UPD_ALT_RE = re.compile(
    r"(?is)\b(?:свин(?:ья)?\s*,?\s*)?"
    r"триг+ер\s*[:\-—]?\s*(\d+)\s+"
    r"(?:измени|поменяй|правь|обнови|обновить)\s*[:\-—]?\s*(.+)$"
)

_UPD_ALT2_RE = re.compile(
    r"(?is)\b(?:свин(?:ья)?\s*,?\s*)?"
    r"триг+ер\s*[:\-—]?\s*(\d+)\s+"
    r"(?:измени|поменяй|правь|обнови|обновить)\b"
    r"(?:\s+на)?\s+(.+)$"
)


def parse_trigger_manage(text: str | None) -> TriggerAdd | TriggerDelete | TriggerUpdate | None:
    if not text:
        return None
    t = text.strip()
    # типичная опечатка: "в тексе"
    t = re.sub(r"(?is)\bв\s+тексе\b", "в тексте", t)

    m = _ADD_RE.search(t)
    if m:
        payload = (m.group(1) or "").strip()
        return _parse_add_payload(payload)

    m = _DEL_RE.search(t)
    if m:
        payload = (m.group(1) or "").strip()
        return _parse_del_payload(payload)

    m = _UPD_RE.search(t)
    if m:
        payload = (m.group(1) or "").strip()
        return _parse_upd_payload(payload)

    m = _UPD_ALT_RE.search(t)
    if m:
        payload = f"{m.group(1)} {m.group(2)}".strip()
        return _parse_upd_payload(payload)

    m = _UPD_ALT2_RE.search(t)
    if m:
        payload = f"{m.group(1)} {m.group(2)}".strip()
        return _parse_upd_payload(payload)

    return None


def _parse_add_payload(payload: str) -> TriggerAdd | None:
    # варианты:
    # "слово НЕТ ответ МИНЕТ"
    # "нет -> минет"
    # "нет = минет"
    # "нет минет" (последний — хуже, но поддержим если есть ключевые слова)
    low = payload.lower()
    # Частый человеческий вариант: "на слово НЕТ ответ МИНЕТ"
    payload = re.sub(r"(?is)^\s*на\s+слово\s+", "слово ", payload).strip()
    once = "1/день" in low or "раз в сутки" in low or "daily" in low
    match = _parse_match_mode(low)

    # слово/ответ по ключам
    m = re.search(r"(?is)\bслово\s+(.+?)\s+\bответ\s+(.+)$", payload)
    if m:
        return TriggerAdd(
            word=m.group(1).strip(),
            response=m.group(2).strip(),
            once_per_day=once,
            match=match,
        )

    # стрелка/равно
    m = re.split(r"\s*(?:->|→|=|=>)\s*", payload, maxsplit=1)
    if len(m) == 2 and m[0].strip() and m[1].strip():
        return TriggerAdd(
            word=m[0].strip(),
            response=m[1].strip(),
            once_per_day=once,
            match=match,
        )

    return None


def _parse_del_payload(payload: str) -> TriggerDelete | None:
    # ожидаем номера: "1" или "1 2 5" или "1,2,5"
    nums = re.findall(r"\d+", payload)
    if not nums:
        return None
    indices = [int(x) for x in nums if int(x) > 0]
    if not indices:
        return None
    return TriggerDelete(indices_1based=sorted(set(indices)))


def _parse_upd_payload(payload: str) -> TriggerUpdate | None:
    # "2 слово ДА ответ ПИЗДА"
    # "2 ответ новый текст"
    m = re.match(r"(?is)\s*(\d+)\s+(.+)$", payload)
    if not m:
        return None
    idx = int(m.group(1))
    rest = m.group(2).strip()
    if idx <= 0 or not rest:
        return None

    word: str | None = None
    resp: str | None = None
    match: str | None = None

    m2 = re.search(r"(?is)\bслово\s+(.+?)(?:\s+\bответ\b\s+|$)", rest)
    if m2:
        word = m2.group(1).strip()

    m3 = re.search(r"(?is)\bответ\s+(.+)$", rest)
    if m3:
        resp = m3.group(1).strip()

    match = _parse_match_mode(rest.lower(), allow_none=True)

    if word is None and resp is None and match is None:
        return None
    return TriggerUpdate(index_1based=idx, word=word, response=resp, match=match)


def _parse_match_mode(low: str, *, allow_none: bool = False) -> str | None:
    """
    Режимы:
    - exact: сообщение целиком равно слову (пример: "да" сработает, "сегодня ... да" — нет)
    - contains: содержится в тексте (срабатывает внутри предложения)
    - word: отдельное слово как токен внутри текста (пример: "сегодня ... да" — да, но "правда" — нет)
    """
    # Два "человеческих" режима по умолчанию:
    # - "режим слово" => exact (сообщение равно слову)
    # - "режим в тексте" => contains (встречается в сообщении)
    if any(x in low for x in ("режим слово", "только слово", "по слову", "слово только")):
        return "exact"
    # Токен-матчинг включаем только если явно попросили.
    if any(x in low for x in ("отдельное слово в тексте", "токен", "границы слова", "word boundary", "word")):
        return "word"
    if any(x in low for x in ("в тексте", "содержит", "contains", "внутри текста")):
        return "contains"
    if any(x in low for x in ("точно", "строго", "exact")):
        return "exact"
    return None if allow_none else "exact"

