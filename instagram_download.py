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

import random
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiohttp
import aiohttp_socks

from config import settings
from instagram_urls import clean_instagram_url, is_instagram_media_url

logger = logging.getLogger(__name__)

TELEGRAM_MAX_BYTES = 52_428_800  # 50 MiB
INSTAGRAM_REQUEST_TIMEOUT = 20
DOWNLOAD_MAX_RETRIES = 1
DOWNLOAD_RETRY_DELAY_SEC = 0.2
DOWNLOAD_TOTAL_TIMEOUT_SEC = 90  # Render free tier — медленная сеть
DOWNLOAD_CHUNK_SIZE = 262144

_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
]

def _random_ua() -> str:
    return random.choice(_USER_AGENTS)


@asynccontextmanager
async def _aiohttp_session() -> AsyncIterator[aiohttp.ClientSession]:
    timeout = aiohttp.ClientTimeout(total=INSTAGRAM_REQUEST_TIMEOUT)
    connector = None
    if PROXY_ENABLED:
        connector = aiohttp_socks.ProxyConnector.from_url(PROXY_URL)
    session = aiohttp.ClientSession(
        timeout=timeout, connector=connector,
        headers={"User-Agent": _random_ua()})
    try:
        yield session
    finally:
        await session.close()

# --- SOCKS5 proxy (xray local) ---
PROXY_URL = os.environ.get("PROXY_URL", "socks5h://127.0.0.1:10808")
PROXY_ENABLED = os.environ.get("PROXY_ENABLED") == "1"

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
_instagrapi_lock: asyncio.Lock | None = None  # защита от конкурентного доступа


def _get_instagrapi_lock() -> asyncio.Lock:
    global _instagrapi_lock
    if _instagrapi_lock is None:
        _instagrapi_lock = asyncio.Lock()
    return _instagrapi_lock


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


def _parse_netscape_cookies_as_dict(path: Path) -> dict[str, str]:
    jar = MozillaCookieJar()
    jar.load(str(path), ignore_discard=True, ignore_expires=True)
    return {c.name: c.value for c in jar}


def _apply_netscape_cookies(cl, path: Path) -> None:
    cookie_dict = _parse_netscape_cookie_dict(path)
    if not cookie_dict:
        raise ValueError(f"файл cookies пустой или неверного формата: {path}")

    sessionid = cookie_dict.get("sessionid", "")
    jar = _parse_netscape_cookies_as_dict(path)
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
    cl.private.cookies.update(env_cookies)
    cl.public.cookies.update(env_cookies)
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
    return _build_client()  # _build_client has internal lock + None-check


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


def _strip_cdn_params(url: str) -> str:
    """Убирает ограничения размера из CDN URL Instagram.

    Instagram CDN (scontent.cdninstagram.com, cdninstagram.com, fbcdn.net)
    ограничивает размер через:
    - Query-параметры (_nc_cat, _nc_ohc, oh, oe, stp и т.д.)
    - Path-компоненты (/s150x150/, /c0.135.1080.1080/, /e15/ и т.д.)
    Без ограничений CDN отдаёт полноразмерное изображение.
    """
    from urllib.parse import urlparse
    import re
    if "cdninstagram.com" not in url and "fbcdn.net" not in url:
        return url
    parsed = urlparse(url)
    path = parsed.path
    # Убираем /sNNNxNNN/ (размер)
    path = re.sub(r'/s\d+x\d+/', '/', path)
    # Убираем /cNNN.NNN.NNN.NNN/ (crop)
    path = re.sub(r'/c[\d.]+/', '/', path)
    # Убираем /eNN/ (enhancement/edge)
    path = re.sub(r'/e\d+/', '/', path)
    # Возвращаем БЕЗ query-параметров (убираем _nc_*, stp, oh, oe и т.д.)
    return f"{parsed.scheme}://{parsed.netloc}{path}"


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
    """Загружает cookies.txt в dict для HTTP-запросов."""
    path = _cookies_file()
    if path.is_file():
        return _parse_netscape_cookie_dict(path)
    return _load_cookies_from_env() or {}


