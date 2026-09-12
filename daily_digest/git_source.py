"""Git collector.

Only repositories that had an AI session during the window are inspected, so
the digest stays coherent with the sessions it reports.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from typing import Any

from .config import Config, expand


def _find_repo(path: str) -> str | None:
    current = os.path.abspath(expand(path))
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _run_log(repo: str, start: datetime, end: datetime, author: str) -> list[dict[str, Any]]:
    cmd = [
        "git",
        "-C",
        repo,
        "log",
        f"--since={start.isoformat()}",
        f"--until={end.isoformat()}",
        "--no-merges",
        "--pretty=format:@@%H|%an|%s",
        "--name-only",
    ]
    if author:
        cmd.insert(3, f"--author={author}")
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
            parts = body.split("|", 2)
            current = {
                "hash": parts[0] if parts else "",
                "author": parts[1] if len(parts) > 1 else "",
                "subject": parts[2] if len(parts) > 2 else "",
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
) -> dict[str, list[dict[str, Any]]]:
    """Return ``{repo_path: [commits]}`` for repos that had sessions."""
    if not config.git.enabled:
        return {}
    author = os.environ.get("DAILY_DIGEST_GIT_AUTHOR", "")
    repos: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for project in project_paths:
        if not project:
            continue
        repo = _find_repo(project)
        if not repo or repo in seen:
            continue
        seen.add(repo)
        commits = _run_log(repo, start, end, author)
        if commits:
            repos[repo] = commits
    return repos
