"""Pure-Python MP4 remuxer: merges a video-only and an audio-only MP4 into one progressive MP4.

No ffmpeg and no re-encoding, so it also runs on Android. Inputs may be regular or fragmented
(DASH) MP4. Samples are copied byte for byte and the sample tables are rebuilt, which also turns
fragmented inputs into a normal seekable file.
"""
import os
import shutil
import struct

from .errors import VidspyError

_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"mvex", b"moof", b"traf", b"edts"}
_MAX32 = 0xFFFFFFFF


class MuxError(VidspyError):
    pass


class Box:
    def __init__(self, type_, data=b"", children=None):
        self.type = type_
        self.data = data
        self.children = children

    def pack(self):
        payload = b"".join(c.pack() for c in self.children) if self.children is not None else self.data
        return struct.pack(">I4s", 8 + len(payload), self.type) + payload

    def find(self, type_):
        return next((c for c in self.children or [] if c.type == type_), None)

    def findall(self, type_):
        return [c for c in self.children or [] if c.type == type_]


def _parse(buf, start=0, end=None):
    end = len(buf) if end is None else end
    boxes, pos = [], start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", buf, pos)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", buf, pos + 8)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            raise MuxError("Corrupt MP4 data")
        body = pos + header
        if kind in _CONTAINERS:
            boxes.append(Box(kind, children=_parse(buf, body, pos + size)))
        else:
            boxes.append(Box(kind, bytes(buf[body:pos + size])))
        pos += size
    return boxes


def _scan(f, file_size):
    """Top-level boxes of a file: (type, offset, size, header_len)."""
    pos = 0
    while pos + 8 <= file_size:
        f.seek(pos)
        head = f.read(16)
        size, kind = struct.unpack(">I4s", head[:8])
        header = 8
        if size == 1:
            size = struct.unpack(">Q", head[8:16])[0]
            header = 16
        elif size == 0:
            size = file_size - pos
        if size < header:
            raise MuxError("Corrupt MP4 data")
        yield kind, pos, size, header
        pos += size


def _u(data, offset, fmt):
    return struct.unpack_from(fmt, data, offset)[0]


class _Track:
    def __init__(self, kind, trak_box, path):
        self.kind = kind
        self.trak = trak_box
        self.path = path
        mdhd = trak_box.find(b"mdia").find(b"mdhd").data
        self.timescale = _u(mdhd, 12 if mdhd[0] == 0 else 20, ">I")
        tkhd = trak_box.find(b"tkhd").data
        self.track_id = _u(tkhd, 12 if tkhd[0] == 0 else 20, ">I")
        self.sizes, self.durations, self.offsets_cto, self.sync = [], [], [], []
        self.chunks = []  # (file offset, byte length, sample count), in storage order


def _handler(trak):
    return trak.find(b"mdia").find(b"hdlr").data[8:12]


def _read_moov(f, file_size):
    moov = None
    for kind, pos, size, header in _scan(f, file_size):
        if kind == b"moov":
            f.seek(pos + header)
            moov = _parse(f.read(size - header))
            return Box(b"moov", children=moov)
    raise MuxError("Not an MP4 file (no moov box)")


def _expand_runs(data, count_offset, fmt_entry, signed_version=False):
    count = _u(data, count_offset, ">I")
    size = struct.calcsize(fmt_entry)
    return [struct.unpack_from(fmt_entry, data, count_offset + 4 + i * size) for i in range(count)]


def _load_progressive(track, moov_stbl):
    """Fill `track` from the sample tables of a regular (non-fragmented) MP4."""
    stsz = moov_stbl.find(b"stsz").data
    constant, count = struct.unpack_from(">II", stsz, 4)
    sizes = [constant] * count if constant else list(struct.unpack_from(f">{count}I", stsz, 12))

    durations = []
    for n, d in _expand_runs(moov_stbl.find(b"stts").data, 4, ">II"):
        durations.extend([d] * n)

    ctts = moov_stbl.find(b"ctts")
    cto = [0] * count
    if ctts is not None:
        fmt = ">Ii" if ctts.data[0] == 1 else ">II"
        i = 0
        for n, off in _expand_runs(ctts.data, 4, fmt):
            cto[i:i + n] = [off] * n
            i += n

    stss = moov_stbl.find(b"stss")
    sync = None if stss is None else [e[0] for e in _expand_runs(stss.data, 4, ">I")]

    stsc = _expand_runs(moov_stbl.find(b"stsc").data, 4, ">III")
    co = moov_stbl.find(b"stco")
    offsets = ([e[0] for e in _expand_runs(co.data, 4, ">I")] if co is not None
               else [e[0] for e in _expand_runs(moov_stbl.find(b"co64").data, 4, ">Q")])
    sample = 0
    for i, offset in enumerate(offsets):
        spc = stsc[0][1]
        for first, per, _ in stsc:
            if first <= i + 1:
                spc = per
        length = sum(sizes[sample:sample + spc])
        track.chunks.append((offset, length, spc))
        sample += spc
    track.sizes, track.durations, track.offsets_cto, track.sync = sizes, durations, cto, sync


