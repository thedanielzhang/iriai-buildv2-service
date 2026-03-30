<!-- SF: declarative-schema -->
### SF-1: Declarative Schema & Primitives

Re-baseline SF-1 to D-GR-22 plus the five SF-6 editor contract requirements the feedback identifies as missing. The authoritative YAML contract now locks five things together: (1) nested phase containment — WorkflowConfig.phases as the top-level phase list, PhaseDefinition.children as the only recursive field (not phases); (2) exactly three atomic node types — AskNode, BranchNode, PluginNode (type discriminant values: ask | branch | plugin) — as the only varieties that may appear in PhaseDefinition.nodes; SwitchFunctionEditor and ErrorFlowNode are not schema node types and must be explicitly rejected by validation; (3) cross-phase edge ownership — WorkflowConfig.edges holds every EdgeDefinition whose source and target resolve to different phases; PhaseDefinition.edges holds only intra-phase connections; (4) synthetic root phase normalization — loading a WorkflowConfig whose phases list is empty normalizes a synthetic root phase (id: __root__, mode: sequential, empty nodes/children/edges) so the editor always receives at least one phase; and (5) a blocking schema bootstrap gate — the composer must receive a 200 from /api/schema/workflow before the canvas renders; no view-only or degraded fallback is permitted. Hook wiring remains edge-based per D-GR-22 with no separate hooks section and no serialized port_type. Composer schema delivery is runtime-served from /api/schema/workflow; static workflow-schema.json is retained for build/test only.

<!-- SF: declarative-schema -->
### J-1 — SF-1: Declarative Schema & Primitives

**Step Annotations:**
- Step 1: WorkflowConfig owns the top-level phases list and the cross-phase edges collection. All edges whose source and target resolve to different phases — or where one endpoint is a phase boundary ref ($input/$output) — are serialized exclusively in WorkflowConfig.edges. PhaseDefinition.edges contains only intra-phase connections. Executable content nests under phases[].nodes (AskNode | BranchNode | PluginNode only) and recursive phases[].children; a parallel top-level nodes collection is never valid [REQ-1, REQ-5, decision: D-GR-22, cross-phase edge ownership contract].
- Step 2: Bootstrap gate: the composer must await a successful 200 from /api/schema/workflow before the canvas renders. Serving a degraded or view-only canvas while the endpoint is unavailable is not permitted. The bootstrap contract also includes synthetic root phase normalization: if WorkflowConfig.phases is empty after loading, the runtime normalizes a synthetic root phase (id: __root__, mode: sequential, empty nodes/children/edges) so the editor always has at least one phase to operate on [blocking bootstrap gate, synthetic root normalization, code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1004].
- Step 4: PhaseDefinition is the authoring boundary for local structure. Each phase serializes its own nodes list (AskNode | BranchNode | PluginNode only), local intra-phase edges, typed ports, and child phases under children. Edges connecting this phase to another phase are moved to WorkflowConfig.edges during serialization [REQ-5, REQ-20, decision: D-GR-22, cross-phase edge ownership contract].
- Step 6: EdgeDefinition serializes every connection with source and target dot-notation (or $input/$output for phase boundaries), optional transform_fn, and optional description. No from_ref/to_ref, no HookEdge payload, and no serialized port_type appear in YAML. Placement in phase.edges vs. workflow.edges is determined by whether both endpoints resolve to nodes within the same phase [REQ-4, decision: D-GR-22, cross-phase edge ownership contract].
- Step 7: Lifecycle behavior is authored as ordinary edges from phase_id.on_start, phase_id.on_end, or node_id.on_end inside the relevant edge list. Hook semantics are inferred from source-port container membership; transform_fn must be absent on hook-sourced edges. A hook edge that crosses phase boundaries serializes in WorkflowConfig.edges alongside other cross-phase edges [REQ-8, decision: D-GR-22, cross-phase edge ownership contract].
- Step 9: Validation walks the nested phase tree recursively, enforcing: (a) phase.nodes contains only AskNode, BranchNode, or PluginNode — node.type values of switch_function, error_flow, or any other string trigger explicit rejection; (b) cross-phase edges appear only in workflow.edges, never in phase.edges; (c) children is the sole recursive phase field; (d) no serialized port_type fields exist [REQ-17, decision: D-GR-22, three atomic node types contract, cross-phase edge ownership contract].

**Error Path UX:** Structural errors identify the exact nested path that failed (e.g., phases[0].children[1].nodes[2]). Forbidden node types produce: 'node.type must be one of: ask, branch, plugin — SwitchFunctionEditor is not a valid schema node type' or 'ErrorFlowNode is not a valid schema node type; error flows are expressed as hook edges off on_end'. Cross-phase edge misplacement produces: 'edge[N].source and target resolve to different phases; cross-phase edges must be in workflow.edges, not phase.edges'. Stale contract fields produce: 'PhaseDefinition.phases is unsupported; use children', 'EdgeDefinition.port_type is not serialized', 'hook-sourced edges must not define transform_fn' [REQ-17, decision: D-GR-22].

