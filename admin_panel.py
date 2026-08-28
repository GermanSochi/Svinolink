from __future__ import annotations

import asyncio
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

@router.message(Command("wf_stats"), F.chat.type == "private")
async def cmd_wf_stats(message: Message) -> None:
    """Show watch feeder statistics."""
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    from watch_feeder import get_stats_summary
    await message.answer(get_stats_summary())


@router.message(Command("wf_run"), F.chat.type == "private")
async def cmd_wf_run(message: Message) -> None:
    """Immediately post best reels from cache or built-in seed (silent)."""
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    from watch_feeder import (
        _pop_top_candidates, _load_candidates, _add_candidates,
        _post_single, _load_posted, _mark_posted,
        _record_cycle, settings as wf_settings,
    )

    if not wf_settings.watch_feeder_enabled:
        await message.answer("⚠️ Watch Feeder выключен")
        return

    cache = _load_candidates()
    if cache:
        items = _pop_top_candidates(wf_settings.wf_posts_per_hour)
    else:
        # DuckDuckGo discovery
        try:
            from watch_discovery import search_reels
            results = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, lambda: search_reels(max_queries=4, query_timeout=10)
                ),
                timeout=60,
            )
            _add_candidates(results)
            items = _pop_top_candidates(wf_settings.wf_posts_per_hour)
        except Exception:
            results = []

        if not items:
            # Fallback: built-in seed
            from watch_feeder import _seed_cache_if_empty
            _seed_cache_if_empty()
            items = _pop_top_candidates(wf_settings.wf_posts_per_hour)

    if not items:
        await message.answer("❌ Нет кандидатов")
        return

    bot = message.bot
    chat_ids = wf_settings.watch_feeder_chat_ids
    posted = _load_posted()
    sent = 0
    errors = 0

    for i, item in enumerate(items):
        sc = item.get("shortcode", "")
        if not sc or sc in posted:
            continue
        ok = await _post_single(bot, chat_ids, item)
        if ok:
            _mark_posted(posted, sc)
            sent += 1
            logger.info("wf_run: [%d/%d] posted %s", i + 1, len(items), sc)
        else:
            errors += 1

    _record_cycle(sent, errors)
    # Minimal confirmation — no verbose stats
    await message.answer(f"✅ {sent} видео")
    logger.info("wf_run: sent=%d errors=%d", sent, errors)


@router.message(Command("wf"), F.chat.type == "private")
async def cmd_wf(message: Message) -> None:
    """Post a reel: /wf (status) or /wf keyword (best reel by keyword via DuckDuckGo)."""
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    from watch_feeder import (
        _post_single, _load_posted, _mark_posted,
        _record_cycle, settings as wf_settings,
    )

    if not wf_settings.watch_feeder_enabled:
        await message.answer("⚠️ Watch Feeder выключен")
        return

    # Parse: /wf or /wf seiko
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    keyword = parts[1].strip().lower() if len(parts) > 1 else ""

    if not keyword:
        # /wf without args → show status + help
        from watch_feeder import get_stats_summary
        await message.answer(
            f"{get_stats_summary()}\n"
            f"Window: {wf_settings.wf_post_start_hour:02d}:00–{wf_settings.wf_post_end_hour:02d}:00 MSK\n"
            f"Posts/hour: {wf_settings.wf_posts_per_hour}\n\n"
            "Команды:\n"
            "/wf_go — 🚀 быстрый пост (1 лучший reel)\n"
            "/wf_run — постить из кэша\n"
            "/wf rolex — лучший reel по бренду\n"
            "/wf_info — полная информация\n"
            "/wf_stats — статистика"
        )
        return

    # DuckDuckGo keyword search
    await message.answer(f"🔍 Ищу reel по «{keyword}» через DuckDuckGo…")
    try:
        from watch_discovery import search_reels
        loop = asyncio.get_event_loop()
        candidates = await loop.run_in_executor(
            None, lambda: search_reels(
                queries=[f"site:instagram.com/reel {keyword}"],
                max_results_per_query=15,
            )
        )
    except Exception as exc:
        await message.answer(f"❌ Ошибка: {exc}")
        return
    if not candidates:
        await message.answer(f"❌ Ничего не нашёл по «{keyword}»")
        return

    posted = _load_posted()
    fresh = [c for c in candidates if c["shortcode"] not in posted]
    if not fresh:
        await message.answer(f"❌ Все {len(candidates)} по «{keyword}» уже постились")
        return

    # Pick first (best by search rank)
    chosen = fresh[0]

    bot = message.bot
    chat_ids = wf_settings.watch_feeder_chat_ids

    ok = await _post_single(bot, chat_ids, chosen)
    if ok:
        _mark_posted(posted, chosen["shortcode"])
        _record_cycle(1, 0)
        sc = chosen["shortcode"]
        await message.answer(
            f"✅ Постнуто!\n"
            f"Reel: instagram.com/reel/{sc}/\n"
            f"Title: {chosen.get('title', '—')[:80]}"
        )
        logger.info("wf keyword post: %s", sc)
    else:
        _record_cycle(0, 1)
        await message.answer("❌ Не удалось постить")

