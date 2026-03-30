# Handoff: Phase B Complete — PMPhase Restructure + iriai-feedback Subfeature Support

## What Was Completed

Phase B (steps 5-14) is fully implemented. No commits made — all changes unstaged.

| Step | File | What changed |
|---|---|---|
| 5 | `roles/lead_pm/__init__.py` + `prompt.md` | New Lead PM role: Opus 1M, 4 modes (broad interview, decomposition, integration review, gate review), citation requirements |
| 6 | `roles/compiler/__init__.py` + `prompt.md` | New Compiler role: Opus, merges per-subfeature artifacts preserving all detail, re-numbers IDs globally |
| 7 | `roles/summarizer/__init__.py` + `prompt.md` | New Summarizer role: Haiku, generates Tier 3 summaries for context injection |
| 8 | `roles/citation_reviewer/__init__.py` + `prompt.md` | New Citation Reviewer role: Opus, verifies code/decision/research citations |
| 9 | `roles/__init__.py` | Imported 4 new roles. Created 7 actors: `lead_pm`, `lead_pm_decomposer`, `lead_pm_reviewer`, `lead_pm_gate_reviewer`, `pm_compiler`, `artifact_summarizer`, `citation_reviewer` |
| 10 | `roles/pm/prompt.md` | Added Citation Requirements section with 3 citation types and [UNJUSTIFIED] flagging |
| 11 | `workflows/_common/_helpers.py` | Added 9 functions: `broad_interview`, `decompose_and_gate`, `_build_subfeature_context`, `generate_summary`, `per_subfeature_loop`, `integration_review`, `compile_artifacts`, `interview_gate_review`, `targeted_revision`, `_get_user`. Updated `get_existing_artifact` to use `_key_to_path` instead of `_KEY_MAP` |
| 11b | `workflows/_common/__init__.py` | Re-exported all new helper functions |
| 12 | `workflows/planning/phases/pm.py` | Full 7-step restructure: resume check → broad interview → decompose_and_gate → per_subfeature_loop → integration_review → compile_artifacts → interview_gate_review |
| 13 | `iriai-feedback/src/server/serve.js` (external repo) | Added `keyToPath()` + `keyToLabel()` mirroring Python's `_key_to_path`. Updated `feedbackDir` for nested paths. Updated `scanArtifacts` to scan `subfeatures/*/`, `broad/`, `reviews/`. Added subfeature URL route (`/features/{id}/subfeatures/{slug}/{key}`). Updated `serveDocument` to handle namespaced keys |
| 14 | `iriai-feedback/src/server/portal-html.js` (external repo) | Updated `renderFeaturePage` to group artifacts by type (compiled, broad, subfeature, review). Added `artifactUrl()` for correct subfeature URL routing. Added `.section-label` CSS |

## What Is NOT Yet Done

### Phase C: Remaining phases (steps 15-21)
| Step | File | What to do |
|---|---|---|
| 15 | `models/outputs.py` | Add `GlobalImplementationStrategy`, add `subfeature_id` to `ImplementationTask` |
| 16-17 | `roles/lead_designer/`, `roles/lead_architect/`, `roles/lead_task_planner/` | Create role directories |
| 18 | `roles/designer/prompt.md`, `roles/architect/prompt.md`, `roles/task_planner/prompt.md` | Add citation requirements |
| 19 | `workflows/planning/phases/design.py` | Full restructure per plan Section 6 |
| 20 | `workflows/planning/phases/architecture.py` | Full restructure per plan Section 7 |
| 21 | `workflows/planning/phases/task_planning.py` | Full restructure per plan Section 7b |

### Phase D: Integration and polish (steps 22-24)
| Step | File | What to do |
|---|---|---|
| 22 | `workflows/planning/phases/plan_review.py` | Replace `gate_and_revise()` with interview-based gates |
| 23 | `interfaces/slack/interaction.py` | Add user turn persistence in `_resolve_pending()` |
| 24 | `interfaces/slack/streamer.py` | Show actor name when `sf-` prefix present |

## Current State of the Codebase

- **All 120 tests pass** (`python -m pytest tests/ -v`)
- All imports verified — no circular imports or missing modules
- `serve.js` and `portal-html.js` load without errors (verified via Node.js)
- Phase B is fully implemented but **not yet end-to-end tested via Slack** (per the handoff plan, this should be tested before Phase C)

### Verification Results

1. **Roles**: All 4 new roles load correctly with expected `name`, `model`, and `tools`. Lead PM uses Opus 1M, compiler uses Opus, summarizer uses Haiku, citation reviewer uses Opus.

2. **Actors**: All 7 new actors registered. Lead actors are `InterviewActor` (correct for multi-turn). Compiler/summarizer are `AgentActor` (one-shot).

3. **PM prompt**: Citation Requirements section present with `[code:]`, `[decision:]`, `[research:]` types and `[UNJUSTIFIED]` flagging.

4. **Helper functions**: All 10 functions importable from `_common`. `_build_subfeature_context` verified: connected subfeatures get full text (Tier 2), unconnected get summary (Tier 3). `get_existing_artifact` correctly uses `_key_to_path`.

5. **PMPhase**: All 7 steps present in `execute()`. Uses correct actors. State management sets `decomposition` and `prd`. `_make_sf_prompt` generates correct per-subfeature prompts.

6. **serve.js**: Loads without errors. `keyToPath` and `keyToLabel` mirror Python. `feedbackDir` uses `keyToPath` for nested paths. `scanArtifacts` scans compiled, broad, subfeature, and review directories. Subfeature URL route added.