async def _download_via_private_api(url: str) -> tuple[list[Path], str] | None:
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
        async with _aiohttp_session() as _s:
            async with _s.get(api_url, headers=headers, cookies=cookies) as resp:
                if resp.status != 200:
                    logger.info("private API %s returned %s", shortcode, resp.status)
                    return None
                data = await resp.json()
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
                media_type = media.get("media_type", "?")
                product_type = media.get("product_type", "?")
                has_carousel = bool(carousel)
                logger.warning(
                    "private API: no image URL for %s (media_type=%s product_type=%s carousel=%s, "
                    "has_video_versions=%s, has_image_versions2=%s, keys=%s)",
                    shortcode, media_type, product_type, has_carousel,
                    bool(media.get("video_versions")),
                    bool(media.get("image_versions2")),
                    list(media.keys())[:15],
                )
                return None
            # Скачиваем ВСЕ изображения
            dests: list[Path] = []
            for idx, img_url in enumerate(image_urls):
                dest = _dest_path_image()
                try:
                    async with _aiohttp_session() as _s:
                        async with _s.get(img_url, headers=headers, cookies=cookies) as dl:
                            logger.info("private-api photo[%d] download status=%s url=%s", idx, dl.status, img_url[:80])
                            dl.raise_for_status()
                            with open(dest, "wb") as f:
                                async for chunk in dl.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
                                    f.write(chunk)
                except Exception as dl_exc:
                    logger.warning("private-api photo[%d] download failed for %s: %s", idx, shortcode, dl_exc)
                    continue
                if dest.stat().st_size < 1024:
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
        async with _aiohttp_session() as _s:
            async with _s.get(video_url, headers=headers) as dl:
                dl.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in dl.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
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


async def _ytdlp_extract_url(url: str) -> str | None:
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
        if PROXY_ENABLED:
            cmd.extend(["--proxy", PROXY_URL])
        cookies_path = _cookies_file()
        if cookies_path.is_file():
            cmd.insert(1, "--cookies")
            cmd.insert(2, str(cookies_path))
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            logger.info("yt-dlp extract failed: %s", stderr.decode(errors="ignore")[:200])
            return None

        info = json.loads(stdout)
        video_url = info.get("url")
        if not video_url:
            formats = info.get("formats", [])
            if formats:
                # Берём формат с видео И аудио (DASH-потоки без звука — пропускаем)
                mp4s = [
                    f for f in formats
                    if f.get("vcodec", "none") != "none"
                    and f.get("acodec", "none") != "none"
                ]
                if mp4s:
                    video_url = mp4s[-1].get("url")
                else:
                    # Нет формата с аудио → fallback через _download_ytdlp_fallback
                    # (корректно мержит video+audio через ffmpeg)
                    return None
        return video_url
    except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as exc:
        logger.info("yt-dlp extract error: %s", exc)
        return None


async def _download_direct_url(direct_url: str, dest: Path) -> None:
    async with _aiohttp_session() as session:
        async with session.get(direct_url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
                    f.write(chunk)


async def _download_ytdlp_fast(url: str) -> Path | None:
    """Быстрый путь: yt-dlp извлекает URL → aiohttp скачивает."""
    direct = await _ytdlp_extract_url(url)
    if not direct:
        return None
    dest = _dest_path()
    await _download_direct_url(direct, dest)
    if dest.stat().st_size < 1024:
        dest.unlink(missing_ok=True)
        return None
    check_file_size(dest, source_url=url)
    logger.info("ytdlp-fast OK %s -> %s (%s bytes)", url, dest, dest.stat().st_size)
    return dest


async def _download_ytdlp_fallback(url: str) -> Path:
    """Полный fallback: yt-dlp скачивает сам."""
    dest = _dest_path()
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-check-certificates",
        "--no-playlist",
        "--no-cache-dir",
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "--merge-output-format", "mp4",
        "-o", str(dest),
        url,
    ]
    if PROXY_ENABLED:
        cmd.extend(["--proxy", PROXY_URL])
    cookies_path = _cookies_file()
    if cookies_path.is_file():
        cmd.insert(1, "--cookies")
        cmd.insert(2, str(cookies_path))
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {stderr.decode(errors='ignore')[:200]}")
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