**Empty State UX:** The minimal valid skeleton is WorkflowConfig with schema_version, workflow_version, name, actors, and one phase (the synthetic root if bootstrapped from empty). Phase nodes, children, and edges are empty collections. There is no top-level nodes list, no standalone hooks section, no SwitchFunctionEditor or ErrorFlowNode entry, and no cross-phase edge inside phase.edges [REQ-1, REQ-5, synthetic root normalization].

**NOT Criteria:**
- Workflow YAML must NOT introduce a top-level nodes collection parallel to phases [decision: D-GR-22].
- Nested phases must NOT serialize under a phases field on PhaseDefinition; the only canonical recursive field is children [decision: D-GATE-2, code: .iriai/artifacts/features/beced7b1/plan-review-discussion-3.md:131].
- Hook behavior must NOT be serialized in a separate hooks section or with edge.port_type metadata [decision: D-GR-22].
- Phase.nodes must NOT contain SwitchFunctionEditor or ErrorFlowNode; only ask, branch, and plugin are valid atomic node types [three atomic node types contract, REQ-3, REQ-17].
- Cross-phase edges must NOT appear in PhaseDefinition.edges; they belong exclusively in WorkflowConfig.edges [cross-phase edge ownership contract, REQ-5].
- The editor must NOT render in view-only or degraded mode while /api/schema/workflow is unavailable; bootstrap is a blocking gate [blocking bootstrap gate contract, code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1004].

<!-- SF: declarative-schema -->
### J-2 — SF-1: Declarative Schema & Primitives

**Step Annotations:**
- Step 1: The interview loop is authored inside one loop-mode phase's nodes collection. Only AskNode and BranchNode may be placed directly as siblings under phases[].nodes for prompting and routing. PluginNode may be included for any tool invocations within the loop. No other node type — including SwitchFunctionEditor or ErrorFlowNode — may appear. The phase itself owns loop exits and any child phases; the schema does not flatten this pattern into workflow-level nodes [REQ-3, REQ-5, REQ-6, decision: D-GR-22, three atomic node types contract].
- Step 2: Synthetic root normalization: if the interview loop workflow is loaded from a file or API response with an empty phases list, the loader normalizes a synthetic root phase before handing the model to the editor. The root phase can then contain the loop phase as a child under children; the editor never starts from a completely phaseless state [synthetic root normalization, code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1006].
- Step 3: Reusable gate or interview templates encapsulate nested phases using the same containment shape: the template's root phase owns AskNode/BranchNode/PluginNode nodes plus any recursive children. Template import/export does not need a second schema form [REQ-9, REQ-20, decision: D-GR-22].
- Step 4: Hosting setup via on_start is a regular edge in the containing phase/workflow edge list with source phase_id.on_start and no extra hook section. If the on_start hook connects to a node or phase in a different phase, the edge lives in WorkflowConfig.edges [REQ-8, decision: D-GR-22, cross-phase edge ownership contract].
- Step 5: Artifact publishing via on_end follows the same single-edge contract. If the on_end hook crosses phase boundaries, it serializes in WorkflowConfig.edges alongside other cross-phase connections [REQ-4, REQ-8, decision: D-GR-22, cross-phase edge ownership contract].
- Step 6: DAG execution groups serialize as nested phases using children. A fold parent phase contains map or sequential child phases in children; each child phase owns its own AskNode/BranchNode/PluginNode nodes and local intra-phase edges. Save/load must round-trip that containment tree intact [REQ-6, AC-12, decision: D-GR-22].

**Error Path UX:** Migration-time validation reports containment mistakes at the phase boundary that introduced them: a child phase emitted into workflow.phases instead of parent.children; a lifecycle hook expressed as callback metadata; a cross-phase edge nested in phase.edges; or a node typed as SwitchFunctionEditor or ErrorFlowNode. Each failure stays localized to the malformed subtree [REQ-17, REQ-19].

**Empty State UX:** If a phase has no subphases, children is an empty collection or omitted; authors do not create placeholder wrapper phases. If a workflow has no lifecycle behavior, there are simply no hook-sourced edges. An empty workflow always loads with at least the synthetic root phase after normalization [REQ-5, REQ-8, synthetic root normalization].

**NOT Criteria:**
- Nested fold/map/loop structures must NOT require compound node types or a second serialized DAG model [REQ-6].
- Save/load must NOT flatten child phases into sibling workflow phases or orphaned top-level nodes [decision: D-GR-22].
- Lifecycle wiring must NOT be split between ordinary edges and a separate callback registry [decision: D-GR-22].
- Only AskNode, BranchNode, and PluginNode may be placed in phase.nodes; no other atomic node types exist in the schema [three atomic node types contract, REQ-3].
- Synthetic root normalization must NOT be skipped for empty-phases workflows; the loader always produces at least one phase before handing the model to the editor [synthetic root normalization contract].

<!-- SF: declarative-schema -->
### J-3 — SF-1: Declarative Schema & Primitives

