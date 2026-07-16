# Checkpoint Thread Acceptance Criteria

## Meaning of "most scenarios"

Coverage is measured against the checked-in 104-scenario catalog, not raw Python
line coverage. The original 80 scenarios remain intact. Twenty-four V2.1
scenarios add the provenance ledger, verification integrity, shared-branch
concurrency, and Hook enforcement without weakening the previous gates.

Scenario weights are:

- P0, weight 5: a failure can lose work, publish unattributed history, misreport a
  delivery, or violate explicit authority.
- P1, weight 3: a common workflow or report becomes incorrect or unusable.
- P2, weight 1: a rare edge has a documented conservative fallback.

Acceptance requires all of the following:

1. At least 90% weighted scenario coverage overall.
2. 100% P0 and P1 coverage, with no open P0/P1 gap.
3. At least 80% weighted coverage in every domain.
4. At least 35% of executable scenarios are negative or blocking cases.
5. At least five scenarios pass against a real GitHub remote with independent
   clones and actor identities.
6. The full local suite passes and every evidence pointer resolves.

Automated local tests, asserted real-remote runs, and explicit semantic contracts
are valid evidence. A prose claim without an executable assertion or exact
contract clause is not coverage.

## Lean-skill gate

The model-loaded `SKILL.md` must remain at or below 100 lines and 650 words, with
at most four conditionally loaded references. A read-only thread creates no
state, and a same-goal continuation dispatches no semantic checkpoint command;
the synchronous mutation guard is runtime enforcement, not model context.

On the acceptance machine, the disposable-repo benchmark requires:

- `status` p95 at or below 250 ms;
- `enter` p95 at or below 750 ms.
- `guard` p95 at or below 500 ms;
- complete `PreToolUse + PostToolUse` no-op round trip p95 at or below 1000 ms.

These are regression budgets, not universal hardware guarantees. The observed
values and machine-independent structural limits are included in the report.

Persistence acceptance additionally requires:

- after initial entry, a no-op Pre/Post Hook pair leaves zero event and operation rows;
- a real Codex edit creates exactly one contribution event;
- multiple threads may enter the same branch concurrently;
- shared paths produce overlap evidence rather than a mutation denial;
- successful ship leaves no live recovery ref for that ledger branch.

## Supported scope

The catalog covers SQLite-only attribution state, operation replay, database integrity,
exact verification identity, contribution spans and shared-branch overlap,
unborn and ordinary worktrees, root commits, staging, untracked and ignored
files, multiple branches/worktrees/repos, local and GitHub remotes, Hook allow
and deny paths, concurrent remote changes, atomic rejection, rebase, conflict,
and push rejection.

Git LFS object transport, server administration, authentication recovery,
platform-specific case-collision behavior, and mutation of submodule contents by
the parent repository are outside the workflow. Submodules and nested repos are
enrolled as separate repositories.
