"""Поставить аватар бота через Bot API (если метод доступен)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp
from aiogram import Bot
from aiogram.types import FSInputFile, InputProfilePhotoStatic

from config import settings

AVATAR = Path(__file__).resolve().parent.parent / "assets" / "avatar.png"


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан")
    if not AVATAR.is_file():
        raise SystemExit(f"Нет файла {AVATAR}")

    bot = Bot(token=settings.bot_token)
    static = InputProfilePhotoStatic(photo=FSInputFile(AVATAR))

    if hasattr(bot, "set_my_profile_photo"):
        await bot.set_my_profile_photo(photo=static)
        print("Аватар обновлён через set_my_profile_photo")
    else:
        url = f"https://api.telegram.org/bot{settings.bot_token}/setMyProfilePhoto"
        form = aiohttp.FormData()
        form.add_field("photo", AVATAR.read_bytes(), filename="avatar.png", content_type="image/png")
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=form) as resp:
                body = await resp.json()
                if body.get("ok"):
                    print("Аватар обновлён")
                else:
                    print("API:", body)
                    print("Загрузи вручную: @BotFather → /setuserpic →", AVATAR)
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
