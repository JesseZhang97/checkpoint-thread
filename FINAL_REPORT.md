# Checkpoint Thread Final Acceptance Report

## Outcome

V2.1 passes the expanded quantified acceptance model. All original 80 scenarios
remain covered, with 24 provenance-ledger, verification, shared-branch, and Hook
scenarios added.

| Gate | Required | Observed | Result |
|---|---:|---:|---|
| Weighted scenario coverage | >= 90% | 100% | Pass |
| P0 coverage | 100% | 100% | Pass |
| P1 coverage | 100% | 100% | Pass |
| Lowest domain coverage | >= 80% | 100% | Pass |
| Negative executable cases | >= 35% | 50% | Pass |
| Real GitHub scenarios | >= 5 | 5 | Pass |
| Local test suite | All pass | 77/77 | Pass |
| `SKILL.md` lines | <= 100 | 94 | Pass |
| `SKILL.md` words | <= 650 | 606 | Pass |
| Conditional references | <= 4 | 4 | Pass |
| `status` p95 | <= 250 ms | 170.33 ms | Pass |
| `enter` p95 | <= 750 ms | 384.04 ms | Pass |
| `guard` p95 | <= 500 ms | 322.27 ms | Pass |
| Hook round trip p95 | <= 1000 ms | 660.42 ms | Pass |

The machine-readable result is in `acceptance/results.json`. The definition,
catalog, and evidence are in `ACCEPTANCE_CRITERIA.md`,
`acceptance/scenarios.json`, and `acceptance/evidence/`.
The lean-architecture and progressive-disclosure review is in
`ARCHITECTURE_ASSESSMENT.md`.

## Operating Model

- A branch is a shared Git workspace; a Codex task is an attribution source.
- Hook identity prefers payload `thread_id`, then the stable
  `CODEX_THREAD_ID`, and uses `session_id` only as a legacy fallback.
- A goal boundary, not a turn, is the checkpoint cadence.
- A goal is the commit candidate; several tasks may contribute on one branch.
- The plugin Hook runs `guard`; without it, `enter` is the lazy idempotent entry.
- The user selects the ledger root once on first mutation; the recommended
  default is under `CODEX_HOME`, and later tasks reuse the saved choice.
- Read-only threads create no ledger or ref; same-goal continuation invokes no
  checkpoint command.
- The model decides `continuation`, `new_goal`, or `ambiguous` semantically. The
  deterministic CLI owns Git mutations and ledger state, not language parsing.

## V2.1 Provenance Ledger

- SQLite schema V2 under the configured ledger root stores attribution and
  milestone audit state. Git remains authoritative for files, branches, commits,
  refs, and remotes.
- No per-task JSON projection or legacy migration path exists.
- Pre-release aliases are intentionally absent: lifecycle entry is `enter`, and
  diagnostics use `inspect` or `doctor`.
- Every receipt self-locates the configuration, database, event, and operation.
- Operation ids remain available for explicit lifecycle retries. Hook guard and
  settle use transient spans instead of durable operation rows.
- Multiple task ledgers may register the same repo/branch. Real Codex edits add
  one contribution event; no-op Hook pairs leave no durable row.
- Same-path contributions record overlap evidence rather than denying mutation.
- Successful ship prunes recovery refs already represented by pushed commits.
- The Hook is synchronous and silent on allow. It is not a daemon or human file
  watcher.

## Supported Workflow

### Enrollment and attribution

- Enroll an initialized non-bare Git repository before its first mutation.
- Keep a thread ledger outside business repositories, keyed by Git common dir.
- Record branch baselines once across repeated `enter` calls.
- Reject detached HEAD, non-repository paths, and corrupt ledger state.
- Do not silently attribute dirty pre-entry paths, pre-entry commits, merely
  visited branches, or external edits to the current task.
- Support repositories without an initial commit through park, restore,
  exact-path root promotion, verification, and first push.

### Goal checkpoints

- Promote explicitly accepted or objectively verified work into a local atomic
  commit containing exact selected paths.
- Create provisional private refs when the user implicitly moves into a distinct,
  low-risk goal without a verbal transition.
- Keep ambiguous changes in one concern instead of manufacturing turn-based
  commits.
- Resolve provisional checkpoints by promotion or explicit exclusion before
  shipping.
- Preserve hook failures and conflict evidence without reporting a false commit.

### Snapshot and recovery

- Preserve `HEAD -> index snapshot -> worktree snapshot` through private commit
  objects without disturbing the live index or worktree.
- Preserve partial staging, staged plus unstaged overlap, untracked files,
  renames, deletions, symlinks, and executable-mode changes.