**Step Annotations:**
- Step 5: Invalid hook-port edge validation resolves the source string against indexed outputs, hooks, and Branch paths. A source that cannot resolve to on_start/on_end, or a hook-sourced edge carrying transform_fn, fails immediately without relying on port_type flags. Cross-phase hook edges are also validated to confirm they live in workflow.edges rather than phase.edges [REQ-4, REQ-8, REQ-17, decision: D-GR-22, cross-phase edge ownership contract].
- Step 6: Phase tree validation checks recursive containment using children and rejects stale aliases or mixed models. It also validates that each phase's nodes list contains only AskNode, BranchNode, or PluginNode; node.type values of switch_function, error_flow, or any other string produce an explicit rejection with the offending phase path [REQ-5, REQ-17, decision: D-GR-22, three atomic node types contract].
- Step 7: Cross-phase edge placement validation: every edge in a PhaseDefinition.edges list is checked to confirm that both source and target resolve to nodes within the same phase. Any edge whose source or target resolves to a node in a different phase produces: 'edge[N] crosses phase boundary; move to workflow.edges' [cross-phase edge ownership contract, REQ-17].
- Step 8: Malformed source/target strings still fail before type checking. Unknown serialized fields like port_type are stale-contract violations rather than silently ignored shims. SwitchFunctionEditor and ErrorFlowNode produce rejection messages referencing the canonical three-type constraint: 'node.type must be one of: ask, branch, plugin' [REQ-4, REQ-17, decision: D-GR-22, three atomic node types contract].

**Error Path UX:** Failure copy is explicit about contract drift: 'children is the only recursive phase field', 'hook edges are inferred from source port resolution', 'port_type is not part of the serialized edge schema', 'node.type must be one of: ask, branch, plugin — SwitchFunctionEditor is not a valid type', 'ErrorFlowNode is not a valid type; error flows use on_end hook edges', 'cross-phase edges belong in workflow.edges'. That wording prevents stale SF-5/SF-6/SF-2 artifacts from reintroducing parallel schemas through permissive validation [decision: D-GR-22, three atomic node types contract, cross-phase edge ownership contract].

**Empty State UX:** Validation on an otherwise empty starter workflow traverses the single root phase and confirms that empty nodes/children/edges collections are valid. The absence of hook edges, child phases, or cross-phase edges is not treated as an error [REQ-5, REQ-17, synthetic root normalization].

**NOT Criteria:**
- Validation must NOT accept a separate serialized hooks section as an alias for hook edges [decision: D-GR-22].
- Validation must NOT require or honor serialized edge.port_type to classify hook edges [decision: D-GR-22].
- Validation must NOT accept PhaseDefinition.phases as a recursive alias once children is canonical [decision: D-GATE-2].
- Validation must NOT allow node.type values outside {ask, branch, plugin}; SwitchFunctionEditor and ErrorFlowNode are not valid types [three atomic node types contract, REQ-3, REQ-17].
- Validation must NOT permit cross-phase edges inside PhaseDefinition.edges; they must be in WorkflowConfig.edges [cross-phase edge ownership contract, REQ-5].

<!-- SF: declarative-schema -->
### J-4 — SF-1: Declarative Schema & Primitives

**Step Annotations:**
- Step 1: Bootstrap is a blocking gate with no view-only fallback. The composer must await a 200 from /api/schema/workflow before the canvas renders. If the endpoint fails, the editor transitions to a full-screen error state with a Retry button — never to a degraded or read-only canvas. WorkflowConfig.model_json_schema() remains the generation mechanism; /api/schema/workflow is the canonical runtime delivery path [decision: D-GR-22, blocking bootstrap gate contract, code: .iriai/artifacts/features/beced7b1/broad/architecture.md:510].
- Step 2: The bootstrap response encodes the synthetic root phase normalization contract and the three-type constraint: the returned schema must define that WorkflowConfig.phases contains at least one phase (the synthetic root when bootstrapped empty), and that PhaseDefinition.nodes accepts only the ask | branch | plugin discriminant values. This alignment keeps inspector generation, validation, and serializer expectations consistent [REQ-16, decision: D-GR-22, three atomic node types contract, synthetic root normalization, code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:794].
- Step 3: The runtime-served JSON Schema must expose the full nested containment and edge contract the loader validates: WorkflowConfig.phases, WorkflowConfig.edges (cross-phase), PhaseDefinition.nodes (discriminant: ask | branch | plugin), PhaseDefinition.edges (intra-phase only), PhaseDefinition.children, and EdgeDefinition without port_type. Omitting PhaseDefinition.children, any of the three node type variants, or the cross-phase/intra-phase edge split from the schema constitutes a bootstrap failure [REQ-16, decision: D-GR-22, cross-phase edge ownership contract].

**Error Path UX:** If bootstrap fails, the editor displays a full-screen error state with the failed endpoint URL and a Retry button — never a partially-loaded canvas. Static workflow-schema.json may still exist for build/test tooling but must not mask a broken runtime endpoint; the editor must not fall back to the static file if /api/schema/workflow is unavailable [decision: D-GR-22, blocking bootstrap gate contract].

**Empty State UX:** The schema endpoint returns the full current contract even when no user workflow exists yet. Bootstrapping never depends on opening a sample YAML file or loading a bundled static schema snapshot. The first successful bootstrap includes the synthetic root phase normalization contract and the three-type node discriminant so the editor can render an empty but valid canvas [decision: D-GR-22, synthetic root normalization].

