from __future__ import annotations

import re

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ChatMemberUpdated, Message

from deps import gpt, store
from trigger_ai import parse_choice, parse_manage_command, suggest_trigger_replies

router = Router(name="trigger_fsm")

GROUP_GREET = "кидай ссылку Instagram/YouTube — пришлю видео"
PRIVATE_GREET = (
    "кидай ссылку Instagram/YouTube — пришлю видео\n\n"
    "/тригер — настроить слово\n"
    "/тригеры — список, удалить, изменить"
)


class SetupTrigger(StatesGroup):
    word = State()
    pick = State()


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
    await bot.send_message(event.chat.id, GROUP_GREET)


@router.message(Command("тригер", "trigger"))
async def cmd_trigger_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(SetupTrigger.word)
    await message.answer("На какое слово ловить? (одно слово, точное совпадение)")


@router.message(StateFilter(SetupTrigger.word), F.text)
async def setup_word(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = (message.text or "").strip()
    if data.get("edit_index") is not None and text in {"=", "оставить", "то же"}:
        word = str(data.get("word", ""))
    else:
        word = text.split()[0].lower() if text else ""
    if not word or word.startswith("/"):
        await message.answer("Напиши одно слово без /")
        return
    cid = _chat_id(message)
    options = await suggest_trigger_replies(gpt, store, cid, word)
    await state.update_data(word=word, options=options, edit_index=None)
    await state.set_state(SetupTrigger.pick)
    lines = ["Как отвечать? Выбери номер или напиши свой текст:\n"]
    for i, opt in enumerate(options, start=1):
        lines.append(f"{i}. {opt}")
    await message.answer("\n".join(lines))


@router.message(StateFilter(SetupTrigger.pick), F.text)
async def setup_pick(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    word = data.get("word", "")
    options: list[str] = data.get("options") or []
    picked = parse_choice(message.text or "", options)
    if not picked:
        await message.answer("Напиши 1–5 или свой ответ текстом")
        return

    cid = _chat_id(message)
    edit_index = data.get("edit_index")
    if edit_index is not None:
        ok = store.update_custom_rule(
            cid, int(edit_index), word=word, response=picked
        )
        await message.answer("Тригер обновлён." if ok else "Не вышло обновить.")
    else:
        store.add_custom_rule(cid, word, picked)
        await message.answer(f"Ок. На «{word}» → {picked}")
    await state.clear()


@router.message(Command("тригеры", "triggers"))
async def cmd_list_triggers(message: Message) -> None:
    cid = _chat_id(message)
    lines = store.all_numbered_for_display(cid)
    if not lines:
        await message.answer("Пока только встроенные. /тригер — добавить свой.")
        return
    tail = (
        "\n\nУдалить: <code>3 удалить</code> или <code>1 2 удалить</code>\n"
        "Изменить: <code>5 отредактировать</code>"
    )
    await message.answer("\n".join(lines) + tail, parse_mode="HTML")


@router.message(F.text.regexp(r"(?i).*(\d+).*(удал|отредакт|редакт|edit)"))
async def cmd_manage_inline(message: Message, state: FSMContext) -> None:
    if await state.get_state():
        return
    parsed = parse_manage_command(message.text or "")
    if not parsed:
        return

    cid = _chat_id(message)
    custom = store.load_custom(cid)
    if not custom:
        await message.answer("Нет своих тригеров. /тригер — добавить.")
        return

    deletes = [idx for action, idx in parsed if action == "delete"]
    edits = [idx for action, idx in parsed if action == "edit"]

    if deletes:
        n = store.delete_custom_by_indices(cid, [i - 1 for i in deletes])
        await message.answer(f"Удалено: {n}")

    if edits:
        if len(edits) > 1:
            await message.answer("Редактируй по одному: `5 отредактировать`", parse_mode="HTML")
            return
        one_based = edits[0]
        if one_based < 1 or one_based > len(custom):
            await message.answer("Нет такого номера.")
            return
        rule = custom[one_based - 1]
        await state.update_data(edit_index=one_based - 1, word=rule.words[0])
        await state.set_state(SetupTrigger.word)
        await state.update_data(edit_index=one_based - 1, word=rule.words[0])
        await message.answer(
            f"Редактируем №{one_based}. Новое слово? (сейчас: {rule.words[0]})\n"
            "Напиши слово или «=» чтобы оставить."
        )

