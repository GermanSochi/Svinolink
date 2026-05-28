"""Участники чата: Telegram-ник ↔ имя ↔ все производные для поиска в базе."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ChatMember:
    telegram: str
    label: str
    aliases: tuple[str, ...]

    def all_patterns(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in (self.telegram, self.label, *self.aliases):
            s = raw.strip().lstrip("@")
            if not s:
                continue
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out


# Основная группа — фиксированный состав (~5 человек)
MEMBERS: tuple[ChatMember, ...] = (
    ChatMember(
        telegram="notintricate",
        label="Гия",
        aliases=(
            "гиа",
            "гия",
            "гиоргий",
            "giorgiy",
            "georgiy",
            "giya",
            "notintricate",
        ),
    ),
    ChatMember(
        telegram="Dadstudio",
        label="Дима",
        aliases=(
            "дима",
            "дмитрий",
            "dmitry",
            "dima",
            "dadstudio",
            "димон",
        ),
    ),
    ChatMember(
        telegram="MokoBlajek",
        label="Максим",
        aliases=(
            "максим",
            "макс",
            "max",
            "maxim",
            "mokoblajek",
            "моко",
        ),
    ),
    ChatMember(
        telegram="Tom_Frod",
        label="Никита",
        aliases=(
            "никита",
            "nikita",
            "tom_frod",
            "tom",
            "frod",
            "том",
        ),
    ),
)

_BY_TELEGRAM: dict[str, ChatMember] = {m.telegram.lower(): m for m in MEMBERS}
_BY_ALIAS: dict[str, ChatMember] = {}
for _m in MEMBERS:
    for _p in _m.all_patterns():
        _BY_ALIAS[_p.lower()] = _m


def resolve_member(query: str) -> ChatMember | None:
    """Имя, ник или производная → участник."""
    q = query.strip().lstrip("@")
    if not q:
        return None
    low = q.lower()
    if low in _BY_ALIAS:
        return _BY_ALIAS[low]
    for m in MEMBERS:
        if low == m.telegram.lower():
            return m
    for m in MEMBERS:
        for p in m.all_patterns():
            if low in p.lower() or p.lower() in low:
                return m
    return None


def resolve_member_from_text(text: str) -> ChatMember | None:
    blob = text.lower()
    best: ChatMember | None = None
    best_len = 0
    for m in MEMBERS:
        for p in m.all_patterns():
            pl = p.lower()
            if len(pl) < 3:
                continue
            if re.search(rf"(?<!\w){re.escape(pl)}(?!\w)", blob):
                if len(pl) > best_len:
                    best = m
                    best_len = len(pl)
    return best


def expand_search_patterns(query: str) -> list[str]:
    m = resolve_member(query)
    if m:
        return m.all_patterns()
    q = query.strip().lstrip("@")
    return [q] if q else []


def format_member_bullet(member: ChatMember, *, msg_count: int | None = None) -> str:
    aliases = ", ".join(
        a for a in member.all_patterns() if a.lower() != member.telegram.lower()
    )
    line = f"🔹 **{member.label}** — `@{member.telegram}`"
    if aliases:
        line += f"\n   🏷 {aliases}"
    if msg_count is not None:
        line += f"\n   💬 {msg_count} сообщ."
    return line


def roster_summary_markdown() -> str:
    lines = ["🐷 **Наши в чате** (ник → имя)\n"]
    for m in MEMBERS:
        lines.append(format_member_bullet(m) + "\n")
    return "\n".join(lines)
