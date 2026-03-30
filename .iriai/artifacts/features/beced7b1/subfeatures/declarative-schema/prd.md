<!-- SF: declarative-schema -->

#### REQ-24: functional (must)
`WorkflowConfig` MUST remain YAML-first and include only `schema_version`, `workflow_version`, `name`, `description`, `metadata`, `actors`, `phases`, `edges`, `templates`, `plugins`, `types`, and `cost_config` at the root. Top-level `phases` are the only place nodes enter the document; top-level `edges` are reserved for cross-phase wiring; unapproved root additions such as `stores` and `plugin_instances` are invalid.

**Citations:**
- [decision] D-GR-22: "YAML remains nested (`phases[].nodes`, `phases[].children`)." -- The cycle-4 baseline makes nested phase containment the authoritative YAML contract with a closed root field set.


#### REQ-25: functional (must)
`ActorDefinition` MUST use `actor_type` as its discriminator with only `agent` and `human` as valid values. `AgentActorDef` carries provider/model/role/persistent/context_keys semantics, and `HumanActorDef` carries identity/channel semantics without embedding environment-specific credentials or reviving `interaction` as a serialized alias.

**Citations:**
- [decision] D-GR-30: "actor_type: agent|human only — no interaction alias." -- Cycle-4 closed the actor discriminator contract.


#### REQ-26: functional (must)
The schema MUST expose exactly three atomic node types: `AskNode`, `BranchNode`, and `PluginNode`. `AskNode` is an atomic actor invocation with a `prompt` field. `BranchNode` uses the D-GR-12 per-port conditions model: each output port in `outputs` carries its own `condition` expression (non-exclusive fan-out — multiple ports may fire if their conditions are satisfied), and an optional `merge_function` handles gather semantics when multiple inputs converge. `PluginNode` invokes external capabilities. `switch_function` and `output_field` are not valid fields; `merge_function` is only valid on `BranchNode` for gather and is not a routing function.

**Citations:**
- [decision] D-GR-35: "D-GR-12 per-port model is the single authority. Non-exclusive fan-out; switch_function remains rejected; merge_function valid for gather." -- Cycle-6 replaced the exclusive single-path routing model with per-port conditions across all subfeatures.


#### REQ-27: functional (must)
`EdgeDefinition` MUST serialize connections with `source` and `target` dot notation plus optional `transform_fn`. It MUST NOT serialize a `port_type` field. Hook-vs-data behavior is determined by resolving the source port container (`hooks` vs `outputs`), and hook wiring is represented only as ordinary edges.

**Citations:**
- [decision] D-GR-22: "Hook wiring remains edge-based with no separate `hooks` section and no serialized `port_type`." -- This is the specific contract correction the stale downstream artifacts must adopt.


#### REQ-28: functional (must)
`PhaseDefinition` MUST be the primary execution container and include `id`, `name`, `mode`, a single discriminated-union `mode_config`, typed `inputs`/`outputs`/`hooks`, `context_keys`, `metadata`, `cost`, `nodes`, `children`, and phase-local `edges`. `nodes` serialize under `phases[].nodes`, nested phases serialize under `phases[].children`, and internal edges live with the owning phase.

**Citations:**
- [decision] D-GR-22: "Nested YAML phase containment is authoritative." -- The broad architecture already models PhaseDefinition with nodes and children under the nested containment shape.


#### REQ-29: functional (must)
The schema MUST support four phase execution modes: `sequential`, `map`, `fold`, and `loop`. Iteration and fan-out semantics belong to phases, not to standalone Map/Fold/Loop node types. Each mode has a matching `ModeConfig` variant discriminated by the `mode` field.

**Citations:**
- [decision] R3-2: "Execution modes belong on phases." -- The earlier phase-mode decision remains valid; ModeConfig is now explicitly a discriminated union.


#### REQ-30: functional (must)
Loop-mode phases MUST expose two independently routable exit ports, `condition_met` and `max_exceeded`, through the same edge model used everywhere else.

**Citations:**
- [decision] R3-4: "Loop mode has dual exit ports." -- The loop termination contract remains part of the stable schema surface.


#### REQ-31: functional (must)
Every node and phase MUST define `on_start` and `on_end` as typed hook ports governed by the same port-definition rules as other outputs, but hook behavior is serialized only through edges and port resolution. The schema MUST NOT introduce a separate serialized hooks section, callback list, or hook-specific edge type.

**Citations:**
- [decision] D-GR-22: "Hooks stay edge-based; no separate serialized hook model." -- Hook serialization is a direct stale-artifact cleanup requirement from cycle 4.


#### REQ-32: functional (must)
`TemplateDefinition` MUST remain a reusable phase abstraction that expands into the same nested phase model. Templates use the same `nodes`, `children`, `edges`, and port contracts as inline phases.

**Citations:**
- [decision] D-9: "Templates are reusable workflow building blocks, not a parallel schema dialect." -- Template reuse must preserve the canonical phase contract.


#### REQ-33: functional (must)
`PluginInterface` MUST define plugin identity, description, typed inputs/outputs, and configuration schema so plugin nodes remain first-class schema participants without changing the core edge or phase model or requiring a separate root `plugin_instances` registry.

**Citations:**
- [decision] D-12: "Plugin interfaces are part of the declarative contract." -- Plugins remain on the schema surface aligned to the same typed-port model; no root registries.


#### REQ-34: functional (must)
Ask-node prompt assembly MUST remain a layered model: workflow/phase context injection, actor role prompt, the node's `prompt` field, and edge-delivered input data. The nested YAML rewrite must not flatten or hide those prompt inputs.

**Citations:**
- [decision] D-GR-35: "Align AskNode field names to Design (prompt instead of task/context_text)." -- The `prompt` field is the canonical AskNode prompt surface; `task` and `context_text` are not valid field names.


#### REQ-35: functional (must)
The schema MUST preserve hybrid data flow: local movement via edges, phase and node context selection via `context_keys`, and existing artifact-oriented persistence semantics where explicitly modeled. Nested phase containment does not justify adding new root registries such as `stores` to carry runtime state.

