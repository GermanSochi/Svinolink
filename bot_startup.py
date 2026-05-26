from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from config import settings

logger = logging.getLogger(__name__)


async def configure_bot(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="как пользоваться"),
            BotCommand(command="trigger", description="добавить триггер"),
            BotCommand(command="triggers", description="список триггеров"),
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
    if settings.webhook_base_url.strip():
        expected = settings.webhook_base_url.rstrip("/")
        if not info.url or not info.url.startswith(expected):
            logger.error(
                "Webhook НЕ настроен! url=%s ожидали %s — проверь Render env",
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
