"""Conversation history in SQLite with full-text search (History tab, export, retention)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

from .db import Database


@dataclass
class Conversation:
    id: int
    title: str
    started: float
    updated: float
    language: str = ""


@dataclass
class Message:
    id: int
    conversation_id: int
    ts: float
    role: str          # user | assistant | tool
    content: str
    meta: dict[str, Any]


class ConversationStore:
    def __init__(self, db: Database):
        self.db = db

    def create(self, title: str = "", language: str = "") -> Conversation:
        now = time.time()
        cur = self.db.execute("INSERT INTO conversations(title, started, updated, language) VALUES (?,?,?,?)",
                              (title[:120], now, now, language))
        return Conversation(int(cur.lastrowid or 0), title, now, now, language)

    def add(self, conversation_id: int, role: str, content: str, meta: dict[str, Any] | None = None) -> int:
        now = time.time()
        with self.db.transaction() as conn:
            cur = conn.execute("INSERT INTO messages(conversation_id, ts, role, content, meta) VALUES (?,?,?,?,?)",
                               (conversation_id, now, role, content,
                                json.dumps(meta, ensure_ascii=False) if meta else None))
            conn.execute("UPDATE conversations SET updated=?, title=CASE WHEN title='' AND ?='user' THEN ? "
                         "ELSE title END WHERE id=?", (now, role, _title(content), conversation_id))
        return int(cur.lastrowid or 0)

    def get(self, conversation_id: int) -> Conversation | None:
        rows = self.db.query("SELECT * FROM conversations WHERE id=?", (conversation_id,))
        return _conv(rows[0]) if rows else None

    def messages(self, conversation_id: int) -> list[Message]:
        return [_msg(r) for r in self.db.query("SELECT * FROM messages WHERE conversation_id=? ORDER BY id",
                                               (conversation_id,))]

    def recent(self, limit: int = 50, offset: int = 0) -> list[Conversation]:
        return [_conv(r) for r in self.db.query("SELECT * FROM conversations ORDER BY updated DESC LIMIT ? OFFSET ?",
                                                (limit, offset))]

    def search(self, query: str, limit: int = 50) -> list[tuple[Conversation, str]]:
        """Full-text search; returns (conversation, highlighted snippet)."""
        terms = [t for t in re.findall(r"\w+", query, re.UNICODE) if t]
        if not terms:
            return []
        fts = " ".join(f'"{t}"*' for t in terms)
        rows = self.db.query(
            "SELECT c.*, snippet(messages_fts, 0, '[', ']', '…', 12) AS snip FROM messages_fts "
            "JOIN messages m ON m.id = messages_fts.rowid JOIN conversations c ON c.id = m.conversation_id "
            "WHERE messages_fts MATCH ? ORDER BY c.updated DESC LIMIT ?", (fts, limit * 4))
        seen, out = set(), []
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            out.append((_conv(r), r["snip"]))
            if len(out) >= limit:
                break
        return out

    def rename(self, conversation_id: int, title: str) -> bool:
        return self.db.execute("UPDATE conversations SET title=? WHERE id=?",
                               (title.strip()[:120], conversation_id)).rowcount > 0

    def delete(self, conversation_id: int) -> bool:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
            return conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,)).rowcount > 0

    def purge(self, retention_days: int) -> int:
        """Delete conversations not updated within ``retention_days`` (0 = keep forever)."""
        if retention_days <= 0:
            return 0
        cutoff = time.time() - retention_days * 86400
        ids = [r["id"] for r in self.db.query("SELECT id FROM conversations WHERE updated < ?", (cutoff,))]
        for cid in ids:
            self.delete(cid)
        return len(ids)

    def export(self, conversation_id: int, fmt: str = "md") -> str:
        conv = self.get(conversation_id)
        if conv is None:
            raise KeyError(conversation_id)
        msgs = [m for m in self.messages(conversation_id) if m.role in ("user", "assistant")]
        if fmt == "json":
            return json.dumps({"id": conv.id, "title": conv.title, "started": conv.started,
                               "messages": [{"role": m.role, "content": m.content, "ts": m.ts, **(
                                   {"meta": m.meta} if m.meta else {})} for m in msgs]},
                              ensure_ascii=False, indent=2)
        lines = [f"# {conv.title or 'Conversation'}", "",
                 f"*{time.strftime('%Y-%m-%d %H:%M', time.localtime(conv.started))}*", ""]
        for m in msgs:
            who = "**You:**" if m.role == "user" else "**Assistant:**"
            lines += [f"{who} {m.content}", ""]
        return "\n".join(lines)


def _title(text: str) -> str:
    t = " ".join(text.split())
    return t if len(t) <= 60 else t[:57].rsplit(" ", 1)[0] + "…"


def _conv(r: Any) -> Conversation:
    return Conversation(r["id"], r["title"], r["started"], r["updated"], r["language"])


def _msg(r: Any) -> Message:
    return Message(r["id"], r["conversation_id"], r["ts"], r["role"], r["content"],
                   json.loads(r["meta"]) if r["meta"] else {})
