# GitHub Collaboration Iteration Report

## Remote

- Repository: `JesseZhang97/checkpoint-thread-lab`
- Visibility: private
- Default branch: `main`
- Driver: `scripts/run_github_collaboration_lab.py`
- Verified run: `20260715034222-329d0e`
- Remote mutations: uniquely named `collab/<run-id>/...` branches only

## Real collaboration scenarios

| Scenario | Observed result |
|---|---|
| Clean concurrent changes | Thread branch rebased onto the collaborator tip, retained a pre-rebase private ref, and pushed successfully |
| Same-file concurrent changes | Ship plan blocked before rewrite or push, reported the exact conflict path, and proposed rebase plus resolution |
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

The clean-divergence branch changed from local tip
`d2e2ff035d2280c305b683e995b4c94f2d67d266` to rebased tip
`ba12c7bfd4d0d19610b7638bc094cff8e20651b4`. Its private safety ref remained
local and the final tip matched the GitHub branch.

## Safety observations

- The conflicting branch remained blocked; the tool did not start a real rebase.
- The unowned branch stayed local and was not created on GitHub.
- Private `refs/codex/checkpoint-thread/...` refs were not published.
- Scenario commits used separate `Thread Agent` and `Remote Collaborator` authors.
- The project default branch was not used as a collaboration scratch branch.

## Remaining collaboration gaps

1. Simulate a remote advance after preflight but immediately before push, and
   verify the rejection receipt and retry plan.
2. Exercise partial failure across two real repositories/remotes, where atomicity
   is impossible.
3. Define ledger completion, archive, and safety-ref retention after merge.
