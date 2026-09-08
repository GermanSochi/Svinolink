# Svinolink — Журнал сессий

---

## 2026-09-08 — Фикс Instagram: карусели, фото, описание

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
