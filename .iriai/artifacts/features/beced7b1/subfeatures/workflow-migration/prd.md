<!-- SF: workflow-migration -->

#### REQ-71: functional (must)
Hierarchical additive context injection in migrated workflows must consume the canonical SF-2 runtime ABI: structural context resolves in `workflow -> phase -> actor -> node` order, deduplicated with first occurrence preserved, and current node identity is supplied via runner-managed `ContextVar` rather than a changed `AgentRuntime.invoke()` signature.

**Citations:**
- [decision] D-GR-23: "Keep `AgentRuntime.invoke()` unchanged and propagate `node_id` via `ContextVar`. Hierarchical context merge order is `workflow -> phase -> actor -> node`." -- This is the authoritative cross-subfeature runtime contract that SF-4 now adopts as a downstream consumer.
- [code] iriai-compose/iriai_compose/runner.py:5-50: "`ContextVar` is already used and `AgentRuntime.invoke()` has no `node_id` parameter." -- The existing runtime interface confirms the non-breaking pattern the PRD must depend on.


#### REQ-72: functional (must)
Tier 2 mock execution tests must consume SF-3's fluent `MockAgentRuntime`/`MockInteractionRuntime`/`MockPluginRuntime` surface only where it remains aligned to the SF-2 ABI owner contract, including `ContextVar`-based node matching and no `invoke(..., node_id=...)` dependency.

**Citations:**
- [decision] D-GR-23: "Node identity propagation uses `ContextVar`, not a breaking keyword argument." -- SF-4's mock-execution contract must align with the ratified runtime contract owned by SF-2 and surfaced by SF-3.
- [code] subfeatures/testing-framework/prd.md:562-617: "SF-3 defines fluent `when_node(...)` matching for mock runtimes backed by ContextVar." -- SF-4 test expectations must reference the producer artifact that SF-3 now exports, aligned to SF-2.


#### REQ-73: functional (must)
The iriai-build-v2 declarative bridge and migration smoke coverage must call declarative workflows through `run()` and `RuntimeConfig`, and must consume SF-2's published execution observability surface (`ExecutionResult`, `ExecutionHistory`, phase metrics) without inventing bridge-specific runtime ABI changes or requiring core checkpoint/resume in SF-2.

**Citations:**
- [decision] D-GR-23: "Avoid an unnecessary ABC break across runtimes." -- The bridge is a downstream consumer and must preserve the non-breaking runtime contract published by SF-2.
- [code] iriai-compose/iriai_compose/runner.py:41-50: "`invoke()` accepts `role`, `prompt`, `output_type`, `workspace`, and `session_key` only." -- The bridge must respect the current abstract interface exported by iriai-compose as published by SF-2.


#### REQ-74: non-functional (must)
SF-4 requirements, acceptance criteria, journeys, and open questions must treat SF-2 dag-loader-runner as the runtime ABI owner and must not contain stale downstream assumptions about `node_id` kwargs, alternate merge precedence, or mandatory core checkpoint/resume behavior. SF-4 is a consumer, not a co-owner, of the SF-2 runtime boundary.

**Citations:**
- [decision] D-GR-23: "SF-2 must remain the ABI owner with a clearly published boundary; SF-3 and SF-4 are consumers that must align downstream." -- Cycle 5 feedback formalizes this ownership model. SF-4 artifacts that imply co-ownership must be corrected.


### SF-5: Composer App Foundation & Tools Hub
<!-- SF: workflow-migration -->

#### AC-57
- **User Action:** Validate hierarchical context injection in a migrated workflow with nested phases and node-scoped mock matching.
- **Expected:** Resolved context is assembled in `workflow -> phase -> actor -> node` order (published by SF-2), Jinja2 templates can access the expected namespaces, and node-scoped behavior is matched through `ContextVar` without a `node_id` kwarg on `AgentRuntime.invoke()`.
- **Not Criteria:** No namespace leakage, no reordered merge precedence, and no SF-4-local reinterpretation of the SF-2 runtime ABI.
- **Requirements:** REQ-71, REQ-74
- **Citations:** - [decision] D-GR-23: "Hierarchical context merge order is `workflow -> phase -> actor -> node`." -- This acceptance criterion directly validates the ratified ordering contract as published by SF-2.


#### AC-58
- **User Action:** Run a Tier 2 planning-workflow mock execution using SF-3 fluent mock runtimes with node-specific matchers.
- **Expected:** The workflow executes with correct phase-mode assertions, and node-specific mocked responses are selected via `when_node(...)` behavior backed by the shared `ContextVar` path defined by SF-2 and consumed by SF-3.
- **Not Criteria:** No dict-constructor mock setup, no direct `invoke(..., node_id=...)` calls, and no test harness dependency on a core checkpoint/resume API in SF-2.
- **Requirements:** REQ-72, REQ-74
- **Citations:** - [code] subfeatures/testing-framework/prd.md:562-617: "SF-3's mock API is fluent and node-scoped, consuming SF-2's ContextVar." -- SF-4's Tier 2 tests must consume the current SF-3 test surface aligned to SF-2, not stale assumptions.


