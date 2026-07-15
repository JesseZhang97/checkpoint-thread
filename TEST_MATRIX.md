# Checkpoint Thread Test Matrix

The lab uses disposable Git repositories and local bare remotes. It never touches
real project repositories. The opt-in GitHub driver uses only uniquely named
`collab/<run-id>/...` branches on an explicitly supplied lab remote.

The quantified catalog contains the original 80 workflow scenarios plus 20 V2
scenarios. The executable suite contains 52 original tests and 20 V2 tests.

## V2 control and enforcement

| Scenario | Expected behavior |
|---|---|
| Canonical store | SQLite schema V2 is the only ledger state and audit store |
| Legacy JSON | Ignore old projection files rather than importing or repairing them |
| Root selection | Non-config commands reject a root different from saved configuration |
| Enter preflight | Receipt locates every store and persists head, branch, changes, and result |
| Operation identity | Completed ids replay once; interrupted ids remain visible to `inspect` |
| Database integrity | `inspect --check` and `doctor` report SQLite integrity directly |
| Verification identity | Exact complete `state_oid` may transfer to a promoted commit |
| Partial promotion | Verification for unrelated dirty state is not transferred |
| Concurrent entry | Exactly one task acquires a repo/branch claim |
| Claim release | Close, park, ship, and clean no-op settle release; dirty settle retains |
| Read-only Hook | Return silently without a ledger, database, or ref |
| Mutation Hook | Enter before the write and deny missing configuration or competing owner |
| Direct Git bypass | Block raw history/delivery commands and require lifecycle CLI |
| Package contract | Skill, plugin, marketplace, and Pre/Post Hook manifests resolve |
| Performance | `status`, `enter`, `guard`, and Hook round trip stay within p95 budgets |

## Lifecycle and state

| Scenario | Expected behavior |
|---|---|
| First use without configuration | Block without mutation and suggest a root under `CODEX_HOME` |
| User-selected ledger root | Persist once, reuse automatically, and require explicit replacement |
| Unborn branch lifecycle | Park, restore, promote an exact-path root commit, verify, and ship |
| Unborn pre-existing path | Refuse to claim files already present when the thread enters |
| First mutation | `guard` creates one preflight, baseline ledger entry, and private ref |
| Repeated enter | Return the original baseline without overwriting it |
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
| Dirty path before enter | Refuse to claim the whole file as thread-owned |

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
| One atomic ref advances after preflight | Reject every ref and mark every branch failed |
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
