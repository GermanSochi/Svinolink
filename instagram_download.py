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
DOWNLOAD_TOTAL_TIMEOUT_SEC = 45  # 45с — достаточно для всех путей, не заставляем ждать
DOWNLOAD_CHUNK_SIZE = 262144  # 256KB — mejor throughput чем 64KB

# --- SOCKS5 proxy (xray local) ---
PROXY_URL = os.environ.get("PROXY_URL", "socks5h://127.0.0.1:10808")
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if os.environ.get("PROXY_ENABLED") == "1" else None

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

_ADMIN_COOKIES_ALERT = (
    "⚠️ **Instagram cookies протухли!**\n\n"
    "Пользователь попытался скачать видео, но сессия истекла.\n"
    "🔧 Обнови `INSTAGRAM_COOKIES_JSON` на Render."
)

_client = None
_last_admin_alert_ts: float = 0  # чтобы не спамить


async def _notify_admin_cookies_expired(bot) -> None:
    """Отправляет уведомление админу один раз в час при протухании cookies."""
    global _last_admin_alert_ts
    now = time.time()
    if now - _last_admin_alert_ts < 3600:
        return
    _last_admin_alert_ts = now
    try:
        for admin_id in settings.admin_ids:
            await bot.send_message(admin_id, _ADMIN_COOKIES_ALERT, parse_mode="Markdown")
    except Exception as exc:
        logger.warning("Failed to notify admin about expired cookies: %s", exc)
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
    """Загружает cookies из env INSTAGRAM_COOKIES_JSON.

    Поддерживает:
    - Netscape format (многострочный, с табами)
    - Pipe-separated Netscape: cookie1|cookie2|cookie3
    - Simple key=value: sessionid=XXX|ds_user_id=YYY (самый надёжный для Render UI)
    """
    raw = os.environ.get("INSTAGRAM_COOKIES_JSON", "").strip()
    if not raw:
        return None

    cookies: dict[str, str] = {}

    # Формат 1: key=value|key=value (самый надёжный для Render)
    if "=" in raw and "\t" not in raw:
        for part in raw.split("|"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                k, v = k.strip(), v.strip()
                if k and v:
                    cookies[k] = v
        return cookies if cookies else None

    # Формат 2: pipe-separated Netscape (табы внутри)
    if "\n" not in raw and "|" in raw:
        lines = [l.strip() for l in raw.split("|") if l.strip()]
    else:
        lines = raw.splitlines()

    for line in lines:
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


def _dest_path_image() -> Path:
    return _downloads_dir() / f"{uuid.uuid4().hex}.jpg"


def is_photo_file(path: Path) -> bool:
    """Check if downloaded file is an image based on extension."""
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}


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


