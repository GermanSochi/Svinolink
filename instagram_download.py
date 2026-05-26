from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from threading import Lock

import requests

from config import settings
from instagram_urls import clean_instagram_url, is_instagram_media_url

logger = logging.getLogger(__name__)

TELEGRAM_MAX_BYTES = 52_428_800  # 50 MiB
INSTAGRAM_REQUEST_TIMEOUT = 45
DOWNLOAD_MAX_RETRIES = 3
DOWNLOAD_RETRY_DELAY_SEC = 2

RENDER_IP_BLOCK_MSG = (
    "❌ Ошибка: Сервера Instagram заблокировали IP-адрес хостинга Render. "
    "Требуются прокси или cookies."
)
COOKIES_EXPIRED_MSG = (
    "❌ Сессия Instagram истекла или сброшена. "
    "Обновите data/cookies.txt (Netscape) и перезапустите бот на Render."
)
TOO_LARGE_MSG = (
    "❌ Ошибка: Видео весит более 50 МБ. "
    "Telegram запрещает ботам отправлять такие тяжелые файлы."
)

_client = None
_client_lock = Lock()
_ready = False
_cookies_loaded = False


def _downloads_dir() -> Path:
    d = settings.downloads_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cookies_file() -> Path:
    return settings.instagram_cookies_file


def _parse_netscape_cookie_dict(path: Path) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]
    return cookies


def _cookie_jar_from_netscape(path: Path) -> requests.cookies.RequestsCookieJar:
    jar = MozillaCookieJar()
    jar.load(str(path), ignore_discard=True, ignore_expires=True)
    return requests.utils.cookiejar_from_dict(requests.utils.dict_from_cookiejar(jar))


def _apply_netscape_cookies(cl, path: Path) -> None:
    cookie_dict = _parse_netscape_cookie_dict(path)
    if not cookie_dict:
        raise ValueError(f"файл cookies пустой или неверного формата: {path}")

    sessionid = cookie_dict.get("sessionid", "")
    if sessionid:
        try:
            cl.login_by_sessionid(sessionid)
            logger.info("instagrapi: сессия восстановлена через sessionid (%s)", path)
            return
        except Exception as exc:
            logger.warning("instagrapi login_by_sessionid failed: %s", exc)

    jar = _cookie_jar_from_netscape(path)
    cl.private.cookies.update(jar)
    cl.public.cookies.update(jar)
    cl.settings["cookies"] = requests.utils.dict_from_cookiejar(cl.private.cookies)
    if sessionid and cookie_dict.get("ds_user_id"):
        cl.authorization_data = {
            "ds_user_id": str(cookie_dict["ds_user_id"]),
            "sessionid": sessionid,
            "should_use_header_over_cookies": True,
        }
    cl.init()
    logger.info("instagrapi: Netscape cookies loaded from %s", path)


def _load_cookies_into_client(cl, path: Path) -> None:
    """JSON settings instagrapi или Netscape cookies.txt (как в браузере)."""
    try:
        cl.load_settings(path)
        logger.info("instagrapi: settings loaded from %s", path)
    except Exception:
        _apply_netscape_cookies(cl, path)


def _new_instagram_client():
    from instagrapi import Client

    return Client(request_timeout=INSTAGRAM_REQUEST_TIMEOUT)


def _build_client():
    """Синхронная инициализация instagrapi — вызывается один раз при старте."""
    global _client, _ready, _cookies_loaded

    with _client_lock:
        if _client is not None:
            _ready = True
            return _client

        cl = _new_instagram_client()
        cookies_path = _cookies_file()
        session_file = settings.instagram_session_file
        user = settings.instagram_username.strip()
        password = settings.instagram_password.strip()
        _cookies_loaded = False

        if cookies_path.is_file():
            try:
                _load_cookies_into_client(cl, cookies_path)
                _cookies_loaded = True
            except Exception as exc:
                logger.error("instagrapi cookies load failed (%s): %s", cookies_path, exc)
                raise RuntimeError(COOKIES_EXPIRED_MSG) from exc
        elif session_file.is_file():
            try:
                cl.load_settings(session_file)
                logger.info("instagrapi: settings loaded from %s", session_file)
            except Exception as exc:
                logger.warning("instagrapi session load failed: %s", exc)
                cl = _new_instagram_client()

        cl.request_timeout = INSTAGRAM_REQUEST_TIMEOUT

        if not _cookies_loaded and user and password:
            try:
                cl.login(user, password)
                session_file.parent.mkdir(parents=True, exist_ok=True)
                cl.dump_settings(session_file)
                logger.info("instagrapi: login OK")
            except Exception as exc:
                logger.warning("instagrapi login failed: %s", exc)

        if cookies_path.is_file() and cl.user_id is None:
            raise RuntimeError(COOKIES_EXPIRED_MSG)

        _client = cl
        _ready = True
        logger.info(
            "instagrapi: client ready (cookies=%s user_id=%s)",
            _cookies_loaded,
            cl.user_id,
        )
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


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    msg = str(exc).lower()
    return "timeout" in msg or "timed out" in msg


