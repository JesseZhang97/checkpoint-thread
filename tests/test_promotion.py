from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import git, init_repo, init_unborn_repo, load_ledger_state, run, run_cli


class PromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.repo = init_repo(self.root / "repo")
        self.ledgers = self.root / "ledgers"
        self.ledger_id = "promotion-thread"
        run_cli(
            self.ledgers, self.ledger_id, "enter", self.repo, "--merge-target", "main"
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

    def test_promote_does_not_claim_a_path_that_was_dirty_before_enter(self) -> None:
        other_repo = init_repo(self.root / "dirty-repo")
        (other_repo / "app.txt").write_text("user state\n", encoding="utf-8")
        dirty_ledger = "dirty-thread"
        run_cli(self.ledgers, dirty_ledger, "enter", other_repo)
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

    def test_promote_handles_spaces_leading_dash_and_safe_env_template(self) -> None:
        (self.repo / "dir").mkdir()
        (self.repo / "dir" / "file with space.txt").write_text(
            "space\n", encoding="utf-8"
        )
        (self.repo / "-leading.txt").write_text("dash\n", encoding="utf-8")
        (self.repo / ".env.example").write_text("TOKEN=replace-me\n", encoding="utf-8")

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "dir/file with space.txt",
            "--path=-leading.txt",
            "--path",
            ".env.example",
            "--message",
            "test: add unusual safe paths",
            "--acceptance-source",
            "objective",
        )

        self.assertEqual(
            {"-leading.txt", ".env.example", "dir/file with space.txt"},
            set(result["paths"]),
        )
        committed = set(
            git(
                self.repo, "show", "--pretty=", "--name-only", "HEAD"
            ).stdout.splitlines()
        )
        self.assertEqual(set(result["paths"]), committed)

    def test_promote_resolves_the_source_provisional_checkpoint(self) -> None:
        (self.repo / "provisional.txt").write_text("draft\n", encoding="utf-8")
        provisional = run_cli(
            self.ledgers,
            self.ledger_id,
            "snapshot",
            self.repo,
            "--kind",
            "provisional",
            "--reason",
            "implicit goal progression",
        )
        (self.repo / "provisional.txt").write_text("accepted\n", encoding="utf-8")

        promoted = run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "provisional.txt",
            "--checkpoint-ref",
            provisional["ref"],
            "--message",
            "feat: accept provisional work",
            "--acceptance-source",
            "explicit",
        )

        ledger = load_ledger_state(self.ledgers, self.ledger_id)
        branch = next(iter(ledger["repos"].values()))["branches"]["main"]
        source = next(
            item for item in branch["checkpoints"] if item["ref"] == provisional["ref"]
        )
        self.assertEqual("promoted", source["resolution"])
        self.assertEqual(promoted["commit"], source["resolved_commit"])

    def test_promote_commits_a_staged_rename_pair_atomically(self) -> None:
        repo = init_repo(self.root / "rename-repo")
        (repo / "old-name.txt").write_text("rename\n", encoding="utf-8")
        git(repo, "add", "old-name.txt")
        git(repo, "commit", "-qm", "test: seed rename source")
        ledger_id = "rename-thread"
        run_cli(self.ledgers, ledger_id, "enter", repo, "--merge-target", "main")
        git(repo, "mv", "old-name.txt", "new-name.txt")

        result = run_cli(
            self.ledgers,
            ledger_id,
            "promote",
            repo,
            "--path",
            "old-name.txt",
            "--path",
            "new-name.txt",
            "--message",
            "refactor: rename tracked file",
            "--acceptance-source",
            "objective",
        )

        self.assertEqual({"old-name.txt", "new-name.txt"}, set(result["paths"]))
        names = git(repo, "show", "--pretty=", "--name-status", "HEAD").stdout
        self.assertIn("old-name.txt", names)
        self.assertIn("new-name.txt", names)
        self.assertTrue(names.startswith("R"))

    def test_unborn_branch_checkpoint_lifecycle_promotes_and_ships_root_commit(
        self,
    ) -> None:
        repo = init_unborn_repo(self.root / "unborn")
        remote = self.root / "unborn.git"
        run(["git", "init", "-q", "--bare", remote])
        git(repo, "remote", "add", "origin", str(remote))
        ledger_id = "unborn-thread"
        entered = run_cli(
            self.ledgers, ledger_id, "enter", repo, "--merge-target", "main"
        )
        self.assertIsNone(entered["head"])

        (repo / "feature.txt").write_text("staged\n", encoding="utf-8")
        git(repo, "add", "feature.txt")
        (repo / "feature.txt").write_text("worktree\n", encoding="utf-8")
        (repo / "notes.txt").write_text("notes\n", encoding="utf-8")
        status_before = git(repo, "status", "--short").stdout
        index_before = git(repo, "diff", "--cached").stdout

        parked = run_cli(
            self.ledgers,
            ledger_id,
            "park",
            repo,
            "--reason",
            "switch away before initial commit",
        )
        self.assertEqual("", git(repo, "status", "--short").stdout)
        self.assertNotEqual(
            0, git(repo, "rev-parse", "--verify", "HEAD", check=False).returncode
        )

        run_cli(
            self.ledgers,
            ledger_id,
            "restore",
            repo,
            "--ref",
            parked["ref"],
            "--confirm",
        )
        self.assertEqual(status_before, git(repo, "status", "--short").stdout)
        self.assertEqual(index_before, git(repo, "diff", "--cached").stdout)
        self.assertEqual("worktree\n", (repo / "feature.txt").read_text())
        run_cli(
            self.ledgers,
            ledger_id,
            "resolve-checkpoint",
            repo,
            "--ref",
            parked["ref"],
            "--resolution",
            "superseded",
            "--reason",
            "restored into the active worktree",
        )

        git(repo, "reset", "-q", "--", "feature.txt")
        (repo / "unrelated.txt").write_text("staged user work\n", encoding="utf-8")
        git(repo, "add", "unrelated.txt")
        provisional = run_cli(
            self.ledgers,
            ledger_id,
            "snapshot",
            repo,
            "--kind",
            "provisional",
            "--reason",
            "implicit progression before root commit",
        )
        promoted = run_cli(
            self.ledgers,
            ledger_id,
            "promote",
            repo,
            "--path",
            "feature.txt",
            "--checkpoint-ref",
            provisional["ref"],
            "--message",
            "feat: create project root",
            "--acceptance-source",
            "explicit",
        )

        self.assertEqual(
            promoted["commit"],
            git(repo, "rev-list", "--max-parents=0", "HEAD").stdout.strip(),
        )
        self.assertEqual(
            ["feature.txt"],
            git(repo, "show", "--pretty=", "--name-only", "HEAD").stdout.splitlines(),
        )
        self.assertEqual(
            "A  unrelated.txt\n?? notes.txt\n",
            git(repo, "status", "--short").stdout,
        )

        git(repo, "restore", "--staged", "unrelated.txt")
        (repo / "unrelated.txt").unlink()
        (repo / "notes.txt").unlink()
        run_cli(
            self.ledgers,
            ledger_id,
            "record-verification",
            repo,
            "--verification-command",
            "not-applicable",
            "--status",
            "not_applicable",
            "--scope",
            "fixture-only Git behavior",
        )
        shipped = run_cli(self.ledgers, ledger_id, "ship", None, "--fetch")
        self.assertEqual("pushed", shipped["branches"][0]["push_status"])
        self.assertEqual(
            promoted["commit"],
            git(remote, "rev-parse", "refs/heads/main").stdout.strip(),
        )

    def test_unborn_branch_does_not_claim_paths_present_before_enter(self) -> None:
        repo = init_unborn_repo(self.root / "unborn-preexisting")
        (repo / "existing.txt").write_text("user scaffold\n", encoding="utf-8")
        ledger_id = "unborn-preexisting-thread"
        run_cli(self.ledgers, ledger_id, "enter", repo)
        (repo / "existing.txt").write_text("agent edit\n", encoding="utf-8")

        result = run_cli(
            self.ledgers,
            ledger_id,
            "promote",
            repo,
            "--path",
            "existing.txt",
            "--message",
            "feat: claim user scaffold",
            "--acceptance-source",
            "explicit",
            expected_code=2,
        )

        self.assertEqual("selected_path_preexisting", result["error"])
        self.assertNotEqual(
            0, git(repo, "rev-parse", "--verify", "HEAD", check=False).returncode
        )

    def test_unborn_commit_hook_failure_keeps_repository_unborn(self) -> None:
        repo = init_unborn_repo(self.root / "unborn-hook")
        ledger_id = "unborn-hook-thread"
        run_cli(self.ledgers, ledger_id, "enter", repo)
        (repo / "root.txt").write_text("root\n", encoding="utf-8")
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

        result = run_cli(
            self.ledgers,
            ledger_id,
            "promote",
            repo,
            "--path",
            "root.txt",
            "--message",
            "feat: blocked root commit",
            "--acceptance-source",
            "explicit",
            expected_code=2,
        )

        self.assertEqual("commit_failed", result["error"])
        self.assertNotEqual(
            0, git(repo, "rev-parse", "--verify", "HEAD", check=False).returncode
        )
        self.assertEqual("?? root.txt\n", git(repo, "status", "--short").stdout)


if __name__ == "__main__":
    unittest.main()
