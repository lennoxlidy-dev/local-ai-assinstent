"""Full action log: every tool call, including blocked/denied ones, with decision and outcome.

A row is written *before* the action runs (decision) and updated afterwards
(outcome), so even a crash mid-action leaves a trace.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from .db import Database

MAX_VALUE_CHARS = 2000


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS] + f"… [{len(value) - MAX_VALUE_CHARS} more chars]"
    if isinstance(value, dict):
        return {k: _truncate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v) for v in value[:100]]
    return value


@dataclass
class LogEntry:
    id: int
    ts: float
    task_id: str | None
    source: str | None
    tool: str
    capability: str | None
    summary: str | None
    args: dict[str, Any]
    risks: list[str]
    decision: str
    outcome: str | None
    result: str | None
    duration_ms: float | None


class ActionLog:
    def __init__(self, db: Database):
        self.db = db

    def begin(self, *, tool: str, args: dict[str, Any], decision: str, task_id: str | None = None,
              source: str | None = None, capability: str | None = None, summary: str | None = None,
              risks: list[str] | None = None, result: str | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO action_log(ts, task_id, source, tool, capability, summary, args, risks, decision, result)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (time.time(), task_id, source, tool, capability, summary,
             json.dumps(_truncate(args), ensure_ascii=False, default=str),
             ",".join(sorted(risks or [])), decision, result))
        return int(cur.lastrowid or 0)

    def finish(self, entry_id: int, *, outcome: str, result: str = "", duration_ms: float | None = None) -> None:
        self.db.execute("UPDATE action_log SET outcome=?, result=?, duration_ms=? WHERE id=?",
                        (outcome, result[:MAX_VALUE_CHARS], duration_ms, entry_id))

    def recent(self, limit: int = 50, *, tool: str | None = None, search: str | None = None) -> list[LogEntry]:
        sql, params = "SELECT * FROM action_log", []
        where = []
        if tool:
            where.append("tool = ?")
            params.append(tool)
        if search:
            where.append("(summary LIKE ? OR args LIKE ? OR result LIKE ?)")
            params += [f"%{search}%"] * 3
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [self._row(r) for r in self.db.query(sql, params)]

    def purge_older_than(self, days: int) -> int:
        if days <= 0:
            return 0
        cur = self.db.execute("DELETE FROM action_log WHERE ts < ?", (time.time() - days * 86400,))
        return cur.rowcount

    @staticmethod
    def _row(r: Any) -> LogEntry:
        return LogEntry(id=r["id"], ts=r["ts"], task_id=r["task_id"], source=r["source"], tool=r["tool"],
                        capability=r["capability"], summary=r["summary"], args=json.loads(r["args"] or "{}"),
                        risks=[x for x in (r["risks"] or "").split(",") if x], decision=r["decision"],
                        outcome=r["outcome"], result=r["result"], duration_ms=r["duration_ms"])
