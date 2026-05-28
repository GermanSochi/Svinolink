"""Запросы к истории чата / Supabase (без GPT)."""
from __future__ import annotations

import re

from chat_query_models import UserLogQuery
from chat_time import parse_time_range

CHAT_EXAMPLES_RE = re.compile(
    r"(?i)(?:"
    r"примеры?\s+(?:из\s+)?(?:чат|баз|переписк|истори|супер)"
    r"|(?:из|со)\s+(?:чат|баз|супер\s*баз|переписк|истори)"
    r"|(?:дай|покаж|приведи|скинь|вытащи|накидай)\s+.{0,40}(?:пример|цитат|сообщен)"
    r"|(?:видишь|видишь\s+ли)\s+.{0,30}(?:истори|чат|переписк)"
    r"|что\s+(?:было|писали)\s+.{0,20}(?:в\s+)?чат"
    r")"
)

_TODAY_RE = re.compile(r"(?i)\bсегодня\b")
_YESTERDAY_RE = re.compile(r"(?i)\bвчера\b")
_DAY_BEFORE_RE = re.compile(r"(?i)\bпозавчера\b")

_WHO_IN_CHAT_RE = re.compile(
    r"(?i)(?:"
    r"кто\s+(?:в\s+)?(?:этом\s+)?чате"
    r"|кто\s+здесь"
    r"|участник(?:ы|ов)?\s+чата"
    r"|кто\s+есть"
    r")"
)

_WHO_IS_RE = re.compile(
    r"(?i)кто\s+так(?:ой|ая|ие)\s+(.+?)(?:[\?\.!,]|$)"
)

_USER_LOG_RE = re.compile(
    r"(?i)(?:"
    r"что\s+(?:писал[аи]?|написал[аи]?|говорил[аи]?|сказал[аи]?)"
    r"(?:\s+в\s+(?:этом\s+)?чате)?"
    r"(?:\s+(?:вчера|сегодня|позавчера))?"
    r"\s+(?P<u>[@\w][\w.-]{1,31})"
    r"|"
    r"что\s+(?:писал[аи]?|написал[аи]?)"
    r"\s+(?:вчера|сегодня|позавчера)\s+(?P<u2>[@\w][\w.-]{1,31})"
    r"|"
    r"(?P<u3>[@\w][\w.-]{1,31})\s+"
    r"(?:писал[аи]?|написал[аи]?|говорил[аи]?)"
    r"(?:\s+(?:вчера|сегодня|позавчера))?"
    r")"
)

_WHEN_SAID_RE = re.compile(
    r"(?i)во\s+сколько\s+"
    r"(?:(?:вчера|сегодня|позавчера)\s+)?"
    r"(?P<u>[@\w][\w.-]{1,31})\s+"
    r"(?:сказал|писал|написал|говорил)\s+(?P<phrase>.+?)[\?\.!,]*$"
)

_TIME_ONLY_RE = re.compile(r"(?i)(?:во\s+сколько|в\s+какое\s+время)")

_USER_LOG_STOP = frozenset(
    {
        "вчера",
        "сегодня",
        "позавчера",
        "чате",
        "чат",
        "этом",
        "свин",
        "свинья",
        "что",
        "как",
    }
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


def detect_history_period(text: str) -> str:
    """today | yesterday | day_before | 24h"""
    blob = text.strip()
    if _DAY_BEFORE_RE.search(blob):
        return "day_before"
    if _YESTERDAY_RE.search(blob):
        return "yesterday"
    if _TODAY_RE.search(blob):
        return "today"
    return "24h"


def is_recap_like_question(text: str) -> bool:
    lower = text.lower()
    if any(w in lower for w in ("позавчера", "вчера", "сегодня")):
        if any(
            w in lower
            for w in (
                "что было",
                "что происходило",
                "о чем",
                "о чём",
                "говорил",
                "говорили",
                "обсуждал",
                "пересказ",
                "итог",
            )
        ):
            return True
    return False


def is_who_in_chat_question(text: str) -> bool:
    return bool(_WHO_IN_CHAT_RE.search(text.strip()))


def parse_user_log_request(text: str) -> UserLogQuery | None:
    """
    «что писал вчера Tom_Frod с 15 до 18» / «во сколько вчера Tom сказал …»
    → UserLogQuery. Ответ из Supabase без Yandex GPT.
    """
    blob = re.sub(r"(?i)^(?:свин|свинья)[\s,!?.\-]+", "", text.strip()).strip()
    if not blob:
        return None
    lower = blob.lower()
    period = detect_history_period(blob)
    hf, ht, mf, mt = parse_time_range(blob)

    wm = _WHEN_SAID_RE.search(blob)
    if wm:
        raw = (wm.group("u") or "").strip().lstrip("@")
        phrase = (wm.group("phrase") or "").strip(" ?!.,")
        if raw and phrase and raw.lower() not in _USER_LOG_STOP:
            return UserLogQuery(
                username=raw,
                period=period,
                hour_from=hf,
                hour_to=ht,
                minute_from=mf,
                minute_to=mt,
                phrase=phrase,
                when_only=True,
            )

    if not any(
        w in lower
        for w in ("писал", "написал", "говорил", "сказал", "сообщен", "сколько", "время")
    ):
        return None

    m = _USER_LOG_RE.search(blob)
    if not m:
        if _TIME_ONLY_RE.search(blob) and hf is not None:
            return None
        return None

    raw = (m.group("u") or m.group("u2") or m.group("u3") or "").strip().lstrip("@")
    if not raw or raw.lower() in _USER_LOG_STOP:
        return None

    phrase = None
    mp = re.search(r"(?i)\bпро\s+(.+?)[\?\.!,]*$", blob)
    if mp:
        cand = mp.group(1).strip(" ?!.,")
        if cand and len(cand) < 120 and not re.search(r"(?i)\b(?:с|до)\s+\d", cand):
            phrase = cand

    return UserLogQuery(
        username=raw,
        period=period,
        hour_from=hf,
        hour_to=ht,
        minute_from=mf,
        minute_to=mt,
        phrase=phrase or None,
        when_only=False,
    )


def is_user_log_request(text: str) -> bool:
    return parse_user_log_request(text) is not None


def extract_who_is_name(text: str) -> str | None:
    blob = text.strip()
    m = _WHO_IS_RE.search(blob)
    if not m:
        return None
    name = m.group(1).strip(" ?!.,")
    if len(name) < 2:
        return None
    return name


def needs_recent_history(text: str) -> bool:
    """Нужна ли выборка истории для ответа (не полный дайджест)."""
    if is_chat_examples_request(text):
        return False
    if is_user_log_request(text):
        return False
    if is_recap_like_question(text):
        return True
    if is_who_in_chat_question(text):
        return True
    if extract_who_is_name(text):
        return True

    lower = text.lower()
    if any(
        w in lower
        for w in (
            "номер",
            "цифр",
            "как зовут",
            "имя",
            "кто сказал",
            "кто такой",
            "кто такая",
            "что писал",
            "сколько",
            "когда",
            "вчера",
            "сегодня",
            "позавчера",
            "помнишь",
            "говорил",
            "участник",
            "в чате",
        )
    ):
        return True
    return False
