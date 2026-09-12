"""Claude Code adapter.

Claude Code writes one JSONL file per session under
``~/.claude/projects/<project-dir>/<session-id>.jsonl``. Sidechain (sub-agent)
entries are skipped so the digest never counts delegated work twice.
"""

from __future__ import annotations

import json
import os
import glob
from datetime import datetime
from typing import Optional

from ..cache import Cache
from ..config import Config, expand
from ..normalize import PROMPT, Session, Event, TOOL_CALL, iso_to_dt
from .base import SessionAdapter

FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Read"}
COMMAND_TOOLS = {"Bash", "BashOutput"}


class ClaudeAdapter(SessionAdapter):
    name = "claude"

    def base_dir(self) -> str:
        return expand(self.config.claude.dir)

    def available(self) -> bool:
        return os.path.isdir(self.base_dir())

    def collect(
        self, start: datetime, end: datetime, cache: Optional[Cache] = None
    ) -> list[Session]:
        if not self.available():
            return []
        sessions: list[Session] = []
        pattern = os.path.join(self.base_dir(), "*", "*.jsonl")
        for file_path in glob.glob(pattern):
            try:
                stat = os.stat(file_path)
            except OSError:
                continue
            session_id = os.path.splitext(os.path.basename(file_path))[0]
            fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}"
            session = cache.get(self.name, session_id, fingerprint) if cache else None
            if session is None:
                session = self._parse_file(file_path, session_id)
                if session is None:
                    continue
                if cache:
                    cache.put(self.name, session_id, fingerprint, session)
            if session.updated_at >= start and session.started_at <= end:
                sessions.append(session)
        return sessions

    def _parse_file(self, file_path: str, session_id: str) -> Session | None:
        started: datetime | None = None
        updated: datetime | None = None
        cwd = ""
        first_message = ""
        summary = ""
        events: list[Event] = []

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(entry, dict) or entry.get("isSidechain"):
                        continue
                    kind = entry.get("type")
                    ts = iso_to_dt(entry.get("timestamp"))
                    if entry.get("cwd") and not cwd:
                        cwd = entry["cwd"]
                    if kind == "summary" and entry.get("summary"):
                        summary = entry["summary"]
                        continue
                    if kind not in ("user", "assistant"):
                        continue
                    if ts.timestamp() > 0:
                        started = ts if started is None else min(started, ts)
                        updated = ts if updated is None else max(updated, ts)
                    message = entry.get("message") or {}
                    role = message.get("role") or entry.get("role") or kind
                    content = message.get("content")
                    if content is None:
                        content = entry.get("content")
                    self._consume(role, content, ts, events)
                    if role == "user" and not first_message:
                        text = self._first_text(content)
                        if text:
                            first_message = text
        except OSError:
            return None

        if started is None:
            return None
        return Session(
            id=session_id,
            source=self.name,
            project_path=cwd,
            title=summary or first_message[:80] or "(sem titulo)",
            started_at=started,
            updated_at=updated or started,
            first_message=first_message,
            file_path=file_path,
            events=events,
        )

    def _consume(self, role: str, content, ts: datetime, events: list[Event]) -> None:
        if isinstance(content, str):
            if role == "user" and content.strip():
                events.append(Event(ts=ts, kind=PROMPT, text=content.strip()))
            return
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if role == "user" and btype == "text":
                text = (block.get("text") or "").strip()
                if text:
                    events.append(Event(ts=ts, kind=PROMPT, text=text))
            elif role == "assistant" and btype == "tool_use":
                events.append(self._tool_event(ts, block))

    def _tool_event(self, ts: datetime, block: dict) -> Event:
        tool = block.get("name", "")
        payload = block.get("input") or {}
        files: list[str] = []
        command = ""
        text = tool
        if isinstance(payload, dict):
            file_path = payload.get("file_path") or payload.get("notebook_path")
            if file_path:
                files.append(str(file_path))
            if "command" in payload:
                command = str(payload["command"])
            if "pattern" in payload:
                text = f"{payload.get('pattern')} @ {payload.get('path', '')}".strip()
        return Event(ts=ts, kind=TOOL_CALL, text=text, files=files, command=command, tool=tool)

    def _first_text(self, content) -> str:
        if isinstance(content, str):
            return first_line(content)
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = first_line(block.get("text") or "")
                    if text:
                        return text
        return ""


def first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("<"):
            return line[:200]
    return ""
