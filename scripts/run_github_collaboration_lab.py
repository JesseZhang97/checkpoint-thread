#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_CLI = (
    PROJECT_ROOT / "skill" / "checkpoint-thread" / "scripts" / "checkpoint_thread.py"
)


class LabError(RuntimeError):
    pass


def run(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    expected: int = 0,
) -> subprocess.CompletedProcess[str]:
    rendered = [str(item) for item in command]
    result = subprocess.run(
        rendered,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != expected:
        raise LabError(
            f"command failed ({result.returncode}, expected {expected}): "
            f"{' '.join(rendered)}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def git(repo: Path, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=repo, expected=expected)


def configure_actor(repo: Path, name: str, email: str) -> None:
    git(repo, "config", "user.name", name)
    git(repo, "config", "user.email", email)


def clone(remote: str, destination: Path, branch: str = "main") -> Path:
    run(["git", "clone", "-q", "--branch", branch, remote, destination])
    configure_actor(destination, "Thread Agent", "thread-agent@example.invalid")
    return destination


def write_fixture(repo: Path, relative: str, content: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def commit_fixture(repo: Path, relative: str, content: str, message: str) -> str:
    write_fixture(repo, relative, content)
    git(repo, "add", "--", relative)
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def checkpoint(
    ledger_root: Path,
    ledger_id: str,
    command: str,
    repo: Path | None = None,
    *extra: str,
) -> dict[str, Any]:
    args = [
        sys.executable,
        str(CHECKPOINT_CLI),
        "--ledger-root",
        str(ledger_root),
        "--ledger-id",
        ledger_id,
        command,
    ]
    if repo is not None:
        args.extend(["--repo", str(repo)])
    args.extend(extra)
    result = run(args)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise LabError(
            f"checkpoint CLI returned invalid JSON: {result.stdout}"
        ) from error


def begin(ledger_root: Path, ledger_id: str, repo: Path) -> dict[str, Any]:
    return checkpoint(
        ledger_root,
        ledger_id,
        "begin",
        repo,
        "--merge-target",
        "main",
    )


def promote(
    ledger_root: Path,
    ledger_id: str,
    repo: Path,
    relative: str,
    message: str,
) -> dict[str, Any]:
    return checkpoint(
        ledger_root,
        ledger_id,
        "promote",
        repo,
        "--path",
        relative,
        "--message",
        message,
        "--acceptance-source",
        "objective",
    )


def create_remote_branch(actor: Path, branch: str) -> None:
    git(actor, "switch", "-qC", branch, "origin/main")
    git(actor, "push", "-qu", "origin", branch)


def clean_divergence(
    remote: str,
    workspace: Path,
    remote_actor: Path,
    run_id: str,
) -> dict[str, Any]:
    branch = f"collab/{run_id}/clean-divergence"
    ledger_id = f"github-{run_id}-clean"
    ledger_root = workspace / "ledgers"
    create_remote_branch(remote_actor, branch)
    local_actor = clone(remote, workspace / "clean-local", branch)
    begin(ledger_root, ledger_id, local_actor)

    local_path = f"collaboration-fixtures/{run_id}-local.txt"
    write_fixture(local_actor, local_path, "local thread change\n")
    promoted = promote(
        ledger_root,
        ledger_id,
        local_actor,
        local_path,
        "feat: add local collaboration fixture",
    )
    old_local_tip = promoted["commit"]

    remote_path = f"collaboration-fixtures/{run_id}-remote.txt"
    remote_tip = commit_fixture(
        remote_actor,
        remote_path,
        "remote collaborator change\n",
        "feat: add remote collaboration fixture",
    )
    git(remote_actor, "push", "-q")

    shipped = checkpoint(ledger_root, ledger_id, "ship", None, "--fetch")
    branch_report = shipped["branches"][0]
    final_tip = git(local_actor, "rev-parse", branch).stdout.strip()
    published_tip = run(
        ["git", "ls-remote", remote, f"refs/heads/{branch}"]
    ).stdout.split()[0]
    safety_refs = git(
        local_actor,
        "for-each-ref",
        "--format=%(refname)",
        f"refs/codex/checkpoint-thread/{ledger_id}/",
    ).stdout.splitlines()
    assert branch_report["push_status"] == "pushed"
    assert branch_report["divergence_handling"]["action"] == "rebased"
    assert branch_report["divergence_handling"]["safety_ref"] in safety_refs
    assert branch_report["push_mode"] == "single"
    assert final_tip == published_tip
    assert old_local_tip != final_tip
    assert any("pre-rebase" in ref for ref in safety_refs)
    return {
        "branch": branch,
        "old_local_tip": old_local_tip,
        "remote_collaborator_tip": remote_tip,
        "final_tip": final_tip,
        "push_status": branch_report["push_status"],
        "divergence_handling": branch_report["divergence_handling"],
        "push_mode": branch_report["push_mode"],
    }


def conflicting_divergence(
    remote: str,
    workspace: Path,
    remote_actor: Path,
    run_id: str,
) -> dict[str, Any]:
    branch = f"collab/{run_id}/conflict"
    ledger_id = f"github-{run_id}-conflict"
    ledger_root = workspace / "ledgers"
    git(remote_actor, "switch", "-qC", branch, "origin/main")
    shared_path = f"collaboration-fixtures/{run_id}-shared.txt"
    commit_fixture(remote_actor, shared_path, "base\n", "test: seed conflict fixture")
    git(remote_actor, "push", "-qu", "origin", branch)

    local_actor = clone(remote, workspace / "conflict-local", branch)
    begin(ledger_root, ledger_id, local_actor)
    write_fixture(local_actor, shared_path, "local version\n")
    promote(
        ledger_root,
        ledger_id,
        local_actor,
        shared_path,
        "fix: apply local conflict version",
    )

    commit_fixture(
        remote_actor,
        shared_path,
        "remote version\n",
        "fix: apply remote conflict version",
    )
    git(remote_actor, "push", "-q")
    plan = checkpoint(ledger_root, ledger_id, "ship-plan", None, "--fetch")
    branch_report = plan["branches"][0]
    assert branch_report["remote_state"] == "conflict"
    assert branch_report["push_status"] == "blocked"
    assert shared_path in branch_report["conflict_paths"]
    assert branch_report["merge_plan"]["conflict_risk"] == "high"
    return {
        "branch": branch,
        "push_status": branch_report["push_status"],
        "remote_state": branch_report["remote_state"],
        "conflict_paths": branch_report["conflict_paths"],
        "solution": branch_report["divergence_solution"],
        "merge_plan": branch_report["merge_plan"],
    }


def unowned_new_branch(
    remote: str,
    workspace: Path,
    run_id: str,
) -> dict[str, Any]:
    branch = f"collab/{run_id}/unowned"
    ledger_id = f"github-{run_id}-unowned"
    ledger_root = workspace / "ledgers"
    local_actor = clone(remote, workspace / "unowned-local")
    git(local_actor, "switch", "-qc", branch, "origin/main")
    pre_thread_path = f"collaboration-fixtures/{run_id}-pre-thread.txt"
    pre_thread_commit = commit_fixture(
        local_actor,
        pre_thread_path,
        "pre-thread\n",
        "chore: add pre-thread collaboration fixture",
    )
    begin(ledger_root, ledger_id, local_actor)
    owned_path = f"collaboration-fixtures/{run_id}-owned.txt"
    write_fixture(local_actor, owned_path, "thread-owned\n")
    promote(
        ledger_root,
        ledger_id,
        local_actor,
        owned_path,
        "feat: add thread-owned collaboration fixture",
    )
    plan = checkpoint(ledger_root, ledger_id, "ship-plan", None, "--fetch")
    branch_report = plan["branches"][0]
    remote_lookup = run(
        ["git", "ls-remote", "--exit-code", remote, f"refs/heads/{branch}"],
        expected=2,
    )
    assert not remote_lookup.stdout
    assert branch_report["push_status"] == "blocked"
    assert pre_thread_commit in branch_report["unowned_local_commits"]
    return {
        "branch": branch,
        "push_status": branch_report["push_status"],
        "ownership_status": branch_report["ownership_status"],
        "unowned_local_commits": branch_report["unowned_local_commits"],
        "remote_branch_created": False,
    }


def atomic_multi_branch(
    remote: str,
    workspace: Path,
    run_id: str,
) -> dict[str, Any]:
    branches = [
        f"collab/{run_id}/atomic-one",
        f"collab/{run_id}/atomic-two",
    ]
    ledger_id = f"github-{run_id}-atomic"
    ledger_root = workspace / "ledgers"
    actor = clone(remote, workspace / "atomic-local")
    for number, branch in enumerate(branches, start=1):
        git(actor, "switch", "-qC", branch, "origin/main")
        begin(ledger_root, ledger_id, actor)
        relative = f"collaboration-fixtures/{run_id}-atomic-{number}.txt"
        write_fixture(actor, relative, f"atomic branch {number}\n")
        promote(
            ledger_root,
            ledger_id,
            actor,
            relative,
            f"feat: add atomic collaboration fixture {number}",
        )

    plan = checkpoint(ledger_root, ledger_id, "ship-plan", None, "--fetch")
    assert len(plan["push_groups"]) == 1
    assert plan["push_groups"][0]["atomic"] is True
    shipped = checkpoint(ledger_root, ledger_id, "ship", None, "--fetch")
    statuses = {item["branch"]: item["push_status"] for item in shipped["branches"]}
    assert all(status == "pushed" for status in statuses.values())
    assert all(item["push_mode"] == "atomic" for item in shipped["branches"])
    published = run(
        [
            "git",
            "ls-remote",
            "--heads",
            remote,
            *(f"refs/heads/{branch}" for branch in branches),
        ]
    ).stdout.splitlines()
    assert len(published) == 2
    return {
        "branches": branches,
        "atomic": True,
        "push_statuses": statuses,
        "published_ref_count": len(published),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run destructive branch-scoped collaboration scenarios on a lab remote."
    )
    parser.add_argument("--remote", required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--allow-remote-mutation",
        action="store_true",
        help="Required acknowledgement; only collab/<run-id> branches are pushed.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.allow_remote_mutation:
        raise LabError("--allow-remote-mutation is required")
    run_id = (
        dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
        + "-"
        + uuid.uuid4().hex[:6]
    )
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.workspace is None:
        temporary = tempfile.TemporaryDirectory(prefix="checkpoint-github-lab-")
        workspace = Path(temporary.name)
    else:
        workspace = args.workspace.expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=False)

    try:
        remote_actor = clone(args.remote, workspace / "remote-actor")
        configure_actor(
            remote_actor,
            "Remote Collaborator",
            "remote-collaborator@example.invalid",
        )
        report = {
            "ok": True,
            "run_id": run_id,
            "remote": args.remote,
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "scenarios": {
                "clean_divergence": clean_divergence(
                    args.remote, workspace, remote_actor, run_id
                ),
                "conflicting_divergence": conflicting_divergence(
                    args.remote, workspace, remote_actor, run_id
                ),
                "unowned_new_branch": unowned_new_branch(
                    args.remote, workspace, run_id
                ),
                "atomic_multi_branch": atomic_multi_branch(
                    args.remote, workspace, run_id
                ),
            },
        }
        report["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        rendered = (
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        if args.report:
            destination = args.report.expanduser().resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
        return 0
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LabError as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2), file=sys.stderr)
        raise SystemExit(2) from error
