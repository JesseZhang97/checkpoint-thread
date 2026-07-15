---
name: checkpoint-thread
description: Checkpoint repository work across a Codex thread. Use before its first persistent change, when work enters another branch or worktree, when a goal boundary needs recovery, or when thread-owned changes must be committed or shipped.
---

# Checkpoint Thread

A thread is the scope, a goal boundary is the checkpoint cadence, and a branch is
the delivery lane. Use the CLI for Git and ledger mutations; keep semantic goal
judgment in the Parent Agent.

## Dispatch

Resolve one `LEDGER_ID` per Codex thread. Prefer the surfaced task/thread id;
otherwise generate one once and retain it in context.

Before the first repository mutation after installation, ask the user once to
choose the ledger root. Recommend
`${CODEX_HOME:-$HOME/.codex}/ledgers/checkpoint-thread/active`; persist it with
`--ledger-root "$LEDGER_ROOT" configure`. Reuse it thereafter. Replace it only
with explicit confirmation via `configure --replace`.

Read-only threads create nothing. Active same-goal continuation makes no CLI call.

Before the first persistent repository change, run:

```bash
python3 scripts/checkpoint_thread.py --ledger-id "$LEDGER_ID" status --repo "$ROOT"
python3 scripts/checkpoint_thread.py --ledger-id "$LEDGER_ID" begin --repo "$ROOT" --merge-target "$TARGET"
```

Dispatch from observable state:

| Event | Action |
|---|---|
| No ledger before first repository mutation | `begin` |
| Active goal continues | Continue; no Git action |
| New branch or worktree enters scope | `begin` there |
| Goal is explicitly or objectively accepted | `promote` exact paths |
| A distinct low-risk goal begins implicitly | `snapshot --kind provisional` |
| Work pauses | `snapshot --kind safety` |
| A dirty branch must be left | `park` |
| User explicitly requests ship/push | Read `references/ship.md` |

Repeated `begin` is idempotent. On `continue`, do not reload references, rescan
branches, or create another checkpoint.

## Goal Boundary

Compare the new request with the ledger's active goal by object, intent, and
concern. Use semantic goal delta, never transition-word matching:

- Treat refinement of the same outcome as `continuation`.
- Treat an independent outcome as `new_goal`.
- Prefer one concern when the boundary is ambiguous; do not split by turn or file.

Apply the checkpoint gate before promotion:

1. Account for every selected staged, unstaged, and untracked path.
2. Establish acceptance by approval, objective evidence, or implicit progression
   into a distinct low-risk goal.
3. Reuse current verification evidence or run the narrowest repository-defined
   check needed for this goal.
4. Leave no known broken intermediate state.

Implicit progression creates a provisional private ref, not remote history.
Schema, auth, migration, concurrency, and external-contract work always requires
objective evidence.

After observing a check, use `record-verification` to bind its command, status,
scope, evidence, and current HEAD to the branch ledger. Never record an unrun check.

## Branch Path

Before switching branches, promote confirmed work or `park` provisional work.
Enroll the destination before mutation. Keep checkpoints, verification, upstream,
and merge target bound to their branch.

Read `references/safety-snapshot.md` for partial staging, untracked files, large
files, secrets, branch parking, or recovery. Read `references/worktree.md` only
when multiple worktrees exist. Read `references/multi-repo.md` only when this
thread touches more than one repository.

## Authority

Treat `begin` as authority to create private local refs and `promote` as authority
to create confirmed local commits. Require an explicit ship/push request before
fetch, history rewrite, rebase, or push. Never push private refs or force-push.

## Receipt

Report only observed results: ledger id, repo, branch, checkpoint or commit SHA,
verification, exclusions, push status, and merge plan. A blocked branch remains
blocked; do not count a snapshot, fetch, rebase, hook, or push failure as success.
