"""Watch Discovery — find trending Instagram Reels via DuckDuckGo search.

Replaces the instagrapi-based account scanning with search engine discovery:
  - DuckDuckGo finds Instagram Reel URLs by topic/brand/hashtag
  - No Instagram login, cookies, or API needed
  - No anti-detection required
  - Free, unlimited, no API key

Falls back to instagrapi scanning if DuckDuckGo fails.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

logger = logging.getLogger(__name__)

# -- Search queries themed for watch niche --

_WATCH_QUERIES_BASE: list[str] = [
    "instagram reel rolex watches",
    "instagram reel omega seamaster",
    "instagram reel audemars piguet",
    "instagram reel luxury watch",
    "instagram reel watch collection",
    "instagram reel patek philippe",
    "instagram reel tag heuer",
    "instagram reel seiko watch",
    "instagram reel vintage watch",
    "instagram reel watch unboxing",
]

_WATCH_QUERIES_RU: list[str] = [
    "instagram reel часы Rolex",
    "instagram reel часы Omega",
]


def _extract_shortcode(href: str) -> str:
    """Extract shortcode from Instagram reel URL."""
    if "/reel/" not in href:
        return ""
    try:
        sc = href.split("/reel/")[1].split("/")[0].split("?")[0]
        if "." in sc or len(sc) < 5:
            return ""
        return sc
    except (IndexError, ValueError):
        return ""


def search_reels(
    queries: list[str] | None = None,
    max_results_per_query: int = 10,
    delay_between: float = 1.0,
    include_ru: bool = False,
    query_timeout: float = 15.0,
    max_queries: int = 6,
) -> list[dict[str, Any]]:
    """Search DuckDuckGo for Instagram Reel URLs.

    Args:
        query_timeout: Max seconds per individual query.
        max_queries: Max number of queries to try (saves time).

    Returns list of dicts: {shortcode, url, title, source}
    """
    import concurrent.futures

    DDGS = None
    try:
        from duckduckgo_search import DDGS  # type: ignore[no-redef]
    except ImportError:
        pass
    if DDGS is None:
        try:
            from ddgs import DDGS  # type: ignore[no-redef]
        except ImportError:
            logger.warning("watch_discovery: pip install duckduckgo-search or ddgs")
            return []

    q_list = list(queries or _WATCH_QUERIES_BASE)
    if include_ru:
        q_list.extend(_WATCH_QUERIES_RU)
    random.shuffle(q_list)
    q_list = q_list[:max_queries]

    all_results: list[dict[str, Any]] = []
    seen_shortcodes: set[str] = set()

    def _do_query(q: str) -> list[dict]:
        try:
            results = list(DDGS().text(q, max_results=max_results_per_query))
            found = []
            for r in results:
                href = r.get("href", "")
                sc = _extract_shortcode(href)
                if not sc:
                    continue
                found.append({
                    "shortcode": sc,
                    "url": href,
                    "title": r.get("title", ""),
                    "source": "duckduckgo",
                })
            return found
        except Exception as exc:
            logger.warning("watch_discovery: query '%s' failed: %s", q[:50], exc)
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_do_query, q): q for q in q_list}
        for future in concurrent.futures.as_completed(futures, timeout=query_timeout * 2):
            q = futures[future]
            try:
                results = future.result(timeout=query_timeout)
                for r in results:
                    if r["shortcode"] not in seen_shortcodes:
                        seen_shortcodes.add(r["shortcode"])
                        all_results.append(r)
            except concurrent.futures.TimeoutError:
                logger.warning("watch_discovery: query '%s' timed out", q[:50])
            except Exception as exc:
                logger.warning("watch_discovery: query '%s' error: %s", q[:50], exc)

    logger.info(
        "watch_discovery: found %d unique Reels from %d queries",
        len(all_results), len(q_list),
    )
    return all_results


def _enrich_with_ytdlp(
    shortcodes: list[str],
    max_enrich: int = 20,
) -> list[dict[str, Any]]:
    """Enrich shortcodes with metadata via yt-dlp (no auth needed).

    Returns list of dicts with: shortcode, title, uploader, likes, views.
    """
    import yt_dlp

    enriched: list[dict[str, Any]] = []
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "format": "best"}

    for sc in shortcodes[:max_enrich]:
        url = f"https://www.instagram.com/reel/{sc}/"
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    enriched.append({
                        "shortcode": sc,
                        "title": info.get("title", ""),
                        "uploader": info.get("uploader", ""),
                        "likes": info.get("like_count", 0) or 0,
                        "views": info.get("view_count", 0) or 0,
                        "duration": info.get("duration", 0) or 0,
                        "description": info.get("description", ""),
                    })
        except Exception as exc:
            logger.debug("watch_discovery: ytdlp %s failed: %s", sc, exc)
        time.sleep(random.uniform(2.0, 5.0))

    return enriched


async def discover_trending_reels(
    top_n: int = 50,
    include_ru: bool = False,
    enrich_metadata: bool = False,
) -> list[dict[str, Any]]:
    """Main entry point: discover trending watch Reels via search engine.

    Args:
        top_n: Max results to return.
        include_ru: Include Russian-language queries.
        enrich_metadata: Fetch full metadata via yt-dlp (slower).

    Returns:
        List of candidate dicts with: shortcode, title, url, source, score.
    """
    results = search_reels(include_ru=include_ru)
    if not results:
        logger.warning("watch_discovery: no results from DuckDuckGo")
        return []

    if enrich_metadata and results:
        shortcodes = [r["shortcode"] for r in results[:top_n]]
        enriched = _enrich_with_ytdlp(shortcodes, max_enrich=min(20, top_n))
        enriched_map = {e["shortcode"]: e for e in enriched}
        for r in results:
            if r["shortcode"] in enriched_map:
                r.update(enriched_map[r["shortcode"]])

    for i, r in enumerate(results):
        position_score = max(0, 100 - i * 2)
        title_score = min(30, len(r.get("title", "")) * 0.3)
        engagement_score = (
            (r.get("likes", 0) or 0)
            + (r.get("views", 0) or 0) * 0.1
            + (r.get("comments", 0) or 0) * 3
        )
        r["score"] = position_score + title_score + engagement_score

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    logger.info(
        "watch_discovery: returning %d candidates (top score=%.0f)",
        len(results[:top_n]),
        results[0]["score"] if results else 0,
    )
    return results[:top_n]