**NOT Criteria:**
- Composer must NOT treat workflow-schema.json as its canonical production schema source [decision: D-GR-22].
- The runtime schema must NOT diverge from the loader's nested containment contract by omitting PhaseDefinition.children or reintroducing port_type [decision: D-GR-22].
- The editor must NOT render in view-only or degraded mode while /api/schema/workflow is unavailable; the only valid non-ready states are loading and error-with-retry [blocking bootstrap gate contract, code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1004].
- The runtime schema must NOT omit any of the three atomic node type variants (ask, branch, plugin) from its NodeDefinition discriminant [three atomic node types contract, REQ-3].

<!-- SF: declarative-schema -->
### J-5 — SF-1: Declarative Schema & Primitives

**Step Annotations:**
- Step 1: Loop-mode exit routing remains phase-level and stays within the nested containment model. condition_met and max_exceeded are PhaseDefinition output ports on the loop phase. If the downstream target is a node or phase within the same parent phase, the exit edge lives in the parent phase's edges list. If the exit edge connects to a node or phase in a different phase — for example, a post-loop processing phase at the workflow level — the edge serializes in WorkflowConfig.edges, not in any PhaseDefinition.edges [REQ-7, REQ-5, decision: D-GR-22, cross-phase edge ownership contract].

**Error Path UX:** Loop validation failures name the loop phase that omitted or miswired condition_met/max_exceeded, including the nested phase path if the loop is inside children. Cross-phase loop exit edges misplaced in phase.edges produce: 'loop exit edge[N] crosses phase boundary; move to workflow.edges'. The error surface does not flatten loop exits into pseudo-nodes or a separate control-flow section [REQ-7, REQ-17, cross-phase edge ownership contract].

**Empty State UX:** A loop phase with no max_iterations still keeps the same phase-scoped output contract; max_exceeded remains a dormant phase output rather than moving to a separate metadata block [REQ-7].

**NOT Criteria:**
- Loop exit routing must NOT bypass the ordinary phase/edge model or introduce special top-level control-flow collections [REQ-7, decision: D-GR-22].
- Cross-phase loop exit edges must NOT be placed in PhaseDefinition.edges; they belong in WorkflowConfig.edges [cross-phase edge ownership contract, REQ-5].

<!-- SF: declarative-schema -->
### CMP-1: EdgeDefinition
<!-- SF: declarative-schema — Original ID: CMP-7 -->

- **Status:** new
- **Location:** `iriai_compose/schema/edges.py`
- **Description:** Single serialized edge model for every connection in the declarative workflow format. Carries source and target dot-notation refs (or $input/$output for phase boundaries), optional transform_fn, and optional description. Hook edges are not a second schema type and do not serialize port_type; hook semantics are inferred by resolving the source port against the containing phase or node's hooks collection vs. outputs. Edge placement follows cross-phase ownership: edges where source and target resolve to different phases live in WorkflowConfig.edges; edges where both endpoints are within the same phase live in PhaseDefinition.edges.
- **Props/Variants:** `source: str ($input | node_id.port | phase_id.port), target: str ($output | node_id.port | phase_id.port), transform_fn: Optional[str], description: Optional[str]`
- **States:** data, hook, cross-phase, error
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:REQ-4` — "EdgeDefinition serializes connections with source and target dot notation... Must NOT serialize port_type field. Hook-vs-data behavior determined by resolving source port container." — REQ-4 defines the single edge surface, makes hook classification a resolution concern, and explicitly forbids port_type serialization.
  - [decision] `D-GR-22` — "Hook wiring remains edge-based with no separate hooks section and no serialized port_type." — Cycle 4 made the no-port_type, no-hooks-section contract authoritative across SF-1, SF-5, and SF-6.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:REQ-5` — "PhaseDefinition includes phase-local edges list." — The phase-local vs. workflow-root edge split derives from REQ-5's ownership model for PhaseDefinition.

### CMP-2: BranchNode
<!-- SF: declarative-schema — Original ID: CMP-8 -->

