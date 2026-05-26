from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from config import settings


@dataclass
class TriggerRule:
    id: str
    words: list[str]
    response: str
    once_per_day: bool
    match: str  # exact | contains
    builtin: bool = False


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
                )
            )
        return out

    def load_defaults(self) -> list[TriggerRule]:
        return self._load_json_rules(settings.triggers_file, builtin=True)

    def load_custom(self, chat_id: int) -> list[TriggerRule]:
        return self._load_json_rules(self._chat_path(chat_id), builtin=False)

    def load_triggers(self, chat_id: int) -> list[TriggerRule]:
        return self.load_defaults() + self.load_custom(chat_id)

    def save_custom(self, chat_id: int, rules: list[TriggerRule]) -> None:
        payload = {
            "triggers": [
                {
                    "id": r.id,
                    "words": r.words,
                    "response": r.response,
                    "once_per_day": r.once_per_day,
                    "match": r.match,
                }
                for r in rules
            ]
        }
        self._chat_path(chat_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add_custom_rule(
        self,
        chat_id: int,
        word: str,
        response: str,
        *,
        once_per_day: bool = False,
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
                match="exact",
            )
        )
        self.save_custom(chat_id, rules)
        self.remember_style(chat_id, response.strip())
        return rule_id

    def update_custom_rule(
        self, chat_id: int, index: int, *, word: str | None, response: str | None
    ) -> bool:
        rules = self.load_custom(chat_id)
        if index < 0 or index >= len(rules):
            return False
        if word is not None:
            rules[index].words = [word.lower().strip()]
        if response is not None:
            rules[index].response = response.strip()
            self.remember_style(chat_id, response.strip())
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
                lines.append(f"{i}. [{w}] → {r.response}{d}")
        return lines

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
                elif normalized == word:
                    return rule
        return None
