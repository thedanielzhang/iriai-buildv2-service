# beced7b1 Plan Review Context

This file is a compact handoff for the in-flight feature `beced7b1` ("iriai-compose workflow creator").
It summarizes the previous plan-review fix cycles and points at the underlying source artifacts.

## What Exists

These prior-cycle artifacts exist on disk in the feature artifact mirror:

- Cycle 1 report: `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-1.md`
- Cycle 1 discussion: `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-1.md`
- Cycle 2 report: `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-2.md`
- Cycle 2 discussion: `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md`
- Cycle 3 report: `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-3.md`
- Cycle 3 discussion: `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-3.md`
- Current cycle 4 report: `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md`

These cycle markers exist in Postgres but are not mirrored as files:

- `plan-review-cycle-1-revised`
- `plan-review-cycle-2-revised`
- `plan-review-cycle-3-revised`

No `plan-review-discussion-4` artifact exists yet.

## Key History

### Cycle 1

Source docs:

- `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-1.md`
- `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-1.md`

High-signal decisions:

- PRD staleness was resolved by treating Design and Plan as authoritative and backfilling PRDs.
- ExecutionResult contract: keep the Plan shape canonical and add `map_fan_out` on `ExecutionHistory`, not `ExecutionResult`.
- Add full checkpoint/resume and error-port routing.
- Security model: AST-validated `exec()` is allowed; dangerous patterns must be rejected.
- SF-5 must restore missing infrastructure such as WorkflowVersion, rate limiting, structured logging, missing endpoints, and Tool library support.

DB-only revised marker:

- `plan-review-cycle-1-revised`
- Text: `Revisions applied: design (5 SFs Mar 27), plan (7 SFs Mar 27), prd (7 SFs Mar 28). Manual marker.`

### Cycle 2

Source docs:

- `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-2.md`
- `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md`

High-signal decisions:

- SF-2 runner plan must be fully rewritten.
- SF-5 composer foundation needs significant revision, including Vite, WorkflowVersion, non-purple branding, payload limits, YAML validation, rollback docs, and system-design alignment.
- SF-3 testing framework must be fully rewritten.
- SF-4 migration must be narrowed to declarative-runner integration plus missing tests.
- SF-7 libraries must add Tool Library, fill step stubs, and fix ownership boundaries.

DB-only revised marker:

- `plan-review-cycle-2-revised`
- Text: `Partial revisions applied before usage limit. Manual marker to advance to cycle 3 fresh reviews.`

### Cycle 3

Source docs:

- `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-3.md`
- `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-3.md`

High-signal context:

- About 85% of cycle 3 findings were repeats of cycle 1-2 decisions that had not actually been applied.
- Cycle 3 explicitly reconfirms that all `D-GR-1` through `D-GR-21` are mandatory, not advisory.

New cycle 3 decisions:

- `D-GR-15`: Checkpoint writes are explicit DAG behavior via `CheckpointPlugin`; no implicit runner checkpoint writes.
- `D-GR-16`: Port storage uses `list[PortDefinition]`; dict shorthand is loader sugar only.
- `D-GR-17`: Expression limit is 10,000 chars at both schema and runner levels.
- `D-GR-18`: CLI flag is `--declarative`.
- `D-GR-19`: `file_first_resolve` is a built-in plugin for resume-safe artifact caching.
- `D-GR-20`: Both `TemplateDefinition` and `TemplateRef` must exist.
- `D-GR-21`: Re-apply all cycle 1-2 decisions fully.

DB-only revised marker:

- `plan-review-cycle-3-revised`
- Summary: partial revisions were dispatched; 15 of roughly 22 completed before usage limits were hit.
- Completed:
  - composer-app-foundation: `prd`, `design`, `plan`
  - dag-loader-runner: `prd`, `design`
  - declarative-schema: `prd`, `design`, `plan`
  - libraries-registries: `prd`, `design`, `plan`
  - testing-framework: `plan`
  - workflow-editor: `design`, `plan`
  - workflow-migration: `plan`
- Not completed:
  - testing-framework: `design`, `system-design`
  - workflow-migration: `design`, `system-design`
  - dag-loader-runner: `plan`, `system-design`
  - workflow-editor: `system-design`
  - composer-app-foundation: `system-design`
  - libraries-registries: `system-design`
  - declarative-schema: `system-design`

## Current Resume Context

The workflow is currently in `plan-review` cycle 4.

Use these together for the next agent turn:

- This file
- `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md`
- `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-3.md`

## Important Limitation

There are not per-cycle frozen snapshots of the compiled main artifacts after each revision round.
The prior fix cycles are preserved mainly as:

- cycle review reports
- cycle discussion docs
- DB-only `*-revised` markers

The compiled working artifacts that remain live for the feature are the current versions in:

- `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/prd.md`
- `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/design-decisions.md`
- `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan.md`
- `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/system-design.html`
