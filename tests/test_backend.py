"""Pure-Python backend tests. Run: pip install pytest requests beautifulsoup4 && pytest tests"""
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


# ---- YouTube / HLS ---------------------------------------------------------

@pytest.mark.parametrize("url,platform", [
    ("https://www.youtube.com/watch?v=abc", "youtube"),
    ("https://youtu.be/abc", "youtube"),
    ("https://cdn.example.com/live/master.m3u8?token=1", "hls"),
    ("https://example.com/video.mp4", None),
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


def test_yt_dlp_hls_fallback_when_no_progressive():
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
