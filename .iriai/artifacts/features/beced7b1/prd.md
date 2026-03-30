# iriai-compose Workflow Creator -- Compiled PRD

## Overview

Two deliverables: (1) Extend iriai-compose with a declarative DAG-based workflow format, loader, run() entry point, and custom testing framework. (2) Build iriai-workflows, a visual workflow composer webapp with Windows XP / MS Paint aesthetic, plus a tools.iriai.app hub for tool discovery. The declarative format uses a minimal primitive set (Ask, Map, Fold, Loop, Branch, Plugin) with typed edges, transforms, hooks, and phase groupings. The litmus test: iriai-build-v2's planning, develop, and bugfix workflows must be fully translatable, representable, and runnable in the new system.

## Problem Statement

Agent workflows in iriai-build-v2 are defined imperatively in Python — tightly coupled to implementation details, non-portable across projects, and invisible to users. Creating or modifying workflows requires deep knowledge of the iriai-compose Python subclass API. There is no way to visually design, inspect, share, or test workflow configurations. This limits workflow iteration speed and prevents the broader iriai platform developer community from building custom agent orchestration flows.

## Target Users

iriai platform developers on hobby tier and above. Power users who build agent orchestration workflows for software development automation. They understand agent roles, prompts, and multi-step execution but should not need to write Python to define workflows.

## Requirements

### Broad Requirements
<!-- SF: broad -->

#### REQ-1: Declarative Format (must)
YAML-primary DAG format representing workflows as typed nodes and edges. Six primitive node types: Ask (atomic agent invocation), Map (parallel fan-out over collection), Fold (sequential iteration with accumulator), Loop (repeat until condition), Branch (conditional routing), Plugin (external service call). Edges carry typed data with optional named transform functions. Nodes have on_start/on_done hooks as named registered functions. YAML primary, JSON accepted (yaml.safe_load handles both).

**Citations:**
- [decision] D-1: "DAG with primitives, limit specialized subfunctions" -- User chose DAG approach over full declarative patterns or templates
- [decision] D-2: "Both Map and Fold primitives" -- User confirmed need for both parallel fan-out and sequential accumulation with context
- [decision] D-3: "Hooks on nodes for side effects" -- User chose hooks over separate side-effect nodes or edge properties
- [code] iriai-compose/iriai_compose/tasks.py: "Ask, Interview, Gate, Choose, Respond task types" -- Existing task types decompose into Ask primitive compositions


#### REQ-2: Declarative Format (must)
Phases as named groups of nodes with their own on_start/on_done hooks and skip conditions (e.g., skip phase if artifact already exists). Phases enforce sequential boundaries in the DAG and are represented as visual bounding boxes in the composer. Phases are saveable as reusable templates in a Phases Library.

**Citations:**
- [decision] D-4: "Phases must be included on account of their start/stop hooks/conditionals" -- User confirmed phases are not just cosmetic — they carry execution semantics
- [code] iriai-build-v2/src/iriai_build_v2/workflows/planning/workflow.py: "PlanningWorkflow.build_phases() returns 6 sequential phases" -- Existing workflows use phases as sequential execution boundaries


#### REQ-3: Declarative Format (must)
Edge transforms as named pure functions (no agent calls) that reshape data between nodes. Four categories: schema transforms (serialize/deserialize Pydantic models), context assembly (merge multiple artifacts into prompts with tiered filtering), filtering/selection (choose which data flows based on conditions), and formatting (cosmetic reshaping like feedback formatting, URL injection). Transforms are registered by name and referenced in YAML.

**Citations:**
- [code] iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py: "_build_subfeature_context() tiered merge, _format_feedback(), to_str()" -- These imperative transforms must become declarative edge transforms
- [decision] D-5: "Pure transforms belong on edges, agent transforms are nodes" -- User confirmed the distinction between pure transforms and agent-powered transforms


#### REQ-4: Declarative Format (must)
Plugins as first-class DAG participants. Services like hosting, workspace management, and preview servers are configured as plugins in the workflow composer and usable as nodes in the DAG. Each plugin declares its interface (inputs, outputs, configuration schema). The runner provides plugin implementations at execution time. Users can define custom plugins.

**Citations:**
- [decision] D-6: "Plugins should be configurable in the workflow composer and plugged into the DAG" -- User chose plugins as first-class nodes over implicit services or hooks
- [code] iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/env_setup.py: "LaunchPreviewServerTask, workspace_manager.setup_feature_workspace()" -- Existing service integrations must become declarative plugin nodes


#### REQ-5: Declarative Format (must)
Cost configuration in the schema — budget caps, model pricing references, and alert thresholds per node/phase. No runtime cost UI in the composer; this is metadata for future runners to enforce and report on. Every Ask node naturally produces cost data via the Claude Agent SDK's ResultMessage (total_cost_usd, usage).

**Citations:**
- [decision] D-7: "Cost tracking built into workflow representation, not displayed in builder UI" -- User decided cost is a schema concern for future runners, not a composer UI feature
- [research] Claude Agent SDK ResultMessage: "total_cost_usd, usage dict with input_tokens/output_tokens" -- SDK provides cost data automatically per invocation


#### REQ-6: Declarative Format (must)
Schema versioning support. Each workflow config has a version identifier. The format supports diffing between versions. Future runners use version metadata for hot-swap decisions. The composer maintains version history per workflow.

**Citations:**
- [decision] D-8: "Hot-swap not a builder concern — builder just produces versioned configs" -- User chose to keep hot-swap as a runner concern, builder just versions


#### REQ-7: Runtime (must)
Top-level run() function in iriai-compose that any consumer can call: load YAML, hydrate into executable DAG, execute against provided runtimes and workspaces. This is the primary entry point for running declarative workflows. Any project importing iriai-compose gets this capability.

**Citations:**
- [decision] D-9: "run() function in iriai-compose, not exposed as separate validate/dry-run methods" -- User wants a single entry point any app can call to run schema-defined workflows
- [code] iriai-compose/iriai_compose/runner.py: "DefaultWorkflowRunner.execute_workflow()" -- Existing runner provides execution infrastructure; run() wraps YAML loading + execution


#### REQ-8: Runtime (must)
Custom testing framework in iriai-compose (iriai_compose.testing) built alongside the schema during development. Validates schema correctness (structural, type flow across edges). Runs workflows against mock/echo runtimes to verify execution paths. Asserts that specific nodes get reached, artifacts get produced, branches take expected paths. Serves as the regression suite proving the litmus test passes.

**Citations:**
- [decision] D-10: "Custom testing framework built as we develop the schema" -- User wants purpose-built testing, not generic validation
- [code] iriai-compose/tests/conftest.py: "MockAgentRuntime records calls with role, prompt, output_type" -- Existing mock infrastructure can be extended for the testing framework


#### REQ-9: Runtime (must)
Migration of iriai-build-v2's three workflows (planning, develop, bugfix) from imperative Python to declarative YAML. This serves as both the litmus test for format completeness and the first real content in the system. Each workflow must be fully translatable, representable, and produce identical execution behavior when run through the new system.

**Citations:**
- [decision] D-11: "Migration plan for converting existing iriai-build-v2 workflows" -- User explicitly requested migration as a requirement, not just aspirational
- [code] iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/pm.py: "per_subfeature_loop, gate_and_revise, compile_artifacts, interview_gate_review" -- Most complex workflow patterns that must be representable in declarative format


#### REQ-10: Platform (must)
Tools hub at tools.iriai.app — minimal authenticated launcher page. Reads dev_tier claim from auth-service JWT (hobby or pro). Displays grid of tool cards, tier-gated. Workflow composer available to hobby+ tier. Links to individual tool URLs. Uses auth-react for authentication.

**Citations:**
- [decision] D-12: "tools.iriai.app with minimal launcher, tier-gated via JWT dev_tier" -- User defined the platform entry point and tier gating model
- [code] platform/auth/auth-service/app/routers/oauth.py:1196: "dev_tier: user.dev_tier in token_claims" -- JWT already includes dev_tier claim — no auth-service changes needed


#### REQ-11: Composer App (must)
Workflow composer as a separate webapp (React + FastAPI + SQLite). Windows XP / MS Paint aesthetic matching the deploy-console design system (purple gradients, 3D beveled effects, frosted glass taskbar). Auth via homelocal auth-react (frontend) and auth-python (backend). Deployed on Railway.

**Citations:**
- [decision] D-13: "Design inspired by MS Paint / current dev platform" -- User specified Windows XP aesthetic matching deploy-console
- [code] platform/deploy-console/deploy-console-frontend/src/app/styles/windows-xp.css: "XP-style inset/outset borders, purple gradients, frosted glass taskbar" -- Existing design system to match/extend


#### REQ-12: Composer App (must)
Workflows List page — landing page showing grid/list of saved workflow configs. Each card shows name, description, last modified, version count. Actions: create new, duplicate, import YAML, delete, search/filter.

**Citations:**
- [decision] D-14: "Screen map confirmed with workflows list as landing page" -- User approved the screen map structure


#### REQ-13: Composer App (must)
Workflow Editor — primary screen with dual-pane layout. Visual DAG canvas (React Flow) as the primary editing surface. Collapsible YAML pane for inspection and power-user editing (secondary, not featured). Node palette sidebar with all 6 primitives plus saved custom tasks and phases. Phase grouping as visual bounding boxes. Node inspector panel with context-specific configuration per node type. Edge inspector with transform selection and type annotations. Inline sub-canvases for Map/Fold/Loop. Toolbar with save, export YAML, validate, version history, undo/redo.

**Citations:**
- [decision] D-15: "Dual-pane with visual graph editor primary, YAML secondary" -- User chose dual-pane but specified YAML should not be the featured editing mode
- [decision] D-16: "Inline sub-canvases for Map/Fold/Loop" -- User confirmed sub-canvas interaction model over separate tabs
- [research] Flowise AgentFlow V2 Iteration Node: "Nested nodes visually inside container node boundaries" -- Industry pattern for sub-flow containment


#### REQ-14: Composer App (must)
Ask node inspector — context-specific configuration: actor selection (pick from role library or create inline), prompt template editor with {{ variable }} interpolation from upstream outputs, output schema selection (reference from schemas library or raw JSON schema editor), hooks selection (on_start/on_done from registered functions), settings (timeout, max_turns, model override, budget cap).

**Citations:**
- [decision] D-17: "Each primitive has specific configuration in the inspector" -- User required every primitive to have a clear configuration surface
- [research] Claude Agent SDK structured outputs: "output_format accepts Pydantic.model_json_schema(), works with tool use" -- Output schema in Ask maps directly to SDK's structured output enforcement


#### REQ-15: Composer App (must)
Map/Fold/Loop/Branch node inspectors — Map: collection source reference, max parallelism, inline sub-canvas. Fold: collection source, accumulator init, inline sub-canvas. Loop: condition expression, max iterations, inline sub-canvas. Branch: condition type (expression or AI-driven), named output paths, per-path routing to downstream nodes.

**Citations:**
- [decision] D-17: "Each primitive has specific configuration in the inspector" -- User required comprehensive configuration for all primitives
- [code] iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/pm.py: "per_subfeature_loop with tiered context accumulation" -- Fold primitive must handle this pattern — sequential with accumulator


#### REQ-16: Composer App (must)
Roles Library — dedicated page for CRUD of agent roles. System prompt editor, tool selector, model picker, metadata fields. Import/export CLAUDE.md format. Inline creation from workflow editor with promotion to library for reuse.

**Citations:**
- [decision] D-18: "Inline + library hybrid for roles" -- User chose the hybrid model — create in-context or pick from library
- [code] iriai-compose/iriai_compose/actors.py:8-16: "Role(name, prompt, tools, model, metadata)" -- Role model defines what the library manages


#### REQ-17: Composer App (must)
Output Schemas Library — dedicated page or section with JSON schema editor for reusable structured output definitions. Referenced by Ask nodes for Claude SDK output_format enforcement.

**Citations:**
- [decision] D-19: "JSON schema editor, not visual field builder" -- User chose raw JSON schema editor over visual schema builder


#### REQ-18: Composer App (must)
Custom Task Templates — dedicated page for saving subgraph compositions as reusable single-node templates with defined input/output interfaces. Appear in the node palette alongside primitives. Like Flowise Execute Flow pattern.

**Citations:**
- [decision] D-17: "Custom task templates as saved subgraphs" -- User confirmed reusable subgraph templates as a requirement
- [code] iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py: "gate_and_revise, per_subfeature_loop — reusable helper patterns" -- These imperative helpers become declarative task templates


#### REQ-19: Composer App (must)
Phases Library — dedicated page for saving phase templates (named groups of nodes with hooks/conditions) as reusable, droppable units across workflows.

**Citations:**
- [decision] D-20: "Phases library for reusable phase templates" -- User explicitly added phases library as a missing requirement


#### REQ-20: Composer App (must)
Plugins Registry — dedicated page for browsing and configuring available plugins. Each plugin declares its parameter schema and I/O types. Users configure plugin instances here and use them as nodes in the DAG.

**Citations:**
- [decision] D-6: "Plugins configurable in workflow composer, plugged into DAG" -- User chose plugins as first-class configurable DAG participants


#### REQ-21: Composer App (must)
Transforms & Hooks Library — dedicated page or section for named pure functions used as edge transforms and node hooks. Shows function signature (input type to output type) and code preview.

**Citations:**
- [decision] D-5: "Pure transforms on edges, registered by name" -- Transforms need a management surface for discovery and configuration


#### REQ-22: Composer App (must)
Version History — per-workflow version list, diff between versions, restore to previous version. Integrated into the workflow editor or as a dedicated view.

**Citations:**
- [decision] D-8: "Builder produces versioned configs" -- Versioning is the builder's contribution to the hot-swap story


#### REQ-23: Testing (must)
Comprehensive testing plan covering: (1) Development-time — unit tests for iriai-compose primitives and loader, integration tests for the testing framework against mock runtimes, E2E tests for the composer UI (React + API), API contract tests for FastAPI endpoints. (2) Post-development verification — the litmus test: all 3 iriai-build-v2 workflows translated to YAML, loaded, and run through mock runtimes with assertions on execution paths, artifact production, and branch decisions.

**Citations:**
- [decision] D-21: "Comprehensive testing plan for during development and post-development verification" -- User explicitly required testing at both development and verification stages


### SF-1: Declarative Schema & Primitives
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
<!-- SF: composer-app-foundation -->

#### REQ-75: functional (must)
SF-5 must follow the accepted repo topology: `tools/compose/backend` for the FastAPI backend, `tools/compose/frontend` for the compose SPA, and `platform/toolshub/frontend` for the static tools hub. `tools/iriai-workflows` is not part of the approved implementation path.

**Citations:**
- [decision] D-A3: "Repo topology is `tools/compose/backend`, `tools/compose/frontend`, and `platform/toolshub/frontend`." -- This is the accepted implementation contract and supersedes stale `tools/iriai-workflows` assumptions.


#### REQ-76: functional (must)
The compose backend must use a structured FastAPI service layout aligned with existing platform services: `app/main.py`, `app/config.py`, `app/database.py`, `app/models/`, `app/schemas/`, `app/routers/`, `app/dependencies/`, and `app/middleware/`, with Pydantic Settings-based environment configuration.

**Citations:**
- [code] platform/deploy-console/deploy-console-service/app/main.py:19: "Imports settings, database, logging, routers, and middleware from a structured FastAPI service layout." -- SF-5 should reuse the existing platform backend layout pattern instead of inventing a new service structure.


#### REQ-77: functional (must)
SF-5 persistence must use PostgreSQL with SQLAlchemy 2.x and Alembic as the schema source of truth, normalize `postgresql://` URLs to `postgresql+psycopg://`, and track migrations in the isolated `alembic_version_compose` table. SQLite is out of scope.

**Citations:**
- [code] platform/deploy-console/deploy-console-service/app/database.py:13: "Converts legacy postgresql:// URLs to postgresql+psycopg:// for psycopg3." -- SF-5 should follow the existing database URL normalization pattern used by platform services.
- [decision] D-A5: "PostgreSQL + Alembic is the compose foundation storage contract." -- Matches the approved platform direction and avoids stale SQLite drift.


