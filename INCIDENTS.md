# Incident Log — Svinolink Bot

## 2026-07-01: TelegramConflictError — Bot не отвечал ~4 часа

**Симптомы:**
- Бот не обрабатывал сообщения (Instagram ссылки, /start)
- Бесконечный цикл ретраев (3353 попытки в логе)
- Health check не проходил на Render

**Root Cause (цепочка):**
1. Два polling-инстанса одновременно → `Conflict: terminated by other getUpdates request`
2. Webhook установлен поверх → `Conflict: terminated by setWebhook request`
3. Webhook застрял на серверах Telegram → `Conflict: can't use getUpdates while webhook is active`
4. Порт не совпадал: Dockerfile/render.yaml = 8080, Render ждал 10000 → health check fail

**Что сломалось:**
- `server_runner.py` — нет агрессивной очистки webhook перед polling
- `Dockerfile` — хардкод `PORT=8080` вместо `10000`
- `render.yaml` — `PORT=8080` вместо `10000`

**Исправления:**
1. Удалён webhook через Telegram API (`deleteWebhook(drop_pending_updates=True)`)
2. Добавлена агрессивная очистка webhook с 3 попытками в `server_runner.py`
3. Добавлен pre-polling webhook cleanup в `run_polling_with_http()`
4. Порт исправлен на `10000` в Dockerfile и render.yaml
5. Куки обновлены в `data/cookies.txt`

**Превентивные меры (добавлены):**
- `test_webhook_conflict.py` — тест проверки конфликта webhook/polling
- `test_port_config.py` — тест совпадения портов
- `server_runner.py` — двойная страховка webhook cleanup

**Статус:** RESOLVED
**Время простоя:** ~4 часа
**Влияние:** Бот не отвечал на все входящие сообщения

---

## 2026-07-08: Instagram Stories не скачиваются + мусор в чате

**Симптомы:**
- Reels скачивались нормально, Stories — нет
- Бот отправлял "Instagram не пускает — сессия на сервере протухла" каждому пользователю
- Спам в чате при каждом запросе Stories

**Root Cause:**
1. **Stories API endpoint** — код использовал `www.instagram.com/api/v1/media/{id}/info/`, а Stories требуют `i.instagram.com/api/v1/media/{id}/info/`
2. **Stories ID** — Stories используют прямой numeric ID (3935441032632448198), а не shortcode
3. **Ошибка cookies** — бот отправлял ошибку пользователю вместо того чтобы молча логировать

**Исправления:**
1. `instagram_download.py` — исправлен `_download_via_private_api()`: Stories теперь используют `i.instagram.com` и прямой numeric ID
2. `chat_handlers.py` — ошибка cookies теперь молча логируется + уведомление админу без спама в чат
3. `chat_handlers.py` — рекламная ссылка обновлена на `clck.ru/3UaRGo` (донаты)

**Интеграция Neon PostgreSQL:**
- Подключена облачная БД Neon (`neondb`) для хранения `chat_history` и `chat_triggers`
- `SUPABASE_DATABASE_URL` на Render обновлена с Neon connection string
- Бот работает 24/7 без зависимости от локального ПК

**Коммиты:**
- `fe540de` — fix: Stories API endpoint
- `12d5537` — fix: remove cookie error spam + new donation link
- `b872d8d` — chore: use short donation link

**Статус:** RESOLVED
**Время простоя:** Stories не работали ~1 день
**Влияние:** Stories не скачивались, спам ошибок в чате
