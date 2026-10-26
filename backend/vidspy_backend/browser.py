"""HTTP session that presents itself like desktop Chrome (stdlib `ssl` only, so it also runs on Android).

Some sites (Reddit) reject clients whose TLS handshake looks like a script's: the default cipher
order of Python's ssl differs from a browser's. Here the cipher order and ALPN follow Chrome's.
"""
import ssl

import requests
from requests.adapters import HTTPAdapter

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
CHROME_HEADERS = {
    "User-Agent": CHROME_UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
CHROME_CIPHERS = ":".join([
    "TLS_AES_128_GCM_SHA256", "TLS_AES_256_GCM_SHA384", "TLS_CHACHA20_POLY1305_SHA256",
    "ECDHE-ECDSA-AES128-GCM-SHA256", "ECDHE-RSA-AES128-GCM-SHA256",
    "ECDHE-ECDSA-AES256-GCM-SHA384", "ECDHE-RSA-AES256-GCM-SHA384",
    "ECDHE-ECDSA-CHACHA20-POLY1305", "ECDHE-RSA-CHACHA20-POLY1305",
    "ECDHE-RSA-AES128-SHA", "ECDHE-RSA-AES256-SHA",
    "AES128-GCM-SHA256", "AES256-GCM-SHA384", "AES128-SHA", "AES256-SHA",
])


class _ChromeAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        try:
            ctx.set_ciphers(CHROME_CIPHERS)
        except ssl.SSLError:  # TLS1.3 suites are not configurable on some builds; keep the rest
            pass
        ctx.set_alpn_protocols(["http/1.1"])
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def chrome_session(cookie_file=None):
    session = requests.Session()
    session.mount("https://", _ChromeAdapter())
    session.headers.update(CHROME_HEADERS)
    if cookie_file:
        from http.cookiejar import MozillaCookieJar
        jar = MozillaCookieJar()
        jar.load(cookie_file, ignore_discard=True, ignore_expires=True)
        session.cookies = jar
    return session
