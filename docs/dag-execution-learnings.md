# DAG Execution Learnings

Living notes from feature `8ac124d6`, especially groups 0-32.

## Current Read

The fastest-looking execution path was not always the fastest end-to-end path.
High fanout made initial implementation waves finish quickly, but broad packed
waves amplified stale artifact, task-spec, and verifier-context drift. Once that
drift entered the retry loop, the workflow spent far more time repairing derived
state than validating product behavior.

The best target is likely not tiny serial waves or hard-packed 20-task waves.
We should move toward smaller semantic waves with explicit integration barriers
for cross-cutting contracts.

## Timing Observations

- Groups 0-19 covered 35 tasks in small natural waves, mostly 1-2 tasks each.
- G0-G19 raw wall time was about 21h36m, but included an obvious 9h32m idle gap
  during G16.
- G0-G19 adjusted active time was about 12h04m, or roughly 20.7 min/task.
- G0-G19 had 40 verifier runs and 0 programmatic preflight failures.
- G20 onward switched into hard-packed 20-task waves.
- Healthy packed waves can be faster. G20-G25 covered 120 tasks in about 33h56m,
  around 17 min/task raw.
- The packed-wave tail was costly. G30 took about 16h16m end to end, with 17
  verifier attempts blocked before semantic verification by stale preflight
  state.
- G32 under lower parallelism took about 8h27m end to end. Its initial 20-task
  implementation took about 1h35m, but most time was still spent in verification
  and revision.

## What Worked

- Small natural groups localized failures well.
- Programmatic preflight was useful when it failed for real structural drift.
- Bounded concurrency made later revision artifacts easier to understand and
  reduced runtime/usage pressure.
- Append-only DAG task result repair is the right persistence model when paired
  with latest-row reconciliation.
- Deterministic host-side reconciliation improved convergence more reliably than
  asking implementers to infer stale metadata repairs from labels.

## What Hurt

- Hard 20-task wave packing mixed unrelated domains into one verification unit.
  A stale chat-sidepane path could block unrelated backend, bridge, artifact, or
  lifecycle work in the same group.
- The root DAG remained stale after downstream repairs. At one point the latest
  root DAG still contained hundreds of retired path references, so runtime
  canonicalization and projection repair were compensating for planning output.
- Stale state appeared in multiple layers:
  - root DAG task specs
  - subfeature DAG fragments
  - generated expanded-verify snapshots
  - changed-files artifacts
  - `dag-task:*` implementation result rows
  - handover/context files
- Repair cycles were often piecemeal. G30 repeatedly rediscovered variants of
  the same retired chat path issue under different RCA names.
- Parallel repair increased edit throughput, but also increased synthetic result
  rows, blocked artifact-repair rows, and coordination surface.
- Generated verifier context sometimes behaved like authoritative source state,
  even though it should be disposable projection.

## Decomposition Learnings

- Task waves should be semantic, not only topologically available.
- A hard max-size group can hide implicit dependencies that are not declared in
  the DAG.
- Shared contract surfaces should create dependency or barrier pressure even
  when file scopes do not overlap directly.
- Examples of implicit coupling:
  - source code and generated catalog mirrors
  - protocol constants and fixture docs
  - approval signing code and approval mirror artifacts
  - barrel exports and sibling feature imports
  - source files and verifier corpus snapshots
  - migrated source paths and expected/forbidden path manifests
- Canonical file paths should be enforced during planning persistence, not only
  at implementation runtime.

## Process Changes To Consider

- Replace hard 20-task waves with semantic waves, probably around 5-10 related
  tasks when the surface is cross-cutting.
- Keep larger waves only when tasks are clearly disjoint by repo, path prefix,
  contract surface, and verifier ownership.
- Add explicit integration barrier tasks for high-coupling surfaces:
  - bridge protocol
  - chat-sidepane relocation
  - artifact lifecycle
  - backend compilation/approval
  - generated catalogs and fixture mirrors
- Add inferred dependency edges for shared file scopes, shared exports, generated
  mirrors, fixture/catalog parity, and tests that validate another task's output.
- Run DAG/task-spec canonicalization before implementation starts and fail the
  group if retired paths remain in authoritative planning artifacts.
- Treat generated verifier context as disposable and regenerate it from
  reconciled source state before retry.
- Keep product repair separate from artifact-only repair. If forbidden product
  files exist on disk or in the git index, route to product cleanup before
  appending corrected `dag-task:*` rows.
- Consider bounded implementation concurrency by default, with higher
  concurrency only for read-only expanded verification once usage allows.

## Open Questions

- What is the best semantic wave target: 5, 8, 10, or dynamic by risk score?
- Should the wave builder score cross-cutting contract risk before packing tasks?
- Should artifact closure scans run once per group before initial implementation,
  or only before verification/retry?
- Should root DAG canonicalization rewrite and persist authoritative task specs,
  or should it fail and require planning repair?
- Can we measure "revision tail risk" before dispatch using path drift, shared
  file ownership, and contract-surface overlap?

