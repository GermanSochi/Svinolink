"""Поиск в интернете (DuckDuckGo) — без API-ключей."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_SEARCH_TRIGGERS = re.compile(
    r"(?is)(?:"
    r"(?:найди|поищи|загугли|погугли|гугли)\s+(?:в\s+)?(?:интернет[еу]?|сети|гугл(?:е)?)"
    r"|(?:что|как)\s+(?:пишут\s+)?(?:в\s+)?интернет[еу]?\s+(?:про|о)"
    r"|поиск\s+в\s+(?:интернет[еу]?|сети)"
    r"|найди\s+в\s+сети"
    r")"
)

_SHORTHAND = re.compile(
    r"(?is)^(?:погугли|загугли|гугли|найди|поищи)\s+(.+)$"
)

_SVIN_PREFIX = re.compile(r"(?i)^(свин|свинья)[\s,!?.\-]*")


def is_web_search_request(text: str | None) -> bool:
    if not text:
        return False
    return extract_search_query(text) is not None


def extract_search_query(text: str) -> str | None:
    """Текст запроса для поиска или None."""
    blob = _SVIN_PREFIX.sub("", text.strip()).strip()
    if not blob or not _SEARCH_TRIGGERS.search(blob):
        m = _SHORTHAND.search(blob)
        if m:
            q = _clean_query(m.group(1))
            return q if q else None
        return None

    for pat in (
        r"(?is)(?:найди|поищи|загугли|погугли|гугли)\s+(?:в\s+)?(?:интернет[еу]?|сети|гугл(?:е)?)\s*[:\-—]?\s*(.+)$",
        r"(?is)(?:что|как)\s+(?:пишут\s+)?(?:в\s+)?интернет[еу]?\s+(?:про|о)\s+(.+)$",
        r"(?is)поиск\s+в\s+(?:интернет[еу]?|сети)\s*[:\-—]?\s*(.+)$",
        r"(?is)найди\s+в\s+сети\s+(.+)$",
    ):
        m = re.search(pat, blob)
        if m:
            q = _clean_query(m.group(1))
            if q:
                return q

    m = _SHORTHAND.search(blob)
    if m:
        q = _clean_query(m.group(1))
        if q:
            return q
    return None


def _clean_query(raw: str) -> str:
    q = raw.strip().strip("«»\"'").strip(" ?!.")
    q = re.sub(r"\s+", " ", q)
    if len(q) < 2:
        return ""
    return q[:300]


def _search_sync(query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
    from ddgs import DDGS

    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


async def _instant_answer_fallback(query: str) -> list[dict[str, Any]]:
    """Запасной канал — DuckDuckGo Instant Answer API (без браузера)."""
    url = "https://api.duckduckgo.com/"
    params = {"q": query, "format": "json", "no_redirect": 1, "no_html": 1}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=12)
        ) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
    except Exception as exc:
        logger.warning("DDG instant answer failed: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    abstract = str(data.get("AbstractText") or "").strip()
    abstract_url = str(data.get("AbstractURL") or "").strip()
    heading = str(data.get("Heading") or query).strip()
    if abstract:
        out.append(
            {
                "title": heading,
                "href": abstract_url or "https://duckduckgo.com/",
                "body": abstract,
            }
        )

    for topic in data.get("RelatedTopics") or []:
        if isinstance(topic, dict) and "Text" in topic:
            text = str(topic["Text"])
            href = str(topic.get("FirstURL") or "")
            title = text.split(" - ", 1)[0] if " - " in text else text[:80]
            out.append({"title": title, "href": href, "body": text})
        elif isinstance(topic, dict) and "Topics" in topic:
            for sub in topic.get("Topics") or []:
                if not isinstance(sub, dict) or "Text" not in sub:
                    continue
                text = str(sub["Text"])
                href = str(sub.get("FirstURL") or "")
                title = text.split(" - ", 1)[0] if " - " in text else text[:80]
                out.append({"title": title, "href": href, "body": text})
        if len(out) >= 5:
            break
    return out


async def search_web(query: str, *, max_results: int = 5) -> list[dict[str, Any]]:
    q = _clean_query(query)
    if not q:
        return []

    try:
        rows = await asyncio.wait_for(
            asyncio.to_thread(_search_sync, q, max_results=max_results),
            timeout=20,
        )
        if rows:
            return rows
    except Exception as exc:
        logger.warning("ddgs search failed: %s", exc)

    return await _instant_answer_fallback(q)


def format_search_markdown(query: str, results: list[dict[str, Any]]) -> str:
    if not results:
        return (
            f"🐷 По запросу **«{query}»** в сети ничего не нашёл.\n\n"
            "💬 Переформулируй или уточни: "
            "**«Свин, найди в интернете …»**"
        )

    lines = [
        f"🐷 **Поиск в сети** — **«{query}»**\n",
        f"🔎 Нашёл **{len(results)}** ссылок:\n",
    ]
    for i, row in enumerate(results[:5], 1):
        title = str(row.get("title") or "Без названия").strip()
        href = str(row.get("href") or row.get("link") or "").strip()
        body = str(row.get("body") or row.get("snippet") or "").strip()
        if len(body) > 220:
            body = body[:220] + "…"
        block = f"\n🔹 **{title}**"
        if body:
            block += f"\n{body}"
        if href:
            block += f"\n🔗 {href}"
        lines.append(block)

    lines.append(
        "\n\n✅ Источник: **DuckDuckGo**. Для уточнения — спроси ещё раз конкретнее."
    )
    return "\n".join(lines)
