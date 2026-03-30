<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test

Revised the workflow-migration design artifact to match D-GR-23's runtime contract by removing any dependence on a breaking `invoke(..., node_id=...)` interface, explicitly documenting ContextVar-based node propagation, and standardizing effective context assembly to `workflow -> phase -> actor -> node` throughout the artifact.

<!-- SF: workflow-migration -->
### workflow-migration-planning — SF-4: Workflow Migration & Litmus Test

**Step Annotations:**
- Planning migration Tier 2 testing now uses ContextVar-aware `MockAgentRuntime`, `MockPluginRuntime`, and `MockInteractionRuntime` instead of a stale `MockRuntime` + `node_id` kwarg assumption.
- Planning migration Tier 3 consumer integration now explicitly keeps `ClaudeAgentRuntime.invoke()` unchanged while declarative execution propagates node identity internally via ContextVar.
- Expanded PM-phase node cards now describe their effective `reads` metadata in `workflow -> phase -> actor -> node` order so the canvas reflects the runtime contract.

**Error Path UX:** Contract drift is surfaced as test/runtime mismatch, not hidden in UI: migrations must fail clearly if a runner, mock, or consumer integration expects `invoke(..., node_id=...)` or assembles context in a different order.

**Empty State UX:** No change; SF-4 remains content-producing rather than empty-state-driven.

**NOT Criteria:**
- Migration tests must not require `AgentRuntime.invoke(..., node_id=...)`.
- Effective context display and prompt assembly must not use any merge order other than `workflow -> phase -> actor -> node`.

<!-- SF: workflow-migration -->
### workflow-migration-develop — SF-4: Workflow Migration & Litmus Test

**Step Annotations:**
- Develop-workflow validation now requires the same effective context merge order as planning so the duplicated planning phases remain behaviorally equivalent, not just visually similar.
- Mock execution references were updated to ContextVar-aware runtimes rather than a signature change on `AgentRuntime.invoke()`.
- Consumer integration language now preserves the existing runtime ABI while validating declarative execution through existing bridges.

**Error Path UX:** Any divergence between planning and develop context assembly should fail in consistency testing rather than being masked as a visual-only difference.

**Empty State UX:** No change.

**NOT Criteria:**
- Develop consistency checks must not accept a different effective context merge order from planning.
- Consumer execution must not patch runtime ABCs just to support declarative node routing.

<!-- SF: workflow-migration -->
### workflow-migration-bugfix — SF-4: Workflow Migration & Litmus Test

**Step Annotations:**
- Bugfix consumer-integration wording now explicitly preserves the non-breaking runtime boundary and ContextVar-based node propagation.
- The bugfix workflow's testing path stays aligned with the same hierarchical context contract used by planning and develop.
- Node-card `reads` metadata is interpreted as resolved effective context, not just local node keys.

**Error Path UX:** Broken runtime-contract assumptions should fail in test/integration stages before any downstream bugfix workflow is treated as valid migration output.

**Empty State UX:** No change.

**NOT Criteria:**
- Bugfix migration must not introduce a bespoke runtime signature for node-aware execution.
- Bugfix prompt assembly must not reorder context outside `workflow -> phase -> actor -> node`.

---

## Component Definitions

<!-- SF: workflow-migration -->
### CMP-10: Node Card Reads Metadata
<!-- SF: workflow-migration — Original ID: CMP-1 -->

