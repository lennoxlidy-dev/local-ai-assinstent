"""Observable runtime state (not persisted): paused, game mode, pipeline phase."""

from __future__ import annotations

import threading
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Any

from .events import EventBus, Topic


class Phase(StrEnum):
    IDLE = "idle"            # only the wake-word detector runs
    LISTENING = "listening"  # recording a request
    THINKING = "thinking"    # STT / intent / LLM
    ACTING = "acting"        # running tools
    SPEAKING = "speaking"    # TTS playback
    DONE = "done"            # answer shown, popup about to fade


@dataclass
class _Snapshot:
    paused: bool = False         # user paused listening (tray)
    game_mode: bool = False      # VRAM freed, wake word may be off
    phase: Phase = Phase.IDLE
    voice_ready: bool = False    # mic + wake word pipeline is running (milestone 4)
    llm_ready: bool = False      # llama.cpp server healthy (milestone 3)


class AppState:
    """Thread-safe state holder that publishes ``state.changed`` with only the changed fields."""

    def __init__(self, bus: EventBus):
        self._bus = bus
        self._lock = threading.Lock()
        self._s = _Snapshot()

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes not found normally -> the snapshot fields.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._s, name)

    def update(self, **changes: Any) -> dict[str, Any]:
        valid = {f.name for f in fields(_Snapshot)}
        unknown = set(changes) - valid
        if unknown:
            raise AttributeError(f"unknown state field(s): {', '.join(sorted(unknown))}")
        with self._lock:
            changed = {k: v for k, v in changes.items() if getattr(self._s, k) != v}
            for k, v in changed.items():
                setattr(self._s, k, v)
        if changed:
            self._bus.publish(Topic.STATE_CHANGED, changed=changed)
        return changed

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {f.name: getattr(self._s, f.name) for f in fields(_Snapshot)}
