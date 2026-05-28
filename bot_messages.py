"""Короткие человеческие ответы бота при сбоях — без техно-простыней."""
from __future__ import annotations

import random

_GPT_GLITCH = (
    "🐷 Туплю на секунду… переспроси через **минуту**.",
    "🐷 Сервер чуть **подвис** — подожди и спроси ещё раз.",
    "🐷 Совсем **забыл**, что хотел ответить… кинь вопрос ещё раз.",
    "🐷 Заглючил, не на тебя — **минутку** и переспроси.",
    "🐷 Мозг свернулся — напиши через минутку, разберёмся.",
)

_IG_TIMEOUT = (
    "🐷 Instagram **тупит** — кинь ссылку ещё раз через минуту.",
    "🐷 Сервер не дождался Instagram — **перекинь** ссылку чуть позже.",
)

_IG_GENERIC = (
    "🐷 Не вышло стянуть видео — попробуй ссылку ещё раз.",
    "🐷 Что-то пошло не так со ссылкой — перекинь через минуту.",
)


def gpt_glitch_message() -> str:
    return random.choice(_GPT_GLITCH)


def yandex_error_message() -> str:
    return gpt_glitch_message()


def server_glitch_message() -> str:
    return gpt_glitch_message()


def video_too_heavy_message(instagram_url: str | None = None) -> str:
    lines = [
        "🐷 Видео **слишком тяжёлое** — больше **50 МБ**.",
        "Telegram ботам такое **не отдаёт** — смотри **сам по ссылке**:",
    ]
    if instagram_url:
        return "\n\n".join(lines) + f"\n\n🔗 {instagram_url}"
    return "\n\n".join(lines) + "\n\n🎞️ Открой reel/post в Instagram."


def instagram_timeout_message() -> str:
    return random.choice(_IG_TIMEOUT)


def instagram_error_message(instagram_url: str | None = None) -> str:
    base = random.choice(_IG_GENERIC)
    if instagram_url:
        return f"{base}\n\n🔗 {instagram_url}"
    return base


def map_instagram_error(exc: Exception, instagram_url: str | None = None) -> str:
    text = str(exc).lower()
    if "50" in text and ("мб" in text or "telegram" in text or "тяжел" in text):
        return video_too_heavy_message(instagram_url)
    if "таймаут" in text or "timeout" in text or "вовремя" in text:
        return instagram_timeout_message()
    if "cookie" in text or "сессия" in text or "instagram истек" in text:
        body = (
            "🐷 Instagram **не пускает** — сессия на сервере протухла.\n\n"
            "🔧 Админу надо обновить cookies."
        )
        if instagram_url:
            return f"{body}\n\n🔗 {instagram_url}"
        return f"{body}\n\n🎞️ Открой в приложении."
    if "❌" in str(exc) and len(str(exc)) < 200:
        return instagram_error_message(instagram_url)
    return instagram_error_message(instagram_url)
