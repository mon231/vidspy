"""vidspy desktop (Windows / macOS / Linux): Eel GUI over the same `vidspy_backend` package as the Android app.

    pip install -r desktop/requirements.txt
    python desktop/app.py                 # GUI
    python desktop/app.py --cli <url>     # print resolved media JSON (backend test)
"""
import json
import os
import re
import sys
import uuid
from urllib.parse import quote, urljoin

# --noconsole builds (PyInstaller) have no stdout/stderr; libraries that print would crash.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w"))

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))  # or: pip install -e backend

import vidspy_backend as backend  # noqa: E402  (same package the Android app bundles)
from vidspy_backend import extractors  # noqa: E402

backend.configure(os.path.join(os.path.expanduser("~"), ".vidspy"))
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "vidspy")
_items = {}  # id -> item dict; lets the browser stream via /media/<id> with the right headers
_last_text = ""  # share/caption text of the last resolve, for the error traces (see traces.py)


def rewrite_playlist(text, base_url, item_id, audio=False):
    """Point every URI of an HLS playlist back at /media/<id>?u=<abs url> so segments get the item's headers."""
    def proxied(uri):
        return f"/media/{item_id}?u=" + quote(urljoin(base_url, uri), safe="") + ("&a=1" if audio else "")

    out = []
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            line = proxied(line.strip())
        else:
            line = re.sub(r'URI="([^"]+)"', lambda m: f'URI="{proxied(m.group(1))}"', line)
        out.append(line)
    return "\n".join(out) + "\n"


def _resolve(text):
    global _last_text
    post = json.loads(backend.resolve(text))
    _last_text = text
    for item in post["items"]:
        item["id"] = uuid.uuid4().hex
        item["platform"] = post["platform"]  # only in the desktop copy: player errors trace it (see traces.py)
        _items[item["id"]] = item
        item["stream"] = f"/media/{item['id']}"
        if item.get("audio"):
            item["audio_stream"] = f"/media/{item['id']}?a=1"
    return post


def main_gui():
    import bottle as btl
    import eel
    import requests

    @btl.route("/media/<item_id>")
    def media(item_id):
        """Proxy a CDN URL with the headers it needs (browser <video> can't set Referer/Cookie).
        Forwards Range so seeking works."""
        item = _items.get(item_id)
        if item is None:
            btl.abort(404)
        audio = bool(btl.request.query.get("a") and item.get("audio"))
        stream = item["audio"] if audio else item
        headers = {"User-Agent": extractors.DEFAULT_UA}
        headers.update(stream.get("headers") or {})
        if btl.request.headers.get("Range"):
            headers["Range"] = btl.request.headers["Range"]
        target = btl.request.query.get("u") or stream["url"]  # HLS segments/variants arrive via ?u=
        if not target.startswith(("http://", "https://")):
            if not target.startswith(("ftp://", "ftps://")):
                btl.abort(400)
            seek = (btl.request.headers.get("Range") or "").split("=", 1)[-1].split("-", 1)[0]
            try:
                offset = max(0, int(seek)) if seek.strip() else 0
            except ValueError:
                offset = 0
            from vidspy_backend import ftp
            try:
                ext, served, size, data = ftp.open_stream(target, offset)
            except (ValueError, OSError) as e:
                btl.abort(502, f"FTP upstream problem: {e}")
            resp = btl.HTTPResponse(status=206 if served else 200)
            resp.set_header("Content-Type", ftp.CONTENT_TYPES.get(ext, "application/octet-stream"))
            resp.set_header("Accept-Ranges", "bytes")
            if served:
                resp.set_header("Content-Range", f"bytes {served}-{size - 1}/{size}")
                resp.set_header("Content-Length", str(size - served))
            elif size is not None:
                resp.set_header("Content-Length", str(size))
            resp.body = data  # chunked: blocks are pulled as the relay sends them
            return resp
        upstream = requests.get(target, headers=headers, stream=True, timeout=30)
        ctype = upstream.headers.get("Content-Type", "")
        if ".m3u8" in target.split("?")[0].lower() or "mpegurl" in ctype.lower():
            body = rewrite_playlist(upstream.text, upstream.url, item_id, audio)
            return btl.HTTPResponse(body, status=upstream.status_code,
                                    headers={"Content-Type": "application/vnd.apple.mpegurl"})
        resp = btl.HTTPResponse(status=upstream.status_code)
        for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
            if h in upstream.headers:
                resp.set_header(h, upstream.headers[h])
        resp.body = upstream.iter_content(chunk_size=64 * 1024)
        return resp

    @eel.expose
    def resolve(text):
        try:
            return {"ok": True, "post": _resolve(text)}
        except Exception as e:
            return {"ok": False, "error": str(e).split("VidspyError: ")[-1]}

    @eel.expose
    def download(item_id):
        try:
            path = backend.download(json.dumps(_items[item_id]), DOWNLOAD_DIR)
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "error": str(e).split("VidspyError: ")[-1]}

    def _call(fn, *args):
        try:
            return {"ok": True, "data": json.loads(fn(*args))}
        except Exception as e:
            return {"ok": False, "error": str(e).split("VidspyError: ")[-1]}

    @eel.expose
    def accounts_list():
        return _call(backend.accounts_list)

    @eel.expose
    def account_connect(platform, text):
        return _call(backend.account_connect, platform, text)

    @eel.expose
    def account_disconnect(platform):
        return _call(backend.account_disconnect, platform)

    @eel.expose
    def trace_report(stage, message, item_id=None):
        """A player error in the browser: recorded like the ones the Android app reports."""
        try:
            item = _items.get(item_id)
            return {"ok": True,
                    "name": backend.trace_report(stage or "playback", message, json.dumps(item) if item else None,
                                                 "", _last_text, item.get("platform") if item else "")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @eel.expose
    def trace_pack():
        """Zip every stored error trace into Downloads/vidspy (the Desktop "Download" button)."""
        try:
            path = backend.trace_pack(DOWNLOAD_DIR)
        except Exception as e:
            return {"ok": False, "error": str(e)}
        if path is None:
            return {"ok": False, "error": "No traces stored yet — they are written only when an action fails."}
        return {"ok": True, "path": path, "count": backend.traces_count()}

    eel.init(os.path.join(HERE, "web"))
    try:
        eel.start("index.html", size=(900, 760))
    except EnvironmentError:  # Chrome/Edge not found -> default browser
        eel.start("index.html", size=(900, 760), mode="default")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--cli":
        print(json.dumps(_resolve(sys.argv[2]), indent=2))
    else:
        main_gui()
