"""Codex CLI adapter.

Codex stores one rollout JSONL per session under
``~/.codex/sessions`` and ``~/.codex/archived_sessions``. User-role messages may
carry provider context envelopes (``<environment_context>`` and friends) which
we strip so only the real prompt survives.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

from ..cache import Cache
from ..config import Config, expand
from ..normalize import PROMPT, Session, Event, TOOL_CALL, iso_to_dt
from .base import SessionAdapter

CONTEXT_ENVELOPES = {
    "app_context",
    "bash_input",
    "bash_stderr",
    "bash_stdout",
    "codex_delegation",
    "collaboration_mode",
    "command_args",
    "command_message",
    "command_name",
    "environment_context",
    "in_app_browser_context",
    "local_command_caveat",
    "local_command_stderr",
    "local_command_stdout",
    "permissions",
    "realtime_delegation",
    "recommended_plugins",
    "skill",
    "system_reminder",
    "task_notification",
    "turn_aborted",
    "user_instructions",
}


class CodexAdapter(SessionAdapter):
    name = "codex"

    def base_dir(self) -> str:
        return expand(self.config.codex.dir)

    def session_dirs(self) -> list[str]:
        base = self.base_dir()
        return [
            os.path.join(base, "sessions"),
            os.path.join(base, "archived_sessions"),
        ]

    def available(self) -> bool:
        return any(os.path.isdir(d) for d in self.session_dirs())

    def _find_files(self) -> list[str]:
        files: list[str] = []
        for root in self.session_dirs():
            if not os.path.isdir(root):
                continue
            for dirpath, _dirs, names in os.walk(root):
                for name in names:
                    if name.startswith("rollout-") and name.endswith(".jsonl"):
                        files.append(os.path.join(dirpath, name))
        return files

    def collect(
        self, start: datetime, end: datetime, cache: Optional[Cache] = None
    ) -> list[Session]:
        if not self.available():
            return []
        sessions: list[Session] = []
        for file_path in self._find_files():
            try:
                stat = os.stat(file_path)
            except OSError:
                continue
            key = os.path.basename(file_path)
            fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}"
            session = cache.get(self.name, key, fingerprint) if cache else None
            if session is None:
                session = self._parse_file(file_path, key)
                if session is None:
                    continue
                if cache:
                    cache.put(self.name, key, fingerprint, session)
            if session.updated_at >= start and session.started_at <= end:
                sessions.append(session)
        return sessions

    def _parse_file(self, file_path: str, fallback_id: str) -> Session | None:
        session_id = fallback_id
        cwd = ""
        started: datetime | None = None
        updated: datetime | None = None
        first_message = ""
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
                    if not isinstance(entry, dict):
                        continue
                    etype = entry.get("type")
                    payload = entry.get("payload") or {}
                    ts = iso_to_dt(entry.get("timestamp"))
                    if etype == "session_meta":
                        if payload.get("id"):
                            session_id = payload["id"]
                        if payload.get("cwd"):
                            cwd = payload["cwd"]
                        if payload.get("timestamp") and ts.timestamp() == 0:
                            ts = iso_to_dt(payload["timestamp"])
                    elif etype == "turn_context":
                        if payload.get("cwd") and not cwd:
                            cwd = payload["cwd"]
                    elif etype == "response_item":
                        self._consume(payload, ts, events)
                        if not first_message:
                            text = self._first_prompt(events)
                            if text:
                                first_message = text
                    if ts.timestamp() > 0:
                        started = ts if started is None else min(started, ts)
                        updated = ts if updated is None else max(updated, ts)
        except OSError:
            return None

        if started is None:
            return None
        return Session(
            id=session_id,
            source=self.name,
            project_path=cwd,
            title=first_message[:80] or "(sem titulo)",
            started_at=started,
            updated_at=updated or started,
            first_message=first_message,
            file_path=file_path,
            events=events,
        )

    def _consume(self, payload: dict, ts: datetime, events: list[Event]) -> None:
        ptype = payload.get("type")
        role = payload.get("role")
        if ptype == "message" and role == "user":
            text = self._extract_text(payload.get("content"), ("input_text", "text"))
            text = _strip_envelopes(text)
            if text:
                events.append(Event(ts=ts, kind=PROMPT, text=text))
        elif ptype in ("function_call", "custom_tool_call", "local_shell_call"):
            events.append(self._tool_event(ts, payload))
        elif ptype == "reasoning":
            return

    def _tool_event(self, ts: datetime, payload: dict) -> Event:
        ptype = payload.get("type")
        name = payload.get("name") or ptype or "tool"
        files: list[str] = []
        command = ""
        args = payload.get("arguments") or payload.get("input")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if isinstance(args, dict):
            file_path = args.get("file_path") or args.get("path")
            if file_path:
                files.append(str(file_path))
            if "command" in args:
                cmd = args["command"]
                command = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        action = payload.get("action")
        if isinstance(action, dict) and "command" in action:
            cmd = action["command"]
            command = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        return Event(ts=ts, kind=TOOL_CALL, text=name, files=files, command=command, tool=name)

    def _extract_text(self, content, kinds) -> str:
        if isinstance(content, str):
            return content
        parts: list[str] = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in kinds:
                    if block.get("text"):
                        parts.append(block["text"])
        return "\n".join(parts).strip()

    def _first_prompt(self, events: list[Event]) -> str:
        for event in events:
            if event.kind == PROMPT and event.text:
                line = event.text.splitlines()[0].strip()
                return line[:200]
        return ""


def _strip_envelopes(text: str) -> str:
    remaining = text.strip()
    while remaining.startswith("<"):
        end = remaining.find(">")
        if end < 2:
            break
        tag = remaining[1:end].strip().split()[0].rstrip("/")
        normalized = tag.lower().replace("-", "_")
        if normalized not in CONTEXT_ENVELOPES:
            break
        close = f"</{tag.lower()}>"
        close_idx = remaining.lower().find(close)
        if close_idx == -1:
            remaining = remaining[end + 1:].strip()
            continue
        remaining = remaining[close_idx + len(close):].strip()
    return remaining
