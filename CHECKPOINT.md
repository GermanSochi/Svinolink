# Checkpoint: рабочая версия Svinolink

**Тег для отката:** `working-2026-05-26` (коммит `2ed4ef7`)

```bash
git checkout working-2026-05-26
# или
git reset --hard working-2026-05-26
```

## Что работает на этом снимке

- Instagram Reels через instagrapi + Netscape cookies
- ИИ «Свин» через Yandex GPT (2 запроса/час)
- Mini App триггеров
- Retry при таймаутах Instagram (15s, 2 попытки)

## Env на Render

- `BOT_TOKEN`, `WEBHOOK_BASE_URL`
- `YANDEX_API_KEY`, `YANDEX_FOLDER_ID`
- `data/cookies.txt` в образе Docker

## После этого checkpoint

Добавлена память чата Supabase (`chat_history`).