async def _ytdlp_download_thumbnail(url: str) -> Path | None:
    """Извлекает фото из Instagram поста через yt-dlp --dump-json.

    Для фото-постов yt-dlp не может скачать, но может извлечь метаданные.
    Работает через proxy если настроен.
    """
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-check-certificates",
        "--no-playlist",
        "--no-cache-dir",
        "-j",  # dump JSON metadata
        url,
    ]
    if PROXY_ENABLED:
        cmd.extend(["--proxy", PROXY_URL])
    cookies_path = _cookies_file()
    if cookies_path.is_file():
        cmd.insert(1, "--cookies")
        cmd.insert(2, str(cookies_path))
    logger.info("ytdlp-json: extracting metadata for %s", url)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp json failed: {stderr.decode(errors='ignore')[:200]}")

    try:
        info = json.loads(stdout.decode(errors="ignore"))
    except (json.JSONDecodeError, ValueError):
        raise RuntimeError("yt-dlp json: invalid output")

    # Ищем URL изображения в метаданных
    img_url = info.get("thumbnail") or info.get("display_url")
    if not img_url:
        # Для карусели берём первый элемент
        entries = info.get("entries", [])
        if entries:
            img_url = entries[0].get("thumbnail") or entries[0].get("display_url")
    if not img_url:
        raise RuntimeError("yt-dlp json: no image URL in metadata")

    # Скачиваем изображение
    img_url = _strip_cdn_params(img_url)
    dest = _dest_path_image()
    async with _aiohttp_session() as _s:
        async with _s.get(img_url) as dl:
            dl.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in dl.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
                    f.write(chunk)

    if dest.stat().st_size < 1024:
        dest.unlink(missing_ok=True)
        return None
    check_file_size(dest, source_url=url)
    logger.info("ytdlp-json OK %s -> %s (%s bytes)", url, dest, dest.stat().st_size)
    return dest


async def _download_oembed_thumbnail(url: str, cookies: dict | None = None) -> Path | None:
    """Прямой вызов oEmbed API для получения thumbnail фото.

    Последний fallback — thumbnail может быть небольшого размера,
    но лучше чем ничего.
    """
    shortcode = _extract_shortcode(url)
    if not shortcode:
        return None

    oembed_url = f"https://api.instagram.com/oembed/?url=https://www.instagram.com/p/{shortcode}/"
    logger.info("oembed-direct: fetching for %s", shortcode)
    async with _aiohttp_session() as _s:
        async with _s.get(oembed_url, cookies=cookies) as resp:
            logger.info("oembed-direct: status=%s for %s", resp.status, shortcode)
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"oEmbed {resp.status}: {body[:100]}")
            data = await resp.json()

    thumbnail = data.get("thumbnail_url", "")
    if not thumbnail or not thumbnail.startswith("http"):
        raise RuntimeError("oEmbed: no thumbnail_url")

    # Скачиваем thumbnail (с query-параметрами — они нужны для CDN)
    logger.info("oembed-direct: downloading %s", thumbnail[:100])
    dest = _dest_path_image()
    async with _aiohttp_session() as _s:
        async with _s.get(thumbnail, cookies=cookies) as dl:
            logger.info("oembed-direct: image status=%s", dl.status)
            dl.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in dl.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
                    f.write(chunk)

    if dest.stat().st_size < 1024:
        dest.unlink(missing_ok=True)
        return None
    check_file_size(dest, source_url=url)
    logger.info("oembed-direct OK %s -> %s (%s bytes)", url, dest, dest.stat().st_size)
    return dest


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
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


async def _download_instagram_video_once(clean: str) -> Path:
    t_lock = time.monotonic()
    async with _get_instagrapi_lock():
        lock_wait_ms = int((time.monotonic() - t_lock) * 1000)
        if lock_wait_ms > 100:
            logger.info("instagrapi-video lock-wait %dms for %s", lock_wait_ms, clean)
        def _sync() -> Path:
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

        return await asyncio.to_thread(_sync)


