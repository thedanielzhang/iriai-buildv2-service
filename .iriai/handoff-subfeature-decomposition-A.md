# Handoff: Phase A Complete — Subfeature Decomposition Infrastructure

## What Was Completed

Phase A (Infrastructure, steps 1-4) is fully implemented. No commits made — changes are unstaged.

| Step | File | What changed |
|---|---|---|
| 1 | `src/iriai_build_v2/models/outputs.py` | Added `Citation` model. Added `citations: list[Citation]` to `Requirement`, `AcceptanceCriterion`, `JourneyStep`, `ComponentDef`, `ImplementationStep`. Added `SubfeatureEdge`, `Subfeature`, `SubfeatureDecomposition`, `EdgeCheck`, `IntegrationReview`, `RevisionRequest`, `RevisionPlan`, `ReviewOutcome` models. |
| 2 | `src/iriai_build_v2/models/state.py` | Added `decomposition: str = ""` to `BuildState` |
| 3 | `src/iriai_build_v2/services/artifacts.py` | Replaced `_key_to_filename()` with `_key_to_path()` — supports nested subdirectory layout for broad, subfeature, integration-review, and summary artifacts. Updated `write_artifact()` to `mkdir(parents=True)` for subdirectories. |
| 3b | `src/iriai_build_v2/services/hosting.py` | Updated `try_collect()` and `clear_feedback()` to use `_key_to_path()` for nested `.feedback/` paths. Updated `_to_display_content()` to resolve namespaced keys (e.g. `prd:broad`) to base keys for model matching. Added `_key_to_path` import. |
| 4 | `src/iriai_build_v2/runtimes/claude.py` | Added turn persistence: appends `{"role": "assistant", "text", "turn"}` to `session.metadata["turns"]` after each invoke. Added `get_active_session_key(feature_id)` method. |

## What Is NOT Yet Done

### Phase B: PMPhase + iriai-feedback serve
| Step | File | What to do |
|---|---|---|
| 5-8 | `roles/lead_pm/`, `roles/compiler/`, `roles/summarizer/`, `roles/citation_reviewer/` | Create new role directories with `prompt.md` files |
| 9 | `roles/__init__.py` | Register new roles and actors |
| 10 | `roles/pm/prompt.md` | Add citation requirements section |
| 11 | `workflows/_common/_helpers.py` | Add all helper functions: `broad_interview()`, `decompose_and_gate()`, `per_subfeature_loop()`, `integration_review()`, `compile_artifacts()`, `interview_gate_review()`, `targeted_revision()`, `_run_step()`, `extract_decisions()`, `generate_summary()`, `verify_compilation_integrity()`, `_build_subfeature_context()` |
| 12 | `workflows/planning/phases/pm.py` | Full restructure per plan Section 3 |
| 13 | `iriai-feedback/src/server/serve.js` | Add subfeature URL route. Update `scanArtifacts()` |
| 14 | `iriai-feedback/src/server/portal-html.js` | Update feature page for subfeature grouping |

### Phase C: Remaining phases (steps 15-21)
### Phase D: Integration and polish (steps 22-24)

## Current State of the Codebase

- **All 120 tests pass** (`python -m pytest tests/ -v`)
- All changes are backward-compatible — existing artifact keys (`prd`, `design`, `plan`, etc.) still map to the same root-level files via `_KEY_MAP`
- No regressions to existing workflows

### Verification Results

All steps were verified programmatically after implementation:

1. **Models**: All 9 new models instantiate correctly with proper defaults. `citations: list[Citation]` field present on all 6 target models (`Requirement`, `AcceptanceCriterion`, `JourneyStep`, `ComponentDef`, `ImplementationStep`, `SubfeatureEdge`). Backward compat confirmed — `PRD`, `DesignDecisions`, `TechnicalPlan`, `Envelope` all still work.

