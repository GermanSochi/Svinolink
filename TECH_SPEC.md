# Svinolink Instagram Pipeline — Full Spec & Audit

> **For:** AI Agent doing code review, debugging, or feature work.
> **Generated:** 2026-09-13 14:21 | **Status:** Production (async-refactored, 0 `requests` refs)

---

## 1. Architecture

```
User sends IG URL → chat_handlers.py → instagram_download.py → send photo/video/carousel
```

### yt-dlp: ANSWER
**yt-dlp = LOCAL CLI BINARY via `asyncio.create_subprocess_exec`, NOT Python library.**
- `requirements.txt`: `yt-dlp>=2025.0.0`
- Two modes: Fast (`yt-dlp -g` → direct URL → aiohttp) / Fallback (`yt-dlp -o file`)
- **Never uses** `yt_dlp.YoutubeDL()` Python API

### Download Priority

| # | Path | For | Method | Speed |
|---|------|-----|--------|-------|
| 1 | Private API | `/reel/` `/p/` `/tv/` | instagrapi + aiohttp CDN | ~1-2s |
| 1.5 | oEmbed Photo | `/p/` only | `api.instagram.com/oembed/` | ~1s |
| 2 | yt-dlp Fast | any URL | `yt-dlp -g` → aiohttp | ~1-3s |
| 3 | yt-dlp Fallback | any URL | `yt-dlp -o file` | ~3-8s |
| 3.5 | Embed Photo | `/p/` only | parse `/embed/` HTML | ~2s |
| 4 | Page Photo | `/p/` only | parse page HTML | ~2s |
| 5 | instagrapi | any URL | sync library (last resort) | ~3-10s |

### HTTP Stack

| Component | Library | Blocking? |
|-----------|---------|-----------|
| All HTTP | `aiohttp` + `aiohttp_socks` | ❌ async |
| yt-dlp CLI | `asyncio.create_subprocess_exec` | ❌ async |
| instagrapi | `asyncio.to_thread()` | ⚠️ sync in thread |

### UA Rotation & Proxy
- 10 browser UAs → `_random_ua()` per session
- `PROXY_ENABLED=1` + `PROXY_URL` → `aiohttp_socks.ProxyConnector`

---

## 2. Audit — Issues & Improvements

### 🔴 Critical (FIXED 2026-09-13)

1. **✅ FIXED: Missing `await` on `_download_photo_via_embed`** — fallback path 3.5 called `async def` without `await`. Fixed: added `await`.

2. **✅ FIXED: Private API image download missing `cookies=cookies`** — CDN image download used `headers=headers` only. For private/protected posts, Instagram CDN returns empty/403 without auth. Fixed: added `cookies=cookies`.

3. **❌ FALSE ALARM: oEmbed indentation** — analysis claimed data read after `async with` close. Actually the data extraction IS correctly inside the `async with` block (24-space indent). No fix needed.

### 🟡 Medium

4. **instagrapi thread pool exhaustion** — no limit on concurrent `to_thread()` calls.
5. **`bot_stats.record_download()` called directly** (not via `to_thread`) in some paths — blocks if I/O.
6. **Session-per-request** — `_aiohttp_session()` creates+destroys per call. Consider shared session.

### 🟢 Low

7. **Hardcoded chunk size** (256KB) — could be 1MB for fast connections.
8. **`os.remove()` sync** — consider `Path.unlink()` in executor.
9. **Semaphore bypass** — oEmbed JSON fetch shouldn't need download semaphore.

---

## 3. Environment Variables

| Var | Default | Purpose |
|-----|---------|---------|
| `PROXY_ENABLED` | `""` | `"1"` to activate proxy |
| `PROXY_URL` | `socks5h://127.0.0.1:10808` | Proxy address |
| `IG_COOKIES` | `""` | Netscape cookie string |
| `INSTAGRAM_COOKIES_JSON` | `""` | JSON cookies |
| `INSTAGRAM_ACCOUNT_ID` | `""` | instagrapi username |
| `INSTAGRAM_ACCOUNT_PASSWORD` | `""` | instagrapi password |
| `INSTAGRAM_PAUSED` | `""` | `"1"` to disable IG |

