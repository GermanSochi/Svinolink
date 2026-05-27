from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from bot_startup import configure_bot
from ai_quota import HOURLY_LIMIT
from chat_memory import check_connection, fetch_audit_rows, init_chat_memory, is_pool_ready, url_hint
from chat_style import daily_style_loop
from config import settings
from instagram_download import init_instagram_downloader
from game_init import init_game_db
from webapp_server import STATIC, register_miniapp_routes

logger = logging.getLogger(__name__)


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
        miniapp_html = ""
        try:
            miniapp_html = (STATIC / "index.html").read_text(encoding="utf-8")
        except OSError:
            pass
        payload = {
            "status": "ok",
            "bot": "svinolink",
            "version": settings.app_version,
            "svin_hourly_limit": HOURLY_LIMIT,
            "miniapp_manual_input": "manualChatId" in miniapp_html,
            "miniapp": "on" if settings.miniapp_url else "off",
            "mode": "webhook" if webhook else "polling",
            "chat_memory": (
                "connected"
                if is_pool_ready()
                else ("configured" if settings.supabase_database_url.strip() else "off")
            ),
        }
        return web.Response(
            text=json.dumps(payload, ensure_ascii=False),
            content_type="application/json",
        )

    async def health_db(_: web.Request) -> web.Response:
        if not settings.supabase_database_url.strip():
            payload = {"ok": False, "detail": "SUPABASE_DATABASE_URL not set"}
        else:
            try:
                ok, detail = await check_connection()
                payload = {
                    "ok": ok,
                    "detail": detail,
                    "pool_ready": is_pool_ready(),
                    "url_hint": url_hint(settings.supabase_database_url),
                }
            except Exception as exc:
                payload = {"ok": False, "detail": str(exc)}
        return web.Response(
            text=json.dumps(payload, ensure_ascii=False),
            content_type="application/json",
        )

    async def health_db_audit(_: web.Request) -> web.Response:
        if not settings.supabase_database_url.strip():
            payload = {"ok": False, "detail": "SUPABASE_DATABASE_URL not set", "rows": []}
        else:
            ok, detail = await check_connection()
            if not ok:
                payload = {"ok": False, "detail": detail, "rows": []}
            else:
                rows = await fetch_audit_rows(10)
                payload = {
                    "ok": True,
                    "detail": detail,
                    "count": len(rows),
                    "rows": rows,
                }
        return web.Response(
            text=json.dumps(payload, ensure_ascii=False, default=str),
            content_type="application/json",
        )

    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/health/db", health_db)
    app.router.add_get("/health/db/audit", health_db_audit)
    register_miniapp_routes(app)

    if webhook:
        route = settings.webhook_route
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=route)
        setup_application(app, dp, bot=bot)

        async def on_startup(app: web.Application) -> None:
            await init_chat_memory()
            init_instagram_downloader()
            with suppress(Exception):
                await init_game_db()
            hooked = await apply_webhook(bot)
            await configure_bot(bot)
            app["style_task"] = asyncio.create_task(daily_style_loop())
            logger.info("Listening POST %s | Mini App %s", route, settings.miniapp_url or "off")

        async def on_shutdown(app: web.Application) -> None:
            task = app.get("style_task")
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            with suppress(Exception):
                await bot.delete_webhook(drop_pending_updates=False)

        app.on_startup.append(on_startup)
        app.on_shutdown.append(on_shutdown)
    else:

        async def on_startup(app: web.Application) -> None:
            await init_chat_memory()
            init_instagram_downloader()
            with suppress(Exception):
                await init_game_db()
            await bot.delete_webhook(drop_pending_updates=True)
            await configure_bot(bot)
            app["style_task"] = asyncio.create_task(daily_style_loop())
            logger.info("Polling mode | Mini App %s", settings.miniapp_url or "off")

        app.on_startup.append(on_startup)

    return app


async def run_http_forever(app: web.Application) -> None:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=settings.port)
    await site.start()
    logger.info("HTTP server on 0.0.0.0:%s", settings.port)
    while True:
        await asyncio.sleep(3600)


async def run_polling_with_http(bot: Bot, dp: Dispatcher) -> None:
    app = build_app(bot, dp, webhook=False)
    me = await bot.get_me()
    logger.info("Polling @%s on port %s", me.username, settings.port)

    async def poll() -> None:
        await dp.start_polling(
            bot,
            drop_pending_updates=True,
            allowed_updates=["message", "my_chat_member"],
        )

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
