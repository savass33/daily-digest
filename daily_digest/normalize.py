"""Normalized session model shared by every adapter.

This is the platform-agnostic "format": adapters convert opencode / Claude Code
/ Codex stores into ``Session`` objects, and nothing downstream needs to know
which agent produced them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

PROMPT = "prompt"
TOOL_CALL = "tool_call"
TODO = "todo"
ASSISTANT = "assistant"


def ms_to_dt(value: int | float | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)


def iso_to_dt(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class Event:
    ts: datetime
    kind: str
    text: str = ""
    files: list[str] = field(default_factory=list)
    command: str = ""
    tool: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": int(self.ts.timestamp() * 1000),
            "kind": self.kind,
            "text": self.text,
            "files": self.files,
            "command": self.command,
            "tool": self.tool,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        return cls(
            ts=ms_to_dt(data.get("ts")),
            kind=data.get("kind", ""),
            text=data.get("text", ""),
            files=list(data.get("files", [])),
            command=data.get("command", ""),
            tool=data.get("tool", ""),
        )


@dataclass
class Session:
    id: str
    source: str
    project_path: str
    title: str
    started_at: datetime
    updated_at: datetime
    parent_id: str | None = None
    is_subagent: bool = False
    agent: str = ""
    model: str = ""
    cost: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    first_message: str = ""
    file_path: str = ""
    events: list[Event] = field(default_factory=list)
    todos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "project_path": self.project_path,
            "title": self.title,
            "started_at": int(self.started_at.timestamp() * 1000),
            "updated_at": int(self.updated_at.timestamp() * 1000),
            "parent_id": self.parent_id,
            "is_subagent": self.is_subagent,
            "agent": self.agent,
            "model": self.model,
            "cost": self.cost,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "first_message": self.first_message,
            "file_path": self.file_path,
            "events": [e.to_dict() for e in self.events],
            "todos": self.todos,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            id=data["id"],
            source=data["source"],
            project_path=data.get("project_path", ""),
            title=data.get("title", ""),
            started_at=ms_to_dt(data.get("started_at")),
            updated_at=ms_to_dt(data.get("updated_at")),
            parent_id=data.get("parent_id"),
            is_subagent=data.get("is_subagent", False),
            agent=data.get("agent", ""),
            model=data.get("model", ""),
            cost=data.get("cost", 0.0),
            tokens_input=data.get("tokens_input", 0),
            tokens_output=data.get("tokens_output", 0),
            first_message=data.get("first_message", ""),
            file_path=data.get("file_path", ""),
            events=[Event.from_dict(e) for e in data.get("events", [])],
            todos=list(data.get("todos", [])),
        )


def rollup(sessions: list[Session]) -> list[Session]:
    """Fold sub-agent sessions into their parents.

    Sub-sessions (opencode ``parent_id``, Claude sidechains already dropped at
    parse time, Codex delegations) stay attached to the parent as compact
    children so the same work is never counted twice at the top level.
    """
    by_id = {s.id: s for s in sessions}
    top: list[Session] = []
    for session in sessions:
        parent = by_id.get(session.parent_id) if session.parent_id else None
        if parent is not None:
            session.is_subagent = True
            parent.events.append(
                Event(
                    ts=session.started_at,
                    kind=ASSISTANT,
                    text=f"[subagent:{session.agent or 'agent'}] {session.title}",
                    tool="subagent",
                )
            )
            for event in session.events:
                if event.kind == PROMPT:
                    parent.events.append(event)
        else:
            top.append(session)
    for session in top:
        session.events.sort(key=lambda e: e.ts)
    return top
