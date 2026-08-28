"""Watch Feeder v2 — auto-post trending watch videos from Instagram.

Features:
  * Queue-based processing: shortcodes from data/watch_queue.json get priority.
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
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

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
_QUEUE_FILE = settings.data_dir / "watch_queue.json"
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


# ── Queue management ──

def load_queue() -> list[dict]:
    """Load queue entries. Each is {shortcode, added_at, source?}."""
    if _QUEUE_FILE.is_file():
        try:
            d = json.loads(_QUEUE_FILE.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return d
        except Exception:
            pass
    return []


def save_queue(queue: list[dict]) -> None:
    _QUEUE_FILE.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def add_to_queue(shortcodes: list[str], source: str = "manual") -> int:
    """Add shortcodes to queue, skip already-posted or already-queued."""
    queue = load_queue()
    posted = _load_posted()
    existing = {e["shortcode"] for e in queue}
    added = 0
    now = time.time()
    for sc in shortcodes:
        sc = sc.strip()
        if not sc or sc in posted or sc in existing:
            continue
        queue.append({"shortcode": sc, "added_at": now, "source": source})
        added += 1
    save_queue(queue)
    return added


def pop_queue_batch(max_items: int = 3) -> list[dict]:
    """Pop up to max_items entries from the front of queue."""
    queue = load_queue()
    batch = queue[:max_items]
    save_queue(queue[max_items:])
    return batch


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
    queue_len = len(load_queue())
    posted_count = len(_load_posted())
    return (
        f"\u231a Watch Feeder Stats\n"
        f"Cycles: {s.get('cycles', 0)}\n"
        f"Total posted: {s.get('total_posted', 0)}\n"
        f"Total errors: {s.get('total_errors', 0)}\n"
        f"In dedup DB: {posted_count}\n"
        f"In queue: {queue_len}\n"
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
    for url_pattern in (
        f"https://www.instagram.com/reel/{shortcode}/",
        f"https://www.instagram.com/p/{shortcode}/",
    ):
        try:
            pk = cl.media_pk_from_url(url_pattern)
            break
        except Exception:
            continue

    if pk is None:
        logger.warning("watch_feed: cannot resolve pk for %s", shortcode)
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
    cl = _get_client()

    for username in accounts:
        try:
            uid = await asyncio.to_thread(cl.user_id_from_username, username)
            medias = await asyncio.to_thread(cl.user_medias, uid, 12)
        except Exception as exc:
            logger.debug("watch_feed: fetch @%s failed: %s", username, exc)
            continue

        for m in medias:
            sc = getattr(m, "code", "") or ""
            if not sc or sc in posted:
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

    candidates.sort(key=lambda x: x["score"], reverse=True)
    logger.info("watch_feed: %d candidates from %d accounts", len(candidates), len(accounts))
    return candidates[:top_n]


# ── Post videos ──

async def _post_single(bot, chat_ids: list[int], item: dict) -> bool:
    """Download, build caption, send to all chats. Returns True if sent."""
    from instagram_download import remove_file
    from aiogram.types import FSInputFile

    sc = item["shortcode"]
    fp = None
    try:
        fp = await asyncio.to_thread(_download_shortcode, sc)
    except Exception as exc:
        logger.warning("watch_feed: dl %s failed: %s", sc, exc)
        return False

    if fp is None:
        return False

    info = await asyncio.to_thread(_get_media_info, sc)
    if info:
        item = {**item, **{k: v for k, v in info.items() if v}}

    caption = _build_caption(item)
    sent = False
    try:
        for cid in chat_ids:
            try:
                await bot.send_video(
                    chat_id=cid,
                    video=FSInputFile(fp),
                    caption=caption,
                )
                sent = True
            except Exception as exc:
                logger.warning("watch_feed: send to %s failed: %s", cid, exc)
    finally:
        remove_file(fp)

    await asyncio.sleep(5)
    return sent


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

async def _notify_admin(bot, sent: int, errors: int, queue_left: int) -> None:
    if not settings.admin_ids:
        return
    msg = (
        f"\u231a Watch Feeder cycle done\n"
        f"Posted: {sent} \u00b7 Errors: {errors} \u00b7 Queue left: {queue_left}"
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, msg)
        except Exception:
            pass


# ── Seed queue from file ──

def seed_queue_from_links_file(filepath: str | Path) -> int:
    """Parse a text file with Instagram reel URLs, extract shortcodes, add to queue."""
    p = Path(filepath)
    if not p.is_file():
        return 0
    text = p.read_text(encoding="utf-8")
    shortcodes = re.findall(r"/reel/([A-Za-z0-9_-]+)", text)
    return add_to_queue(shortcodes, source="links_file")


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


# ── Main loop ──

async def watch_feed_loop(bot) -> None:
    """Background loop: post 5-10 items/hour during posting window (7:00–01:00 MSK)."""
    if not settings.watch_feeder_enabled:
        logger.info("watch_feed: DISABLED")
        return
    chat_ids = settings.watch_feeder_chat_ids
    if not chat_ids:
        logger.warning("watch_feed: no chat IDs configured")
        return

    await asyncio.sleep(120)

    interval = settings.watch_feeder_interval_hours * 3600
    posts_per_cycle = settings.wf_posts_per_hour
    logger.info(
        "watch_feed: started | interval=%dh | posts_per_cycle=%d | window=%02d:00–%02d:00 MSK | chats=%s",
        settings.watch_feeder_interval_hours,
        posts_per_cycle,
        settings.wf_post_start_hour,
        settings.wf_post_end_hour,
        chat_ids,
    )

    while True:
        try:
            # ── Check posting window ──
            if not _in_posting_window():
                wait = _seconds_until_next_window()
                logger.info(
                    "watch_feed: outside window (%02d:00 MSK), sleeping %d min until %02d:00",
                    _now_msk().hour, wait // 60, settings.wf_post_start_hour,
                )
                # Sleep in 60s chunks so we can wake up promptly
                while wait > 0:
                    await asyncio.sleep(min(wait, 60))
                    wait -= 60
                continue

            from instagram_download import instagram_is_active_check

            if not instagram_is_active_check():
                logger.info("watch_feed: Instagram inactive, skipping cycle")
                await asyncio.sleep(interval)
                continue

            sent = 0
            errors = 0

            # 1. Process queue first (manual / imported shortcodes) — take more per cycle
            queue_batch = pop_queue_batch(max_items=posts_per_cycle)
            if queue_batch:
                logger.info("watch_feed: processing %d queue items", len(queue_batch))
                queue_items = [
                    {"shortcode": e["shortcode"], "username": "", "likes": 0,
                     "comments": 0, "views": 0}
                    for e in queue_batch
                ]
                s, e = await _process_items(bot, chat_ids, queue_items)
                sent += s
                errors += e

            # 2. Account discovery — fill remaining slots with best reels sorted by engagement
            remaining = posts_per_cycle - sent
            if remaining > 0:
                try:
                    candidates = await _fetch_from_accounts(top_n=remaining)
                    if candidates:
                        s, e = await _process_items(bot, chat_ids, candidates)
                        sent += s
                        errors += e
                except Exception as exc:
                    logger.warning("watch_feed: account discovery failed: %s", exc)
                    errors += 1

            queue_left = len(load_queue())
            _record_cycle(sent, errors)
            if sent > 0 or errors > 0:
                await _notify_admin(bot, sent, errors, queue_left)

            # Sleep until next hour within the window
            now = _now_msk()
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            sleep_sec = max(10, int((next_hour - now).total_seconds()))
            logger.info("watch_feed: cycle done (sent=%d), sleeping %d min", sent, sleep_sec // 60)
            await asyncio.sleep(sleep_sec)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("watch_feed loop error: %s", exc, exc_info=True)
            await asyncio.sleep(interval)






