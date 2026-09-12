"""Digest assembly: window resolution, compression, redaction and grouping."""

from __future__ import annotations

import os
import re
from datetime import date as date_cls
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .adapters import build_adapters
from .cache import Cache
from .config import Config
from .git_source import collect as collect_git
from .normalize import PROMPT, Session, TOOL_CALL, rollup
from .redact import Redactor

VALID_OUTPUTS = {"markdown", "whatsapp", "both", "none"}


def resolve_tz(config: Config) -> ZoneInfo:
    if config.timezone and config.timezone.lower() != "local":
        try:
            return ZoneInfo(config.timezone)
        except Exception:
            pass
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def parse_date(value: str | None, tz) -> date_cls:
    today = datetime.now(tz).date()
    if not value or value.lower() == "today":
        return today
    if value.lower() == "yesterday":
        return today - timedelta(days=1)
    try:
        return date_cls.fromisoformat(value)
    except ValueError:
        return today


def window_for(target: date_cls, tz) -> tuple[datetime, datetime]:
    start = datetime.combine(target, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = (datetime.combine(target, time.min, tzinfo=tz) + timedelta(days=1)).astimezone(
        timezone.utc
    )
    return start, end


def resolve_outputs(config: Config, override: str | None) -> dict[str, bool]:
    outputs = {
        "markdown": config.output.markdown,
        "whatsapp": config.output.whatsapp,
        "review": config.output.review_before_send,
    }
    key = (override or "").strip().lower()
    if key in ("markdown", "md"):
        outputs.update(markdown=True, whatsapp=False)
    elif key == "whatsapp":
        outputs.update(markdown=False, whatsapp=True)
    elif key == "both":
        outputs.update(markdown=True, whatsapp=True)
    elif key == "none":
        outputs.update(markdown=False, whatsapp=False)
    return outputs


def _truncate(text: str, limit: int = 280) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _compact_session(
    session: Session, start: datetime, end: datetime, redactor: Redactor, config: Config
) -> dict[str, Any] | None:
    in_window = [
        e
        for e in session.events
        if start <= e.ts < end
    ]
    if not in_window and not (start <= session.updated_at < end):
        return None

    prompts: list[str] = []
    files: list[str] = []
    commands: list[str] = []
    tools: dict[str, int] = {}
    subagents: list[dict[str, str]] = []
    for event in in_window:
        if event.kind == PROMPT:
            prompts.append(redactor.text(_truncate(event.text)))
        elif event.kind == TOOL_CALL:
            if event.tool == "subagent":
                continue
            tools[event.tool] = tools.get(event.tool, 0) + 1
            for path in event.files:
                files.append(redactor.path(path))
            if event.command:
                commands.append(redactor.text(_truncate(event.command, 200)))
        elif event.kind == "assistant" and event.tool == "subagent":
            subagents.append({"title": redactor.text(event.text)})

    prompt_limit = config.limits.max_prompts_per_session
    if len(prompts) > prompt_limit:
        head = prompts[: prompt_limit - 2]
        prompts = head + ["…"] + prompts[-2:]

    return {
        "source": session.source,
        "id": session.id,
        "title": redactor.text(session.title),
        "project": redactor.path(session.project_path),
        "model": session.model,
        "agent": session.agent,
        "started": session.started_at.astimezone(resolve_tz(config)).isoformat(),
        "updated": session.updated_at.astimezone(resolve_tz(config)).isoformat(),
        "duration_min": max(
            1, int((session.updated_at - session.started_at).total_seconds() // 60)
        ),
        "cost": round(session.cost, 4),
        "tokens_input": session.tokens_input,
        "tokens_output": session.tokens_output,
        "todos": [redactor.text(t) for t in session.todos],
        "prompts": prompts,
        "files": _dedupe(files)[: config.limits.max_files_per_session],
        "commands": _dedupe(commands)[: config.limits.max_commands_per_session],
        "tools": tools,
        "subagents": subagents[:5],
    }


def build_digest(
    config: Config,
    date_value: str | None = None,
    sources: list[str] | None = None,
    output_override: str | None = None,
    cache: Optional[Cache] = None,
) -> dict[str, Any]:
    tz = resolve_tz(config)
    target = parse_date(date_value, tz)
    start, end = window_for(target, tz)
    redactor = Redactor(config.redact.enabled, config.redact.extra_patterns)

    adapters = build_adapters(config, only=sources)
    raw: list[Session] = []
    for adapter in adapters:
        try:
            raw.extend(adapter.collect(start, end, cache))
        except Exception:  # a broken store must not sink the whole digest
            continue

    top = rollup(raw)
    compact: list[dict[str, Any]] = []
    for session in top:
        item = _compact_session(session, start, end, redactor, config)
        if item is not None:
            compact.append(item)

    projects: dict[str, dict[str, Any]] = {}
    for item in compact:
        key = item["project"] or "(sem projeto)"
        bucket = projects.setdefault(
            key, {"name": os.path.basename(item["project"].rstrip("/")) or key, "path": key, "sessions": []}
        )
        bucket["sessions"].append(item)

    project_list = sorted(
        projects.values(), key=lambda p: sum(s["duration_min"] for s in p["sessions"]), reverse=True
    )

    project_paths = [item["project"] for item in compact]
    git_data = collect_git(project_paths, start, end, config)

    totals = {
        "sessions": len(compact),
        "prompts": sum(len(s["prompts"]) for s in compact),
        "files": len({f for s in compact for f in s["files"]}),
        "cost": round(sum(s["cost"] for s in compact), 4),
        "tokens_input": sum(s["tokens_input"] for s in compact),
        "tokens_output": sum(s["tokens_output"] for s in compact),
    }

    return {
        "date": target.isoformat(),
        "window": {
            "start": start.astimezone(tz).isoformat(),
            "end": end.astimezone(tz).isoformat(),
        },
        "outputs": resolve_outputs(config, output_override),
        "sources": [a.name for a in adapters],
        "totals": totals,
        "projects": project_list,
        "git": {
            redactor.path(repo): [
                {
                    "hash": c["hash"][:8],
                    "subject": redactor.text(c["subject"]),
                    "files": [redactor.path(f) for f in c["files"][:10]],
                }
                for c in commits
            ]
            for repo, commits in git_data.items()
        },
    }


def list_sessions(
    config: Config,
    period: str | None = None,
    source: str | None = None,
    cache: Optional[Cache] = None,
) -> dict[str, Any]:
    sources = [source] if source and source != "all" else None
    digest = build_digest(config, period, sources, "none", cache)
    out = []
    for project in digest["projects"]:
        for session in project["sessions"]:
            out.append(
                {
                    "id": session["id"],
                    "source": session["source"],
                    "project": project["path"],
                    "title": session["title"],
                    "started": session["started"],
                    "duration_min": session["duration_min"],
                    "prompts": len(session["prompts"]),
                    "files": session["files"],
                }
            )
    out.sort(key=lambda s: s["started"], reverse=True)
    return {"date": digest["date"], "count": len(out), "sessions": out}


def search_sessions(
    config: Config,
    query: str,
    source: str | None = None,
    cache: Optional[Cache] = None,
) -> dict[str, Any]:
    if not query:
        return {"query": query, "count": 0, "results": []}
    sources = [source] if source and source != "all" else None
    digest = build_digest(config, "today", sources, "none", cache)
    needle = query.lower()
    results = []
    for project in digest["projects"]:
        for session in project["sessions"]:
            haystack = " ".join(
                [session["title"], project["path"], " ".join(session["prompts"]), " ".join(session["files"])]
            ).lower()
            if needle in haystack:
                results.append(
                    {
                        "id": session["id"],
                        "source": session["source"],
                        "project": project["path"],
                        "title": session["title"],
                        "started": session["started"],
                    }
                )
    return {"query": query, "count": len(results), "results": results}


def write_digest(config: Config, markdown: str, target_date: str | None = None) -> dict[str, Any]:
    """Write ``~/daily/<date>.md`` and archive the previous version."""
    tz = resolve_tz(config)
    target = parse_date(target_date, tz)
    out_dir = config.resolved_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{target.isoformat()}.md"

    archived = None
    if path.exists():
        archive_dir = out_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz).strftime("%H%M%S")
        archived_path = archive_dir / f"{target.isoformat()}_{stamp}.md"
        path.replace(archived_path)
        archived = str(archived_path)

    path.write_text(markdown, encoding="utf-8")
    return {"path": str(path), "archived": archived}


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
