"""Watch Feeder - auto-post trending watch videos from Instagram."""
from __future__ import annotations
import asyncio, json, logging, os
from pathlib import Path
from config import settings

logger = logging.getLogger(__name__)

_WATCH_ACCOUNTS = [
    "rolex", "omega", "audemarspiguet", "hublot", "cartier",
    "iwc", "jaegerlecoultre", "tagheuer", "breitling", "panerai",
    "tudorwatch", "longines", "oriswatches", "hamiltonwatch",
    "citizenwatch", "seikowatches", "tissot", "mido", "certina",
    "hodinkee", "watchesandwonders", "watchfinder", "bobswatches",
    "watchbox", "luxurybazaar", "jondwatches", "meisterwatch",
    "watchgrafia", "watchvp", "watchanish",
]

_ACCOUNTS_FILE = settings.data_dir / "watch_accounts.json"
_POSTED_FILE = settings.data_dir / "watch_posted.json"
_MAX_BYTES = 52_428_800


def _load_accounts():
    if _ACCOUNTS_FILE.is_file():
        try:
            d = json.loads(_ACCOUNTS_FILE.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return d
        except Exception:
            pass
    _ACCOUNTS_FILE.write_text(json.dumps(_WATCH_ACCOUNTS, ensure_ascii=False, indent=2), encoding="utf-8")
    return list(_WATCH_ACCOUNTS)


def _load_posted():
    if _POSTED_FILE.is_file():
        try:
            d = json.loads(_POSTED_FILE.read_text(encoding="utf-8"))
            if isinstance(d, list):
                return set(d)
        except Exception:
            pass
    return set()


def _save_posted(posted):
    _POSTED_FILE.write_text(json.dumps(sorted(posted)[-500:]), encoding="utf-8")


def _mark_posted(posted, media_id):
    posted.add(media_id)
    _save_posted(posted)


def _download_shortcode(shortcode):
    from instagram_download import _get_client, _downloads_dir, _dest_path
    cl = _get_client()
    folder = _downloads_dir()
    try:
        pk = cl.media_pk_from_url(f"https://www.instagram.com/reel/{shortcode}/")
    except Exception:
        pk = cl.media_pk_from_url(f"https://www.instagram.com/p/{shortcode}/")
    try:
        raw = cl.clip_download(pk, folder=folder)
    except Exception:
        raw = cl.video_download(pk, folder=folder)
    dest = _dest_path()
    os.rename(str(raw), str(dest))
    if dest.stat().st_size > _MAX_BYTES:
        dest.unlink(missing_ok=True)
        return None
    return dest


async def fetch_trending_videos(top_n=5):
    from instagram_download import _get_client
    posted = _load_posted()
    accounts = _load_accounts()
    candidates = []
    cl = _get_client()
    for username in accounts:
        try:
            uid = await asyncio.to_thread(cl.user_id_from_username, username)
            medias = await asyncio.to_thread(cl.user_medias, uid, 6)
        except Exception as exc:
            logger.warning("watch_feed: fetch @%s failed: %s", username, exc)
            continue
        for m in medias:
            if m.id in posted or m.media_type not in (2, 8):
                continue
            likes = getattr(m, "like_count", 0) or 0
            comments = getattr(m, "comment_count", 0) or 0
            views = getattr(m, "view_count", 0) or 0
            score = likes + comments * 3 + views * 0.1
            candidates.append({
                "username": username, "media_id": m.id,
                "shortcode": getattr(m, "code", "") or "",
                "likes": likes, "comments": comments,
                "views": int(views), "score": score,
            })
    candidates.sort(key=lambda x: x["score"], reverse=True)
    logger.info("watch_feed: %d candidates, top %d", len(candidates), top_n)
    return candidates[:top_n]


async def post_videos_to_chat(bot, chat_ids, videos):
    from instagram_download import remove_file
    from aiogram.types import FSInputFile
    posted = _load_posted()
    sent = 0
    for item in videos:
        if item["media_id"] in posted:
            continue
        try:
            fp = await asyncio.to_thread(_download_shortcode, item["shortcode"])
        except Exception as exc:
            logger.warning("watch_feed: dl @%s failed: %s", item["username"], exc)
            continue
        if fp is None:
            continue
        cap = f"\u231a @{item['username']} | \u2764\ufe0f {item['likes']} \U0001f4ac {item['comments']} \U0001f441 {item['views']}"
        try:
            for cid in chat_ids:
                try:
                    await bot.send_video(chat_id=cid, video=FSInputFile(fp), caption=cap)
                    sent += 1
                except Exception as exc:
                    logger.warning("watch_feed: send %s failed: %s", cid, exc)
            _mark_posted(posted, item["media_id"])
        finally:
            remove_file(fp)
        await asyncio.sleep(5)
    logger.info("watch_feed: sent %d videos", sent)
    return sent


async def watch_feed_loop(bot):
    if not settings.watch_feeder_enabled:
        logger.info("watch_feed: DISABLED")
        return
    chat_ids = settings.watch_feeder_chat_ids
    if not chat_ids:
        logger.warning("watch_feed: no chat IDs")
        return
    await asyncio.sleep(120)
    interval = settings.watch_feeder_interval_hours * 3600
    logger.info("watch_feed: interval=%dh, chats=%s", settings.watch_feeder_interval_hours, chat_ids)
    while True:
        try:
            from instagram_download import instagram_is_active_check
            if not instagram_is_active_check():
                await asyncio.sleep(interval)
                continue
            vids = await fetch_trending_videos(top_n=3)
            if vids:
                await post_videos_to_chat(bot, chat_ids, vids)
        except Exception as exc:
            logger.error("watch_feed loop: %s", exc, exc_info=True)
        await asyncio.sleep(interval)
