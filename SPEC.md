# Checkpoint Thread Specification

## Intent

Give one Codex thread local recovery and delivery structure without requiring a
manual begin command or treating every turn as a commit. The skill tracks the
thread's work across files, branches, worktrees, and occasional multiple repos,
then ships only the history owned by that thread.

## Operating model

- Thread is the ownership scope.
- Goal or accepted milestone is the checkpoint cadence.
- Branch is the delivery lane.
- Turn is only conversation context, never a checkpoint unit.
- Begin is lazy and idempotent before the first persistent repo mutation.
- Goal transitions are semantic judgments by the model, not keyword parsing.

An accepted goal becomes a local branch commit. Implicit progression into a
distinct low-risk goal creates a provisional private ref. Ambiguous progression
stays within one concern until evidence supports a boundary.

## Git and ledger invariants

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
- Remote-reachable history is never rewritten and pushes are never forced.

## Delivery invariants

- Fetch, rebase, history rewrite, and push require an explicit ship request.
- Ship set contains only ledger branches with unpublished thread-owned commits.
- Clean non-conflicting divergence may auto-rebase only after a safety ref and an
  ownership check; conflicts block with a resolution sequence.
- Branches for one remote push atomically. Cross-repo or cross-remote delivery is
  preflighted but reported as non-atomic.
- Every branch receipt includes push status and a merge plan with target,
  strategy, dependency order, verification evidence, and conflict risk.
- Unresolved checkpoints, failed checks, stale recorded verification, dirty
  worktrees, or unowned local commits block shipping.

## Stable test seam

The bundled CLI exposes JSON for `status`, `snapshot`, `promote`, `ship-plan`,
and related lifecycle commands. Disposable-repo tests assert observable Git
refs, branch tips, index/worktree state, remote state, and ledger decisions. The
model retains responsibility for semantic goal classification.
