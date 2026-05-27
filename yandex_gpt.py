from __future__ import annotations

import asyncio
import json
import logging
import os
import re

import aiohttp

from svin_system_prompt import SVIN_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

YANDEX_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

_REFUSAL_RE = re.compile(
    r"(?i)("
    r"не\s+могу\s+(?:обсуждать|поддерживать)"
    r"|давайте\s+поговорим\s+о\s+ч(?:ё|е)м(?:-|\s)?нибудь\s+ещ"
    r"|я\s+не\s+могу\s+.*(?:тем|разговор)"
    r")"
)


class YandexGPTError(RuntimeError):
    pass


class YandexGPT:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def reply(self, user_text: str, *, system: str | None = None) -> str:
        system_text = system or SVIN_SYSTEM_PROMPT
        answer = await self._complete(user_text, system_text)
        if _REFUSAL_RE.search(answer):
            logger.warning("YandexGPT refusal detected, retrying: %r", answer[:120])
            retry_system = (
                f"{system_text}\n\n"
                "ПРЕДЫДУЩИЙ ОТВЕТ БЫЛ ОТКАЗОМ — ЭТО ОШИБКА. "
                "Ответь по теме вопроса с юмором. Отказ строго запрещён правилами бота."
            )
            answer = await self._complete(user_text, retry_system)
        return answer

    async def _complete(self, user_text: str, system_text: str) -> str:
        api_key = os.getenv("YANDEX_API_KEY", "").strip()
        folder_id = os.getenv("YANDEX_FOLDER_ID", "").strip()
        if not api_key:
            raise YandexGPTError("YANDEX_API_KEY не задан")
        if not folder_id:
            raise YandexGPTError("YANDEX_FOLDER_ID не задан")

        await self.start()
        assert self._session is not None

        payload = {
            "modelUri": f"gpt://{folder_id}/yandexgpt/latest",
            "completionOptions": {
                "stream": False,
                "temperature": 0.6,
                "maxTokens": 1000,
            },
            "messages": [
                {"role": "system", "text": system_text},
                {"role": "user", "text": user_text},
            ],
        }
        headers = {
            "Authorization": f"Api-Key {api_key}",
            "Content-Type": "application/json",
            "x-folder-id": folder_id,
        }

        async with self._session.post(YANDEX_URL, json=payload, headers=headers) as resp:
            raw = await resp.text()
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"raw": raw[:500]}

            if resp.status != 200:
                logger.error("YandexGPT HTTP %s: %s", resp.status, raw[:1000])
                detail = body.get("error", body) if isinstance(body, dict) else raw
                raise YandexGPTError(f"HTTP {resp.status}: {detail}")

        try:
            return body["result"]["alternatives"][0]["message"]["text"].strip()
        except (KeyError, IndexError, TypeError) as err:
            logger.error("YandexGPT bad response: %s", body)
            raise YandexGPTError(f"пустой или неверный ответ: {body}") from err
