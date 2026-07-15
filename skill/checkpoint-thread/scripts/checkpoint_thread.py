#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Sequence


DEFAULT_LARGE_FILE_LIMIT = 25 * 1024 * 1024
SECRET_BASENAMES = {
    ".env",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
    "service-account.json",
}
SECRET_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
SAFE_ENV_NAMES = {".env.example", ".env.sample", ".env.template"}
GENERATED_COMPONENTS = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "coverage",
    "playwright-report",
    "test-results",
}


class CheckpointError(Exception):
    def __init__(self, error: str, **details: Any) -> None:
        super().__init__(error)
        self.payload = {"ok": False, "error": error, **details}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def emit(payload: dict[str, Any], code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return code


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def configuration_path() -> Path:
    return codex_home() / "checkpoint-thread" / "config.json"


def suggested_ledger_root() -> Path:
    return codex_home() / "ledgers" / "checkpoint-thread" / "active"


def run(
    args: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        env={**os.environ, **(env or {})},
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise CheckpointError(
            "command_failed",
            command=list(args),
            returncode=result.returncode,
            stderr=result.stderr.decode("utf-8", "replace").strip(),
        )
    return result


def git(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return run(
        ["git", "--no-pager", *args],
        cwd=repo,
        env=env,
        input_bytes=input_bytes,
        check=check,
    )


def text(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stdout.decode("utf-8", "surrogateescape").strip()


def nul_paths(result: subprocess.CompletedProcess[bytes]) -> list[str]:
    return sorted(
        item.decode("utf-8", "surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    )


def repo_root(repo: Path) -> Path:
    result = git(repo, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0:
        raise CheckpointError("not_a_git_repository", repo=str(repo.resolve()))
    return Path(text(result)).resolve()


def common_git_dir(root: Path) -> Path:
    value = text(git(root, "rev-parse", "--git-common-dir"))
    path = Path(value)
    return (root / path).resolve() if not path.is_absolute() else path.resolve()


def current_branch(root: Path) -> str:
    result = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if result.returncode != 0:
        raise CheckpointError("detached_head", repo=str(root))
    return text(result)


def head_sha(root: Path) -> str | None:
    result = git(root, "rev-parse", "--verify", "HEAD", check=False)
    return text(result) if result.returncode == 0 else None


def upstream_name(root: Path, branch: str) -> str | None:
    result = git(
        root,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        f"{branch}@{{upstream}}",
        check=False,
    )
    return text(result) if result.returncode == 0 else None


def operation_state(root: Path) -> str | None:
    checks = [
        ("MERGE_HEAD", "merge"),
        ("CHERRY_PICK_HEAD", "cherry_pick"),
        ("REVERT_HEAD", "revert"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
    ]
    command = ["rev-parse"]
    for path_name, _ in checks:
        command.extend(["--git-path", path_name])
    resolved = text(git(root, *command)).splitlines()
    for (_, operation), value in zip(checks, resolved, strict=True):
        path = Path(value)
        path = (root / path).resolve() if not path.is_absolute() else path.resolve()
        if path.exists():
            return operation
    return None


def collect_changes(root: Path) -> dict[str, list[str]]:
    staged = nul_paths(git(root, "diff", "--cached", "--name-only", "-z"))
    unstaged = nul_paths(git(root, "diff", "--name-only", "-z"))
    untracked = nul_paths(git(root, "ls-files", "--others", "--exclude-standard", "-z"))
    unmerged = nul_paths(git(root, "diff", "--name-only", "--diff-filter=U", "-z"))
    return {
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "ignored": [],
        "unmerged": unmerged,
        "index_worktree_overlap": sorted(set(staged) & set(unstaged)),
    }


def collect_ignored_paths(root: Path) -> list[str]:
    result = git(
        root,
        "status",
        "--porcelain=v1",
        "-z",
        "--ignored=matching",
        "--untracked-files=normal",
    )
    return sorted(
        item[3:].decode("utf-8", "surrogateescape")
        for item in result.stdout.split(b"\0")
        if item.startswith(b"!! ")
    )


def is_secret_path(path: str) -> bool:
    pure = PurePosixPath(path)
    name = pure.name.lower()
    if name in SAFE_ENV_NAMES:
        return False
    if name in SECRET_BASENAMES or name.startswith(".env."):
        return True
    return pure.suffix.lower() in SECRET_SUFFIXES


def is_generated_output(path: str) -> bool:
    pure = PurePosixPath(path)
    return (
        bool(set(part.lower() for part in pure.parts) & GENERATED_COMPONENTS)
        or pure.suffix == ".pyc"
    )


def large_untracked_paths(root: Path, paths: Sequence[str], limit: int) -> list[str]:
    large: list[str] = []
    for relative in paths:
        path = root / relative
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_size > limit:
                large.append(relative)
        except OSError:
            continue
    return sorted(large)


def ensure_clean_operation_state(root: Path) -> dict[str, list[str]]:
    changes = collect_changes(root)
    if changes["unmerged"]:
        raise CheckpointError("unmerged_index", paths=changes["unmerged"])
    operation = operation_state(root)
    if operation:
        raise CheckpointError("git_operation_in_progress", operation=operation)
    return changes


def safe_component(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._/-]+", "-", value).strip("./-")
    sanitized = sanitized.replace("..", "-").replace("@{", "-")
    if not sanitized:
        raise CheckpointError("invalid_ref_component", value=value)
    return sanitized


def repo_id_for(common_dir: Path) -> str:
    return hashlib.sha256(str(common_dir).encode()).hexdigest()[:16]


def worktree_id_for(root: Path) -> str:
    return hashlib.sha256(str(root).encode()).hexdigest()[:16]


def ledger_path(root: Path, ledger_id: str) -> Path:
    return root / safe_component(ledger_id) / "ledger.json"


def load_configuration() -> dict[str, Any] | None:
    path = configuration_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CheckpointError(
            "configuration_unreadable", path=str(path), detail=str(error)
        ) from error
    if payload.get("version") != 1 or not isinstance(payload.get("ledger_root"), str):
        raise CheckpointError("configuration_invalid", path=str(path))
    return payload


def save_configuration(ledger_root: Path) -> None:
    path = configuration_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "ledger_root": str(ledger_root),
        "updated_at": now_iso(),
    }
    fd, temporary = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def ledger_lock(root: Path, ledger_id: str) -> Iterator[None]:
    directory = root / safe_component(ledger_id)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / ".lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def new_ledger(ledger_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "ledger_id": ledger_id,
        "state": "active",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "repos": {},
    }


def load_ledger(root: Path, ledger_id: str, *, required: bool = True) -> dict[str, Any]:
    path = ledger_path(root, ledger_id)
    if not path.exists():
        if required:
            raise CheckpointError("ledger_not_found", ledger_id=ledger_id)
        return new_ledger(ledger_id)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CheckpointError(
            "ledger_unreadable", path=str(path), detail=str(error)
        ) from error


def save_ledger(root: Path, ledger_id: str, ledger: dict[str, Any]) -> None:
    path = ledger_path(root, ledger_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger["updated_at"] = now_iso()
    fd, temporary = tempfile.mkstemp(prefix="ledger-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(ledger, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def find_repo_entry(ledger: dict[str, Any], root: Path) -> tuple[str, dict[str, Any]]:
    common_dir = common_git_dir(root)
    repo_id = repo_id_for(common_dir)
    entry = ledger["repos"].get(repo_id)
    if entry is None:
        raise CheckpointError("repo_not_enrolled", repo=str(root))
    return repo_id, entry


def find_branch_entry(
    ledger: dict[str, Any], root: Path, branch: str
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    repo_id, repo_entry = find_repo_entry(ledger, root)
    branch_entry = repo_entry["branches"].get(branch)
    if branch_entry is None:
        raise CheckpointError("branch_not_enrolled", repo=str(root), branch=branch)
    return repo_id, repo_entry, branch_entry


def commit_tree(root: Path, tree: str, parent: str | None, message: str) -> str:
    args = ["commit-tree", tree]
    if parent:
        args.extend(["-p", parent])
    identity = {
        "GIT_AUTHOR_NAME": "Codex Checkpoint",
        "GIT_AUTHOR_EMAIL": "checkpoint@local.invalid",
        "GIT_COMMITTER_NAME": "Codex Checkpoint",
        "GIT_COMMITTER_EMAIL": "checkpoint@local.invalid",
    }
    return text(git(root, *args, env=identity, input_bytes=(message + "\n").encode()))


def pathspec_bytes(paths: Sequence[str]) -> bytes:
    return b"".join(os.fsencode(path) + b"\0" for path in paths)


def git_add_paths(
    root: Path,
    paths: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    intent_to_add: bool = False,
) -> None:
    if not paths:
        return
    args = ["add"]
    args.append("--intent-to-add" if intent_to_add else "-A")
    args.extend(["--pathspec-from-file=-", "--pathspec-file-nul"])
    git(root, *args, env=env, input_bytes=pathspec_bytes(paths))


def snapshot_internal(
    *,
    ledger: dict[str, Any],
    ledger_id: str,
    root: Path,
    branch: str,
    kind: str,
    reason: str,
    large_file_limit: int,
) -> dict[str, Any]:
    changes = ensure_clean_operation_state(root)
    ignored = collect_ignored_paths(root)
    _, _, branch_entry = find_branch_entry(ledger, root, branch)
    secret = sorted(
        path for path in set(sum(changes.values(), [])) if is_secret_path(path)
    )
    staged_secret = sorted(set(secret) & set(changes["staged"]))
    if staged_secret:
        raise CheckpointError(
            "staged_secret_paths",
            paths=staged_secret,
            message="Remove secret paths from the index before creating a Git snapshot.",
        )
    generated = sorted(
        path for path in changes["untracked"] if is_generated_output(path)
    )
    large = large_untracked_paths(root, changes["untracked"], large_file_limit)
    excluded_secret = sorted(
        set(secret) & (set(changes["unstaged"]) | set(changes["untracked"]))
    )
    excluded = set(generated) | set(large) | set(excluded_secret)

    index_tree = text(git(root, "write-tree"))
    head = head_sha(root)
    sequence = len(branch_entry["checkpoints"]) + 1
    prefix = f"checkpoint-thread {ledger_id} {branch} {sequence:04d}"
    index_commit = commit_tree(root, index_tree, head, f"{prefix} index")

    fd, temporary_index = tempfile.mkstemp(prefix="checkpoint-index-")
    os.close(fd)
    os.unlink(temporary_index)
    temp_env = {"GIT_INDEX_FILE": temporary_index}
    try:
        git(root, "read-tree", index_tree, env=temp_env)
        worktree_paths = sorted(
            (set(changes["unstaged"]) | set(changes["untracked"])) - excluded
        )
        git_add_paths(root, worktree_paths, env=temp_env)
        worktree_tree = text(git(root, "write-tree", env=temp_env))
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_index)

    worktree_commit = commit_tree(
        root, worktree_tree, index_commit, f"{prefix} worktree"
    )
    ref = (
        "refs/codex/checkpoint-thread/"
        f"{safe_component(ledger_id)}/{safe_component(branch)}/{sequence:04d}-{safe_component(kind)}"
    )
    git(root, "update-ref", ref, worktree_commit)
    checkpoint = {
        "sequence": sequence,
        "kind": kind,
        "reason": reason,
        "created_at": now_iso(),
        "head": head,
        "index_commit": index_commit,
        "worktree_commit": worktree_commit,
        "ref": ref,
        "complete": not excluded,
        "excluded": {
            "secret": excluded_secret,
            "ignored": ignored,
            "large_untracked": large,
            "generated_output": generated,
        },
    }
    branch_entry["checkpoints"].append(checkpoint)
    return checkpoint


def command_begin(args: argparse.Namespace) -> dict[str, Any]:
    root = repo_root(args.repo)
    changes = ensure_clean_operation_state(root)
    branch = current_branch(root)
    common_dir = common_git_dir(root)
    repo_id = repo_id_for(common_dir)
    worktree_id = worktree_id_for(root)
    with ledger_lock(args.ledger_root, args.ledger_id):
        ledger = load_ledger(args.ledger_root, args.ledger_id, required=False)
        repo_entry = ledger["repos"].get(repo_id)
        if repo_entry is None:
            repo_entry = {
                "id": repo_id,
                "root": str(root),
                "common_git_dir": str(common_dir),
                "worktrees": {},
                "branches": {},
            }
            ledger["repos"][repo_id] = repo_entry
        worktree_entry = repo_entry["worktrees"].setdefault(
            worktree_id,
            {"path": str(root), "branches": []},
        )
        if branch not in worktree_entry["branches"]:
            worktree_entry["branches"].append(branch)
            worktree_entry["branches"].sort()
        existing = repo_entry["branches"].get(branch)
        if existing is not None:
            save_ledger(args.ledger_root, args.ledger_id, ledger)
            return {
                "ok": True,
                "action": "already_active",
                "ledger_id": args.ledger_id,
                "repo_id": repo_id,
                "repo": str(root),
                "branch": branch,
                "head": existing["baseline_head"],
                "baseline_ref": existing["baseline_ref"],
            }

        branch_entry = {
            "branch": branch,
            "baseline_head": head_sha(root),
            "baseline_ref": None,
            "upstream": upstream_name(root, branch),
            "merge_target": args.merge_target,
            "initial_changes": changes,
            "checkpoints": [],
            "thread_commits": [],
            "verification": [],
        }
        repo_entry["branches"][branch] = branch_entry
        checkpoint = snapshot_internal(
            ledger=ledger,
            ledger_id=args.ledger_id,
            root=root,
            branch=branch,
            kind="baseline",
            reason="thread baseline",
            large_file_limit=args.large_file_limit,
        )
        branch_entry["baseline_ref"] = checkpoint["ref"]
        save_ledger(args.ledger_root, args.ledger_id, ledger)
        return {
            "ok": True,
            "action": "begun",
            "ledger_id": args.ledger_id,
            "repo_id": repo_id,
            "repo": str(root),
            "branch": branch,
            "head": branch_entry["baseline_head"],
            "baseline_ref": checkpoint["ref"],
            "baseline_complete": checkpoint["complete"],
        }


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    root = repo_root(args.repo)
    branch = current_branch(root)
    changes = collect_changes(root)
    operation = operation_state(root)
    ledger = load_ledger(args.ledger_root, args.ledger_id, required=False)
    repo_id = repo_id_for(common_git_dir(root))
    repo_entry = ledger["repos"].get(repo_id)
    branch_registered = bool(repo_entry and branch in repo_entry["branches"])
    if changes["unmerged"] or operation:
        action = "blocked"
    elif not ledger_path(args.ledger_root, args.ledger_id).exists():
        action = "begin"
    elif not branch_registered:
        action = "enroll"
    else:
        action = "continue"
    return {
        "ok": True,
        "action": action,
        "ledger_id": args.ledger_id,
        "repo": str(root),
        "repo_id": repo_id,
        "branch": branch,
        "branch_registered": branch_registered,
        "operation": operation,
        "changes": changes,
    }


def command_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    root = repo_root(args.repo)
    ensure_clean_operation_state(root)
    branch = current_branch(root)
    with ledger_lock(args.ledger_root, args.ledger_id):
        ledger = load_ledger(args.ledger_root, args.ledger_id)
        checkpoint = snapshot_internal(
            ledger=ledger,
            ledger_id=args.ledger_id,
            root=root,
            branch=branch,
            kind=args.kind,
            reason=args.reason,
            large_file_limit=args.large_file_limit,
        )
        save_ledger(args.ledger_root, args.ledger_id, ledger)
        return {"ok": True, **checkpoint}


def remove_untracked_path(root: Path, relative: str) -> None:
    path = root / relative
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        path.rmdir()
    parent = path.parent
    while parent != root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def command_park(args: argparse.Namespace) -> dict[str, Any]:
    root = repo_root(args.repo)
    branch = current_branch(root)
    if head_sha(root) is None:
        raise CheckpointError(
            "unborn_branch_cannot_park", repo=str(root), branch=branch
        )
    original_changes = ensure_clean_operation_state(root)
    with ledger_lock(args.ledger_root, args.ledger_id):
        ledger = load_ledger(args.ledger_root, args.ledger_id)
        _, _, branch_entry = find_branch_entry(ledger, root, branch)
        checkpoint = snapshot_internal(
            ledger=ledger,
            ledger_id=args.ledger_id,
            root=root,
            branch=branch,
            kind="safety",
            reason=args.reason,
            large_file_limit=args.large_file_limit,
        )
        save_ledger(args.ledger_root, args.ledger_id, ledger)
        if not checkpoint["complete"]:
            raise CheckpointError(
                "snapshot_incomplete",
                ref=checkpoint["ref"],
                excluded=checkpoint["excluded"],
            )
        restored = git(
            root,
            "restore",
            "--source=HEAD",
            "--staged",
            "--worktree",
            "--",
            ".",
            check=False,
        )
        if restored.returncode != 0:
            raise CheckpointError(
                "park_restore_head_failed",
                ref=checkpoint["ref"],
                stderr=restored.stderr.decode("utf-8", "replace").strip(),
            )
        for relative in original_changes["untracked"]:
            remove_untracked_path(root, relative)
        remaining = collect_changes(root)
        if remaining["staged"] or remaining["unstaged"] or remaining["untracked"]:
            raise CheckpointError(
                "park_incomplete",
                ref=checkpoint["ref"],
                remaining=remaining,
            )
        branch_entry["parked_ref"] = checkpoint["ref"]
        branch_entry["state"] = "parked"
        save_ledger(args.ledger_root, args.ledger_id, ledger)
        return {
            "ok": True,
            "action": "parked",
            "repo": str(root),
            "branch": branch,
            **checkpoint,
        }


def checkpoint_for_ref(branch_entry: dict[str, Any], ref: str) -> dict[str, Any]:
    for checkpoint in branch_entry["checkpoints"]:
        if checkpoint["ref"] == ref:
            return checkpoint
    raise CheckpointError("checkpoint_ref_not_in_ledger", ref=ref)


def command_restore(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm:
        raise CheckpointError("restore_requires_confirmation", ref=args.ref)
    root = repo_root(args.repo)
    branch = current_branch(root)
    changes = ensure_clean_operation_state(root)
    if changes["staged"] or changes["unstaged"] or changes["untracked"]:
        raise CheckpointError("restore_target_dirty", changes=changes)
    with ledger_lock(args.ledger_root, args.ledger_id):
        ledger = load_ledger(args.ledger_root, args.ledger_id)
        _, _, branch_entry = find_branch_entry(ledger, root, branch)
        checkpoint = checkpoint_for_ref(branch_entry, args.ref)
        if not checkpoint["complete"]:
            raise CheckpointError(
                "checkpoint_incomplete",
                ref=args.ref,
                excluded=checkpoint["excluded"],
            )
        current_head = head_sha(root)
        if current_head != checkpoint["head"]:
            raise CheckpointError(
                "restore_head_mismatch",
                ref=args.ref,
                saved_head=checkpoint["head"],
                current_head=current_head,
            )
        assert current_head is not None
        restore_paths = nul_paths(
            git(
                root,
                "diff",
                "--name-only",
                "-z",
                current_head,
                checkpoint["worktree_commit"],
            )
        )
        if restore_paths:
            worktree_restore = git(
                root,
                "restore",
                f"--source={checkpoint['worktree_commit']}",
                "--worktree",
                "--",
                *restore_paths,
                check=False,
            )
            if worktree_restore.returncode != 0:
                raise CheckpointError(
                    "worktree_restore_failed",
                    ref=args.ref,
                    stderr=worktree_restore.stderr.decode("utf-8", "replace").strip(),
                )
        git(root, "read-tree", checkpoint["index_commit"])
        branch_entry["state"] = "active"
        branch_entry["restored_ref"] = args.ref
        save_ledger(args.ledger_root, args.ledger_id, ledger)
        return {
            "ok": True,
            "action": "restored",
            "repo": str(root),
            "branch": branch,
            "ref": args.ref,
            "paths": restore_paths,
        }


def normalize_selected_paths(root: Path, paths: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    for value in paths:
        pure = PurePosixPath(value)
        if pure.is_absolute() or ".." in pure.parts or value in {"", "."}:
            raise CheckpointError("invalid_path", path=value)
        relative = pure.as_posix()
        exists = (root / relative).exists() or (root / relative).is_symlink()
        tracked = git(root, "ls-files", "--error-unmatch", "--", relative, check=False)
        tracked_in_head = git(
            root,
            "cat-file",
            "-e",
            f"HEAD:{relative}",
            check=False,
        )
        if not exists and tracked.returncode != 0 and tracked_in_head.returncode != 0:
            raise CheckpointError("path_not_found", path=relative)
        normalized.append(relative)
    return sorted(set(normalized))


def command_promote(args: argparse.Namespace) -> dict[str, Any]:
    root = repo_root(args.repo)
    changes = ensure_clean_operation_state(root)
    branch = current_branch(root)
    selected = normalize_selected_paths(root, args.path)
    selected_set = set(selected)
    all_changes = (
        set(changes["staged"]) | set(changes["unstaged"]) | set(changes["untracked"])
    )
    unchanged = sorted(
        path
        for path in selected_set - all_changes
        if git(root, "diff", "--quiet", "HEAD", "--", path, check=False).returncode == 0
    )
    if unchanged:
        raise CheckpointError("selected_paths_unchanged", paths=unchanged)
    overlap = sorted(selected_set & set(changes["index_worktree_overlap"]))
    if overlap:
        raise CheckpointError("selected_path_has_partial_staging", paths=overlap)
    secret = sorted(path for path in selected if is_secret_path(path))
    untracked_selected = sorted(selected_set & set(changes["untracked"]))
    large = large_untracked_paths(root, untracked_selected, args.large_file_limit)
    generated = sorted(path for path in untracked_selected if is_generated_output(path))
    if secret or large or generated:
        raise CheckpointError(
            "unsafe_paths",
            secret=secret,
            large_untracked=large,
            generated_output=generated,
        )

    with ledger_lock(args.ledger_root, args.ledger_id):
        ledger = load_ledger(args.ledger_root, args.ledger_id)
        _, _, branch_entry = find_branch_entry(ledger, root, branch)
        preexisting = set(branch_entry["initial_changes"]["staged"])
        preexisting |= set(branch_entry["initial_changes"]["unstaged"])
        preexisting |= set(branch_entry["initial_changes"]["untracked"])
        ambiguous = sorted(selected_set & preexisting)
        if ambiguous:
            raise CheckpointError("selected_path_preexisting", paths=ambiguous)

        checkpoint = snapshot_internal(
            ledger=ledger,
            ledger_id=args.ledger_id,
            root=root,
            branch=branch,
            kind="confirmed",
            reason=f"promotion: {args.acceptance_source}",
            large_file_limit=args.large_file_limit,
        )
        save_ledger(args.ledger_root, args.ledger_id, ledger)
        intent_paths = [
            path
            for path in untracked_selected
            if git(
                root, "ls-files", "--error-unmatch", "--", path, check=False
            ).returncode
            != 0
        ]
        if intent_paths:
            git_add_paths(root, intent_paths, intent_to_add=True)
        diff_check = git(root, "diff", "--check", "HEAD", "--", *selected, check=False)
        if diff_check.returncode != 0:
            if intent_paths:
                git(root, "reset", "-q", "--", *intent_paths, check=False)
            save_ledger(args.ledger_root, args.ledger_id, ledger)
            raise CheckpointError(
                "diff_check_failed",
                details=diff_check.stdout.decode("utf-8", "replace").strip(),
                checkpoint_ref=checkpoint["ref"],
            )
        commit = git(
            root,
            "commit",
            "--only",
            "-m",
            args.message,
            "--pathspec-from-file=-",
            "--pathspec-file-nul",
            input_bytes=pathspec_bytes(selected),
            check=False,
        )
        if commit.returncode != 0:
            if intent_paths:
                git(root, "reset", "-q", "--", *intent_paths, check=False)
            save_ledger(args.ledger_root, args.ledger_id, ledger)
            raise CheckpointError(
                "commit_failed",
                checkpoint_ref=checkpoint["ref"],
                stderr=commit.stderr.decode("utf-8", "replace").strip(),
            )
        commit_sha = head_sha(root)
        assert commit_sha is not None
        record = {
            "commit": commit_sha,
            "message": args.message,
            "paths": selected,
            "acceptance_source": args.acceptance_source,
            "checkpoint_ref": checkpoint["ref"],
            "created_at": now_iso(),
        }
        checkpoint["resolution"] = "promoted"
        checkpoint["resolved_commit"] = commit_sha
        if args.checkpoint_ref:
            source_checkpoint = checkpoint_for_ref(branch_entry, args.checkpoint_ref)
            source_checkpoint["resolution"] = "promoted"
            source_checkpoint["resolved_commit"] = commit_sha
        branch_entry["thread_commits"].append(record)
        save_ledger(args.ledger_root, args.ledger_id, ledger)
        return {
            "ok": True,
            "action": "promoted",
            "repo": str(root),
            "branch": branch,
            "commit": commit_sha,
            "paths": selected,
            "checkpoint_ref": checkpoint["ref"],
        }


def command_resolve_checkpoint(args: argparse.Namespace) -> dict[str, Any]:
    root = repo_root(args.repo)
    branch = current_branch(root)
    with ledger_lock(args.ledger_root, args.ledger_id):
        ledger = load_ledger(args.ledger_root, args.ledger_id)
        _, _, branch_entry = find_branch_entry(ledger, root, branch)
        checkpoint = checkpoint_for_ref(branch_entry, args.ref)
        if checkpoint["kind"] == "baseline":
            raise CheckpointError("baseline_cannot_be_resolved", ref=args.ref)
        checkpoint["resolution"] = args.resolution
        checkpoint["resolution_reason"] = args.reason
        checkpoint["resolved_at"] = now_iso()
        if branch_entry.get("parked_ref") == args.ref:
            branch_entry["state"] = "active"
        save_ledger(args.ledger_root, args.ledger_id, ledger)
        return {
            "ok": True,
            "action": "checkpoint_resolved",
            "repo": str(root),
            "branch": branch,
            "ref": args.ref,
            "resolution": args.resolution,
        }


def command_record_verification(args: argparse.Namespace) -> dict[str, Any]:
    root = repo_root(args.repo)
    branch = current_branch(root)
    with ledger_lock(args.ledger_root, args.ledger_id):
        ledger = load_ledger(args.ledger_root, args.ledger_id)
        _, _, branch_entry = find_branch_entry(ledger, root, branch)
        record = {
            "command": args.verification_command,
            "status": args.status,
            "scope": args.scope,
            "evidence": args.evidence,
            "recorded_at": now_iso(),
            "head": head_sha(root),
        }
        branch_entry["verification"].append(record)
        save_ledger(args.ledger_root, args.ledger_id, ledger)
        return {
            "ok": True,
            "action": "verification_recorded",
            "repo": str(root),
            "branch": branch,
            "verification": record,
        }


def configured_remote(
    root: Path, branch: str, entry: dict[str, Any]
) -> tuple[str | None, str]:
    upstream = entry.get("upstream")
    if upstream and "/" in upstream:
        remote, upstream_branch = upstream.split("/", 1)
        return remote, upstream_branch
    result = git(root, "config", "--get", f"branch.{branch}.remote", check=False)
    if result.returncode == 0 and text(result) not in {"", "."}:
        remote = text(result)
    else:
        remotes = [line for line in text(git(root, "remote")).splitlines() if line]
        remote = remotes[0] if len(remotes) == 1 else None
    merge = git(root, "config", "--get", f"branch.{branch}.merge", check=False)
    upstream_branch = (
        text(merge).removeprefix("refs/heads/") if merge.returncode == 0 else branch
    )
    return remote, upstream_branch


def ref_exists(root: Path, ref: str) -> bool:
    return (
        git(root, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0
    )


def rev_count(root: Path, revision_range: str) -> int:
    return int(text(git(root, "rev-list", "--count", revision_range)) or "0")


def changed_paths(root: Path, base: str, tip: str) -> set[str]:
    return set(nul_paths(git(root, "diff", "--name-only", "-z", base, tip)))


def rev_list(root: Path, revision_range: str) -> list[str]:
    result = text(git(root, "rev-list", "--reverse", revision_range))
    return [line for line in result.splitlines() if line]


def remote_tracking_refs(root: Path, remote: str) -> list[str]:
    result = text(
        git(
            root,
            "for-each-ref",
            "--format=%(refname)",
            f"refs/remotes/{remote}/",
        )
    )
    return [line for line in result.splitlines() if line]


def commits_not_on_remote(root: Path, local_ref: str, remote: str) -> list[str]:
    remote_refs = remote_tracking_refs(root, remote)
    command = ["rev-list", "--reverse", local_ref]
    if remote_refs:
        command.extend(["--not", *remote_refs])
    result = text(git(root, *command))
    return [line for line in result.splitlines() if line]


def infer_merge_target(root: Path, remote: str | None) -> str | None:
    if not remote:
        return None
    symbolic = git(
        root,
        "symbolic-ref",
        "--quiet",
        "--short",
        f"refs/remotes/{remote}/HEAD",
        check=False,
    )
    if symbolic.returncode == 0:
        value = text(symbolic)
        return value.split("/", 1)[1] if "/" in value else value
    return None


def infer_branch_dependencies(
    root: Path,
    branch: str,
    entry: dict[str, Any],
    repo_entry: dict[str, Any],
) -> list[str]:
    dependencies = set(entry.get("depends_on", []))
    baseline = entry.get("baseline_head")
    if not baseline:
        return sorted(dependencies)
    for other_branch, other_entry in repo_entry["branches"].items():
        if other_branch == branch or not other_entry["thread_commits"]:
            continue
        other_tip = other_entry["thread_commits"][-1]["commit"]
        ancestor = git(
            root,
            "merge-base",
            "--is-ancestor",
            other_tip,
            baseline,
            check=False,
        )
        if ancestor.returncode == 0:
            dependencies.add(other_branch)
    return sorted(dependencies)


def effective_verifications(history: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    order: list[tuple[str, str]] = []
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in history:
        key = (record["command"], record["scope"])
        if key not in latest:
            order.append(key)
        latest[key] = record
    return [latest[key] for key in order]


def primary_blocker(blockers: Sequence[str]) -> str:
    priority = [
        "rebase_onto_upstream_then_resolve",
        "separate_or_confirm_local_commits",
        "promote_or_exclude_unresolved_checkpoints",
        "checkpoint_or_clean_worktree",
        "fix_and_rerun_verification",
        "rerun_stale_verification",
        "configure_upstream",
        "record_verification_or_not_applicable",
        "rebase_onto_upstream",
    ]
    for candidate in priority:
        if candidate in blockers:
            return candidate
    return blockers[0]


def build_ship_plan(ledger: dict[str, Any], *, fetch: bool) -> dict[str, Any]:
    fetched: set[tuple[str, str]] = set()
    branches: list[dict[str, Any]] = []
    for repo_id, repo_entry in ledger["repos"].items():
        root = Path(repo_entry["root"])
        for branch, entry in repo_entry["branches"].items():
            unresolved = [
                checkpoint["ref"]
                for checkpoint in entry["checkpoints"]
                if checkpoint["kind"] != "baseline" and not checkpoint.get("resolution")
            ]
            if not entry["thread_commits"] and not unresolved:
                continue
            local_ref = f"refs/heads/{branch}"
            if not ref_exists(root, local_ref):
                branches.append(
                    {
                        "repo_id": repo_id,
                        "repo": str(root),
                        "branch": branch,
                        "push_status": "blocked",
                        "remote_state": "missing_local_branch",
                        "divergence_solution": "restore_or_remove_branch_from_ledger",
                        "blockers": ["restore_or_remove_branch_from_ledger"],
                    }
                )
                continue
            remote, remote_branch = configured_remote(root, branch, entry)
            if fetch and remote and (repo_id, remote) not in fetched:
                fetch_result = git(root, "fetch", remote, check=False)
                if fetch_result.returncode != 0:
                    raise CheckpointError(
                        "fetch_failed",
                        repo=str(root),
                        remote=remote,
                        stderr=fetch_result.stderr.decode("utf-8", "replace").strip(),
                    )
                fetched.add((repo_id, remote))
            remote_ref = f"refs/remotes/{remote}/{remote_branch}" if remote else None
            local_tip = text(git(root, "rev-parse", local_ref))
            conflict_paths: list[str] = []
            unowned_local_commits: list[str] = []
            ownership_status = "thread_owned"
            owned = {item["commit"] for item in entry["thread_commits"]}
            unpublished_thread_commits: list[str] = []
            ahead = 0
            behind = 0
            if not remote:
                remote_state = "no_remote"
                push_status = "blocked"
                solution = "configure_upstream"
                unpublished_thread_commits = [
                    item["commit"] for item in entry["thread_commits"]
                ]
            elif not remote_ref or not ref_exists(root, remote_ref):
                remote_state = "new"
                push_status = "ready"
                solution = "push_new_branch"
                local_unpublished = commits_not_on_remote(root, local_ref, remote)
                unowned_local_commits = sorted(set(local_unpublished) - owned)
                unpublished_thread_commits = [
                    item["commit"] for item in entry["thread_commits"]
                ]
            else:
                ahead = rev_count(root, f"{remote_ref}..{local_ref}")
                behind = rev_count(root, f"{local_ref}..{remote_ref}")
                local_only = rev_list(root, f"{remote_ref}..{local_ref}")
                local_unpublished = commits_not_on_remote(root, local_ref, remote)
                unowned_local_commits = sorted(set(local_unpublished) - owned)
                unpublished_thread_commits = [
                    item["commit"]
                    for item in entry["thread_commits"]
                    if item["commit"] in local_only
                ]
                if ahead and behind:
                    merge = git(
                        root,
                        "merge-tree",
                        "--write-tree",
                        "--quiet",
                        local_ref,
                        remote_ref,
                        check=False,
                    )
                    base = text(git(root, "merge-base", local_ref, remote_ref))
                    overlap = changed_paths(root, base, local_ref) & changed_paths(
                        root, base, remote_ref
                    )
                    conflict_paths = sorted(overlap) if merge.returncode != 0 else []
                    if merge.returncode != 0:
                        remote_state = "conflict"
                        solution = "rebase_onto_upstream_then_resolve"
                    else:
                        remote_state = "diverged"
                        solution = "rebase_onto_upstream"
                    push_status = "blocked"
                elif behind:
                    remote_state = "behind"
                    push_status = "blocked"
                    solution = "rebase_onto_upstream"
                elif ahead:
                    remote_state = "ahead"
                    push_status = "ready"
                    solution = "push"
                else:
                    remote_state = "up_to_date"
                    push_status = "ready"
                    solution = "none"
            blockers = [solution] if push_status == "blocked" else []
            if unowned_local_commits:
                ownership_status = "unowned_local_commits"
            if not unresolved and not unpublished_thread_commits:
                continue
            if ownership_status == "unowned_local_commits":
                blockers.append("separate_or_confirm_local_commits")
            worktree = branch_worktree(root, branch)
            worktree_dirty = False
            if worktree is not None:
                worktree_changes = collect_changes(worktree)
                worktree_dirty = (
                    any(
                        worktree_changes[key]
                        for key in ("staged", "unstaged", "untracked", "unmerged")
                    )
                    or operation_state(worktree) is not None
                )
            if unresolved:
                blockers.append("promote_or_exclude_unresolved_checkpoints")
            elif worktree_dirty:
                blockers.append("checkpoint_or_clean_worktree")
            verification = effective_verifications(entry["verification"])
            verification_failed = any(
                item["status"] == "failed" for item in verification
            )
            stale_verification = [
                item
                for item in verification
                if item["status"] == "passed" and item.get("head") != local_tip
            ]
            if verification_failed:
                blockers.append("fix_and_rerun_verification")
            elif stale_verification:
                blockers.append("rerun_stale_verification")
            elif not verification:
                blockers.append("record_verification_or_not_applicable")
            blockers = list(dict.fromkeys(blockers))
            if blockers:
                push_status = "blocked"
                solution = primary_blocker(blockers)
            else:
                push_status = "ready"
            target = entry.get("merge_target") or infer_merge_target(root, remote)
            commit_count = len(entry["thread_commits"])
            merge_strategy = "rebase_and_merge" if commit_count else "none"
            dependencies = infer_branch_dependencies(root, branch, entry, repo_entry)
            if remote_state == "conflict":
                conflict_risk = "high"
            elif remote_state in {"behind", "diverged"}:
                conflict_risk = "medium"
            else:
                conflict_risk = "low"
            branches.append(
                {
                    "repo_id": repo_id,
                    "repo": str(root),
                    "branch": branch,
                    "local_tip": local_tip,
                    "remote": remote,
                    "remote_branch": remote_branch,
                    "remote_state": remote_state,
                    "ahead": ahead,
                    "behind": behind,
                    "push_status": push_status,
                    "divergence_solution": solution,
                    "blockers": blockers,
                    "ownership_status": ownership_status,
                    "unowned_local_commits": unowned_local_commits,
                    "unresolved_checkpoints": unresolved,
                    "worktree_dirty": worktree_dirty,
                    "verification": verification,
                    "verification_history": entry["verification"],
                    "stale_verification": stale_verification,
                    "conflict_paths": conflict_paths,
                    "thread_commits": [
                        item["commit"] for item in entry["thread_commits"]
                    ],
                    "unpublished_thread_commits": unpublished_thread_commits,
                    "merge_plan": {
                        "source": branch,
                        "target": target,
                        "strategy": merge_strategy,
                        "depends_on": dependencies,
                        "pre_merge_action": solution,
                        "post_merge_verification": verification,
                        "conflict_risk": conflict_risk,
                    },
                }
            )
    branches.sort(key=lambda item: (item["repo"], item["branch"]))
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for branch in branches:
        if branch["push_status"] != "ready" or not branch.get("remote"):
            continue
        grouped.setdefault((branch["repo_id"], branch["remote"]), []).append(branch)
    push_groups = [
        {
            "repo_id": repo_id,
            "repo": items[0]["repo"],
            "remote": remote,
            "branches": [item["branch"] for item in items],
            "atomic": len(items) > 1,
        }
        for (repo_id, remote), items in grouped.items()
    ]
    push_groups.sort(key=lambda item: (item["repo"], item["remote"]))
    return {
        "ok": True,
        "ledger_id": ledger["ledger_id"],
        "branches": branches,
        "push_groups": push_groups,
        "all_ready": all(item["push_status"] == "ready" for item in branches),
        "nothing_to_ship": not branches,
        "cross_remote_atomic": len(push_groups) <= 1,
    }


def command_ship_plan(args: argparse.Namespace) -> dict[str, Any]:
    with ledger_lock(args.ledger_root, args.ledger_id):
        ledger = load_ledger(args.ledger_root, args.ledger_id)
        return build_ship_plan(ledger, fetch=args.fetch)


def branch_worktree(root: Path, branch: str) -> Path | None:
    result = text(git(root, "worktree", "list", "--porcelain"))
    path: Path | None = None
    for line in [*result.splitlines(), ""]:
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree ")).resolve()
        elif line == f"branch refs/heads/{branch}" and path is not None:
            return path
        elif not line:
            path = None
    return None


def rebase_thread_owned_branch(
    ledger: dict[str, Any], ledger_id: str, branch_plan: dict[str, Any]
) -> dict[str, Any]:
    root = Path(branch_plan["repo"])
    branch = branch_plan["branch"]
    remote_ref = f"refs/remotes/{branch_plan['remote']}/{branch_plan['remote_branch']}"
    _, _, entry = find_branch_entry(ledger, root, branch)
    old_tip = text(git(root, "rev-parse", f"refs/heads/{branch}"))
    old_commits = rev_list(root, f"{remote_ref}..refs/heads/{branch}")
    safety_ref = (
        "refs/codex/checkpoint-thread/"
        f"{safe_component(ledger_id)}/{safe_component(branch)}/pre-rebase-{old_tip[:12]}"
    )
    git(root, "update-ref", safety_ref, old_tip)
    entry.setdefault("history_safety_refs", []).append(
        {
            "kind": "pre_rebase",
            "ref": safety_ref,
            "commit": old_tip,
            "created_at": now_iso(),
        }
    )

    worktree = branch_worktree(root, branch)
    temporary_worktree: Path | None = None
    if worktree is None:
        temporary_worktree = Path(tempfile.mkdtemp(prefix="checkpoint-rebase-"))
        add = git(
            root,
            "worktree",
            "add",
            "--quiet",
            str(temporary_worktree),
            branch,
            check=False,
        )
        if add.returncode != 0:
            raise CheckpointError(
                "rebase_worktree_unavailable",
                repo=str(root),
                branch=branch,
                safety_ref=safety_ref,
                stderr=add.stderr.decode("utf-8", "replace").strip(),
            )
        worktree = temporary_worktree
    try:
        dirty = git(worktree, "status", "--porcelain", "-z")
        if dirty.stdout:
            raise CheckpointError(
                "rebase_worktree_dirty",
                repo=str(root),
                branch=branch,
                safety_ref=safety_ref,
            )
        rebased = git(worktree, "rebase", remote_ref, check=False)
        if rebased.returncode != 0:
            conflict_paths = collect_changes(worktree)["unmerged"]
            git(worktree, "rebase", "--abort", check=False)
            raise CheckpointError(
                "rebase_conflict",
                repo=str(root),
                branch=branch,
                safety_ref=safety_ref,
                conflict_paths=conflict_paths,
                solution="resolve_on_branch_then_rerun_ship",
            )
        new_commits = rev_list(root, f"{remote_ref}..refs/heads/{branch}")
        records_by_commit = {item["commit"]: item for item in entry["thread_commits"]}
        if len(old_commits) == len(new_commits):
            for old, new in zip(old_commits, new_commits, strict=True):
                record = records_by_commit.get(old)
                if record is not None:
                    record["rewritten_from"] = old
                    record["commit"] = new
        else:
            by_message: dict[str, list[str]] = {}
            for commit in new_commits:
                subject = text(git(root, "show", "-s", "--format=%s", commit))
                by_message.setdefault(subject, []).append(commit)
            for record in entry["thread_commits"]:
                matches = by_message.get(record["message"], [])
                if len(matches) == 1:
                    old = record["commit"]
                    record["rewritten_from"] = old
                    record["commit"] = matches[0]
                elif record["commit"] in old_commits:
                    record["rewrite_status"] = "dropped_or_ambiguous"
        new_tip = text(git(root, "rev-parse", f"refs/heads/{branch}"))
    finally:
        if temporary_worktree is not None:
            git(
                root,
                "worktree",
                "remove",
                "--force",
                str(temporary_worktree),
                check=False,
            )
            with contextlib.suppress(OSError):
                temporary_worktree.rmdir()
    return {
        "action": "rebased",
        "old_tip": old_tip,
        "new_tip": new_tip,
        "onto": remote_ref,
        "safety_ref": safety_ref,
    }


def prepare_rebases(
    ledger: dict[str, Any], ledger_id: str, plan: dict[str, Any]
) -> dict[tuple[str, str], dict[str, Any]]:
    receipts: dict[tuple[str, str], dict[str, Any]] = {}
    for branch in plan["branches"]:
        if (
            branch["remote_state"] == "diverged"
            and branch["ownership_status"] == "thread_owned"
            and not branch["conflict_paths"]
            and branch["blockers"] == ["rebase_onto_upstream"]
        ):
            key = (branch["repo_id"], branch["branch"])
            receipts[key] = rebase_thread_owned_branch(ledger, ledger_id, branch)
    return receipts


def command_ship(args: argparse.Namespace) -> dict[str, Any]:
    with ledger_lock(args.ledger_root, args.ledger_id):
        ledger = load_ledger(args.ledger_root, args.ledger_id)
        plan = build_ship_plan(ledger, fetch=args.fetch)
        rebase_receipts = prepare_rebases(ledger, args.ledger_id, plan)
        if rebase_receipts:
            save_ledger(args.ledger_root, args.ledger_id, ledger)
            plan = build_ship_plan(ledger, fetch=False)
        for item in plan["branches"]:
            key = (item["repo_id"], item["branch"])
            item["divergence_handling"] = rebase_receipts.get(
                key,
                {
                    "action": "not_needed",
                    "planned_action": item["divergence_solution"],
                },
            )
            item["push_mode"] = "not_started"
        if not plan["all_ready"]:
            raise CheckpointError(
                "ship_preflight_blocked",
                branches=plan["branches"],
                push_groups=plan["push_groups"],
            )
        by_key = {(item["repo_id"], item["branch"]): item for item in plan["branches"]}
        for group in plan["push_groups"]:
            root = Path(group["repo"])
            push_mode = "atomic" if group["atomic"] else "single"
            for branch in group["branches"]:
                by_key[(group["repo_id"], branch)]["push_mode"] = push_mode
            refspecs = [
                f"refs/heads/{branch}:refs/heads/{branch}"
                for branch in group["branches"]
            ]
            command = ["push", "--set-upstream"]
            if group["atomic"]:
                command.append("--atomic")
            command.extend([group["remote"], *refspecs])
            pushed = git(root, *command, check=False)
            if pushed.returncode != 0:
                for branch in group["branches"]:
                    failed_branch = by_key[(group["repo_id"], branch)]
                    failed_branch["push_status"] = "failed"
                    failed_branch["divergence_solution"] = "fetch_and_replan"
                    failed_branch["blockers"] = ["fetch_and_replan"]
                    failed_branch["merge_plan"]["pre_merge_action"] = "fetch_and_replan"
                failure = {
                    "at": now_iso(),
                    "remote": group["remote"],
                    "branches": group["branches"],
                    "atomic": group["atomic"],
                    "completed": [
                        item
                        for item in plan["branches"]
                        if item["push_status"] == "pushed"
                    ],
                }
                ledger["last_ship_failure"] = failure
                save_ledger(args.ledger_root, args.ledger_id, ledger)
                raise CheckpointError(
                    "push_failed",
                    repo=str(root),
                    remote=group["remote"],
                    branches=group["branches"],
                    stderr=pushed.stderr.decode("utf-8", "replace").strip(),
                    atomic=group["atomic"],
                    completed=failure["completed"],
                    branches_report=plan["branches"],
                )
            for branch in group["branches"]:
                by_key[(group["repo_id"], branch)]["push_status"] = "pushed"
        ledger["last_ship"] = {
            "at": now_iso(),
            "branches": [
                {
                    "repo_id": item["repo_id"],
                    "branch": item["branch"],
                    "commit": item["local_tip"],
                    "remote": item["remote"],
                    "divergence_handling": item["divergence_handling"],
                    "push_mode": item["push_mode"],
                }
                for item in plan["branches"]
            ],
        }
        save_ledger(args.ledger_root, args.ledger_id, ledger)
        return plan


def command_ledger_status(args: argparse.Namespace) -> dict[str, Any]:
    ledger = load_ledger(args.ledger_root, args.ledger_id)
    repos = []
    for entry in ledger["repos"].values():
        repos.append(
            {
                "id": entry["id"],
                "root": entry["root"],
                "worktree_count": len(entry["worktrees"]),
                "branches": sorted(entry["branches"]),
            }
        )
    repos.sort(key=lambda item: item["root"])
    return {
        "ok": True,
        "ledger_id": args.ledger_id,
        "state": ledger["state"],
        "repo_count": len(repos),
        "repos": repos,
    }


def command_configure(args: argparse.Namespace) -> dict[str, Any]:
    if args.ledger_root is None:
        raise CheckpointError(
            "ledger_root_required",
            suggested_ledger_root=str(suggested_ledger_root().resolve()),
        )
    ledger_root = args.ledger_root.expanduser().resolve()
    existing = load_configuration()
    if existing and Path(existing["ledger_root"]).resolve() != ledger_root:
        if not args.replace:
            raise CheckpointError(
                "configuration_already_exists",
                config_path=str(configuration_path()),
                ledger_root=existing["ledger_root"],
                requested_ledger_root=str(ledger_root),
                solution="rerun_configure_with_replace_after_user_confirmation",
            )
    action = (
        "unchanged"
        if existing and existing["ledger_root"] == str(ledger_root)
        else "configured"
    )
    save_configuration(ledger_root)
    return {
        "ok": True,
        "action": action,
        "config_path": str(configuration_path()),
        "ledger_root": str(ledger_root),
    }


def resolve_ledger_root(value: Path | None) -> Path:
    if value is not None:
        return value.expanduser().resolve()
    configuration = load_configuration()
    if configuration is None:
        raise CheckpointError(
            "ledger_root_not_configured",
            config_path=str(configuration_path()),
            suggested_ledger_root=str(suggested_ledger_root().resolve()),
            solution="ask_user_then_run_configure",
        )
    return Path(configuration["ledger_root"]).expanduser().resolve()


def add_repo_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", type=Path, required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic Git checkpoint helper")
    parser.add_argument("--ledger-root", type=Path)
    parser.add_argument("--ledger-id")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure")
    configure.add_argument("--replace", action="store_true")
    configure.set_defaults(handler=command_configure)

    begin = subparsers.add_parser("begin")
    add_repo_argument(begin)
    begin.add_argument("--merge-target")
    begin.add_argument("--large-file-limit", type=int, default=DEFAULT_LARGE_FILE_LIMIT)
    begin.set_defaults(handler=command_begin)

    status = subparsers.add_parser("status")
    add_repo_argument(status)
    status.set_defaults(handler=command_status)

    snapshot = subparsers.add_parser("snapshot")
    add_repo_argument(snapshot)
    snapshot.add_argument(
        "--kind",
        choices=["baseline", "provisional", "confirmed", "safety"],
        required=True,
    )
    snapshot.add_argument("--reason", required=True)
    snapshot.add_argument(
        "--large-file-limit", type=int, default=DEFAULT_LARGE_FILE_LIMIT
    )
    snapshot.set_defaults(handler=command_snapshot)

    park = subparsers.add_parser("park")
    add_repo_argument(park)
    park.add_argument("--reason", required=True)
    park.add_argument("--large-file-limit", type=int, default=DEFAULT_LARGE_FILE_LIMIT)
    park.set_defaults(handler=command_park)

    restore = subparsers.add_parser("restore")
    add_repo_argument(restore)
    restore.add_argument("--ref", required=True)
    restore.add_argument("--confirm", action="store_true")
    restore.set_defaults(handler=command_restore)

    promote = subparsers.add_parser("promote")
    add_repo_argument(promote)
    promote.add_argument("--path", action="append", required=True)
    promote.add_argument("--message", required=True)
    promote.add_argument("--checkpoint-ref")
    promote.add_argument(
        "--acceptance-source",
        choices=["explicit", "objective", "implicit_progression"],
        required=True,
    )
    promote.add_argument(
        "--large-file-limit", type=int, default=DEFAULT_LARGE_FILE_LIMIT
    )
    promote.set_defaults(handler=command_promote)

    resolve_checkpoint = subparsers.add_parser("resolve-checkpoint")
    add_repo_argument(resolve_checkpoint)
    resolve_checkpoint.add_argument("--ref", required=True)
    resolve_checkpoint.add_argument(
        "--resolution", choices=["excluded", "superseded"], required=True
    )
    resolve_checkpoint.add_argument("--reason", required=True)
    resolve_checkpoint.set_defaults(handler=command_resolve_checkpoint)

    verification = subparsers.add_parser("record-verification")
    add_repo_argument(verification)
    verification.add_argument("--verification-command", required=True)
    verification.add_argument(
        "--status", choices=["passed", "failed", "not_applicable"], required=True
    )
    verification.add_argument("--scope", required=True)
    verification.add_argument("--evidence")
    verification.set_defaults(handler=command_record_verification)

    ship_plan = subparsers.add_parser("ship-plan")
    ship_plan.add_argument("--fetch", action="store_true")
    ship_plan.set_defaults(handler=command_ship_plan)

    ship = subparsers.add_parser("ship")
    ship.add_argument("--fetch", action="store_true")
    ship.set_defaults(handler=command_ship)

    ledger_status = subparsers.add_parser("ledger-status")
    ledger_status.set_defaults(handler=command_ledger_status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command != "configure":
            if not args.ledger_id:
                raise CheckpointError("ledger_id_required")
            args.ledger_root = resolve_ledger_root(args.ledger_root)
        if hasattr(args, "repo"):
            args.repo = args.repo.expanduser().resolve()
        return emit(args.handler(args))
    except CheckpointError as error:
        return emit(error.payload, code=2)
    except KeyboardInterrupt:
        return emit({"ok": False, "error": "interrupted"}, code=130)


if __name__ == "__main__":
    raise SystemExit(main())
