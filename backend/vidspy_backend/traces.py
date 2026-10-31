"""Error traces, for backwards debugging: a report file is written only when something fails.

A report holds everything that is needed afterwards to understand why the error happened and
how to get around it: the extractor chain with per-step errors and tracebacks, yt-dlp /
gallery-dl debug logs (captured during resolve, saved only when the resolve fails), the
resolved media item with its CDN headers, linked-account status and version numbers.

Frontends never use this module directly, only through `api.py`:

- `resolve()` / `download()` trace themselves: a report appears automatically on failure and
  nothing is written on success.
- `playback_report()` records errors that happen outside Python (the players of both apps
  call `api.trace_report`).
- `info()` / `pack()` / `last_name()` back the "download traces" buttons in both apps.

Reports stay in app-private storage (`<data>/traces` on Android, `~/.vidspy/traces` on
desktop) and leave the device only when the user packs and shares them. Secrets are redacted
when the report is written: cookie and token header values are dropped, but cookie *names*
are kept, because which cookies were attached (or missing) is exactly what a 403 question
is about.
"""
import collections
import json
import logging
import os
import re
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from ._version import __version__

KEEP = 50          # newest reports kept; older ones are deleted after a new one is written
LOG_LINES = 400    # framework debug lines (yt-dlp / gallery-dl) kept per trace

_AUTH_KEYS = {"cookie", "set-cookie", "authorization", "proxy-authorization", "x-api-key"}
_COOKIE_IN_TEXT = re.compile(
    r"(?i)\b(cookie|set-cookie|authorization|proxy-authorization)\b(\s*[?:]\s*)([^\r\n\"]+)")

_dir = None
_tls = threading.local()
_last = None  # name of the report written most recently


def configure(data_dir):
    global _dir
    _dir = os.path.join(data_dir, "traces")


def dir_path():
    return _dir or os.path.join(os.path.expanduser("~"), ".vidspy", "traces")


