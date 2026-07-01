from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
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
INSTAGRAM_REQUEST_TIMEOUT = 20
DOWNLOAD_MAX_RETRIES = 1
DOWNLOAD_RETRY_DELAY_SEC = 0.2
DOWNLOAD_TOTAL_TIMEOUT_SEC = 90  # Render free tier — медленная сеть
DOWNLOAD_CHUNK_SIZE = 262144  # 256KB — mejor throughput чем 64KB

RENDER_IP_BLOCK_MSG = (
    "❌ Ошибка: Сервера Instagram заблокировали IP-адрес хостинга Render. "
    "Требуются прокси или cookies."
)
COOKIES_EXPIRED_MSG = (
    "❌ Сессия Instagram истекла или сброшена. "
    "Обновите data/cookies.txt (Netscape) и перезапустите бот на Render."
)

INSTAGRAM_PAUSED_MSG = (
    "🐷 **Instagram на паузе** — бот **не заходит** в аккаунт и **не качает** видео.\n\n"
    "💬 Свин, поиск и память чата работают как обычно."
)

INSTAGRAM_NO_CREDS_MSG = (
    "🐷 **Видео из Instagram выключено** — нет cookies на сервере.\n\n"
    "🔧 Чтобы включить: положи `data/cookies.txt` и сними паузу "
    "(`INSTAGRAM_PAUSED=0` на Render)."
)

_client = None
_client_lock = Lock()
_ready = False
_cookies_loaded = False
_download_semaphore = asyncio.Semaphore(3)  # макс 3 параллельных скачивания


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


def _load_cookies_from_env() -> dict[str, str] | None:
    """Загружает cookies из env INSTAGRAM_COOKIES_JSON (Netscape-табличный текст)."""
    raw = os.environ.get("INSTAGRAM_COOKIES_JSON", "").strip()
    if not raw:
        return None
    cookies: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]
    return cookies if cookies else None


def _cookie_jar_from_netscape(path: Path) -> requests.cookies.RequestsCookieJar:
    jar = MozillaCookieJar()
    jar.load(str(path), ignore_discard=True, ignore_expires=True)
    return requests.utils.cookiejar_from_dict(requests.utils.dict_from_cookiejar(jar))


def _apply_netscape_cookies(cl, path: Path) -> None:
    cookie_dict = _parse_netscape_cookie_dict(path)
    if not cookie_dict:
        raise ValueError(f"файл cookies пустой или неверного формата: {path}")

    sessionid = cookie_dict.get("sessionid", "")
    jar = _cookie_jar_from_netscape(path)
    cl.private.cookies.update(jar)
    cl.public.cookies.update(jar)
    cl.settings["cookies"] = dict(cookie_dict)
    if sessionid and cookie_dict.get("ds_user_id"):
        cl.authorization_data = {
            "ds_user_id": str(cookie_dict["ds_user_id"]),
            "sessionid": sessionid,
            "should_use_header_over_cookies": True,
        }
    cl.init()
    logger.info("instagrapi: Netscape cookies loaded from %s (user_id=%s)", path, cl.user_id)


def _load_cookies_into_client(cl, path: Path) -> None:
    """JSON settings instagrapi или Netscape cookies.txt (как в браузере)."""
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    first = lines[0].strip().lower() if lines else ""
    if first.startswith("# netscape") or (lines and "\t" in lines[0]):
        _apply_netscape_cookies(cl, path)
        return
    try:
        cl.load_settings(path)
        logger.info("instagrapi: settings loaded from %s", path)
    except Exception:
        _apply_netscape_cookies(cl, path)


def _apply_env_cookies(cl) -> None:
    """Применяет cookies из INSTAGRAM_COOKIES_JSON env var напрямую в клиент."""
    env_cookies = _load_cookies_from_env()
    if not env_cookies:
        return
    sessionid = env_cookies.get("sessionid", "")
    jar = requests.utils.cookiejar_from_dict(env_cookies)
    cl.private.cookies.update(jar)
    cl.public.cookies.update(jar)
    cl.settings["cookies"] = dict(env_cookies)
    if sessionid and env_cookies.get("ds_user_id"):
        cl.authorization_data = {
            "ds_user_id": str(env_cookies["ds_user_id"]),
            "sessionid": sessionid,
            "should_use_header_over_cookies": True,
        }
    cl.init()
    logger.info("instagrapi: env cookies applied (user_id=%s)", cl.user_id)


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
        elif _load_cookies_from_env():
            try:
                _apply_env_cookies(cl)
                _cookies_loaded = True
            except Exception as exc:
                logger.error("instagrapi env cookies failed: %s", exc)
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


