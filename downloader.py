"""Обратная совместимость — используйте instagram_urls / instagram_download."""
from __future__ import annotations

from pathlib import Path

from instagram_download import download_instagram_video, remove_file
from instagram_urls import clean_instagram_url, extract_instagram_url, is_instagram_media_url


def download_to_temp_mp4(url: str) -> Path:
    return download_instagram_video(url)


def cleanup_paths(*paths: Path) -> None:
    for p in paths:
        remove_file(p)
