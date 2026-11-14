"""SQLite persistence: server-side sessions, conversations and messages (history)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    sid TEXT PRIMARY KEY,
    kit_token TEXT,
    user_json TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    mode TEXT NOT NULL,
    title TEXT,
    pending_action TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_owner ON conversations(owner, updated_at);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    meta TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
"""


class Store:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _exec(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, args)
            rows = cur.fetchall()
            self._conn.commit()
            return rows

    # ---- sessions ----
    def create_session(self, ttl: float = 8 * 3600) -> str:
        sid = uuid.uuid4().hex + uuid.uuid4().hex
        now = time.time()
        self._exec("INSERT INTO sessions(sid, created_at, expires_at) VALUES (?,?,?)", (sid, now, now + ttl))
        return sid

    def get_session(self, sid: str) -> dict | None:
        rows = self._exec("SELECT * FROM sessions WHERE sid=? AND expires_at>?", (sid, time.time()))
        if not rows:
            return None
        r = rows[0]
        return {"sid": r["sid"], "kit_token": r["kit_token"],
                "user": json.loads(r["user_json"]) if r["user_json"] else None}

    def set_session_user(self, sid: str, token: str | None, user: dict | None, ttl: float = 8 * 3600) -> None:
        self._exec("UPDATE sessions SET kit_token=?, user_json=?, expires_at=? WHERE sid=?",
                   (token, json.dumps(user) if user else None, time.time() + ttl, sid))

    def delete_session(self, sid: str) -> None:
        self._exec("DELETE FROM sessions WHERE sid=?", (sid,))

    # ---- conversations ----
    def create_conversation(self, owner: str, mode: str, title: str) -> str:
        cid = uuid.uuid4().hex
        now = time.time()
        self._exec("INSERT INTO conversations(id, owner, mode, title, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                   (cid, owner, mode, title[:80], now, now))
        return cid

    def get_conversation(self, cid: str, owner: str) -> dict | None:
        rows = self._exec("SELECT * FROM conversations WHERE id=? AND owner=?", (cid, owner))
        return dict(rows[0]) if rows else None

    def list_conversations(self, owner: str, limit: int = 50) -> list[dict]:
        rows = self._exec(
            "SELECT c.id, c.mode, c.title, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count "
            "FROM conversations c WHERE owner=? ORDER BY updated_at DESC LIMIT ?", (owner, limit))
        return [dict(r) for r in rows]

    def set_pending_action(self, cid: str, action: dict | None) -> None:
        self._exec("UPDATE conversations SET pending_action=? WHERE id=?",
                   (json.dumps(action) if action else None, cid))

    def get_pending_action(self, cid: str) -> dict | None:
        rows = self._exec("SELECT pending_action FROM conversations WHERE id=?", (cid,))
        return json.loads(rows[0]["pending_action"]) if rows and rows[0]["pending_action"] else None

    # ---- messages ----
    def add_message(self, cid: str, role: str, content: str, meta: dict | None = None) -> None:
        now = time.time()
        self._exec("INSERT INTO messages(conversation_id, role, content, meta, created_at) VALUES (?,?,?,?,?)",
                   (cid, role, content, json.dumps(meta) if meta else None, now))
        self._exec("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))

    def get_messages(self, cid: str) -> list[dict]:
        rows = self._exec("SELECT role, content, meta, created_at FROM messages WHERE conversation_id=? ORDER BY id", (cid,))
        return [{"role": r["role"], "content": r["content"], "meta": json.loads(r["meta"]) if r["meta"] else None,
                 "created_at": r["created_at"]} for r in rows]

    def recent_turns(self, cid: str, turns: int = 10) -> list[dict]:
        rows = self._exec("SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                          (cid, turns * 2))
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
