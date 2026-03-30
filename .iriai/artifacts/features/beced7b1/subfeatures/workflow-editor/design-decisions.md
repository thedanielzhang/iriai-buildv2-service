<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas

The revised design builds on the Cycle 4 canonical contract—nested YAML phase containment, edge-based hook serialization, and `/api/schema/workflow` as the canonical schema source—and closes the three gaps the prior cycle left open.

First, the node vocabulary is reduced to the three atomic types the SF-6 PRD requires (REQ-2): Ask, Branch, and Plugin. ErrorFlowNode is removed entirely. It does not appear in REQ-2 or anywhere in the PRD and was incorrectly carried into the prior design cycle. Terminal and error conditions are expressed through Branch conditions and phase-level routing rather than a dedicated terminal node. SwitchFunctionEditor is explicitly excluded; branch condition authoring remains in OutputPathsEditor and PortConditionRow under the D-GR-12 per-port model.

Second, synthetic root phase normalization is added as a first-class editor contract aligned with AC-7: the canvas has an implicit synthetic root phase; unparented nodes always belong to it; and the normalizer runs on every save, auto-save, export, and import so that no node exists outside a phase container in the persisted YAML. The synthetic root phase has no visible canvas boundary but always exists structurally as the first `workflow.phases[]` entry when it contains any nodes.

Third, cross-phase edge ownership at `workflow.edges[]` is made explicit and inviolable. Any edge whose source and target belong to different phases—including edges crossing the synthetic root boundary—serializes to `workflow.edges[]`. Intra-phase edges are owned by their containing phase. This rule applies identically to DataEdge and HookEdge.

All component paths are relative to `tools/compose/frontend/src/`. The editor boots against the SF-5 compose foundation contract only and must not depend on `tools/iriai-workflows`, SQLite, `/api/plugins`, or `workflow_entity_refs` endpoints. SchemaBootstrapGate is strictly blocking with no view-only fallback: REQ-13 and AC-10 require a blocking error state when the schema endpoint is unavailable, and the design enforces this at the gate component level with no partial initialization path.

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

<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas
The editor remains desktop-first and maintains the existing sub-768px block. At 768px and above, the same flat canvas model applies at every breakpoint. SchemaBootstrapGate owns the full editor viewport until schema load succeeds, so at no breakpoint does the palette or any inspector hydrate before the canonical schema response arrives. Nested PhaseContainer headers and CollapsedGroupCard labels compress text rather than changing the underlying containment model. The synthetic root phase has no visual boundary at any breakpoint.

<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas

Schema bootstrap (blocking gate): the route enters through SchemaBootstrapGate (CMP-27), fetches `/api/schema/workflow`, and only then mounts palette metadata (Ask, Branch, Plugin only), inspectors, import validators, and save/export affordances. If the endpoint is unavailable the gate shows a blocking error card with Retry and no partial initialization. Test harnesses may inject a static fixture; the production path never treats `workflow-schema.json` as canonical.

Synthetic root phase normalization: the canvas always maintains an implicit synthetic root phase for unparented nodes. The normalizer runs on every save, auto-save, export, and import: (a) collects all nodes whose `parentId` is null or undefined into the synthetic root phase entry; (b) emits the synthetic root as the first entry in `workflow.phases[]` if it contains any nodes, omits it entirely if empty; (c) on import, assigns nodes found at the YAML workflow root without a phase parent to the synthetic root before canvas hydration. The synthetic root phase is never rendered as a visible PhaseContainer boundary on the canvas.

Cross-phase edge routing: every edge in the flat React Flow store is classified by comparing the phase membership of its source and target nodes. Edges where source and target share the same nearest named phase ancestor serialize to that phase's edge list. Edges where source and target belong to different phases—or where one endpoint is in the synthetic root and the other is in a named phase—serialize to `workflow.edges[]`. This rule applies to both DataEdge and HookEdge. The serializer runs this routing on every save, export, and auto-save.

Nested phase round-trip: SF-6 keeps the performant flat React Flow store with `parentId`, but every save, export, auto-save, and import normalizes through nested YAML. Top-level named phases serialize under `workflow.phases[]`, each owns `nodes[]` and `children[]`, and the synthetic root occupies the first `workflow.phases[]` slot when it contains nodes.

Edge-based hooks: hook wiring is created only through `on_start` and `on_end` handles and is visually classified by resolving the source handle against the hooks container. Export writes the same edge shape as data edges, minus any transform, and places the edge in `workflow.edges[]` or the containing phase edge list based on the cross-phase routing rule. Import re-derives HookEdge styling from the source handle; no serialized `port_type` is ever read.