2. **`_key_to_path` mappings**: All 7 documented examples verified. Standard keys backward-compatible. Broad, subfeature, integration-review, summary, and mockup namespaced keys all resolve correctly.

3. **`write_artifact` nested dirs**: Confirmed via tempdir test — writes to `subfeatures/canvas/prd.md`, `broad/prd.md`, `reviews/pm.md` with automatic parent directory creation.

4. **Hosting namespaced keys**: `try_collect("prd:canvas")` reads from `subfeatures/canvas/.feedback/prd/annotations/`. `clear_feedback("prd:canvas")` clears the correct nested path. `_to_display_content` resolves `prd:broad` and `prd:canvas` to the PRD model for markdown rendering.

5. **Turn persistence**: `get_active_session_key()` returns correct session keys. Turn append code confirmed in `invoke()` source. Session load-then-update pattern verified (doesn't create duplicate sessions).

## Decisions Made During Implementation

1. **`RevisionPlan.new_decisions` uses `list[str]` instead of `list[Decision]`**: The plan references a `Decision` model tied to a new DB table, but the handoff explicitly says "No new DB tables — use existing `events` table + `log_event()`." Since `Decision` was never formally defined as a standalone Pydantic model and the handoff anti-patterns say no `PostgresDecisionStore`, I used `list[str]` to store decision descriptions. This matches the pattern in `SystemDesign.decisions: list[str]`.

2. **`rehost_existing()` left unchanged**: It only looks at root-level compiled artifacts, which is correct behavior. It uses a local import of `_KEY_MAP` which still exists.

3. **Turn persistence saves merged into session save**: Rather than a separate save call, the assistant turn is persisted in the same `session_store.save()` call that saves the session ID. This avoids a double save and ensures atomicity.

## Gotchas and Warnings

1. **`_key_to_path` is now imported at module level in `hosting.py`**: `from .artifacts import _key_to_path`. This is a new cross-module dependency. The old `from .artifacts import _KEY_MAP` import in `rehost_existing()` is still there (local import).

2. **User turn persistence is NOT yet implemented**: Step 4 only adds assistant turn persistence in `ClaudeAgentRuntime.invoke()`. The user turn persistence (in `SlackInteractionRuntime._resolve_pending()`) is Phase D, step 23.

3. **`_BROAD_MAP` is defined inside `_key_to_path`**: It's a local dict created on each call. If performance matters, it could be moved to module level, but the overhead is negligible.

4. **Summary key pattern**: `prd-summary:slug` maps to `subfeatures/slug/prd-summary.md`. The `_key_to_path` function handles this by stripping `-summary` from the prefix to find the base key in `_KEY_MAP`, then re-adding `-summary` to the filename.

## Files the Next Agent Should Read First

1. **`src/iriai_build_v2/models/outputs.py`** — see the new models and where they sit relative to existing ones
2. **`src/iriai_build_v2/services/artifacts.py`** — understand `_key_to_path()` mapping
3. **`src/iriai_build_v2/workflows/_common/_helpers.py`** — existing `gate_and_revise()` pattern to understand before adding new helpers
4. **`src/iriai_build_v2/workflows/_common/_tasks.py`** — `HostedInterview` implementation
5. **`src/iriai_build_v2/workflows/planning/phases/pm.py`** — current PM phase being restructured
6. **`src/iriai_build_v2/roles/__init__.py`** — actor construction patterns for new roles

## How to Start

Read `roles/__init__.py` to understand how roles and actors are registered. Then create the new role directories (`roles/lead_pm/prompt.md`, `roles/compiler/prompt.md`, `roles/summarizer/prompt.md`, `roles/citation_reviewer/prompt.md`) following the plan's Section 9 for role descriptions. After roles are created, register them in `roles/__init__.py`, then proceed to the helper functions in `_helpers.py`.

## Reference Chain

- **Full plan**: `~/.claude/plans/sequential-swinging-ocean.md`
- **Original handoff**: `.iriai/handoff-subfeature-decomposition.md`
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