async def _download_photo_via_instagrapi(url: str) -> tuple[list[Path], str] | None:
    """Скачивание фото через instagrapi.photo_download (тот же клиент, что и для видео).

    Использует media_info_v1 + photo_download_by_url. Работает в thread pool,
    чтобы не блокировать async event loop.
    """
    t_lock = time.monotonic()
    async with _get_instagrapi_lock():
        lock_wait_ms = int((time.monotonic() - t_lock) * 1000)
        if lock_wait_ms > 100:
            logger.info("instagrapi-photo lock-wait %dms for %s", lock_wait_ms, url)
        def _sync() -> tuple[list[Path], str]:
            cl = _get_client()
            if cl.user_id is None and _cookies_file().is_file():
                raise RuntimeError(COOKIES_EXPIRED_MSG)
            media_pk = cl.media_pk_from_url(url)
            media = cl.media_info(media_pk)
            folder = _downloads_dir()

            # media_type: 1=photo, 2=video, 8=album
            if media.media_type == 2:
                raise RuntimeError("not a photo post")

            paths: list[Path] = []

            # Для альбомов (media_type==8) thumbnail_url ОТСУТСТВУЕТ — берём из resources
            if media.resources:
                for res in media.resources:
                    if res.media_type == 1 and res.thumbnail_url:
                        p = cl.photo_download_by_url(res.thumbnail_url, folder=folder)
                        paths.append(p)
            elif media.thumbnail_url:
                # Одиночное фото (media_type==1) — thumbnail_url = largest candidate
                p = cl.photo_download_by_url(media.thumbnail_url, folder=folder)
                paths.append(p)
            else:
                # Fallback: пробуем media_info_v1 напрямую
                try:
                    raw = cl.media_info_v1(media_pk)
                    if hasattr(raw, "resources") and raw.resources:
                        for res in raw.resources:
                            if res.media_type == 1 and res.thumbnail_url:
                                p = cl.photo_download_by_url(res.thumbnail_url, folder=folder)
                                paths.append(p)
                    elif hasattr(raw, "thumbnail_url") and raw.thumbnail_url:
                        p = cl.photo_download_by_url(raw.thumbnail_url, folder=folder)
                        paths.append(p)
                except Exception as v1_exc:
                    logger.warning("instagrapi media_info_v1 fallback failed: %s", v1_exc)

            if not paths:
                raise RuntimeError("instagrapi: no photo URL found")

            caption = media.caption_text or ""
            # Перемещаем в стандартные имена
            result: list[Path] = []
            for p in paths:
                dest = _dest_path_image()
                os.rename(str(p), str(dest))
                check_file_size(dest, source_url=url)
                result.append(dest)
            logger.info("instagrapi-photo OK %s -> %d images (%s bytes total)", url, len(result), sum(d.stat().st_size for d in result))
            return result, caption

        return await asyncio.to_thread(_sync)


