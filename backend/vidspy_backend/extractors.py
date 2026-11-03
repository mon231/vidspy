"""Media extractors. Each takes a post URL and returns item dicts {type, url, ext, title, thumbnail, headers}; they raise on failure."""
import json
import re

from . import accounts, traces

DEFAULT_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp", "gif", "heic", "avif"}
MAX_ITEMS = 30


def _kind(ext):
    return "image" if (ext or "").lower() in IMAGE_EXTS else "video"


def _ext_from_url(url, default):
    m = re.search(r"\.([A-Za-z0-9]{2,5})(?:\?|$)", url.split("#")[0])
    return m.group(1).lower() if m else default


MAX_MERGE_HEIGHT = 720  # streams that must be downloaded and merged before playing: keep them phone-sized


def _cap(formats):
    """Formats up to MAX_MERGE_HEIGHT; all of them if none is that small."""
    small = [f for f in formats if (f.get("height") or 0) <= MAX_MERGE_HEIGHT]
    return small or formats


def _best_progressive(formats):
    """Pick the best single-file http(s) format. Android has no ffmpeg, so
    streams that need muxing (separate audio/video, HLS, DASH) are skipped."""
    ok = [
        f for f in formats
        if f.get("url")
        and f.get("protocol") in ("http", "https")
        and f.get("vcodec") != "none"
    ]
    if not ok:
        return None
    ok.sort(key=lambda f: (f.get("acodec") != "none", f.get("height") or 0, f.get("tbr") or 0))
    return ok[-1]


def _best_hls(formats):
    """Fallback when no progressive format exists: best m3u8 that `hls.py` can download
    (DRM / encrypted renditions are skipped)."""
    import requests
    from . import hls

    ok = [f for f in formats
          if f.get("url") and (f.get("protocol") or "").startswith("m3u8") and f.get("vcodec") != "none"]
    ok.sort(key=lambda f: (f.get("acodec") != "none", f.get("height") or 0, f.get("tbr") or 0), reverse=True)
    for f in _cap(ok):
        session = requests.Session()
        session.headers.update(f.get("http_headers") or {})
        try:
            hls.playable(session, f["url"])
            return f
        except Exception:  # noqa: BLE001 - unusable rendition, try the next one
            continue
    return None


def _is_audio_only(f):
    return bool(f.get("url") and f.get("vcodec") == "none" and f.get("acodec") != "none"
                and not str(f.get("format_id", "")).endswith("-drc"))


