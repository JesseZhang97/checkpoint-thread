# Safety Snapshots

Read this reference before preserving dirty state, leaving a branch, rewriting
history, or recovering a checkpoint.

## Snapshot model

The CLI creates two private commit objects without changing the real index or
worktree:

```text
current HEAD -> index snapshot -> worktree snapshot <- private ref
```

The index snapshot preserves staged content. The worktree snapshot adds allowed
unstaged and untracked content through a temporary index. Private refs live under
`refs/codex/checkpoint-thread/<ledger>/<branch>/` and are never included in a
normal branch push.

Run:

```bash
python3 scripts/checkpoint_thread.py --ledger-id "$LEDGER_ID" snapshot \
  --repo "$ROOT" --kind safety --reason "$REASON"
```

Use `park --repo "$ROOT" --reason "$REASON"` before leaving a dirty branch. It
cleans only after a complete snapshot exists. Return to the unchanged branch and
run `restore --repo "$ROOT" --ref "$REF" --confirm` only with explicit restore
authorization.

## Boundaries

- Preserve staged, unstaged, partial-staged, renamed, deleted, symlink, and file
  mode state without changing the user's index.
- Leave ignored files and recognized ignored test output outside the snapshot and
  report their collapsed paths. Mark the snapshot incomplete when eligible dirty
  content is excluded for secrets, size, or generated-output policy.
- Preserve modified large tracked files; they already belong to repository state.
- Treat submodule contents as another repository and enroll them separately.
- Block when the index is unmerged. Create the safety ref before merge or rebase.
- Keep history safety refs until the shipped branch is merged.

## Recovery

Do not restore automatically. First show the saved HEAD, index snapshot,
worktree snapshot, excluded paths, and the diff against current state. Restore
only after explicit user authorization, and preserve any state created after the
snapshot before replacing index or worktree content.
