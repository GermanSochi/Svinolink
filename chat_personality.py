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


def _humor_instruction(level: int) -> str:
    if level <= 20:
        return (
            "Юмор **выкл** (1–20): без шуток и каламбуров — только суть, сухо и по делу."
        )
    if level <= 45:
        return (
            "Юмор **лёгкий** (21–45): максимум одна короткая ирония, если уместна."
        )
    if level <= 70:
        return (
            "Юмор **средний** (46–70): заметная ирония, можно сравнение; не устраивай стендап."
        )
    return (
        "Юмор **высокий** (71–100): 1–2 остроты на ответ, живо, но в тему вопроса."
    )


def _toxicity_instruction(level: int) -> str:
    if level <= 15:
        return (
            "Подкол **выкл** (1–15): не подъёбывай по имени, без сарказма в адрес людей."
        )
    if level <= 35:
        return (
            "Подкол **мягкий** (16–35): без прямых подколов; только дружеский тон."
        )
    if level <= 65:
        return (
            "Подкол **средний** (36–65): можно мягко подъебать по имени "
            "(Гия, Дима, Максим, Никита, Гера) — по-братски, без оскорблений."
        )
    return (
        "Подкол **жёсткий** (66–100): заметный подъёб в стиле друзей 40+, "
        "остро, но без мата, унижений и травли."
    )


def tone_appendix_for_chat(chat_id: int) -> str:
    """Блок в system prompt — GPT должен реально менять стиль."""
    p = get_personality(chat_id)
    return (
        f"\n\n=== ТОН ЧАТА (настройки группы: юмор {p.humor}%, подкол {p.toxicity}%) ===\n"
        "Это не цифры для красоты — **меняй подачу** под шкалу:\n"
        f"- {_humor_instruction(p.humor)}\n"
        f"- {_toxicity_instruction(p.toxicity)}\n"
        "- Если юмор низкий, а подкол высокий — шути мало, подкалывай сильнее.\n"
        "- Если оба высокие — живо и с подъёбом; если оба низкие — как спокойная справка.\n"
        "- На фактологические вопросы тон всё равно чувствуется (длина шуток, дерзость).\n"
        "=== конец ТОН ЧАТА ==="
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
    persist = (
        "💾 Настройки **сохраняются** в базе на сервере."
        if settings.is_render
        else "💾 Настройки в локальной базе `data/svinolink.db`."
    )
    return (
        "🐷 **Тон Свина в этом чате**\n\n"
        f"😏 Юмор: **{p.humor}%** — {_humor_instruction(p.humor)}\n\n"
        f"🔥 Подкол: **{p.toxicity}%** — {_toxicity_instruction(p.toxicity)}\n\n"
        f"{persist}\n\n"
        "📌 Работает в **обычных ответах Свина** и в **«что такое / как сделать»** "
        "(поиск в интернете). Не влияет на игры и список «со ссылками».\n\n"
        "⚙️ Меняет **админ**:\n\n"
        "🔹 `Свин уровень юмора 10 процентов`\n\n"
        "🔹 `Свин уровень токсичности 90 процентов`\n\n"
        "🔹 `Свин уровни` — показать снова"
    )
