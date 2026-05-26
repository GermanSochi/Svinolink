from __future__ import annotations

import re
from urllib.parse import urlparse

# Как в subzeroid/instagram-downloader-tgbot — ищем URL в любом месте текста
INSTAGRAM_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?instagram\.com/[a-zA-Z0-9_./?=&%-]+",
    re.IGNORECASE,
)

_SUPPORTED_PATH = re.compile(r"/(reel|reels|p|tv)/", re.IGNORECASE)


def clean_instagram_url(url: str) -> str:
    """Убирает ?igsh=, #fragment и хвостовую пунктуацию."""
    raw = url.strip().strip("()[]<>.,!?:;\"'")
    if "?" in raw:
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
    return f"https://{host}{path}"


def is_instagram_media_url(url: str) -> bool:
    return "instagram.com" in url.lower() and bool(_SUPPORTED_PATH.search(url))


def extract_instagram_url(text: str) -> str | None:
    if not text:
        return None
    m = INSTAGRAM_URL_PATTERN.search(text)
    if not m:
        return None
    clean = clean_instagram_url(m.group(0))
    if is_instagram_media_url(clean):
        return clean
    return None
