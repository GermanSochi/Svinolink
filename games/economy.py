from __future__ import annotations

import random
from typing import Any, Callable

from sqlalchemy import select

from game_db import session_scope
from game_models import EconomyBalance, GameState


async def apply_economy_action(
    user_id: int,
    state_id: int,
    action_type: str,
    payload: dict[str, Any],
    *,
    rng: Callable[[], float] = random.random,
) -> dict[str, Any]:
    """
    MVP гараж/пивоварня:
    - buy_upgrade: тратим рубли
    - work_shift: простая выручка/затраты, шанс события 15%
    """
    async with session_scope() as s:
        bal = (await s.execute(select(EconomyBalance).where(EconomyBalance.user_id == user_id))).scalar_one()
        st = (await s.execute(select(GameState).where(GameState.id == state_id))).scalar_one()
        state = dict(st.state or {})

        if action_type in {"buy_upgrade", "upgrade"}:
            cost = int(payload.get("cost") or 0)
            if cost <= 0:
                # авто-стоимость по "tier"
                tier = int(payload.get("tier") or 1)
                cost = min(50_000, max(5_000, tier * 7_500))
            if bal.rubles < cost:
                return {"ok": False, "reason": "money", "rubles": bal.rubles, "cost": cost}
            bal.rubles -= cost
            upgrades = int(state.get("upgrades", 0)) + 1
            state["upgrades"] = upgrades
            st.state = state
            await s.commit()
            return {"ok": True, "rubles": bal.rubles, "upgrades": upgrades, "spent": cost}

        if action_type not in {"work", "work_shift", "brew", "repair"}:
            return {}

        upgrades = int(state.get("upgrades", 0))
        income = 800 + upgrades * 250
        expense = 200 + upgrades * 80
        delta = income - expense
        bal.rubles += delta

        event = None
        if rng() < 0.15:
            event = random.choice(
                [
                    ("Налоговая заглянула", -900),
                    ("Партию пива повело", -700),
                    ("Клиент дал на чай", +500),
                    ("Нашёл хороший контракт", +1200),
                ]
            )
            bal.rubles += int(event[1])

        shifts = int(state.get("shifts", 0)) + 1
        state["shifts"] = shifts
        st.state = state

        await s.commit()
        return {"rubles": bal.rubles, "delta": delta, "event": event, "shifts": shifts}

