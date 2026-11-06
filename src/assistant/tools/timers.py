"""Timer/reminder service: persisted in SQLite, fired by one background thread."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from ..storage.timers import TimerEntry, TimerStore

log = logging.getLogger(__name__)


class TimerService:
    def __init__(self, store: TimerStore, on_fire: Callable[[TimerEntry], None]):
        self.store = store
        self.on_fire = on_fire
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="timers", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def add(self, kind: str, label: str, due: float) -> TimerEntry:
        entry = self.store.add(kind, label, due)
        self._wake.set()
        return entry

    def cancel(self, timer_id: int) -> bool:
        ok = self.store.cancel(timer_id)
        self._wake.set()
        return ok

    def pending(self) -> list[TimerEntry]:
        return self.store.pending()

    def fire_due(self, now: float | None = None) -> list[TimerEntry]:
        """Fire everything that is due (also used by tests). Missed ones (PC was off) fire late."""
        now = time.time() if now is None else now
        fired = []
        for t in self.store.pending():
            if t.due <= now and self.store.mark_done(t.id):
                fired.append(t)
                try:
                    self.on_fire(t)
                except Exception:
                    log.exception("timer callback failed")
        return fired

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.fire_due()
            pending = self.store.pending()
            delay = min((t.due - time.time() for t in pending), default=3600.0)
            self._wake.wait(max(0.2, min(delay, 60.0)))
            self._wake.clear()
