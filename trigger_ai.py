from __future__ import annotations

import re

from store import TriggerStore
from yandex_gpt import YandexGPT, YandexGPTError

_FALLBACK = [
    "пиздец, опять ты",
    "жёстко, но по делу",
    "ну ты и даёшь",
    "классика, братан",
    "в точку, как всегда",
]


async def suggest_trigger_replies(
    gpt: YandexGPT,
    store: TriggerStore,
    chat_id: int,
    word: str,
) -> list[str]:
    hints = store.style_hints(chat_id)
    style_block = ""
    if hints:
        style_block = "Примеры вашего стиля (ориентируйся на тон):\n" + "\n".join(
            f"- {h}" for h in hints[-5:]
        )

    system = (
        "Ты помогаешь настроить автоответы в мужском дружеском Telegram-чате. "
        "Ответы короткие (до 100 символов), смешные, дерзые, можно мат и сленг. "
        "Верни РОВНО 5 вариантов, каждый с новой строки, без нумерации и без кавычек."
    )
    user = (
        f"Слово-триггер: «{word}»\n"
        f"{style_block}\n\n"
        "Предложи 5 разных ответов на это слово."
    )
    try:
        raw = await gpt.reply(user, system=system)
        lines = [ln.strip(" •-\t") for ln in raw.splitlines() if ln.strip()]
        lines = [ln for ln in lines if len(ln) >= 2][:5]
        if len(lines) >= 3:
            return lines
    except YandexGPTError:
        pass
    return list(_FALLBACK)


def parse_choice(text: str, options: list[str]) -> str | None:
    t = text.strip()
    if re.fullmatch(r"[1-5]", t):
        idx = int(t) - 1
        if 0 <= idx < len(options):
            return options[idx]
    return t if len(t) >= 2 else None


def parse_manage_command(text: str) -> list[tuple[str, int]]:
    """
    Возвращает ('delete'|'edit', index 1-based) для каждого совпадения.
    Примеры: '3 удалить', '5 отредактировать', '1 2 3 удалить'
    """
    t = text.strip().lower()
    results: list[tuple[str, int]] = []

    if re.search(r"удал", t):
        nums = [int(x) for x in re.findall(r"\d+", t)]
        for n in nums:
            results.append(("delete", n))
        return results

    m = re.match(r"(\d+)\s*(отредакт|редакт|edit)", t)
    if m:
        return [("edit", int(m.group(1)))]

    return results