def _download_via_private_api(url: str) -> tuple[list[Path], str] | None:
    """
    Прямой путь: shortcode → media_id → /api/v1/media/{id}/info/
    → прямая ссылка на видео → stream в память → запись на диск.
    Самый быстрый метод (~0.5-2с на скачивание).
    Возвращает ([path, ...], caption).
    """
    import re
    from urllib.parse import urlparse, parse_qs

    cookies = _load_cookies_dict()
    if not cookies.get("sessionid"):
        return None

    headers = {
        "User-Agent": "Instagram 275.0.0.27.98 Android",
        "X-IG-App-ID": "936619743392459",
        "Accept": "*/*",
        "Accept-Language": "en-US",
        "Referer": "https://www.instagram.com/",
    }

    # Определяем тип контента: Stories используют numeric ID напрямую
    is_story = "/stories/" in url or "/s/" in url
    if is_story:
        # Stories: /stories/username/3935441032632448198
        m = re.search(r"/stories/[^/]+/(\d+)", url)
        if m:
            story_id = m.group(1)
        else:
            # Highlights: /s/XXX?story_media_id=XXX
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            story_id = qs.get("story_media_id", [None])[0]
            if not story_id:
                m = re.search(r"/s/(\d+)", url)
                story_id = m.group(1) if m else None
        if not story_id:
            return None
        # Stories требуют другой эндпоинт
        api_url = f"https://i.instagram.com/api/v1/media/{story_id}/info/"
        shortcode = story_id
    else:
        # Reels/Posts: используем shortcode → media_id
        shortcode = _extract_shortcode(url)
        if not shortcode:
            return None
        media_id = _shortcode_to_media_id(shortcode)
        api_url = f"https://www.instagram.com/api/v1/media/{media_id}/info/"

    try:
        resp = requests.get(api_url, headers=headers, cookies=cookies, timeout=10, proxies=PROXIES)
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
            # ═══ IMAGE (photo / image-only carousel) ═══
            media_type = media.get("media_type")
            logger.info("private API: photo path for %s (media_type=%s, has_image_versions2=%s)",
                        shortcode, media_type, bool(media.get("image_versions2")))
            image_urls: list[str] = []
            # Собираем ВСЕ изображения из карусели
            carousel = media.get("carousel_media", [])
            if carousel:
                for item in carousel:
                    img_candidates = item.get("image_versions2", {}).get("candidates", [])
                    if img_candidates:
                        image_urls.append(img_candidates[0]["url"])
            if not image_urls:
                # Одиночное фото (не карусель)
                candidates = media.get("image_versions2", {}).get("candidates", [])
                if candidates:
                    image_urls.append(candidates[0]["url"])
            if not image_urls:
                logger.info("private API: no video or image URL for %s", shortcode)
                return None
            # Скачиваем ВСЕ изображения
            dests: list[Path] = []
            for img_url in image_urls:
                dest = _dest_path_image()
                try:
                    with requests.get(img_url, stream=True, timeout=20, headers=headers, proxies=PROXIES) as dl:
                        dl.raise_for_status()
                        with open(dest, "wb") as f:
                            for chunk in dl.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                                f.write(chunk)
                except Exception as img_exc:
                    logger.warning("private API: image download failed for %s: %s", img_url[:80], img_exc)
                    continue
                if dest.stat().st_size < 1024:
                    logger.info("private API: image too small (%d bytes) for %s", dest.stat().st_size, img_url[:80])
                    dest.unlink(missing_ok=True)
                    continue
                check_file_size(dest, source_url=url)
                dests.append(dest)
            if not dests:
                return None
            logger.info("private-api OK (photo x%d) %s (%s bytes total)", len(dests), url, sum(d.stat().st_size for d in dests))
            return dests, caption

        # Скачиваем видео напрямую по URL → на диск
        dest = _dest_path()
        with requests.get(video_url, stream=True, timeout=30, headers=headers, proxies=PROXIES) as dl:
            dl.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in dl.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    f.write(chunk)

        if dest.stat().st_size < 1024:
            dest.unlink(missing_ok=True)
            return None

        check_file_size(dest, source_url=url)
        logger.info("private-api OK %s -> %s (%s bytes)", url, dest, dest.stat().st_size)
        return [dest], caption
    except Exception as exc:
        logger.info("private API failed for %s: %s", url, exc)
        return None


# ── yt-dlp: быстрое извлечение прямой ссылки ──────────────────────────


def _ytdlp_extract_info(url: str) -> dict | None:
    """Извлекает JSON-метаданные через yt-dlp (url + description)."""
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
        if PROXIES:
            cmd.extend(["--proxy", PROXY_URL])
        cookies_path = _cookies_file()
        if cookies_path.is_file():
            cmd.insert(1, "--cookies")
            cmd.insert(2, str(cookies_path))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.info("yt-dlp extract failed: %s", result.stderr[:200])
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as exc:
        logger.info("yt-dlp extract error: %s", exc)
        return None


def _ytdlp_extract_url(url: str) -> str | None:
    """Извлекает прямую ссылку на видео через yt-dlp (без скачивания)."""
    info = _ytdlp_extract_info(url)
    if not info:
        return None
    video_url = info.get("url")
    if not video_url:
        formats = info.get("formats", [])
        if formats:
            # Берём лучший MP4-формат
            mp4s = [f for f in formats if f.get("vcodec", "none") != "none"]
            if mp4s:
                video_url = mp4s[-1].get("url")
    return video_url


def _ytdlp_extract_image_url(url: str) -> str | None:
    """Извлекает URL изображения из yt-dlp metadata для фото-постов."""
    info = _ytdlp_extract_info(url)
    if not info:
        return None
    # 1. thumbnail — для /p/ это реальное фото поста
    thumb = info.get("thumbnail")
    if thumb:
        return thumb
    # 2. formats без video codec (image-only)
    for fmt in info.get("formats", []):
        if fmt.get("vcodec", "none") == "none" and fmt.get("url"):
            return fmt["url"]
    return None


