from __future__ import annotations

import random
from typing import Any

from sqlalchemy import func, select

from game_db import session_scope
from game_models import EconomyBalance, QuizQuestion, QuizSession


DEFAULT_QUESTIONS = [
    ("Столица Канады?", "Оттава"),
    ("Сколько секунд в минуте?", "60"),
    ("Самая большая планета Солнечной системы?", "Юпитер"),
]


async def _ensure_questions() -> None:
    async with session_scope() as s:
        cnt = (await s.execute(select(func.count()).select_from(QuizQuestion))).scalar_one()
        if cnt and cnt > 0:
            return
        for q, a in DEFAULT_QUESTIONS:
            s.add(QuizQuestion(question=q, answer=a, category="pub", difficulty=1))
        await s.commit()


async def apply_quiz_action(
    chat_id: int,
    user_id: int,
    state_id: int,
    action_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    MVP:
    - ask: выдаём вопрос
    - answer: простая проверка (без LLM-валидации)
    """
    await _ensure_questions()

    async with session_scope() as s:
        if action_type in {"ask", "question", "start"}:
            row = await s.execute(select(QuizQuestion).order_by(func.random()).limit(1))
            q = row.scalar_one()
            sess = QuizSession(chat_id=chat_id, active=True, question_id=q.id, meta={})
            s.add(sess)
            await s.commit()
            return {"ok": True, "question": q.question, "session_id": sess.id}

        if action_type in {"answer", "reply"}:
            ans = str(payload.get("answer") or "").strip()
            if not ans:
                return {"ok": False, "reason": "no_answer"}
            row = await s.execute(
                select(QuizSession).where(QuizSession.chat_id == chat_id, QuizSession.active == True).order_by(QuizSession.id.desc())
            )
            sess = row.scalar_one_or_none()
            if not sess or not sess.question_id:
                return {"ok": False, "reason": "no_session"}
            q = (await s.execute(select(QuizQuestion).where(QuizQuestion.id == sess.question_id))).scalar_one()
            ok = ans.lower() == q.answer.strip().lower()
            sess.active = False
            if ok:
                bal = (await s.execute(select(EconomyBalance).where(EconomyBalance.user_id == user_id))).scalar_one()
                bal.rubles += 250
            await s.commit()
            return {"ok": ok, "correct": q.answer}

        return {}

