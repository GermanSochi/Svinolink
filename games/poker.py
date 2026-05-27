from __future__ import annotations

from typing import Any

from sqlalchemy import select

from game_db import session_scope
from game_models import EconomyBalance, PokerTable


MIN_BUYIN = 500


async def apply_poker_action(
    chat_id: int,
    user_id: int,
    state_id: int,
    action_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Очень упрощённый MVP:
    - join: покупка фишек (из рублей в chips)
    - bet/check/fold: двигаем банк и "ход"
    """
    async with session_scope() as s:
        table = (await s.execute(select(PokerTable).where(PokerTable.chat_id == chat_id))).scalar_one_or_none()
        if not table:
            table = PokerTable(chat_id=chat_id, active=False, pot=0, meta={})
            s.add(table)
            await s.flush()

        bal = (await s.execute(select(EconomyBalance).where(EconomyBalance.user_id == user_id))).scalar_one()

        if action_type in {"join", "buyin"}:
            amount = int(payload.get("buyin") or MIN_BUYIN)
            if amount < MIN_BUYIN:
                amount = MIN_BUYIN
            if bal.rubles < amount:
                return {"ok": False, "reason": "money", "rubles": bal.rubles, "need": amount}
            bal.rubles -= amount
            bal.chips += amount
            table.active = True
            await s.commit()
            return {"ok": True, "rubles": bal.rubles, "chips": bal.chips, "buyin": amount, "pot": table.pot}

        if not table.active:
            return {"ok": False, "reason": "inactive"}

        if action_type == "bet":
            amt = int(payload.get("amount") or 0)
            if amt <= 0:
                amt = 100
            if bal.chips < amt:
                return {"ok": False, "reason": "chips", "chips": bal.chips, "need": amt}
            bal.chips -= amt
            table.pot += amt
            await s.commit()
            return {"ok": True, "chips": bal.chips, "pot": table.pot, "bet": amt}

        if action_type == "check":
            await s.commit()
            return {"ok": True, "chips": bal.chips, "pot": table.pot}

        if action_type == "fold":
            await s.commit()
            return {"ok": True, "chips": bal.chips, "pot": table.pot}

        return {}