**Citations:**
- [decision] D-3: "Hybrid edge flow plus persistent artifacts/context remains part of the data model." -- The rewrite changes serialization structure, not the underlying data-flow capabilities.


#### REQ-36: functional (should)
Cost configuration MUST remain attachable at workflow, phase, and node levels so the declarative contract can carry pricing metadata, caps, and alert thresholds without coupling the schema to any one UI.

**Citations:**
- [decision] D-13: "Cost tracking belongs in the workflow representation." -- The schema must still carry cost metadata even though the rewrite focuses on containment and interfaces.


#### REQ-37: functional (must)
The schema MUST version both the wire format (`schema_version`) and the content (`workflow_version`) so consumers can distinguish schema evolution from ordinary workflow edits.

**Citations:**
- [decision] D-14: "Schema versioning and workflow versioning are distinct." -- The canonical contract still needs explicit version fields after the interface rewrite.


#### REQ-38: functional (must)
Port typing MUST remain explicit through `type_ref` or `schema_def` with strict mutual exclusion. This applies to phase ports, node ports, hook ports, and `BranchNode.outputs` (each `BranchOutputPort` must carry exactly one of `type_ref` or `schema_def`), and type-chain validation must work across nested phases and cross-phase edges.

**Citations:**
- [decision] R7-1: "Port definitions use `type_ref` XOR `schema_def`." -- The type system contract extends to BranchOutputPort entries; BranchNode.paths is replaced by BranchNode.outputs.


#### REQ-39: functional (must)
The schema package MUST generate JSON Schema via `model_json_schema()`, and `/api/schema/workflow` MUST be the canonical composer-facing delivery path for that schema. Static `workflow-schema.json` remains a build/test artifact only and MUST NOT be treated as the editor's runtime source of truth.

**Citations:**
- [decision] D-GR-22: "`/api/schema/workflow` is canonical; static `workflow-schema.json` is build/test only." -- This is the cycle-4 contract that resolves the remaining SF-1/SF-5/SF-6 split.


#### REQ-40: functional (must)
Validation MUST reject stale contract variants and structural violations, including flat top-level node assumptions, malformed `source`/`target` refs, hook edges with transforms, serialized `port_type`, separate hook sections, invalid nested containment, unresolved refs, type mismatches, invalid mode configs, unknown branch output ports, stale actor discriminators or values, unapproved root additions such as `stores` or `plugin_instances`, and rejected branch fields such as `switch_function`. The stale exclusive-routing BranchNode shape (`condition_type`, `condition`, top-level `paths`) is also rejected — authors must use the per-port `outputs` model.

**Citations:**
- [decision] D-GR-35: "output_field is fully removed; switch_function remains rejected; stale condition_type/condition/paths shape is superseded by per-port outputs." -- Validation is where the D-GR-35 contract actively prevents both old branch shapes from surviving.


#### REQ-41: functional (must)
The declarative schema module MUST remain additive to the existing imperative `iriai-compose` subclass API. Introducing nested YAML, edge-only hook serialization, and canonical schema delivery MUST NOT break the current runtime ABCs or imperative workflows.

**Citations:**
- [decision] scope constraint — Backward compatibility with iriai-compose's existing Python subclass API: "The declarative format is additive, not a replacement." -- Backward compatibility is an explicit scope constraint and remains mandatory.


#### REQ-42: functional (must)
The litmus test remains unchanged: the planning, develop, and bugfix workflows from `iriai-build-v2` MUST be fully translatable, representable, and runnable through the nested phase contract, with three atomic node types, phase modes, hook edges, templates, and plugins.

**Citations:**
- [decision] scope constraint — iriai-build-v2 workflows must be fully translatable, representable, and runnable: "Planning, develop, and bugfix workflows remain the completeness test." -- The rewrite cannot reduce expressiveness relative to the agreed project scope.


#### REQ-43: functional (must)
Schema structure is no longer architect-deferred. Phases MUST be saveable, importable, inline-creatable, detachable, and reusable while preserving the canonical nested YAML contract of `phases[].nodes` and `phases[].children`.

**Citations:**
- [decision] D-GR-22: "Nested YAML phase containment is authoritative." -- Cycle 4 explicitly removed the remaining structure ambiguity.


#### REQ-44: security (must)
Expression evaluation security is a formal contract: AST allowlist, blocked builtins, bounded size/complexity, timeout, and defined scope contexts. All `BranchNode` per-port conditions are expression strings subject to this sandbox contract. The sandbox also applies to `transform_fn` on data edges and expression-backed phase mode configs.

**Citations:**
- [decision] D-GR-35: "Per-port conditions are expressions only — no output_field mode per port." -- All BranchNode conditions are now expressions; the sandbox applies uniformly. output_field is removed so there is no non-evaluated branch path remaining.


#### REQ-45: functional (must)
Universal port-definition rules MUST apply everywhere in the nested model: all input/output/hook port maps and `BranchNode.outputs` (each `BranchOutputPort`) use the same typed-port contract (`type_ref` XOR `schema_def`), support YAML shorthand, and preserve type information through save/load/export/import without relying on serialized `port_type`.

**Citations:**
- [decision] R7-1: "Typed port definitions are universal across the schema." -- The nested rewrite should consolidate, not fragment, port-definition rules. BranchNode.paths is replaced by BranchNode.outputs with BranchOutputPort entries following the same contract.


#### REQ-46: functional (must)
This PRD-defined wire shape is the canonical contract for downstream SF-1 design, plan, runner, backend, editor, and migration artifacts. No consumer may add alternate actor discriminators, extra root registries, alternate branch routing fields, or runtime `workflow-schema.json` consumption without a later approved decision.

**Citations:**
- [decision] D-GR-30: "SF-1 PRD is canonical; plan and system-design must match exactly." -- Cycle-5 feedback established this PRD as the enforcement boundary for downstream artifact drift.


### SF-2: DAG Loader & Runner
<!-- SF: declarative-schema -->

