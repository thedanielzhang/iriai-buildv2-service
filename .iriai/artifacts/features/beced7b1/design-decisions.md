# Design Decisions — Compiled

> **Compiled from 7 subfeature design artifacts.** No broad design artifact was provided; framing derives from the decomposition and per-subfeature artifacts. All detail is preserved verbatim from source artifacts with globally re-numbered IDs.

## Component ID Mapping

| Global ID | Original ID | Component Name | Subfeature |
|-----------|-------------|----------------|------------|
| CMP-1 | SF-1:CMP-7 | EdgeDefinition | declarative-schema |
| CMP-2 | SF-1:CMP-8 | BranchNode | declarative-schema |
| CMP-3 | SF-1:CMP-9 | PhaseDefinition | declarative-schema |
| CMP-4 | SF-1:CMP-10 | WorkflowConfig | declarative-schema |
| CMP-5 | SF-1:CMP-11 | AskNode | declarative-schema |
| CMP-6 | SF-1:CMP-12 | PluginNode | declarative-schema |
| CMP-7 | SF-3:CMP-1 | MockAgentRuntime | testing-framework |
| CMP-8 | SF-3:CMP-2 | MockInteractionRuntime | testing-framework |
| CMP-9 | SF-3:CMP-3 | MockPluginRuntime | testing-framework |
| CMP-10 | SF-4:CMP-1 | Node Card Reads Metadata | workflow-migration |
| CMP-11 | SF-4:CMP-2 | Tier 2 Mock Runtime Contract | workflow-migration |
| CMP-12 | SF-4:CMP-3 | Consumer Integration Boundary | workflow-migration |
| CMP-13 | SF-5:CMP-3 | SidebarTree | composer-app-foundation |
| CMP-14 | SF-5:CMP-10 | NewDropdown | composer-app-foundation |
| CMP-15 | SF-5:CMP-15 | GridCard | composer-app-foundation |
| CMP-16 | SF-5:CMP-18 | EditorSchemaBootstrapGate | composer-app-foundation |
| CMP-17 | SF-5:CMP-19 | YAMLContractErrorPanel | composer-app-foundation |
| CMP-18 | SF-6:CMP-35 | AskFlowNode | workflow-editor |
| CMP-19 | SF-6:CMP-36 | BranchFlowNode | workflow-editor |
| CMP-20 | SF-6:CMP-37 | PluginFlowNode | workflow-editor |
| CMP-21 | SF-6:CMP-42 | DataEdge | workflow-editor |
| CMP-22 | SF-6:CMP-43 | HookEdge | workflow-editor |
| CMP-23 | SF-6:CMP-64 | OutputPathsEditor | workflow-editor |
| CMP-24 | SF-6:CMP-65 | MergeFunctionEditor | workflow-editor |
| CMP-25 | SF-6:CMP-66 | PortConditionRow | workflow-editor |
| CMP-26 | SF-6:CMP-68 | PhaseContainer | workflow-editor |
| CMP-27 | SF-6:CMP-69 | SchemaBootstrapGate | workflow-editor |
| CMP-28 | SF-7:CMP-133 | EntityDeleteDialog | libraries-registries |
| CMP-29 | SF-7:CMP-134 | LibraryCollectionPage | libraries-registries |
| CMP-30 | SF-7:CMP-135 | RoleEditorForm | libraries-registries |
| CMP-31 | SF-7:CMP-136 | ToolEditorForm | libraries-registries |
| CMP-32 | SF-7:CMP-137 | ActorSlotsEditor | libraries-registries |
| CMP-33 | SF-7:CMP-138 | ResourceStateCard | libraries-registries |

---

## Design Approach

<!-- SF: declarative-schema -->
### SF-1: Declarative Schema & Primitives

Re-baseline SF-1 to D-GR-22 plus the five SF-6 editor contract requirements the feedback identifies as missing. The authoritative YAML contract now locks five things together: (1) nested phase containment — WorkflowConfig.phases as the top-level phase list, PhaseDefinition.children as the only recursive field (not phases); (2) exactly three atomic node types — AskNode, BranchNode, PluginNode (type discriminant values: ask | branch | plugin) — as the only varieties that may appear in PhaseDefinition.nodes; SwitchFunctionEditor and ErrorFlowNode are not schema node types and must be explicitly rejected by validation; (3) cross-phase edge ownership — WorkflowConfig.edges holds every EdgeDefinition whose source and target resolve to different phases; PhaseDefinition.edges holds only intra-phase connections; (4) synthetic root phase normalization — loading a WorkflowConfig whose phases list is empty normalizes a synthetic root phase (id: __root__, mode: sequential, empty nodes/children/edges) so the editor always receives at least one phase; and (5) a blocking schema bootstrap gate — the composer must receive a 200 from /api/schema/workflow before the canvas renders; no view-only or degraded fallback is permitted. Hook wiring remains edge-based per D-GR-22 with no separate hooks section and no serialized port_type. Composer schema delivery is runtime-served from /api/schema/workflow; static workflow-schema.json is retained for build/test only.

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner

Supersede the older Branch-only revision with the D-GR-22 contract bundle as SF-2's baseline. The loader/runner now assumes one canonical serialized workflow shape: `WorkflowConfig.phases[]` at the root, each phase containing `nodes[]` and `children[]`, with recursion used for parsing, validation, and execution graph construction instead of any flat top-level `nodes` contract. Hook behavior is serialized only through ordinary `edges`; hook-vs-data is inferred from the resolved source port container, so SF-2 does not read or write a separate hook section or serialized `edge.port_type`. For composer consumers, the only canonical schema source is `WorkflowConfig.model_json_schema()` served through `/api/schema/workflow`; any checked-in `workflow-schema.json` is secondary build/test output, not the runtime contract. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/plan-review-discussion-4.md:20] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1004] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1006] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1021] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:353] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:510]

<!-- SF: testing-framework -->
### SF-3: Testing Framework

Revised the testing-framework artifact at `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/design-decisions.md` to align with D-GR-23. The update removes stale `invoke(..., node_id=...)` assumptions, makes `ContextVar`-based node propagation the canonical mock-routing contract, and standardizes hierarchical context merge order as `workflow -> phase -> actor -> node`. No code tests were run because this was a document-only revision.

<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test

Revised the workflow-migration design artifact to match D-GR-23's runtime contract by removing any dependence on a breaking `invoke(..., node_id=...)` interface, explicitly documenting ContextVar-based node propagation, and standardizing effective context assembly to `workflow -> phase -> actor -> node` throughout the artifact.

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub

SF-5 treats `/api/schema/workflow` as the only runtime schema handshake between the compose app and `iriai-compose`. The Explorer shell still loads independently, but editor entry, import validation, save/export behavior, and stale-contract error messaging all align to the same persisted workflow contract: nested phase containment (`phases[].nodes`, `phases[].children`) and edge-only hook serialization with no separate serialized `port_type`. The foundation therefore owns the schema bootstrap/loading/error experience and must never treat a bundled `workflow-schema.json` as the runtime source of truth.

SF-5 also owns the definition of four contract requirements that cascade downstream to SF-6's implementation. These are non-negotiable persistence/bootstrap requirements, not SF-6 design choices: (1) Synthetic root phase normalization — every workflow the editor opens must have at least one phase; if the stored payload has no phases, the load path wraps content in a synthetic root phase before the canvas mounts. (2) Three atomic node types — only Ask, Branch, and Plugin nodes are directly placeable in the editor canvas; no SwitchFunctionEditor or ErrorFlowNode surfaces in the palette, inspector, or serialization format. (3) Cross-phase edges at workflow root — edges that connect nodes in different phases are stored in the workflow-root `edges` array, never inside a phase definition. (4) Blocking schema gate — the schema bootstrap gate has no view-only fallback; when schema is unavailable, the editor shows the blocking error panel and nothing else.

<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas

The revised design builds on the Cycle 4 canonical contract—nested YAML phase containment, edge-based hook serialization, and `/api/schema/workflow` as the canonical schema source—and closes the three gaps the prior cycle left open.

First, the node vocabulary is reduced to the three atomic types the SF-6 PRD requires (REQ-2): Ask, Branch, and Plugin. ErrorFlowNode is removed entirely. It does not appear in REQ-2 or anywhere in the PRD and was incorrectly carried into the prior design cycle. Terminal and error conditions are expressed through Branch conditions and phase-level routing rather than a dedicated terminal node. SwitchFunctionEditor is explicitly excluded; branch condition authoring remains in OutputPathsEditor and PortConditionRow under the D-GR-12 per-port model.

Second, synthetic root phase normalization is added as a first-class editor contract aligned with AC-7: the canvas has an implicit synthetic root phase; unparented nodes always belong to it; and the normalizer runs on every save, auto-save, export, and import so that no node exists outside a phase container in the persisted YAML. The synthetic root phase has no visible canvas boundary but always exists structurally as the first `workflow.phases[]` entry when it contains any nodes.

Third, cross-phase edge ownership at `workflow.edges[]` is made explicit and inviolable. Any edge whose source and target belong to different phases—including edges crossing the synthetic root boundary—serializes to `workflow.edges[]`. Intra-phase edges are owned by their containing phase. This rule applies identically to DataEdge and HookEdge.

All component paths are relative to `tools/compose/frontend/src/`. The editor boots against the SF-5 compose foundation contract only and must not depend on `tools/iriai-workflows`, SQLite, `/api/plugins`, or `workflow_entity_refs` endpoints. SchemaBootstrapGate is strictly blocking with no view-only fallback: REQ-13 and AC-10 require a blocking error state when the schema endpoint is unavailable, and the design enforces this at the gate component level with no partial initialization path.

<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries

SF-7 is scoped to the library extension layer on top of SF-5's five-table compose foundation in `tools/compose`. The design centers four launch-critical capabilities: role/schema/template delete preflight backed by persisted workflow references, Tool Library CRUD with role-aware delete blocking, task-template `actor_slots` persistence UX, and explicit auth/validation feedback for 404/413/422 outcomes. Plugin-library surfaces, promotion flows, validation-code chips, and stale legacy scope are removed. Component paths are relative to `tools/compose/frontend/src/` — not the deprecated `tools/iriai-workflows` topology.

SF-6 save/load contract alignment: SF-7's `workflow_entity_refs` reference index is entirely downstream of SF-6's blocking schema bootstrap gate. Library pages load and operate independently of whether any workflow canvas is bootstrapped. `workflow_entity_refs` rows are only written when SF-6's bootstrap-gated save path completes — pre-bootstrap editor state and unsaved canvas changes never count as persisted references. Only the three atomic node types SF-6 supports for direct placement — Ask, Branch, and Plugin — can create library entity references. SwitchFunctionEditor and ErrorFlowNode do not exist in SF-6 and cannot generate reference rows. Synthetic root phase normalization means the implicit root container is a serialization artifact, not a user-placed node; it never creates actor, schema, or tool reference rows. Cross-phase edges stored at the workflow root in SF-6's nested YAML are included in the save path's reference scan, so Ask nodes connecting across phases via workflow-root edges still register actor references correctly.

---

## Journey Annotations

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

<!-- SF: dag-loader-runner -->
### J-1 — SF-2: DAG Loader & Runner

**Step Annotations:**
- Step 1 uses a shared recursive load path for both `validate()` and `run()`: parse YAML, `model_validate()`, then descend through `WorkflowConfig.phases -> PhaseDefinition.nodes/children`; stale flat contracts such as top-level `nodes` or nested `phase.phases` are rejected instead of silently normalized. [decision: D-GR-22]
- Graph construction keeps hook wiring inside ordinary edge lists only. An edge is treated as a hook when its `source` port resolves from the element's hook container; SF-2 never requires a serialized `port_type` field in YAML. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1004]
- Composer-facing schema assumptions for this journey come from `/api/schema/workflow`, backed by `WorkflowConfig.model_json_schema()`. The runner must not depend on a static schema snapshot being newer than the live library models. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:353] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:510]

**Error Path UX:** Validation returns deterministic nested field paths such as `phases[0].children[1].edges[2]` for illegal `port_type`, malformed dot-notation, or deprecated separate hook serialization so downstream API/UI layers can point to the exact failing container.

**Empty State UX:** An empty workflow or empty sibling container fails structural validation rather than falling back to an implicit flat root graph.

**NOT Criteria:**
- SF-2 must NOT accept a separate serialized hook-wiring section as a second source of truth.
- SF-2 must NOT require or emit `edge.port_type` in persisted YAML.
- SF-2 must NOT treat a checked-in `workflow-schema.json` file as the authoritative runtime schema contract.
- SF-2 must NOT rebuild a flat top-level `workflow.nodes` format as its persisted contract.

