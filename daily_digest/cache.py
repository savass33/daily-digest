"""Incremental cache backed by SQLite (WAL).

Several MCP server processes (one per agent) may run at once, so the cache must
tolerate concurrent writers. Each adapter stores a parsed session keyed by a
fingerprint; when the fingerprint is unchanged the expensive parse is skipped.

Sessions are **redacted before they are written**, so secrets never sit in the
cache in plain text. A ``salt`` (hash of the redaction rules) invalidates stale
entries when the rules change.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .normalize import Session
from .redact import Redactor

SCHEMA_VERSION = 2


def redaction_salt(redactor: Redactor) -> str:
    payload = json.dumps(
        {"enabled": redactor.enabled, "extra": [p.pattern for p in redactor.extra]},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def redact_session(session: Session, redactor: Redactor) -> Session:
    for event in session.events:
        event.text = redactor.text(event.text)
        event.command = redactor.text(event.command)
        event.files = [redactor.path(f) for f in event.files]
    session.title = redactor.text(session.title)
    session.first_message = redactor.text(session.first_message)
    session.todos = [redactor.text(t) for t in session.todos]
    session.file_path = redactor.path(session.file_path)
    session.project_path = redactor.path(session.project_path)
    return session


class Cache:
    def __init__(self, path: Path, redactor: Redactor | None = None):
        self.path = path
        self.salt = redaction_salt(redactor) if redactor else ""
        self._redactor = redactor
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass
            self.conn = self._open()
            self._migrate()
        except sqlite3.DatabaseError:
            # Corrupted cache: recreate from scratch.
            try:
                self.conn.close()
            except Exception:
                pass
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(str(self.path) + suffix)
                except OSError:
                    pass
            self.conn = self._open()
            self._migrate()

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _migrate(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_cache (
                source      TEXT NOT NULL,
                session_id  TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                salt        TEXT NOT NULL DEFAULT '',
                schema      INTEGER NOT NULL DEFAULT 0,
                payload     TEXT NOT NULL,
                updated_at  INTEGER NOT NULL,
                PRIMARY KEY (source, session_id)
            )
            """
        )
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(session_cache)")}
        if "salt" not in columns:
            self.conn.execute(
                "ALTER TABLE session_cache ADD COLUMN salt TEXT NOT NULL DEFAULT ''"
            )
        if "schema" not in columns:
            self.conn.execute(
                "ALTER TABLE session_cache ADD COLUMN schema INTEGER NOT NULL DEFAULT 0"
            )
        self.conn.commit()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def set_redactor(self, redactor: Redactor) -> None:
        """Attach redaction rules (and their salt) to an already-open cache."""
        self._redactor = redactor
        self.salt = redaction_salt(redactor)

    def get(self, source: str, session_id: str, fingerprint: str) -> Session | None:
        row = self.conn.execute(
            "SELECT fingerprint, salt, schema, payload FROM session_cache "
            "WHERE source=? AND session_id=?",
            (source, session_id),
        ).fetchone()
        if not row:
            return None
        fingerprint_stored, salt, schema, payload = row
        if fingerprint_stored != fingerprint or salt != self.salt:
            return None
        if schema != SCHEMA_VERSION:
            return None
        try:
            return Session.from_dict(json.loads(payload))
        except (ValueError, KeyError):
            return None

    def put(self, source: str, session_id: str, fingerprint: str, session: Session) -> None:
        if self._redactor is not None:
            session = redact_session(session, self._redactor)
        payload = json.dumps(session.to_dict(), ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO session_cache(source, session_id, fingerprint, salt, schema, payload, updated_at) "
            "VALUES(?,?,?,?,?,?,strftime('%s','now')) "
            "ON CONFLICT(source, session_id) DO UPDATE SET "
            "fingerprint=excluded.fingerprint, salt=excluded.salt, schema=excluded.schema, "
            "payload=excluded.payload, updated_at=excluded.updated_at",
            (source, session_id, fingerprint, self.salt, SCHEMA_VERSION, payload),
        )
        self.conn.commit()

    def stats(self) -> dict[str, Any]:
        total = self.conn.execute("SELECT COUNT(*) FROM session_cache").fetchone()[0]
        return {"entries": total, "path": str(self.path), "schema": SCHEMA_VERSION}

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass
