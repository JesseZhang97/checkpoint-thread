# Checkpoint Thread Specification

## Intent

Give one Codex thread local recovery and delivery structure without requiring a
manual entry command or treating every turn as a commit. The skill tracks the
thread's work across files, branches, worktrees, and occasional multiple repos,
then ships only the history owned by that thread.

## Operating model

- Thread is the ownership scope.
- Goal or accepted milestone is the checkpoint cadence.
- Branch is the delivery lane.
- Turn is only conversation context, never a checkpoint unit.
- Enter is lazy and idempotent before the first persistent repo mutation.
- Goal transitions are semantic judgments by the model, not keyword parsing.
- The synchronous Hook enforces repository entry; it is not a daemon or a human
  file watcher.

An accepted goal becomes a local branch commit. Implicit progression into a
distinct low-risk goal creates a provisional private ref. Ambiguous progression
stays within one concern until evidence supports a boundary.

## Control-plane invariants

- The selected ledger root contains one SQLite V2 control plane. It is the only
  ledger state and audit store; no projection or legacy migration path exists.
- Every mutation receipt identifies the effective configuration, ledger root,
  control-plane path, and event or operation id.
- Operation ids make completed calls replayable and incomplete calls diagnosable.
- A `(repo common dir, branch)` claim has at most one task owner. Explicit
  clean-local `close`, `park`, and successful ship release it; PostToolUse
  releases only clean no-op ownership.
- Non-config commands cannot override the configured ledger root.

## Git and recovery invariants

- On first mutation after installation, the user chooses the ledger root once.
  The recommended default is
  `${CODEX_HOME:-$HOME/.codex}/ledgers/checkpoint-thread/active`; the persisted
  choice remains outside business repositories.
- Recovery refs live under `refs/codex/checkpoint-thread/` and are never pushed.
- A snapshot preserves HEAD, index, worktree, partial staging, and allowed
  untracked files without changing the user's index or worktree.
- Secrets, ignored files, local test output, generated output, and oversized
  untracked files are excluded and reported; large tracked files are preserved.
- Existing dirty paths, unrelated staged changes, local commits, and branches are
  not silently claimed as thread-owned.
- An unborn branch can park and restore dirty state; its first accepted promotion
  creates an exact-path root commit without claiming pre-existing files.
- Remote-reachable history is never rewritten and pushes are never forced.

## Enforcement invariants

- `PreToolUse` is silent for read-only tools and records `guard -> enter` before
  recognized Codex file or Git mutations.
- Missing configuration, another task's active branch claim, Git operations in
  progress, and direct Git history/delivery commands fail closed.
- `PostToolUse` never releases a claim while the branch is dirty, has unresolved
  checkpoints, or contains unpublished task-owned commits.
- Hook enforcement covers Codex tool calls. Human edits and processes outside
  Codex remain an explicit boundary.

## Verification invariants

- Verification binds to a Git `state_oid` for exact eligible worktree content,
  including untracked files, rather than merely the current HEAD.
- Promotion transfers verification to the new commit only when the verified
  state is complete and all changed paths are promoted together.
- Partial promotion, exclusions, later edits, failed checks, or stale evidence
  require a new verification before ship.

## Delivery invariants

- Fetch, rebase, history rewrite, and push require an explicit ship request.
- Ship set contains only ledger branches with unpublished thread-owned commits.
- Clean non-conflicting divergence may auto-rebase only after a safety ref and an
  ownership check; conflicts block with a resolution sequence.
- Branches for one remote push atomically. Cross-repo or cross-remote delivery is
  preflighted but reported as non-atomic.
- If one ref advances after preflight, an atomic group rejects every ref and
  reports every branch as failed with `fetch_and_replan`.
- Every branch receipt includes push status and a merge plan with target,
  strategy, dependency order, verification evidence, and conflict risk.
- Unresolved checkpoints, failed checks, stale recorded verification, dirty
  worktrees, or unowned local commits block shipping.

## Stable test seam

The bundled CLI exposes JSON for `enter`, `guard`, `settle`, `inspect`,
`snapshot`, `promote`, `ship-plan`, and related lifecycle commands. Disposable
repo tests assert SQLite state, events, operation replay, claims, Hook decisions,
Git refs, exact state ids, branch tips, index/worktree state, remote state, and
ledger decisions. The model retains responsibility for semantic goal
classification.
