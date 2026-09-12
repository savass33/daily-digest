"""Configuration loading for daily-digest.

Reads ``~/.config/daily-digest/config.toml`` (or ``$DAILY_DIGEST_CONFIG``) and
merges it over built-in defaults. Only stdlib is used (``tomllib``, Python 3.11+).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = "~/.config/daily-digest/config.toml"


def expand(path: str) -> str:
    """Expand ``~`` and environment variables in a path."""
    return os.path.expanduser(os.path.expandvars(path))


@dataclass
class SourceConfig:
    # "auto" enables the source only when its store exists.
    enabled: str = "auto"
    db: str = "~/.local/share/opencode/opencode.db"
    dir: str = ""


@dataclass
class GitConfig:
    enabled: bool = True
    roots: list[str] = field(default_factory=lambda: ["~/Documentos"])
    author: str = ""


@dataclass
class WorkspaceConfig:
    name: str = ""
    aliases: list[str] = field(default_factory=list)
    match_paths: list[str] = field(default_factory=list)
    match_remotes: list[str] = field(default_factory=list)
    output_dir: str = ""
    whatsapp_target: str = ""


@dataclass
class RedactConfig:
    enabled: bool = True
    extra_patterns: list[str] = field(default_factory=list)


@dataclass
class OutputConfig:
    markdown: bool = True
    whatsapp: bool = False
    review_before_send: bool = True


@dataclass
class DigestLimits:
    max_prompts_per_session: int = 6
    max_files_per_session: int = 40
    max_commands_per_session: int = 12
    max_output_bytes: int = 24000
    archive_retention_days: int = 90


@dataclass
class Config:
    timezone: str = "local"
    profile: str = ""
    output_dir: str = "~/daily"
    cache_path: str = "~/.cache/daily-digest/cache.db"
    opencode: SourceConfig = field(
        default_factory=lambda: SourceConfig(
            db="~/.local/share/opencode/opencode.db"
        )
    )
    claude: SourceConfig = field(
        default_factory=lambda: SourceConfig(dir="~/.claude/projects")
    )
    codex: SourceConfig = field(default_factory=lambda: SourceConfig(dir="~/.codex"))
    git: GitConfig = field(default_factory=GitConfig)
    redact: RedactConfig = field(default_factory=RedactConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    limits: DigestLimits = field(default_factory=DigestLimits)
    workspaces: dict[str, WorkspaceConfig] = field(default_factory=dict)
    loaded_from: str = ""
    load_error: str = ""

    def resolved_output_dir(self) -> Path:
        return Path(expand(self.output_dir))

    def resolved_cache_path(self) -> Path:
        return Path(expand(self.cache_path))


def _apply(target: Any, data: dict[str, Any]) -> None:
    """Apply a nested dict onto a dataclass instance, ignoring unknown keys."""
    valid = {f.name for f in fields(target)}
    for key, value in data.items():
        if key not in valid:
            continue
        current = getattr(target, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _apply(current, value)
        else:
            setattr(target, key, value)


def load_config(path: str | None = None) -> Config:
    """Load configuration, falling back to defaults when the file is absent.

    A malformed config never crashes the server: the error is recorded in
    ``load_error`` and built-in defaults are used.
    """
    cfg = Config()
    candidate = path or os.environ.get("DAILY_DIGEST_CONFIG") or DEFAULT_CONFIG_PATH
    candidate = expand(candidate)
    if os.path.isfile(candidate):
        try:
            with open(candidate, "rb") as fh:
                raw = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            cfg.load_error = f"{candidate}: {exc}"
            cfg.loaded_from = candidate
            return cfg
        general = raw.get("general", {})
        if "timezone" in general:
            cfg.timezone = general["timezone"]
        if "profile" in general:
            cfg.profile = general["profile"]
        if "output_dir" in general:
            cfg.output_dir = general["output_dir"]
        if "cache_path" in general:
            cfg.cache_path = general["cache_path"]
        sources = raw.get("sources", {})
        for name in ("opencode", "claude", "codex"):
            if name in sources and isinstance(sources[name], dict):
                _apply(getattr(cfg, name), sources[name])
        if "git" in raw:
            _apply(cfg.git, raw["git"])
        if "redact" in raw:
            _apply(cfg.redact, raw["redact"])
        if "output" in raw:
            _apply(cfg.output, raw["output"])
        if "digest" in raw:
            _apply(cfg.limits, raw["digest"])
        workspaces = raw.get("workspaces", {})
        if isinstance(workspaces, dict):
            for ws_name, ws_data in workspaces.items():
                ws = WorkspaceConfig(name=ws_name)
                if isinstance(ws_data, dict):
                    _apply(ws, ws_data)
                cfg.workspaces[ws_name] = ws
        cfg.loaded_from = candidate

    env_profile = os.environ.get("DAILY_DIGEST_PROFILE")
    if env_profile:
        cfg.profile = env_profile
    return cfg
