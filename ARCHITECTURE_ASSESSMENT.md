# Skill Architecture Assessment

## Verdict

V2.2 passes only when both context load and persisted operational load are
measured. SQLite is a provenance ledger, not a Git control plane: it attributes
actual edits to threads and goals while Git remains the sole authority for
content, branches, commits, and remotes. The fail-open Hook observes recognized
writes without locking branches or controlling normal Git use.

This is the intended scale boundary: a compact model-facing router plus a safety
kernel that records business-relevant state transitions. There is no daemon,
file watcher, semantic goal parser, branch ownership protocol, or distributed
coordinator.

## Quantified structure

| Layer | Observed | Assessment |
|---|---:|---|
| Triggered `SKILL.md` | 95 lines, 625 words | Pass: within 100-line and 650-word budgets |
| Conditional references | 4 | Pass: exactly at the allowed count |
| Reference depth | One hop from `SKILL.md` | Pass |
| Reference payload | 122 lines, 783 words total | Pass: each file is 12-49 lines |
| Lifecycle CLI | 2,549 lines, 6,256 words | Bundled and not model-loaded |
| SQLite store | 453 lines, 1,250 words | Separate persistence boundary |
| Pre/Post Hook | 169 lines, 438 words | 29% fewer lines; silent on success |
| Skill package | 1 skill, 4 references, 2 scripts, 1 UI metadata file | Pass |
| Acceptance suite | 107 scenarios, 80 tests | Original 80 plus 27 V2.2 scenarios |

The hot file is deliberately near its ceiling. New Git mechanics belong in code;
rare policy belongs in an existing one-hop reference. A new hot-path concept must
replace or compress an old one rather than simply expanding context.

## Disclosure layers

### Layer 1: discovery metadata

The frontmatter says to activate on every repo-mutating task and identifies the
automatic Hook fallback. Unrelated tasks see metadata only, not Git plumbing.

### Layer 2: hot workflow

`SKILL.md` contains entry, attribution, semantic goal boundary, authority, and
receipt contracts. Read-only work creates no state. Same-goal continuation adds
no agent-dispatched checkpoint or reference load.

### Layer 3: conditional policy

- `safety-snapshot.md` loads only for dirty-state preservation or recovery.
- `worktree.md` loads only when more than one worktree exists.
- `multi-repo.md` loads only when the task touches more than one repository.
- `ship.md` loads only after an explicit ship or push request.

All references are linked directly from `SKILL.md`; none links to another
reference. The conditions are observable, mutually understandable, and avoid
loading ship or topology policy on the common same-branch path.

### Layer 4: synchronous observation

`PreToolUse` recognizes explicit write tools and stores one transient
before-state. It ignores ordinary builds, tests, topology commands, and delivery.
`PostToolUse` deletes the span and writes one contribution only when content
changed. Failures warn and fail open; successful calls emit nothing into context.

### Layer 5: deterministic control and payload

The CLI orchestrates lifecycle and Git operations. `checkpoint_store.py` owns
SQLite state, milestone events, replay, transient Hook spans, and integrity
checks. Private Git refs hold recovery payloads only until delivered. The model
retains semantic goal classification and final attribution judgment.

## Runtime efficiency

- Same-goal continuation: no agent-dispatched checkpoint command.
- Read-only task: no ledger, database, ref, or reference load.
- `status` p95: 180.75 ms against a 250 ms budget.
- `enter` p95: 418.72 ms against a 750 ms budget.
- `guard` p95: 338.34 ms against a 500 ms budget.
- Pre/Post no-op round trip p95: 713.57 ms against a 1000 ms budget.
- Repeated `enter` is idempotent; operation ids make retries replayable.
- Failed Post spans remain diagnosable and are pruned by later guards after 24 hours.

## Persistence budgets

- No-op Pre/Post Hook round trip: zero event rows and zero operation rows after
  its transient span is removed.
- Real Codex edit: one contribution and one contribution event, independent of
  how many audit helpers ran.
- Same branch: any number of thread ledgers may enter; shared paths produce
  overlap evidence rather than a denial.
- Successful ship: zero live recovery refs for the shipped ledger branch.
- SQLite growth is O(real contributions + milestones), never O(all tool calls).

## Maintenance boundary

The V2.2 split stops at a deep persistence module instead of creating many shallow
packages. Keep Git lifecycle orchestration together while its commands share the
same invariants; split snapshot or delivery only if it gains an independent API
or platform implementation.

The remaining boundary is explicit: Hook attribution covers Codex tool calls in
their declared working repo, not human saves or external processes. Those edits
remain unattributed until commit selection. SQLite labels Git state transitions;
it never becomes an alternate branch graph, operating-system sandbox, or
distributed transaction service.
