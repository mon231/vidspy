"""vidspy backend facade: plain strings in, JSON strings out (called from Kotlin via Chaquopy and from Eel)."""
import hashlib
import json
import os
import re
from urllib.parse import urlparse

from . import accounts, extractors as ex, platforms
from .errors import VidspyError
from .platforms import detect_platform
from .reddit import reddit_extract


# Extractors tried in order for each platform; first non-empty result wins.
PLATFORM_EXTRACTORS = {
    "instagram": (ex.ytdlp_extract, ex.gallerydl_extract),
    "tiktok": (ex.ytdlp_extract, ex.gallerydl_extract),
    "linkedin": (ex.html_extract,),
    "youtube": (ex.ytdlp_extract,),
    "twitter": (ex.ytdlp_extract, ex.gallerydl_extract),   # videos/GIFs, then photo tweets
    "facebook": (ex.ytdlp_extract, ex.gallerydl_extract),
    "reddit": (reddit_extract, ex.ytdlp_extract, ex.gallerydl_extract),     # videos, then image posts/galleries
    "pinterest": (ex.ytdlp_extract, ex.gallerydl_extract),  # video pins, then image pins
    "vimeo": (ex.ytdlp_extract, ex.vimeo_player_extract),
    "twitch": (ex.ytdlp_extract,),                          # clips (mp4), VODs (HLS) and live streams
    "snapchat": (ex.ytdlp_extract, ex.gallerydl_extract),   # spotlight pages, then stories/users
    "imgur": (ex.ytdlp_extract, ex.gallerydl_extract, ex.direct_extract),   # videos/GIFs, then albums; direct i.imgur links
    "tumblr": (ex.ytdlp_extract, ex.gallerydl_extract, ex.tumblr_html_extract),  # API, then photos; an HTML fallback when the shared API key is rate-limited
    "dailymotion": (ex.dailymotion_extract,),   # the CDN needs the site's Referer on every m3u8 request
    "hls": (ex.hls_extract,),  # any direct .m3u8 link
    "direct": (ex.direct_extract,),  # any direct .mp4/.jpg/... media file link
}

_URL_RE = re.compile(r"(?:https?|ftps?)://[^\s<>\"']+")
_CONTENT_TYPE_EXT = {
    "video/mp4": "mp4", "video/mp2t": "ts", "video/webm": "webm", "video/quicktime": "mov",
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
}


def extract_url(text):
    """Pull the first http(s) URL out of shared text like 'Check this https://...'."""
    m = _URL_RE.search(text or "")
    if not m:
        raise VidspyError("No link found. Paste a link from a supported site (Instagram, TikTok, YouTube, X, ...).")
    return m.group(0).rstrip(".,;)")


def resolve(text):
    url = extract_url(text)
    platform = detect_platform(url)
    if platform is None:
        names = ", ".join(i["label"] for i in platforms.PLATFORMS.values())
        raise VidspyError(f"Unsupported link. Supported: {names}, .m3u8 playlists, "
                          "or direct media file links (.mp4, .jpg, ...).")

    errors = []
    for extractor in PLATFORM_EXTRACTORS[platform]:
        try:
            items = extractor(url)
        except Exception as e:  # try next extractor
            errors.append(str(e).strip().splitlines()[-1] if str(e).strip() else type(e).__name__)
            continue
        if items:
            return json.dumps({"platform": platform, "url": url, "items": items})
    detail = errors[0] if errors else "no media found"
    if platform == "reddit" and any("Reddit API" in e or "HTTP 403" in e for e in errors):
        raise VidspyError("Reddit blocked this app's request (\"blocked by network security\"). "
                          "This is not a login problem: Reddit rejects non-browser clients on this network.")
    raise VidspyError(f"Could not get media from {platform} link ({detail}). "
                      "The post may be private or require login.")


def _slug(text, limit=40):
    s = re.sub(r"[^A-Za-z0-9]+", "_", text or "").strip("_")
    return (s[:limit] or "vidspy")


def _http_get(url, headers, base, ext, kind):
    import requests

    with requests.get(url, headers=headers, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        ext = ext or _CONTENT_TYPE_EXT.get(
            resp.headers.get("Content-Type", "").split(";")[0].strip(), "mp4" if kind == "video" else "jpg")
        path = f"{base}.{ext}"
        with open(path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                f.write(chunk)
    return path


def download(item_json, dest_dir):
    """Download one item and return its path. A separate audio stream (item["audio"]) is merged in."""
    import requests
    from . import hls, mux

    item = json.loads(item_json)
    os.makedirs(dest_dir, exist_ok=True)
    headers = {"User-Agent": ex.DEFAULT_UA}
    headers.update(item.get("headers") or {})
    digest = hashlib.sha1(item["url"].encode()).hexdigest()[:8]
    base = os.path.join(dest_dir, f"{_slug(item.get('title'))}_{digest}")
    audio = item.get("audio")

    try:
        if item.get("ext") == "m3u8":
            session = requests.Session()
            session.headers.update(headers)
            return hls.download_hls(session, item["url"], base, audio["url"] if audio else None)
        url_scheme = urlparse(item["url"]).scheme.lower()
        if url_scheme in ("ftp", "ftps"):
            from . import ftp
            return ftp.download(item["url"], base, item.get("ext") or "mp4")
        if not audio:
            return _http_get(item["url"], headers, base, item.get("ext"), item.get("type"))

        audio_headers = {"User-Agent": ex.DEFAULT_UA}
        audio_headers.update(audio.get("headers") or {})
        parts = [_http_get(item["url"], headers, base + ".video", item.get("ext"), "video"),
                 _http_get(audio["url"], audio_headers, base + ".audio", audio.get("ext") or "m4a", "audio")]
        try:
            return mux.mux_mp4(parts[0], parts[1], base + ".mp4")
        finally:
            for part in parts:
                os.remove(part)
    except (requests.RequestException, hls.HlsError, mux.MuxError) as e:
        raise VidspyError(f"Download failed: {e}")


def configure(data_dir):
    accounts.configure(data_dir)


def accounts_list():
    return json.dumps(accounts.list_accounts())


def account_connect(platform, text):
    return json.dumps(accounts.connect(platform, text))


def account_disconnect(platform):
    return json.dumps(accounts.disconnect(platform))


def suggestions():
    """Real, working example links (one per platform + the two pseudo-platforms), for the apps' paste boxes."""
    out = [{"platform": p, "label": info["label"], "sample": platforms.SAMPLE_LINKS[p], "color": info["color"]}
           for p, info in platforms.PLATFORMS.items()]
    extras = {platforms.HLS: "HLS stream (.m3u8)", platforms.DIRECT: "Media file (.mp4/.jpg/...)"}
    out += [{"platform": p, "label": label, "sample": platforms.SAMPLE_LINKS[p]}
            for p, label in extras.items()]
    return json.dumps(out)
