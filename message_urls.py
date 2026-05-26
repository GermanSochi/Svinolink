from __future__ import annotations

from aiogram.types import Message

from downloader import extract_supported_url


def url_from_message(message: Message) -> str | None:
    text = message.text or message.caption or ""
    found = extract_supported_url(text)
    if found:
        return found

    entities = message.entities or message.caption_entities or []
    for ent in entities:
        if ent.type == "url":
            chunk = text[ent.offset : ent.offset + ent.length]
            found = extract_supported_url(chunk)
            if found:
                return found
        if ent.type == "text_link" and ent.url:
            found = extract_supported_url(ent.url)
            if found:
                return found
    return None
