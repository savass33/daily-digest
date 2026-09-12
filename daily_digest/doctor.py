"""Diagnostics for daily-digest: run ``python -m daily_digest.doctor``.

Prints a human-readable report about configuration, sources, scope and a quick
dry-run of today's digest, to explain why a digest may look empty or partial.
"""

from __future__ import annotations

import json
import sys

from .adapters import build_adapters
from .cache import Cache
from .config import load_config
from .digest import build_digest
from .git_source import resolve_author
from .redact import Redactor
from .workspace import allowed_workspaces, effective_profile


def _line(ok: bool, label: str, detail: str = "") -> str:
    mark = "OK " if ok else "!! "
    return f"[{mark}] {label}" + (f" — {detail}" if detail else "")


def main() -> int:
    cfg = load_config()
    print("daily-digest doctor")
    print("=" * 50)
    print(_line(not cfg.load_error, "config", cfg.loaded_from or "(defaults)"))
    if cfg.load_error:
        print(_line(False, "config error", cfg.load_error))
    print(_line(True, "profile", effective_profile(cfg)))
    print(_line(True, "workspaces", ", ".join(cfg.workspaces) or "(none)"))
    print(_line(True, "allowed", ", ".join(allowed_workspaces(cfg))))
    print(_line(True, "output_dir", str(cfg.resolved_output_dir())))

    author = resolve_author(cfg)
    print(_line(bool(author), "git author", author or "não definido (commits não atribuídos)"))

    adapters = build_adapters(cfg)
    names = [a.name for a in adapters]
    print(_line(bool(names), "sources ativas", ", ".join(names) or "nenhuma"))

    try:
        cache = Cache(cfg.resolved_cache_path(), Redactor(cfg.redact.enabled, cfg.redact.extra_patterns))
        print(_line(True, "cache", json.dumps(cache.stats(), ensure_ascii=False)))
    except Exception as exc:  # pragma: no cover
        print(_line(False, "cache", str(exc)))
        cache = None

    try:
        digest = build_digest(cfg, "today", None, "none", cache)
        print(_line(True, "digest hoje", f"{digest['totals']['sessions']} sessões, "
                                        f"{len(digest['projects'])} projetos, "
                                        f"workspace={digest['workspace']}"))
        counts: dict[str, int] = {}
        for project in digest["projects"]:
            for session in project["sessions"]:
                counts[session["source"]] = counts.get(session["source"], 0) + 1
        if counts:
            breakdown = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            print(_line(True, "por fonte", breakdown))
        if digest.get("sources_failed"):
            print(_line(False, "sources com erro", json.dumps(digest["sources_failed"], ensure_ascii=False)))
        if digest.get("warnings"):
            for warning in digest["warnings"]:
                print(_line(False, "aviso", warning))
    except Exception as exc:
        print(_line(False, "digest", str(exc)))
    finally:
        if cache is not None:
            cache.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
