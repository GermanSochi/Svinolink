# Instagram Photo Fix — Context for Next Session

## Problem
Bot on Render fails to download Instagram **photo** posts (`/p/` URLs). Videos work fine.
**Test URL:** `https://www.instagram.com/p/DcbB6jbiAfe`

## Timeline
- 11:00 AM — photos WORKED (some method succeeded)
- 14:56 PM — photos FAILED (all methods return None/error)
- ~15:30 PM — all fixes deployed (be4e0f1) but NOT yet tested

## Root Causes Found & Fixed

### 1. NameError — cookies NEVER passed to embed/page (CRITICAL)
Code called `_load_ig_session()` and `_insta_cookies_to_aio()` which **DON'T EXIST**.
Fixed to use `_load_cookies_dict()` which exists.

### 2. yt-dlp `--write-thumbnail` doesn't work for IG photos
yt-dlp Instagram extractor only handles video. Replaced with `_download_oembed_thumbnail()`.

### 3. `_strip_cdn_params` could break CDN URLs
Added fallback: try stripped URL first, if 400+, try original with query params.

### 4. Cookies now passed to all embed/page/image requests
All aiohttp requests in embed/page functions now get `cookies=` parameter.

## Current Photo Download Chain (be4e0f1)
```
1. Private API    → /api/v1/media/{id}/info/ (needs sessionid cookie)
2. embed page     → /p/SHORTCODE/embed/ + og:image + <img> parsing (+cookies)
3. page HTML      → /p/SHORTCODE/ + regex cdninstagram/fbcdn URLs (+cookies)
4. oEmbed direct  → api.instagram.com/oembed/ + thumbnail download (+cookies)
5. ❌ Error with details
```

## Last Known Render Logs (before cccb263)
```
embed→None; page→None; ytdlp-thumb→No video formats found
```
After cccb263: NOT YET TESTED. Added instagrapi fallback + detailed photo logging.

## What cccb263 Added
1. **instagrapi photo_download** (Path 1.5): Uses same client/session as video download
   - `cl.media_info(media_pk)` → gets `thumbnail_url`
   - `cl.photo_download_by_url(url)` → plain `requests.get` (no IG headers)
   - Runs in `asyncio.to_thread` to not block event loop
2. **Detailed logging in private API photo path**:
   - `private-api photo[N] download status=XXX` — CDN response status
   - `private API: no image URL for X (media_type=X, keys=[...])` — what API returned

## Possible Remaining Issues (check Render logs from be4e0f1)

### A. Cookies Expired
Private API returns 401 → None. Same cookies for embed/page → still blocked.
**Fix:** Update `INSTAGRAM_COOKIES_JSON` env var on Render.

### B. Instagram IP Block (no proxy)
If `PROXY_ENABLED != "1"` on Render, all requests go directly from Render IP.
**Fix:** Set `PROXY_ENABLED=1` and `PROXY_URL` on Render.

### C. embed page returns login wall
Even with cookies, embed page might show login wall.
**Check:** Look for `embed: got login wall` in logs.

### D. oEmbed returns tiny thumbnail (~150x150)
oEmbed thumbnail might be too small. `_strip_cdn_params` might fix size or break URL.
**Check:** Look for `oembed-direct OK` in logs.

## Key Functions Reference
| Function | Status | Purpose |
|----------|--------|---------|
| `_load_cookies_dict()` | ✅ EXISTS | Loads cookies from file or env |
| `_load_ig_session()` | ❌ DOES NOT EXIST | Was causing NameError |
| `_insta_cookies_to_aio()` | ❌ DOES NOT EXIST | Was causing NameError |
| `_download_via_private_api()` | ✅ | Private API with IG mobile headers |
| `_download_photo_via_embed()` | ✅ | Parse embed page HTML for images |
| `_download_photo_via_page()` | ✅ | Parse post page HTML for CDN URLs |
| `_download_oembed_thumbnail()` | ✅ NEW | Direct oEmbed API → thumbnail download |
| `_download_photo_via_instagrapi()` | ✅ NEW | instagrapi photo_download via thread pool |
| `_strip_cdn_params()` | ✅ | Remove size limits from CDN URLs |
| `_extract_shortcode()` | ✅ | Extract shortcode from IG URL |
| `_aiohttp_session()` | ✅ | aiohttp session with proxy if enabled |

## Env Variables on Render
- `INSTAGRAM_COOKIES_JSON` — session cookies (key=value|key=value format)
- `PROXY_ENABLED` — "1" to enable SOCKS5 proxy
- `PROXY_URL` — SOCKS5 proxy URL (default: socks5h://127.0.0.1:10808)
- `INSTAGRAM_PAUSED` — "1" to disable Instagram downloads

## Git Commits (this session, newest first)
```
cccb263 feat: add instagrapi photo_download fallback + detailed photo API logging
be4e0f1 fix: use existing _load_cookies_dict + add oembed-direct fallback
838548e fix: pass cookies to embed/page requests + strip CDN path params
be3129d fix: add yt-dlp thumbnail for photo posts + detailed error logging
d98f45a fix: add yt-dlp fallback for photo URLs + better error messages
eb607dd fix: strip CDN query params for full-size Instagram images
97b91f3 refactor: replace requests with async aiohttp + UA rotation
```

## Current Photo Download Chain (cccb263)
```
1. Private API    → /api/v1/media/{id}/info/ (aiohttp, custom headers + cookies)
                   → image_versions2.candidates[0].url → download
2. instagrapi     → cl.media_info() + cl.photo_download_by_url() (requests, no IG headers)
                   → NEW: uses same client that downloads videos
3. embed page     → /p/SHORTCODE/embed/ + og:image + <img> parsing (+cookies)
4. page HTML      → /p/SHORTCODE/ + regex cdninstagram/fbcdn URLs (+cookies)
5. oEmbed direct  → api.instagram.com/oembed/ + thumbnail download (+cookies)
6. ❌ Error with details
```

## Next Steps

### Priority 1: Check Render Logs from be4e0f1
Look for these log lines to understand what's happening:
- `embed: starting photo download for DcbB6jbiAfe, proxy=OFF, cookies=YES`
- `embed page status=XXX` (200=good, 401/403=blocked)
- `embed: got NNN chars HTML` (large=good, small=login wall)
- `oEmbed status=XXX` (200=good)
- `oembed-direct: status=XXX`
- `image download status=XXX` (200=good, 403=CDN rejected)

### Priority 2: Nuclear Option — instagrapi photo_download
If nothing works, use instagrapi's built-in photo download:
```python
from instagrapi import Client
cl = Client()
cl.load_settings(path)  # or login
media_pk = cl.media_pk_from_url(url)
path = cl.photo_download(media_pk)
```
Already installed, handles anti-ban internally.

### Priority 3: Proxy Setup
Instagram likely blocks Render IPs. A SOCKS5 proxy would fix ALL methods at once.