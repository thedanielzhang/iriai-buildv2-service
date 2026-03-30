<!-- SF: testing-framework -->
### SF-3: Testing Framework

Revised the testing-framework artifact at `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/design-decisions.md` to align with D-GR-23. The update removes stale `invoke(..., node_id=...)` assumptions, makes `ContextVar`-based node propagation the canonical mock-routing contract, and standardizes hierarchical context merge order as `workflow -> phase -> actor -> node`. No code tests were run because this was a document-only revision.

<!-- SF: testing-framework -->
### J-2 — SF-3: Testing Framework

**Step Annotations:**
- Execution-path tests keep using `MockAgentRuntime.when_node(...)`, but node-aware resolution now comes from the runner-owned current-node `ContextVar`, not an added `node_id` invoke parameter.
- `respond_with(...)` callbacks receive prompt context assembled in the canonical additive order `workflow -> phase -> actor -> node`, matching dag-loader-runner and workflow-migration.
- Resume tests continue to use `RuntimeConfig(history=...)`; this revision changes only the runtime context contract, not the run/resume surface.

**Error Path UX:** If node-aware matching fails, diagnostics report the node ID read from runtime context plus the configured matcher set; developers do not need to debug a missing `node_id` argument path anymore.

**Empty State UX:** For tests without explicit node matchers, mock resolution falls through to role-based rules and finally `default_response()`; if none exist, `MockConfigurationError` explains the missing runtime-context match.

**NOT Criteria:**
- `AgentRuntime.invoke()` must NOT gain a breaking `node_id` keyword parameter.
- Testing callbacks must NOT assume any merge order other than `workflow -> phase -> actor -> node`.
- SF-3 must NOT define its own competing ContextVar store for current-node lookup.

<!-- SF: testing-framework -->
### CMP-7: MockAgentRuntime
<!-- SF: testing-framework — Original ID: CMP-1 -->

- **Status:** extending
- **Location:** `iriai_compose/runner.py`
- **Description:** Extends the existing `AgentRuntime` contract without changing its signature. `when_node()` matching reads the active node from the SF-2 runtime ContextVar and records merged prompt context for diagnostics.
- **Props/Variants:** `when_node | when_role | default_response ; respond | respond_sequence | respond_with | raise_error | then_crash | on_call | with_cost`
- **States:** node_id_match, role_prompt_match, role_match, default_match, no_match, sequence_exhausted, error_injected, crash_injected
- **Citations:**
  - [code] `/Users/danielzhang/src/iriai/iriai-compose/iriai_compose/runner.py:5` — "ContextVar" — The existing runtime already uses ContextVar-backed execution state, so SF-3 should reuse that pattern instead of widening the ABC.
  - [decision] `D-GR-23` — "Keep AgentRuntime.invoke() unchanged" — This is the authoritative cross-subfeature contract for node propagation.

### CMP-8: MockInteractionRuntime
<!-- SF: testing-framework — Original ID: CMP-2 -->

- **Status:** extending
- **Location:** `iriai_compose/runner.py`
- **Description:** Extends `InteractionRuntime` with node-aware matcher selection driven by the same runtime ContextVar and callback context diagnostics aligned to the canonical merge order.
- **Props/Variants:** `when_node ; approve_sequence | respond_with | script | raise_error | then_crash`
- **States:** approve_sequence, conditional_response, scripted_conversation, no_match, exhausted
- **Citations:**
  - [decision] `D-GR-23` — "workflow -> phase -> actor -> node" — Interaction callbacks must observe the same prompt-context assembly model as Ask-node mocks.

### CMP-9: MockPluginRuntime
<!-- SF: testing-framework — Original ID: CMP-3 -->

- **Status:** new
- **Location:** `iriai_compose/testing/mock_plugin.py`
- **Description:** Plugin-node test double that keeps fluent per-ref configuration while using current-node ContextVar state for per-node observability instead of a dedicated call parameter.
- **Props/Variants:** `when_ref ; respond | respond_sequence | raise_error | then_crash | with_cost`
- **States:** ref_match, error_injected, no_match
- **Citations:**
  - [decision] `D-GR-23` — "non-breaking runtime contract" — Plugin-side observability should align with the shared runtime-context model rather than introduce a parallel node-id propagation path.

<!-- SF: testing-framework -->
### CMP-7 (MockAgentRuntime) States

| State | Visual Description |
|-------|-------------------|
| node_id_match | Call record contains the current node ID read from runtime context, `matched_by` is `node_id`, and the node-scoped matcher wins over any role-scoped fallback. |
| no_match | `MockConfigurationError` lists the ContextVar-derived node ID, role, prompt excerpt, and configured matchers, making missing node-context routing obvious. |

### CMP-8 (MockInteractionRuntime) States

| State | Visual Description |
|-------|-------------------|
| conditional_response | `respond_with(prompt, context)` receives a merged context object where workflow values are available first, then phase, then actor, then node-specific additions. |

### CMP-9 (MockPluginRuntime) States

| State | Visual Description |
|-------|-------------------|
| ref_match | Plugin mock resolves by `plugin_ref` and records the current node identity from runtime context for downstream assertions and diagnostics. |

<!-- SF: testing-framework -->
### SF-3: Testing Framework
Not applicable. SF-3 remains a backend Python testing module with no visual UI.

<!-- SF: testing-framework -->
### SF-3: Testing Framework

Fluent mock configuration remains unchanged for test authors. The runtime contract underneath it is now: `run(workflow, config, *, inputs=None)` stays canonical, `AgentRuntime.invoke()` stays non-breaking, current-node identity is read from a runner-owned ContextVar, and all dynamic prompt context exposed to callbacks is merged in `workflow -> phase -> actor -> node` order.

<!-- SF: testing-framework -->
### SF-3: Testing Framework
No end-user UI exists. Equivalent DX requirements remain: assertion failures must clearly report expected vs actual values, runtime-context-derived node identity, and enough execution context to debug matcher selection without inspecting internal runner state.

<!-- SF: testing-framework -->
### SF-3: Testing Framework

1. Add `node_id` as a new keyword parameter to `AgentRuntime.invoke()` and thread it through every runtime call site.
2. Keep mixed or conflicting hierarchical merge orders across testing-framework, dag-loader-runner, and workflow-migration.
3. Create a testing-owned ContextVar layer instead of consuming the runner-owned runtime context.

<!-- SF: testing-framework -->
### SF-3: Testing Framework

The revised artifact now matches the resolved cross-subfeature runtime decision: preserve the existing ABC, propagate node identity through ContextVar, and use one hierarchical prompt-context assembly model everywhere. The main edited sections are the overview and decision log, the SF-2 -> SF-3 contract section, and the detailed specs for `MockAgentRuntime`, `MockInteractionRuntime`, and `MockPluginRuntime` in `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/design-decisions.md`.