7. **portal-html.js**: `renderFeaturePage` groups artifacts into Compiled, Broad, Subfeature (by slug), and Review sections. Subfeature URLs use `/subfeatures/{slug}/{key}` route.

8. **Import chain**: Full import of all new modules succeeds without circular imports.

## Decisions Made During Implementation

1. **`decompose_and_gate` uses `HostedInterview` (not plain `Ask`)**: The plan says `Ask` for decomposition, but the handoff's original decision table says "Interview everywhere." I used `HostedInterview` so the lead can ask the user clarifying questions about boundaries before producing the decomposition. A simple `Gate` follows for approval.

2. **`_get_user()` lazy import helper**: To avoid circular imports between `_helpers.py` and `roles/__init__.py`, I added a `_get_user()` function that lazily imports the `user` actor.

3. **`get_existing_artifact` updated to use `_key_to_path`**: The old code used `_KEY_MAP.get(artifact_key)` which only matched root-level keys. Now it uses `_key_to_path` so it can find subfeature artifacts on the filesystem too (e.g., `prd:canvas` → `subfeatures/canvas/prd.md`).

4. **`feedbackDir` in serve.js mirrors Python pattern**: Uses `keyToPath` to resolve the artifact's parent directory, then puts `.feedback/{stem}/` inside it. This matches how Python's `try_collect` and `clear_feedback` work.

5. **Integration review → targeted revision bridge**: When the integration review returns `needs_revision`, the PMPhase constructs a `RevisionPlan` from `review.revision_instructions` and calls `targeted_revision`. This bridges the `IntegrationReview` model (which has `revision_instructions: dict[str, str]`) with the `RevisionPlan` model (which has `RevisionRequest` objects).

## Gotchas and Warnings

1. **iriai-feedback is a separate repo**: Changes to `serve.js` and `portal-html.js` are at `/Users/danielzhang/src/iriai/iriai-feedback/`, NOT inside iriai-build-v2. These need to be committed separately.

2. **`per_subfeature_loop` creates actors dynamically**: Each subfeature gets `InterviewActor(name=f"{artifact_prefix}-sf-{sf.slug}", ...)`. These actors share the `pm_role` (or `designer_role`, etc.) but have unique names for session isolation. This is the pattern Phase C must follow.

3. **The helper functions are generic**: `broad_interview`, `per_subfeature_loop`, `compile_artifacts`, `interview_gate_review`, `targeted_revision` all accept `artifact_prefix`, `output_type`, `base_role` etc. Phase C should reuse them directly — only the role, output_type, and `make_prompt` differ.

4. **`_build_subfeature_context` is a standalone function, not a class**: Per the anti-pattern in the original handoff. It's called in `per_subfeature_loop` to build context for each subfeature agent.

5. **Node.js `dirname` import**: `serve.js` already imported `dirname` from `node:path`. The updated `feedbackDir` uses it for path manipulation.

## Files the Next Agent Should Read First

1. **`workflows/_common/_helpers.py`** — all new helper functions, the core pattern for Phase C
2. **`workflows/planning/phases/pm.py`** — the restructured PMPhase, template for design.py/architecture.py
3. **`roles/__init__.py`** — see how actors are registered, pattern for lead_designer/lead_architect
4. **`workflows/planning/phases/design.py`** — current design phase to be restructured
5. **`workflows/planning/phases/architecture.py`** — current architecture phase to be restructured
6. **Plan Section 6** (~line 540-700) — design phase restructure spec
7. **Plan Section 7** (~line 700-900) — architecture phase restructure spec

## How to Start

Read `_helpers.py` to understand the helper functions, then read the current `design.py`. The restructure follows the exact same pattern as `pm.py`: `broad_interview` → `decompose_and_gate` (reuse existing decomposition) → `per_subfeature_loop` → `integration_review` → `compile_artifacts` → `interview_gate_review`. The only differences are: different roles (lead_designer, designer_role), different output_type (DesignDecisions), different artifact_prefix ("design"), and the addition of mockup handling per subfeature.

## Reference Chain

- **Full plan**: `~/.claude/plans/sequential-swinging-ocean.md`
- **Original handoff**: `.iriai/handoff-subfeature-decomposition.md`
- **Phase A handoff**: `.iriai/handoff-subfeature-decomposition-A.md`
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

Write a new file at `.iriai/handoff-subfeature-decomposition-{phase}.md` (e.g., `handoff-subfeature-decomposition-B.md`) containing:

1. **What was completed**: List every file you created or modified, with a one-line summary of what changed. Include commit hashes if you committed.

2. **What is NOT yet done**: List remaining steps from the implementation sequence, starting from where you stopped. Be specific — don't just say "Phase C". List the exact step numbers and files.

3. **Current state of the codebase**: What works, what's partially done, what's broken. Include test results AND verification results.

4. **Decisions you made during implementation**: Anything that deviated from or refined the plan. Include the reasoning.

5. **Gotchas and warnings**: Things the next agent needs to know that aren't in the plan — edge cases you discovered, imports that were tricky, patterns that didn't work as expected.

6. **Files the next agent should read first**: The specific files they need to understand before continuing. Prioritize files you changed (so they see your patterns) over files from the original plan.

7. **How to start**: The exact first step the next agent should take. Be specific: "Read `_helpers.py` to understand the `per_subfeature_loop()` implementation, then open `design.py` and restructure it following the same pattern."

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
