"""Normalization tests: sub-agent rollup and model round-trip."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daily_digest.normalize import PROMPT, Event, Session, rollup  # noqa: E402

T0 = datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 9, 12, 10, 5, tzinfo=timezone.utc)


def make(session_id, parent_id=None, agent="build", ts=T0):
    return Session(
        id=session_id,
        source="opencode",
        project_path="/home/u/proj",
        title=f"t-{session_id}",
        agent=agent,
        started_at=ts,
        updated_at=ts,
        parent_id=parent_id,
        events=[Event(ts=ts, kind=PROMPT, text=f"prompt-{session_id}")],
    )


class RollupTest(unittest.TestCase):
    def test_child_folds_into_present_parent(self):
        parent = make("p")
        child = make("c", parent_id="p", agent="explore", ts=T1)
        top = rollup([parent, child])
        self.assertEqual([s.id for s in top], ["p"])
        self.assertTrue(child.is_subagent)
        texts = [e.text for e in parent.events]
        self.assertIn("prompt-c", texts)
        self.assertTrue(any(e.tool == "subagent" for e in parent.events))

    def test_orphan_child_gets_stub_parent(self):
        child = make("c", parent_id="missing", agent="explore", ts=T1)
        top = rollup([child])
        # The child must not surface as a normal top-level session.
        self.assertEqual(len(top), 1)
        root = top[0]
        self.assertEqual(root.id, "missing")
        self.assertIn("prompt-c", [e.text for e in root.events])
        self.assertTrue(child.is_subagent)
        self.assertNotIn("c", [s.id for s in top])

    def test_grandchild_propagates(self):
        parent = make("p")
        child = make("c", parent_id="p", agent="explore", ts=T0)
        grand = make("g", parent_id="c", agent="explore", ts=T1)
        top = rollup([parent, child, grand])
        self.assertEqual([s.id for s in top], ["p"])
        texts = [e.text for e in parent.events]
        self.assertIn("prompt-c", texts)
        self.assertIn("prompt-g", texts)


class ModelRoundTripTest(unittest.TestCase):
    def test_round_trip(self):
        original = make("x")
        restored = Session.from_dict(original.to_dict())
        self.assertEqual(restored.id, original.id)
        self.assertEqual(restored.started_at, original.started_at)
        self.assertEqual(restored.events[0].text, "prompt-x")


if __name__ == "__main__":
    unittest.main()