def _load_fragmented(track, moov, f, file_size):
    """Fill `track` from the moof/mdat fragments of a fragmented MP4."""
    trex = {}
    for t in moov.find(b"mvex").findall(b"trex"):
        tid, _, dur, size, flags = struct.unpack_from(">5I", t.data, 4)
        trex[tid] = (dur, size, flags)
    default = trex.get(track.track_id, (0, 0, 0))
    index = 0
    for kind, pos, size, header in _scan(f, file_size):
        if kind != b"moof":
            continue
        f.seek(pos)
        moof = _parse(f.read(size))[0]
        for traf in moof.findall(b"traf"):
            tfhd = traf.find(b"tfhd").data
            flags = int.from_bytes(tfhd[1:4], "big")
            if _u(tfhd, 4, ">I") != track.track_id:
                continue
            base, p = pos, 8
            if flags & 0x1:
                base = _u(tfhd, p, ">Q")
                p += 8
            if flags & 0x2:
                p += 4
            d_dur, d_size, d_flags = default
            if flags & 0x8:
                d_dur = _u(tfhd, p, ">I")
                p += 4
            if flags & 0x10:
                d_size = _u(tfhd, p, ">I")
                p += 4
            if flags & 0x20:
                d_flags = _u(tfhd, p, ">I")
            cursor = None
            for trun in traf.findall(b"trun"):
                d = trun.data
                tf = int.from_bytes(d[1:4], "big")
                count = _u(d, 4, ">I")
                p = 8
                start = cursor
                if tf & 0x1:
                    start = base + _u(d, p, ">i")
                    p += 4
                if start is None:
                    raise MuxError("Unsupported fragment layout")
                first_flags = None
                if tf & 0x4:
                    first_flags = _u(d, p, ">I")
                    p += 4
                total = 0
                for i in range(count):
                    dur, size, sflags, cto = d_dur, d_size, d_flags, 0
                    if tf & 0x100:
                        dur = _u(d, p, ">I"); p += 4
                    if tf & 0x200:
                        size = _u(d, p, ">I"); p += 4
                    if tf & 0x400:
                        sflags = _u(d, p, ">I"); p += 4
                    if tf & 0x800:
                        cto = _u(d, p, ">i" if d[0] == 1 else ">I"); p += 4
                    if i == 0 and first_flags is not None:
                        sflags = first_flags
                    index += 1
                    track.sizes.append(size)
                    track.durations.append(dur)
                    track.offsets_cto.append(cto)
                    if not (sflags >> 16) & 1:
                        track.sync.append(index)
                    total += size
                track.chunks.append((start, total, count))
                cursor = start + total
    if len(track.sync) == len(track.sizes):
        track.sync = None  # every sample is a sync sample