<!-- SF: composer-app-foundation -->
### J-1 — SF-5: Composer App Foundation & Tools Hub

**Step Annotations:**
- Tools hub navigation remains same-tab and auth-aware, but the compose Workflows shell renders without waiting on editor schema delivery.
- The Workflows view is the first stable landing state; schema-dependent editor affordances are deferred until the user opens an editor route.
- If schema delivery later fails, the user stays inside the authenticated compose shell and gets a recoverable editor-specific failure state rather than a blank app shell.

**Error Path UX:** Editor bootstrap failures use an in-content blocking panel with Retry and Back to Workflows actions while the shell chrome remains intact.

**Empty State UX:** The Workflows empty state still shows starter templates plus primary create/import actions without requiring the runtime schema endpoint first.

**NOT Criteria:**
- The compose shell must NOT block initial Workflows rendering on `/api/schema/workflow`.
- Navigation from tools hub must NOT open a new tab.
- The app must NOT preload a static schema file and treat it as the runtime contract.
- App shell must NOT provide a view-only or read-only mode for the editor when schema is unavailable.

<!-- SF: workflow-editor -->
### J-1 — SF-6: Workflow Editor & Canvas

**Step Annotations:**
- Step 1 — Open editor: SchemaBootstrapGate (CMP-27) owns the full editor viewport until both the workflow record and `/api/schema/workflow` succeed. There is no partial initialization mode and no view-only fallback. If either request fails the gate shows the blocking error card and all canvas, palette, inspector, and save surfaces remain disabled behind it.
- Step 2 — Drag Ask node: AskFlowNode (CMP-18) is one of three atomic types available for direct canvas placement. Nodes dropped onto the top-level canvas without an explicit phase parent belong to the implicit synthetic root phase. The synthetic root phase is not rendered as a visible PhaseContainer boundary—nodes within it appear as floating elements on the grid—but it exists structurally as the first `workflow.phases[]` entry after normalization.
- Step 3 — Add Branch and connect: BranchFlowNode (CMP-19) receives a typed DataEdge (CMP-21) from the Ask output. Branch path handles update live via OutputPathsEditor (CMP-23) and PortConditionRow (CMP-25). No SwitchFunctionEditor is created; all condition authoring stays in CMP-23 and CMP-25. Both source nodes are in the synthetic root phase at this point; the connecting edge is intra-root and serializes to the synthetic root phase's edge list.
- Step 4 — Create phase, fold mode, validate, save: creating a fold phase wraps selected nodes in PhaseContainer (CMP-26) and removes those nodes from the synthetic root. The save/export normalizer then: (a) collects any remaining unparented nodes into the synthetic root phase entry in `workflow.phases[]`, (b) routes each edge to `workflow.edges[]` if it crosses a phase boundary or to the containing phase's edge list if intra-phase, (c) emits hook edges as ordinary dot-notation edges with `transform_fn: null`, and (d) preserves per-node and per-phase position metadata for lossless round-trip.

**Error Path UX:** If `/api/schema/workflow` fails, the editor remains behind the blocking error card with a Retry button and a concise failure summary. No canvas, palette, or inspector surface is partially initialized. If save normalization encounters a node that cannot be assigned to any phase (an impossible state given the synthetic root always exists), the normalizer marks the node with the error visual treatment and blocks save until the structure is resolvable.

**Empty State UX:** After schema bootstrap succeeds, the blank canvas shows the grid, the three-type primitive palette (Ask, Branch, Plugin only), and helper copy for dropping the first node. The synthetic root phase exists but has no visible boundary. Before bootstrap completes, the SchemaBootstrapGate loading card occupies the entire editor area.

**NOT Criteria:**
- The palette must NOT include ErrorFlowNode or any node type beyond Ask, Branch, and Plugin for direct canvas placement.
- SwitchFunctionEditor must NOT exist as a component; all branch condition editing belongs in OutputPathsEditor and PortConditionRow.
- The editor must NOT offer a view-only fallback or partial initialization mode while schema bootstrap is pending or has failed; SchemaBootstrapGate is the only gate state.
- Save and export must NOT emit loose top-level nodes; synthetic root normalization is mandatory on every save, export, and auto-save.
- Save and export must NOT serialize `edge.port_type`, `from_port`, `to_port`, or any separate hooks section.
- Cross-phase edges must NOT be placed in a phase's edge list; they belong exclusively in `workflow.edges[]`.
- SF-6 must NOT depend on `tools/iriai-workflows`, SQLite, `/api/plugins`, or `workflow_entity_refs` at any point in boot, save, load, or validate.

<!-- SF: libraries-registries -->
### J-1 — SF-7: Libraries & Registries

**Step Annotations:**
- Step 1 renders a shared LibraryCollectionPage with XP cards, search, and a sticky New Role action; warm-cache data stays visible while React Query silently refreshes in the background.
- Step 2 opens RoleEditorForm in the content pane, with built-in tools grouped above registered tools so the user can distinguish permanent catalog items from editable custom tools. Validation stays inline at the field row and the save bar remains disabled until required fields are valid.
- Step 3 uses optimistic route continuity but not optimistic entity creation: success is confirmed only after the role save returns and the cached role list plus Ask-node picker queries are invalidated.

**Error Path UX:** If the role save fails with 422 or 413, the editor stays open, focus moves to the first invalid field or the banner heading, and no success toast is shown. If a detail route resolves to a cross-user or deleted role, the pane renders a neutral not-found state rather than an access-denied message.

**Empty State UX:** The Roles list empty state shows a short explainer, 'Create your first role', and a secondary 'Browse tools' hint so the user understands why the tools checklist will be useful once creation starts.

**NOT Criteria:**
- The Roles list must NOT blank the whole page during background refetch.
- Role save must NOT create duplicates from repeated clicks while a request is pending.
- The Role editor must NOT merge built-in and custom tools into one unlabeled checklist.

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

<!-- SF: dag-loader-runner -->
### J-2 — SF-2: DAG Loader & Runner

**Step Annotations:**
- Nested execution preserves containment: each phase executes its local `nodes` plus nested `children` without flattening the entire workflow into one global node list. This keeps translated fold > loop and loop > map structures representable. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:35]
- Cross-phase flow still uses normal edges and boundary ports, but child phases remain serialized inside their parent phase in YAML. The runner builds container-local DAGs from that nested structure instead of asking SF-6 to persist a flat graph. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1006] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1021]

**Error Path UX:** Containment errors identify the closest enclosing phase path so authors can tell whether the defect belongs to the parent container or a specific child phase.

**Empty State UX:** A phase with no executable `nodes` and no `children` is treated as invalid configuration until the schema explicitly defines a pure container-only phase mode.

**NOT Criteria:**
- The runner must NOT flatten `children` into the workflow root before validation.
- Nested phase edges must NOT be rewritten into a separate hook registry or callback section.
- Hook edges must NOT bypass phase-boundary resolution rules just because they target nested content.

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

<!-- SF: composer-app-foundation -->
### J-2 — SF-5: Composer App Foundation & Tools Hub

**Step Annotations:**
- `+ New` stays a lightweight toolbar action in SF-5 and creates a workflow plus `WorkflowVersion` v1 before any editor boot happens.
- Opening the new workflow transitions into an editor bootstrap gate that requests the workflow record and `/api/schema/workflow`.
- New workflow creation seeds a synthetic root phase in the initial persisted YAML so the editor always opens into a canonically phased workflow — the canvas never mounts a phaseless node graph, even for brand-new workflows.
- The newly created workflow is treated as canonical nested YAML from the start; no flat root-level node graph is ever presented as the saved contract.

**Error Path UX:** If workflow creation succeeds but schema bootstrap fails on open, the user sees the schema gate error panel rather than losing the created workflow.

**Empty State UX:** When no workflows exist, the empty state keeps focus on create/import/template actions and does not expose editor-only schema setup UI.

**NOT Criteria:**
- Workflow creation must NOT succeed without `WorkflowVersion` v1.
- Opening `/workflows/{id}/edit` must NOT assume a bundled schema is good enough.
- The create flow must NOT generate or persist legacy top-level node storage.
- Workflow creation must NOT produce a flat phaseless node graph in the initial persisted YAML.

<!-- SF: workflow-editor -->
### J-2 — SF-6: Workflow Editor & Canvas

**Step Annotations:**
- Step 1 — Arrange nodes: loose Ask, Branch, and Plugin nodes placed on the canvas without an explicit phase are implicitly in the synthetic root phase. No PhaseContainer boundary is visible for the root.
- Step 2 — Create outer fold phase: the selection-rectangle gesture creates a PhaseContainer (CMP-26) whose `parentId` is null in the flat store, mapping to a top-level named phase in `workflow.phases[]`. Nodes moved into this container are removed from the synthetic root phase's node list. Edges from inside the named phase to nodes still in the synthetic root are cross-phase and serialize to `workflow.edges[]` on save.
- Step 3 — Create nested inner loop phase: the inner PhaseContainer's `parentId` in the flat store points to the outer phase. On save, this nesting is expressed through the outer phase's `children[]` array. The loop's `condition_met` and `max_exceeded` exits serialize as edge source references anchored on the inner phase boundary. An edge from inside the inner loop to a node in the outer phase (but not beyond) routes to the outer phase's edge list; an edge that exits the outer phase entirely routes to `workflow.edges[]`.

**Error Path UX:** Selections that would force a node into two different phase containers simultaneously, include read-only template children, or create a containment cycle are rejected before phase creation. The canvas preserves the pre-selection state and shows an inline toast explaining the containment rule that was violated. Nested phase export that detects a node appearing in both a parent `nodes[]` and a sub-phase `nodes[]` is treated as a normalization error and blocks save.

**Empty State UX:** A newly created named phase with no sub-phases renders as a tinted boundary with a mode-labeled header and default ports only. The synthetic root phase remains invisible. Collapsed named phases switch to the compact CollapsedGroupCard pattern.

**NOT Criteria:**
- Nested phase export must NOT duplicate inner nodes into both the outer phase `nodes[]` and the inner phase `nodes[]`.
- Cross-phase edges must NOT be placed in `phase.edges[]`; they belong in `workflow.edges[]`.
- The synthetic root phase must NOT be rendered as a visible PhaseContainer boundary on the canvas.
- SwitchFunctionEditor must NOT be introduced to handle branch conditions inside any phase.

<!-- SF: libraries-registries -->
### J-2 — SF-7: Libraries & Registries

**Step Annotations:**
- Step 1 opens EntityDeleteDialog in a loading/checking state immediately on user intent; the modal body reserves space for the workflow list so the dialog does not jump when the preflight response arrives.
- Step 1 reference resolution: the blocked-by-workflows list comes from workflow_entity_refs rows written during SF-6's bootstrap-gated save. Workflows open in the editor but not yet saved do not count as active references; dialog copy must say 'saved workflows' not 'open workflows' and must not suggest closing the editor resolves the block.
- Step 2 assumes that clearing the block requires a workflow save in SF-6 (which only runs after bootstrap succeeds). Delete copy explicitly directs the user back to the editor for a save rather than expecting unsaved canvas changes to count.
- Step 3 reruns the preflight check every time the dialog reopens. A zero-reference result reveals the destructive action; a stale 409 after confirm rehydrates the blocked-workflows state using the server payload instead of a generic toast.

**Error Path UX:** Preflight transport failures keep the modal open with Retry and Close actions; Delete remains disabled. Server-side delete races reuse the same workflow-list presentation rather than switching to a different modal pattern.

**Empty State UX:** When no workflows reference the role, the modal uses concise recoverability copy and shows only Cancel plus Delete role.

**NOT Criteria:**
- The role delete flow must NOT call DELETE merely to discover references.
- The blocked state must NOT claim that unsaved SF-6 editor state or pre-bootstrap canvas state counts as a persisted reference.
- Synthetic root phase containers in SF-6's serialized YAML must NOT appear as reference sources in the blocked-by-workflows list.
- The blocked state must NOT show validation-code chips or plugin-specific warnings outside the current SF-7 scope.
- Cross-phase edges stored at the workflow root in SF-6's save format MUST count as persisted references once the save path completes; they must NOT be silently excluded from the reference scan.

<!-- SF: dag-loader-runner -->
### J-3 — SF-2: DAG Loader & Runner

**Step Annotations:**
- `validate()` reuses the same nested YAML parser as `run()`, but stops before runtime hydration, so editor and API callers can detect stale shape mismatches without `agent_runtimes` or `RuntimeConfig`. [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:795]
- Legacy documents that still serialize hook metadata separately or include `edge.port_type` receive schema errors instructing authors to move hook wiring into edges and rely on source-port resolution. [decision: D-GR-22]

