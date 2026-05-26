from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from chat_handlers import is_admin_user
from deps import store
from game_store import GameStore

router = Router(name="admin_panel")
game = GameStore()


@router.message(Command("admin"), F.chat.type == "private")
async def cmd_admin(message: Message) -> None:
  if not message.from_user or not is_admin_user(
    message.from_user.id, message.from_user.username
  ):
    return
  await message.answer(
    "Панель GERSOCHI\n\n"
    "/admin_stats — статистика\n"
    "/admin_reset USER_ID — сброс загадки игрока\n"
    "/admin_broadcast текст — только в личку тебе (тест)"
  )


@router.message(Command("admin_stats"), F.chat.type == "private")
async def cmd_admin_stats(message: Message) -> None:
  if not message.from_user or not is_admin_user(
    message.from_user.id, message.from_user.username
  ):
    return
  import sqlite3
  from config import settings

  db = settings.data_dir / "game.db"
  solved = 0
  if db.is_file():
    with sqlite3.connect(db) as conn:
      row = conn.execute("SELECT COUNT(*) FROM riddle WHERE solved=1").fetchone()
      solved = int(row[0]) if row else 0
  await message.answer(f"Разгадали загадку: {solved} чел.\nТригеры: см. /тригеры")


@router.message(Command("admin_reset"), F.chat.type == "private")
async def cmd_admin_reset(message: Message) -> None:
  if not message.from_user or not is_admin_user(
    message.from_user.id, message.from_user.username
  ):
    return
  parts = (message.text or "").split()
  if len(parts) < 2 or not parts[1].isdigit():
    await message.answer("/admin_reset TELEGRAM_USER_ID")
    return
  uid = int(parts[1])
  import sqlite3
  from config import settings

  with sqlite3.connect(settings.data_dir / "game.db") as conn:
    conn.execute("DELETE FROM riddle WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM ai_quota WHERE user_id=?", (uid,))
  await message.answer(f"Сброшено для user_id {uid}")
