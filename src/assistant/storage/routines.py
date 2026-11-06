"""Named multi-step macros ("gaming mode" = open Steam + Discord, volume 40%)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from .db import Database


@dataclass
class Routine:
    name: str
    steps: list[dict[str, Any]]          # [{"tool": "open_app", "args": {"name": "Steam"}}, ...]
    description: str = ""
    id: int | None = None
    updated: float = field(default_factory=time.time)


class RoutineStore:
    def __init__(self, db: Database):
        self.db = db

    def save(self, routine: Routine) -> Routine:
        now = time.time()
        steps = json.dumps(routine.steps, ensure_ascii=False)
        self.db.execute(
            "INSERT INTO routines(name, description, steps, created, updated) VALUES (?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET description=excluded.description, steps=excluded.steps, "
            "updated=excluded.updated",
            (routine.name.strip(), routine.description, steps, now, now))
        return self.get(routine.name)  # type: ignore[return-value]

    def get(self, name: str) -> Routine | None:
        rows = self.db.query("SELECT * FROM routines WHERE name = ? COLLATE NOCASE", (name.strip(),))
        return self._row(rows[0]) if rows else None

    def all(self) -> list[Routine]:
        return [self._row(r) for r in self.db.query("SELECT * FROM routines ORDER BY name COLLATE NOCASE")]

    def delete(self, name: str) -> bool:
        return self.db.execute("DELETE FROM routines WHERE name = ? COLLATE NOCASE", (name.strip(),)).rowcount > 0

    @staticmethod
    def _row(r: Any) -> Routine:
        return Routine(name=r["name"], steps=json.loads(r["steps"]), description=r["description"],
                       id=r["id"], updated=r["updated"])
