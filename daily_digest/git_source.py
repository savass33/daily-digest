"""Git collector.

Only repositories that had an AI session during the window are inspected, so
the digest stays coherent with the sessions it reports. Commits are filtered by
the same workspace scope and, when an author identity is known, attributed to
the user. Without an identity we never claim authorship: commits are collected
but marked ``attributed = false``.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from typing import Any, Optional

from .config import Config, expand
from .workspace import Classifier


def _find_repo(path: str) -> str | None:
    current = os.path.abspath(expand(path))
    if not os.path.isdir(current):
        current = os.path.dirname(current)
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def resolve_author(config: Config) -> str:
    """Resolve the git author identity: config, then env, then git config."""
    if config.git.author:
        return config.git.author
    env = os.environ.get("DAILY_DIGEST_GIT_AUTHOR")
    if env:
        return env
    try:
        result = subprocess.run(
            ["git", "config", "--global", "--get", "user.email"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _run_log(
    repo: str, start: datetime, end: datetime, author: str, attributed: bool
) -> list[dict[str, Any]]:
    cmd = [
        "git",
        "-C",
        repo,
        "log",
        f"--since={start.isoformat()}",
        f"--until={end.isoformat()}",
        "--no-merges",
        "--pretty=format:@@%H|%an|%ae|%s",
        "--name-only",
    ]
    if author:
        cmd.insert(4, f"--author={author}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []

    commits: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in result.stdout.splitlines():
        if line.startswith("@@"):
            body = line[2:]
            parts = body.split("|", 3)
            current = {
                "hash": parts[0] if parts else "",
                "author": parts[1] if len(parts) > 1 else "",
                "email": parts[2] if len(parts) > 2 else "",
                "subject": parts[3] if len(parts) > 3 else "",
                "attributed": attributed,
                "files": [],
            }
            commits.append(current)
        elif line.strip() and current is not None:
            current["files"].append(line.strip())
    return commits


def collect(
    project_paths: list[str],
    start: datetime,
    end: datetime,
    config: Config,
    selected: Optional[list[str]] = None,
    classifier: Optional[Classifier] = None,
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    """Return ``({repo_path: [commits]}, attributed)``.

    ``selected`` is the workspace scope (``None`` = all). Repos outside the
    scope are skipped. ``attributed`` is True only when an author identity was
    used to filter commits.
    """
    if not config.git.enabled:
        return {}, True
    author = resolve_author(config)
    attributed = bool(author)
    repos: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for project in project_paths:
        if not project:
            continue
        repo = _find_repo(expand(project))
        if not repo or repo in seen:
            continue
        seen.add(repo)
        if selected is not None and classifier is not None:
            if classifier.classify(repo).workspace not in selected:
                continue
        commits = _run_log(repo, start, end, author, attributed)
        if commits:
            repos[repo] = commits
    return repos, attributed
