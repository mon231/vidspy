"""Audio/video merging: the pure-Python MP4 muxer and everything that feeds it."""
import itertools
import json
import os
import shutil
import struct
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from test_backend import _serve  # noqa: E402
from vidspy_backend import api as logic  # noqa: E402
from vidspy_backend import extractors as ex  # noqa: E402
from vidspy_backend import hls, mux, reddit  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
VIDEOS = ["v_prog.mp4", "v_frag.mp4"]
AUDIOS = ["a_prog.m4a", "a_frag.m4a"]


def fx(name):
    return os.path.join(FIX, name)


def sample_bytes(path, kind):
    with open(path, "rb") as f:
        out = []
        for offset, size in mux.track_samples(path, kind):
            f.seek(offset)
            out.append(f.read(size))
    return out


@pytest.mark.parametrize("video,audio", list(itertools.product(VIDEOS, AUDIOS)))
def test_mux_keeps_every_sample_byte_for_byte(video, audio, tmp_path):
    out = mux.mux_mp4(fx(video), fx(audio), str(tmp_path / "out.mp4"))
    assert sample_bytes(out, b"vide") == sample_bytes(fx(video), b"vide")
    assert sample_bytes(out, b"soun") == sample_bytes(fx(audio), b"soun")
    assert len(sample_bytes(out, b"vide")) == 30


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
@pytest.mark.parametrize("video,audio", list(itertools.product(VIDEOS, AUDIOS)))
def test_muxed_file_decodes_with_ffmpeg(video, audio, tmp_path):
    out = mux.mux_mp4(fx(video), fx(audio), str(tmp_path / "out.mp4"))
    run = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-f", "null", "-"], capture_output=True, text=True)
    assert run.stderr == ""
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", out],
                           capture_output=True, text=True)
    assert sorted(probe.stdout.split()) == ["audio", "video"]


def test_mux_rejects_missing_tracks(tmp_path):
    with pytest.raises(mux.MuxError):
        mux.mux_mp4(fx("a_prog.m4a"), fx("a_prog.m4a"), str(tmp_path / "x.mp4"))
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not an mp4 file at all")
    with pytest.raises(mux.MuxError):
        mux.mux_mp4(str(bad), fx("a_prog.m4a"), str(tmp_path / "x.mp4"))


def test_download_merges_separate_audio_item(tmp_path):
    files = {"v.mp4": open(fx("v_frag.mp4"), "rb").read(), "a.m4a": open(fx("a_frag.m4a"), "rb").read()}
    srv, base = _serve(files)
    try:
        item = {"type": "video", "url": f"{base}/v.mp4", "ext": "mp4", "title": "t", "headers": {},
                "audio": {"url": f"{base}/a.m4a", "ext": "m4a", "headers": {}}}
        path = logic.download(json.dumps(item), str(tmp_path))
        assert path.endswith(".mp4")
        assert sample_bytes(path, b"soun") == sample_bytes(fx("a_frag.m4a"), b"soun")
        assert sorted(os.listdir(tmp_path)) == [os.path.basename(path)]  # temp parts removed
    finally:
        srv.shutdown()


def _fmp4_parts(path):
    """Split a fragmented MP4 into (init segment, [moof+mdat segments])."""
    data = open(path, "rb").read()
    boxes, pos = [], 0
    while pos < len(data):
        size, kind = struct.unpack_from(">I4s", data, pos)
        boxes.append((kind, data[pos:pos + size]))
        pos += size
    init = b"".join(b for k, b in boxes if k in (b"ftyp", b"moov"))
    body = [b for k, b in boxes if k in (b"moof", b"mdat")]
    return init, [body[i] + body[i + 1] for i in range(0, len(body), 2)]


def _hls_files(prefix, path):
    init, segments = _fmp4_parts(path)
    names = {f"{prefix}_init.mp4": init}
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-TARGETDURATION:3", f'#EXT-X-MAP:URI="{prefix}_init.mp4"']
    for i, seg in enumerate(segments):
        names[f"{prefix}_{i}.m4s"] = seg
        lines += ["#EXTINF:1,", f"{prefix}_{i}.m4s"]
    lines.append("#EXT-X-ENDLIST")
    names[f"{prefix}.m3u8"] = "\n".join(lines).encode()
    return names


def test_hls_master_with_separate_audio_rendition_is_merged(tmp_path):
    files = {**_hls_files("video", fx("v_frag.mp4")), **_hls_files("audio", fx("a_frag.m4a"))}
    files["master.m3u8"] = (b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="en",DEFAULT=YES,URI="audio.m3u8"\n'
                            b'#EXT-X-STREAM-INF:BANDWIDTH=900,AUDIO="aud"\nvideo.m3u8\n')
    srv, base = _serve(files)
    try:
        post = json.loads(logic.resolve(f"{base}/master.m3u8"))
        path = logic.download(json.dumps(post["items"][0]), str(tmp_path))
        assert path.endswith(".mp4")
        assert len(sample_bytes(path, b"vide")) == 30 and sample_bytes(path, b"soun")
    finally:
        srv.shutdown()


