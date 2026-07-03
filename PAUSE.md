# Svinolink — Instagram включён, всё остальное выключено

**Сейчас:** Instagram **включён** (`INSTAGRAM_PAUSED=0`). Реакции на тег "свин" **отключены** пока AI выключен.

**Выключено:** AI (Yandex GPT), поиск в интернете, игры, память чата (Supabase), команды файлов (/tts, /scrape и т.д.).

---

## Что работает

- Скачивание видео по ссылке Instagram (reel/post) → reply с подписью (до 3 попыток при ошибках)
- Триггеры (встроенные из `triggers.json`)
- Управление триггерами из чата (добавить/удалить/править)
- Админ-панель (`/admin`)

## Что выключено (флаги в `.env` / `config.py`)

| Фича | Флаг | Значение |
|-------|------|----------|
| AI (Yandex GPT) | `AI_ENABLED` | `false` |
| Поиск в интернете | `WEB_SEARCH_ENABLED` | `false` |
| Игры | `GAMES_ENABLED` | `false` |
| Память чата | `MEMORY_ENABLED` | `false` |
| Команды файлов | `SKILLS_TOOLS_ENABLED` | `false` |

---

## Когда захочешь включить обратно

1. В `.env` добавь нужные флаги:
   - `AI_ENABLED=1` + `YANDEX_API_KEY=...` — AI-режим
   - `WEB_SEARCH_ENABLED=1` — поиск в интернете
   - `GAMES_ENABLED=1` — игры (требует AI)
   - `MEMORY_ENABLED=1` + `SUPABASE_DATABASE_URL=...` — память чата
   - `SKILLS_TOOLS_ENABLED=1` — команды /tts, /scrape, /pdf и т.д.
2. **Redeploy** на Render

## Включение Instagram (если выключен)

- `INSTAGRAM_PAUSED=1` — Instagram выключен
- `INSTAGRAM_PAUSED=0` — включён
- Нужны cookies в `data/cookies.txt` на сервере

---

## Render (чеклист)

| Переменная | Сейчас |
|------------|--------|
| `INSTAGRAM_PAUSED` | `0` (включён) |
| `AI_ENABLED` | не задан (выключен) |
| `WEB_SEARCH_ENABLED` | не задан (выключен) |
| `GAMES_ENABLED` | не задан (выключен) |
| `MEMORY_ENABLED` | не задан (выключен) |
| `SKILLS_TOOLS_ENABLED` | не задан (выключен) |