#### AC-9
- **User Action:** Developer authors a workflow YAML with top-level `phases`, nested `phases[].nodes`, nested `phases[].children`, and top-level cross-phase `edges`, then validates and round-trips it.
- **Expected:** `WorkflowConfig.model_validate()` accepts the document, `model_json_schema()` reflects the nested structure, and save/load preserves phase containment without flattening nodes to the workflow root or adding extra root registries.
- **Not Criteria:** Validation accepts top-level nodes, drops nested child phases, rewrites the document into a flat graph, or tolerates stray root `stores` / `plugin_instances`.
- **Requirements:** REQ-24, REQ-28, REQ-39, REQ-43, REQ-46
- **Citations:** - [decision] D-GR-22: "Nested YAML is authoritative." -- This acceptance criterion verifies the main containment rewrite.


#### AC-10
- **User Action:** Developer defines an `AskNode` with an actor reference, a `prompt` field, and typed outputs inside `phases[].nodes`.
- **Expected:** Validation succeeds, the actor reference resolves to the existing runtime actor model, the `prompt` field is accepted as the node's primary prompt surface, and the node remains an atomic prompt/response primitive inside the nested phase container.
- **Not Criteria:** The node requires `task` or `context_text` instead of `prompt`, a sub-DAG body, runtime credentials, or an alternate non-atomic execution model.
- **Requirements:** REQ-25, REQ-26, REQ-34
- **Citations:** - [decision] D-GR-35: "Align AskNode field names to Design (prompt instead of task/context_text)." -- The `prompt` field is the canonical AskNode prompt surface.


#### AC-11
- **User Action:** Developer defines both a phase-local edge and a cross-phase edge using `source`/`target` dot notation plus `$input` and `$output` boundary refs.
- **Expected:** Validation accepts `source`/`target` references, stores phase-local edges with the owning phase, stores cross-phase edges at workflow level, and does not require any serialized `port_type`.
- **Not Criteria:** Edges require `from_node`/`from_port`, serialize a `port_type`, or ignore `$input`/`$output` boundaries.
- **Requirements:** REQ-24, REQ-27, REQ-28
- **Citations:** - [decision] D-GR-22: "No serialized port_type; nested phase containment is authoritative." -- Phase-local and cross-phase edges must follow the canonical dot-notation contract.


#### AC-12
- **User Action:** Developer defines a fold-mode phase with `nodes`, a nested child phase under `children`, and the required fold configuration.
- **Expected:** Validation succeeds and the fold phase preserves both its contained nodes and its nested child phase without introducing a standalone Fold node type.
- **Not Criteria:** The phase must be represented as a separate Fold node, or nested content is rejected because it is not flat.
- **Requirements:** REQ-28, REQ-29, REQ-43
- **Citations:** - [decision] R3-2: "Execution modes belong on phases." -- This criterion checks that fold remains a phase concern inside the nested model.


#### AC-13
- **User Action:** Developer defines a loop-mode phase with `max_iterations: 5` and wires `condition_met` and `max_exceeded` to different targets.
- **Expected:** Both exit ports validate as routable outputs on the phase and remain addressable through the normal edge contract.
- **Not Criteria:** Only one exit port is available, or `max_exceeded` is merged into the normal exit path.
- **Requirements:** REQ-30
- **Citations:** - [decision] R3-4: "Loop mode exposes two exit ports." -- This criterion verifies the explicit loop termination contract.


#### AC-14
- **User Action:** Developer defines a loop-mode phase with a condition and no `max_iterations`.
- **Expected:** Validation succeeds, `condition_met` is active, and `max_exceeded` remains part of the phase contract without requiring a safety cap in every loop.
- **Not Criteria:** Loop mode is rejected for omitting `max_iterations`, or the dual-exit contract disappears when the cap is omitted.
- **Requirements:** REQ-30
- **Citations:** - [decision] R3-4: "Dual exits are part of the stable loop model." -- The schema should preserve loop semantics with or without an explicit safety cap.


#### AC-15
- **User Action:** Developer defines a map-mode phase with collection and `max_parallelism` settings inside the nested phase model.
- **Expected:** Validation succeeds and the phase expresses parallel fan-out without introducing Map as a standalone node type.
- **Not Criteria:** Parallel fan-out requires a separate Map node, or nested structure is disallowed for map-mode phases.
- **Requirements:** REQ-28, REQ-29
- **Citations:** - [decision] R3-2: "Map semantics live on phases." -- This verifies that phase-mode modeling survives the rewrite.


#### AC-16
- **User Action:** Developer wires a phase `on_start` hook to a plugin node using an ordinary edge like `source: "phase_a.on_start"`.
- **Expected:** Validation accepts the edge, infers that it is a hook edge from the source port container, and forbids a transform without requiring any serialized `port_type`.
- **Not Criteria:** The workflow needs a separate hook section, a hook callback list, or `port_type: "hook"` in YAML.
- **Requirements:** REQ-27, REQ-31, REQ-40
- **Citations:** - [decision] D-GR-22: "Hook wiring stays edge-based with no serialized `port_type`." -- This is one of the direct stale-contract fixes from cycle 4.


#### AC-17
- **User Action:** Developer wires a node `on_end` hook to a plugin node using an ordinary edge.
- **Expected:** Validation accepts the edge, infers hook behavior from the source port, and preserves the same edge representation used for data flow.
- **Not Criteria:** Node hook edges require a dedicated hook edge class, a separate hook section, or a serialized hook discriminator.
- **Requirements:** REQ-27, REQ-31, REQ-40
- **Citations:** - [decision] D-GR-22: "Hooks are ordinary edges plus port resolution." -- Both phase and node hooks must use the same serialized model.


#### AC-18
- **User Action:** Developer creates an edge whose source and target port types do not match across nested phases.
- **Expected:** Validation fails with a clear error naming the edge and the incompatible types.
- **Not Criteria:** The mismatch is silently accepted, or the error loses the edge context because the graph is nested.
- **Requirements:** REQ-38, REQ-40, REQ-45
- **Citations:** - [decision] R7-1: "Typed ports remain the basis for edge compatibility checks." -- Type mismatch reporting must continue to work after the nested rewrite.


