from __future__ import annotations

from typing import Any


def render_game_response(game_id: str, action_type: str, data: dict[str, Any]) -> str:
    """
    Ответы должны быть приземлёнными: только то, что реально посчитали.
    Минимум эмодзи — один якорь на абзац.
    """
    if game_id == "fishing":
        if action_type in {"set_bait", "equip_bait"}:
            bait = data.get("bait") or "наживка"
            return f"🎣 Наживка: **{bait}**.\n\nКидай: **«закинь удочку»**."
        if action_type in {"equip_rod", "set_rod"}:
            rod = data.get("rod") or "удочка"
            return f"🎣 Удочка: **{rod}**.\n\nКидай: **«закинь удочку»**."
        if action_type in {"cast_rod", "fish", "hunt"}:
            if data.get("caught"):
                fish = data.get("fish") or "трофей"
                w = data.get("weight")
                cnt = data.get("count")
                return f"🎣 Подсёк. **{fish}**, **{w} кг**.\n\nУлов за сегодня: **{cnt}**."
            return "🎣 Пусто. В следующий заброс зайдёт."

    if game_id == "economy":
        if action_type in {"buy_upgrade", "upgrade"}:
            if data.get("ok") is False and data.get("reason") == "money":
                return f"🛠️ Не хватает денег. Сейчас **{data.get('rubles')}₽**, нужно **{data.get('cost')}₽**."
            return f"🛠️ Апгрейд поставил. Баланс: **{data.get('rubles')}₽**.\n\nУлучшений: **{data.get('upgrades')}**."
        if action_type in {"work", "work_shift", "brew", "repair"}:
            rub = data.get("rubles")
            delta = data.get("delta")
            ev = data.get("event")
            shifts = data.get("shifts")
            out = f"🛠️ Смена закрыта. Плюс/минус: **{delta}₽**.\n\nБаланс: **{rub}₽**."
            if ev:
                out += f"\n\n🍻 Событие: **{ev[0]}** ({ev[1]}₽)."
            out += f"\n\nСмен отработано: **{shifts}**."
            return out

    if game_id == "poker":
        if action_type in {"join", "buyin"}:
            if data.get("ok") is False and data.get("reason") == "money":
                return f"🃏 Бай-ин не прошёл. Сейчас **{data.get('rubles')}₽**, нужно **{data.get('need')}₽**."
            return (
                f"🃏 За столом. Бай-ин: **{data.get('buyin')}**.\n\n"
                f"Фишки: **{data.get('chips')}**, банк: **{data.get('pot')}**."
            )
        if action_type == "bet":
            if data.get("ok") is False and data.get("reason") == "chips":
                return f"🃏 Фишек не хватает. Сейчас **{data.get('chips')}**, нужно **{data.get('need')}**."
            return f"🃏 Ставка: **{data.get('bet')}**.\n\nФишки: **{data.get('chips')}**, банк: **{data.get('pot')}**."
        if action_type in {"check", "fold"}:
            return f"🃏 {action_type.upper()}. Фишки: **{data.get('chips')}**, банк: **{data.get('pot')}**."

    if game_id == "quiz":
        if action_type in {"ask", "question", "start"} and data.get("question"):
            return f"🧠 Вопрос:\n\n{data['question']}\n\nОтветь текстом: **«мой ответ: …»**."
        if action_type in {"answer", "reply"}:
            if data.get("ok") is True:
                return f"🧠 Верно. Ответ: **{data.get('correct')}**.\n\nПлюс к уважению и **+250₽**."
            if data.get("reason") == "no_session":
                return "🧠 Сейчас нет активного вопроса. Скажи: **«дай вопрос»**."
            return f"🧠 Мимо. Правильный ответ: **{data.get('correct')}**."

    return "🐷 Понял. Но действие пока не поддержано движком."

