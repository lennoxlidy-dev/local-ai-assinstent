"""Persisted timers and reminders (survive a restart)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .db import Database


@dataclass
class TimerEntry:
    id: int
    kind: str      # "timer" | "reminder"
    label: str
    due: float     # epoch seconds
    created: float

    @property
    def remaining_s(self) -> float:
        return self.due - time.time()


class TimerStore:
    def __init__(self, db: Database):
        self.db = db

    def add(self, kind: str, label: str, due: float) -> TimerEntry:
        now = time.time()
        cur = self.db.execute("INSERT INTO timers(kind, label, due, created) VALUES (?,?,?,?)",
                              (kind, label, due, now))
        return TimerEntry(int(cur.lastrowid or 0), kind, label, due, now)

    def pending(self) -> list[TimerEntry]:
        return [TimerEntry(r["id"], r["kind"], r["label"], r["due"], r["created"])
                for r in self.db.query("SELECT * FROM timers WHERE done = 0 ORDER BY due")]

    def mark_done(self, timer_id: int) -> bool:
        return self.db.execute("UPDATE timers SET done = 1 WHERE id = ? AND done = 0", (timer_id,)).rowcount > 0

    def cancel(self, timer_id: int) -> bool:
        return self.mark_done(timer_id)
