from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from ai_quota import reset_user
from chat_handlers import is_admin_user

router = Router(name="admin_panel")


@router.message(Command("admin"), F.chat.type == "private")
async def cmd_admin(message: Message) -> None:
  if not message.from_user or not is_admin_user(
    message.from_user.id, message.from_user.username
  ):
    return
  await message.answer(
    "Панель GERSOCHI\n\n"
    "/admin_stats — статистика\n"
    "/admin_reset USER_ID — сброс ИИ-лимита «свин»\n"
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
  from ai_quota import HOURLY_LIMIT

  await message.answer(
    f"Разгадали загадку: {solved} чел.\n"
    f"ИИ «свин»: {HOURLY_LIMIT} вопросов/час на человека."
  )


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
  reset_user(uid)
  await message.answer(f"Сброшен ИИ-лимит «свин» для user_id {uid}")