#### AC-59
- **User Action:** Run the declarative iriai-build-v2 bridge path through `run_declarative()` or the CLI `--declarative` flag.
- **Expected:** The bridge constructs `RuntimeConfig`, calls `run()`, and inspects `ExecutionResult`/`ExecutionHistory`/phase metrics without requiring or passing a `node_id` keyword and without depending on a built-in resume contract in SF-2.
- **Not Criteria:** No direct runtime ABI changes, no bridge-specific `invoke(..., node_id=...)` shim, and no assumption that SF-2 owns checkpoint persistence or resume orchestration.
- **Requirements:** REQ-73
- **Citations:** - [code] iriai-compose/iriai_compose/runner.py:41-50: "The abstract runtime signature is unchanged." -- The consumer integration must remain compatible with the current runtime interface as published by SF-2.


#### AC-60
- **User Action:** Review the revised SF-4 migration artifact and downstream parity expectations against the SF-2 PRD.
- **Expected:** All runtime-boundary language in SF-4 points to SF-2 as ABI owner; SF-4 uses only SF-2's published observability surface; no open question asks SF-2 to define a core checkpoint/resume contract.
- **Not Criteria:** No stale downstream artifact may continue to treat `node_id` kwargs or checkpoint/resume as part of the canonical SF-2 ABI. No SF-4 language implies co-ownership of the SF-2 runtime boundary.
- **Requirements:** REQ-74
- **Citations:** - [decision] D-GR-23: "SF-2 must remain the ABI owner with a clearly published boundary; SF-3 and SF-4 are consumers that must align downstream." -- This criterion validates the artifact-level hygiene requirement enforced by Cycle 5 feedback.


### SF-5: Composer App Foundation & Tools Hub
<!-- SF: workflow-migration -->

#### J-20: Translate Planning Workflow Against The Canonical SF-2 ABI
- **Actor:** Migration engineer with access to iriai-build-v2 source, the SF-2 published runtime ABI, and SF-3 fluent mock runtimes
- **Path:** happy
- **Preconditions:** SF-2 has published the approved runtime ABI (invoke unchanged, ContextVar node identity, canonical merge order, ExecutionResult observability, no core checkpoint/resume). SF-3 exposes mock runtimes aligned to that ABI. `planning.yaml` is ready for iterative migration.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Author or update `planning.yaml` using hierarchical context references that rely on workflow, phase, actor, and node scopes. | The YAML and prompt templates assume the canonical structural context order `workflow -> phase -> actor -> node` as published by SF-2. | No conflicting merge-order assumption and no lower-scope key duplication intended to override earlier scopes. | [decision] D-GR-23 |
| 2 | Run Tier 2 mock execution tests with SF-3 fluent mocks using `when_node(...)` for node-specific behavior. | Node-scoped matching works through `ContextVar` propagation (runtime-managed by SF-2 and consumed by SF-3 mocks) while `AgentRuntime.invoke()` remains unchanged. | No direct `invoke(..., node_id=...)` dependency and no stale mock-runtime contract layered on top of the SF-2 ABI. | [code] subfeatures/testing-framework/prd.md:1976-1980 |
| 3 | Execute the migrated workflow through the iriai-build-v2 declarative bridge. | The bridge passes existing runtimes through `RuntimeConfig`, calls `run()`, and consumes `ExecutionResult`/history metrics as the published SF-2 observability surface. | No bridge-local runtime shim and no expectation that SF-2 exposes checkpoint/resume APIs to complete the run. | [decision] D-GR-23 |

- **Outcome:** The migrated workflow, its tests, and its consumer bridge all run against one published SF-2 runtime ABI. SF-4 has made no extension to that boundary.
- **Requirements:** REQ-71, REQ-72, REQ-73


