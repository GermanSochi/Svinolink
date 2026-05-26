from __future__ import annotations

import re

from aiogram.types import Message

from downloader import extract_supported_url

_LINK_HINT = re.compile(r"(?i)(instagram\.com|youtube\.com|youtu\.be)")


def message_has_link_hint(message: Message) -> bool:
    text = message.text or message.caption or ""
    if _LINK_HINT.search(text):
        return True
    for ent in message.entities or message.caption_entities or []:
        if ent.type == "text_link" and ent.url and _LINK_HINT.search(ent.url):
            return True
        if ent.type == "url":
            chunk = text[ent.offset : ent.offset + ent.length]
            if _LINK_HINT.search(chunk):
                return True
    return False


def url_from_message(message: Message) -> str | None:
    text = message.text or message.caption or ""
    found = extract_supported_url(text)
    if found:
        return found

    for ent in message.entities or message.caption_entities or []:
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
