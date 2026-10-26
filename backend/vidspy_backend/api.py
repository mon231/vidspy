"""vidspy backend facade, called from Kotlin through Chaquopy.

Public API (all arguments/returns are plain strings, JSON where structured):

    resolve(text)                       -> JSON {"platform", "items": [...]}
    download(item_json, dest_dir)       -> absolute path of the downloaded file

    configure(data_dir)                 -> where linked-account cookies are stored
    accounts_list()                     -> JSON [{"platform", "label", "connected", "help", ...}]
    account_connect(platform, text)     -> JSON status (text: cookies.txt / JSON export / Cookie header)
    account_disconnect(platform)        -> JSON status
"""
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
    "hls": (ex.hls_extract,),  # any direct .m3u8 link
}

_URL_RE = re.compile(r"https?://[^\s<>\"']+")
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
        raise VidspyError(f"Unsupported link. Supported: {names}, .m3u8 playlists.")

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


def download(item_json, dest_dir):
    import requests

    item = json.loads(item_json)
    os.makedirs(dest_dir, exist_ok=True)
    headers = {"User-Agent": ex.DEFAULT_UA}
    headers.update(item.get("headers") or {})

    if item.get("ext") == "m3u8":  # HLS: fetch + concatenate segments (no ffmpeg)
        from . import hls
        digest = hashlib.sha1(item["url"].encode()).hexdigest()[:8]
        session = requests.Session()
        session.headers.update(headers)
        try:
            return hls.download_hls(session, item["url"],
                                    os.path.join(dest_dir, f"{_slug(item.get('title'))}_{digest}"))
        except (requests.RequestException, hls.HlsError) as e:
            raise VidspyError(f"Download failed: {e}")

    try:
        with requests.get(item["url"], headers=headers, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            ext = item.get("ext") or _CONTENT_TYPE_EXT.get(
                resp.headers.get("Content-Type", "").split(";")[0].strip(),
                "mp4" if item.get("type") == "video" else "jpg")
            digest = hashlib.sha1(item["url"].encode()).hexdigest()[:8]
            path = os.path.join(dest_dir, f"{_slug(item.get('title'))}_{digest}.{ext}")
            with open(path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    f.write(chunk)
    except requests.RequestException as e:
        raise VidspyError(f"Download failed: {e}")
    return path


# ---- linked accounts (implemented once in accounts.py; UIs just call these) ---------------

def configure(data_dir):
    accounts.configure(data_dir)


def accounts_list():
    return json.dumps(accounts.list_accounts())


def account_connect(platform, text):
    return json.dumps(accounts.connect(platform, text))


def account_disconnect(platform):
    return json.dumps(accounts.disconnect(platform))
