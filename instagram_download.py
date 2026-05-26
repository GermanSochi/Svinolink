from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path
from threading import Lock

from config import settings
from instagram_urls import clean_instagram_url, is_instagram_media_url

logger = logging.getLogger(__name__)

TELEGRAM_MAX_BYTES = 52_428_800  # 50 MiB

RENDER_IP_BLOCK_MSG = (
    "❌ Ошибка: Сервера Instagram заблокировали IP-адрес хостинга Render. "
    "Требуются прокси или cookies."
)
TOO_LARGE_MSG = (
    "❌ Ошибка: Видео весит более 50 МБ. "
    "Telegram запрещает ботам отправлять такие тяжелые файлы."
)

_client = None
_client_lock = Lock()
_ready = False


def _downloads_dir() -> Path:
    d = settings.downloads_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_client():
    """Синхронная инициализация instagrapi — вызывается один раз при старте."""
    global _client, _ready
    from instagrapi import Client

    with _client_lock:
        if _client is not None:
            _ready = True
            return _client

        cl = Client()
        session_file = settings.instagram_session_file
        user = settings.instagram_username.strip()
        password = settings.instagram_password.strip()

        if session_file.is_file():
            try:
                cl.load_settings(session_file)
                logger.info("instagrapi: settings loaded from %s", session_file)
            except Exception as exc:
                logger.warning("instagrapi session load failed: %s", exc)
                cl = Client()

        if user and password:
            try:
                cl.login(user, password)
                session_file.parent.mkdir(parents=True, exist_ok=True)
                cl.dump_settings(session_file)
                logger.info("instagrapi: login OK")
            except Exception as exc:
                logger.warning("instagrapi login failed: %s", exc)

        _client = cl
        _ready = True
        logger.info("instagrapi: client ready")
        return _client


def init_instagram_downloader() -> None:
    """Вызов при старте приложения (Render on_startup)."""
    try:
        _build_client()
    except Exception as exc:
        logger.error("instagrapi init failed (downloads may fail): %s", exc, exc_info=True)


def _get_client():
    if _client is None:
        return _build_client()
    return _client


def _dest_path() -> Path:
    return _downloads_dir() / f"{uuid.uuid4().hex}.mp4"


def _is_block_or_network_error(exc: Exception) -> bool:
    from instagrapi.exceptions import ClientError, ClientLoginRequired

    if isinstance(exc, (ClientError, ClientLoginRequired, ConnectionError, TimeoutError, OSError)):
        return True
    msg = str(exc).lower()
    needles = (
        "403",
        "401",
        "429",
        "challenge",
        "login",
        "blocked",
        "forbidden",
        "timeout",
        "connection",
        "connect",
        "ssl",
        "proxy",
    )
    return any(n in msg for n in needles)


def check_file_size(path: Path) -> None:
    size = os.path.getsize(path)
    if size > TELEGRAM_MAX_BYTES:
        path.unlink(missing_ok=True)
        raise ValueError(TOO_LARGE_MSG)


def download_instagram_video(url: str) -> Path:
    """
    Скачивание Reel через instagrapi (экосистема subzeroid).
    yt-dlp не используется — только instagrapi.
    """
    clean = clean_instagram_url(url)
    if not is_instagram_media_url(clean):
        raise ValueError("нужна ссылка Instagram: /reel/ или /p/")

    from instagrapi.exceptions import ClientError

    try:
        cl = _get_client()
        media_pk = cl.media_pk_from_url(clean)
        folder = _downloads_dir()
        info = cl.media_info(media_pk)

        if info.media_type != 2 or not info.video_url:
            raise ValueError("в посте нет видео (только фото)")

        try:
            raw_path = cl.clip_download(media_pk, folder=folder)
        except Exception:
            raw_path = cl.video_download(media_pk, folder=folder)

        dest = _dest_path()
        shutil.copy2(raw_path, dest)
        check_file_size(dest)
        logger.info("instagrapi OK %s -> %s (%s bytes)", clean, dest, dest.stat().st_size)
        return dest

    except ValueError:
        raise
    except ClientError as exc:
        logger.error("instagrapi ClientError %s: %s", clean, exc, exc_info=True)
        if _is_block_or_network_error(exc):
            raise RuntimeError(RENDER_IP_BLOCK_MSG) from exc
        raise RuntimeError(f"❌ Ошибка instagrapi: {exc}") from exc
    except Exception as exc:
        logger.error("instagrapi failed %s: %s", clean, exc, exc_info=True)
        if _is_block_or_network_error(exc):
            raise RuntimeError(RENDER_IP_BLOCK_MSG) from exc
        raise


def remove_file(path: Path | None) -> None:
    if not path:
        return
    try:
        if path.is_file():
            path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("remove_file %s: %s", path, exc)
