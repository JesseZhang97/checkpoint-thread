#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = PROJECT_ROOT / "acceptance" / "scenarios.json"
CHECKPOINT_CLI = (
    PROJECT_ROOT / "skill" / "checkpoint-thread" / "scripts" / "checkpoint_thread.py"
)
CHECKPOINT_HOOK = PROJECT_ROOT / "hooks" / "checkpoint_guard.py"
WEIGHTS = {"P0": 5, "P1": 3, "P2": 1}
VALID_STATUSES = {"automated", "benchmark", "contract", "gap", "real_remote"}


def run(
    command: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(item) for item in command],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        input=input_text,
    )


def percentile(values: Sequence[float], percentile_value: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return ordered[index]


def benchmark_hot_path() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="checkpoint-acceptance-") as directory:
        root = Path(directory)
        repo = root / "repo"
        ledgers = root / "ledgers"
        codex_home = root / "codex-home"
        environment = {"CODEX_HOME": str(codex_home)}
        repo.mkdir()
        commands = [
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.name", "Acceptance"],
            ["git", "config", "user.email", "acceptance@example.invalid"],
        ]
        for command in commands:
            result = run(command, cwd=repo)
            if result.returncode != 0:
                raise RuntimeError(result.stderr)
        (repo / "app.txt").write_text("base\n", encoding="utf-8")
        for command in (["git", "add", "app.txt"], ["git", "commit", "-qm", "initial"]):
            result = run(command, cwd=repo)
            if result.returncode != 0:
                raise RuntimeError(result.stderr)
        configured = subprocess.run(
            [
                sys.executable,
                str(CHECKPOINT_CLI),
                "--ledger-root",
                str(ledgers),
                "configure",
            ],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, **environment},
        )
        if configured.returncode != 0:
            raise RuntimeError(configured.stderr or configured.stdout)

        status_samples: list[float] = []
        for index in range(20):
            started = time.perf_counter()
            result = run(
                [
                    sys.executable,
                    CHECKPOINT_CLI,
                    "--ledger-id",
                    f"status-{index}",
                    "status",
                    "--repo",
                    repo,
                ],
                env={**os.environ, **environment},
            )
            status_samples.append((time.perf_counter() - started) * 1000)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout)

        enter_samples: list[float] = []
        for index in range(10):
            started = time.perf_counter()
            result = run(
                [
                    sys.executable,
                    CHECKPOINT_CLI,
                    "--ledger-id",
                    "hot-path-thread",
                    "enter",
                    "--repo",
                    repo,
                    "--merge-target",
                    "main",
                ],
                env={**os.environ, **environment},
            )
            enter_samples.append((time.perf_counter() - started) * 1000)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout)

        guard_samples: list[float] = []
        for index in range(20):
            started = time.perf_counter()
            result = run(
                [
                    sys.executable,
                    CHECKPOINT_CLI,
                    "--ledger-id",
                    "hot-path-thread",
                    "--operation-id",
                    f"guard-{index}",
                    "guard",
                    "--repo",
                    repo,
                ],
                env={**os.environ, **environment},
            )
            guard_samples.append((time.perf_counter() - started) * 1000)
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout)

        hook_samples: list[float] = []
        hook_environment = {
            **os.environ,
            **environment,
            "PLUGIN_ROOT": str(PROJECT_ROOT),
            "CODEX_THREAD_ID": "hot-path-thread",
        }
        for index in range(10):
            payload = {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "*** Begin Patch\n*** End Patch",
                    "workdir": str(repo),
                },
                "session_id": "hot-path-thread",
                "tool_use_id": f"hook-{index}",
                "cwd": str(repo),
            }
            started = time.perf_counter()
            for hook_event in ("PreToolUse", "PostToolUse"):
                payload["hook_event_name"] = hook_event
                result = run(
                    [sys.executable, CHECKPOINT_HOOK],
                    env=hook_environment,
                    input_text=json.dumps(payload),
                )
                if result.returncode != 0 or result.stdout:
                    raise RuntimeError(result.stderr or result.stdout)
            hook_samples.append((time.perf_counter() - started) * 1000)

    return {
        "status_p50_ms": round(statistics.median(status_samples), 2),
        "status_p95_ms": round(percentile(status_samples, 0.95), 2),
        "enter_p50_ms": round(statistics.median(enter_samples), 2),
        "enter_p95_ms": round(percentile(enter_samples, 0.95), 2),
        "guard_p50_ms": round(statistics.median(guard_samples), 2),
        "guard_p95_ms": round(percentile(guard_samples, 0.95), 2),
        "hook_roundtrip_p50_ms": round(statistics.median(hook_samples), 2),
        "hook_roundtrip_p95_ms": round(percentile(hook_samples, 0.95), 2),
        "status_samples": len(status_samples),
        "enter_samples": len(enter_samples),
        "guard_samples": len(guard_samples),
        "hook_roundtrip_samples": len(hook_samples),
    }


