from __future__ import annotations

import html

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from bot_miniapp import miniapp_keyboard, miniapp_url_for_chat
from config import settings
from deps import store

router = Router(name="trigger_fsm")

GROUP_GREET = "кидай ссылку Instagram — пришлю видео"
PRIVATE_GREET = "кидай ссылку Instagram — пришлю видео"


def _remember_chat(message: Message) -> None:
    if message.chat.type in {"group", "supergroup"}:
        store.register_chat(
            message.chat.id,
            title=message.chat.title,
            chat_type=message.chat.type,
        )


def _format_trigger_lines(chat_id: int) -> list[str]:
    lines: list[str] = []
    for r in store.load_defaults():
        w = ", ".join(r.words)
        lines.append(f"  · [{w}] → {r.response} (системный)")
    for r in store.load_custom(chat_id):
        w = ", ".join(r.words)
        if r.added_by_username:
            who = f"@{r.added_by_username.lstrip('@')}"
        elif r.added_by_user_id:
            who = f"id{r.added_by_user_id}"
        else:
            who = "неизвестно"
        lines.append(f"  · [{w}] → {r.response} ({who})")
    return lines


async def send_private_triggers_menu(message: Message) -> None:
    chats = store.list_active_chats()
    if not chats:
        url = settings.miniapp_url
        hint = (
            "🐷 Пока меня нет ни в одной группе.\n"
            "Добавь в группу и напиши там /triggers."
        )
        if url:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⚙️ Открыть Mini App",
                            web_app=WebAppInfo(url=url),
                        )
                    ]
                ]
            )
            await message.answer(hint, reply_markup=kb)
        else:
            await message.answer(hint)
        return

    chunks: list[str] = ["🐷 <b>Группы, где я стою:</b>\n"]
    rows: list[list[InlineKeyboardButton]] = []

    for chat in chats:
        cid = chat["chat_id"]
        title = chat["title"].strip() or f"Чат {cid}"
        safe_title = html.escape(title)
        summary = store.chat_trigger_summary(cid)
        chunks.append(f"\n<b>{safe_title}</b>\n<i>{summary}</i>")
        trigger_lines = _format_trigger_lines(cid)
        if trigger_lines:
            chunks.extend(html.escape(line) for line in trigger_lines[:8])
            if len(trigger_lines) > 8:
                chunks.append(f"  … ещё {len(trigger_lines) - 8}")

        url = miniapp_url_for_chat(cid)
        if url:
            btn_title = title if len(title) <= 28 else title[:25] + "…"
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"⚙️ {btn_title}",
                        web_app=WebAppInfo(url=url),
                    )
                ]
            )

    chunks.append("\n\nЖми кнопку — откроется Mini App для группы.")
    kb = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    await message.answer("\n".join(chunks), reply_markup=kb, parse_mode="HTML")


@router.my_chat_member()
async def bot_joined(event: ChatMemberUpdated, bot: Bot) -> None:
    new = event.new_chat_member
    if not new or new.user.id != (await bot.get_me()).id:
        return
    cid = event.chat.id
    if new.status in {"member", "administrator"}:
        if event.chat.type in {"group", "supergroup"}:
            store.register_chat(cid, title=event.chat.title, chat_type=event.chat.type)
            kb = miniapp_keyboard(cid) if settings.miniapp_url else None
            privacy = (
                "\n\n@BotFather → /setprivacy → Disable — иначе в группе не увижу ссылки."
            )
            await bot.send_message(cid, GROUP_GREET + privacy, reply_markup=kb)
        return
    if new.status in {"left", "kicked"}:
        store.deactivate_chat(cid)


@router.message(Command("triggers"))
async def cmd_triggers(message: Message) -> None:
    if message.chat.type == "private":
        await send_private_triggers_menu(message)
        return

    _remember_chat(message)
    cid = message.chat.id
    kb = miniapp_keyboard(cid)
    if not kb:
        await message.answer("Mini App недоступен: задай WEBHOOK_BASE_URL на Render.")
        return
    await message.answer(
        "🐷 Нажми на кнопку ниже, чтобы управлять триггерами этого чата:",
        reply_markup=kb,
    )