def _download_ytdlp_image(url: str) -> Path | None:
    """Скачивает фото поста через yt-dlp thumbnail URL."""
    img_url = _ytdlp_extract_image_url(url)
    if not img_url:
        return None
    dest = _dest_path_image()
    _download_direct_url(img_url, dest)
    if dest.stat().st_size < 1024:
        dest.unlink(missing_ok=True)
        return None
    check_file_size(dest, source_url=url)
    logger.info("ytdlp-image OK %s -> %s (%s bytes)", url, dest, dest.stat().st_size)
    return dest


def _ytdlp_extract_caption(url: str) -> str:
    """Извлекает описание/подпись поста через yt-dlp."""
    info = _ytdlp_extract_info(url)
    if not info:
        return ""
    return info.get("description", "") or ""


def _download_direct_url(direct_url: str, dest: Path) -> None:
    """Скачивает видео/фото по прямой URL через requests."""
    dl_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.instagram.com/",
    }
    with requests.get(direct_url, stream=True, timeout=40, headers=dl_headers, proxies=PROXIES) as resp:
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
    if PROXIES:
        cmd.extend(["--proxy", PROXY_URL])
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
    """Скачивание одиночного медиа через instagrapi: clip → video → photo."""
    cl = _get_client()
    if cl.user_id is None and _cookies_file().is_file():
        raise RuntimeError(COOKIES_EXPIRED_MSG)

    media_pk = cl.media_pk_from_url(clean)
    folder = _downloads_dir()

    # Сначала пробуем видео/клипы
    try:
        raw_path = cl.clip_download(media_pk, folder=folder)
        dest = _dest_path()  # .mp4
        os.rename(str(raw_path), str(dest))
        check_file_size(dest, source_url=clean)
        logger.info("instagrapi clip OK %s -> %s (%s bytes)", clean, dest, dest.stat().st_size)
        return dest
    except Exception as exc:
        if _is_timeout_error(exc):
            raise
        logger.info("clip_download failed: %s", exc)

    try:
        raw_path = cl.video_download(media_pk, folder=folder)
        dest = _dest_path()  # .mp4
        os.rename(str(raw_path), str(dest))
        check_file_size(dest, source_url=clean)
        logger.info("instagrapi video OK %s -> %s (%s bytes)", clean, dest, dest.stat().st_size)
        return dest
    except Exception as exc:
        if _is_timeout_error(exc):
            raise
        logger.info("video_download failed, trying photo_download: %s", exc)

    # Одиночные фото: photo_download работает только для media_type=1
    raw_path = cl.photo_download(media_pk, folder=folder)
    # Сохраняем с правильным расширением (не .mp4!)
    suffix = Path(raw_path).suffix or ".jpg"
    dest = _downloads_dir() / f"{uuid.uuid4().hex}{suffix}"
    os.rename(str(raw_path), str(dest))
    check_file_size(dest, source_url=clean)
    logger.info("instagrapi photo OK %s -> %s (%s bytes)", clean, dest, dest.stat().st_size)
    return dest


def _download_instagram_carousel_via_instagrapi(clean: str) -> list[Path] | None:
    """Скачивание карусели через instagrapi.media_info → resources.

    media_type=8 (carousel) не поддерживается clip/video/photo_download.
    Используем media_info().resources и скачиваем каждый элемент по URL.
    Возвращает list[Path] или None.
    """
    cl = _get_client()
    if cl.user_id is None and _cookies_file().is_file():
        raise RuntimeError(COOKIES_EXPIRED_MSG)

    media_pk = cl.media_pk_from_url(clean)
    media = cl.media_info(media_pk)

    # Не карусель — None (пусть другие пути обработают)
    if not media.resources:
        return None

    headers = {
        "User-Agent": "Instagram 275.0.0.27.98 Android",
        "X-IG-App-ID": "936619743392459",
        "Accept": "*/*",
        "Referer": "https://www.instagram.com/",
    }

    paths: list[Path] = []

    for idx, resource in enumerate(media.resources):
        try:
            url = None
            is_video = False
            if resource.video_url:
                url = str(resource.video_url)
                is_video = True
            elif resource.thumbnail_url:
                url = str(resource.thumbnail_url)

            if not url:
                logger.warning("carousel item %d has no URL for %s", idx, clean)
                continue

            dest = _dest_path() if is_video else _dest_path_image()
            with requests.get(url, stream=True, timeout=30, headers=headers, proxies=PROXIES) as dl:
                dl.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in dl.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                        f.write(chunk)

            if dest.stat().st_size < 1024:
                dest.unlink(missing_ok=True)
                continue
            check_file_size(dest, source_url=clean)
            paths.append(dest)
        except Exception as exc:
            logger.warning("carousel item %d download failed for %s: %s", idx, clean, exc)
            continue

    if not paths:
        logger.warning("instagrapi carousel: 0 items downloaded for %s", clean)
        return None

    logger.info("instagrapi carousel OK %s: %d items (%d bytes total)", clean, len(paths), sum(p.stat().st_size for p in paths))
    return paths


