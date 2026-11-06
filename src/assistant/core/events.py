"""A tiny thread-safe publish/subscribe bus.

The core (audio, STT, agent, tools) runs on worker threads and must not
depend on Qt. Components publish events here; the UI layer subscribes and
re-emits them as Qt signals (queued onto the GUI thread).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

log = logging.getLogger(__name__)


class Topic:
    """Well-known event topics (string constants, so plugins can add their own)."""

    CONFIG_CHANGED = "config.changed"   # keys: list[str]
    STATE_CHANGED = "state.changed"     # changed: dict[str, Any]
    NOTIFY = "ui.notify"                # title, message, level
    SHUTDOWN = "app.shutdown"


@dataclass(frozen=True, slots=True)
class Event:
    topic: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to ``topic`` (``"*"`` = everything). Returns an unsubscribe function."""
        with self._lock:
            self._subs[topic].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._subs.get(topic, []):
                    self._subs[topic].remove(handler)

        return unsubscribe

    def publish(self, topic: str, **data: Any) -> Event:
        """Deliver synchronously, in the publisher's thread. Handler errors are logged, never raised."""
        event = Event(topic, data)
        with self._lock:
            handlers = list(self._subs.get(topic, ())) + list(self._subs.get("*", ()))
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                log.exception("event handler failed for %s", topic)
        return event