**Error Path UX:** Errors are returned as field-scoped validation problems consumable by SF-5 validate endpoints and SF-6 inspector panels.

**Empty State UX:** N/A — validation API surface, not a rendered UI state.

**NOT Criteria:**
- `validate()` must NOT require runtime dependencies to reject stale serialized contracts.
- Validation must NOT autocorrect deprecated hook serialization silently.
- Validation must NOT accept a static schema artifact as the canonical parse contract.

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

<!-- SF: composer-app-foundation -->
### J-3 — SF-5: Composer App Foundation & Tools Hub

**Step Annotations:**
- Editor route boot requests the workflow payload and canonical schema in parallel, then mounts schema-driven UI only after both resolve.
- Synthetic root phase normalization is applied in the load path before the canvas mounts: if the loaded workflow has no phase structure, it is wrapped in a synthetic root phase. The canvas always receives a workflow with at least one phase — it never renders a phaseless flat node graph.
- The editor canvas surfaces only Ask, Branch, and Plugin node types for direct placement. SwitchFunctionEditor and ErrorFlowNode do not appear in the node palette, inspector, or canvas.
- Cross-phase edges in the loaded workflow appear in the workflow-root `edges` array; the editor reconstructs phase membership from node containment. No cross-phase edges are read from inside phase definitions.
- The schema gate is route-scoped and cache-backed: once `/api/schema/workflow` resolves for the session, later editor entries can reuse the cached contract until an explicit refresh.
- Inspector fields and validation behavior are explained as runtime-backed, not static-file-backed, so user-facing error copy stays consistent with the backend and runner.

**Error Path UX:** Loading state shows a dedicated schema bootstrap card with spinner and disabled editor scaffolding; failure state replaces it with a retryable blocking error panel.

**Empty State UX:** If the workflow exists but has no editable content yet, the editor host still waits for the canonical schema first and then shows the editor's own empty canvas state.

**NOT Criteria:**
- The editor must NOT fall back silently to `workflow-schema.json` when `/api/schema/workflow` fails.
- Schema delivery must NOT depend on a separate frontend build artifact being current.
- Editor state must NOT assume persisted nodes live outside phases.
- Editor canvas must NOT render SwitchFunctionEditor or ErrorFlowNode types.
- Editor must NOT mount a phaseless flat node graph — synthetic root normalization must complete before canvas mount.
- Cross-phase edges must NOT be read from inside phase definitions.
- The editor must NOT enter a view-only or degraded-editing mode in lieu of the blocking error state.

<!-- SF: libraries-registries -->
### J-3 — SF-7: Libraries & Registries

**Step Annotations:**
- Step 1 reuses EntityDeleteDialog, but the blocked state swaps workflow names for role names because tool usage is role-local rather than workflow-backed.
- Step 2 depends on persisted role saves, not local checklist edits. The dialog copy and empty/loading states stay symmetric with J-2 so the user does not need to learn a second delete pattern.
- Step 3 invalidates both the tools list and any cached role-editor tool checklist query so the deleted tool disappears the next time a role form mounts.

**Error Path UX:** Tool reference-check failures show retryable inline error copy in the dialog. Tool save or delete failures surface a toast plus inline banner in the tool editor if the detail pane is open.

**Empty State UX:** If the user has no custom tools, the Tools page empty state shows the built-in catalog first and a Register custom tool CTA second so the page still feels useful before any CRUD records exist.

**NOT Criteria:**
- Tool deletion must NOT read from workflow_entity_refs.
- The blocked tool delete state must NOT show workflow names.
- The Role editor must NOT continue showing deleted custom tools after a successful invalidate-and-refetch cycle.

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

<!-- SF: composer-app-foundation -->
### J-4 — SF-5: Composer App Foundation & Tools Hub

**Step Annotations:**
- Save/export copy and confirmation states describe the persisted YAML as nested phases, not as the editor's internal flat graph store.
- Save writes only Ask, Branch, and Plugin nodes to the persisted phase structure; the editor rejects persistence of any other node type.
- Cross-phase edges are written to the workflow-root `edges` array on save; they are never serialized inside any phase definition.
- Hook connections are preserved as ordinary edges in save and export flows; any UI-only hook/data distinction is derived transiently from port resolution.
- Save success feedback does not claim success until the canonical YAML has been accepted and a new workflow version has been appended.

**Error Path UX:** Save validation failures point to canonical-contract issues in a structured path/message panel (YAMLContractErrorPanel) and keep the editor in place for correction.

**Empty State UX:** If a workflow has no nested phases yet, save remains disabled or no-op in the editor layer rather than producing placeholder flat-graph YAML from the shell.

**NOT Criteria:**
- Save/export must NOT emit a separate serialized hooks section.
- Saved hook edges must NOT depend on serialized `port_type`.
- Export must NOT diverge from the same nested contract used for save and validate.
- Save must NOT persist node types other than Ask, Branch, and Plugin inside phase definitions.
- Cross-phase edges must NOT be serialized inside any phase definition.

<!-- SF: workflow-editor -->
### J-4 — SF-6: Workflow Editor & Canvas

**Step Annotations:**
- Steps 1–2 — Create hook edge: hook edges are created only by dragging from `on_start` or `on_end` handles on nodes or phase boundaries. The canvas classifies the edge as a HookEdge (CMP-22) by resolving the source handle against the source element's hooks container. Hook edges connecting nodes within the same phase route to that phase's edge list on save; hook edges crossing a phase boundary—including a boundary to or from the synthetic root phase—route to `workflow.edges[]`.
- Step 3 — Inspect hook edge: the hook edge inspector remains intentionally read-only except for delete. It surfaces the fire-and-forget semantics and the absence of transform support, consistent with the serialized payload differing only in the absence of a transform.
- Step 4 — Export hook wiring: export emits hook wiring as `source: "generate_prd.on_end"`, `target: "publish_artifact.input"`, `transform_fn: null`, placed in either `workflow.edges[]` or the appropriate phase edge list based on the source and target phase membership. Re-import rebuilds the dashed HookEdge appearance by resolving the source port against the source element's hooks container rather than reading any serialized `port_type`.

**Error Path UX:** If a user attempts to attach a transform to a hook edge, the edge inspector shows a blocking inline error and the save button remains blocked until the transform is cleared. Hook edges targeting missing nodes or ports on import are listed in the validation panel and do not silently disappear.

**Empty State UX:** Nodes and phases always render their hook handles even when no hook edges exist yet, keeping the lifecycle wiring affordance visible without opening any inspector.

**NOT Criteria:**
- Hook edge export must NOT include `port_type: hook` or a parallel hook-specific YAML section.
- Hook edges must NOT share the same midpoint type badge or transform editor as data edges.
- Hook edges must NOT bypass the cross-phase vs. intra-phase edge routing rule that applies to data edges; their containment routing is identical.

<!-- SF: libraries-registries -->
### J-4 — SF-7: Libraries & Registries

**Step Annotations:**
- Step 1 uses ActorSlotsEditor as a structured repeater embedded beside the task-template canvas summary. Each slot row collects slot name, actor type constraint, and optional default role in one compact row with add/remove controls.
- Step 2 confirms persistence through a full page refresh path, so success feedback includes a short note that actor slots were saved to the reusable template definition, not just the current browser session.

**Error Path UX:** Duplicate slot names, blank slot names, and invalid default-role bindings are shown inline on the row plus in a summary banner above the table. Save remains disabled until row-level issues are cleared.

**Empty State UX:** A first-time template with no actor slots renders a dashed empty panel titled 'No actor slots defined yet' with a primary 'Add actor slot' button and helper text explaining where slot bindings are used.

**NOT Criteria:**
- Actor slots must NOT exist only in local canvas state.
- Save success must NOT omit persisted actor_slots from the follow-up detail response.
- The actor-slot editor must NOT allow duplicate slot names to appear valid.

<!-- SF: declarative-schema -->
### J-5 — SF-1: Declarative Schema & Primitives

**Step Annotations:**
- Step 1: Loop-mode exit routing remains phase-level and stays within the nested containment model. condition_met and max_exceeded are PhaseDefinition output ports on the loop phase. If the downstream target is a node or phase within the same parent phase, the exit edge lives in the parent phase's edges list. If the exit edge connects to a node or phase in a different phase — for example, a post-loop processing phase at the workflow level — the edge serializes in WorkflowConfig.edges, not in any PhaseDefinition.edges [REQ-7, REQ-5, decision: D-GR-22, cross-phase edge ownership contract].

**Error Path UX:** Loop validation failures name the loop phase that omitted or miswired condition_met/max_exceeded, including the nested phase path if the loop is inside children. Cross-phase loop exit edges misplaced in phase.edges produce: 'loop exit edge[N] crosses phase boundary; move to workflow.edges'. The error surface does not flatten loop exits into pseudo-nodes or a separate control-flow section [REQ-7, REQ-17, cross-phase edge ownership contract].

**Empty State UX:** A loop phase with no max_iterations still keeps the same phase-scoped output contract; max_exceeded remains a dormant phase output rather than moving to a separate metadata block [REQ-7].

**NOT Criteria:**
- Loop exit routing must NOT bypass the ordinary phase/edge model or introduce special top-level control-flow collections [REQ-7, decision: D-GR-22].
- Cross-phase loop exit edges must NOT be placed in PhaseDefinition.edges; they belong in WorkflowConfig.edges [cross-phase edge ownership contract, REQ-5].

<!-- SF: composer-app-foundation -->
### J-5 — SF-5: Composer App Foundation & Tools Hub

**Step Annotations:**
- Schema endpoint failure is handled as a recoverable route state inside the content area, not as an app-wide fatal crash.
- Retry is the primary recovery action; Back to Workflows is secondary so the user can keep navigating even while schema delivery is down.
- Error copy explicitly names `/api/schema/workflow` as unavailable to reinforce that the runtime contract comes from the backend.

**Error Path UX:** Red bordered panel with concise explanation, retry button, secondary back action, and preserved route context.

**Empty State UX:** Not applicable; this is a blocking error state rather than a data-empty state.

**NOT Criteria:**
- The screen must NOT stay in an infinite spinner.
- The app must NOT hard refresh to recover after retry succeeds.
- The failure state must NOT silently swap to a stale local schema.
- Editor must NOT degrade to a view-only mode when schema fetch fails — the blocking error panel is the only permissible state.
- The schema gate must NOT be treated as optional or bypassable under any code path.

<!-- SF: composer-app-foundation -->
### J-6 — SF-5: Composer App Foundation & Tools Hub

**Step Annotations:**
- Import distinguishes malformed YAML from stale-contract YAML: syntax failures block immediately, while non-canonical structural fields surface as clear validation issues.
- Error details use canonical paths and messages that call out root-level node persistence, separate hooks sections, or serialized `port_type`.
- No partial workflow is created when the imported YAML reflects a rejected stale serialization contract.

**Error Path UX:** The import result panel can render either a parse-error block or a stale-contract validation list with path/message rows and retry guidance.

**Empty State UX:** After a failed import, the Workflows view remains unchanged and returns focus to the Import action.

**NOT Criteria:**
- Import must NOT silently normalize stale hook serialization into saved state.
- Error messaging must NOT be generic or omit the failing path.
- A failed import must NOT create a partial workflow row.

<!-- SF: workflow-editor -->
### J-8 — SF-6: Workflow Editor & Canvas

**Step Annotations:**
- Step 1 — Import confirmation: the import dialog explicitly frames import as a full graph replacement, because a successful load rebuilds the flat store from the normalized YAML rather than merging fragments into the current canvas.
- Step 2 — Normalization before canvas mutation: syntax parsing, schema validation, and synthetic root normalization all complete before the current store mutates. Normalization steps: (a) nodes that appear at the YAML workflow root without a phase parent are assigned to a synthetic root phase entry; (b) all edges are re-routed to `workflow.edges[]` or the appropriate phase edge list based on resolved phase membership; (c) hook edges are re-derived from source handle resolution rather than any serialized `port_type` field. The existing canvas remains visible until the full normalization pipeline succeeds.
- Step 3 — Canvas hydration: successful normalization produces a fully valid flat store, re-derives HookEdge versus DataEdge display from the source handles, and opens the validation panel for any structural warnings attached to the imported elements.

**Error Path UX:** Malformed YAML, invalid dot-notation edge refs, impossible containment relationships, or synthetic root normalization failures all leave the current canvas untouched and present a targeted error message. Import failure must not leave the editor in any partial or view-only state — either the import succeeds completely or the current canvas is fully preserved and editable.

**Empty State UX:** Importing a valid workflow that contains only empty named phases renders the phase shells and their headers rather than collapsing to the generic blank-canvas empty state. The synthetic root phase shell is not rendered as a visible boundary even if the imported YAML contains an explicit root phase entry.