async def _download_photo_via_embed(url: str, cookies: dict | None = None) -> tuple[list[Path], str] | None:
    """
    Fallback для фото-постов: парсит embed-страницу Instagram.
    Работает без авторизации (~2-4с).
    Возвращает ([path, ...], caption) или None.
    """
    shortcode = _extract_shortcode(url)
    if not shortcode:
        return None

    embed_url = f"https://www.instagram.com/p/{shortcode}/embed/"
    logger.info("embed: starting photo download for %s, proxy=%s, cookies=%s",
                shortcode, "ON" if PROXY_ENABLED else "OFF", "YES" if cookies else "NO")
    try:
        image_url: str | None = None
        orig_image_url: str | None = None  # до _strip_cdn_params
        embed_html: str | None = None

        # 1) Fetch embed page (with retry on rate-limit)
        from bs4 import BeautifulSoup

        for _embed_attempt in range(3):
            try:
                logger.info("embed: fetching %s (attempt %s)...", embed_url[:60], _embed_attempt + 1)
                async with _aiohttp_session() as _s:
                    async with _s.get(embed_url, cookies=cookies) as resp:
                        logger.info("embed page status=%s for %s (attempt %s)",
                                   resp.status, shortcode, _embed_attempt + 1)
                        if resp.status == 429:
                            logger.warning("embed rate-limited (429), waiting 3s...")
                            await asyncio.sleep(3)
                            continue
                        resp.raise_for_status()
                        embed_html = await resp.text()
                        logger.info("embed: got %d chars HTML for %s", len(embed_html), shortcode)
                        # Проверяем что это не login wall
                        if "login" in embed_html[:500].lower() and len(embed_html) < 2000:
                            logger.warning("embed: got login wall (%d chars) for %s", len(embed_html), shortcode)
                        break
            except Exception as embed_exc:
                logger.warning("embed attempt %s failed: %s", _embed_attempt + 1, embed_exc)
                if _embed_attempt < 2:
                    await asyncio.sleep(2)

        # 2) Try oEmbed API (parallel strategy)
        if not embed_html:
            logger.info("embed page failed, trying oEmbed API for %s", shortcode)
            oembed_url = f"https://api.instagram.com/oembed/?url=https://www.instagram.com/p/{shortcode}/"
            try:
                async with _aiohttp_session() as _s:
                    async with _s.get(oembed_url, cookies=cookies) as oe_resp:
                        logger.info("oEmbed status=%s for %s", oe_resp.status, shortcode)
                        if oe_resp.status == 200:
                            oe_data = await oe_resp.json()
                            thumbnail = oe_data.get("thumbnail_url", "")
                            logger.info("oEmbed response: thumbnail_url=%s", thumbnail[:120] if thumbnail else "EMPTY")
                            if thumbnail and thumbnail.startswith("http"):
                                orig_image_url = thumbnail
                                image_url = _strip_cdn_params(thumbnail)
                                logger.info("oEmbed thumbnail_url found: %s", thumbnail[:120])
                        else:
                            oe_body = await oe_resp.text()
                            logger.warning("oEmbed error %s: %s", oe_resp.status, oe_body[:200])
            except Exception as oe_exc:
                logger.warning("oEmbed exception for %s: %s", shortcode, oe_exc)

        # 3) Parse embed HTML for image
        if embed_html:
            soup = BeautifulSoup(embed_html, "html.parser")

            # Strategy A: og:image meta tag
            og_img = soup.find("meta", attrs={"property": "og:image"})
            if og_img:
                og_val = og_img.get("content", "").strip()
                if og_val and og_val.startswith("http"):
                    orig_image_url = og_val
                    image_url = _strip_cdn_params(og_val)
                    logger.info("embed og:image found: %s", og_val[:100])

            # Strategy B: largest <img> in embed (not tiny avatars)
            if not image_url:
                for img in soup.find_all("img"):
                    src = img.get("src", "")
                    if not src.startswith("http"):
                        continue
                    # Skip avatars, icons, small images
                    width = img.get("width", "")
                    if width and width.isdigit() and int(width) < 100:
                        continue
                    # cdninstagram images are the actual post content
                    if "cdninstagram" in src or "fbcdn" in src:
                        orig_image_url = src
                        image_url = _strip_cdn_params(src)
                        logger.info("embed <img> cdninstagram found: %s", src[:100])
                        break

        if not image_url:
            logger.warning("embed/oembed: NO image found for %s", shortcode)
            return None

        # Скачиваем фото (пробуем stripped URL, если не получится — оригинальный)
        dest = _dest_path_image()
        logger.info("downloading image from %s to %s", image_url[:80], dest)
        async with _aiohttp_session() as _s:
            async with _s.get(image_url, cookies=cookies) as img_resp:
                logger.info("image download status=%s content-type=%s size-header=%s",
                            img_resp.status, img_resp.headers.get("content-type"),
                            img_resp.headers.get("content-length"))
                if img_resp.status >= 400 and orig_image_url and image_url != orig_image_url:
                    # CDN отклонил stripped URL — пробуем оригинальный с query-параметрами
                    logger.warning("stripped CDN URL failed (%s), trying original: %s", img_resp.status, orig_image_url[:80])
                    async with _s.get(orig_image_url, cookies=cookies) as img2:
                        logger.info("original URL status=%s", img2.status)
                        img2.raise_for_status()
                        with open(dest, "wb") as f:
                            async for chunk in img2.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
                                f.write(chunk)
                else:
                    img_resp.raise_for_status()
                    with open(dest, "wb") as f:
                        async for chunk in img_resp.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
                            f.write(chunk)

        if dest.stat().st_size < 1024:
            dest.unlink(missing_ok=True)
            return None

        check_file_size(dest, source_url=url)

        logger.info("embed photo OK %s -> %s (%s bytes)", url, dest, dest.stat().st_size)
        return [dest], ""

    except Exception as exc:
        logger.info("embed photo fallback failed for %s: %s", shortcode, exc)
        return None


