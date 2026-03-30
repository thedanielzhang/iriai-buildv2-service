<!-- SF: dag-loader-runner -->

#### REQ-47: functional (must)
SF-2 MUST treat the current SF-1 PRD and its WorkflowConfig models as the only authoritative declarative wire contract. Validation and execution must use the in-process SF-1 models directly rather than a checked-in schema snapshot or stale SF-1 plan/system-design variants.

**Citations:**
- [decision] D-GR-22: "Nested YAML, edge-based hooks, live schema endpoint." -- Defines the authoritative schema/interface contract SF-2 must consume and enforce.
- [code] .iriai/artifacts/features/beced7b1/broad/architecture.md:353: "/api/schema/workflow returns JSON Schema from model_json_schema()." -- Confirms the canonical live schema endpoint used by composer integrations.


#### REQ-48: functional (must)
WorkflowConfig loading MUST accept only the SF-1 root fields schema_version, workflow_version, name, description, metadata, actors, phases, edges, templates, plugins, types, and cost_config. The loader MUST reject unapproved root additions such as stores, plugin_instances, top-level nodes, or any alternate root graph containers with actionable field-path errors.

**Citations:**
- [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md: "WorkflowConfig Root Fields (Closed Set). No stores or plugin_instances root fields permitted." -- SF-1 PRD defines the exact closed root set SF-2 must enforce.


#### REQ-49: functional (must)
Actor hydration MUST follow the SF-1 actor union exactly: actor_type is only agent or human. The loader MUST reject stale actor discriminators including interaction, and the runner MUST preserve this wire contract even when host applications adapt human interactions onto existing runtime abstractions.

**Citations:**
- [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md: "Actor Types: discriminated by actor_type field with only two valid values: agent and human. No interaction alias permitted." -- SF-1 PRD closes the actor union to exactly two discriminators.


#### REQ-50: functional (must)
Nested phase containment is authoritative: WorkflowConfig.phases contains top-level phases, each PhaseDefinition owns typed inputs, outputs, hooks, nodes, children, and phase-local edges, and flattened editor stores are never valid serialized runtime input.

**Citations:**
- [decision] D-GR-22: "Nested YAML, edge-based hooks, live schema endpoint." -- Defines the authoritative schema/interface contract SF-2 must consume and enforce.
- [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md: "PhaseDefinition: Typed inputs, outputs, and hooks (all use PortDefinition). nodes list, children list for nested phases, phase-local edges list." -- SF-1 PRD makes nested containment and typed phase ports authoritative.


#### REQ-51: functional (must)
The loader MUST index and validate typed ports across workflow boundaries, phases, nodes, hooks, and BranchNode.outputs using the SF-1 typed-port contract (type_ref XOR schema_def). Each port must define exactly one of type_ref or schema_def. Hook ports participate in the same typed-port system as data ports.

**Citations:**
- [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md: "Port Typing: Applies uniformly to phase inputs/outputs/hooks, node inputs/outputs/hooks, and BranchNode output ports. Each port uses PortDefinition with exactly one of type_ref or schema_def." -- SF-1 PRD establishes the typed-port contract as universal; updated to reference BranchNode.outputs per D-GR-35.


#### REQ-52: functional (must)
The runner MUST build recursive DAGs from nested phases and in-phase nodes at every depth, executing child phases inside their parent phase context and preserving phase-local versus workflow-level edge ownership.

**Citations:**
- [decision] D-GR-22: "Nested YAML, edge-based hooks, live schema endpoint." -- Defines the authoritative schema/interface contract SF-2 must consume and enforce.
- [code] iriai-compose/iriai_compose/runner.py:106: "parallel() already provides fail-fast concurrency semantics." -- Supports recursive map/fan-out execution expectations.


#### REQ-53: functional (must)
Hooks MUST be serialized and executed only as ordinary edges whose source resolves to a hook port. Serialized workflows must not include edge.port_type, separate hook sections, callback registries, or any hook-specific edge type.

**Citations:**
- [decision] D-GR-22: "Nested YAML, edge-based hooks, live schema endpoint." -- Defines the authoritative schema/interface contract SF-2 must consume and enforce.
- [code] .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1004: "edge.port_type is dropped from serialized form." -- Supports the edge-only hook serialization contract.


#### REQ-54: functional (must)
BranchNode execution MUST follow the D-GR-35 per-port model: inputs is a dict of typed input ports supporting gather from multiple upstream sources; the optional merge_function is valid and governs how multiple inputs are combined before condition evaluation; outputs is a dict where each key names an output port and each port's condition expression is evaluated independently; fan-out is non-exclusive — multiple output ports MAY fire in the same execution if their conditions are met. switch_function is not a valid field and MUST be rejected. The old SF-1 BranchNode fields condition_type, condition (top-level), paths, and output_field mode are stale and MUST be rejected.

**Citations:**
- [decision] D-GR-35: "D-GR-12 per-port model is the single authority. Fan-out is non-exclusive. merge_function is valid for gather. switch_function remains rejected. output_field is fully removed. old condition_type/condition/paths are stale." -- D-GR-35 makes the per-port BranchNode model authoritative and supersedes the old SF-1 exclusive three-field schema.


#### REQ-55: functional (must)
SF-2 MUST execute only the canonical atomic node types AskNode, BranchNode, and PluginNode, with sequential/map/fold/loop behavior owned by phase modes rather than standalone Map/Fold/Loop node executors.

**Citations:**
- [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md: "Three Atomic Node Types: AskNode, BranchNode, PluginNode." -- SF-1 PRD limits atomic node types to three; phase modes own iteration semantics.
- [code] iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py:25: "Existing workflows rely on nested review loops and phase sequencing." -- Confirms the litmus-test workflow patterns SF-2 must execute declaratively.


#### REQ-56: functional (must)
Sequential, map, fold, and loop phases MUST dispatch recursively from the nested phase tree so translated iriai-build-v2 workflows preserve review loops, parallel analysis, retry behavior, and child-phase structure. Loop-mode phases must preserve the independently routable condition_met and max_exceeded exits.

**Citations:**
- [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md: "Loop mode exposes two exit ports: condition_met and max_exceeded." -- SF-1 PRD defines the loop dual-exit contract SF-2 must route through the ordinary edge model.
- [code] iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py:25: "Existing workflows rely on nested review loops and phase sequencing." -- Confirms the litmus-test workflow patterns SF-2 must execute declaratively.


#### REQ-57: functional (must)
AgentRuntime.invoke() MUST remain unchanged, and SF-2 MUST propagate node identity and hierarchical context through runner-managed ContextVar state with merge order workflow -> phase -> actor -> node. Declarative execution must not require a breaking runtime ABI change.

**Citations:**
- [decision] D-GR-23: "Keep invoke() unchanged; merge workflow -> phase -> actor -> node." -- Preserves runtime compatibility while standardizing declarative context assembly.
- [code] iriai-compose/iriai_compose/runner.py:32: "ContextVar-backed phase identity already exists in the runtime." -- Supports the non-breaking context propagation requirement.


#### REQ-58: functional (must)
SF-2 MUST expose validate(workflow) for structural validation without live runtimes and run(workflow, config, *, inputs=None) for structural plus runtime-reference validation against the exact same SF-1 contract. run() must not accept documents that validate() would reject as non-canonical.

**Citations:**
- [decision] D-GR-22: "Nested YAML, edge-based hooks, live schema endpoint." -- Defines the authoritative schema/interface contract SF-2 must consume and enforce.
- [decision] D-GR-23: "Keep invoke() unchanged; merge workflow -> phase -> actor -> node." -- Preserves runtime compatibility while standardizing declarative context assembly.


#### REQ-59: functional (must)
/api/schema/workflow MUST remain the canonical composer-facing schema delivery path because it is derived from the same SF-1 models SF-2 executes. SF-2 must not depend on runtime workflow-schema.json, and composer/editor failure states must surface endpoint unavailability instead of silently falling back to a stale local bundle.

**Citations:**
- [decision] D-GR-22: "Nested YAML, edge-based hooks, live schema endpoint." -- Defines the authoritative schema/interface contract SF-2 must consume and enforce.
- [code] .iriai/artifacts/features/beced7b1/broad/architecture.md:353: "/api/schema/workflow returns JSON Schema from model_json_schema()." -- Confirms the canonical live schema endpoint used by composer integrations.


#### REQ-60: functional (must)
Validation MUST reject stale contract drift with actionable errors, including: stores, plugin_instances, top-level nodes (root-level), alternate actor discriminators (interaction), missing typed hook ports, switch_function, old BranchNode top-level fields condition_type / condition / paths / output_field mode, unknown branch output port references, serialized port_type, separate hook sections, invalid nested containment, and hook edges carrying transform_fn. merge_function is valid and MUST NOT be rejected.

**Citations:**
- [decision] D-GR-35: "switch_function remains rejected. merge_function is valid for gather. output_field is fully removed. old condition_type/condition/paths are stale." -- D-GR-35 revises the stale-field rejection list: merge_function is removed from rejection, switch_function and old three-field schema remain.
- [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md: "Acceptance criteria validate rejection of stale fields including port_type, interaction actor, switch_function, stores." -- SF-1 PRD makes rejection of stale fields a first-class requirement.


#### REQ-61: functional (must)
Declarative execution MUST return a single observability contract via ExecutionResult plus ExecutionHistory / phase metrics keyed by logical phase ID, while keeping checkpoint/resume out of the core SF-2 API.

**Citations:**
- [decision] D-GR-24: "Execution history and phase metrics are core; checkpoint/resume is not." -- Moves resumability above SF-2 while keeping observability in scope.
- [code] .iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:1815: "ExecutionResult includes history-based observability surface." -- Supports the execution-output contract after D-GR-24.


#### REQ-62: security (must)
Expression-bearing behavior and hook behavior MUST remain explicit and inspectable. Each BranchNode output port condition is an expression string evaluated under the shared expression security contract (AST allowlist, timeout, size limits). There is no output_field mode per port — per-port conditions are expressions only. Hook classification must come from port resolution rather than executable serialized metadata.

**Citations:**
- [decision] D-GR-35: "Per-port conditions are expressions only — no output_field mode per port. output_field is fully removed from the BranchNode schema everywhere." -- D-GR-35 removes output_field as a per-port routing mode; all per-port conditions are expressions subject to sandbox security.
- [decision] D-GR-22: "Nested YAML, edge-based hooks, live schema endpoint." -- Defines the authoritative schema/interface contract SF-2 must consume and enforce.


#### REQ-63: non-functional (must)
Declarative execution MUST ship additively under a new namespace without breaking DefaultWorkflowRunner, WorkflowRunner.parallel(), current storage abstractions, or existing imperative workflows that import iriai-compose.

**Citations:**
- [decision] D-GR-23: "Keep invoke() unchanged; merge workflow -> phase -> actor -> node." -- Preserves runtime compatibility while standardizing declarative context assembly.
- [code] iriai-compose/iriai_compose/runner.py:106: "parallel() already provides fail-fast concurrency semantics." -- Supports recursive map/fan-out execution expectations.


#### REQ-64: functional (should)
Live integration coverage SHOULD use configured plugin runtimes or externally managed stdio MCP servers plus separate test runtimes; the SF-2 runner must not take ownership of MCP subprocess lifecycle or add production-plugin test branches.

**Citations:**
- [decision] D-GR-25: "Use separate test runtimes and external stdio MCP servers." -- Keeps plugin/runtime integrations aligned with existing repo boundaries.


### SF-3: Testing Framework
<!-- SF: dag-loader-runner -->

#### AC-37
- **User Action:** Developer runs a workflow whose root document contains only the approved SF-1 fields and whose phases contain nested nodes and children.
- **Expected:** The loader accepts the workflow through the in-process SF-1 models, builds recursive phase/node DAGs, and executes it successfully through the declarative runner.
- **Not Criteria:** The loader expects flattened top-level nodes, accepts extra root containers, or relies on a checked-in schema file.
- **Requirements:** REQ-47, REQ-48, REQ-50, REQ-52, REQ-58
- **Citations:** - [decision] D-GR-22: "Nested YAML, edge-based hooks, live schema endpoint." -- Defines the authoritative schema/interface contract SF-2 must consume and enforce.


#### AC-38
- **User Action:** Developer validates YAML that includes root-level stores or plugin_instances.
- **Expected:** Validation fails before execution with a field-specific error explaining that those root additions are not part of the canonical SF-1 WorkflowConfig contract.
- **Not Criteria:** Runtime silently ignores the extra root fields or accepts them as informal extensions.
- **Requirements:** REQ-47, REQ-48, REQ-60
- **Citations:** - [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md: "No stores or plugin_instances root fields permitted." -- SF-1 PRD closes the root set; AC-2 verifies the loader enforces that closure.


#### AC-39
- **User Action:** Developer defines both an agent actor and a human actor in one workflow and executes Ask nodes that reference them.
- **Expected:** The loader accepts the actor union exactly as declared by SF-1, and the runner resolves each actor through the host runtime bridge without changing the workflow wire shape.
- **Not Criteria:** The workflow must serialize interaction instead of human, or the runner mutates the saved contract to match a host-specific actor model.
- **Requirements:** REQ-49, REQ-57, REQ-63
- **Citations:** - [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md: "Actor Types: discriminated by actor_type field with only two valid values: agent and human. No interaction alias permitted." -- SF-1 PRD closes the actor union; AC-3 verifies round-trip fidelity.


#### AC-40
- **User Action:** Developer validates YAML that uses actor_type: interaction or mixes human fields with agent-only fields.
- **Expected:** Validation fails with a precise actor-path error that points back to actor_type: agent|human and the correct field family.
- **Not Criteria:** The loader tolerates stale actor discriminators or guesses how to coerce the actor into a valid shape.
- **Requirements:** REQ-49, REQ-60
- **Citations:** - [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md: "No interaction alias permitted." -- SF-1 PRD makes interaction explicitly prohibited; AC-4 verifies early rejection.


#### AC-41
- **User Action:** Developer wires a phase on_start edge and a node on_end edge using ordinary source/target refs with typed hook ports.
- **Expected:** Validation accepts the edges, infers hook behavior from the source hook port, and preserves hook ports inside the same typed-port system used for data ports.
- **Not Criteria:** Hook execution requires port_type, a separate hooks block, or untyped hook ports that bypass validation.
- **Requirements:** REQ-50, REQ-51, REQ-53, REQ-62
- **Citations:** - [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md: "Hook ports are part of the node port model (no separate hook section). EdgeDefinition: No serialized port_type field." -- SF-1 PRD makes typed hook ports and edge-based hook inference authoritative.


#### AC-42
- **User Action:** Developer defines a port using only schema_def, another using only type_ref, and then creates a hook edge and a data edge across nested phases.
- **Expected:** Validation succeeds for the XOR-typed ports, indexes both data and hook ports correctly, and enforces type compatibility across the nested graph and BranchNode.outputs.
- **Not Criteria:** Hook ports are exempt from the typed-port rules, or the runner accepts ports with both or neither typing field.
- **Requirements:** REQ-51, REQ-60
- **Citations:** - [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md: "Each port uses PortDefinition with exactly one of type_ref or schema_def. Must not define both; must define at least one." -- SF-1 PRD defines the XOR constraint; AC-6 verifies uniform enforcement across all port positions including BranchNode.outputs.


#### AC-43
- **User Action:** Developer defines a BranchNode with inputs (one or more typed input ports), an optional merge_function, and outputs where each port has a condition expression; then connects downstream edges from selected output ports.
- **Expected:** For each output port whose condition evaluates to true, the runner fires every edge attached to that port — multiple output ports may fire in the same execution. When no condition is met, no output fires and execution records the no-match outcome. merge_function is accepted and used to combine multiple inputs before condition evaluation.
- **Not Criteria:** Branch routing depends on switch_function; old condition_type / condition / paths fields are accepted; only one output port is permitted to fire per execution (exclusive routing); merge_function triggers a validation error.
- **Requirements:** REQ-51, REQ-54, REQ-62
- **Citations:** - [decision] D-GR-35: "Fan-out is non-exclusive. merge_function is valid for gather. Per-port conditions are expressions only. switch_function remains rejected. output_field is fully removed." -- D-GR-35 per-port model is the single authority; AC-7 verifies the non-exclusive fan-out, merge_function acceptance, and per-port expression evaluation.


#### AC-44
- **User Action:** Developer validates YAML containing switch_function, old BranchNode fields condition_type, condition (top-level), paths, or output_field mode, or an edge referencing an unknown BranchNode output port name.
- **Expected:** Validation fails with a migration-oriented error naming each unsupported field and directing the author to the D-GR-35 per-port outputs model. For unknown output port references, the error lists the valid output port names. merge_function does NOT trigger an error.
- **Not Criteria:** Runtime silently accepts switch_function or the old three-field branch schema; merge_function is incorrectly rejected as stale.
- **Requirements:** REQ-54, REQ-60
- **Citations:** - [decision] D-GR-35: "switch_function remains rejected. merge_function is valid. old condition_type/condition/paths are stale. output_field is fully removed." -- D-GR-35 revises the stale-field rejection list; AC-8 verifies the updated boundary.


#### AC-45
- **User Action:** Developer executes translated iriai-build-v2 workflows that include nested fold/loop review patterns and parallel analysis steps.
- **Expected:** Phase modes and child-phase recursion execute correctly, and phase metrics/history are keyed by logical phase ID.
- **Not Criteria:** Branch nodes or hook edges are repurposed to emulate missing phase semantics, or nested loops flatten into one-level execution.
- **Requirements:** REQ-55, REQ-56, REQ-61
- **Citations:** - [code] iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py:25: "Existing workflows rely on nested review loops and phase sequencing." -- Confirms the litmus-test workflow patterns SF-2 must execute declaratively.


#### AC-46
- **User Action:** Runtime implementer inspects the declarative runner API and executes a workflow with existing AgentRuntime implementations.
- **Expected:** AgentRuntime.invoke() remains unchanged, node identity/context are propagated through runner-managed context, and no runtime ABI shim is required.
- **Not Criteria:** Declarative execution requires every runtime to adopt a new node_id parameter or a new agent interface.
- **Requirements:** REQ-57, REQ-63
- **Citations:** - [decision] D-GR-23: "Keep invoke() unchanged; merge workflow -> phase -> actor -> node." -- Preserves runtime compatibility while standardizing declarative context assembly.


#### AC-47
- **User Action:** Composer backend serves GET /api/schema/workflow, and the editor uses it for authoring controls while the runner validates the same YAML in-process.
- **Expected:** Backend, editor, validator, and runner all stay aligned because the endpoint is derived from the exact SF-1 models SF-2 executes.
- **Not Criteria:** Composer or runtime treats workflow-schema.json as a runtime contract or allows endpoint/schema drift to go unnoticed.
- **Requirements:** REQ-47, REQ-58, REQ-59
- **Citations:** - [code] .iriai/artifacts/features/beced7b1/broad/architecture.md:353: "/api/schema/workflow returns JSON Schema from model_json_schema()." -- Confirms the canonical live schema endpoint used by composer integrations.


#### AC-48
- **User Action:** Editor opens while /api/schema/workflow is unavailable.
- **Expected:** The UI reports schema unavailability explicitly and defers schema-driven authoring until the endpoint recovers.
- **Not Criteria:** The editor silently falls back to a stale bundled workflow-schema.json and continues authoring against a different contract than the runner.
- **Requirements:** REQ-59
- **Citations:** - [decision] D-GR-22: "Nested YAML, edge-based hooks, live schema endpoint." -- Defines the authoritative schema/interface contract SF-2 must consume and enforce.


#### AC-49
- **User Action:** Consumer inspects execution output after a declarative run.
- **Expected:** ExecutionResult exposes completion data plus ExecutionHistory / phase metrics, and no mandatory core checkpoint or resume API is required.
- **Not Criteria:** Runtime correctness depends on a built-in checkpoint store or a resume flag in the core runner surface.
- **Requirements:** REQ-61
- **Citations:** - [decision] D-GR-24: "Execution history and phase metrics are core; checkpoint/resume is not." -- Moves resumability above SF-2 while keeping observability in scope.


#### AC-50
- **User Action:** Live preview or MCP-backed plugin workflows are exercised in test and production-like environments.
- **Expected:** Tests use separate test runtimes and runtime integration uses configured plugin runtimes or external stdio servers.
- **Not Criteria:** The runner spawns and owns MCP subprocess lifecycle or adds production-plugin test-mode branches.
- **Requirements:** REQ-64
- **Citations:** - [decision] D-GR-25: "Use separate test runtimes and external stdio MCP servers." -- Keeps plugin/runtime integrations aligned with existing repo boundaries.


### SF-3: Testing Framework
<!-- SF: dag-loader-runner -->

#### J-13: Execute a canonical nested declarative workflow
- **Actor:** Platform engineer running a YAML workflow through iriai_compose.declarative.run()
- **Path:** happy
- **Preconditions:** The workflow uses only the SF-1 PRD root fields, actors use actor_type: agent|human, phases contain typed inputs/outputs/hooks, BranchNodes use the D-GR-35 per-port model (inputs, optional merge_function, outputs with per-port conditions), and required runtimes are configured.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Call run() with a workflow path and runtime config. | SF-2 loads the workflow through the current SF-1 models rather than a copied schema file or stale alternate artifact. | Loading depends on workflow-schema.json at runtime or on a second root-shape definition. | [decision] D-GR-22 |
| 2 | Let the loader validate the document root and actors. | Validation confirms only the approved root fields are present and accepts only actor_type: agent|human. | Extra root fields (stores, plugin_instances) are tolerated, or actor coercion hides a stale interaction discriminator. | [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md |
| 3 | Let the loader walk the workflow structure. | The loader indexes typed phase, node, hook, and branch output-port definitions across phases[].nodes, phases[].children, and workflow-level edges. Each BranchNode.outputs port is validated as a BranchOutputPort (typed PortDefinition plus a condition expression). | Hook ports bypass the typed-port system, nested child phases are flattened implicitly, or old BranchNode.paths fields are accepted. | [decision] D-GR-35 |
| 4 | Enter a phase and execute an Ask node, a Branch node, and a hook edge. | The Ask node resolves through the unchanged runtime boundary. The Branch node evaluates each output port's condition expression independently; all ports whose conditions evaluate true fire their downstream edges (non-exclusive fan-out). The optional merge_function is called before condition evaluation if multiple inputs are present. The hook edge is discovered by source-port resolution with no switch_function, port_type, or breaking invoke(..., node_id=...) signature required. | The runner requires switch_function, the old condition_type/condition/paths schema, port_type, or enforces exclusive single-path routing. | [decision] D-GR-35; [decision] D-GR-23 |
| 5 | Observe the completed workflow result. | ExecutionResult reports completion plus history and phase metrics keyed by logical phase ID. | Completion depends on a mandatory built-in checkpoint/resume API. | [decision] D-GR-24 |

- **Outcome:** The workflow runs from the same canonical SF-1 / D-GR-35 contract the backend publishes and the editor authors.
- **Requirements:** REQ-47, REQ-48, REQ-49, REQ-50, REQ-51, REQ-52, REQ-54, REQ-56, REQ-57, REQ-58


#### J-14: Share one schema contract across backend, editor, and runner
- **Actor:** Composer/backend engineer integrating SF-5 and SF-6 with iriai-compose
- **Path:** happy
- **Preconditions:** The backend exposes GET /api/schema/workflow, the editor keeps a flat internal store only internally, and SF-2 validates workflows directly against the same SF-1 models.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Serve GET /api/schema/workflow from the backend. | The endpoint returns JSON Schema derived from WorkflowConfig.model_json_schema() for the canonical SF-1 contract, including the D-GR-35 BranchNode shape. | The backend serves a stale copied schema file, or serves the old condition_type/condition/paths BranchNode shape. | [code] .iriai/artifacts/features/beced7b1/broad/architecture.md:353 |
| 2 | Save a workflow from the editor's flat internal canvas store. | Save/export serializes to the canonical nested YAML root, groups nodes into phase.nodes, emits children for nested phases, keeps typed hooks, emits BranchNode with inputs/outputs per-port model (and merge_function if present), and omits serialized port_type. | Save persists editor-only flattening, extra root fields, alternate hook metadata, or old BranchNode.paths shape that the runner rejects. | [decision] D-GR-35 |
| 3 | Send the saved YAML to validate() and then to run(). | Both APIs accept the same workflow shape because they consume the exact same SF-1 / D-GR-35 contract the endpoint publishes. | Validation and runtime diverge because they used different schema authorities, or merge_function triggers a rejection in one but not the other. | [decision] D-GR-22 |

- **Outcome:** Backend, editor, and runner round-trip one canonical workflow shape with no unofficial schema dialects.
- **Requirements:** REQ-47, REQ-50, REQ-51, REQ-53, REQ-58, REQ-59, REQ-60


#### J-15: Reject stale actor or root-shape drift before execution
- **Actor:** Workflow author importing older YAML into the composer or runner
- **Path:** failure
- **Preconditions:** YAML includes root-level stores, plugin_instances, top-level nodes, actor_type: interaction, or another pre-canonical shape.
- **Failure Trigger:** Structural validation sees a stale root field or invalid actor discriminator.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Call validate() or run() on the stale workflow. | Validation fails before execution and points to the unsupported root or actor field with guidance toward the canonical SF-1 PRD shape. | The loader silently ignores, coerces, or partially executes the stale document. | [code] .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md |
| 2 | Rewrite the workflow to use only approved root fields and actor_type: agent|human, then retry. | The corrected workflow validates and proceeds to execution against the same canonical contract used everywhere else. | The author has to maintain a second legacy serialization format for SF-2. | [decision] D-GR-22 |

- **Outcome:** Root-shape and actor-shape drift are blocked early so stale SF-1 artifacts cannot survive as alternate runtime contracts.
- **Requirements:** REQ-48, REQ-49, REQ-58, REQ-60
- **Related Journey:** J-1


#### J-16: Reject stale hook or branch serialization
- **Actor:** Workflow author importing older YAML into the composer or runner
- **Path:** failure
- **Preconditions:** YAML includes edge.port_type, a separate hooks block, switch_function, old BranchNode fields condition_type / condition (top-level) / paths / output_field mode, or another stale routing field. Note: merge_function is valid under D-GR-35 and does NOT appear in this failure precondition.
- **Failure Trigger:** Structural validation sees stale hook metadata or a stale branch routing field (switch_function, old condition_type/condition/paths, or output_field mode per port).

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Call validate() or run() on the stale workflow. | Validation fails with field-specific guidance directing the author back to typed ports, ordinary edges for hooks, and the D-GR-35 per-port BranchNode.outputs model. For old condition_type/condition/paths fields, the error explicitly names each stale field and references the inputs/merge_function/outputs replacement shape. merge_function by itself does NOT fail validation. | The runtime silently infers semantics from stale port_type; switch_function or old condition_type/condition/paths are accepted as compatibility shims; merge_function is incorrectly rejected. | [decision] D-GR-35 |
| 2 | Rewrite the workflow through the canonical save/export path and retry. | The workflow validates because hook behavior is encoded only through source/target port refs and branch routing uses the inputs/merge_function/outputs per-port model. | The author must preserve a second branch or hook dialect that only one downstream tool understands. | [decision] D-GR-22 |

- **Outcome:** Hook and branch drift are rejected early and corrected toward the single D-GR-35-aligned executable wire format.
- **Requirements:** REQ-51, REQ-53, REQ-54, REQ-58, REQ-60, REQ-62
- **Related Journey:** J-1


#### J-17: Surface schema-endpoint failure instead of falling back to a stale runtime schema file
- **Actor:** Composer user opening the workflow editor while the backend schema endpoint is unavailable
- **Path:** failure
- **Preconditions:** The editor depends on GET /api/schema/workflow for live authoring metadata.
- **Failure Trigger:** The schema request fails or times out.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open the editor and request GET /api/schema/workflow. | The UI surfaces an explicit schema-unavailable error and disables schema-driven authoring actions until the endpoint recovers. | The editor silently falls back to a stale bundled workflow-schema.json file. | [decision] D-GR-22 |
| 2 | Retry after the backend restores the endpoint. | The editor resumes using the live schema and saved workflows continue to validate against the same models SF-2 runs. | Recovery requires rebuilding the editor or swapping schema files to restore correctness. | [code] .iriai/artifacts/features/beced7b1/broad/architecture.md:353 |

- **Outcome:** Schema availability failures degrade visibly and safely instead of reintroducing a static-schema-first runtime contract.
- **Requirements:** REQ-59
- **Related Journey:** J-2


### SF-3: Testing Framework
#### WorkflowConfig <!-- SF: dag-loader-runner -->
- **Fields:** schema_version (str), workflow_version (int), name (str), description (Optional[str]), metadata (Optional[dict]), actors (dict[str, ActorDefinition]), phases (list[PhaseDefinition]), edges (list[EdgeDefinition]) — cross-phase only, templates (Optional[dict[str, TemplateDefinition]]), plugins (Optional[dict[str, PluginInterface]]), types (Optional[dict[str, JsonSchema]]), cost_config (Optional[WorkflowCostConfig])
- **Constraints:** Closed set — only the twelve SF-1 PRD root fields are allowed; No root-level stores or plugin_instances; No top-level nodes container; Workflow-level edges are cross-phase only
- **New:** no


#### ActorDefinition <!-- SF: dag-loader-runner -->
- **Fields:** actor_type: agent | human (discriminator), agent fields: provider, model, role, persistent, context_keys, human fields: identity, channel
- **Constraints:** Discriminated union — exactly agent or human; No interaction alias permitted in serialized workflows; No environment-specific credentials embedded in workflow YAML
- **New:** no


#### PhaseDefinition <!-- SF: dag-loader-runner -->
- **Fields:** id (str), name (str), mode: sequential | map | fold | loop, mode-specific config, inputs (dict[str, PortDefinition]), outputs (dict[str, PortDefinition]), hooks (dict[str, PortDefinition]), nodes (list[NodeDefinition]), children (list[PhaseDefinition]), edges (list[EdgeDefinition]), context_keys, metadata, cost
- **Constraints:** nodes serialize under phases[].nodes; Nested phases serialize under phases[].children; Phase-local edges stay with the owning phase; Loop mode exposes condition_met and max_exceeded exit ports
- **New:** no


#### NodeDefinition <!-- SF: dag-loader-runner -->
- **Fields:** id (str), type: ask | branch | plugin, inputs (dict[str, PortDefinition]), outputs (dict[str, PortDefinition]), hooks (dict[str, PortDefinition]), artifact_key, context_keys, cost
- **Constraints:** Only three atomic node types (AskNode, BranchNode, PluginNode); Nodes serialize only inside phases[].nodes; Hook ports participate in the same typed-port system as data ports
- **New:** no


#### BranchNode <!-- SF: dag-loader-runner -->
- **Fields:** inputs (dict[str, PortDefinition]) — one or more typed input ports; supports gather from multiple upstream sources, merge_function (Optional[str]) — optional callable name invoked to combine multiple inputs before condition evaluation; valid field, outputs (dict[str, BranchOutputPort]) — named output ports, each carrying a typed PortDefinition plus a condition expression string
- **Constraints:** Fan-out is non-exclusive: each output port's condition is evaluated independently; multiple ports MAY fire in the same execution if their conditions are satisfied; Per-port conditions are expressions only — evaluated under the shared sandbox security contract (AST allowlist, timeout, size limits); No output_field mode per output port; switch_function is not a valid field and MUST be rejected at validation; Old SF-1 BranchNode fields condition_type, top-level condition, paths, and output_field mode are stale and MUST be rejected at validation; merge_function is valid and MUST NOT be rejected; Unknown output port name references in edges are invalid and rejected at validation
- **New:** no


#### BranchOutputPort <!-- SF: dag-loader-runner -->
- **Fields:** type_ref (Optional[str]) — reference to named type in types registry (inherited from PortDefinition), schema_def (Optional[dict]) — inline JSON Schema (inherited from PortDefinition), description (Optional[str]) — (inherited from PortDefinition), condition (str) — expression string evaluated to determine whether this output port fires; required on every branch output port
- **Constraints:** XOR: exactly one of type_ref or schema_def must be present (inherited from PortDefinition); condition must be a non-empty string; empty or missing condition is a validation error; Condition evaluation uses the shared AST-allowlist expression sandbox with timeout and size limits; No output_field shorthand — per-port conditions are expressions only
- **New:** yes


#### PortDefinition <!-- SF: dag-loader-runner -->
- **Fields:** type_ref (Optional[str]) — reference to named type in types registry, schema_def (Optional[dict]) — inline JSON Schema, description (Optional[str]), required (Optional[bool]) — for input ports
- **Constraints:** XOR: exactly one of type_ref or schema_def must be present; Must not define both; must define at least one; Applies uniformly to phase inputs/outputs/hooks, node inputs/outputs/hooks, and BranchNode.inputs; YAML shorthand (bare string type name) normalizes to full PortDefinition
- **New:** no


#### EdgeDefinition <!-- SF: dag-loader-runner -->
- **Fields:** source (str) — dot notation e.g. phase_a.node_1 or phase_b.on_end, target (str) — dot notation, transform_fn (Optional[str]), description (Optional[str])
- **Constraints:** No serialized port_type field; Hook-vs-data inferred from resolving source port container (hooks vs outputs); Hook edges must not define transform_fn; Source and target use dot notation or boundary refs
- **New:** no


#### RuntimeConfig <!-- SF: dag-loader-runner -->
- **Fields:** agent_runtime, interaction_runtimes (host-managed human-interaction adapters), artifacts, sessions, context_provider, plugin_registry, workflow execution wiring
- **Constraints:** Runtime dependency bundle only; not part of WorkflowConfig; Must not change the declarative wire contract; Must not require breaking AgentRuntime changes
- **New:** yes


#### HierarchicalContext <!-- SF: dag-loader-runner -->
- **Fields:** workflow scope, phase scope, actor scope, node scope
- **Constraints:** Merge order: workflow -> phase -> actor -> node; Propagated via runner-managed ContextVar — no breaking invoke() changes
- **New:** yes


#### ExecutionResult / ExecutionHistory <!-- SF: dag-loader-runner -->
- **Fields:** completion state, workflow output, trace and branch path data (including which output ports fired per BranchNode execution), phase metrics/history (keyed by logical phase ID), hook warnings and execution errors
- **Constraints:** Metrics keyed by logical phase ID; No mandatory core checkpoint/resume API
- **New:** yes


#### ValidationError <!-- SF: dag-loader-runner -->
- **Fields:** field_path (str), message (str), severity (str), code (str)
- **Constraints:** Used to reject stale root fields, actor discriminators, hook metadata, branch routing fields, and nested-DAG violations before execution
- **New:** yes


### From: testing-framework