- Preserve modified large tracked files.
- Exclude and report secret paths, ignored files, recognized local test output,
  generated output, and oversized untracked files.
- Park a dirty branch only after a complete snapshot, then restore only with
  explicit confirmation.
- Refuse restore into a dirty state or onto a different `HEAD`.
- Block snapshotting during merge/rebase/cherry-pick/revert or an unmerged index.

### Verification

- Bind command, scope, status, evidence, timestamp, exclusions, and exact
  recoverable `state_oid` to a branch.
- Transfer a passed check to the promoted commit only when that complete state
  exactly matches every promoted change.
- Require either current verification or an explicit `not_applicable` record
  before shipping.
- Let the latest result for the same command and scope supersede older failures
  while retaining full history in the receipt.
- Keep older passed scopes as audit history without blocking a currently verified
  tip.
- Block failed and stale passed checks. A factual `not_applicable` record remains
  valid across a rebase because it is a policy statement, not build output.

### Branches, worktrees, and repositories

- Track multiple branches and worktrees through the common Git directory.
- Rebase a clean attributed branch in its existing worktree, or create a temporary
  worktree when that branch is not checked out elsewhere.
- Leave user-created worktrees intact.
- Track multiple repositories in one ledger with independent baselines, remotes,
  verification, checkpoints, and merge plans.
- Treat nested repositories and submodules as separate enrollments.
- Infer dependency order between attributed branches when their ancestry makes
  it observable.

### Delivery

- Require an explicit ship/push request before fetch, rebase, rewrite, or push.
- Build the ship set only from unpublished commits attributed in the ledger.
- Exclude untouched and already-published branches.
- Require a named local branch and a configured or unambiguous remote.
- Auto-rebase clean, non-conflicting, attributed divergence only after creating
  a private pre-rebase safety ref.
- Block conflicts with exact paths and a rebase-resolution action.
- Block dirty worktrees, unresolved checkpoints, unattributed commits, missing/stale/
  failed verification, missing branches, remote ambiguity, and fetch failure.
- Push multiple branches to one remote using Git atomic push.
- Reject an entire atomic group when one ref advances after preflight, and mark
  every branch failed without partial publication.
- Preflight multiple repositories/remotes, report non-atomic delivery, and retain
  completed branch receipts when a later remote fails.
- Never force-push and never publish private checkpoint refs.
- Treat a remote advance between preflight and push as a failed delivery with
  `fetch_and_replan`, not as success.

### Reporting and merge planning

- Report ledger, repository, branch, final SHA, upstream, verification, exclusions,
  divergence handling, safety ref, push mode, and final push status.
- Return every applicable blocker instead of hiding secondary problems behind one
  primary error.
- Produce a per-branch merge plan with source, target, dependency order, strategy,
  pre-merge action, post-merge verification, and conflict risk.
- Distinguish `pushed`, `local-only`, `blocked`, and `failed`; a snapshot, fetch,
  rebase, hook, or push failure is never counted as delivery.

## Real Collaboration Evidence

The private GitHub lab used separate `Thread Agent` and `Remote Collaborator`
identities and uniquely named collaboration branches. Run
`20260715130951-87ccf4` passed these scenarios:

| Scenario | Observed result |
|---|---|
| Clean remote divergence | Safety ref, automatic rebase, successful push |
| Conflicting divergence | Exact conflict path, blocked push, high-risk merge plan |
| Late remote advance | Stale push rejected, final status `failed`, replan action |
| Unattributed new branch | Publication blocked and no remote branch created |
| Two branches, one remote | Both refs published in one atomic push group |

The run also proves that private `refs/codex/checkpoint-thread/...` refs were not
published and that the final clean-divergence tip matched GitHub. The temporary
repository is private and archived after the run; deletion is pending the
GitHub CLI `delete_repo` scope.

## Remaining Scope

The checked-in acceptance catalog has no remaining coverage gaps.

Git LFS object transport, server administration, authentication recovery,
platform-specific case-collision behavior, and parent-repository mutation of
submodule contents are excluded product scope. These exclusions do not weaken
the covered provenance, recovery, and delivery invariants.

## Reproduction

Run the complete acceptance check with:

```bash
python3 scripts/verify_acceptance.py \
  --remote-evidence acceptance/evidence/github-collaboration-final.json \
  --output acceptance/results.json
```

The verifier runs the 77-test disposable-repository suite, resolves all 104
catalog evidence pointers, measures the four hot paths, validates the lean-skill
limits, and exits nonzero when any acceptance gate fails.