- **Status:** new
- **Location:** `iriai_compose/schema/nodes.py`
- **Description:** Atomic routing node — one of three valid atomic types (type discriminant: branch) that may be placed directly in PhaseDefinition.nodes. Serializes condition_type, condition, and paths where each paths key is both a routing outcome and an output port name used by EdgeDefinition.source suffixes. Participates in the same nested phase and hook-port model as AskNode and PluginNode. SwitchFunctionEditor is not a schema node type and must not be used as an alias or replacement for BranchNode.
- **Props/Variants:** `type: Literal['branch'], id: str, name: str, condition_type: expression | output_field, condition: str, paths: dict[str, PortDefinition], inputs/outputs/hooks implicit`
- **States:** default, path-resolved, error
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:REQ-3` — "BranchNode — condition + typed paths for exclusive routing. NO switch_function or merge_function." — REQ-3 identifies BranchNode as one of the three atomic types and explicitly forbids switch_function and merge_function variants.
  - [decision] `D-GR-22` — "YAML remains nested (phases[].nodes, phases[].children)." — BranchNode lives inside phase.nodes per the nested containment contract locked in Cycle 4.

### CMP-3: PhaseDefinition
<!-- SF: declarative-schema — Original ID: CMP-9 -->

- **Status:** new
- **Location:** `iriai_compose/schema/phases.py`
- **Description:** Primary execution container and recursive serialization unit. Each phase owns its execution mode, typed ports, AskNode/BranchNode/PluginNode nodes under nodes, intra-phase-only edges under edges, and child phases under children. children is the only recursive field name; phases is not a supported alias. PhaseDefinition.edges must not contain cross-phase edges — those belong to WorkflowConfig.edges. The synthetic root phase (__root__, mode: sequential, empty nodes/children/edges) is the normalized representation produced by the bootstrap loader when WorkflowConfig.phases is empty.
- **Props/Variants:** `id: str, name: str, mode: sequential | map | fold | loop, mode_config: ModeConfig, nodes: list[AskNode | BranchNode | PluginNode], children: list[PhaseDefinition], edges: list[EdgeDefinition] (intra-phase only), inputs/outputs/hooks: dict[str, PortDefinition], context_keys: list[str], cost: Optional[CostConfig]`
- **States:** sequential, map, fold, loop, nested, synthetic-root, error
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:REQ-5` — "PhaseDefinition is primary execution container with nodes, children, and phase-local edges." — REQ-5 defines the phase's canonical fields and identifies children as the recursive nesting key.
  - [code] `.iriai/artifacts/features/beced7b1/plan-review-discussion-3.md:131` — "Fix naming: PhaseDefinition.children (not phases)." — Earlier review work corrected the recursive field name; the revised design preserves that correction.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1006` — "Loose top-level nodes normalized under synthetic root phase." — SF-6 PRD identifies synthetic root normalization as the bootstrap contract; CMP-3 must define the synthetic root phase shape.

### CMP-4: WorkflowConfig
<!-- SF: declarative-schema — Original ID: CMP-10 -->

- **Status:** new
- **Location:** `iriai_compose/schema/workflow.py`
- **Description:** Top-level workflow envelope with actors, types, plugins, top-level phases, and cross-phase edges. WorkflowConfig.edges is the authoritative home for every EdgeDefinition whose source and target resolve to different phases; intra-phase edges live in their owning PhaseDefinition.edges. WorkflowConfig.model_json_schema() generates the JSON Schema; the composer receives it at runtime from /api/schema/workflow with no static fallback. The generated schema must expose the three-type NodeDefinition discriminant (ask | branch | plugin), the cross-phase edge ownership split, and the PhaseDefinition.children recursive field.
- **Props/Variants:** `schema_version: str, workflow_version: str, name: str, description: Optional[str], actors: dict[str, ActorDefinition], phases: list[PhaseDefinition], edges: list[EdgeDefinition] (cross-phase only), templates: Optional[dict], plugins: Optional[dict], types: Optional[dict], cost_config: Optional[CostConfig]`
- **States:** default, schema-export, bootstrap-gate, error
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:REQ-1` — "Root WorkflowConfig MUST include only: schema_version, workflow_version, name, actors, phases, edges... Top-level nodes invalid." — REQ-1 establishes WorkflowConfig as the root model and explicitly forbids a top-level nodes collection.
  - [decision] `D-GR-22` — "/api/schema/workflow is the canonical schema delivery path for the composer; static workflow-schema.json is build/test only." — Cycle 4 elevated the runtime endpoint to canonical status and demoted the static file.
  - [code] `.iriai/artifacts/features/beced7b1/broad/architecture.md:510` — "@router.get('/api/schema/workflow') ... _cached_schema = WorkflowConfig.model_json_schema()" — The broad architecture already implements the runtime delivery path that D-GR-22 made canonical.

### CMP-5: AskNode
<!-- SF: declarative-schema — Original ID: CMP-11 -->

- **Status:** new
- **Location:** `iriai_compose/schema/nodes.py`
- **Description:** Atomic prompt/collection node — one of three valid atomic types (type discriminant: ask) that may be placed directly in PhaseDefinition.nodes. Serializes actor (reference to WorkflowConfig.actors key), prompt (Jinja2 template with {{ }} interpolation), and optional output_type (type_ref string or inline schema dict). Exposes implicit hook ports on_start and on_end plus a single output named response. No special editor renderer (SwitchFunctionEditor or equivalent) is introduced; AskNode uses the standard node inspector panel in SF-6.
- **Props/Variants:** `type: Literal['ask'], id: str, name: str, actor: str, prompt: str, output_type: Optional[str | dict], inputs/outputs/hooks implicit`
- **States:** default, waiting, responded, error
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:REQ-3` — "AskNode — atomic actor invocation. Three atomic node types only: AskNode, BranchNode, PluginNode." — REQ-3 identifies AskNode as one of the three atomic types with actor, prompt, and output_type fields.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:794` — "Canvas exposes exactly three atomic nodes: Ask, Branch, Plugin." — SF-6 PRD confirms AskNode as a required atomic type for direct canvas placement with a standard inspector panel.
  - [decision] `D-GR-22` — "YAML remains nested (phases[].nodes)." — AskNode lives inside phase.nodes per the nested containment contract; it has no top-level placement.

### CMP-6: PluginNode
<!-- SF: declarative-schema — Original ID: CMP-12 -->