**NOT Criteria:**
- Import must NOT partially hydrate the canvas before synthetic root normalization and edge phase-membership routing have both completed.
- Production import must NOT fall back to a stale bundled schema file when `/api/schema/workflow` is unavailable; it must fail closed and preserve the current canvas.
- Imported hook edges must NOT be reclassified from a serialized `port_type` field because that field is never present in the canonical YAML contract.
- Import failure must NOT enter a view-only or read-only editor mode; the pre-import canvas must be fully preserved and editable.

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

<!-- SF: composer-app-foundation -->
### CMP-13: SidebarTree
<!-- SF: composer-app-foundation — Original ID: CMP-3 -->

- **Status:** new
- **Location:** `tools/compose/frontend — shell layout`
- **Description:** Explorer tree for the compose shell with 4 fixed top-level folders: Workflows, Roles, Output Schemas, and Task Templates. The shell can render before schema-aware editor bootstrapping begins.
- **Props/Variants:** `selectedFolder: workflows | roles | schemas | templates`
- **States:** loading, populated
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:154` — "sidebar shows 4 top-level folders" — The SidebarTree exists to express the revised SF-5 Explorer navigation model and fixed folder set.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:222` — "The Explorer shell, starter templates, and the user's workflows render." — The shell must be able to render before editor-specific schema bootstrap starts.

### CMP-14: NewDropdown
<!-- SF: composer-app-foundation — Original ID: CMP-10 -->

- **Status:** new
- **Location:** `tools/compose/frontend — toolbar`
- **Description:** Toolbar dropdown for SF-5 workflow creation. In the foundation scope it exposes the single `New Workflow` action and closes on selection before the workflow list refreshes.
- **Props/Variants:** `action: new_workflow`
- **States:** closed, open, submitting
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:237` — "User clicks `+ New` and creates a workflow." — The revised journey keeps SF-5's creation affordance focused on creating workflows, not broader library entities.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:156` — "default compose landing experience is the Workflows view with starter templates, the user's workflows, create/import/search actions" — Toolbar creation belongs to the Workflows view's primary action set.

### CMP-15: GridCard
<!-- SF: composer-app-foundation — Original ID: CMP-15 -->

- **Status:** new
- **Location:** `tools/compose/frontend — Workflows view`
- **Description:** Workflow card used in the Workflows view. Supports a normal state and a contract-warning state when an imported workflow carries canonical-schema warnings that require attention in the editor.
- **Props/Variants:** `variant: default | warning | selected`
- **States:** default, warning, selected
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:130` — "allows schema-invalid YAML to import with explicit validation warnings" — The Workflows grid needs a visible warning variant for imported workflows that remain usable but not fully clean.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:334` — "Import succeeds or surfaces only current-contract validation warnings." — Warning state on the card keeps those validation warnings discoverable after the import flow completes.

### CMP-16: EditorSchemaBootstrapGate
<!-- SF: composer-app-foundation — Original ID: CMP-18 -->

- **Status:** new
- **Location:** `tools/compose/frontend — editor route`
- **Description:** Route-level blocking gate between the Explorer shell and the editor host. Fetches `/api/schema/workflow`, caches success, and shows explicit loading or retryable error states. Strictly blocking — no view-only fallback is permitted. When schema is unavailable the editor must show the error panel; it must not render in any degraded or read-only editing state.
- **Props/Variants:** `routeState: loading | ready | error`
- **States:** loading, ready, error
- **Citations:**
  - [decision] `D-GR-22` — "`/api/schema/workflow` is the canonical schema delivery path for the composer." — This component exists specifically to enforce the authoritative runtime schema handshake instead of static-schema-first or view-only fallback behavior.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:134` — "canonical schema endpoint for the composer" — The gate has to request the canonical backend schema before schema-dependent editing UI appears.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:158` — "frontend must bootstrap schema-aware editor flows from `/api/schema/workflow`" — SF-5 owns the frontend infrastructure that makes this bootstrap path visible, blocking, and recoverable.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:33` — "blocks editing when the canonical runtime schema endpoint is unavailable" — SF-6 REQ-13 confirms the blocking-gate behavior; SF-5 design must enforce no view-only fallback in the gate contract.

### CMP-17: YAMLContractErrorPanel
<!-- SF: composer-app-foundation — Original ID: CMP-19 -->

- **Status:** new
- **Location:** `tools/compose/frontend — feedback surfaces`
- **Description:** Shared error and warning surface for stale-contract import failures and save/bootstrap validation messages. Renders path/message rows for issues like separate hooks sections, serialized `port_type`, root-level node persistence, or cross-phase edges inside phase definitions.
- **Props/Variants:** `tone: warning | error; rows: path/message list`
- **States:** warning, error, dismissed
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:144` — "Validation and import errors must explicitly surface stale schema assumptions" — The panel's core job is to turn stale-contract mismatches into explicit, understandable user feedback.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:325` — "separate hooks section, serialized `port_type`, or another non-canonical structure" — These are the concrete stale-contract cases the panel needs to enumerate with path-specific rows.

<!-- SF: workflow-editor -->
### CMP-18: AskFlowNode
<!-- SF: workflow-editor — Original ID: CMP-35 -->

- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/nodes/AskNode.tsx`
- **Description:** One of three atomic node types for direct canvas placement (REQ-2). React Flow wrapper composing AskNodePrimitive (CMP-102) plus Handle wiring for typed data ports and hook ports. Nodes placed without a phase parent belong to the implicit synthetic root phase. The wrapper keeps editor-only handle metadata for React Flow interactions, but save and export collapse those handles to dot-notation edge refs routed by phase membership, never serializing a separate `port_type`.
- **Props/Variants:** `nodeData forwarded to CMP-102; schema-backed port metadata hydrated from /api/schema/workflow`
- **States:** default, selected, error, warning, actor-dragging
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:22` — "The canvas must expose only three atomic node types for direct placement: Ask, Branch, and Plugin." — AskFlowNode is one of exactly three atomic placement types; no fourth type exists.
  - [decision] `D-GR-22` — Ask node handles remain editor-only; hook and data edges serialize through the canonical dot-notation edge contract routed by phase membership.

### CMP-19: BranchFlowNode
<!-- SF: workflow-editor — Original ID: CMP-36 -->

- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/nodes/BranchNode.tsx`
- **Description:** One of three atomic node types for direct canvas placement (REQ-2). React Flow wrapper composing BranchNodePrimitive (CMP-103). Dynamic output Handles remain one-per-output-port from `data.outputs[]`, each carrying D-GR-12 per-port condition data for non-exclusive fan-out. No SwitchFunctionEditor exists; all condition authoring is in OutputPathsEditor (CMP-23) and PortConditionRow (CMP-25). Handles are editor-only; save/export emits ordinary dot-notation edge refs routed by phase membership.
- **Props/Variants:** `nodeData forwarded to CMP-103; includes outputs[] with per-port conditions, inputs[], and optional merge_function`
- **States:** default, selected, error, gathering
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:22` — "The canvas must expose only three atomic node types for direct placement: Ask, Branch, and Plugin." — BranchFlowNode is one of exactly three atomic placement types.
  - [decision] `D-GR-12` — "BranchNode = gather + non-exclusive fan-out. Per-port conditions on output ports. No switch_function." — BranchFlowNode renders per-port conditions; SwitchFunctionEditor is explicitly excluded.
  - [decision] `D-GR-22` — Branch handles participate in the same nested phase and dot-notation edge contract as every other editor edge.

### CMP-20: PluginFlowNode
<!-- SF: workflow-editor — Original ID: CMP-37 -->

- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/nodes/PluginNode.tsx`
- **Description:** One of three atomic node types for direct canvas placement (REQ-2). React Flow wrapper composing PluginNodePrimitive (CMP-104). PluginFlowNode accepts both data edges and hook edges, but connection type is inferred at the edge layer from the source handle. Plugin node data stays in the SF-5 compose foundation schema; the component must not depend on `/api/plugins` endpoints from SF-7.
- **Props/Variants:** `nodeData forwarded to CMP-104`
- **States:** default-configured, default-unconfigured, selected, error
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:22` — "The canvas must expose only three atomic node types for direct placement: Ask, Branch, and Plugin." — PluginFlowNode is the third of exactly three atomic placement types.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:34` — "Core editor flows must not depend on tools/iriai-workflows, SQLite, /api/plugins, or workflow_entity_refs endpoints." — PluginFlowNode must not call SF-7 plugin registry endpoints; it works from SF-5 foundation data only.
  - [decision] `D-GR-22` — Plugin nodes consume hook wiring through ordinary edge serialization with no plugin-side hooks section.

### CMP-21: DataEdge
<!-- SF: workflow-editor — Original ID: CMP-42 -->

- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/edges/DataEdge.tsx`
- **Description:** React Flow custom edge. Positions EdgeTypeLabel (CMP-106, SF-7) at midpoint and surfaces transform or mismatch states. The persisted contract is `source`, `target`, and optional `transform_fn` only; no `port_type` or other editor-side discriminator is serialized. Intra-phase edges serialize to the containing phase's edge list; cross-phase edges serialize to `workflow.edges[]`.
- **Props/Variants:** `sourceType, targetType, hasTransform`
- **States:** default, with-transform, mismatch-warning, selected
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:26` — "Edge serialization must use only source, target, and optional transform_fn; no serialized port_type field may appear in YAML." — DataEdge serialization is strictly dot-notation with no extra discriminator fields.
  - [decision] `D-GR-22` — The revised contract removes serialized `port_type` and keeps edge persistence on the dot-notation schema.

### CMP-22: HookEdge
<!-- SF: workflow-editor — Original ID: CMP-43 -->

- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/edges/HookEdge.tsx`
- **Description:** React Flow custom edge for lifecycle wiring. HookEdge is a visual classification only: the editor renders it when the source handle resolves to a hook port (`on_start` or `on_end`). Save and export persist it as a normal edge with dot-notation `source`/`target` and `transform_fn: null`, with no serialized `port_type`. Intra-phase hook edges serialize to the containing phase's edge list; cross-phase hook edges serialize to `workflow.edges[]`—the same routing rule as DataEdge.
- **Props/Variants:** `sourceHandle, readOnlyInspector`
- **States:** default
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:23` — "Hook behavior serialized through normal edges rather than a separate hooks section." — HookEdge is a visual-only classification; the serialized payload is a normal edge.
  - [decision] `D-GR-22` — Cycle 4 made edge-based hook serialization with no `port_type` authoritative across SF-1, SF-2, SF-5, and SF-6.

### CMP-23: OutputPathsEditor
<!-- SF: workflow-editor — Original ID: CMP-64 -->

- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/inspectors/OutputPathsEditor.tsx`
- **Description:** Editable list of output ports for BranchNode. Each row is a PortConditionRow (CMP-25). This is the primary and only surface for branch condition authoring; SwitchFunctionEditor does not exist. Field metadata hydrates from the live `/api/schema/workflow` payload, never from a bundled schema file.
- **Props/Variants:** `outputs: PortDefinition[], onChange, upstreamType?, readOnly`
- **States:** default, single-path, empty-condition-warning
- **Citations:**
  - [decision] `D-GR-12` — "Per-port conditions on output ports." — OutputPathsEditor owns per-port condition editing for BranchNode outputs; SwitchFunctionEditor is excluded.
  - [decision] `D-GR-22` — Inspector field metadata comes from the canonical schema endpoint instead of static-schema-first bootstrapping.

### CMP-24: MergeFunctionEditor
<!-- SF: workflow-editor — Original ID: CMP-65 -->

- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/inspectors/MergeFunctionEditor.tsx`
- **Description:** Optional merge_function editor for BranchNode multi-input gather. Collapsed by default; enabled only when 2+ input ports are present. Independent from phase or edge serialization. Reads from the same canonical schema bootstrap as the rest of the inspector surface.
- **Props/Variants:** `mergeFunction: string | undefined, onChange, inputPorts: PortDefinition[], readOnly`
- **States:** collapsed, expanded, disabled
- **Citations:**
  - [decision] `D-GR-12` — "Optional merge_function (Python expression) to combine gathered inputs." — MergeFunctionEditor remains required for multi-input gather.
  - [decision] `D-GR-22` — Inspector affordances must hydrate from the canonical schema endpoint.

### CMP-25: PortConditionRow
<!-- SF: workflow-editor — Original ID: CMP-66 -->

- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/inspectors/PortConditionRow.tsx`
- **Description:** Single row within OutputPathsEditor for one Branch output port's condition configuration. Atomic editing row for branch-routing. Assumes live schema payload is available before rendering.
- **Props/Variants:** `port: PortDefinition, onChange, onRemove, upstreamType?, readOnly`
- **States:** expression-mode, output-field-mode, empty-condition
- **Citations:**
  - [decision] `D-GR-12` — "Each output port carries its own condition configuration." — PortConditionRow is the atomic editing row for Branch fan-out behavior.
  - [decision] `D-GR-22` — These rows should be rendered only after the canonical schema contract is available from the composer backend.

