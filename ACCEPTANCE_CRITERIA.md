# Checkpoint Thread Acceptance Criteria

## Meaning of "most scenarios"

Coverage is measured against the checked-in scenario catalog, not against raw
Python line coverage. The catalog is the product of six workflow phases
(invocation, goal boundary, recovery, promotion, topology, and delivery) crossed
with the Git hazards relevant to that phase.

Scenario weights are:

- P0, weight 5: a failure can lose work, publish unowned history, misreport a
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

The model-loaded `SKILL.md` must remain at or below 100 lines and 700 words, with
at most four conditionally loaded references. A read-only thread creates no
state, and a same-goal continuation executes no checkpoint CLI command.

On the acceptance machine, the disposable-repo benchmark requires:

- `status` p95 at or below 250 ms;
- `begin` p95 at or below 750 ms.

These are regression budgets, not universal hardware guarantees. The observed
values and machine-independent structural limits are included in the report.

## Supported scope

The acceptance catalog covers unborn and ordinary non-bare Git worktrees, root
commits, partial staging, untracked and ignored files, branches, multiple
worktrees, multiple repositories, local and GitHub remotes, hooks, concurrent
remote changes, atomic group rejection, rebase, conflict and push rejection paths.

Git LFS object transport, server administration, authentication recovery,
platform-specific case-collision behavior, and mutation of submodule contents by
the parent repository are outside the workflow. Submodules and nested repos are
enrolled as separate repositories.
