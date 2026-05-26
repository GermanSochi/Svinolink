from __future__ import annotations

import logging

import aiohttp

from config import settings

logger = logging.getLogger(__name__)
YANDEX_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


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

    async def reply(self, user_text: str, *, system: str) -> str:
        if not settings.yandex_api_key:
            raise YandexGPTError("YANDEX_API_KEY не задан")

        await self.start()
        assert self._session is not None

        payload = {
            "modelUri": f"gpt://{settings.yandex_folder_id}/{settings.yandex_model}",
            "completionOptions": {
                "stream": False,
                "temperature": 0.6,
                "maxTokens": "200",
            },
            "messages": [
                {"role": "system", "text": system},
                {"role": "user", "text": user_text},
            ],
        }
        headers = {
            "Authorization": f"Api-Key {settings.yandex_api_key}",
            "Content-Type": "application/json",
            "x-folder-id": settings.yandex_folder_id,
        }

        async with self._session.post(YANDEX_URL, json=payload, headers=headers) as resp:
            body = await resp.json()
            if resp.status != 200:
                logger.error("YandexGPT %s: %s", resp.status, body)
                raise YandexGPTError("AI недоступен")

        try:
            return body["result"]["alternatives"][0]["message"]["text"].strip()
        except (KeyError, IndexError, TypeError) as err:
            raise YandexGPTError("пустой ответ AI") from err