#### AC-19
- **User Action:** Migration engineer encodes the `iriai-build-v2` planning PM patterns using nested phases, phase-local nodes, per-port branch conditions, and hook edges.
- **Expected:** The workflow validates without requiring extra node kinds or imperative escape hatches, and the major planning patterns remain representable.
- **Not Criteria:** Translation requires top-level nodes, compound Map/Fold/Loop nodes, or `switch_function` to model existing workflows.
- **Requirements:** REQ-26, REQ-28, REQ-29, REQ-31, REQ-42
- **Citations:** - [decision] scope constraint — iriai-build-v2 workflows must be fully translatable, representable, and runnable: "Existing workflows remain the completeness test." -- This acceptance criterion verifies the scope-level litmus test.


#### AC-20
- **User Action:** Developer nests a map child phase inside a fold parent using `children`.
- **Expected:** Validation succeeds and preserves the child phase under the parent's `children` array.
- **Not Criteria:** Nested phases are rejected, flattened to siblings, or serialized under a stale alternate field.
- **Requirements:** REQ-28, REQ-29, REQ-43
- **Citations:** - [decision] D-GR-22: "Nested child phases serialize under the phase container." -- This directly checks the canonical nested containment shape.


#### AC-21
- **User Action:** Developer defines both an `agent` actor and a `human` actor using `actor_type` in the same workflow.
- **Expected:** Both validate against the declarative actor union without requiring environment-specific runtime secrets.
- **Not Criteria:** The schema accepts `type: interaction` as a wire alias, rejects one actor class, or leaks runtime credential details into workflow YAML.
- **Requirements:** REQ-25, REQ-46
- **Citations:** - [decision] D-GR-30: "actor_type: agent|human only — no interaction alias." -- The criterion explicitly verifies that interaction is not accepted as a wire value.


#### AC-22
- **User Action:** Developer creates an inter-phase cycle outside loop mode in the nested graph.
- **Expected:** Validation fails and reports the cycle path clearly.
- **Not Criteria:** The cycle passes because containment is nested, or the error only surfaces at runtime.
- **Requirements:** REQ-40
- **Citations:** - [decision] D-GR-22: "Nested containment is canonical, not an excuse to weaken structural validation." -- The validator still needs to enforce graph correctness under the new layout.


#### AC-23
- **User Action:** Developer attaches `transform_fn` to a hook edge.
- **Expected:** Validation fails because hook edges are fire-and-forget lifecycle triggers inferred from the source hook port.
- **Not Criteria:** Hook edges accept transforms because `port_type` is absent, or the system cannot tell a hook edge from a data edge.
- **Requirements:** REQ-27, REQ-31, REQ-40
- **Citations:** - [decision] D-GR-22: "Hooks are inferred from port resolution, not an explicit edge type." -- The contract still forbids transforms on hook edges even without serialized `port_type`.


#### AC-24
- **User Action:** Composer backend serves `/api/schema/workflow`, and the frontend fetches it at runtime before rendering the editor.
- **Expected:** The returned JSON Schema reflects the live nested phase contract, and the frontend does not need a bundled static schema as its runtime source of truth.
- **Not Criteria:** The editor depends on a checked-in `workflow-schema.json` at runtime or drifts from the backend schema until a rebuild.
- **Requirements:** REQ-39
- **Citations:** - [decision] D-GR-22: "`GET /api/schema/workflow` is already described as the canonical schema endpoint." -- The revised PRD aligns SF-1 delivery to the existing backend contract.


#### AC-25
- **User Action:** Developer writes an expression longer than 10,000 characters into an expression-backed schema field.
- **Expected:** Validation fails with a specific size-limit error.
- **Not Criteria:** Oversized expressions pass or only fail later at runtime.
- **Requirements:** REQ-44
- **Citations:** - [decision] R6-1: "Expression fields are bounded by an explicit sandbox contract." -- The schema must continue to enforce the expression safety ceiling.


#### AC-26
- **User Action:** SF-2 implementer reads the schema PRD to build the shared expression evaluator.
- **Expected:** The PRD specifies that all `BranchNode` per-port conditions are expression strings subject to the full sandbox contract (AST allowlist, blocked builtins, size/complexity bounds, timeout). The contract is clear that `switch_function` is rejected and `merge_function` is not an expression field.
- **Not Criteria:** The contract says expressions are restricted without defining the restrictions, or conflates the gather `merge_function` with a Python-evaluated routing function.
- **Requirements:** REQ-44
- **Citations:** - [decision] D-GR-35: "Per-port conditions are expressions only." -- All conditions are now expressions; the sandbox applies uniformly. The implementer needs to know merge_function is a gather hook, not a sandboxed expression.


#### AC-27
- **User Action:** Developer defines a phase, node, or hook port using only `schema_def`.
- **Expected:** Validation succeeds and uses the inline JSON Schema for type-flow checks, including hook-port payload typing.
- **Not Criteria:** Validation rejects inline schema-only hook ports or silently strips the schema.
- **Requirements:** REQ-31, REQ-38, REQ-45
- **Citations:** - [decision] R7-1: "Ports may be typed by `schema_def` instead of `type_ref`." -- Inline schemas remain part of the typed-port contract; hook ports are included.


#### AC-28
- **User Action:** Developer defines a port or branch output port with both `type_ref` and `schema_def`.
- **Expected:** Validation fails because exactly one typing mechanism must be present.
- **Not Criteria:** Both fields are accepted simultaneously.
- **Requirements:** REQ-38, REQ-45
- **Citations:** - [decision] R7-1: "Port typing uses strict mutual exclusion." -- This is a core typed-port invariant; BranchOutputPort entries follow the same XOR rule.