def scrub_instagram_secrets() -> None:
    """На паузе удаляем cookies/сессию с диска — чтобы instagrapi не дёргал Instagram."""
    if not settings.instagram_paused:
        return
    for path in (settings.instagram_cookies_file, settings.instagram_session_file):
        try:
            if path.is_file():
                path.unlink()
                logger.info("Instagram pause: removed %s", path)
        except OSError as exc:
            logger.warning("Instagram pause: could not remove %s: %s", path, exc)


def instagram_user_message() -> str:
    if settings.instagram_paused:
        return INSTAGRAM_PAUSED_MSG
    if _cookies_loaded or (_client is not None and _client.user_id is not None):
        return ""
    if settings.instagram_is_active():
        return ""
    return INSTAGRAM_NO_CREDS_MSG


def init_instagram_downloader() -> None:
    """Вызов при старте приложения (Render on_startup)."""
    scrub_instagram_secrets()
    if settings.instagram_paused:
        logger.info("Instagram downloader: PAUSED (INSTAGRAM_PAUSED)")
        return
    if not settings.instagram_is_active():
        logger.info("Instagram downloader: no cookies/session — skip init")
        return
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


# ── Private API: самый быстрый путь ──────────────────────────────────

_SHORTCODE_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

def _shortcode_to_media_id(shortcode: str) -> int:
    """Конвертирует shortcode в numeric media_id (base64-like)."""
    result = 0
    for ch in shortcode:
        result = result * 64 + _SHORTCODE_CHARS.index(ch)
    return result


