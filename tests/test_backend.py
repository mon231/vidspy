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
    assert logic.extract_url("grab ftp://ftp.example.com/pub/clip.mp4 now") == "ftp://ftp.example.com/pub/clip.mp4"
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
    ("https://example.com/video.mp4", "direct"),
    ("https://x.com/user/status/1", "twitter"),
    ("https://twitter.com/user/status/1", "twitter"),
    ("https://t.co/abc", "twitter"),
    ("https://www.facebook.com/watch/?v=1", "facebook"),
    ("https://fb.watch/abc/", "facebook"),
    ("https://m.facebook.com/reel/1", "facebook"),
    ("https://clips.twitch.tv/FaintLightGullWholeWheat", "twitch"),
    ("https://www.twitch.tv/videos/831681911", "twitch"),
    ("https://www.snapchat.com/spotlight/W7_AAA123", "snapchat"),
    ("https://t.snapchat.com/abc", "snapchat"),
    ("https://imgur.com/gallery/YcAQlkx", "imgur"),
    ("https://imgur.com/a/iX265HX", "imgur"),
    ("https://i.imgur.com/YcAQlkxh.jpg", "imgur"),
    ("https://imgur.io/x", "imgur"),
    ("https://www.tumblr.com/BLOG/12345", "tumblr"),
    ("https://user.tumblr.com/post/1/pi", "tumblr"),
    ("https://tmblr.co/abc", "tumblr"),
    ("https://www.dailymotion.com/video/x8abc", "dailymotion"),
    ("https://dai.ly/x94cnnk", "dailymotion"),
    ("https://example.com/a/clip.MP4", "direct"),
    ("https://example.com/img/photo.png?width=1", "direct"),
    ("https://example.com/anim.webm", "direct"),
    # a platform host wins over its own CDN's media-looking path
    ("https://i.imgur.com/clip.mp4", "imgur"),
    ("ftp://ftp.example.com/pub/clip.mp4", "direct"),
    ("ftps://user:pw@ftp.example.com/files/fig.png", "direct"),
    ("ftp://ftp.example.com/pub/dir/", None),
    ("https://example.com/page.html", None),
    ("https://notx.com/a", None),
])
def test_detect_youtube_and_hls(url, platform):
    assert logic.detect_platform(url) == platform


