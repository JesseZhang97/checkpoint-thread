# Skill Architecture Assessment

## Verdict

V2 passes the lean-architecture and progressive-disclosure review. It adds a
SQLite control plane and synchronous enforcement Hook without putting either in
model context. The hot skill remains a compact policy router; deterministic code
owns auditing, branch claims, exact state identity, recovery, and delivery.

This is the intended scale boundary: more runtime rigor, nearly unchanged model
load. There is still no daemon, file watcher, semantic goal parser, or distributed
coordinator.

## Quantified structure

| Layer | Observed | Assessment |
|---|---:|---|
| Triggered `SKILL.md` | 99 lines, 649 words | Pass: within 100-line and 650-word budgets |
| Conditional references | 4 | Pass: exactly at the allowed count |
| Reference depth | One hop from `SKILL.md` | Pass |
| Reference payload | 122 lines, 783 words total | Pass: each file is 12-49 lines |
| Lifecycle CLI | 2,345 lines, 5,864 words | Bundled and not model-loaded |
| SQLite store | 459 lines, 1,306 words | Separate persistence boundary |
| Pre/Post Hook | 224 lines, 555 words | Silent on allow; no model tokens |
| Skill package | 1 skill, 4 references, 2 scripts, 1 UI metadata file | Pass |
| Acceptance suite | 100/100 scenarios, 73 tests | Original 80 plus 20 V2 scenarios |

The hot file is deliberately near its ceiling. New Git mechanics belong in code;
rare policy belongs in an existing one-hop reference. A new hot-path concept must
replace or compress an old one rather than simply expanding context.

## Disclosure layers

### Layer 1: discovery metadata

The frontmatter says to activate on every repo-mutating task and identifies the
automatic Hook fallback. Unrelated tasks see metadata only, not Git plumbing.

### Layer 2: hot workflow

`SKILL.md` contains entry, dispatch, semantic goal boundary, authority, and
receipt contracts. Read-only work creates no state. Same-goal continuation adds
no agent-dispatched checkpoint or reference load; the silent Hook guard still
enforces each recognized mutation.

### Layer 3: conditional policy

- `safety-snapshot.md` loads only for dirty-state preservation or recovery.
- `worktree.md` loads only when more than one worktree exists.
- `multi-repo.md` loads only when the task touches more than one repository.
- `ship.md` loads only after an explicit ship or push request.

All references are linked directly from `SKILL.md`; none links to another
reference. The conditions are observable, mutually understandable, and avoid
loading ship or topology policy on the common same-branch path.

### Layer 4: synchronous enforcement

`PreToolUse` classifies tool calls, blocks direct history/delivery bypass, and
runs `guard` before recognized mutations. `PostToolUse` settles clean no-op
claims. Successful Hook calls emit nothing into context.

### Layer 5: deterministic control and payload

The CLI orchestrates lifecycle and Git operations. `checkpoint_store.py` owns
SQLite schema, events, replay, claims, and integrity checks. Private Git
refs hold recovery payloads. The model retains only semantic goal classification.

## Runtime efficiency

- Same-goal continuation: no agent-dispatched checkpoint command.
- Read-only task: no ledger, database, ref, or reference load.
- `status` p95: 170.61 ms against a 250 ms budget.
- `enter` p95: 412.10 ms against a 750 ms budget.
- `guard` p95: 232.28 ms against a 500 ms budget.
- Pre/Post no-op round trip p95: 577.77 ms against a 1000 ms budget.
- Repeated `enter` is idempotent; operation ids make retries replayable.

## Maintenance boundary

The V2 split stops at a deep persistence module instead of creating many shallow
packages. Keep Git lifecycle orchestration together while its commands share the
same invariants; split snapshot or delivery only if it gains an independent API
or platform implementation.

The remaining boundary is explicit: Hook enforcement covers Codex tool calls in
their declared working repo, not human saves or external processes; SQLite
coordinates one local ledger root, not multiple machines. Those limits avoid
pretending a lightweight local skill is an operating-system sandbox or
distributed transaction service.