#### REQ-78: functional (must)
SF-5 database scope is exactly five foundation tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`. SF-5 must not add plugin tables, a tools table, phase-template tables, or `workflow_entity_refs`.

**Citations:**
- [decision] D-SF5-R1: "SF-5 is rebased to the accepted `tools/compose` + PostgreSQL/Alembic contract and stays limited to exactly five foundation tables." -- The explicit revision request for the current artifact.
- [decision] D-SF5-R2: "Foundation-level `workflow_entity_refs` assumptions are removed from SF-5; reference-index expansion belongs to SF-7." -- Prevents scope contamination and keeps table ownership aligned with the accepted contract.


#### REQ-79: functional (must)
`WorkflowVersion` is a required audit entity. Workflow create, import, and duplicate operations must create version 1 atomically, and save-version behavior must remain append-only and immutable. Version writes do not trigger mutation hook events on the parent Workflow entity. Version-history browsing, diffing, and restore UI are out of scope for SF-5.

**Citations:**
- [decision] D-13: "Backend workflow version recording remains required in v1 even though version-history UI is deferred." -- Auditability survives the v1 UI scope cut.
- [decision] D-SF5-R6: "`version_saved` does not emit a Workflow entity hook — versions are append-only audit rows, not mutable entity state." -- Prevents SF-7 from over-counting entity mutations caused by version writes.


#### REQ-80: security (must)
All non-health backend endpoints must require JWT Bearer authentication validated against auth-service JWKS, derive `user_id` from `sub`, and expose `dev_tier` for tools-hub gating.

**Citations:**
- [code] platform/deploy-console/deploy-console-service/app/dependencies/auth.py:83: "JWKS cache and fetch path are defined for JWT validation against the auth service." -- SF-5 should follow the same platform JWKS validation pattern for backend auth.
- [code] platform/auth/auth-service/app/routers/oauth.py:1196: "The access token includes the `dev_tier` claim." -- Tools-hub tier gating depends on a real token claim already emitted by auth-service.


#### REQ-81: security (must)
`workflows`, `roles`, `output_schemas`, and `custom_task_templates` must be user-scoped and soft-deletable. Cross-user access attempts must return not-found semantics rather than leaking record existence. `workflow_versions` are immutable audit rows and are not soft-deleted independently.

**Citations:**
- [decision] D-2: "Soft-delete with recovery was chosen as the safer data-management model." -- Soft delete is a user-facing recovery requirement, not just an implementation preference.


#### REQ-82: functional (must)
The backend must provide the standard platform error envelope, public `GET /health`, public `GET /ready` with database readiness checks, and an explicit production CORS allow-list for compose and tools-hub browser origins rather than wildcard credentials configuration.

**Citations:**
- [code] platform/deploy-console/deploy-console-service/app/schemas/errors.py:29: "ErrorResponse standardizes `error`, `error_description`, and optional `details`." -- SF-5 should match the platform error envelope so downstream clients can handle failures consistently.


#### REQ-83: security (must)
SF-5 must implement per-user rate limiting and structured JSON logging with request correlation and auth/import/delete event coverage, while avoiding raw workflow YAML and prompt-body logging.

**Citations:**
- [code] platform/deploy-console/deploy-console-service/app/middleware/rate_limit.py:16: "Rate-limit key extraction uses JWT `sub` when present and falls back to remote address." -- SF-5 should bucket limits by authenticated user whenever possible.


#### REQ-84: functional (must)
The backend must expose authenticated workflow CRUD, search, and cursor-paginated list/detail endpoints for user-owned workflows.

**Citations:**
- [decision] D-3: "Cursor-based pagination was chosen for platform consistency." -- The workflow list should follow the same pagination contract as the rest of the platform.


#### REQ-85: functional (must)
The workflow API must support duplicate, import, export, starter-template retrieval, and save-version actions, subject to the following canonical contracts: (a) Import endpoint: `POST /api/workflows/import` is the collection-level creation endpoint — it creates a new user-owned workflow from uploaded YAML. `POST /api/workflows/{id}/import` is not a valid SF-5 endpoint. Import must reject malformed YAML with parse errors, may return validation warnings for schema-invalid YAML, and must never create partial workflow state. (b) Starter template persistence: Starter templates are system-owned rows in the `workflows` table with `user_id='__system__'` and `deleted_at=NULL`, seeded by an Alembic data migration that reads iriai-build-v2 planning/develop/bugfix YAML source files at migration time. No filesystem template assets are served by the compose backend at request time. `GET /api/workflows/templates` returns starter templates without user-scoping. Duplicating a template creates a new user-owned workflow; the system template row is never modified by user actions.

**Citations:**
- [decision] D-SF5-R5: "The canonical import endpoint is `POST /api/workflows/import` (collection-level creation). `POST /api/workflows/{id}/import` is not a valid SF-5 endpoint." -- Import is fundamentally a creation operation. Standardizing on the collection form removes the endpoint path ambiguity between plan and system design.
- [decision] D-SF5-R4: "Starter templates are persisted as `user_id='__system__'` rows in the `workflows` table, seeded by an Alembic data migration. Filesystem asset serving is not used." -- DB rows are queryable via the same API layer as user-owned workflows and support duplicate/versioning semantics consistently without a separate asset-serving surface.
- [decision] D-7: "Starter templates and user workflows both belong on the landing page." -- Starter-template delivery is a product requirement, not a developer convenience.


#### REQ-86: functional (should)
SF-5 must provide baseline authenticated CRUD/list endpoints for `roles`, `output_schemas`, and `custom_task_templates` using the same user-scoping and soft-delete conventions, while deferring advanced delete-reference checks and tool-library surfaces to SF-7.

**Citations:**
- [decision] D-SF5-R2: "Reference-index expansion and advanced delete-reference checks belong to SF-7." -- SF-5 ships foundation-level CRUD only; SF-7 builds the reference-safety layer on top.


#### REQ-87: functional (must)
`GET /api/schema/workflow` is the canonical runtime schema endpoint and must return `WorkflowConfig.model_json_schema()` from `iriai-compose`. `POST /api/workflows/{id}/validate` must validate against that same runtime contract and return path/message error details.

**Citations:**
- [decision] D-GR-22: "Runtime schema delivery comes from `/api/schema/workflow`; static `workflow-schema.json` is build/test only." -- This is the explicit cycle-4 resolution for schema delivery.
- [code] .iriai/artifacts/features/beced7b1/broad/architecture.md:510: "`@router.get("/api/schema/workflow")` returns `WorkflowConfig.model_json_schema()`." -- The broad architecture already assumes runtime schema delivery from the backend endpoint.


#### REQ-88: functional (must)
Persisted and exported workflow YAML must use the canonical nested workflow contract: phase-contained nodes under `phases[].nodes` / `phases[].children`, hook wiring represented through ordinary edges, and no serialized `port_type` or separate `hooks` section.

**Citations:**
- [decision] D-GR-22: "YAML remains nested; hook wiring remains edge-based with no separate `hooks` section and no serialized `port_type`." -- Nested phase containment and edge-only hook serialization are the authoritative YAML contract across all affected subfeatures.


#### REQ-89: functional (must)
The compose frontend must live in `tools/compose/frontend` as a React 18 + TypeScript + Vite SPA using React Router, React Query, `@homelocal/auth`, and an authenticated API client for the compose backend.

**Citations:**
- [decision] D-19: "Vite was chosen for the new greenfield frontends." -- Bundler selection is already resolved for the compose and tools-hub apps.


#### REQ-90: functional (must)
SF-5 must provide the compose shell and workflows landing experience with exactly four foundation folders in the Explorer-style sidebar: Workflows, Roles, Output Schemas, and Task Templates. Plugin pages, Tool Library pages, and reference-check UI do not ship in SF-5.

**Citations:**
- [decision] D-18: "Compose uses Explorer-style sidebar navigation." -- This is the approved shell pattern for the compose app.
- [code] .iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/design-decisions.md:13: "The sidebar shows Workflows, Roles, Output Schemas, and Task Templates, with no Plugins folder." -- The current SF-5 design decision artifact already removes stale plugin-folder assumptions.


#### REQ-91: functional (must)
The tools hub must live at `platform/toolshub/frontend` as a static authenticated SPA that reads `dev_tier`, renders a hardcoded developer-tools card catalog, and routes the Workflow Composer card to `compose.iriai.app` in the same tab.

**Citations:**
- [decision] D-10: "The tools hub uses a hardcoded tool-card array for the initial release." -- The first release does not require a separate backend for the tools hub catalog.


#### REQ-92: functional (must)
SF-5's service layer must expose a stable, in-process mutation hook interface for all four foundation entity types (`Workflow`, `Role`, `OutputSchema`, `CustomTaskTemplate`). Hooks are invoked synchronously after a successful database commit and emit one of exactly four event kinds — `created`, `updated`, `soft_deleted`, `restored` — together with the entity type, entity id, and `user_id`. This enumeration is exhaustive: `imported`, `version_saved`, `deleted`, and any other event names are not valid hook events (import maps to `created`; a soft-delete is `soft_deleted` — there is no separate `deleted` kind; version writes do not trigger entity hooks). Hooks must cover all four entity types; a Workflow-only implementation is not sufficient. SF-7 (or any downstream extension) registers refresh callbacks against this interface without modifying SF-5 service code. SF-5 must never create or update `workflow_entity_refs` rows — that responsibility belongs entirely to SF-7 via this hook interface.

**Citations:**
- [decision] D-SF5-R3: "SF-5 exposes an in-process, post-commit mutation hook interface on all four foundation entity types; SF-7 subscribes to those hooks to maintain `workflow_entity_refs`. SF-5 never creates reference-index rows directly." -- Cleanly separates SF-5 from SF-7 without tight coupling while giving SF-7 a reliable trigger surface.
- [decision] D-SF5-R6: "The mutation hook event type enumeration is exhaustive: `created`, `updated`, `soft_deleted`, `restored`. No other event kinds exist." -- Removes the contradiction between plan and system design event-type lists and gives SF-7 a stable, closed interface to code against.


### SF-6: Workflow Editor & Canvas
<!-- SF: workflow-editor -->

#### REQ-93: functional (must)
The editor must use a React Flow canvas as the primary editing surface with pan, zoom, fit-to-screen, and floating inspectors for graph authoring.

**Citations:**
- [decision] D-2: "Left palette, center canvas, no YAML pane." -- Establishes the canvas as the primary authoring surface.
- [decision] D-3: "Floating XP windows for node/edge/phase inspectors." -- Defines the required inspection model around the canvas.


#### REQ-94: functional (must)
The canvas must expose only three atomic node types for direct placement: Ask, Branch, and Plugin; iteration semantics must be expressed through phase modes rather than extra node kinds.

**Citations:**
- [decision] D-7: "Three atomic node types only: Ask, Branch, Plugin." -- Locks the visible primitive set.
- [decision] D-8: "Phases carry execution modes: sequential, map, fold, loop." -- Moves iteration semantics into phases instead of separate node types.


#### REQ-95: functional (must)
Lifecycle hooks must be authored only as visible `on_start` and `on_end` ports on nodes and phases, with hook behavior serialized through normal edges rather than a separate hooks section.

**Citations:**
- [decision] D-13: "Hooks are `on_start` / `on_end` ports on nodes and phases." -- Defines the user-facing hook model.
- [decision] D-GR-22: "Hook wiring remains edge-based with no separate `hooks` section and no serialized `port_type`." -- Locks the canonical serialization contract.


#### REQ-96: functional (must)
Users must create phases with a selection-rectangle gesture, configure sequential/map/fold/loop mode in the phase inspector, and nest phases as needed for real workflows.

**Citations:**
- [decision] D-9: "Paint-style selection rectangle creates phases." -- Defines the containment-creation interaction.
- [decision] D-25: "Phases can nest." -- Required for fold-with-inner-loop workflow patterns.


#### REQ-97: functional (must)
All save, load, import, export, and validation flows must normalize to canonical nested YAML where top-level workflows own `phases[]`, each phase owns `nodes[]`, and nested phases live in `children[]`.

**Citations:**
- [decision] D-GR-22: "YAML remains nested (`phases[].nodes`, `phases[].children`)." -- Establishes the canonical runtime shape.
- [decision] D-33: "Serialization maps a flat React Flow store to nested YAML and back." -- Defines how the editor can remain flat internally while honoring the runtime contract.


#### REQ-98: functional (must)
Edge serialization must use only `source`, `target`, and optional `transform_fn`; hook-vs-data must be derived from source port resolution, and no serialized `port_type` field may appear in YAML.

**Citations:**
- [decision] D-19: "Edge transforms are authored as inline Python and stored as `transform_fn`." -- Aligns the editor to the runtime edge field.
- [decision] D-GR-22: "Hook wiring remains edge-based with no serialized `port_type`." -- Removes stale serialization fields from the contract.


#### REQ-99: functional (must)
The editor must fetch `GET /api/schema/workflow` on load and use it as the canonical runtime schema source for schema-driven validation and editor boot.

**Citations:**
- [decision] D-34: "Runtime schema for the composer comes from `GET /api/schema/workflow`." -- Defines the canonical schema delivery path.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:36: "`GET /api/schema/workflow` returns `WorkflowConfig.model_json_schema()` from iriai-compose." -- Documents the backend endpoint that serves the runtime schema.


#### REQ-100: functional (must)
Validation must have two tiers: client-side checks against the fetched runtime schema plus server-side deep validation through SF-2 `validate()` exposed by the workflow backend.

**Citations:**
- [decision] D-28: "Server-side validation uses SF-2 `validate()`." -- Defines the deep-validation engine.
- [decision] D-29: "Two-tier validation: client-side fast checks plus server-side deep checks." -- Defines the validation architecture.


#### REQ-101: functional (must)
The editor must preserve round-trip fidelity across save, export, import, and reload, including nested phases, hook edges, loop exits, and positions.

**Citations:**
- [decision] D-33: "Serialization maps a flat React Flow store to nested YAML and back." -- Makes lossless round-trip a core editor responsibility.
- [decision] D-GR-22: "Nested YAML containment and edge-based hook serialization are authoritative." -- Defines what must survive round-trip.


#### REQ-102: functional (must)
Collapsed phases and templates must render as `CollapsedGroupCard` metadata cards with no mini-canvas thumbnail and no child-node React Flow rendering while collapsed.

**Citations:**
- [decision] D-35: "Collapsed phases/templates use `CollapsedGroupCard` (260x52px), not mini-canvas thumbnails." -- Locks the collapsed rendering choice.


#### REQ-103: functional (must)
The editor must support undo/redo for meaningful canvas mutations and auto-save after inactivity without interrupting active text or code editing.

**Citations:**
- [decision] D-22: "No version-history UI in v1; auto-save every 30s." -- Defines save behavior.
- [decision] D-23: "Undo/redo stack depth is 50." -- Defines revision-stack expectations.


#### REQ-104: performance (must)
Canvas interactions must remain responsive at 50+ visible nodes, with collapsed groups used as the primary rendering optimization.

**Citations:**
- [decision] D-24: "Target responsive performance is 50+ visible nodes." -- Sets the scale target.
- [decision] D-35: "Collapsed phases/templates use `CollapsedGroupCard`." -- Provides the main performance lever for large graphs.


#### REQ-105: security (must)
The editor must never execute inline Python locally; it stores `transform_fn` and other expressions as data, validates them structurally, and blocks editing when the canonical runtime schema endpoint is unavailable.

**Citations:**
- [decision] D-34: "Runtime schema ... comes from `GET /api/schema/workflow`." -- Prevents fallback to stale or untrusted schema contracts.
- [decision] D-28: "Server-side validation uses SF-2 `validate()`." -- Keeps structural validation centralized without local code execution.


#### REQ-106: functional (must)
The editor must ship inside `tools/compose/frontend` and boot/save against only the SF-5 compose foundation contract: workflow/version CRUD, roles, output schemas, custom task templates, `POST /validate`, and `GET /api/schema/workflow` backed by the five canonical PostgreSQL/Alembic tables. Core editor flows must not depend on `tools/iriai-workflows`, SQLite, `/api/plugins`, or `workflow_entity_refs` endpoints.

**Citations:**
- [decision] D-SF5-R1: "SF-5 rebased to tools/compose + PostgreSQL + exactly five foundation tables." -- Defines the canonical foundation topology the editor depends on.
- [decision] D-SF5-R2: "Stale tools/iriai-workflows, SQLite, plugin-surface, and foundation-level workflow_entity_refs assumptions removed; reference-index expansion belongs to SF-7." -- Locks the SF-6 dependency boundary against stale artifacts.


### SF-7: Libraries & Registries
<!-- SF: libraries-registries -->

#### REQ-107: functional (must)
SF-7 must extend the accepted compose topology: library surfaces live inside the compose app backed by `tools/compose` frontend/backend and PostgreSQL + Alembic, not `tools/iriai-workflows` or SQLite.

**Citations:**
- [decision] D-GR-27: "`tools/compose` is accepted; `tools/iriai-workflows` is rejected." -- The revision must inherit the accepted topology rather than preserve stale paths.
- [decision] D-GR-28: "PostgreSQL + SQLAlchemy + Alembic remains canonical." -- This fixes the stale SQLite assumption.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/integration-review-sources-plan.md:6170: "`tools/compose/frontend`, `tools/compose/backend`, `tools/iriai-workflows` NOT used." -- The repo-wide accepted topology is recorded in the feature plan.


#### REQ-108: functional (must)
Roles, Schemas, and Task Templates must use a pre-delete reference check backed by `workflow_entity_refs`, introduced as an SF-7-owned follow-on PostgreSQL/Alembic extension; SF-5 remains limited to exactly five foundation tables and only exposes the workflow mutation hooks SF-7 needs to refresh the index.

**Citations:**
- [decision] D-GR-29: "SF-5 stays at five tables; `workflow_entity_refs` moves to SF-7 scope." -- This is the core ownership change requested in the Cycle 5 feedback.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:22: "Create exactly 5 SF-5 tables." -- The foundation contract leaves no room for foundation-owned reference-index tables.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-5.md:24: "SF-7 should own the `workflow_entity_refs` reference-index extension." -- The accepted Cycle 5 guidance explicitly moves ownership into SF-7.


#### REQ-109: functional (must)
SF-7 delete UX for Roles, Schemas, and Task Templates must be non-destructive: `EntityDeleteDialog` and `useReferenceCheck` call `GET /api/{entity}/references/{id}` before any DELETE request, and the backend must not parse workflow YAML on demand for that lookup.

**Citations:**
- [decision] D-GR-26: "`workflow_entity_refs` backs `GET /api/{entity}/references/{id}`." -- This is the canonical delete-preflight contract.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:26: "`useReferenceCheck` calls the references endpoint before delete." -- The SF-7 interaction design already encodes the desired UX.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:52: "Remove YAML-scan delete helpers in favor of indexed reference checks." -- The plan language matches the requested revision.


#### REQ-110: functional (must)
The Tool Library remains a full CRUD library page with list, detail, and editor views; registered tools populate the Role editor tool checklist via `GET /api/tools`, and tool delete protection remains role-backed rather than `workflow_entity_refs`-backed.

**Citations:**
- [decision] D-GR-7: "Tool Library restored with full CRUD and role integration." -- Tool CRUD remains active scope after the rebase.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md:106: "/tools route, Tool entity CRUD, role editor integration." -- The review history records the accepted tool-library scope.
- [code] /Users/danielzhang/src/iriai/iriai-compose/iriai_compose/actors.py:13: "`Role.tools` is `list[str]`." -- Tool delete checks still branch on persisted role arrays rather than workflow refs.


#### REQ-111: functional (must)
`custom_task_templates` must persist `actor_slots` through a follow-on Alembic migration and API support so task template actor-slot definitions survive reloads and remain reusable across workflows.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md:108: "Alembic migration for `actor_slots` is an implementation prerequisite." -- The revision must keep actor-slot persistence explicit.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/reviews/pm.md:143: "SF-7 adds actor_slots to CustomTaskTemplate." -- The cross-subfeature review confirms this is an SF-7 extension, not SF-5 foundation scope.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:199: "Without the migration, actor slot definitions are lost on reload." -- This captures the concrete failure the requirement prevents.


#### REQ-112: non-functional (should)
Library pages must feel immediate: warm-cache list pages load within 500ms, cold fetches within 2 seconds, and data access uses stale-while-revalidate query behavior.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/compile-sources-prd.md:6710: "Warm-cache within 500ms; cold fetches within 2 seconds." -- These are the established SF-7 responsiveness targets.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/prd_sf7_and_merged.md:271: "Use stale-while-revalidate query behavior for library APIs." -- The prior merged PRD already fixed the desired caching model.


#### REQ-113: security (must)
All library API endpoints require JWT Bearer auth, scope data to the authenticated user, and return 404 rather than 403 for cross-user access attempts.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:24: "Scope all resource access by authenticated `user_id`; return `404` for other users." -- SF-7 inherits compose foundation tenancy controls.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:25: "JWT auth on all non-health endpoints." -- Library APIs stay behind the same compose auth boundary.


#### REQ-114: security (must)
Server-side validation must enforce JSON payload size limits and entity-name sanitization across library entities, with clear 413/422 responses and matching frontend guards.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md:110: "256KB JSON payload size limits." -- The review history keeps payload limits as required SF-7 scope.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md:111: "Name sanitization regex on the server and frontend." -- Entity naming rules remain part of the accepted SF-7 guardrails.
- [research] OWASP Input Validation Cheat Sheet: "Apply server-side allowlist validation with length limits as early as possible." -- This supports rejecting malformed or oversized library payloads before persistence.


#### REQ-115: functional (must)
SF-7 library scope remains limited to Roles, Output Schemas, Task Templates, and Tools inside compose; do not restore Plugins Library pages, plugin endpoints, or PluginPicker surfaces.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:198: "Plugin surfaces must be removed rather than restored." -- The review explicitly called stale plugin surfaces the largest SF-7 contradiction.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:213: "Do not create a PluginPicker." -- The current SF-7 plan already narrows picker scope to non-plugin library entities.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:40: "Exclude plugin-management entities and `/api/plugins` from SF-5." -- The foundation contract also rejects plugin-management database/API surfaces.


## Acceptance Criteria

### Broad Acceptance Criteria
<!-- SF: broad -->

#### AC-1
- **User Action:** User exports a workflow YAML from the composer and runs it via iriai-compose run()
- **Expected:** The workflow executes successfully against provided runtimes, producing the expected artifacts and following the expected DAG execution order
- **Not Criteria:** 
- **Requirements:** REQ-1, REQ-7
- **Citations:** - [decision] D-9: "run() function in iriai-compose any app can call" -- Primary entry point for executing declarative workflows


#### AC-2
- **User Action:** Developer translates iriai-build-v2's planning workflow to declarative YAML
- **Expected:** All 6 phases (scoping, PM, design, architecture, plan review, task planning) are representable. Key patterns — per-subfeature Fold with tiered context, gate-and-revise loops, compilation, interview-based gate review — all work. Testing framework assertions pass.
- **Not Criteria:** 
- **Requirements:** REQ-1, REQ-2, REQ-3, REQ-8, REQ-9
- **Citations:** - [code] iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/pm.py: "broad_interview, decompose_and_gate, per_subfeature_loop, integration_review, targeted_revision, compile_artifacts, interview_gate_review" -- All helper functions must be representable as primitive compositions


#### AC-3
- **User Action:** Developer translates iriai-build-v2's develop workflow (implementation phase) to declarative YAML
- **Expected:** DAG execution groups (parallel within group, sequential across groups), per-group verification with retry, handover document compression, QA → review → user approval loop — all representable and passing tests
- **Not Criteria:** 
- **Requirements:** REQ-1, REQ-8, REQ-9
- **Citations:** - [code] iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py: "_implement_dag with parallel groups, _verify, handover.compress()" -- Implementation phase patterns must work in declarative format


#### AC-4
- **User Action:** Developer translates iriai-build-v2's bugfix workflow to declarative YAML
- **Expected:** Linear 8-phase flow with parallel RCA (dual analyst pattern), diagnosis-and-fix retry loop, preview server plugin integration — all representable and passing tests
- **Not Criteria:** 
- **Requirements:** REQ-1, REQ-4, REQ-8, REQ-9
- **Citations:** - [code] iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py: "Parallel RCA analysts, bug_fixer adjudication, verification loop" -- Bugfix patterns including parallel analysis must be representable


#### AC-5
- **User Action:** User edits a workflow in the visual canvas and checks the YAML pane
- **Expected:** YAML pane updates in real-time to reflect canvas changes. Editing YAML updates the canvas. Round-trip is lossless — no data lost when switching between views.
- **Not Criteria:** 
- **Requirements:** REQ-13
- **Citations:** - [decision] D-15: "Dual-pane with visual graph editor primary, YAML secondary" -- Both panes must stay in sync


#### AC-6
- **User Action:** User logs into tools.iriai.app with a hobby-tier account
- **Expected:** Tools hub shows workflow composer card as enabled. Pro-only tool cards are visible but disabled/locked with tier upgrade prompt.
- **Not Criteria:** 
- **Requirements:** REQ-10
- **Citations:** - [code] platform/auth/auth-service/app/routers/oauth.py:1196: "dev_tier: user.dev_tier in JWT claims" -- Tier gating reads directly from JWT


#### AC-7
- **User Action:** User creates a role inline on an Ask node and promotes it to the library
- **Expected:** Role appears in the Roles Library and is selectable from other Ask nodes in the same or different workflows
- **Not Criteria:** 
- **Requirements:** REQ-16
- **Citations:** - [decision] D-18: "Inline + library hybrid for roles" -- Inline roles must be promotable to reusable library entries


#### AC-8
- **User Action:** User creates a workflow with 50+ nodes on the canvas
- **Expected:** Canvas remains responsive — zoom, pan, node selection, and inspector panel all perform without perceptible lag
- **Not Criteria:** Does not need to support 500+ node workflows in initial release
- **Requirements:** REQ-13
- **Citations:** - [research] React Flow performance documentation: "React.memo and selective rendering for large graphs" -- Performance is a known concern for large node graphs


### SF-1: Declarative Schema & Primitives
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
<!-- SF: composer-app-foundation -->

#### AC-61
- **User Action:** An engineer inspects the approved SF-5 file/repo contract.
- **Expected:** The compose backend/frontend map to `tools/compose/{backend,frontend}`, the tools hub maps to `platform/toolshub/frontend`, and SF-5 does not depend on `tools/iriai-workflows`.
- **Not Criteria:** New SF-5 implementation work is planned under `tools/iriai-workflows`.
- **Requirements:** REQ-75, REQ-89, REQ-91
- **Citations:** - [decision] D-A3: "Repo topology is `tools/compose/backend`, `tools/compose/frontend`, and `platform/toolshub/frontend`." -- This is the accepted implementation contract and supersedes stale `tools/iriai-workflows` assumptions.


#### AC-62
- **User Action:** An engineer inspects the initial Alembic chain and database contract.
- **Expected:** The migration chain uses PostgreSQL, tracks revisions in `alembic_version_compose`, and creates exactly five SF-5 tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`.
- **Not Criteria:** SQLite remains the foundation engine, or plugin/tools/reference-index tables are created in SF-5.
- **Requirements:** REQ-77, REQ-78
- **Citations:** - [decision] D-A5: "PostgreSQL + Alembic is the compose foundation storage contract." -- Matches the approved platform direction and avoids stale SQLite drift.


