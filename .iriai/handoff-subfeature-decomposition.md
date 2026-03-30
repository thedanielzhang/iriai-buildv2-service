# Handoff: Subfeature Decomposition for Planning Phases

## What You're Implementing

Restructure the PM, Design, Architecture, and TaskPlanning phases in iriai-build-v2's planning workflow from single-agent monolithic artifacts to a broad-to-narrow decomposition pattern. Each phase follows: **Broad Interview → Subfeature Decomposition → Per-Subfeature Interviews → Lead Integration Review → Compilation → Interview-Based Gate Review**.

## Read These First

1. **The full plan**: `/Users/danielzhang/.claude/plans/sequential-swinging-ocean.md` — this is the complete specification with 17 sections. Read it fully before writing any code.
2. **CLAUDE.md**: `/Users/danielzhang/src/iriai/iriai-build-v2/CLAUDE.md` — project conventions and framework overview.
3. **Memory files**: `/Users/danielzhang/.claude/projects/-Users-danielzhang-src-iriai-iriai-build-v2/memory/MEMORY.md` — user preferences including: no silent degradation, no refactoring existing code, all Slack interactions via cards, quality over speed, cite everything, gates as interviews, don't over-engineer.

## Key Decision Summary

These decisions were made during planning and must be followed:

- **Interview everywhere**: Per-subfeature agents use `HostedInterview` (multi-turn with user), not `Ask`. No cap on questions. Goal is maximum depth.
- **Sequential, not parallel**: Subfeature agents run one at a time so each has access to prior subfeatures' artifacts.
- **Tiered context injection**: Build context inline in `_helpers.py` (NOT a separate `SubfeatureContextProvider` class). Tier 1 = always full text. Tier 2 = full text for edge-connected subfeatures. Tier 3 = summary for unconnected.
- **No new DB tables**: Decisions and progress tracked via existing `events` table + `log_event()`. No `PostgresDecisionStore` or `PostgresProgressStore`.
- **Reuse existing output models**: Broad design uses `DesignDecisions`. Broad architecture uses `TechnicalPlan`. No `BroadDesignSystem`, `BroadArchitecture`, or `DecompositionAlignment` models.
- **Nested subdirectory filesystem layout**: `subfeatures/{slug}/prd.md`, not flat `prd--slug.md`.
- **Interview-based gate review**: Replace binary Approve/Reject `Gate` with `HostedInterview`. Lead agent interviews user to understand changes, produces `RevisionPlan`, routes to affected subfeature agents. The compiled artifact is NEVER edited directly — all changes flow through subfeature revisions.
- **Citations on everything**: Every requirement, journey, component, and architectural decision must cite its justification (`[code: file:line]`, `[decision: D-N]`, or `[research: description]`).
- **Turn persistence for mid-interview resume**: Persist turns to `sessions.metadata.turns` JSONB after each message in the runtime.
- **`GlobalImplementationStrategy`** is the only new model that doesn't reuse existing types (needed for TaskPlanningPhase's subfeature execution ordering).

## Implementation Phasing

### Phase A: Infrastructure (do this first)

| Step | File | What to do |
|---|---|---|
| 1 | `src/iriai_build_v2/models/outputs.py` | Add `Citation`, `Subfeature`, `SubfeatureDecomposition`, `SubfeatureEdge`, `EdgeCheck`, `IntegrationReview`, `RevisionRequest`, `RevisionPlan`, `ReviewOutcome`. Add `citations: list[Citation]` to `Requirement`, `AcceptanceCriterion`, `JourneyStep`, `ComponentDef`, `ImplementationStep`, `SubfeatureEdge` |
| 2 | `src/iriai_build_v2/models/state.py` | Add `decomposition: str = ""` to `BuildState` |
| 3 | `src/iriai_build_v2/services/artifacts.py` | Replace `_key_to_filename()` with `_key_to_path()` for nested subdirectory structure. Update `write_artifact()` to `mkdir(parents=True)`. See plan Section 15 for the exact mapping function |
| 3b | `src/iriai_build_v2/services/hosting.py` | Update `try_collect()` and `clear_feedback()` for nested `.feedback/` paths |
| 4 | `src/iriai_build_v2/runtimes/claude.py` | Add turn persistence: append `{"role", "text", "turn"}` to `session.metadata["turns"]` after each message. Add `get_active_session_key(feature_id)` method |

