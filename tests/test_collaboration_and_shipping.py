from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import clone_repo, git, init_repo, run, run_cli


class CollaborationAndShippingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.remote = self.root / "origin.git"
        run(["git", "init", "-q", "--bare", str(self.remote)])
        seed = init_repo(self.root / "seed")
        git(seed, "remote", "add", "origin", str(self.remote))
        git(seed, "push", "-qu", "origin", "main")
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/main")
        self.repo = clone_repo(self.remote, self.root / "repo")
        self.collaborator = clone_repo(self.remote, self.root / "collaborator")
        self.ledgers = self.root / "ledgers"
        self.ledger_id = "shipping-thread"
        run_cli(
            self.ledgers, self.ledger_id, "begin", self.repo, "--merge-target", "main"
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_ship_plan_detects_remote_conflict_and_gives_a_merge_solution(self) -> None:
        (self.repo / "app.txt").write_text("local\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "app.txt",
            "--message",
            "fix: local change",
            "--acceptance-source",
            "explicit",
        )
        (self.collaborator / "app.txt").write_text("remote\n", encoding="utf-8")
        git(self.collaborator, "commit", "-qam", "fix: remote change")
        git(self.collaborator, "push", "-q")

        result = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")
        branch = result["branches"][0]

        self.assertEqual("conflict", branch["remote_state"])
        self.assertEqual("blocked", branch["push_status"])
        self.assertEqual(
            "rebase_onto_upstream_then_resolve", branch["divergence_solution"]
        )
        self.assertEqual("main", branch["merge_plan"]["target"])
        self.assertIn("app.txt", branch["conflict_paths"])

    def test_ship_pushes_only_branches_with_thread_owned_commits(self) -> None:
        (self.repo / "feature.txt").write_text("feature\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "feature.txt",
            "--message",
            "feat: add feature",
            "--acceptance-source",
            "explicit",
        )
        git(self.repo, "branch", "untouched")

        plan = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")
        self.assertEqual(["main"], [branch["branch"] for branch in plan["branches"]])

        shipped = run_cli(self.ledgers, self.ledger_id, "ship", None, "--fetch")
        self.assertEqual("pushed", shipped["branches"][0]["push_status"])
        self.assertEqual(
            git(self.repo, "rev-parse", "main").stdout.strip(),
            git(self.remote, "rev-parse", "refs/heads/main").stdout.strip(),
        )
        self.assertNotEqual(
            0,
            git(
                self.remote,
                "show-ref",
                "--verify",
                "--quiet",
                "refs/heads/untouched",
                check=False,
            ).returncode,
        )

        repeated_plan = run_cli(
            self.ledgers, self.ledger_id, "ship-plan", None, "--fetch"
        )
        self.assertEqual([], repeated_plan["branches"])
        self.assertEqual([], repeated_plan["push_groups"])
        self.assertTrue(repeated_plan["nothing_to_ship"])

        repeated_ship = run_cli(self.ledgers, self.ledger_id, "ship", None, "--fetch")
        self.assertEqual([], repeated_ship["branches"])
        self.assertTrue(repeated_ship["all_ready"])

    def test_ship_rebases_clean_thread_owned_divergence_before_push(self) -> None:
        (self.repo / "local.txt").write_text("local\n", encoding="utf-8")
        promoted = run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "local.txt",
            "--message",
            "feat: add local change",
            "--acceptance-source",
            "explicit",
        )
        old_tip = promoted["commit"]
        (self.collaborator / "remote.txt").write_text("remote\n", encoding="utf-8")
        git(self.collaborator, "add", "remote.txt")
        git(self.collaborator, "commit", "-qm", "feat: add remote change")
        git(self.collaborator, "push", "-q")

        result = run_cli(self.ledgers, self.ledger_id, "ship", None, "--fetch")

        self.assertEqual("pushed", result["branches"][0]["push_status"])
        self.assertNotEqual(old_tip, git(self.repo, "rev-parse", "main").stdout.strip())
        self.assertEqual(
            git(self.repo, "rev-parse", "main").stdout.strip(),
            git(self.remote, "rev-parse", "refs/heads/main").stdout.strip(),
        )
        safety_refs = git(
            self.repo,
            "for-each-ref",
            "--format=%(refname)",
            f"refs/codex/checkpoint-thread/{self.ledger_id}/main/",
        ).stdout.splitlines()
        self.assertTrue(any("pre-rebase" in ref for ref in safety_refs))

    def test_ship_plan_blocks_local_commits_not_owned_by_the_thread(self) -> None:
        (self.repo / "preexisting.txt").write_text("before thread\n", encoding="utf-8")
        git(self.repo, "add", "preexisting.txt")
        git(self.repo, "commit", "-qm", "chore: preexisting local commit")
        separate_ledger = "ownership-thread"
        run_cli(
            self.ledgers, separate_ledger, "begin", self.repo, "--merge-target", "main"
        )
        (self.repo / "owned.txt").write_text("thread owned\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            separate_ledger,
            "promote",
            self.repo,
            "--path",
            "owned.txt",
            "--message",
            "feat: thread owned commit",
            "--acceptance-source",
            "explicit",
        )

        result = run_cli(self.ledgers, separate_ledger, "ship-plan", None, "--fetch")

        branch = result["branches"][0]
        self.assertEqual("blocked", branch["push_status"])
        self.assertEqual("unowned_local_commits", branch["ownership_status"])
        self.assertEqual(
            "separate_or_confirm_local_commits", branch["divergence_solution"]
        )

    def test_new_remote_branch_blocks_pre_thread_local_commits(self) -> None:
        git(self.repo, "switch", "-qc", "feat/preexisting")
        (self.repo / "preexisting.txt").write_text("pre-thread\n", encoding="utf-8")
        git(self.repo, "add", "preexisting.txt")
        git(self.repo, "commit", "-qm", "chore: pre-thread branch work")
        run_cli(
            self.ledgers,
            "new-branch-ownership",
            "begin",
            self.repo,
            "--merge-target",
            "main",
        )
        (self.repo / "owned.txt").write_text("owned\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            "new-branch-ownership",
            "promote",
            self.repo,
            "--path",
            "owned.txt",
            "--message",
            "feat: thread-owned branch work",
            "--acceptance-source",
            "explicit",
        )

        result = run_cli(
            self.ledgers,
            "new-branch-ownership",
            "ship-plan",
            None,
            "--fetch",
        )

        branch = result["branches"][0]
        self.assertEqual("new", branch["remote_state"])
        self.assertEqual("blocked", branch["push_status"])
        self.assertEqual("unowned_local_commits", branch["ownership_status"])
        self.assertEqual(1, len(branch["unowned_local_commits"]))

    def test_ship_plan_blocks_until_provisional_checkpoints_are_resolved(self) -> None:
        (self.repo / "confirmed.txt").write_text("confirmed\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "confirmed.txt",
            "--message",
            "feat: confirmed work",
            "--acceptance-source",
            "explicit",
        )
        (self.repo / "provisional.txt").write_text("provisional\n", encoding="utf-8")
        checkpoint = run_cli(
            self.ledgers,
            self.ledger_id,
            "snapshot",
            self.repo,
            "--kind",
            "provisional",
            "--reason",
            "implicit progression",
        )

        blocked = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")
        branch = blocked["branches"][0]
        self.assertEqual("blocked", branch["push_status"])
        self.assertEqual(
            "promote_or_exclude_unresolved_checkpoints",
            branch["divergence_solution"],
        )
        self.assertEqual([checkpoint["ref"]], branch["unresolved_checkpoints"])

        (self.repo / "provisional.txt").unlink()
        run_cli(
            self.ledgers,
            self.ledger_id,
            "resolve-checkpoint",
            self.repo,
            "--ref",
            checkpoint["ref"],
            "--resolution",
            "excluded",
            "--reason",
            "user omitted provisional goal from ship",
        )
        ready = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")
        self.assertEqual("ready", ready["branches"][0]["push_status"])

    def test_multi_branch_same_remote_is_planned_as_atomic(self) -> None:
        (self.repo / "main.txt").write_text("main\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "main.txt",
            "--message",
            "feat: update main",
            "--acceptance-source",
            "explicit",
        )
        git(self.repo, "switch", "-qc", "feat/secondary", "origin/main")
        run_cli(
            self.ledgers, self.ledger_id, "begin", self.repo, "--merge-target", "main"
        )
        (self.repo / "secondary.txt").write_text("secondary\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "secondary.txt",
            "--message",
            "feat: add secondary",
            "--acceptance-source",
            "explicit",
        )

        result = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")

        self.assertEqual(2, len(result["branches"]))
        self.assertTrue(result["push_groups"][0]["atomic"])
        self.assertEqual("origin", result["push_groups"][0]["remote"])

        shipped = run_cli(self.ledgers, self.ledger_id, "ship", None, "--fetch")
        self.assertTrue(
            all(branch["push_status"] == "pushed" for branch in shipped["branches"])
        )
        self.assertEqual(
            git(self.repo, "rev-parse", "main").stdout.strip(),
            git(self.remote, "rev-parse", "refs/heads/main").stdout.strip(),
        )
        self.assertEqual(
            git(self.repo, "rev-parse", "feat/secondary").stdout.strip(),
            git(self.remote, "rev-parse", "refs/heads/feat/secondary").stdout.strip(),
        )

    def test_merge_plan_infers_stacked_branch_dependencies_and_verification(
        self,
    ) -> None:
        git(self.repo, "switch", "-qc", "feat/api")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "begin",
            self.repo,
            "--merge-target",
            "main",
        )
        (self.repo / "api.txt").write_text("api\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "api.txt",
            "--message",
            "feat(api): add contract",
            "--acceptance-source",
            "objective",
        )
        run_cli(
            self.ledgers,
            self.ledger_id,
            "record-verification",
            self.repo,
            "--verification-command",
            "python3 -m unittest tests.api",
            "--status",
            "passed",
            "--scope",
            "api contract",
            "--evidence",
            "3 passed",
        )

        git(self.repo, "switch", "-qc", "feat/ui")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "begin",
            self.repo,
            "--merge-target",
            "main",
        )
        (self.repo / "ui.txt").write_text("ui\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "ui.txt",
            "--message",
            "feat(ui): consume contract",
            "--acceptance-source",
            "objective",
        )

        result = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")
        branches = {branch["branch"]: branch for branch in result["branches"]}

        self.assertEqual(["feat/api"], branches["feat/ui"]["merge_plan"]["depends_on"])
        verification = branches["feat/api"]["merge_plan"]["post_merge_verification"]
        self.assertEqual("passed", verification[0]["status"])
        self.assertEqual("python3 -m unittest tests.api", verification[0]["command"])

    def test_ship_plan_blocks_verification_from_an_older_head(self) -> None:
        (self.repo / "first.txt").write_text("first\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "first.txt",
            "--message",
            "feat: first change",
            "--acceptance-source",
            "objective",
        )
        run_cli(
            self.ledgers,
            self.ledger_id,
            "record-verification",
            self.repo,
            "--verification-command",
            "python3 -m unittest",
            "--status",
            "passed",
            "--scope",
            "unit tests",
            "--evidence",
            "all passed",
        )
        (self.repo / "second.txt").write_text("second\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "second.txt",
            "--message",
            "feat: second change",
            "--acceptance-source",
            "objective",
        )

        result = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")

        branch = result["branches"][0]
        self.assertEqual("blocked", branch["push_status"])
        self.assertEqual("rerun_stale_verification", branch["divergence_solution"])
        self.assertEqual(1, len(branch["stale_verification"]))

    def test_worktree_and_multi_repo_are_enrolled_in_one_ledger(self) -> None:
        git(self.repo, "branch", "feat/worktree")
        worktree = self.root / "secondary-worktree"
        git(self.repo, "worktree", "add", "-q", str(worktree), "feat/worktree")
        run_cli(
            self.ledgers, self.ledger_id, "begin", worktree, "--merge-target", "main"
        )
        other_repo = init_repo(self.root / "other-repo")
        run_cli(
            self.ledgers, self.ledger_id, "begin", other_repo, "--merge-target", "main"
        )

        result = run_cli(self.ledgers, self.ledger_id, "ledger-status", None)

        self.assertEqual(2, result["repo_count"])
        origin_entry = next(
            repo for repo in result["repos"] if repo["root"] == str(self.repo.resolve())
        )
        self.assertEqual(2, origin_entry["worktree_count"])
        self.assertEqual({"feat/worktree", "main"}, set(origin_entry["branches"]))

    def test_multi_repo_ship_plan_reports_non_atomic_delivery_groups(self) -> None:
        (self.repo / "first.txt").write_text("first\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "first.txt",
            "--message",
            "feat: first repo",
            "--acceptance-source",
            "explicit",
        )

        second_remote = self.root / "second.git"
        run(["git", "init", "-q", "--bare", str(second_remote)])
        second_seed = init_repo(self.root / "second-seed")
        git(second_seed, "remote", "add", "origin", str(second_remote))
        git(second_seed, "push", "-qu", "origin", "main")
        git(second_remote, "symbolic-ref", "HEAD", "refs/heads/main")
        second = clone_repo(second_remote, self.root / "second")
        run_cli(self.ledgers, self.ledger_id, "begin", second, "--merge-target", "main")
        (second / "second.txt").write_text("second\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            second,
            "--path",
            "second.txt",
            "--message",
            "feat: second repo",
            "--acceptance-source",
            "explicit",
        )

        result = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")

        self.assertEqual(2, len(result["push_groups"]))
        self.assertFalse(result["cross_remote_atomic"])


if __name__ == "__main__":
    unittest.main()