#### AC-63
- **User Action:** The user opens `tools.iriai.app`, authenticates, and clicks Workflow Composer.
- **Expected:** The tools hub shows the composer card and same-tab navigation lands on `compose.iriai.app` with the authenticated compose shell available.
- **Not Criteria:** Protected tool states are visible before auth resolves, or the composer opens in a new tab.
- **Requirements:** REQ-90, REQ-91
- **Citations:** - [decision] D-10: "The tools hub uses hardcoded tool cards for the initial tool catalog." -- The initial tools-hub experience is card-driven and does not depend on a backend catalog.


#### AC-64
- **User Action:** The user creates a workflow from the Workflows view.
- **Expected:** A workflow row and `WorkflowVersion` v1 are created atomically, and the workflow appears without a full-page reload.
- **Not Criteria:** A workflow is created without version 1, or the user must refresh to see it.
- **Requirements:** REQ-79, REQ-84, REQ-85
- **Citations:** - [decision] D-13: "Backend workflow version recording remains required in v1 for the audit trail." -- Creation is incomplete unless the initial version row exists immediately.


#### AC-65
- **User Action:** An authenticated caller requests `GET /api/schema/workflow`.
- **Expected:** The backend returns JSON Schema generated from `WorkflowConfig.model_json_schema()`.
- **Not Criteria:** A bundled static file is treated as the canonical runtime response.
- **Requirements:** REQ-87
- **Citations:** - [decision] D-GR-22: "`/api/schema/workflow` is the canonical schema delivery path for the composer." -- This is the direct acceptance test for the cycle-4 schema-source decision.


#### AC-66
- **User Action:** A user opens `/workflows/{id}/edit`.
- **Expected:** The frontend requests the workflow record and `GET /api/schema/workflow` before rendering schema-dependent editing affordances.
- **Not Criteria:** The editor boots from a stale bundled schema or skips the runtime schema request.
- **Requirements:** REQ-87, REQ-89
- **Citations:** - [code] .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:261: "Editor boot requests the workflow record and `GET /api/schema/workflow`." -- SF-6 already assumes this backend integration point.


#### AC-67
- **User Action:** The user saves or exports a workflow with nested phases and hook edges.
- **Expected:** Persisted/exported YAML uses nested phase containment and edge-only hook serialization with no serialized `port_type`.
- **Not Criteria:** Save/export emits a flat root graph, a separate hooks section, or persisted `port_type`.
- **Requirements:** REQ-79, REQ-85, REQ-88
- **Citations:** - [decision] D-GR-22: "YAML remains nested and hook wiring remains edge-based with no serialized `port_type`." -- Both structural and hook-serialization assertions are part of the same resolved contract.


#### AC-68
- **User Action:** The user invokes `POST /api/workflows/import` with malformed YAML.
- **Expected:** Import returns parse errors and no workflow or version rows are created.
- **Not Criteria:** Partial workflow or version rows are persisted; the endpoint path used is `POST /api/workflows/{id}/import`; or the user receives only a generic failure with no path/message context.
- **Requirements:** REQ-85, REQ-88
- **Citations:** - [decision] D-SF5-R5: "The canonical import endpoint is `POST /api/workflows/import` (collection-level creation)." -- AC-8 must reference the canonical endpoint to verify the path standardization decision.
- [code] .iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/plan.md:608: "Import is a first-class endpoint with dedicated validation behavior." -- Import should be all-or-nothing and should not persist partial invalid workflows.


#### AC-69
- **User Action:** User A attempts to access User B's workflow by ID.
- **Expected:** The API returns a not-found response.
- **Not Criteria:** The API returns a permission error that confirms the record exists.
- **Requirements:** REQ-80, REQ-81
- **Citations:** - [code] .iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/plan.md:553: "Ownership checks return 404, not 403." -- The current SF-5 plan already defines the security posture for cross-user access.


#### AC-70
- **User Action:** An engineer inspects the SF-5 API surface.
- **Expected:** Workflow endpoints plus baseline role/schema/task-template CRUD exist, while `/api/plugins`, `/api/tools`, and `/api/{entity}/references/{id}` are absent from SF-5.
- **Not Criteria:** Plugin, tools, or reference-index surfaces are introduced as foundation APIs.
- **Requirements:** REQ-78, REQ-86, REQ-90
- **Citations:** - [decision] D-55: "Plugin database entities and `/api/plugins` are removed from SF-5." -- The current SF-5 design decisions already lock the API surface boundaries.


#### AC-71
- **User Action:** An engineer inspects production-ready backend behavior.
- **Expected:** `GET /health` and `GET /ready` exist, readiness checks database connectivity, and production CORS is limited to the compose/tools hub browser origins.
- **Not Criteria:** Production uses wildcard credentialed CORS or lacks a readiness check.
- **Requirements:** REQ-82
- **Citations:** - [code] platform/deploy-console/deploy-console-service/app/main.py:11: "FastAPI app wiring includes global error handling and middleware at the service entry point." -- Health and CORS behavior belong in the same service-level integration surface.


#### AC-72
- **User Action:** An authenticated caller exceeds the per-user API limit.
- **Expected:** The API returns `429` with retry guidance and logs the event without storing raw YAML or prompt bodies.
- **Not Criteria:** Rate limits are global-only, or structured logs capture full workflow bodies.
- **Requirements:** REQ-83
- **Citations:** - [code] platform/deploy-console/deploy-console-service/app/middleware/rate_limit.py:50: "Rate-limit handler returns HTTP 429 and logs rate-limit events." -- SF-5 should match the existing platform behavior for limit enforcement and logging.


#### AC-73
- **User Action:** An engineer inspects SF-5's service layer.
- **Expected:** A stable mutation hook registration interface exists; it accepts typed callbacks for exactly the four event kinds `created`, `updated`, `soft_deleted`, and `restored` on all four foundation entity types (`Workflow`, `Role`, `OutputSchema`, `CustomTaskTemplate`); hooks fire after successful commit; and no event kinds beyond these four exist in the interface. SF-5 contains no code that creates or updates `workflow_entity_refs` rows.
- **Not Criteria:** SF-5 creates reference-index rows directly; SF-7 must reach into SF-5 model internals to detect entity mutations; the hook interface covers workflows only; or additional event kinds such as `imported`, `version_saved`, or `deleted` exist in the interface.
- **Requirements:** REQ-78, REQ-92
- **Citations:** - [decision] D-SF5-R3: "SF-5 exposes an in-process, post-commit mutation hook interface on all four foundation entity types." -- The hook interface is SF-5's contribution to the SF-7 reference-index handoff.
- [decision] D-SF5-R6: "The mutation hook event type enumeration is exhaustive: `created`, `updated`, `soft_deleted`, `restored`." -- AC-13 must verify the closed enumeration to prevent SF-7 from coding against phantom event kinds.