def _serve(files):
    """Serve files under 127.0.0.1. A value may be bytes or (bytes, content-type)."""
    import functools
    import http.server
    import tempfile
    import threading
    d = tempfile.mkdtemp()
    ctype = {}
    for name, value in files.items():
        data, ct = value if isinstance(value, tuple) else (value, None)
        with open(os.path.join(d, name), "wb") as f:
            f.write(data)
        if ct:
            ctype[name] = ct

    class Q(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def guess_type(self, path):
            return ctype.get(os.path.basename(path)) or super().guess_type(path)

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


def test_direct_resolve_and_download(tmp_path):
    video = b"0123456789" * 2048  # 20 KB
    photo = b"\x89PNG" + b"\0" * 124
    srv, base = _serve({"clip.mp4": video, "photo.png": photo})
    try:
        post = json.loads(logic.resolve(f"{base}/clip.mp4"))
        assert post["platform"] == "direct"
        assert post["items"][0]["type"] == "video" and post["items"][0]["ext"] == "mp4"
        assert post["items"][0]["title"] == "clip"
        path = logic.download(json.dumps(post["items"][0]), str(tmp_path))
        assert open(path, "rb").read() == video

        post = json.loads(logic.resolve(f"{base}/photo.png"))
        assert post["items"][0]["type"] == "image" and post["items"][0]["ext"] == "png"
        path = logic.download(json.dumps(post["items"][0]), str(tmp_path))
        assert open(path, "rb").read() == photo
    finally:
        srv.shutdown()


def test_direct_rejects_web_page_served_as_media():
    """A CDN error/interstitial page behind a .mp4-looking link is not 'media'."""
    page = b"<html><body><video src='/real.mp4'></video></body></html>"
    srv, base = _serve({"clip.mp4": (page, "text/html")})
    try:
        with pytest.raises(logic.VidspyError) as e:
            logic.resolve(f"{base}/clip.mp4")
        assert "text/html" in str(e.value)
    finally:
        srv.shutdown()


class _FtpError(Exception):
    pass


class _FakeSock:
    """Data-channel stand-in: `recv` hands out the payload in whatever blocksize is asked."""

    def __init__(self, payload):
        self.payload = payload
        self.pos = 0
        self.blocks = 0
        self.closed = False

    def recv(self, blocksize):
        self.blocks += 1
        block = self.payload[self.pos:self.pos + blocksize]
        self.pos += len(block)
        return block

    def close(self):
        self.closed = True


class _FakeFTP:
    """Stdlib-`ftplib`-shaped fake: files per class dict, chunks fed by `retrbinary` and
    `transfercmd`'s data channel."""
    files = {}
    tls = False  # the FTP_TLS subclass below flips this
    error_perm = _FtpError
    all_errors = None  # not used here; monkeypatched module carries the tuple

    def __init__(self, timeout=None):
        self.timeout = timeout
        self.blocksize = None
        self.rest = None
        self.voidresp_called = False
        self.prot_p_called = False
        self.quitted = False
        _FakeFTP.conns.append(self)

    def connect(self, host, port=21):
        self.host, self.port = host, port
        return "220 fake"

    def login(self, user="", passwd=""):
        self.user = user
        self.passwd = passwd

    def prot_p(self):
        self.prot_p_called = True

    def voidcmd(self, cmd):
        assert cmd == "TYPE I"

    def sendcmd(self, cmd):
        path = cmd.split(" ", 1)[1]
        if path not in _FakeFTP.files:
            raise _FtpError("550 No such file")
        return f"213 {len(_FakeFTP.files[path])}"

    def retrbinary(self, cmd, callback, blocksize=8192, rest=None):
        path = cmd.split(" ", 1)[1]
        data = _FakeFTP.files.get(path)
        if data is None:
            raise _FtpError("550 No such file")
        self.blocksize = blocksize
        body = data[rest or 0:]
        for i in range(0, len(body), blocksize):
            callback(body[i:i + blocksize])  # arrives in chunks, like the real protocol

    def transfercmd(self, cmd, rest=None):
        path = cmd.split(" ", 1)[1]
        data = _FakeFTP.files.get(path)
        if data is None:
            raise _FtpError("550 No such file")
        self.rest = rest
        self.last_sock = _FakeSock(data[rest or 0:])
        return self.last_sock

    def voidresp(self):
        self.voidresp_called = True

    def quit(self):
        self.quitted = True


class _FakeFTPS(_FakeFTP):
    tls = True  # the FTP_TLS target in the patched module: probe goes through prot_p()


def _fake_ftp(monkeypatch):
    import types
    from vidspy_backend import ftp as ftpmod
    _FakeFTP.conns = []
    monkeypatch.setattr(ftpmod, "ftplib", types.SimpleNamespace(
        FTP=_FakeFTP, FTP_TLS=_FakeFTPS, error_perm=_FtpError, all_errors=(_FtpError, OSError)))
    return ftpmod


def test_ftp_resolve_and_download(tmp_path, monkeypatch):
    ftpmod = _fake_ftp(monkeypatch)
    payload = b"FTPDATA" * 4096  # 28KB through chunked RETR
    _FakeFTP.files = {"/pub/clip.mp4": payload}

    post = json.loads(logic.resolve("ftp://ftp.example.com/pub/clip.mp4"))
    assert post["platform"] == "direct" and post["items"][0]["url"] == "ftp://ftp.example.com/pub/clip.mp4"
    assert post["items"][0]["type"] == "video" and post["items"][0]["ext"] == "mp4"
    assert post["items"][0]["title"] == "clip" and post["items"][0]["note"]  # download-first

    path = logic.download(json.dumps(post["items"][0]), str(tmp_path))
    assert open(path, "rb").read() == payload and path.endswith(".mp4")
    assert _FakeFTP.conns and all(c.quitted for c in _FakeFTP.conns)  # both sessions were released
    assert _FakeFTP.conns[-1].blocksize == 64 * 1024  # chunked transfer, never the whole file at once


def test_ftp_missing_file_fails_without_download(monkeypatch):
    _fake_ftp(monkeypatch)
    _FakeFTP.files = {}
    with pytest.raises(logic.VidspyError) as e:
        logic.resolve("ftp://ftp.example.com/pub/gone.mp4")
    assert "FTP server refused" in str(e.value)


def test_ftp_open_stream_relays_chunks_and_offsets(monkeypatch):
    ftpmod = _fake_ftp(monkeypatch)
    payload = bytes(range(256)) * 300  # 76800 bytes: more than one 64KB block after the seek
    _FakeFTP.files = {"/pub/clip.mp4": payload}

    ext, served, size, chunks = ftpmod.open_stream("ftp://ftp.example.com/pub/clip.mp4", 150)
    assert (ext, served, size) == ("mp4", 150, len(payload))
    assert b"".join(chunks) == payload[150:]  # REST skipped the first 150 bytes
    conn = _FakeFTP.conns[-1]
    sock = conn.last_sock
    assert sock.blocks >= 2  # relayed block by block, never as one blob
    assert sock.closed and conn.voidresp_called and conn.quitted  # both ends released


def test_ftps_resolves_and_downloads_over_tls(tmp_path, monkeypatch):
    ftpmod = _fake_ftp(monkeypatch)
    payload = b"TLSOK" * 512
    _FakeFTP.files = {"/pub/fig.png": payload}

    post = json.loads(logic.resolve("ftps://user:pw@ftp.example.com/pub/fig.png"))
    item = post["items"][0]
    assert post["platform"] == "direct" and item["type"] == "image" and item["ext"] == "png"
    conn = _FakeFTP.conns[0]
    assert conn.tls and conn.prot_p_called  # explicit FTPS: TLS session before TYPE I / RETR
    assert conn.user == "user" and conn.passwd == "pw"  # userinfo credentials win, no anonymous fallback

    path = logic.download(json.dumps(item), str(tmp_path))
    assert open(path, "rb").read() == payload and path.endswith(".png")
    assert all(c.quitted for c in _FakeFTP.conns)


def test_dailymotion_rejects_link_without_id():
    with pytest.raises(ValueError):
        ex.dailymotion_extract("https://www.dailymotion.com/")


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
    assert {a["platform"] for a in out} == set(platforms.PLATFORMS)  # pseudo-platforms stay out
    assert len(out) == 14
    for a in out:
        assert not a["connected"] and len(a["steps"]) == 3 and a["privacy"]
        assert a["login_url"].startswith("https://") and a["icon"] and a["color"].startswith("#")


def test_suggestions_cover_every_platform():
    out = json.loads(logic.suggestions())  # paste-box sample links, one per platform
    assert [s["platform"] for s in out] == list(platforms.PLATFORMS) + [platforms.HLS, platforms.DIRECT]
    for s in out:  # every sample is real enough to detect as its own platform (incl. ftp(s) for direct)
        assert s["label"] and s["sample"]
        assert logic.detect_platform(s["sample"]) == s["platform"], s["sample"]


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


def test_manifest_opens_every_platform_domain():
    root = os.path.join(os.path.dirname(__file__), "..")
    manifest = open(os.path.join(root, "app", "src", "main", "AndroidManifest.xml"),
                    encoding="utf-8").read()
    action_view = manifest.split("android.intent.action.VIEW", 1)[1].split("</intent-filter>", 1)[0]
    for info in platforms.PLATFORMS.values():  # links opened from other apps must reach vidspy
        for domain in info["domains"]:
            # every full domain may also appear WITHOUT the "*" wildcard, so require either form
            assert f'"*.{domain}"' in action_view or f'"{domain}"' in action_view, domain


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


def test_tiktok_api_fallback_items(monkeypatch):
    """The phone's only working TikTok route: parse the tikwm response (extractors.py)."""
    from vidspy_backend import extractors

    video = extractors.tikwm_items({
        "play": "//v19.tiktokcdn-us.com/x/video/tos/a/?a=1",
        "hdplay": None, "wmplay": "//v19.tiktokcdn-us.com/y.mp4",
        "cover": "https://p16/cover.jpg", "title": "delicious",
    })
    assert len(video) == 1 and video[0]["type"] == "video" and video[0]["ext"] == "mp4"
    assert video[0]["url"] == "https://v19.tiktokcdn-us.com/x/video/tos/a/?a=1"  # hdplay preferred, // completed
    assert video[0]["thumbnail"] == "https://p16/cover.jpg"

    images = extractors.tikwm_items({"images": ["https://f/a.jpg", "https://f/b.png"], "title": ""})
    assert [i["type"] for i in images] == ["image", "image"]
    assert images[1]["ext"] == "png" and images[0]["ext"] == "jpg"

    assert extractors.tikwm_items({}) == []   # nothing usable -> none

    # errors: the API said no / network path
    import requests
    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"code": -1, "msg": "FreeApi lies too much, please upgrade"}
    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    with pytest.raises(ValueError, match="tikwm said"):
        extractors.tiktok_api_extract("https://www.tiktok.com/@a/video/1")


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


