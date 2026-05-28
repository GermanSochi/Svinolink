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


def ddg_search_query(display_query: str) -> str:
    """Запрос в DuckDuckGo — без «что такое», чтобы сниппеты были по делу."""
    q = _clean_query(display_query)
    if not q:
        return display_query
    s = re.sub(r"(?i)^(?:что|кто)\s+такое\s+", "", q)
    s = re.sub(r"(?i)^(?:расскажи|объясни)\s+(?:про\s+)?", "", s)
    return s.strip() or q


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


_LINKS_HINT = re.compile(
    r"(?i)(?:"
    r"со\s+ссылками"
    r"|дай\s+ссылки"
    r"|покажи\s+ссылки"
    r"|с\s+источниками"
    r"|источники"
    r"|ссылки\s+на"
    r")"
)


def wants_search_links(text: str) -> bool:
    return bool(_LINKS_HINT.search(text))


async def search_web(query: str, *, max_results: int = 6) -> list[dict[str, Any]]:
    q = _clean_query(query)
    if not q:
        return []

    ddg_q = ddg_search_query(q)
    try:
        rows = await asyncio.wait_for(
            asyncio.to_thread(_search_sync, ddg_q, max_results=max_results),
            timeout=20,
        )
        if rows:
            return rows
    except Exception as exc:
        logger.warning("ddgs search failed: %s", exc)

    return await _instant_answer_fallback(ddg_q)


def format_search_markdown(
    query: str,
    results: list[dict[str, Any]],
    *,
    max_show: int = 3,
) -> str:
    if not results:
        return (
            f"🐷 По запросу **«{query}»** в сети ничего не нашёл.\n\n"
            "💬 Переформулируй или уточни: "
            "**«Свин, найди в интернете …»**"
        )

    lines = [f"🐷 **«{query}»** — источники:\n"]
    for row in results[:max_show]:
        title = str(row.get("title") or "Без названия").strip()
        href = str(row.get("href") or row.get("link") or "").strip()
        body = str(row.get("body") or row.get("snippet") or "").strip()
        if len(body) > 140:
            body = body[:140] + "…"
        block = f"\n🔹 **{title}**"
        if body:
            block += f"\n{body}"
        if href:
            block += f"\n🔗 {href}"
        lines.append(block)
    return "\n".join(lines)


def build_search_evidence(
    query: str,
    results: list[dict[str, Any]],
    pages: list[tuple[str, dict[str, str]]],
) -> str:
    parts = [f"Запрос: {query}\n"]
    for i, row in enumerate(results, 1):
        title = str(row.get("title") or "").strip()
        body = str(row.get("body") or row.get("snippet") or "").strip()
        parts.append(f"\n[{i}] {title}\n{body[:500]}")
    for url, data in pages:
        if data.get("error"):
            continue
        chunk = "\n".join(
            x
            for x in (
                data.get("title"),
                data.get("h1"),
                data.get("description"),
                (data.get("body") or "")[:900],
            )
            if x
        )
        parts.append(f"\n[страница {url}]\n{chunk}")
    return "\n".join(parts)


def _strip_urls(text: str) -> str:
    return re.sub(r"https?://\S+", "", text).strip()


def wrap_short_answer(query: str, answer: str) -> str:
    body = _strip_urls(answer.strip())
    if len(body) > 1100:
        body = body[:1100].rsplit(" ", 1)[0] + "…"
    return f"🐷 **«{query}»**\n\n{body}"


SEARCH_SYNTHESIS_SYSTEM = """
Ты — Свин, свой в чате друзей. Ниже — фрагменты из поиска в интернете (сниппеты и страницы).

Задача: один короткий ответ по-русски, как будто сам гуглил и объясняешь в чате.

Правила:
- 3–5 предложений, без воды и без длинных списков
- БЕЗ ссылок, URL, «источник:», «по данным сайта…»
- Сведи факты из фрагментов в одну версию; противоречия — осторожно («ходят версии…»)
- Не выдумывай факты, которых нет во фрагментах ниже
- Если тема косвенно есть (синонимы, соседние слова) — свяжи и объясни простыми словами
- Народное прозвище, мем, сленг — объясни суть

ЗАПРЕЩЕНО в ответе пользователю:
- слова «выжимка», «предоставлен», «в материалах», «в сниппетах», «в источниках»
- фразы «нет информации в …» — вместо этого: «в сети мало пишут» или перескажи близкое из фрагментов
- отказы и мета-объяснения про то, как тебе дали данные

Можно **жирным** 1–2 слова. Без «Я ИИ».
""".strip()


_META_REFUSAL_RE = re.compile(
    r"(?i)("
    r"выжимк"
    r"|предоставленн"
    r"|в\s+(?:этих\s+)?(?:материалах|источниках|сниппетах|фрагментах)"
    r"|нет\s+(?:в\s+)?(?:информации|данных)"
    r"|не\s+наш[её]л\s+(?:ничего|информации|упоминан)"
    r"|отсутствует\s+информация"
    r")"
)


def is_meta_refusal_answer(text: str) -> bool:
    return bool(_META_REFUSAL_RE.search(text))


def build_snippet_fallback(query: str, results: list[dict[str, Any]]) -> str:
    """Ответ из сниппетов DDG, если GPT ушёл в «нет в выжимках»."""
    parts: list[str] = []
    for row in results[:3]:
        title = str(row.get("title") or "").strip()
        body = str(row.get("body") or row.get("snippet") or "").strip()
        if title and body:
            parts.append(f"{title}: {body}")
        elif body:
            parts.append(body)
        elif title:
            parts.append(title)
    if not parts:
        return (
            f"🐷 **«{query}»**\n\n"
            "В поиске пусто — попробуй короче, например: **«автомобиль пирожок»**."
        )
    merged = " ".join(parts)
    if len(merged) > 950:
        merged = merged[:950].rsplit(" ", 1)[0] + "…"
    return f"🐷 **«{query}»**\n\n{merged}"


async def fetch_top_pages(
    results: list[dict[str, Any]], *, limit: int = 3
) -> list[tuple[str, dict[str, str]]]:
    urls: list[str] = []
    for row in results:
        href = str(row.get("href") or row.get("link") or "").strip()
        if href.startswith("http") and "instagram.com" not in href.lower():
            urls.append(href)
        if len(urls) >= limit:
            break

    async def _one(url: str) -> tuple[str, dict[str, str]]:
        try:
            data = await asyncio.wait_for(fetch_page_preview(url), timeout=14)
            return url, data
        except Exception as exc:
            logger.warning("page fetch %s: %s", url[:80], exc)
            return url, {"error": str(exc)}

    if not urls:
        return []
    return list(await asyncio.gather(*[_one(u) for u in urls]))


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
