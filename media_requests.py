from __future__ import annotations

import re


_MEME_RE = re.compile(
    r"(?is)\b(?:свин(?:ья)?\s*,?\s*)?(?:сделай|сгенерируй|нахуячь|сваргань)\s+(?:мем|картинк\w+)\s*[:\-—]?\s*(.+)$"
)
_VIDEO_RE = re.compile(
    r"(?is)\b(?:свин(?:ья)?\s*,?\s*)?(?:сделай|сгенерируй|нахуячь|сваргань)\s+(?:видос|видео|ролик)\s*[:\-—]?\s*(.+)$"
)


def parse_meme_request(text: str | None) -> str | None:
    if not text:
        return None
    m = _MEME_RE.search(text.strip())
    if not m:
        return None
    payload = (m.group(1) or "").strip()
    return payload or None


def parse_video_request(text: str | None) -> str | None:
    if not text:
        return None
    m = _VIDEO_RE.search(text.strip())
    if not m:
        return None
    payload = (m.group(1) or "").strip()
    return payload or None

