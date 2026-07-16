# Ship

Read this reference only after the user explicitly requests ship or push.

## Prepare

Run `ship-plan --fetch` and define the ship set as branches registered by this
ledger with unpublished attributed commits. Do not include branches that were
merely visited, unrelated local commits, tags, or other remotes.

Reconcile every provisional, safety, or failed confirmed checkpoint before
shipping. Promote accepted work with `promote --checkpoint-ref "$REF"`; mark an
intentionally omitted checkpoint with `resolve-checkpoint --resolution excluded`
and a factual reason. Never let `ship` silently ignore unresolved checkpoint refs.

Require every ship-set branch to have a named local branch, configured or
unambiguous remote, merge target, current verification evidence, and no unmerged
index or unfinished Git operation.

When upstream advanced, automatically rebase only when the worktree is clean,
every local-only commit is attributed in this ledger, and a pre-rebase safety ref
exists. Abort and block on conflict. Report the conflict paths and the
rebase-based resolution sequence; do not weaken verification or force-push.

## Push

Run `ship --fetch` only after all branches pass preflight. Push multiple branches
to the same remote with `--atomic`. Preflight all groups before sequential pushes
across different remotes or repositories, and report that cross-remote delivery
cannot be atomic.

## Report

Produce two sections:

1. Push report: repo, branch, final SHA, upstream, divergence handling, safety ref,
   verification, and pushed/local-only/blocked status.
2. Merge plan: source, target, dependency order, recommended squash/rebase/merge
   strategy, pre-merge action, post-merge verification, and conflict risk.

Prefer repository contribution rules. Otherwise squash provisional fragments for
one concern, preserve independently useful atomic commits with rebase-and-merge,
and use a merge commit only when integration topology matters. Leave an unknown
merge target unresolved instead of inventing `main`.