### CMP-26: PhaseContainer
<!-- SF: workflow-editor — Original ID: CMP-68 -->

- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/phases/PhaseContainer.tsx`
- **Description:** React Flow group node for named phases (sequential, map, fold, loop). Only explicitly named phases render as visible PhaseContainer boundaries; the implicit synthetic root phase is NEVER rendered as a PhaseContainer. Expanded state uses `parentId` containment in the flat store; collapsed state switches to CollapsedGroupCard. The save/export normalizer converts the flat store to nested YAML: `phase.nodes[]` for leaf contents, `phase.children[]` for nested phases. Edges crossing this phase boundary serialize to `workflow.edges[]`; edges fully inside this phase serialize to its edge list.
- **Props/Variants:** `mode: sequential | map | fold | loop; expanded | collapsed; hasNestedChildren; validationState`
- **States:** expanded, collapsed, loop-exits-visible, error
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:47` — "The saved YAML normalizes loose nodes under a synthetic root phase and preserves nested children[], nodes[], and hook edges as normal edges." — PhaseContainer is the visual container for named phases only; the synthetic root is the implicit container for unparented nodes and is never rendered as a PhaseContainer.
  - [decision] `D-GR-22` — Cycle 4 makes nested YAML containment authoritative while allowing a flat React Flow store internally.

### CMP-27: SchemaBootstrapGate
<!-- SF: workflow-editor — Original ID: CMP-69 -->

- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/schema/SchemaBootstrapGate.tsx`
- **Description:** Route-level loading and error shell that fetches `/api/schema/workflow` before enabling the workflow editor. Strictly blocking: the canvas, palette, inspectors, save controls, and import affordances are all disabled until the live schema payload is available. No view-only fallback, no partial initialization from a bundled schema file. Static schema fixtures exist for tests only and are never referenced by this component in production.
- **Props/Variants:** `status: loading | ready | error; schemaVersionLabel; onRetry`
- **States:** loading, ready, error
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:40` — "The editor waits for both the workflow payload and GET /api/schema/workflow to succeed before rendering the working canvas." — SchemaBootstrapGate enforces AC-1: strictly blocking with no fallback.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:50` — "A blocking error state appears and editing is deferred until the canonical schema endpoint recovers. [NOT: The app silently falls back to a local workflow-schema.json copy.]" — AC-10 mandates a blocking error state with no silent fallback to a bundled schema.
  - [decision] `D-GR-22` — Cycle 4 explicitly makes `/api/schema/workflow` the canonical composer schema source and relegates static schema files to build/test usage.

<!-- SF: libraries-registries -->
### CMP-28: EntityDeleteDialog
<!-- SF: libraries-registries — Original ID: CMP-133 -->

- **Status:** new
- **Location:** `features/libraries/shared/EntityDeleteDialog.tsx`
- **Description:** Shared alert dialog for delete preflight and confirmation across roles, schemas, templates, and tools. Starts in reference-check state, resolves to blocked-by-workflows, blocked-by-roles, confirm-delete, or retryable error. Roles/schemas/templates show saved workflow names from workflow_entity_refs (populated only by SF-6 bootstrap-gated saves); tools show saved role names. Copy always says 'saved workflows' to reflect that pre-bootstrap/unsaved editor state never counts.
- **Props/Variants:** `entityType ('role' | 'schema' | 'template' | 'tool'), entityName, isOpen, checkState ('loading' | 'blocked-workflows' | 'blocked-roles' | 'ready' | 'error'), referenceNames[], onRetry, onClose, onConfirmDelete`
- **States:** checking-references, blocked-by-workflows, blocked-by-roles, confirm-delete, reference-check-error
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:19` — "Roles, Schemas, and Task Templates must use a pre-delete reference check backed by the canonical workflow_entity_refs junction table." — The dialog contract is defined by the PRD's delete preflight requirement.
  - [decision] `D-GR-26` — "workflow_entity_refs is the canonical persisted reference model; only SF-6 bootstrap-gated saves write to it." — The blocked-by-workflows variant must reflect only post-bootstrap saved state, not pre-bootstrap or unsaved editor state.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:REQ-7` — "Editor bootstrap waits for /api/schema/workflow before enabling save/import/validate." — SF-6's bootstrap gate is what controls when workflow_entity_refs rows are written; this bounds what the dialog can show.

### CMP-29: LibraryCollectionPage
<!-- SF: libraries-registries — Original ID: CMP-134 -->

- **Status:** new
- **Location:** `features/libraries/shared/LibraryCollectionPage.tsx`
- **Description:** Reusable list shell for Roles, Output Schemas, Task Templates, and Tools. Combines search, primary create action, XP card/list layout, stale-while-revalidate loading treatment, and inline empty/error states. Loads independently of SF-6 bootstrap state.
- **Props/Variants:** `entityType ('roles' | 'schemas' | 'templates' | 'tools'), viewMode ('grid' | 'list'), items[], queryState ('loading' | 'empty' | 'error' | 'ready'), selectedId?, onCreate, onSelect, onSearch`
- **States:** loading, empty, error, populated
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:21` — "The Tool Library remains a full CRUD library page with list, detail, and editor views." — PRD defines the full library CRUD surface that this component shells.
  - [research] `TanStack Query important defaults` — "Stale queries refetch in the background." — Stale-while-revalidate pattern keeps list visible during refresh.

### CMP-30: RoleEditorForm
<!-- SF: libraries-registries — Original ID: CMP-135 -->

- **Status:** new
- **Location:** `features/libraries/roles/RoleEditorForm.tsx`
- **Description:** Form-based role editor for name, model, prompt, metadata, and grouped tool checklist. Uses inline validation for bad names and oversized payloads, shows built-in tools in a locked section, and refreshes downstream role-picker caches after save.
- **Props/Variants:** `mode ('create' | 'edit'), roleDraft, builtInTools[], customTools[], validationState ('idle' | 'invalid' | 'saving' | 'saved'), onSave, onDelete, onCancel`
- **States:** draft, invalid, saving, saved
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:48` — "The Role editor shows built-in plus registered tools in separate groups." — PRD requires grouped tool display to distinguish permanent catalog items from editable custom tools.

### CMP-31: ToolEditorForm
<!-- SF: libraries-registries — Original ID: CMP-136 -->

- **Status:** new
- **Location:** `features/libraries/tools/ToolEditorForm.tsx`
- **Description:** Tool CRUD editor for custom tools only. Captures name, source, description, and input schema. Delete entry points route through EntityDeleteDialog so tools can be blocked by role references before deletion.
- **Props/Variants:** `mode ('create' | 'edit'), toolDraft, validationState ('idle' | 'invalid' | 'saving' | 'saved'), onSave, onDelete, onCancel`
- **States:** draft, invalid, saving, saved
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:34` — "Tool delete is blocked with referencing role names until references are removed." — Tool delete must route through EntityDeleteDialog's blocked-by-roles state before mutation.

### CMP-32: ActorSlotsEditor
<!-- SF: libraries-registries — Original ID: CMP-137 -->

- **Status:** new
- **Location:** `features/libraries/templates/ActorSlotsEditor.tsx`
- **Description:** Structured repeater for task-template actor_slots. Each row edits slot name, actor type constraint, and optional default role, with row-level validation and a compact summary for persisted reuse.
- **Props/Variants:** `slots[], availableRoles[], editorState ('empty' | 'editing' | 'invalid' | 'saved'), onAddSlot, onUpdateSlot, onRemoveSlot`
- **States:** empty, populated, invalid, saved
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:22` — "custom_task_templates must persist actor_slots through migration and API support." — PRD requirement for round-trip actor_slots persistence drives this component's save contract.
  - [decision] `D-GR-29` — "The actor-slot editor belongs in SF-7's extension layer, not SF-5's five-table foundation." — Scope boundary decision places this component in SF-7.

### CMP-33: ResourceStateCard
<!-- SF: libraries-registries — Original ID: CMP-138 -->

- **Status:** new
- **Location:** `features/libraries/shared/ResourceStateCard.tsx`
- **Description:** Route-level fallback surface for loading, not found, server error, and request-validation states. Used by library detail panes and list routes so auth scoping and validation failures are explicit without exposing cross-user existence information.
- **Props/Variants:** `tone ('neutral' | 'error' | 'warning'), state ('loading' | 'not-found' | 'error' | 'validation'), title, body, actionLabel?, onAction?`
- **States:** loading, not-found, error, validation
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:24` — "Cross-user access attempts must return 404 rather than 403." — PRD's auth scoping requirement drives the not-found vs. forbidden presentation decision.

---

## Verifiable States

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

<!-- SF: composer-app-foundation -->
### CMP-13 (SidebarTree) States

| State | Visual Description |
|-------|-------------------|
| loading | Sidebar shows 4 row skeletons aligned like tree items, with no extra folders and no schema status blocker in the shell chrome. |
| populated | Sidebar shows exactly 4 top-level folders: Workflows, Roles, Output Schemas, Task Templates. Workflows is selected by default. |

### CMP-14 (NewDropdown) States

| State | Visual Description |
|-------|-------------------|
| open | Toolbar dropdown is anchored under `+ New` and contains a single `New Workflow` action row. |

### CMP-15 (GridCard) States

| State | Visual Description |
|-------|-------------------|
| warning | Workflow card shows an amber warning badge or icon indicating schema or import warnings while remaining selectable and openable. |

### CMP-16 (EditorSchemaBootstrapGate) States

| State | Visual Description |
|-------|-------------------|
| loading | Content area shows a full-panel card titled `Loading workflow schema` with spinner and disabled editor scaffold placeholders. |
| error | Content area shows a red bordered panel titled `Can't load workflow schema` with Retry (primary) and Back to Workflows (secondary) actions. No view-only editor surface is visible. |
| ready | Bootstrap gate disappears and hands off the full content area to the editor host with no warning chrome remaining. |

### CMP-17 (YAMLContractErrorPanel) States

| State | Visual Description |
|-------|-------------------|
| warning | Amber panel with summary text such as `Imported with warnings` and an expandable list of path/message rows. |
| error | Red panel with path-specific validation rows calling out non-canonical fields like `edges[2].port_type`, `hooks`, or phase-level cross-phase edges. |

<!-- SF: workflow-editor -->
### CMP-18 (AskFlowNode) States

| State | Visual Description |
|-------|-------------------|
| default | White card (~260–280px wide) with purple header, actor slot, prompt summary text, and visible data handles plus dashed hook handles (on_start, on_end). No hook inspector section exists — hook behavior is only on-canvas via edges. |
| selected | 2px purple glow ring surrounds the node card. Handle positions do not shift on selection. |
| error | Red glow border plus top-right red error badge. Actor slot turns red when the required actor is missing. |

### CMP-19 (BranchFlowNode) States

| State | Visual Description |
|-------|-------------------|
| default | White rectangular card with amber header. One visible output row per configured port with condition badges on each output row. Output handles align 1:1 with configured ports. No switch function UI is present; condition editing happens in the inspector via OutputPathsEditor. |
| selected | 2px amber glow ring surrounds the node while per-port badges and output handles remain stationary. |
| error | Red glow border and top-right red error badge. Any output row missing a condition shows a red outline on that specific row. |
| gathering | Two or more input handles appear on the left edge with a subdued gather indicator. A `merge(...)` pill appears only when merge_function is configured. |

### CMP-20 (PluginFlowNode) States

| State | Visual Description |
|-------|-------------------|
| default-configured | Plugin card with green configured badge above the field summary. Accepts both solid DataEdge connections and dashed HookEdge connections from upstream sources. |
| default-unconfigured | Plugin card with muted placeholder values and a warning-toned unconfigured badge. |

### CMP-21 (DataEdge) States

| State | Visual Description |
|-------|-------------------|
| default | Solid curved line with midpoint type label. No extra edge-type pill, no serialized connection marker. |
| with-transform | Solid curve with midpoint type label and a transform badge indicating inline Python is configured. |
| mismatch-warning | Red dashed stroke with a warning chip at the midpoint. Edge remains selectable. |

### CMP-22 (HookEdge) States

| State | Visual Description |
|-------|-------------------|
| default | Muted dashed purple line with no type label and no transform badge. Visually distinguishable from all DataEdge strokes by both dashed style and purple color. |

### CMP-23 (OutputPathsEditor) States

