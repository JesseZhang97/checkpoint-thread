# GitHub Collaboration Iteration Report

## Remote

- Repository: `JesseZhang97/checkpoint-thread-lab`
- Visibility: private
- Default branch: `main`
- Driver: `scripts/run_github_collaboration_lab.py`
- Verified run: `20260715040846-a8ab38`
- Remote mutations: uniquely named `collab/<run-id>/...` branches only

## Real collaboration scenarios

| Scenario | Observed result |
|---|---|
| Clean concurrent changes | Thread branch rebased onto the collaborator tip, retained a pre-rebase private ref, and pushed successfully |
| Same-file concurrent changes | Ship plan blocked before rewrite or push, reported the exact conflict path, and proposed rebase plus resolution |
| Remote advances after preflight | The stale push was rejected, the final receipt reported `failed`, and the solution was `fetch_and_replan` |
| New branch with a pre-thread commit | Ownership check blocked the branch and verified that no remote branch was created |
| Two thread branches on one remote | GitHub accepted one atomic push group and both branch receipts reported `push_mode: atomic` |

## Receipt iteration

The first real-remote run proved the Git operations but exposed missing delivery
evidence in the returned receipt. The second iteration added:

- `divergence_handling` with action, old tip, new tip, upstream ref, and safety ref;
- `push_mode` per branch (`single` or `atomic`);
- merge-plan source, target, pre-merge action, verification, dependency order,
  strategy, and conflict risk;
- explicit high-risk classification for a verified content conflict.

The final clean-divergence branch changed from local tip
`d70ca755d1324307abb9b86558f240a7c9a2fcd2` to rebased tip
`578a4bdeb33197606d912c1297c095156469179a`. Its private safety ref remained
local and the final tip matched the GitHub branch.

## Safety observations

- The conflicting branch remained blocked; the tool did not start a real rebase.
- The unowned branch stayed local and was not created on GitHub.
- Private `refs/codex/checkpoint-thread/...` refs were not published.
- Scenario commits used separate `Thread Agent` and `Remote Collaborator` authors.
- The project default branch was not used as a collaboration scratch branch.

## Local collaboration extensions

- A two-repository test proves non-atomic partial delivery reporting: the first
  push remains `pushed`, the rejected group is `failed`, and both appear in the
  receipt.
- Verification omission, supersession, stale evidence, dirty worktrees, missing
  branches, remote ambiguity, fetch failures, and multiple simultaneous blockers
  are asserted in disposable repositories.
- Post-merge ledger archival and safety-ref retention policy remain lifecycle
  policy work rather than a push-correctness gap.
