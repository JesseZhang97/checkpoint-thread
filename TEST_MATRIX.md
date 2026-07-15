# Checkpoint Thread Test Matrix

The lab uses disposable Git repositories and local bare remotes. It never touches
real project repositories. The opt-in GitHub driver uses only uniquely named
`collab/<run-id>/...` branches on an explicitly supplied lab remote.

## Lifecycle and state

| Scenario | Expected behavior |
|---|---|
| First use without configuration | Block without mutation and suggest a root under `CODEX_HOME` |
| User-selected ledger root | Persist once, reuse automatically, and require explicit replacement |
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
| Rename, delete, symlink, executable mode | Preserve Git path identity and file modes exactly |
| Unmerged index | Block snapshots; rely on the pre-operation safety ref |
| Explicit restore gate | Refuse restore without `--confirm` |
| Dirty or moved restore target | Refuse to overwrite current work or restore onto a different HEAD |
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
| Cross-repo second push fails | Preserve the first success and report the rejected group as failed |
| Remote advances after preflight | Reject the stale push and return `fetch_and_replan` |
| Missing verification | Block until a current check or explicit `not_applicable` is recorded |
| Later verification supersedes failure | Use the latest command/scope result and retain full history |
| Multiple blockers | Return all applicable blockers and a stable primary action |
| Branch in another worktree | Rebase in its clean owning worktree without repurposing it |
| Private checkpoint refs | Never include them in any remote push |
| Push report | Include final branch status and divergence handling |
| Merge plan | Include target, strategy, dependencies, verification, and risk |
| Verification bound to old HEAD | Block ship until the check is rerun |

Run the full matrix with:

```bash
python3 -m unittest discover -s tests -v
```

Run the quantified acceptance model, including evidence validation, the full
local suite, structural budgets, and hot-path benchmarks, with:

```bash
python3 scripts/verify_acceptance.py \
  --remote-evidence acceptance/evidence/github-collaboration-final.json \
  --output acceptance/results.json
```

Run the opt-in real-remote collaboration matrix with:

```bash
python3 scripts/run_github_collaboration_lab.py \
  --remote git@github.com:OWNER/checkpoint-thread.git \
  --allow-remote-mutation \
  --report work/github-collaboration-report.json
```