#### AC-29
- **User Action:** Developer defines a port or branch output port with neither `type_ref` nor `schema_def`.
- **Expected:** Validation fails because the port has no declared type information.
- **Not Criteria:** Untyped ports pass silently in places where the contract requires explicit typing.
- **Requirements:** REQ-38, REQ-45
- **Citations:** - [decision] R7-1: "Ports must resolve to exactly one typed definition." -- The same XOR rule also rejects missing type declarations in BranchOutputPort entries.


#### AC-30
- **User Action:** Developer uses YAML shorthand with a bare string type name in a port definition.
- **Expected:** The shorthand is accepted and normalized as a typed-port definition without requiring verbose JSON syntax.
- **Not Criteria:** Bare-string shorthand is rejected or creates ambiguous state that cannot round-trip cleanly.
- **Requirements:** REQ-45
- **Citations:** - [decision] R7-1: "The schema supports concise typed-port authoring." -- YAML ergonomics remain important even after the containment rewrite.


#### AC-31
- **User Action:** Developer defines a `BranchNode` with multiple output ports each carrying a `condition` expression, then connects edges from `branch_id.approved`, `branch_id.needs_revision`, and `branch_id.rejected`.
- **Expected:** Validation succeeds, each output port key becomes an addressable port name, all per-port conditions are subject to the expression sandbox, and multiple ports may fire concurrently if their conditions are satisfied (non-exclusive fan-out).
- **Not Criteria:** Branch routing collapses to a single-path exclusive model, requires a top-level `condition_type` / `condition` / `paths` shape, or treats only one condition as active.
- **Requirements:** REQ-26, REQ-27, REQ-44
- **Citations:** - [decision] D-GR-35: "D-GR-12 per-port model is the single authority. Non-exclusive fan-out; each output port carries its own condition." -- This AC is the primary verification of the D-GR-35 branch model change.


#### AC-32
- **User Action:** Developer defines a `BranchNode` with a `merge_function` string and two output ports, where the gather node receives inputs from parallel upstream branches before evaluating port conditions.
- **Expected:** Validation succeeds, `merge_function` is accepted as an optional gather hook string on `BranchNode`, and the resulting merged payload is then available to per-port condition evaluation.
- **Not Criteria:** `merge_function` is rejected as an invalid field, or it is treated as a routing function equivalent to the removed `switch_function`.
- **Requirements:** REQ-26, REQ-44
- **Citations:** - [decision] D-GR-35: "`merge_function` is valid for gather semantics when multiple inputs converge." -- merge_function was previously rejected; D-GR-35 makes it valid for gather. This AC verifies that reversal.


#### AC-33
- **User Action:** Developer adds `switch_function` to a branch definition.
- **Expected:** Validation fails with an error directing the author to use the per-port `outputs` model with individual `condition` expressions on each port.
- **Not Criteria:** `switch_function` is accepted as an alias or tolerated for backward compatibility.
- **Requirements:** REQ-26, REQ-40, REQ-46
- **Citations:** - [decision] D-GR-35: "`switch_function` remains rejected — not a valid field." -- switch_function is explicitly and permanently invalid in the D-GR-35 model.


#### AC-34
- **User Action:** Developer defines an edge from `branch_id.unknown_port` where `unknown_port` is not present in the branch node's `outputs`.
- **Expected:** Validation fails and reports the invalid port plus the set of valid branch output port names.
- **Not Criteria:** Unknown branch ports pass because the graph is nested, or the error omits the available port names.
- **Requirements:** REQ-26, REQ-27, REQ-40
- **Citations:** - [decision] D-GR-35: "Per-port model: output port keys in `outputs` are the authoritative port names." -- The edge model must enforce branch output port names as real ports; `paths` terminology replaced by `outputs`.


#### AC-35
- **User Action:** Developer adds `stores` or `plugin_instances` to the workflow root and validates the file.
- **Expected:** Validation fails with an unsupported-root-field error naming the rejected key and preserves the rest of the canonical root shape.
- **Not Criteria:** Extra root registries are silently ignored, stripped, or treated as part of the approved wire contract.
- **Requirements:** REQ-24, REQ-40, REQ-46
- **Citations:** - [decision] D-GR-30: "The workflow root field set is closed; unapproved registries are invalid." -- This criterion directly enforces the no-stores/no-plugin_instances contract at the root level.


#### AC-36
- **User Action:** Developer defines a `BranchNode` using the stale `condition_type` / `condition` / `paths` shape from before D-GR-35.
- **Expected:** Validation fails, naming `condition_type`, `condition`, and `paths` as unsupported top-level branch fields and directing the author to the per-port `outputs` model.
- **Not Criteria:** The stale exclusive-routing shape is silently accepted or partially interpreted.
- **Requirements:** REQ-26, REQ-40, REQ-46
- **Citations:** - [decision] D-GR-35: "BranchNode semantics replaced by D-GR-12 per-port model. stale condition_type/condition/paths shape is superseded." -- The old model must fail fast with clear guidance to the new per-port model.


### SF-2: DAG Loader & Runner
<!-- SF: declarative-schema -->