def _pair_mp4(formats):
    """(video-only mp4, m4a audio) over plain http(s), to be merged by `mux.py`; None if not available."""
    video = [f for f in formats
             if f.get("url") and f.get("protocol") in ("http", "https") and f.get("ext") == "mp4"
             and f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none")]
    video = _cap(video)
    audio = [f for f in formats
             if _is_audio_only(f) and f.get("protocol") in ("http", "https") and f.get("ext") in ("m4a", "mp4")]
    if not video or not audio:
        return None
    video.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
    audio.sort(key=lambda f: f.get("abr") or f.get("tbr") or 0)
    return video[-1], audio[-1]


def _hls_audio(formats):
    """Best separate HLS audio rendition that the player can use."""
    import requests
    from . import hls

    ok = [f for f in formats if _is_audio_only(f) and (f.get("protocol") or "").startswith("m3u8")]
    ok.sort(key=lambda f: f.get("abr") or f.get("tbr") or 0, reverse=True)
    for f in ok:
        session = requests.Session()
        session.headers.update(f.get("http_headers") or {})
        try:
            hls.playable(session, f["url"])
            return f
        except Exception:  # noqa: BLE001
            continue
    return None


def _choose(formats):
    """Pick (format, audio format or None, is_hls). Never returns a silent video if a sound
    source exists: audio is either inside the format or merged from a second one."""
    best = _best_progressive(formats)
    if best is not None and best.get("acodec") != "none":
        return best, None, False
    pair = _pair_mp4(formats)
    if pair:
        return pair[0], pair[1], False
    stream = _best_hls(formats)
    if stream is not None:
        if stream.get("acodec") != "none":
            return stream, None, True
        audio = _hls_audio(formats)
        if audio:
            return stream, audio, True
    return best, None, False


def ytdlp_extract(url):
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "format": "best[ext=mp4]/best",
        "ignore_no_formats_error": True,  # YouTube has few single-file formats; we pick from info["formats"]
        "playlistend": 20,
    }
    if traces.current() is not None:  # tracing (e.g. this resolve may end up failing): keep its debug lines
        opts["verbose"] = True
        opts["logger"] = traces.Probe("ytdlp")
    cookies = accounts.cookie_file_for_url(url)
    if cookies:  # linked account (see accounts.py)
        opts["cookiefile"] = cookies
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        entries = [e for e in (info.get("entries") or [info]) if e]
        items = []
        for entry in entries:
            fmt, audio, hls = _choose(entry.get("formats") or [])
            if fmt is None and entry.get("protocol") in ("http", "https") and entry.get("url"):
                fmt = entry
            if fmt is None:
                continue
            media_url = fmt["url"]
            headers = dict(fmt.get("http_headers") or entry.get("http_headers") or {})
            try:  # TikTok CDN requires the session cookies yt-dlp collected
                cookie = ydl.cookiejar.get_cookie_header(media_url)
                if cookie:
                    headers["Cookie"] = cookie
            except Exception:
                pass
            ext = "m3u8" if hls else (fmt.get("ext") or _ext_from_url(media_url, "mp4"))
            silent = fmt.get("acodec") == "none" and audio is None
            item = {
                "type": _kind(ext),
                "url": media_url,
                "ext": ext,
                "title": entry.get("title") or entry.get("id") or "video",
                "thumbnail": entry.get("thumbnail"),
                "headers": headers,
                "note": "No sound: no audio track available" if silent else None,
            }
            if audio:
                item["audio"] = {"url": audio["url"], "ext": "m3u8" if hls else (audio.get("ext") or "m4a"),
                                 "headers": dict(audio.get("http_headers") or headers)}
            items.append(item)
        return items[:MAX_ITEMS]


def vimeo_player_extract(url):
    """vimeo.com pages demand a login for yt-dlp's web client; the embeddable player page does not."""
    m = re.search(r"vimeo\.com/(?:.*/)?(\d{6,})", url)
    if not m:
        raise ValueError("no Vimeo video id in link")
    return ytdlp_extract(f"https://player.vimeo.com/video/{m.group(1)}")


def tumblr_html_extract(url):
    """Tumblr rate-limits the API gallery-dl shares (429) and rejects script TLS on the
    legacy blog hosts; the public post page on www.tumblr.com serves og: media without login.

    Same approach as LinkedIn below, hence `parse_html_media`."""
    from . import browser

    m = re.search(r"//([a-z0-9-]+)\.tumblr\.com/(?:post/)?(\d+)", url, re.I)
    if m:  # blog.tumblr.com/homePOST/... and shortener redirects 403: use the www post page
        url = f"https://www.tumblr.com/{m.group(1)}/{m.group(2)}"
    resp = browser.chrome_session().get(url, timeout=20)
    if resp.status_code in (401, 403):
        raise ValueError(f"Tumblr blocked this request (HTTP {resp.status_code})")
    resp.raise_for_status()
    items = parse_html_media(resp.text)
    for it in items:
        it["headers"] = {"User-Agent": browser.CHROME_UA, "Referer": "https://www.tumblr.com/"}
    if not items:
        raise ValueError("no media found on the public Tumblr page")
    return items[:MAX_ITEMS]


