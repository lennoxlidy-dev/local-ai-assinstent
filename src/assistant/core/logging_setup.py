"""Logging: a normal log, a verbose debug log (with timings), crash dumps.

Files (in ``%LOCALAPPDATA%\\Assistant\\logs``):

* ``<name>.log``        INFO and above, human readable
* ``<name>-debug.log``  everything incl. ``TIMING`` lines (if enabled)
* ``crash.log``         native crashes (faulthandler) - should stay empty

``<name>`` is ``assistant`` for the tray app and ``cli`` for the debug CLI so
the two never fight over the same rotating file (rotation fails on Windows
while another process has the file open).

Handlers sit behind a QueueHandler/QueueListener so the audio thread never
blocks on disk I/O when it logs.
"""

from __future__ import annotations

import atexit
import faulthandler
import logging
import logging.handlers
import queue
import sys
import threading
from pathlib import Path
from typing import IO

FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(threadName)-12.12s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"

_listener: logging.handlers.QueueListener | None = None
_crash_file: IO[str] | None = None

# Third-party loggers that are chatty at DEBUG.
_QUIET = ("comtypes", "PIL", "urllib3", "httpx", "httpcore", "asyncio")


def setup_logging(logs_dir: Path, *, name: str = "assistant", level: str = "INFO",
                  debug_log: bool = True, console: bool = False,
                  console_level: str = "INFO") -> None:
    """Configure process-wide logging. Safe to call again (reconfigures)."""
    global _listener, _crash_file
    shutdown_logging()
    logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(FORMAT, DATEFMT)

    handlers: list[logging.Handler] = []
    main = logging.handlers.RotatingFileHandler(
        logs_dir / f"{name}.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8", delay=True)
    main.setLevel(_level(level))
    main.setFormatter(fmt)
    handlers.append(main)

    if debug_log:
        dbg = logging.handlers.RotatingFileHandler(
            logs_dir / f"{name}-debug.log", maxBytes=5_000_000, backupCount=2, encoding="utf-8", delay=True)
        dbg.setLevel(logging.DEBUG)
        dbg.setFormatter(fmt)
        handlers.append(dbg)

    if console and sys.stderr is not None:
        con = logging.StreamHandler(sys.stderr)
        con.setLevel(_level(console_level))
        con.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
        handlers.append(con)

    q: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(logging.handlers.QueueHandler(q))
    root.setLevel(logging.DEBUG if debug_log or _level(console_level) == logging.DEBUG else _level(level))
    for noisy in _QUIET:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _listener = logging.handlers.QueueListener(q, *handlers, respect_handler_level=True)
    _listener.start()
    atexit.register(shutdown_logging)

    _install_excepthooks()
    try:
        if _crash_file is None:
            _crash_file = open(logs_dir / "crash.log", "a", encoding="utf-8")
        faulthandler.enable(file=_crash_file, all_threads=True)
    except (OSError, RuntimeError):  # pragma: no cover
        pass


def shutdown_logging() -> None:
    global _listener
    if _listener is not None:
        try:
            _listener.stop()
        except Exception:  # pragma: no cover
            pass
        for h in _listener.handlers:
            try:
                h.close()
            except Exception:  # pragma: no cover
                pass
        _listener = None


def _level(name: str) -> int:
    return logging.getLevelNamesMapping().get(str(name).upper(), logging.INFO)


def _install_excepthooks() -> None:
    """Uncaught exceptions must reach the log: under pythonw there is no console at all."""
    log = logging.getLogger("assistant.crash")

    def hook(exc_type, exc, tb):  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        log.critical("uncaught exception in thread %s",
                     args.thread.name if args.thread else "?",
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))  # type: ignore[arg-type]

    sys.excepthook = hook
    threading.excepthook = thread_hook


def redact(text: str, allowed: bool) -> str:
    """What may go into logs: the text itself only if the user enabled transcript logging."""
    return text if allowed else f"<{len(text)} chars>"
