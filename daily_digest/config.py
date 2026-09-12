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


@dataclass
class Config:
    timezone: str = "local"
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
    loaded_from: str = ""

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
    """Load configuration, falling back to defaults when the file is absent."""
    cfg = Config()
    candidate = path or os.environ.get("DAILY_DIGEST_CONFIG") or DEFAULT_CONFIG_PATH
    candidate = expand(candidate)
    if os.path.isfile(candidate):
        with open(candidate, "rb") as fh:
            raw = tomllib.load(fh)
        general = raw.get("general", {})
        if "timezone" in general:
            cfg.timezone = general["timezone"]
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
        cfg.loaded_from = candidate
    return cfg
