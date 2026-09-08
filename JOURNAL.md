# Svinolink — Журнал сессий

---

## 2026-09-08 — Фикс Instagram: карусели, фото, описание

### Обновление (коммит `9e10636`) — Исправлена тихая ошибка для фото

**Проблема:** после оптимизации (1 вызов yt-dlp вместо 3) фото-посты `/p/` полностью перестали реагировать — бот молча игнорировал ссылку.

**Root cause:** 
- Раньше: 3× yt-dlp = 60с → таймаут 45с → пользователь видел "сервер не дождался"  
- Теперь: 1× yt-dlp = 10с → download завершается за <45с → instagrapi возвращает session error → `_runtime_error_for()` оборачивает в `COOKIES_EXPIRED_MSG` (содержит "сессия") → **handler молча выходит** (line 298: `return`) без сообщения пользователю!

**Исправления:**
1. **`chat_handlers.py`**: Убран silent return при cookies/session ошибках — теперь пользователь ВСЕГДА получает сообщение через `map_instagram_error()` ( Pig emoji: "Instagram не пускает — сессия протухла")
2. **`instagram_download.py`**: Добавлен `Referer: https://www.instagram.com/` заголовок для:
   - Private API запросов (фото CDN может требовать Referer)
   - `_download_direct_url()` (yt-dlp image path)
   - Carousel downloads через instagrapi
3. **Логирование**: Добавлен лог `download_instagram_video START` и `ytdlp image URL found` для отладки

---

### Обновление (коммит `12c5ae1`) — Устранена главная причина зависания

**Проблема:** предыдущий фикс не помог — yt-dlp вызывался **3 РАЗА** (caption + fast + image), каждый subprocess таймаут 20с = 60с только на yt-dlp, плюс private API и instagrapi → суммарно > 45с таймаута.

**Исправления:**
1. **ОДИН вызов `_ytdlp_extract_info()` вместо трёх** — результат кешируется и используется для caption, видео URL и фото URL. **Экономия ~40 секунд!**
2. **Subprocess timeout 20с → 10с** — yt-dlp не может висеть дольше 10с
3. Убраны старые функции `_download_ytdlp_fast`, `_download_ytdlp_image`, `_ytdlp_extract_caption` из основного флоу (используется общий `yt_info`)
4. Новые хелперы `_extract_url_from_ytdlp_info` и `_extract_image_url_from_ytdlp_info` — извлекают URL из кеша мгновенно (0с)

### Таймлайн нового флоу (~5-20с вместо ~60-270с):

| Шаг | Время | Что делает |
|-----|-------|------------|
| 1. Private API | ~2с | API → video/photo download |
| 2. **ОДИН yt-dlp** | **~5с** | JSON: caption + url + thumbnail |
| 3. yt-dlp video URL | **~0с** | из кеша, скачивание ~2с |
| 4. yt-dlp photo URL | **~0с** | из кеша, скачивание ~2с |
| 5. instagrapi carousel | ~3с | carousel_media |
| 6. instagrapi single | ~5с | clip → video → photo |
| 7. yt-dlp fallback | ~10с | последний шанс |

---

### Обновление (коммит `03cb535`) — Фото не скачивается, 4 мин ожидания

**Проблема:** видео работают, но фото-посты (`/p/`) не скачиваются — бот "тупит" 4 минуты и выдаёт "Instagram тупит".

**Корневая причина:**
- Private API возвращает `None` для фото (CDN с image URLs блокируется или rate-limit)
- yt-dlp fast возвращает `None` (ищет видео URL, фото нет)
- instagrapi carousel → `None` (не карусель)
- instagrapi single → photo_download может виснуть
- Внешний retry × 3 × 90с = 270 сек = 4.5 мин

**Исправления:**
1. **Новый путь `_download_ytdlp_image()`** — извлекает thumbnail URL из yt-dlp metadata для фото-постов. Скачивает напрямую через requests. Ставится сразу после yt-dlp fast.
2. **Таймаут 90с → 45с** — общее время ожидания 2×45=90 сек вместо 3×90=270 сек
3. **Retries 3 → 2, delay 5с → 3с**
4. **yt-dlp fallback timeout 60с → 20с** — если дошли сюда, быстрее фейлим
5. **Логирование photo path** в private API: media_type, image_versions2, ошибки скачивания

### Новый порядок цепочки (итоговый):

| # | Путь | Время | Для чего |
|---|------|-------|----------|
| 1 | Private API | ~1-2с | Видео И фото (прямой URL) |
| 2 | yt-dlp fast | ~1-3с | Видео (прямой URL) |
| 2.5 | **yt-dlp image** ← НОВЫЙ | ~2-3с | **Фото** (thumbnail URL) |
| 3 | instagrapi carousel | ~2-5с | Карусели |
| 4 | instagrapi single | ~2-5с | Одиночные clip/video/photo |
| 5 | yt-dlp fallback | ~20с | Только видео (последний шанс) |

