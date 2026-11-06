"""SQLite access: one connection (WAL mode) guarded by a lock, schema migrations via user_version."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MIGRATIONS: list[str] = [
    # v1 (milestone 2): action log, memory, routines, timers
    """
    CREATE TABLE action_log(
        id INTEGER PRIMARY KEY,
        ts REAL NOT NULL,
        task_id TEXT,
        source TEXT,
        tool TEXT NOT NULL,
        capability TEXT,
        summary TEXT,
        args TEXT,
        risks TEXT,
        decision TEXT NOT NULL,
        outcome TEXT,
        result TEXT,
        duration_ms REAL
    );
    CREATE INDEX action_log_ts ON action_log(ts);
    CREATE TABLE facts(
        id INTEGER PRIMARY KEY,
        text TEXT NOT NULL,
        created REAL NOT NULL,
        updated REAL NOT NULL,
        source TEXT
    );
    CREATE TABLE routines(
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        description TEXT NOT NULL DEFAULT '',
        steps TEXT NOT NULL,
        created REAL NOT NULL,
        updated REAL NOT NULL
    );
    CREATE TABLE timers(
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        due REAL NOT NULL,
        created REAL NOT NULL,
        done INTEGER NOT NULL DEFAULT 0
    );
    """,
]


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            for i in range(version, len(MIGRATIONS)):
                log.info("database: migrating to schema v%d", i + 1)
                with self.transaction():
                    for stmt in _split_sql(MIGRATIONS[i]):
                        self.conn.execute(stmt)
                    self.conn.execute(f"PRAGMA user_version={i + 1}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self.conn.close()


def _split_sql(script: str) -> list[str]:
    """Split a migration script into statements (our migrations contain no ';' inside literals)."""
    return [s.strip() for s in script.split(";") if s.strip()]