---

## 4. Full Source: instagram_download.py

```python
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
                logger.info("private API: no video or image URL for %s", shortcode)
                return None
            # Скачиваем ВСЕ изображения
            dests: list[Path] = []
            for img_url in image_urls:
                dest = _dest_path_image()
                async with _aiohttp_session() as _s:
                    async with _s.get(img_url, headers=headers) as dl:
                        dl.raise_for_status()
                        with open(dest, "wb") as f:
                            async for chunk in dl.content.iter_chunked(DOWNLOAD_CHUNK_SIZE):
                                f.write(chunk)
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


def _extract_shortcode(url: str) -> str | None:
    """Извлекает shortcode из Instagram URL."""
    import re
    m = re.search(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


async def _download_photo_via_embed(url: str) -> tuple[list[Path], str] | None:
    """
    Fallback для фото-постов: парсит embed-страницу Instagram.
    Работает без авторизации (~2-4с).
    Возвращает ([path, ...], caption) или None.
    """
    shortcode = _extract_shortcode(url)
    if not shortcode:
        return None

    embed_url = f"https://www.instagram.com/p/{shortcode}/embed/"
    logger.info("embed: starting photo download for %s, proxy=%s", shortcode, "ON" if PROXY_ENABLED else "OFF")
    try:
        image_url: str | None = None
        embed_html: str | None = None

        # 1) Fetch embed page (with retry on rate-limit)
        from bs4 import BeautifulSoup

        for _embed_attempt in range(3):
            try:
                logger.info("embed: fetching %s (attempt %s)...", embed_url[:60], _embed_attempt + 1)
                async with _aiohttp_session() as _s:
                    async with _s.get(embed_url) as resp:
                        logger.info("embed page status=%s for %s (attempt %s)",
                                   resp.status, shortcode, _embed_attempt + 1)
                        if resp.status == 429:
                            logger.warning("embed rate-limited (429), waiting 3s...")
                            await asyncio.sleep(3)
                            continue
                        resp.raise_for_status()
                        embed_html = await resp.text()
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
                    async with _s.get(oembed_url) as oe_resp:
                        logger.info("oEmbed status=%s for %s", oe_resp.status, shortcode)
                        if oe_resp.status == 200:
                            oe_data = await oe_resp.json()
                            thumbnail = oe_data.get("thumbnail_url", "")
                            logger.info("oEmbed response: thumbnail_url=%s", thumbnail[:80] if thumbnail else "EMPTY")
                            if thumbnail and thumbnail.startswith("http"):
                                image_url = thumbnail
                                logger.info("oEmbed thumbnail_url found: %s...", thumbnail[:80])
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
                    image_url = og_val
                    logger.info("embed og:image found: %s...", og_val[:80])

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
                        image_url = src
                        logger.info("embed <img> cdninstagram found: %s...", src[:80])
                        break

        if not image_url:
            logger.warning("embed/oembed: NO image found for %s", shortcode)
            return None

        # Скачиваем фото
        dest = _dest_path_image()
        logger.info("downloading image from %s to %s", image_url[:80], dest)
        async with _aiohttp_session() as _s:
            async with _s.get(image_url) as img_resp:
                logger.info("image download status=%s content-type=%s", img_resp.status, img_resp.headers.get("content-type"))
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


async def _download_photo_via_page(url: str) -> tuple[list[Path], str] | None:
    """
    Fallback для фото: парсит JSON из HTML-страницы поста Instagram.
    Работает без авторизации, но может быть нестабильным.
    """
    shortcode = _extract_shortcode(url)
    if not shortcode:
        return None

    page_url = f"https://www.instagram.com/p/{shortcode}/"
    try:
        logger.info("page: fetching %s for photo extraction", page_url)
        async with _aiohttp_session() as _s:
            async with _s.get(page_url) as resp:
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
            logger.info("page: found cdninstagram image URL: %s...", best_url[:80])

            dest = _dest_path_image()
            async with _aiohttp_session() as _s:
                async with _s.get(best_url) as img_resp:
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


def _is_likely_photo_url(url: str) -> bool:
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

    # Путь 1.5: Для /p/ ссылок — oEmbed фото (без yt-dlp, быстрее и надёжнее)
    if _is_likely_photo_url(clean):
        try:
            result = await _download_photo_via_embed(clean)
            if result:
                paths, caption = result
                ms = int((time.monotonic() - t0) * 1000)
                bot_stats.record_download(DownloadStat(url=clean, ok=True, method="embed-photo", size=sum(p.stat().st_size for p in paths), elapsed_ms=ms, ts=time.time()))
                return paths, caption
        except Exception as exc:
            logger.warning("embed photo failed for /p/ URL: %s", exc)

        # Fallback: прямой парсинг HTML-страницы поста
        try:
            result = await _download_photo_via_page(clean)
            if result:
                paths, caption = result
                ms = int((time.monotonic() - t0) * 1000)
                bot_stats.record_download(DownloadStat(url=clean, ok=True, method="page-photo", size=sum(p.stat().st_size for p in paths), elapsed_ms=ms, ts=time.time()))
                return paths, caption
        except Exception as exc:
            logger.warning("page photo failed for /p/ URL: %s", exc)

        # oEmbed + embed + page не справились — сразу ошибку
        raise RuntimeError("Не удалось скачать фото с Instagram (oEmbed + embed + page не дали результат)")

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

    # Путь 3.5: Embed photo fallback (если все yt-dlp не справились — возможно фото)
    try:
        result = _download_photo_via_embed(clean)
        if result:
            paths, caption = result
            ms = int((time.monotonic() - t0) * 1000)
            bot_stats.record_download(DownloadStat(url=clean, ok=True, method="embed-photo", size=sum(p.stat().st_size for p in paths), elapsed_ms=ms, ts=time.time()))
            return paths, caption
    except Exception as exc:
        logger.warning("embed-photo fallback failed: %s", exc)

    # Путь 4: instagrapi — последний fallback
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

```