def dailymotion_extract(url):
    """Dailymotion's CDN 403s a script's m3u8 fetch unless it carries the site's Referer
    (the extractor's Chrome impersonation isn't available on-device either -- yt-dlp fails).
    The `metadata` endpoint yields the title and a signed manifest to use it with."""
    import requests
    from . import browser

    m = (re.search(r"(?:dailymotion\.com/(?:[^/?#]+/)*video/|dai\.ly/)([0-9a-zA-Z]+)", url)
         or re.search(r"player(?:/[a-z]+)?\.html\?(?:[^#]*&)?video=([0-9a-zA-Z]+)", url))
    if not m:
        raise ValueError("no Dailymotion video id in link")
    session = browser.chrome_session()
    session.headers.update({"Referer": "https://www.dailymotion.com/"})
    resp = session.get(f"https://www.dailymotion.com/player/metadata/video/{m.group(1)}",
                       params={"app": "com.dailymotion.neon"}, timeout=20)
    resp.raise_for_status()
    meta = resp.json()
    error = meta.get("error")
    if error:
        raise ValueError(f"Dailymotion said: {error.get('raw_message') or error.get('title')}")
    entries = (meta.get("qualities") or [])
    if isinstance(entries, dict):
        entries = [v for media_list in entries.values() for v in media_list if isinstance(v, dict)]
    else:
        entries = [v for quality in entries for v in (quality.get("videos") or [])
                   if isinstance(v, dict)]
    media = next((v for v in entries if v.get("type") == "application/x-mpegURL"),
                 next((v for v in entries if v.get("url")), None))
    if not media or not media.get("url"):
        raise ValueError("no media found in the Dailymotion metadata")
    posters = meta.get("posters") or {}
    thumb = None
    if isinstance(posters, dict):
        sizes = [u for v in posters.values() for v in (v if isinstance(v, list) else [v])
                 if isinstance(v, dict) and v.get("url")]
        if sizes:
            thumb = sizes[-1]["url"]
    return [{
        "type": "video", "url": media["url"], "ext": "m3u8",
        "title": meta.get("title") or m.group(1), "thumbnail": thumb,
        "headers": {"User-Agent": browser.CHROME_UA, "Referer": "https://www.dailymotion.com/"},
    }]


def _session_headers(session, media_url, referer):
    """Headers gallery-dl's own session used, limited to cookies valid for the media host
    (e.g. TikTok's CDN answers 403 without the anonymous session cookies)."""
    from urllib.parse import urlparse

    host = urlparse(media_url).hostname or ""
    cookie = "; ".join(f"{c.name}={c.value}" for c in session.cookies
                       if c.domain and host.endswith(c.domain.lstrip(".")))
    headers = {"User-Agent": session.headers.get("User-Agent") or DEFAULT_UA}
    if referer:
        headers["Referer"] = referer
    if cookie:
        headers["Cookie"] = cookie
    return headers


def gallerydl_extract(url):
    from gallery_dl import config, extractor
    from gallery_dl.extractor.message import Message

    config.set(("extractor",), "cookies", accounts.cookie_file_for_url(url))  # None = anonymous

    with traces.Probe("gallerydl") as probe:  # logs go into the trace only if one runs
        ex = extractor.find(url)
        if ex is None:
            raise ValueError("gallery-dl does not support this URL")
        if hasattr(ex, "initialize"):
            ex.initialize()

        items = []
        for msg in ex:
            if msg[0] != Message.Url:
                continue
            media_url, kw = msg[1], msg[2]
            ext = (kw.get("extension") or _ext_from_url(media_url, "jpg")).lower()
            items.append({
                "type": _kind(ext),
                "url": media_url,
                "ext": ext,
                "title": str(kw.get("description") or kw.get("title") or kw.get("post_shortcode")
                             or kw.get("tiktok_id") or "post")[:80],
                "thumbnail": media_url if _kind(ext) == "image" else None,
                "headers": _session_headers(ex.session, media_url, f"{getattr(ex, 'root', '')}/" if getattr(ex, "root", "") else None),
            })
            if len(items) >= MAX_ITEMS:
                break
        return items