#### AC-74
- **User Action:** An authenticated user calls `GET /api/workflows/templates` and then duplicates a starter template.
- **Expected:** The response lists system-seeded starter templates (including the iriai-build-v2 planning/develop/bugfix workflows) sourced from `user_id='__system__'` DB rows; duplicating one creates a new user-owned workflow row with version 1.
- **Not Criteria:** Starter template content is loaded from a filesystem path at request time; duplicating a template modifies the system template row; or starter templates appear in the user's own editable workflow list without an explicit duplicate step.
- **Requirements:** REQ-79, REQ-85
- **Citations:** - [decision] D-SF5-R4: "Starter templates are persisted as `user_id='__system__'` rows, seeded by an Alembic data migration. Filesystem asset serving is not used." -- AC-14 directly verifies the persistence approach decision against the two competing options.


### SF-6: Workflow Editor & Canvas
<!-- SF: workflow-editor -->

#### AC-75
- **User Action:** User opens `/workflows/:id/edit`.
- **Expected:** The editor waits for both the workflow payload and `GET /api/schema/workflow` to succeed before rendering the working canvas.
- **Not Criteria:** The editor silently boots against a stale bundled schema or enters a partially usable state.
- **Requirements:** REQ-93, REQ-99, REQ-105
- **Citations:** - [decision] D-34: "Runtime schema ... comes from `GET /api/schema/workflow`." -- Defines the required boot dependency.


#### AC-76
- **User Action:** User drags an Ask node from the palette to the canvas.
- **Expected:** An Ask node appears at the drop position with data ports and visible hook ports; placement mode ends after the drop.
- **Not Criteria:** Sticky placement mode persists or hook ports are missing.
- **Requirements:** REQ-93, REQ-94, REQ-95
- **Citations:** - [decision] D-6: "Drag-and-drop from palette is one-shot." -- Defines placement behavior.
- [decision] D-13: "Hooks are `on_start` / `on_end` ports on nodes and phases." -- Requires visible hook ports on the node.


#### AC-77
- **User Action:** User draws from a node's `on_end` port to another node input and saves the workflow.
- **Expected:** A dashed hook edge appears on canvas, and saved YAML contains only dot-notation `source` / `target` refs for that hook edge with no `port_type`.
- **Not Criteria:** A separate hooks block is emitted or YAML includes serialized `port_type`.
- **Requirements:** REQ-95, REQ-98, REQ-101
- **Citations:** - [decision] D-GR-22: "Hook wiring remains edge-based with no separate `hooks` section and no serialized `port_type`." -- Directly defines hook-edge serialization.


#### AC-78
- **User Action:** User creates a phase with the selection rectangle and changes it to fold mode.
- **Expected:** The selected nodes become phase children, fold config controls appear, and fold-only context variables become available inside child inspectors.
- **Not Criteria:** Fold requires a separate Fold node type or fold context leaks outside the phase.
- **Requirements:** REQ-96
- **Citations:** - [decision] D-9: "Paint-style selection rectangle creates phases." -- Defines the phase-creation gesture.
- [decision] D-8: "Phases carry execution modes: sequential, map, fold, loop." -- Defines mode configuration on the phase.


#### AC-79
- **User Action:** User nests a loop phase inside another phase and sets `max_iterations=3`.
- **Expected:** The inner phase stays inside the outer phase and exposes both `condition_met` and `max_exceeded` exits as distinct connections.
- **Not Criteria:** The loop shows only one exit or the inner phase escapes the parent boundary.
- **Requirements:** REQ-96
- **Citations:** - [decision] D-25: "Phases can nest." -- Required for nested containment behavior.
- [decision] D-27: "Loop phases expose `condition_met` and `max_exceeded` exits." -- Defines the expected loop exits.


#### AC-80
- **User Action:** User clicks Validate.
- **Expected:** Client-side issues are calculated against the fetched runtime schema and merged with server-side `validate()` results into one validation panel.
- **Not Criteria:** Validation uses only a static schema artifact or requires running the workflow.
- **Requirements:** REQ-99, REQ-100, REQ-105
- **Citations:** - [decision] D-29: "Two-tier validation: client-side fast checks plus server-side deep checks." -- Defines the merged validation flow.
- [decision] D-34: "Runtime schema ... comes from `GET /api/schema/workflow`." -- Requires runtime schema-backed client validation.


#### AC-81
- **User Action:** User saves a workflow with loose top-level nodes, nested phases, and hook edges.
- **Expected:** The saved YAML normalizes loose nodes under a synthetic root phase and preserves nested `children[]`, `nodes[]`, and hook edges as normal edges.
- **Not Criteria:** The saved YAML emits top-level nodes, flattens nesting, or loses hook-edge identity.
- **Requirements:** REQ-97, REQ-98, REQ-101
- **Citations:** - [decision] D-33: "Serialization maps a flat React Flow store to nested YAML and back." -- Defines the normalization behavior.
- [decision] D-GR-22: "YAML remains nested (`phases[].nodes`, `phases[].children`)." -- Makes top-level-node output invalid.


#### AC-82
- **User Action:** User collapses several large phases and pans/zooms a 50+ node workflow.
- **Expected:** Interactions stay responsive and collapsed groups render as lightweight metadata cards without child-node mounts.
- **Not Criteria:** Collapsed groups still render their children or visibly degrade interaction latency.
- **Requirements:** REQ-102, REQ-104
- **Citations:** - [decision] D-35: "Collapsed phases/templates use `CollapsedGroupCard` ... not mini-canvas thumbnails." -- Defines the collapsed rendering.


#### AC-83
- **User Action:** User performs multiple edits then presses Undo and Redo.
- **Expected:** Changes revert and reapply in order, and open inspector state stays synchronized with canvas state.
- **Not Criteria:** Inspector state drifts from the reverted graph state or edit history truncates unexpectedly.
- **Requirements:** REQ-103
- **Citations:** - [decision] D-23: "Undo/redo stack depth is 50." -- Defines expected edit-history behavior.


#### AC-84
- **User Action:** User opens the editor while `/api/schema/workflow` is unavailable.
- **Expected:** A blocking error state appears and editing is deferred until the canonical schema endpoint recovers.
- **Not Criteria:** The app silently falls back to a local `workflow-schema.json` copy.
- **Requirements:** REQ-99, REQ-105
- **Citations:** - [decision] D-GR-22: "`/api/schema/workflow` is the canonical schema delivery path ... static `workflow-schema.json` is build/test only." -- Makes runtime fallback invalid.


#### AC-85
- **User Action:** User opens an existing workflow in the compose editor before SF-7 plugin/reference endpoints are deployed.
- **Expected:** The core canvas, save/validate flows, and role/schema/task-template affordances load from the compose foundation and remain usable.
- **Not Criteria:** Editor boot blocks on `/api/plugins`, `GET /api/{entity}/references/{id}`, SQLite-only assumptions, or a legacy `tools/iriai-workflows` app shell.
- **Requirements:** REQ-99, REQ-100, REQ-106
- **Citations:** - [decision] D-SF5-R1: "SF-5 rebased to tools/compose + PostgreSQL + exactly five foundation tables." -- Core editor must operate without SF-7 surfaces.
- [decision] D-SF5-R2: "Reference-index expansion belongs to SF-7." -- SF-7 surfaces are additive; blocking on them is invalid.


### SF-7: Libraries & Registries
<!-- SF: libraries-registries -->

#### AC-86
- **User Action:** A user tries to delete a role that is still referenced by a saved workflow, then removes that reference in the workflow and saves again.
- **Expected:** Delete is blocked before any DELETE call with the referencing workflow list; after the workflow save, reopening delete shows the normal confirmation with no stale workflow names.
- **Not Criteria:** The user must not have to issue a DELETE request just to discover references, and stale reference rows must not remain after the saved workflow changes clear them.
- **Requirements:** REQ-108, REQ-109
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:26: "Delete preflight starts with the references endpoint." -- This is the intended user-visible flow for referenced roles.
- [decision] D-GR-29: "Reference-index ownership moves into SF-7 follow-on scope." -- The acceptance test must validate the rebased ownership model.


#### AC-87
- **User Action:** An engineer inspects the initial SF-5 migration and the first SF-7 extension migrations for compose.
- **Expected:** SF-5 creates exactly five foundation tables (`workflows`, `workflow_versions`, `roles`, `output_schemas`, `custom_task_templates`), while SF-7 follow-on Alembic revisions add `workflow_entity_refs`, the `tools` table, and the `actor_slots` column on `custom_task_templates` inside the compose PostgreSQL backend.
- **Not Criteria:** The foundation migration must not create `workflow_entity_refs`, `tools`, plugin tables, or SQLite-specific persistence, and the extension work must not target `tools/iriai-workflows`.
- **Requirements:** REQ-107, REQ-108, REQ-111
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:58: "Exactly 5 tables exist in the SF-5 foundation migration." -- This anchors the inspection criterion for the foundation layer.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/reviews/pm.md:143: "SF-7 adds a Tool entity and actor_slots." -- The extension inspection must confirm these stay in SF-7 scope.


#### AC-88
- **User Action:** A user creates a task template with actor slots, saves it, refreshes the page, and reopens the template.
- **Expected:** Actor slots are fully persisted with names, type constraints, and default bindings, and the API returns `actor_slots` on reload.
- **Not Criteria:** Actor slots must not exist only in frontend state or disappear on reload.
- **Requirements:** REQ-111
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:199: "Without the migration, actor slots are lost on page reload." -- This is the direct user-facing acceptance condition for the fix.


#### AC-89
- **User Action:** A user edits a custom tool and then opens a Role editor that references it.
- **Expected:** The tool detail view updates, and the Role editor checklist shows the updated tool metadata after query invalidation.
- **Not Criteria:** Editing must not create a second tool record or leave stale tool metadata in the Role editor.
- **Requirements:** REQ-110
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:131: "Tool Library keeps list, detail, and editor flows." -- The updated tool must propagate across that whole flow.


#### AC-90
- **User Action:** A user tries to delete a custom tool that is still referenced by roles.
- **Expected:** Delete is blocked with the referencing role names; after removing those role references, the standard delete confirmation appears and the tool disappears from Role editor checklists.
- **Not Criteria:** The tool must not be deleted while still referenced, and deleted tools must not remain selectable.
- **Requirements:** REQ-110
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:42: "Tool delete checks role usage, not workflow refs." -- This is the intended blocking contract for tool deletion.


#### AC-91
- **User Action:** A user submits oversized JSON or an invalid entity name through the UI or API.
- **Expected:** The server rejects the request with the documented 413 or 422 validation errors and no record is created or updated.
- **Not Criteria:** Validation must not exist only in the frontend, and malformed or oversized payloads must not be stored.
- **Requirements:** REQ-114
- **Citations:** - [research] OWASP Input Validation Cheat Sheet: "Server-side validation must happen before processing untrusted input." -- This supports the rejection behavior for malformed library payloads.


#### AC-92
- **User Action:** A user opens the compose library sidebar and library-selection pickers in the editor.
- **Expected:** The available library surfaces are Roles, Output Schemas, Task Templates, and Tools, with no Plugins page and no PluginPicker affordance.
- **Not Criteria:** A Plugins library, plugin endpoint affordance, or PluginPicker must not reappear in the rebased SF-7 surface.
- **Requirements:** REQ-115
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:205: "Pickers are RolePicker, SchemaPicker, and TemplateBrowser." -- The picker surface already excludes plugins in the revised plan.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:213: "Do not create a PluginPicker." -- This is the concrete artifact-level guardrail.


#### AC-93
- **User Action:** User A attempts to access User B's role, schema, template, or tool by direct API or deep link.
- **Expected:** The request resolves as not found, and no foreign resource metadata is revealed.
- **Not Criteria:** The API must not return 403 or otherwise confirm that the other user's library item exists.
- **Requirements:** REQ-113
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:24: "Return `404` for other users' records." -- SF-7 inherits the compose tenancy boundary.


#### AC-94
- **User Action:** A user opens a library list, then revisits it in the same session after the initial load.
- **Expected:** The cached list renders within the warm-cache 500ms target and background refresh does not block interaction; a cold visit still resolves within the 2-second target.
- **Not Criteria:** The user must not sit behind a spinner beyond the cold-load target, and cached revisits must not feel like full reloads.
- **Requirements:** REQ-112
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/compile-sources-prd.md:6710: "Warm-cache 500ms; cold fetches 2 seconds." -- This directly defines the page-load acceptance thresholds.


## User Journeys

### Broad Journeys
<!-- SF: broad -->

#### J-1: Create a workflow from scratch
- **Actor:** Platform developer (hobby+ tier)
- **Path:** happy
- **Preconditions:** User is authenticated on the iriai platform with hobby or pro tier

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | User navigates to tools.iriai.app and sees the workflow composer card (available for hobby+ tier) | Tool cards displayed, composer card is enabled/clickable |  |  |
| 2 | User clicks the composer card and is routed to the composer app | Workflows List page loads showing any existing workflows |  |  |
| 3 | User clicks 'New Workflow', enters name and description | Empty workflow editor canvas opens with node palette sidebar visible |  |  |
| 4 | User drags an Ask node from the palette onto the canvas | Ask node appears on canvas, node inspector panel opens on the right |  |  |
| 5 | User configures the Ask node: creates a role inline (system prompt, tools, model), writes a prompt template with {{ variable }} interpolation, selects an output schema from the library | Inspector shows all configuration fields, role is created inline with option to promote to library |  |  |
| 6 | User drags a Branch node and connects an edge from the Ask output | Edge appears with type annotation showing the data type flowing. Edge inspector allows adding a transform |  |  |
| 7 | User adds a Loop node on the rejection path of the Branch and connects it back to form a gate-and-revise pattern | Loop node contains an inline sub-canvas for the retry body. DAG structure is clearly visible |  |  |
| 8 | User selects multiple nodes and groups them into a Phase, configuring phase hooks and skip conditions | Visual bounding box appears around the selected nodes with phase label and configuration |  |  |
| 9 | User clicks Validate in the toolbar | Validation runs — type flow across edges is checked, required fields verified, any errors highlighted on the canvas |  |  |
| 10 | User clicks Save, then Export YAML | Workflow saved to database with version 1. YAML file downloaded. YAML pane shows the generated output |  |  |

- **Outcome:** A new declarative workflow YAML is saved, validated, and exportable for use with any iriai-compose runner
- **Requirements:** REQ-1, REQ-2, REQ-10, REQ-12, REQ-13, REQ-14, REQ-15, REQ-16


#### J-2: Translate iriai-build-v2 planning workflow to declarative format
- **Actor:** Platform developer / migration engineer
- **Path:** happy
- **Preconditions:** iriai-build-v2 planning workflow exists as imperative Python. Declarative format and primitives are implemented in iriai-compose.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Developer analyzes the 6 phases of the planning workflow (scoping, PM, design, architecture, plan review, task planning) | Each phase maps to a Phase group in the declarative format with appropriate hooks |  |  |
| 2 | Developer creates roles for all actors (lead_pm, pm_decomposer, pm_compiler, designer, architect, reviewers, user) in the Roles Library | Roles saved with system prompts, tool lists, and model preferences from iriai-build-v2 |  |  |
| 3 | Developer builds the PM phase DAG: broad interview (Ask loop) → decompose (Ask + Branch for gate) → per-subfeature Fold (with tiered context edge transform) → integration review → conditional revision (Branch) → compile (Ask) → interview gate review (Loop with nested Ask + Branch) | All patterns representable using Ask, Fold, Loop, Branch primitives with edge transforms |  |  |
| 4 | Developer saves the gate-and-revise pattern as a Custom Task Template for reuse across phases | Template appears in the node palette, usable as a single node in design and architecture phases |  |  |
| 5 | Developer exports the complete YAML and runs it through iriai-compose's testing framework with mock runtimes | All nodes reached in expected order, artifacts produced at correct keys, branch decisions match expected paths |  |  |
| 6 | Developer runs the YAML workflow via iriai-compose run() with real Claude runtimes | Workflow executes identically to the imperative Python version — same phases, same actor interactions, same artifact outputs |  |  |

- **Outcome:** Planning workflow is fully represented as YAML DAG, passes validation, and executes identically through iriai-compose run()
- **Requirements:** REQ-1, REQ-2, REQ-3, REQ-4, REQ-7, REQ-8, REQ-9, REQ-18


#### J-3: Build and reuse a custom task template
- **Actor:** Platform developer
- **Path:** happy
- **Preconditions:** User has the workflow composer open with an existing workflow

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | User builds a subgraph: Ask (produce artifact) → Branch (gate approval) → on rejection: Loop back with Ask (revise with feedback) | Subgraph visible on canvas with correct edge connections |  |  |
| 2 | User selects the subgraph nodes and clicks 'Save as Template' | Dialog prompts for template name, description, and input/output interface definition |  |  |
| 3 | User defines inputs (actor role, prompt, output schema) and outputs (approved artifact text) | Template saved to Custom Task Templates library |  |  |
| 4 | In a different workflow, user drags the saved template from the node palette | Template appears as a single node with the defined input/output ports |  |  |
| 5 | User configures the template node's inputs (selects a designer role, provides design prompt) | Template instance configured, internal subgraph hidden but viewable via expand/inspect |  |  |

