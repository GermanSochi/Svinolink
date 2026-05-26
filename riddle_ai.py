from __future__ import annotations

import re

from deps import gpt
from game_store import GameStore
from yandex_gpt import YandexGPTError

_INTRO = (
    "На связи Svinolink. Моя работа — перегонять ссылки Instagram и YouTube Shorts в видео.\n"
    "Со мной можно поговорить, но сначала угадай загадку:"
)


async def generate_riddle() -> tuple[str, str]:
  system = (
    "Придумай одну короткую загадку на общие знания (столица, страна, простой факт). "
    "Без мата. Ответ — одно слово или короткая фраза (до 4 слов). "
    "Формат строго:\nЗагадка: ...\nОтвет: ..."
  )
  raw = await gpt.reply("Новая загадка для друзей в чате.", system=system)
  q_m = re.search(r"Загадка:\s*(.+)", raw, re.IGNORECASE)
  a_m = re.search(r"Ответ:\s*(.+)", raw, re.IGNORECASE)
  if q_m and a_m:
    return q_m.group(1).strip(), a_m.group(1).strip()
  lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
  if len(lines) >= 2:
    return lines[0], lines[-1]
  raise YandexGPTError("не смог придумать загадку")


async def check_answer(expected: str, user_text: str) -> bool:
  exp = GameStore.normalize_answer(expected)
  got = GameStore.normalize_answer(user_text)
  if not got:
    return False
  if exp == got or exp in got or got in exp:
    return True
  try:
    verdict = await gpt.reply(
      f"Загадка. Правильный ответ: «{expected}». Ответ игрока: «{user_text}». "
      "Это засчитывается? Ответь только ДА или НЕТ.",
      system="Ты судья загадок. Синонимы и опечатки в 1 букву — ДА.",
    )
    return verdict.strip().upper().startswith("ДА")
  except YandexGPTError:
    return False


async def start_riddle_flow(chat_id: int, user_id: int, game: GameStore) -> str:
  question, answer = await generate_riddle()
  game.set_riddle(chat_id, user_id, question, answer)
  return f"{_INTRO}\n\n{question}"


async def try_solve_riddle(
  chat_id: int, user_id: int, text: str, game: GameStore
) -> str | None:
  row = game.get_riddle(chat_id, user_id)
  if not row:
    return None
  question, answer, solved = row
  if solved:
    return None
  if await check_answer(answer, text):
    game.mark_solved(chat_id, user_id)
    left = game.questions_left(chat_id, user_id)
    return (
      f"Верно. Доступ к разговору открыт — {left} вопроса в этот час.\n"
      "Кидай ссылки как обычно."
    )
  return "Неа. Подумай ещё или напиши «свин» — новая загадка."
