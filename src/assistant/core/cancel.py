"""Cooperative cancellation for the panic hotkey / "<name>, stop".

Long-running work (agent loop, tool calls, typing, TTS) receives a
:class:`CancelToken` and checks it between steps. The panic action cancels
the root token, which cancels every child, and runs registered cleanup
callbacks (e.g. "release all held keys and mouse buttons").
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


class Cancelled(Exception):
    """Raised by :meth:`CancelToken.check` when the work was cancelled."""


class CancelToken:
    def __init__(self, parent: CancelToken | None = None):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._children: list[CancelToken] = []
        self._callbacks: list[Callable[[], None]] = []
        self.reason = ""
        if parent is not None:
            parent._add_child(self)

    def _add_child(self, child: CancelToken) -> None:
        with self._lock:
            if self._event.is_set():
                cancel_now = True
            else:
                self._children.append(child)
                cancel_now = False
        if cancel_now:
            child.cancel(self.reason)

    def child(self) -> CancelToken:
        return CancelToken(self)

    def on_cancel(self, fn: Callable[[], None]) -> None:
        """Run ``fn`` when cancelled (immediately if already cancelled)."""
        with self._lock:
            if not self._event.is_set():
                self._callbacks.append(fn)
                return
        fn()

    def cancel(self, reason: str = "cancelled") -> None:
        with self._lock:
            if self._event.is_set():
                return
            self.reason = reason
            self._event.set()
            children, self._children = self._children, []
            callbacks, self._callbacks = self._callbacks, []
        for c in children:
            c.cancel(reason)
        for fn in callbacks:
            try:
                fn()
            except Exception:
                log.exception("cancel callback failed")

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled(self.reason)

    def wait(self, timeout: float) -> bool:
        """Sleep up to ``timeout`` seconds; returns True early if cancelled."""
        return self._event.wait(timeout)
