# vidspy

Android app. Paste (or share) a social-media link, **watch it directly** in the app, **download** it to your gallery. No account, no login.

- Kotlin UI (native Android views)
- Python backend running on-device via [Chaquopy](https://chaquo.com/chaquopy/)
- Desktop app (Windows / macOS / Linux, [Eel](https://github.com/python-eel/Eel) GUI) for manual backend testing
- **One shared backend package** (`backend/vidspy_backend`) used unchanged by both the APK and the desktop app
- Platforms: **Instagram**, **TikTok**, **LinkedIn**, **YouTube**, plus any direct **`.m3u8`** (HLS) link
- Videos and images, including multi-item carousels

## Features

| Feature | Notes |
|---|---|
| Direct watch | Streams the CDN URL in a `VideoView`. Falls back to a cached download if the player rejects the stream. |
| Download | Saves to `Movies/vidspy` (video) or `Pictures/vidspy` (image) through MediaStore. |
| Download all | Saves every item of a carousel / multi-media post. |
| Share target | Share a link from the Instagram / TikTok / LinkedIn app straight to vidspy. |
| Anonymous only | No cookies, no login. Private or login-walled posts fail with a clear message. |

## Layout

```
backend/vidspy_backend/   shared Python package (api.py, extractors.py)  <- APK + desktop
app/                      Android app (Kotlin); Gradle adds ../backend as Chaquopy source dir
desktop/                  Eel GUI: app.py + web/index.html
assets/                   icon master (make_icons.py -> svg/png/ico, Android mipmaps, favicon)
tests/                    backend tests (pytest)
```

## Architecture

```
┌─ Android (Kotlin) ──────────────┐   ┌─ Desktop (Eel) ────────────────┐
│ MainActivity  UI + viewer       │   │ web/index.html  UI + viewer    │
│ Backend       Chaquopy bridge   │   │ app.py  Eel calls + /media/<id>│
│ MediaSaver    → gallery         │   │         proxy (headers, Range) │
└───────────────┬─────────────────┘   └───────────────┬────────────────┘
                │ JSON strings                        │ JSON/dicts
┌───────────────▼─────────────────────────────────────▼────────────────┐
│ backend/vidspy_backend  (SAME package in both)                       │
│ api.py         resolve(text) / download(item, dir), platform logic   │
│ extractors.py  yt-dlp · gallery-dl · HTML scraper                    │
└──────────────────────────────────────────────────────────────────────┘
```

### Backend API

```python
resolve(text)  -> '{"platform": "tiktok", "url": "...", "items": [Item, ...]}'
download(item_json, dest_dir) -> "/abs/path/file.mp4"

Item = {type: "video"|"image", url, ext, title, thumbnail, headers}
```

`headers` holds whatever the CDN needs (User-Agent, Referer, TikTok session cookie), and is used for both streaming and downloading.

### Extractor chain per platform

First extractor returning media wins; errors fall through to the next.

| Platform | 1st | 2nd |
|---|---|---|
| Instagram | yt-dlp (reels, video posts) | gallery-dl (photo posts, carousels) |
| TikTok | yt-dlp (videos) | gallery-dl (photo slideshows) |
| LinkedIn | built-in HTML scraper (`requests` + `beautifulsoup4`) | — |
| YouTube | yt-dlp (progressive mp4, else HLS) | — |
| `.m3u8` URL | built-in HLS validator (`m3u8`) | — |

LinkedIn has no yt-dlp extractor for public posts, so vidspy reads `<video data-sources>` / `og:video` / `og:image` from the public post page itself.

Android has no ffmpeg, so vidspy never muxes. It prefers single-file progressive HTTP formats and falls back to HLS. HLS is downloaded by `hls.py`: parse with [`m3u8`](https://github.com/globocom/m3u8), pick the highest-bandwidth variant, concatenate segments into `.ts` (or `.mp4` for fragmented-MP4 playlists). Separate-audio HLS variants, DASH and AES-encrypted HLS are not supported. YouTube mostly offers progressive formats only up to 360p, so expect low quality there.

## Libraries

| Library | Why |
|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Most-starred, most actively maintained media extractor. |
| [gallery-dl](https://github.com/mikf/gallery-dl) | Best-in-class for image posts/carousels, which yt-dlp does not handle. |
| [requests](https://github.com/psf/requests) | HTTP downloads and page fetch. |
| [beautifulsoup4](https://pypi.org/project/beautifulsoup4/) | HTML parsing for the LinkedIn scraper. |
| [m3u8](https://github.com/globocom/m3u8) | HLS playlist parsing. |
| [hls.js](https://github.com/video-dev/hls.js) | HLS playback in the desktop webview (loaded from CDN; Android plays HLS natively). |
| [Chaquopy](https://chaquo.com/chaquopy/) | Runs CPython inside the Android app. |

Extractors break when platforms change. Fix is usually bumping `yt-dlp` / `gallery-dl` in `app/build.gradle` (versions are unpinned, so a rebuild picks up the latest).

## Build

Requirements: JDK 17+, Android SDK (API 35), and a host Python 3.8+ on `PATH` (Chaquopy uses it at build time).

```bash
./gradlew assembleDebug
# app/build/outputs/apk/debug/app-debug.apk
```

CI (`.github/workflows/build.yml`) builds the APK on every push.

Only `arm64-v8a` is packaged (see `abiFilters`). Add `x86_64` for emulators.

## Test backend

Pure-Python tests run on the host, no Android needed. For manual end-to-end tests use the desktop app below.

```bash
pip install pytest requests beautifulsoup4
pytest tests
```

## Desktop app (manual backend testing)

```bash
pip install -r desktop/requirements.txt     # eel + backend deps (app.py imports ../backend directly)
python desktop/app.py                        # GUI (Chrome/Edge app window, else default browser)
python desktop/app.py --cli <url>            # print resolved media JSON, no GUI
```

Same flow as the phone: paste link, watch, prev/next, download (to `~/Downloads/vidspy`). The browser can't set `Referer`/`Cookie` on media requests, so `app.py` serves media through a local `/media/<id>` proxy that adds the backend's headers and forwards `Range` for seeking. Any backend change (extractors, headers, fallbacks) shows up in both apps because both import `vidspy_backend`.

## Usage

1. Open vidspy, paste a link (or share one to vidspy from another app).
2. Tap **Watch**. Video streams; images load. Use `<` `>` for carousels.
3. Tap **Download** (current item) or **Download all**.

## Icon

An eye (spy) with a play button as the iris. `python assets/make_icons.py` regenerates every size from one geometry: `assets/icon.{svg,png,ico}`, the Android adaptive-icon vectors and legacy mipmaps, and the desktop favicon. The Windows exe uses `assets/icon.ico`.

## Limitations

- Anonymous only: private accounts, age/region-gated and login-walled posts fail. Instagram increasingly login-walls anonymous requests; expect failures there.
- LinkedIn scraping depends on the public page markup and may break.
- Live HLS streams download only the segments currently listed. Stories, live streams and profile/playlist links are not supported (only single posts).
- Use only for content you have the right to save. Respect each platform's terms and creators' copyright.

## Roadmap

- **Linked accounts** (Instagram / YouTube / LinkedIn / …): optional login so private-to-you and login-walled content works. Plan: store session cookies per platform in `EncryptedSharedPreferences`, pass them to yt-dlp (`cookiefile`) and gallery-dl (`config cookies`); the extractor interface already takes one URL and returns items, so accounts slot in as an optional `session` argument.
- More platforms: X/Twitter, Facebook, Reddit (yt-dlp/gallery-dl already support them; add a `PLATFORM_DOMAINS` + `PLATFORM_EXTRACTORS` entry).
- Quality picker, download history, background downloads with notifications.
