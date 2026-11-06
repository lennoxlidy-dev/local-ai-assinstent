"""Fallback file search when Everything isn't installed: a low-priority background filename index.

* SQLite FTS5 with the trigram tokenizer (substring search) in ``cache/files.db``
* the indexer thread runs at background priority (Windows: THREAD_MODE_BACKGROUND_BEGIN
  lowers CPU *and* I/O priority) and rescans every few hours
* skips hidden/system folders, dev junk, and blocked folders
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from ..core.cancel import CancelToken

log = logging.getLogger(__name__)

SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".svn", ".hg", ".venv", "venv", "$recycle.bin",
             "system volume information", "appdata", ".cache", ".gradle", ".m2", ".nuget", "site-packages",
             "windowsapps", "temp", "tmp"}
MAX_FILES = 600_000


class FileIndex:
    name = "file index"

    def __init__(self, db_path: Path, roots: Callable[[], Iterable[str]],
                 allowed: Callable[[str], bool] = lambda p: True, rescan_hours: float = 6.0):
        self.db_path = Path(db_path)
        self.roots = roots
        self.allowed = allowed
        self.rescan_s = rescan_hours * 3600
        self._lock = threading.Lock()
        self._conn = self._open()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_scan = 0.0
        self.count = self._count()

    def _open(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
        try:
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS files USING fts5(name, path UNINDEXED, "
                         "tokenize='trigram')")
            self.trigram = True
        except sqlite3.OperationalError:  # very old SQLite: fall back to LIKE
            conn.execute("CREATE TABLE IF NOT EXISTS files(name TEXT, path TEXT)")
            self.trigram = False
        row = conn.execute("SELECT v FROM meta WHERE k='last_scan'").fetchone()
        self.last_scan = float(row[0]) if row else 0.0
        return conn

    def _count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT count(*) FROM files").fetchone()[0])

    # ------------------------------------------------------------------ search
    def search(self, query: str, limit: int, folder: str | None, cancel: CancelToken) -> list[str]:
        q = query.strip()
        if not q:
            return []
        with self._lock:
            if self.trigram and len(q) >= 3:
                rows = self._conn.execute(
                    "SELECT path FROM files WHERE name MATCH ? ORDER BY rank LIMIT ?",
                    ('"' + q.replace('"', '""') + '"', limit * 5)).fetchall()
            else:
                rows = self._conn.execute("SELECT path FROM files WHERE name LIKE ? LIMIT ?",
                                          (f"%{q}%", limit * 5)).fetchall()
        paths = [r[0] for r in rows]
        if folder:
            f = folder.rstrip("\\/").casefold()
            paths = [p for p in paths if p.casefold().startswith(f + os.sep.casefold()) or p.casefold() == f]
        # exact / prefix name matches first, then shorter paths
        ql = q.casefold()
        paths.sort(key=lambda p: (Path(p).stem.casefold() != ql, not Path(p).name.casefold().startswith(ql),
                                  len(p)))
        return [p for p in paths if os.path.exists(p)][:limit]

    # ------------------------------------------------------------------ indexing
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="file-index", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        _background_priority(True)
        while not self._stop.is_set():
            if time.time() - self.last_scan > self.rescan_s:
                try:
                    self.rebuild()
                except Exception:
                    log.exception("file index rebuild failed")
            self._stop.wait(300)

    def rebuild(self) -> int:
        t0 = time.monotonic()
        batch: list[tuple[str, str]] = []
        n = 0
        with self._lock:
            self._conn.execute("BEGIN")
            self._conn.execute("DELETE FROM files")
        try:
            for root in self.roots():
                for path in self._walk(root):
                    batch.append((os.path.basename(path), path))
                    n += 1
                    if len(batch) >= 2000:
                        with self._lock:
                            self._conn.executemany("INSERT INTO files(name, path) VALUES (?,?)", batch)
                        batch.clear()
                        time.sleep(0.01)  # stay polite
                    if n >= MAX_FILES or self._stop.is_set():
                        break
            with self._lock:
                if batch:
                    self._conn.executemany("INSERT INTO files(name, path) VALUES (?,?)", batch)
                self.last_scan = time.time()
                self._conn.execute("INSERT OR REPLACE INTO meta VALUES ('last_scan', ?)", (str(self.last_scan),))
                self._conn.execute("COMMIT")
        except BaseException:
            with self._lock:
                self._conn.execute("ROLLBACK")
            raise
        self.count = n
        log.info("file index: %d entries in %.1fs", n, time.monotonic() - t0)
        return n

    def _walk(self, root: str) -> Iterable[str]:
        stack = [root]
        while stack and not self._stop.is_set():
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    for e in it:
                        name = e.name
                        if name.startswith((".", "$", "~$")):
                            continue
                        try:
                            is_dir = e.is_dir(follow_symlinks=False)
                        except OSError:
                            continue
                        if is_dir:
                            if name.casefold() in SKIP_DIRS or not self.allowed(e.path):
                                continue
                            stack.append(e.path)
                        yield e.path
            except OSError:
                continue


def _background_priority(on: bool) -> None:
    """Windows: background mode (low CPU + I/O + memory priority) for the current thread."""
    if os.name != "nt":
        return
    try:
        import ctypes

        THREAD_MODE_BACKGROUND_BEGIN, THREAD_MODE_BACKGROUND_END = 0x00010000, 0x00020000
        k32 = ctypes.windll.kernel32
        k32.GetCurrentThread.restype = ctypes.c_void_p
        k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        k32.SetThreadPriority(k32.GetCurrentThread(),
                              THREAD_MODE_BACKGROUND_BEGIN if on else THREAD_MODE_BACKGROUND_END)
    except Exception as e:  # pragma: no cover
        log.debug("could not lower thread priority: %s", e)