def tikwm_items(data):
    """Turn a tikwm.com response item into extractor item dicts. Pure function, unit-testable."""
    title = (data.get("title") or data.get("author", {}).get("unique_id") or "tiktok post")[:80]
    images = [i for i in (data.get("images") or []) if i]
    if images:
        headers = {"User-Agent": DEFAULT_UA, "Referer": "https://www.tiktok.com/"}
        return [{"type": "image", "url": i, "ext": _ext_from_url(i, "jpg"), "title": title,
                 "thumbnail": data.get("cover"), "headers": headers} for i in images[:MAX_ITEMS]]
    for key in ("hdplay", "play", "wmplay"):
        media_url = data.get(key)
        if media_url:
            if media_url.startswith("//"):
                media_url = "https:" + media_url
            return [{
                "type": "video",
                "url": media_url,
                "ext": "mp4",
                "title": title,
                "thumbnail": data.get("cover"),
                # the CDN links are signed in the URL itself; a browser-ish UA keeps them serving
                "headers": {"User-Agent": DEFAULT_UA, "Referer": "https://www.tiktok.com/"},
            }]
    return []


def tiktok_api_extract(url):
    """TikTok's web page and its yt-dlp request reject non-browser TLS fingerprints, and
    yt-dlp's Chrome impersonation (`curl_cffi`) has no on-device build (like Dailymotion above):
    they fail on the phone. The tikwm.com API serves the same post over common HTTP and returns
    the item's real tiktokcdn URLs (verified with the phone's failing video) -- kept as the
    last extractor so yt-dlp/gallery-dl stay first choice wherever they work."""
    import requests

    resp = requests.get("https://www.tikwm.com/api/", params={"url": url},
                        headers={"User-Agent": DEFAULT_UA}, timeout=20)
    resp.raise_for_status()
    out = resp.json()
    if out.get("code") != 0:
        raise ValueError(f"tikwm said: {out.get('msg') or out.get('code')}")
    items = tikwm_items(out.get("data") or {})
    if not items:
        raise ValueError("no media found in the tikwm response")
    return items


