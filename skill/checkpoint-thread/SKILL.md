---
name: checkpoint-thread
description: Attribute Codex repository edits to their thread and business goal before organizing verified changes into commits and shipping them. Use on every repo-mutating task; the fail-open hook observes recognized writes without locking the branch or blocking normal tools.
---

# Checkpoint Thread

A branch is a shared workspace, a thread is an attribution source, and a goal is
the commit candidate. Git owns repository truth; the ledger records why each
Codex change belongs together.

## Dispatch

Resolve one `LEDGER_ID` per Codex thread. Prefer the surfaced task/thread id;
otherwise generate one once and retain it in context.

Before the first repository mutation after installation, ask the user once to
choose the ledger root. Recommend
`${CODEX_HOME:-$HOME/.codex}/ledgers/checkpoint-thread/active`; persist it with
`--ledger-root "$LEDGER_ROOT" configure`. Reuse it thereafter. Replace it only
with explicit confirmation via `configure --replace`.

The synchronous `PreToolUse` hook silently captures the before-state and lazily
enters the repo. `PostToolUse` records one contribution only when content changed.
It is not a daemon: it fails open with a warning, ignores ordinary build/test and
delivery commands, and does not observe human file saves. Without the hook, run:

```bash
python3 scripts/checkpoint_thread.py --ledger-id "$LEDGER_ID" enter --repo "$ROOT" --merge-target "$TARGET"
```

Read-only threads create nothing. Same-goal continuation requires no agent CLI call;
the Hook only updates attribution when a tool changes repository content.

| Event | Action |
|---|---|
| First Codex mutation | Hook `guard`; fallback `enter` |
| Active goal continues | Continue; no checkpoint |
| New branch or worktree enters scope | `enter` there before mutation |
| Goal is explicitly or objectively accepted | Verify, then `promote` exact paths |
| A distinct low-risk goal begins implicitly | `snapshot --kind provisional` |
| Work pauses | `snapshot --kind safety` |
| A dirty branch must be left | `park` |
| Clean local-only task is finalized | `close --reason "$REASON"` |
| User explicitly requests ship/push | Read `references/ship.md` |

Repeated `enter` is idempotent. Multiple threads may enter and edit the same
branch. Overlapping paths are recorded for reconciliation, never blocked as
ownership conflicts.

## Goal Boundary

Compare the request with the active goal by object, intent, and concern. Use semantic goal delta; never match transition words:

- Treat refinement of the same outcome as `continuation`.
- Treat an independent outcome as `new_goal`.
- Prefer one concern when the boundary is ambiguous; do not split by turn or file.

Before promotion, account for selected changes, establish acceptance, reuse or
run the narrowest verification, and leave no known broken intermediate state.
Implicit progression creates a provisional private ref, not remote history.
Schema, auth, migration, concurrency, and external-contract work always requires
objective evidence.

Record observed checks with `record-verification`; bind command, status, scope,
evidence, exclusions, and exact `state_oid`. Promotion carries evidence only
when the verified state matches every promoted change. Never record an unrun
check.

## Attribution

Treat Hook-recorded contributions as evidence, not authority. A path may appear
under multiple threads or goals. Report overlaps before promotion. Changes made
outside Codex remain unattributed until explicitly assigned during commit
selection; never silently assign pre-entry dirty work to the current thread.

Before switching branches, promote confirmed work or `park` provisional work.
Read `references/safety-snapshot.md` for recovery and exclusions,
`references/worktree.md` for multiple worktrees, and `references/multi-repo.md`
only when those branches occur.

## Authority

`enter` authorizes private recovery refs; `promote` authorizes selected local
commits. Fetch, rebase, history rewrite, and push require an explicit ship
request. Prefer lifecycle commands for auditable history and delivery. Never force-push or
push private refs. Successful ship prunes recovery refs represented by pushed
commits.

## Receipt

Report observed ledger paths, goal and contribution ids, repo, branch,
checkpoint or commit SHA, overlaps, verification, exclusions, push status, and
merge plan. Never count a failed Hook, snapshot, fetch, rebase, or push as
success.