### Phase B: PMPhase + iriai-feedback serve (prove the pattern)

| Step | File | What to do |
|---|---|---|
| 5-8 | `roles/lead_pm/`, `roles/compiler/`, `roles/summarizer/`, `roles/citation_reviewer/` | Create new role directories with `prompt.md` files. See plan Section 9 for role descriptions |
| 9 | `roles/__init__.py` | Register new roles and actors |
| 10 | `roles/pm/prompt.md` | Add citation requirements section |
| 11 | `workflows/_common/_helpers.py` | Add all helper functions: `broad_interview()`, `decompose_and_gate()`, `per_subfeature_loop()`, `integration_review()`, `compile_artifacts()`, `interview_gate_review()`, `targeted_revision()`, `_run_step()`, `extract_decisions()`, `generate_summary()`, `verify_compilation_integrity()`, `_build_subfeature_context()` |
| 12 | `workflows/planning/phases/pm.py` | Full restructure per plan Section 3 |
| 13 | `iriai-feedback/src/server/serve.js` | Add subfeature URL route. Update `scanArtifacts()` to scan `subfeatures/*/` and `broad/` |
| 14 | `iriai-feedback/src/server/portal-html.js` | Update feature page for subfeature grouping |

**Test**: Run the restructured PMPhase on a 2-3 subfeature feature via Slack before proceeding.

### Phase C: Remaining phases (after PM is proven)

| Step | File | What to do |
|---|---|---|
| 15 | `models/outputs.py` | Add `GlobalImplementationStrategy`, add `subfeature_id` to `ImplementationTask` |
| 16-17 | `roles/lead_designer/`, `roles/lead_architect/`, `roles/lead_task_planner/` | Create roles |
| 18 | `roles/designer/prompt.md`, `roles/architect/prompt.md`, `roles/task_planner/prompt.md` | Add citation requirements |
| 19 | `workflows/planning/phases/design.py` | Full restructure per plan Section 6 |
| 20 | `workflows/planning/phases/architecture.py` | Full restructure per plan Section 7 |
| 21 | `workflows/planning/phases/task_planning.py` | Full restructure per plan Section 7b |

### Phase D: Integration and polish

| Step | File | What to do |
|---|---|---|
| 22 | `workflows/planning/phases/plan_review.py` | Replace `gate_and_revise()` with interview-based gates. Add citation reviewer |
| 23 | `interfaces/slack/interaction.py` | Add turn persistence hook in `_resolve_pending()` |
| 24 | `interfaces/slack/streamer.py` | Show actor name when `sf-` prefix present |

## Critical Files to Read Before Implementing

| File | Why |
|---|---|
| `workflows/planning/phases/pm.py` | Current PM phase — you're restructuring this |
| `workflows/planning/phases/design.py` | Current Design phase — understand mockup handling |
| `workflows/planning/phases/architecture.py` | Current Architecture phase — understand dual gate pattern |
| `workflows/_common/_helpers.py` | `gate_and_revise()` and `get_existing_artifact()` patterns |
| `workflows/_common/_tasks.py` | `HostedInterview` — extends `Interview` with artifact hosting |
| `runtimes/claude.py` | Session management, turn tracking, cycling |
| `services/artifacts.py` | `ArtifactMirror` and `_KEY_MAP` |
| `services/hosting.py` | `DocHostingService` — push, update, try_collect, clear_feedback |
| `models/outputs.py` | All Pydantic output models (PRD, DesignDecisions, TechnicalPlan, etc.) |
| `models/state.py` | `BuildState` — threaded through phases |
| `roles/__init__.py` | Actor construction patterns |
| `storage/features.py` | `log_event()` — reuse for decisions and progress |
| `iriai-feedback/src/server/serve.js` | URL routing and `scanArtifacts()` |
| `iriai-compose/iriai_compose/runner.py` | Framework: `resolve()`, `parallel()`, context injection, session keys |

