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
    NEAR_DUPLICATE = 95   # same fact with tiny wording differences -> update instead of adding

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
        near = self._near_duplicate(text)
        if near is not None:     # e.g. "My sister's name is Lea" vs "my sisters name is Lea."
            self.db.execute("UPDATE facts SET text=?, updated=? WHERE id=?", (text, now, near.id))
            return Fact(near.id, text, near.created, now)
        cur = self.db.execute("INSERT INTO facts(text, created, updated, source) VALUES (?,?,?,?)",
                              (text, now, now, source))
        # keep the store bounded: drop the oldest beyond the limit
        self.db.execute("DELETE FROM facts WHERE id NOT IN (SELECT id FROM facts ORDER BY updated DESC LIMIT ?)",
                        (self.MAX_FACTS,))
        return Fact(int(cur.lastrowid or 0), text, now, now)

    def _near_duplicate(self, text: str) -> Fact | None:
        from rapidfuzz import fuzz

        key = _key(text)
        best = max(((fuzz.ratio(key, _key(f.text)), f) for f in self.all()), key=lambda x: x[0], default=None)
        return best[1] if best is not None and best[0] >= self.NEAR_DUPLICATE else None

    def count(self) -> int:
        return int(self.db.query("SELECT COUNT(*) AS n FROM facts")[0]["n"])

    def all(self) -> list[Fact]:
        return [Fact(r["id"], r["text"], r["created"], r["updated"])
                for r in self.db.query("SELECT * FROM facts ORDER BY updated DESC")]

    def search(self, query: str, limit: int = 10) -> list[Fact]:
        return [f for _, f in self.search_scored(query, limit)]

    def search_scored(self, query: str, limit: int = 10, min_score: float = 50) -> list[tuple[float, Fact]]:
        """Best matches first, with their fuzzy score (0-100)."""
        from rapidfuzz import fuzz

        facts = self.all()
        q = query.lower().strip()
        if not q:
            return [(100.0, f) for f in facts[:limit]]
        scored = sorted(((float(fuzz.token_set_ratio(q, f.text.lower())), f) for f in facts),
                        key=lambda x: x[0], reverse=True)
        return [(sc, f) for sc, f in scored if sc >= min_score][:limit]

    def delete(self, fact_id: int) -> bool:
        return self.db.execute("DELETE FROM facts WHERE id=?", (fact_id,)).rowcount > 0

    def update(self, fact_id: int, text: str) -> bool:
        return self.db.execute("UPDATE facts SET text=?, updated=? WHERE id=?",
                               (text.strip(), time.time(), fact_id)).rowcount > 0


def _key(text: str) -> str:
    import re

    return " ".join(re.findall(r"[^\W_]+", text.casefold().replace("'", "").replace("’", "")))
