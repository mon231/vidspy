"""Minimal HLS (.m3u8) support without ffmpeg: parse with `m3u8`, download segments, concatenate.

MPEG-TS segments are concatenated into a `.ts` file; fragmented-MP4 playlists (EXT-X-MAP)
become a `.mp4`. Encrypted playlists are not supported.
"""
import os

import m3u8

from . import mux

TIMEOUT = 30


class HlsError(Exception):
    pass


def load_playlist(session, url):
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    # use the post-redirect URL so relative segment URIs resolve correctly
    return m3u8.loads(resp.text, uri=resp.url)


def pick_media_playlist(session, url):
    """Return (media_playlist, url). A master playlist resolves to its highest-bandwidth variant."""
    playlist = load_playlist(session, url)
    if playlist.is_variant:
        variants = [p for p in playlist.playlists if p.absolute_uri]
        if not variants:
            raise HlsError("Playlist has no variants")
        best = max(variants, key=lambda p: (p.stream_info.bandwidth or 0))
        url = best.absolute_uri
        playlist = load_playlist(session, url)
    if not playlist.segments:
        raise HlsError("Not a valid HLS playlist (no segments)")
    return playlist, url


def is_encrypted(playlist):
    return any(k and (k.method or "NONE").upper() != "NONE" for k in playlist.keys)


_DRM_MARKERS = ("SAMPLE-AES", "skd://", "widevine", "playready", "streamingkeydelivery")


def playable(session, url):
    """Cheap check used before streaming: `url` is an HLS playlist and not DRM-protected.

    Only the playlist text is read, never the media, so live streams and huge VODs are fine.
    AES-128 is accepted: the players decrypt it themselves."""
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    if not resp.text.lstrip().startswith("#EXTM3U"):
        raise HlsError("Not a valid HLS playlist")
    if any(marker in resp.text for marker in _DRM_MARKERS):
        raise HlsError("This stream is DRM-protected")


def check_playlist(session, url):
    """Return the media playlist of `url`; raise HlsError/requests errors if it is not downloadable."""
    playlist, _ = pick_media_playlist(session, url)
    if is_encrypted(playlist):
        raise HlsError("Encrypted HLS streams are not supported")
    return playlist


def audio_rendition(session, url):
    """URL of the separate audio playlist belonging to the chosen variant of a master playlist."""
    master = load_playlist(session, url)
    if not master.is_variant:
        return None
    variants = [p for p in master.playlists if p.absolute_uri]
    if not variants:
        return None
    group = max(variants, key=lambda p: (p.stream_info.bandwidth or 0)).stream_info.audio
    renditions = [m for m in master.media
                  if m.type == "AUDIO" and m.group_id == group and m.uri and m.absolute_uri]
    renditions.sort(key=lambda m: m.default != "YES")
    return renditions[0].absolute_uri if group and renditions else None


def _fetch(session, url, retries=3):
    last = None
    for _ in range(retries):
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r.content
        except Exception as e:  # noqa: BLE001
            last = e
    raise HlsError(f"Segment download failed: {last}")


def _save(session, playlist, dest_base):
    if not playlist.is_endlist:
        raise HlsError("This is a live stream: it can be watched but not downloaded")
    maps = [m for m in (playlist.segment_map or []) if m.absolute_uri]
    path = f"{dest_base}.{'mp4' if maps else 'ts'}"
    with open(path, "wb") as f:
        written = set()
        for m in maps:
            if m.absolute_uri not in written:
                written.add(m.absolute_uri)
                f.write(_fetch(session, m.absolute_uri))
        for seg in playlist.segments:
            f.write(_fetch(session, seg.absolute_uri))
    return path


def download_hls(session, url, dest_base, audio_url=None):
    """Download the stream at `url` to `dest_base` + '.ts'/'.mp4'; returns the path.

    A separate audio playlist (given, or declared by a master playlist) is merged in by `mux.py`.
    """
    playlist = check_playlist(session, url)
    audio_url = audio_url or audio_rendition(session, url)
    if not audio_url:
        return _save(session, playlist, dest_base)

    audio = check_playlist(session, audio_url)
    if not (playlist.segment_map and audio.segment_map):
        raise HlsError("This stream keeps its audio in separate MPEG-TS segments, which cannot be merged")
    video_path = _save(session, playlist, dest_base + ".video")
    audio_path = _save(session, audio, dest_base + ".audio")
    try:
        return mux.mux_mp4(video_path, audio_path, dest_base + ".mp4")
    finally:
        for part in (video_path, audio_path):
            os.remove(part)
