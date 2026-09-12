"""Workspace classification and scope resolution.

A *workspace* (e.g. "work" / "personal") groups projects so a digest never
mixes personal sessions into professional ones and vice versa. Classification
is deterministic and runs in the collector, before any content reaches an agent.

Precedence for a project path:

1. an explicit ``.daily-digest.toml`` marker in the repo (``context = "work"``)
2. ``match_paths`` globs
3. ``match_remotes`` globs (normalized git remote)
4. ``other``

A path that matches more than one workspace is reported as a conflict and is
not assigned to any named workspace.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config, WorkspaceConfig, expand

OTHER = "other"
ALL = "all"

_REMOTE_SSH = re.compile(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$")
_REMOTE_URL = re.compile(r"^[a-z][a-z0-9+.\-]*://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$")

_remote_cache: dict[str, str] = {}


def normalize_remote(url: str) -> str:
    """Normalize a git remote URL to ``host/org/repo`` (no scheme/credentials)."""
    if not url:
        return ""
    url = url.strip()
    match = _REMOTE_SSH.match(url) or _REMOTE_URL.match(url)
    if not match:
        return url
    host, path = match.group(1), match.group(2)
    return f"{host}/{path}".strip("/")


def find_repo_root(path: str) -> str | None:
    current = os.path.abspath(expand(path))
    if not os.path.isdir(current):
        current = os.path.dirname(current)
    while True:
        if os.path.isdir(os.path.join(current, ".git")) or os.path.isfile(
            os.path.join(current, ".git")
        ):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def git_remote(repo_root: str) -> str:
    if not repo_root:
        return ""
    if repo_root in _remote_cache:
        return _remote_cache[repo_root]
    value = ""
    try:
        result = subprocess.run(
            ["git", "-C", repo_root, "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            value = normalize_remote(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        value = ""
    _remote_cache[repo_root] = value
    return value


def find_marker_root(path: str) -> str | None:
    """Walk up from ``path`` looking for a ``.daily-digest.toml`` marker."""
    current = os.path.abspath(expand(path))
    if not os.path.isdir(current):
        current = os.path.dirname(current)
    while True:
        if os.path.isfile(os.path.join(current, ".daily-digest.toml")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def read_marker(repo_root: str | None) -> str | None:
    if not repo_root:
        return None
    marker = os.path.join(repo_root, ".daily-digest.toml")
    if not os.path.isfile(marker):
        return None
    try:
        import tomllib

        with open(marker, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError):
        return None
    context = data.get("context") or data.get("workspace")
    return str(context).strip() if context else None


def _match_path(path: str, pattern: str) -> bool:
    path = os.path.normpath(expand(path))
    pattern = os.path.normpath(expand(pattern))
    if pattern.endswith("/**") or pattern.endswith(os.sep + "**"):
        base = pattern[:-3]
        return path == base or path.startswith(base + os.sep)
    if pattern.endswith("/"):
        base = pattern[:-1]
        return path == base or path.startswith(base + os.sep)
    return fnmatch.fnmatch(path, pattern)


def _match_remote(remote: str, pattern: str) -> bool:
    if not remote:
        return False
    pattern = pattern.replace("/**", "/*")
    return fnmatch.fnmatch(remote, pattern) or fnmatch.fnmatch(
        remote, pattern + "/*"
    )


@dataclass
class Classification:
    workspace: str
    matched_by: str = ""
    conflict_with: list[str] = field(default_factory=list)

    @property
    def is_other(self) -> bool:
        return self.workspace == OTHER


class Classifier:
    """Classify project paths into workspaces using config rules."""

    def __init__(self, config: Config):
        self.workspaces: dict[str, WorkspaceConfig] = dict(config.workspaces)
        self._cache: dict[str, Classification] = {}

    def classify(self, project_path: str) -> Classification:
        key = project_path or ""
        if key in self._cache:
            return self._cache[key]
        result = self._classify(key)
        self._cache[key] = result
        return result

    def _classify(self, project_path: str) -> Classification:
        if not project_path:
            return Classification(OTHER)
        repo_root = find_repo_root(project_path)

        marker = read_marker(find_marker_root(project_path))
        if marker and marker in self.workspaces:
            return Classification(marker, "marker")

        by_path = self._match_any(project_path, "match_paths")
        remote = git_remote(repo_root) if repo_root else ""
        by_remote = self._match_any(remote, "match_remotes", is_remote=True)

        candidates = sorted(set(by_path) | set(by_remote))
        if not candidates:
            if marker:
                return Classification(OTHER, "marker-unknown")
            return Classification(OTHER)
        if len(candidates) == 1:
            matched_by = "path" if by_path else "remote"
            return Classification(candidates[0], matched_by)
        return Classification(OTHER, "conflict", candidates)

    def _match_any(
        self, value: str, attr: str, is_remote: bool = False
    ) -> list[str]:
        matches = []
        for name, ws in self.workspaces.items():
            patterns = getattr(ws, attr, []) or []
            for pattern in patterns:
                if is_remote:
                    if _match_remote(value, pattern):
                        matches.append(name)
                        break
                elif _match_path(value, pattern):
                    matches.append(name)
                    break
        return matches

    def aliases_of(self, workspace: str) -> list[str]:
        ws = self.workspaces.get(workspace)
        return list(ws.aliases) if ws else []


@dataclass
class Scope:
    workspace: str | None = None
    project: str | None = None
    query: str | None = None
    candidates: list[str] = field(default_factory=list)
    needs_clarification: bool = False
    profile: str = ALL
    allowed: list[str] = field(default_factory=list)
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace": self.workspace,
            "project": self.project,
            "query": self.query,
            "candidates": self.candidates,
            "needs_clarification": self.needs_clarification,
            "profile": self.profile,
            "allowed": self.allowed,
            "text": self.text,
        }


def effective_profile(config: Config) -> str:
    return (config.profile or ALL).strip().lower() or ALL


def allowed_workspaces(config: Config) -> list[str]:
    profile = effective_profile(config)
    if profile == ALL:
        return [ALL] + list(config.workspaces.keys()) + [OTHER]
    if profile in config.workspaces:
        # A restrictive profile locks to exactly that workspace (+ other is not
        # allowed: the whole point is isolation).
        return [profile]
    return [profile]


def _workspace_terms(config: Config) -> dict[str, list[str]]:
    """Build a map term -> workspace from names, aliases and path basenames."""
    terms: dict[str, list[str]] = {}
    for name, ws in config.workspaces.items():
        values = [name] + list(ws.aliases or [])
        for pattern in ws.match_paths or []:
            base = os.path.basename(os.path.normpath(expand(pattern)).rstrip("/"))
            if base and base != "*" and base != "**":
                values.append(base)
        for value in values:
            key = value.lower().strip()
            if key:
                terms.setdefault(key, []).append(name)
    return terms


def resolve_scope(
    text: str, config: Config, profile: str | None = None
) -> Scope:
    """Map free text (or a structured keyword) to a workspace/project scope.

    Only config and known paths/remotes are consulted -- never session content.
    Ambiguous matches return candidates and ``needs_clarification`` so the agent
    can ask before collecting anything.
    """
    active_profile = (profile or effective_profile(config)).lower()
    allowed = allowed_workspaces(config) if profile is None else (
        [active_profile] if active_profile != ALL else allowed_workspaces(config)
    )
    scope = Scope(profile=active_profile, allowed=allowed, text=text or "")
    normalized = (text or "").strip().lower()
    if not normalized:
        scope.workspace = None
        return scope

    # A bare workspace name or alias.
    if normalized in config.workspaces and normalized in allowed:
        scope.workspace = normalized
        return scope

    tokens = set(re.findall(r"[a-z0-9_\-]+", normalized))
    terms = _workspace_terms(config)
    matched: list[str] = []
    denied: list[str] = []
    for term, workspaces in terms.items():
        if term in tokens or term in normalized:
            for ws in workspaces:
                if ws in allowed:
                    if ws not in matched:
                        matched.append(ws)
                elif ws not in denied:
                    denied.append(ws)

    if len(matched) == 1:
        scope.workspace = matched[0]
        return scope
    if len(matched) > 1:
        scope.candidates = matched
        scope.needs_clarification = True
        return scope
    if denied and not matched:
        # The user named a workspace outside the active profile. Never silently
        # fall back: surface it so the agent can explain the restriction.
        scope.candidates = denied
        scope.needs_clarification = True
        return scope

    # No workspace matched: keep the text as a free query, scoped to allowed.
    if active_profile != ALL:
        scope.workspace = active_profile
    scope.query = text.strip()
    return scope


def output_dir_for(config: Config, workspace: str | None) -> Path:
    """Resolve the output directory for a workspace (``all`` -> base dir)."""
    if not workspace or workspace == ALL or workspace == OTHER:
        return config.resolved_output_dir()
    ws = config.workspaces.get(workspace)
    if ws and ws.output_dir:
        return Path(expand(ws.output_dir))
    return config.resolved_output_dir() / workspace


def whatsapp_target_for(config: Config, workspace: str | None) -> str:
    if not workspace:
        return ""
    ws = config.workspaces.get(workspace)
    return ws.whatsapp_target if ws else ""


def workspace_names(config: Config) -> list[str]:
    return list(config.workspaces.keys())
