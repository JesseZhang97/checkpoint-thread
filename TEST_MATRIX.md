# Checkpoint Thread Test Matrix

The lab uses disposable Git repositories and local bare remotes. It never touches
real project repositories or network remotes.

## Lifecycle and state

| Scenario | Expected behavior |
|---|---|
| First mutation | Create one baseline ledger entry and private ref |
| Repeated begin | Return the original baseline without overwriting it |
| Detached HEAD | Block before creating a ledger entry |
| Multiple worktrees | Share repo refs while retaining distinct worktree paths |
| Multiple repos | Keep independent repo entries in one central ledger |

## Index and recovery

| Scenario | Expected behavior |
|---|---|
| Partial staging | Report index/worktree overlap without changing the index |
| Staged + unstaged + untracked | Preserve all three layers in a two-commit snapshot |
| Park and restore | Round-trip partial staging and untracked files exactly |
| Unmerged index | Block snapshots; rely on the pre-operation safety ref |
| Explicit restore gate | Refuse restore without `--confirm` |
| Conflict markers | Block promotion while retaining a recovery ref |
| Failed commit hook | Leave branch unchanged and retain the checkpoint ref |

## File safety

| Scenario | Expected behavior |
|---|---|
| Secret path staged | Block baseline snapshot |
| Secret path untracked | Exclude it and mark snapshot incomplete |
| Large untracked file | Exclude it above the configured limit |
| Large tracked file | Preserve it because it already belongs to repo history |
| Ignored test output | Leave it outside snapshots and commits; report its root |
| Unignored generated output | Exclude it and report the path |
| Dirty path before begin | Refuse to claim the whole file as thread-owned |

## Collaboration and delivery

| Scenario | Expected behavior |
|---|---|
| Remote advances without overlap | Create pre-rebase ref, rebase, then push |
| Remote advances with overlap | Block and report conflict paths and rebase solution |
| Unowned local commits | Block ship until commits are separated or confirmed |
| New remote branch with pre-thread commits | Block before publishing unowned ancestry |
| Untouched local branch | Exclude it from the ship set |
| Already-pushed thread branch | Exclude it from later ship sets |
| Multiple branches, one remote | Push with one atomic group |
| Multiple repos/remotes | Preflight separately and report non-atomic delivery |
| Push report | Include final branch status and divergence handling |
| Merge plan | Include target, strategy, dependencies, verification, and risk |
| Verification bound to old HEAD | Block ship until the check is rerun |

Run the full matrix with:

```bash
python3 -m unittest discover -s tests -v
```