# -- error traces: only on failures, secrets redacted (see vidspy_backend/traces.py) --

@pytest.fixture
def traced(tmp_path):
    logic.configure(str(tmp_path))
    yield tmp_path / "traces"
    from vidspy_backend import traces
    traces.end()
    traces._dir = None
    traces._last = None


def test_trace_written_only_on_errors(traced, monkeypatch):
    item = {"type": "video", "url": "https://cdn/x.mp4", "ext": "mp4", "title": "t", "headers": {}}
    monkeypatch.setitem(logic.PLATFORM_EXTRACTORS, "tiktok", (lambda u: [item],))
    out = json.loads(logic.resolve("https://www.tiktok.com/@a/video/1"))
    assert out["platform"] == "tiktok"
    assert list(traced.glob("*.json")) == []  # success -> nothing was written

    monkeypatch.setitem(logic.PLATFORM_EXTRACTORS, "tiktok", (lambda u: None,))  # no media
    with pytest.raises(logic.VidspyError):
        logic.resolve("https://vm.tiktok.com/xyz/")

    files = list(traced.glob("*.json"))
    assert len(files) == 1
    data = json.load(open(files[0], encoding="utf-8"))
    assert data["stage"] == "resolve" and data["input"]["platform"] == "tiktok"
    assert data["error"]["type"] == "VidspyError" and data["error"]["traceback"]
    assert data["steps"][0]["step"] == "<lambda>" and data["steps"][0]["items"] == 0  # the chain ran, found nothing
    assert data["app"]["yt_dlp"] and data["app"]["vidspy"]  # versions are in there for backwards debugging