def test_hls_separate_ts_audio_is_refused_instead_of_silent(tmp_path):
    srv, base = _serve({
        "master.m3u8": (b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="en",URI="a.m3u8"\n'
                        b'#EXT-X-STREAM-INF:BANDWIDTH=9,AUDIO="a"\nv.m3u8\n'),
        "v.m3u8": b"#EXTM3U\n#EXTINF:2,\nv0.ts\n#EXT-X-ENDLIST\n",
        "a.m3u8": b"#EXTM3U\n#EXTINF:2,\na0.ts\n#EXT-X-ENDLIST\n", "v0.ts": b"V", "a0.ts": b"A"})
    try:
        item = {"url": f"{base}/master.m3u8", "ext": "m3u8", "title": "t", "headers": {}}
        with pytest.raises(logic.VidspyError, match="separate"):
            logic.download(json.dumps(item), str(tmp_path))
    finally:
        srv.shutdown()


def fmt(fid, protocol="https", ext="mp4", height=None, v="avc1", a="none", **extra):
    return {"format_id": fid, "url": f"u{fid}", "protocol": protocol, "ext": ext, "height": height,
            "vcodec": v, "acodec": a, **extra}


def test_choose_prefers_a_format_that_already_has_sound():
    formats = [fmt("18", height=360, a="aac"), fmt("v", height=720), fmt("a", ext="m4a", v="none", a="mp4a")]
    assert ex._choose(formats)[0]["format_id"] == "18"


def test_choose_merges_video_only_mp4_with_m4a_audio():
    formats = [fmt("lo", height=360), fmt("hi", height=720), fmt("big", height=2160),
               fmt("web", ext="webm", v="vp9", height=720),
               fmt("a1", ext="m4a", v="none", a="mp4a", abr=48), fmt("a2", ext="m4a", v="none", a="mp4a", abr=128),
               fmt("a2-drc", ext="m4a", v="none", a="mp4a", abr=999), fmt("opus", ext="webm", v="none", a="opus")]
    video, audio, is_hls = ex._choose(formats)
    assert (video["format_id"], audio["format_id"], is_hls) == ("hi", "a2", False)


def test_choose_never_returns_silent_video_when_audio_exists_elsewhere(monkeypatch):
    monkeypatch.setattr(hls, "playable", lambda session, url: None)
    formats = [fmt("web", ext="webm", v="vp9", height=720),
               fmt("hv", protocol="m3u8_native", height=720),
               fmt("ha", protocol="m3u8_native", v="none", a=None)]
    video, audio, is_hls = ex._choose(formats)
    assert (video["format_id"], audio["format_id"], is_hls) == ("hv", "ha", True)


def test_choose_falls_back_to_silent_only_without_any_audio():
    formats = [fmt("v", height=720)]
    video, audio, _ = ex._choose(formats)
    assert video["format_id"] == "v" and audio is None


MPD = """<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period>
<AdaptationSet><Representation bandwidth="2000" height="1080" mimeType="video/mp4"><BaseURL>DASH_1080</BaseURL></Representation>
<Representation bandwidth="900" height="480" mimeType="video/mp4"><BaseURL>DASH_480</BaseURL></Representation>
<Representation bandwidth="1500" height="720" mimeType="video/mp4"><BaseURL>DASH_720</BaseURL></Representation></AdaptationSet>
<AdaptationSet><Representation bandwidth="64" mimeType="audio/mp4"><BaseURL>DASH_AUDIO_64</BaseURL></Representation>
<Representation bandwidth="128" mimeType="audio/mp4"><BaseURL>DASH_AUDIO_128</BaseURL></Representation></AdaptationSet>
</Period></MPD>"""


class FakeSession:
    def __init__(self, text):
        self.text = text

    def get(self, url, **kw):
        outer = self

        class R:
            text = outer.text

            def raise_for_status(self):
                pass
        return R()


def test_reddit_dash_item_has_video_and_audio():
    item = reddit.dash_item(FakeSession(MPD), "abc123", "t", None, {})
    assert item["url"] == "https://v.redd.it/abc123/DASH_720"
    assert item["audio"]["url"] == "https://v.redd.it/abc123/DASH_AUDIO_128"
    post = {"title": "v", "secure_media": {"reddit_video": {
        "fallback_url": "https://v.redd.it/abc123/DASH_720.mp4?source=fallback"}}}
    assert reddit.parse_post(post, FakeSession(MPD))[0]["audio"]["ext"] == "mp4"


def test_playable_accepts_live_and_aes_but_not_drm():
    live = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:900\n#EXTINF:4,\nseg900.ts\n"
    aes = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="k"\n#EXTINF:4,\ns.ts\n#EXT-X-ENDLIST\n'
    drm = '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://drm"\n#EXTINF:4,\ns.m4s\n#EXT-X-ENDLIST\n'
    srv, base = _serve({"live.m3u8": live.encode(), "aes.m3u8": aes.encode(), "drm.m3u8": drm.encode(),
                        "bad.m3u8": b"<html>nope</html>"})
    import requests
    try:
        session = requests.Session()
        hls.playable(session, f"{base}/live.m3u8")
        hls.playable(session, f"{base}/aes.m3u8")
        for name in ("drm.m3u8", "bad.m3u8"):
            with pytest.raises(hls.HlsError):
                hls.playable(session, f"{base}/{name}")
    finally:
        srv.shutdown()


def test_live_stream_can_be_watched_but_not_downloaded(tmp_path):
    srv, base = _serve({"live.m3u8": b"#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nseg0.ts\n", "seg0.ts": b"X"})
    try:
        assert json.loads(logic.resolve(f"{base}/live.m3u8"))["items"][0]["ext"] == "m3u8"
        item = {"url": f"{base}/live.m3u8", "ext": "m3u8", "title": "live", "headers": {}}
        with pytest.raises(logic.VidspyError, match="live"):
            logic.download(json.dumps(item), str(tmp_path))
    finally:
        srv.shutdown()
