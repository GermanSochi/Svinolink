from __future__ import annotations

import random
from typing import Any

from sqlalchemy import select, update

from game_db import session_scope
from game_models import GameState


FISH_TIERS = [
    ("карась", 0.5, 2.0),
    ("лещ", 1.0, 4.0),
    ("щука", 2.0, 12.0),
    ("судак", 2.0, 10.0),
    ("сом", 8.0, 50.0),
]


async def apply_fishing_action(
    user_id: int,
    state_id: int,
    action_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    MVP:
    - cast_rod: шанс поймать рыбу, вес 0.5–50кг по тиру
    - equip_rod / set_bait: флаги в state
    """
    async with session_scope() as s:
        st = (await s.execute(select(GameState).where(GameState.id == state_id))).scalar_one()
        state = dict(st.state or {})

        if action_type in {"equip_rod", "set_rod"}:
            state["rod"] = payload.get("rod", "обычная")
            st.state = state
            await s.commit()
            return {"rod": state["rod"]}

        if action_type in {"set_bait", "equip_bait"}:
            state["bait"] = payload.get("bait", "червь")
            st.state = state
            await s.commit()
            return {"bait": state["bait"]}

        if action_type not in {"cast_rod", "fish", "hunt"}:
            return {}

        base = 0.40
        rod_bonus = 0.30 if str(state.get("rod", "")).lower() in {"апгрейд", "улучшенная", "premium"} else 0.0
        bait_bonus = 0.20 if str(state.get("bait", "")).lower() in {"премиум", "икра", "premium"} else 0.0
        p = min(0.95, base + rod_bonus + bait_bonus)

        roll = random.random()
        if roll > p:
            return {"caught": False, "p": p, "roll": roll}

        name, w_min, w_max = random.choice(FISH_TIERS)
        weight = round(random.uniform(w_min, w_max), 1)
        count = int(state.get("caught_count", 0)) + 1
        state["caught_count"] = count
        st.state = state
        await s.commit()
        return {"caught": True, "fish": name, "weight": weight, "count": count}

