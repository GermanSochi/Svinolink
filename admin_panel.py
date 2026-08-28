from __future__ import annotations

import json
import logging

import aiohttp
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ai_quota import reset_user
from admin_auth import is_admin_user
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
    "/admin_broadcast текст — только в личку тебе (тест)\n\n"
    "🎭 **Тон в групповом чате** (только админ, прямо в группе):\n"
    "🔹 `Свин уровень юмора 40 процентов`\n"
    "🔹 `Свин уровень токсичности 25 процентов`\n"
    "🔹 `Свин уровни` — текущие значения\n\n"
    "Шкала **1–100**. Юмор и подкол настраиваются отдельно.",
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


# ── Watch Feeder admin commands ──────────────────────────────

@router.message(Command("wf"), F.chat.type == "private")
async def cmd_wf(message: Message) -> None:
    """Watch Feeder status + help."""
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    from watch_feeder import get_stats_summary, settings as wf_settings
    enabled = wf_settings.watch_feeder_enabled
    chats = wf_settings.watch_feeder_chat_ids
    interval = wf_settings.watch_feeder_interval_hours
    status = "ON" if enabled else "OFF"
    await message.answer(
        f"{get_stats_summary()}\n"
        f"Status: {status}\n"
        f"Chat IDs: {chats}\n"
        f"Interval: {interval}h\n\n"
        "/wf_run — запустить цикл сейчас\n"
        "/wf_add URL — добавить ссылку в очередь\n"
        "/wf_queue — показать очередь\n"
        "/wf_stats — статистика\n"
        "/wf_clear — очистить очередь"
    )


@router.message(Command("wf_add"), F.chat.type == "private")
async def cmd_wf_add(message: Message) -> None:
    """Add Instagram reel URL(s) to the watch feeder queue."""
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    import re as _re
    from watch_feeder import add_to_queue
    text = message.text or ""
    urls = _re.findall(
        r"https?://(?:www\.)?instagram\.com/reel/[A-Za-z0-9_-]+", text
    )
    if not urls:
        await message.answer("Пришли ссылку на Instagram reel:\n/wf_add https://instagram.com/reel/XXXX/")
        return
    shortcodes = [
        _re.search(r"/reel/([A-Za-z0-9_-]+)", u).group(1)
        for u in urls
        if _re.search(r"/reel/([A-Za-z0-9_-]+)", u)
    ]
    added = add_to_queue(shortcodes, source="admin")
    await message.answer(f"Добавлено в очередь: {added} из {len(shortcodes)}")


@router.message(Command("wf_queue"), F.chat.type == "private")
async def cmd_wf_queue(message: Message) -> None:
    """Show current watch feeder queue."""
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    from watch_feeder import load_queue
    queue = load_queue()
    if not queue:
        await message.answer("Очередь пуста")
        return
    lines = []
    for i, entry in enumerate(queue[:20], 1):
        sc = entry.get("shortcode", "?")
        src = entry.get("source", "?")
        lines.append(f"{i}. {sc} ({src})")
    if len(queue) > 20:
        lines.append(f"... и ещё {len(queue) - 20}")
    await message.answer(f"Очередь ({len(queue)}):\n" + "\n".join(lines))


@router.message(Command("wf_stats"), F.chat.type == "private")
async def cmd_wf_stats(message: Message) -> None:
    """Show watch feeder statistics."""
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    from watch_feeder import get_stats_summary
    await message.answer(get_stats_summary())


@router.message(Command("wf_clear"), F.chat.type == "private")
async def cmd_wf_clear(message: Message) -> None:
    """Clear the watch feeder queue."""
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    from watch_feeder import save_queue
    save_queue([])
    await message.answer("Очередь очищена")


@router.message(Command("wf_run"), F.chat.type == "private")
async def cmd_wf_run(message: Message) -> None:
    """Immediately run one watch feeder cycle (2 posts from queue)."""
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    from watch_feeder import (
        load_queue, pop_queue_batch, _process_items, _record_cycle,
        _notify_admin, settings as wf_settings,
    )
    from instagram_download import instagram_is_active_check

    if not wf_settings.watch_feeder_enabled:
        await message.answer("⚠️ Watch Feeder выключен (WATCH_FEEDER_ENABLED=0)")
        return
    if not wf_settings.watch_feeder_chat_ids:
        await message.answer("⚠️ Нет chat IDs (WATCH_FEEDER_CHAT_IDS)")
        return
    if not instagram_is_active_check():
        await message.answer("⚠️ Instagram неактивен (нет cookies/сессии)")
        return

    queue = load_queue()
    await message.answer(
        f"🔄 Запускаю тестовый цикл...\n"
        f"Очередь: {len(queue)} | Чаты: {wf_settings.watch_feeder_chat_ids}"
    )

    batch = pop_queue_batch(max_items=2)
    if not batch:
        await message.answer("❌ Очередь пуста!")
        return

    items = [
        {"shortcode": e["shortcode"], "username": "", "likes": 0,
         "comments": 0, "views": 0}
        for e in batch
    ]

    bot = message.bot
    chat_ids = wf_settings.watch_feeder_chat_ids
    sent, errors = await _process_items(bot, chat_ids, items)
    queue_left = len(load_queue())
    _record_cycle(sent, errors)

    result = f"✅ Готово!\nОтправлено: {sent}\nОшибки: {errors}\nОсталось в очереди: {queue_left}"
    await message.answer(result)
    logger.info("wf_run: sent=%d errors=%d queue_left=%d", sent, errors, queue_left)
