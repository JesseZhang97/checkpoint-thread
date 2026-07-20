# Checkpoint Thread Specification

## Intent

Let several Codex threads work in a shared dirty Git workspace while preserving
recovery points and recording which thread and business goal produced each
change. The ledger helps organize those contributions into verified commits; it
does not own or lock branches.

## Operating model

- Thread is an attribution source.
- Goal or accepted milestone is the checkpoint cadence.
- Branch is a shared workspace and eventual delivery lane.
- Contribution is an observed before/after state transition during one Codex
  tool call under a thread and goal. Concurrent edits make it evidence rather
  than exclusive authorship.
- Turn is only conversation context, never a checkpoint unit.
- Enter is lazy and idempotent before the first persistent repo mutation.
- Goal transitions are semantic judgments by the model, not keyword parsing.
- The synchronous Hook observes recognized Codex writes; it is not a daemon,
  human file watcher, or Git policy gate.

An accepted goal becomes a local branch commit. Implicit progression into a
distinct low-risk goal creates a provisional private ref. Ambiguous progression
stays within one concern until evidence supports a boundary.

## Ledger invariants

- The selected ledger root contains one SQLite V2 provenance ledger. It stores
  attribution and milestone audit state, never file content or an alternate Git
  branch graph.
- Successful `guard` and no-op `settle` calls create no durable audit event or
  operation row. A real edit creates one contribution event.
- Hook spans are transient before-state records, deleted by successful
  PostToolUse and pruned after 24 hours when a later guard starts.
- Multiple ledgers may register the same repo and branch concurrently.
- Contributions record goal id, before and after `state_oid`, changed paths, and
  conservative cross-thread path overlaps.
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
  not silently attributed to the entering thread.
- An unborn branch can park and restore dirty state; its first accepted promotion
  creates an exact-path root commit without attributing pre-existing files.
- Remote-reachable history is never rewritten and pushes are never forced.
- Successful ship prunes recovery refs whose state is represented by pushed
  commits; receipts retain their historical ids.

## Enforcement invariants

- `PreToolUse` is silent for read-only tools, lazily enters the thread, and stores
  a transient before-state before recognized Codex mutations.
- Attribution failures fail open with a warning so the observer cannot prevent
  normal work. Ordinary build/test and delivery commands are ignored.
- `PostToolUse` records a contribution only when the resulting state differs.
- Same-branch threads are allowed. Same-path changes are marked as overlaps and
  reconciled during commit selection rather than blocked during editing.
- Hook observation covers recognized Codex write calls. Human edits and processes outside
  Codex remain unattributed until explicitly assigned.

## Verification invariants

- Verification binds to a Git `state_oid` for exact eligible worktree content,
  including untracked files, rather than merely the current HEAD.
- Promotion transfers verification to the new commit only when the verified
  state is complete and all changed paths are promoted together.
- Partial promotion, exclusions, later edits, failed checks, or stale evidence
  require a new verification before ship.
- Once the current tip has valid evidence, older passed command/scope records
  remain audit history and do not keep the branch permanently blocked.

## Delivery invariants

- Fetch, rebase, history rewrite, and push require an explicit ship request.
- Ship set contains only ledger branches with unpublished attributed commits.
- Clean non-conflicting divergence may auto-rebase only after a safety ref and an
  attribution check; conflicts block with a resolution sequence.
- Branches for one remote push atomically. Cross-repo or cross-remote delivery is
  preflighted but reported as non-atomic.
- If one ref advances after preflight, an atomic group rejects every ref and
  reports every branch as failed with `fetch_and_replan`.
- Every branch receipt includes push status and a merge plan with target,
  strategy, dependency order, verification evidence, and conflict risk.
- Unresolved checkpoints, failed checks, stale recorded verification, dirty
  worktrees, or unattributed local commits block shipping.

## Stable test seam

The bundled CLI exposes JSON for `enter`, `guard`, `settle`, `inspect`,
`snapshot`, `promote`, `ship-plan`, and related lifecycle commands. Disposable
repo tests assert SQLite state, events, operation replay, Hook decisions,
Git refs, contributions, shared-branch overlap reporting, exact state ids,
branch tips, index/worktree state, remote state, and ledger decisions. The model
retains responsibility for semantic goal classification and final attribution.
