"""Digest assembly: window resolution, compression, redaction and grouping."""

from __future__ import annotations

import json
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
from .workspace import (
    ALL,
    OTHER,
    Classifier,
    allowed_workspaces,
    effective_profile,
    output_dir_for,
    whatsapp_target_for,
)

VALID_OUTPUTS = {"markdown", "whatsapp", "both", "none"}
VALID_WORKSPACE_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


class ScopeError(ValueError):
    """Raised when a requested workspace is outside the active profile."""


def resolve_tz(config: Config) -> ZoneInfo:
    if config.timezone and config.timezone.lower() != "local":
        try:
            return ZoneInfo(config.timezone)
        except Exception:
            pass
    # Prefer the system zone when resolvable, so DST is handled.
    try:
        localtime = os.path.realpath("/etc/localtime")
        marker = "/zoneinfo/"
        if marker in localtime:
            return ZoneInfo(localtime.split(marker, 1)[1])
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


def window_range(date_from: str, date_to: str, tz) -> tuple[datetime, datetime]:
    start_day = parse_date(date_from, tz)
    end_day = parse_date(date_to, tz)
    if end_day < start_day:
        start_day, end_day = end_day, start_day
    start, _ = window_for(start_day, tz)
    _, end = window_for(end_day, tz)
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


def _selected_workspaces(
    config: Config, workspace: str | None, profile: str | None
) -> tuple[list[str] | None, str]:
    """Return (selected_workspaces, effective_profile).

    ``None`` means "all workspaces". Raises ``ScopeError`` when a requested
    workspace is outside the active profile.
    """
    active = (profile or effective_profile(config)).lower()
    allowed = allowed_workspaces(config)
    if workspace:
        name = workspace.strip().lower()
        if name not in allowed:
            raise ScopeError(
                f"workspace '{workspace}' não é permitido no perfil '{active}' "
                f"(permitidos: {', '.join(allowed)})"
            )
        if name == ALL:
            return None, active
        return [name], active
    if active != ALL:
        return [active], active
    return None, active


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


