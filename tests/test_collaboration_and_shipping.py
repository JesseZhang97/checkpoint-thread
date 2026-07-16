from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import clone_repo, git, init_repo, load_ledger_state, run, run_cli


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
            self.ledgers, self.ledger_id, "enter", self.repo, "--merge-target", "main"
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def record_not_applicable(self, repo: Path, ledger_id: str | None = None) -> None:
        run_cli(
            self.ledgers,
            ledger_id or self.ledger_id,
            "record-verification",
            repo,
            "--verification-command",
            "not-applicable",
            "--status",
            "not_applicable",
            "--scope",
            "no repository-defined check",
            "--evidence",
            "fixture-only Git behavior",
        )

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
        self.assertIn("rebase_onto_upstream_then_resolve", branch["blockers"])
        self.assertIn("record_verification_or_not_applicable", branch["blockers"])
        self.assertEqual("main", branch["merge_plan"]["target"])
        self.assertEqual("high", branch["merge_plan"]["conflict_risk"])
        self.assertEqual(
            "rebase_onto_upstream_then_resolve",
            branch["merge_plan"]["pre_merge_action"],
        )
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
        self.record_not_applicable(self.repo)
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
        private_refs = git(
            self.remote,
            "for-each-ref",
            "--format=%(refname)",
            "refs/codex/checkpoint-thread/",
        ).stdout
        self.assertEqual("", private_refs)

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
        self.record_not_applicable(self.repo)
        old_tip = promoted["commit"]
        (self.collaborator / "remote.txt").write_text("remote\n", encoding="utf-8")
        git(self.collaborator, "add", "remote.txt")
        git(self.collaborator, "commit", "-qm", "feat: add remote change")
        git(self.collaborator, "push", "-q")

        result = run_cli(self.ledgers, self.ledger_id, "ship", None, "--fetch")

        self.assertEqual("pushed", result["branches"][0]["push_status"])
        handling = result["branches"][0]["divergence_handling"]
        self.assertEqual("rebased", handling["action"])
        self.assertEqual(old_tip, handling["old_tip"])
        self.assertEqual(
            git(self.repo, "rev-parse", "main").stdout.strip(), handling["new_tip"]
        )
        self.assertIn("pre-rebase", handling["safety_ref"])
        self.assertEqual("single", result["branches"][0]["push_mode"])
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
        self.assertEqual([], safety_refs)
        self.assertIn(
            handling["safety_ref"], result["branches"][0]["pruned_recovery_refs"]
        )

    def test_ship_plan_blocks_local_commits_not_owned_by_the_thread(self) -> None:
        (self.repo / "preexisting.txt").write_text("before thread\n", encoding="utf-8")
        git(self.repo, "add", "preexisting.txt")
        git(self.repo, "commit", "-qm", "chore: preexisting local commit")
        (self.repo / "owned.txt").write_text("thread owned\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "owned.txt",
            "--message",
            "feat: thread owned commit",
            "--acceptance-source",
            "explicit",
        )

        result = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")

        branch = result["branches"][0]
        self.assertEqual("blocked", branch["push_status"])
        self.assertEqual("unattributed_local_commits", branch["attribution_status"])
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
            "enter",
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
        self.assertEqual("unattributed_local_commits", branch["attribution_status"])
        self.assertEqual(1, len(branch["unattributed_local_commits"]))

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
        self.record_not_applicable(self.repo)
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
        self.record_not_applicable(self.repo)
        git(self.repo, "switch", "-qc", "feat/secondary", "origin/main")
        run_cli(
            self.ledgers, self.ledger_id, "enter", self.repo, "--merge-target", "main"
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
        self.record_not_applicable(self.repo)

        result = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")

        self.assertEqual(2, len(result["branches"]))
        self.assertTrue(result["push_groups"][0]["atomic"])
        self.assertEqual("origin", result["push_groups"][0]["remote"])

        shipped = run_cli(self.ledgers, self.ledger_id, "ship", None, "--fetch")
        self.assertTrue(
            all(branch["push_status"] == "pushed" for branch in shipped["branches"])
        )
        self.assertTrue(
            all(branch["push_mode"] == "atomic" for branch in shipped["branches"])
        )
        self.assertEqual(
            git(self.repo, "rev-parse", "main").stdout.strip(),
            git(self.remote, "rev-parse", "refs/heads/main").stdout.strip(),
        )
        self.assertEqual(
            git(self.repo, "rev-parse", "feat/secondary").stdout.strip(),
            git(self.remote, "rev-parse", "refs/heads/feat/secondary").stdout.strip(),
        )

    def test_atomic_group_race_rejects_every_ref_and_reports_every_branch_failed(
        self,
    ) -> None:
        initial_remote_main = git(self.remote, "rev-parse", "main").stdout.strip()
        (self.repo / "main-race.txt").write_text("local main\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "main-race.txt",
            "--message",
            "feat: local main in atomic race",
            "--acceptance-source",
            "explicit",
        )
        self.record_not_applicable(self.repo)
        git(self.repo, "switch", "-qc", "feat/atomic-race", "origin/main")
        run_cli(
            self.ledgers, self.ledger_id, "enter", self.repo, "--merge-target", "main"
        )
        (self.repo / "secondary-race.txt").write_text(
            "local secondary\n", encoding="utf-8"
        )
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "secondary-race.txt",
            "--message",
            "feat: secondary in atomic race",
            "--acceptance-source",
            "explicit",
        )
        self.record_not_applicable(self.repo)

        hook = self.repo / ".git" / "hooks" / "pre-push"
        hook.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            f"printf 'remote race\\n' > '{self.collaborator}/remote-race.txt'\n"
            f"git -C '{self.collaborator}' add remote-race.txt\n"
            f"git -C '{self.collaborator}' commit -qm 'test: advance during atomic push'\n"
            f"git -C '{self.collaborator}' push -q origin main\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "ship",
            None,
            "--fetch",
            expected_code=2,
        )

        self.assertEqual("push_failed", result["error"])
        self.assertTrue(result["atomic"])
        self.assertEqual([], result["completed"])
        self.assertEqual({"main", "feat/atomic-race"}, set(result["branches"]))
        self.assertTrue(
            all(
                branch["push_status"] == "failed"
                and branch["push_mode"] == "atomic"
                and branch["divergence_solution"] == "fetch_and_replan"
                for branch in result["branches_report"]
            )
        )
        remote_main = git(self.remote, "rev-parse", "main").stdout.strip()
        self.assertNotEqual(initial_remote_main, remote_main)
        self.assertNotEqual(
            git(self.repo, "rev-parse", "main").stdout.strip(), remote_main
        )
        self.assertNotEqual(
            0,
            git(
                self.remote,
                "show-ref",
                "--verify",
                "--quiet",
                "refs/heads/feat/atomic-race",
                check=False,
            ).returncode,
        )
        ledger = load_ledger_state(self.ledgers, self.ledger_id)
        self.assertTrue(ledger["last_ship_failure"]["atomic"])
        self.assertEqual([], ledger["last_ship_failure"]["completed"])

    def test_merge_plan_infers_stacked_branch_dependencies_and_verification(
        self,
    ) -> None:
        git(self.repo, "switch", "-qc", "feat/api")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "enter",
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
            "enter",
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

    def test_current_verification_allows_older_passed_scopes_to_remain_history(
        self,
    ) -> None:
        (self.repo / "first.txt").write_text("first\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "first.txt",
            "--message",
            "feat: first verified state",
            "--acceptance-source",
            "objective",
        )
        run_cli(
            self.ledgers,
            self.ledger_id,
            "record-verification",
            self.repo,
            "--verification-command",
            "check-old-scope",
            "--status",
            "passed",
            "--scope",
            "first goal",
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
            "feat: second verified state",
            "--acceptance-source",
            "objective",
        )
        run_cli(
            self.ledgers,
            self.ledger_id,
            "record-verification",
            self.repo,
            "--verification-command",
            "check-release-scope",
            "--status",
            "passed",
            "--scope",
            "current release",
        )

        result = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")

        branch = result["branches"][0]
        self.assertEqual("ready", branch["push_status"])
        self.assertEqual(1, len(branch["current_verification"]))
        self.assertEqual(1, len(branch["stale_verification"]))

    def test_latest_verification_supersedes_an_earlier_failure(self) -> None:
        (self.repo / "verified.txt").write_text("verified\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "verified.txt",
            "--message",
            "feat: add verified change",
            "--acceptance-source",
            "objective",
        )
        verification_args = (
            "--verification-command",
            "python3 -m unittest",
            "--scope",
            "unit tests",
        )
        run_cli(
            self.ledgers,
            self.ledger_id,
            "record-verification",
            self.repo,
            *verification_args,
            "--status",
            "failed",
            "--evidence",
            "1 failed",
        )
        blocked = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")
        self.assertEqual(
            "fix_and_rerun_verification", blocked["branches"][0]["divergence_solution"]
        )

        run_cli(
            self.ledgers,
            self.ledger_id,
            "record-verification",
            self.repo,
            *verification_args,
            "--status",
            "passed",
            "--evidence",
            "all passed",
        )
        ready = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")

        branch = ready["branches"][0]
        self.assertEqual("ready", branch["push_status"])
        self.assertEqual("passed", branch["verification"][0]["status"])
        self.assertEqual(2, len(branch["verification_history"]))

    def test_ship_requires_an_explicit_verification_or_not_applicable_receipt(
        self,
    ) -> None:
        (self.repo / "needs-verification.txt").write_text(
            "verify me\n", encoding="utf-8"
        )
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "needs-verification.txt",
            "--message",
            "feat: require verification receipt",
            "--acceptance-source",
            "explicit",
        )

        blocked = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")
        self.assertEqual(
            "record_verification_or_not_applicable",
            blocked["branches"][0]["divergence_solution"],
        )
        self.assertEqual(
            ["record_verification_or_not_applicable"],
            blocked["branches"][0]["blockers"],
        )

        self.record_not_applicable(self.repo)
        ready = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")
        self.assertEqual("ready", ready["branches"][0]["push_status"])

    def test_ship_plan_blocks_dirty_worktree_and_missing_local_branch(self) -> None:
        git(self.repo, "switch", "-qc", "feat/deleted", "origin/main")
        run_cli(
            self.ledgers, self.ledger_id, "enter", self.repo, "--merge-target", "main"
        )
        (self.repo / "owned.txt").write_text("owned\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "owned.txt",
            "--message",
            "feat: add owned work",
            "--acceptance-source",
            "explicit",
        )
        (self.repo / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
        dirty = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")
        dirty_branch = next(
            item for item in dirty["branches"] if item["branch"] == "feat/deleted"
        )
        self.assertEqual(
            "checkpoint_or_clean_worktree", dirty_branch["divergence_solution"]
        )

        (self.repo / "uncommitted.txt").unlink()
        git(self.repo, "switch", "-q", "main")
        git(self.repo, "branch", "-D", "feat/deleted")
        missing = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")
        missing_branch = next(
            item for item in missing["branches"] if item["branch"] == "feat/deleted"
        )
        self.assertEqual("missing_local_branch", missing_branch["remote_state"])
        self.assertEqual("blocked", missing_branch["push_status"])

    def test_ship_plan_blocks_no_remote_ambiguous_remote_and_fetch_failure(
        self,
    ) -> None:
        no_remote = init_repo(self.root / "no-remote")
        run_cli(self.ledgers, "no-remote", "enter", no_remote, "--merge-target", "main")
        (no_remote / "change.txt").write_text("change\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            "no-remote",
            "promote",
            no_remote,
            "--path",
            "change.txt",
            "--message",
            "feat: local only",
            "--acceptance-source",
            "explicit",
        )
        no_remote_plan = run_cli(self.ledgers, "no-remote", "ship-plan", None)
        self.assertEqual(
            "configure_upstream", no_remote_plan["branches"][0]["divergence_solution"]
        )

        ambiguous = init_repo(self.root / "ambiguous")
        first = self.root / "ambiguous-one.git"
        second = self.root / "ambiguous-two.git"
        run(["git", "init", "-q", "--bare", str(first)])
        run(["git", "init", "-q", "--bare", str(second)])
        git(ambiguous, "remote", "add", "one", str(first))
        git(ambiguous, "remote", "add", "two", str(second))
        run_cli(self.ledgers, "ambiguous", "enter", ambiguous, "--merge-target", "main")
        (ambiguous / "change.txt").write_text("change\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            "ambiguous",
            "promote",
            ambiguous,
            "--path",
            "change.txt",
            "--message",
            "feat: ambiguous remote",
            "--acceptance-source",
            "explicit",
        )
        ambiguous_plan = run_cli(self.ledgers, "ambiguous", "ship-plan", None)
        self.assertEqual(
            "configure_upstream", ambiguous_plan["branches"][0]["divergence_solution"]
        )

        (self.repo / "fetch.txt").write_text("fetch\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "fetch.txt",
            "--message",
            "feat: fetch failure fixture",
            "--acceptance-source",
            "explicit",
        )
        self.remote.rename(self.root / "origin-offline.git")
        fetch = run_cli(
            self.ledgers,
            self.ledger_id,
            "ship-plan",
            None,
            "--fetch",
            expected_code=2,
        )
        self.assertEqual("fetch_failed", fetch["error"])

    def test_push_rejection_returns_final_branch_receipt(self) -> None:
        (self.repo / "rejected.txt").write_text("reject\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "rejected.txt",
            "--message",
            "feat: rejected push",
            "--acceptance-source",
            "explicit",
        )
        self.record_not_applicable(self.repo)
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\necho rejected by collaboration policy >&2\nexit 1\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "ship",
            None,
            "--fetch",
            expected_code=2,
        )

        self.assertEqual("push_failed", result["error"])
        branch = result["branches_report"][0]
        self.assertEqual("failed", branch["push_status"])
        self.assertEqual("fetch_and_replan", branch["divergence_solution"])
        self.assertEqual("single", branch["push_mode"])

    def test_worktree_and_multi_repo_are_enrolled_in_one_ledger(self) -> None:
        git(self.repo, "branch", "feat/worktree")
        worktree = self.root / "secondary-worktree"
        git(self.repo, "worktree", "add", "-q", str(worktree), "feat/worktree")
        run_cli(
            self.ledgers, self.ledger_id, "enter", worktree, "--merge-target", "main"
        )
        other_repo = init_repo(self.root / "other-repo")
        run_cli(
            self.ledgers, self.ledger_id, "enter", other_repo, "--merge-target", "main"
        )

        result = run_cli(self.ledgers, self.ledger_id, "inspect", None)

        self.assertEqual(2, result["repo_count"])
        origin_entry = next(
            repo for repo in result["repos"] if repo["root"] == str(self.repo.resolve())
        )
        self.assertEqual(2, origin_entry["worktree_count"])
        self.assertEqual({"feat/worktree", "main"}, set(origin_entry["branches"]))

    def test_ship_rebases_a_clean_branch_in_its_existing_worktree(self) -> None:
        git(self.repo, "switch", "-qc", "feat/worktree-rebase", "origin/main")
        git(self.repo, "push", "-qu", "origin", "feat/worktree-rebase")
        git(self.repo, "switch", "-q", "main")
        worktree = self.root / "rebase-worktree"
        git(
            self.repo,
            "worktree",
            "add",
            "-q",
            str(worktree),
            "feat/worktree-rebase",
        )
        run_cli(
            self.ledgers,
            self.ledger_id,
            "enter",
            worktree,
            "--merge-target",
            "main",
        )
        (worktree / "local-worktree.txt").write_text("local\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            worktree,
            "--path",
            "local-worktree.txt",
            "--message",
            "feat: add worktree-local change",
            "--acceptance-source",
            "explicit",
        )
        self.record_not_applicable(worktree)

        git(self.collaborator, "fetch", "-q", "origin")
        git(
            self.collaborator,
            "switch",
            "-qc",
            "feat/worktree-rebase",
            "--track",
            "origin/feat/worktree-rebase",
        )
        (self.collaborator / "remote-worktree.txt").write_text(
            "remote\n", encoding="utf-8"
        )
        git(self.collaborator, "add", "remote-worktree.txt")
        git(self.collaborator, "commit", "-qm", "feat: add remote worktree change")
        git(self.collaborator, "push", "-q")

        shipped = run_cli(self.ledgers, self.ledger_id, "ship", None, "--fetch")

        branch = next(
            item
            for item in shipped["branches"]
            if item["branch"] == "feat/worktree-rebase"
        )
        self.assertEqual("pushed", branch["push_status"])
        self.assertEqual("rebased", branch["divergence_handling"]["action"])
        self.assertEqual(
            git(worktree, "rev-parse", "HEAD").stdout.strip(),
            git(
                self.remote, "rev-parse", "refs/heads/feat/worktree-rebase"
            ).stdout.strip(),
        )

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
        self.record_not_applicable(self.repo)

        second_remote = self.root / "second.git"
        run(["git", "init", "-q", "--bare", str(second_remote)])
        second_seed = init_repo(self.root / "second-seed")
        git(second_seed, "remote", "add", "origin", str(second_remote))
        git(second_seed, "push", "-qu", "origin", "main")
        git(second_remote, "symbolic-ref", "HEAD", "refs/heads/main")
        second = clone_repo(second_remote, self.root / "second")
        run_cli(self.ledgers, self.ledger_id, "enter", second, "--merge-target", "main")
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
        self.record_not_applicable(second)

        result = run_cli(self.ledgers, self.ledger_id, "ship-plan", None, "--fetch")

        self.assertEqual(2, len(result["push_groups"]))
        self.assertFalse(result["cross_remote_atomic"])

    def test_cross_repo_partial_push_reports_completed_and_failed_groups(self) -> None:
        (self.repo / "first.txt").write_text("first\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "first.txt",
            "--message",
            "feat: first repository change",
            "--acceptance-source",
            "explicit",
        )
        self.record_not_applicable(self.repo)

        second_remote = self.root / "z-second-origin.git"
        run(["git", "init", "-q", "--bare", str(second_remote)])
        second_seed = init_repo(self.root / "z-second-seed")
        git(second_seed, "remote", "add", "origin", str(second_remote))
        git(second_seed, "push", "-qu", "origin", "main")
        git(second_remote, "symbolic-ref", "HEAD", "refs/heads/main")
        second_initial = git(second_remote, "rev-parse", "main").stdout.strip()
        second = clone_repo(second_remote, self.root / "z-second-repo")
        run_cli(self.ledgers, self.ledger_id, "enter", second, "--merge-target", "main")
        (second / "second.txt").write_text("second\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            second,
            "--path",
            "second.txt",
            "--message",
            "feat: second repository change",
            "--acceptance-source",
            "explicit",
        )
        self.record_not_applicable(second)
        hook = second_remote / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\necho reject second repository >&2\nexit 1\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "ship",
            None,
            "--fetch",
            expected_code=2,
        )

        self.assertEqual("push_failed", result["error"])
        self.assertFalse(result["atomic"])
        self.assertEqual(1, len(result["completed"]))
        by_repo = {item["repo"]: item for item in result["branches_report"]}
        self.assertEqual("pushed", by_repo[str(self.repo.resolve())]["push_status"])
        self.assertEqual("failed", by_repo[str(second.resolve())]["push_status"])
        self.assertEqual(
            git(self.repo, "rev-parse", "main").stdout.strip(),
            git(self.remote, "rev-parse", "main").stdout.strip(),
        )
        self.assertEqual(
            second_initial,
            git(second_remote, "rev-parse", "main").stdout.strip(),
        )


if __name__ == "__main__":
    unittest.main()