def _is_session_error(exc: Exception) -> bool:
    from instagrapi.exceptions import ClientLoginRequired, LoginRequired

    if isinstance(exc, (ClientLoginRequired, LoginRequired)):
        return True
    msg = str(exc).lower()
    needles = (
        "login_required",
        "login required",
        "please wait",
        "challenge",
        "checkpoint",
        "consent_required",
        "sessionid",
        "session expired",
        "user_has_logged_out",
    )
    return any(n in msg for n in needles)


def _is_block_or_network_error(exc: Exception) -> bool:
    from instagrapi.exceptions import ClientError, ClientLoginRequired

    if _is_session_error(exc):
        return False
    if isinstance(exc, (ClientError, ClientLoginRequired, ConnectionError, TimeoutError, OSError)):
        return True
    msg = str(exc).lower()
    needles = (
        "403",
        "401",
        "429",
        "blocked",
        "forbidden",
        "timeout",
        "connection",
        "connect",
        "ssl",
        "proxy",
        "sentry",
    )
    return any(n in msg for n in needles)


def _runtime_error_for(exc: Exception) -> RuntimeError:
    if _is_timeout_error(exc):
        return RuntimeError(
            "❌ Instagram не ответил вовремя после нескольких попыток. "
            "Отправь ссылку ещё раз."
        )
    if _is_session_error(exc):
        return RuntimeError(COOKIES_EXPIRED_MSG)
    if _cookies_loaded and _is_block_or_network_error(exc):
        return RuntimeError(COOKIES_EXPIRED_MSG)
    if _is_block_or_network_error(exc):
        return RuntimeError(RENDER_IP_BLOCK_MSG)
    return RuntimeError(f"❌ Ошибка instagrapi: {exc}")


def check_file_size(path: Path) -> None:
    size = os.path.getsize(path)
    if size > TELEGRAM_MAX_BYTES:
        path.unlink(missing_ok=True)
        raise ValueError(TOO_LARGE_MSG)


def _download_instagram_video_once(clean: str) -> Path:
    from instagrapi.exceptions import ClientError

    cl = _get_client()
    if cl.user_id is None and _cookies_file().is_file():
        raise RuntimeError(COOKIES_EXPIRED_MSG)

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


def download_instagram_video(url: str) -> Path:
    """
    Скачивание Reel через instagrapi (экосистема subzeroid).
    yt-dlp не используется — только instagrapi.
    """
    clean = clean_instagram_url(url)
    if not is_instagram_media_url(clean):
        raise ValueError("нужна ссылка Instagram: /reel/ или /p/")

    from instagrapi.exceptions import ClientError

    last_exc: Exception | None = None
    for attempt in range(DOWNLOAD_MAX_RETRIES):
        try:
            return _download_instagram_video_once(clean)
        except ValueError:
            raise
        except RuntimeError:
            raise
        except ClientError as exc:
            last_exc = exc
            if _is_timeout_error(exc) and attempt < DOWNLOAD_MAX_RETRIES - 1:
                logger.warning(
                    "instagrapi timeout attempt %s/%s for %s: %s",
                    attempt + 1,
                    DOWNLOAD_MAX_RETRIES,
                    clean,
                    exc,
                )
                time.sleep(DOWNLOAD_RETRY_DELAY_SEC)
                continue
            logger.error("instagrapi ClientError %s: %s", clean, exc, exc_info=True)
            raise _runtime_error_for(exc) from exc
        except Exception as exc:
            last_exc = exc
            if _is_timeout_error(exc) and attempt < DOWNLOAD_MAX_RETRIES - 1:
                logger.warning(
                    "instagrapi timeout attempt %s/%s for %s: %s",
                    attempt + 1,
                    DOWNLOAD_MAX_RETRIES,
                    clean,
                    exc,
                )
                time.sleep(DOWNLOAD_RETRY_DELAY_SEC)
                continue
            logger.error("instagrapi failed %s: %s", clean, exc, exc_info=True)
            raise _runtime_error_for(exc) from exc

    if last_exc is not None:
        raise _runtime_error_for(last_exc)
    raise RuntimeError("❌ Не удалось скачать видео с Instagram")


def remove_file(path: Path | None) -> None:
    if not path:
        return
    try:
        if path.is_file():
            path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("remove_file %s: %s", path, exc)
