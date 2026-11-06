"""COM / WinRT threading helpers.

* Classic COM (pycaw, pyvda, uiautomation, shell) wants the calling thread to
  be initialized; :func:`ensure_com` does that once per thread (STA).
* WinRT async APIs (media sessions, OCR) run on one dedicated MTA thread with
  its own asyncio loop (:class:`WinRTWorker`), so they never collide with the
  STA threads.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any, TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")
_tls = threading.local()


def ensure_com() -> None:
    if getattr(_tls, "com", False):
        return
    try:
        import pythoncom

        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    except Exception as e:  # already initialized in another mode: fine for our use
        log.debug("CoInitializeEx: %s", e)
    _tls.com = True


class WinRTWorker:
    _instance: WinRTWorker | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="winrt", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    @classmethod
    def get(cls) -> WinRTWorker:
        with cls._lock:
            if cls._instance is None:
                cls._instance = WinRTWorker()
            return cls._instance

    def _run(self) -> None:
        try:
            from winrt.runtime import ApartmentType, init_apartment

            init_apartment(ApartmentType.MULTI_THREADED)
        except Exception as e:
            log.debug("winrt init_apartment: %s", e)
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def run(self, factory: Callable[[], Coroutine[Any, Any, T]], timeout: float = 10.0) -> T:
        fut: Future[T] = asyncio.run_coroutine_threadsafe(factory(), self._loop)
        return fut.result(timeout)
