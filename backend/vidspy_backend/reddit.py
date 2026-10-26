"""Reddit extractor reading the post's public `.json` through a Chrome-like session."""
import html
import re

from . import accounts, hls
from .browser import CHROME_HEADERS, chrome_session
from .errors import VidspyError

MAX_HEIGHT = 720


def _unescape(url):
    return html.unescape(url) if url else url


def _ext(url, default):
    m = re.search(r"\.([A-Za-z0-9]{2,5})(?:\?|$)", (url or "").split("#")[0])
    return m.group(1).lower() if m else default


def _post_json_url(url):
    m = re.search(r"reddit\.com/(?:r/[^/]+/)?comments/([a-z0-9]+)", url, re.I) or re.search(r"redd\.it/([a-z0-9]+)", url, re.I)
    if not m:
        raise VidspyError("Not a Reddit post link.")
    return f"https://www.reddit.com/comments/{m.group(1)}/.json"


def dash_item(session, video_id, title, thumb, headers):
    """Video item for a v.redd.it video: its DASH MP4s (video and audio are separate files)."""
    from xml.etree import ElementTree

    base = f"https://v.redd.it/{video_id}/"
    resp = session.get(base + "DASHPlaylist.mpd", timeout=30)
    resp.raise_for_status()
    reps = {"video": [], "audio": []}
    for rep in ElementTree.fromstring(resp.text).iter():
        if rep.tag.rpartition("}")[2] != "Representation":
            continue
        name = next((c.text for c in rep if c.tag.rpartition("}")[2] == "BaseURL"), None)
        kind = (rep.get("mimeType") or "").split("/")[0]
        if name and kind in reps:
            reps[kind].append((int(rep.get("height") or 0), int(rep.get("bandwidth") or 0), name))
    if not reps["video"]:
        raise VidspyError("No DASH video found")
    small = [r for r in reps["video"] if r[0] <= MAX_HEIGHT] or reps["video"]
    item = {"type": "video", "url": base + max(small)[2], "ext": "mp4", "title": title,
            "thumbnail": thumb, "headers": headers}
    if reps["audio"]:
        item["audio"] = {"url": base + max(reps["audio"], key=lambda r: r[1])[2], "ext": "mp4", "headers": headers}
    return item


def parse_post(post, session=None):
    """Turn a Reddit post dict (listing child `data`) into vidspy media items.

    With a `session`, videos are fetched as DASH MP4s (video + audio merged on download);
    otherwise the HLS playlist is used."""
    post = (post.get("crosspost_parent_list") or [post])[0]
    title = post.get("title") or "reddit"
    headers = dict(CHROME_HEADERS)
    items = []

    def add(kind, url, ext, thumb=None, note=None):
        item = {"type": kind, "url": url, "ext": ext, "title": title, "thumbnail": thumb, "headers": headers}
        if note:
            item["note"] = note
        items.append(item)

    thumb = _unescape(((post.get("preview") or {}).get("images") or [{}])[0].get("source", {}).get("url"))
    video = (post.get("secure_media") or post.get("media") or {}).get("reddit_video") or \
        ((post.get("preview") or {}).get("reddit_video_preview"))
    if video:
        link = _unescape(video.get("fallback_url") or video.get("hls_url") or "")
        vid = re.search(r"v\.redd\.it/(\w+)/", link)
        if session and vid:
            try:
                items.append(dash_item(session, vid.group(1), title, thumb, headers))
                return items
            except Exception:  # noqa: BLE001 - fall back to the playlist below
                pass
        if video.get("hls_url"):
            add("video", _unescape(video["hls_url"]), "m3u8", thumb)
        elif video.get("fallback_url"):
            add("video", _unescape(video["fallback_url"]), "mp4", thumb, "No sound: no audio track available")
        return items

    meta = post.get("media_metadata") or {}
    if post.get("is_gallery") and meta:
        for entry in (post.get("gallery_data") or {}).get("items", []):
            m = meta.get(entry.get("media_id")) or {}
            if m.get("status") != "valid":
                continue
            src = m.get("s") or {}
            url = _unescape(src.get("u") or src.get("gif") or src.get("mp4"))
            if url:
                add("image", url, _ext(url, "jpg"), _unescape((m.get("p") or [{}])[0].get("u")))
        return items

    url = _unescape(post.get("url_overridden_by_dest") or post.get("url"))
    if url and (post.get("post_hint") == "image" or _ext(url, "") in ("jpg", "jpeg", "png", "gif", "webp")):
        add("image", url, _ext(url, "jpg"), thumb)
        return items

    variants = ((post.get("preview") or {}).get("images") or [{}])[0].get("variants", {})
    mp4 = (variants.get("mp4") or {}).get("source", {}).get("url")
    if mp4:
        add("video", _unescape(mp4), "mp4", thumb)
    return items


def parse_rss(xml, session=None):
    """Anonymous fallback: the post's Atom feed names the linked media (v.redd.it / i.redd.it)."""
    entry = re.search(r"<entry>(.*?)</entry>", xml, re.S)
    if not entry:
        return []
    body = html.unescape(entry.group(1))
    title = html.unescape((re.search(r"<title>(.*?)</title>", body, re.S) or [None, "reddit"])[1]).strip()
    link = re.search(r'<span><a href="([^"]+)">\[link\]</a></span>', body)
    target = link.group(1) if link else ""
    thumb = (re.search(r'<media:thumbnail url="([^"]+)"', body) or [None, None])[1]
    headers = dict(CHROME_HEADERS)
    vid = re.match(r"https?://v\.redd\.it/(\w+)", target)
    if vid:
        if session is not None:
            try:
                return [dash_item(session, vid.group(1), title, thumb, headers)]
            except Exception:  # noqa: BLE001 - fall back to the playlist below
                pass
        playlist = f"https://v.redd.it/{vid.group(1)}/HLSPlaylist.m3u8"
        if session is not None:
            hls.playable(session, playlist)
        return [{"type": "video", "url": playlist, "ext": "m3u8", "title": title, "thumbnail": thumb, "headers": headers}]
    if re.match(r"https?://i\.redd\.it/", target):
        return [{"type": "image", "url": target, "ext": _ext(target, "jpg"), "title": title,
                 "thumbnail": thumb, "headers": headers}]
    return []


def reddit_extract(url):
    session = chrome_session(accounts.cookie_file("reddit"))
    post_id = _post_json_url(url).split("/comments/")[1].split("/")[0]
    resp = session.get(_post_json_url(url), headers={"Accept": "application/json"}, timeout=30)
    if resp.status_code == 200 and "json" in resp.headers.get("content-type", ""):
        children = resp.json()[0]["data"]["children"]
        items = parse_post(children[0]["data"], session) if children else []
        if items:
            return items
    # anonymous clients get no .json: the Atom feed is still served
    session.get("https://old.reddit.com/", timeout=30)  # sets the anonymous session cookies
    feed = session.get(f"https://www.reddit.com/comments/{post_id}/.rss", timeout=30)
    if feed.status_code != 200:
        raise VidspyError(f"Reddit returned HTTP {resp.status_code}")
    return parse_rss(feed.text, session)
