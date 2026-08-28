# Checkpoint: рабочая версия Svinolink

**Тег для отката:** `f20e0a6` (2026-08-29)

```bash
git checkout f20e0a6
# или
git reset --hard f20e0a6
```

## Что работает на этом снимке

### Основной бот
- Instagram Reels через instagrapi + yt-dlp + private API (4 метода с фоллбэком)
- Instagram Stories через `i.instagram.com` endpoint
- ИИ «Свин» через Yandex GPT (2 запроса/час)
- Mini App триггеров
- Neon PostgreSQL (chat_history, chat_triggers)
- Реклама: донаты через clck.ru/3UaRGo
- Бот реагирует на IG ссылки в чате → скачивает видео → отправляет в чат

### Watch Feeder (автопостинг)
- Фоновый цикл `watch_feed_loop` — каждый час берёт shortcode из кэша
- Кэш кандидатов (`data/wf_candidates.json`) — наполняется сканером + DuckDuckGo + встроенный seed
- **`_post_single`** — скачивает видео и отправляет **только видео** в канал (без текстовых ссылок)
- Расписание постинга: configurable `wf_post_start_hour`–`wf_post_end_hour` MSK
- Jitter между постами (Gumbel-распределение)
- `/wf_stop` — пауза автопостинга
- `/wf_start` — запуск автопостинга
- `/wf_run` — ручной пост из кэша
- `/wf_go` — быстрый пост 1 лучшего reel
- `/wf <keyword>` — поиск по бренду через DuckDuckGo
- `/wf_info` — полная информация
- `/wf_stats` — статистика (с статусом паузы)

### Ключевые файлы
- `watch_feeder.py` — автопостинг, кэш, кандидаты, `_post_single`
- `watch_discovery.py` — DuckDuckGo поиск reels
- `instagram_download.py` — скачивание видео (4 метода)
- `chat_handlers.py` — обработка IG ссылок в чате
- `admin_panel.py` — admin команды (/wf_*, /wf_stop, /wf_start)
- `server_runner.py` — webhook/polling + запуск фоновых задач

## Env на Render

- `BOT_TOKEN`, `WEBHOOK_BASE_URL`
- `YANDEX_API_KEY`, `YANDEX_FOLDER_ID`
- `INSTAGRAM_COOKIES_JSON` — pipe-separated format
- `SUPABASE_DATABASE_URL` — Neon PostgreSQL
- `WATCH_FEEDER_ENABLED=true`
- `WATCH_FEEDER_CHAT_IDS` — ID каналов через запятую

## Известные проблемы

- DDG полностью rate-limited на Render (429/timeout) — зависит от seed cache
- YandexGPT `chat_style` daily loop — HTTP 403 permission error (отдельная тема)

## История коммитов (автопостинг)

- `674eac4` — feat: /wf, /wf_info, /wf_stats, /wf_go, /wf_run
- `3bad679` — feat: 24/7 background scanner + cache
- `6c40dfa` — feat: DuckDuckGo search, убрана зависимость от instagrapi для discovery
- `e3098a8` — fix: убран instagram_is_active из главного цикла
- `12075b8` — fix: bypass posting window для теста, yt-dlp primary
- `e097735` — feat: bot реагирует на свои ссылки через feed_update
- `f95f9fb` — feat: _post_single скачивает+отправляет видео напрямую, /wf_stop, /wf_start
- `f20e0a6` — simplify: только видео, без текстовых ссылок-фоллбэков
