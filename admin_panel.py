from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from admin_auth import is_admin_user
from config import settings

router = Router(name="admin_panel")
logger = logging.getLogger(__name__)

WAKE_CB = "admin:wake"


def _admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="\u0420\u0430\u0437\u0431\u0443\u0434\u0438\u0442\u044c \u0441\u0435\u0440\u0432\u0435\u0440", callback_data=WAKE_CB)],
        ]
    )


def _health_url() -> str:
    base = settings.app_base_url.strip()
    if base:
        return f"{base}/health"
    return "https://svinolink.onrender.com/health"


@router.message(F.chat.type == "private", F.text.lower().in_({"\u0430\u0434\u043c\u0438\u043d", "\u043f\u0430\u043d\u0435\u043b\u044c", "admin"}))
async def admin_panel_text(message: Message) -> None:
    if not message.from_user or not is_admin_user(message.from_user.id, message.from_user.username):
        return
    await message.answer(
        "\u041f\u0430\u043d\u0435\u043b\u044c \u0421\u0432\u0438\u043d\u0430.\n\n"
        "\u041a\u043d\u043e\u043f\u043a\u0430 \u043d\u0438\u0436\u0435 \u043f\u0438\u043d\u0433\u0443\u0435\u0442 /health.",
        reply_markup=_admin_kb(),
    )


@router.message(Command("admin"), F.chat.type == "private")
async def cmd_admin(message: Message) -> None:
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    await message.answer(
        "\u041f\u0430\u043d\u0435\u043b\u044c GERSOCHI\n\n"
        "/admin_stats \u2014 \u0441\u0442\u0430\u0442\u0438\u0441\u0442\u0438\u043a\u0430\n"
        "/admin_broadcast \u0442\u0435\u043a\u0441\u0442 \u2014 \u0442\u0435\u0441\u0442 \u0432 \u043b\u0438\u0447\u043a\u0443",
        reply_markup=_admin_kb(),
    )


@router.callback_query(F.data == WAKE_CB)
async def wake_callback(call: CallbackQuery) -> None:
    user = call.from_user
    if not user or not is_admin_user(user.id, user.username):
        await call.answer("\u041d\u0435 \u0434\u043b\u044f \u0442\u0435\u0431\u044f.", show_alert=True)
        return

    url = _health_url()
    await call.answer("\u0411\u0443\u0434\u0438\u043c\u2026", show_alert=False)

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
        await call.message.answer("\u0421\u0435\u0440\u0432\u0435\u0440 \u043d\u0435 \u043f\u043e\u0434\u043d\u044f\u043b\u0441\u044f. \u041f\u043e\u0432\u0442\u043e\u0440\u0438 \u0447\u0435\u0440\u0435\u0437 20\u201340 \u0441\u0435\u043a\u0443\u043d\u0434.")
        return

    version = str(body.get("version", "")).strip() if isinstance(body, dict) else ""
    msg = f"\u0421\u0435\u0440\u0432\u0435\u0440 \u043f\u043d\u0443\u0442: {status}."
    if version:
        msg += f"\n\u0412\u0435\u0440\u0441\u0438\u044f: {version}"
    await call.message.answer(msg)


@router.message(Command("admin_stats"), F.chat.type == "private")
async def cmd_admin_stats(message: Message) -> None:
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    ig_status = "активен" if settings.instagram_is_active() else "выключен"
    ig_paused = "да" if settings.instagram_paused else "нет"
    await message.answer(
        "Статус:\n"
        f"Версия: {settings.app_version}\n"
        f"Инстаграм: {ig_status}\n"
        f"Пауза IG: {ig_paused}"
    )


@router.message(Command("admin_broadcast"), F.chat.type == "private")
async def cmd_admin_broadcast(message: Message) -> None:
    if not message.from_user or not is_admin_user(
        message.from_user.id, message.from_user.username
    ):
        return
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer("\u0423\u043a\u0430\u0436\u0438 \u0442\u0435\u043a\u0441\u0442: /admin_broadcast \u0442\u0435\u043a\u0441\u0442")
        return
    await message.answer(f"\u041f\u043e\u043b\u0443\u0447\u0435\u043d\u043e: {parts[1]}")

WAKE_CB = "admin:wake"


