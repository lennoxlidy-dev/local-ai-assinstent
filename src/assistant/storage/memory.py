"""Remembered facts ("remember that my sister's birthday is May 3rd")."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .db import Database


@dataclass
class Fact:
    id: int
    text: str
    created: float
    updated: float


class MemoryStore:
    MAX_FACTS = 500

    def __init__(self, db: Database):
        self.db = db

    def add(self, text: str, source: str = "user") -> Fact:
        text = " ".join(text.split()).strip()
        if not text:
            raise ValueError("empty fact")
        now = time.time()
        existing = self.db.query("SELECT * FROM facts WHERE lower(text) = lower(?)", (text,))
        if existing:
            self.db.execute("UPDATE facts SET updated=? WHERE id=?", (now, existing[0]["id"]))
            return Fact(existing[0]["id"], text, existing[0]["created"], now)
        cur = self.db.execute("INSERT INTO facts(text, created, updated, source) VALUES (?,?,?,?)",
                              (text, now, now, source))
        # keep the store bounded: drop the oldest beyond the limit
        self.db.execute("DELETE FROM facts WHERE id NOT IN (SELECT id FROM facts ORDER BY updated DESC LIMIT ?)",
                        (self.MAX_FACTS,))
        return Fact(int(cur.lastrowid or 0), text, now, now)

    def all(self) -> list[Fact]:
        return [Fact(r["id"], r["text"], r["created"], r["updated"])
                for r in self.db.query("SELECT * FROM facts ORDER BY updated DESC")]

    def search(self, query: str, limit: int = 10) -> list[Fact]:
        from rapidfuzz import fuzz

        facts = self.all()
        q = query.lower().strip()
        if not q:
            return facts[:limit]
        scored = sorted(((fuzz.token_set_ratio(q, f.text.lower()), f) for f in facts),
                        key=lambda x: x[0], reverse=True)
        return [f for score, f in scored if score >= 50][:limit]

    def delete(self, fact_id: int) -> bool:
        return self.db.execute("DELETE FROM facts WHERE id=?", (fact_id,)).rowcount > 0

    def update(self, fact_id: int, text: str) -> bool:
        return self.db.execute("UPDATE facts SET text=?, updated=? WHERE id=?",
                               (text.strip(), time.time(), fact_id)).rowcount > 0
