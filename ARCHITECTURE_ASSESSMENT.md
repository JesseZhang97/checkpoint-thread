# Skill Architecture Assessment

## Verdict

The skill passes both the lean-architecture and progressive-disclosure review.
Its model-facing surface is compact, operational details are loaded only when
needed, and fragile Git mutations are isolated behind one deterministic JSON CLI.

The only watch item is implementation maintainability: the CLI is intentionally
single-file and has reached 1,769 lines. This does not consume model context and
currently buys simple distribution, but future unrelated feature growth should
trigger a module split rather than a larger `SKILL.md`.

## Quantified structure

| Layer | Observed | Assessment |
|---|---:|---|
| Triggered `SKILL.md` | 94 lines, 573 words | Pass: 94% of line budget, 82% of word budget |
| Conditional references | 4 | Pass: exactly at the allowed count |
| Reference depth | One hop from `SKILL.md` | Pass |
| Reference payload | 122 lines, 783 words total | Pass: each file is 12-49 lines |
| Deterministic CLI | 1,769 lines, 4,516 words | Pass with watch: bundled, not model-loaded |
| Skill package artifacts | 1 skill, 4 references, 1 CLI, 1 UI metadata file | Pass |
| Acceptance suite | 80/80 scenarios, 52 tests | Pass |

The line budget is close to its ceiling because the dispatch table occupies one
line per event. The word budget has meaningful headroom. Future branch, recovery,
or delivery detail belongs in an existing reference rather than the hot file.

## Disclosure layers

### Layer 1: discovery metadata

The frontmatter description and `agents/openai.yaml` explain when to activate the
skill without embedding the workflow. They do not expose Git plumbing or test
details during unrelated tasks.

### Layer 2: hot workflow

`SKILL.md` contains only the operating model, first-use configuration, dispatch,
semantic goal boundary, authority boundary, and receipt contract. Read-only work
creates no state, and same-goal continuation performs no CLI call or reference
load.

### Layer 3: conditional policy

- `safety-snapshot.md` loads only for dirty-state preservation or recovery.
- `worktree.md` loads only when more than one worktree exists.
- `multi-repo.md` loads only when the task touches more than one repository.
- `ship.md` loads only after an explicit ship or push request.

All references are linked directly from `SKILL.md`; none links to another
reference. The conditions are observable, mutually understandable, and avoid
loading ship or topology policy on the common same-branch path.

### Layer 4: deterministic execution

The Python CLI owns ledger persistence, Git snapshots, exact-path promotion,
recovery, ownership checks, rebase, push, and receipts. The model retains only
semantic goal classification. This is the correct freedom split: language
judgment remains flexible while destructive operations use a low-freedom tool.

## Runtime efficiency

- Same-goal continuation: no checkpoint command.
- Read-only task: no ledger, ref, configuration, or reference load.
- `status` p95: 177.34 ms against a 250 ms budget.
- `begin` p95: 420.66 ms against a 750 ms budget.
- Repeated `begin`: idempotent and returns the existing baseline.

## Maintenance boundary

Keeping one executable remains reasonable because installation copies one
self-contained script and the CLI presents stable JSON commands. Split it only
when at least one of these becomes true:

1. Snapshot, promotion, or delivery logic needs independent reuse.
2. Changes repeatedly cross unrelated sections of the file.
3. Platform-specific Git behavior requires separate implementations.
4. Unit isolation becomes materially harder than disposable-repository testing.

Until then, a module split would add packaging and navigation cost without
reducing model context or observable risk.
