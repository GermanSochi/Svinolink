from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime

from config import settings


class GameStore:
  def __init__(self) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    self._db = settings.data_dir / "game.db"
    self._init()

  def _init(self) -> None:
    with sqlite3.connect(self._db) as conn:
      conn.execute(
        """
        CREATE TABLE IF NOT EXISTS riddle (
          chat_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL,
          question TEXT NOT NULL,
          answer TEXT NOT NULL,
          solved INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (chat_id, user_id)
        )
        """
      )
      conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_quota (
          chat_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL,
          hour_key TEXT NOT NULL,
          cnt INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (chat_id, user_id, hour_key)
        )
        """
      )

  def _hour_key(self) -> str:
    return datetime.now().strftime("%Y%m%d%H")

  def set_riddle(self, chat_id: int, user_id: int, question: str, answer: str) -> None:
    with sqlite3.connect(self._db) as conn:
      conn.execute(
        """
        INSERT INTO riddle (chat_id, user_id, question, answer, solved)
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
          question=excluded.question,
          answer=excluded.answer,
          solved=0
        """,
        (chat_id, user_id, question, answer),
      )

  def get_riddle(self, chat_id: int, user_id: int) -> tuple[str, str, bool] | None:
    with sqlite3.connect(self._db) as conn:
      row = conn.execute(
        "SELECT question, answer, solved FROM riddle WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
      ).fetchone()
    if not row:
      return None
    return row[0], row[1], bool(row[2])

  def mark_solved(self, chat_id: int, user_id: int) -> None:
    with sqlite3.connect(self._db) as conn:
      conn.execute(
        "UPDATE riddle SET solved=1 WHERE chat_id=? AND user_id=?",
        (chat_id, user_id),
      )

  def is_unlocked(self, chat_id: int, user_id: int) -> bool:
    r = self.get_riddle(chat_id, user_id)
    return bool(r and r[2])

  def questions_left(self, chat_id: int, user_id: int, *, limit: int = 2) -> int:
    if not self.is_unlocked(chat_id, user_id):
      return 0
    hk = self._hour_key()
    with sqlite3.connect(self._db) as conn:
      row = conn.execute(
        "SELECT cnt FROM ai_quota WHERE chat_id=? AND user_id=? AND hour_key=?",
        (chat_id, user_id, hk),
      ).fetchone()
    used = int(row[0]) if row else 0
    return max(0, limit - used)

  def use_question(self, chat_id: int, user_id: int) -> bool:
    if self.questions_left(chat_id, user_id) <= 0:
      return False
    hk = self._hour_key()
    with sqlite3.connect(self._db) as conn:
      conn.execute(
        """
        INSERT INTO ai_quota (chat_id, user_id, hour_key, cnt)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(chat_id, user_id, hour_key) DO UPDATE SET cnt = cnt + 1
        """,
        (chat_id, user_id, hk),
      )
    return True

  @staticmethod
  def normalize_answer(text: str) -> str:
    t = text.strip().lower()
    t = re.sub(r"[^\wа-яё\s-]", "", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()
