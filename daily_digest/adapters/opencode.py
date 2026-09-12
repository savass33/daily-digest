"""opencode / verboo code adapter.

All opencode-family sessions live in a single SQLite database written in WAL
mode. We open it read-only with a busy timeout so a live writer never blocks us
and we never touch the file.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Optional

from ..cache import Cache
from ..config import Config, expand
from ..normalize import ASSISTANT, PROMPT, Session, Event, TODO, TOOL_CALL, ms_to_dt
from .base import SessionAdapter

FILE_TOOLS = {"edit", "write", "patch", "multiedit"}
COMMAND_TOOLS = {"bash", "shell"}


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

    def collect(
        self, start: datetime, end: datetime, cache: Optional[Cache] = None
    ) -> list[Session]:
        if not self.available():
            return []
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, parent_id, directory, title, model, agent, cost,
                       tokens_input, tokens_output, time_created, time_updated
                FROM session
                WHERE time_updated >= ? AND time_created <= ?
                ORDER BY time_created
                """,
                (start_ms, end_ms),
            ).fetchall()

            sessions: list[Session] = []
            for row in rows:
                fingerprint = self._fingerprint(conn, row)
                cached = cache.get(self.name, row["id"], fingerprint) if cache else None
                if cached is not None:
                    sessions.append(cached)
                    continue
                session = self._parse_session(conn, row)
                if cache:
                    cache.put(self.name, row["id"], fingerprint, session)
                sessions.append(session)
            return sessions
        finally:
            conn.close()

    def _fingerprint(self, conn: sqlite3.Connection, row: sqlite3.Row) -> str:
        seq = conn.execute(
            "SELECT seq FROM event_sequence WHERE aggregate_id=?", (row["id"],)
        ).fetchone()
        seq_value = seq[0] if seq else 0
        return f"{row['time_updated']}:{seq_value}"

    def _parse_session(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Session:
        model = ""
        raw_model = row["model"]
        if raw_model:
            try:
                parsed = json.loads(raw_model)
                model = parsed.get("id", "") if isinstance(parsed, dict) else str(parsed)
            except (ValueError, TypeError):
                model = str(raw_model)

        started = ms_to_dt(row["time_created"])
        updated = ms_to_dt(row["time_updated"])
        events = self._events(conn, row["id"])
        first_message = next(
            (e.text for e in events if e.kind == PROMPT and e.text), ""
        )
        todos = [
            r["content"]
            for r in conn.execute(
                "SELECT content FROM todo WHERE session_id=? ORDER BY position",
                (row["id"],),
            ).fetchall()
        ]
        return Session(
            id=row["id"],
            source=self.name,
            project_path=row["directory"] or "",
            title=row["title"] or "(sem titulo)",
            started_at=started,
            updated_at=updated,
            parent_id=row["parent_id"],
            is_subagent=bool(row["parent_id"]),
            agent=row["agent"] or "",
            model=model,
            cost=float(row["cost"] or 0.0),
            tokens_input=int(row["tokens_input"] or 0),
            tokens_output=int(row["tokens_output"] or 0),
            first_message=first_message,
            events=events,
            todos=todos,
        )

    def _events(self, conn: sqlite3.Connection, session_id: str) -> list[Event]:
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
