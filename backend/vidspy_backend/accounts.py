"""Linked accounts: per-platform login cookies, stored once here for every frontend.

Frontends (Android / desktop) only call `list_accounts`, `connect`, `disconnect`.
Cookies are the credential: password logins are blocked or short-lived on these platforms,
while a browser session cookie works with yt-dlp, gallery-dl and plain `requests`.

Storage: one Netscape-format file per platform in the directory given to `configure()`
(app-private storage on Android, ~/.vidspy/accounts on desktop), mode 0600. Never logged.
"""
import json
import os
import re
import time

from . import platforms
from .errors import VidspyError

_dir = None
_YEAR = 365 * 24 * 3600

PRIVACY = "Stored only on this device, never uploaded. Treat it like a password."
STEPS = (
    "Open the {label} login page below and sign in with your account.",
    "On a computer, press F12 in that browser tab, open the Network tab and reload the page. Click any "
    "request to {label} and copy the value of its 'Cookie' request header.",
    "Paste it below and press Save.",
)


def configure(data_dir):
    global _dir
    _dir = data_dir
    os.makedirs(_dir, exist_ok=True)


def _base():
    d = _dir or os.path.join(os.path.expanduser("~"), ".vidspy", "accounts")
    os.makedirs(d, exist_ok=True)
    return d


def _path(platform):
    return os.path.join(_base(), f"{platform}.cookies.txt")


def cookie_file(platform):
    p = _path(platform)
    return p if os.path.exists(p) else None


def cookie_file_for_url(url):
    name = platforms.detect_platform(url)
    return cookie_file(name) if name in platforms.PLATFORMS else None


def _clean(value):
    return re.sub(r"[\t\r\n]", "", str(value)).strip()


def _from_header(text, platform):
    text = re.sub(r"^\s*cookie\s*:\s*", "", text.strip(), flags=re.I)
    cookies = []
    for part in text.split(";"):
        if "=" in part:
            name, value = part.split("=", 1)
            if _clean(name):
                for domain in platforms.PLATFORMS[platform]["cookie_domains"]:
                    cookies.append((domain, "/", True, 0, _clean(name), _clean(value)))
    return cookies


def _from_netscape(text):
    cookies = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        if not line or line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) == 7:
            cookies.append((f[0], f[2], f[3].upper() == "TRUE", int(float(f[4] or 0)), f[5], f[6]))
    return cookies


def _from_json(data, platform):
    if isinstance(data, dict) and "cookies" in data:
        data = data["cookies"]
    if isinstance(data, dict):  # {"name": "value"} map
        return _from_header("; ".join(f"{k}={v}" for k, v in data.items()), platform)
    cookies = []
    for c in data:
        name, value, domain = c.get("name"), c.get("value"), c.get("domain")
        if not name or value is None or not domain:
            continue
        expiry = c.get("expirationDate") or c.get("expires") or 0
        cookies.append((domain, c.get("path") or "/", bool(c.get("secure")),
                        int(expiry) if isinstance(expiry, (int, float)) and expiry > 0 else 0,
                        name, str(value)))
    return cookies


def parse_cookies(text, platform):
    """Accept Netscape cookies.txt, a JSON export, or a raw `Cookie:` header. Returns tuples
    (domain, path, secure, expiry, name, value) restricted to the platform's own domains."""
    text = (text or "").strip()
    if not text:
        raise VidspyError("Nothing to save: paste your cookies first.")
    try:
        if text[0] in "[{":
            cookies = _from_json(json.loads(text), platform)
        elif "\t" in text:
            cookies = _from_netscape(text)
        else:
            cookies = _from_header(text, platform)
    except (ValueError, TypeError, AttributeError):
        raise VidspyError("Could not read these cookies. Paste the value of the Cookie request header.")

    info = platforms.PLATFORMS[platform]
    wanted = {d.lstrip(".").lower() for d in info["domains"] + info["cookie_domains"]}
    kept = []
    for domain, path, secure, expiry, name, value in cookies:
        host = domain.lstrip(".").lower()
        if any(host == w or host.endswith("." + w) for w in wanted):
            kept.append((domain, path, secure, expiry, _clean(name), _clean(value)))
    if not kept:
        raise VidspyError(f"No {info['label']} cookies found in what you pasted.")
    return kept


def _write(platform, cookies):
    far = int(time.time()) + 2 * _YEAR  # session cookies (expiry 0) would be dropped by cookie jars
    lines = ["# Netscape HTTP Cookie File"]
    for domain, path, secure, expiry, name, value in cookies:
        lines.append("\t".join([domain, "TRUE" if domain.startswith(".") else "FALSE", path,
                                "TRUE" if secure else "FALSE", str(expiry or far), name, value]))
    target = _path(platform)
    tmp = target + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


def _status(platform):
    info = platforms.PLATFORMS[platform]
    names = []
    path = cookie_file(platform)
    if path:
        names = [l.split("\t")[5] for l in open(path, encoding="utf-8").read().splitlines()
                 if l and not l.startswith("#") and l.count("\t") == 6]
    return {
        "platform": platform,
        "label": info["label"],
        "connected": path is not None,
        "count": len(names),
        "key_cookies": list(info["key_cookies"]),
        "login_url": info["login_url"],
        "color": info["color"],
        "icon": info["icon"],  # 24x24 SVG path (white glyph on `color` badge)
        "steps": [t.format(label=info["label"]) for t in STEPS],
        "privacy": PRIVACY,
    }


def _check(platform):
    if platform not in platforms.PLATFORMS:
        raise VidspyError(f"Unknown platform: {platform}")


def list_accounts():
    return [_status(p) for p in platforms.PLATFORMS]


def connect(platform, text):
    _check(platform)
    cookies = parse_cookies(text, platform)
    info = platforms.PLATFORMS[platform]
    if not any(c[4] in info["key_cookies"] for c in cookies):
        raise VidspyError(
            f"Not saved: these cookies don't include a {info['label']} login "
            f"({' or '.join(info['key_cookies'])}). Sign in to {info['label']} first, "
            "then copy the Cookie header again.")
    _write(platform, cookies)
    return _status(platform)


def disconnect(platform):
    _check(platform)
    try:
        os.remove(_path(platform))
    except FileNotFoundError:
        pass
    return _status(platform)
