<!-- SF: testing-framework -->

#### REQ-65: functional (must)
`MockAgentRuntime` must keep the fluent no-argument API and perform node-specific matching from the current-node `ContextVar` published by SF-2 dag-loader-runner rather than from any change to `AgentRuntime.invoke()`.

**Citations:**
- [decision] D-GR-23: "Keep `AgentRuntime.invoke()` unchanged and propagate `node_id` via `ContextVar`." -- Authoritative cross-subfeature runtime contract that SF-3 consumes.
- [code] iriai-compose/iriai_compose/runner.py:36-50: "`AgentRuntime.invoke()` has no `node_id` kwarg in the production ABC." -- The existing ABC is the non-breaking contract SF-3 must target.


#### REQ-66: functional (must)
Prompt-aware mock behavior and downstream migration parity must consume hierarchical context from SF-2 in the canonical merge order `workflow -> phase -> actor -> node`, deduplicated in that order.

**Citations:**
- [decision] D-GR-23: "Hierarchical context merge order is `workflow -> phase -> actor -> node`." -- Resolves drifting merge-order assumptions across SF-2, SF-3, and SF-4.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:31: "SF-2 REQ-11 mandates this merge order." -- SF-2 owns the merge order as part of its published ABI.


#### REQ-67: non-functional (must)
SF-2 dag-loader-runner is the canonical runtime ABI owner for SF-3 and SF-4, as established by SF-2 REQ-11: `AgentRuntime.invoke()` stays unchanged (no `node_id` kwarg), node identity is runner-owned `ContextVar` state, the merge order is `workflow -> phase -> actor -> node`, and core checkpoint/resume is not part of the mandatory SF-2 runtime contract. SF-3 is a consumer of this ABI; it may not redefine any part of it.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:31: "SF-2 REQ-11: `AgentRuntime.invoke()` MUST remain unchanged; node identity travels via runner-managed `ContextVar`; core checkpoint/resume outside mandatory contract." -- SF-2 PRD is the authoritative ABI owner; SF-3 is a downstream consumer.
- [code] iriai-compose/iriai_compose/runner.py:36-50: "Production ABC has no `node_id` kwarg." -- Confirms the non-breaking contract is already live.


#### REQ-68: functional (must)
The Architect must remove plan decision D-SF3-16 ('AgentRuntime.invoke() explicitly owns the node routing contract via `node_id` kwarg') and the stale ABC block in the SF-3 plan that shows `node_id: str | None = None` as a parameter of `invoke()`. No SF-3 or SF-4 consumer artifact may retain this contract.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/plan.md:28: "D-SF3-16: `AgentRuntime.invoke()` explicitly owns the node routing contract via `node_id` kwarg." -- This is the specific stale plan decision that directly contradicts SF-2 REQ-11 and must be removed.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/plan.md:78-90: "Stale ABC block showing `node_id: str | None = None` on `invoke()`." -- The plan's verified contract section encodes the breaking interface and must be corrected.


#### REQ-69: functional (must)
Execution-path assertions and migration parity checks must rely on SF-2's published observability surface (`ExecutionResult`, `ExecutionHistory`, and phase metrics) rather than on any built-in checkpoint/resume contract from SF-2.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:35: "SF-2 REQ-15: declarative execution returns `ExecutionResult` plus `ExecutionHistory`/phase metrics while keeping checkpoint/resume out of the core SF-2 API." -- Observability is published; checkpoint/resume ownership is not mandatory core ABI.


#### REQ-70: functional (must)
SF-3 must not introduce any wrapper, adapter, or consumer-owned mechanism that carries node identity to `AgentRuntime.invoke()` other than reading the runner-published `ContextVar`. Any `when_node()` routing in `MockAgentRuntime` must source node identity exclusively from that `ContextVar`.

**Citations:**
- [code] iriai-compose/iriai_compose/runner.py:32-33: "`_current_phase_var: ContextVar[str]` already exists in production runner." -- Establishes the ContextVar pattern that node identity must follow in the declarative runner.
- [decision] D-GR-23: "Node identity propagated via ContextVar." -- Consumer-owned carriers would reintroduce the broken ABI through the back door.


### SF-4: Workflow Migration & Litmus Test
<!-- SF: testing-framework -->

