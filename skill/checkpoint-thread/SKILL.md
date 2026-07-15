---
name: checkpoint-thread
description: Automatically checkpoint Codex repo work before its first mutation and across branches. Use on every repo-mutating task; the hook enforces entry without explicit invocation, while the CLI ships only verified, thread-owned work.
---

# Checkpoint Thread

A thread is the scope, a goal boundary is the checkpoint cadence, and a branch is
the delivery lane. The Parent Agent judges goals; the CLI owns deterministic Git,
recovery, verification, and delivery mutations.

## Dispatch

Resolve one `LEDGER_ID` per Codex thread. Prefer the surfaced task/thread id;
otherwise generate one once and retain it in context.

Before the first repository mutation after installation, ask the user once to
choose the ledger root. Recommend
`${CODEX_HOME:-$HOME/.codex}/ledgers/checkpoint-thread/active`; persist it with
`--ledger-root "$LEDGER_ROOT" configure`. Reuse it thereafter. Replace it only
with explicit confirmation via `configure --replace`.

The synchronous `PreToolUse` hook silently runs `guard`, recording the
preflight and immutable baseline before Codex writes. `PostToolUse` releases a
clean no-op claim. It is not a daemon and does not observe human file saves. If
the hook is unavailable, run this before the first persistent change:

```bash
python3 scripts/checkpoint_thread.py --ledger-id "$LEDGER_ID" enter --repo "$ROOT" --merge-target "$TARGET"
```

Read-only threads create nothing. Active same-goal continuation makes no CLI call
from the agent; only the silent hook runs.

| Event | Action |
|---|---|
| First Codex mutation | Hook `guard`; fallback `enter` |
| Active goal continues | Continue; no Git action |
| New branch or worktree enters scope | `enter` there before mutation |
| Goal is explicitly or objectively accepted | Verify, then `promote` exact paths |
| A distinct low-risk goal begins implicitly | `snapshot --kind provisional` |
| Work pauses | `snapshot --kind safety` |
| A dirty branch must be left | `park` |
| User explicitly requests ship/push | Read `references/ship.md` |

Repeated `enter` is idempotent. On `continue`, do not reload references or create
another checkpoint. One thread may claim a repo branch at a
time; `park`, successful ship, or a clean no-op post-hook releases the claim.

## Goal Boundary

Compare the request with the active goal by object, intent, and concern. Use semantic goal delta; never match transition words:

- Treat refinement of the same outcome as `continuation`.
- Treat an independent outcome as `new_goal`.
- Prefer one concern when the boundary is ambiguous; do not split by turn or file.

Apply the checkpoint gate before promotion:

1. Account for every selected changed path.
2. Establish acceptance by approval, objective evidence, or implicit progression
   into a distinct low-risk goal.
3. Reuse exact-state evidence or run the narrowest check needed for this goal.
4. Leave no known broken intermediate state.

Implicit progression creates a provisional private ref, not remote history.
Schema, auth, migration, concurrency, and external-contract work always requires
objective evidence.

After observing a check, use `record-verification` to bind its command, status,
scope, evidence, exclusions, and exact recoverable `state_oid`. Promotion carries
it only when that state matches all promoted changes; otherwise rerun the narrow
check. Never record an unrun check.

## Branch Path

Before switching branches, promote confirmed work or `park` provisional work.
Enroll the destination before mutation. Keep checkpoints, claims, verification,
upstream, and merge target bound to their branch.

Read `references/safety-snapshot.md` for staging, untracked or large files,
secrets, parking, or recovery. Read `references/worktree.md` only
when multiple worktrees exist. Read `references/multi-repo.md` only when this
thread touches more than one repository.

## Authority

Treat `enter` as authority to create private local refs and `promote` as authority
to create confirmed local commits. Require an explicit ship/push request before
fetch, history rewrite, rebase, or push. Use lifecycle commands rather than direct
Git history/delivery commands. Never push private refs or force-push.

## Receipt

Report only observed results: ledger id and paths, event or operation id, repo,
branch, checkpoint or commit SHA, verification, exclusions, claim/push status,
and merge plan. A blocked operation remains blocked; never count a snapshot,
hook, fetch, rebase, or push failure as success.