def parse_html_media(html):
    """Extract media from a public page. Returns list of item dicts without
    headers (caller adds them). Pure function, unit-testable."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    title = None
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title["content"]
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    title = title or "linkedin post"

    items = []
    seen = set()

    def add(kind, media_url, ext, thumb=None):
        if media_url and media_url not in seen:
            seen.add(media_url)
            items.append({"type": kind, "url": media_url.replace("&amp;", "&"), "ext": ext,
                          "title": title, "thumbnail": thumb})

    for video in soup.find_all("video"):
        poster = video.get("poster")
        sources = []
        raw = video.get("data-sources")
        if raw:
            try:
                sources = [s for s in json.loads(raw) if s.get("src")]
            except ValueError:
                pass
        if video.get("src"):
            sources.append({"src": video["src"], "type": "video/mp4"})
        for s in video.find_all("source"):
            if s.get("src"):
                sources.append({"src": s["src"], "type": s.get("type", "video/mp4")})
        # prefer progressive mp4 over HLS (no ffmpeg on device)
        mp4 = [s for s in sources if "mpegurl" not in s.get("type", "") and ".m3u8" not in s["src"]]
        if mp4:
            add("video", mp4[0]["src"], "mp4", poster)

    if not items:
        og_video = soup.find("meta", property="og:video") or soup.find("meta", property="og:video:url")
        if og_video and og_video.get("content"):
            og_img = soup.find("meta", property="og:image")
            add("video", og_video["content"], "mp4", og_img.get("content") if og_img else None)

    if not items:
        og_img = soup.find("meta", property="og:image")
        if og_img and og_img.get("content") and "favicon" not in og_img["content"]                 and not og_img["content"].split("?")[0].endswith(".ico"):
            u = og_img["content"]
            add("image", u, _ext_from_url(u, "jpg"), u)

    return items


def onehd_extract(url):
    """1hd.one movie/series pages list several providers (Vidmoly, UpCloud, ...);
    fetch that list and try each provider until one yields playable media."""
    import requests
    import re as _re

    headers = {"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"}
    resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    text = resp.text

    m = _re.search(r"pl_url\s*=\s*['\"]([^'\"]+)['\"]", text)
    if not m:
        m = _re.search(r"(https?://[^'\"]+/ajax/ajax\.php\?[^'\"]+)", text)
    if not m:
        raise ValueError("no provider list found on the 1hd page")
    pl_url = m.group(1)

    prov_resp = requests.get(pl_url, headers={"User-Agent": DEFAULT_UA,
                                             "Referer": url}, timeout=20)
    prov_resp.raise_for_status()
    provider_urls = _re.findall(r'data-id="(https://[^"]+)"', prov_resp.text)
    if not provider_urls:
        raise ValueError("no providers listed on the 1hd page")

    last_error = None
    for purl in provider_urls:
        try:
            items = ytdlp_extract(purl)
        except Exception as e:  # try next provider
            last_error = e
            continue
        if items:
            for it in items:
                it.setdefault("headers", {}).setdefault("Referer", purl)
            return items[:MAX_ITEMS]
    raise ValueError(f"no available provider ({last_error})")


def html_extract(url):
    import requests

    headers = {"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"}
    jar = None
    cookie_file = accounts.cookie_file_for_url(url)
    if cookie_file:  # linked account (see accounts.py)
        from http.cookiejar import MozillaCookieJar
        jar = MozillaCookieJar()
        jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
    resp = requests.get(url, headers=headers, cookies=jar, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    if "/authwall" in resp.url or "/login" in resp.url:
        raise ValueError("LinkedIn requires login for this post")
    items = parse_html_media(resp.text)
    for it in items:
        it["headers"] = {"User-Agent": DEFAULT_UA, "Referer": "https://www.linkedin.com/"}
    return items[:MAX_ITEMS]


def hls_extract(url):
    import requests
    from . import hls

    headers = {"User-Agent": DEFAULT_UA}
    session = requests.Session()
    session.headers.update(headers)
    hls.playable(session, url)
    name = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0] or "stream"
    return [{"type": "video", "url": url, "ext": "m3u8", "title": name, "thumbnail": None,
             "headers": headers}]


def direct_extract(url):
    """Any direct media-file link (the `direct` pseudo-platform, like `hls`).

    FTP(S) links are probed with status commands only (`ftp.py`) and carry a download-first
    note; HTTP links fetch the headers only to confirm the server serves a file rather than a
    web page -- never the body: the player keeps streaming it and `_http_get` downloads it in
    chunks."""
    from urllib.parse import urlsplit

    if urlsplit(url).scheme.lower() in ("ftp", "ftps"):
        from . import ftp
        name, ext, _ = ftp.probe(url)
        return [{"type": _kind(ext), "url": url, "ext": ext, "title": name.rsplit(".", 1)[0] or "file",
                 "thumbnail": None, "headers": {},
                 "note": ("The phone can't stream ftp:// links (players only speak http(s)) -- download "
                          "instead. The desktop app can also play it through its local proxy.")}]

    import requests

    headers = {"User-Agent": DEFAULT_UA}
    with requests.get(url, headers=headers, stream=True, timeout=20, allow_redirects=True) as resp:
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        ok = (not ctype or ctype == "application/octet-stream" or "attachment" in resp.headers.get(
            "Content-Disposition", "").lower())
        ok = ok or ctype.startswith(("video/", "image/", "audio/", "application/mp4", "application/vnd"))
        if not ok:
            raise ValueError(f"the link serves {ctype}, not a media file")
        final = resp.url
        ext = _ext_from_url(final, None)
        if not ext:
            ext = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov",
                   "video/mp2t": "ts", "image/jpeg": "jpg", "image/png": "png",
                   "image/webp": "webp", "image/gif": "gif",
                   "image/heic": "heic"}.get(ctype, "mp4" if ctype.startswith("video/") else "jpg")
    name = final.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or "file"
    return [{"type": _kind(ext), "url": final, "ext": ext,
             "title": name.rsplit(".", 1)[0] or "file", "thumbnail": None, "headers": headers}]


# aliases for the 1hd.one extractor
one_hd_extract = onehd_extract
hd1_extract = onehd_extract
hd_one_extract = onehd_extract
onehdone_extract = onehd_extract
