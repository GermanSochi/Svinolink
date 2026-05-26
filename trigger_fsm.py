from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message

from bot_miniapp import miniapp_keyboard
from config import settings

router = Router(name="trigger_fsm")

GROUP_GREET = (
    "кидай ссылку Instagram Reel или пост — пришлю видео\n"
    "/triggers — настройка триггеров"
)
PRIVATE_GREET = "кидай ссылку Instagram Reel или пост — пришлю видео"


def _chat_id(message: Message) -> int:
    return message.chat.id


@router.my_chat_member()
async def bot_joined(event: ChatMemberUpdated, bot: Bot) -> None:
    new = event.new_chat_member
    if not new or new.user.id != (await bot.get_me()).id:
        return
    if new.status not in {"member", "administrator"}:
        return
    if event.chat.type not in {"group", "supergroup"}:
        return
    kb = miniapp_keyboard(event.chat.id) if settings.miniapp_url else None
    privacy = (
        "\n\n@BotFather → /setprivacy → Disable — иначе в группе не увижу ссылки."
    )
    await bot.send_message(event.chat.id, GROUP_GREET + privacy, reply_markup=kb)


@router.message(Command("triggers"))
async def cmd_triggers(message: Message) -> None:
    cid = _chat_id(message)
    kb = miniapp_keyboard(cid)
    if not kb:
        await message.answer("Mini App недоступен: задай WEBHOOK_BASE_URL на Render.")
        return
    await message.answer(
        "🐷 Нажми на кнопку ниже, чтобы управлять триггерами этого чата:",
        reply_markup=kb,
    )
