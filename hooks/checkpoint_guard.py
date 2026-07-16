#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator


PATCH_TOOLS = {"apply_patch", "Edit", "Write"}
SHELL_TOOLS = {"Bash", "exec_command", "unified_exec", "shell"}
PROTECTED_GIT_COMMANDS = {
    "add",
    "am",
    "checkout",
    "cherry-pick",
    "clean",
    "commit",
    "fetch",
    "merge",
    "pull",
    "push",
    "rebase",
    "reset",
    "restore",
    "revert",
    "stash",
    "switch",
    "tag",
    "update-ref",
}
MUTATION_PATTERNS = (
    re.compile(
        r"(?:^|[;&|\s])(?:cp|install|mkdir|mv|rm|rmdir|touch|truncate|unlink)(?:\s|$)"
    ),
    re.compile(r"(?:^|[;&|\s])(?:sed\s+-[^\n;&|]*i|perl\s+-[^\n;&|]*i)"),
    re.compile(r"(?:^|[;&|\s])tee(?:\s|$)"),
    re.compile(r"(?:^|[^<])(?:>>?|[12]>)\s*[^&]"),
    re.compile(r"\b(?:write_text|write_bytes|open\([^\n)]*,\s*['\"]?[wax+])"),
    re.compile(
        r"\bgit(?:\s+-\S+)*\s+(?:add|am|apply|checkout|cherry-pick|clean|commit|fetch|merge|mv|pull|push|rebase|reset|restore|revert|rm|stash|switch|tag|update-ref)(?:\s|$)"
    ),
    re.compile(
        r"\b(?:npm|pnpm|yarn|bun)\s+(?:add|install|remove|update|upgrade)(?:\s|$)"
    ),
    re.compile(r"\b(?:npm|pnpm|yarn|bun)\s+(?:run|exec|dlx|x|test|build)\b"),
    re.compile(
        r"\b(?:ruff\s+(?:format|check\b[^\n;&|]*--fix)|black|prettier\b[^\n;&|]*--write|eslint\b[^\n;&|]*--fix|clang-format\b[^\n;&|]*-i)(?:\s|$)"
    ),
    re.compile(r"\b(?:go\s+(?:fmt|generate|get)|cargo\s+(?:fmt|fix|add|update))\b"),
    re.compile(r"(?:^|[;&|\s])(?:patch|rsync|unzip)(?:\s|$)"),
    re.compile(r"(?:^|[;&|\s])tar\s+[^\n;&|]*-[^\s]*x"),
    re.compile(r"(?:^|[;&|\s])(?:make|ninja)(?:\s|$)"),
    re.compile(r"\bcmake\s+--build\b"),
    re.compile(
        r"\bgit(?:\s+-\S+)*\s+worktree\s+(?:add|remove|move|prune|lock|unlock)\b"
    ),
    re.compile(r"\bgit(?:\s+-\S+)*\s+branch\s+(?:-[dDmMcC]\b|[^-\s][^\s]*)"),
    re.compile(r"\bapply_patch\b"),
)


def strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from strings(nested)


def is_mutation(tool_name: str, tool_input: dict[str, Any]) -> bool:
    if tool_name in PATCH_TOOLS:
        return True
    if tool_name not in SHELL_TOOLS:
        return False
    source = "\n".join(strings(tool_input))
    return any(pattern.search(source) for pattern in MUTATION_PATTERNS)


def protected_git_command(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    if tool_name not in SHELL_TOOLS:
        return None
    source = "\n".join(strings(tool_input))
    for segment in re.split(r"(?:&&|\|\||[;|\n])", source):
        try:
            words = shlex.split(segment)
        except ValueError:
            continue
        git_index = next(
            (index for index, word in enumerate(words) if Path(word).name == "git"),
            None,
        )
        if git_index is None:
            continue
        index = git_index + 1
        while index < len(words):
            word = words[index]
            if word in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}:
                index += 2
                continue
            if word.startswith("-"):
                index += 1
                continue
            if word in PROTECTED_GIT_COMMANDS:
                return word
            break
    return None


def deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def git_root(path: Path) -> Path | None:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def resolve_ledger_identity(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    candidates = (
        ("thread_id", payload.get("thread_id")),
        ("CODEX_THREAD_ID", os.environ.get("CODEX_THREAD_ID")),
        ("session_id", payload.get("session_id")),
    )
    for source, value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip(), source
    return None, None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (OSError, json.JSONDecodeError) as error:
        deny(f"Checkpoint Thread could not parse the mutation request: {error}")
        return 0
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not is_mutation(tool_name, tool_input):
        return 0
    hook_event = payload.get("hook_event_name", "PreToolUse")
    protected = protected_git_command(tool_name, tool_input)
    if hook_event not in {"PreToolUse", "PostToolUse"}:
        return 0

    ledger_id, identity_source = resolve_ledger_identity(payload)
    if ledger_id is None:
        if hook_event == "PostToolUse":
            return 0
        deny("Checkpoint Thread cannot identify this Codex thread; mutation blocked.")
        return 0
    workdir_value = tool_input.get("workdir") or payload.get("cwd") or os.getcwd()
    workdir = Path(workdir_value).expanduser()
    root = git_root(workdir)
    if root is None:
        return 0
    if hook_event == "PreToolUse" and protected:
        deny(
            f"Checkpoint Thread blocks direct git {protected}; use the checkpoint "
            "lifecycle CLI so attribution, recovery, verification, and delivery "
            "remain auditable."
        )
        return 0

    operation_id = payload.get("tool_use_id") or payload.get("tool_call_id")
    if not isinstance(operation_id, str) or not operation_id:
        digest = hashlib.sha256(
            json.dumps(tool_input, sort_keys=True).encode("utf-8")
        ).hexdigest()[:20]
        operation_id = f"hook-{ledger_id}-{digest}"
    plugin_root = Path(
        os.environ.get("PLUGIN_ROOT", Path(__file__).resolve().parents[1])
    )
    cli = (
        plugin_root / "skill" / "checkpoint-thread" / "scripts" / "checkpoint_thread.py"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(cli),
            "--ledger-id",
            ledger_id,
            "guard" if hook_event == "PreToolUse" else "settle",
            "--repo",
            str(root),
            "--span-id",
            operation_id,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return 0
    if hook_event == "PostToolUse":
        return 0
    try:
        failure = json.loads(result.stdout)
        error = failure.get("error", "guard_failed")
        solution = failure.get("solution")
        facts = [f"ledger_id={ledger_id}", f"identity_source={identity_source}"]
        if solution:
            facts.append(f"solution={solution}")
        detail = f" ({', '.join(facts)})"
    except json.JSONDecodeError:
        error = result.stderr.strip() or "guard_failed"
        detail = ""
    deny(f"Checkpoint Thread blocked this mutation: {error}{detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