- **Status:** new
- **Location:** `iriai_compose/schema/nodes.py`
- **Description:** Atomic plugin/tool invocation node — one of three valid atomic types (type discriminant: plugin) that may be placed directly in PhaseDefinition.nodes. Serializes plugin_ref (reference to WorkflowConfig.plugins key or built-in plugin id) and config (key/value map passed to the plugin). Plugin outputs are discoverable as EdgeDefinition source suffixes derived from the plugin's registered output schema. No special editor renderer is introduced. ErrorFlowNode is not a valid alternative for error routing; error flows are expressed as hook edges off on_end ports, consistent with the D-GR-22 edge-only contract.
- **Props/Variants:** `type: Literal['plugin'], id: str, name: str, plugin_ref: str, config: Optional[dict[str, Any]], inputs/outputs/hooks implicit from plugin schema`
- **States:** default, executing, completed, error
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:REQ-3` — "PluginNode — external capabilities. Three atomic node types only: AskNode, BranchNode, PluginNode." — REQ-3 identifies PluginNode as one of the three atomic types and provides plugin_ref and config as its fields.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:REQ-10` — "PluginInterface defines plugin identity, typed inputs/outputs, config schema so plugin nodes stay first-class." — REQ-10 provides the plugin output schema contract that backs the PluginNode's discoverable output handles.
  - [decision] `D-GR-22` — "YAML remains nested (phases[].nodes)." — PluginNode lives inside phase.nodes per the nested containment contract; it has no top-level placement.

<!-- SF: declarative-schema -->
### CMP-1 (EdgeDefinition) States

| State | Visual Description |
|-------|-------------------|
| data | Serialized edge entry contains source and target only (plus optional transform_fn/description). The resolved source port is a data output such as node_id.response, plugin_id.result, or branch_id.approved. No port_type field is present. Both endpoints resolve to nodes within the same phase, so this edge appears in PhaseDefinition.edges. |
| hook | Serialized edge entry contains source, target, and no transform_fn. Source resolves to on_start or on_end on a node or phase. Hook semantics are recognizable solely by source-port resolution against the hooks collection — no port_type discriminator is present. If the hook crosses phases, it appears in WorkflowConfig.edges. |
| cross-phase | Edge appears in WorkflowConfig.edges (not in any PhaseDefinition.edges). Source and target resolve to nodes or phase boundaries in different phases. The presence of this edge at the workflow root is what distinguishes it as cross-phase; the edge object itself has the same shape as any other EdgeDefinition. |
| error | Validation reports the edge path plus one of: malformed dot notation in source/target, transform_fn set on a hook-sourced edge, stale field port_type encountered in the object, or 'edge[N].source and target resolve to different phases — move to workflow.edges'. |

### CMP-2 (BranchNode) States

| State | Visual Description |
|-------|-------------------|
| default | BranchNode appears inside a phase's nodes collection. node.type discriminant is 'branch'. condition_type, condition, and a paths map with at least two named output keys are present. Each path key is usable as an EdgeDefinition source suffix (e.g., branch_review.approved). |
| path-resolved | A downstream edge source string references one of the configured paths keys (e.g., branch_review.approved), confirming that routing is expressed through ordinary edge wiring rather than compound node logic or a SwitchFunctionEditor variant. |
| error | Validation flags a BranchNode whose paths map is missing, contains fewer than two entries, or is referenced through a non-existent source suffix. The failure message includes the containing phase path and the invalid branch path key. |

### CMP-3 (PhaseDefinition) States

| State | Visual Description |
|-------|-------------------|
| sequential | Phase with mode: sequential containing only AskNode, BranchNode, or PluginNode entries in nodes, intra-phase edges in edges, and optional children. No SwitchFunctionEditor or ErrorFlowNode entries appear in nodes. |
| map | Phase with mode: map; its nodes list may be empty while children contains parallel subphases. Intra-phase edges connect nodes within this phase only; cross-phase edges that exit this phase live in WorkflowConfig.edges. |
| fold | Phase with mode: fold; children are sequential fold iterations. Cross-phase edges that connect a fold child to a sibling or parent phase live in WorkflowConfig.edges, not in this phase's edges list. |
| loop | Loop-mode phase exposes phase-level outputs condition_met and max_exceeded. Loop exit edges whose targets resolve to a node or phase in a different phase live in WorkflowConfig.edges, not in this phase's edges list. Both exits are wired through ordinary EdgeDefinition entries. |
| nested | PhaseDefinition contains local nodes (ask/branch/plugin only) under nodes and nested subphases under children. A child phase is serialized inline beneath its parent, not promoted to workflow.phases. No phases alias appears. |
| synthetic-root | Phase with id: __root__, mode: sequential, empty nodes/children/edges. Present when the bootstrap loader normalizes an empty-phases WorkflowConfig. The synthetic root is structurally indistinguishable from an authored phase except for the reserved __root__ identifier. |
| error | Validation reports one of: node.type outside {ask, branch, plugin} with path to the offending node, cross-phase edge found in phase.edges with direction to move it to workflow.edges, or use of the PhaseDefinition.phases alias. The error includes the full nested phase index (e.g., phases[0].children[1]). |

