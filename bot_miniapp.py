from __future__ import annotations

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo

from config import settings


def miniapp_url_for_chat(chat_id: int | None = None) -> str | None:
    base = settings.miniapp_url
    if not base:
        return None
    if chat_id is not None:
        return f"{base}?chat_id={chat_id}"
    return base


def miniapp_keyboard(chat_id: int | None = None) -> InlineKeyboardMarkup | None:
    url = miniapp_url_for_chat(chat_id)
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙️ Тригеры",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


async def setup_menu_webapp(bot: Bot) -> None:
    url = settings.miniapp_url
    if not url:
        return
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="⚙️ Тригеры", web_app=WebAppInfo(url=url))
    )