def _redact(value):
    """Drop secret header values, keep cookie names and everything else."""
    if isinstance(value, dict):
        return {k: _redact_header(k.lower(), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def _redact_header(key, value):
    if key not in _AUTH_KEYS:
        return _redact(value)
    if isinstance(value, str) and key in ("cookie", "set-cookie"):
        names = re.findall(r"(?:^|;\s*)([A-Za-z0-9_\-.]+)\s*=", value)
        if names:
            return f"<redacted (names kept): {'; '.join(names)}>"
    return "<redacted>"


def _scrub(text):
    """The same redaction for values that surface inside the frameworks' own debug logs."""

    def repl(m):
        keep = "; ".join(f"{n}=<redacted>" for n in re.findall(r"([A-Za-z0-9_\-.]+)=", m.group(3)))
        return f"{m.group(1)}{m.group(2)}{keep or '<redacted>'}"

    return _COOKIE_IN_TEXT.sub(repl, str(text))


def _fmt_log(msg, args):
    if not args:
        return str(msg)
    try:
        return str(msg) % args
    except (TypeError, ValueError, KeyError):
        return " ".join(map(str, (msg, *args)))


class Probe(logging.Handler):
    """Captures an extractor's debug lines into the active trace. Silent when none runs.

    Given to yt-dlp as its `logger` option (it calls debug/warning/error), and attached to
    gallery-dl's logger with the context manager protocol.
    """

    def __init__(self, target):
        super().__init__(level=logging.DEBUG)
        self.target = target

    # as a logging.Handler (gallery-dl)
    def emit(self, record):
        self._store(record.getMessage())

    # as a plain logger (yt-dlp)
    def debug(self, msg, *args, **kwargs): self._store(_fmt_log(msg, args))
    def info(self, msg, *args, **kwargs): self._store(_fmt_log(msg, args))
    def warning(self, msg, *args, **kwargs): self._store(f"WARNING: {_fmt_log(msg, args)}")
    def error(self, msg, *args, **kwargs): self._store(f"ERROR: {_fmt_log(msg, args)}")

    def _store(self, line):
        trace = getattr(_tls, "trace", None)
        if trace is not None:
            trace.logs[self.target].append(line)

    def __enter__(self):
        logging.getLogger("gallery-dl").addHandler(self)
        return self

    def __exit__(self, *exc):
        logging.getLogger("gallery-dl").removeHandler(self)
        return False


class Step:
    """One stage of a traced operation (usually one extractor attempt)."""

    def __init__(self, trace, name, detail):
        self.trace, self.name = trace, name
        self.detail = {k: v for k, v in detail.items() if v not in (None, "")}
        self.started = time.monotonic()
        self.seconds = None
        self.outcome = {"ok": None}

    def finish(self):
        self.seconds = round(time.monotonic() - self.started, 2)

    def done(self, **outcome):
        self.outcome = {"ok": True, **outcome}

    def fail(self, exc):
        self.outcome = {"ok": False, "type": type(exc).__name__,
                        "message": _scrub(str(exc)), "traceback": _scrub(traceback.format_exc())}

    def record(self):
        rec = {"step": self.name, "seconds": self.seconds, **self.detail, **self.outcome}
        if self.outcome.get("ok") is False:  # a long traceback: the cause is in its last lines
            lines = rec["traceback"].splitlines()
            if len(lines) > 10:
                rec["traceback"] = "  ...\n" + "\n".join(lines[-10:])
        return rec


class Trace:
    def __init__(self, stage, **context):
        self.stage = stage
        self.context = {k: v for k, v in context.items() if v not in (None, "")}
        self.steps = []
        self.notes = []
        self.logs = {t: collections.deque(maxlen=LOG_LINES) for t in ("ytdlp", "gallerydl")}
        self.started = time.monotonic()

    def new_step(self, name, **detail):
        s = Step(self, name, detail)
        self.steps.append(s)
        return s

    def note(self, **detail):
        self.notes.append(detail)

    def _versions(self):
        import platform as host

        info = {"vidspy": __version__,
                "python": ".".join(map(str, __import__("sys").version_info[:3])),
                "os": host.platform()}
        for name in ("yt_dlp", "gallery_dl"):
            try:
                info[name] = __import__(f"{name}.version", fromlist=["version"]).__version__
            except Exception:  # noqa: BLE001 - bundled tool missing, still fine
                info[name] = "not installed"
        return info

    def _linked(self):
        """Whether a linked account would have been used (its cookie names, not values)."""
        from . import accounts, platforms

        platform = self.context.get("platform") or platforms.detect_platform(self.context.get("url") or "")
        if platform not in platforms.PLATFORMS:
            return {}
        path = accounts.cookie_file(platform)
        if not path:
            return {platform: "not connected"}
        names = sorted({line.split("\t")[5] for line in open(path, encoding="utf-8").read().splitlines()
                        if line and not line.startswith("#") and line.count("\t") == 6})
        return {platform: "connected: " + ", ".join(names)}

    def report(self, error=None, item=None):
        out = {
            "vidspy_trace": 1,
            "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "stage": self.stage,
            "duration_s": round(time.monotonic() - self.started, 1),
            "app": self._versions(),
            "linked_account": self._linked(),
            "input": _redact(self.context),
        }
        if self.notes:
            out["notes"] = self.notes
        if self.steps:
            out["steps"] = [s.record() for s in self.steps]
        logs = {t: [_scrub(l) for l in buf] for t, buf in self.logs.items() if buf}
        if logs:
            out["logs"] = logs
        if item is not None:
            out["item"] = _redact(item)
        if error:
            out["error"] = error
        return out

    def _write(self, report):
        global _last
        base = dir_path()
        os.makedirs(base, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^A-Za-z0-9]+", "_", self.context.get("platform") or self.stage).strip("_") or "app"
        path = os.path.join(base, f"{stamp}_{self.stage}_{slug}_{uuid.uuid4().hex[:6]}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        _last = os.path.basename(path)
        for old in [n for n in sorted(os.listdir(base)) if n.endswith(".json")][:-KEEP]:
            try:
                os.remove(os.path.join(base, old))
            except OSError:
                pass
        return path


def begin(stage, **context):
    """Trace the current thread's next operation; it ends silently on success."""
    trace = Trace(stage, **context)
    _tls.trace = trace
    return trace


def end():
    _tls.trace = None


def current():
    return getattr(_tls, "trace", None)


def _error(exc):
    return {"type": type(exc).__name__, "message": _scrub(str(exc)),
            "traceback": _scrub(traceback.format_exc())}


def fail(trace, exc=None, error=None, item=None):
    """Write the report for the finished trace and stop tracing."""
    end()
    return trace._write(trace.report(error=error or ({} if exc is None else _error(exc)), item=item))


@contextmanager
def step(name, **detail):
    """Stage of the active trace, else a no-op (e.g. an extractor called directly)."""
    trace = current()
    if trace is None:
        yield None
        return
    s = trace.new_step(name, **detail)
    try:
        yield s
    except Exception as e:
        s.fail(e)
        raise
    else:
        s.done()
    finally:
        s.finish()


def note(**detail):
    trace = current()
    if trace is not None:
        trace.note(**detail)


def files():
    try:
        return [n for n in sorted(os.listdir(dir_path())) if n.endswith(".json")]
    except OSError:
        return []


def last_name():
    return _last or ""


def info():
    return {"dir": dir_path(), "count": len(files()), "files": files()}


def playback_report(message, item=None, detail="", text="", platform=""):
    """Record an error that happened outside Python (the players of both apps)."""
    trace = Trace("playback", text=text, platform=platform)
    error = {"type": "PlaybackError", "message": _scrub(message)}
    if detail:
        error["detail"] = _scrub(detail)
    return os.path.basename(fail(trace, error=error, item=item) or "")


def pack(dest_dir):
    """Zip every stored report for the apps' "download traces" buttons; None if there are none."""
    if not files():
        return None
    import zipfile

    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"vidspy-traces-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.zip")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for name in files():
            z.write(os.path.join(dir_path(), name), arcname=name)
    return dest
