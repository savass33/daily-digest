"""daily-digest MCP server (stdio).

Exposes the local digest to any MCP-compatible agent. The host agent's own
model performs the summarization; this server only collects, redacts and
returns compact data, and writes the markdown file on request.
"""

from __future__ import annotations

from typing import Any

try:  # mcp 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[assignment]

from .cache import Cache
from .config import load_config
from .digest import build_digest, list_sessions as _list, search_sessions as _search, write_digest as _write

mcp = _Server("daily-digest")


def _sources(value: str | None) -> list[str] | None:
    if not value or value.strip().lower() in ("all", "", "auto"):
        return None
    return [s.strip() for s in value.split(",") if s.strip()]


@mcp.tool()
def collect_digest(
    date: str = "today",
    sources: str = "all",
    output: str = "",
) -> dict[str, Any]:
    """Collect the day's AI coding sessions across opencode, [CC] and Codex.

    Returns compact, secret-redacted data grouped by project: per-session
    prompts, files touched, commands, todos, subagents, plus git commits.
    The host agent should summarize this into Objetivo / Feito / Decisoes /
    Bloqueios / Proximos passos.

    Args:
        date: "today", "yesterday" or an ISO date (YYYY-MM-DD).
        sources: "all" or a comma list of opencode,claude,codex.
        output: "" (use config), or markdown, whatsapp, both, none.
    """
    config = load_config()
    cache = Cache(config.resolved_cache_path())
    try:
        return build_digest(config, date, _sources(sources), output or None, cache)
    finally:
        cache.close()


@mcp.tool()
def list_sessions(period: str = "today", source: str = "all") -> dict[str, Any]:
    """List the day's sessions (id, source, project, title, files)."""
    config = load_config()
    cache = Cache(config.resolved_cache_path())
    try:
        return _list(config, period, source, cache)
    finally:
        cache.close()


@mcp.tool()
def search_sessions(query: str, source: str = "all") -> dict[str, Any]:
    """Search sessions for a keyword across agents."""
    config = load_config()
    cache = Cache(config.resolved_cache_path())
    try:
        return _search(config, query, source, cache)
    finally:
        cache.close()


@mcp.tool()
def write_digest(markdown: str, date: str = "today") -> dict[str, Any]:
    """Write the provided markdown to ~/daily/<date>.md.

    Any existing file for that date is archived under ~/daily/archive/ first,
    so running the digest more than once per day keeps a versioned history.
    """
    config = load_config()
    return _write(config, markdown, date)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
