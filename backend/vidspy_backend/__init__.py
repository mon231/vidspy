"""vidspy backend: shared by the Android app (Chaquopy) and the desktop app (Eel)."""
from .api import VidspyError, detect_platform, download, extract_url, resolve

__all__ = ["VidspyError", "detect_platform", "download", "extract_url", "resolve"]
