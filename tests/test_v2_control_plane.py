from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import (
    CLI,
    git,
    init_repo,
    load_ledger_state,
    ref_exists,
    run,
    run_cli,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HOOK = PROJECT_ROOT / "hooks" / "checkpoint_guard.py"


class V2ControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.repo = init_repo(self.root / "repo")
        self.ledgers = self.root / "ledgers"
        self.codex_home = self.root / "codex-home"
        self.environment = {"CODEX_HOME": str(self.codex_home)}
        self.ledger_id = "v2-thread"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_hook(
        self, payload: dict, *, configured: bool = True
    ) -> subprocess.CompletedProcess[str]:
        if configured:
            run_cli(self.ledgers, "configure-only", "status", self.repo)
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                **self.environment,
                "PLUGIN_ROOT": str(PROJECT_ROOT),
            },
        )

    def mutation_payload(self, session_id: str, call_id: str) -> dict:
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** End Patch"},
            "session_id": session_id,
            "tool_use_id": call_id,
            "cwd": str(self.repo),
        }

    def test_enter_receipt_is_self_locating_and_records_preflight(self) -> None:
        result = run_cli(self.ledgers, self.ledger_id, "enter", self.repo)

        self.assertEqual(2, result["schema_version"])
        self.assertEqual(str(self.ledgers.resolve()), result["ledger_root"])
        self.assertNotIn("ledger_path", result)
        self.assertEqual(
            str((self.ledgers / "checkpoint-thread.sqlite3").resolve()),
            result["control_plane_path"],
        )
        self.assertEqual("passed", result["preflight"]["result"])
        self.assertEqual("main", result["preflight"]["branch"])
        self.assertEqual(result["head"], result["preflight"]["head"])
        self.assertIsNone(result["preflight"]["operation_state"])
        self.assertEqual(
            {
                "staged": [],
                "unstaged": [],
                "untracked": [],
                "ignored": [],
                "unmerged": [],
                "index_worktree_overlap": [],
            },
            result["preflight"]["initial_changes"],
        )
        self.assertEqual("acquired", result["claim"]["action"])
        self.assertTrue(result["event_id"])
        ledger = load_ledger_state(self.ledgers, self.ledger_id)
        checkpoint = ledger["repos"][result["repo_id"]]["branches"]["main"][
            "checkpoints"
        ][0]
        self.assertEqual(2, ledger["version"])
        self.assertTrue(checkpoint["state_oid"])
        self.assertTrue(Path(result["control_plane_path"]).is_file())
        self.assertEqual([], list(self.ledgers.rglob("ledger.json")))

    def test_non_config_command_rejects_a_different_ledger_root(self) -> None:
        run_cli(self.ledgers, self.ledger_id, "status", self.repo)
        result = run(
            [
                sys.executable,
                CLI,
                "--ledger-root",
                self.root / "other-ledgers",
                "--ledger-id",
                self.ledger_id,
                "status",
                "--repo",
                self.repo,
            ],
            check=False,
            env=self.environment,
        )
        payload = json.loads(result.stdout)

        self.assertEqual(2, result.returncode)
        self.assertEqual("ledger_root_mismatch", payload["error"])
        self.assertEqual(str(self.ledgers.resolve()), payload["configured_ledger_root"])

    def test_legacy_state_and_cli_aliases_are_not_supported(self) -> None:
        run_cli(self.ledgers, "configure-only", "status", self.repo)
        path = self.ledgers / self.ledger_id / "ledger.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "ledger_id": self.ledger_id,
                    "state": "active",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "repos": {},
                }
            ),
            encoding="utf-8",
        )

        result = run_cli(
            self.ledgers,
            self.ledger_id,
            "inspect",
            None,
            "--check",
            expected_code=2,
        )

        self.assertEqual("ledger_not_found", result["error"])
        self.assertEqual(1, json.loads(path.read_text(encoding="utf-8"))["version"])
        self.assertFalse((self.ledgers / "checkpoint-thread.sqlite3").exists())
        for command in ("begin", "ledger-status"):
            legacy = run(
                [sys.executable, CLI, "--ledger-id", self.ledger_id, command],
                check=False,
                env=self.environment,
            )
            self.assertEqual(2, legacy.returncode)
            self.assertIn("invalid choice", legacy.stderr)

    def test_verification_state_is_carried_to_an_exact_promoted_commit(self) -> None:
        run_cli(self.ledgers, self.ledger_id, "enter", self.repo)
        (self.repo / "app.txt").write_text("verified\n", encoding="utf-8")
        verification = run_cli(
            self.ledgers,
            self.ledger_id,
            "record-verification",
            self.repo,
            "--verification-command",
            "python3 -m unittest",
            "--status",
            "passed",
            "--scope",
            "all selected changes",
        )
        promoted = run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "app.txt",
            "--message",
            "feat: verified state",
            "--acceptance-source",
            "objective",
        )

        self.assertTrue(verification["verification"]["state_oid"])
        self.assertEqual(["python3 -m unittest"], promoted["carried_verifications"])
        ledger = load_ledger_state(self.ledgers, self.ledger_id)
        branch = next(iter(ledger["repos"].values()))["branches"]["main"]
        self.assertEqual(
            promoted["commit"], branch["verification"][0]["verified_commit"]
        )

    def test_verification_is_not_carried_when_unrelated_state_was_tested(self) -> None:
        run_cli(self.ledgers, self.ledger_id, "enter", self.repo)
        (self.repo / "app.txt").write_text("selected\n", encoding="utf-8")
        (self.repo / "other.txt").write_text("unrelated\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            self.ledger_id,
            "record-verification",
            self.repo,
            "--verification-command",
            "check-all",
            "--status",
            "passed",
            "--scope",
            "dirty worktree",
        )
        promoted = run_cli(
            self.ledgers,
            self.ledger_id,
            "promote",
            self.repo,
            "--path",
            "app.txt",
            "--message",
            "feat: selected only",
            "--acceptance-source",
            "objective",
        )

        self.assertEqual([], promoted["carried_verifications"])

    def test_concurrent_threads_cannot_claim_the_same_branch(self) -> None:
        run_cli(self.ledgers, "configure-only", "status", self.repo)
        commands = []
        for ledger_id in ("thread-a", "thread-b"):
            commands.append(
                [
                    sys.executable,
                    str(CLI),
                    "--ledger-id",
                    ledger_id,
                    "enter",
                    "--repo",
                    str(self.repo),
                ]
            )
        processes = [
            subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, **self.environment},
            )
            for command in commands
        ]
        results = [process.communicate(timeout=30) for process in processes]
        codes = [process.returncode for process in processes]
        payloads = [json.loads(stdout) for stdout, _ in results]

        self.assertEqual([0, 2], sorted(codes))
        loser = next(payload for payload in payloads if payload["ok"] is False)
        self.assertEqual("branch_claimed", loser["error"])
        refs = git(
            self.repo,
            "for-each-ref",
            "--format=%(refname)",
            "refs/codex/checkpoint-thread/",
        ).stdout.splitlines()
        self.assertEqual(1, len(refs))

    def test_park_releases_the_branch_for_another_thread(self) -> None:
        run_cli(self.ledgers, "thread-a", "enter", self.repo)
        (self.repo / "app.txt").write_text("park me\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            "thread-a",
            "park",
            self.repo,
            "--reason",
            "handoff",
        )

        entered = run_cli(self.ledgers, "thread-b", "enter", self.repo)

        self.assertEqual("entered", entered["action"])
        self.assertEqual("acquired", entered["claim"]["action"])

    def test_completed_operation_id_replays_without_duplicate_event(self) -> None:
        first = run_cli(
            self.ledgers,
            self.ledger_id,
            "guard",
            self.repo,
            operation_id="operation-1",
        )
        second = run_cli(
            self.ledgers,
            self.ledger_id,
            "guard",
            self.repo,
            operation_id="operation-1",
        )
        inspected = run_cli(self.ledgers, self.ledger_id, "inspect")

        self.assertFalse(first.get("replayed", False))
        self.assertTrue(second["replayed"])
        self.assertEqual(first["event_id"], second["event_id"])
        self.assertEqual(1, inspected["event_count"])

    def test_inspect_reports_an_incomplete_operation(self) -> None:
        entered = run_cli(self.ledgers, self.ledger_id, "enter", self.repo)
        run_cli(
            self.ledgers,
            self.ledger_id,
            "restore",
            self.repo,
            "--ref",
            entered["baseline_ref"] + "-missing",
            "--confirm",
            expected_code=2,
            operation_id="failed-restore",
        )

        inspected = run_cli(self.ledgers, self.ledger_id, "inspect", None, "--check")

        self.assertEqual("issues", inspected["integrity"])
        self.assertEqual(
            "failed-restore", inspected["incomplete_operations"][0]["operation_id"]
        )

    def test_inspect_reports_sqlite_integrity_without_projection_repair(self) -> None:
        run_cli(self.ledgers, self.ledger_id, "enter", self.repo)
        legacy = self.ledgers / "legacy-thread" / "ledger.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("{broken\n", encoding="utf-8")

        inspected = run_cli(self.ledgers, self.ledger_id, "inspect", None, "--check")
        doctor = run_cli(self.ledgers, self.ledger_id, "doctor", None)

        self.assertEqual("ok", inspected["database_integrity"])
        self.assertNotIn("projection_matches", inspected)
        self.assertEqual("ok", doctor["integrity"])
        self.assertEqual("ok", doctor["database_integrity"])
        self.assertNotIn("repaired_projections", doctor)

    def test_hook_is_silent_for_read_only_tools_and_creates_no_state(self) -> None:
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "exec_command",
            "tool_input": {"command": "rg -n TODO ."},
            "session_id": "read-only-thread",
            "tool_use_id": "read-only-call",
            "cwd": str(self.repo),
        }
        result = self.run_hook(payload)

        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertFalse((self.ledgers / "checkpoint-thread.sqlite3").exists())

    def test_hook_silently_enters_before_a_mutation(self) -> None:
        result = self.run_hook(self.mutation_payload("hook-thread", "hook-call"))

        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertTrue((self.ledgers / "checkpoint-thread.sqlite3").is_file())
        ledger = load_ledger_state(self.ledgers, "hook-thread")
        branch = next(iter(ledger["repos"].values()))["branches"]["main"]
        self.assertTrue(ref_exists(self.repo, branch["baseline_ref"]))

    def test_hook_denies_a_mutation_when_configuration_is_missing(self) -> None:
        result = self.run_hook(
            self.mutation_payload("unconfigured-thread", "unconfigured-call"),
            configured=False,
        )

        payload = json.loads(result.stdout)
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual("deny", payload["hookSpecificOutput"]["permissionDecision"])
        self.assertIn("ledger_root_not_configured", reason)

    def test_hook_denies_a_second_thread_claiming_the_same_branch(self) -> None:
        first = self.run_hook(self.mutation_payload("hook-a", "hook-call-a"))
        second = self.run_hook(self.mutation_payload("hook-b", "hook-call-b"))

        self.assertEqual("", first.stdout)
        payload = json.loads(second.stdout)
        self.assertEqual("deny", payload["hookSpecificOutput"]["permissionDecision"])
        self.assertIn(
            "branch_claimed",
            payload["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_post_hook_releases_a_clean_no_op_claim(self) -> None:
        mutation = self.mutation_payload("hook-a", "hook-call-a")
        pre = self.run_hook(mutation)
        post = self.run_hook({**mutation, "hook_event_name": "PostToolUse"})

        self.assertEqual("", pre.stdout)
        self.assertEqual("", post.stdout)
        entered = run_cli(self.ledgers, "hook-b", "enter", self.repo)
        self.assertEqual("acquired", entered["claim"]["action"])

    def test_post_hook_retains_a_dirty_claim(self) -> None:
        mutation = self.mutation_payload("hook-a", "hook-call-a")
        self.run_hook(mutation)
        (self.repo / "app.txt").write_text("dirty\n", encoding="utf-8")

        post = self.run_hook({**mutation, "hook_event_name": "PostToolUse"})
        blocked = run_cli(
            self.ledgers,
            "hook-b",
            "enter",
            self.repo,
            expected_code=2,
        )

        self.assertEqual("", post.stdout)
        self.assertEqual("branch_claimed", blocked["error"])

    def test_hook_blocks_direct_git_delivery_commands(self) -> None:
        result = self.run_hook(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "exec_command",
                "tool_input": {
                    "cmd": "git status --short && git push origin main",
                    "workdir": str(self.repo),
                },
                "session_id": "raw-git-thread",
                "tool_use_id": "raw-git-call",
                "cwd": str(self.repo),
            }
        )

        payload = json.loads(result.stdout)
        reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual("deny", payload["hookSpecificOutput"]["permissionDecision"])
        self.assertIn("direct git push", reason)
        self.assertFalse((self.ledgers / "checkpoint-thread.sqlite3").exists())

    def test_successful_ship_releases_the_branch_claim(self) -> None:
        remote = self.root / "remote.git"
        run(["git", "init", "-q", "--bare", str(remote)])
        git(self.repo, "remote", "add", "origin", str(remote))
        git(self.repo, "push", "-qu", "origin", "main")
        run_cli(self.ledgers, "shipper", "enter", self.repo)
        (self.repo / "app.txt").write_text("ship me\n", encoding="utf-8")
        run_cli(
            self.ledgers,
            "shipper",
            "record-verification",
            self.repo,
            "--verification-command",
            "check",
            "--status",
            "passed",
            "--scope",
            "all selected changes",
        )
        run_cli(
            self.ledgers,
            "shipper",
            "promote",
            self.repo,
            "--path",
            "app.txt",
            "--message",
            "feat: ship",
            "--acceptance-source",
            "objective",
        )
        closed = run_cli(
            self.ledgers,
            "shipper",
            "close",
            self.repo,
            "--reason",
            "local task complete",
        )
        bridge = run_cli(self.ledgers, "bridge-thread", "enter", self.repo)
        run_cli(
            self.ledgers,
            "bridge-thread",
            "close",
            self.repo,
            "--reason",
            "claim handoff test",
        )
        run_cli(self.ledgers, "shipper", "ship", None, "--fetch")

        entered = run_cli(self.ledgers, "next-thread", "enter", self.repo)

        self.assertTrue(closed["claim_released"])
        self.assertEqual("acquired", bridge["claim"]["action"])
        self.assertEqual("acquired", entered["claim"]["action"])

    def test_plugin_and_hook_manifests_are_installable_contracts(self) -> None:
        plugin = json.loads(
            (PROJECT_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        hooks = json.loads(
            (PROJECT_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        )
        marketplace = json.loads(
            (PROJECT_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual("checkpoint-thread", plugin["name"])
        self.assertEqual("2.0.0", plugin["version"])
        self.assertEqual("./skill/", plugin["skills"])
        self.assertEqual("checkpoint-thread", marketplace["name"])
        self.assertEqual(".", marketplace["plugins"][0]["source"]["path"])
        pre_tool = hooks["hooks"]["PreToolUse"][0]
        self.assertIn("apply_patch", pre_tool["matcher"])
        self.assertIn("checkpoint_guard.py", pre_tool["hooks"][0]["command"])
        post_tool = hooks["hooks"]["PostToolUse"][0]
        self.assertIn("apply_patch", post_tool["matcher"])
        self.assertIn("checkpoint_guard.py", post_tool["hooks"][0]["command"])

    def test_sqlite_control_plane_uses_version_two_schema(self) -> None:
        run_cli(self.ledgers, self.ledger_id, "enter", self.repo)
        database = self.ledgers / "checkpoint-thread.sqlite3"
        with sqlite3.connect(database) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }

        self.assertEqual(2, version)
        self.assertTrue({"ledgers", "events", "branch_claims", "operations"} <= tables)
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE ledgers SET state_json = ? WHERE ledger_id = ?",
                ("{", self.ledger_id),
            )
            connection.commit()
        invalid_state = run_cli(
            self.ledgers,
            self.ledger_id,
            "inspect",
            None,
            "--check",
            expected_code=2,
        )
        self.assertEqual("control_plane_state_invalid", invalid_state["error"])
        database.write_bytes(b"not a sqlite database")
        failure = run_cli(
            self.ledgers,
            self.ledger_id,
            "inspect",
            None,
            "--check",
            expected_code=2,
        )
        self.assertEqual("control_plane_unreadable", failure["error"])


if __name__ == "__main__":
    unittest.main()
