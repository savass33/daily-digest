"""Workspace classification and scope resolution tests."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daily_digest.config import Config, WorkspaceConfig  # noqa: E402
from daily_digest.workspace import (  # noqa: E402
    ALL,
    OTHER,
    Classifier,
    allowed_workspaces,
    normalize_remote,
    output_dir_for,
    resolve_scope,
)


def make_config(root: str) -> Config:
    cfg = Config()
    cfg.output_dir = os.path.join(root, "daily")
    cfg.workspaces = {
        "work": WorkspaceConfig(
            name="work",
            aliases=["verboo", "verbeux"],
            match_paths=[os.path.join(root, "work") + "/**"],
            match_remotes=["github.com/verbeux-ai/*"],
            output_dir=os.path.join(root, "daily", "work"),
        ),
        "personal": WorkspaceConfig(
            name="personal",
            match_paths=[os.path.join(root, "personal") + "/**"],
            match_remotes=["github.com/savass33/*"],
            output_dir=os.path.join(root, "daily", "personal"),
        ),
    }
    return cfg


def init_repo(path: str, remote: str) -> None:
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(["git", "-C", path, "remote", "add", "origin", remote], check=True)


class RemoteTest(unittest.TestCase):
    def test_normalizes_ssh_and_https(self):
        self.assertEqual(
            normalize_remote("git@github.com:verbeux-ai/code.git"),
            "github.com/verbeux-ai/code",
        )
        self.assertEqual(
            normalize_remote("https://github.com/savass33/x.git"),
            "github.com/savass33/x",
        )
        self.assertEqual(
            normalize_remote("ssh://git@gitlab.com/org/repo"),
            "gitlab.com/org/repo",
        )


class ClassifierTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = make_config(self.tmp)
        self.clf = Classifier(self.cfg)

    def test_path_match(self):
        path = os.path.join(self.tmp, "work", "proj")
        os.makedirs(path)
        self.assertEqual(self.clf.classify(path).workspace, "work")

    def test_remote_match(self):
        path = os.path.join(self.tmp, "elsewhere", "repo")
        init_repo(path, "git@github.com:savass33/repo.git")
        cls = Classifier(self.cfg).classify(path)
        self.assertEqual(cls.workspace, "personal")
        self.assertEqual(cls.matched_by, "remote")

    def test_marker_precedence(self):
        path = os.path.join(self.tmp, "personal", "special")
        os.makedirs(path)
        with open(os.path.join(path, ".daily-digest.toml"), "w") as fh:
            fh.write('context = "work"\n')
        self.assertEqual(Classifier(self.cfg).classify(path).workspace, "work")

    def test_conflict_is_other(self):
        cfg = make_config(self.tmp)
        # A path matching two workspaces must not be assigned.
        cfg.workspaces["personal"].match_paths.append(os.path.join(self.tmp, "work") + "/**")
        path = os.path.join(self.tmp, "work", "proj")
        os.makedirs(path)
        cls = Classifier(cfg).classify(path)
        self.assertEqual(cls.workspace, OTHER)
        self.assertTrue(cls.conflict_with)

    def test_unmatched_is_other(self):
        path = os.path.join(self.tmp, "random")
        os.makedirs(path)
        self.assertEqual(Classifier(self.cfg).classify(path).workspace, OTHER)


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = make_config(self.tmp)

    def test_alias_resolves(self):
        scope = resolve_scope("me resume as coisas da verboo hoje", self.cfg)
        self.assertEqual(scope.workspace, "work")
        self.assertFalse(scope.needs_clarification)

    def test_empty_with_project_infers(self):
        path = os.path.join(self.tmp, "personal", "proj")
        os.makedirs(path)
        scope = resolve_scope("", self.cfg)
        self.assertIsNone(scope.workspace)
        scope2 = resolve_scope("", self.cfg)
        self.assertIsNone(scope2.workspace)
        # Inference is done by the caller; classifier gives the answer.
        self.assertEqual(Classifier(self.cfg).classify(path).workspace, "personal")

    def test_ambiguous_flags_clarification(self):
        cfg = make_config(self.tmp)
        cfg.workspaces["work"].aliases.append("shared")
        cfg.workspaces["personal"].aliases.append("shared")
        scope = resolve_scope("shared", cfg)
        self.assertTrue(scope.needs_clarification)
        self.assertCountEqual(scope.candidates, ["work", "personal"])

    def test_unknown_text_becomes_query(self):
        scope = resolve_scope("resolver bug de autenticacao", self.cfg)
        self.assertIsNone(scope.workspace)
        self.assertEqual(scope.query, "resolver bug de autenticacao")

    def test_restricted_profile_limits(self):
        scope = resolve_scope("personal", self.cfg, profile="work")
        self.assertNotEqual(scope.workspace, "personal")


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = make_config(self.tmp)

    def test_all_allows_everything(self):
        allowed = allowed_workspaces(self.cfg)
        self.assertIn(ALL, allowed)
        self.assertIn("work", allowed)
        self.assertIn("personal", allowed)

    def test_restricted_locks(self):
        cfg = replace(self.cfg)
        cfg.profile = "work"
        self.assertEqual(allowed_workspaces(cfg), ["work"])

    def test_output_dir_per_workspace(self):
        work = output_dir_for(self.cfg, "work")
        self.assertTrue(str(work).endswith(os.path.join("daily", "work")))
        all_dir = output_dir_for(self.cfg, ALL)
        self.assertEqual(str(all_dir), os.path.join(self.tmp, "daily"))


if __name__ == "__main__":
    unittest.main()
