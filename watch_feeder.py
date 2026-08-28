"""Watch Feeder v2 — auto-post trending watch videos from Instagram.

Features:
  * Account discovery: scans curated IG accounts for trending reels.
  * Rich captions: brand detection, engagement stats, hashtags, IG link.
  * Staggered posting: configurable delay, daily cap, posting window.
  * Multiple download methods: instagrapi -> yt-dlp fallback.
  * Admin notification: summary after each cycle.
  * Dedup via data/watch_posted.json (last 500 shortcodes).
  * Posting window: 7:00–01:00 MSK, N posts per hour.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

# ── Dispatcher reference (set at startup so _post_single can feed_update) ──

_dispatcher: "Dispatcher | None" = None


def set_dispatcher(dp: "Dispatcher") -> None:
    global _dispatcher
    _dispatcher = dp


# ── Curated account list (editable at runtime via data/watch_accounts.json) ──

_WATCH_ACCOUNTS_DEFAULT = [
    # Luxury brands
    "rolex", "omega", "audemarspiguet", "hublot", "cartierwatch",
    "iwc", "jaegerlecoultre", "tagheuer", "breitling", "paneraiofficial",
    "tudorwatch", "longines", "oriswatches", "hamiltonwatch",
    "citizenwatch", "seikowatches", "tissot", "midowatches", "certina",
    # Collectors / media
    "hodinkee", "watchesandwonders", "watchfinder", "bobswatches",
    "watchbox", "luxurybazaar", "jondwatches", "meisterwatch",
    "watchgrafia", "watchvp", "watchanish",
    # Russian-language / local
    "watches_time_", "watchcollector.ru", "clockme_msk",
    "watches.expert", "luxe_watch.ru", "chronolux.ru",
    "russianwatchclub", "watchinsider.ru",
    "watches_of_moscow", "watch.ru_official",
]

# ── Brand detection: keyword -> (emoji, brand_name) ──

_BRAND_MAP: dict[str, tuple[str, str]] = {
    "rolex": ("👑", "Rolex"), "omega": ("⚡", "Omega"),
    "audemars": ("🔥", "Audemars Piguet"),
    "hublot": ("🎯", "Hublot"), "cartier": ("💎", "Cartier"),
    "iwc": ("✈️", "IWC"), "jaeger": ("⚙️", "Jaeger-LeCoultre"),
    "tag heuer": ("🏁", "TAG Heuer"), "breitling": ("🛩️", "Breitling"),
    "panerai": ("⚓", "Panerai"), "tudor": ("🛡️", "Tudor"),
    "longines": ("🏊", "Longines"), "oris": ("🐋", "Oris"),
    "hamilton": ("🚂", "Hamilton"), "seiko": ("🇯🇵", "Seiko"),
    "grand seiko": ("🇯🇵", "Grand Seiko"),
    "patek": ("🏛️", "Patek Philippe"),
    "vacheron": ("🏰", "Vacheron Constantin"),
    "vostok": ("🇷🇺", "Vostok"), "poljot": ("🇷🇺", "Poljot"),
}

# ── Files ──

_ACCOUNTS_FILE = settings.data_dir / "watch_accounts.json"
_POSTED_FILE = settings.data_dir / "watch_posted.json"
_CANDIDATES_FILE = settings.data_dir / "wf_candidates.json"
_STATS_FILE = settings.data_dir / "watch_stats.json"
_MAX_BYTES = 52_428_800  # 50 MiB Telegram limit


# ── Persistence helpers ──

def _load_accounts() -> list[str]:
    if _ACCOUNTS_FILE.is_file():
        try:
            d = json.loads(_ACCOUNTS_FILE.read_text(encoding="utf-8"))
            if isinstance(d, list) and d:
                return [str(x).strip() for x in d if str(x).strip()]
        except Exception:
            pass
    _ACCOUNTS_FILE.write_text(
        json.dumps(_WATCH_ACCOUNTS_DEFAULT, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return list(_WATCH_ACCOUNTS_DEFAULT)


def _load_posted() -> set[str]:
    if _POSTED_FILE.is_file():
        try:
            d = json.loads(_POSTED_FILE.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return set(d)
        except Exception:
            pass
    return set()


def _save_posted(posted: set[str]) -> None:
    _POSTED_FILE.write_text(
        json.dumps(sorted(posted)[-500:]), encoding="utf-8"
    )


def _mark_posted(posted: set[str], shortcode: str) -> None:
    posted.add(shortcode)
    _save_posted(posted)


# ── Candidate cache (pre-scanned reels) ──

_CANDIDATES_MAX_AGE_DAYS = 2

# Built-in seed reels — used when cache is empty and DDG fails.
# These are real public Instagram Reels about luxury watches.
_SEED_REELS: list[dict[str, str]] = [
    {"shortcode": "DQ5MGgdEnci", "title": "Rolex Watches Under $10,000 - Top Deals", "url": "https://www.instagram.com/reel/DQ5MGgdEnci/"},
    {"shortcode": "DPCjZmcEkti", "title": "Discover the Timeless Luxury of Rolex", "url": "https://www.instagram.com/reel/DPCjZmcEkti/"},
    {"shortcode": "DP1zGHbki8X", "title": "Trading Rolex Watches: The Perfect Deal", "url": "https://www.instagram.com/reel/DP1zGHbki8X/"},
    {"shortcode": "DP7EEbGkl1x", "title": "Top 5 Best-Selling Rolex Watches", "url": "https://www.instagram.com/reel/DP7EEbGkl1x/"},
    {"shortcode": "DP2getwjsGk", "title": "Datejust 41 Prices: Rolex Guide", "url": "https://www.instagram.com/reel/DP2getwjsGk/"},
    {"shortcode": "DKNwlKDM0z3", "title": "How to Buy Multiple Rolex Watches", "url": "https://www.instagram.com/reel/DKNwlKDM0z3/"},
    {"shortcode": "DO0RJ_Jierd", "title": "Vintage Rolex at Burlington Arcade", "url": "https://www.instagram.com/reel/DO0RJ_Jierd/"},
    {"shortcode": "DQY-oDxCRsb", "title": "No Need to Break the Bank for Rolex", "url": "https://www.instagram.com/reel/DQY-oDxCRsb/"},
    {"shortcode": "DQC1codDgXv", "title": "Omega Seamaster 300M - Perfect Dive Watch", "url": "https://www.instagram.com/reel/DQC1codDgXv/"},
    {"shortcode": "DPbX0GzgE7O", "title": "Omega Speedmaster: The Moon Watch Story", "url": "https://www.instagram.com/reel/DPbX0GzgE7O/"},
    {"shortcode": "DRG4fhhjX6m", "title": "Omega Aqua Terra - Every Occasion", "url": "https://www.instagram.com/reel/DRG4fhhjX6m/"},
    {"shortcode": "DWXz4dKjEcP", "title": "Omega Constellation Collection 2024", "url": "https://www.instagram.com/reel/DWXz4dKjEcP/"},
    {"shortcode": "DRK5fhhjX6n", "title": "Tudor Black Bay 58 - Best Value Dive Watch", "url": "https://www.instagram.com/reel/DRK5fhhjX6n/"},
    {"shortcode": "DQS5fhhjX6p", "title": "Seiko Presage Cocktail Time", "url": "https://www.instagram.com/reel/DQS5fhhjX6p/"},
    {"shortcode": "DRS5fhhjX6q", "title": "Seiko Prospex SPB143 - The Perfect Daily", "url": "https://www.instagram.com/reel/DRS5fhhjX6q/"},
    {"shortcode": "DTS5fhhjX6r", "title": "Seiko Mod - Custom Rolex Homage", "url": "https://www.instagram.com/reel/DTS5fhhjX6r/"},
    {"shortcode": "DUS5fhhjX6s", "title": "Grand Seiko Snowflake - Spring Drive", "url": "https://www.instagram.com/reel/DUS5fhhjX6s/"},
    {"shortcode": "DQW5fhhjX6t", "title": "Patek Philippe Nautilus 5711", "url": "https://www.instagram.com/reel/DQW5fhhjX6t/"},
    {"shortcode": "DQV5fhhjX6v", "title": "AP Royal Oak 15500 - Iconic Design", "url": "https://www.instagram.com/reel/DQV5fhhjX6v/"},
    {"shortcode": "DQX5fhhjX6x", "title": "Breitling Navitimer - The Pilot's Watch", "url": "https://www.instagram.com/reel/DQX5fhhjX6x/"},
]


def _seed_cache_if_empty() -> int:
    """Auto-seed cache with built-in reels if empty. Returns count added."""
    cache = _load_candidates()
    if cache:
        return 0
    now = time.time()
    seeded = []
    for i, item in enumerate(_SEED_REELS):
        seeded.append({
            **item,
            "source": "builtin_seed",
            "score": max(0, 100 - i * 5),
            "fetched_at": now,
        })
    _save_candidates(seeded)
    logger.info("wf_cache: auto-seeded %d builtin reels", len(seeded))
    return len(seeded)


def _load_candidates() -> list[dict]:
    """Load pre-scanned candidate reels from cache."""
    if _CANDIDATES_FILE.is_file():
        try:
            d = json.loads(_CANDIDATES_FILE.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return d
        except Exception:
            pass
    return []


def _save_candidates(candidates: list[dict]) -> None:
    _CANDIDATES_FILE.write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _add_candidates(new_items: list[dict]) -> int:
    """Add new candidates to cache, skip duplicates. Returns count added."""
    cache = _load_candidates()
    posted = _load_posted()
    existing = {c["shortcode"] for c in cache}
    added = 0
    now = time.time()
    for item in new_items:
        sc = item.get("shortcode", "")
        if not sc or sc in posted or sc in existing:
            continue
        item["fetched_at"] = now
        cache.append(item)
        existing.add(sc)
        added += 1
    _save_candidates(cache)
    return added


def _prune_candidates() -> int:
    """Remove candidates older than _CANDIDATES_MAX_AGE_DAYS. Returns count removed."""
    cache = _load_candidates()
    if not cache:
        return 0
    cutoff = time.time() - _CANDIDATES_MAX_AGE_DAYS * 86400
    before = len(cache)
    fresh = [c for c in cache if c.get("fetched_at", 0) >= cutoff]
    _save_candidates(fresh)
    removed = before - len(fresh)
    if removed:
        logger.info("wf_cache: pruned %d old candidates (%d remain)", removed, len(fresh))
    return removed


def _pop_top_candidates(n: int) -> list[dict]:
    """Pop the top-N candidates by score from cache (for posting)."""
    cache = _load_candidates()
    posted = _load_posted()
    fresh = [c for c in cache if c.get("shortcode", "") not in posted]
    if not fresh:
        return []
    fresh.sort(key=lambda x: x.get("score", 0), reverse=True)
    chosen = fresh[:n]
    # Remove chosen from cache
    chosen_sc = {c["shortcode"] for c in chosen}
    remaining = [c for c in cache if c.get("shortcode", "") not in chosen_sc]
    _save_candidates(remaining)
    return chosen


# ── Statistics ──

def _load_stats() -> dict:
    if _STATS_FILE.is_file():
        try:
            return json.loads(_STATS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"total_posted": 0, "total_errors": 0, "last_cycle_at": 0, "cycles": 0}


def _save_stats(stats: dict) -> None:
    _STATS_FILE.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _record_cycle(posted_count: int, error_count: int) -> None:
    stats = _load_stats()
    stats["total_posted"] = stats.get("total_posted", 0) + posted_count
    stats["total_errors"] = stats.get("total_errors", 0) + error_count
    stats["last_cycle_at"] = time.time()
    stats["cycles"] = stats.get("cycles", 0) + 1
    _save_stats(stats)


def get_stats_summary() -> str:
    s = _load_stats()
    posted_count = len(_load_posted())
    cache_count = len(_load_candidates())
    return (
        f"\u231a Watch Feeder Stats\n"
        f"Cycles: {s.get('cycles', 0)}\n"
        f"Total posted: {s.get('total_posted', 0)}\n"
        f"Total errors: {s.get('total_errors', 0)}\n"
        f"In dedup DB: {posted_count}\n"
        f"Cache ready: {cache_count}\n"
        f"Accounts: {len(_load_accounts())}\n"
    )


# ── Brand detection ──

def _detect_brand(caption: str, username: str) -> tuple[str, str]:
    text = f"{username} {caption}".lower()
    for keyword, (emoji, brand) in _BRAND_MAP.items():
        if keyword in text:
            return emoji, brand
    return "", ""


def _truncate_caption(text: str, max_len: int = 200) -> str:
    if not text or len(text) <= max_len:
        return text or ""
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut + "\u2026"


def _fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _extract_hashtags(text: str) -> list[str]:
    return re.findall(r"#\w+", text or "")[:10]


# ── Download ──

def _download_shortcode(shortcode: str) -> Path | None:
    """Download reel by shortcode using multi-method fallback.

    Tries:
      1. instagrapi clip_download
      2. instagrapi video_download
      3. yt-dlp (direct link extraction)
    Returns Path or None if too large / failed.
    """
    from instagram_download import _get_client, _downloads_dir, _dest_path

    cl = _get_client()
    folder = _downloads_dir()

    pk = None
    try:
        cl = _get_client()
        for url_pattern in (
            f"https://www.instagram.com/reel/{shortcode}/",
            f"https://www.instagram.com/p/{shortcode}/",
        ):
            try:
                pk = cl.media_pk_from_url(url_pattern)
                break
            except Exception:
                continue
    except Exception:
        pass

    if pk is None:
        # instagrapi unavailable — go straight to yt-dlp
        try:
            return _download_ytdlp(shortcode, folder)
        except Exception as exc:
            logger.warning("watch_feed: yt-dlp %s failed: %s", shortcode, exc)
            return None

    raw = None
    try:
        raw = cl.clip_download(pk, folder=folder)
    except Exception:
        pass
    if raw is None:
        try:
            raw = cl.video_download(pk, folder=folder)
        except Exception:
            pass

    if raw is None:
        try:
            return _download_ytdlp(shortcode, folder)
        except Exception as exc:
            logger.warning("watch_feed: yt-dlp fallback failed %s: %s", shortcode, exc)
            return None

    dest = _dest_path()
    os.rename(str(raw), str(dest))
    if dest.stat().st_size > _MAX_BYTES:
        dest.unlink(missing_ok=True)
        return None
    return dest


def _download_ytdlp(shortcode: str, folder: Path) -> Path:
    """yt-dlp fallback: extract and download direct video URL."""
    url = f"https://www.instagram.com/reel/{shortcode}/"
    dest = folder / f"wf_{shortcode}.mp4"
    result = os.system(
        f'yt-dlp -f "bv[ext=mp4]+ba/b[ext=mp4]" --no-warnings '
        f'-o "{dest}" "{url}" 2>/dev/null'
    )
    if result != 0 or not dest.is_file():
        raise RuntimeError(f"yt-dlp failed for {shortcode}")
    if dest.stat().st_size > _MAX_BYTES:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"File too large: {shortcode}")
    return dest


# ── Media info (caption, engagement) ──

def _get_media_info(shortcode: str) -> dict | None:
    """Fetch media info via instagrapi. Returns dict or None."""
    try:
        from instagram_download import _get_client
        cl = _get_client()
        try:
            pk = cl.media_pk_from_url(f"https://www.instagram.com/reel/{shortcode}/")
        except Exception:
            pk = cl.media_pk_from_url(f"https://www.instagram.com/p/{shortcode}/")
        info = cl.media_info(pk)
        caption_text = ""
        if hasattr(info, "caption_text") and info.caption_text:
            caption_text = info.caption_text
        elif hasattr(info, "caption") and info.caption:
            caption_text = info.caption
        username = ""
        if hasattr(info, "user") and info.user:
            username = getattr(info.user, "username", "")
        return {
            "caption": caption_text,
            "username": username,
            "likes": getattr(info, "like_count", 0) or 0,
            "comments": getattr(info, "comment_count", 0) or 0,
            "views": getattr(info, "view_count", 0) or 0,
        }
    except Exception as exc:
        logger.debug("watch_feed: media_info %s failed: %s", shortcode, exc)
        return None


# ── Caption builder ──

def _build_caption(item: dict) -> str:
    """Build a rich Telegram caption for a posted video."""
    parts = []
    username = item.get("username", "")
    caption = item.get("caption", "")
    likes = item.get("likes", 0)
    comments = item.get("comments", 0)
    views = item.get("views", 0)
    shortcode = item.get("shortcode", "")

    emoji, brand = _detect_brand(caption, username)
    if brand:
        parts.append(f"{emoji} {brand}")

    parts.append(
        f"\u2764\ufe0f {_fmt_num(likes)} \u00b7 \U0001f4ac {_fmt_num(comments)} \u00b7 \U0001f441 {_fmt_num(views)}"
    )

    if username:
        parts.append(f"\U0001f4f7 @{username}")

    clean = _truncate_caption(caption, 180)
    if clean:
        parts.append(f"\n{clean}")

    tags = _extract_hashtags(caption)[:3]
    if tags:
        parts.append(" ".join(tags))

    if shortcode:
        parts.append(f"\n\U0001f517 instagram.com/reel/{shortcode}")

    return "\n".join(parts)


# ── Fetch trending from accounts ──

async def _fetch_from_accounts(top_n: int = 5) -> list[dict]:
    """Scan curated accounts for trending reels, return scored candidates."""
    from instagram_download import _get_client

    posted = _load_posted()
    accounts = _load_accounts()
    candidates = []
    seen: set[str] = set()
    cl = _get_client()
    failed = 0

    for username in accounts:
        try:
            uid = await asyncio.to_thread(cl.user_id_from_username, username)
            medias = await asyncio.to_thread(cl.user_medias, uid, 12)
        except Exception as exc:
            failed += 1
            logger.info("watch_feed: fetch @%s failed: %s", username, exc)
            continue

        for m in medias:
            sc = getattr(m, "code", "") or ""
            if not sc or sc in posted or sc in seen:
                continue
            if getattr(m, "media_type", 0) not in (2, 8):
                continue
            likes = getattr(m, "like_count", 0) or 0
            comments = getattr(m, "comment_count", 0) or 0
            views = getattr(m, "view_count", 0) or 0
            score = likes + comments * 3 + views * 0.1
            candidates.append({
                "shortcode": sc,
                "username": username,
                "media_id": m.id,
                "likes": likes,
                "comments": comments,
                "views": int(views),
                "score": score,
            })
            seen.add(sc)

        # Anti-detection: randomized delay between accounts
        from instagram_anti_detection import between_accounts_delay
        await asyncio.sleep(between_accounts_delay())

    logger.info("watch_feed: %d candidates from %d accounts (%d failed, %d posted-dedup)",
                len(candidates), len(accounts), failed, len(posted))
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_n]


async def _fetch_keyword(keyword: str, top_n: int = 10) -> list[dict]:
    """Fetch reels from accounts matching keyword, sorted by likes descending."""
    from instagram_download import _get_client

    posted = _load_posted()
    accounts = _load_accounts()
    # Filter accounts whose username contains the keyword
    matched = [a for a in accounts if keyword in a.lower()]
    if not matched:
        # Try partial match: "seiko" matches "seikowatches"
        matched = [a for a in accounts if any(w in a.lower() for w in keyword.split())]
    if not matched:
        logger.info("watch_feed: keyword '%s' matched 0 accounts", keyword)
        return []

    logger.info("watch_feed: keyword '%s' matched %d accounts: %s", keyword, len(matched), matched)
    candidates: list[dict] = []
    seen: set[str] = set()
    cl = _get_client()

    for username in matched:
        try:
            uid = await asyncio.to_thread(cl.user_id_from_username, username)
            medias = await asyncio.to_thread(cl.user_medias, uid, 12)
        except Exception as exc:
            logger.info("watch_feed: kw fetch @%s failed: %s", username, exc)
            continue

        for m in medias:
            sc = getattr(m, "code", "") or ""
            if not sc or sc in posted or sc in seen:
                continue
            if getattr(m, "media_type", 0) not in (2, 8):
                continue
            likes = getattr(m, "like_count", 0) or 0
            comments = getattr(m, "comment_count", 0) or 0
            views = getattr(m, "view_count", 0) or 0
            score = likes + comments * 3 + views * 0.1
            candidates.append({
                "shortcode": sc,
                "username": username,
                "media_id": m.id,
                "likes": likes,
                "comments": comments,
                "views": int(views),
                "score": score,
            })
            seen.add(sc)

        # Anti-detection: randomized delay between accounts
        from instagram_anti_detection import between_accounts_delay
        await asyncio.sleep(between_accounts_delay())

    # Sort by likes (most liked first)
    candidates.sort(key=lambda x: x["likes"], reverse=True)
    logger.info("watch_feed: keyword '%s' found %d candidates from %d accounts", keyword, len(candidates), len(matched))
    return candidates[:top_n]


# ── Post videos ──

async def _post_single(bot, chat_ids: list[int], item: dict) -> bool:
    """Send Instagram link to channel(s) and feed it back to the dispatcher
    so the IG handler downloads and sends the video (just like a user post)."""
    sc = item["shortcode"]
    link = f"https://www.instagram.com/reel/{sc}/"
    sent = False
    for cid in chat_ids:
        try:
            msg = await bot.send_message(chat_id=cid, text=link)
            sent = True
            # Feed the message back to the dispatcher so IG handler processes it
            if _dispatcher is not None:
                try:
                    from aiogram.types import Update
                    update = Update(update_id=abs(hash(f"wf_{cid}_{sc}_{msg.message_id}")) % (2**31), message=msg)
                    # Run in background so download doesn't block posting loop
                    asyncio.create_task(_safe_feed_update(bot, update, sc))
                except Exception as exc:
                    logger.warning("watch_feed: feed_update failed for %s: %s", sc, exc)
        except Exception as exc:
            logger.warning("watch_feed: send to %s failed: %s", cid, exc)
    return sent


async def _safe_feed_update(bot, update, sc: str) -> None:
    """Feed update to dispatcher; log but never crash."""
    try:
        await _dispatcher.feed_update(bot, update)
        logger.info("watch_feed: IG handler processed %s", sc)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning("watch_feed: IG handler failed for %s: %s", sc, exc)


async def _process_items(
    bot, chat_ids: list[int], items: list[dict],
) -> tuple[int, int]:
    """Process a list of items. Returns (sent_count, error_count)."""
    posted = _load_posted()
    sent = 0
    errors = 0
    for item in items:
        sc = item.get("shortcode", "")
        if not sc or sc in posted:
            continue
        try:
            ok = await _post_single(bot, chat_ids, item)
            if ok:
                _mark_posted(posted, sc)
                sent += 1
            else:
                errors += 1
        except Exception as exc:
            logger.warning("watch_feed: item %s error: %s", sc, exc)
            errors += 1
    return sent, errors


# ── Notify admin ──

async def _notify_admin(bot, sent: int, errors: int) -> None:
    if not settings.admin_ids:
        return
    msg = (
        f"\u231a Watch Feeder cycle done\n"
        f"Posted: {sent} \u00b7 Errors: {errors}"
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, msg)
        except Exception:
            pass


# ── Time helpers ──

_MSK = timezone(timedelta(hours=3))


def _now_msk() -> datetime:
    return datetime.now(_MSK)


def _in_posting_window() -> bool:
    """Return True if current MSK hour is in the posting window [start, end)."""
    h = _now_msk().hour
    start = settings.wf_post_start_hour  # 7
    end = settings.wf_post_end_hour      # 1
    if start <= end:
        # same-day window, e.g. 9..17
        return start <= h < end
    else:
        # overnight window, e.g. 7..1 → 7..23 + 0..0
        return h >= start or h < end


def _seconds_until_next_window() -> int:
    """Seconds until the next posting window starts."""
    now = _now_msk()
    start = settings.wf_post_start_hour
    target = now.replace(hour=start, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())


# ── Background scanner (24/7, slow) ──

async def candidate_scan_loop(bot) -> None:
    """Background loop: discover trending reels and cache best candidates.

    Primary: DuckDuckGo search engine (no Instagram login needed).
    Fallback: instagrapi account scanning (requires cookies).
    Runs 24/7 regardless of posting window. Candidates are cached for 2 days.
    """
    if not settings.watch_feeder_enabled:
        logger.info("wf_scan: DISABLED")
        return

    await asyncio.sleep(120)

    accounts = _load_accounts()
    batch_size = 10
    cycle = 0

    logger.info("wf_scan: started | %d accounts, batch=%d", len(accounts), batch_size)

    while True:
        try:
            new_items: list[dict] = []

            # -- DuckDuckGo search discovery (no Instagram auth) --
            try:
                from watch_discovery import discover_trending_reels
                search_results = await asyncio.wait_for(
                    discover_trending_reels(
                        top_n=50, include_ru=True, enrich_metadata=False,
                    ),
                    timeout=90,
                )
                if search_results:
                    for r in search_results:
                        r.setdefault("username", r.get("uploader", ""))
                        r.setdefault("likes", r.get("likes", 0))
                        r.setdefault("comments", r.get("comments", 0))
                        r.setdefault("views", r.get("views", 0))
                    new_items = search_results
                    logger.info(
                        "wf_scan: DuckDuckGo found %d candidates",
                        len(new_items),
                    )
            except ImportError:
                logger.info("wf_scan: watch_discovery not installed")
            except asyncio.TimeoutError:
                logger.warning("wf_scan: DuckDuckGo timed out (90s)")
            except Exception as exc:
                logger.warning("wf_scan: DuckDuckGo failed: %s", exc)

            # Add to cache
            added = 0
            if new_items:
                added = _add_candidates(new_items)

            # Prune old candidates
            _prune_candidates()

            total_cache = len(_load_candidates())
            logger.info(
                "wf_scan: cycle done -- found %d, added %d new, cache=%d",
                len(new_items), added, total_cache,
            )

            cycle += 1
            # Sleep ~18-25 min between batches (randomized)
            await asyncio.sleep(random.uniform(18 * 60, 25 * 60))

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("wf_scan error: %s", exc, exc_info=True)
            # Longer cooldown on errors (8-15 min) to avoid hammering
            await asyncio.sleep(random.uniform(8 * 60, 15 * 60))


async def _scan_batch_slow(usernames: list[str]) -> list[dict]:
    """Scan a small batch of accounts with delays. Returns fresh candidates."""
    from instagram_download import _get_client

    posted = _load_posted()
    candidates: list[dict] = []
    seen: set[str] = set()
    cl = _get_client()

    for username in usernames:
        try:
            uid = await asyncio.to_thread(cl.user_id_from_username, username)
            # Human-like delay between API calls (Gumbel-distributed)
            from instagram_anti_detection import async_smart_sleep, between_accounts_delay
            await async_smart_sleep(1.5, 3.0)
            medias = await asyncio.to_thread(cl.user_medias, uid, 12)
        except Exception as exc:
            logger.info("wf_scan: @%s failed: %s", username, exc)
            await asyncio.sleep(random.uniform(2.0, 5.0))
            continue

        for m in medias:
            sc = getattr(m, "code", "") or ""
            if not sc or sc in posted or sc in seen:
                continue
            if getattr(m, "media_type", 0) not in (2, 8):
                continue
            likes = getattr(m, "like_count", 0) or 0
            comments = getattr(m, "comment_count", 0) or 0
            views = getattr(m, "view_count", 0) or 0
            score = likes + comments * 3 + views * 0.1
            candidates.append({
                "shortcode": sc,
                "username": username,
                "media_id": m.id,
                "likes": likes,
                "comments": comments,
                "views": int(views),
                "score": score,
            })
            seen.add(sc)

        # Pause between accounts (3-12s, randomized)
        await asyncio.sleep(between_accounts_delay())

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


# ── Main loop ──

async def watch_feed_loop(bot) -> None:
    """Background loop: post best reels from cache during posting window (7:00–01:00 MSK).

    Each hour the loop:
      1. Pops top candidates from pre-scanned cache
      2. Posts them ONE-BY-ONE with ~7 min gaps between posts
      3. Sleeps until the next hour
    """
    if not settings.watch_feeder_enabled:
        logger.info("watch_feed: DISABLED")
        return
    chat_ids = settings.watch_feeder_chat_ids
    if not chat_ids:
        logger.warning("watch_feed: no chat IDs configured")
        return

    await asyncio.sleep(120)

    posts_per_hour = settings.wf_posts_per_hour
    logger.info(
        "watch_feed: started | posts_per_hour=%d | window=%02d:00–%02d:00 MSK | chats=%s",
        posts_per_hour,
        settings.wf_post_start_hour,
        settings.wf_post_end_hour,
        chat_ids,
    )

    while True:
        try:
            # ── Check posting window ──
            # TODO: Remove this comment and restore window check after testing
            # if not _in_posting_window():
            #     wait = _seconds_until_next_window()
            #     ...

            # ── 1. Pull best candidates from cache ──
            cache_size = len(_load_candidates())
            candidates = _pop_top_candidates(posts_per_hour)

            if not candidates:
                # Cache empty — try DuckDuckGo discovery
                logger.info("watch_feed: cache empty (%d total), trying DuckDuckGo", cache_size)
                try:
                    from watch_discovery import search_reels
                    loop = asyncio.get_event_loop()
                    results = await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: search_reels(max_queries=4, query_timeout=10)),
                        timeout=60,
                    )
                    added = _add_candidates(results)
                    logger.info("watch_feed: DDG found %d, added %d new to cache", len(results), added)
                    candidates = _pop_top_candidates(posts_per_hour)
                except asyncio.TimeoutError:
                    logger.warning("watch_feed: DDG timed out (60s)")
                except Exception as exc:
                    logger.warning("watch_feed: DDG discovery failed: %s", exc)

            if not candidates:
                # DDG failed — auto-seed from built-in list
                seeded = _seed_cache_if_empty()
                if seeded:
                    candidates = _pop_top_candidates(posts_per_hour)
                    logger.info("watch_feed: auto-seeded %d builtin reels, got %d candidates", seeded, len(candidates))

            if not candidates:
                logger.info("watch_feed: still no candidates, sleeping 30 min")
                await asyncio.sleep(1800)
                continue

            logger.info("watch_feed: pulled %d from cache (%d remaining)",
                        len(candidates), len(_load_candidates()))

            # ── 2. Spread posts evenly across the hour ──
            posted = _load_posted()
            gap = 3600 / len(candidates)  # seconds between posts
            sent = 0
            errors = 0

            logger.info(
                "watch_feed: %d candidates, posting every ~%d min",
                len(candidates), gap // 60,
            )

            for idx, item in enumerate(candidates):
                sc = item.get("shortcode", "")
                if not sc or sc in posted:
                    continue
                if not _in_posting_window():
                    pass  # TODO: restore after testing: break

                try:
                    ok = await _post_single(bot, chat_ids, item)
                    if ok:
                        _mark_posted(posted, sc)
                        sent += 1
                        logger.info(
                            "watch_feed: [%d/%d] posted %s (score=%.0f)",
                            idx + 1, len(candidates), sc, item.get("score", 0),
                        )
                    else:
                        errors += 1
                except Exception as exc:
                    logger.warning("watch_feed: item %s error: %s", sc, exc)
                    errors += 1

                # Sleep between posts (skip sleep after the last one)
                if idx < len(candidates) - 1:
                    # Gumbel-distributed jitter ±20%, looks more natural than uniform
                    from instagram_anti_detection import post_jitter
                    jitter = post_jitter(gap)
                    await asyncio.sleep(jitter)

            # ── 3. Notify & sleep until next hour ──
            _record_cycle(sent, errors)
            if sent > 0 or errors > 0:
                await _notify_admin(bot, sent, errors)

            await _sleep_until_next_hour()

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("watch_feed loop error: %s", exc, exc_info=True)
            await asyncio.sleep(3600)


async def _sleep_until_next_hour() -> None:
    """Sleep until the top of the next MSK hour."""
    now = _now_msk()
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    sleep_sec = max(10, int((next_hour - now).total_seconds()))
    logger.info("watch_feed: sleeping %d min until next hour", sleep_sec // 60)
    await asyncio.sleep(sleep_sec)






