# Svinolink — пауза Instagram

**Сейчас:** Instagram **выключен**. Бот **не логинится** и **не использует** твои cookies.

Работает: **Свин**, память чата, поиск, игры, файлы.

---

## Что сделано

- `data/cookies.txt` удалён из проекта (и из git)
- По умолчанию `INSTAGRAM_PAUSED=1`
- При старте на паузе бот **стирает** `cookies.txt` и `instagram_session.json` с диска сервера

---

## Когда захочешь снова видео из Instagram

1. Экспортируй свежие cookies из браузера (формат Netscape) → файл **`data/cookies.txt`**
2. На **Render** → Environment:
   - `INSTAGRAM_PAUSED` = **`0`** (или `false`)
   - убери `INSTAGRAM_USERNAME` / `INSTAGRAM_PASSWORD`, если были (логином лучше не пользоваться)
3. Положи `cookies.txt` на сервер (volume `/app/data/` или деплой — лучше **только на диск Render**, не в GitHub)
4. **Redeploy** / перезапуск сервиса

После этого ссылка на reel/post снова должна отдавать видео.

---

## Render (чеклист)

| Переменная | Сейчас (пауза) | Когда включишь |
|------------|----------------|----------------|
| `INSTAGRAM_PAUSED` | `1` | `0` |
| `INSTAGRAM_COOKIES_FILE` | `/app/data/cookies.txt` | то же |
| `INSTAGRAM_USERNAME` | пусто | пусто |
| `INSTAGRAM_PASSWORD` | пусто | пусто |

**Важно:** не храни cookies в публичном репозитории GitHub.
