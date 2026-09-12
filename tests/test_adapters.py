"""Adapter and redaction tests using stdlib unittest and local fixtures."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daily_digest.config import Config, SourceConfig  # noqa: E402
from daily_digest.adapters.claude import ClaudeAdapter  # noqa: E402
from daily_digest.adapters.codex import CodexAdapter  # noqa: E402
from daily_digest.normalize import PROMPT, TOOL_CALL  # noqa: E402
from daily_digest.redact import Redactor  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
WINDOW_START = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)
WINDOW_END = WINDOW_START + timedelta(days=1)


class ClaudeAdapterTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(claude=SourceConfig(dir=os.path.join(FIXTURES, "claude")))
        self.adapter = ClaudeAdapter(self.cfg)

    def test_collects_session(self):
        sessions = self.adapter.collect(WINDOW_START, WINDOW_END)
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session.source, "claude")
        self.assertEqual(session.title, "Setup do terminal")
        self.assertEqual(session.project_path, "/home/u/proj")

    def test_extracts_prompts_and_tool_calls(self):
        session = self.adapter.collect(WINDOW_START, WINDOW_END)[0]
        prompts = [e.text for e in session.events if e.kind == PROMPT]
        self.assertIn("configura o terminal ghostty", prompts)
        self.assertIn("agora deixa roxo", prompts)
        tool_calls = [e for e in session.events if e.kind == TOOL_CALL]
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(tool_calls[0].files, ["/home/u/proj/config"])

    def test_skips_sidechain(self):
        session = self.adapter.collect(WINDOW_START, WINDOW_END)[0]
        commands = [e.command for e in session.events if e.kind == TOOL_CALL]
        self.assertNotIn("echo hello", commands)


class CodexAdapterTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(codex=SourceConfig(dir=os.path.join(FIXTURES, "codex")))
        self.adapter = CodexAdapter(self.cfg)

    def test_collects_session(self):
        sessions = self.adapter.collect(WINDOW_START, WINDOW_END)
        self.assertEqual(len(sessions), 1)
        session = sessions[0]
        self.assertEqual(session.source, "codex")
        self.assertEqual(session.id, "codex-abc")
        self.assertEqual(session.project_path, "/home/u/proj2")

    def test_strips_context_envelope(self):
        session = self.adapter.collect(WINDOW_START, WINDOW_END)[0]
        prompts = [e.text for e in session.events if e.kind == PROMPT]
        self.assertIn("corrige o parser de datas", prompts)
        self.assertTrue(all("<environment_context>" not in p for p in prompts))

    def test_extracts_tool_command(self):
        session = self.adapter.collect(WINDOW_START, WINDOW_END)[0]
        commands = [e.command for e in session.events if e.kind == TOOL_CALL]
        self.assertIn("npm test", commands)


class RedactorTest(unittest.TestCase):
    def setUp(self):
        self.redactor = Redactor(True)

    def test_redacts_assignment(self):
        out = self.redactor.text("JWT_SECRET=supersecretvalue123")
        self.assertNotIn("supersecretvalue123", out)
        self.assertIn("***REDACTED***", out)

    def test_redacts_prefixed_tokens(self):
        keys = [
            "vbk_ultra_36e942920d0eb80f7053fef1b106f7f7fd420fb8",
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            "sk-ant-abcdefghijklmnop1234",
        ]
        for key in keys:
            self.assertNotIn(key, self.redactor.text(f"key={key}"))

    def test_redacts_url_credentials(self):
        out = self.redactor.text("postgres://user:secretpass@host/db")
        self.assertNotIn("secretpass", out)

    def test_generalizes_home_path(self):
        out = self.redactor.path(os.path.expanduser("~/proj/file.py"))
        self.assertTrue(out.startswith("~"))


if __name__ == "__main__":
    unittest.main()
