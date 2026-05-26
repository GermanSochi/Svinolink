"""Обратная совместимость."""
from __future__ import annotations

from pathlib import Path

from instagram_download import (
    TELEGRAM_MAX_BYTES,
    download_instagram_video,
    init_instagram_downloader,
    remove_file,
)
from instagram_urls import clean_instagram_url, extract_instagram_url, is_instagram_media_url


def download_to_temp_mp4(url: str) -> Path:
    return download_instagram_video(url)


def cleanup_paths(*paths: Path) -> None:
    for p in paths:
        remove_file(p)