def _fetch_caption_only(url: str) -> str:
    """Быстро получить caption через private API без скачивания файла."""
    import re
    from urllib.parse import urlparse, parse_qs

    cookies = _load_cookies_dict()
    if not cookies.get("sessionid"):
        return ""

    headers = {
        "User-Agent": "Instagram 275.0.0.27.98 Android",
        "X-IG-App-ID": "936619743392459",
        "Accept": "*/*",
        "Accept-Language": "en-US",
    }

    is_story = "/stories/" in url or "/s/" in url
    if is_story:
        m = re.search(r"/stories/[^/]+/(\d+)", url)
        if m:
            story_id = m.group(1)
        else:
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)
            story_id = qs.get("story_media_id", [None])[0]
            if not story_id:
                m = re.search(r"/s/(\d+)", url)
                story_id = m.group(1) if m else None
        if not story_id:
            return ""
        api_url = f"https://i.instagram.com/api/v1/media/{story_id}/info/"
    else:
        shortcode = _extract_shortcode(url)
        if not shortcode:
            return ""
        media_id = _shortcode_to_media_id(shortcode)
        api_url = f"https://www.instagram.com/api/v1/media/{media_id}/info/"

    try:
        resp = requests.get(api_url, headers=headers, cookies=cookies, timeout=8, proxies=PROXIES)
        if resp.status_code != 200:
            return ""
        data = resp.json()
        media = data.get("items", [{}])[0]
        caption_obj = media.get("caption")
        if isinstance(caption_obj, dict):
            return caption_obj.get("text", "")
        elif isinstance(caption_obj, str):
            return caption_obj
    except Exception:
        pass
    return ""


def _extract_url_from_ytdlp_info(info: dict) -> str | None:
    """Извлекает прямую ссылку на видео из yt-dlp JSON."""
    video_url = info.get("url")
    if video_url:
        return video_url
    formats = info.get("formats", [])
    if formats:
        mp4s = [f for f in formats if f.get("vcodec", "none") != "none"]
        if mp4s:
            return mp4s[-1].get("url")
    return None


def _extract_image_url_from_ytdlp_info(info: dict) -> str | None:
    """Извлекает URL изображения из yt-dlp JSON."""
    thumb = info.get("thumbnail")
    if thumb:
        return thumb
    for fmt in info.get("formats", []):
        if fmt.get("vcodec", "none") == "none" and fmt.get("url"):
            return fmt["url"]
    return None


