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
