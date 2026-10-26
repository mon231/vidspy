"""Media extractors for vidspy.

Every extractor takes a post URL and returns a list of media item dicts:

    {
        "type": "video" | "image",
        "url": "<direct media url>",
        "ext": "mp4",
        "title": "...",
        "thumbnail": "<url or None>",
        "headers": {"User-Agent": "...", "Cookie": "..."},   # needed to fetch `url`
    }

Extractors raise on failure; the facade in `vidspy_backend.api` tries the next one.
"""
import json
import re

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


# --------------------------------------------------------------------------
# yt-dlp: primary extractor for video (Instagram, TikTok)
# --------------------------------------------------------------------------

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
    """Fallback when no progressive format exists: best m3u8 (downloaded by `hls.py`)."""
    ok = [f for f in formats
          if f.get("url") and (f.get("protocol") or "").startswith("m3u8") and f.get("vcodec") != "none"]
    if not ok:
        return None
    ok.sort(key=lambda f: (f.get("acodec") != "none", f.get("height") or 0, f.get("tbr") or 0))
    return ok[-1]


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
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        entries = [e for e in (info.get("entries") or [info]) if e]
        items = []
        for entry in entries:
            fmt = _best_progressive(entry.get("formats") or [])
            if fmt is None and entry.get("protocol") in ("http", "https") and entry.get("url"):
                fmt = entry
            hls = False
            if fmt is None:
                fmt = _best_hls(entry.get("formats") or [])
                hls = fmt is not None
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
            items.append({
                "type": _kind(ext),
                "url": media_url,
                "ext": ext,
                "title": entry.get("title") or entry.get("id") or "video",
                "thumbnail": entry.get("thumbnail"),
                "headers": headers,
            })
        return items[:MAX_ITEMS]


# --------------------------------------------------------------------------
# gallery-dl: image posts / carousels (Instagram, TikTok photo posts)
# --------------------------------------------------------------------------

def gallerydl_extract(url):
    from gallery_dl import extractor
    from gallery_dl.extractor.message import Message

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
            "headers": {"User-Agent": DEFAULT_UA},
        })
        if len(items) >= MAX_ITEMS:
            break
    return items


# --------------------------------------------------------------------------
# Generic HTML scraper: LinkedIn public posts (no yt-dlp extractor exists)
# --------------------------------------------------------------------------

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
        if og_img and og_img.get("content"):
            u = og_img["content"]
            add("image", u, _ext_from_url(u, "jpg"), u)

    return items


def html_extract(url):
    import requests

    headers = {"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"}
    resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    if "/authwall" in resp.url or "/login" in resp.url:
        raise ValueError("LinkedIn requires login for this post")
    items = parse_html_media(resp.text)
    for it in items:
        it["headers"] = {"User-Agent": DEFAULT_UA, "Referer": "https://www.linkedin.com/"}
    return items[:MAX_ITEMS]


# --------------------------------------------------------------------------
# Direct .m3u8 links (any host)
# --------------------------------------------------------------------------

def hls_extract(url):
    import requests
    from . import hls

    headers = {"User-Agent": DEFAULT_UA}
    session = requests.Session()
    session.headers.update(headers)
    hls.check_playlist(session, url)
    name = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[0] or "stream"
    return [{"type": "video", "url": url, "ext": "m3u8", "title": name, "thumbnail": None,
             "headers": headers}]