#### AC-51
- **User Action:** Developer configures `MockAgentRuntime` with both `when_node()` and `when_role()` matchers and runs a workflow through `run(workflow, RuntimeConfig(agent_runtime=mock))`.
- **Expected:** The node-specific matcher wins for the targeted node, the role matcher remains the fallback, and this works under the unchanged `AgentRuntime.invoke()` ABC because node identity is sourced from the SF-2 runner `ContextVar`.
- **Not Criteria:** Role matching must not override node matching, unmatched calls must not silently return `None`, and the test must not require a breaking `invoke(..., node_id=...)` contract.
- **Requirements:** REQ-65, REQ-67
- **Citations:** - [code] iriai-compose/iriai_compose/runner.py:36-50: "Production ABC confirms no `node_id` kwarg exists." -- The acceptance criterion verifies end-to-end node-routing without an ABI break.


#### AC-52
- **User Action:** Developer creates `MockAgentRuntime()` with no constructor arguments and configures node-aware behavior through fluent methods only.
- **Expected:** `when_node()` routing and call recording work while `AgentRuntime.invoke()` remains unchanged, and no dict constructor or `node_id` kwarg path exists.
- **Not Criteria:** Dict-based constructor paths must not be accepted, and `when_node()` must not depend on a parameter added to `invoke()`.
- **Requirements:** REQ-65, REQ-68, REQ-70
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/plan.md:25: "D-SF3-2: MockRuntime keeps fluent no-arg builder API." -- Even the plan's own fluent-builder decision conflicts with D-SF3-16, confirming D-SF3-16 is the stale outlier.


#### AC-53
- **User Action:** Developer or migration engineer uses prompt-aware mock handlers or prompt rendering that depends on hierarchical context.
- **Expected:** Context-sensitive behavior is evaluated against the canonical merged context ordered as `workflow -> phase -> actor -> node`, and no consumer-specific merge contract is needed.
- **Not Criteria:** No alternate merge order may be assumed, and context assembly must not drop or reorder higher-level inputs relative to the published SF-2 ABI.
- **Requirements:** REQ-66, REQ-67
- **Citations:** - [decision] D-GR-23: "Hierarchical context merge order `workflow -> phase -> actor -> node`." -- Makes merge-order behavior directly testable in consumer code.


#### AC-54
- **User Action:** Developer writes execution-path or migration parity assertions after a completed declarative run.
- **Expected:** The available observability surface is `ExecutionResult`, `ExecutionHistory`, and phase metrics as published by SF-2; no mandatory core checkpoint/resume API is required for the assertion contract.
- **Not Criteria:** Tests must not depend on a built-in SF-2 checkpoint/resume ABI, a synthetic `history=` `run()` kwarg, or any consumer-owned resumability contract.
- **Requirements:** REQ-67, REQ-69
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:35: "SF-2 REQ-15 keeps checkpoint/resume outside the core API." -- SF-3 consumers must not reintroduce a checkpoint/resume dependency SF-2 explicitly excluded.


#### AC-55
- **User Action:** Architect reviews the SF-3 plan after this revision is applied.
- **Expected:** Plan decision D-SF3-16 has been removed, the stale ABC block showing `node_id: str | None = None` on `invoke()` has been corrected, and every implementation note referencing node routing via `invoke()` parameter has been rewritten to reference the runner `ContextVar`.
- **Not Criteria:** Any version of D-SF3-16 or any `node_id` kwarg on `AgentRuntime.invoke()` must not remain in the consumer plan.
- **Requirements:** REQ-67, REQ-68
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/plan.md:28: "D-SF3-16 is the specific stale decision to remove." -- Providing a verifiable before/after target for the Architect's plan correction.


#### AC-56
- **User Action:** Runtime implementer inspects the declarative runner API and implements `AgentRuntime` for use with the SF-3 test harness.
- **Expected:** `AgentRuntime.invoke()` matches the current production ABC exactly (role, prompt, output_type, workspace, session_key — no `node_id`), and node identity is available through `ContextVar` without any ABC change.
- **Not Criteria:** The SF-3 test harness must not require a runtime implementation that adds `node_id` to `invoke()`.
- **Requirements:** REQ-67, REQ-68, REQ-70
- **Citations:** - [code] iriai-compose/iriai_compose/runner.py:36-50: "Production ABC: role, prompt, output_type, workspace, session_key — no node_id." -- This is the ground truth the plan's stale ABC must be corrected to match.


### SF-4: Workflow Migration & Litmus Test
<!-- SF: testing-framework -->

