from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from config import settings

logger = logging.getLogger(__name__)


async def configure_bot(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(
                command="triggers",
                description="Настройка и редактирование триггеров чата",
            ),
        ]
    )

    url = settings.miniapp_url
    if url:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="⚙️ Тригеры", web_app=WebAppInfo(url=url))
            )
            logger.info("Menu WebApp: %s", url)
        except Exception as exc:
            logger.warning("Menu WebApp failed: %s", exc)
    else:
        logger.warning(
            "Mini App выключен: задай WEBHOOK_BASE_URL или PUBLIC_BASE_URL (HTTPS)"
        )

    info = await bot.get_webhook_info()
    expected = settings.webhook_full_url
    if expected:
        if info.url != expected:
            logger.error(
                "Webhook mismatch: got=%s expected=%s",
                info.url,
                expected,
            )
        else:
            logger.info("Webhook OK: %s pending=%s", info.url, info.pending_update_count)
    elif info.url:
        logger.warning(
            "На боте висит webhook %s — локальный polling может не получать сообщения",
            info.url,
        )
