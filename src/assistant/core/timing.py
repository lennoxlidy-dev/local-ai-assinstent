"""Per-stage latency measurement.

Every interaction gets a :class:`Trace`. Stages are recorded as marks
(points in time) and spans (durations) and written to the debug log in a
stable, grep-able format that ``bench/`` parses::

    TIMING trace=12 span=stt ms=312.4
    TIMING trace=12 summary wake>popup=121.0 speech_end>transcript=310.9 total=1840.2
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from contextlib import contextmanager
from collections.abc import Iterator

log = logging.getLogger("assistant.timing")

_ids = itertools.count(1)


def now_ms() -> float:
    return time.perf_counter() * 1000.0


class Trace:
    def __init__(self, kind: str = "interaction"):
        self.id = next(_ids)
        self.kind = kind
        self._lock = threading.Lock()
        self.t0 = now_ms()
        self.marks: dict[str, float] = {}
        self.spans: dict[str, float] = {}

    def mark(self, name: str) -> float:
        t = now_ms()
        with self._lock:
            self.marks[name] = t
        log.debug("TIMING trace=%d mark=%s t=%.1f", self.id, name, t - self.t0)
        return t

    def between(self, a: str, b: str) -> float | None:
        with self._lock:
            if a in self.marks and b in self.marks:
                return self.marks[b] - self.marks[a]
        return None

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        start = now_ms()
        try:
            yield
        finally:
            ms = now_ms() - start
            with self._lock:
                self.spans[name] = self.spans.get(name, 0.0) + ms
            log.debug("TIMING trace=%d span=%s ms=%.1f", self.id, name, ms)

    def finish(self, pairs: tuple[tuple[str, str], ...] = ()) -> dict[str, float]:
        """Log a one-line summary. ``pairs`` = (from_mark, to_mark) latencies to report."""
        out: dict[str, float] = {}
        for a, b in pairs:
            d = self.between(a, b)
            if d is not None:
                out[f"{a}>{b}"] = d
        with self._lock:
            out.update({f"span:{k}": v for k, v in self.spans.items()})
        out["total"] = now_ms() - self.t0
        log.debug("TIMING trace=%d summary %s", self.id,
                  " ".join(f"{k}={v:.1f}" for k, v in out.items()))
        return out


@contextmanager
def timed(stage: str, logger: logging.Logger = log) -> Iterator[None]:
    """Log how long a block took (for one-off stages outside an interaction, e.g. model load)."""
    start = now_ms()
    try:
        yield
    finally:
        logger.debug("TIMING stage=%s ms=%.1f", stage, now_ms() - start)
