from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path
from threading import Lock

from instagrapi import Client
from instagrapi.exceptions import ClientError, LoginRequired
from yt_dlp import YoutubeDL

from config import settings
from instagram_urls import clean_instagram_url, is_instagram_media_url

logger = logging.getLogger(__name__)

_client: Client | None = None
_client_lock = Lock()


def _downloads_dir() -> Path:
    d = settings.downloads_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_client() -> Client:
    global _client
    with _client_lock:
        if _client is not None:
            return _client
        cl = Client()
        session_file = settings.instagram_session_file
        user = settings.instagram_username.strip()
        password = settings.instagram_password.strip()

        if session_file.is_file():
            try:
                cl.load_settings(session_file)
                logger.info("instagrapi: settings from %s", session_file)
            except Exception as exc:
                logger.warning("instagrapi session load failed: %s", exc)
                cl = Client()
        if user and password:
            try:
                cl.login(user, password)
                session_file.parent.mkdir(parents=True, exist_ok=True)
                cl.dump_settings(session_file)
                logger.info("instagrapi: login OK, session saved")
            except Exception as exc:
                logger.warning("instagrapi login failed (public reels still OK): %s", exc)

        _client = cl
        return _client


def _dest_path() -> Path:
    return _downloads_dir() / f"{uuid.uuid4().hex}.mp4"


def _ensure_size_ok(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"файл не найден: {path}")
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > 48:
        path.unlink(missing_ok=True)
        raise ValueError(f"слишком большой файл ({size_mb:.0f} МБ, лимит Telegram 48)")
    if size_mb < 0.01:
        path.unlink(missing_ok=True)
        raise ValueError("пустой видеофайл")
    return path


def _copy_to_dest(src: str | Path) -> Path:
    src_p = Path(src)
    dest = _dest_path()
    shutil.copy2(src_p, dest)
    return _ensure_size_ok(dest)


def _download_instagrapi(clean_url: str) -> Path:
    cl = _get_client()
    media_pk = cl.media_pk_from_url(clean_url)
    folder = _downloads_dir()
    info = cl.media_info(media_pk)

    if info.media_type == 2 and info.video_url:
        try:
            path = cl.clip_download(media_pk, folder=folder)
        except Exception:
            path = cl.video_download(media_pk, folder=folder)
    else:
        raise ValueError("в посте нет видео (только фото)")

    return _copy_to_dest(path)


def _cookies_path() -> str | None:
    for key in ("INSTAGRAM_COOKIES_FILE", "COOKIES_FILE"):
        path = os.environ.get(key, "").strip()
        if path and Path(path).is_file():
            return path
    default = settings.data_dir / "cookies.txt"
    if default.is_file():
        return str(default)
    return None


def _download_ytdlp(clean_url: str) -> Path:
    dest = _dest_path()
    out_tpl = str(dest.with_suffix(".%(ext)s"))

    ydl_opts: dict = {
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": out_tpl,
        "socket_timeout": 60,
        "retries": 3,
    }
    cookies = _cookies_path()
    if cookies:
        ydl_opts["cookiefile"] = cookies

    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([clean_url])

    if dest.is_file():
        return _ensure_size_ok(dest)
    for p in dest.parent.glob(f"{dest.stem}.*"):
        if p.suffix.lower() in {".mp4", ".mov", ".webm"}:
            shutil.move(p, dest)
            return _ensure_size_ok(dest)
    raise FileNotFoundError("yt-dlp не сохранил видео")


def download_instagram_video(url: str) -> Path:
    """
    Скачивает Reel/видео-пост. Сначала instagrapi (как в экосистеме subzeroid),
    затем yt-dlp с cookies.
    """
    clean = clean_instagram_url(url)
    if not is_instagram_media_url(clean):
        raise ValueError("нужна ссылка Instagram: /reel/ или /p/")

    errors: list[str] = []

    try:
        path = _download_instagrapi(clean)
        logger.info("downloaded via instagrapi %s -> %s", clean, path)
        return path
    except (ClientError, LoginRequired, Exception) as exc:
        errors.append(f"instagrapi: {exc}")
        logger.warning("instagrapi failed %s: %s", clean, exc)

    try:
        path = _download_ytdlp(clean)
        logger.info("downloaded via yt-dlp %s -> %s", clean, path)
        return path
    except Exception as exc:
        errors.append(f"yt-dlp: {exc}")
        logger.error("yt-dlp failed %s: %s", clean, exc)

    raise RuntimeError(" | ".join(errors))


def remove_file(path: Path | None) -> None:
    if not path:
        return
    try:
        if path.is_file():
            path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("remove_file %s: %s", path, exc)
