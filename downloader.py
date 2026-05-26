from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from yt_dlp import YoutubeDL

_IG_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv|share/reel|share/p)/[\w-]+",
    re.IGNORECASE,
)
_YT_SHORTS_RE = re.compile(
    r"https?://(?:www\.)?(?:m\.)?youtube\.com/shorts/[\w-]+",
    re.IGNORECASE,
)
def _is_supported(url: str) -> bool:
    if _IG_RE.search(url):
        return True
    if _YT_SHORTS_RE.search(url):
        return True
    return False


def extract_supported_url(text: str) -> str | None:
    if not text:
        return None
    for m in re.finditer(r"https?://\S+", text):
        raw = m.group(0).strip("()[]<>.,!?:;\"'")
        if _is_supported(raw):
            return raw
    return None


def extract_url_from_message_text(text: str | None) -> str | None:
    return extract_supported_url(text or "")


def download_to_temp_mp4(url: str) -> Path:
    tmp_dir = tempfile.mkdtemp(prefix="svinolink_")
    out = Path(tmp_dir) / "video.%(ext)s"

    ydl_opts: dict = {
        "format": (
            "best[ext=mp4][filesize<48M]/"
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "best[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": str(out),
        "socket_timeout": 45,
        "retries": 3,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
        },
    }

    cookies = os.environ.get("INSTAGRAM_COOKIES_FILE", "").strip()
    if cookies and Path(cookies).is_file():
        ydl_opts["cookiefile"] = cookies

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))

    if not path.is_file():
        for p in Path(tmp_dir).glob("*.mp4"):
            return p
        raise FileNotFoundError("файл не скачался")

    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > 48:
        raise ValueError(f"слишком большой файл ({size_mb:.0f} МБ)")

    return path


def cleanup_paths(*paths: Path) -> None:
    for p in paths:
        if not p:
            continue
        try:
            if p.is_file():
                p.unlink(missing_ok=True)
            parent = p.parent
            if parent.name.startswith("svinolink_") and parent.exists():
                for child in parent.iterdir():
                    child.unlink(missing_ok=True)
                parent.rmdir()
        except OSError:
            pass
