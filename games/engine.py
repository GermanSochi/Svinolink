from __future__ import annotations

import random
from typing import Any

from sqlalchemy import select

from game_db import session_scope
from game_models import EconomyBalance, GameState, User

from .economy import apply_economy_action
from .fishing import apply_fishing_action
from .poker import apply_poker_action
from .quiz import apply_quiz_action


async def ensure_user(telegram_user_id: int, username: str | None) -> User:
    async with session_scope() as s:
        row = await s.execute(select(User).where(User.telegram_user_id == telegram_user_id))
        user = row.scalar_one_or_none()
        if user:
            if username and user.username != username:
                user.username = username
                await s.commit()
            return user
        user = User(telegram_user_id=telegram_user_id, username=username)
        s.add(user)
        await s.flush()
        s.add(EconomyBalance(user_id=user.id, rubles=10_000, chips=0))
        await s.commit()
        return user


async def get_state(chat_id: int, game_id: str, user_id: int) -> GameState:
    async with session_scope() as s:
        row = await s.execute(
            select(GameState).where(
                GameState.chat_id == chat_id,
                GameState.game_id == game_id,
                GameState.user_id == user_id,
            )
        )
        st = row.scalar_one_or_none()
        if st:
            return st
        st = GameState(chat_id=chat_id, game_id=game_id, user_id=user_id, state={})
        s.add(st)
        await s.commit()
        return st


async def execute_game_action(
    *,
    chat_id: int,
    telegram_user_id: int,
    username: str | None,
    game_id: str,
    action_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Возвращает dict с возможными доп. данными для text_response (например баланс).
    """
    user = await ensure_user(telegram_user_id, username)
    if game_id == "fishing":
        st = await get_state(chat_id, "fishing", user.id)
        return await apply_fishing_action(user.id, st.id, action_type, payload)
    if game_id == "economy":
        st = await get_state(chat_id, "economy", user.id)
        return await apply_economy_action(user.id, st.id, action_type, payload, rng=random.random)
    if game_id == "poker":
        st = await get_state(chat_id, "poker", user.id)
        return await apply_poker_action(chat_id, user.id, st.id, action_type, payload)
    if game_id == "quiz":
        st = await get_state(chat_id, "quiz", user.id)
        return await apply_quiz_action(chat_id, user.id, st.id, action_type, payload)
    return {}

