# Svinolink — Context & Continuation Log

## Session Sept13,2025 — Download Chain Lock Bottleneck Fix

### Проблема
Когда пользователь кидает 2 ссылки Instagram подряд (в пределах минуты), вторая не обрабатывалась.

**Root cause:** `_instagrapi_lock` (`asyncio.Lock`) держался на протяжении ВСЕГО `asyncio.to_thread(_sync)` (20-60 сек). Старый порядок скачивания для `/p/` URL-ов:
```
private API → instagrapi photo (🔒 LOCK 20-60с!) → embed → page → oEmbed → yt-dlp
```
Первая ссылка захватывала лок на instagrapi photo (шаг2), вторая ссылка **не могла пройти шаги3-7** потому что тоже упиралась в шаг2. Если ожидание + скачивание > 90с → таймаут → "что-то пошло не так".

### Что сделано
1. **Переставлен порядок скачивания** — instagrapi (lock-holding) пути перемещены в самый конец:
   ```
   Path1:   private API        (~1-2s)  ✅ параллельно
   Path1.5: embed/page/oEmbed  (~2-5s)  ✅ параллельно
   Path2:   yt-dlp fast        (~1-3s)  ✅ параллельно
   Path3:   yt-dlp full        (~3-8s)  ✅ параллельно
   Path3.5: embed photo        (no lock) ✅ параллельно
   Path3.7: instagrapi photo   (🔒 LOCK — последний шанс для фото)
   Path4:   instagrapi video   (🔒 LOCK — абсолютный фоллбэк)
   ```
2. **Добавлено логирование lock contention** — если ожидание на локе >100мс, логируется `lock-wait` / `lock-held` с миллисекундами.
3. **Commit** `e5a84b4` pushed → Render auto-deploy.

### Файлы
- `C:\Claude\Svinolink\instagram_download.py` — основной файл, изменена функция `download_instagram_video()` (строки1202-1353)

### Тест (TODO)
- [ ] Кинуть 2 ссылки Instagram одновременно → обе должны обработаться
- [ ] Проверить Render логи на `lock-wait` / `lock-held` сообщения
- [ ] Если contention всё ещё есть → рассмотреть per-request instagrapi sessions

### Коммиты (хронология, последний сверху)
```
e5a84b4 fix: reorder download chain — move instagrapi (lock-holding) paths to end, enable parallel downloads
0232f42 fix: critical Instagram download bugs — orchestrator, locks, race conditions, timeouts
```

### Диаграмма потока (для следующей сессии)
```
User sends URL1 + URL2
  ├─ URL1: chat_handlers.py → _download_with_retries() → download_instagram_video(url1)
  │   └─ Path1 (private API) → success ✅ (no lock, ~1-2s)
  ├─ URL2: chat_handlers.py → _download_with_retries() → download_instagram_video(url2)
  │   └─ Path1 (private API) → success ✅ (no lock, ~1-2s, PARALLEL with URL1!)
  └─ Both sent to user ✅
```

### Следующие шаги (если тест не пройден)
1. Проверить логи Render — есть ли `lock-wait` / `lock-held`
2. Если private API падает для обеих ссылок → обе уходят на instagrapi → serialize again
3. Варианты решения:
   - Per-request instagrapi client (отдельный клиент на каждый запрос)
   - Использовать threading.Lock вместо asyncio.Lock (чтобы не блокировать event loop)
   - Увеличить DOWNLOAD_TOTAL_TIMEOUT_SEC с90 до120

---

## Previous Sessions

## Session Sept12,2025 — Download Chain Lock Bottleneck Fix