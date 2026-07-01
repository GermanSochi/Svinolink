from __future__ import annotations

from aiogram import Bot, Router
from aiogram.types import ChatMemberUpdated, Message

from deps import store

router = Router(name="trigger_fsm")

GROUP_GREET = "кидай ссылку Instagram — пришлю видео"
PRIVATE_GREET = "Привет! Пришли ссылку на видео из Instagram — пришлю 🐷"


@router.message(lambda m: m.chat.type == "private" and m.forward_from_chat is not None)
async def register_from_forward(message: Message) -> None:
    src = message.forward_from_chat
    if not src or src.type not in {"group", "supergroup"}:
        await message.answer("Перешли сообщение из группы, не из канала или лички.")
        return
    store.register_chat(src.id, title=src.title, chat_type=src.type)
    title = src.title or f"Чат {src.id}"
    await message.answer(
        f"🐷 Группа «{title}» добавлена в список.\n\n"
        f"🆔 ID: `{src.id}`\n\n"
        "Открой ⚙️ Триггеры → жми «Обновить список».",
        parse_mode="Markdown",
    )


@router.my_chat_member()
async def bot_joined(event: ChatMemberUpdated, bot: Bot) -> None:
    new = event.new_chat_member
    if not new or new.user.id != (await bot.get_me()).id:
        return
    cid = event.chat.id
    if new.status in {"member", "administrator"}:
        if event.chat.type in {"group", "supergroup"}:
            added_by = event.from_user.id if event.from_user else None
            store.register_chat(
                cid,
                title=event.chat.title,
                chat_type=event.chat.type,
                added_by_user_id=added_by,
            )
            privacy = (
                "\n\n@BotFather → /setprivacy → Disable — иначе в группе не увижу ссылки."
            )
            await bot.send_message(
                cid,
                f"{GROUP_GREET}{privacy}\n\n🆔 ID группы для настройки триггеров: `{cid}`",
                parse_mode="Markdown",
            )
        return
    if new.status in {"left", "kicked"}:
        store.deactivate_chat(cid)
