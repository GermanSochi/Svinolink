## Svinolink

Минималистичный бот-репостер для чата: кидаете ссылку на **Instagram Reels/пост** или **YouTube Shorts** → бот молча отвечает **видео-файлом** (reply) с подписью:

> `Svinolink любит донаты`

### Важные правила
- Бот реагирует **только** на Instagram (`/reel/` и `/p/`) и YouTube Shorts (`/shorts/`).
- Любые другие ссылки (TikTok и т.п.) **игнорируются**.
- В чат не пишет “скачиваю / ошибка”. При ошибке просто молчит.

### Триггеры в группе (настраиваются)
Файл `triggers.json` (или команды в личке для админа):
- `да` → `пизда` (**1 раз в сутки** на человека)
- `300` / `триста` / `стристо` → `отсоси у тракториста`

Админ в личке:
- `/myid` — узнать свой ID → вписать в `.env` как `ADMIN_IDS=...`
- `/addtrigger слово ответ` или `... daily` — добавить триггер
- `/triggers` — список
- `/deltrigger id` — удалить

**Обязательно в @BotFather:** `/setprivacy` → **Disable**, иначе в группе бот не увидит «да» без @упоминания.

---

## Локальный запуск (polling)

```powershell
cd c:\Claude\Svinolink
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
$env:BOT_TOKEN="...токен..."
python main.py
```

---

## 24/7 без ПК (webhook)

В проде используем **webhook**. Нужен публичный HTTPS домен от хостинга.

Переменные окружения:
- `BOT_TOKEN` – токен бота
- `WEBHOOK_BASE_URL` – публичный URL сервиса, например `https://svinolink.onrender.com`
- `WEBHOOK_PATH` – необязательно; если не задан, берётся токен (секретный путь)
- `PORT` – порт (хостинг задаёт сам)

### Render (24/7, ПК можно выключить)

**OAuth scopes GitHub вам не нужны** — это для сторонних OAuth-приложений.
Render при логине через GitHub сам просит доступ к репозиториям (GitHub App).
Документация про scopes: [Scopes for OAuth apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps).

1. Репозиторий: https://github.com/GermanSochi/Svinolink
2. Render → **New** → **Web Service** → подключить GitHub → выбрать `Svinolink`
3. **Environment: Docker** (есть `Dockerfile`)
4. После создания скопируйте URL сервиса, например `https://svinolink-bot.onrender.com`
5. **Environment** (Settings → Environment):

| Переменная | Значение |
|------------|----------|
| `BOT_TOKEN` | токен от @BotFather |
| `WEBHOOK_BASE_URL` | `https://<имя>.onrender.com` **без** слэша в конце |
| `YANDEX_API_KEY` | ключ Yandex Cloud (опционально, для `/ai`) |
| `YANDEX_FOLDER_ID` | `b1g3l4knr91bsq8mqhaq` |
| `ADMIN_IDS` | ваш Telegram ID (`/myid` в боте) |
| `PORT` | `8080` (Render часто подставляет сам) |

6. **Manual Deploy** → Deploy latest commit

Проверка: открой в браузере `https://<имя>.onrender.com/health` — должно быть `ok`.

После деплоя бот на webhook; локальный `python main.py` на ПК **останови**, иначе будет конфликт.

---

## Аватарка бота

Аватар ставится через `@BotFather`:
- `/setuserpic` → выбрать бота → загрузить картинку.

Промпт для генерации аватарки (если делаете через любой генератор):
> “Мультяшный поросёнок-маскот Svinolink в тёмных очках, держит значок-цепочку (link) и смартфон с иконкой play. Векторный плоский стиль, толстый контур, читаемо в маленьком размере, фон тёмно-синий, без текста.”

