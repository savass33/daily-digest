"""Tests for _compact_session window behaviour (title-of-the-day, empty stubs)."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daily_digest.config import Config  # noqa: E402
from daily_digest.digest import _compact_session  # noqa: E402
from daily_digest.normalize import PROMPT, Event, Session  # noqa: E402
from daily_digest.redact import Redactor  # noqa: E402
from daily_digest.workspace import Classifier  # noqa: E402

DAY = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
START = DAY.replace(hour=0, minute=0, second=0)
END = START + timedelta(days=1)
PREV_DAY = START - timedelta(days=2)


def make_session(session_id: str, events: list[Event], title: str) -> Session:
    return Session(
        id=session_id,
        source="verboo",
        project_path="/home/u/proj",
        title=title,
        started_at=events[0].ts if events else PREV_DAY,
        updated_at=events[-1].ts if events else DAY,
        events=events,
    )


class CompactSessionTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.redactor = Redactor(False, [])
        self.classifier = Classifier(self.cfg)

    def compact(self, session: Session):
        return _compact_session(
            session, START, END, self.redactor, self.cfg, self.classifier
        )

    def test_multiday_session_takes_title_from_first_prompt_of_the_day(self):
        events = [
            Event(ts=PREV_DAY, kind=PROMPT, text="configura a vpn wireguard"),
            Event(
                ts=DAY,
                kind=PROMPT,
                text="roda a bateria de testes do identificador na gpu",
            ),
        ]
        session = make_session("s1", events, "configura a vpn wireguard")
        item = self.compact(session)
        self.assertIsNotNone(item)
        self.assertEqual(item["title"], "roda a bateria de testes do identificador na gpu")

    def test_session_without_window_events_is_dropped_even_if_updated_inside(self):
        # /resume stub: history copied from another directory, nothing typed today.
        events = [Event(ts=PREV_DAY, kind=PROMPT, text="tarefa antiga")]
        session = make_session("s2", events, "tarefa antiga")
        session.updated_at = DAY  # touched today (resume), but no events today
        self.assertIsNone(self.compact(session))

    def test_session_with_only_tool_events_in_window_is_kept(self):
        events = [
            Event(ts=PREV_DAY, kind=PROMPT, text="tarefa antiga"),
            Event(ts=DAY, kind="tool_call", tool="Bash", text="Bash"),
        ]
        session = make_session("s3", events, "tarefa antiga")
        item = self.compact(session)
        self.assertIsNotNone(item)
        # No prompt inside the window: keep the session, keep the historic title.
        self.assertEqual(item["title"], "tarefa antiga")


if __name__ == "__main__":
    unittest.main()