- **Status:** extending
- **Location:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-migration/design-decisions.md`
- **Description:** On-face `reads` metadata for Ask/Branch/Plugin cards now represents the resolved effective context set, not just local node-level keys.
- **Props/Variants:** `resolved_context_keys in workflow -> phase -> actor -> node order`
- **States:** default, selected, error
- **Citations:**
  - [decision] `D-GR-23` — "Keep `AgentRuntime.invoke()` unchanged and propagate `node_id` via `ContextVar`... merge order is `workflow -> phase -> actor -> node`." — The visible `reads` line should mirror the actual runtime assembly model.
  - [code] `iriai-compose/iriai_compose/runner.py:5-50` — "`ContextVar` exists in runner and `AgentRuntime.invoke()` has no `node_id` kwarg." — The artifact should not imply a different runtime ABI than the codebase already exposes.

### CMP-11: Tier 2 Mock Runtime Contract
<!-- SF: workflow-migration — Original ID: CMP-2 -->

- **Status:** extending
- **Location:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-migration/design-decisions.md`
- **Description:** Tier 2 execution references now use `MockAgentRuntime`, `MockPluginRuntime`, and `MockInteractionRuntime` under a ContextVar-based routing model.
- **Props/Variants:** `agent | plugin | interaction mocks`
- **States:** configured, executing, mismatch
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:1976-1980` — "`AgentRuntime.invoke()` remains unchanged; current node identity is propagated via `ContextVar`." — SF-4 must depend on the authoritative SF-3 runtime boundary, not stale `node_id` kwarg assumptions.

### CMP-12: Consumer Integration Boundary
<!-- SF: workflow-migration — Original ID: CMP-3 -->

- **Status:** extending
- **Location:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-migration/design-decisions.md`
- **Description:** iriai-build-v2 integration criteria now explicitly preserve existing runtime method signatures while allowing declarative execution to propagate node identity internally.
- **Props/Variants:** `CLI | Slack | programmatic load path`
- **States:** loaded, executing, contract-aligned
- **Citations:**
  - [decision] `D-GR-23` — "Non-breaking runtime contract; ContextVar-based node propagation." — Consumer integration must validate declarative execution without forcing runtime ABC changes.
  - [code] `iriai-compose/iriai_compose/runner.py:41-50` — "`AgentRuntime.invoke()` accepts role, prompt, output_type, workspace, session_key only." — The consumer boundary must remain compatible with existing runtimes.

<!-- SF: workflow-migration -->
### CMP-10 (Node Card Reads Metadata) States

| State | Visual Description |
|-------|-------------------|
| default | Node cards show a `reads: ...` line whose keys are interpreted in effective runtime order `workflow -> phase -> actor -> node`, matching the declarative prompt assembly contract. |
| selected | Selected node card still opens inspector, but on-face `reads` metadata remains the resolved effective list rather than a node-local-only list. |

### CMP-11 (Tier 2 Mock Runtime Contract) States

| State | Visual Description |
|-------|-------------------|
| configured | Tier 2 testing language references `MockAgentRuntime`, `MockPluginRuntime`, and `MockInteractionRuntime` explicitly, with no `invoke(..., node_id=...)` contract required. |
| mismatch | Any stale assumption about a `node_id` invoke kwarg is treated as a contract error in tests/integration, not silently tolerated. |

### CMP-12 (Consumer Integration Boundary) States

| State | Visual Description |
|-------|-------------------|
| contract-aligned | Consumer-integration criteria explicitly state that existing runtimes keep their method signatures and receive node identity through declarative-runner ContextVar propagation instead. |

<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test
No responsive changes. This revision is backend-contract driven; the existing desktop-only mockup remains unchanged aside from the semantic interpretation of each node's `reads` line.

<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test

Tier 2 and Tier 3 migration verification now follow one runtime-interaction model: the runner owns current-node propagation via ContextVar, mocks/consumers observe that implicitly, and effective context ordering is always `workflow -> phase -> actor -> node`. The artifact explicitly rejects any alternate `invoke(..., node_id=...)` pattern.

<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test
Accessible reading order for node cards still includes the `reads` metadata, but that metadata is now defined as the resolved effective context order so screen-reader output matches runtime behavior.

<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test

1. Keep a breaking `AgentRuntime.invoke(..., node_id=...)` change across artifacts.
2. Allow each subfeature to assume its own context merge order.
3. Treat node-aware routing as prompt-text inference instead of ContextVar propagation.

<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test

The updated artifact now matches the resolved D-GR-23 contract instead of stale cross-subfeature assumptions. That keeps the runtime ABI non-breaking, keeps SF-4 aligned with the current runner code and SF-3 PRD, and makes the visible node metadata consistent with actual prompt-context assembly. The revised artifact was written to [/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-migration/design-decisions.md]. Key alignment points are the overview/journey updates, the node-card/read-state updates, and the SF-2/SF-3/consumer interface sections. No tests were run; this was a markdown artifact revision only.