def _extract_shortcode(url: str) -> str | None:
    """Извлекает shortcode/media_id из URL.

    Поддерживает: /reel/XXX, /p/XXX, /tv/XXX, /stories/user/ID, /s/ID?story_media_id=XXX
    """
    import re
    from urllib.parse import urlparse, parse_qs

    # Stories: /stories/username/3121992728853110933
    m = re.search(r"/stories/[^/]+/(\d+)", url)
    if m:
        return m.group(1)

    # Highlights: /s/XXX?story_media_id=1823418211811645388
    if "/s/" in url:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        story_id = qs.get("story_media_id", [None])[0]
        if story_id:
            return story_id
        # Fallback: extract base64 ID from path
        m = re.search(r"/s/([A-Za-z0-9_-]+)", url)
        if m:
            return m.group(1)

    # Reels/Posts: /reel/XXX, /p/XXX, /tv/XXX
    m = re.search(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


def _load_cookies_dict() -> dict[str, str]:
    """Загружает cookies.txt в dict для requests."""
    path = _cookies_file()
    if path.is_file():
        return _parse_netscape_cookie_dict(path)
    return _load_cookies_from_env() or {}


def _download_via_private_api(url: str) -> tuple[Path, str] | None:
    """
    Прямой путь: shortcode → media_id → /api/v1/media/{id}/info/
    → прямая ссылка на видео → stream в память → запись на диск.
    Самый быстрый метод (~0.5-2с на скачивание).
    Возвращает (path, caption).
    """
    shortcode = _extract_shortcode(url)
    if not shortcode:
        return None

    cookies = _load_cookies_dict()
    if not cookies.get("sessionid"):
        return None

    media_id = _shortcode_to_media_id(shortcode)
    api_url = f"https://www.instagram.com/api/v1/media/{media_id}/info/"

    headers = {
        "User-Agent": "Instagram 275.0.0.27.98 Android",
        "X-IG-App-ID": "936619743392459",
        "Accept": "*/*",
        "Accept-Language": "en-US",
    }

    try:
        resp = requests.get(api_url, headers=headers, cookies=cookies, timeout=10)
        if resp.status_code != 200:
            logger.info("private API %s returned %s", media_id, resp.status_code)
            return None

        data = resp.json()
        media = data.get("items", [{}])[0]

        # Caption (текст под видео)
        caption_obj = media.get("caption")
        caption = ""
        if isinstance(caption_obj, dict):
            caption = caption_obj.get("text", "")
        elif isinstance(caption_obj, str):
            caption = caption_obj

        # Ищем URL видео
        video_url = media.get("video_versions", [{}])[0].get("url") if media.get("video_versions") else None
        if not video_url:
            # Carousel — берём первый видео-элемент
            carousel = media.get("carousel_media", [])
            for item in carousel:
                if item.get("video_versions"):
                    video_url = item["video_versions"][0].get("url")
                    break

        if not video_url:
            logger.info("private API: no video URL in response for %s", shortcode)
            return None

        # Скачиваем видео напрямую по URL → на диск
        dest = _dest_path()
        with requests.get(video_url, stream=True, timeout=30, headers=headers) as dl:
            dl.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in dl.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    f.write(chunk)

        if dest.stat().st_size < 1024:
            dest.unlink(missing_ok=True)
            return None

        check_file_size(dest, source_url=url)
        logger.info("private-api OK %s -> %s (%s bytes)", url, dest, dest.stat().st_size)
        return dest, caption
    except Exception as exc:
        logger.info("private API failed for %s: %s", url, exc)
        return None


# ── yt-dlp: быстрое извлечение прямой ссылки ──────────────────────────


def _ytdlp_extract_url(url: str) -> str | None:
    """Извлекает прямую ссылку на видео через yt-dlp (без скачивания)."""
    try:
        cmd = [
            "yt-dlp",
            "--no-warnings",
            "--no-check-certificates",
            "--no-playlist",
            "--no-cache-dir",
            "-j",
            url,
        ]
        cookies_path = _cookies_file()
        if cookies_path.is_file():
            cmd.insert(1, "--cookies")
            cmd.insert(2, str(cookies_path))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            logger.info("yt-dlp extract failed: %s", result.stderr[:200])
            return None

        info = json.loads(result.stdout)
        video_url = info.get("url")
        if not video_url:
            formats = info.get("formats", [])
            if formats:
                # Берём лучший MP4-формат
                mp4s = [f for f in formats if f.get("vcodec", "none") != "none"]
                if mp4s:
                    video_url = mp4s[-1].get("url")
        return video_url
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as exc:
        logger.info("yt-dlp extract error: %s", exc)
        return None


def _download_direct_url(direct_url: str, dest: Path) -> None:
    """Скачивает видео по прямой URL через requests."""
    with requests.get(direct_url, stream=True, timeout=40) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                f.write(chunk)


def _download_ytdlp_fast(url: str) -> Path | None:
    """Быстрый путь: yt-dlp извлекает URL → requests скачивает."""
    direct = _ytdlp_extract_url(url)
    if not direct:
        return None
    dest = _dest_path()
    _download_direct_url(direct, dest)
    if dest.stat().st_size < 1024:
        dest.unlink(missing_ok=True)
        return None
    check_file_size(dest, source_url=url)
    logger.info("ytdlp-fast OK %s -> %s (%s bytes)", url, dest, dest.stat().st_size)
    return dest


def _download_ytdlp_fallback(url: str) -> Path:
    """Полный fallback: yt-dlp скачивает сам."""
    dest = _dest_path()
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-check-certificates",
        "--no-playlist",
        "--no-cache-dir",
        "-f", "best[ext=mp4]/best",
        "-o", str(dest),
        url,
    ]
    cookies_path = _cookies_file()
    if cookies_path.is_file():
        cmd.insert(1, "--cookies")
        cmd.insert(2, str(cookies_path))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr[:200]}")
    if not dest.exists():
        # yt-dlp может добавить расширение
        candidates = list(dest.parent.glob(f"{dest.stem}*"))
        if candidates:
            dest = candidates[0]
        else:
            raise RuntimeError("yt-dlp: файл не создан")
    check_file_size(dest, source_url=url)
    logger.info("ytdlp-fallback OK %s -> %s (%s bytes)", url, dest, dest.stat().st_size)
    return dest


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


def check_file_size(path: Path, *, source_url: str = "") -> None:
    from bot_messages import video_too_heavy_message

    size = os.path.getsize(path)
    if size > TELEGRAM_MAX_BYTES:
        path.unlink(missing_ok=True)
        raise ValueError(video_too_heavy_message(source_url or None))


