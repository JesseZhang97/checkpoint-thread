from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import git, init_repo, run_cli


class PromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.repo = init_repo(self.root / "repo")
        self.ledgers = self.root / "ledgers"
        self.ledger_id = "promotion-thread"
        run_cli(
            self.ledgers, self.ledger_id, "begin", self.repo, "--merge-target", "main"
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_promote_commits_only_selected_paths_and_preserves_unrelated_staging(
        self,
    ) -> None:
        (self.repo / "feature.txt").write_text("feature\n", encoding="utf-8")
        (self.repo / "unrelated.txt").write_text("user work\n", encoding="utf-8")
        git(self.repo, "add", "unrelated.txt")

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "feature.txt",
            "--message",
            "feat: add isolated feature",
            "--acceptance-source",
            "explicit",
        )

        self.assertEqual(
            "feat: add isolated feature",
            git(self.repo, "log", "-1", "--pretty=%s").stdout.strip(),
        )
        self.assertEqual(
            ["feature.txt"],
            git(
                self.repo, "show", "--pretty=", "--name-only", "HEAD"
            ).stdout.splitlines(),
        )
        self.assertEqual(
            "A  unrelated.txt\n",
            git(self.repo, "status", "--short", "unrelated.txt").stdout,
        )
        self.assertEqual(
            result["commit"], git(self.repo, "rev-parse", "HEAD").stdout.strip()
        )

    def test_promote_blocks_partial_staging_in_a_selected_path(self) -> None:
        (self.repo / "app.txt").write_text("staged\n", encoding="utf-8")
        git(self.repo, "add", "app.txt")
        (self.repo / "app.txt").write_text("unstaged\n", encoding="utf-8")
        before_head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        before_index = git(self.repo, "diff", "--cached").stdout

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "app.txt",
            "--message",
            "fix: change app",
            "--acceptance-source",
            "explicit",
            expected_code=2,
        )

        self.assertEqual("selected_path_has_partial_staging", result["error"])
        self.assertEqual(
            before_head, git(self.repo, "rev-parse", "HEAD").stdout.strip()
        )
        self.assertEqual(before_index, git(self.repo, "diff", "--cached").stdout)

    def test_promote_rejects_secrets_and_large_untracked_files(self) -> None:
        (self.repo / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        secret = run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            ".env",
            "--message",
            "chore: add env",
            "--acceptance-source",
            "explicit",
            expected_code=2,
        )
        self.assertEqual("unsafe_paths", secret["error"])

        (self.repo / "large.bin").write_bytes(b"x" * 2048)
        large = run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "large.bin",
            "--message",
            "feat: add binary",
            "--acceptance-source",
            "explicit",
            "--large-file-limit",
            "1024",
            expected_code=2,
        )
        self.assertEqual("unsafe_paths", large["error"])
        self.assertEqual(["large.bin"], large["large_untracked"])

    def test_failed_commit_hook_keeps_the_branch_and_records_a_checkpoint(self) -> None:
        (self.repo / "hooked.txt").write_text("content\n", encoding="utf-8")
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.write_text(
            "#!/bin/sh\necho blocked by test >&2\nexit 1\n", encoding="utf-8"
        )
        hook.chmod(0o755)
        before = git(self.repo, "rev-parse", "HEAD").stdout.strip()

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "hooked.txt",
            "--message",
            "feat: hook failure",
            "--acceptance-source",
            "explicit",
            expected_code=2,
        )

        self.assertEqual("commit_failed", result["error"])
        self.assertEqual(before, git(self.repo, "rev-parse", "HEAD").stdout.strip())
        self.assertTrue(
            result["checkpoint_ref"].startswith("refs/codex/checkpoint-thread/")
        )
        self.assertEqual(
            "?? hooked.txt\n", git(self.repo, "status", "--short", "hooked.txt").stdout
        )

    def test_promote_blocks_conflict_markers_but_keeps_a_recovery_ref(self) -> None:
        (self.repo / "conflicted.txt").write_text(
            "<<<<<<< local\nleft\n=======\nright\n>>>>>>> remote\n",
            encoding="utf-8",
        )
        before = git(self.repo, "rev-parse", "HEAD").stdout.strip()

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "conflicted.txt",
            "--message",
            "fix: unresolved content",
            "--acceptance-source",
            "explicit",
            expected_code=2,
        )

        self.assertEqual("diff_check_failed", result["error"])
        self.assertEqual(before, git(self.repo, "rev-parse", "HEAD").stdout.strip())
        self.assertTrue(
            result["checkpoint_ref"].startswith("refs/codex/checkpoint-thread/")
        )

    def test_promote_does_not_claim_a_path_that_was_dirty_before_begin(self) -> None:
        other_repo = init_repo(self.root / "dirty-repo")
        (other_repo / "app.txt").write_text("user state\n", encoding="utf-8")
        dirty_ledger = "dirty-thread"
        run_cli(self.ledgers, dirty_ledger, "begin", other_repo)
        (other_repo / "app.txt").write_text("agent state\n", encoding="utf-8")

        result = run_cli(
            self.ledgers,
            dirty_ledger,
            "promote",
            other_repo,
            "--path",
            "app.txt",
            "--message",
            "fix: overwrite ambiguous path",
            "--acceptance-source",
            "explicit",
            expected_code=2,
        )

        self.assertEqual("selected_path_preexisting", result["error"])


if __name__ == "__main__":
    unittest.main()