### CMP-4 (WorkflowConfig) States

| State | Visual Description |
|-------|-------------------|
| default | WorkflowConfig top level includes schema_version, workflow_version, actors, phases, and edges. workflow.edges contains only cross-phase connections. There is no top-level nodes list, no standalone hooks section, and no intra-phase edges misplaced at the workflow root. |
| schema-export | The JSON Schema served from /api/schema/workflow exposes: WorkflowConfig.phases, WorkflowConfig.edges (cross-phase), PhaseDefinition.nodes as a discriminated union (ask \| branch \| plugin), PhaseDefinition.edges (intra-phase), PhaseDefinition.children, and EdgeDefinition without port_type. The NodeDefinition discriminant contains exactly three type values. |
| bootstrap-gate | The editor is in a blocking loading state awaiting a successful 200 from /api/schema/workflow. No canvas, no node palette, and no workflow content are rendered. On failure, the only UI is a full-screen error state with the failed endpoint URL and a Retry button. |
| error | Schema delivery is stale or invalid when the served schema omits PhaseDefinition.children, omits any of the three node type variants (ask, branch, plugin), reintroduces port_type in EdgeDefinition, or is sourced from a bundled static file instead of /api/schema/workflow. |

### CMP-5 (AskNode) States

| State | Visual Description |
|-------|-------------------|
| default | AskNode appears inside phase.nodes with node.type discriminant 'ask', actor (actors dict key), and prompt set. output_type is optional. No SwitchFunctionEditor or special renderer variant is associated with this node type. |
| waiting | Runtime state only — not directly serializable in the schema. The schema defines timeout as the waiting boundary; a timed-out node produces a validation or runtime error. This state is observable in runner logs, not in the static YAML. |
| responded | AskNode's response output handle is populated at runtime; downstream edges from node_id.response can resolve. The output handle name 'response' is the canonical output port name for AskNode, discoverable from the schema-export state of CMP-4. |
| error | Validation flags missing actor, unresolvable actor reference (key not in WorkflowConfig.actors), missing prompt, or invalid output_type reference. The error message includes the containing phase path and node index. |

### CMP-6 (PluginNode) States

| State | Visual Description |
|-------|-------------------|
| default | PluginNode appears inside phase.nodes with node.type discriminant 'plugin' and plugin_ref set. config is optional. No ErrorFlowNode alternative exists; error flows route through on_end hook edges, consistent with the edge-only hook contract. |
| executing | Runtime state only — not directly serializable. The schema defines the invocation contract via plugin_ref and config; execution state is observable in runner logs but does not appear in static YAML. |
| completed | Plugin outputs are available as EdgeDefinition source suffixes derived from the plugin's registered output schema (plugin_id.output_name). The available output names are discoverable from /api/schema/workflow's PluginInterface definitions. |
| error | Validation flags unknown plugin_ref (key not in WorkflowConfig.plugins or built-in registry) or invalid config keys that don't match the plugin's declared config schema. The error message includes the phase path and node index. |

<!-- SF: declarative-schema -->
### SF-1: Declarative Schema & Primitives
N/A for the schema package itself. The D-GR-22 impact on responsive editor behavior is indirect: the three-type node constraint (ask, branch, plugin) and the cross-phase edge ownership contract reduce the editor's state space, making responsive canvas behavior in SF-6 easier to implement without tracking SwitchFunctionEditor or ErrorFlowNode variants. The blocking bootstrap gate also simplifies responsive layout because there is only one non-ready state (loading or error-with-retry) rather than a degraded view-only mode that would require its own responsive breakpoints.

<!-- SF: declarative-schema -->
### SF-1: Declarative Schema & Primitives

Authoring and consumption follow one contract everywhere. Authors serialize executable structure into WorkflowConfig.phases with AskNode, BranchNode, or PluginNode under each phase's nodes. Cross-phase connections (including cross-phase hook edges) go into WorkflowConfig.edges; intra-phase connections go into PhaseDefinition.edges. Data and hook wiring both use the same EdgeDefinition surface, with hook inference from source-port resolution and transform_fn forbidden on hook-sourced edges.

Bootstrap is a blocking gate: the editor awaits /api/schema/workflow before rendering; no view-only or degraded fallback is permitted. On failure, the editor shows a full-screen error state with a Retry button. On empty-phases bootstrap, the loader normalizes a synthetic root phase (__root__, mode: sequential) before handing the model to the editor.

JSON Schema generation is model-driven via WorkflowConfig.model_json_schema(). The generated schema must expose the three-type NodeDefinition discriminant (ask | branch | plugin), the PhaseDefinition.edges (intra-phase) vs. WorkflowConfig.edges (cross-phase) ownership split, and PhaseDefinition.children as the recursive field. Static workflow-schema.json is explicitly limited to build/test support.

Validation enforces contract drift aggressively: PhaseDefinition.phases aliases, top-level hooks sections, serialized edge.port_type, SwitchFunctionEditor, ErrorFlowNode, and cross-phase edges in phase.edges all produce explicit named rejection messages [decision: D-GR-22, three atomic node types contract, cross-phase edge ownership contract, blocking bootstrap gate, synthetic root normalization, REQ-3, REQ-4, REQ-5, REQ-16, REQ-17].