def _download_instagram_video_once(clean: str) -> Path:
    cl = _get_client()
    if cl.user_id is None and _cookies_file().is_file():
        raise RuntimeError(COOKIES_EXPIRED_MSG)

    media_pk = cl.media_pk_from_url(clean)
    folder = _downloads_dir()

    try:
        raw_path = cl.clip_download(media_pk, folder=folder)
    except Exception as exc:
        if _is_timeout_error(exc):
            raise
        logger.info("clip_download failed, trying video_download: %s", exc)
        raw_path = cl.video_download(media_pk, folder=folder)

    # Переименовываем вместо копирования — экономим время и диск
    dest = _dest_path()
    os.rename(str(raw_path), str(dest))
    check_file_size(dest, source_url=clean)
    logger.info("instagrapi OK %s -> %s (%s bytes)", clean, dest, dest.stat().st_size)
    return dest


def download_instagram_video(url: str) -> tuple[Path, str]:
    """
    Скачивание Reel: private API (быстрый) → yt-dlp fast → yt-dlp → instagrapi.
    Возвращает (path, caption).
    """
    from bot_stats import DownloadStat, bot_stats

    t0 = time.monotonic()
    if settings.instagram_paused:
        raise RuntimeError(INSTAGRAM_PAUSED_MSG)
    if not settings.instagram_is_active():
        raise RuntimeError(INSTAGRAM_NO_CREDS_MSG)

    clean = clean_instagram_url(url)
    if not is_instagram_media_url(clean):
        raise ValueError("нужна ссылка Instagram: /reel/, /p/, /stories/ или /s/")

    # Путь 1: Instagram private API — напрямую (~0.5-2с)
    try:
        result = _download_via_private_api(clean)
        if result:
            path, caption = result
            ms = int((time.monotonic() - t0) * 1000)
            bot_stats.record_download(DownloadStat(url=clean, ok=True, method="private-api", size=path.stat().st_size, elapsed_ms=ms, ts=time.time()))
            return path, caption
    except Exception as exc:
        logger.warning("private-api failed: %s", exc)

    # Путь 2: yt-dlp — извлечение прямой ссылки (~1-3с)
    try:
        path = _download_ytdlp_fast(clean)
        if path:
            ms = int((time.monotonic() - t0) * 1000)
            bot_stats.record_download(DownloadStat(url=clean, ok=True, method="ytdlp-fast", size=path.stat().st_size, elapsed_ms=ms, ts=time.time()))
            return path, ""
    except Exception as exc:
        logger.warning("ytdlp-fast failed: %s", exc)

    # Путь 3: yt-dlp полный fallback (~3-8с)
    try:
        path = _download_ytdlp_fallback(clean)
        ms = int((time.monotonic() - t0) * 1000)
        bot_stats.record_download(DownloadStat(url=clean, ok=True, method="ytdlp-full", size=path.stat().st_size, elapsed_ms=ms, ts=time.time()))
        return path, ""
    except Exception as exc:
        logger.warning("ytdlp-fallback failed: %s", exc)

    # Путь 4: instagrapi — последний fallback
    from instagrapi.exceptions import ClientError

    last_exc: Exception | None = None
    for attempt in range(DOWNLOAD_MAX_RETRIES):
        try:
            path = _download_instagram_video_once(clean)
            ms = int((time.monotonic() - t0) * 1000)
            bot_stats.record_download(DownloadStat(url=clean, ok=True, method="instagrapi", size=path.stat().st_size, elapsed_ms=ms, ts=time.time()))
            return path, ""
        except ValueError:
            raise
        except RuntimeError:
            raise
        except ClientError as exc:
            last_exc = exc
            if _is_timeout_error(exc) and attempt < DOWNLOAD_MAX_RETRIES - 1:
                logger.warning(
                    "instagrapi timeout attempt %s/%s: %s",
                    attempt + 1, DOWNLOAD_MAX_RETRIES, exc,
                )
                continue
            ms = int((time.monotonic() - t0) * 1000)
            bot_stats.record_download(DownloadStat(url=clean, ok=False, method="instagrapi", size=0, elapsed_ms=ms, ts=time.time(), error=str(exc)[:120]))
            raise _runtime_error_for(exc) from exc
        except Exception as exc:
            last_exc = exc
            if _is_timeout_error(exc) and attempt < DOWNLOAD_MAX_RETRIES - 1:
                logger.warning(
                    "instagrapi timeout attempt %s/%s: %s",
                    attempt + 1, DOWNLOAD_MAX_RETRIES, exc,
                )
                continue
            ms = int((time.monotonic() - t0) * 1000)
            bot_stats.record_download(DownloadStat(url=clean, ok=False, method="instagrapi", size=0, elapsed_ms=ms, ts=time.time(), error=str(exc)[:120]))
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
