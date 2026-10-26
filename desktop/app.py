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

backend.configure(os.path.join(os.path.expanduser("~"), ".vidspy", "accounts"))
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "vidspy")
_items = {}  # id -> item dict; lets the browser stream via /media/<id> with the right headers


def rewrite_playlist(text, base_url, item_id):
    """Point every URI of an HLS playlist back at /media/<id>?u=<abs url> so segments get the item's headers."""
    def proxied(uri):
        return f"/media/{item_id}?u=" + quote(urljoin(base_url, uri), safe="")

    out = []
    for line in text.splitlines():
        if line.strip() and not line.startswith("#"):
            line = proxied(line.strip())
        else:
            line = re.sub(r'URI="([^"]+)"', lambda m: f'URI="{proxied(m.group(1))}"', line)
        out.append(line)
    return "\n".join(out) + "\n"


def _resolve(text):
    post = json.loads(backend.resolve(text))
    for item in post["items"]:
        item["id"] = uuid.uuid4().hex
        _items[item["id"]] = item
        item["stream"] = f"/media/{item['id']}"
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
        headers = {"User-Agent": extractors.DEFAULT_UA}
        headers.update(item.get("headers") or {})
        if btl.request.headers.get("Range"):
            headers["Range"] = btl.request.headers["Range"]
        target = btl.request.query.get("u") or item["url"]  # HLS segments/variants arrive via ?u=
        if not target.startswith(("http://", "https://")):
            btl.abort(400)
        upstream = requests.get(target, headers=headers, stream=True, timeout=30)
        ctype = upstream.headers.get("Content-Type", "")
        if ".m3u8" in target.split("?")[0].lower() or "mpegurl" in ctype.lower():
            body = rewrite_playlist(upstream.text, upstream.url, item_id)
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

    # Linked accounts: all logic lives in vidspy_backend.accounts; these just forward.
    @eel.expose
    def accounts_list():
        return _call(backend.accounts_list)

    @eel.expose
    def account_connect(platform, text):
        return _call(backend.account_connect, platform, text)

    @eel.expose
    def account_disconnect(platform):
        return _call(backend.account_disconnect, platform)

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
