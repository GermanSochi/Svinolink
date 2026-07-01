from __future__ import annotations

import re
from urllib.parse import urlparse

INSTAGRAM_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?instagram\.com(?:/[a-zA-Z0-9_./?=&%-]*)?",
    re.IGNORECASE,
)
_INSTAGRAM_LOOSE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com(?:/[a-zA-Z0-9_./?=&%-]*)?",
    re.IGNORECASE,
)
_MEDIA_PATH = re.compile(r"/(reel|reels|p|tv|stories|s)/", re.IGNORECASE)


def clean_instagram_url(url: str) -> str:
    raw = url.strip().strip("()[]<>.,!?:;\"'")
    # Для Highlights (/s/) сохраняем query string (story_media_id нужен)
    is_highlight = "/s/" in raw.lower()
    if not is_highlight and "?" in raw:
        raw = raw.split("?", 1)[0]
    if "#" in raw:
        raw = raw.split("#", 1)[0]
    raw = raw.rstrip("/")

    if not raw.lower().startswith("http"):
        raw = "https://" + raw.lstrip("/")

    parsed = urlparse(raw)
    host = (parsed.netloc or "www.instagram.com").lower()
    if host in {"instagram.com", "www.instagram.com", "m.instagram.com"}:
        host = "www.instagram.com"
    elif not host.endswith("instagram.com"):
        host = "www.instagram.com"
    path = parsed.path or ""
    result = f"https://{host}{path}"
    if is_highlight and parsed.query:
        result += f"?{parsed.query}"
    return result


def is_instagram_media_url(url: str) -> bool:
    if "instagram.com" not in url.lower():
        return False
    return bool(_MEDIA_PATH.search(url))


def extract_instagram_url(text: str) -> str | None:
    if not text:
        return None

    for pattern in (INSTAGRAM_URL_PATTERN, _INSTAGRAM_LOOSE):
        m = pattern.search(text)
        if m:
            return clean_instagram_url(m.group(0))

    if "instagram.com" in text.lower():
        for m in re.finditer(r"https?://\S+", text):
            chunk = m.group(0).strip("()[]<>.,!?:;\"'")
            if "instagram.com" in chunk.lower():
                return clean_instagram_url(chunk)

    return None
