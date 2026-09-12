"""Incremental cache backed by SQLite (WAL).

Several MCP server processes (one per agent) may run at once, so the cache must
tolerate concurrent writers. Each adapter stores a parsed session keyed by a
fingerprint; when the fingerprint is unchanged the expensive parse is skipped.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .normalize import Session


class Cache:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=10)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_cache (
                source      TEXT NOT NULL,
                session_id  TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                payload     TEXT NOT NULL,
                updated_at  INTEGER NOT NULL,
                PRIMARY KEY (source, session_id)
            )
            """
        )
        self.conn.commit()

    def get(self, source: str, session_id: str, fingerprint: str) -> Session | None:
        row = self.conn.execute(
            "SELECT fingerprint, payload FROM session_cache "
            "WHERE source=? AND session_id=?",
            (source, session_id),
        ).fetchone()
        if not row or row[0] != fingerprint:
            return None
        try:
            return Session.from_dict(json.loads(row[1]))
        except (ValueError, KeyError):
            return None

    def put(self, source: str, session_id: str, fingerprint: str, session: Session) -> None:
        payload = json.dumps(session.to_dict(), ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO session_cache(source, session_id, fingerprint, payload, updated_at) "
            "VALUES(?,?,?,?,strftime('%s','now')) "
            "ON CONFLICT(source, session_id) DO UPDATE SET "
            "fingerprint=excluded.fingerprint, payload=excluded.payload, "
            "updated_at=excluded.updated_at",
            (source, session_id, fingerprint, payload),
        )
        self.conn.commit()

    def stats(self) -> dict[str, Any]:
        total = self.conn.execute("SELECT COUNT(*) FROM session_cache").fetchone()[0]
        return {"entries": total, "path": str(self.path)}

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass
