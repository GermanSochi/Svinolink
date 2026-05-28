from __future__ import annotations

import re


_CAP_RE = re.compile(
    r"(?is)(?:^|\s|[\"'“”«»])("
    r"что\s+ты\s+умеешь"
    r"|что\s+умеешь"
    r"|что\s+можешь"
    r"|умеешь\s*\?"
    r"|твои\s+навык"
    r"|навык(?:и)?\s+свина"
    r"|help"
    r"|хелп"
    r")(?:$|\s|[?!.,:;\"'“”«»])"
)


def is_capabilities_question(text: str | None) -> bool:
    if not text:
        return False
    t = text.strip()
    low = t.lower()
    if "умеешь" in low and ("что" in low or "чё" in low or "че" in low):
        return True
    return _CAP_RE.search(t) is not None


def capabilities_markdown() -> str:
    return (
        "🐷 **Что я умею в этом чате**\n\n"
        "🎞️ **Главное: Instagram → видео**\n\n"
        "🔗 Кинь ссылку на **reel** или **post** — пришлю видео в ответ\n\n"
        "🔔 **Как меня позвать**\n\n"
        "💬 **«Свин …»** в одном сообщении\n\n"
        "↩️ **reply** на моё сообщение\n\n"
        "🧠 **Память чата**\n\n"
        "📌 **«что было сегодня/вчера»** — выжимка из переписки\n\n"
        "👥 **«кто в чате»** — ники из базы\n\n"
        "🧾 **«примеры из чата»** — реальные цитаты\n\n"
        "📄 **Текст из файла**\n\n"
        "📎 PDF/DOCX/XLSX/TXT + **«Свин, достань текст из файла»**\n\n"
        "⚙️ **Триггеры**\n\n"
        "🧷 **«какие триггеры»** — список\n\n"
        "➕ **«Свин, добавь триггер: …»** из чата\n\n"
        "🎣 **Игры (без /команд)**\n\n"
        "🎣 Рыбалка · 🛠️ гараж · 🃏 покер · 🧠 квиз — словами в чат"
    )
