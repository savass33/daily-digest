"""Adapter interface for reading agent session stores."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..cache import Cache
from ..config import Config
from ..normalize import Session


class SessionAdapter:
    name = "base"

    def __init__(self, config: Config):
        self.config = config

    def available(self) -> bool:
        raise NotImplementedError

    def collect(
        self, start: datetime, end: datetime, cache: Optional[Cache] = None
    ) -> list[Session]:
        raise NotImplementedError
