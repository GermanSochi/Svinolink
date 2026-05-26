from __future__ import annotations

import asyncio
import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.types import FSInputFile, Message

from config import settings
from deps import gpt, store
from downloader import cleanup_paths, download_to_temp_mp4
from message_urls import url_from_message
from game_store import GameStore
from riddle_ai import start_riddle_flow, try_solve_riddle
from yandex_gpt import YandexGPTError

logger = logging.getLogger(__name__)
router = Router(name="chat_handlers")

CAPTION = "Svinolink любит донаты"
_WAKE_RE = re.compile(
  r"(?i)(@svinolink_bot|svinolink|свинолинк|свино\s*линк|свин\b|свино\b)"
)

game = GameStore()


def is_admin_user(user_id: int, username: str | None) -> bool:
  if user_id in settings.admin_ids:
    return True
  if username and username.lower().lstrip("@") in settings.admin_usernames:
    return True
  return False


def is_wake_message(message: Message) -> bool:
  text = message.text or message.caption or ""
  if _WAKE_RE.search(text):
    return True
  if message.entities and message.from_user:
    for ent in message.entities:
      if ent.type == "mention" and "svinolink" in text.lower():
        return True
  return False


@router.message(StateFilter(None))
async def handle_links_first(message: Message, bot: Bot) -> None:
  url = url_from_message(message)
  if not url:
    return
  logger.info("link chat=%s url=%s", message.chat.id, url[:80])

  status = await bot.send_message(
    message.chat.id,
    "Сек…",
    reply_to_message_id=message.message_id,
  )
  file_path = None
  try:
    file_path = await asyncio.to_thread(download_to_temp_mp4, url)
    await bot.send_video(
      chat_id=message.chat.id,
      video=FSInputFile(file_path),
      caption=CAPTION,
      reply_to_message_id=message.message_id,
      supports_streaming=True,
    )
    await status.delete()
  except Exception as exc:
    logger.exception("download failed url=%s: %s", url, exc)
    await status.edit_text("поломался")
  finally:
    if file_path:
      cleanup_paths(file_path)


@router.message(StateFilter(None), F.text)
async def handle_wake(message: Message, bot: Bot) -> None:
  if not is_wake_message(message):
    return
  if not message.from_user:
    return
  try:
    text = await start_riddle_flow(
      message.chat.id, message.from_user.id, game
    )
    await message.reply(text)
  except YandexGPTError:
    await message.reply(
      "На связи Svinolink. Кидаю ссылки Instagram/YouTube в видео. "
      "Загадка временно недоступна — кидай ссылку."
    )


@router.message(StateFilter(None), F.text)
async def handle_riddle_answer(message: Message, bot: Bot) -> None:
  if not message.from_user or not message.text:
    return
  if message.text.startswith("/"):
    return
  if url_from_message(message):
    return
  if is_wake_message(message):
    return

  uid = message.from_user.id
  cid = message.chat.id

  reply = await try_solve_riddle(cid, uid, message.text, game)
  if reply:
    await message.reply(reply)
    return

  if game.is_unlocked(cid, uid) and not store.find_match(
    message.text, store.load_triggers(cid)
  ):
    if game.questions_left(cid, uid) <= 0:
      await message.reply("Лимит: 2 вопроса в час. Потом снова или кидай ссылки.")
      return
    try:
      answer = await gpt.reply(
        message.text,
        system=(
          "Ты Svinolink — дерзкий бот в мужском чате друзей. "
          "Отвечай коротко, по делу, можно мат. Не упоминай код и админов."
        ),
      )
      game.use_question(cid, uid)
      left = game.questions_left(cid, uid)
      await message.reply(f"{answer}\n\n(осталось {left}/2 вопроса в час)")
    except YandexGPTError:
      await message.reply("Сейчас не могу ответить. Кидай ссылку — видео скину.")
