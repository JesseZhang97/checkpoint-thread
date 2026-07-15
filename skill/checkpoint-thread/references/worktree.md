# Worktrees

Read this reference only when `git worktree list` returns more than one worktree.

- Identify a repository by `git rev-parse --git-common-dir`, not by worktree path.
- Record each worktree path separately and bind its checked-out branch once.
- Share private checkpoint refs through the common Git directory.
- Check cleanliness only in the worktree that owns the branch being rebased or
  promoted; another worktree may remain active independently.
- Create a temporary worktree for a clean automatic rebase only when the branch is
  not checked out elsewhere. Remove it after success or abort.
- Never prune, delete, or repurpose a user-created worktree during checkpointing.