| State | Visual Description |
|-------|-------------------|
| default | Two or more output port rows visible. Each row has port name, condition type control, condition editor, and remove button. Inspector header above the list is interactive because schema bootstrap has completed. No SwitchFunctionEditor panel is present. |
| single-path | Single output row locked in place with the Add Path button still available below. |
| empty-condition-warning | A row with an empty condition field is highlighted amber with helper copy explaining the port will always fire. |

### CMP-24 (MergeFunctionEditor) States

| State | Visual Description |
|-------|-------------------|
| collapsed | Muted inline 'Add merge function' action with no editor surface expanded. |
| expanded | Inline code editor visible with merge helper copy and a side hint listing available input-port variable names. |
| disabled | Gray helper text reads 'Merge function requires 2+ input ports'; no interactive editor is shown. |

### CMP-25 (PortConditionRow) States

| State | Visual Description |
|-------|-------------------|
| expression-mode | Row shows 'Expression' in the condition type dropdown and an inline code editor with Python syntax highlighting. |
| output-field-mode | Row shows 'Output Field' in the dropdown and a simple text input for a field path such as `verdict.approved`. |
| empty-condition | Condition area outlined amber with helper copy explaining the port has no condition yet. |

### CMP-26 (PhaseContainer) States

| State | Visual Description |
|-------|-------------------|
| expanded | Tinted phase boundary with mode-specific border styling, visible child nodes inside, and boundary ports visible. Nested named phases render inside the parent boundary, not as duplicate top-level boxes. The synthetic root phase is never visible as a PhaseContainer boundary. |
| collapsed | Compact CollapsedGroupCard showing phase title, mode badge, and node count. No miniature internal topology. |
| loop-exits-visible | Loop-mode phase shows two labeled right-side output handles — `condition_met` and `max_exceeded` — on the phase boundary itself. |
| error | Phase boundary gets a red outline and inline issue badge when containment or phase-level validation errors prevent serialization. |

### CMP-27 (SchemaBootstrapGate) States

| State | Visual Description |
|-------|-------------------|
| loading | Full-editor blocking overlay with spinner and 'Loading workflow schema' copy. All canvas, palette, inspector, and save controls are disabled and visually inaccessible behind the overlay. No partial canvas is visible. |
| ready | Overlay removed; normal canvas, palette, and toolbar are interactive. Optional schema version label in editor chrome. |
| error | Blocking error card with failure summary, Retry button receiving initial focus, and no partially initialized palette or inspector elements visible behind it. No view-only canvas is shown. |

<!-- SF: libraries-registries -->
### CMP-28 (EntityDeleteDialog) States

| State | Visual Description |
|-------|-------------------|
| checking-references | XP modal titled 'Delete Code Review Lead?' with a spinner row labeled 'Checking references...'; destructive button is hidden and body height is reserved for later content. |
| blocked-by-workflows | Warning modal titled 'Can't delete role yet' with body copy including 'saved workflows' (not 'open workflows') and a stacked list of saved workflow names such as 'build-v2 / planning' and 'deploy-preview'. Footer shows only Close. |
| blocked-by-roles | Warning modal titled 'Can't delete tool yet' with a stacked list of role names such as 'code-review-lead' and 'qa-runner'; no workflow names visible. Footer shows only Close. |
| confirm-delete | Neutral modal with recovery copy such as 'This tool will be recoverable for 30 days.' No reference list rendered. Footer shows Cancel and a red Delete button. |
| reference-check-error | Modal body contains a red inline banner titled 'Couldn't verify references', a short explanation, and Retry plus Close actions; Delete is unavailable. |

### CMP-29 (LibraryCollectionPage) States

| State | Visual Description |
|-------|-------------------|
| loading | Content area shows a toolbar and a grid of XP-style skeleton cards; previous route chrome remains visible and no blocking overlay covers the shell. |
| empty | Centered empty card with heading like 'No custom tools yet', one primary CTA, and one short helper sentence. |
| error | Inline content card with a red icon, 'Couldn't load roles', and a Retry button; sidebar and toolbar stay visible. |
| populated | Search bar, create button, and at least one entity card row or grid render together, with selection highlighting on the active item. |

### CMP-30 (RoleEditorForm) States

| State | Visual Description |
|-------|-------------------|
| draft | Role form shows editable name/model/prompt fields and two tool groups titled 'Built-in tools' and 'Custom tools'; save bar is idle. |
| invalid | One or more field rows show red helper text, and a summary banner states 'Fix 2 issues before saving'; Save is disabled. |
| saving | Sticky footer save button shows spinner text such as 'Saving role...'; inputs are temporarily disabled. |
| saved | Success toast appears with 'Role saved', and the dirty-state badge disappears from the editor header. |

### CMP-31 (ToolEditorForm) States

| State | Visual Description |
|-------|-------------------|
| draft | Tool form shows Name, Source, Description, and Input schema sections with a destructive Delete tool action in the footer. |
| invalid | Input-schema or name validation shown inline with red helper text and the footer summary banner explains what failed. |
| saving | Footer save button shows spinner text such as 'Saving tool...'; the form remains visible in place. |
| saved | Success toast appears with 'Tool updated', and reopening a role editor later shows the updated tool description. |

### CMP-32 (ActorSlotsEditor) States

| State | Visual Description |
|-------|-------------------|
| empty | Dashed panel titled 'No actor slots defined yet' with an Add actor slot button and helper text about reusable role bindings. |
| populated | Table-like list of actor-slot rows showing slot name, actor type chip, default role dropdown value, and row delete control. |
| invalid | One or more rows show inline errors such as 'Slot names must be unique', and the header summary banner repeats the issue count. |
| saved | Compact success note beneath the section title reads 'Actor slots saved to template', and the rows remain visible after save completes. |

### CMP-33 (ResourceStateCard) States

| State | Visual Description |
|-------|-------------------|
| loading | XP card centered in the detail pane with spinner and message like 'Loading tool details...'. |
| not-found | Neutral card with heading 'Role not found', explanation that it may have been deleted, and a Back to Roles action. |
| error | Error card with heading 'Request failed', short retry guidance, and a primary Retry action. |
| validation | Warning card with heading 'Request rejected' and a short list of server-returned 413/422 issues. |

---

## Responsive Behavior

<!-- SF: declarative-schema -->
### SF-1: Declarative Schema & Primitives
N/A for the schema package itself. The D-GR-22 impact on responsive editor behavior is indirect: the three-type node constraint (ask, branch, plugin) and the cross-phase edge ownership contract reduce the editor's state space, making responsive canvas behavior in SF-6 easier to implement without tracking SwitchFunctionEditor or ErrorFlowNode variants. The blocking bootstrap gate also simplifies responsive layout because there is only one non-ready state (loading or error-with-retry) rather than a degraded view-only mode that would require its own responsive breakpoints.

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner
N/A — backend-only Python subfeature with no rendered interface. The only composer-facing surface added by this revision is the schema contract consumed via `/api/schema/workflow`, not a responsive UI.

<!-- SF: testing-framework -->
### SF-3: Testing Framework
Not applicable. SF-3 remains a backend Python testing module with no visual UI.

<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test
No responsive changes. This revision is backend-contract driven; the existing desktop-only mockup remains unchanged aside from the semantic interpretation of each node's `reads` line.

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub
Desktop-first. The Explorer shell and schema bootstrap gate are optimized for full desktop widths; below the supported desktop breakpoint the app should show a blocking informational screen rather than attempt a reduced schema-aware editor experience. Within supported desktop widths, bootstrap and error panels collapse to a single-column card inside the content area and never displace the sidebar.

<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas
The editor remains desktop-first and maintains the existing sub-768px block. At 768px and above, the same flat canvas model applies at every breakpoint. SchemaBootstrapGate owns the full editor viewport until schema load succeeds, so at no breakpoint does the palette or any inspector hydrate before the canonical schema response arrives. Nested PhaseContainer headers and CollapsedGroupCard labels compress text rather than changing the underlying containment model. The synthetic root phase has no visual boundary at any breakpoint.

<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries
Desktop-first and consistent with the compose shell at tools/compose. Under 1024px, SF-7 does not invent a separate mobile CRUD flow and instead relies on the existing compose unsupported-screen treatment. From 1024px to 1359px, list pages stay single-column within the content pane and detail editors stack metadata sections vertically. At 1360px and above, list/detail views can split into a wider two-column workspace, while delete dialogs grow from roughly 520px to 600px wide without changing their content model.

---

## Interaction Patterns

<!-- SF: declarative-schema -->
### SF-1: Declarative Schema & Primitives

Authoring and consumption follow one contract everywhere. Authors serialize executable structure into WorkflowConfig.phases with AskNode, BranchNode, or PluginNode under each phase's nodes. Cross-phase connections (including cross-phase hook edges) go into WorkflowConfig.edges; intra-phase connections go into PhaseDefinition.edges. Data and hook wiring both use the same EdgeDefinition surface, with hook inference from source-port resolution and transform_fn forbidden on hook-sourced edges.

Bootstrap is a blocking gate: the editor awaits /api/schema/workflow before rendering; no view-only or degraded fallback is permitted. On failure, the editor shows a full-screen error state with a Retry button. On empty-phases bootstrap, the loader normalizes a synthetic root phase (__root__, mode: sequential) before handing the model to the editor.

JSON Schema generation is model-driven via WorkflowConfig.model_json_schema(). The generated schema must expose the three-type NodeDefinition discriminant (ask | branch | plugin), the PhaseDefinition.edges (intra-phase) vs. WorkflowConfig.edges (cross-phase) ownership split, and PhaseDefinition.children as the recursive field. Static workflow-schema.json is explicitly limited to build/test support.

Validation enforces contract drift aggressively: PhaseDefinition.phases aliases, top-level hooks sections, serialized edge.port_type, SwitchFunctionEditor, ErrorFlowNode, and cross-phase edges in phase.edges all produce explicit named rejection messages [decision: D-GR-22, three atomic node types contract, cross-phase edge ownership contract, blocking bootstrap gate, synthetic root normalization, REQ-3, REQ-4, REQ-5, REQ-16, REQ-17].

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner

Backend-only subfeature. `load_workflow()` parses YAML and recursively descends `phases[].nodes` plus `phases[].children`, building one execution graph per container rather than normalizing to a persisted flat graph. `validate()` shares that same parser and returns field-scoped structural errors without requiring runtimes, which makes it safe for SF-5 validation endpoints and SF-6 authoring flows. `run()` classifies edges by resolving the source port container: hook edges are ordinary edges whose source lives in a hook port set, remain fire-and-forget, and may not carry transforms; data edges may carry transforms. Composer integrations fetch schema from `/api/schema/workflow`; a static `workflow-schema.json` can still exist for build/test snapshots, but it is never the authoritative runtime interface. [decision: D-GR-22] [code: iriai-compose/iriai_compose/runner.py:62] [code: iriai-compose/iriai_compose/runner.py:162] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:353]

<!-- SF: testing-framework -->
### SF-3: Testing Framework

Fluent mock configuration remains unchanged for test authors. The runtime contract underneath it is now: `run(workflow, config, *, inputs=None)` stays canonical, `AgentRuntime.invoke()` stays non-breaking, current-node identity is read from a runner-owned ContextVar, and all dynamic prompt context exposed to callbacks is merged in `workflow -> phase -> actor -> node` order.

<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test

Tier 2 and Tier 3 migration verification now follow one runtime-interaction model: the runner owns current-node propagation via ContextVar, mocks/consumers observe that implicitly, and effective context ordering is always `workflow -> phase -> actor -> node`. The artifact explicitly rejects any alternate `invoke(..., node_id=...)` pattern.

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub

Schema bootstrap — lazy, route-scoped, strictly blocking. The Workflows shell loads without calling `/api/schema/workflow`. Navigating to `/workflows/{id}/edit` requests the workflow record and canonical schema in parallel and mounts schema-dependent UI only after both resolve. There is no view-only fallback: if schema fetch fails, the EditorSchemaBootstrapGate shows the blocking error panel and the editor canvas does not render in any state. Retry triggers a new fetch; success removes the gate and mounts the editor. Back to Workflows exits the route without altering the workflow record.

Synthetic root phase normalization. Before the editor canvas mounts, the load path guarantees every workflow has at least one phase. If the persisted workflow payload has no phase structure, it is wrapped in a synthetic root phase. This normalization runs in the data preparation layer (API response transform or load hook), not inside the editor canvas itself. The canvas always receives a canonically phased workflow and never renders a phaseless flat node graph. Normalization does not alter the persisted YAML unless the user explicitly saves after opening.