async def _download_photo_via_page(url: str, cookies: dict | None = None) -> tuple[list[Path], str] | None:
    """
    Fallback для фото: парсит JSON из HTML-страницы поста Instagram.
    Работает без авторизации, но может быть нестабильным.
    """
    shortcode = _extract_shortcode(url)
    if not shortcode:
        return None

    page_url = f"https://www.instagram.com/p/{shortcode}/"
    try:
        logger.info("page: fetching %s for photo extraction (cookies=%s)", page_url, "YES" if cookies else "NO")
        async with _aiohttp_session() as _s:
            async with _s.get(page_url, cookies=cookies) as resp:
                logger.info("page status=%s for %s", resp.status, shortcode)
                if resp.status != 200:
                    return None
                html = await resp.text()
                logger.info("page: len=%s", len(html))

        # Ищем image URL

        # Ищем cdninstagram/fbcdn URL для изображений
        img_pattern = re.compile(r'https?://[^\s"\'<>\\]+(?:cdninstagram\.com|fbcdn\.net)[^\s"\'<>\\]+\.(?:jpg|jpeg|webp)[^\s"\'<>\\]*')
        matches = img_pattern.findall(html)
        if matches:
            # Берём самый длинный URL (обычно самый полный)
            best_url = max(matches, key=len)
            # Декодируем unicode escapes
            best_url = best_url.replace("\\u0026", "&")
            # Убираем query-параметры CDN для полного размера
            best_url = _strip_cdn_params(best_url)
            logger.info("page: found cdninstagram image URL: %s", best_url[:100])

            dest = _dest_path_image()
            async with _aiohttp_session() as _s:
                async with _s.get(best_url, cookies=cookies) as img_resp:
                    logger.info("page image download status=%s", img_resp.status)
                    img_resp.raise_for_status()
                    with open(dest, "wb") as f:
                        async for chunk in img_resp.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
                            f.write(chunk)
            if dest.stat().st_size < 1024:
                dest.unlink(missing_ok=True)
                return None
            logger.info("page photo OK %s -> %s (%s bytes)", url, dest, dest.stat().st_size)
            return [dest], ""

        logger.warning("page: no cdninstagram image found in HTML for %s", shortcode)
        return None

    except Exception as exc:
        logger.warning("page photo error for %s: %s", url, exc, exc_info=True)
        return None


def _is_likely_photo_url(url: str) -> bool:
    """
    Быстрая эвристика: /p/ ссылки ЧАЩЕ фото, /reel/ — видео.
    Не идеально, но позволяет пропустить yt-dlp для явных фото-ссылок.
    """
    return "/p/" in url and "/reel/" not in url


