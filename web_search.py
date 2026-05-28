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
    r"|(?:прочитай|узнай|расскажи)\s+(?:в\s+)?интернет[еу]?"
    r"|(?:что|как)\s+(?:пишут\s+)?(?:в\s+)?интернет[еу]?\s+(?:про|о)"
    r"|поиск\s+в\s+(?:интернет[еу]?|сети)"
    r"|найди\s+в\s+сети"
    r")"
)

_HTTP_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

_READ_URL_WORDS = (
    "прочитай",
    "открой",
    "посмотри",
    "глянь",
    "что на сайте",
    "что на странице",
    "расскажи про сайт",
    "вытащи текст",
    "достань текст со страницы",
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
        r"(?is)(?:прочитай|узнай|расскажи)\s+(?:в\s+)?интернет[еу]?\s+(?:что\s+такое\s+)?(.+)$",
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


def extract_http_url(text: str) -> str | None:
    """Любая http(s) ссылка, кроме Instagram (тот — отдельный обработчик)."""
    m = _HTTP_URL_RE.search(text)
    if not m:
        return None
    url = m.group(0).rstrip(").,]>")
    if "instagram.com" in url.lower():
        return None
    return url


def wants_url_read(text: str) -> bool:
    low = text.lower()
    if not extract_http_url(text):
        return False
    return any(w in low for w in _READ_URL_WORDS)


async def fetch_page_preview(url: str) -> dict[str, str]:
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; SvinolinkBot/1.0; +https://github.com/GermanSochi/Svinolink)"
        )
    }
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=18),
        headers=headers,
    ) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status >= 400:
                return {"error": f"HTTP {resp.status}"}
            html = await resp.text(errors="ignore")

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    h1 = soup.find("h1")
    h1_text = h1.get_text(" ", strip=True) if h1 else ""

    meta_desc = ""
    meta = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
    if meta and meta.get("content"):
        meta_desc = str(meta["content"]).strip()

    chunks: list[str] = []
    for p in soup.find_all("p", limit=8):
        t = p.get_text(" ", strip=True)
        if len(t) > 40:
            chunks.append(t)
    body = "\n".join(chunks)
    if len(body) > 1800:
        body = body[:1800] + "…"

    return {
        "title": title,
        "h1": h1_text,
        "description": meta_desc,
        "body": body,
        "final_url": url,
    }


def format_page_markdown(url: str, data: dict[str, str]) -> str:
    if data.get("error"):
        return (
            f"🐷 Не смог открыть ссылку.\n\n"
            f"🔗 {url}\n\n"
            f"❌ {data['error']}"
        )

    lines = [f"🐷 **Страница по ссылке**\n\n🔗 {url}\n"]
    if data.get("title"):
        lines.append(f"\n🏷️ **{data['title']}**")
    if data.get("h1") and data["h1"] != data.get("title"):
        lines.append(f"\n📌 **{data['h1']}**")
    if data.get("description"):
        lines.append(f"\n💬 {data['description']}")
    if data.get("body"):
        lines.append(f"\n\n🧾 {data['body']}")
    if len(lines) == 1:
        lines.append("\n\n⚠️ Текст на странице не вытащился — возможно, сайт грузится через JS.")
    return "\n".join(lines)
