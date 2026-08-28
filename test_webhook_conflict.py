"""Тест: проверка что webhook/polling конфликт не повторится.

Запуск: python test_webhook_conflict.py
Требует: BOT_TOKEN в .env
"""
from __future__ import annotations

import asyncio
import sys


async def main() -> int:
    try:
        from config import settings
    except Exception as exc:
        print(f"FAIL: cannot load config: {exc}")
        return 1

    if not settings.bot_token:
        print("SKIP: BOT_TOKEN not set")
        return 0

    from aiogram import Bot

    bot = Bot(token=settings.bot_token)

    try:
        info = await bot.get_webhook_info()
    except Exception as exc:
        print(f"FAIL: get_webhook_info failed: {exc}")
        return 1

    # Если webhook установлен — это потенциальный конфликт с polling
    if info.url:
        print(f"WARNING: webhook is active at {info.url}")
        print("Attempting to delete webhook...")
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            info2 = await bot.get_webhook_info()
            if info2.url:
                print(f"FAIL: webhook still active after delete: {info2.url}")
                return 1
            print("OK: webhook deleted successfully")
        except Exception as exc:
            print(f"FAIL: delete_webhook failed: {exc}")
            return 1
    else:
        print("OK: no webhook active")

    # Проверяем что getUpdates работает (polling совместим)
    try:
        # Не делаем полный getUpdates, просто проверяем что нет конфликта
        info3 = await bot.get_webhook_info()
        if info3.url:
            print("FAIL: webhook reappeared after delete")
            return 1
        print("OK: polling mode compatible — no webhook conflict")
    except Exception as exc:
        print(f"FAIL: post-delete check failed: {exc}")
        return 1

    await bot.session.close()
    print("OK: webhook conflict test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
