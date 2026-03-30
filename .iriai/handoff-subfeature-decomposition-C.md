# Handoff: Phase C Complete — Design, Architecture, TaskPlanning Restructured

## What Was Completed

Phase C (steps 15-21) is fully implemented. No commits made — all changes unstaged.

| Step | File | What changed |
|---|---|---|
| 15 | `models/outputs.py` | Added `GlobalImplementationStrategy` model (execution order, shared infra, cross-SF deps, parallel opps, constraints, citations). Added `subfeature_id: str = ""` to `ImplementationTask`. |
| 16 | `roles/lead_designer/__init__.py` + `prompt.md` | New Lead Designer role: Opus 1M, 3 modes (broad design, integration review, gate review), citation requirements |
| 16 | `roles/lead_architect/__init__.py` + `prompt.md` | New Lead Architect role: Opus 1M with context7 MCP, 3 modes (broad arch, integration review, gate review), citation requirements |
| 16 | `roles/lead_task_planner/__init__.py` + `prompt.md` | New Lead Task Planner role: Opus, 3 modes (global strategy, DAG integration review, gate review), citation requirements |
| 17 | `roles/__init__.py` | Imported 3 new lead roles. Created 13 new actors: lead_designer/reviewer/gate_reviewer, design_compiler, lead_architect/reviewer/gate_reviewer, plan_arch_compiler, sysdesign_compiler, lead_task_planner/reviewer/gate_reviewer, dag_compiler |
| 18 | `roles/designer/prompt.md` | Added Citation Requirements section |
| 18 | `roles/architect/prompt.md` | Added Citation Requirements section |
| 18 | `roles/planning_lead/prompt.md` | Added Citation Requirements section |
| 19 | `workflows/planning/phases/design.py` | Full 6-step restructure: resume → broad design (DesignDecisions, NOT BroadDesignSystem) → per-SF design loop → integration review → compile → interview gate review. Per-subfeature mockup hosting via `_host_sf_mockup()`. |
| 20 | `workflows/planning/phases/architecture.py` | Full restructure with dual artifacts: resume → broad arch (TechnicalPlan, NOT BroadArchitecture) → manual per-SF arch loop (ArchitectureOutput = plan+sysdesign) with dual gates → integration review → dual compilation (plan + system design) → dual interview gate review. Per-SF system design HTML rendering. |
| 21 | `workflows/planning/phases/task_planning.py` | Full 6-step restructure: resume → global strategy (GlobalImplementationStrategy) → per-SF task planning in execution order → DAG integration review → DAG compilation → interview gate review. Respects `subfeature_execution_order`. |

## What Is NOT Yet Done

### Phase D: Integration and polish (steps 22-24)
| Step | File | What to do |
|---|---|---|
| 22 | `workflows/planning/phases/plan_review.py` | Replace `gate_and_revise()` with interview-based gates. Add `plan_citation_reviewer` as third auto-reviewer. |
| 23 | `interfaces/slack/interaction.py` | Add user turn persistence in `_resolve_pending()` |
| 24 | `interfaces/slack/streamer.py` | Show actor name when `sf-` prefix present |

## Current State of the Codebase

- **All 120 tests pass** (`python -m pytest tests/ -v`)
- All 34 actor imports verified — no circular imports
- Backward compatibility aliases intact (`task_planner`, `qa_engineer`, `reviewer`)
- No banned model types (`BroadDesignSystem`, `BroadArchitecture`, `DecompositionAlignment`) in any imports — only in warning comments

### Verification Results

1. **`GlobalImplementationStrategy`**: Instantiates correctly with all 7 fields (execution_order, shared_infra, cross_SF_deps, parallel_opps, constraints, citations, complete).

2. **`ImplementationTask.subfeature_id`**: Defaults to `""`, accepts string values. Backward compatible.

3. **3 lead roles**: All load with correct `name`, `model`, and `tools`. Lead designer uses Opus 1M. Lead architect uses Opus 1M with context7 MCP. Lead task planner uses Opus.

4. **13 new actors**: All registered correctly. Lead actors are `InterviewActor`, compilers are `AgentActor`. Verified all types.

5. **Citation requirements**: Present in `designer`, `architect`, and `planning_lead` prompts (verified via `role.prompt` inspection).

6. **DesignPhase**: All 6 steps present. Uses `DesignDecisions` (NOT `BroadDesignSystem`). Has per-subfeature mockup hosting via `_host_sf_mockup()`. Loads decomposition from state or artifact store.

7. **ArchitecturePhase**: Has manual per-SF loop producing `ArchitectureOutput` (plan + system_design). Dual per-SF gates (plan then system design). Dual compilation. Dual interview gate review. Per-SF system design HTML rendering.

8. **TaskPlanningPhase**: Uses `GlobalImplementationStrategy` for execution ordering. Reorders decomposition subfeatures per `subfeature_execution_order`. Uses `per_subfeature_loop` with `planning_lead_role` and `ImplementationDAG`.

9. **No anti-pattern violations**: No banned types in any import lines. `gate_and_revise` preserved (used by per-SF gates). No `runner.parallel()` for subfeature agents. No new DB tables.

## Decisions Made During Implementation

1. **ArchitecturePhase uses a manual per-SF loop instead of `per_subfeature_loop`**: The helper function expects a single `output_type`, but architecture produces `ArchitectureOutput` (containing both `TechnicalPlan` and `SystemDesign`). The manual loop handles splitting the dual output, hosting per-SF system design HTML, and running dual gates.

2. **TaskPlanningPhase reorders decomposition per strategy**: If `GlobalImplementationStrategy.subfeature_execution_order` is provided, the decomposition subfeatures are reordered accordingly before the per-SF loop. Subfeatures not listed in the order are appended at the end.