<!-- SF: declarative-schema -->
### SF-1: Declarative Schema & Primitives
No direct end-user UI is introduced in SF-1. The three-type node constraint (ask, branch, plugin) reduces the set of node-type-specific inspector panels SF-6 must implement, simplifying keyboard navigation and screen reader announcement patterns downstream. The blocking bootstrap gate prevents partial renders that could present an incomplete or inconsistent DOM to assistive technology; the editor is either fully ready or fully blocked with a clear retry affordance. The explicit cross-phase edge ownership contract ensures that edge data has one canonical location, which avoids state inconsistencies that could produce confusing focus traps or aria-live announcements in the editor.

<!-- SF: declarative-schema -->
### SF-1: Declarative Schema & Primitives

1. **Alternative A (rejected):** Keep a flat YAML authoring contract with workflow-level nodes and phase membership inferred by IDs, while only the editor stores nested phases internally. Rejected because D-GR-22 explicitly makes nested containment authoritative in YAML, and flattening would break the library-saveable, importable, detachable phase constraints from REQ-20.
2. **Alternative B (rejected):** Preserve a separate serialized hooks section or edge.port_type discriminator to distinguish lifecycle wiring from data flow. Rejected because the authoritative edge model already infers hook semantics from source-port resolution, and duplicating that classification in serialized data reintroduces the SF-1/SF-6 split D-GR-22 was written to close.
3. **Alternative C (rejected):** Treat static workflow-schema.json as the composer's canonical schema source, with /api/schema/workflow only as an optional mirror. Rejected because runtime-served schema prevents drift between backend validation and frontend inspector generation; D-GR-22 demotes the static artifact to build/test only.
4. **Alternative D (rejected):** Allow SwitchFunctionEditor as a valid schema node type for conditional plugin dispatch. Rejected because SF-6 PRD and the cycle 5 feedback explicitly remove SwitchFunctionEditor; the condition/dispatch pattern is fully covered by BranchNode + PluginNode wiring without a compound or editor-specific node type.
5. **Alternative E (rejected):** Allow ErrorFlowNode as a dedicated error-routing node type. Rejected because the cycle 5 feedback explicitly removes ErrorFlowNode; error flows are expressed as hook edges off on_end ports, consistent with the D-GR-22 edge-only hook contract and REQ-8.
6. **Alternative F (rejected):** Allow view-only or degraded editor mode when /api/schema/workflow is unavailable. Rejected because the blocking bootstrap gate is a hard contract requirement from the SF-6 PRD; permitting degraded modes would allow the editor to display stale or incorrect schema-derived inspector panels, and is explicitly cited as a problem in the cycle 5 feedback.
7. **Alternative G (rejected):** Store all edges (including cross-phase) in the containing phase's edges list. Rejected because the cross-phase edge ownership contract requires WorkflowConfig.edges to be the authoritative home for connections that cross phase boundaries, ensuring serializer, validator, and editor all have a single unambiguous location.
8. **Alternative H (adopted):** One nested YAML contract, three atomic node types (ask, branch, plugin), cross-phase edges at workflow root, synthetic root normalization on bootstrap, and blocking /api/schema/workflow gate with no view-only fallback. This is the complete contract that SF-6's editor save/load path implements against.

<!-- SF: declarative-schema -->
### SF-1: Declarative Schema & Primitives

The prior declarative-schema artifact correctly locked nested YAML phase containment, edge-based hook wiring, and runtime schema delivery per D-GR-22, but did not encode four additional constraints that SF-6's editor contract requires and that the cycle 5 feedback calls out explicitly as missing: (1) exactly three atomic node types — AskNode, BranchNode, PluginNode — for direct phase.nodes placement; SwitchFunctionEditor and ErrorFlowNode are not valid schema node types; (2) cross-phase edge ownership at WorkflowConfig.edges with PhaseDefinition.edges restricted to intra-phase connections only; (3) a blocking schema bootstrap gate with no view-only or degraded fallback; and (4) synthetic root phase normalization so the editor always receives at least one phase from the loader.

These omissions would have allowed stale SF-6 artifacts to reintroduce the rejected SwitchFunctionEditor and ErrorFlowNode types, place cross-phase edges ambiguously inside phase.edges, keep a view-only fallback mode, and skip synthetic root normalization for empty workflows. The feedback identifies all four as blocking issues that the declarative-schema must own so SF-6's editor reset pushes only the required save/load contract downstream.

The revised artifact closes those gaps by promoting all four constraints to schema-level contracts: the NodeDefinition discriminant union (ask | branch | plugin) enforces the three-type limit in model_json_schema output; PhaseDefinition.edges vs. WorkflowConfig.edges ownership is explicit in both the model definition and validation error messages; the bootstrap gate and synthetic root normalization are first-class schema-loader behaviors that CMP-4 and CMP-3 document as verifiable states. These five constraints together — nested containment (D-GR-22), three atomic types, cross-phase edge ownership, blocking bootstrap, and synthetic root normalization — form the complete contract that SF-6's editor save/load path must implement against, with no stale alternatives permitted.
