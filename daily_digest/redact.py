"""Secret and path redaction.

Runs in the collector, *before* any content reaches the host agent, so the
digest never carries API keys, tokens, or passwords into a model context.
"""

from __future__ import annotations

import os
import re

REDACTED = "***REDACTED***"
HOME_MARKER = "~"

# Assignment-style secrets: FOO_SECRET=..., "apiKey": "...", token: xxx
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (
      [A-Z0-9_]*                 # optional prefix, e.g. JWT_, AWS_
      (?:SECRET|TOKEN|PASSWORD|PASSWD|PWD|APIKEY|API_KEY|PRIVATE_KEY|
         ACCESS_KEY|SECRET_KEY|CREDENTIAL|AUTH|BEARER)
      [A-Z0-9_]*
    )
    (\s*[:=]\s*)
    (["']?)
    ([^\s"',;]{6,})
    \3
    """,
)

# Known token prefixes and shapes.
_PREFIXED = re.compile(
    r"""(?x)
    (?:
      sk-ant-[A-Za-z0-9_\-]{10,} |
      sk-[A-Za-z0-9_\-]{16,}       |
      vbk_[A-Za-z0-9_\-]{10,}      |
      ghp_[A-Za-z0-9_\-]{20,}      |
      gho_[A-Za-z0-9_\-]{20,}      |
      ghs_[A-Za-z0-9_\-]{20,}      |
      github_pat_[A-Za-z0-9_]{20,} |
      xox[baprs]-[A-Za-z0-9\-]{10,} |
      AKIA[0-9A-Z]{16}             |
      AIza[0-9A-Za-z_\-]{30,}      |
      eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}
    )
    """,
)

_BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._\-]{12,})")
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_URL_CREDENTIALS = re.compile(r"(?i)([a-z][a-z0-9+.\-]*://[^/\s:@]+):([^@\s/]+)@")


class Redactor:
    def __init__(self, enabled: bool = True, extra_patterns: list[str] | None = None):
        self.enabled = enabled
        self.extra = [
            re.compile(p) for p in (extra_patterns or []) if p
        ]

    def text(self, value: str | None) -> str:
        if not value or not self.enabled:
            return value or ""
        out = value
        out = _PRIVATE_KEY.sub(REDACTED, out)
        out = _BEARER.sub(lambda m: m.group(1) + REDACTED, out)
        out = _URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}:{REDACTED}@", out)
        out = _ASSIGNMENT.sub(
            lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}{m.group(3)}",
            out,
        )
        out = _PREFIXED.sub(REDACTED, out)
        for pattern in self.extra:
            out = pattern.sub(REDACTED, out)
        return out

    def path(self, value: str | None) -> str:
        if not value:
            return ""
        if not self.enabled:
            return value
        home = os.path.expanduser("~")
        if home and value.startswith(home):
            return HOME_MARKER + value[len(home):]
        return value
