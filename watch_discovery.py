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
    "site:instagram.com/reel rolex watches",
    "site:instagram.com/reel omega seamaster",
    "site:instagram.com/reel audemars piguet",
    "site:instagram.com/reel luxury watch collection",
    "site:instagram.com/reel watch review",
    "site:instagram.com/reel watch unboxing",
    "site:instagram.com/reel patek philippe",
    "site:instagram.com/reel tag heuer",
    "site:instagram.com/reel breitling",
    "site:instagram.com/reel panerai",
    "site:instagram.com/reel tudor watches",
    "site:instagram.com/reel seiko mod",
    "site:instagram.com/reel hublot",
    "site:instagram.com/reel cartier watch",
    "site:instagram.com/reel watch collection",
    "site:instagram.com/reel watch of the day",
    "site:instagram.com/reel wrist watch",
    "site:instagram.com/reel mens watch",
    "site:instagram.com/reel vintage watch",
]

_WATCH_QUERIES_RU: list[str] = [
    "site:instagram.com/reel часы Rolex",
    "site:instagram.com/reel часы Omega",
    "site:instagram.com/reel часы luxury",
    "site:instagram.com/reel часы коллекция",
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
    delay_between: float = 1.5,
    include_ru: bool = False,
) -> list[dict[str, Any]]:
    """Search DuckDuckGo for Instagram Reel URLs.

    Returns list of dicts: {shortcode, url, title, source}
    """
    # Prefer duckduckgo_search (older, uses DDG backend) over ddgs (newer, uses Yahoo)
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

    all_results: list[dict[str, Any]] = []
    seen_shortcodes: set[str] = set()

    for q in q_list:
        try:
            results = list(DDGS().text(q, max_results=max_results_per_query))
            for r in results:
                href = r.get("href", "")
                sc = _extract_shortcode(href)
                if not sc or sc in seen_shortcodes:
                    continue
                seen_shortcodes.add(sc)
                all_results.append({
                    "shortcode": sc,
                    "url": href,
                    "title": r.get("title", ""),
                    "source": "duckduckgo",
                })
        except Exception as exc:
            logger.warning("watch_discovery: query '%s' failed: %s", q[:50], exc)
        time.sleep(delay_between + random.uniform(0, 0.5))

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