def test_trace_linked_account_cookie_names_not_values(traced):
    from vidspy_backend import accounts

    accounts.connect("tiktok", "sessionid=TOKEN_VALUE; other=zzz")
    logic.trace_report("playback", "HTTP 403", None, "", "https://www.tiktok.com/@a/video/1", "tiktok")
    data = json.load(open(traced / logic.last_trace_name(), encoding="utf-8"))
    assert "sessionid" in data["linked_account"]["tiktok"]   # names are the useful part
    assert "TOKEN_VALUE" not in json.dumps(data)             # values never leave the app


def test_trace_headers_and_item_redacted(traced):
    item = {"type": "video", "url": "https://cdn/x.mp4", "ext": "mp4", "title": "t",
            "headers": {"Cookie": "sessionid=SECRET_VAL; other=V", "User-Agent": "UA"},
            "audio": {"url": "https://cdn/a.m4a", "ext": "m4a", "headers": {"Cookie": "sid=SECRET2"}}}
    logic.trace_report("playback", "Cannot play this video", json.dumps(item), "ERR: cause", "", "tiktok")
    raw = open(traced / logic.last_trace_name(), encoding="utf-8").read()
    data = json.loads(raw)
    assert "SECRET_VAL" not in raw and "SECRET2" not in raw
    assert "sessionid" in data["item"]["headers"]["Cookie"]  # names kept: what was attached?
    assert data["item"]["headers"]["User-Agent"] == "UA"     # non-secret headers stay complete
    assert data["error"]["detail"] == "ERR: cause"


def test_trace_steps_and_logs_from_a_traced_resolve(traced, monkeypatch):
    import logging

    from vidspy_backend import traces

    def loud_extractor(url):
        t = traces.current()
        assert t is not None
        t.logs["ytdlp"]  # a probe writes here
        return []

    monkeypatch.setitem(logic.PLATFORM_EXTRACTORS, "tiktok", (loud_extractor,))
    with pytest.raises(logic.VidspyError):
        with traces.Probe("ytdlp"):  # like a real extractor that attaches its own logger
            logic.resolve("https://www.tiktok.com/@a/video/1")
    data = json.load(open(traced / logic.last_trace_name(), encoding="utf-8"))
    assert any(s["step"] == "loud_extractor" for s in data["steps"])
    probe = traces.Probe("ytdlp")  # records into the (already ended) trace: no-op, stays silent
    probe.debug("should be ignored")
    assert data.get("logs") is None


def test_trace_pack_takes_every_report_for_the_button(traced, tmp_path):
    assert logic.trace_pack(str(tmp_path)) is None  # nothing failed yet: no zip, the button says so
    logic.trace_report("playback", "boom", None, "", "", "")
    logic.trace_report("playback", "bam", None, "", "", "")
    assert logic.traces_count() == 2
    zip_path = logic.trace_pack(str(tmp_path))
    import zipfile

    with zipfile.ZipFile(zip_path) as z:
        assert len(z.namelist()) == 2 and all(n.endswith(".json") for n in z.namelist())
    assert os.path.basename(zip_path).startswith("vidspy-traces-")
