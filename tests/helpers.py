from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI = PROJECT_ROOT / "skill" / "checkpoint-thread" / "scripts" / "checkpoint_thread.py"


def run(
    args: Iterable[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [str(arg) for arg in args]
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=repo, check=check)


def init_repo(path: Path, *, initial_branch: str = "main") -> Path:
    path.mkdir(parents=True)
    run(["git", "init", "-q", "-b", initial_branch], cwd=path)
    git(path, "config", "user.name", "Checkpoint Test")
    git(path, "config", "user.email", "checkpoint@example.test")
    (path / "app.txt").write_text("base\n", encoding="utf-8")
    git(path, "add", "app.txt")
    git(path, "commit", "-qm", "initial")
    return path


def init_unborn_repo(path: Path, *, initial_branch: str = "main") -> Path:
    path.mkdir(parents=True)
    run(["git", "init", "-q", "-b", initial_branch], cwd=path)
    git(path, "config", "user.name", "Checkpoint Test")
    git(path, "config", "user.email", "checkpoint@example.test")
    return path


def clone_repo(remote: Path, path: Path, *, branch: str = "main") -> Path:
    run(["git", "clone", "-q", "-b", branch, str(remote), str(path)])
    git(path, "config", "user.name", "Checkpoint Test")
    git(path, "config", "user.email", "checkpoint@example.test")
    return path


def run_cli(
    ledger_root: Path,
    ledger_id: str,
    command: str,
    repo: Path | None = None,
    *extra: str,
    expected_code: int = 0,
    operation_id: str | None = None,
) -> dict:
    codex_home = ledger_root.parent / "codex-home"
    environment = {"CODEX_HOME": str(codex_home)}
    config_path = codex_home / "checkpoint-thread" / "config.json"
    if not config_path.exists():
        configured = run(
            [sys.executable, str(CLI), "--ledger-root", str(ledger_root), "configure"],
            check=False,
            env=environment,
        )
        if configured.returncode != 0:
            raise AssertionError(
                f"could not configure test ledger root\n{configured.stdout}\n{configured.stderr}"
            )
    args = [
        sys.executable,
        str(CLI),
        "--ledger-id",
        ledger_id,
    ]
    if operation_id is not None:
        args.extend(["--operation-id", operation_id])
    args.append(command)
    if repo is not None:
        args.extend(["--repo", str(repo)])
    args.extend(extra)
    result = run(args, check=False, env=environment)
    if result.returncode != expected_code:
        raise AssertionError(
            f"CLI returned {result.returncode}, expected {expected_code}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"CLI did not return JSON: {result.stdout}\n{result.stderr}"
        ) from error


def ref_exists(repo: Path, ref: str) -> bool:
    return (
        git(repo, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0
    )


def load_ledger_state(ledger_root: Path, ledger_id: str) -> dict:
    database = ledger_root / "checkpoint-thread.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT state_json FROM ledgers WHERE ledger_id = ?", (ledger_id,)
        ).fetchone()
    if row is None:
        raise AssertionError(f"ledger not found: {ledger_id}")
    return json.loads(row[0])
