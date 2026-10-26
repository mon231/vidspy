"""Minimal HLS (.m3u8) support without ffmpeg: parse with `m3u8`, download segments, concatenate.

MPEG-TS segments are concatenated into a `.ts` file; fragmented-MP4 playlists (EXT-X-MAP)
become a `.mp4`. Encrypted playlists are not supported.
"""
import m3u8

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


def check_playlist(session, url):
    """Raise HlsError/requests error if `url` is not a downloadable (unencrypted) HLS playlist."""
    playlist, _ = pick_media_playlist(session, url)
    if is_encrypted(playlist):
        raise HlsError("Encrypted HLS streams are not supported")


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


def download_hls(session, url, dest_base):
    """Download the stream at `url` to `dest_base` + '.ts'/'.mp4'; returns the path."""
    playlist, _ = pick_media_playlist(session, url)
    if is_encrypted(playlist):
        raise HlsError("Encrypted HLS streams are not supported")
    maps = [m for m in (playlist.segment_map or []) if m.absolute_uri]
    ext = "mp4" if maps else "ts"
    path = f"{dest_base}.{ext}"
    with open(path, "wb") as f:
        written = set()
        for m in maps:
            if m.absolute_uri not in written:
                written.add(m.absolute_uri)
                f.write(_fetch(session, m.absolute_uri))
        for seg in playlist.segments:
            f.write(_fetch(session, seg.absolute_uri))
    return path
