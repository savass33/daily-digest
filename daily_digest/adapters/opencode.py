"""opencode / verboo code adapter.

All opencode-family sessions live in a single SQLite database written in WAL
mode. We open it read-only with a busy timeout so a live writer never blocks us
and we never touch the file.

The schema is probed before querying and missing columns degrade to defaults,
so a version drift does not silently drop a source.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Callable, Optional

from ..cache import Cache
from ..config import Config, expand
from ..normalize import PROMPT, Session, Event, TOOL_CALL, ms_to_dt
from .base import SessionAdapter

SESSION_COLUMNS = [
    "id",
    "parent_id",
    "directory",
    "title",
    "model",
    "agent",
    "cost",
    "tokens_input",
    "tokens_output",
    "time_created",
    "time_updated",
]


def _val(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    try:
        if key in row.keys():
            value = row[key]
            return default if value is None else value
    except (IndexError, KeyError):
        pass
    return default


class OpencodeAdapter(SessionAdapter):
    name = "opencode"

    def db_path(self) -> str:
        return expand(self.config.opencode.db)

    def available(self) -> bool:
        path = self.db_path()
        return os.path.isfile(path)

    def _connect(self) -> sqlite3.Connection:
        uri = f"file:{self.db_path()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        try:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            return set()

    def collect(
        self,
        start: datetime,
        end: datetime,
        cache: Optional[Cache] = None,
        path_filter: Optional[Callable[[str], bool]] = None,
    ) -> list[Session]:
        if not self.available():
            return []
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        conn = self._connect()
        try:
            available = self._columns(conn, "session")
            if "id" not in available:
                return []
            select = ", ".join(
                c if c in available else f"NULL AS {c}" for c in SESSION_COLUMNS
            )
            timed = "time_updated" in available and "time_created" in available
            query = f"SELECT {select} FROM session"
            params: tuple = ()
            if timed:
                query += " WHERE time_updated >= ? AND time_created <= ?"
                params = (start_ms, end_ms)
            query += " ORDER BY time_created" if "time_created" in available else ""

            sessions: list[Session] = []
            for row in conn.execute(query, params).fetchall():
                if not timed:
                    created = _val(row, "time_created", 0) or 0
                    updated = _val(row, "time_updated", 0) or 0
                    if updated < start_ms or created > end_ms:
                        continue
                directory = _val(row, "directory", "") or ""
                # Filter before reading message parts: cheaper and prevents
                # out-of-scope content from being parsed at all.
                if path_filter is not None and not path_filter(directory):
                    continue
                fingerprint = self._fingerprint(conn, row)
                cached = (
                    cache.get(self.name, _val(row, "id"), fingerprint) if cache else None
                )
                if cached is not None:
                    sessions.append(cached)
                    continue
                session = self._parse_session(conn, row)
                if cache:
                    cache.put(self.name, _val(row, "id"), fingerprint, session)
                sessions.append(session)
            return sessions
        finally:
            conn.close()

    def _fingerprint(self, conn: sqlite3.Connection, row: sqlite3.Row) -> str:
        seq_value = 0
        try:
            seq = conn.execute(
                "SELECT seq FROM event_sequence WHERE aggregate_id=?",
                (_val(row, "id"),),
            ).fetchone()
            seq_value = seq[0] if seq else 0
        except sqlite3.Error:
            seq_value = 0
        return f"{_val(row, 'time_updated', 0)}:{seq_value}"

    def _parse_session(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Session:
        model = ""
        raw_model = _val(row, "model", "")
        if raw_model:
            try:
                parsed = json.loads(raw_model)
                model = parsed.get("id", "") if isinstance(parsed, dict) else str(parsed)
            except (ValueError, TypeError):
                model = str(raw_model)

        session_id = _val(row, "id", "")
        started = ms_to_dt(_val(row, "time_created", 0))
        updated = ms_to_dt(_val(row, "time_updated", 0))
        events = self._events(conn, session_id)
        first_message = next(
            (e.text for e in events if e.kind == PROMPT and e.text), ""
        )
        todos = self._todos(conn, session_id)
        parent_id = _val(row, "parent_id")
        return Session(
            id=session_id,
            source=self.name,
            project_path=_val(row, "directory", "") or "",
            title=_val(row, "title", "") or "(sem titulo)",
            started_at=started,
            updated_at=updated,
            parent_id=parent_id,
            is_subagent=bool(parent_id),
            agent=_val(row, "agent", "") or "",
            model=model,
            cost=float(_val(row, "cost", 0) or 0.0),
            tokens_input=int(_val(row, "tokens_input", 0) or 0),
            tokens_output=int(_val(row, "tokens_output", 0) or 0),
            first_message=first_message,
            events=events,
            todos=todos,
        )

    def _todos(self, conn: sqlite3.Connection, session_id: str) -> list[str]:
        try:
            rows = conn.execute(
                "SELECT content FROM todo WHERE session_id=? ORDER BY position",
                (session_id,),
            ).fetchall()
            return [r["content"] for r in rows]
        except sqlite3.Error:
            return []

    def _events(self, conn: sqlite3.Connection, session_id: str) -> list[Event]:
        try:
            rows = conn.execute(
                """
                SELECT json_extract(m.data, '$.role') AS role,
                       json_extract(p.data, '$.type') AS ptype,
                       p.data AS pdata,
                       p.time_created AS ts
                FROM part p
                JOIN message m ON m.id = p.message_id
                WHERE p.session_id = ?
                ORDER BY p.time_created, p.id
                """,
                (session_id,),
            ).fetchall()
        except sqlite3.Error:
            return []

        events: list[Event] = []
        for row in rows:
            try:
                data = json.loads(row["pdata"])
            except (ValueError, TypeError):
                continue
            ts = ms_to_dt(row["ts"])
            ptype = row["ptype"]
            if ptype == "text" and row["role"] == "user":
                text = (data.get("text") or "").strip()
                if text:
                    events.append(Event(ts=ts, kind=PROMPT, text=text))
            elif ptype == "tool":
                events.append(self._tool_event(ts, data))
        return events

    def _tool_event(self, ts: datetime, data: dict) -> Event:
        tool = data.get("tool", "")
        state = data.get("state") or {}
        payload = state.get("input") or {}
        text = ""
        files: list[str] = []
        command = ""

        if isinstance(payload, dict):
            file_path = payload.get("filePath") or payload.get("path")
            if file_path:
                files.append(str(file_path))
            if "command" in payload:
                command = str(payload["command"])
            if "description" in payload:
                text = str(payload["description"])
            elif "pattern" in payload and "path" in payload:
                text = f"{payload.get('pattern')} @ {payload.get('path')}"
        if not text and tool:
            text = tool
        return Event(
            ts=ts,
            kind=TOOL_CALL,
            text=text,
            files=files,
            command=command,
            tool=tool,
        )