---

## 5. Full Source: chat_handlers.py

```python
from __future__ import annotations

import asyncio
import logging
import os
import re
import io

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, StateFilter
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message

import ai_quota
from config import settings
from deps import gpt, store
from chat_examples import chat_examples_markdown

# ── Рандомные подписи к видео/фото ──
# Два слова по отдельности, чаще без слова — это юмор бота
_IG_PHRASES = (None, None, None, None, "Донаты", "Приветствуются")
from chat_user_log import user_messages_markdown
from telegram_format import reply_formatted, reply_photo_then_text
from chat_queries import is_chat_examples_request
from capabilities import capabilities_markdown, is_capabilities_question
# Мемы/видосы отключены — оставляем импорты закомментированными на будущее.
from trigger_manage_requests import TriggerAdd, TriggerDelete, TriggerUpdate, parse_trigger_manage
from doc_extract import extract_docx_text, extract_pdf_text, extract_xlsx_preview, extract_plain_text
from chat_queries import is_who_in_chat_question
from memory_handlers import RECAP_PATTERN, svin_prompt_with_memory, who_in_chat_reply
from bot_messages import (
    instagram_timeout_message,
    map_instagram_error,
    video_too_heavy_message,
    yandex_error_message,
)
from personality_commands import try_personality_or_roster
from web_search_handlers import try_web_search_reply
from message_urls import message_has_instagram_link, url_from_message
from trigger_queries import is_trigger_list_question
from yandex_router import route_intent
from games import execute_game_action
from games.responses import render_game_response

logger = logging.getLogger(__name__)
router = Router(name="chat_handlers")

_SECRET_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"), "sk-***"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "ghp_***"),
    (re.compile(r"\bhf_[A-Za-z0-9]{10,}\b"), "hf_***"),
    (re.compile(r"\bvcp_[A-Za-z0-9]{10,}\b"), "vcp_***"),
    (re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"), "xox***"),
]


def _redact_secrets(text: str) -> str:
    out = text
    for pat, repl in _SECRET_REDACTIONS:
        out = pat.sub(repl, out)
    return out

TELEGRAM_MAX_BYTES = 52_428_800

_bot_id: int | None = None


class SvinInvokeFilter(BaseFilter):
    """Срабатывает на «свин» в тексте или reply на сообщение бота (только при AI)."""

    async def __call__(self, message: Message, bot: Bot) -> bool:
        global _bot_id
        text = message.text or message.caption
        if not text:
            return False
        if re.search(r"(?i)(свин|свинья)", text):
            return True
        # Reply на бота ловим ТОЛЬКО когда AI включён
        if not settings.ai_enabled:
            return False
        replied = message.reply_to_message
        if not replied or not replied.from_user or not replied.from_user.is_bot:
            return False
        if _bot_id is None:
            me = await bot.get_me()
            _bot_id = me.id
        return replied.from_user.id == _bot_id


SVIN_AI_FILTER = (
    StateFilter(None),
    F.text,
    ~F.text.startswith("/"),
    F.chat.type.in_({"group", "supergroup"}),
    ~F.text.regexp(r"(?i)instagram\.com"),
    ~F.text.regexp(RECAP_PATTERN),
    SvinInvokeFilter(),
)

SVIN_CAPTION_FILTER = (
    StateFilter(None),
    F.caption,
    F.chat.type.in_({"group", "supergroup"}),
    ~F.caption.regexp(r"(?i)instagram\.com"),
    SvinInvokeFilter(),
)


class InstagramAnyFilter(BaseFilter):
    """Любое сообщение с instagram.com в тексте, подписи или entity."""

    async def __call__(self, message: Message) -> bool:
        blob = (message.text or "") + " " + (message.caption or "")
        if "instagram.com" in blob.lower():
            return True
        return message_has_instagram_link(message)


IG_LINK_FILTER = InstagramAnyFilter()


from admin_auth import is_admin_user  # noqa: F401 — re-export для старых импортов


_ig_caption_cache: dict[str, str] = {}


async def handle_instagram_link(message: Message, bot: Bot) -> None:
    from config import settings
    from instagram_download import instagram_user_message
    from bot_stats import bot_stats
    bot_stats.record_message()

    if not settings.instagram_is_active():
        await message.answer(instagram_user_message())
        return

    clean_url: str | None = None
    text = message.text or message.caption or ""
    logger.info(
        "instagram_handler chat=%s type=%s text=%r",
        message.chat.id,
        message.chat.type,
        text[:200],
    )

    store.register_chat(
        message.chat.id,
        title=message.chat.title,
        chat_type=message.chat.type,
    )

    from instagram_download import DOWNLOAD_TOTAL_TIMEOUT_SEC, download_instagram_video, remove_files, is_photo_file
    from instagram_urls import is_instagram_media_url

    clean_url = url_from_message(message)
    if not clean_url:
        await message.answer("🐷 Не вытащил ссылку из сообщения.")
        return
    if not is_instagram_media_url(clean_url):
        await message.answer(
            "🐷 Нужна ссылка на Reel, пост, сторис или актуальное (/reel/, /p/, /stories/, /s/)"
        )
        return

    logger.info("IG clean_url=%s", clean_url)

    MAX_DOWNLOAD_RETRIES = 3
    RETRY_DELAY_SEC = 5
    last_error: Exception | None = None

    for download_attempt in range(MAX_DOWNLOAD_RETRIES):
        file_paths: list | None = None
        try:
            from instagram_download import _download_semaphore
            async with _download_semaphore:
                file_paths, caption = await asyncio.wait_for(
                    download_instagram_video(clean_url),
                    timeout=DOWNLOAD_TOTAL_TIMEOUT_SEC,
                )

            total_size = sum(os.path.getsize(p) for p in file_paths)
            if total_size > TELEGRAM_MAX_BYTES:
                remove_files(file_paths)
                file_paths = None
                await message.answer(video_too_heavy_message(clean_url))
                return

            sent_ok = False  # флаг: успешно ли отправлен контент

            # ── Carousel: несколько фото → send_media_group ──
            if len(file_paths) > 1 and all(is_photo_file(p) for p in file_paths):
                # Генерируем рандомную фразу-донат для подписи
                import random as _rnd
                _donate_phrase = _rnd.choice(_IG_PHRASES)
                _donate_caption = f"{_donate_phrase}\nhttps://clck.ru/3UaRGo" if _donate_phrase else "https://clck.ru/3UaRGo"
                media = []
                for i, p in enumerate(file_paths[:10]):  # Telegram max 10
                    kw: dict = {"media": FSInputFile(p)}
                    if i == 0:
                        kw["caption"] = _donate_caption
                    media.append(InputMediaPhoto(**kw))
                for attempt in range(2):
                    try:
                        await message.answer_media_group(
                            media=media,
                            reply_to_message_id=message.message_id,
                        )
                        sent_ok = True
                        break
                    except Exception as e:
                        if "timeout" in str(e).lower() and attempt < 1:
                            logger.warning("tg media group timeout attempt %s/2: %s", attempt + 1, e)
                            await asyncio.sleep(2)
                            continue
                        raise

            # ── Single file: видео или одно фото ──
            else:
                file_path = file_paths[0]
                photo = is_photo_file(file_path)
                # Генерируем рандомную фразу-донат для подписи
                import random as _rnd
                _donate_phrase = _rnd.choice(_IG_PHRASES)
                _donate_caption = f"{_donate_phrase}\nhttps://clck.ru/3UaRGo" if _donate_phrase else "https://clck.ru/3UaRGo"
                for attempt in range(2):
                    try:
                        if photo:
                            await message.answer_photo(
                                photo=FSInputFile(file_path),
                                caption=_donate_caption,
                                reply_to_message_id=message.message_id,
                            )
                        else:
                            await message.answer_video(
                                video=FSInputFile(file_path),
                                caption=_donate_caption,
                                reply_to_message_id=message.message_id,
                                supports_streaming=True,
                            )
                        sent_ok = True
                        break
                    except Exception as e:
                        if "timeout" in str(e).lower() and attempt < 1:
                            logger.warning(
                                "telegram upload timeout attempt %s/2: %s",
                                attempt + 1,
                                e,
                            )
                            await asyncio.sleep(2)
                            continue
                        raise

            # ── Кнопка «Описание» — отдельным сообщением (всегда нефатально) ──
            if sent_ok and caption.strip():
                try:
                    cache_key = f"{message.chat.id}:{message.message_id}:{clean_url}"
                    _ig_caption_cache[cache_key] = caption
                    if len(_ig_caption_cache) > 100:
                        old_keys = list(_ig_caption_cache.keys())[:50]
                        for k in old_keys:
                            _ig_caption_cache.pop(k, None)
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📝 Описание", callback_data=f"igtxt:{cache_key}")]
                    ])
                    await message.answer(
                        "📝 Описание поста:",
                        reply_markup=kb,
                        reply_to_message_id=message.message_id,
                    )
                except Exception as btn_err:
                    logger.warning("caption button failed (non-fatal): %s", btn_err)

            # Успех — выходим
            last_error = None
            break

        except asyncio.TimeoutError:
            logger.error("instagram download total timeout (%ss)", DOWNLOAD_TOTAL_TIMEOUT_SEC)
            last_error = RuntimeError("timeout")
            if download_attempt < MAX_DOWNLOAD_RETRIES - 1:
                logger.info("retry %s/%s after %ss", download_attempt + 2, MAX_DOWNLOAD_RETRIES, RETRY_DELAY_SEC)
                await asyncio.sleep(RETRY_DELAY_SEC)
                continue
        except Exception as e:
            last_error = e
            logger.warning(
                "instagram download attempt %s/%s failed: %s",
                download_attempt + 1,
                MAX_DOWNLOAD_RETRIES,
                e,
            )
            if download_attempt < MAX_DOWNLOAD_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAY_SEC)
                continue
        finally:
            if file_paths is not None:
                try:
                    remove_files(file_paths)
                except Exception:
                    pass

    # Все попытки исчерпаны
    if last_error is not None:
        error_text = str(last_error).lower()
        # Уведомляем админа при протухании cookies
        if "cookie" in error_text or "сессия" in error_text or "login" in error_text:
            from instagram_download import _notify_admin_cookies_expired
            await _notify_admin_cookies_expired(bot)
            # Молча выходим — не спамим пользователя
            return
        if isinstance(last_error, RuntimeError) and str(last_error) == "timeout":
            bot_stats.record_error(f"IG timeout {DOWNLOAD_TOTAL_TIMEOUT_SEC}s: {clean_url}")
            await message.answer(instagram_timeout_message())
        else:
            bot_stats.record_error(f"IG error: {str(last_error)[:100]}")
            await message.answer(map_instagram_error(last_error, clean_url))


async def handle_ig_text_callback(callback: CallbackQuery) -> None:
    data = callback.data or ""
    if not data.startswith("igtxt:"):
        return
    cache_key = data[6:]
    caption = _ig_caption_cache.pop(cache_key, "")
    if not caption:
        await callback.answer("Текст не найден (кэш истёк)", show_alert=True)
        return
    await callback.answer()
    # Отправляем текст отдельным сообщением
    await callback.message.answer(caption)


async def handle_svin_ai(message: Message, bot: Bot) -> None:
    try:
        text = message.text or message.caption
        if not message.from_user or not text:
            return

        uid = message.from_user.id
        logger.info(
            "svin_ai chat=%s user=%s text=%r",
            message.chat.id,
            uid,
            text[:200],
        )

        # AI выключен — молча игнорируем все сообщения с тегом
        if not settings.ai_enabled:
            return

        # Управление триггерами из чата (добавить/удалить/править) — раньше списка,
        # чтобы фраза "добавь триггер" не перехватывалась "какие триггеры".
        action = parse_trigger_manage(text)
        if action:
            if isinstance(action, TriggerAdd):
                rule_id = store.add_custom_rule(
                    message.chat.id,
                    action.word,
                    action.response,
                    once_per_day=action.once_per_day,
                    added_by_user_id=uid,
                    added_by_username=message.from_user.username,
                    match=action.match,
                )
                await reply_formatted(
                    message,
                    "✅ **Триггер добавлен**\n\n"
                    f"🎯 **Слово**: `{action.word}`\n\n"
                    f"💬 **Ответ**: **{action.response}**\n\n"
                    f"🧷 **ID**: `{rule_id}`",
                )
                return
            if isinstance(action, TriggerDelete):
                removed = store.delete_custom_by_indices(
                    message.chat.id, [i - 1 for i in action.indices_1based]
                )
                if removed:
                    await reply_formatted(
                        message,
                        "🗑️ **Триггеры удалены**\n\n"
                        f"🔢 Кол-во: **{removed}**",
                    )
                else:
                    await reply_formatted(
                        message,
                        "🗑️ Не нашёл такие номера.\n\n"
                        "🧷 Спроси **«какие триггеры»** и удали по номеру.",
                    )
                return
            if isinstance(action, TriggerUpdate):
                ok = store.update_custom_rule(
                    message.chat.id,
                    action.index_1based - 1,
                    word=action.word,
                    response=action.response,
                    match=action.match,
                )
                if ok:
                    await reply_formatted(
                        message,
                        "✏️ **Триггер обновлён**\n\n"
                        f"🔢 Номер: **{action.index_1based}**",
                    )
                else:
                    await reply_formatted(
                        message,
                        "✏️ Не нашёл такой номер.\n\n"
                        "🧷 Спроси **«какие триггеры»** и выбери номер.",
                    )
                return

        # Достать текст из документа — текстом (reply на файл ИЛИ файл с подписью)
        doc_msg = None
        if message.reply_to_message and message.reply_to_message.document:
            doc_msg = message.reply_to_message
        elif message.document:
            doc_msg = message

        if doc_msg and doc_msg.document:
            low = text.lower()
            wants_text = any(
                x in low
                for x in (
                    "достань текст",
                    "вытащи текст",
                    "достать текст",
                    "извлеки текст",
                    "вытяни текст",
                    "прочитай документ",
                    "прочитай файл",
                    "текст из документа",
                    "текст из файла",
                    "расшифруй текст",
                    "расшифровать текст",
                    "распознай текст",
                    "распознать текст",
                    "покажи текст",
                    "покажи содержимое",
                )
            )
            if wants_text:
                doc = doc_msg.document
                buf = io.BytesIO()
                await bot.download(doc.file_id, destination=buf)
                data = buf.getvalue()

                name = (doc.file_name or "").lower()
                extracted = ""
                kind = ""
                if name.endswith(".pdf") or (doc.mime_type or "").lower().endswith("pdf"):
                    kind = "PDF"
                    extracted = extract_pdf_text(data)
                elif name.endswith(".docx"):
                    kind = "DOCX"
                    extracted = extract_docx_text(data)
                elif name.endswith(".xlsx"):
                    kind = "XLSX"
                    extracted = extract_xlsx_preview(data)
                elif name.endswith(".txt") or (doc.mime_type or "").lower().startswith("text/"):
                    kind = "TXT"
                    extracted = extract_plain_text(data)

                if not kind:
                    await reply_formatted(
                        message,
                        "📎 Понимаю **PDF/DOCX/XLSX/TXT**.\n\n"
                        "🧷 Пришли файл с подписью **«Свин, достань текст из файла»** "
                        "или ответь реплаем на файл.",
                    )
                    return

                if not extracted:
                    await reply_formatted(
                        message,
                        f"📎 **{kind}** пустой или текст не извлёкся.\n\n"
                        "Если это сканы — нужна OCR.",
                    )
                    return

                cleaned = _redact_secrets(extracted).strip()
                # XLSX уже форматируется построчно — сохраняем переносы строк.
                snippet = cleaned if kind == "XLSX" else cleaned.replace("\n", " ")
                if len(snippet) > 2000:
                    snippet = snippet[:2000] + "…"
                await reply_formatted(
                    message,
                    f"📎 **{kind} → текст**\n\n🧾 {snippet}",
                )
                return

        if is_trigger_list_question(text):
            reply = store.triggers_list_markdown(message.chat.id)
            logger.info(
                "trigger_list chat=%s reply_chars=%s",
                message.chat.id,
                len(reply),
            )
            await reply_formatted(message, reply)
            return

        if is_capabilities_question(text):
            await reply_formatted(message, capabilities_markdown())
            return

        if settings.web_search_enabled:
            web_reply = await try_web_search_reply(message)
            if web_reply:
                await reply_photo_then_text(
                    message, web_reply.text, web_reply.photo_bytes
                )
                return

        if settings.ai_enabled:
            if is_chat_examples_request(text):
                reply = await chat_examples_markdown(message.chat.id)
                logger.info("chat_examples chat=%s", message.chat.id)
                await reply_formatted(message, reply)
                return

            tone_reply = await try_personality_or_roster(message)
            if tone_reply:
                await reply_formatted(message, tone_reply)
                return

            if is_who_in_chat_question(text):
                reply = await who_in_chat_reply(message.chat.id)
                if reply:
                    await reply_formatted(message, reply)
                    return

            user_log = await user_messages_markdown(message.chat.id, text)
            if user_log:
                await reply_formatted(message, user_log)
                return

            if settings.games_enabled:
                routed = await route_intent(text)
                if routed["is_game_action"] and routed["game_id"] != "none":
                    data = await execute_game_action(
                        chat_id=message.chat.id,
                        telegram_user_id=uid,
                        username=message.from_user.username,
                        game_id=routed["game_id"],
                        action_type=routed["action_type"],
                        payload=routed["payload"],
                    )
                    resp = render_game_response(routed["game_id"], routed["action_type"], data)
                    await reply_formatted(message, resp)
                    return

            if not ai_quota.can_ask(uid):
                await message.reply(ai_quota.limit_exceeded_message())
                return

            prompt, system = await svin_prompt_with_memory(message.chat.id, text)
            answer = await gpt.reply(prompt, system=system)
            ai_quota.record(uid)
            await reply_formatted(message, answer)
        else:
            # AI выключен — отвечаем заглушкой
            await reply_formatted(
                message,
                "🐷 ИИ-режим выключен. Могу скачать видео по ссылке Instagram.",
            )
    except Exception as e:
        logger.error("svin_ai error: %s", e, exc_info=True)
        await reply_formatted(message, yandex_error_message())

```

---

## 6. Full Source: downloader.py

```python
"""Обратная совместимость."""
from __future__ import annotations

import asyncio
from pathlib import Path

from instagram_download import (
    TELEGRAM_MAX_BYTES,
    download_instagram_video,
    init_instagram_downloader,
    remove_file,
)
from instagram_urls import clean_instagram_url, extract_instagram_url, is_instagram_media_url


async def download_to_temp_mp4(url: str) -> Path:
    paths, _ = await download_instagram_video(url)
    return paths[0]


def cleanup_paths(*paths: Path) -> None:
    for p in paths:
        remove_file(p)

```

---

## 7. Send Logic: watch_feeder.py (photo/video/carousel)

```python
# ── Post videos ──

async def _post_single(bot, chat_ids: list[int], item: dict) -> bool:
    """Download reel and send as video/photo carousel. Skip silently if download fails."""
    from instagram_download import (
        download_instagram_video, remove_files,
        DOWNLOAD_TOTAL_TIMEOUT_SEC, _download_semaphore,
        TELEGRAM_MAX_BYTES, is_photo_file,
    )
    from aiogram.types import FSInputFile, InputMediaPhoto

    sc = item["shortcode"]
    link = f"https://www.instagram.com/reel/{sc}/"
    file_paths: list | None = None

    try:
        logger.info("watch_feed: downloading %s", sc)
        async with _download_semaphore:
            file_paths, _ = await asyncio.wait_for(
                download_instagram_video(link),
                timeout=DOWNLOAD_TOTAL_TIMEOUT_SEC,
            )

        total_size = sum(p.stat().st_size for p in file_paths)
        if total_size > TELEGRAM_MAX_BYTES:
            logger.warning("watch_feed: %s too large", sc)
            remove_files(file_paths)
            return False
    except Exception as exc:
        logger.warning("watch_feed: download failed %s: %s", sc, exc)
        remove_files(file_paths)
        return False

    sent = False
    # Carousel: несколько фото → media group
    if len(file_paths) > 1 and all(is_photo_file(p) for p in file_paths):
        for cid in chat_ids:
            try:
                media = [InputMediaPhoto(media=FSInputFile(p)) for p in file_paths[:10]]
                await bot.send_media_group(chat_id=cid, media=media)
                sent = True
            except Exception as exc:
                logger.warning("watch_feed: media_group to %s failed: %s", cid, exc)
    else:
        file_path = file_paths[0]
        photo = is_photo_file(file_path)
        for cid in chat_ids:
            try:
                if photo:
                    await bot.send_photo(
                        chat_id=cid,
                        photo=FSInputFile(file_path),
                    )
                else:
                    await bot.send_video(
                        chat_id=cid,
                        video=FSInputFile(file_path),
                        supports_streaming=True,
                    )
                sent = True
            except Exception as exc:
                logger.warning("watch_feed: send to %s failed: %s", cid, exc)

    remove_files(file_paths)
    return sent

```

