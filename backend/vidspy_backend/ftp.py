"""Bare ftp:// and ftps:// media links: anonymous probing and chunked streaming (stdlib `ftplib`).

Like the `hls` pseudo-platform: `extractors.direct_extract` probes the file with status commands
only (TYPE I + SIZE, no body), then the same chunked machinery moves the bytes -- `api.download`
writes 64KB chunks straight to disk, and the desktop app relays the file as a seekable http
stream through its /media proxy (phones can't play plain ftp:// URLs, only http(s), so FTP items
carry a download-first note).
"""
import ftplib
from urllib.parse import unquote, urlsplit

TIMEOUT = 30

CONTENT_TYPES = {
    "mp4": "video/mp4", "m4v": "video/mp4", "webm": "video/webm", "mkv": "video/x-matroska",
    "mov": "video/quicktime", "ts": "video/mp2t", "3gp": "video/3gpp", "ogv": "video/ogg",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp",
    "gif": "image/gif", "avif": "image/avif", "heic": "image/heic", "bmp": "image/bmp",
}


def parse(url):
    """('ftp'|'ftps', host, port, user, password, path) of a bare-FTP link, or ValueError."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("ftp", "ftps") or not parts.hostname:
        raise ValueError("not an FTP link")
    path = unquote(parts.path or "")
    if not path or path.endswith("/"):
        raise ValueError("no file path in the FTP link")
    return (scheme, parts.hostname, parts.port or 21,
            unquote(parts.username or "anonymous"),
            unquote(parts.password or "vidspy@example.invalid"), path)


def connect(url):
    """(logged-in TYPE-I connection, decoded file path). Anonymous unless user:pass@ is given."""
    scheme, host, port, user, passwd, path = parse(url)
    conn = (ftplib.FTP_TLS if scheme == "ftps" else ftplib.FTP)(timeout=TIMEOUT)
    try:
        conn.connect(host, port)
        conn.login(user, passwd)
        if scheme == "ftps":
            try:
                conn.prot_p()  # PROT-P-less servers still accept a clear data channel
            except ftplib.all_errors:
                pass
        conn.voidcmd("TYPE I")  # SIZE and RETR become binary-safe
    except ftplib.all_errors as e:
        _quietly_close(conn)
        raise ValueError(f"the FTP server {host} refused the session: {_one_line(e)}") from None
    return conn, path


def _one_line(e):
    return " ".join(str(e).split())[:200]


def _quietly_close(conn):
    try:
        conn.quit()
    except Exception:  # noqa: BLE001 - dead control connections are fine to abandon
        pass


def _size(conn, path):
    """File size from SIZE; None when the server lacks the command (downloads just lose the bar)."""
    try:
        return int(conn.sendcmd(f"SIZE {path}").split()[-1])
    except ftplib.error_perm as e:
        if _one_line(e)[:3] in ("500", "502"):  # SIZE unsupported is not a failed probe
            return None
        raise ValueError(f"the FTP server refused the file: {_one_line(e)}") from None


def probe(url):
    """(file name, ext, size | None) of an FTP media file, via status commands only -- no body."""
    conn, path = connect(url)
    try:
        size = _size(conn, path)
    finally:
        _quietly_close(conn)
    name = path.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return name, ext, size


def download(url, dest_base, ext):
    """RETR straight to `dest_base.ext` in chunks -- nothing is buffered in RAM."""
    conn, path = connect(url)
    try:
        out = f"{dest_base}.{ext}"
        with open(out, "wb") as f:
            conn.retrbinary(f"RETR {path}", f.write, blocksize=64 * 1024)
        return out
    finally:
        _quietly_close(conn)


def open_stream(url, requested_offset=0):
    """(ext, served_offset, size | None, chunks) to relay one FTP file as an http body.

    `chunks` is a lazy 64KB-block generator. With a requested offset larger than 0 it seeks via
    REST -- but only when the total size is known, because a valid Content-Range needs it;
    otherwise it serves the whole file from byte 0 with 200."""
    conn, path = connect(url)
    size = _size(conn, path)
    offset = requested_offset if (requested_offset and size is not None) else 0
    name = path.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

    def chunks():
        try:
            sock = conn.transfercmd(f"RETR {path}", rest=offset or None)
        except ftplib.all_errors:
            _quietly_close(conn)
            return
        try:
            while True:
                block = sock.recv(64 * 1024)
                if not block:
                    break
                yield block
        except ftplib.all_errors:
            pass  # consumer aborted mid-stream: the relay connection is dropped anyway
        finally:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                conn.voidresp()  # normal completion: read the 226 so the session stays sane
            except Exception:  # noqa: BLE001
                pass
            _quietly_close(conn)

    return ext, offset, size, chunks()
