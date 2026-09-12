"""Claude Code / Verboo Code adapter.

Both agents store one JSONL transcript per session under
``~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`` (Verboo Code defaults to
the same directory unless ``VERBOO_PROJECTS_DIR`` is set, and also reads
``~/.openclaude/projects``). The layouts are identical, so a single parser is
used and each transcript is classified as ``claude`` or ``verboo``.

Detection: Claude Code stamps every line with its semver ``version``; Verboo
Code writes ``version: "unknown"`` and its system banner mentions "Verboo Code".
Sidechain (sub-agent) entries are skipped so delegated work is not counted
twice, and meta/injected user turns are ignored so they are not mistaken for
real prompts.
"""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime
from typing import Callable, Optional

from ..cache import Cache
from ..config import Config, expand
from ..normalize import PROMPT, Event, Session, TOOL_CALL, iso_to_dt
from .base import SessionAdapter

# Both agents share this cache namespace so a transcript is parsed only once
# even though ClaudeAdapter and VerbooAdapter scan the same directory.
CACHE_SOURCE = "claude-family"

VERBOO_MARKER = "verboo code"
UNKNOWN_VERSIONS = {"", "unknown", "none", "null"}

# User-role lines that are injected by the agent, not typed by the human.
META_PREFIXES = (
    "caveat:",
    "<command-name>",
    "<bash-input>",
    "<local-command-stdout>",
    "<local-command-caveat>",
    "[request interrupted",
)


def _looks_like_claude_version(value) -> bool:
    return isinstance(value, str) and value.strip().lower() not in UNKNOWN_VERSIONS


class ClaudeAdapter(SessionAdapter):
    name = "claude"
    allowed_sources: tuple[str, ...] = ("claude",)

    def base_dirs(self) -> list[str]:
        return [expand(self.config.claude.dir)]

    def available(self) -> bool:
        return any(os.path.isdir(d) for d in self.base_dirs())

    def collect(
        self,
        start: datetime,
        end: datetime,
        cache: Optional[Cache] = None,
        path_filter: Optional[Callable[[str], bool]] = None,
    ) -> list[Session]:
        if not self.available():
            return []
        sessions: list[Session] = []
        seen: set[str] = set()
        for base in self.base_dirs():
            if not os.path.isdir(base):
                continue
            for file_path in sorted(glob.glob(os.path.join(base, "*", "*.jsonl"))):
                try:
                    stat = os.stat(file_path)
                except OSError:
                    continue
                session_id = os.path.splitext(os.path.basename(file_path))[0]
                fingerprint = f"{stat.st_size}:{stat.st_mtime_ns}"
                session = (
                    cache.get(CACHE_SOURCE, session_id, fingerprint) if cache else None
                )
                if session is None:
                    session = self._parse_file(file_path, session_id)
                    if session is None:
                        continue
                    if cache:
                        cache.put(CACHE_SOURCE, session_id, fingerprint, session)
                if session.source not in self.allowed_sources:
                    continue
                if session.id in seen:
                    continue
                if path_filter is not None and not path_filter(session.project_path):
                    continue
                if session.updated_at >= start and session.started_at <= end:
                    seen.add(session.id)
                    sessions.append(session)
        return sessions

    def _parse_file(self, file_path: str, fallback_id: str) -> Session | None:
        started: datetime | None = None
        updated: datetime | None = None
        cwd = ""
        session_id = fallback_id
        first_message = ""
        summary = ""
        events: list[Event] = []
        mentions_verboo = False
        has_claude_version = False

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

                    if _looks_like_claude_version(entry.get("version")):
                        has_claude_version = True
                    if entry.get("sessionId"):
                        session_id = str(entry["sessionId"])
                    if not mentions_verboo:
                        mentions_verboo = _mentions_verboo(entry)

                    # Skip sub-agent (sidechain) transcripts entirely.
                    if entry.get("isSidechain"):
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
                    self._consume(role, content, ts, events, is_meta=bool(entry.get("isMeta")))
                    if role == "user" and not first_message:
                        text = self._first_text(content)
                        if text:
                            first_message = text
        except OSError:
            return None

        if started is None:
            return None
        source = "claude" if (has_claude_version and not mentions_verboo) else "verboo"
        return Session(
            id=session_id,
            source=source,
            project_path=cwd,
            title=summary or first_message[:80] or "(sem titulo)",
            started_at=started,
            updated_at=updated or started,
            first_message=first_message,
            file_path=file_path,
            events=events,
        )

    def _consume(
        self, role: str, content, ts: datetime, events: list[Event], is_meta: bool = False
    ) -> None:
        if isinstance(content, str):
            if role == "user" and not is_meta and content.strip():
                text = content.strip()
                if not _is_meta_prompt(text):
                    events.append(Event(ts=ts, kind=PROMPT, text=text))
            return
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if role == "user" and btype == "text" and not is_meta:
                text = (block.get("text") or "").strip()
                if text and not _is_meta_prompt(text):
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


class VerbooAdapter(ClaudeAdapter):
    """Verboo Code sessions (stored in the Claude Code layout)."""

    name = "verboo"
    allowed_sources = ("verboo",)

    def base_dirs(self) -> list[str]:
        dirs: list[str] = []
        env = os.environ.get("VERBOO_PROJECTS_DIR")
        if env:
            dirs.append(expand(env))
        else:
            dirs.append(expand(self.config.verboo.dir))
        openclaude = expand("~/.openclaude/projects")
        if os.path.isdir(openclaude):
            dirs.append(openclaude)
        # Preserve order, drop duplicates and empty entries.
        result: list[str] = []
        for d in dirs:
            if d and d not in result:
                result.append(d)
        return result


def _mentions_verboo(entry: dict) -> bool:
    content = entry.get("content")
    if isinstance(content, str) and VERBOO_MARKER in content.lower():
        return True
    message = entry.get("message")
    if isinstance(message, dict):
        body = message.get("content")
        if isinstance(body, str) and VERBOO_MARKER in body.lower():
            return True
        if isinstance(body, list):
            for block in body:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    if VERBOO_MARKER in block["text"].lower():
                        return True
    return False


def _is_meta_prompt(text: str) -> bool:
    lowered = text.lstrip().lower()
    return any(lowered.startswith(prefix) for prefix in META_PREFIXES)


def first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("<"):
            return line[:200]
    return ""