#### J-18: Run a Node-Aware Test Against the Published SF-2 ABI
- **Actor:** Workflow developer
- **Path:** happy
- **Preconditions:** The developer has the revised SF-3 testing package and an SF-2 runner that publishes current node identity via `ContextVar` and execution observability via `ExecutionResult`/`ExecutionHistory`. SF-2 REQ-11 is the implemented ABI.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Create `MockAgentRuntime()` and configure both `when_node("x")` and `when_role("pm")` matchers. | The fluent API accepts the configuration with no constructor dicts or explicit runtime-signature changes, because SF-3 is a consumer of the published SF-2 ABI rather than a definer of it. | The developer must not need to configure a `node_id` kwarg on `AgentRuntime.invoke()` or any consumer-owned context-carrier mechanism. | [code] iriai-compose/iriai_compose/runner.py:36-50 |
| 2 | Run the workflow via `run(workflow, RuntimeConfig(agent_runtime=mock))`. | Node-specific routing works under the unchanged `AgentRuntime.invoke()` ABC because SF-2 supplies current node identity through its runner `ContextVar`, and prompt-aware handlers see context in `workflow -> phase -> actor -> node` order. | Execution must not require a breaking `invoke(..., node_id=...)` contract, an alternate merge order, or any wrapper that changes the runner ABI. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:31 |
| 3 | Assert execution-path behavior with the standard SF-3 assertions. | Assertions validate the expected node path and execution observability by consuming `ExecutionResult`, `ExecutionHistory`, and phase metrics from SF-2 — no checkpoint/resume API required. | Assertions must not require a built-in core checkpoint/resume contract, synthetic `history=` `run()` parameters, or consumer-specific result fields outside the published SF-2 ABI. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:35 |

- **Outcome:** The developer can write deterministic node-aware tests and execution assertions without forcing a runtime-interface break or inventing a parallel resumability contract.
- **Requirements:** REQ-65, REQ-67, REQ-69, REQ-70


#### J-19: Remove Stale Consumer Assumptions Before Implementation
- **Actor:** Architect
- **Path:** failure
- **Preconditions:** The SF-3 plan still contains D-SF3-16 and the stale ABC block showing `node_id: str | None = None` on `invoke()`. The revised R18 PRD and SF-2 PRD/REQ-11 are the authoritative product artifacts.
- **Failure Trigger:** A consumer plan or design note requires `invoke(..., node_id=...)`, implies a different context merge order, or treats checkpoint/resume as part of the core SF-2 ABI.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Review the SF-3 plan against SF-2 REQ-11 and the revised R18 PRD. | D-SF3-16 and the stale ABC block (plan.md lines 78–90 showing `node_id: str | None = None`) are identifiable and directly conflict with the published SF-2 ABI. | The mismatch must not be treated as optional, consumer-local, or deferrable. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/plan.md:28 |
| 2 | Remove D-SF3-16, correct the stale ABC block, and rewrite all node-routing notes to reference the runner `ContextVar` with merge order `workflow -> phase -> actor -> node`. | The consumer plan now aligns to SF-2 as ABI owner: `invoke()` has no `node_id` kwarg, node identity comes from `ContextVar`, execution assertions consume SF-2 observability without a checkpoint/resume dependency. | The revised plan must not retain any `invoke(..., node_id=...)` requirement, conflicting merge-order text, or mandatory core checkpoint/resume dependency. | [code] iriai-compose/iriai_compose/runner.py:36-50 |

- **Outcome:** Implementation planning proceeds against the published SF-2 ABI (REQ-11) instead of the stale D-SF3-16 breaking-interface assumption.
- **Requirements:** REQ-66, REQ-67, REQ-68, REQ-69, REQ-70
- **Related Journey:** J-1


### SF-4: Workflow Migration & Litmus Test
#### MockAgentRuntime <!-- SF: testing-framework -->
- **Fields:** no-arg constructor, _matchers: list[ResponseMatcher], calls: list[MockCall], when_node(), when_role(), default_response()
- **Constraints:** Must use fluent configuration only; Must read current node identity from the runner-owned ContextVar published by SF-2 — not from any parameter added to AgentRuntime.invoke(); Must not require or simulate AgentRuntime.invoke(node_id=...); Must not define a testing-owned ABI variant
- **New:** no


#### MockCall <!-- SF: testing-framework -->
- **Fields:** node_id, role, prompt, output_type, response, cost, timestamp
- **Constraints:** node_id is captured from runner ContextVar state, not from an invoke() kwarg; Recorded call shape must remain compatible with the unchanged production ABC (runner.py:36–50)
- **New:** no


#### ExecutionResult / ExecutionHistory <!-- SF: testing-framework -->
- **Fields:** success, nodes_executed: list[tuple[str, str]], branch_paths: dict[str, str], history: ExecutionHistory | None, phase metrics
- **Constraints:** They are the published observability surface for SF-3/SF-4 consumers; They must not be treated as a built-in core checkpoint/resume contract; SF-3 assertion helpers compute node-ID views locally from nodes_executed — they do not extend these SF-2 dataclasses
- **New:** no


### From: workflow-migration