#### J-21: Remove A Stale node_id Consumer Assumption
- **Actor:** Migration engineer or architect reviewing downstream SF-3 or SF-4 artifacts
- **Path:** failure
- **Preconditions:** SF-2 has published its canonical ABI. A downstream artifact still assumes `AgentRuntime.invoke(..., node_id=...)`.
- **Failure Trigger:** A plan, test, or bridge helper encodes a `node_id` kwarg or another consumer-owned ABI extension that was not published by SF-2.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Compare the stale consumer artifact against the SF-2 PRD and the current runner signature. | The mismatch is explicit: SF-2 is the ABI owner with an unchanged `AgentRuntime.invoke()` signature; node identity flows via runner-managed `ContextVar`, not a kwarg. | The mismatch must not be treated as optional, implicit, or safe to paper over with a consumer-local shim. | [decision] D-GR-23 |
| 2 | Rewrite the consumer artifact so node-aware behavior reads from the shared `ContextVar` path and rerun the affected test or bridge flow. | The downstream artifact now matches the canonical SF-2 ABI and continues to support node-aware behavior through SF-3 tooling aligned to that ABI. | The fix must not preserve a hidden `node_id` argument path or a second competing runtime contract. | [code] subfeatures/testing-framework/prd.md:562-617 |

- **Outcome:** Downstream testing and migration artifacts converge back to the canonical SF-2 runtime boundary, with SF-4 remaining a clean consumer.
- **Requirements:** REQ-72, REQ-74
- **Related Journey:** J-1


#### J-22: Run Declarative Bridge Without A Core Resume Contract
- **Actor:** Platform developer integrating iriai-build-v2 with declarative workflows as a downstream consumer of SF-2
- **Path:** happy
- **Preconditions:** A migrated workflow is loadable, the bridge can construct `RuntimeConfig`, and SF-2's published observability surface (`ExecutionResult`, `ExecutionHistory`, phase metrics) is available.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Invoke the declarative bridge path for a migrated workflow. | The bridge calls `run()` with the canonical SF-2 inputs and existing runtime instances without needing a resume flag, checkpoint store contract, or modified runtime signature. | The bridge must not require a custom resume flag, checkpoint store contract, or modified runtime signature to start execution. | [decision] D-GR-23 |
| 2 | Inspect the completed run for parity evidence. | Completion and debugging data come from SF-2's published `ExecutionResult`, `ExecutionHistory`, and phase metrics keyed by logical phase ID. | Consumer validation must not depend on a core checkpoint/resume API being present in SF-2; resumability is an application-layer concern. | [code] subfeatures/dag-loader-runner/prd.md |

- **Outcome:** Consumer integration validates migration parity through the approved SF-2 observability surface. SF-4 remains a downstream consumer and adds no extension to SF-2's core runtime.
- **Requirements:** REQ-73, REQ-74


#### J-23: Consumer Expects Core Checkpoint/Resume From SF-2
- **Actor:** Platform developer or migration engineer whose downstream artifact treats checkpoint/resume as part of the SF-2 core
- **Path:** failure
- **Preconditions:** SF-2's PRD is available and explicitly scopes checkpoint/resume out of the mandatory core contract.
- **Failure Trigger:** A bridge helper, test harness, or migration note treats checkpoint/resume as a mandatory SF-2 runtime API.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Review the downstream artifact against the SF-2 PRD and Cycle 4/5 decision log. | The artifact is out of contract: SF-2 owns execution observability, not a mandatory checkpoint/resume API. | The mismatch must not be reframed as missing SF-2 functionality or left as an open migration blocker. | [decision] D-GR-23 |
| 2 | Update the artifact to use workflow-level/plugin-level/app-level recovery where needed and keep SF-2 assertions focused on execution observability. | The downstream flow now treats resume as an application-layer concern and remains compatible with the canonical SF-2 runner contract. | The recovery path must not smuggle checkpoint/resume requirements back into SF-2 through test-only or bridge-only abstractions. | [code] subfeatures/dag-loader-runner/prd.md |

- **Outcome:** Migration and bridge validation no longer depend on a core SF-2 checkpoint/resume contract. SF-4 responsibilities are bounded to the published SF-2 observability surface.
- **Requirements:** REQ-73, REQ-74
- **Related Journey:** J-3


### SF-5: Composer App Foundation & Tools Hub
#### HierarchicalContext <!-- SF: workflow-migration -->
- **Fields:** workflow scope, phase scope, actor scope, node scope
- **Constraints:** Merge order is `workflow -> phase -> actor -> node`, published by SF-2 as the canonical order; Duplicate keys preserve first occurrence in that order; Node identity is runtime-published through runner-managed ContextVar; not passed as a new invoke argument; SF-4 consumes this contract; it does not define or extend it
- **New:** no


#### ExecutionResult / ExecutionHistory <!-- SF: workflow-migration -->
- **Fields:** completion state, workflow output, branch paths, execution history, phase metrics
- **Constraints:** Observability surface is owned and published by SF-2; SF-4 consumes it; Phase metrics are keyed by logical phase ID; No mandatory core checkpoint/resume API is implied by or required from these structures; SF-4 must not treat the absence of a resume API as an SF-2 gap to be filled by a consumer-layer shim
- **New:** no


### From: composer-app-foundation