def _read_track(path, want):
    """Return the first track of kind `want` (b'vide' / b'soun') with its sample tables."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        moov = _read_moov(f, size)
        for trak in moov.findall(b"trak"):
            if _handler(trak) == want:
                track = _Track(want, trak, path)
                track.moov = moov
                if moov.find(b"mvex") is not None:
                    _load_fragmented(track, moov, f, size)
                else:
                    _load_progressive(track, trak.find(b"mdia").find(b"minf").find(b"stbl"))
                return track
    raise MuxError(f"No {'video' if want == b'vide' else 'audio'} track found")


def track_samples(path, want):
    """(offset, size) of every sample of the first `want` track, in decode order (for tests)."""
    track = _read_track(path, want)
    samples, sample = [], 0
    for offset, _, count in track.chunks:
        for size in track.sizes[sample:sample + count]:
            samples.append((offset, size))
            offset += size
        sample += count
    return samples


def _set(data, offset, fmt, value):
    buf = bytearray(data)
    struct.pack_into(fmt, buf, offset, value)
    return bytes(buf)


def _runs(values):
    out = []
    for v in values:
        if out and out[-1][1] == v:
            out[-1][0] += 1
        else:
            out.append([1, v])
    return out


def _full(version, payload):
    return struct.pack(">I", version << 24) + payload


def _rebuild_trak(track, track_id, movie_timescale, keep_edits, wide_offsets):
    """A fresh trak box for `track`: sample tables rebuilt, chunk offsets still zero."""
    trak = _parse(track.trak.pack())[0]
    total = sum(track.durations)

    tkhd = trak.find(b"tkhd")
    v1 = tkhd.data[0] == 1
    tkhd.data = _set(tkhd.data, 20 if v1 else 12, ">I", track_id)
    movie_duration = round(total * movie_timescale / track.timescale)
    tkhd.data = _set(tkhd.data, 28 if v1 else 20, ">Q" if v1 else ">I", movie_duration)

    mdia = trak.find(b"mdia")
    mdhd = mdia.find(b"mdhd")
    v1 = mdhd.data[0] == 1
    mdhd.data = _set(mdhd.data, 24 if v1 else 16, ">Q" if v1 else ">I", total)

    if not keep_edits:
        trak.children = [c for c in trak.children if c.type != b"edts"]

    stbl = mdia.find(b"minf").find(b"stbl")
    children = [stbl.find(b"stsd"),
                Box(b"stts", _full(0, struct.pack(">I", len(_runs(track.durations)))
                                   + b"".join(struct.pack(">II", n, d) for n, d in _runs(track.durations))))]
    if any(track.offsets_cto):
        signed = any(c < 0 for c in track.offsets_cto)
        runs = _runs(track.offsets_cto)
        children.append(Box(b"ctts", _full(1 if signed else 0, struct.pack(">I", len(runs))
                                           + b"".join(struct.pack(">Ii" if signed else ">II", n, c)
                                                      for n, c in runs))))
    if track.sync is not None:
        children.append(Box(b"stss", _full(0, struct.pack(">I", len(track.sync))
                                           + b"".join(struct.pack(">I", s) for s in track.sync))))
    stsc, first = [], 1
    for n, per in _runs([c[2] for c in track.chunks]):
        stsc.append(struct.pack(">III", first, per, 1))
        first += n
    children.append(Box(b"stsc", _full(0, struct.pack(">I", len(stsc)) + b"".join(stsc))))
    children.append(Box(b"stsz", _full(0, struct.pack(">II", 0, len(track.sizes))
                                       + struct.pack(f">{len(track.sizes)}I", *track.sizes))))
    n = len(track.chunks)
    offsets = Box(b"co64" if wide_offsets else b"stco",
                  _full(0, struct.pack(">I", n) + b"\0" * (n * (8 if wide_offsets else 4))))
    children.append(offsets)
    stbl.children = children
    return trak, offsets, movie_duration


def _fill_offsets(box, offsets, wide):
    box.data = _full(0, struct.pack(">I", len(offsets)) + struct.pack(f">{len(offsets)}{'Q' if wide else 'I'}", *offsets))


def _chunk_times(track):
    times, sample, now = [], 0, 0
    for _, _, count in track.chunks:
        times.append(now / track.timescale)
        now += sum(track.durations[sample:sample + count])
        sample += count
    return times


def mux_mp4(video_path, audio_path, out_path):
    """Write a progressive MP4 with the video track of `video_path` and audio track of `audio_path`."""
    video = _read_track(video_path, b"vide")
    audio = _read_track(audio_path, b"soun")

    mvhd = video.moov.find(b"mvhd").data
    v1 = mvhd[0] == 1
    movie_timescale = _u(mvhd, 20 if v1 else 12, ">I")
    audio_id = max(video.track_id, audio.track_id) + 1 if audio.track_id == video.track_id else audio.track_id

    entries = []  # (time, order, track index, chunk index)
    for t_index, track in enumerate((video, audio)):
        for c_index, time in enumerate(_chunk_times(track)):
            entries.append((time, t_index, c_index))
    entries.sort()
    payload = sum(c[1] for track in (video, audio) for c in track.chunks)
    wide = payload > _MAX32 - (64 << 20)

    v_trak, v_off, v_dur = _rebuild_trak(video, video.track_id, movie_timescale, True, wide)
    a_trak, a_off, a_dur = _rebuild_trak(audio, audio_id, movie_timescale, False, wide)

    mvhd_data = _set(mvhd, 24 if v1 else 16, ">Q" if v1 else ">I", max(v_dur, a_dur))
    mvhd_data = _set(mvhd_data, 108 if v1 else 96, ">I", max(video.track_id, audio_id) + 1)
    moov = Box(b"moov", children=[Box(b"mvhd", mvhd_data), v_trak, a_trak])

    ftyp = Box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41").pack()
    mdat_header = 8 if payload + 8 <= _MAX32 else 16
    start = len(ftyp) + len(moov.pack()) + mdat_header

    new_offsets = ([], [])
    pos = start
    for _, t_index, c_index in entries:
        track = (video, audio)[t_index]
        new_offsets[t_index].append(pos)
        pos += track.chunks[c_index][1]
    _fill_offsets(v_off, new_offsets[0], wide)
    _fill_offsets(a_off, new_offsets[1], wide)
    moov_bytes = moov.pack()
    if len(ftyp) + len(moov_bytes) + mdat_header != start:
        raise MuxError("Internal error: moov size changed")

    tmp = out_path + ".part"
    sources = {video.path: open(video.path, "rb"), audio.path: open(audio.path, "rb")}
    try:
        with open(tmp, "wb") as out:
            out.write(ftyp)
            out.write(moov_bytes)
            out.write(struct.pack(">I4s", 8 + payload, b"mdat") if mdat_header == 8
                      else struct.pack(">I4sQ", 1, b"mdat", 16 + payload))
            for _, t_index, c_index in entries:
                track = (video, audio)[t_index]
                offset, length, _ = track.chunks[c_index]
                src = sources[track.path]
                src.seek(offset)
                shutil.copyfileobj(_Limited(src, length), out, 1 << 20)
    finally:
        for f in sources.values():
            f.close()
    os.replace(tmp, out_path)
    return out_path


class _Limited:
    def __init__(self, f, length):
        self.f, self.left = f, length

    def read(self, n=-1):
        n = self.left if n < 0 else min(n, self.left)
        data = self.f.read(n)
        if len(data) < n:
            raise MuxError("Truncated media file")
        self.left -= len(data)
        return data