def test_method_exists(evidence: str) -> bool:
    path_text, separator, method = evidence.partition("::")
    if not separator:
        return False
    path = PROJECT_ROOT / path_text
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method
        for node in ast.walk(tree)
    )


def contract_exists(evidence: str) -> bool:
    path_text, separator, clause = evidence.partition("#")
    if not separator or not clause:
        return False
    path = PROJECT_ROOT / path_text
    return path.is_file() and clause in path.read_text(encoding="utf-8")


def load_remote_evidence(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("ok") is not True or not isinstance(payload.get("scenarios"), dict):
        raise ValueError("remote evidence is not a successful collaboration report")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify quantified skill acceptance")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--remote-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--skip-tests", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scenarios = json.loads(args.matrix.read_text(encoding="utf-8"))["scenarios"]
    remote = load_remote_evidence(args.remote_evidence)
    assert remote is not None

    validation_errors: list[str] = []
    seen: set[str] = set()
    for scenario in scenarios:
        scenario_id = scenario.get("id")
        if not scenario_id or scenario_id in seen:
            validation_errors.append(f"duplicate or missing scenario id: {scenario_id}")
        seen.add(scenario_id)
        if scenario.get("priority") not in WEIGHTS:
            validation_errors.append(f"{scenario_id}: invalid priority")
        if scenario.get("status") not in VALID_STATUSES:
            validation_errors.append(f"{scenario_id}: invalid status")
        if not scenario.get("domain") or not scenario.get("name"):
            validation_errors.append(f"{scenario_id}: missing domain or name")

    tests_passed = args.skip_tests
    test_count: int | None = None
    test_seconds: float | None = None
    test_output = "skipped by caller"
    if not args.skip_tests:
        started = time.perf_counter()
        test_result = run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
            cwd=PROJECT_ROOT,
        )
        test_seconds = round(time.perf_counter() - started, 3)
        test_output = (test_result.stdout + test_result.stderr).strip()
        tests_passed = test_result.returncode == 0
        match = re.search(r"Ran (\d+) tests", test_output)
        test_count = int(match.group(1)) if match else None

    benchmark = benchmark_hot_path()
    skill_text = (PROJECT_ROOT / "skill" / "checkpoint-thread" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    lean = {
        "lines": len(skill_text.splitlines()),
        "words": len(skill_text.split()),
        "conditional_references": skill_text.count("references/"),
    }

    remote_scenarios = remote["scenarios"]
    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        status = scenario["status"]
        evidence = scenario.get("evidence", "")
        if status == "automated":
            evidence_valid = test_method_exists(evidence)
            covered = tests_passed and evidence_valid
        elif status == "contract":
            evidence_valid = contract_exists(evidence)
            covered = evidence_valid
        elif status == "real_remote":
            key = evidence.removeprefix("github:")
            evidence_valid = evidence.startswith("github:") and key in remote_scenarios
            covered = evidence_valid
        elif status == "benchmark":
            thresholds = {"enter_p95_ms": 750, "status_p95_ms": 250}
            if evidence == "guard_hook_p95_ms":
                evidence_valid = True
                covered = (
                    benchmark["guard_p95_ms"] <= 500
                    and benchmark["hook_roundtrip_p95_ms"] <= 1000
                )
            else:
                evidence_valid = evidence in thresholds
                covered = evidence_valid and benchmark[evidence] <= thresholds[evidence]
        else:
            evidence_valid = bool(scenario.get("rationale"))
            covered = False
        if not evidence_valid:
            validation_errors.append(
                f"{scenario['id']}: unresolved evidence {evidence!r}"
            )
        results.append(
            {**scenario, "covered": covered, "evidence_valid": evidence_valid}
        )

    total_weight = sum(WEIGHTS[item["priority"]] for item in results)
    covered_weight = sum(
        WEIGHTS[item["priority"]] for item in results if item["covered"]
    )

    def score(items: Sequence[dict[str, Any]]) -> float:
        denominator = sum(WEIGHTS[item["priority"]] for item in items)
        numerator = sum(WEIGHTS[item["priority"]] for item in items if item["covered"])
        return numerator / denominator if denominator else 1.0

    by_priority = {
        priority: score([item for item in results if item["priority"] == priority])
        for priority in WEIGHTS
    }
    domains = sorted({item["domain"] for item in results})
    by_domain = {
        domain: score([item for item in results if item["domain"] == domain])
        for domain in domains
    }
    executable = [
        item
        for item in results
        if item["covered"] and item["status"] in {"automated", "real_remote"}
    ]
    negative_ratio = (
        sum(bool(item.get("negative")) for item in executable) / len(executable)
        if executable
        else 0.0
    )
    real_remote_count = sum(
        item["covered"] and item["status"] == "real_remote" for item in results
    )
    overall = covered_weight / total_weight
    gates = {
        "overall_weighted_coverage": overall >= 0.90,
        "p0_coverage": by_priority["P0"] == 1.0,
        "p1_coverage": by_priority["P1"] == 1.0,
        "per_domain_coverage": min(by_domain.values()) >= 0.80,
        "negative_case_ratio": negative_ratio >= 0.35,
        "real_remote_scenarios": real_remote_count >= 5,
        "tests_pass": tests_passed,
        "evidence_resolves": not validation_errors,
        "skill_lines": lean["lines"] <= 100,
        "skill_words": lean["words"] <= 650,
        "conditional_references": lean["conditional_references"] <= 4,
        "status_p95": benchmark["status_p95_ms"] <= 250,
        "enter_p95": benchmark["enter_p95_ms"] <= 750,
        "guard_p95": benchmark["guard_p95_ms"] <= 500,
        "hook_roundtrip_p95": benchmark["hook_roundtrip_p95_ms"] <= 1000,
    }
    report = {
        "accepted": all(gates.values()),
        "scenario_count": len(results),
        "covered_scenario_count": sum(item["covered"] for item in results),
        "weighted_coverage": round(overall, 4),
        "coverage_by_priority": {
            key: round(value, 4) for key, value in by_priority.items()
        },
        "coverage_by_domain": {
            key: round(value, 4) for key, value in by_domain.items()
        },
        "negative_executable_ratio": round(negative_ratio, 4),
        "real_remote_scenario_count": real_remote_count,
        "gates": gates,
        "lean_skill": lean,
        "benchmark": benchmark,
        "tests": {
            "passed": tests_passed,
            "count": test_count,
            "seconds": test_seconds,
            "summary": test_output,
        },
        "gaps": [
            {
                "id": item["id"],
                "name": item["name"],
                "priority": item["priority"],
                "rationale": item.get("rationale"),
            }
            for item in results
            if not item["covered"]
        ],
        "validation_errors": validation_errors,
        "remote_run_id": remote.get("run_id"),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
