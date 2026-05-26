from __future__ import annotations

import json
import sqlite3
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


class TriggerStore:
    def __init__(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
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

    def load_triggers(self) -> list[TriggerRule]:
        path = settings.triggers_file
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
                )
            )
        return out

    def save_triggers(self, rules: list[TriggerRule]) -> None:
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
        settings.triggers_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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

    def add_rule(self, word: str, response: str, *, once_per_day: bool) -> str:
        rules = self.load_triggers()
        rule_id = f"custom-{word.lower()[:20]}"
        rules.append(
            TriggerRule(
                id=rule_id,
                words=[word.lower()],
                response=response,
                once_per_day=once_per_day,
                match="exact",
            )
        )
        self.save_triggers(rules)
        return rule_id
