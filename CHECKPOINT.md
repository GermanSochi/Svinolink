# Checkpoint: рабочая версия Svinolink

**Тег для отката:** `working-2026-07-08` (коммит `6d0bece`)

```bash
git checkout working-2026-07-08
# или
git reset --hard working-2026-07-08
```

## Что работает на этом снимке

- Instagram Reels через instagrapi + Netscape cookies
- Instagram Stories через `i.instagram.com` endpoint ✅
- ИИ «Свин» через Yandex GPT (2 запроса/час)
- Mini App триггеров
- Neon PostgreSQL (chat_history, chat_triggers)
- Реклама: донаты через clck.ru/3UaRGo

## Env на Render

- `BOT_TOKEN`, `WEBHOOK_BASE_URL`
- `YANDEX_API_KEY`, `YANDEX_FOLDER_ID`
- `INSTAGRAM_COOKIES_JSON` — pipe-separated format
- `SUPABASE_DATABASE_URL` — Neon PostgreSQL

## После этого checkpoint

- Stories работают через `i.instagram.com`
- Ошибки cookies молча логируются (без спама в чат)
- Neon PostgreSQL подключена для persistent storage