@router.message(Command("wf_info"), F.chat.type == "private")
async def cmd_wf_info(message: Message) -> None:
    """Full watch feeder info: settings, stats, cache, discovery status."""
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    from watch_feeder import (
        get_stats_summary, _load_candidates, _load_posted,
        _load_accounts, settings as wf_settings,
    )

    posted = _load_posted()
    cache = _load_candidates()
    accounts = _load_accounts()
    stats_text = get_stats_summary()

    # Top 5 from cache
    top5 = sorted(cache, key=lambda x: x.get("score", 0), reverse=True)[:5]
    top5_lines = []
    for i, c in enumerate(top5, 1):
        sc = c.get("shortcode", "?")
        title = c.get("title", "")[:40]
        src = c.get("source", "?")
        top5_lines.append(f"  {i}. [{src}] {sc} — {title}")

    # Discovery source
    try:
        from watch_discovery import search_reels
        ddg_status = "✅ duckduckgo_search installed"
    except ImportError:
        try:
            from ddgs import DDGS
            ddg_status = "✅ ddgs installed"
        except ImportError:
            ddg_status = "❌ not installed"

    chat_ids = wf_settings.watch_feeder_chat_ids
    text = (
        f"📊 Watch Feeder — полная информация\n"
        f"{'═' * 35}\n\n"
        f"{stats_text}\n\n"
        f"⚙️ Settings:\n"
        f"  Enabled: {wf_settings.watch_feeder_enabled}\n"
        f"  Window: {wf_settings.wf_post_start_hour:02d}:00–{wf_settings.wf_post_end_hour:02d}:00 MSK\n"
        f"  Posts/hour: {wf_settings.wf_posts_per_hour}\n"
        f"  Interval: {wf_settings.watch_feeder_interval_hours}h\n"
        f"  Chats: {chat_ids}\n"
        f"  Accounts loaded: {len(accounts)}\n\n"
        f"🔍 Discovery:\n"
        f"  {ddg_status}\n\n"
        f"📦 Cache ({len(cache)} candidates, {len(posted)} posted):\n"
    )
    if top5_lines:
        text += "\n".join(top5_lines)
    else:
        text += "  (пусто)"

    await message.answer(text)


@router.message(Command("wf_go"), F.chat.type == "private")
async def cmd_wf_go(message: Message) -> None:
    """Quick post: find best reel and post immediately (silent)."""
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    from watch_feeder import (
        _post_single, _load_posted, _mark_posted,
        _record_cycle, settings as wf_settings,
    )

    if not wf_settings.watch_feeder_enabled:
        await message.answer("⚠️ Watch Feeder выключен")
        return

    # Try DDG
    try:
        from watch_discovery import search_reels
        loop = asyncio.get_event_loop()
        results = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: search_reels(max_queries=3, query_timeout=10)),
            timeout=45,
        )
    except Exception:
        results = []

    if not results:
        # Fallback: built-in seed
        from watch_feeder import _seed_cache_if_empty, _load_candidates
        _seed_cache_if_empty()
        results = _load_candidates()
        if not results:
            await message.answer("❌ Ничего не нашёл")
            return

    posted = _load_posted()
    fresh = [r for r in results if r["shortcode"] not in posted]
    if not fresh:
        await message.answer(f"❌ Все {len(results)} уже постились")
        return

    best = fresh[0]
    bot = message.bot
    chat_ids = wf_settings.watch_feeder_chat_ids

    ok = await _post_single(bot, chat_ids, best)
    if ok:
        _mark_posted(posted, best["shortcode"])
        _record_cycle(1, 0)
        # Silent — no verbose reply to admin
    else:
        _record_cycle(0, 1)
        await message.answer("❌ Не удалось отправить")