def _overlap_minutes(session: Session, start: datetime, end: datetime) -> int:
    lo = max(session.started_at, start)
    hi = min(session.updated_at, end)
    return max(1, int((hi - lo).total_seconds() // 60))


def _compact_session(
    session: Session,
    start: datetime,
    end: datetime,
    redactor: Redactor,
    config: Config,
    classifier: Classifier,
) -> dict[str, Any] | None:
    in_window = [e for e in session.events if start <= e.ts < end]
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

    classification = classifier.classify(session.project_path)
    return {
        "source": session.source,
        "id": session.id,
        "title": redactor.text(session.title),
        "project": redactor.path(session.project_path),
        "workspace": classification.workspace,
        "model": session.model,
        "agent": session.agent,
        "started": session.started_at.astimezone(resolve_tz(config)).isoformat(),
        "updated": session.updated_at.astimezone(resolve_tz(config)).isoformat(),
        "duration_min": _overlap_minutes(session, start, end),
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


def _matches_query(item: dict[str, Any], needle: str) -> bool:
    haystack = " ".join(
        [
            item.get("title", ""),
            item.get("project", ""),
            " ".join(item.get("prompts", [])),
            " ".join(item.get("files", [])),
            " ".join(item.get("todos", [])),
        ]
    ).lower()
    return needle in haystack


def _enforce_budget(digest: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    """Shrink the digest until it fits ``max_bytes``; flag truncation."""
    def size() -> int:
        return len(json.dumps(digest, ensure_ascii=False).encode("utf-8"))

    if size() <= max_bytes:
        digest["truncated"] = False
        return digest

    digest["truncated"] = True
    # Degrade gracefully: commands -> subagents -> files -> prompts.
    for project in digest["projects"]:
        for session in project["sessions"]:
            session["commands"] = []
    if size() <= max_bytes:
        return digest
    for project in digest["projects"]:
        for session in project["sessions"]:
            session["subagents"] = []
    if size() <= max_bytes:
        return digest
    for project in digest["projects"]:
        for session in project["sessions"]:
            session["files"] = session["files"][:10]
    if size() <= max_bytes:
        return digest
    while size() > max_bytes:
        changed = False
        for project in digest["projects"]:
            for session in project["sessions"]:
                if len(session["prompts"]) > 1:
                    session["prompts"].pop()
                    changed = True
        if not changed:
            break
    return digest


def build_digest(
    config: Config,
    date_value: str | None = None,
    sources: list[str] | None = None,
    output_override: str | None = None,
    cache: Optional[Cache] = None,
    workspace: str | None = None,
    project: str | None = None,
    query: str | None = None,
    profile: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    tz = resolve_tz(config)
    if start is None or end is None:
        target = parse_date(date_value, tz)
        start, end = window_for(target, tz)
    else:
        target = start.astimezone(tz).date()

    selected, active_profile = _selected_workspaces(config, workspace, profile)
    classifier = Classifier(config)
    redactor = Redactor(config.redact.enabled, config.redact.extra_patterns)
    if cache is not None and getattr(cache, "_redactor", None) is None:
        cache.set_redactor(redactor)

    def path_filter(path: str) -> bool:
        cls = classifier.classify(path)
        if selected is not None and cls.workspace not in selected:
            return False
        if project:
            target_path = os.path.normpath(os.path.expanduser(project))
            current = os.path.normpath(os.path.expanduser(path))
            if not (current == target_path or current.startswith(target_path + os.sep)):
                return False
        return True

    adapters = build_adapters(config, only=sources)
    raw: list[Session] = []
    warnings: list[dict[str, str]] = []
    sources_used: list[str] = []
    for adapter in adapters:
        try:
            raw.extend(adapter.collect(start, end, cache, path_filter))
            sources_used.append(adapter.name)
        except Exception as exc:  # a broken store must not sink the whole digest
            warnings.append({"source": adapter.name, "error": str(exc)[:200]})

    top = rollup(raw)
    needle = (query or "").strip().lower()
    compact: list[dict[str, Any]] = []
    for session in top:
        item = _compact_session(session, start, end, redactor, config, classifier)
        if item is None:
            continue
        if selected is not None and item["workspace"] not in selected:
            continue
        if needle and not _matches_query(item, needle):
            continue
        compact.append(item)

    projects: dict[str, dict[str, Any]] = {}
    for item in compact:
        key = item["project"] or "(sem projeto)"
        bucket = projects.setdefault(
            key,
            {
                "name": os.path.basename(item["project"].rstrip("/")) or key,
                "path": key,
                "workspace": item["workspace"],
                "sessions": [],
            },
        )
        bucket["sessions"].append(item)

    project_list = sorted(
        projects.values(),
        key=lambda p: sum(s["duration_min"] for s in p["sessions"]),
        reverse=True,
    )

    project_paths = [item["project"] for item in compact]
    git_data, git_attributed = collect_git(
        project_paths, start, end, config, selected, classifier
    )

    totals = {
        "sessions": len(compact),
        "prompts": sum(len(s["prompts"]) for s in compact),
        "files": len({f for s in compact for f in s["files"]}),
        "cost": round(sum(s["cost"] for s in compact), 4),
        "tokens_input": sum(s["tokens_input"] for s in compact),
        "tokens_output": sum(s["tokens_output"] for s in compact),
    }

    digest: dict[str, Any] = {
        "date": target.isoformat(),
        "window": {
            "start": start.astimezone(tz).isoformat(),
            "end": end.astimezone(tz).isoformat(),
        },
        "workspace": workspace or (active_profile if active_profile != ALL else ALL),
        "profile": active_profile,
        "outputs": resolve_outputs(config, output_override),
        "whatsapp_target": whatsapp_target_for(config, workspace or active_profile),
        "sources": sources_used,
        "sources_failed": warnings,
        "git_attributed": git_attributed,
        "totals": totals,
        "projects": project_list,
        "empty": len(compact) == 0,
        "git": {
            redactor.path(repo): [
                {
                    "hash": c["hash"][:8],
                    "subject": redactor.text(c["subject"]),
                    "author": c.get("author", ""),
                    "attributed": c.get("attributed", False),
                    "files": [redactor.path(f) for f in c["files"][:10]],
                }
                for c in commits
            ]
            for repo, commits in git_data.items()
        },
    }

    if config.load_error:
        digest["config_error"] = config.load_error
    if git_data and not git_attributed:
        digest.setdefault("warnings", []).append(
            "git: identidade não configurada; commits podem incluir trabalho de outras pessoas "
            "(defina [git] author ou git config user.email)"
        )

    return _enforce_budget(digest, config.limits.max_output_bytes)


def list_sessions(
    config: Config,
    period: str | None = None,
    source: str | None = None,
    cache: Optional[Cache] = None,
    workspace: str | None = None,
    project: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    sources = [source] if source and source != "all" else None
    digest = build_digest(
        config,
        period,
        sources,
        "none",
        cache,
        workspace=workspace,
        project=project,
        profile=profile,
    )
    out = []
    for proj in digest["projects"]:
        for session in proj["sessions"]:
            out.append(
                {
                    "id": session["id"],
                    "source": session["source"],
                    "project": proj["path"],
                    "workspace": session["workspace"],
                    "title": session["title"],
                    "started": session["started"],
                    "duration_min": session["duration_min"],
                    "prompts": len(session["prompts"]),
                    "files": session["files"],
                }
            )
    out.sort(key=lambda s: s["started"], reverse=True)
    return {
        "date": digest["date"],
        "workspace": digest["workspace"],
        "count": len(out),
        "sessions": out,
    }


def search_sessions(
    config: Config,
    query: str,
    source: str | None = None,
    cache: Optional[Cache] = None,
    since: str | None = None,
    until: str | None = None,
    workspace: str | None = None,
    project: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    if not query:
        return {"query": query, "count": 0, "results": []}
    sources = [source] if source and source != "all" else None
    tz = resolve_tz(config)
    if since or until:
        start, end = window_range(since or "today", until or (since or "today"), tz)
        digest = build_digest(
            config,
            None,
            sources,
            "none",
            cache,
            workspace=workspace,
            project=project,
            profile=profile,
            start=start,
            end=end,
        )
    else:
        digest = build_digest(
            config,
            "today",
            sources,
            "none",
            cache,
            workspace=workspace,
            project=project,
            profile=profile,
        )
    needle = query.lower()
    results = []
    for proj in digest["projects"]:
        for session in proj["sessions"]:
            if _matches_query(session, needle):
                results.append(
                    {
                        "id": session["id"],
                        "source": session["source"],
                        "project": proj["path"],
                        "workspace": session["workspace"],
                        "title": session["title"],
                        "started": session["started"],
                    }
                )
    return {"query": query, "count": len(results), "results": results}


def _safe_workspace_name(config: Config, workspace: str | None, profile: str | None) -> str | None:
    active = (profile or effective_profile(config)).lower()
    if not workspace:
        # In a restricted profile, default to that workspace instead of the
        # shared root, so a write never escapes the profile scope.
        return None if active == ALL else active
    name = workspace.strip().lower()
    if name in (ALL, ""):
        return None
    if name == OTHER:
        raise ScopeError("não é possível gravar diretamente no workspace 'other'")
    if not VALID_WORKSPACE_NAME.match(name):
        raise ScopeError(f"nome de workspace inválido: {workspace}")
    if active != ALL and name != active:
        raise ScopeError(
            f"perfil '{active}' não pode gravar no workspace '{name}'"
        )
    return name


def _prune_archive(archive_dir, retention_days: int, now: datetime) -> int:
    if retention_days <= 0 or not archive_dir.is_dir():
        return 0
    cutoff = now - timedelta(days=retention_days)
    removed = 0
    for entry in archive_dir.iterdir():
        if entry.is_file():
            try:
                if datetime.fromtimestamp(entry.stat().st_mtime, tz=now.tzinfo) < cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


def write_digest(
    config: Config,
    markdown: str,
    target_date: str | None = None,
    workspace: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Write ``<workspace>/<date>.md`` and archive the previous version.

    The workspace is validated against the active profile so a restricted
    profile cannot write outside its scope. Files are written ``0600``.
    """
    name = _safe_workspace_name(config, workspace, profile)
    tz = resolve_tz(config)
    target = parse_date(target_date, tz)
    out_dir = output_dir_for(config, name)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(out_dir, 0o700)
        if out_dir != config.resolved_output_dir():
            os.chmod(config.resolved_output_dir(), 0o700)
    except OSError:
        pass

    path = out_dir / f"{target.isoformat()}.md"
    archived = None
    if path.exists():
        archive_dir = out_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(archive_dir, 0o700)
        except OSError:
            pass
        stamp = datetime.now(tz).strftime("%H%M%S_%f")
        archived_path = archive_dir / f"{target.isoformat()}_{stamp}.md"
        path.replace(archived_path)
        archived = str(archived_path)
        removed = _prune_archive(archive_dir, config.limits.archive_retention_days, datetime.now(tz))
    else:
        removed = 0

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(markdown)
    return {"path": str(path), "archived": archived, "pruned": removed, "workspace": name}


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
