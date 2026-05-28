from __future__ import annotations

import json
import logging

import aiohttp
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ai_quota import reset_user
from chat_handlers import is_admin_user
from config import settings

router = Router(name="admin_panel")
logger = logging.getLogger(__name__)

WAKE_CB = "admin:wake"


def _admin_kb() -> InlineKeyboardMarkup:
  return InlineKeyboardMarkup(
    inline_keyboard=[
      [InlineKeyboardButton(text="Разбудить сервер", callback_data=WAKE_CB)],
    ]
  )


def _health_url() -> str:
  base = settings.app_base_url.strip()
  if base:
    return f"{base}/health"
  return "https://svinolink.onrender.com/health"


@router.message(F.chat.type == "private", F.text.lower().in_({"админ", "панель", "admin"}))
async def admin_panel_text(message: Message) -> None:
  if not message.from_user or not is_admin_user(message.from_user.id, message.from_user.username):
    return
  await message.answer(
    "Панель Свина.\n\n"
    "Кнопка ниже не пишет в чат — просто пингует /health, чтобы разбудить Render.",
    reply_markup=_admin_kb(),
  )


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
    "/admin_broadcast текст — только в личку тебе (тест)",
    reply_markup=_admin_kb(),
  )


@router.callback_query(F.data == WAKE_CB)
async def wake_callback(call: CallbackQuery) -> None:
  user = call.from_user
  if not user or not is_admin_user(user.id, user.username):
    await call.answer("Не для тебя.", show_alert=True)
    return

  url = _health_url()
  await call.answer("Будим…", show_alert=False)

  try:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as s:
      async with s.get(url) as resp:
        raw = await resp.text()
        body = {}
        try:
          body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
          body = {"raw": raw[:200]}
        ok = resp.status == 200
        status = "ok" if ok else f"HTTP {resp.status}"
  except Exception as exc:
    logger.warning("wake failed: %s", exc)
    await call.message.answer("Сервер не поднялся с первого пинка. Повтори кнопку через 20–40 секунд.")
    return

  version = str(body.get("version", "")).strip() if isinstance(body, dict) else ""
  msg = f"Сервер пнут: {status}."
  if version:
    msg += f"\nВерсия: {version}"
  await call.message.answer(msg)


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
