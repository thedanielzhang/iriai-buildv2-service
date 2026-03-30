<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub

SF-5 treats `/api/schema/workflow` as the only runtime schema handshake between the compose app and `iriai-compose`. The Explorer shell still loads independently, but editor entry, import validation, save/export behavior, and stale-contract error messaging all align to the same persisted workflow contract: nested phase containment (`phases[].nodes`, `phases[].children`) and edge-only hook serialization with no separate serialized `port_type`. The foundation therefore owns the schema bootstrap/loading/error experience and must never treat a bundled `workflow-schema.json` as the runtime source of truth.

SF-5 also owns the definition of four contract requirements that cascade downstream to SF-6's implementation. These are non-negotiable persistence/bootstrap requirements, not SF-6 design choices: (1) Synthetic root phase normalization — every workflow the editor opens must have at least one phase; if the stored payload has no phases, the load path wraps content in a synthetic root phase before the canvas mounts. (2) Three atomic node types — only Ask, Branch, and Plugin nodes are directly placeable in the editor canvas; no SwitchFunctionEditor or ErrorFlowNode surfaces in the palette, inspector, or serialization format. (3) Cross-phase edges at workflow root — edges that connect nodes in different phases are stored in the workflow-root `edges` array, never inside a phase definition. (4) Blocking schema gate — the schema bootstrap gate has no view-only fallback; when schema is unavailable, the editor shows the blocking error panel and nothing else.

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

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub
Desktop-first. The Explorer shell and schema bootstrap gate are optimized for full desktop widths; below the supported desktop breakpoint the app should show a blocking informational screen rather than attempt a reduced schema-aware editor experience. Within supported desktop widths, bootstrap and error panels collapse to a single-column card inside the content area and never displace the sidebar.

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub

Schema bootstrap — lazy, route-scoped, strictly blocking. The Workflows shell loads without calling `/api/schema/workflow`. Navigating to `/workflows/{id}/edit` requests the workflow record and canonical schema in parallel and mounts schema-dependent UI only after both resolve. There is no view-only fallback: if schema fetch fails, the EditorSchemaBootstrapGate shows the blocking error panel and the editor canvas does not render in any state. Retry triggers a new fetch; success removes the gate and mounts the editor. Back to Workflows exits the route without altering the workflow record.

Synthetic root phase normalization. Before the editor canvas mounts, the load path guarantees every workflow has at least one phase. If the persisted workflow payload has no phase structure, it is wrapped in a synthetic root phase. This normalization runs in the data preparation layer (API response transform or load hook), not inside the editor canvas itself. The canvas always receives a canonically phased workflow and never renders a phaseless flat node graph. Normalization does not alter the persisted YAML unless the user explicitly saves after opening.

Three atomic node types. The editor canvas surfaces exactly three atomic node types for direct placement: Ask, Branch, and Plugin. SwitchFunctionEditor and ErrorFlowNode do not exist in the palette, inspector, or serialization format. Branching behavior is expressed through Branch nodes and their condition ports. Error routing is expressed through error ports present on all three atomic types. Save and export reject any node type outside these three from the persisted phase structure.

Cross-phase edge storage. Edges that connect nodes belonging to different phases are stored in the workflow-root `edges` array, not inside any phase definition. On load, the editor reconstructs phase membership from node containment metadata. On save, any edge whose endpoints belong to different phases is lifted to the workflow-root array before serialization. Phase-level edge arrays, if present in any loaded payload, are treated as stale-contract violations and surfaced through the YAMLContractErrorPanel.

Save / export / import contract. All four operations speak the same persisted shape: nested `phases[].nodes` and `phases[].children`, only Ask/Branch/Plugin node types inside phases, cross-phase edges at workflow root, and hook connections in the edges array with no serialized `port_type`. Any internal editor-only `port_type` concept is reconstructed from port resolution and stripped before persistence. Import distinguishes parse errors from stale-contract validation failures; both keep the user in recoverable shell states and never partially write invalid workflows.

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub
The schema bootstrap gate uses `aria-busy` during loading and moves focus to the panel heading on failure so keyboard users encounter the retry action immediately. Error and warning panels expose path/message details in keyboard-operable expandable regions and announce blocking failures through an assertive live region. SidebarTree uses roving focus with arrow-key navigation, and `+ New` supports Enter or Space to open plus Escape to close. Toasts for import and save outcomes are announced through live regions, while blocking schema and import errors remain persistent in the content area until dismissed or resolved.

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub

1. Keep `workflow-schema.json` as the runtime schema source and use `/api/schema/workflow` only as a build-time or fallback aid. Rejected because it reintroduces schema drift between the frontend, backend validation, and runtime loader.
2. Persist a separate serialized hooks section or hook-specific edge mode in saved YAML. Rejected because the canonical contract already models hook wiring as ordinary edges whose hook-ness is inferred from source-port resolution.
3. Persist the editor's flat internal graph as the saved workflow format and only nest phases in memory. Rejected because the canonical stored contract is nested phase containment, and save, export, and import must all agree on that shape.
4. Degrade to a view-only editor mode when schema is unavailable. Rejected because the schema gate is strictly blocking. A view-only fallback allows the editor to render with a potentially stale or missing schema surface, corrupting the user's mental model of the canonical contract and creating a hidden divergence path between what the user sees and what the runner expects.
5. SwitchFunctionEditor as a dedicated node type. Rejected because branching behavior is fully expressible through Branch nodes and their condition ports. A dedicated switch UI duplicates semantics and introduces an alternative serialization format for the same behavior.
6. ErrorFlowNode as a dedicated node type. Rejected because error routing is expressed through error ports present on all three atomic node types (Ask, Branch, Plugin). A dedicated error-flow node type diverges from the port-based error model and adds a fourth type to the serialization surface.
7. Store cross-phase edges inside the destination or source phase definition. Rejected because phase-level edge arrays create ambiguity about edge ownership when phases are reordered or re-parented. Workflow-root edge storage is unambiguous and mirrors the iriai-compose runner's edge resolution model.

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub

The prior SF-5 design artifact was still centered on an older plugin-management contradiction. Cycle 4 made a different contract the real blocker: where the composer gets its schema, what shape persisted YAML has, and how hook edges are represented. D-GR-22 settles those together. SF-5 therefore needs a design that keeps the Explorer shell stable, treats `/api/schema/workflow` as the runtime authority, and makes stale-contract failures explicit at editor boot, import, save, and export boundaries instead of silently normalizing legacy formats.

The Cycle 5 revision feedback adds four specific contract requirements that SF-5's design must make explicit so they cascade as hard requirements into SF-6: (1) the schema gate is strictly blocking with no view-only fallback; (2) synthetic root phase normalization must run before every editor canvas mount; (3) only Ask, Branch, and Plugin node types are directly placeable — no SwitchFunctionEditor or ErrorFlowNode; (4) cross-phase edges are stored at the workflow root, not inside phase definitions. These are persistence/bootstrap contract requirements owned by SF-5, not editor implementation choices left to SF-6. Documenting them in SF-5's design gives SF-6 a clear, unambiguous contract to implement and prevents stale patterns (view-only fallback, fourth node type, phase-level cross-phase edges) from re-entering through the editor layer.