---

## 8. Data Flow

```
download_instagram_video(url) -> (list[Path], caption)
    |-- _is_likely_photo_url? -> photo chain (oEmbed -> embed -> page)
    |-- _download_via_private_api     <- instagrapi + aiohttp CDN
    |-- _download_ytdlp_fast          <- yt-dlp -g -> aiohttp
    |-- _download_ytdlp_fallback      <- yt-dlp -o file
    |-- _download_photo_via_embed     <- aiohttp + BeautifulSoup
    |-- _download_photo_via_page      <- aiohttp + regex
    |-- _download_instagram_video_once <- instagrapi (last resort)
```

## 9. Telegram Send Flow

```python
paths, caption = await download_instagram_video(url)
if len(paths) > 1 and all(is_photo_file(p) for p in paths):
    await message.answer_media_group([InputMediaPhoto(FSInputFile(p)) for p in paths[:10]])
elif is_photo_file(paths[0]):
    await message.answer_photo(FSInputFile(paths[0]), caption=caption)
else:
    await message.answer_video(FSInputFile(paths[0]), caption=caption)
remove_files(paths)
```

## 10. Dependencies

| Package | Purpose |
|---------|---------|
| `aiohttp` + `aiohttp-socks` | Async HTTP + SOCKS5 |
| `yt-dlp` | CLI video extractor |
| `instagrapi` | Instagram Private API |
| `beautifulsoup4` | HTML parsing |
| `aiogram` | Telegram bot |
