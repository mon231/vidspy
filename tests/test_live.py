"""Live smoke tests: resolve (and download) a public video post anonymously, per platform.

They only use the shared backend package (`vidspy_backend`), exactly like the APK and the desktop
app do, with no linked account. Sites change and rate-limit, so each platform has several sample
posts and every sample is retried before the platform counts as failed.

Run:  pytest -m live            (all platforms)
      pytest -m live -k reddit  (one platform)
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import vidspy_backend as backend  # noqa: E402

pytestmark = pytest.mark.live

ATTEMPTS = 3  # tries per sample post before moving on to the next one

# platform -> public video posts (first one that works is enough)
SAMPLES = {
    "youtube": ["https://www.youtube.com/watch?v=jNQXAC9IVRw", "https://www.youtube.com/watch?v=aqz-KE-bpKQ"],
    "vimeo": ["https://vimeo.com/22439234", "https://vimeo.com/76979871"],  # the 2nd one is DRM-protected
    "tiktok": ["https://www.tiktok.com/@tiktok/video/7106594312292453675",
               "https://www.tiktok.com/@scout2015/video/6718335390845095173"],
    "instagram": ["https://instagram.com/p/aye83DjauH/", "https://www.instagram.com/tv/aye83DjauH/"],
    "linkedin": ["https://www.linkedin.com/feed/update/urn:li:activity:6526155145804341248",
                 "https://www.linkedin.com/posts/skyperry_shortvideo-a-golden-key-to-businesss-activity-6988053558688456704-W_Ji"],
    "twitter": ["https://twitter.com/NASA/status/1595471051403644929"],
    "facebook": ["https://www.facebook.com/watch/?v=10153231379946729",
                 "https://www.facebook.com/facebook/videos/10153231379946729/"],
    "reddit": ["https://www.reddit.com/r/videos/comments/6rrwyj/that_small_heart_attack/"],
    "pinterest": ["https://www.pinterest.com/pin/424605071144623935/",
                  "https://www.pinterest.com/pin/424605071145711021/"],
    "hls": ["https://devstreaming-cdn.apple.com/videos/streaming/examples/img_bipbop_adv_example_fmp4/master.m3u8",
            "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"],
    "twitch": ["https://clips.twitch.tv/FaintLightGullWholeWheat"],
    "snapchat": ["https://www.snapchat.com/spotlight/W7_EDlXWTBiXAEEniNoMPwAAYdGFzb3FpYXVuAZqDMm6sAZqDMm6VAAAAAQ"],
    "imgur": ["https://imgur.com/gallery/kx2uD3C",
              "https://imgur.com/gallery/YcAQlkx",
              "https://imgur.com/a/iX265HX"],
    "tumblr": ["https://www.tumblr.com/anyaboz/765332564457209856/my-music-video-for-selkie-by-nobodys-wolf-child",
               "https://www.tumblr.com/silverfoxstole/765305403763556352/jamie-robertson-doctor-who-8th-doctor"],
    "dailymotion": ["https://www.dailymotion.com/video/x8u4owg"],
    "direct": ["https://download.samplelib.com/mp4/sample-5s.mp4",
               "https://upload.wikimedia.org/wikipedia/commons/3/3a/Cat03.jpg"],
}
NO_DOWNLOAD = {"hls"}  # long streams: resolving already validates the playlist
MAY_BE_IMAGE = {"imgur", "tumblr", "direct"}  # some sample posts are image-only
WITH_SOUND = {"youtube", "vimeo", "tiktok", "linkedin", "twitter", "facebook", "reddit", "tumblr"}

# Public anonymous FTP is vanishing; funet's astronomy tree has kept real media for decades.
FTP_SAMPLES = [
    "ftp://ftp.funet.fi/pub/astro/images/ccd/Trifid.jpg",
    "ftp://ftp.funet.fi/pub/astro/solarrep/Images/eclipse.gif",
    "ftp://ftp.funet.fi/pub/astro/solarrep/comets/01jun98a.mpg",
    "ftp://ftp.funet.fi/pub/astro/images/ccd/m13b.jpg",
]


@pytest.mark.parametrize("url", FTP_SAMPLES)
def test_ftp_direct_links(url, tmp_path):
    for attempt in range(ATTEMPTS):
        try:
            post = json.loads(backend.resolve(url))
            break
        except Exception as e:  # noqa: BLE001 - retry, FTP data connections drop sometimes
            if attempt == ATTEMPTS - 1:
                pytest.fail(f"ftp resolve failed: {e}")
            time.sleep(2)
    assert post["platform"] == "direct"
    item = post["items"][0]
    assert item["url"] == url and item["note"]  # note: phones stream http(s) only, download instead
    assert item["ext"] and item["type"] in ("video", "image")
    path = backend.download(json.dumps(item), str(tmp_path))
    assert os.path.getsize(path) > 10_000, path  # sample posts that have audio


@pytest.fixture(autouse=True)
def anonymous(tmp_path):
    backend.configure(str(tmp_path / "accounts"))  # empty dir: no linked accounts
    yield


def _resolve_first_working(urls, image_ok=False):
    errors = []
    for url in urls:
        for attempt in range(ATTEMPTS):
            try:
                post = json.loads(backend.resolve(url))
                media = next((i for i in post["items"] if i["type"] == "video"), None)
                if not media and image_ok:
                    media = next((i for i in post["items"] if i["type"] == "image"), None)
                if media:
                    return post, media
                errors.append(f"{url}: no media item")
                break
            except Exception as e:  # noqa: BLE001 - collect and retry
                errors.append(f"{url} (try {attempt + 1}): {e}")
                time.sleep(2 * (attempt + 1))
    pytest.fail("no sample post worked:\n  " + "\n  ".join(errors))


def test_every_platform_has_a_sample():
    assert set(SAMPLES) == set(backend.platforms.PLATFORMS) | {"hls", "direct"}


@pytest.mark.parametrize("platform", sorted(SAMPLES))
def test_anonymous_public_video(platform, tmp_path):
    post, media = _resolve_first_working(SAMPLES[platform], platform in MAY_BE_IMAGE)
    assert post["platform"] == platform
    assert media["url"].startswith("http") and media["ext"] and media["type"] in ("video", "image")
    if platform in NO_DOWNLOAD:
        return
    path = backend.download(json.dumps(media), str(tmp_path))
    assert os.path.getsize(path) > 10_000, path
    if platform in WITH_SOUND and media["type"] == "video":
        assert backend.mux.track_samples(path, b"soun"), f"downloaded video has no sound: {path}"