async def download_instagram_video(url: str) -> tuple[list[Path], str]:
    """
    Скачивание Reel: private API (быстрый) → yt-dlp fast → yt-dlp → instagrapi.
    Возвращает ([path, ...], caption).
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
        result = await _download_via_private_api(clean)
        if result:
            paths, caption = result
            ms = int((time.monotonic() - t0) * 1000)
            bot_stats.record_download(DownloadStat(url=clean, ok=True, method="private-api", size=sum(p.stat().st_size for p in paths), elapsed_ms=ms, ts=time.time()))
            return paths, caption
    except Exception as exc:
        logger.warning("private-api failed: %s", exc)

    # Путь 1.5: Для /p/ ссылок — embed/page/oEmbed (HTML parsing)
    # ⚠️ ДОЛЖЕН идти ДО instagrapi, т.к. instagrapi держит asyncio.Lock
    #    и блокирует параллельные скачивания других ссылок.
    photo_errors: list[str] = []
    if _is_likely_photo_url(clean):
        # Загружаем cookies для embed/page запросов
        _ig_cookies = _load_cookies_dict() or None

        try:
            result = await _download_photo_via_embed(clean, cookies=_ig_cookies)
            if result:
                paths, caption = result
                ms = int((time.monotonic() - t0) * 1000)
                bot_stats.record_download(DownloadStat(url=clean, ok=True, method="embed-photo", size=sum(p.stat().st_size for p in paths), elapsed_ms=ms, ts=time.time()))
                return paths, caption
            else:
                photo_errors.append("embed→None")
        except Exception as exc:
            photo_errors.append(f"embed→{exc}")
            logger.warning("embed photo failed for /p/ URL: %s", exc)

        # Fallback: прямой парсинг HTML-страницы поста
        try:
            result = await _download_photo_via_page(clean, cookies=_ig_cookies)
            if result:
                paths, caption = result
                ms = int((time.monotonic() - t0) * 1000)
                bot_stats.record_download(DownloadStat(url=clean, ok=True, method="page-photo", size=sum(p.stat().st_size for p in paths), elapsed_ms=ms, ts=time.time()))
                return paths, caption
            else:
                photo_errors.append("page→None")
        except Exception as exc:
            photo_errors.append(f"page→{exc}")
            logger.warning("page photo failed for /p/ URL: %s", exc)

        # embed/page не справились — пробуем oEmbed напрямую (последний шанс)
        logger.info("embed+page failed for %s (%s), trying direct oEmbed...", clean, "; ".join(photo_errors))
        try:
            path = await _download_oembed_thumbnail(clean, cookies=_ig_cookies)
            if path:
                ms = int((time.monotonic() - t0) * 1000)
                bot_stats.record_download(DownloadStat(url=clean, ok=True, method="oembed-thumb", size=path.stat().st_size, elapsed_ms=ms, ts=time.time()))
                return [path], ""
        except Exception as exc:
            photo_errors.append(f"oembed→{exc}")
            logger.warning("oembed direct failed: %s", exc)

        logger.info("photo methods exhausted for %s (%s), trying video methods...", clean, "; ".join(photo_errors))

    # ─── VIDEO PATHS (также для /p/ — пост может быть видео!) ───

    # Путь 2: yt-dlp — извлечение прямой ссылки (~1-3с)
    try:
        path = await _download_ytdlp_fast(clean)
        if path:
            ms = int((time.monotonic() - t0) * 1000)
            bot_stats.record_download(DownloadStat(url=clean, ok=True, method="ytdlp-fast", size=path.stat().st_size, elapsed_ms=ms, ts=time.time()))
            return [path], ""
    except Exception as exc:
        logger.warning("ytdlp-fast failed: %s", exc)

    # Путь 3: yt-dlp полный fallback (~3-8с)
    try:
        path = await _download_ytdlp_fallback(clean)
        ms = int((time.monotonic() - t0) * 1000)
        bot_stats.record_download(DownloadStat(url=clean, ok=True, method="ytdlp-full", size=path.stat().st_size, elapsed_ms=ms, ts=time.time()))
        return [path], ""
    except Exception as exc:
        logger.warning("ytdlp-fallback failed: %s", exc)

    # Путь 3.5: Embed photo fallback (только если ещё не пробовали в Path 1.7)
    if not photo_errors:
        try:
            result = await _download_photo_via_embed(clean)
            if result:
                paths, caption = result
                ms = int((time.monotonic() - t0) * 1000)
                bot_stats.record_download(DownloadStat(url=clean, ok=True, method="embed-photo", size=sum(p.stat().st_size for p in paths), elapsed_ms=ms, ts=time.time()))
                return paths, caption
        except Exception as exc:
            logger.warning("embed-photo fallback failed: %s", exc)

    # Путь 3.7: instagrapi photo — после ВСЕХ безоплочных методов,
    # т.к. держит asyncio.Lock и блокирует параллельные запросы.
    if _is_likely_photo_url(clean):
        try:
            t_lock = time.monotonic()
            result = await _download_photo_via_instagrapi(clean)
            lock_ms = int((time.monotonic() - t_lock) * 1000)
            logger.info("instagrapi-photo lock-held %dms for %s", lock_ms, clean)
            if result:
                paths, caption = result
                ms = int((time.monotonic() - t0) * 1000)
                bot_stats.record_download(DownloadStat(url=clean, ok=True, method="instagrapi-photo", size=sum(p.stat().st_size for p in paths), elapsed_ms=ms, ts=time.time()))
                return paths, caption
        except Exception as exc:
            photo_errors.append(f"instagrapi-photo→{exc}")
            logger.warning("instagrapi-photo failed: %s", exc)

    # Путь 4: instagrapi video — последний fallback
    from instagrapi.exceptions import ClientError

    last_exc: Exception | None = None
    for attempt in range(DOWNLOAD_MAX_RETRIES):
        try:
            path = await _download_instagram_video_once(clean)
            ms = int((time.monotonic() - t0) * 1000)
            bot_stats.record_download(DownloadStat(url=clean, ok=True, method="instagrapi", size=path.stat().st_size, elapsed_ms=ms, ts=time.time()))
            return [path], ""
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
    if photo_errors:
        raise RuntimeError(f"❌ Не удалось скачать ({'; '.join(photo_errors)})")
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