- **Outcome:** A reusable gate-and-revise pattern is saved as a template and used across multiple workflows
- **Requirements:** REQ-13, REQ-18


#### J-4: Validation catches type mismatch on edge
- **Actor:** Platform developer
- **Path:** failure
- **Preconditions:** User is building a workflow with multiple connected nodes
- **Failure Trigger:** User connects an Ask node outputting a PRD schema to a Branch node expecting a Verdict schema

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | User draws an edge from an Ask node (output_type: PRD) to a Branch node (expects: Verdict with .approved boolean) | Edge appears with a warning indicator (type mismatch) |  |  |
| 2 | User clicks Validate | Validation error highlights the edge: 'Type mismatch — PRD does not satisfy Verdict. Add a transform or insert a node.' |  |  |
| 3 | User clicks the edge and adds a transform from the Transforms Library, or inserts an Ask node between them to produce a Verdict from the PRD | Validation re-runs automatically, error clears, edge shows green type annotation |  |  |

- **Outcome:** User is alerted to a type mismatch between connected nodes and can fix it by adding a transform
- **Requirements:** REQ-3, REQ-13, REQ-14
- **Related Journey:** J-1


#### J-5: Configure and use a plugin in the DAG
- **Actor:** Platform developer
- **Path:** happy
- **Preconditions:** User needs artifact hosting in their workflow (like iriai-build-v2's HostedInterview pattern)

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | User navigates to Plugins Registry and finds the hosting plugin | Plugin card shows its interface: inputs (artifact_key, content, label), outputs (url), and configuration (hosting_url) |  |  |
| 2 | User configures a plugin instance with their hosting URL | Configured instance saved and available in the node palette |  |  |
| 3 | User drags the hosting plugin node into the workflow after an Ask node that produces an artifact | Plugin node appears with input ports matching the declared interface |  |  |
| 4 | User connects the Ask output to the plugin input, optionally adding an edge transform to extract the artifact text | Edge shows type flow: Ask output → transform → plugin input. Plugin node shows it will host the artifact |  |  |

- **Outcome:** A hosting plugin is configured and used as a node in the workflow DAG
- **Requirements:** REQ-4, REQ-13, REQ-20


### SF-1: Declarative Schema & Primitives
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
<!-- SF: composer-app-foundation -->

#### J-24: Developer launches Compose from the Tools Hub
- **Actor:** Authenticated hobby-tier or pro-tier platform developer
- **Path:** happy
- **Preconditions:** The developer has a valid platform account and can authenticate with auth-service.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open `tools.iriai.app`. | A static authenticated tools hub loads with a developer-tools card catalog. | The page is blank while auth resolves, or protected cards appear actionable before auth state is known. | [decision] D-12 |
| 2 | Click the Workflow Composer card. | The browser navigates in the same tab to `compose.iriai.app`. | A new tab opens, or the route points to a stale `tools/iriai-workflows` URL. | [decision] D-A3 |
| 3 | Wait for compose to load. | The compose shell opens on the Workflows landing experience with the four SF-5 folders visible. | Plugin or tool-library surfaces appear in the SF-5 shell, or editor boot blocks the shell from rendering. | [code] .iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/design-decisions.md:13 |

- **Outcome:** The developer reaches the canonical compose app entry point through the tools hub.
- **Requirements:** REQ-75, REQ-90, REQ-91


#### J-25: Developer creates a workflow and starts editor bootstrap
- **Actor:** Authenticated compose user
- **Path:** happy
- **Preconditions:** The user is on the compose Workflows landing view.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Click the primary new-workflow action and submit the create form. | A workflow row is created and `WorkflowVersion` v1 exists immediately. | The workflow is created without an audit version, or the list requires a full refresh. | [decision] D-13 |
| 2 | Open the new workflow. | The app routes to `/workflows/{id}/edit` without losing authenticated state. | The route breaks auth state or opens an unknown workflow id. | [code] platform/deploy-console/deploy-console-frontend/src/App.tsx:79 |
| 3 | Let the editor bootstrap start. | The frontend requests both the workflow record and `GET /api/schema/workflow` before mounting schema-driven editor affordances. | The editor treats a static bundled schema as authoritative or skips runtime schema fetch. | [decision] D-GR-22 |

- **Outcome:** A new workflow exists with version 1 and is ready for schema-aware editing.
- **Requirements:** REQ-79, REQ-84, REQ-85, REQ-87, REQ-89


#### J-26: User saves and exports a canonical workflow definition
- **Actor:** Authenticated compose user editing a workflow
- **Path:** happy
- **Preconditions:** A workflow exists and the editor has schema data available.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Trigger save from the editor. | The payload is validated against the canonical runtime contract before persistence. | The save path persists the editor's flat internal graph as canonical YAML. | [code] .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1006 |
| 2 | Save completes. | The workflow stores nested phase YAML, hook connections remain edges, and a new immutable workflow version is appended. | Save writes a separate hooks section, stores serialized `port_type`, or updates workflow YAML without creating the next version row. | [decision] D-GR-22 |
| 3 | Export the workflow. | The downloaded YAML matches the same canonical structure used for save and validation. | Export emits a different schema shape than the one the backend validated and stored. | [code] .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1004 |

- **Outcome:** The saved and exported workflow remains portable and runtime-compatible.
- **Requirements:** REQ-79, REQ-85, REQ-88


#### J-27: User imports a valid workflow YAML
- **Actor:** Authenticated compose user
- **Path:** happy
- **Preconditions:** The user has a valid iriai-compose YAML file (e.g. exported from iriai-build-v2).

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Navigate to the Workflows landing view and click Import. | An import dialog or file picker opens. | The import action routes to an existing workflow detail or requires an existing workflow id in the URL. | [decision] D-SF5-R5 |
| 2 | Upload the YAML file. | The frontend sends `POST /api/workflows/import` with the file content. | The request is routed to `POST /api/workflows/{id}/import`, conflating import with an instance-level update. | [decision] D-SF5-R5 |
| 3 | Import succeeds. | A new user-owned workflow row is created with `WorkflowVersion` v1, and the user is navigated to the new workflow detail. | The import overwrites an existing workflow, creates a workflow without a version row, or requires a full-page refresh to see the new entry. | [decision] D-13 |
| 4 | Mutation hooks fire. | SF-5's mutation hook interface emits a `created` event (not `imported`) for the new Workflow entity. | The import triggers a hook event kind other than `created`, or no hook fires for the new workflow. | [decision] D-SF5-R6 |

- **Outcome:** A valid YAML file is imported as a new user-owned workflow with version 1, and downstream hook subscribers receive a standard `created` event.
- **Requirements:** REQ-79, REQ-85, REQ-88, REQ-92


#### J-28: User starts from a starter template
- **Actor:** Authenticated compose user
- **Path:** happy
- **Preconditions:** The user is on the compose Workflows landing view. System starter templates have been seeded by the Alembic data migration.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Request `GET /api/workflows/templates`. | The backend returns system-seeded starter templates (including iriai-build-v2 planning/develop/bugfix workflows) sourced from `user_id='__system__'` DB rows. | Template content is loaded from a filesystem path at request time rather than from the DB. | [decision] D-SF5-R4 |
| 2 | Select a starter template to use. | A duplicate action is triggered, creating a new user-owned workflow row with version 1 derived from the template. | The system template row is modified, or the duplicate action fails because the template's `user_id` does not match the caller's id. | [decision] D-SF5-R4 |
| 3 | Open the duplicated workflow. | The new workflow appears in the user's workflow list and can be edited, saved, and exported. | The duplicate points back to the system template row, or the new workflow has no associated version 1 row. | [decision] D-13 |

- **Outcome:** The user has an editable copy of the starter template; the system template row is unchanged.
- **Requirements:** REQ-79, REQ-85


#### J-29: Canonical schema endpoint is unavailable during editor bootstrap
- **Actor:** Authenticated compose user opening a workflow
- **Path:** failure
- **Preconditions:** A workflow exists, but the schema endpoint is temporarily unavailable.
- **Failure Trigger:** `GET /api/schema/workflow` fails or times out.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open `/workflows/{id}/edit` while the schema endpoint is unavailable. | The editor route shows a blocking, recoverable schema-load error state with retry and back navigation. | The app silently falls back to a stale local schema or spins forever. | [decision] D-GR-22 |
| 2 | Retry after the backend recovers. | The editor bootstrap succeeds using the same runtime schema endpoint. | Recovery requires a hard refresh or a manual local-schema workaround. | [code] platform/deploy-console/deploy-console-frontend/src/App.tsx:46 |

- **Outcome:** Schema failure is explicit and recoverable without changing contracts.
- **Requirements:** REQ-87, REQ-89
- **Related Journey:** J-2


#### J-30: User imports malformed YAML
- **Actor:** Authenticated compose user importing a workflow file
- **Path:** failure
- **Preconditions:** The user has a YAML file to import.
- **Failure Trigger:** The uploaded file is malformed YAML.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Upload malformed YAML via `POST /api/workflows/import`. | The backend returns parse errors and creates no workflow or version rows. | A partial workflow is persisted; the user receives only a generic failure with no path/message context; or the error comes from `POST /api/workflows/{id}/import`. | [decision] D-SF5-R5 |
| 2 | Correct the file and retry import. | Import succeeds, creates a workflow plus version 1, and may surface non-blocking validation warnings if the YAML is structurally parseable but not fully schema-valid. | Retry leaves behind duplicate partial rows from the failed attempt. | [decision] D-GR-22 |

- **Outcome:** Invalid YAML is rejected safely, and retrying produces a clean imported workflow.
- **Requirements:** REQ-79, REQ-85, REQ-88
- **Related Journey:** J-8


#### J-31: Cross-user workflow access is denied without leaking existence
- **Actor:** Authenticated platform developer
- **Path:** failure
- **Preconditions:** Another user's workflow id is known or guessed.
- **Failure Trigger:** The actor requests a workflow they do not own.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Navigate directly to another user's workflow detail or editor route. | The API and UI return a not-found result. | The UI or API confirms that the workflow exists but belongs to someone else. | [code] .iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/plan.md:553 |
| 2 | Return to the user's own workflow list. | The user's own data remains available and unchanged. | The failed lookup corrupts local session state or reveals cross-user metadata. | [decision] D-2 |

- **Outcome:** Unauthorized access is blocked without existence leakage.
- **Requirements:** REQ-80, REQ-81
- **Related Journey:** J-2


#### J-32: Tools hub session is missing and the user must authenticate first
- **Actor:** Platform developer without an active browser session
- **Path:** failure
- **Preconditions:** The user opens the tools hub from a logged-out browser state.
- **Failure Trigger:** Protected tools-hub content is requested without a valid auth session.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open `tools.iriai.app` without an active session. | The user is prompted to authenticate before protected tool cards are usable. | The hub exposes protected tool access states without auth or renders a blank page. | [code] platform/deploy-console/deploy-console-frontend/src/App.tsx:46 |
| 2 | Complete authentication and return to the tools hub. | The developer-tools card catalog appears and the Workflow Composer card can be used normally. | The user is stranded on the callback route or loses the intended return path. | [code] platform/deploy-console/deploy-console-frontend/src/App.tsx:79 |

- **Outcome:** Missing session state is resolved through normal auth flow without exposing protected tools.
- **Requirements:** REQ-80, REQ-91
- **Related Journey:** J-1


### SF-6: Workflow Editor & Canvas
<!-- SF: workflow-editor -->

#### J-33: Build A Workflow From Scratch
- **Actor:** Platform developer with an authenticated workflow-editing session in the compose app
- **Path:** happy
- **Preconditions:** A new or existing workflow is open in `tools/compose/frontend` and the runtime schema endpoint is healthy.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open the editor page for a workflow. | The canvas, palette, and toolbar render after the workflow record and `/api/schema/workflow` finish loading. | The editor boots against a stale local schema or renders an incomplete editing surface. | [decision] D-34 |
| 2 | Drag an Ask node to the canvas and open its inspector. | The node appears with visible hook ports and the inspector opens near it with prompt and output-type editing fields. | Hooks are hidden from the node or only editable through hidden metadata. | [decision] D-13 |
| 3 | Add a Branch node, connect the Ask output, and create named paths. | A typed data edge appears and Branch path handles update live on the node. | Branch routing requires a separate node type or delayed page refresh to show path ports. | [decision] D-11 |
| 4 | Create a phase around several nodes, switch it to fold mode, validate, and save. | The phase becomes a fold container, validation merges client/server results, and save persists canonical nested YAML. | Save flattens nesting, validation wipes the canvas, or YAML emits separate hook metadata. | [decision] D-GR-22 |

- **Outcome:** The user visually authors a valid workflow that round-trips to the canonical iriai-compose YAML contract.
- **Requirements:** REQ-93, REQ-94, REQ-95, REQ-96, REQ-97, REQ-98, REQ-99, REQ-100, REQ-101


#### J-34: Build Nested Fold And Loop Phases
- **Actor:** Platform developer modeling per-subfeature iteration
- **Path:** happy
- **Preconditions:** The workflow contains upstream data that can serve as a collection source.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Arrange several Ask, Branch, and Plugin nodes for a subfeature pipeline. | The nodes remain loose on the canvas until explicitly grouped. | Nodes auto-group without a deliberate phase action. | [decision] D-10 |
| 2 | Create an outer phase and set it to fold mode. | The outer phase exposes fold configuration and child nodes stay visible inside the phase. | Fold mode requires a standalone Fold node type or hides child nodes unexpectedly. | [decision] D-8 |
| 3 | Create a nested inner phase and set it to loop mode with `max_iterations`. | The inner phase remains nested and shows distinct `condition_met` and `max_exceeded` exits. | The loop has only one exit or the inner phase breaks parent containment. | [decision] D-27 |

- **Outcome:** The user represents nested fold-with-inner-loop behavior directly in the canvas and saves it losslessly to nested YAML.
- **Requirements:** REQ-96, REQ-97, REQ-101


#### J-35: Import Malformed YAML
- **Actor:** Platform developer importing an external workflow file
- **Path:** failure
- **Preconditions:** An existing workflow is open in the editor.
- **Failure Trigger:** The selected YAML file contains syntax errors or parse failures.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Choose File -> Import and select a YAML file. | The editor asks for confirmation before replacing the current canvas. | The existing canvas changes before confirmation. | [decision] D-29 |
| 2 | Confirm the import and hit a parse error. | The editor shows a red error toast with a line-numbered parse message and leaves the current canvas untouched. | The import partially mutates the existing workflow or loses the prior state. | [decision] D-29 |
| 3 | Retry with parseable but structurally invalid YAML. | The workflow loads with explicit validation warnings in the validation panel. | Warnings are silently swallowed or invalid content is treated as fully clean. | [decision] D-28 |

- **Outcome:** Malformed YAML is rejected safely; parseable-but-invalid YAML stays repairable inside the editor.
- **Requirements:** REQ-100, REQ-101
- **Related Journey:** J-1


#### J-36: Schema Endpoint Unavailable On Editor Load
- **Actor:** Platform developer opening a workflow
- **Path:** failure
- **Preconditions:** The workflow API is reachable but `/api/schema/workflow` is failing or timing out.
- **Failure Trigger:** The canonical runtime schema cannot be fetched during editor boot.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open `/workflows/:id/edit`. | The editor shows a blocking error state explaining that the runtime schema could not be loaded. | The editor silently falls back to a stale local schema copy or permits edits against an unknown contract. | [decision] D-GR-22 |
| 2 | Retry after the schema endpoint recovers. | The editor loads normally and the workflow opens. | The failure is cached permanently after the endpoint is healthy again. | [decision] D-34 |

- **Outcome:** The user never edits against a stale or untrusted schema contract.
- **Requirements:** REQ-99, REQ-105
- **Related Journey:** J-1


#### J-37: Core Editor Works On The Five-table Compose Foundation
- **Actor:** Platform developer editing workflows during a staged compose rollout
- **Path:** happy
- **Preconditions:** `tools/compose/frontend` and `tools/compose/backend` are deployed with the SF-5 foundation tables (`workflows`, `workflow_versions`, `roles`, `output_schemas`, `custom_task_templates`), while SF-7 plugin/reference-index endpoints are not yet live.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open `/workflows/:id/edit` in the compose app. | The editor boots from the workflow payload plus `/api/schema/workflow` and shows the core canvas. | Boot attempts to route through `tools/iriai-workflows` or blocks on optional plugin/reference APIs. | [decision] D-SF5-R1 |
| 2 | Open Ask or template inspectors that use library-backed pickers. | Roles, output schemas, and task templates load from the compose foundation and remain usable. | Picker rendering requires plugin-management tables or `workflow_entity_refs` data. | [decision] D-SF5-R2 |
| 3 | Save the current workflow. | Save and validation succeed through the workflow/version endpoints while preserving the nested YAML contract. | Save requires `/api/plugins`, reference-index endpoints, or any SQLite-local persistence path. | [decision] D-SF5-R2 |

- **Outcome:** Core editor flows remain usable on the accepted five-table compose foundation, with SF-7 expansion kept additive.
- **Requirements:** REQ-99, REQ-100, REQ-106


#### J-38: Optional Library Expansion Is Not Yet Available
- **Actor:** Platform developer editing workflows on the compose foundation
- **Path:** failure
- **Preconditions:** The editor is open and optional SF-7 plugin or reference-check surfaces are not deployed.
- **Failure Trigger:** The user invokes an affordance that belongs to the later SF-7 library/reference expansion.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Click an optional plugin-library or reference-check affordance from the editor chrome. | The control is disabled or shows a non-blocking unavailable/coming-soon message. | The editor crashes, shows a blank screen, or forces the user out of the current workflow. | [decision] D-SF5-R2 |
| 2 | Continue editing and save the workflow. | The current canvas state stays intact and core save succeeds through the compose foundation endpoints. | Unsaved edits are lost or save is blocked because optional SF-7 surfaces are missing. | [decision] D-SF5-R1 |

- **Outcome:** Missing SF-7 surfaces degrade gracefully without blocking core workflow editing.
- **Requirements:** REQ-106
- **Related Journey:** J-5


### SF-7: Libraries & Registries
<!-- SF: libraries-registries -->

#### J-39: Create and Use a Role from the Roles Library
- **Actor:** Platform developer with at least one saved workflow in compose
- **Path:** happy
- **Preconditions:** Authenticated user in the compose app; Roles Library is accessible from the rebased compose shell.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open the Roles Library inside compose. | The list view loads with existing role cards, search, and a primary New Role action. | Other users' roles are not visible, and the user does not land in a stale `tools/iriai-workflows` surface. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:90 |
| 2 | Create a new role with a name, model, prompt, and selected tools. | The Role editor accepts the values and shows built-in and registered tools as selectable groups. | Registered tools are not missing, and invalid names are not accepted. | [code] /Users/danielzhang/src/iriai/iriai-compose/iriai_compose/actors.py:13 |
| 3 | Save the role and select it from the Ask-node role picker in a workflow. | The role appears in the library and becomes selectable from RolePicker in the workflow editor. | Duplicate role rows are not created, and the picker does not require delete-preflight data just to list roles. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:205 |

- **Outcome:** A reusable role exists in the compose library and can be attached to saved workflow content.
- **Requirements:** REQ-110, REQ-113, REQ-115


#### J-40: Delete a Role Referenced by Saved Workflows
- **Actor:** Platform developer cleaning up an unused role
- **Path:** failure
- **Preconditions:** A saved workflow currently references the role through persisted library data.
- **Failure Trigger:** The user initiates delete on a role that is still referenced by saved workflow content.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open delete for the referenced role. | A blocking dialog appears before any destructive request, listing the referencing workflows from the SF-7 reference index. | The role is not deleted, and the system does not parse workflow YAML or require a DELETE attempt just to discover references. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:26 |
| 2 | Remove the role reference in the workflow editor and save the workflow. | The workflow save succeeds and the role's reference status updates on the next delete preflight. | Unsaved editor changes are not treated as cleared references, and stale workflow names do not persist after the saved change. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:12 |
| 3 | Retry delete after all saved references are removed. | The standard delete confirmation appears and the user can safely remove the role. | The reference list is not stale, and the role is not blocked by a foundation-owned table that SF-5 was never supposed to create. | [decision] D-GR-29 |

- **Outcome:** The user understands why deletion was blocked, clears the saved references safely, and then deletes the role without stale index data.
- **Requirements:** REQ-108, REQ-109
- **Related Journey:** J-1


#### J-41: Delete a Tool Referenced by Roles
- **Actor:** Platform developer attempting to remove a custom tool
- **Path:** failure
- **Preconditions:** The custom tool is still referenced by one or more saved roles.
- **Failure Trigger:** The user initiates delete on a tool that is still referenced by role `tools` arrays.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open delete for the referenced tool. | A blocking dialog lists the referencing roles and offers only Close until the role references are removed. | The tool is not deleted, and the dialog does not show workflow names or `workflow_entity_refs` validation codes. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:42 |
| 2 | Remove the tool from the referencing roles and save those roles. | The roles save successfully with updated `tools` arrays. | Other tool selections are not corrupted, and the saved role shape does not switch from string identifiers to a new ID model. | [code] /Users/danielzhang/src/iriai/iriai-compose/iriai_compose/actors.py:13 |
| 3 | Retry tool delete after the role saves complete. | The normal delete confirmation appears and the deleted tool disappears from later Role editor checklists. | The tool is not deleted while still referenced, and deleted tools do not remain selectable in the role editor. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:43 |

- **Outcome:** The tool is deleted only after all saved role references are removed, preserving role integrity.
- **Requirements:** REQ-110
- **Related Journey:** J-1


#### J-42: Persist Actor Slots in a Task Template
- **Actor:** Platform developer creating a reusable multi-agent task template
- **Path:** happy
- **Preconditions:** Authenticated user in the compose Task Templates editor with the scoped template canvas available.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Create a task template subgraph and define one or more actor slots. | The editor captures each actor slot's name, type constraint, and optional default binding. | Duplicate or unnamed actor slots are not accepted, and the editor does not imply that client-only state is enough. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:179 |
| 2 | Save the template, refresh the page, and reopen it. | The template reloads with the same actor slots intact because the server persists and returns `actor_slots`. | Actor slots are not dropped on reload, and they do not rely on local browser persistence to survive a refresh. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:199 |

- **Outcome:** The task template stores reusable actor-slot definitions that survive reloads and can be reused in later workflows.
- **Requirements:** REQ-111


#### J-43: Reject Invalid Actor Slot Definitions
- **Actor:** Platform developer editing a task template
- **Path:** failure
- **Preconditions:** Authenticated user is in the task-template editor and attempts to save invalid actor-slot data.
- **Failure Trigger:** The user enters malformed actor-slot data, such as duplicate names or an invalid default binding.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Enter invalid actor-slot definitions and attempt to save the template. | The UI and API reject the save with clear validation feedback describing the invalid actor-slot data. | The template is not partially saved, and invalid actor-slot payloads are not silently normalized into persisted data. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:207 |
| 2 | Correct the actor-slot data and save again. | The save succeeds, and a refresh reopens the template with only the corrected actor-slot definitions. | The user does not need to rely on local workarounds, and the server does not preserve the previously invalid slot payload. | [research] OWASP Input Validation Cheat Sheet |

- **Outcome:** The user is prevented from persisting invalid actor-slot data, corrects the issue, and saves a consistent template state.
- **Requirements:** REQ-111, REQ-114
- **Related Journey:** J-4


## Security Profile

### broad

- **Compliance Requirements:** No specific compliance requirements. Standard platform security practices apply.
- **Data Sensitivity:** Workflow configs may contain system prompts with proprietary process knowledge. Role definitions may contain sensitive operational instructions. No PII stored directly.
- **Pii Handling:** No PII in workflow configs. User identity (user_id from JWT sub claim) associated with owned resources for access control.
- **Auth Requirements:** JWT-based authentication via auth-service. All composer API endpoints require valid access token. Tools hub reads dev_tier claim for tier gating. Backend validates JWT via auth-python (JWKS endpoint).
- **Data Retention:** Workflow configs and versions retained indefinitely. No automatic purging.
- **Third Party Exposure:** Exported YAML files may be shared externally. They contain role prompts and workflow structure but no credentials or secrets. Plugin configurations may reference external service URLs.
- **Data Residency:** SQLite database local to the FastAPI backend deployment. Workflow YAML exports are portable files.
- **Risk Mitigation Notes:** Transforms and hooks reference Python functions by name — the runner resolves them at execution time, not the builder. No arbitrary code execution in the composer. Plugin credentials are stored in the runner environment, not in the YAML.

### declarative-schema

- **Compliance Requirements:** None beyond standard internal engineering controls.
- **Data Sensitivity:** Internal. Workflow YAML and JSON Schema expose proprietary orchestration structure, prompts, and typed interfaces, but not operational secrets by default.
- **Pii Handling:** No direct PII handling in the schema module. Human actors are declared abstractly rather than by storing personal profile data.
- **Auth Requirements:** The schema package itself has no intrinsic auth boundary. When exposed to the composer, the canonical `/api/schema/workflow` delivery path should inherit the backend's authenticated API policy rather than rely on a public static file.
- **Data Retention:** Not applicable for the library artifact itself. Exported YAML or generated JSON Schema retention is determined by the consuming service or repository.
- **Third Party Exposure:** YAML or JSON Schema may be shared externally, but the contract should expose workflow structure and types only; it should not require embedded credentials or secrets. Static `workflow-schema.json` is build/test only, reducing accidental runtime drift from checked-in artifacts.
- **Data Residency:** Not applicable at the library level; residency is inherited from whichever backend serves or stores workflows.
- **Risk Mitigation Notes:** The main product risk is contract drift between schema producer and consumers. D-GR-22, D-GR-30, and D-GR-35 mitigate that by making nested YAML, actor_type:agent|human, closed root field set, edge-based hook serialization, per-port branch conditions, and `/api/schema/workflow` the single canonical interface. All BranchNode per-port conditions are expressions subject to the sandbox; `switch_function` is rejected. Validation must fail fast on stale `switch_function`, the old `condition_type`/`condition`/`paths` branch shape, serialized `port_type`, or separate hook-section assumptions so downstream tools cannot silently diverge.

### dag-loader-runner

- **Compliance Requirements:** None beyond standard platform engineering controls.
- **Data Sensitivity:** Internal workflow definitions, prompts, typed interfaces, and execution metadata.
- **Pii Handling:** No new direct PII surface in the loader/runner itself. Human actors are schema-level interaction definitions (identity, channel) rather than stored credentials or profiles.
- **Auth Requirements:** Library-level runtime has no auth boundary; composer access to GET /api/schema/workflow is handled by SF-5, but the endpoint must remain the canonical runtime schema source for authoring.
- **Data Retention:** Execution-history retention is determined by the consuming application; SF-2 itself only defines the runtime result surface.
- **Third Party Exposure:** Only through configured agent/plugin runtimes or host-managed human-interaction channels supplied by the consuming application.
- **Data Residency:** No library-level residency guarantees.
- **Risk Mitigation Notes:** Treat the current SF-1 PRD as the only authoritative wire contract and fail fast on all stale variants. Reject alternate root fields (stores, plugin_instances), alternate actor discriminators (interaction), serialized hook metadata (port_type, hooks sections), and stale branch routing surfaces (switch_function, old condition_type/condition/paths, output_field mode per port) so downstream tools cannot drift back toward multiple workflow dialects. Per D-GR-35: merge_function is valid for gather and must not be rejected. All BranchNode per-port output conditions are expressions evaluated under the shared sandbox security contract (AST allowlist, timeout, size limits); there is no output_field declarative-lookup mode on branch output ports.

### testing-framework

- **Compliance Requirements:** None; testing-only library module.
- **Data Sensitivity:** Public synthetic test data.
- **Pii Handling:** No PII expected; mocks and fixtures use synthetic inputs.
- **Auth Requirements:** None at the library surface.
- **Data Retention:** Snapshot files remain developer-managed test artifacts.
- **Third Party Exposure:** None; no external calls are required for the revised contract.
- **Data Residency:** N/A.
- **Risk Mitigation Notes:** Prevent accidental ABI widening in downstream consumers. SF-3 must not force a breaking `AgentRuntime.invoke()` change, must not define a competing runtime-context contract, and must not reintroduce a mandatory checkpoint/resume dependency that SF-2 explicitly does not own. D-SF3-16 is the primary non-compliance risk; its removal must be verified before any SF-3 implementation file is written.

### workflow-migration

- **Compliance Requirements:** None
- **Data Sensitivity:** Internal
- **Pii Handling:** No PII in YAML workflow files or migration test fixtures.
- **Auth Requirements:** No new auth requirement for this PRD revision; runtime/plugin auth remains inherited from the consuming environment.
- **Data Retention:** YAML workflows, parity fixtures, and PRD artifacts remain version-controlled source files with standard repository retention.
- **Third Party Exposure:** No new third-party exposure introduced by this revision; runtime integrations still reference external services only through configured runtimes/plugins.
- **Data Residency:** No geographic residency constraint identified.
- **Risk Mitigation Notes:** The revision makes SF-2 the explicit and sole ABI owner so downstream consumers (SF-4 included) cannot widen the runtime boundary independently. Node-aware behavior is documented as ContextVar-based (runner-managed, not caller-supplied). Checkpoint/resume is explicitly an application-layer concern and must not re-enter the SF-2 core contract through consumer-layer workarounds. SF-4 is positioned as a downstream consumer; any SF-4 language that implies co-ownership of the SF-2 runtime boundary must be treated as a stale artifact requiring correction.

### composer-app-foundation

- **Compliance Requirements:** None beyond standard platform security controls.
- **Data Sensitivity:** Internal workflow definitions, prompts, reusable role/task metadata, and JSON Schemas.
- **Pii Handling:** No new end-user PII is stored in SF-5. The backend uses opaque JWT-derived `user_id` for ownership and reads `dev_tier` for tool access gating.
- **Auth Requirements:** JWT Bearer auth via auth-service JWKS on all non-health endpoints. Browser clients use the existing homelocal auth packages and send bearer tokens to the backend.
- **Data Retention:** Workflow, role, schema, and task-template rows are soft-deleted. `workflow_versions` remain append-only audit records. Automatic purge is out of scope for v1.
- **Third Party Exposure:** YAML exports can leave the platform when users download or share them. SF-5 foundation must not store plugin runtime secrets, custom tool configs, or runner-managed credentials.
- **Data Residency:** Railway-hosted PostgreSQL in the configured compose deployment region.
- **Risk Mitigation Notes:** Keep production CORS explicit; rely on bearer-token API calls rather than cookie-bound mutation flows; do not log raw YAML or prompt bodies; use `/api/schema/workflow` as the single runtime schema source; enforce the five-table SF-5 boundary so plugin, tools, and reference-index surfaces only land in their owning subfeatures; expose mutation hooks post-commit using the exhaustive four-kind enumeration (`created`, `updated`, `soft_deleted`, `restored`) so SF-7 can maintain reference-index state without SF-5 owning `workflow_entity_refs` rows; keep starter templates as `user_id='__system__'` DB rows so they are subject to the same query/access layer as user-owned workflows and never served from uncontrolled filesystem paths.

### workflow-editor

- **Compliance Requirements:** None specific to the editor beyond inherited platform controls.
- **Data Sensitivity:** Internal workflow definitions may contain proprietary prompts, role definitions, and process logic.
- **Pii Handling:** No workflow-specific PII is expected; authenticated user identity is used only for scoping and ownership.
- **Auth Requirements:** Standard JWT-based authenticated session via auth-react and backend auth enforcement.
- **Data Retention:** Workflow saves persist under the compose backend's `workflows` / `workflow_versions` retention behavior until deleted.
- **Third Party Exposure:** Users may export YAML externally; the editor must not embed secrets.
- **Data Residency:** Inherits `tools/compose/backend` Railway deployment and PostgreSQL/Alembic storage policy.
- **Risk Mitigation Notes:** The editor stores inline Python as data and does not execute it locally. Structural validation stays centralized through runtime schema fetch and backend `validate()`. If the canonical schema endpoint is unavailable, editing is blocked rather than falling back to a stale local schema. Core editor boot/save must stay within the accepted five-table compose foundation. Workflow mutation hooks (fired by SF-5 on create/update/delete) drive reference-index synchronization in SF-7 downstream; the editor has no write dependency on `workflow_entity_refs` and plugin/reference-index surfaces remain optional SF-7 additions.

### libraries-registries

- **Compliance Requirements:** No new external compliance regime is introduced beyond standard platform auth, tenancy isolation, and input-validation controls.
- **Data Sensitivity:** Internal — workflow-library metadata, prompts, schema JSON, and tool definitions.
- **Pii Handling:** No new high-sensitivity PII is introduced; the main identity field is JWT `sub`, used for ownership and tenancy scoping.
- **Auth Requirements:** JWT Bearer auth on compose library APIs via the existing auth-service boundary; all reads and writes are user-scoped and return 404 for cross-user access.
- **Data Retention:** Library entities follow the compose soft-delete lifecycle; reference-index rows are rebuilt or removed as workflows and library entities change. Automated hard-delete policy is out of scope for this revision.
- **Third Party Exposure:** No direct third-party exposure is added by library CRUD. Tool definitions may describe external systems, but secrets and runtime credentials are not stored in these tables.
- **Data Residency:** Compose library data resides in the compose PostgreSQL deployment region used by the accepted `tools/compose` backend.
- **Risk Mitigation Notes:** Keep SF-5 at five base tables; ship `workflow_entity_refs` and `tools` as SF-7 follow-on Alembic changes. Use non-destructive reference preflights before delete. Reject malformed or oversized payloads server-side. Do not restore plugin library surfaces. Keep tool references role-backed rather than workflow-ref-backed.

## Data Entities

### From: broad

#### Workflow <!-- SF: broad -->
- **Fields:** id: uuid, name: string, description: string, yaml_content: text, current_version: integer, created_at: datetime, updated_at: datetime, user_id: string
- **Constraints:** name unique per user; yaml_content must pass schema validation
- **New:** yes


#### WorkflowVersion <!-- SF: broad -->
- **Fields:** id: uuid, workflow_id: fk, version_number: integer, yaml_content: text, created_at: datetime, change_description: string
- **Constraints:** version_number auto-increments per workflow
- **New:** yes


#### Role <!-- SF: broad -->
- **Fields:** id: uuid, name: string, system_prompt: text, tools: json_array, model: string nullable, metadata: json, user_id: string, created_at: datetime
- **Constraints:** name unique per user
- **New:** yes


#### OutputSchema <!-- SF: broad -->
- **Fields:** id: uuid, name: string, json_schema: json, description: string, user_id: string, created_at: datetime
- **Constraints:** json_schema must be valid JSON Schema
- **New:** yes


#### CustomTaskTemplate <!-- SF: broad -->
- **Fields:** id: uuid, name: string, description: string, subgraph_yaml: text, input_interface: json, output_interface: json, user_id: string, created_at: datetime
- **Constraints:** subgraph_yaml must pass schema validation
- **New:** yes


#### PhaseTemplate <!-- SF: broad -->
- **Fields:** id: uuid, name: string, description: string, nodes_yaml: text, hooks: json, skip_conditions: json, user_id: string, created_at: datetime
- **Constraints:** nodes_yaml must pass schema validation
- **New:** yes


#### PluginConfig <!-- SF: broad -->
- **Fields:** id: uuid, plugin_type: string, instance_name: string, configuration: json, parameter_schema: json, io_types: json, user_id: string, created_at: datetime
- **Constraints:** instance_name unique per user and plugin_type
- **New:** yes


#### TransformFunction <!-- SF: broad -->
- **Fields:** id: uuid, name: string, input_type: string, output_type: string, code: text, description: string, user_id: string, created_at: datetime
- **Constraints:** name unique per user
- **New:** yes


### From: declarative-schema

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

#### HierarchicalContext <!-- SF: workflow-migration -->
- **Fields:** workflow scope, phase scope, actor scope, node scope
- **Constraints:** Merge order is `workflow -> phase -> actor -> node`, published by SF-2 as the canonical order; Duplicate keys preserve first occurrence in that order; Node identity is runtime-published through runner-managed ContextVar; not passed as a new invoke argument; SF-4 consumes this contract; it does not define or extend it
- **New:** no


#### ExecutionResult / ExecutionHistory <!-- SF: workflow-migration -->
- **Fields:** completion state, workflow output, branch paths, execution history, phase metrics
- **Constraints:** Observability surface is owned and published by SF-2; SF-4 consumes it; Phase metrics are keyed by logical phase ID; No mandatory core checkpoint/resume API is implied by or required from these structures; SF-4 must not treat the absence of a resume API as an SF-2 gap to be filled by a consumer-layer shim
- **New:** no


### From: composer-app-foundation

#### Workflow <!-- SF: composer-app-foundation -->
- **Fields:** id: UUID, name: string, description: string | null, yaml_content: text, current_version: int, is_valid: bool, user_id: string, created_at: datetime, updated_at: datetime | null, deleted_at: datetime | null
- **Constraints:** Unique per user among non-deleted workflow names (user_id, name, deleted_at IS NULL); `yaml_content` stores canonical nested workflow YAML; `current_version` mirrors the latest `workflow_versions.version_number`; Rows with `user_id='__system__'` are system-seeded starter templates; they are never soft-deleted by user actions and are returned only by `GET /api/workflows/templates`
- **New:** yes


#### WorkflowVersion <!-- SF: composer-app-foundation -->
- **Fields:** id: UUID, workflow_id: UUID, version_number: int, yaml_content: text, change_description: string | null, user_id: string, created_at: datetime
- **Constraints:** Unique (workflow_id, version_number); Append-only after creation; Version writes do not trigger mutation hook events on the parent Workflow entity
- **New:** yes


#### Role <!-- SF: composer-app-foundation -->
- **Fields:** id: UUID, name: string, prompt: text, tools: JSON list[string], model: string | null, effort: string | null, metadata: JSON object, user_id: string, created_at: datetime, updated_at: datetime | null, deleted_at: datetime | null
- **Constraints:** Unique per user among non-deleted role names; Fields align with the current iriai_compose.Role contract (name, prompt, tools, model, effort, metadata)
- **New:** yes


#### OutputSchema <!-- SF: composer-app-foundation -->
- **Fields:** id: UUID, name: string, description: string | null, json_schema: JSON, user_id: string, created_at: datetime, updated_at: datetime | null, deleted_at: datetime | null
- **Constraints:** Unique per user among non-deleted schema names; `json_schema` stores the reusable output contract referenced by workflows
- **New:** yes


#### CustomTaskTemplate <!-- SF: composer-app-foundation -->
- **Fields:** id: UUID, name: string, description: string | null, subgraph_yaml: text, input_interface: JSON, output_interface: JSON, user_id: string, created_at: datetime, updated_at: datetime | null, deleted_at: datetime | null
- **Constraints:** Unique per user among non-deleted template names; SF-5 stores the foundation record only; SF-7 may extend this table with actor_slots
- **New:** yes


### From: workflow-editor

#### WorkflowRecord <!-- SF: workflow-editor -->
- **Fields:** id, name, yaml_content, current_version, user_id, created_at, updated_at
- **Constraints:** Persisted in the compose backend `workflows` table; Version snapshots live in `workflow_versions`; Core editor boot/save must not require `workflow_entity_refs`
- **New:** no


#### WorkflowConfig <!-- SF: workflow-editor -->
- **Fields:** schema_version, name, description, actors, types, phases, edges, plugins, plugin_instances, stores, context_keys, context_text
- **Constraints:** No top-level serialized nodes collection; Top-level graph structure is rooted in `phases[]`; Cross-phase edges live at workflow root
- **New:** no


#### PhaseDefinition <!-- SF: workflow-editor -->
- **Fields:** id, mode, sequential_config, map_config, fold_config, loop_config, nodes, edges, children, inputs, outputs, position
- **Constraints:** Nested containment must use `children[]`; Loop phases expose `condition_met` and `max_exceeded` exits; Each phase owns its internal nodes and edges
- **New:** no


#### Edge <!-- SF: workflow-editor -->
- **Fields:** source, target, transform_fn, description
- **Constraints:** Hook-vs-data is inferred from source port resolution; Hook edges must not carry `transform_fn`; No serialized `port_type` field
- **New:** no


#### WorkflowEditorState <!-- SF: workflow-editor -->
- **Fields:** graph.nodes, graph.edges, schema.workflowSchema, ui.openInspectors, ui.selectionRect, ui.collapsedGroups, ui.validationIssues, undoStack, redoStack
- **Constraints:** Internal state may stay flat for React Flow; Serialization must normalize to nested YAML `phases[].nodes` / `phases[].children`; Hook-vs-data may be derived internally but not serialized; Core editor boot depends only on the five-table compose foundation endpoints; optional SF-7 plugin/reference-index surfaces remain non-blocking and are never a save dependency
- **New:** yes


### From: libraries-registries

#### Tool <!-- SF: libraries-registries -->
- **Fields:** id, user_id, name, description, source, input_schema, created_at, updated_at, deleted_at
- **Constraints:** Created by SF-7 as a follow-on table, not by the SF-5 foundation migration; Unique per user among non-deleted rows; Built-in tools are not stored in this table; Delete is blocked while any non-deleted role still references the tool name
- **New:** yes


#### WorkflowEntityRef <!-- SF: libraries-registries -->
- **Fields:** workflow_id, entity_type, entity_id, created_at
- **Constraints:** Created by SF-7 as a follow-on extension on top of the five-table foundation; Composite uniqueness on (workflow_id, entity_type, entity_id); Only persisted workflow references count toward delete blocking; Applies to roles, output schemas, and task templates; tools remain role-referenced
- **New:** yes


#### CustomTaskTemplate <!-- SF: libraries-registries -->
- **Fields:** actor_slots
- **Constraints:** `actor_slots` must be a JSON array of unique slot definitions; The API must persist and return `actor_slots` after reload; The `actor_slots` column is added by an SF-7 follow-on migration without expanding SF-5 beyond five foundation tables
- **New:** no


## Cross-Service Impacts

### From: broad

#### iriai-compose

- **Impact:** Major extension — new declarative format, YAML loader, DAG runner, primitive node types (Ask, Map, Fold, Loop, Branch, Plugin), edge transform system, hook system, phase groupings, run() entry point, and testing framework (iriai_compose.testing)
- **Action Needed:** Extend library with new modules: schema definition, loader, DAG executor, testing framework. Existing Python subclass API can be broken if needed — new declarative format supersedes it.

#### iriai-build-v2

- **Impact:** Read-only reference. Its 3 workflows (planning, develop, bugfix) are the litmus test — must be translatable to declarative YAML.
- **Action Needed:** No code changes. Analyze workflows to extract patterns and validate format completeness. Produce equivalent YAML representations.

#### auth-service

- **Impact:** No changes needed. JWT already includes dev_tier claim.
- **Action Needed:** None — existing JWT claims sufficient for tier gating.

#### tools.iriai.app (new)

- **Impact:** New minimal frontend app. Reads JWT, displays tier-gated tool cards.
- **Action Needed:** Build new React SPA with auth-react integration. Deploy on Railway.

#### deploy-console-frontend

- **Impact:** Design system reference. Windows XP theme CSS to be replicated or extracted into shared package.
- **Action Needed:** Consider extracting windows-xp.css and UI components into a shared @iriai/ui package, or copy theme files.

### From: declarative-schema

#### iriai-compose (SF-2 loader/runner)

- **Impact:** The loader and runner must consume nested phase containment as `phases[].nodes` and `phases[].children`, honor phase-local `edges`, resolve `actor_type: agent|human`, infer hook-vs-data behavior from port resolution with no serialized `port_type`, and evaluate `BranchNode` per-port conditions using the expression sandbox with non-exclusive fan-out.
- **Action Needed:** Update loader hydration, graph-building, and validation to treat `children` as the recursive phase field, evaluate per-port conditions non-exclusively (multiple outputs may fire), accept `merge_function` as a gather hook, reject stale actor/root/hook/branch fields including `switch_function`, `condition_type`, `output_field`, and `interaction` alias, and preserve additive compatibility with the imperative API.

#### iriai-compose (SF-3 testing framework)

- **Impact:** Fixtures and assertions must construct workflows in the nested YAML shape and stop assuming flat nodes, serialized `port_type`, or any alternate hook model. Branch fixtures must use the per-port `outputs` model with per-port `condition` expressions.
- **Action Needed:** Refresh schema fixtures, round-trip tests, and negative tests so they author nested phases, use `actor_type: agent|human`, use per-port branch conditions, and assert rejection of stale fields like `switch_function`, `condition_type`/`condition`/`paths`, and serialized `port_type`.

#### iriai-build-v2 migration tooling (SF-4)

- **Impact:** Migration output must emit nested phase YAML using `children`, ordinary hook edges, and per-port `BranchNode.outputs` conditions so the translated workflows are valid for both the loader and editor.
- **Action Needed:** Rewrite translation and fixture assumptions away from stale branch fields (`switch_function`, `condition_type`/`condition`/`paths`) and ensure build-v2 planning, develop, and bugfix workflows target the canonical per-port branch contract.

#### iriai-workflows backend (SF-5 composer-app-foundation)

- **Impact:** The backend becomes the canonical schema delivery layer through `GET /api/schema/workflow`; validation and editor bootstrap should consume the live schema rather than a bundled static file, and backend models must not add root `stores` / `plugin_instances` drift.
- **Action Needed:** Implement `/api/schema/workflow` as the authoritative composer schema endpoint, wire validation to the same schema package, and remove static-schema-first plus extra-root-field wording from PRD/plan artifacts.

#### iriai-workflows frontend (SF-6 workflow-editor)

- **Impact:** The editor's serializer/deserializer must keep its internal flat store private and round-trip to the nested YAML contract with `phases[].nodes`, `phases[].children`, ordinary hook edges, per-port `BranchNode.outputs` conditions, and no serialized `port_type`.
- **Action Needed:** Keep the transformation layer but rewrite stale PRD/system-design text so runtime schema fetch comes from `/api/schema/workflow`, hook serialization stays edge-based, branch UI reflects per-port non-exclusive conditions with optional `merge_function`, actor model uses `agent|human` only, and nested containment is the only YAML contract.

### From: dag-loader-runner

#### SF-1 Declarative Schema PRD

- **Impact:** SF-2 now treats the current SF-1 PRD as the only canonical wire contract, including the D-GR-35 per-port BranchNode model. BranchNode entity must reflect inputs/merge_function/outputs shape; old condition_type/condition/paths shape is stale.
- **Action Needed:** Align SF-1 BranchNode schema to D-GR-35: inputs dict + optional merge_function + outputs dict with per-port BranchOutputPort (PortDefinition + condition expression). Remove condition_type, top-level condition, paths, and output_field mode from BranchNode everywhere.

#### SF-1 stale plan / system-design artifacts

- **Impact:** Stale SF-1 artifacts still describe the old three-field BranchNode (condition_type/condition/paths) and may reference merge_function as rejected — both are now incorrect under D-GR-35. Also still reference runtime workflow-schema.json and alternate actor forms.
- **Action Needed:** Rewrite stale SF-1 plan/system-design BranchNode sections to the D-GR-35 per-port model. Remove rejections of merge_function; add rejections of switch_function and old condition_type/condition/paths fields. Also fix workflow-schema.json and interaction actor references.

#### SF-5 Composer App Foundation

- **Impact:** Backend must expose GET /api/schema/workflow from the same in-process SF-1 models SF-2 validates and runs, reflecting the D-GR-35 BranchNode shape with inputs/merge_function/outputs.
- **Action Needed:** Remove any static-schema-first assumptions; ensure the schema endpoint reflects BranchNode.outputs (per-port conditions) rather than the old paths shape. Keep endpoint behavior tied to canonical SF-1 models.

#### SF-6 Workflow Editor

- **Impact:** Editor may keep a flat internal store, but save/load/import/export must normalize to the canonical nested YAML contract including D-GR-35 BranchNode shape. merge_function must be accepted without error. Old condition_type/condition/paths must be rejected on import.
- **Action Needed:** Align BranchNode authoring surface to the inputs/merge_function/outputs per-port model; update serializer/importer to stop emitting or tolerating old condition_type/condition/paths/switch_function fields. Validate that merge_function is passed through correctly.

#### SF-3 Testing Framework

- **Impact:** Tests and fixtures must target the D-GR-35 BranchNode contract (per-port outputs, non-exclusive fan-out, merge_function valid, switch_function rejected, old three-field schema rejected).
- **Action Needed:** Refresh BranchNode fixtures to use inputs/merge_function/outputs per-port model. Update assertions so old condition_type/condition/paths/switch_function variants fail explicitly, and merge_function passes. Add non-exclusive fan-out test coverage (multiple ports firing simultaneously).

#### SF-4 Workflow Migration

- **Impact:** Migrated workflows must emit only the canonical SF-1 / D-GR-35 shape. Translated iriai-build-v2 BranchNode usages must use the per-port outputs model.
- **Action Needed:** Update migration emitters to produce D-GR-35 BranchNode output: translate any old condition_type/condition/paths shapes to inputs/merge_function/outputs per-port form. Verify translated iriai-build-v2 workflows validate and run against the canonical contract.

#### iriai-compose imperative runtime

- **Impact:** Declarative runtime remains additive and cannot break WorkflowRunner, DefaultWorkflowRunner, or existing host integrations. Human actor adaptation happens at the host boundary, not in the wire contract.
- **Action Needed:** Keep new declarative APIs under a separate namespace and preserve current runtime ABCs while adapting human actors at the host boundary.

### From: testing-framework

#### dag-loader-runner (SF-2)

- **Impact:** SF-2 REQ-11 is the canonical ABI contract and SF-2 is the sole owner: AgentRuntime.invoke() unchanged, node identity via runner ContextVar, hierarchical context workflow -> phase -> actor -> node, observability via ExecutionResult/ExecutionHistory, checkpoint/resume outside core contract.
- **Action Needed:** Keep AgentRuntime.invoke() unchanged matching current production ABC at runner.py:36–50. Propagate current node identity via ContextVar. Assemble hierarchical context in canonical order. Keep checkpoint/resume out of the mandatory core runtime contract. No new action needed beyond maintaining REQ-11.

#### testing-framework plan (SF-3)

- **Impact:** Plan decision D-SF3-16 and the stale ABC block at plan.md lines 78–90 showing `node_id: str | None = None` on `invoke()` directly contradict SF-2 REQ-11 and the production ABC. These are the primary non-compliance artifacts blocking implementation.
- **Action Needed:** Remove D-SF3-16 entirely. Correct the stale ABC block to match the production signature (no node_id). Rewrite all node-routing implementation notes to read from the runner ContextVar. Verify no test module adds node_id to invoke().

#### workflow-migration (SF-4)

- **Impact:** Migration tests, open questions, and bridge assumptions must consume the same SF-2 ABI and observability boundary as SF-3. Any SF-4 artifact that treats D-SF3-16 as a dependency is non-compliant.
- **Action Needed:** Align downstream migration artifacts to the unchanged AgentRuntime.invoke() interface, the canonical merge order, and the no-core-checkpoint/resume boundary. Remove any migration artifact that treats D-SF3-16 as a dependency or assumes invoke() carries node_id.

### From: workflow-migration

#### iriai-compose dag-loader-runner (SF-2)

- **Impact:** ABI owner. SF-4 explicitly treats SF-2 as the canonical publisher of the runtime contract: unchanged AgentRuntime.invoke(), ContextVar node propagation, workflow -> phase -> actor -> node merge order, ExecutionResult/ExecutionHistory observability, and no mandatory core checkpoint/resume API.
- **Action Needed:** Keep the published ABI stable as defined. SF-4 has no action items against SF-2; any conflict between SF-4 language and the SF-2 PRD is a stale SF-4 artifact that must be corrected.

#### iriai-compose testing-framework (SF-3)

- **Impact:** Downstream consumer aligned to SF-2 ABI. SF-4 parity tests consume SF-3 only where SF-3 is aligned to the SF-2-owned runtime contract (fluent mocks, ContextVar-based node matching, no node_id kwarg, no checkpoint/resume dependency).
- **Action Needed:** Maintain fluent mock runtimes that read current node identity from the SF-2-published ContextVar; remove any stale node_id kwarg or checkpoint/resume dependency. SF-4 must not consume SF-3 APIs that contradict the SF-2 ABI.

#### iriai-build-v2

- **Impact:** Downstream consumer. The declarative bridge and smoke coverage are explicitly constrained to the published SF-2 runner boundary and observability surface.
- **Action Needed:** Keep the consumer integration additive through run() and RuntimeConfig only, with no bridge-specific invoke shim and no requirement for SF-2-owned checkpoint/resume behavior. Resume is an application-layer concern for iriai-build-v2 to handle independently.

### From: composer-app-foundation

#### auth-service

- **Impact:** SF-5 consumes JWTs, JWKS validation, and the `dev_tier` claim for tools-hub/composer access flows.
- **Action Needed:** Register compose and tools-hub OAuth clients; no auth-service code changes are required.

#### deploy-console

- **Impact:** SF-5 reuses service layout, auth validation, logging/rate-limit patterns, and authenticated SPA shell conventions.
- **Action Needed:** Use deploy-console as an implementation reference only.

#### iriai-compose

- **Impact:** SF-5 depends on `WorkflowConfig.model_json_schema()` and runtime validation semantics to keep compose persistence aligned with the runner contract.
- **Action Needed:** Keep the compose backend pinned to the iriai-compose version that defines the canonical workflow schema.

#### iriai-build-v2

- **Impact:** SF-5 reads iriai-build-v2 planning/develop/bugfix YAML source files once at Alembic data migration time to seed `user_id='__system__'` starter template rows. No filesystem paths from iriai-build-v2 are retained in the compose service after migration.
- **Action Needed:** Read iriai-build-v2 YAML files during Alembic data migration only; no ongoing runtime dependency on iriai-build-v2 paths.

#### SF-6 Workflow Editor

- **Impact:** SF-6 consumes the authenticated compose shell, workflow CRUD/versioning, runtime schema endpoint, validation endpoint, and canonical YAML contract.
- **Action Needed:** Build editor flows against `/api/schema/workflow` and the nested workflow contract only.

#### SF-7 Libraries & Registries

- **Impact:** SF-7 owns the `workflow_entity_refs` reference-index table and subscribes to SF-5's mutation hook interface to keep that index fresh. SF-5 hooks cover all four entity types and emit exactly four event kinds (`created`, `updated`, `soft_deleted`, `restored`). SF-7 must not register against event kinds beyond these four. SF-7 also adds advanced library UI, reference-safe delete flows, a tools table, and custom_task_templates.actor_slots.
- **Action Needed:** SF-5 must ship the mutation hook interface (REQ-18) before SF-7 work begins. SF-5 must not create or mutate `workflow_entity_refs` rows at any point. SF-7 must not assume `imported`, `version_saved`, or `deleted` event kinds exist.

### From: workflow-editor

#### SF-1 Declarative Schema

- **Impact:** SF-6 now explicitly treats `phases[].nodes` / `phases[].children` as canonical and expects hook wiring to remain edge-only.
- **Action Needed:** Ensure SF-1 PRD/design/plan/system-design consistently use `children[]` and never describe a separate hooks section or serialized `port_type`.

#### SF-2 DAG Loader & Runner

- **Impact:** SF-6 depends on the loader and validator consuming the same nested structure and inferring hook edges from port resolution.
- **Action Needed:** Keep SF-2 validation and graph-build logic aligned to edge-only hook serialization and `transform_fn=None` for hook edges.

#### compose-frontend (tools/compose/frontend)

- **Impact:** SF-6 is mounted in the accepted compose SPA rather than a legacy `tools/iriai-workflows` shell.
- **Action Needed:** Keep routing, auth providers, and editor bootstrap inside `tools/compose/frontend`.

#### compose-backend (tools/compose/backend)

- **Impact:** SF-6 depends on workflow/version CRUD, roles, output schemas, custom task templates, validation, and `/api/schema/workflow` backed by PostgreSQL/Alembic and exactly five SF-5 foundation tables. SF-5 also fires workflow mutation hooks (create/update/delete lifecycle events) that downstream consumers can subscribe to; the editor itself does not subscribe to or depend on those hooks.
- **Action Needed:** Keep SF-5 limited to `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`; expose mutation hooks for SF-7 reference-index refresh; do not make `/api/plugins`, `workflow_entity_refs`, or reference-index endpoints a prerequisite for core editor boot/save.

#### SF-7 Libraries & Registries

- **Impact:** SF-7 owns the `workflow_entity_refs` reference-index table and `GET /api/{entity}/references/{id}` endpoint as a downstream extension of SF-5. SF-7 subscribes to SF-5 workflow mutation hooks to keep the reference index synchronized after editor save/create/delete flows; the editor's save path flows through SF-5 endpoints only and is unaware of the reference refresh.
- **Action Needed:** SF-7 must own all `workflow_entity_refs` schema and sync logic; plugin registry surfaces and reference-check affordances must remain additive and non-blocking for the core editor; templates and optional affordances must preserve `children[]` plus edge-based hook wiring without becoming a boot dependency.

### From: libraries-registries

#### SF-5 composer-app-foundation

- **Impact:** Provides the accepted `tools/compose` PostgreSQL/Alembic foundation, the five base tables, and workflow mutation hooks that SF-7 extends. SF-5 must not absorb `workflow_entity_refs`, `tools`, plugin tables, or SQLite assumptions.
- **Action Needed:** Keep SF-5 limited to `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`; expose workflow create/import/duplicate/save/delete mutation hooks so SF-7 can refresh the reference index from saved workflow state.

#### SF-6 workflow-editor

- **Impact:** Workflow saves determine when role/schema/template references become persisted and visible to library delete preflights.
- **Action Needed:** Continue saving persisted library references through the compose workflow routes so SF-7 can refresh `workflow_entity_refs` from saved state rather than unsaved canvas state.

#### SF-4 workflow-migration

- **Impact:** Imported or migrated workflows must produce the same persisted library-reference shape that the SF-7 index reads.
- **Action Needed:** Ensure workflow import and migration flows end at the compose workflow save boundary so SF-7 reference-index rows can be rebuilt after import.

#### iriai-compose

- **Impact:** `Role.tools` remains a string-array contract consumed by the Role editor and tool delete protection.
- **Action Needed:** Preserve the current `list[str]` tool identifier model for v1; any future move to tool IDs is a separate follow-up decision.

## Open Questions

### From: broad

- Should the Windows XP theme CSS be extracted into a shared @iriai/ui package, or should iriai-workflows copy the theme files from deploy-console?
- What URL should the workflow composer live at? (e.g., compose.iriai.app, workflows.iriai.app)
- For the migration: should the 3 translated iriai-build-v2 workflows ship as built-in starter templates in the composer?
- How should transforms and hooks be distributed? As a built-in library in iriai-compose, or user-defined in the composer, or both?
- Should the YAML format support $ref for reusable inline definitions (like JSON Schema $ref), or should all reuse go through the library system?

### From: declarative-schema

- No schema-shape open questions remain. Caching behavior for `/api/schema/workflow` is an implementation concern and does not change the canonical wire contract.

### From: testing-framework

- Should execution snapshots remain JSON-only, or is there still a case for YAML snapshot files?
- Should the enhanced `MockAgentRuntime` extend the existing test `MockAgentRuntime` from `iriai-compose/tests/conftest.py`, or remain a fresh implementation in the production `testing/` namespace?
- How deep should `validate_type_flow()` inspect inline transforms when inferring type compatibility?
- If resume-oriented helpers remain desirable in SF-3, should they be deferred to a follow-on artifact that layers above SF-2's observability surface rather than expanding the runner ABI?

### From: workflow-migration

- Should actor-centric templates use the exact same storage format in YAML and the composer's CustomTaskTemplate table, or does the composer wrap them with extra metadata?
- Is there a nesting-depth limit for phase modes beyond the four-level develop-workflow pattern?
- How should the runner resolve inline EdgeTransform `fn` names such as `envelope_extract`?
- What mechanism should the declarative path use for phase tracking in iriai-build-v2: callback/hook, wrapper around `run()`, or custom runner subclass?
- Should consumer-specific plugin implementations live in iriai-compose with dependency injection or in iriai-build-v2 as adapters?
- Should the migrated YAML workflow files live in iriai-build-v2 or in iriai-compose as portable reference workflows?

### From: composer-app-foundation

- Should `/api/schema/workflow` expose an ETag or schema hash so the frontend can safely cache and invalidate runtime schema changes?
- Should import validation reject unknown extra fields strictly, or allow warning-level tolerance for forward-compatible schema additions?
- Should SF-5's mutation hook interface be a simple in-process callback list, or should it use a lightweight event emitter pattern (e.g. Python `blinker`) to support multiple SF-7 subscribers without coupling to import order?

### From: libraries-registries

- Should `workflow_entity_refs` materialize `user_id` directly for faster queries, or should tenancy remain derived via joins to `workflows`?
- Should custom tool references remain name-based in `Role.tools` for v1, or should a later phase migrate them to stable tool IDs?
- What exact serialized shape should task-template actor-slot default bindings use in declarative workflow YAML so SF-1, SF-6, and SF-7 stay aligned?

## Out of Scope

### From: broad

- Multi-user collaboration on workflow configs
- Runtime agent execution inside the composer — it is a builder/config tool only
- Cost dashboards or analytics UI — cost configuration lives in the YAML schema for future runners
- Hot-swap UI — the builder produces versioned configs, runners handle swap mechanics
- Migration tooling from iriai-build v1 (legacy)
- Quality or subjective scoring — cost tracking limited to token counts and USD
- Mobile-responsive design — desktop-first tool for developers

### From: declarative-schema

- Execution-engine implementation details beyond the schema/validation contract.
- Any alternate flat YAML dialect with top-level nodes or detached phase membership lists.
- Separate serialized hook sections, hook registries, or hook-specific edge discriminators such as `port_type`.
- Treating static `workflow-schema.json` as the editor's canonical runtime schema source.
- Root `stores` or `plugin_instances` registries without new approval.
- Actor wire aliases other than `actor_type: agent|human` — `interaction` is explicitly excluded.
- `switch_function` or any other routing-function branch field — `merge_function` is valid for gather but is not a routing function.
- `output_field` as a BranchNode routing mode — removed by D-GR-35.
- The stale exclusive single-path `condition_type`/`condition`/`paths` BranchNode shape — replaced by per-port `outputs` model.
- Standalone Map/Fold/Loop node types or other compound-node replacements for phase modes.
- Replacing or breaking the existing imperative `iriai-compose` subclass API.
- Runtime agent execution inside the composer application.
- Migration tooling for legacy iriai-build v1 configs.

### From: dag-loader-runner

- Supporting a second serialized workflow dialect for flattened graphs or alternate root containers.
- Serializing hooks through port_type, hidden callback lists, or separate hook sections.
- Serializing branch logic through switch_function or the old three-field schema (condition_type / condition / paths). The D-GR-35 per-port outputs model with optional merge_function is the only valid branch routing surface.
- Per-port output_field declarative-lookup mode on branch output ports — per-port conditions are expressions only.
- Treating workflow-schema.json as a runtime/editor schema contract.
- Adding stores or plugin_instances to the declarative WorkflowConfig root without an explicit future PRD change.
- A mandatory built-in core checkpoint/resume API in SF-2.
- Runner-managed MCP subprocess lifecycle.
- Production-plugin test-mode branches as the live-test strategy.

### From: testing-framework

- Introducing new testing capabilities beyond the R18 ABI-alignment correction.
- Breaking the `AgentRuntime` ABC to add a `node_id` kwarg — explicitly prohibited by SF-2 REQ-11 and this PRD.
- Supporting alternate hierarchical context merge orders.
- Defining or requiring a built-in core checkpoint/resume API in SF-2.
- Restoring D-SF3-16 under any consumer-local framing.

### From: workflow-migration

- Changing the abstract `AgentRuntime.invoke()` signature.
- Introducing a `node_id` keyword contract in migration tests, bridge code, or any other downstream consumer.
- Treating checkpoint/resume as a mandatory core SF-2 runtime API or backfilling it through consumer-layer abstractions.
- Reopening the resolved hierarchical merge-order decision from D-GR-23.
- Co-ownership of the SF-2 runtime boundary by SF-4; SF-4 is a consumer only.

### From: composer-app-foundation

- Multi-user collaboration on workflow configs
- Runtime workflow execution inside the compose app or tools hub
- Reusing or extending `tools/iriai-workflows` as the canonical compose implementation path
- SQLite support or a SQLite-first local persistence contract
- Plugin registry UI, plugin tables, or `/api/plugins` endpoints
- Tool Library UI, custom tools table, or `/api/tools` endpoints
- `workflow_entity_refs` table creation, row materialization, or `GET /api/{entity}/references/{id}` in SF-5 — hook infrastructure is SF-5's responsibility; the reference index and its API belong to SF-7
- Version-history list, diff, or restore UI
- Phase template library pages
- Migration tooling from iriai-build v1
- Serving starter template content from filesystem paths at request time — all template content lives in DB rows seeded by Alembic data migration
- An instance-level import endpoint (`POST /api/workflows/{id}/import`) — if replace-from-import semantics are needed, that decision belongs to a future subfeature
- Mutation hook event kinds beyond the four canonical ones (`imported`, `version_saved`, `deleted`, etc.)

### From: workflow-editor

- YAML side pane and live bidirectional YAML editing
- Version-history browsing UI inside the editor
- Visual JSON Schema builder
- Named transform registry UI or transform picker
- Runtime workflow execution inside the editor
- Collaborative multi-user editing
- Separate serialized hooks section
- Serialized `port_type` field
- Runtime fallback to static `workflow-schema.json`
- MiniCanvasThumbnail / CMP-64
- `tools/iriai-workflows` as the editor deployment shell
- SQLite as a runtime persistence dependency for compose editor flows
- Core-editor boot dependency on `/api/plugins` or `GET /api/{entity}/references/{id}`
- Foundation-owned `workflow_entity_refs` expansion

### From: libraries-registries

- Plugins Library pages, plugin endpoints, and PluginPicker surfaces
- Phase Templates Library
- Multi-user sharing or collaboration on library entities
- Tool auto-discovery from MCP servers
- Template version history or versioning UI
- Changing SF-5 foundation ownership beyond the accepted five-table boundary