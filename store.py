from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class TriggerRule:
    id: str
    words: list[str]
    response: str
    once_per_day: bool
    match: str  # exact | contains | word
    builtin: bool = False
    added_by_user_id: int | None = None
    added_by_username: str | None = None


class TriggerStore:
    def __init__(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "chats").mkdir(parents=True, exist_ok=True)
        self._db = settings.data_dir / "svinolink.db"
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trigger_daily (
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    trigger_id TEXT NOT NULL,
                    used_on TEXT NOT NULL,
                    PRIMARY KEY (chat_id, user_id, trigger_id, used_on)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS known_chats (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    chat_type TEXT NOT NULL DEFAULT 'group',
                    active INTEGER NOT NULL DEFAULT 1,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    added_by_user_id INTEGER
                )
                """
            )
            self._ensure_chat_columns(conn)
        self._sync_chats_from_disk()

    def _ensure_chat_columns(self, conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(known_chats)")}
        if "added_by_user_id" not in cols:
            conn.execute(
                "ALTER TABLE known_chats ADD COLUMN added_by_user_id INTEGER"
            )

    def _chat_path(self, chat_id: int) -> Path:
        return settings.data_dir / "chats" / f"{chat_id}.json"

    def _style_path(self, chat_id: int) -> Path:
        return settings.data_dir / "chats" / f"{chat_id}_style.jsonl"

    def _load_json_rules(self, path: Path, *, builtin: bool) -> list[TriggerRule]:
        if not path.is_file():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
        out: list[TriggerRule] = []
        for item in raw.get("triggers", []):
            out.append(
                TriggerRule(
                    id=str(item["id"]),
                    words=[w.lower() for w in item.get("words", [])],
                    response=str(item["response"]),
                    once_per_day=bool(item.get("once_per_day", False)),
                    match=str(item.get("match", "exact")),
                    builtin=builtin,
                    added_by_user_id=item.get("added_by_user_id"),
                    added_by_username=item.get("added_by_username"),
                )
            )
        return out

    def load_defaults(self) -> list[TriggerRule]:
        return self._load_json_rules(settings.triggers_file, builtin=True)

    def load_custom(self, chat_id: int) -> list[TriggerRule]:
        if self._supabase_enabled():
            try:
                from trigger_supabase import (
                    load_custom_triggers,
                    run_async,
                    save_custom_triggers,
                )

                rules = run_async(load_custom_triggers(chat_id))
                if rules:
                    return rules
                file_rules = self._load_json_rules(
                    self._chat_path(chat_id), builtin=False
                )
                if file_rules:
                    run_async(save_custom_triggers(chat_id, file_rules))
                    return file_rules
                return []
            except Exception as exc:
                logger.warning(
                    "load_custom supabase chat=%s: %s", chat_id, exc
                )
        return self._load_json_rules(self._chat_path(chat_id), builtin=False)

    def load_triggers(self, chat_id: int) -> list[TriggerRule]:
        return self.load_defaults() + self.load_custom(chat_id)

    def _supabase_enabled(self) -> bool:
        from chat_memory import is_memory_enabled

        return is_memory_enabled()

    def save_custom(self, chat_id: int, rules: list[TriggerRule]) -> None:
        payload = {
            "triggers": [
                {
                    "id": r.id,
                    "words": r.words,
                    "response": r.response,
                    "once_per_day": r.once_per_day,
                    "match": r.match,
                    "added_by_user_id": r.added_by_user_id,
                    "added_by_username": r.added_by_username,
                }
                for r in rules
            ]
        }
        self._chat_path(chat_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.register_chat(chat_id)
        if self._supabase_enabled():
            try:
                from trigger_supabase import run_async, save_custom_triggers

                run_async(save_custom_triggers(chat_id, rules))
            except Exception as exc:
                logger.warning(
                    "save_custom supabase chat=%s: %s", chat_id, exc
                )

    def add_custom_rule(
        self,
        chat_id: int,
        word: str,
        response: str,
        *,
        once_per_day: bool = False,
        added_by_user_id: int | None = None,
        added_by_username: str | None = None,
        match: str = "exact",
    ) -> str:
        rules = self.load_custom(chat_id)
        safe = re.sub(r"[^a-z0-9_-]", "", word.lower())[:16] or "w"
        rule_id = f"t-{int(time.time())}-{safe}"
        rules.append(
            TriggerRule(
                id=rule_id,
                words=[word.lower().strip()],
                response=response.strip(),
                once_per_day=once_per_day,
                match=match,
                added_by_user_id=added_by_user_id,
                added_by_username=added_by_username,
            )
        )
        self.save_custom(chat_id, rules)
        self.remember_style(chat_id, response.strip())
        return rule_id

    def update_custom_rule(
        self,
        chat_id: int,
        index: int,
        *,
        word: str | None,
        response: str | None,
        match: str | None = None,
    ) -> bool:
        rules = self.load_custom(chat_id)
        if index < 0 or index >= len(rules):
            return False
        if word is not None:
            rules[index].words = [word.lower().strip()]
        if response is not None:
            rules[index].response = response.strip()
            self.remember_style(chat_id, response.strip())
        if match is not None:
            rules[index].match = match
        self.save_custom(chat_id, rules)
        return True

    def delete_custom_by_indices(self, chat_id: int, indices: list[int]) -> int:
        rules = self.load_custom(chat_id)
        to_drop = {i for i in indices if 0 <= i < len(rules)}
        if not to_drop:
            return 0
        kept = [r for i, r in enumerate(rules) if i not in to_drop]
        self.save_custom(chat_id, kept)
        return len(to_drop)

    def list_numbered(self, chat_id: int) -> list[tuple[int, TriggerRule]]:
        """Нумерация только пользовательских (не встроенных)."""
        return list(enumerate(self.load_custom(chat_id)))

    def all_numbered_for_display(self, chat_id: int) -> list[str]:
        lines: list[str] = []
        defaults = self.load_defaults()
        if defaults:
            lines.append("<b>Встроенные:</b>")
            for r in defaults:
                w = ", ".join(r.words)
                d = " · 1/день" if r.once_per_day else ""
                lines.append(f"• [{w}] → {r.response}{d}")
        custom = self.load_custom(chat_id)
        if custom:
            lines.append("\n<b>Ваши (номер для удаления/правки):</b>")
            for i, r in enumerate(custom, start=1):
                w = ", ".join(r.words)
                d = " · 1/день" if r.once_per_day else ""
                who = ""
                if r.added_by_username:
                    who = f" · @{r.added_by_username.lstrip('@')}"
                elif r.added_by_user_id:
                    who = f" · id{r.added_by_user_id}"
                lines.append(f"{i}. [{w}] → {r.response}{d}{who}")
        return lines

    def register_chat(
        self,
        chat_id: int,
        title: str | None = None,
        chat_type: str = "group",
        *,
        active: bool = True,
        added_by_user_id: int | None = None,
    ) -> None:
        now = time.time()
        safe_title = (title or "").strip()
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """
                INSERT INTO known_chats (
                    chat_id, title, chat_type, active, first_seen, last_seen, added_by_user_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title=CASE
                        WHEN excluded.title != '' THEN excluded.title
                        ELSE known_chats.title
                    END,
                    chat_type=excluded.chat_type,
                    active=excluded.active,
                    last_seen=excluded.last_seen,
                    added_by_user_id=COALESCE(
                        excluded.added_by_user_id,
                        known_chats.added_by_user_id
                    )
                """,
                (
                    chat_id,
                    safe_title,
                    chat_type,
                    1 if active else 0,
                    now,
                    now,
                    added_by_user_id,
                ),
            )

    def deactivate_chat(self, chat_id: int) -> None:
        now = time.time()
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "UPDATE known_chats SET active=0, last_seen=? WHERE chat_id=?",
                (now, chat_id),
            )

    def remove_chat_from_miniapp(self, chat_id: int) -> None:
        """Убрать группу из Mini App и удалить все кастомные триггеры."""
        self.deactivate_chat(chat_id)
        path = self._chat_path(chat_id)
        if path.is_file():
            path.unlink()
        if self._supabase_enabled():
            try:
                from trigger_supabase import delete_custom_triggers, run_async

                run_async(delete_custom_triggers(chat_id))
            except Exception as exc:
                logger.warning(
                    "delete triggers supabase chat=%s: %s", chat_id, exc
                )

    def get_chat_title(self, chat_id: int) -> str:
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                "SELECT title FROM known_chats WHERE chat_id=?",
                (chat_id,),
            ).fetchone()
        return str(row[0]) if row and row[0] else ""

    def list_active_chats(self) -> list[dict]:
        with sqlite3.connect(self._db) as conn:
            rows = conn.execute(
                """
                SELECT chat_id, title, chat_type, added_by_user_id
                FROM known_chats
                WHERE active=1
                ORDER BY last_seen DESC
                """
            ).fetchall()
        return [
            {
                "chat_id": int(r[0]),
                "title": str(r[1] or ""),
                "chat_type": str(r[2]),
                "added_by_user_id": int(r[3]) if r[3] is not None else None,
            }
            for r in rows
        ]

    def chat_trigger_summary(self, chat_id: int) -> str:
        custom = self.load_custom(chat_id)
        builtin_n = len(self.load_defaults())
        if not custom:
            return f"системных {builtin_n}, своих нет"
        return f"системных {builtin_n}, своих {len(custom)}"

    def _sync_chats_from_disk(self) -> None:
        chats_dir = settings.data_dir / "chats"
        if not chats_dir.is_dir():
            return
        for path in chats_dir.glob("*.json"):
            stem = path.stem
            if not stem.lstrip("-").isdigit():
                continue
            self.register_chat(int(stem), title=f"Чат {stem}")

    def remember_style(self, chat_id: int, phrase: str) -> None:
        path = self._style_path(chat_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "p": phrase}, ensure_ascii=False) + "\n")
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > 30:
            path.write_text("\n".join(lines[-30:]) + "\n", encoding="utf-8")

    def style_hints(self, chat_id: int, limit: int = 8) -> list[str]:
        path = self._style_path(chat_id)
        if not path.is_file():
            return []
        hints: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                hints.append(json.loads(line)["p"])
            except (json.JSONDecodeError, KeyError):
                continue
        return hints

    def was_used_today(self, chat_id: int, user_id: int, trigger_id: str) -> bool:
        if self._supabase_enabled():
            try:
                from trigger_supabase import run_async, was_trigger_used_today

                return run_async(
                    was_trigger_used_today(chat_id, user_id, trigger_id)
                )
            except Exception as exc:
                logger.warning("was_used_today supabase: %s", exc)
        today = date.today().isoformat()
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM trigger_daily
                WHERE chat_id=? AND user_id=? AND trigger_id=? AND used_on=?
                """,
                (chat_id, user_id, trigger_id, today),
            ).fetchone()
        return row is not None

    def mark_used_today(self, chat_id: int, user_id: int, trigger_id: str) -> None:
        if self._supabase_enabled():
            try:
                from trigger_supabase import mark_trigger_used_today, run_async

                run_async(mark_trigger_used_today(chat_id, user_id, trigger_id))
                return
            except Exception as exc:
                logger.warning("mark_used_today supabase: %s", exc)
        today = date.today().isoformat()
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO trigger_daily (chat_id, user_id, trigger_id, used_on)
                VALUES (?, ?, ?, ?)
                """,
                (chat_id, user_id, trigger_id, today),
            )

    def find_match(self, text: str, rules: list[TriggerRule]) -> TriggerRule | None:
        normalized = text.strip().lower()
        if not normalized:
            return None
        for rule in rules:
            for word in rule.words:
                if rule.match == "contains":
                    if word in normalized:
                        return rule
                elif rule.match == "word":
                    # Токен-матчинг: "сегодня ... да" сработает, а "правда" — нет.
                    # Граница слова по \w (unicode): подходит для кириллицы.
                    import re

                    pat = rf"(?<!\w){re.escape(word)}(?!\w)"
                    if re.search(pat, normalized, flags=re.UNICODE):
                        return rule
                elif normalized == word:
                    return rule
        return None

    @staticmethod
    def _format_rule_line(rule: TriggerRule) -> str:
        words = ", ".join(rule.words)
        match_label = (
            "содержит"
            if rule.match == "contains"
            else ("слово" if rule.match == "word" else "точное")
        )
        daily = " · 1/день" if rule.once_per_day else ""
        who = ""
        if rule.added_by_username:
            who = f" · @{rule.added_by_username.lstrip('@')}"
        return f"[{words}] ({match_label}) → {rule.response}{daily}{who}"

    def triggers_summary_text(self, chat_id: int) -> str:
        """Текстовая выжимка триггеров для промпта GPT."""
        defaults = self.load_defaults()
        custom = self.load_custom(chat_id)
        lines: list[str] = []
        if defaults:
            lines.append(f"Встроенные ({len(defaults)}):")
            for rule in defaults:
                lines.append(f"  • {self._format_rule_line(rule)}")
        if custom:
            lines.append(f"Кастомные в Supabase ({len(custom)}):")
            for rule in custom:
                lines.append(f"  • {self._format_rule_line(rule)}")
        if not lines:
            return "Нет сохранённых триггеров — только реакция на «Свин» и reply."
        return "\n".join(lines)

    def triggers_list_markdown(self, chat_id: int) -> str:
        """Ответ пользователю: список триггеров из Supabase (без GPT)."""
        defaults = self.load_defaults()
        custom = self.load_custom(chat_id)
        lines: list[str] = [
            "🐷 **Триггеры этой группы** (Supabase + встроенные)\n"
        ]

        if defaults:
            lines.append(f"⚙️ **Встроенные** — {len(defaults)}:\n")
            for rule in defaults:
                words = ", ".join(rule.words)
                daily = " · 1/день" if rule.once_per_day else ""
                match_label = "содержит" if rule.match == "contains" else "точно"
                if rule.match == "word":
                    match_label = "слово"
                lines.append(
                    f"🔹 `{words}` ({match_label}) → **{rule.response}**{daily}\n"
                )

        if custom:
            lines.append(f"\n💾 **Кастомные в Supabase** — {len(custom)}:\n")
            for rule in custom:
                words = ", ".join(rule.words)
                daily = " · 1/день" if rule.once_per_day else ""
                match_label = "содержит" if rule.match == "contains" else "точно"
                if rule.match == "word":
                    match_label = "слово"
                who = ""
                if rule.added_by_username:
                    who = f" · @{rule.added_by_username.lstrip('@')}"
                lines.append(
                    f"🎯 `{words}` ({match_label}) → **{rule.response}**{daily}{who}\n"
                )
        else:
            lines.append(
                "\n📭 Кастомных триггеров в Supabase пока нет — "
                "добавь через Mini App ⚙️ в меню бота.\n"
            )

        if not defaults and not custom:
            return (
                "🐷 В Supabase для этой группы триггеров пока нет.\n\n"
                "Добавь через Mini App ⚙️ — сохраню навсегда."
            )

        lines.append("\n✏️ Редактировать: Mini App ⚙️ в меню бота.")
        return "\n".join(lines)

    def triggers_list_html(self, chat_id: int) -> str:
        """Совместимость: отдаём Markdown."""
        return self.triggers_list_markdown(chat_id)
