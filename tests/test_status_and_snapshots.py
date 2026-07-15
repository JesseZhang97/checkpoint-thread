from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import CLI, git, init_repo, ref_exists, run, run_cli


class StatusAndSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.repo = init_repo(self.root / "repo")
        self.ledgers = self.root / "ledgers"
        self.ledger_id = "thread-test"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_begin_is_idempotent_and_records_the_original_baseline(self) -> None:
        first = run_cli(self.ledgers, self.ledger_id, "begin", self.repo)
        first_head = first["head"]
        first_ref = first["baseline_ref"]

        (self.repo / "app.txt").write_text("changed later\n", encoding="utf-8")
        second = run_cli(self.ledgers, self.ledger_id, "begin", self.repo)

        self.assertEqual("begun", first["action"])
        self.assertEqual("already_active", second["action"])
        self.assertEqual(first_head, second["head"])
        self.assertEqual(first_ref, second["baseline_ref"])
        self.assertTrue(ref_exists(self.repo, first_ref))

    def test_read_only_status_does_not_create_a_ledger_or_private_ref(self) -> None:
        result = run_cli(self.ledgers, self.ledger_id, "status", self.repo)

        self.assertEqual("begin", result["action"])
        self.assertFalse((self.ledgers / self.ledger_id / "ledger.json").exists())
        refs = git(
            self.repo,
            "for-each-ref",
            "--format=%(refname)",
            "refs/codex/checkpoint-thread/",
        ).stdout
        self.assertEqual("", refs)

    def test_first_use_requests_a_user_selected_root_under_codex_home(self) -> None:
        codex_home = self.root / "codex-home"
        result = run(
            [
                sys.executable,
                CLI,
                "--ledger-id",
                self.ledger_id,
                "status",
                "--repo",
                self.repo,
            ],
            check=False,
            env={"CODEX_HOME": str(codex_home)},
        )
        payload = json.loads(result.stdout)

        self.assertEqual(2, result.returncode)
        self.assertEqual("ledger_root_not_configured", payload["error"])
        self.assertEqual(
            str((codex_home / "ledgers" / "checkpoint-thread" / "active").resolve()),
            payload["suggested_ledger_root"],
        )
        self.assertFalse(codex_home.exists())

    def test_configured_ledger_root_is_reused_and_replacement_is_explicit(
        self,
    ) -> None:
        codex_home = self.root / "codex-home"
        selected = self.root / "selected-ledgers"
        environment = {"CODEX_HOME": str(codex_home)}

        configured = run(
            [sys.executable, CLI, "--ledger-root", selected, "configure"],
            env=environment,
        )
        configured_payload = json.loads(configured.stdout)
        self.assertEqual("configured", configured_payload["action"])
        self.assertEqual(str(selected.resolve()), configured_payload["ledger_root"])

        begun = run(
            [
                sys.executable,
                CLI,
                "--ledger-id",
                self.ledger_id,
                "begin",
                "--repo",
                self.repo,
            ],
            env=environment,
        )
        self.assertEqual("begun", json.loads(begun.stdout)["action"])
        self.assertTrue((selected / self.ledger_id / "ledger.json").is_file())

        replacement = self.root / "replacement-ledgers"
        blocked = run(
            [sys.executable, CLI, "--ledger-root", replacement, "configure"],
            check=False,
            env=environment,
        )
        self.assertEqual(2, blocked.returncode)
        self.assertEqual(
            "configuration_already_exists", json.loads(blocked.stdout)["error"]
        )

        replaced = run(
            [
                sys.executable,
                CLI,
                "--ledger-root",
                replacement,
                "configure",
                "--replace",
            ],
            env=environment,
        )
        self.assertEqual("configured", json.loads(replaced.stdout)["action"])

    def test_status_reports_non_repo_and_corrupt_ledger_as_json_errors(self) -> None:
        non_repo = self.root / "not-a-repo"
        non_repo.mkdir()
        missing = run_cli(
            self.ledgers,
            "not-a-repo",
            "status",
            non_repo,
            expected_code=2,
        )
        self.assertEqual("not_a_git_repository", missing["error"])

        ledger_path = self.ledgers / self.ledger_id / "ledger.json"
        ledger_path.parent.mkdir(parents=True)
        ledger_path.write_text("{broken", encoding="utf-8")
        corrupt = run_cli(
            self.ledgers,
            self.ledger_id,
            "status",
            self.repo,
            expected_code=2,
        )
        self.assertEqual("ledger_unreadable", corrupt["error"])

    def test_status_reports_partial_staging_without_mutating_the_index(self) -> None:
        run_cli(self.ledgers, self.ledger_id, "begin", self.repo)
        (self.repo / "app.txt").write_text("staged\n", encoding="utf-8")
        git(self.repo, "add", "app.txt")
        (self.repo / "app.txt").write_text("unstaged\n", encoding="utf-8")
        before = git(self.repo, "diff", "--cached").stdout

        result = run_cli(self.ledgers, self.ledger_id, "status", self.repo)

        self.assertEqual(["app.txt"], result["changes"]["index_worktree_overlap"])
        self.assertEqual(before, git(self.repo, "diff", "--cached").stdout)
        self.assertEqual("continue", result["action"])

    def test_snapshot_preserves_staged_unstaged_and_untracked_content(self) -> None:
        run_cli(self.ledgers, self.ledger_id, "begin", self.repo)
        (self.repo / "app.txt").write_text("staged version\n", encoding="utf-8")
        git(self.repo, "add", "app.txt")
        (self.repo / "app.txt").write_text("working version\n", encoding="utf-8")
        (self.repo / "notes.txt").write_text("untracked note\n", encoding="utf-8")
        status_before = git(self.repo, "status", "--short").stdout

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "snapshot",
            self.repo,
            "--kind",
            "provisional",
            "--reason",
            "goal progression",
        )

        self.assertTrue(ref_exists(self.repo, result["ref"]))
        self.assertEqual(
            "staged version\n",
            git(self.repo, "show", f"{result['index_commit']}:app.txt").stdout,
        )
        self.assertEqual(
            "working version\n",
            git(self.repo, "show", f"{result['worktree_commit']}:app.txt").stdout,
        )
        self.assertEqual(
            "untracked note\n",
            git(self.repo, "show", f"{result['worktree_commit']}:notes.txt").stdout,
        )
        self.assertEqual(status_before, git(self.repo, "status", "--short").stdout)

    def test_snapshot_excludes_generated_outputs_large_untracked_and_secrets(
        self,
    ) -> None:
        (self.repo / ".gitignore").write_text("ignored-output/\n", encoding="utf-8")
        git(self.repo, "add", ".gitignore")
        git(self.repo, "commit", "-qm", "ignore generated output")
        run_cli(self.ledgers, self.ledger_id, "begin", self.repo)
        (self.repo / "ignored-output").mkdir()
        (self.repo / "ignored-output" / "results.xml").write_text(
            "ignored\n", encoding="utf-8"
        )
        (self.repo / "test-results").mkdir()
        (self.repo / "test-results" / "run.log").write_text(
            "generated\n", encoding="utf-8"
        )
        (self.repo / "large.bin").write_bytes(b"x" * 2048)
        (self.repo / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "snapshot",
            self.repo,
            "--kind",
            "safety",
            "--reason",
            "before branch switch",
            "--large-file-limit",
            "1024",
        )

        self.assertFalse(result["complete"])
        self.assertIn(".env", result["excluded"]["secret"])
        self.assertIn("ignored-output/", result["excluded"]["ignored"])
        self.assertIn("large.bin", result["excluded"]["large_untracked"])
        self.assertIn("test-results/run.log", result["excluded"]["generated_output"])
        tree = git(
            self.repo, "ls-tree", "-r", "--name-only", result["worktree_commit"]
        ).stdout.splitlines()
        self.assertNotIn(".env", tree)
        self.assertNotIn("large.bin", tree)
        self.assertNotIn("test-results/run.log", tree)
        self.assertNotIn("ignored-output/results.xml", tree)

    def test_snapshot_blocks_when_the_index_contains_unmerged_entries(self) -> None:
        git(self.repo, "switch", "-qc", "side")
        (self.repo / "app.txt").write_text("side\n", encoding="utf-8")
        git(self.repo, "commit", "-qam", "side change")
        git(self.repo, "switch", "-q", "main")
        (self.repo / "app.txt").write_text("main\n", encoding="utf-8")
        git(self.repo, "commit", "-qam", "main change")
        merge = git(self.repo, "merge", "side", check=False)
        self.assertNotEqual(0, merge.returncode)
        run_cli(self.ledgers, self.ledger_id, "begin", self.repo, expected_code=2)

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "snapshot",
            self.repo,
            "--kind",
            "safety",
            "--reason",
            "during conflict",
            expected_code=2,
        )

        self.assertEqual("unmerged_index", result["error"])
        self.assertEqual(["app.txt"], result["paths"])

    def test_begin_blocks_a_clean_index_during_an_in_progress_merge(self) -> None:
        git(self.repo, "switch", "-qc", "side")
        (self.repo / "side.txt").write_text("side\n", encoding="utf-8")
        git(self.repo, "add", "side.txt")
        git(self.repo, "commit", "-qm", "test: add side file")
        git(self.repo, "switch", "-q", "main")
        git(self.repo, "merge", "--no-commit", "--no-ff", "side")

        result = run_cli(
            self.ledgers,
            "merge-thread",
            "begin",
            self.repo,
            expected_code=2,
        )

        self.assertEqual("git_operation_in_progress", result["error"])
        self.assertEqual("merge", result["operation"])

    def test_snapshot_keeps_modified_large_tracked_files(self) -> None:
        (self.repo / "tracked.bin").write_bytes(b"a" * 2048)
        git(self.repo, "add", "tracked.bin")
        git(self.repo, "commit", "-qm", "add tracked binary")
        run_cli(self.ledgers, self.ledger_id, "begin", self.repo)
        (self.repo / "tracked.bin").write_bytes(b"b" * 4096)

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "snapshot",
            self.repo,
            "--kind",
            "safety",
            "--reason",
            "large tracked file changed",
            "--large-file-limit",
            "1024",
        )

        self.assertTrue(result["complete"])
        size = int(
            git(
                self.repo, "cat-file", "-s", f"{result['worktree_commit']}:tracked.bin"
            ).stdout
        )
        self.assertEqual(4096, size)

    def test_snapshot_preserves_rename_delete_symlink_and_executable_mode(self) -> None:
        (self.repo / "old-name.txt").write_text("rename me\n", encoding="utf-8")
        (self.repo / "delete-me.txt").write_text("delete me\n", encoding="utf-8")
        script = self.repo / "script.sh"
        script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        git(self.repo, "add", "old-name.txt", "delete-me.txt", "script.sh")
        git(self.repo, "commit", "-qm", "test: seed file state fixtures")
        git(self.repo, "config", "core.filemode", "true")
        run_cli(self.ledgers, self.ledger_id, "begin", self.repo)

        git(self.repo, "mv", "old-name.txt", "renamed.txt")
        (self.repo / "delete-me.txt").unlink()
        script.chmod(0o755)
        (self.repo / "link-to-app").symlink_to("app.txt")

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "snapshot",
            self.repo,
            "--kind",
            "safety",
            "--reason",
            "preserve file system states",
        )

        tree = git(
            self.repo,
            "ls-tree",
            "-r",
            result["worktree_commit"],
        ).stdout.splitlines()
        self.assertTrue(any(line.endswith("\trenamed.txt") for line in tree))
        self.assertFalse(any(line.endswith("\told-name.txt") for line in tree))
        self.assertFalse(any(line.endswith("\tdelete-me.txt") for line in tree))
        self.assertTrue(
            any(
                line.startswith("100755 ") and line.endswith("\tscript.sh")
                for line in tree
            )
        )
        self.assertTrue(
            any(
                line.startswith("120000 ") and line.endswith("\tlink-to-app")
                for line in tree
            )
        )

    def test_park_and_restore_round_trip_partial_staging_and_untracked_files(
        self,
    ) -> None:
        run_cli(self.ledgers, self.ledger_id, "begin", self.repo)
        (self.repo / "app.txt").write_text("staged\n", encoding="utf-8")
        git(self.repo, "add", "app.txt")
        (self.repo / "app.txt").write_text("working\n", encoding="utf-8")
        (self.repo / "notes.txt").write_text("notes\n", encoding="utf-8")
        status_before = git(self.repo, "status", "--short").stdout
        index_before = git(self.repo, "diff", "--cached").stdout

        parked = run_cli(
            self.ledgers,
            self.ledger_id,
            "park",
            self.repo,
            "--reason",
            "switch branch",
        )

        self.assertEqual("", git(self.repo, "status", "--short").stdout)
        self.assertFalse((self.repo / "notes.txt").exists())

        restored = run_cli(
            self.ledgers,
            self.ledger_id,
            "restore",
            self.repo,
            "--ref",
            parked["ref"],
            "--confirm",
        )

        self.assertEqual("restored", restored["action"])
        self.assertEqual(
            "working\n", (self.repo / "app.txt").read_text(encoding="utf-8")
        )
        self.assertEqual(
            "notes\n", (self.repo / "notes.txt").read_text(encoding="utf-8")
        )
        self.assertEqual(status_before, git(self.repo, "status", "--short").stdout)
        self.assertEqual(index_before, git(self.repo, "diff", "--cached").stdout)

    def test_park_refuses_to_clean_an_incomplete_snapshot(self) -> None:
        run_cli(self.ledgers, self.ledger_id, "begin", self.repo)
        (self.repo / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "park",
            self.repo,
            "--reason",
            "unsafe branch switch",
            expected_code=2,
        )

        self.assertEqual("snapshot_incomplete", result["error"])
        self.assertTrue((self.repo / ".env").exists())

    def test_begin_blocks_detached_head(self) -> None:
        git(self.repo, "checkout", "-q", "--detach")

        result = run_cli(
            self.ledgers,
            "detached-thread",
            "begin",
            self.repo,
            expected_code=2,
        )

        self.assertEqual("detached_head", result["error"])

    def test_begin_blocks_a_secret_already_in_the_index(self) -> None:
        (self.repo / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        git(self.repo, "add", ".env")

        result = run_cli(
            self.ledgers,
            "secret-thread",
            "begin",
            self.repo,
            expected_code=2,
        )

        self.assertEqual("staged_secret_paths", result["error"])
        self.assertEqual([".env"], result["paths"])

    def test_restore_requires_explicit_confirmation(self) -> None:
        run_cli(self.ledgers, self.ledger_id, "begin", self.repo)
        (self.repo / "notes.txt").write_text("notes\n", encoding="utf-8")
        parked = run_cli(
            self.ledgers,
            self.ledger_id,
            "park",
            self.repo,
            "--reason",
            "pause",
        )

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "restore",
            self.repo,
            "--ref",
            parked["ref"],
            expected_code=2,
        )

        self.assertEqual("restore_requires_confirmation", result["error"])

    def test_restore_refuses_to_overwrite_new_dirty_state(self) -> None:
        run_cli(self.ledgers, self.ledger_id, "begin", self.repo)
        (self.repo / "notes.txt").write_text("saved\n", encoding="utf-8")
        parked = run_cli(
            self.ledgers,
            self.ledger_id,
            "park",
            self.repo,
            "--reason",
            "pause before new work",
        )
        (self.repo / "new-state.txt").write_text("do not overwrite\n", encoding="utf-8")

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "restore",
            self.repo,
            "--ref",
            parked["ref"],
            "--confirm",
            expected_code=2,
        )

        self.assertEqual("restore_target_dirty", result["error"])
        self.assertTrue((self.repo / "new-state.txt").exists())

    def test_restore_refuses_a_snapshot_from_a_different_head(self) -> None:
        run_cli(self.ledgers, self.ledger_id, "begin", self.repo)
        (self.repo / "saved.txt").write_text("saved\n", encoding="utf-8")
        checkpoint = run_cli(
            self.ledgers,
            self.ledger_id,
            "snapshot",
            self.repo,
            "--kind",
            "safety",
            "--reason",
            "before head changes",
        )
        (self.repo / "saved.txt").unlink()
        (self.repo / "later.txt").write_text("later\n", encoding="utf-8")
        git(self.repo, "add", "later.txt")
        git(self.repo, "commit", "-qm", "test: advance head")

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "restore",
            self.repo,
            "--ref",
            checkpoint["ref"],
            "--confirm",
            expected_code=2,
        )

        self.assertEqual("restore_head_mismatch", result["error"])


if __name__ == "__main__":
    unittest.main()
