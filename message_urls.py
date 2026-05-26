from __future__ import annotations

import re

from aiogram.types import Message

from instagram_urls import clean_instagram_url, extract_instagram_url

_IG_HINT = re.compile(r"(?i)instagram\.com")


def message_has_instagram_link(message: Message) -> bool:
    text = message.text or message.caption or ""
    if _IG_HINT.search(text):
        return True
    for ent in message.entities or message.caption_entities or []:
        if ent.type == "text_link" and ent.url and _IG_HINT.search(ent.url):
            return True
        if ent.type == "url":
            chunk = text[ent.offset : ent.offset + ent.length]
            if _IG_HINT.search(chunk):
                return True
    return False


def url_from_message(message: Message) -> str | None:
    text = message.text or message.caption or ""
    found = extract_instagram_url(text)
    if found:
        return clean_instagram_url(found)

    for ent in message.entities or message.caption_entities or []:
        if ent.type == "url":
            chunk = text[ent.offset : ent.offset + ent.length]
            found = extract_instagram_url(chunk)
            if found:
                return clean_instagram_url(found)
        if ent.type == "text_link" and ent.url:
            found = extract_instagram_url(ent.url)
            if found:
                return clean_instagram_url(ent.url)
    return None