Three atomic node types. The editor canvas surfaces exactly three atomic node types for direct placement: Ask, Branch, and Plugin. SwitchFunctionEditor and ErrorFlowNode do not exist in the palette, inspector, or serialization format. Branching behavior is expressed through Branch nodes and their condition ports. Error routing is expressed through error ports present on all three atomic types. Save and export reject any node type outside these three from the persisted phase structure.

Cross-phase edge storage. Edges that connect nodes belonging to different phases are stored in the workflow-root `edges` array, not inside any phase definition. On load, the editor reconstructs phase membership from node containment metadata. On save, any edge whose endpoints belong to different phases is lifted to the workflow-root array before serialization. Phase-level edge arrays, if present in any loaded payload, are treated as stale-contract violations and surfaced through the YAMLContractErrorPanel.

Save / export / import contract. All four operations speak the same persisted shape: nested `phases[].nodes` and `phases[].children`, only Ask/Branch/Plugin node types inside phases, cross-phase edges at workflow root, and hook connections in the edges array with no serialized `port_type`. Any internal editor-only `port_type` concept is reconstructed from port resolution and stripped before persistence. Import distinguishes parse errors from stale-contract validation failures; both keep the user in recoverable shell states and never partially write invalid workflows.

<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas

Schema bootstrap (blocking gate): the route enters through SchemaBootstrapGate (CMP-27), fetches `/api/schema/workflow`, and only then mounts palette metadata (Ask, Branch, Plugin only), inspectors, import validators, and save/export affordances. If the endpoint is unavailable the gate shows a blocking error card with Retry and no partial initialization. Test harnesses may inject a static fixture; the production path never treats `workflow-schema.json` as canonical.

Synthetic root phase normalization: the canvas always maintains an implicit synthetic root phase for unparented nodes. The normalizer runs on every save, auto-save, export, and import: (a) collects all nodes whose `parentId` is null or undefined into the synthetic root phase entry; (b) emits the synthetic root as the first entry in `workflow.phases[]` if it contains any nodes, omits it entirely if empty; (c) on import, assigns nodes found at the YAML workflow root without a phase parent to the synthetic root before canvas hydration. The synthetic root phase is never rendered as a visible PhaseContainer boundary on the canvas.

Cross-phase edge routing: every edge in the flat React Flow store is classified by comparing the phase membership of its source and target nodes. Edges where source and target share the same nearest named phase ancestor serialize to that phase's edge list. Edges where source and target belong to different phases—or where one endpoint is in the synthetic root and the other is in a named phase—serialize to `workflow.edges[]`. This rule applies to both DataEdge and HookEdge. The serializer runs this routing on every save, export, and auto-save.

Nested phase round-trip: SF-6 keeps the performant flat React Flow store with `parentId`, but every save, export, auto-save, and import normalizes through nested YAML. Top-level named phases serialize under `workflow.phases[]`, each owns `nodes[]` and `children[]`, and the synthetic root occupies the first `workflow.phases[]` slot when it contains nodes.

Edge-based hooks: hook wiring is created only through `on_start` and `on_end` handles and is visually classified by resolving the source handle against the hooks container. Export writes the same edge shape as data edges, minus any transform, and places the edge in `workflow.edges[]` or the containing phase edge list based on the cross-phase routing rule. Import re-derives HookEdge styling from the source handle; no serialized `port_type` is ever read.

Branch editing: OutputPathsEditor (CMP-23), PortConditionRow (CMP-25), and MergeFunctionEditor (CMP-24) retain D-GR-12's per-port condition model. SwitchFunctionEditor does not exist and must not be created. Field metadata hydrates from the live schema payload. Handle changes participate in the same nested-phase and dot-notation save pipeline as the rest of the canvas.

<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries

List data uses stale-while-revalidate behavior: cached entity lists render immediately, background refetch updates them silently, and create/update/delete mutations invalidate the relevant list plus dependent pickers or checklists. Delete is always two-step: opening the dialog triggers a reference preflight; only an unreferenced result reveals the destructive action. Confirm delete expects a server recheck to protect against races. Tool checklist updates are not optimistic; the UI waits for a successful tool mutation, then invalidates the role-editor query. Actor-slot editing is local until save, but the save response is the source of truth. Cross-user access is presented as not-found. Reference population alignment with SF-6 bootstrap: workflow_entity_refs is populated exclusively by SF-6's save path, which only runs after the blocking schema bootstrap from /api/schema/workflow completes. Library list pages and detail editors load independently and do not gate on SF-6's bootstrap state. The blocked-by-workflows dialog copy says 'saved workflows' so users know they must open and save the referencing workflow in SF-6 to clear the block. Cross-phase edges are part of SF-6's workflow-root save payload and do generate reference rows; they are not a separate path the reference scan can miss.

---

## Accessibility Notes

<!-- SF: declarative-schema -->
### SF-1: Declarative Schema & Primitives
No direct end-user UI is introduced in SF-1. The three-type node constraint (ask, branch, plugin) reduces the set of node-type-specific inspector panels SF-6 must implement, simplifying keyboard navigation and screen reader announcement patterns downstream. The blocking bootstrap gate prevents partial renders that could present an incomplete or inconsistent DOM to assistive technology; the editor is either fully ready or fully blocked with a clear retry affordance. The explicit cross-phase edge ownership contract ensures that edge data has one canonical location, which avoids state inconsistencies that could produce confusing focus traps or aria-live announcements in the editor.

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner
N/A for a rendered surface. SF-2 should emit stable `field_path` and `message` validation records so SF-5/SF-6 can expose errors accessibly; this revision adds no direct keyboard, focus, or screen-reader surface inside SF-2 itself.

<!-- SF: testing-framework -->
### SF-3: Testing Framework
No end-user UI exists. Equivalent DX requirements remain: assertion failures must clearly report expected vs actual values, runtime-context-derived node identity, and enough execution context to debug matcher selection without inspecting internal runner state.

<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test
Accessible reading order for node cards still includes the `reads` metadata, but that metadata is now defined as the resolved effective context order so screen-reader output matches runtime behavior.

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub
The schema bootstrap gate uses `aria-busy` during loading and moves focus to the panel heading on failure so keyboard users encounter the retry action immediately. Error and warning panels expose path/message details in keyboard-operable expandable regions and announce blocking failures through an assertive live region. SidebarTree uses roving focus with arrow-key navigation, and `+ New` supports Enter or Space to open plus Escape to close. Toasts for import and save outcomes are announced through live regions, while blocking schema and import errors remain persistent in the content area until dismissed or resolved.

<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas
SchemaBootstrapGate uses `role="status"` during loading and `role="alert"` on failure so screen readers understand why the editor is blocked. Retry receives initial focus when the error card appears. No view-only or partial canvas is presented during error states, so there is no risk of an inaccessible read-only surface.
Hook edges rely on both color (purple) and dashed stroke for distinction; the hook edge inspector title explicitly includes "Hook" so the semantic difference is not color-only.
PhaseContainer exposes an accessible name including phase title, mode, and nesting depth, keeping nested `children[]` relationships understandable for assistive technology. The synthetic root phase has no accessible boundary element since it is not rendered.
Import and save failures announce the exact blocking issue in a live region before focus moves to the offending canvas element or inspector control.
Disabled destructive actions, such as removing the last required branch path, stay in the tab order only when they provide explanatory help text; otherwise focus moves to the next valid editing control.

<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries
EntityDeleteDialog uses role='alertdialog', moves focus to the title or first action on open, traps focus within the modal while open, and returns focus to the invoking delete button on close. Reference results are semantic lists, not comma-separated text, so screen readers can count blocked workflows or roles. Success toasts remain aria-live='polite' and error/warning toasts use alert semantics, matching the existing global toast pattern. Inline validation pairs field inputs with visible helper text and error summary banners, so duplicate actor-slot names or invalid tool names are announced in more than one place. Not-found and validation cards use explicit headings and do not rely on color alone to communicate 404 vs 422/413 differences.

---

## Alternatives Considered

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

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner

1. Flatten the persisted workflow into top-level `workflow.nodes` plus phase membership metadata and let the runner reconstruct containment later — rejected because D-GR-22 makes nested YAML phase containment authoritative and the editor already owns the flat-to-nested transformation internally.
2. Serialize hook wiring in a separate hook section or keep author-provided `edge.port_type` in YAML — rejected because hook-ness is derived from source-port resolution and the saved contract must expose one edge model, not parallel hook metadata.
3. Treat a checked-in `workflow-schema.json` as the canonical composer contract and `/api/schema/workflow` as optional — rejected because D-GR-22 makes runtime schema delivery from `WorkflowConfig.model_json_schema()` authoritative; static schema files are build/test artifacts only.
4. Silently normalize stale flat or separate-hook documents during load — rejected because it preserves split contracts across SF-1, SF-2, SF-5, and SF-6 and hides the exact contract drift D-GR-22 was created to eliminate.

<!-- SF: testing-framework -->
### SF-3: Testing Framework

1. Add `node_id` as a new keyword parameter to `AgentRuntime.invoke()` and thread it through every runtime call site.
2. Keep mixed or conflicting hierarchical merge orders across testing-framework, dag-loader-runner, and workflow-migration.
3. Create a testing-owned ContextVar layer instead of consuming the runner-owned runtime context.

<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test

1. Keep a breaking `AgentRuntime.invoke(..., node_id=...)` change across artifacts.
2. Allow each subfeature to assume its own context merge order.
3. Treat node-aware routing as prompt-text inference instead of ContextVar propagation.

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub

1. Keep `workflow-schema.json` as the runtime schema source and use `/api/schema/workflow` only as a build-time or fallback aid. Rejected because it reintroduces schema drift between the frontend, backend validation, and runtime loader.
2. Persist a separate serialized hooks section or hook-specific edge mode in saved YAML. Rejected because the canonical contract already models hook wiring as ordinary edges whose hook-ness is inferred from source-port resolution.
3. Persist the editor's flat internal graph as the saved workflow format and only nest phases in memory. Rejected because the canonical stored contract is nested phase containment, and save, export, and import must all agree on that shape.
4. Degrade to a view-only editor mode when schema is unavailable. Rejected because the schema gate is strictly blocking. A view-only fallback allows the editor to render with a potentially stale or missing schema surface, corrupting the user's mental model of the canonical contract and creating a hidden divergence path between what the user sees and what the runner expects.
5. SwitchFunctionEditor as a dedicated node type. Rejected because branching behavior is fully expressible through Branch nodes and their condition ports. A dedicated switch UI duplicates semantics and introduces an alternative serialization format for the same behavior.
6. ErrorFlowNode as a dedicated node type. Rejected because error routing is expressed through error ports present on all three atomic node types (Ask, Branch, Plugin). A dedicated error-flow node type diverges from the port-based error model and adds a fourth type to the serialization surface.
7. Store cross-phase edges inside the destination or source phase definition. Rejected because phase-level edge arrays create ambiguity about edge ownership when phases are reordered or re-parented. Workflow-root edge storage is unambiguous and mirrors the iriai-compose runner's edge resolution model.

<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas

1. Retain ErrorFlowNode as a fourth atomic canvas type. Rejected because REQ-2 constrains direct palette placement to Ask, Branch, and Plugin only. ErrorFlowNode does not appear in the PRD. Terminal and error conditions are expressed through Branch conditions (empty path, timeout, explicit error condition) and phase-level error routing, not through a dedicated terminal node.
2. Introduce SwitchFunctionEditor as the primary branch condition surface. Rejected because D-GR-12 established per-port conditions in OutputPathsEditor and PortConditionRow as the canonical branch authoring model, and the PRD lists no switch_function field in the branch schema. SwitchFunctionEditor would duplicate capabilities already covered by OutputPathsEditor.
3. Provide a view-only fallback mode when schema bootstrap fails. Rejected because REQ-13 requires blocking editing when the canonical runtime schema endpoint is unavailable, and AC-10 explicitly lists silent fallback to a local schema copy as a NOT criterion. A partial read-only surface exposes inspectors and node data derived from a potentially stale schema.
4. Keep a bundled `workflow-schema.json` as the primary runtime schema source and call `/api/schema/workflow` only as a fallback. Rejected because D-GR-22 makes the backend endpoint authoritative; static-schema-first boot paths drift from the running contract and bypass the blocking gate AC-10 requires.
5. Persist hook edges with a serialized `port_type` or a dedicated hooks section. Rejected because the canonical edge model identifies hook behavior by resolving the source handle against the hooks container; adding duplicate serialized state recreates the split D-GR-22 was written to remove.
6. Flatten all nodes to a workflow-root list and recover phases only from metadata. Rejected because it loses explicit phase ownership, makes nested phase export ambiguous, conflicts with the authoritative `phases[].nodes` plus `phases[].children` persistence model, and eliminates the deterministic cross-phase edge routing rule.
7. Render the synthetic root phase as a visible PhaseContainer boundary on the canvas. Rejected because the synthetic root is an implicit structural concept, not a named authoring artifact; rendering it as a visible boundary would mislead users into treating it like a named phase they created and could nest or rename.