Branch editing: OutputPathsEditor (CMP-23), PortConditionRow (CMP-25), and MergeFunctionEditor (CMP-24) retain D-GR-12's per-port condition model. SwitchFunctionEditor does not exist and must not be created. Field metadata hydrates from the live schema payload. Handle changes participate in the same nested-phase and dot-notation save pipeline as the rest of the canvas.

<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas
SchemaBootstrapGate uses `role="status"` during loading and `role="alert"` on failure so screen readers understand why the editor is blocked. Retry receives initial focus when the error card appears. No view-only or partial canvas is presented during error states, so there is no risk of an inaccessible read-only surface.
Hook edges rely on both color (purple) and dashed stroke for distinction; the hook edge inspector title explicitly includes "Hook" so the semantic difference is not color-only.
PhaseContainer exposes an accessible name including phase title, mode, and nesting depth, keeping nested `children[]` relationships understandable for assistive technology. The synthetic root phase has no accessible boundary element since it is not rendered.
Import and save failures announce the exact blocking issue in a live region before focus moves to the offending canvas element or inspector control.
Disabled destructive actions, such as removing the last required branch path, stay in the tab order only when they provide explanatory help text; otherwise focus moves to the next valid editing control.

<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas

1. Retain ErrorFlowNode as a fourth atomic canvas type. Rejected because REQ-2 constrains direct palette placement to Ask, Branch, and Plugin only. ErrorFlowNode does not appear in the PRD. Terminal and error conditions are expressed through Branch conditions (empty path, timeout, explicit error condition) and phase-level error routing, not through a dedicated terminal node.
2. Introduce SwitchFunctionEditor as the primary branch condition surface. Rejected because D-GR-12 established per-port conditions in OutputPathsEditor and PortConditionRow as the canonical branch authoring model, and the PRD lists no switch_function field in the branch schema. SwitchFunctionEditor would duplicate capabilities already covered by OutputPathsEditor.
3. Provide a view-only fallback mode when schema bootstrap fails. Rejected because REQ-13 requires blocking editing when the canonical runtime schema endpoint is unavailable, and AC-10 explicitly lists silent fallback to a local schema copy as a NOT criterion. A partial read-only surface exposes inspectors and node data derived from a potentially stale schema.
4. Keep a bundled `workflow-schema.json` as the primary runtime schema source and call `/api/schema/workflow` only as a fallback. Rejected because D-GR-22 makes the backend endpoint authoritative; static-schema-first boot paths drift from the running contract and bypass the blocking gate AC-10 requires.
5. Persist hook edges with a serialized `port_type` or a dedicated hooks section. Rejected because the canonical edge model identifies hook behavior by resolving the source handle against the hooks container; adding duplicate serialized state recreates the split D-GR-22 was written to remove.
6. Flatten all nodes to a workflow-root list and recover phases only from metadata. Rejected because it loses explicit phase ownership, makes nested phase export ambiguous, conflicts with the authoritative `phases[].nodes` plus `phases[].children` persistence model, and eliminates the deterministic cross-phase edge routing rule.
7. Render the synthetic root phase as a visible PhaseContainer boundary on the canvas. Rejected because the synthetic root is an implicit structural concept, not a named authoring artifact; rendering it as a visible boundary would mislead users into treating it like a named phase they created and could nest or rename.

<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas

This revision closes the three gaps identified in the Cycle 5 feedback. First, ErrorFlowNode is removed: it was never in the PRD, REQ-2 explicitly limits atomic types to Ask, Branch, and Plugin, and carrying it forward would have caused the plan and system design to build scaffolding for a component the runtime does not define. Second, synthetic root phase normalization is promoted from an implicit AC-7 behavior to a first-class editor contract with its own interaction pattern, specific normalizer steps, and NOT criteria in J-1, J-2, and J-8 — giving the plan a clear save/load contract to implement without ambiguity. Third, cross-phase edge ownership at `workflow.edges[]` is made explicit and inviolable with per-phase edge list ownership for intra-phase edges and a clear routing rule that applies equally to data edges and hook edges. SwitchFunctionEditor is excluded via explicit NOT criteria and an alternative rejection so the plan cannot reintroduce it. The blocking schema gate is given a firm no-fallback statement aligned to AC-10 and REQ-13. All component paths now reference `tools/compose/frontend/src/` so that no downstream implementation plan targets the stale `tools/iriai-workflows` location. The prior cycle's D-GR-11, D-GR-12, and D-GR-22 baseline decisions are all preserved; this revision adds specificity, not contradiction.
