from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from bot_startup import configure_bot
from config import settings
from instagram_download import init_instagram_downloader

logger = logging.getLogger(__name__)

SELF_PING_INTERVAL = 480  # 8 минут — Render таймаут 15 мин


async def apply_webhook(bot: Bot) -> str:
    url = settings.webhook_full_url
    if not url:
        raise RuntimeError("WEBHOOK_BASE_URL не задан — нельзя установить webhook")
    await bot.set_webhook(
        url=url,
        drop_pending_updates=True,
        allowed_updates=["message", "edited_message", "channel_post", "callback_query", "my_chat_member"],
    )
    info = await bot.get_webhook_info()
    logger.info(
        "WEBHOOK SET url=%s pending=%s last_error=%s",
        info.url,
        info.pending_update_count,
        info.last_error_message or "none",
    )
    if info.url != url:
        raise RuntimeError(f"webhook mismatch: got {info.url!r} expected {url!r}")
    return url


def build_app(bot: Bot, dp: Dispatcher, *, webhook: bool) -> web.Application:
    app = web.Application()

    async def health(_: web.Request) -> web.Response:
        payload = {
            "status": "ok",
            "bot": "svinolink",
            "version": settings.app_version,
            "mode": "webhook" if webhook else "polling",
            "instagram": "active" if settings.instagram_is_active() else "off",
        }
        return web.Response(
            text=json.dumps(payload, ensure_ascii=False),
            content_type="application/json",
        )

    app.router.add_get("/health", health)
    app.router.add_head("/health", health)

    async def on_startup(app_: web.Application) -> None:
        logger.info("on_startup: begin")
        await configure_bot(bot)
        init_instagram_downloader()
        logger.info("on_startup: done — bot ready")

    app.on_startup.append(on_startup)

    if webhook:
        route = settings.webhook_route
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=route)
        setup_application(app, dp, bot=bot)

        async def on_startup_hook(app_: web.Application) -> None:
            hooked = await apply_webhook(bot)
            logger.info("Webhook set: %s", hooked)

        app.on_startup.append(on_startup_hook)

    return app


async def run_http_forever(app: web.Application) -> None:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()
    logger.info("HTTP server on 0.0.0.0:%s", settings.port)
    # Self-ping to keep Render alive
    asyncio.create_task(_self_ping_loop(settings.port))
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()


async def _self_ping_loop(port: int) -> None:
    """Пингует /health каждые 8 минут — не даёт Render заснуть."""
    url = f"http://127.0.0.1:{port}/health"
    logger.info("self-ping started: %s every %ss", url, SELF_PING_INTERVAL)
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            await asyncio.sleep(SELF_PING_INTERVAL)
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        logger.info("self-ping OK (%s)", url)
                    else:
                        logger.warning("self-ping HTTP %s", resp.status)
            except Exception as exc:
                logger.warning("self-ping failed: %s", exc)


async def run_polling_with_http(bot: Bot, dp: Dispatcher) -> None:
    # Тройная страховка: удалить webhook ДО запуска HTTP и polling
    for attempt in range(3):
        try:
            info = await bot.get_webhook_info()
            if info.url:
                logger.warning(
                    "Pre-polling: webhook active at %s (attempt %d), removing",
                    info.url, attempt + 1,
                )
                await bot.delete_webhook(drop_pending_updates=True)
                # Проверяем что действительно удалился
                info2 = await bot.get_webhook_info()
                if not info2.url:
                    logger.info("Webhook removed successfully")
                    break
            else:
                logger.info("Pre-polling: no webhook active — OK")
                break
        except Exception as exc:
            logger.warning("Pre-polling webhook cleanup attempt %d failed: %s", attempt + 1, exc)
            await asyncio.sleep(1)

    app = build_app(bot, dp, webhook=False)
    me = await bot.get_me()
    logger.info("Polling @%s on port %s", me.username, settings.port)

    async def poll() -> None:
        # Периодическая проверка webhook каждые 5 минут
        async def _watchdog():
            while True:
                await asyncio.sleep(300)
                try:
                    info = await bot.get_webhook_info()
                    if info.url:
                        logger.error(
                            "WATCHDOG: webhook reappeared at %s during polling! Removing.",
                            info.url,
                        )
                        await bot.delete_webhook(drop_pending_updates=True)
                except Exception as exc:
                    logger.warning("watchdog webhook check failed: %s", exc)

        watchdog = asyncio.create_task(_watchdog())
        try:
            await dp.start_polling(
                bot,
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query", "my_chat_member"],
            )
        finally:
            watchdog.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog

    poll_task = asyncio.create_task(poll())
    try:
        await run_http_forever(app)
    finally:
        poll_task.cancel()
        with suppress(asyncio.CancelledError):
            await poll_task


async def run_webhook_mode(bot: Bot, dp: Dispatcher) -> None:
    if not settings.webhook_base_url.strip():
        raise RuntimeError("WEBHOOK_BASE_URL обязателен для webhook-режима")
    app = build_app(bot, dp, webhook=True)
    await run_http_forever(app)