#### J-6: Define a nested declarative workflow from scratch
- **Actor:** Platform developer authoring workflow YAML
- **Path:** happy
- **Preconditions:** `iriai-compose` declarative models are available and the developer has a text editor plus access to the schema docs.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | The developer creates a new YAML file and writes a top-level `WorkflowConfig` with version fields, actors, top-level `phases`, and workflow-level cross-phase `edges`. | The workflow shape is phase-first rather than node-first, uses the closed root field set, and does not require top-level nodes. | The developer has to flatten nodes to the workflow root, add unapproved root registries, or invent a second top-level graph structure. | [decision] D-GR-22 |
| 2 | The developer defines actor entries and named types used by the workflow. | Actor definitions align to `actor_type: agent|human` while still mapping cleanly to existing runtime actor concepts in `iriai-compose`. | Actor entries require environment secrets, revive `interaction`, or introduce a third unsupported actor family. | [decision] D-GR-30 |
| 3 | The developer defines a top-level phase and places atomic nodes under `phases[0].nodes`. | The phase owns its internal execution elements instead of referencing top-level node IDs. | Nodes must live outside the phase or be referenced only by a detached ID list. | [decision] D-GR-22 |
| 4 | The developer nests a sub-phase under `phases[0].children` to express a contained execution group. | The child phase is serialized inline under the parent phase rather than flattened to a sibling list. | Nested phases are forced into a stale alternate field or moved back to workflow level. | [decision] D-GR-22 |
| 5 | The developer adds `AskNode`, `BranchNode`, and `PluginNode` definitions inside phase `nodes`. For `AskNode` they provide a `prompt` field; for `BranchNode` they provide an `outputs` map where each port carries its own `condition` expression. | Only three atomic node kinds are needed, branch routing uses per-port conditions with non-exclusive fan-out, and `switch_function` / `output_field` do not appear. | Map/Fold/Loop must be authored as extra node types, `switch_function` reappears, or branch routing reverts to the stale exclusive `condition_type`/`condition`/`paths` shape. | [decision] D-GR-35 |
| 6 | The developer wires phase-local and cross-phase connections with `source` and `target` dot notation plus `$input` and `$output` when crossing phase boundaries. | Phase-local edges stay with the owning phase, and cross-phase edges stay at workflow level. | Edges require `from_node`/`to_node` fields or lose boundary information when phases are nested. | [decision] D-GR-22 |
| 7 | The developer wires lifecycle behavior from `on_start` and `on_end` ports using ordinary edges. | Hook behavior is represented entirely through edges and port resolution with no extra hook section or serialized edge discriminator. | Hooks must be declared in a separate callback list or serialized with `port_type: hook`. | [decision] D-GR-22 |
| 8 | The developer loads the YAML with `yaml.safe_load()` and `WorkflowConfig.model_validate()`. | Validation checks nested containment, typed ports, per-port branch conditions, hook-edge rules, and stale-field rejection before any runner logic executes. | Broken nested structure or stale fields are only discovered later in the editor or runner. | [decision] D-GR-22 |

- **Outcome:** A complete nested workflow YAML validates successfully and is ready for loader execution, editor round-tripping, or migration comparison.
- **Requirements:** REQ-24, REQ-26, REQ-27, REQ-28, REQ-39, REQ-40, REQ-43


#### J-7: Translate `iriai-build-v2` planning and implementation patterns into the nested schema
- **Actor:** Migration engineer converting existing imperative workflows
- **Path:** happy
- **Preconditions:** The engineer has the current `iriai-build-v2` planning/develop sources and the declarative schema contract.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | The engineer models the planning interview/gating logic as loop-mode and sequential phases whose internal nodes live under `nodes` and whose contained execution groups live under `children`. | Imperative phase sequencing maps cleanly into nested declarative phases without introducing extra compound node kinds. | The translation requires flattening every phase to workflow level or introducing special-purpose loop nodes. | [code] iriai-build-v2/src/iriai_build_v2/workflows/planning/workflow.py:24-56 |
| 2 | The engineer models per-subfeature iteration and retry logic with fold/map/loop phase modes and branch nodes inside those phases. | Nested iteration remains expressible through phase modes plus atomic nodes, even when the imperative source loops over subfeatures or retries fixes. | The translation requires standalone Map/Fold/Loop nodes or an imperative escape hatch. | [code] iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py:399-547 |
| 3 | The engineer models review/approve branches using a `BranchNode` with per-port conditions: each outcome port carries its own expression, and multiple outcomes can fire if their conditions are simultaneously satisfied. | Each output port becomes an explicit port name used by normal edges, `switch_function` is not needed, and the non-exclusive fan-out allows parallel routing where the imperative code had multiple simultaneous true branches. | Routing bypasses edges, reverts to the stale exclusive `condition_type`/`condition`/`paths` shape, or requires `switch_function`. | [decision] D-GR-35 |
| 4 | The engineer models setup/publication side effects as hook edges from phase or node lifecycle ports. | Lifecycle behavior is visible in the graph as ordinary edges, not hidden in callback lists. | Artifact publishing or setup logic has to be encoded in a separate hook registry or callback block. | [decision] D-GR-22 |
| 5 | The engineer validates the translated workflow against the schema. | The translated workflow stays representable within the three-node, phase-mode, nested-containment model. | Any required planning/develop pattern falls outside the declarative contract. | [decision] scope constraint — iriai-build-v2 workflows must be fully translatable, representable, and runnable |

- **Outcome:** The translated workflow preserves the meaningful `iriai-build-v2` execution structure while conforming to the new nested YAML contract.
- **Requirements:** REQ-26, REQ-28, REQ-29, REQ-31, REQ-42, REQ-43


#### J-8: Validation rejects stale or structurally invalid schema variants
- **Actor:** Developer or migration engineer loading malformed YAML
- **Path:** failure
- **Preconditions:** A workflow YAML file contains stale fields or structural mistakes.
- **Failure Trigger:** The document uses flat node placement, stale actor or branch fields, unauthorized root additions, serialized `port_type`, separate hook sections, invalid nested containment, or other rejected contract variants.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | The developer loads YAML that places nodes at workflow root, adds `stores` / `plugin_instances`, or nests phases outside `children`. | Validation fails with a structural error explaining that nodes belong under phases, child phases belong under `children`, and the workflow root is closed to unapproved registries. | The loader quietly normalizes the file into an unspecified structure or silently strips the extra root keys. | [decision] D-GR-22 |
| 2 | The developer loads YAML that serializes hook behavior with a separate hook section or `port_type`. | Validation fails with a clear unsupported-field error that directs the author back to edge-based hook serialization. | The stale hook model is tolerated or silently ignored. | [decision] D-GR-22 |
| 3 | The developer loads YAML where a hook edge also defines `transform_fn`. | Validation fails because hook edges are inferred from source hook ports and cannot carry transforms. | The document passes because no explicit hook edge type exists. | [decision] D-GR-22 |
| 4 | The developer loads YAML that uses `type: interaction` on an actor, includes `switch_function` on a branch node, or uses the stale `condition_type`/`condition`/`paths` branch shape. | Validation fails with guidance to use `actor_type: agent|human` and the per-port `outputs` branch model with individual `condition` expressions per port. | Stale actor aliases or stale branch shapes are treated as backward-compatible wire variants. | [decision] D-GR-35 |
| 5 | The developer fixes the stale fields and reruns validation. | The corrected document validates against the canonical nested phase, edge, actor, and per-port branch model. | The developer has to guess which of multiple competing schema variants the system now expects. | [decision] D-GR-22 |