def download_instagram_video(url: str) -> tuple[list[Path], str]:
    """Скачивание Instagram media: private API → yt-dlp (1 вызов!) → instagrapi."""
    from bot_stats import DownloadStat, bot_stats

    t0 = time.monotonic()
    if settings.instagram_paused:
        raise RuntimeError(INSTAGRAM_PAUSED_MSG)
    if not settings.instagram_is_active():
        raise RuntimeError(INSTAGRAM_NO_CREDS_MSG)

    clean = clean_instagram_url(url)
    if not is_instagram_media_url(clean):
        raise ValueError("нужна ссылка Instagram: /reel/, /p/, /stories/ или /s/")

    logger.info("download_instagram_video START %s (has_p=%s)", clean, "/p/" in clean)

    # ── Путь 1: Instagram private API (~0.5-2с) ──
    try:
        result = _download_via_private_api(clean)
        if result:
            paths, caption = result
            ms = int((time.monotonic() - t0) * 1000)
            bot_stats.record_download(DownloadStat(url=clean, ok=True, method="private-api", size=sum(p.stat().st_size for p in paths), elapsed_ms=ms, ts=time.time()))
            return paths, caption
    except Exception as exc:
        logger.warning("private-api failed: %s", exc)

    # ── ОДИН вызов yt-dlp вместо трёх! (~3-10с) ──
    yt_info: dict | None = None
    try:
        yt_info = _ytdlp_extract_info(clean)
        if yt_info:
            logger.info("ytdlp info OK (title=%s)", yt_info.get("title"))
    except Exception as exc:
        logger.warning("ytdlp extract failed: %s", exc)

    # Caption из кеша yt_info
    fallback_caption = ""
    if yt_info:
        fallback_caption = yt_info.get("description", "") or ""
    if not fallback_caption:
        try:
            fallback_caption = _fetch_caption_only(clean)
        except Exception:
            pass

    # ── Путь 2: yt-dlp видео URL (из кеша — 0с!) ──
    if yt_info:
        vid_url = _extract_url_from_ytdlp_info(yt_info)
        if vid_url:
            try:
                dest = _dest_path()
                _download_direct_url(vid_url, dest)
                if dest.stat().st_size >= 1024:
                    check_file_size(dest, source_url=clean)
                    ms = int((time.monotonic() - t0) * 1000)
                    bot_stats.record_download(DownloadStat(url=clean, ok=True, method="ytdlp-fast", size=dest.stat().st_size, elapsed_ms=ms, ts=time.time()))
                    return [dest], fallback_caption
                dest.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("ytdlp video dl failed: %s", exc)

    # ── Путь 2.5: yt-dlp фото thumbnail (из кеша — 0с!) ──
    if yt_info:
        img_url = _extract_image_url_from_ytdlp_info(yt_info)
        if img_url:
            logger.info("ytdlp image URL found: %s", img_url[:120])
            try:
                dest = _dest_path_image()
                _download_direct_url(img_url, dest)
                if dest.stat().st_size >= 1024:
                    check_file_size(dest, source_url=clean)
                    ms = int((time.monotonic() - t0) * 1000)
                    bot_stats.record_download(DownloadStat(url=clean, ok=True, method="ytdlp-image", size=dest.stat().st_size, elapsed_ms=ms, ts=time.time()))
                    return [dest], fallback_caption
                logger.info("ytdlp image too small: %d bytes", dest.stat().st_size)
                dest.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("ytdlp image dl failed: %s", exc)

    # ── Путь 3: instagrapi carousel ──
    try:
        carousel_paths = _download_instagram_carousel_via_instagrapi(clean)
        if carousel_paths:
            ms = int((time.monotonic() - t0) * 1000)
            bot_stats.record_download(DownloadStat(url=clean, ok=True, method="instagrapi-carousel", size=sum(p.stat().st_size for p in carousel_paths), elapsed_ms=ms, ts=time.time()))
            return carousel_paths, fallback_caption
    except Exception as exc:
        logger.warning("instagrapi carousel failed: %s", exc)

    # ── Путь 4: instagrapi single (clip → video → photo) ──
    from instagrapi.exceptions import ClientError
    last_exc: Exception | None = None
    for attempt in range(DOWNLOAD_MAX_RETRIES):
        try:
            path = _download_instagram_video_once(clean)
            ms = int((time.monotonic() - t0) * 1000)
            bot_stats.record_download(DownloadStat(url=clean, ok=True, method="instagrapi", size=path.stat().st_size, elapsed_ms=ms, ts=time.time()))
            return [path], fallback_caption
        except (ValueError, RuntimeError):
            raise
        except (ClientError, Exception) as exc:
            last_exc = exc
            if _is_timeout_error(exc) and attempt < DOWNLOAD_MAX_RETRIES - 1:
                continue
            raise _runtime_error_for(exc) from exc

    # ── Путь 5: yt-dlp полный fallback (последний шанс) ──
    try:
        path = _download_ytdlp_fallback(clean)
        ms = int((time.monotonic() - t0) * 1000)
        bot_stats.record_download(DownloadStat(url=clean, ok=True, method="ytdlp-full", size=path.stat().st_size, elapsed_ms=ms, ts=time.time()))
        return [path], fallback_caption
    except Exception:
        pass

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


def remove_files(paths: list[Path] | None) -> None:
    """Удаляет список файлов (для каруселей)."""
    if not paths:
        return
    for p in paths:
        remove_file(p)