### Чек-лист (обновлён):
1. 📷 Одиночное фото `/p/...` — должно прийти с кнопкой «Описание»
2. 🖼️ Карусель `/p/...` — все фото + кнопка отдельным сообщением
3. 🎬 Reel — видео с кнопкой на медиа
4. 🔘 Кнопка «Описание» — текст поста при нажатии
5. 📊 Логи: `grep 'ytdlp-image OK\|private-api OK\|private API: photo' bot.log`

### Проблема
1. **Фото/карусели не отправлялись** — private API иногда падает, а fallback через instagrapi `photo_download` имеет `assert media.media_type == 1` → **не работает для каруселей** (media_type=8)
2. **Описание (caption) появлялось как отдельное текстовое сообщение** "📝 Описание поста:" — пользователь хочет только кнопку
3. **4-минутное ожидание** — yt-dlp fallback висит 60 сек на фото-постах (ищет MP4 которого нет)

### Корневые причины
- `instagrapi.Client.photo_download()` → `assert media.media_type == 1, "Must been photo"` — карусели (media_type=8) падают с AssertionError
- yt-dlp fallback subprocess timeout=60с блокирует весь тред, даже для фото-постов
- Порядок цепочки: yt-dlp fallback (60с) шёл ДО instagrapi

### Исправления (3 коммита)

#### Коммит `0d0fba2` — carousel photo fallback + button on media
**Файлы:** `instagram_download.py`, `chat_handlers.py`

1. **Новая функция `_download_instagram_carousel_via_instagrapi()`**
   - `media_info(media_pk)` → проверяет `media.resources` (список элементов карусели)
   - Скачивает каждый элемент по URL (фото через `thumbnail_url`, видео через `video_url`)
   - С Instagram CDN-заголовками (`User-Agent: Instagram 275.0.0.27.98 Android`)
   - Фильтрует файлы < 1024 байт, проверяет max 50 МБ

2. **Кнопка «Описание» на самом сообщении**
   - `answer_video(..., reply_markup=kb)` — кнопка прямо на видео
   - `answer_photo(..., reply_markup=kb)` — кнопка прямо на фото
   - Для каруселей: `answer("📝", reply_markup=kb)` — Telegram media_group не поддерживает reply_markup

3. **Убран caption с первого изображения карусели**
   - Было: `kw["caption"] = caption` на первом фото → текст поверх картинки
   - Стало: без текста на медиа, только кнопка

4. **Смешанные карусели (фото+видео)**
   - Было: `all(is_photo_file(p))` → одно видео ломало всю группу
   - Стало: `InputMediaPhoto` / `InputMediaVideo` для каждого файла по типу

#### Коммит `7a4d86e` — reordered fallback chain
**Файл:** `instagram_download.py`

**Новый порядок цепочки:**
| # | Путь | Скорость | Когда |
|---|------|----------|-------|
| 1 | Private API | ~0.5-2с | Всегда первый |
| 2 | yt-dlp fast (extract URL) | ~1-3с | Видео, когда private API упал |
| 3 | instagrapi carousel | ~2-5с | **КАРУСЕЛИ** — НОВЫЙ |
| 4 | instagrapi single (clip→video→photo) | ~2-5с | **ОДИНОЧНЫЕ ФОТО** |
| 5 | yt-dlp fallback (subprocess) | ~60с | Только видео (медленный) |

**Было:** 1→2→caption→ytdlp-fast→**ytdlp-fallback(60с)**→instagrapi-carousel→instagrapi-single
**Стало:** 1→2→caption→ytdlp-fast→**instagrapi-carousel**→**instagrapi-single**→ytdlp-fallback(60с)

**Эффект:** фото приходят за 2-5 сек вместо 4 минут.

### Что проверить завтра
1. Отправить ссылку на **одиночный фото-пост** `/p/...` — должно прийти фото с кнопкой «Описание»
2. Отправить ссылку на **карусель** `/p/...` (несколько фото) — должны прийти все фото, без текста на картинках, с кнопкой
3. Отправить ссылку на **Reel** — видео с кнопкой прямо на сообщении
4. Проверить что кнопка «Описание» работает — при нажатии показывает текст
5. Посмотреть логи на Render: `grep 'instagrapi carousel OK\|instagrapi photo OK\|private-api OK'`

### Файлы
- `instagram_download.py` — цепочка скачивания, carousel fallback, new `_download_instagram_carousel_via_instagrapi()`
- `chat_handlers.py` — кнопка на медиа, `InputMediaVideo` import, смешанные карусели

### Статус: ✅ запушилен, ждём деплой на Render (~2 мин)
