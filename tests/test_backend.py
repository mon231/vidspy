"""Offline backend tests."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from vidspy_backend import api as logic  # noqa: E402
from vidspy_backend import extractors as ex  # noqa: E402


@pytest.mark.parametrize("url,platform", [
    ("https://www.instagram.com/reel/abc/", "instagram"),
    ("https://vm.tiktok.com/xyz/", "tiktok"),
    ("https://www.tiktok.com/@u/video/1", "tiktok"),
    ("https://www.linkedin.com/posts/foo", "linkedin"),
    ("https://lnkd.in/abc", "linkedin"),
    ("https://notinstagram.com/x", None),
    ("https://example.com/instagram.com", None),
])
def test_detect_platform(url, platform):
    assert logic.detect_platform(url) == platform


def test_extract_url_from_share_text():
    assert logic.extract_url("look https://www.tiktok.com/@u/video/1.") == "https://www.tiktok.com/@u/video/1"
    with pytest.raises(logic.VidspyError):
        logic.extract_url("no link here")


def test_resolve_rejects_unsupported():
    with pytest.raises(logic.VidspyError):
        logic.resolve("https://example.com/v")


def test_resolve_falls_back_to_next_extractor(monkeypatch):
    def boom(url):
        raise RuntimeError("login required")
    item = {"type": "image", "url": "https://cdn/x.jpg", "ext": "jpg", "title": "t", "headers": {}}
    monkeypatch.setitem(logic.PLATFORM_EXTRACTORS, "instagram", (boom, lambda u: [item]))
    out = json.loads(logic.resolve("https://instagram.com/p/abc/"))
    assert out["platform"] == "instagram" and out["items"] == [item]


def test_resolve_reports_failure(monkeypatch):
    monkeypatch.setitem(logic.PLATFORM_EXTRACTORS, "tiktok", (lambda u: [],))
    with pytest.raises(logic.VidspyError):
        logic.resolve("https://tiktok.com/@a/video/1")


def test_linkedin_video_data_sources():
    html = '''<html><head><meta property="og:title" content="My post"></head><body>
    <video poster="https://p/poster.jpg"
      data-sources='[{"src":"https://v/stream.m3u8","type":"application/x-mpegURL"},
                     {"src":"https://v/clip.mp4?a=1&amp;b=2","type":"video/mp4"}]'></video></body></html>'''
    items = ex.parse_html_media(html)
    assert len(items) == 1
    assert items[0]["type"] == "video"
    assert items[0]["url"] == "https://v/clip.mp4?a=1&b=2"
    assert items[0]["thumbnail"] == "https://p/poster.jpg"


def test_linkedin_og_image_fallback():
    html = '<meta property="og:image" content="https://i/pic.png"><title>x</title>'
    items = ex.parse_html_media(html)
    assert items[0]["type"] == "image" and items[0]["ext"] == "png"


def test_best_progressive_skips_hls_and_prefers_audio():
    formats = [
        {"url": "a", "protocol": "m3u8_native", "vcodec": "h264", "height": 1080},
        {"url": "b", "protocol": "https", "vcodec": "h264", "acodec": "none", "height": 1080},
        {"url": "c", "protocol": "https", "vcodec": "h264", "acodec": "aac", "height": 720},
    ]
    assert ex._best_progressive(formats)["url"] == "c"


@pytest.mark.parametrize("url,platform", [
    ("https://www.youtube.com/watch?v=abc", "youtube"),
    ("https://youtu.be/abc", "youtube"),
    ("https://cdn.example.com/live/master.m3u8?token=1", "hls"),
    ("https://example.com/video.mp4", None),
    ("https://x.com/user/status/1", "twitter"),
    ("https://twitter.com/user/status/1", "twitter"),
    ("https://t.co/abc", "twitter"),
    ("https://www.facebook.com/watch/?v=1", "facebook"),
    ("https://fb.watch/abc/", "facebook"),
    ("https://m.facebook.com/reel/1", "facebook"),
    ("https://notx.com/a", None),
])
def test_detect_youtube_and_hls(url, platform):
    assert logic.detect_platform(url) == platform


def _serve(files):
    import functools
    import http.server
    import tempfile
    import threading
    d = tempfile.mkdtemp()
    for name, data in files.items():
        with open(os.path.join(d, name), "wb") as f:
            f.write(data)

    class Q(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Q, directory=d))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_hls_resolve_and_download(tmp_path):
    media = b"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nseg0.ts\n#EXTINF:2,\nseg1.ts\n#EXT-X-ENDLIST\n"
    master = (b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nlow.m3u8\n"
              b"#EXT-X-STREAM-INF:BANDWIDTH=900\nhigh.m3u8\n")
    srv, base = _serve({"master.m3u8": master, "low.m3u8": b"#EXTM3U\n#EXTINF:2,\nlow0.ts\n#EXT-X-ENDLIST\n",
                        "high.m3u8": media, "seg0.ts": b"AAA", "seg1.ts": b"BBB", "low0.ts": b"XXX"})
    try:
        post = json.loads(logic.resolve(f"{base}/master.m3u8"))
        assert post["platform"] == "hls" and post["items"][0]["ext"] == "m3u8"
        path = logic.download(json.dumps(post["items"][0]), str(tmp_path))
        assert path.endswith(".ts")
        assert open(path, "rb").read() == b"AAABBB"  # highest variant, segments concatenated in order
    finally:
        srv.shutdown()


def test_hls_rejects_non_playlist_and_encrypted(tmp_path):
    srv, base = _serve({"bad.m3u8": b"not a playlist",
                        "enc.m3u8": b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k"\n#EXTINF:2,\ns.ts\n#EXT-X-ENDLIST\n',
                        "s.ts": b"S"})
    try:
        with pytest.raises(logic.VidspyError):
            logic.resolve(f"{base}/bad.m3u8")
        item = {"url": f"{base}/enc.m3u8", "ext": "m3u8", "title": "e", "headers": {}}
        with pytest.raises(logic.VidspyError):
            logic.download(json.dumps(item), str(tmp_path))
    finally:
        srv.shutdown()


def test_yt_dlp_hls_fallback_when_no_progressive(monkeypatch):
    from vidspy_backend import hls
    monkeypatch.setattr(hls, "playable", lambda session, url: None)
    formats = [{"url": "h", "protocol": "m3u8_native", "vcodec": "h264", "acodec": "aac", "height": 720},
               {"url": "l", "protocol": "m3u8_native", "vcodec": "h264", "acodec": "aac", "height": 360}]
    assert ex._best_progressive(formats) is None
    assert ex._best_hls(formats)["url"] == "h"


def test_desktop_playlist_rewrite():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop"))
    import app as desktop
    out = desktop.rewrite_playlist('#EXTM3U\n#EXT-X-KEY:METHOD=NONE,URI="k.key"\nseg 1.ts\n', "https://h/a/p.m3u8", "ID")
    assert "/media/ID?u=https%3A%2F%2Fh%2Fa%2Fseg%201.ts" in out
    assert 'URI="/media/ID?u=https%3A%2F%2Fh%2Fa%2Fk.key"' in out


from vidspy_backend import accounts, platforms  # noqa: E402


@pytest.fixture
def acc(tmp_path):
    accounts.configure(str(tmp_path))
    yield accounts
    accounts._dir = None


def test_connect_cookie_header_sets_all_domains(acc):
    st = acc.connect("twitter", "Cookie: auth_token=abc; ct0=def")
    assert st["connected"] and st["count"] == 4  # 2 cookies x (.x.com, .twitter.com)
    jar = open(acc.cookie_file("twitter"), encoding="utf-8").read()
    assert ".x.com\tTRUE\t/\tTRUE" in jar and "auth_token\tabc" in jar


def test_connect_netscape_filters_other_sites(acc):
    text = ("# Netscape HTTP Cookie File\n"
            ".instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tS1\n"
            "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1893456000\tcsrftoken\tC1\n"
            ".evil.com\tTRUE\t/\tFALSE\t0\tsessionid\tEVIL\n")
    st = acc.connect("instagram", text)
    assert st["count"] == 2 and "EVIL" not in open(acc.cookie_file("instagram"), encoding="utf-8").read()


def test_connect_json_export(acc):
    data = json.dumps([{"name": "li_at", "value": "bar", "domain": ".linkedin.com", "path": "/",
                        "secure": True, "expirationDate": 1893456000.5}])
    assert acc.connect("linkedin", data)["connected"]


def test_connect_refuses_cookies_without_login(acc):
    """No session cookie -> nothing is saved (instead of a warning after saving)."""
    for platform, header in [("reddit", "loid=1; csrf_token=2"), ("youtube", "PREF=a; VISITOR_INFO1_LIVE=b"),
                             ("instagram", "csrftoken=x; mid=y")]:
        with pytest.raises(logic.VidspyError) as e:
            acc.connect(platform, header)
        assert "Not saved" in str(e.value)
        assert acc.cookie_file(platform) is None
    assert acc.connect("reddit", "token_v2=abc")["connected"]  # either key cookie is enough


def test_account_status_has_no_tool_recommendation(acc):
    for a in acc.list_accounts():
        assert "tool_url" not in a and "has_session" not in a
        assert not any("cookies.txt" in step for step in a["steps"])


def test_cookie_file_for_url_and_disconnect(acc):
    assert acc.cookie_file_for_url("https://www.tiktok.com/@a/video/1") is None
    acc.connect("tiktok", "sessionid=zzz")
    assert acc.cookie_file_for_url("https://vm.tiktok.com/x").endswith("tiktok.cookies.txt")
    assert acc.cookie_file_for_url("https://example.com/") is None
    assert acc.disconnect("tiktok")["connected"] is False
    assert acc.cookie_file_for_url("https://www.tiktok.com/x") is None


def test_connect_rejects_bad_input(acc):
    for platform, text in [("instagram", ""), ("instagram", ".other.com\tTRUE\t/\tTRUE\t0\tn\tv"), ("nope", "a=b")]:
        with pytest.raises(logic.VidspyError):
            acc.connect(platform, text)


def test_accounts_list_covers_all_platforms(acc):
    out = json.loads(logic.accounts_list())
    assert {a["platform"] for a in out} == set(platforms.PLATFORMS)
    assert len(out) == 9
    for a in out:
        assert not a["connected"] and len(a["steps"]) == 3 and a["privacy"]
        assert a["login_url"].startswith("https://") and a["icon"] and a["color"].startswith("#")


def test_cookies_reach_extractors(acc, monkeypatch):
    acc.connect("instagram", "sessionid=S")
    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen.update(opts)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=False): return {"formats": []}

    import types
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    ex.ytdlp_extract("https://www.instagram.com/reel/x/")
    assert seen["cookiefile"].endswith("instagram.cookies.txt")


def _req_names(path):
    import re
    names = []
    for line in open(path, encoding="utf-8").read().splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("-"):
            names.append(re.split(r"[<>=!~\[; ]", line)[0].lower().replace("_", "-"))
    return names


def test_requirements_have_no_duplicates():
    root = os.path.join(os.path.dirname(__file__), "..")
    backend = _req_names(os.path.join(root, "backend", "requirements.txt"))
    desktop = _req_names(os.path.join(root, "desktop", "requirements.txt"))
    assert len(backend) == len(set(backend))
    assert len(desktop) == len(set(desktop))
    assert not set(backend) & set(desktop)
    # Android and pyproject read backend/requirements.txt instead of listing packages
    gradle = open(os.path.join(root, "app", "build.gradle"), encoding="utf-8").read()
    assert "../backend/requirements.txt" in gradle
    for name in backend:
        assert f'"{name}"' not in gradle
    assert 'file = ["requirements.txt"]' in open(os.path.join(root, "backend", "pyproject.toml"), encoding="utf-8").read()


@pytest.mark.parametrize("url,platform", [
    ("https://www.reddit.com/r/x/comments/1/t/", "reddit"),
    ("https://v.redd.it/abc", "reddit"),
    ("https://i.redd.it/abc.jpg", "reddit"),
    ("https://www.pinterest.com/pin/1/", "pinterest"),
    ("https://pin.it/abc", "pinterest"),
    ("https://vimeo.com/76979871", "vimeo"),
    ("https://player.vimeo.com/video/1", "vimeo"),
])
def test_detect_reddit_pinterest_vimeo(url, platform):
    assert logic.detect_platform(url) == platform
    assert platform in logic.PLATFORM_EXTRACTORS


def test_every_platform_has_extractors_and_android_logo():
    root = os.path.join(os.path.dirname(__file__), "..")
    for name in platforms.PLATFORMS:
        assert logic.PLATFORM_EXTRACTORS[name]
        drawable = os.path.join(root, "app", "src", "main", "res", "drawable", f"ic_platform_{name}.xml")
        # generated by assets/make_icons.py from platforms.py; fails if they drift apart
        assert platforms.PLATFORMS[name]["icon"] in open(drawable, encoding="utf-8").read()


def test_reddit_parse_image_gallery_and_video():
    from vidspy_backend import reddit
    img = reddit.parse_post({"title": "t", "post_hint": "image", "url": "https://i.redd.it/a.jpeg",
                             "preview": {"images": [{"source": {"url": "https://preview.redd.it/a.jpeg?x=1&amp;y=2"}}]}})
    assert img[0]["type"] == "image" and img[0]["ext"] == "jpeg"
    assert img[0]["thumbnail"].endswith("x=1&y=2")

    gal = reddit.parse_post({"title": "g", "is_gallery": True,
                             "gallery_data": {"items": [{"media_id": "a"}, {"media_id": "b"}]},
                             "media_metadata": {"a": {"status": "valid", "s": {"u": "https://preview.redd.it/1.png?a=1&amp;b=2"}},
                                                "b": {"status": "failed"}}})
    assert [i["url"] for i in gal] == ["https://preview.redd.it/1.png?a=1&b=2"]

    vid = reddit.parse_post({"title": "v", "secure_media": {"reddit_video": {
        "hls_url": "https://v.redd.it/x/HLSPlaylist.m3u8?a=1&amp;b=2", "fallback_url": "https://v.redd.it/x/DASH_720.mp4"}}})
    assert vid[0]["ext"] == "m3u8" and "&amp;" not in vid[0]["url"]

    cross = reddit.parse_post({"title": "c", "crosspost_parent_list": [{"title": "orig", "post_hint": "image", "url": "https://i.redd.it/o.png"}]})
    assert cross[0]["title"] == "orig"


def test_chrome_session_headers_and_cipher_order():
    from vidspy_backend import browser
    s = browser.chrome_session()
    assert "Chrome/" in s.headers["User-Agent"]
    assert browser.CHROME_CIPHERS.startswith("TLS_AES_128_GCM_SHA256")


def test_hls_fallback_skips_drm_renditions(monkeypatch):
    from vidspy_backend import hls

    def check(session, url):
        if url == "drm":
            raise hls.HlsError("This stream is DRM-protected")

    monkeypatch.setattr(hls, "playable", check)
    formats = [{"url": "drm", "protocol": "m3u8_native", "vcodec": "h264", "acodec": "aac", "height": 1080},
               {"url": "clear", "protocol": "m3u8_native", "vcodec": "h264", "acodec": "aac", "height": 360}]
    assert ex._best_hls(formats)["url"] == "clear"


def test_version_is_single_dynamic_source():
    import re
    import vidspy_backend
    text = open(os.path.join(os.path.dirname(__file__), "..", "backend", "pyproject.toml"), encoding="utf-8").read()
    assert not re.search(r"^version\s*=", text.split("[tool.")[0], re.M)
    assert "vidspy_backend._version.__version__" in text
    assert vidspy_backend.__version__
