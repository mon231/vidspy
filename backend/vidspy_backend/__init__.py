"""vidspy backend: shared by the Android app (Chaquopy) and the desktop app (Eel)."""
from .api import (VidspyError, account_connect, account_disconnect, accounts_list, configure,
                  detect_platform, download, extract_url, resolve)

__all__ = ["VidspyError", "account_connect", "account_disconnect", "accounts_list", "configure",
           "detect_platform", "download", "extract_url", "resolve"]