3. **`_load_decomposition` duplicated in each phase**: Rather than a shared function, each phase has its own static `_load_decomposition`. This avoids adding another import path and keeps each phase self-contained. The implementation is identical (3 lines).

4. **`design_compiler` vs `pm_compiler`**: Separate compiler actors for each phase even though they share the same role. This gives unique session keys per phase.

## Gotchas and Warnings

1. **ArchitecturePhase's `_per_subfeature_arch_loop` doesn't use `per_subfeature_loop`**: If the helper is ever changed (e.g., context injection logic), the architecture loop must be updated manually.

2. **`sysdesign_compiler` handles system design compilation**: The plan specifies compiling system designs separately from technical plans. The architecture phase calls `_compile_system_design` which uses `sysdesign_compiler` with `SystemDesign` output type.

3. **System design gate review artifact_prefix is `"system-design"`**: The `interview_gate_review` for system design uses `artifact_prefix="system-design"` and `broad_key="plan:broad"` (since there's no separate `system-design:broad`).

4. **`_host_sf_mockup` globs for `mockup*{sf_slug}*.html`**: The designer may name mockups unpredictably, so we glob with the slug as a substring match.

## Files the Next Agent Should Read First

1. **`workflows/planning/phases/plan_review.py`** — current plan review phase to be restructured
2. **`interfaces/slack/interaction.py`** — where to add user turn persistence
3. **`interfaces/slack/streamer.py`** — where to add sf- actor name display
4. **`workflows/_common/_helpers.py`** — the helper functions (especially `interview_gate_review`)
5. **`runtimes/claude.py`** — turn persistence pattern (assistant turns already implemented in Phase A)

## How to Start

Read `plan_review.py` to understand the current 4-gate pattern. Replace each `gate_and_revise()` call with an `interview_gate_review()` call, using the appropriate lead actor for each artifact type (lead_pm for PRD, lead_designer for design, lead_architect for plan and system design). Add the citation reviewer as a third auto-reviewer. Then move to `interaction.py` for user turn persistence and `streamer.py` for sf- display.

## Reference Chain

- **Full plan**: `~/.claude/plans/sequential-swinging-ocean.md`
- **Original handoff**: `.iriai/handoff-subfeature-decomposition.md`
- **Phase A handoff**: `.iriai/handoff-subfeature-decomposition-A.md`
- **Phase B handoff**: `.iriai/handoff-subfeature-decomposition-B.md`
- **CLAUDE.md**: `/Users/danielzhang/src/iriai/iriai-build-v2/CLAUDE.md`
- **Memory files**: `/Users/danielzhang/.claude/projects/-Users-danielzhang-src-iriai-iriai-build-v2/memory/MEMORY.md`

## Recursive Handoff Protocol

**Before your context runs low or when you complete a phase boundary (A→B→C→D), you MUST create a handoff document for the next agent.** Do not wait until you are out of context — create the handoff proactively when you sense you're approaching limits or finishing a natural phase boundary.

### When to create a handoff

- You have completed one of the implementation phases (A, B, C, or D)
- You estimate you have less than 20% context remaining
- You are about to start a phase that will require reading many new files

### How to create a handoff

**Before writing the handoff**, you MUST run the verification protocol (see below). The handoff document must include verification results proving correctness. Do not assume your implementation is correct — prove it.

Write a new file at `.iriai/handoff-subfeature-decomposition-{phase}.md` (e.g., `handoff-subfeature-decomposition-C.md`) containing:

1. **What was completed**: List every file you created or modified, with a one-line summary of what changed. Include commit hashes if you committed.

2. **What is NOT yet done**: List remaining steps from the implementation sequence, starting from where you stopped. Be specific — don't just say "Phase D". List the exact step numbers and files.

3. **Current state of the codebase**: What works, what's partially done, what's broken. Include test results AND verification results.

4. **Decisions you made during implementation**: Anything that deviated from or refined the plan. Include the reasoning.

5. **Gotchas and warnings**: Things the next agent needs to know that aren't in the plan — edge cases you discovered, imports that were tricky, patterns that didn't work as expected.

6. **Files the next agent should read first**: The specific files they need to understand before continuing. Prioritize files you changed (so they see your patterns) over files from the original plan.

7. **How to start**: The exact first step the next agent should take. Be specific.

8. **This section**: Copy this entire "Recursive Handoff Protocol" section (including the Verification Protocol) verbatim into the new handoff so the chain continues.

### Verification protocol

**This is mandatory before writing a handoff.** Do not assume correctness — be proven correct via the implementation.

1. **Re-read your changed files**: Read back every file you modified. Don't rely on memory of what you wrote — look at the actual file contents. Check for typos, stale references, duplicate code, and inconsistencies.

2. **Write programmatic verification scripts**: For every behavioral change you made, write a Python script (via Bash tool) that imports the actual code and exercises it. Examples:
   - New models: instantiate them, verify fields, defaults, and types
   - Path mappings: call the function with every documented example and assert the output
   - File I/O changes: use `tempfile.TemporaryDirectory()` to create real files and verify the directory structure
   - Service changes: call the actual methods and verify behavior (use asyncio.run for async)
   - Don't just check that code exists in the source — execute it and check the results

3. **Verify backward compatibility**: Confirm that existing code paths still work. Import existing models, call existing functions with their original arguments, and verify the same results.

4. **Check for dangling references**: Grep for any old function/class names you replaced. Verify no import errors across the codebase.

5. **Run the full test suite**: `python -m pytest tests/ -v` — all tests must pass.

6. **Document what you verified**: Include a "Verification Results" section in the handoff with a numbered list of what was tested and confirmed. This tells the next agent what ground is solid and what isn't.

If verification reveals bugs, fix them before writing the handoff. The handoff must describe verified-correct code, not "I think this works."

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
