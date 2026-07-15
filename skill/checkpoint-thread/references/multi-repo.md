# Multiple Repositories

Read this reference only when one Codex thread mutates more than one Git common
directory.

- Keep one central ledger with a separate repo entry keyed by the absolute common
  Git directory.
- Maintain independent baselines, goals, branches, verification, remotes, safety
  refs, and merge plans for each repository.
- Treat nested repositories and submodules as separate repos; never let an outer
  `git add` absorb an inner repo's working files.
- Preflight every repository before any push. Push groups are atomic only within a
  single remote that supports atomic push.
- Report dependency order across repositories as a delivery plan, not a Git merge
  relationship.
- When one repository blocks, leave all unpushed repositories local unless the
  user explicitly accepts partial cross-repo delivery.
