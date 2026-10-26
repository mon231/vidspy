# vidspy

Android app. Paste (or share) a social-media link, **watch it directly** in the app, **download** it to your gallery. No account, no login.

- Kotlin UI (native Android views)
- Python backend running on-device via [Chaquopy](https://chaquo.com/chaquopy/)
- Desktop app (Windows / macOS / Linux, [Eel](https://github.com/python-eel/Eel) GUI) for manual backend testing
- **One shared backend package** (`backend/vidspy_backend`) used unchanged by both the APK and the desktop app
- Platforms: **Instagram**, **TikTok**, **LinkedIn**, **YouTube**, **X/Twitter**, **Facebook**, **Reddit**, **Pinterest**, **Vimeo**, plus any direct **`.m3u8`** (HLS) link
- Videos and images, including multi-item carousels

## Features

| Feature | Notes |
|---|---|
| Direct watch | Streams the CDN URL with Media3 ExoPlayer (seek bar, 0.5x-2x speed, mute). Only a sliding window around the playback position is buffered, so huge files and live HLS work. A separate audio stream is merged on the fly (`MergingMediaSource`); desktop syncs a second `<audio>` element. |
| Download | Saves to `Movies/vidspy` (video) or `Pictures/vidspy` (image) through MediaStore. |
| Download all | Saves every item of a carousel / multi-media post. |
| Share target | Share a link from the Instagram / TikTok / LinkedIn app straight to vidspy. |
| Anonymous by default | No login needed. Login-walled posts fail with a clear message, unless you link an account (below). |
| Linked accounts | **Accounts** button: connect each platform with your browser cookies; used automatically for that platform's links. |

## Layout

```
backend/vidspy_backend/   shared Python package (api, extractors, hls, accounts, platforms)  <- APK + desktop
backend/requirements.txt  the ONLY list of backend dependencies (Gradle, pyproject and desktop all read it)
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
| X / Twitter | yt-dlp (videos, GIFs) | gallery-dl (photo tweets, multi-image) |
| Facebook | yt-dlp (public videos, reels, `fb.watch`) | gallery-dl (photos) |
| Reddit | yt-dlp (videos) | gallery-dl (images, galleries) |
| Pinterest | yt-dlp (video pins) | gallery-dl (image pins) |
| Vimeo | yt-dlp | — |
| `.m3u8` URL | built-in HLS validator (`m3u8`) | — |

LinkedIn has no yt-dlp extractor for public posts, so vidspy reads `<video data-sources>` / `og:video` / `og:image` from the public post page itself.

Viewing never resolves or downloads the whole media. Downloads of a video plus separate audio are merged by `mux.py`, a pure-Python MP4 remuxer (no ffmpeg, no re-encode). Live streams can be watched, not downloaded. It prefers single-file progressive HTTP formats and falls back to HLS. HLS is downloaded by `hls.py`: parse with [`m3u8`](https://github.com/globocom/m3u8), pick the highest-bandwidth variant, concatenate segments into `.ts` (or `.mp4` for fragmented-MP4 playlists). Separate-audio HLS downloads need fMP4 audio; DRM is refused. YouTube mostly offers progressive formats only up to 360p, so expect low quality there.

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

Dependencies are declared once in `backend/requirements.txt`: Gradle (`pip { install "-r", "../backend/requirements.txt" }`), `pyproject.toml` (dynamic dependencies) and `desktop/requirements.txt` (`-r ../backend/requirements.txt`, plus `eel`) all read it, and a test fails if a name is duplicated.

Extractors break when platforms change. Fix is usually bumping `yt-dlp` / `gallery-dl` in `app/build.gradle` (versions are unpinned, so a rebuild picks up the latest).

## Build

Requirements: JDK 17+, Android SDK (API 35), and a host Python 3.8+ on `PATH` (Chaquopy uses it at build time).

```bash
./gradlew assembleDebug
# app/build/outputs/apk/debug/app-debug.apk
```

CI builds the debug APK (`build.yml`) and the desktop executables (`desktop.yml`) on every push.

### Releases

Pushing a tag like `v1.2.0` runs `.github/workflows/release.yml`, which tests the backend, builds the onefile Windows `.exe` and Linux executable (no console, with icon) and a signed release APK, then publishes them as a GitHub release (`vidspy-windows-x64.exe`, `vidspy-linux-x64`, `vidspy-v1.2.0.apk`, and the backend wheel `vidspy_backend-1.2.0-py3-none-any.whl`). The tag (without the `v`) is the version of everything: APK `versionName`, the wheel, and `vidspy_backend.__version__` bundled in the executables. `backend/vidspy_backend/_version.py` holds a placeholder that CI overwrites.

```bash
git tag v1.2.0 && git push origin v1.2.0
```

Gradle builds the unsigned release APK, then [`signapp`](https://pypi.org/project/signapp/) (PyPI) zip-aligns and signs it with its built-in keystore, so no secrets are needed. That keystore is public: the APK installs fine, but its signature does not prove who built it. To sign with your own key instead, pass `-k release.jks` to `signapp` (it prompts for alias and password on stdin).

Only `arm64-v8a` is packaged (see `abiFilters`). Add `x86_64` for emulators.

## Test backend

Pure-Python tests run on the host, no Android needed. For manual end-to-end tests use the desktop app below.

```bash
pip install pytest requests beautifulsoup4
pytest tests            # offline unit tests
pytest -m live          # anonymous public-video smoke test per platform (needs internet)
```

## Linked accounts

Tap **Accounts** (phone) or the **Accounts** button (desktop). Every platform shows its logo, its connection state, and a step-by-step panel with a direct link to that platform's login page. The credential is your browser **session cookies**, not your password: password logins are blocked or short-lived on these platforms, while a cookie works with yt-dlp, gallery-dl and the LinkedIn scraper alike.

1. Log in to the platform in your browser.
2. On a computer, press F12 in that tab, open the Network tab, reload, click any request to the platform and copy its `Cookie` request header.
3. Paste it and Save. Saving is refused unless the cookies include a real login (e.g. `sessionid` for Instagram, `li_at` for LinkedIn, `reddit_session` or `token_v2` for Reddit), so you never end up with a half-working account.

Implemented once, in `backend/vidspy_backend/accounts.py`. Both UIs only call `accounts_list()`, `account_connect(platform, text)` and `account_disconnect(platform)`. Labels, steps, login URLs, brand colors and logos all come from the backend registry (`platforms.py`, logos from [Simple Icons](https://simpleicons.org), CC0); `python assets/make_icons.py` turns that registry into the Android logo drawables, and a test fails if they drift apart. Cookies for other sites in a pasted export are discarded. They are stored as one Netscape cookie file per platform (mode 0600): app-private storage on Android (backup disabled) and `~/.vidspy/accounts` on desktop. They are never logged and never leave the device except in requests to that platform. Treat them like a password: anyone with the file can act as you.

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

- Without a linked account Vimeo, Reddit (currently), Facebook and X often login-wall posts, so link an account for them;
- Reddit serves video and audio as separate streams. Merging needs ffmpeg, so Reddit videos play without sound (the app shows a note); private accounts, age/region-gated and login-walled posts fail. Instagram increasingly login-walls anonymous requests; expect failures there.
- LinkedIn scraping depends on the public page markup and may break.
- Live HLS streams can be watched but not downloaded. Stories and profile/playlist links are not supported (only single posts).
- Use only for content you have the right to save. Respect each platform's terms and creators' copyright.

## Roadmap

- Linked accounts: encrypt the cookie files at rest (Android Keystore / OS keyring); in-app browser login instead of pasting cookies.
- More platforms: Threads, Twitch, Bluesky, Snapchat Spotlight (yt-dlp/gallery-dl already support them; add a `PLATFORM_DOMAINS` + `PLATFORM_EXTRACTORS` entry).
- Quality picker, download history, background downloads with notifications.