- **Outcome:** Invalid legacy or structurally inconsistent workflow YAML is blocked early with clear guidance toward the canonical schema.
- **Requirements:** REQ-27, REQ-40, REQ-43
- **Related Journey:** J-1


#### J-9: Composer consumes the live schema contract from `/api/schema/workflow`
- **Actor:** Workflow editor frontend developer
- **Path:** happy
- **Preconditions:** The composer backend is running and can import the `iriai-compose` declarative schema package.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | The backend exposes `GET /api/schema/workflow` using the current `WorkflowConfig.model_json_schema()` output. | The endpoint returns the current JSON Schema for the nested workflow contract. | The canonical schema is only available as a checked-in static file or a manually copied artifact. | [decision] D-GR-22 |
| 2 | The frontend fetches `/api/schema/workflow` before rendering editor inspectors and validation rules. | The editor receives a schema that includes nested phase containment and the no-`port_type` edge model. | The editor authors against a stale bundled schema or assumes flat nodes because the live endpoint is ignored. | [decision] D-GR-22 |
| 3 | The frontend renders phase, edge, and branch UI from the fetched schema. | The UI expects `phases[].nodes`, `phases[].children`, `source`/`target` edges, per-port branch conditions on `BranchNode.outputs`, and hook inference via ports rather than a serialized hook discriminator. | The UI renders stale `port_type` controls, a stale exclusive branch UI, or a runtime dependency on `workflow-schema.json`. | [decision] D-GR-35 |
| 4 | The frontend saves and reloads a workflow using the fetched schema contract. | Round-trip preserves nested phase containment and edge-only hook serialization without needing runtime schema patches. | Round-trip only works against an internal editor-only shape that diverges from the live backend schema. | [decision] D-GR-22 |

- **Outcome:** The composer renders and validates against the live, current schema contract instead of a stale static snapshot.
- **Requirements:** REQ-27, REQ-28, REQ-39, REQ-43


#### J-10: Author a loop with explicit success and safety-cap exits
- **Actor:** Workflow author modeling retry or interview logic
- **Path:** happy
- **Preconditions:** The author is editing a loop-mode phase in the declarative schema.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | The author defines a loop-mode phase and wires `condition_met` and `max_exceeded` to different targets. | Both exits are available through normal edge references and can be routed independently. | Loop termination collapses to a single implicit output path. | [decision] R3-4 |
| 2 | The author validates and reloads the loop definition. | The two exit ports remain part of the phase contract after round-trip serialization. | One of the loop exits disappears after save/load because nested serialization normalizes it away. | [decision] R3-4 |

- **Outcome:** Loop-mode workflows can express both normal completion and safety-cap termination without leaving the canonical edge model.
- **Requirements:** REQ-30


#### J-11: Composer detects schema-source drift or endpoint failure instead of silently using stale schema
- **Actor:** Workflow editor frontend developer
- **Path:** failure
- **Preconditions:** The editor is loading its schema contract at runtime.
- **Failure Trigger:** The schema endpoint is unavailable or a stale bundled schema would otherwise be used as a silent fallback.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | The editor attempts to fetch `/api/schema/workflow` and the request fails or returns an unexpected response. | The failure is surfaced as a schema-load problem and blocks or warns on editor initialization. | The editor silently falls back to a stale bundled schema and continues authoring against the wrong contract. | [decision] D-GR-22 |
| 2 | The developer restores the endpoint or retries against a healthy backend. | The editor resumes using the live schema contract from `/api/schema/workflow`. | The editor remains pinned to a stale local schema after the backend is fixed. | [decision] D-GR-22 |

- **Outcome:** Schema-source failures are explicit and recoverable; they do not reintroduce static-schema-first drift.
- **Requirements:** REQ-39, REQ-40
- **Related Journey:** J-4


#### J-12: Migration output using stale hook or branch fields fails fast and is corrected
- **Actor:** Migration engineer validating translated workflow YAML
- **Path:** failure
- **Preconditions:** A translated workflow still contains old `switch_function`, `condition_type`/`condition`/`paths` branch shape, serialized `port_type`, or separate-hook assumptions.
- **Failure Trigger:** Migration emits stale fields instead of the D-GR-22/D-GR-35 nested phase + per-port branch contract.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | The engineer validates the translated YAML and sees errors for stale branch or hook fields. | Validation points directly to `switch_function`, the stale `condition_type`/`condition`/`paths` shape, serialized `port_type`, or invalid hook structure as unsupported by the canonical schema. | The loader partially accepts stale translation output and leaves downstream tools to guess what the schema means. | [decision] D-GR-35 |
| 2 | The engineer rewrites the translation to use `children`, ordinary edges for hooks, and a `BranchNode.outputs` map with per-port `condition` expressions, then validates again. | The corrected translation passes validation and matches the same contract expected by the loader and editor. | Migration keeps a private alternate schema dialect that only one downstream tool understands. | [decision] D-GR-35 |

- **Outcome:** Migration output is either canonical or rejected; stale translation formats cannot persist as an unofficial second schema.
- **Requirements:** REQ-26, REQ-27, REQ-40, REQ-43
- **Related Journey:** J-2


### SF-2: DAG Loader & Runner
#### WorkflowConfig <!-- SF: declarative-schema -->
- **Fields:** schema_version: str, workflow_version: int, name: str, description: Optional[str], metadata: Optional[dict], actors: dict[str, ActorDefinition], phases: list[PhaseDefinition], edges: list[EdgeDefinition], templates: Optional[dict[str, TemplateDefinition]], plugins: Optional[dict[str, PluginInterface]], types: Optional[dict[str, JsonSchema]], cost_config: Optional[WorkflowCostConfig]
- **Constraints:** YAML-first root model; No top-level nodes; Workflow-level edges are cross-phase only; No root `stores` or `plugin_instances`; All refs resolve; Workflow graph is acyclic outside intentional loop semantics
- **New:** yes


