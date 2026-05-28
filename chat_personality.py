"""Юмор и токсичность (1–100) на чат — из SQLite, команды «Свин уровень …»."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from config import settings

DEFAULT_HUMOR = 45
DEFAULT_TOXICITY = 28


@dataclass(frozen=True)
class ChatPersonality:
    humor: int
    toxicity: int


def _db() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / "svinolink.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_personality (
            chat_id INTEGER PRIMARY KEY,
            humor INTEGER NOT NULL,
            toxicity INTEGER NOT NULL
        )
        """
    )
    return conn


def _clamp(value: int) -> int:
    return max(1, min(100, int(value)))


def get_personality(chat_id: int) -> ChatPersonality:
    with _db() as conn:
        row = conn.execute(
            "SELECT humor, toxicity FROM chat_personality WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    if not row:
        return ChatPersonality(humor=DEFAULT_HUMOR, toxicity=DEFAULT_TOXICITY)
    return ChatPersonality(humor=int(row[0]), toxicity=int(row[1]))


def set_personality(
    chat_id: int,
    *,
    humor: int | None = None,
    toxicity: int | None = None,
) -> ChatPersonality:
    cur = get_personality(chat_id)
    h = _clamp(humor if humor is not None else cur.humor)
    t = _clamp(toxicity if toxicity is not None else cur.toxicity)
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO chat_personality (chat_id, humor, toxicity)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                humor = excluded.humor,
                toxicity = excluded.toxicity
            """,
            (chat_id, h, t),
        )
    return ChatPersonality(humor=h, toxicity=t)


def tone_appendix_for_chat(chat_id: int) -> str:
    p = get_personality(chat_id)
    toxic_note = ""
    if p.toxicity >= 20:
        toxic_note = (
            f"\n- Подколы участников (**{p.toxicity}%**): по-братски, взрослый мужской юмор, "
            "без оскорблений и без травли. Можно подъебать мягко, с иронией. "
            "Имена из ростра: Гия, Дима, Максим, Никита, Гера (и их ники)."
        )
    humor_note = ""
    if p.humor >= 30:
        humor_note = (
            f"\n- Шутки (**{p.humor}%**): уместные, короткие, не затягивай стендап."
        )
    if p.toxicity < 15 and p.humor < 20:
        return (
            f"\n\nТОН ЧАТА: спокойный (юмор {p.humor}%, подкол {p.toxicity}%). "
            "Без лишней дерзости."
        )
    return (
        f"\n\nТОН ЧАТА (шкала 1–100, настройки группы):"
        f"\n- Юмор: **{p.humor}%**"
        f"\n- Токсичность/подкол: **{p.toxicity}%**"
        f"{humor_note}{toxic_note}"
        "\n- Не выходи за рамки дружеского чата 40+."
    )


_TOX_SET_RE = re.compile(
    r"(?i)(?:свин[\s,]+)?уровень\s+токсичност[ьи]\s+(\d{1,3})\s*(?:%|процент(?:ов)?)?"
)
_HUMOR_SET_RE = re.compile(
    r"(?i)(?:свин[\s,]+)?уровень\s+юмора\s+(\d{1,3})\s*(?:%|процент(?:ов)?)?"
)
_SHOW_RE = re.compile(
    r"(?i)(?:свин[\s,]+)?(?:уровни|настройки\s+тона|уровень\s+юмора\s+и\s+токсичност)"
)


def parse_personality_command(text: str) -> str | None:
    """set_humor:42 | set_toxic:20 | show | None"""
    blob = text.strip()
    m = _TOX_SET_RE.search(blob)
    if m:
        return f"set_toxic:{int(m.group(1))}"
    m = _HUMOR_SET_RE.search(blob)
    if m:
        return f"set_humor:{int(m.group(1))}"
    if _SHOW_RE.search(blob):
        return "show"
    low = blob.lower()
    if "уровень" in low and "токсич" in low and re.search(r"\d", blob):
        n = re.search(r"(\d{1,3})", blob)
        if n:
            return f"set_toxic:{int(n.group(1))}"
    if "уровень" in low and "юмор" in low and re.search(r"\d", blob):
        n = re.search(r"(\d{1,3})", blob)
        if n:
            return f"set_humor:{int(n.group(1))}"
    return None


def personality_status_markdown(chat_id: int) -> str:
    p = get_personality(chat_id)
    return (
        "🐷 **Тон Свина в этом чате**\n\n"
        f"😏 Юмор: **{p.humor}%**\n\n"
        f"🔥 Подкол (токсичность): **{p.toxicity}%**\n\n"
        "⚙️ Меняет **админ**:\n\n"
        "🔹 `Свин уровень юмора 40 процентов`\n\n"
        "🔹 `Свин уровень токсичности 25 процентов`\n\n"
        "🔹 `Свин уровни` — показать снова"
    )
