"""vidspy backend: shared by the Android app (Chaquopy) and the desktop app (Eel)."""
from ._version import __version__
from .api import (VidspyError, account_connect, account_disconnect, accounts_list, configure,
                  detect_platform, download, extract_url, resolve, suggestions)

__all__ = ["__version__", "VidspyError", "account_connect", "account_disconnect", "accounts_list", "configure",
           "detect_platform", "download", "extract_url", "resolve", "suggestions"]
