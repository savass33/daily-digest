"""Adapter registry with auto-detection.

Only adapters whose store actually exists are enabled, so the same install
works on a machine with opencode only, [CC] only, Codex only, or any mix.
"""

from __future__ import annotations

from ..config import Config
from .base import SessionAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .opencode import OpencodeAdapter

ALL_ADAPTERS = (OpencodeAdapter, ClaudeAdapter, CodexAdapter)


def build_adapters(config: Config, only: list[str] | None = None) -> list[SessionAdapter]:
    """Return the active adapters.

    ``only`` restricts to the named sources. Otherwise ``enabled="auto"``
    activates a source when its store exists, and ``enabled=true`` forces it.
    """
    adapters: list[SessionAdapter] = []
    for cls in ALL_ADAPTERS:
        name = cls.name
        if only and name not in only:
            continue
        setting = getattr(config, name)
        adapter = cls(config)
        if setting.enabled is True or str(setting.enabled).lower() == "true":
            adapters.append(adapter)
        elif str(setting.enabled).lower() in ("auto", "1", "yes"):
            if adapter.available():
                adapters.append(adapter)
    return adapters