#### ActorDefinition <!-- SF: declarative-schema -->
- **Fields:** actor_type: Literal['agent','human'], agent fields: provider, model, role, persistent, context_keys, human fields: identity, channel
- **Constraints:** Discriminator field is `actor_type`; Valid values are only `agent` and `human`; No `interaction` alias; No environment-specific credential fields
- **New:** yes


#### AskNode <!-- SF: declarative-schema -->
- **Fields:** id: str, type: Literal['ask'], actor_ref: str, prompt: str, inputs: dict[str, WorkflowInputDefinition], outputs: dict[str, WorkflowOutputDefinition], hooks: dict[str, WorkflowOutputDefinition], artifact_key: Optional[str], cost: Optional[NodeCostConfig], context_keys: Optional[list[str]]
- **Constraints:** `prompt` is the canonical prompt field; `task` and `context_text` are not valid; `actor_ref` resolves to an `ActorDefinition` in the workflow's `actors` map; Serializes only inside `phases[].nodes`
- **New:** yes


#### BranchNode <!-- SF: declarative-schema -->
- **Fields:** id: str, type: Literal['branch'], merge_function: Optional[str], outputs: dict[str, BranchOutputPort]
- **Constraints:** At least two output ports in `outputs`; Non-exclusive fan-out: multiple ports may fire if their conditions are satisfied; `merge_function` is an optional gather hook — not a routing function; invoked when multiple inputs converge before condition evaluation; `switch_function` is not a valid field; `output_field` is not a valid field; Stale top-level `condition_type`, `condition`, and `paths` fields are rejected; Serializes only inside `phases[].nodes`
- **New:** yes


#### BranchOutputPort <!-- SF: declarative-schema -->
- **Fields:** condition: str, type_ref: Optional[str], schema_def: Optional[dict], description: Optional[str]
- **Constraints:** `condition` is always an expression string subject to the security sandbox (AST allowlist, blocked builtins, size/complexity bounds, timeout); Exactly one of `type_ref` or `schema_def` (same XOR rule as all other typed ports); Port key in the parent `outputs` map becomes the addressable output port name on the BranchNode
- **New:** yes


#### PhaseDefinition <!-- SF: declarative-schema -->
- **Fields:** id: str, name: str, mode: Literal['sequential','map','fold','loop'], mode_config: Optional[ModeConfig], inputs: dict[str, WorkflowInputDefinition], outputs: dict[str, WorkflowOutputDefinition], hooks: dict[str, WorkflowOutputDefinition], nodes: list[NodeDefinition], children: list[PhaseDefinition], edges: list[EdgeDefinition], context_keys: list[str], cost: Optional[PhaseCostConfig], metadata: Optional[dict]
- **Constraints:** Primary execution container; `nodes` serialize under `phases[].nodes`; Nested phases serialize under `phases[].children`; Phase-local edges stay with the phase; Loop mode exposes `condition_met` and `max_exceeded`; `mode_config` is a single discriminated-union field; separate flat mode config fields are not valid
- **New:** yes


#### ModeConfig <!-- SF: declarative-schema -->
- **Fields:** MapModeConfig: mode=Literal['map'], collection: str, max_parallelism: Optional[int], FoldModeConfig: mode=Literal['fold'], collection: str, accumulator_init: Any, LoopModeConfig: mode=Literal['loop'], condition: str, max_iterations: Optional[int], SequentialModeConfig: mode=Literal['sequential'], metadata: Optional[dict]
- **Constraints:** Discriminated union with `mode` as discriminator field; `mode_config` on PhaseDefinition typed as Union[MapModeConfig, FoldModeConfig, LoopModeConfig, SequentialModeConfig]; Exactly one mode-specific config variant applies per phase; Non-sequential modes require their matching config variant; Separate flat fields (`map_config`, `fold_config`, `loop_config`, `sequential_config`) are not valid on PhaseDefinition
- **New:** yes


#### EdgeDefinition <!-- SF: declarative-schema -->
- **Fields:** source: str, target: str, transform_fn: Optional[str], description: Optional[str]
- **Constraints:** `source` and `target` use dot notation or `$input`/`$output` boundary refs; No serialized `port_type`; Hook-vs-data determined by source port container; Hook edges must not define `transform_fn`
- **New:** yes


#### PortDefinition <!-- SF: declarative-schema -->
- **Fields:** type_ref: Optional[str], schema_def: Optional[dict], description: Optional[str], required: Optional[bool]
- **Constraints:** Exactly one of `type_ref` or `schema_def`; Applies uniformly to inputs, outputs, hooks, and `BranchNode.outputs` (BranchOutputPort extends this contract with a `condition` field)
- **New:** yes


#### HookPortEvent <!-- SF: declarative-schema -->
- **Fields:** source_id: str, source_type: str, event: str, status: str, result: Optional[Any], error: Optional[str], timestamp: str, duration_ms: Optional[int], cost_usd: Optional[float]
- **Constraints:** Produced by hook ports and delivered through ordinary edges
- **New:** yes


#### TemplateDefinition <!-- SF: declarative-schema -->
- **Fields:** id: str, name: str, description: Optional[str], phase: PhaseDefinition | Ref, bind: Optional[dict[str, Any]]
- **Constraints:** Expands into the same nested phase contract as inline phases
- **New:** yes


#### PluginInterface <!-- SF: declarative-schema -->
- **Fields:** id: str, name: str, description: Optional[str], inputs: dict[str, WorkflowInputDefinition], outputs: dict[str, WorkflowOutputDefinition], config_schema: dict
- **Constraints:** Plugin nodes stay within the shared typed-port contract; No separate root `plugin_instances` registry required
- **New:** yes


### From: dag-loader-runner