<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries

1. Keep plugin-library and promotion surfaces in the design. Rejected because the current SF-7 PRD and review feedback do not trace them as launch scope.
2. Use DELETE 409 responses as the first time the user learns about references. Rejected because REQ-1 and REQ-2 require a non-destructive preflight.
3. Persist actor-slot definitions only inside unsaved canvas state and infer them later. Rejected because AC-2 requires round-trip persistence and reload visibility.
4. Show explicit 403 access denied messaging for cross-user detail routes. Rejected because REQ-6 requires 404 semantics to avoid leaking record existence.
5. Keep a view-only editor fallback when /api/schema/workflow is unavailable. Rejected because SF-6 requires a blocking bootstrap gate that disables the full editor (with retry affordance) on schema fetch failure. A view-only fallback would allow library entities to appear unreferenced while the schema is stale, producing incorrect delete-preflight results in SF-7.
6. Handle references from SwitchFunctionEditor or ErrorFlowNode node types in delete preflight. Rejected because SF-6's editor reset removes these node types entirely; only Ask, Branch, and Plugin are supported for direct canvas placement, so no reference rows from those removed types can ever reach workflow_entity_refs.
7. Treat the synthetic root phase container as a potential reference source. Rejected because SF-6's synthetic root is a serialization normalization artifact, not a user-placed node; it must not generate actor, schema, or tool reference rows, and the dialog must never show it as a referencing workflow.

---

## Rationale

<!-- SF: declarative-schema -->
### SF-1: Declarative Schema & Primitives

The prior declarative-schema artifact correctly locked nested YAML phase containment, edge-based hook wiring, and runtime schema delivery per D-GR-22, but did not encode four additional constraints that SF-6's editor contract requires and that the cycle 5 feedback calls out explicitly as missing: (1) exactly three atomic node types — AskNode, BranchNode, PluginNode — for direct phase.nodes placement; SwitchFunctionEditor and ErrorFlowNode are not valid schema node types; (2) cross-phase edge ownership at WorkflowConfig.edges with PhaseDefinition.edges restricted to intra-phase connections only; (3) a blocking schema bootstrap gate with no view-only or degraded fallback; and (4) synthetic root phase normalization so the editor always receives at least one phase from the loader.

These omissions would have allowed stale SF-6 artifacts to reintroduce the rejected SwitchFunctionEditor and ErrorFlowNode types, place cross-phase edges ambiguously inside phase.edges, keep a view-only fallback mode, and skip synthetic root normalization for empty workflows. The feedback identifies all four as blocking issues that the declarative-schema must own so SF-6's editor reset pushes only the required save/load contract downstream.

The revised artifact closes those gaps by promoting all four constraints to schema-level contracts: the NodeDefinition discriminant union (ask | branch | plugin) enforces the three-type limit in model_json_schema output; PhaseDefinition.edges vs. WorkflowConfig.edges ownership is explicit in both the model definition and validation error messages; the bootstrap gate and synthetic root normalization are first-class schema-loader behaviors that CMP-4 and CMP-3 document as verifiable states. These five constraints together — nested containment (D-GR-22), three atomic types, cross-phase edge ownership, blocking bootstrap, and synthetic root normalization — form the complete contract that SF-6's editor save/load path must implement against, with no stale alternatives permitted.

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner

SF-2 sits at the center of the contract chain: it parses the YAML that SF-6 saves, powers the validation surface that SF-5 exposes, and executes the result inside `iriai-compose`. If SF-2 tolerates stale flat-node, separate-hook, or static-schema-first assumptions, it becomes the accidental compatibility layer that keeps the cross-subfeature split alive. D-GR-22 resolves that split explicitly: persisted workflows remain nested under `phases[].nodes` and `phases[].children`, hook connectivity is represented only through ordinary edges with inferred hook classification, and composer schema consumers read the live contract from `/api/schema/workflow`. This revised artifact therefore replaces the older Branch-conflict-centered framing with a loader/runner contract centered on recursive phase containment, single-edge-model hook handling, and live schema export. That design remains additive to the existing imperative runner abstractions in `WorkflowRunner` and `DefaultWorkflowRunner`, while giving SF-5/SF-6 one stable parse/validate/execute interface for translated `iriai-build-v2` workflows. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/plan-review-discussion-4.md:20] [code: iriai-compose/iriai_compose/runner.py:62] [code: iriai-compose/iriai_compose/runner.py:162]

<!-- SF: testing-framework -->
### SF-3: Testing Framework

The revised artifact now matches the resolved cross-subfeature runtime decision: preserve the existing ABC, propagate node identity through ContextVar, and use one hierarchical prompt-context assembly model everywhere. The main edited sections are the overview and decision log, the SF-2 -> SF-3 contract section, and the detailed specs for `MockAgentRuntime`, `MockInteractionRuntime`, and `MockPluginRuntime` in `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/design-decisions.md`.

<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test

The updated artifact now matches the resolved D-GR-23 contract instead of stale cross-subfeature assumptions. That keeps the runtime ABI non-breaking, keeps SF-4 aligned with the current runner code and SF-3 PRD, and makes the visible node metadata consistent with actual prompt-context assembly. The revised artifact was written to [/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-migration/design-decisions.md]. Key alignment points are the overview/journey updates, the node-card/read-state updates, and the SF-2/SF-3/consumer interface sections. No tests were run; this was a markdown artifact revision only.

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub

The prior SF-5 design artifact was still centered on an older plugin-management contradiction. Cycle 4 made a different contract the real blocker: where the composer gets its schema, what shape persisted YAML has, and how hook edges are represented. D-GR-22 settles those together. SF-5 therefore needs a design that keeps the Explorer shell stable, treats `/api/schema/workflow` as the runtime authority, and makes stale-contract failures explicit at editor boot, import, save, and export boundaries instead of silently normalizing legacy formats.

The Cycle 5 revision feedback adds four specific contract requirements that SF-5's design must make explicit so they cascade as hard requirements into SF-6: (1) the schema gate is strictly blocking with no view-only fallback; (2) synthetic root phase normalization must run before every editor canvas mount; (3) only Ask, Branch, and Plugin node types are directly placeable — no SwitchFunctionEditor or ErrorFlowNode; (4) cross-phase edges are stored at the workflow root, not inside phase definitions. These are persistence/bootstrap contract requirements owned by SF-5, not editor implementation choices left to SF-6. Documenting them in SF-5's design gives SF-6 a clear, unambiguous contract to implement and prevents stale patterns (view-only fallback, fourth node type, phase-level cross-phase edges) from re-entering through the editor layer.

<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas

This revision closes the three gaps identified in the Cycle 5 feedback. First, ErrorFlowNode is removed: it was never in the PRD, REQ-2 explicitly limits atomic types to Ask, Branch, and Plugin, and carrying it forward would have caused the plan and system design to build scaffolding for a component the runtime does not define. Second, synthetic root phase normalization is promoted from an implicit AC-7 behavior to a first-class editor contract with its own interaction pattern, specific normalizer steps, and NOT criteria in J-1, J-2, and J-8 — giving the plan a clear save/load contract to implement without ambiguity. Third, cross-phase edge ownership at `workflow.edges[]` is made explicit and inviolable with per-phase edge list ownership for intra-phase edges and a clear routing rule that applies equally to data edges and hook edges. SwitchFunctionEditor is excluded via explicit NOT criteria and an alternative rejection so the plan cannot reintroduce it. The blocking schema gate is given a firm no-fallback statement aligned to AC-10 and REQ-13. All component paths now reference `tools/compose/frontend/src/` so that no downstream implementation plan targets the stale `tools/iriai-workflows` location. The prior cycle's D-GR-11, D-GR-12, and D-GR-22 baseline decisions are all preserved; this revision adds specificity, not contradiction.

<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries

The previous SF-7 design drifted in three directions at once: legacy journey IDs, plugin-library contamination, and inconsistent delete/reference semantics. The revised PRD and plan-review cycles settle the boundary: SF-5 remains a five-table foundation, while SF-7 owns the reference-index extension and the library behaviors built on top of it. This revision additionally aligns SF-7's design to SF-6's editor reset contract. SF-6 owns the blocking bootstrap gate, synthetic root phase normalization, and three-atomic-node-type enforcement — SF-7 receives only the completed save/load payload downstream. The delete-preflight UX communicates that only persisted saves (post-bootstrap) write to workflow_entity_refs, synthetic root containers never appear as reference sources, and cross-phase edges at the workflow root ARE included in reference scans. SwitchFunctionEditor and ErrorFlowNode are removed from SF-6 entirely, so SF-7's design never needs to handle references from those node types. All component paths are anchored to tools/compose/frontend/src/ per the accepted topology.

---

## Component Summary

| ID | Name | Subfeature | Status | Location |
|----|------|------------|--------|----------|
| CMP-1 | EdgeDefinition | declarative-schema | new | iriai_compose/schema/edges.py |
| CMP-2 | BranchNode | declarative-schema | new | iriai_compose/schema/nodes.py |
| CMP-3 | PhaseDefinition | declarative-schema | new | iriai_compose/schema/phases.py |
| CMP-4 | WorkflowConfig | declarative-schema | new | iriai_compose/schema/workflow.py |
| CMP-5 | AskNode | declarative-schema | new | iriai_compose/schema/nodes.py |
| CMP-6 | PluginNode | declarative-schema | new | iriai_compose/schema/nodes.py |
| CMP-7 | MockAgentRuntime | testing-framework | extending | iriai_compose/runner.py |
| CMP-8 | MockInteractionRuntime | testing-framework | extending | iriai_compose/runner.py |
| CMP-9 | MockPluginRuntime | testing-framework | new | iriai_compose/testing/mock_plugin.py |
| CMP-10 | Node Card Reads Metadata | workflow-migration | extending | design artifact |
| CMP-11 | Tier 2 Mock Runtime Contract | workflow-migration | extending | design artifact |
| CMP-12 | Consumer Integration Boundary | workflow-migration | extending | design artifact |
| CMP-13 | SidebarTree | composer-app-foundation | new | tools/compose/frontend — shell layout |
| CMP-14 | NewDropdown | composer-app-foundation | new | tools/compose/frontend — toolbar |
| CMP-15 | GridCard | composer-app-foundation | new | tools/compose/frontend — Workflows view |
| CMP-16 | EditorSchemaBootstrapGate | composer-app-foundation | new | tools/compose/frontend — editor route |
| CMP-17 | YAMLContractErrorPanel | composer-app-foundation | new | tools/compose/frontend — feedback surfaces |
| CMP-18 | AskFlowNode | workflow-editor | new | features/editor/nodes/AskNode.tsx |
| CMP-19 | BranchFlowNode | workflow-editor | new | features/editor/nodes/BranchNode.tsx |
| CMP-20 | PluginFlowNode | workflow-editor | new | features/editor/nodes/PluginNode.tsx |
| CMP-21 | DataEdge | workflow-editor | new | features/editor/edges/DataEdge.tsx |
| CMP-22 | HookEdge | workflow-editor | new | features/editor/edges/HookEdge.tsx |
| CMP-23 | OutputPathsEditor | workflow-editor | new | features/editor/inspectors/OutputPathsEditor.tsx |
| CMP-24 | MergeFunctionEditor | workflow-editor | new | features/editor/inspectors/MergeFunctionEditor.tsx |
| CMP-25 | PortConditionRow | workflow-editor | new | features/editor/inspectors/PortConditionRow.tsx |
| CMP-26 | PhaseContainer | workflow-editor | new | features/editor/phases/PhaseContainer.tsx |
| CMP-27 | SchemaBootstrapGate | workflow-editor | new | features/editor/schema/SchemaBootstrapGate.tsx |
| CMP-28 | EntityDeleteDialog | libraries-registries | new | features/libraries/shared/EntityDeleteDialog.tsx |
| CMP-29 | LibraryCollectionPage | libraries-registries | new | features/libraries/shared/LibraryCollectionPage.tsx |
| CMP-30 | RoleEditorForm | libraries-registries | new | features/libraries/roles/RoleEditorForm.tsx |
| CMP-31 | ToolEditorForm | libraries-registries | new | features/libraries/tools/ToolEditorForm.tsx |
| CMP-32 | ActorSlotsEditor | libraries-registries | new | features/libraries/templates/ActorSlotsEditor.tsx |
| CMP-33 | ResourceStateCard | libraries-registries | new | features/libraries/shared/ResourceStateCard.tsx |