## Anti-Patterns to Avoid

- Do NOT create a `SubfeatureContextProvider` class — build context inline in helpers
- Do NOT create new DB tables for decisions or progress — use the `events` table
- Do NOT create `BroadDesignSystem`, `BroadArchitecture`, `DecompositionAlignment` models — reuse existing types
- Do NOT edit compiled artifacts directly — all changes flow through subfeature revisions
- Do NOT use `runner.parallel()` for subfeature agents — they must run sequentially
- Do NOT use flat filenames (`prd--slug.md`) — use nested directories (`subfeatures/slug/prd.md`)
- Do NOT break existing `gate_and_revise()` — it's still used for per-subfeature gates. The interview-based gate review is a new function for compiled artifacts only
- Do NOT modify iriai-compose framework code — all changes are in iriai-build-v2 (application layer)

## How to Verify

After each phase, run:
```bash
python -m pytest tests/ -v
```

For Phase B specifically, test end-to-end via Slack:
```bash
iriai-build plan --name "Test feature" --workspace /path/to/project
```
Verify: broad interview works → decomposition gate shows → per-SF interviews produce artifacts in `subfeatures/{slug}/` → integration review catches inconsistencies → compiled PRD includes all subfeature content → interview gate review allows revisions routed to correct subfeature.

## Recursive Handoff Protocol

**Before your context runs low or when you complete a phase boundary (A→B→C→D), you MUST create a handoff document for the next agent.** Do not wait until you are out of context — create the handoff proactively when you sense you're approaching limits or finishing a natural phase boundary.

### When to create a handoff

- You have completed one of the implementation phases (A, B, C, or D)
- You estimate you have less than 20% context remaining
- You are about to start a phase that will require reading many new files

### How to create a handoff

Write a new file at `.iriai/handoff-subfeature-decomposition-{phase}.md` (e.g., `handoff-subfeature-decomposition-B.md`) containing:

1. **What was completed**: List every file you created or modified, with a one-line summary of what changed. Include commit hashes if you committed.

2. **What is NOT yet done**: List remaining steps from the implementation sequence, starting from where you stopped. Be specific — don't just say "Phase C". List the exact step numbers and files.

3. **Current state of the codebase**: What works, what's partially done, what's broken. Include any test results.

4. **Decisions you made during implementation**: Anything that deviated from or refined the plan. Include the reasoning.

5. **Gotchas and warnings**: Things the next agent needs to know that aren't in the plan — edge cases you discovered, imports that were tricky, patterns that didn't work as expected.

6. **Files the next agent should read first**: The specific files they need to understand before continuing. Prioritize files you changed (so they see your patterns) over files from the original plan.

7. **How to start**: The exact first step the next agent should take. Be specific: "Read `_helpers.py` to understand the `per_subfeature_loop()` implementation, then open `design.py` and restructure it following the same pattern."

8. **This section**: Copy this entire "Recursive Handoff Protocol" section verbatim into the new handoff so the chain continues.

### Handoff prompt for the user

After writing the handoff file, tell the user:

> Handoff ready at `.iriai/handoff-subfeature-decomposition-{phase}.md`. Start the next conversation with:
>
> ```
> Read the handoff at .iriai/handoff-subfeature-decomposition-{phase}.md and the full plan at ~/.claude/plans/sequential-swinging-ocean.md, then continue implementation from where the previous agent left off.
> ```

### Reference chain

Each handoff should reference:
- The **full plan**: `~/.claude/plans/sequential-swinging-ocean.md` (always — this is the source of truth)
- The **original handoff**: `.iriai/handoff-subfeature-decomposition.md` (for key decisions and anti-patterns)
- The **previous handoff**: `.iriai/handoff-subfeature-decomposition-{prev-phase}.md` (for what was done)
- **CLAUDE.md** and **memory files** (always)
