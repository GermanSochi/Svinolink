from __future__ import annotations

import json
import logging
import re
from typing import Any, TypedDict, Literal

from deps import gpt
from games.prompts import GAME_ROUTER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


GameId = Literal["fishing", "economy", "poker", "quiz", "none"]


class RouterResult(TypedDict):
    is_game_action: bool
    game_id: GameId
    action_type: str
    payload: dict[str, Any]
    text_response: str


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def _coerce_router_result(obj: dict[str, Any]) -> RouterResult:
    is_game_action = bool(obj.get("is_game_action", False))
    game_id = str(obj.get("game_id", "none"))
    if game_id not in {"fishing", "economy", "poker", "quiz", "none"}:
        game_id = "none"
        is_game_action = False
    action_type = str(obj.get("action_type", "none") or "none")
    payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
    text_response = str(obj.get("text_response", "") or "").strip()
    if not text_response:
        text_response = "🐷 Понял. Дай команду по-человечески — и поехали."
    return {
        "is_game_action": is_game_action,
        "game_id": game_id,  # type: ignore[typeddict-item]
        "action_type": action_type,
        "payload": payload,  # type: ignore[typeddict-item]
        "text_response": text_response,
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    t = text.strip()
    m = _JSON_FENCE_RE.search(t)
    if m:
        t = m.group(1).strip()
    # Находим первый JSON-объект.
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    chunk = t[start : end + 1]
    try:
        obj = json.loads(chunk)
    except json.JSONDecodeError:
        # мягкий ремонт: заменим одинарные кавычки на двойные (часто ломается)
        repaired = re.sub(r"(?<!\\\\)'", "\"", chunk)
        obj = json.loads(repaired)
    return obj if isinstance(obj, dict) else None


async def route_intent(user_text: str) -> RouterResult:
    """
    Единственная точка входа: отправляем текст в Yandex GPT,
    получаем JSON, парсим, чиним, возвращаем RouterResult.
    """
    prompt = (
        f"Сообщение пользователя:\n{user_text}\n\n"
        "Верни JSON по схеме."
    )
    try:
        raw = await gpt.reply(prompt, system=GAME_ROUTER_SYSTEM_PROMPT)
    except Exception as exc:
        logger.warning("router yandex failed: %s", exc)
        return _coerce_router_result(
            {
                "is_game_action": False,
                "game_id": "none",
                "action_type": "none",
                "payload": {},
                "text_response": "🐷 Сейчас связь с ведущим глючит. Повтори фразу ещё раз.",
            }
        )

    try:
        obj = _extract_json(raw) or {}
        return _coerce_router_result(obj)
    except Exception as exc:
        logger.warning("router json parse failed: %s raw=%r", exc, raw[:200])
        return _coerce_router_result(
            {
                "is_game_action": False,
                "game_id": "none",
                "action_type": "none",
                "payload": {},
                "text_response": "🐷 Я понял смысл, но ведущий выдал кривой формат. Скажи ещё раз короче.",
            }
        )

