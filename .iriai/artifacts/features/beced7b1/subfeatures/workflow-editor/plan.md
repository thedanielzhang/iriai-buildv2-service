### SF-6: Workflow Editor & Canvas

<!-- SF: workflow-editor -->



## Architecture Decisions

| ID | Decision | Rationale | Citation |
|----|----------|-----------|----------|
| D-SF6-1 | Unified expand-to-real-nodes for both phases and templates — no MiniTopologyPreview | Both collapsed phases and collapsed templates use the same pattern: collapsed = compact card showing metadata + node count; expanded = children injected as real React Flow nodes with `parentId`. This eliminates the div-based MiniTopologyPreview entirely. React Flow's `parentId` grouping is the single mechanism for containment. Templates expand to read-only inspectable nodes; phases expand to fully editable nodes. | [decision: D-24 collapsible phases]; [decision: D-25 task templates read-only]; [Context7: React Flow — parentId sub-flows, not nested instances] |
| D-SF6-2 | Full snapshot undo/redo via structuredClone, 50 depth | Partial undo (command pattern) requires every mutation to define its inverse — combinatorial explosion with node config, edge transforms, phase modes, actor slots. Full snapshots are simpler, correct, and fast enough: structuredClone of ~200 nodes + edges < 1ms. 50 depth = ~2MB worst case. | [decision: D-23 undo/redo 50 depth]; [research: structuredClone perf benchmarks] |
| D-SF6-3 | React Flow flat node/edge arrays as canonical store shape | React Flow expects `Node[]` and `Edge[]`. Storing nested phase trees forces constant flattening/unflattening on every render. Flat shape = zero transform cost for React Flow, phase membership tracked via `parentId` on nodes. Serialization to nested YAML is a one-time cost on save/export. | [code: React Flow — nodes/edges props]; [decision: D-8 phases as iteration containers] |
| D-SF6-4 | Hybrid validation — isValidConnection for instant checks, debounced for deep analysis | `isValidConnection` must be synchronous and < 1ms (React Flow calls it on every mouse move during drag). It handles cycle detection (DFS) and port type compatibility. Full type-flow analysis and schema validation run debounced (500ms) on mutation. | [decision: D-20 live validation debounced]; [code: React Flow isValidConnection signature] |
| D-SF6-5 | Custom recursive dagre for auto-layout | Phases are nested containers. Standard dagre treats all nodes as flat — it cannot respect phase bounding boxes. Recursive dagre: layout leaf phase internals first, compute bounding box, treat phase as oversized node in parent layout, repeat up to root. `@dagrejs/dagre` with `rankdir: 'LR'` for left-to-right flow matching data port positions (input left, output right). | [decision: D-40 data ports left/right]; [research: dagre nested graph layout] |
| D-SF6-6 | YAML serialization via `js-yaml`; TS types mirror `iriai_compose.schema` (NOT `iriai_compose.declarative.schema`) [C-2] | `js-yaml` is the standard JS YAML library (200KB gzipped). Serialization walks flat RF nodes/edges, groups by `parentId` into nested `PhaseDefinition` trees, maps RF handle IDs to SF-1 `"node_id.port_name"` edge format. **Module path confirmed:** SF-1 places Pydantic models at `iriai_compose/schema/` with canonical import `iriai_compose.schema` per D-SF1-1. No `declarative` intermediate package. `yamlSchema.ts` and all validation endpoints must use this path. | [decision: D-SF1-1 module at iriai_compose/schema/]; [code: SF-1 plan — C-2 canonical import path]; [decision: D-SF1-8 YAML serialization] |
| D-SF6-7 | Templates use stamp-and-detach semantics — no live link after drop | Dropping a template from palette stamps independent copies of its nodes onto the canvas inside a read-only TemplateGroup container. There is NO live link back to the library template. Nodes are inspectable (read-only inspectors) but not editable. "Detach" converts to fully editable independent nodes. This avoids template sync/versioning complexity entirely — templates are reusable paste shortcuts. | [decision: D-25 task templates read-only]; [decision: D-38 inline-to-library promotion] |
| D-SF6-8 | BranchNode dual routing model — `switch_function` (exclusive) vs per-port `condition` (non-exclusive), mutually exclusive [C-1] | SF-1 defines two routing strategies on BranchNode (D-SF1-2, D-SF1-28): (1) **Exclusive:** `switch_function` is a Python expression returning a port name string — only that port fires. (2) **Non-exclusive:** per-port `condition` expressions on output PortDefinitions — all matching ports fire simultaneously. These are mutually exclusive per D-SF1-28: a BranchNode with `switch_function` set MUST NOT have `condition` on any output port. BranchInspector implements this as two modes: SwitchFunctionEditor (CodeMirror for `switch_function` field) is the default; clearing `switch_function` reveals per-port condition editors on OutputPathsEditor. Client validator enforces mutual exclusivity (`invalid_switch_function_config`). | [code: SF-1 plan — D-SF1-2, D-SF1-28]; [decision: D-28 Branch = programmatic switch] |
| D-SF6-9 | `createEditorStore(options?)` factory exported alongside singleton [H-5] | SF-7's TaskTemplateEditorView needs an independent store instance (no phases, no template stamping, scoped undo). Exporting a factory function allows multiple co-existing store instances. The default export remains a singleton for the main workflow editor. Factory accepts `EditorStoreOptions` to disable phase-specific features. | [decision: D-39 Task Templates canvas-dominant]; [decision: D-40 Shared canvas UX across scales] |

## User Decisions Log

| ID | Decision | Source |
|----|----------|--------|
| D-U1 | Phases use expand-to-real-nodes (no MiniTopologyPreview thumbnails). Collapsed = compact card with metadata. Expanded = real RF child nodes. | User feedback — "phases should use this as well" |
| D-U2 | Templates use expand-to-real-nodes pattern, same as phases. Collapsed = green card. Expanded = real RF child nodes on main canvas with sub-phases collapsed. | User feedback — "instead of nested can we frame it as collapsed node" |
| D-U3 | Template children are read-only but fully inspectable — can select and open read-only inspectors with all fields visible but disabled. Cannot edit, move, or delete template children. | User feedback — "they are still read only, its just we have access to every node's inspector element" |
| D-U4 | Templates use stamp-and-detach — no live link to library after drop. Detach converts to editable independent nodes. | User choice — Option A over linked instances |

## File Structure Overview

```
src/features/editor/
├── store/
│   ├── editorStore.ts          # STEP-47: Zustand store — singleton + createEditorStore factory [H-5]
│   ├── undoMiddleware.ts        # STEP-47: withUndo wrapper, snapshot management
│   └── selectors.ts             # STEP-47: Memoized selectors for derived data
├── serialization/
│   ├── serializeToYaml.ts       # STEP-47: Flat RF → nested YAML
│   ├── deserializeFromYaml.ts   # STEP-47: Nested YAML → flat RF
│   ├── autoLayout.ts            # STEP-47: Recursive dagre layout
│   └── yamlSchema.ts            # STEP-47: TS types mirroring iriai_compose.schema [C-2]
├── validation/
│   └── validationTypes.ts       # STEP-47: ValidationIssue type definition
├── canvas/
│   ├── EditorCanvas.tsx         # STEP-48: ReactFlow wrapper component
│   ├── connectionValidator.ts   # STEP-48: isValidConnection — cycle + port type checks
│   └── canvasStyles.css         # STEP-48: Dot grid, selection ring, phase borders
├── nodes/
│   ├── nodeTypes.ts             # STEP-48 (placeholder) → STEP-50 (final registration)
│   ├── shared/
│   │   ├── NodeCard.tsx         # STEP-49: 260px card with colored header bar
│   │   ├── SocketPort.tsx       # STEP-49: 12px recessed port with always-visible label
│   │   ├── ActorSlot.tsx        # STEP-49: 12px recessed circle for role drag-drop
│   │   ├── NodeSummary.tsx      # STEP-49: 1-2 line italic muted text
│   │   ├── ContextKeys.tsx      # STEP-49: "reads: key1, key2, ..." display
│   │   ├── ArtifactKey.tsx      # STEP-49: "produces: artifact_name" display
│   │   ├── PromptPreview.tsx    # STEP-49: Truncated monospace prompt
│   │   ├── SwitchFunctionLabel.tsx  # STEP-49: Amber pill — "ƒ switch(...)" or "conditions" based on routing mode [C-1]
│   │   ├── StatusIndicator.tsx  # STEP-49: Dot + status text
│   │   ├── ErrorBadge.tsx       # STEP-49: Red circle with error count
│   │   └── CollapsedGroupCard.tsx   # STEP-49: Shared collapsed card for phases + templates
│   ├── AskNode.tsx              # STEP-50: Purple Ask node component
│   ├── BranchNode.tsx           # STEP-50: Amber Branch node component — dual routing indicator [C-1]
│   ├── PluginNode.tsx           # STEP-50: Gray Plugin node component
│   └── TemplateGroup.tsx        # STEP-50: Green template group (collapsible, read-only children)
├── phases/
│   ├── PhaseContainer.tsx       # STEP-51: Mode-styled group node (collapsible, editable children)
│   ├── PhaseLabelBar.tsx        # STEP-51: Mode icon + name + collapse + detach
│   └── LoopExitPorts.tsx        # STEP-51: Dual exit ports for loop mode
├── edges/
│   ├── DataEdge.tsx             # STEP-52: Type label + transform indicator
│   ├── HookEdge.tsx             # STEP-52: Dashed purple, no label
│   ├── EdgeLabel.tsx            # STEP-52: Midpoint type/transform label
│   └── edgeTypes.ts             # STEP-48 (placeholder) → STEP-52 (final)
├── toolbar/
│   ├── PaintMenuBar.tsx         # STEP-53: File/Edit/View menus
│   ├── IconToolbar.tsx          # STEP-53: Action buttons + tool mode toggle
│   ├── ToolbarButton.tsx        # STEP-53: 32x32 icon button
│   └── ToolModeToggle.tsx       # STEP-53: Hand vs Select
├── palette/
│   ├── NodePalette.tsx          # STEP-53: 48px right-side strip
│   ├── PaletteItem.tsx          # STEP-53: Draggable icon
│   └── RolePalette.tsx          # STEP-53: Role chips for drag-to-actor-slot
├── inspectors/
│   ├── InspectorWindowManager.tsx   # STEP-54: Portal rendering + z-ordering
│   ├── InspectorWindow.tsx      # STEP-54: Draggable XP panel
│   ├── TetherLine.tsx           # STEP-54: SVG line to canvas element
│   ├── AskInspector.tsx         # STEP-55: Purple titlebar, actor/prompt/schema
│   ├── BranchInspector.tsx      # STEP-55: Amber titlebar, dual routing mode [C-1]
│   ├── PluginInspector.tsx      # STEP-55: Gray titlebar, plugin config
│   ├── PhaseInspector.tsx       # STEP-55: Mode-colored, mode config
│   ├── EdgeInspector.tsx        # STEP-56: Data ~500px / Hook ~280px
│   ├── InspectorActions.tsx     # STEP-55: Footer action buttons
│   ├── PromptTemplateEditor.tsx # STEP-55: {{ }} autocomplete
│   ├── InlineRoleCreator.tsx    # STEP-55: Tier 1 role editor
│   ├── InlineOutputSchemaCreator.tsx  # STEP-55: Field-by-field schema
│   ├── OutputPathsEditor.tsx    # STEP-55: Branch paths → ports, per-port condition editors [C-1]
│   ├── SwitchFunctionEditor.tsx # STEP-55: CodeMirror Python for switch_function field [C-1]
│   └── CodeEditor.tsx           # STEP-55: Shared CodeMirror wrapper
├── hooks/
│   ├── useAutoSave.ts           # STEP-58: 30s inactivity auto-save
│   ├── useKeyboardShortcuts.ts  # STEP-61: Canvas-scoped shortcuts
│   └── useDragAndDrop.ts        # STEP-60: Palette→canvas + role→slot
├── dialogs/
│   ├── ImportConfirmDialog.tsx   # STEP-58: Canvas replacement warning
│   ├── PromotionDialog.tsx      # STEP-60: Inline → library save
│   └── SaveAsTemplateDialog.tsx # STEP-60: Subgraph → template
├── validation/
│   ├── clientValidator.ts       # STEP-57: Structural validation incl. switch_function mutual exclusivity [C-1]
│   ├── ValidationPanel.tsx      # STEP-57: Floating issue list
│   └── validationTypes.ts       # STEP-47: ValidationIssue type
├── canvas/
│   └── SelectionRectangle.tsx   # STEP-59: Marching ants for phase creation
└── WorkflowEditorPage.tsx       # STEP-62: Route component assembly
```

---

### STEP-47: Editor Zustand Store + Undo/Redo + Serialization

**Objective:** Create the canonical editor store using React Flow's flat node/edge shape with full snapshot undo/redo (structuredClone, 50 depth) and bidirectional YAML serialization (flat RF nodes/edges with parentId for phase membership to nested SF-1 PhaseDefinition trees). The store is the single source of truth for the entire editor — all mutations go through Zustand actions that push undo snapshots. **Additionally, export a `createEditorStore(options?)` factory function [H-5] so SF-7's TaskTemplateEditorView can instantiate independent store instances.**

**Requirement IDs:** REQ-13, REQ-14, REQ-15
**Journey IDs:** J-16, J-22

**Scope:**
| Path | Action |
|------|--------|
| `features/editor/store/editorStore.ts` | create |
| `features/editor/store/undoMiddleware.ts` | create |
| `features/editor/store/selectors.ts` | create |
| `features/editor/serialization/serializeToYaml.ts` | create |
| `features/editor/serialization/deserializeFromYaml.ts` | create |
| `features/editor/serialization/autoLayout.ts` | create |
| `features/editor/serialization/yamlSchema.ts` | create |
| `features/editor/validation/validationTypes.ts` | create |

**Instructions:**

**1. yamlSchema.ts — TypeScript mirrors of SF-1 Pydantic models**

Define TypeScript interfaces that mirror the SF-1 schema models for type-safe serialization. These are the YAML-side types — distinct from React Flow's Node/Edge types but convertible to/from them.

**[C-2] CRITICAL: The canonical Python import path is `iriai_compose.schema` (per SF-1 decision D-SF1-1). There is NO `iriai_compose.declarative` intermediate package. All comments referencing the schema source MUST use `iriai_compose.schema`. The validation endpoint (`POST /api/workflows/:id/validate`) fetches the JSON Schema generated by `iriai_compose.schema.WorkflowConfig.model_json_schema()`. Frontend type definitions below mirror that schema.**

```typescript
// TypeScript mirrors of iriai_compose.schema Pydantic models [C-2]
// Canonical Python path: iriai_compose.schema (NOT iriai_compose.declarative.schema)
// JSON Schema source: WorkflowConfig.model_json_schema()

export interface PortDefinition {
  name: string;
  direction: 'input' | 'output';
  type_ref?: string;
  description?: string;
  condition?: string; // Python predicate — only meaningful on output ports [D-SF1-2]
}

export interface EdgeDefinition {
  source: string;       // "node_id.port_name"
  target: string;       // "node_id.port_name"
  transform_fn?: string; // inline Python — D-19
}

export interface NodeDefinition {
  id: string;
  type: 'ask' | 'branch' | 'plugin';
  summary?: string;
  context_keys?: string[];
  context_text?: Record<string, string>;
  artifact_key?: string;
  input_type?: string;
  input_schema?: Record<string, unknown>;
  output_type?: string;
  output_schema?: Record<string, unknown>;
  inputs?: PortDefinition[];
  outputs?: PortDefinition[];
  hooks?: PortDefinition[];
  position?: { x: number; y: number };

  // Ask-specific
  actor?: string;
  inline_role?: InlineRoleDefinition;
  prompt?: string;

  // Branch-specific [C-1: dual routing model — D-SF1-2, D-SF1-28]
  switch_function?: string | null; // Python expression returning port name (exclusive routing)
  merge_function?: string | null;  // Python expression merging multi-port inputs
  // When switch_function is set: routing is exclusive (function returns port name string)
  // When switch_function is null/empty: routing via per-port condition on PortDefinition.condition
  // These two modes are MUTUALLY EXCLUSIVE per D-SF1-28

  // Plugin-specific
  plugin_ref?: string;
  instance_ref?: string;
  plugin_config?: Record<string, unknown>;
}

// ... remaining types unchanged ...
```

Also define: `ActorDefinition`, `InlineRoleDefinition`, `TypeDefinition`, `PluginInterface`, `PluginInstanceConfig`, `StoreDefinition`, `CostConfig`, `TemplateRef`, `SequentialConfig`, `MapConfig`, `FoldConfig`, `LoopConfig`. Keep these minimal — only fields the editor reads/writes. Runtime-only fields (e.g., `fresh_sessions` on FoldConfig/LoopConfig) are preserved through serialization but not edited in STEP-47.

**2. validationTypes.ts — Validation issue model**

```typescript
export type ValidationSeverity = 'error' | 'warning';

export interface ValidationIssue {
  code: string;
  path: string;
  message: string;
  nodeId?: string;
  edgeId?: string;
  severity: ValidationSeverity;
}
```

**3. undoMiddleware.ts — Snapshot undo/redo wrapper**

Define the data slice that gets snapshot:

```typescript
import type { Node, Edge } from '@xyflow/react';

export interface WorkflowSnapshot {
  nodes: Node[];
  edges: Edge[];
  actors: Record<string, ActorDef>;
  types: Record<string, TypeDef>;
  plugins: Record<string, PluginDef>;
  pluginInstances: Record<string, PluginInstanceDef>;
  stores: Record<string, StoreDef>;
  contextKeys: string[];
}
```

`withUndo` is a higher-order function that wraps a Zustand state mutation:

```typescript
export function createUndoMiddleware(get: () => EditorState, set: (partial: Partial<EditorState>) => void) {
  return {
    withUndo: (mutationFn: (state: EditorState) => Partial<EditorState>) => {
      const state = get();
      const snapshot = takeSnapshot(state);
      const updates = mutationFn(state);
      set({
        ...updates,
        undoStack: [...state.undoStack, snapshot].slice(-50),
        redoStack: [],
        isDirty: true,
        autoSaveStatus: 'dirty',
      });
    },
    undo: () => { /* pop undoStack, push current to redoStack, restore */ },
    redo: () => { /* pop redoStack, push current to undoStack, restore */ },
  };
}

function takeSnapshot(state: EditorState): WorkflowSnapshot {
  return structuredClone({
    nodes: state.nodes,
    edges: state.edges,
    actors: state.actors,
    types: state.types,
    plugins: state.plugins,
    pluginInstances: state.pluginInstances,
    stores: state.stores,
    contextKeys: state.contextKeys,
  });
}
```

Key behaviors:
- `undo()`: If `undoStack` is empty, no-op. Otherwise: snapshot current state, push to `redoStack`; pop last `undoStack` entry and restore its fields into state.
- `redo()`: If `redoStack` is empty, no-op. Otherwise: snapshot current state, push to `undoStack`; pop last `redoStack` entry and restore.
- DO NOT snapshot `undoStack`, `redoStack`, `validationIssues`, `toolMode`, `autoSaveStatus`, `inspectors`, or `isDirty`. These are UI-only.
- DO NOT use JSON.parse/JSON.stringify — structuredClone handles Map, Set, Date, ArrayBuffer correctly and is faster for plain objects.
- Cap array at 50 by slicing from the end: `.slice(-50)`.

**4. editorStore.ts — Zustand store definition with factory export [H-5]**

**[H-5] CRITICAL: Export both a `createEditorStore(options?)` factory function AND a default singleton.** SF-7's TaskTemplateEditorView needs independent store instances with scoped capabilities (no phases, no template stamping). The factory is the real implementation; the singleton calls it.

```typescript
import { create, type StoreApi, type UseBoundStore } from 'zustand';
import type { Node, Edge, OnNodesChange, OnEdgesChange, Connection } from '@xyflow/react';
import { applyNodeChanges, applyEdgeChanges, addEdge } from '@xyflow/react';

// --- Store options for factory [H-5] ---

export interface EditorStoreOptions {
  /** When true, disables phase creation, template stamping, and phase-specific
   *  actions. Used by SF-7 TaskTemplateEditorView. Default: false. */
  scopedMode?: boolean;

  /** Initial workflow ID. Default: '' */
  initialWorkflowId?: string;

  /** Initial workflow name. Default: 'Untitled' */
  initialWorkflowName?: string;
}

// --- State types ---

export interface InspectorState {
  windowId: string;
  elementId: string;
  elementType: 'node' | 'edge' | 'phase' | 'template-group';
  position: { x: number; y: number };
  readOnly?: boolean;
}

export interface EditorState {
  // Store options (immutable after creation)
  _options: EditorStoreOptions;

  // Workflow identity
  workflowId: string;
  workflowName: string;

  // React Flow canonical data (flat shape — D-SF6-3)
  nodes: Node[];
  edges: Edge[];

  // Collapse state
  collapsedGroups: Record<string, boolean>;

  // Workflow-level registries
  actors: Record<string, ActorDef>;
  types: Record<string, TypeDef>;
  plugins: Record<string, PluginDef>;
  pluginInstances: Record<string, PluginInstanceDef>;
  stores: Record<string, StoreDef>;
  contextKeys: string[];

  // Undo/redo (D-SF6-2)
  undoStack: WorkflowSnapshot[];
  redoStack: WorkflowSnapshot[];

  // Validation
  validationIssues: ValidationIssue[];

  // UI state (NOT in undo snapshots)
  toolMode: 'hand' | 'select';
  autoSaveStatus: 'clean' | 'dirty' | 'saving' | 'error';
  inspectors: InspectorState[];
  isDirty: boolean;

  // Actions — all structural mutations go through withUndo
  addNode: (node: Node) => void;
  removeNodes: (nodeIds: string[]) => void;
  updateNodeData: (nodeId: string, data: Partial<Node['data']>) => void;

  addEdge: (connection: Connection) => void;
  removeEdges: (edgeIds: string[]) => void;

  onNodesChange: OnNodesChange;
  onEdgesChange: OnEdgesChange;
  onNodeDragStop: () => void;

  undo: () => void;
  redo: () => void;

  toggleCollapse: (groupId: string) => void;
  isCollapsed: (groupId: string) => boolean;

  // Template stamp-and-detach (D-SF6-7) — disabled in scopedMode
  stampTemplate: (templateId: string, position: { x: number; y: number }, templateData: TemplateStampData) => void;
  detachTemplateGroup: (groupId: string) => void;

  // Registry mutations
  setActors: (actors: Record<string, ActorDef>) => void;
  updateActor: (id: string, actor: ActorDef) => void;
  removeActor: (id: string) => void;

  // Serialization
  loadFromYaml: (yaml: string) => void;
  serializeToYaml: () => string;

  // UI actions (no undo)
  setToolMode: (mode: 'hand' | 'select') => void;
  openInspector: (inspector: InspectorState) => void;
  closeInspector: (windowId: string) => void;
  setValidationIssues: (issues: ValidationIssue[]) => void;
  setAutoSaveStatus: (status: EditorState['autoSaveStatus']) => void;

  initWorkflow: (id: string, name: string, yaml?: string) => void;
}

// --- Factory function [H-5] ---

/**
 * Creates an independent editor store instance.
 * SF-7 TaskTemplateEditorView uses this with { scopedMode: true } to get
 * a store without phase creation or template stamping.
 */
export function createEditorStore(
  options: EditorStoreOptions = {}
): UseBoundStore<StoreApi<EditorState>> {
  const opts: Required<EditorStoreOptions> = {
    scopedMode: options.scopedMode ?? false,
    initialWorkflowId: options.initialWorkflowId ?? '',
    initialWorkflowName: options.initialWorkflowName ?? 'Untitled',
  };

  return create<EditorState>()((set, get) => {
    const undo = createUndoMiddleware(get, set);

    return {
      _options: opts,
      workflowId: opts.initialWorkflowId,
      workflowName: opts.initialWorkflowName,
      nodes: [],
      edges: [],
      collapsedGroups: {},
      actors: {},
      types: {},
      plugins: {},
      pluginInstances: {},
      stores: {},
      contextKeys: [],
      undoStack: [],
      redoStack: [],
      validationIssues: [],
      toolMode: 'hand',
      autoSaveStatus: 'clean',
      inspectors: [],
      isDirty: false,

      addNode: (node) => undo.withUndo((s) => ({ nodes: [...s.nodes, node] })),
      removeNodes: (nodeIds) => undo.withUndo((s) => ({
        nodes: s.nodes.filter(n => !nodeIds.includes(n.id)),
        edges: s.edges.filter(e => !nodeIds.includes(e.source) && !nodeIds.includes(e.target)),
      })),
      updateNodeData: (nodeId, data) => undo.withUndo((s) => ({
        nodes: s.nodes.map(n => n.id === nodeId ? { ...n, data: { ...n.data, ...data } } : n),
      })),

      addEdge: (conn) => undo.withUndo((s) => ({
        edges: addEdge({ ...conn, type: isHookHandle(conn.sourceHandle) ? 'hook' : 'data' }, s.edges),
      })),
      removeEdges: (edgeIds) => undo.withUndo((s) => ({
        edges: s.edges.filter(e => !edgeIds.includes(e.id)),
      })),

      onNodesChange: (changes) => { /* applyNodeChanges, withUndo on remove only */ },
      onEdgesChange: (changes) => { /* applyEdgeChanges, withUndo on remove only */ },
      onNodeDragStop: () => { /* push one undo snapshot for pre-drag state */ },

      undo: undo.undo,
      redo: undo.redo,

      toggleCollapse: (groupId) => undo.withUndo((s) => ({
        collapsedGroups: { ...s.collapsedGroups, [groupId]: !s.collapsedGroups[groupId] },
      })),
      isCollapsed: (groupId) => get().collapsedGroups[groupId] ?? false,

      stampTemplate: (templateId, position, templateData) => {
        if (opts.scopedMode) {
          console.warn('stampTemplate disabled in scopedMode');
          return;
        }
        // ... create template-group node + cloned children with _readOnly ...
      },
      detachTemplateGroup: (groupId) => {
        if (opts.scopedMode) {
          console.warn('detachTemplateGroup disabled in scopedMode');
          return;
        }
        // ... convert to editable independent nodes ...
      },

      setActors: (actors) => undo.withUndo(() => ({ actors })),
      updateActor: (id, actor) => undo.withUndo((s) => ({ actors: { ...s.actors, [id]: actor } })),
      removeActor: (id) => undo.withUndo((s) => {
        const { [id]: _, ...rest } = s.actors;
        return { actors: rest };
      }),

      loadFromYaml: (yaml) => { /* deserializeFromYaml → set state, clear undo/redo */ },
      serializeToYaml: () => { /* serializeToYaml(get()) */ return ''; },

      setToolMode: (mode) => {
        if (opts.scopedMode && mode === 'select') return; // no phase creation in scoped mode
        set({ toolMode: mode });
      },
      openInspector: (inspector) => set((s) => ({ inspectors: [...s.inspectors, inspector] })),
      closeInspector: (windowId) => set((s) => ({ inspectors: s.inspectors.filter(i => i.windowId !== windowId) })),
      setValidationIssues: (issues) => set({ validationIssues: issues }),
      setAutoSaveStatus: (status) => set({ autoSaveStatus: status }),

      initWorkflow: (id, name, yaml) => {
        set({ workflowId: id, workflowName: name, undoStack: [], redoStack: [], isDirty: false });
        if (yaml) get().loadFromYaml(yaml);
      },
    };
  });
}

// --- Default singleton for workflow editor ---

export const useEditorStore = createEditorStore();
```

**Key `scopedMode` behaviors [H-5]:**
- `stampTemplate()`: no-op with console warning
- `detachTemplateGroup()`: no-op with console warning
- `setToolMode('select')`: blocked (no phase creation)
- All other actions (addNode, removeNodes, updateNodeData, addEdge, undo/redo, collapse, serialization) work normally
- SF-7 components access the store by receiving the factory-created instance via React context or prop, NOT by importing the singleton

**5. selectors.ts — Memoized selectors**

```typescript
import type { EditorState } from './editorStore';

export const selectNodes = (s: EditorState) => s.nodes;
export const selectEdges = (s: EditorState) => s.edges;
export const selectCollapsedGroups = (s: EditorState) => s.collapsedGroups;
export const selectToolMode = (s: EditorState) => s.toolMode;
export const selectUndoAvailable = (s: EditorState) => s.undoStack.length > 0;
export const selectRedoAvailable = (s: EditorState) => s.redoStack.length > 0;
export const selectIsDirty = (s: EditorState) => s.isDirty;
export const selectAutoSaveStatus = (s: EditorState) => s.autoSaveStatus;
export const selectValidationIssues = (s: EditorState) => s.validationIssues;
export const selectInspectors = (s: EditorState) => s.inspectors;
export const selectActors = (s: EditorState) => s.actors;
export const selectOptions = (s: EditorState) => s._options;
```

NEVER use `.filter()`, `.map()`, or `[]` indexing inside a selector.

The **visible nodes** derivation happens in `EditorCanvas` via `useMemo`, NOT in a selector.

**6. serializeToYaml.ts / 7. deserializeFromYaml.ts / 8. autoLayout.ts** — Unchanged from original plan. See original STEP-47 sections 6-8.

**Acceptance Criteria:**
- Create a workflow in store, add nodes and edges, call `serializeToYaml()`, then `deserializeFromYaml()` on the result — nodes and edges match original (round-trip fidelity)
- Add 3 nodes + 2 edges → undo 5 times → store has zero nodes/edges → redo 5 times → all restored
- Import a YAML file with no `position` fields → all nodes positioned by autoLayout without overlap
- `stampTemplate()` creates a template-group with read-only children; `detachTemplateGroup()` converts them to editable
- Collapsing a group hides its children from visible nodes; expanding restores them at original positions
- **[H-5]** `createEditorStore({ scopedMode: true })` returns a functional store where `stampTemplate`, `detachTemplateGroup`, and Select tool mode are disabled
- **[H-5]** Two store instances created via `createEditorStore()` are fully independent — mutations to one do not affect the other
- **[C-2]** `yamlSchema.ts` header comment references `iriai_compose.schema` (NOT `iriai_compose.declarative.schema`)
- **[C-1]** BranchNode serialization preserves `switch_function` field and per-port `condition` fields on outputs

**Counterexamples:**
- DO NOT store React Flow viewport state (zoom, pan) in undo snapshots
- DO NOT use JSON.parse/JSON.stringify for snapshots — use structuredClone
- DO NOT mutate the undoStack or redoStack arrays directly
- DO NOT auto-generate new IDs during deserialization
- DO NOT put `.filter()` or `.map()` inside Zustand selectors
- DO NOT serialize template-group children individually — serialize as `$template_ref`
- **[H-5]** DO NOT make the singleton the only export — the factory function `createEditorStore` MUST be a named export
- **[H-5]** DO NOT share state between factory-created instances — each is fully independent
- **[C-2]** DO NOT reference `iriai_compose.declarative.schema` anywhere — the canonical path is `iriai_compose.schema`

**Citations:**
- [decision: D-SF6-2] Full snapshot undo/redo
- [decision: D-SF6-3] React Flow flat shape as canonical store
- [decision: D-SF6-5] Custom recursive dagre for auto-layout
- [decision: D-SF6-6] js-yaml for serialization; iriai_compose.schema path [C-2]
- [decision: D-SF6-7] Stamp-and-detach templates
- [decision: D-SF6-9] createEditorStore factory [H-5]
- [decision: D-U1] Phases use expand-to-real-nodes
- [decision: D-U2] Templates use same pattern
- [code: SF-1 plan — D-SF1-1 module at iriai_compose/schema/, C-2 canonical import]
- [code: SF-1 schema — WorkflowConfig, PhaseDefinition, NodeBase, Edge]

---

### STEP-48: React Flow Canvas Foundation + Connection Validation

**Objective:** Set up the ReactFlow canvas component with custom node/edge type registration, viewport controls, tool mode system (Hand = panOnDrag, Select = selection rectangle), and the isValidConnection callback implementing synchronous DFS cycle detection and port type compatibility checking.

**Requirement IDs:** REQ-13
**Journey IDs:** J-16, J-17

**Scope:**
| Path | Action |
|------|--------|
| `features/editor/canvas/EditorCanvas.tsx` | create |
| `features/editor/canvas/connectionValidator.ts` | create |
| `features/editor/canvas/canvasStyles.css` | create |
| `features/editor/nodes/nodeTypes.ts` | create |
| `features/editor/edges/edgeTypes.ts` | create |

**Instructions:**

**1. nodeTypes.ts — Node type registry (placeholder)**

Define at MODULE LEVEL (outside any component):

```typescript
import type { NodeTypes } from '@xyflow/react';

// Placeholder components — replaced in STEP-50/5
function AskNodePlaceholder({ data }: NodeProps) {
  return <div style={{ width: 260, minHeight: 120, background: '#f5f3ff', border: '2px solid #8b5cf6', borderRadius: 8 }}>{data.label || 'Ask'}</div>;
}
// Similar for BranchNodePlaceholder, PluginNodePlaceholder, PhaseContainerPlaceholder, TemplateGroupPlaceholder

export const nodeTypes: NodeTypes = {
  ask: AskNodePlaceholder,
  branch: BranchNodePlaceholder,
  plugin: PluginNodePlaceholder,
  phase: PhaseContainerPlaceholder,
  'template-group': TemplateGroupPlaceholder,
};
```

**2. edgeTypes.ts — Edge type registry (placeholder)**

```typescript
export const edgeTypes: EdgeTypes = {
  data: DataEdgePlaceholder,
  hook: HookEdgePlaceholder,
};
```

**3. connectionValidator.ts — Synchronous validation**

```typescript
export function createConnectionValidator(
  getNodes: () => Node[],
  getEdges: () => Edge[],
) {
  return function isValidConnection(connection: Connection): boolean {
    const { source, target, sourceHandle, targetHandle } = connection;
    if (source === target) return false;

    const edges = getEdges();
    const duplicate = edges.some(
      e => e.source === source && e.target === target
        && e.sourceHandle === sourceHandle && e.targetHandle === targetHandle
    );
    if (duplicate) return false;

    // Port type compatibility: hook↔data blocked
    const sourceIsHook = isHookHandle(sourceHandle);
    const targetIsHook = isHookHandle(targetHandle);
    if (sourceIsHook !== targetIsHook) return false;

    // Block connections TO read-only template children
    const nodes = getNodes();
    const targetNode = nodes.find(n => n.id === target);
    if (targetNode?.data?._readOnly) return false;

    // DFS cycle detection
    return !wouldCreateCycle(source, target, nodes, edges);
  };
}
```

**4. EditorCanvas.tsx — ReactFlow wrapper**

```tsx
export function EditorCanvas() {
  const nodes = useEditorStore(selectNodes);
  const edges = useEditorStore(selectEdges);
  const collapsedGroups = useEditorStore(selectCollapsedGroups);
  const toolMode = useEditorStore(selectToolMode);

  const visibleNodes = useMemo(() => {
    return nodes.filter(node => {
      if (!node.parentId) return true;
      let ancestorId: string | undefined = node.parentId;
      while (ancestorId) {
        if (collapsedGroups[ancestorId]) return false;
        const ancestor = nodes.find(n => n.id === ancestorId);
        ancestorId = ancestor?.parentId;
      }
      return true;
    });
  }, [nodes, collapsedGroups]);

  const visibleEdges = useMemo(() => {
    const visibleNodeIds = new Set(visibleNodes.map(n => n.id));
    return edges.filter(e => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target));
  }, [edges, visibleNodes]);

  return (
    <div className="editor-canvas" data-testid="editor-canvas" style={{ width: '100%', height: '100%' }}>
      <ReactFlow
        nodes={visibleNodes}
        edges={visibleEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeDragStop={onNodeDragStop}
        onConnect={onConnect}
        isValidConnection={isValidConnection}
        panOnDrag={toolMode === 'hand'}
        selectionOnDrag={toolMode === 'select'}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.1}
        maxZoom={2}
        defaultEdgeOptions={{ type: 'data' }}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="#d1d5db" />
        {visibleNodes.length === 0 && <EmptyStateHint />}
      </ReactFlow>
    </div>
  );
}
```

**5. canvasStyles.css** — dot grid, selection rings by node type, phase border styles, empty state hint.

**Acceptance Criteria:**
- Canvas renders with dot-grid and empty state hint when no nodes
- Cycle-creating connections rejected
- Hook↔data port connections rejected
- Connections TO read-only template children rejected
- Hand mode pans, Select mode draws selection rectangle
- Collapsed groups hide their children and internal edges

**Counterexamples:**
- DO NOT define `nodeTypes` or `edgeTypes` inside a component
- DO NOT push undo on every pixel of position change during drag
- DO NOT allow connections from a node to itself
- DO NOT render child nodes of collapsed groups

**Citations:**
- [decision: D-9] Hand vs Select tool modes
- [decision: D-SF6-4] Hybrid validation
- [decision: D-U1, D-U2] Expand-to-real-nodes for phases and templates
- [Context7: React Flow — parentId sub-flows, isValidConnection]

---

### STEP-49: Shared Node Primitives + CollapsedGroupCard

**Objective:** Build the foundational UI components consumed by all node types: NodeCard (260px base card with colored header bar), SocketPort (12px recessed circle with always-visible label), ActorSlot (12px recessed circle for role drag-drop), metadata display components, and CollapsedGroupCard (shared compact card used by both collapsed phases and collapsed template groups).

**Requirement IDs:** REQ-13
**Journey IDs:** J-16

**Scope:**
| Path | Action |
|------|--------|
| `features/editor/nodes/shared/NodeCard.tsx` | create |
| `features/editor/nodes/shared/SocketPort.tsx` | create |
| `features/editor/nodes/shared/ActorSlot.tsx` | create |
| `features/editor/nodes/shared/NodeSummary.tsx` | create |
| `features/editor/nodes/shared/ContextKeys.tsx` | create |
| `features/editor/nodes/shared/ArtifactKey.tsx` | create |
| `features/editor/nodes/shared/PromptPreview.tsx` | create |
| `features/editor/nodes/shared/SwitchFunctionLabel.tsx` | create |
| `features/editor/nodes/shared/StatusIndicator.tsx` | create |
| `features/editor/nodes/shared/ErrorBadge.tsx` | create |
| `features/editor/nodes/shared/CollapsedGroupCard.tsx` | create |

**Instructions:**

Components 1-7 and 9-10 (NodeCard, SocketPort, ActorSlot, NodeSummary, ContextKeys, ArtifactKey, PromptPreview, StatusIndicator, ErrorBadge) remain identical to the original plan — see original STEP-49 for full specifications. All are `React.memo` wrapped with `data-testid` attributes.

**8. SwitchFunctionLabel.tsx — Dual routing mode indicator [C-1]**

This component shows the BranchNode's routing mode on the card face. It must distinguish between switch_function routing and per-port condition routing.

```tsx
import React from 'react';

interface SwitchFunctionLabelProps {
  switchFunction?: string | null;
  hasPerPortConditions?: boolean;
}

export const SwitchFunctionLabel = React.memo<SwitchFunctionLabelProps>(
  function SwitchFunctionLabel({ switchFunction, hasPerPortConditions }) {
    // Determine which routing mode badge to show [C-1]
    const hasSwitchFn = switchFunction != null && switchFunction.trim() !== '';

    if (hasSwitchFn) {
      // Exclusive routing via switch_function
      const preview = switchFunction!.length > 30
        ? switchFunction!.slice(0, 27) + '...'
        : switchFunction;
      return (
        <div
          data-testid="switch-function-label"
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            background: 'rgba(245, 158, 11, 0.12)', borderRadius: 4,
            padding: '2px 8px', fontFamily: 'monospace', fontSize: '0.6875rem',
            color: '#92400e', maxWidth: '100%', overflow: 'hidden',
          }}
        >
          <span style={{ fontWeight: 700 }}>ƒ</span>
          <span
            data-testid="switch-function-label-preview"
            style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
          >
            switch({preview})
          </span>
        </div>
      );
    }

    if (hasPerPortConditions) {
      // Non-exclusive routing via per-port conditions [C-1]
      return (
        <div
          data-testid="switch-function-label-conditions"
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            background: 'rgba(245, 158, 11, 0.08)', borderRadius: 4,
            padding: '2px 8px', fontSize: '0.6875rem',
            color: '#92400e',
          }}
        >
          <span style={{ fontWeight: 600 }}>⑂</span>
          <span>per-port conditions</span>
        </div>
      );
    }

    // No routing configured
    return (
      <div
        data-testid="switch-function-label-empty"
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 4,
          background: 'rgba(245, 158, 11, 0.06)', borderRadius: 4,
          padding: '2px 8px', fontSize: '0.6875rem',
          color: '#9ca3af', fontStyle: 'italic',
        }}
      >
        no routing configured
      </div>
    );
  }
);
```

**11. CollapsedGroupCard.tsx** — Unchanged from original plan.

**Acceptance Criteria:**
- All 11 components have `data-testid` on root elements and are `React.memo` wrapped
- CollapsedGroupCard renders mode badge for phases, TEMPLATE badge for template groups
- CollapsedGroupCard shows expand ▶ button, node count, optional detach ⎘ button
- NodeCard renders at exactly 260px wide with 3px colored top border
- SocketPort renders 12px circle with always-visible label
- ActorSlot supports drag-drop with purple glow feedback
- **[C-1]** SwitchFunctionLabel shows "ƒ switch(...)" when switch_function is set, "⑂ per-port conditions" when per-port conditions exist, "no routing configured" when neither

**Counterexamples:**
- DO NOT use MiniTopologyPreview — it does not exist in this plan [D-U1, D-U2]
- DO NOT make SocketPort labels hover-only [D-49]
- DO NOT render ActorSlot as rectangular dashed box [D-51]
- DO NOT show output schema on NodeCard face [D-50]
- **[C-1]** DO NOT show "ƒ switch(...)" when switch_function is null/empty and per-port conditions are active

**Citations:**
- [decision: D-29/D-47] All nodes 260-280px rectangular cards
- [decision: D-49] All ports uniform 12px, always-visible labels
- [decision: D-U1] Phases use collapsed card, not thumbnail
- [decision: D-U2] Templates use same collapsed card pattern
- [decision: D-SF6-8] Dual routing model [C-1]

---

### STEP-50: Custom Node Components (Ask, Branch, Plugin) + TemplateGroup

**Objective:** Build the 3 atomic node components + the TemplateGroup collapsible container. Atomic nodes use STEP-49 primitives and are memoized components registered in `nodeTypes`. TemplateGroup is a React Flow group node that renders as CollapsedGroupCard when collapsed and as a green-bordered container with read-only children when expanded. **BranchNode card face indicates which routing mode is active [C-1].**

**Requirement IDs:** REQ-13
**Journey IDs:** J-16, J-18, J-19

**Scope:**
| Path | Action |
|------|--------|
| `features/editor/nodes/AskNode.tsx` | create |
| `features/editor/nodes/BranchNode.tsx` | create |
| `features/editor/nodes/PluginNode.tsx` | create |
| `features/editor/nodes/TemplateGroup.tsx` | create |
| `features/editor/nodes/nodeTypes.ts` | modify |

**Instructions:**

**1. AskNode, 3. PluginNode** — identical to original plan STEP-50 specifications.

**2. BranchNode.tsx — Amber Branch node with dual routing indicator [C-1]**

```tsx
function BranchNodeComponent({ id, data, selected }: NodeProps) {
  const isReadOnly = data._readOnly === true;

  // Determine routing mode from data [C-1]
  const hasSwitchFunction = data.switch_function != null && data.switch_function.trim() !== '';
  const hasPerPortConditions = !hasSwitchFunction && (data.outputs ?? []).some(
    (port: PortDefinition) => port.condition != null && port.condition.trim() !== ''
  );

  const outputs = data.outputs ?? [{ name: 'path_1' }, { name: 'path_2' }];

  return (
    <div data-testid={`branch-node-${id}`} data-type="branch">
      <NodeCard
        id={id}
        type="branch"
        name={data.label || data.name || 'Branch'}
        headerColor="#f59e0b"
        selected={selected}
        errorCount={data.ui?.validationErrors?.length}
      >
        <div style={{ opacity: isReadOnly ? 0.85 : 1, pointerEvents: isReadOnly ? 'none' : 'auto' }}>
          {/* Summary */}
          {data.summary && <NodeSummary text={data.summary} testId={`branch-node-${id}-summary`} />}

          {/* Context keys */}
          {data.context_keys?.length > 0 && <ContextKeys keys={data.context_keys} testId={`branch-node-${id}-context-keys`} />}

          {/* Routing mode indicator [C-1] */}
          <SwitchFunctionLabel
            switchFunction={data.switch_function}
            hasPerPortConditions={hasPerPortConditions}
          />

          {/* Output paths list — shows port names and routing info */}
          <div data-testid={`branch-node-${id}-paths-list`} style={{ marginTop: 4 }}>
            {outputs.map((output: PortDefinition) => (
              <div key={output.name} style={{
                display: 'flex', alignItems: 'center', gap: 4,
                padding: '2px 0', fontSize: '0.6875rem',
              }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#f59e0b', flexShrink: 0 }} />
                <span style={{ color: '#1e293b', fontWeight: 500 }}>{output.name}</span>
                {/* Show condition preview in per-port mode [C-1] */}
                {!hasSwitchFunction && output.condition && (
                  <span style={{
                    color: '#9ca3af', fontFamily: 'monospace', fontSize: '0.625rem',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    maxWidth: 120,
                  }}>
                    if {output.condition}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Multiple input ports (left) — data only */}
        {(data.inputs ?? [{ name: 'input' }]).map((input: PortDefinition) => (
          <SocketPort key={input.name} id={`${id}-${input.name}-in`} position="left" portType="data-in" label={input.name} />
        ))}

        {/* Output ports (right) — one per path */}
        {outputs.map((output: PortDefinition) => (
          <SocketPort key={output.name} id={`${id}-${output.name}-out`} position="right" portType="data-out" label={output.name} />
        ))}

        {/* Hook ports (bottom) */}
        <SocketPort id={`${id}-on_start-out`} position="bottom" portType="hook" label="on_start" />
        <SocketPort id={`${id}-on_end-out`} position="bottom" portType="hook" label="on_end" />
      </NodeCard>
    </div>
  );
}

export const BranchNode = React.memo(BranchNodeComponent, (prev, next) =>
  prev.data === next.data && prev.selected === next.selected
);
```

**Key [C-1] details:**
- Card face shows `SwitchFunctionLabel` with the appropriate mode indicator
- In switch_function mode: output paths show names only (the function decides routing)
- In per-port condition mode: output paths show name + truncated condition preview ("if data.approved")
- In unconfigured mode: "no routing configured" in muted italic

**4. TemplateGroup.tsx** — Unchanged from original plan.

**5. Update nodeTypes.ts** — Unchanged from original plan.

**Acceptance Criteria:**
- All original acceptance criteria from STEP-50 remain valid
- **[C-1]** BranchNode card face shows "ƒ switch(...)" when `switch_function` field is populated
- **[C-1]** BranchNode card face shows "⑂ per-port conditions" when `switch_function` is empty but output ports have `condition` values
- **[C-1]** BranchNode card face shows "no routing configured" when neither mode is active
- **[C-1]** In per-port condition mode, each output path row shows a truncated condition preview
- **[C-1]** Output port names on BranchNode match the `outputs[].name` values (these are the strings the switch_function returns)

**Counterexamples:**
- DO NOT use MiniTopologyPreview [D-U1, D-U2]
- DO NOT render Branch as diamond [D-47]
- DO NOT show actor slot on Branch or Plugin [D-28]
- DO NOT show output_schema on card face [D-50]
- DO NOT allow editing of _readOnly nodes [D-U3]
- DO NOT maintain a live link between template-group and library template [D-SF6-7]
- **[C-1]** DO NOT show "ƒ switch(...)" when switch_function is null — show the per-port condition indicator or empty state instead
- **[C-1]** DO NOT show per-port condition previews when switch_function IS set — the two modes are mutually exclusive

**Citations:**
- [decision: D-SF6-7] Stamp-and-detach templates
- [decision: D-SF6-8] Dual routing model [C-1]
- [decision: D-U2] Templates expand to real nodes
- [decision: D-U3] Read-only but inspectable
- [decision: D-47] All nodes rectangular cards
- [decision: D-50] Card face metadata
- [decision: D-28/D-46] Branch = programmatic switch, no actor
- [code: SF-1 plan — D-SF1-2 dual routing, D-SF1-28 switch_function]

---

### STEP-51: PhaseContainer Group Node + Collapse/Expand

**Objective:** Build PhaseContainer as a React Flow group node (type: 'phase') with mode-styled borders, collapsible to CollapsedGroupCard (compact card with metadata, no thumbnail), PhaseLabelBar (mode icon + name + collapse ▼/▶ + detach ⎘), LoopExitPorts, and proper parentId containment. Phases and template groups share the same collapse/expand mechanism via `collapsedGroups` in the store.

**Requirement IDs:** REQ-13, REQ-14
**Journey IDs:** J-17, J-21, J-6

**Scope:**
| Path | Action |
|------|--------|
| `features/editor/phases/PhaseContainer.tsx` | create |
| `features/editor/phases/PhaseLabelBar.tsx` | create |
| `features/editor/phases/LoopExitPorts.tsx` | create |
| `features/editor/nodes/nodeTypes.ts` | modify |

**Instructions:**

Unchanged from original plan. PhaseContainer uses `extent: 'parent'` on children. Border styles: sequential=`2px solid #64748b`, map=`3px double #14b8a6`, fold=`2px dotted #6366f1`, loop=`2px dashed #f59e0b`. Light tinted fill (4-6% opacity).

**Acceptance Criteria / Counterexamples / Citations:** Unchanged from original plan.

---

### STEP-52: Custom Edge Components (DataEdge + HookEdge)

Unchanged from original plan.

---

### STEP-53: PaintMenuBar + IconToolbar + NodePalette

Unchanged from original plan.

---

### STEP-54: Inspector Window System + Tether Lines

Unchanged from original plan.

---

### STEP-55: Node Inspectors (Ask, Branch, Plugin, Phase)

**Objective:** Build inspector content for all 4 node types + phase. Each renders inside InspectorWindow with type-colored titlebar. All field changes debounced 500ms → push undo snapshot → update node data. Read-only mode disables all fields when `inspector.readOnly === true`. **BranchInspector implements the dual routing model [C-1]: SwitchFunctionEditor for exclusive `switch_function` mode, and per-port condition editors in OutputPathsEditor for non-exclusive mode.**

**Requirement IDs:** REQ-13, REQ-14
**Journey IDs:** J-16, J-17, J-18

**Scope:**
| Path | Action |
|------|--------|
| `features/editor/inspectors/AskInspector.tsx` | create |
| `features/editor/inspectors/BranchInspector.tsx` | create |
| `features/editor/inspectors/PluginInspector.tsx` | create |
| `features/editor/inspectors/PhaseInspector.tsx` | create |
| `features/editor/inspectors/InspectorActions.tsx` | create |
| `features/editor/inspectors/PromptTemplateEditor.tsx` | create |
| `features/editor/inspectors/InlineRoleCreator.tsx` | create |
| `features/editor/inspectors/InlineOutputSchemaCreator.tsx` | create |
| `features/editor/inspectors/OutputPathsEditor.tsx` | create |
| `features/editor/inspectors/SwitchFunctionEditor.tsx` | create |
| `features/editor/inspectors/CodeEditor.tsx` | create |

**Instructions:**

All inspectors accept `readOnly: boolean` prop from InspectorWindow. When `true`, all form elements are disabled and InspectorActions is not rendered.

**AskInspector** (~280px, purple), **PluginInspector** (~280px, gray), **PhaseInspector** (mode-colored) — unchanged from original plan.

**CodeEditor** — unchanged from original plan (shared `@uiw/react-codemirror` wrapper, lazy-loaded).

**BranchInspector.tsx (~280px, amber titlebar) — Dual routing mode [C-1]**

BranchInspector implements the two mutually exclusive routing strategies defined by SF-1 (D-SF1-2, D-SF1-28). The inspector detects which mode is active based on the `switch_function` field and renders the appropriate editor.

```tsx
import React, { useCallback, useMemo } from 'react';
import { SwitchFunctionEditor } from './SwitchFunctionEditor';
import { OutputPathsEditor } from './OutputPathsEditor';
import { InspectorActions } from './InspectorActions';

interface BranchInspectorProps {
  nodeId: string;
  data: BranchNodeData;
  readOnly: boolean;
  onUpdateData: (patch: Partial<BranchNodeData>) => void;
}

export function BranchInspector({ nodeId, data, readOnly, onUpdateData }: BranchInspectorProps) {
  // Determine routing mode [C-1]
  const hasSwitchFunction = data.switch_function != null && data.switch_function.trim() !== '';

  // Switch between routing modes [C-1]
  const handleEnableSwitchFunction = useCallback(() => {
    // Entering switch_function mode: set switch_function to placeholder,
    // clear all per-port conditions (mutual exclusivity — D-SF1-28)
    const cleanedOutputs = (data.outputs ?? []).map(port => ({
      ...port,
      condition: undefined, // strip per-port conditions
    }));
    onUpdateData({
      switch_function: '# Return a port name string\nreturn "path_1"',
      outputs: cleanedOutputs,
    });
  }, [data.outputs, onUpdateData]);

  const handleClearSwitchFunction = useCallback(() => {
    // Leaving switch_function mode: clear switch_function, enable per-port conditions
    onUpdateData({ switch_function: null });
  }, [onUpdateData]);

  const handleSwitchFunctionChange = useCallback((value: string) => {
    onUpdateData({ switch_function: value });
  }, [onUpdateData]);

  return (
    <div data-testid={`branch-inspector-${nodeId}`}>
      {/* Summary field */}
      <label data-testid={`branch-inspector-${nodeId}-summary-label`}>Summary</label>
      <textarea
        data-testid={`branch-inspector-${nodeId}-summary-input`}
        value={data.summary ?? ''}
        onChange={(e) => onUpdateData({ summary: e.target.value })}
        disabled={readOnly}
        rows={2}
        placeholder="Describe this branch's purpose..."
      />

      {/* Context keys */}
      <label data-testid={`branch-inspector-${nodeId}-context-keys-label`}>Context Keys</label>
      <input
        data-testid={`branch-inspector-${nodeId}-context-keys-input`}
        value={(data.context_keys ?? []).join(', ')}
        onChange={(e) => onUpdateData({ context_keys: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })}
        disabled={readOnly}
        placeholder="key1, key2"
      />

      {/* Routing Mode Section [C-1] */}
      <div data-testid={`branch-inspector-${nodeId}-routing-section`} style={{ marginTop: 12, borderTop: '1px solid #e5e7eb', paddingTop: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
          <span style={{ fontWeight: 600, fontSize: '0.75rem', color: '#92400e' }}>Routing</span>
          {!readOnly && (
            <button
              data-testid={`branch-inspector-${nodeId}-routing-mode-toggle`}
              onClick={hasSwitchFunction ? handleClearSwitchFunction : handleEnableSwitchFunction}
              style={{
                fontSize: '0.625rem', color: '#6b7280', background: 'none',
                border: '1px solid #d1d5db', borderRadius: 4, padding: '2px 8px',
                cursor: 'pointer',
              }}
              title={hasSwitchFunction
                ? 'Switch to per-port conditions (non-exclusive routing)'
                : 'Switch to switch function (exclusive routing)'}
            >
              {hasSwitchFunction ? 'Use Per-Port Conditions' : 'Use Switch Function'}
            </button>
          )}
        </div>

        {/* Mode A: switch_function (exclusive routing) [C-1] */}
        {hasSwitchFunction && (
          <SwitchFunctionEditor
            nodeId={nodeId}
            value={data.switch_function!}
            onChange={handleSwitchFunctionChange}
            readOnly={readOnly}
            outputPortNames={(data.outputs ?? []).map(p => p.name)}
          />
        )}

        {/* Mode B: Per-port conditions (non-exclusive routing) [C-1]
            Shown when switch_function is null/empty */}
        {!hasSwitchFunction && (
          <div
            data-testid={`branch-inspector-${nodeId}-condition-mode-hint`}
            style={{ fontSize: '0.625rem', color: '#6b7280', marginBottom: 6, fontStyle: 'italic' }}
          >
            Each port evaluates its condition independently — all matching ports fire.
          </div>
        )}
      </div>

      {/* Output Paths Editor — always shown, adapts to routing mode [C-1] */}
      <OutputPathsEditor
        nodeId={nodeId}
        outputs={data.outputs ?? [{ name: 'path_1' }, { name: 'path_2' }]}
        routingMode={hasSwitchFunction ? 'switch_function' : 'per_port_condition'}
        readOnly={readOnly}
        onChange={(outputs) => onUpdateData({ outputs })}
      />

      {/* Merge function (orthogonal to routing mode) */}
      {(data.inputs ?? []).length > 1 && (
        <>
          <label>Merge Function</label>
          <CodeEditor
            data-testid={`branch-inspector-${nodeId}-merge-editor`}
            value={data.merge_function ?? ''}
            onChange={(val) => onUpdateData({ merge_function: val })}
            readOnly={readOnly}
            language="python"
            placeholder="# Merge multiple inputs\nreturn {'combined': list(inputs.values())}"
            height={80}
          />
        </>
      )}

      {/* NO actor field [D-28] */}

      {!readOnly && (
        <InspectorActions
          nodeId={nodeId}
          nodeType="branch"
          data-testid={`branch-inspector-${nodeId}-actions`}
        />
      )}
    </div>
  );
}
```

**SwitchFunctionEditor.tsx — CodeMirror for `switch_function` field [C-1]**

This component exclusively edits the `switch_function` field on BranchNode. It provides a CodeMirror Python editor with autocomplete hints for available output port names.

```tsx
import React from 'react';
import { CodeEditor } from './CodeEditor';

interface SwitchFunctionEditorProps {
  nodeId: string;
  value: string;
  onChange: (value: string) => void;
  readOnly: boolean;
  /** Names of the output ports — shown as autocomplete hints and used for
   *  validation (the function should return one of these strings) */
  outputPortNames: string[];
}

export function SwitchFunctionEditor({
  nodeId, value, onChange, readOnly, outputPortNames,
}: SwitchFunctionEditorProps) {
  return (
    <div data-testid={`branch-inspector-${nodeId}-switch-editor`}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 4 }}>
        <span style={{ fontSize: '0.6875rem', fontWeight: 600, color: '#92400e' }}>
          Switch Function
        </span>
        <span style={{ fontSize: '0.5625rem', color: '#9ca3af' }}>
          (exclusive — returns port name)
        </span>
      </div>

      <CodeEditor
        data-testid={`branch-inspector-${nodeId}-switch-function-code`}
        value={value}
        onChange={onChange}
        readOnly={readOnly}
        language="python"
        placeholder={`# Available: data (merged input)\n# Must return one of: ${outputPortNames.map(n => `"${n}"`).join(', ')}\nreturn "${outputPortNames[0] ?? 'path_1'}"`}
        height={120}
      />

      {/* Available port names reference */}
      <div
        data-testid={`branch-inspector-${nodeId}-switch-port-hints`}
        style={{
          marginTop: 4, padding: '4px 8px',
          background: 'rgba(245, 158, 11, 0.06)', borderRadius: 4,
          fontSize: '0.625rem', color: '#6b7280',
        }}
      >
        <span style={{ fontWeight: 600 }}>Return values → ports:</span>{' '}
        {outputPortNames.length > 0
          ? outputPortNames.map(n => `"${n}"`).join(', ')
          : <span style={{ fontStyle: 'italic' }}>Add output paths below</span>
        }
      </div>
    </div>
  );
}
```

**OutputPathsEditor.tsx — Named output ports with conditional per-port editors [C-1]**

This component manages the list of named output ports on a BranchNode. In `switch_function` mode, it shows only port names (the function decides routing). In `per_port_condition` mode, it additionally shows a condition editor for each port.

```tsx
import React, { useCallback } from 'react';
import { CodeEditor } from './CodeEditor';

interface OutputPathsEditorProps {
  nodeId: string;
  outputs: PortDefinition[];
  /** Which routing mode is active [C-1] */
  routingMode: 'switch_function' | 'per_port_condition';
  readOnly: boolean;
  onChange: (outputs: PortDefinition[]) => void;
}

export function OutputPathsEditor({
  nodeId, outputs, routingMode, readOnly, onChange,
}: OutputPathsEditorProps) {
  const handleAddPath = useCallback(() => {
    const newName = `path_${outputs.length + 1}`;
    onChange([...outputs, { name: newName, direction: 'output' as const }]);
  }, [outputs, onChange]);

  const handleRemovePath = useCallback((index: number) => {
    if (outputs.length <= 2) return; // BranchNode requires min 2 output ports
    onChange(outputs.filter((_, i) => i !== index));
  }, [outputs, onChange]);

  const handleRenamePath = useCallback((index: number, newName: string) => {
    const updated = outputs.map((p, i) => i === index ? { ...p, name: newName } : p);
    onChange(updated);
  }, [outputs, onChange]);

  const handleConditionChange = useCallback((index: number, condition: string) => {
    // Per-port condition editing [C-1] — only available when routingMode === 'per_port_condition'
    const updated = outputs.map((p, i) =>
      i === index ? { ...p, condition: condition || undefined } : p
    );
    onChange(updated);
  }, [outputs, onChange]);

  return (
    <div data-testid={`branch-inspector-${nodeId}-paths-list`} style={{ marginTop: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <span style={{ fontWeight: 600, fontSize: '0.6875rem', color: '#1e293b' }}>
          Output Paths
          <span style={{ fontWeight: 400, color: '#9ca3af', marginLeft: 4 }}>
            (min 2)
          </span>
        </span>
        {!readOnly && (
          <button
            data-testid={`branch-inspector-${nodeId}-add-path-btn`}
            onClick={handleAddPath}
            style={{ fontSize: '0.625rem', color: '#f59e0b', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 600 }}
          >
            + Add Path
          </button>
        )}
      </div>

      {outputs.map((output, index) => (
        <div
          key={index}
          data-testid={`branch-inspector-${nodeId}-path-${index}`}
          style={{
            padding: '6px 8px', marginBottom: 4,
            background: '#fffbeb', borderRadius: 4,
            border: '1px solid rgba(245, 158, 11, 0.2)',
          }}
        >
          {/* Port name row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#f59e0b', flexShrink: 0 }} />
            <input
              data-testid={`branch-inspector-${nodeId}-path-${index}-name`}
              value={output.name}
              onChange={(e) => handleRenamePath(index, e.target.value)}
              disabled={readOnly}
              style={{
                flex: 1, fontWeight: 500, fontSize: '0.75rem',
                border: 'none', background: 'transparent', padding: '2px 4px',
                color: '#1e293b',
              }}
              placeholder="path_name"
            />
            {!readOnly && outputs.length > 2 && (
              <button
                data-testid={`branch-inspector-${nodeId}-path-${index}-remove`}
                onClick={() => handleRemovePath(index)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#ef4444', fontSize: '0.75rem', padding: 0 }}
                aria-label={`Remove path ${output.name}`}
              >
                ×
              </button>
            )}
          </div>

          {/* Per-port condition editor [C-1] — only in per_port_condition mode */}
          {routingMode === 'per_port_condition' && (
            <div style={{ marginTop: 4 }}>
              <label style={{ fontSize: '0.5625rem', color: '#6b7280', display: 'block', marginBottom: 2 }}>
                Condition (Python → bool)
              </label>
              <input
                data-testid={`branch-inspector-${nodeId}-path-${index}-condition`}
                value={output.condition ?? ''}
                onChange={(e) => handleConditionChange(index, e.target.value)}
                disabled={readOnly}
                style={{
                  width: '100%', fontFamily: 'monospace', fontSize: '0.6875rem',
                  padding: '4px 6px', border: '1px solid #d1d5db', borderRadius: 4,
                  background: readOnly ? '#f9fafb' : '#fff',
                }}
                placeholder='data.verdict == "approved"'
              />
            </div>
          )}
        </div>
      ))}

      {outputs.length < 2 && (
        <div style={{ color: '#ef4444', fontSize: '0.625rem', marginTop: 4 }}>
          Branch requires at least 2 output paths
        </div>
      )}
    </div>
  );
}
```

**Key [C-1] behaviors summary:**

| Routing Mode | `switch_function` | Per-port `condition` | SwitchFunctionEditor | OutputPathsEditor |
|---|---|---|---|---|
| **Exclusive** | Python expression set | All `undefined` | Visible — CodeMirror for `switch_function` | Shows port names only, no condition inputs |
| **Non-exclusive** | `null` / empty string | Per-port Python predicates | Hidden | Shows port names + condition input per port |
| **Unconfigured** | `null` / empty string | All `undefined` | Hidden | Shows port names + empty condition inputs |

**Switching between modes:**
- "Use Switch Function" button: sets `switch_function` to placeholder code, clears all per-port `condition` values (D-SF1-28 mutual exclusivity)
- "Use Per-Port Conditions" button: sets `switch_function` to `null`, leaves conditions as-is for user to fill

**Acceptance Criteria:**
- AskInspector shows actor controls, prompt editor, inline schema builder
- **[C-1]** BranchInspector shows SwitchFunctionEditor (CodeMirror) when `switch_function` field is non-empty
- **[C-1]** BranchInspector hides SwitchFunctionEditor and shows per-port condition inputs when `switch_function` is null/empty
- **[C-1]** SwitchFunctionEditor edits the `switch_function` field specifically (not a generic code field)
- **[C-1]** SwitchFunctionEditor shows available output port names as hints below the editor
- **[C-1]** OutputPathsEditor creates named output ports — port names match what `switch_function` returns
- **[C-1]** OutputPathsEditor shows per-port condition inputs only in `per_port_condition` mode
- **[C-1]** "Use Switch Function" button clears all per-port conditions when activating switch_function mode
- **[C-1]** "Use Per-Port Conditions" button sets switch_function to null
- Phase mode change updates border immediately
- All fields disabled in read-only mode
- 500ms debounce on field changes
- NO actor field on BranchInspector [D-28]

**Counterexamples:**
- DO NOT put actor field on BranchInspector [D-28]
- DO NOT show output schema on card face [D-32]
- DO NOT eagerly load CodeMirror — lazy-load [RISK-91]
- DO NOT show action buttons in read-only inspectors [D-U3]
- **[C-1]** DO NOT allow both `switch_function` AND per-port `condition` values simultaneously — they are mutually exclusive per D-SF1-28
- **[C-1]** DO NOT show per-port condition inputs when switch_function is set
- **[C-1]** DO NOT show SwitchFunctionEditor when switch_function is null/empty
- **[C-1]** DO NOT silently preserve per-port conditions when switching to switch_function mode — clear them

**Citations:**
- [decision: D-23] Two-tier role editing
- [decision: D-26] Inline output schema
- [decision: D-28] Branch = switch, no actor
- [decision: D-SF6-8] Dual routing model [C-1]
- [decision: D-U3] Read-only inspectable
- [code: SF-1 plan — D-SF1-2 dual routing model, D-SF1-28 switch_function, expression evaluation contexts]

---

### STEP-56: Edge Inspector + CodeMirror Transform Editor

Unchanged from original plan.

---

### STEP-57: Client Validation + ValidationPanel + Server Integration

**Objective:** Build client-side structural validator, ValidationPanel UI, and wire Validate button to server. **Includes `invalid_switch_function_config` rule enforcing mutual exclusivity of `switch_function` and per-port `condition` on BranchNodes [C-1].**

**Requirement IDs:** REQ-15
**Journey IDs:** J-5, J-22

**Scope:**
| Path | Action |
|------|--------|
| `features/editor/validation/clientValidator.ts` | create |
| `features/editor/validation/ValidationPanel.tsx` | create |
| `features/editor/store/editorStore.ts` | modify |
| `features/editor/validation/validationTypes.ts` | modify |

**Instructions:**

`validateStructural(nodes, edges)` checks:
- `dangling_edge` — edge references nonexistent node
- `duplicate_node_id` — two nodes share same ID
- `missing_required_field` — Ask without actor, Branch < 2 output paths
- `cycle_detected` — DFS cycle in edges
- **[C-1] `invalid_switch_function_config`** — BranchNode has BOTH `switch_function` set AND any output port with `condition` set. Error message: `"Branch '{nodeId}' has both switch_function and per-port conditions — these are mutually exclusive. Use one routing strategy."` Path: `"nodes[{index}]"`.

```typescript
// In clientValidator.ts [C-1]

function validateBranchRouting(node: Node): ValidationIssue[] {
  if (node.type !== 'branch') return [];
  const issues: ValidationIssue[] = [];

  const hasSwitchFn = node.data.switch_function != null
    && node.data.switch_function.trim() !== '';
  const hasPerPortCondition = (node.data.outputs ?? []).some(
    (p: PortDefinition) => p.condition != null && p.condition.trim() !== ''
  );

  // D-SF1-28: mutual exclusivity
  if (hasSwitchFn && hasPerPortCondition) {
    issues.push({
      code: 'invalid_switch_function_config',
      path: `nodes.${node.id}`,
      message: `Branch '${node.data.label || node.id}' has both switch_function and per-port conditions — these are mutually exclusive. Use one routing strategy.`,
      nodeId: node.id,
      severity: 'error',
    });
  }

  // Min 2 output paths
  const outputCount = (node.data.outputs ?? []).length;
  if (outputCount < 2) {
    issues.push({
      code: 'missing_required_field',
      path: `nodes.${node.id}.outputs`,
      message: `Branch '${node.data.label || node.id}' requires at least 2 output paths (has ${outputCount}).`,
      nodeId: node.id,
      severity: 'error',
    });
  }

  return issues;
}
```

Store: debounce 500ms on mutations → run validator → update issues. ValidationPanel: floating XPWindow, "Go to →" scrolls to node. Manual Validate: serialize → POST /api/workflows/:id/validate → merge server results. **Server validation endpoint consumes JSON Schema from `iriai_compose.schema.WorkflowConfig.model_json_schema()` [C-2].**

**Acceptance Criteria:**
- Missing required fields → red badge within 500ms
- Validation panel lists issues with "Go to" links
- Manual Validate catches server-only errors
- Validation does NOT block saving
- **[C-1]** BranchNode with both `switch_function` and per-port `condition` → `invalid_switch_function_config` error shown
- **[C-1]** BranchNode with only `switch_function` → no routing conflict error
- **[C-1]** BranchNode with only per-port conditions → no routing conflict error

**Counterexamples:**
- Validation must NOT block saving [J-5 NOT]
- Client validation must NOT call server [D-SF6-4]
- **[C-1]** DO NOT silently allow both switch_function and per-port conditions to coexist — always flag as error

**Citations:**
- [decision: D-SF6-4] Hybrid validation
- [decision: D-SF6-8] Dual routing model [C-1]
- [code: SF-1 plan — D-SF1-28 validation rule: invalid_switch_function_config]

---

### STEP-58: Save/Auto-Save + Import/Export YAML

Unchanged from original plan.

---

### STEP-59: Selection Rectangle + Phase Creation from Selection

Unchanged from original plan.

---

### STEP-60: SF-7 Library Integration (Pickers + Promotion + Templates)

Unchanged from original plan.

---

### STEP-61: Keyboard Shortcuts + Accessibility + Responsive

Unchanged from original plan.

---

### STEP-62: WorkflowEditorPage Assembly + Integration

Unchanged from original plan.

---

## Architectural Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-87 | Serialization round-trip fidelity — template groups serialize as `$template_ref` but expand to full nodes on load. Any template library changes between save/load could cause mismatch. | high | Template ref stores template version hash. On load, if hash mismatches, show warning toast and stamp with the version available. Round-trip tests must cover template ref serialization. | STEP-47, STEP-58 |
| RISK-88 | React Flow performance with 60+ nodes in develop workflow when all groups expanded simultaneously | medium | React.memo on all nodes with custom comparator. Collapsed groups filter children from RF. Default-collapsed on load. Users expand one group at a time. | STEP-48, STEP-50, STEP-51 |
| RISK-89 | Inspector form state vs undo — debounced typing conflicts with undo stack | medium | On undo, flush pending debounce first. Inspectors re-read from store on snapshot change. | STEP-47, STEP-55 |
| RISK-90 | Read-only enforcement on template children — user might find ways to mutate via edge connections or phase creation | medium | Connection validator blocks edges TO _readOnly nodes. Phase creation (STEP-59) rejects selection containing _readOnly nodes. Store mutations check _readOnly before applying. | STEP-48, STEP-50, STEP-59 |
| RISK-91 | CodeMirror bundle size (~150KB gzipped) | low | Lazy-load via React.lazy(). Only loaded when first inspector opens. | STEP-55, STEP-56 |
| RISK-92 | SF-7 picker components not ready when SF-6 starts | medium | STEP-60 is last functional step. Use stub dropdowns during STEP-55 development. | STEP-60 |
| RISK-93 | Collapsed group sizing — when collapsed, group nodes need explicit dimensions for dagre layout and edge routing | medium | CollapsedGroupCard has fixed 260×52 dimensions. Store these in node data when collapsing. AutoLayout uses collapsed dimensions for collapsed groups. | STEP-47, STEP-51 |
| RISK-94 | BranchNode dual routing mode complexity [C-1] — switching modes clears data (switch_function or per-port conditions). User may accidentally lose work. | medium | Mode toggle button shows confirmation if data would be lost ("Switching to per-port conditions will clear the switch function. Continue?"). Undo immediately restores previous state. SwitchFunctionEditor and OutputPathsEditor validate independently within their mode. | STEP-55, STEP-57 |
| RISK-95 | Schema module path divergence [C-2] — if SF-1 changes `iriai_compose.schema` path, all validation endpoints and type mirrors break | low | D-SF6-6 documents the canonical path. `yamlSchema.ts` header has a machine-grep-able comment `// Canonical Python path: iriai_compose.schema`. Validation endpoint URL is a single constant in API client. | STEP-47, STEP-57 |
| RISK-96 | Store factory [H-5] — SF-7 TaskTemplateEditorView using `createEditorStore({ scopedMode: true })` may diverge from main editor behavior over time | low | Both stores share 100% of implementation code (same factory function). `scopedMode` only gates 3 specific actions. All other behavior is identical by construction. Tests exercise both modes. | STEP-47 |

## Journey Verifications

### J-16: Build a Workflow from Scratch
| Step | Verify | Type | data-testids |
|------|--------|------|-------------|
| 1. Empty canvas | Element [data-testid='editor-canvas'] visible with empty hint | browser | editor-canvas, editor-canvas-empty |
| 2. Drag Ask from palette | 260px purple card appears | browser | ask-node-{id}, editor-palette-ask |
| 3. Double-click node | Inspector opens with tether | browser | inspector-{id}, inspector-{id}-tether |
| 4. Configure | Card face updates with summary | browser | node-summary |
| 5. Draw edge | Edge connects two nodes | browser | edge-{id} |
| 6. Add Branch node | Amber card appears with "no routing configured" | browser | branch-node-{id}, switch-function-label-empty |
| 7. Configure switch_function | SwitchFunctionEditor CodeMirror visible, card shows "ƒ switch(...)" [C-1] | browser | branch-inspector-{id}-switch-editor, switch-function-label |
| 8. Ctrl+S | Green toast | browser | editor-toolbar-save |
| 9. Reload | All restored including switch_function | browser+api | editor-canvas |

### J-17: Create Nested Phases
| Step | Verify | Type | data-testids |
|------|--------|------|-------------|
| 1. Select tool | Tool button pressed | browser | editor-toolbar-select |
| 2. Draw rectangle | Phase created | browser | phase-{id} |
| 3. Set fold mode | Dotted indigo border | browser | phase-{id}-mode-badge |
| 4. Collapse phase | Children hidden, compact card shown with node count | browser | collapsed-group-{id}, collapsed-group-{id}-node-count |
| 5. Expand phase | Children restored at original positions | browser | phase-{id} |
| 6. Inner rectangle | Nested phase inside fold | browser | phase-{inner-id} |
| 7. Set loop | Dashed amber + dual exits | browser | phase-{inner-id} |

### J-18: Configure Ask with Inline Role
| Step | Verify | Type | data-testids |
|------|--------|------|-------------|
| 1. Click "+ Inline" | InlineRoleCreator expands | browser | ask-inspector-{id}-actor-inline-btn |
| 2. Fill fields | Actor slot fills purple | browser | actor-slot-assigned |
| 3. "Save to Library" | POST /api/roles succeeds | api+browser | promotion-dialog-save-btn |

### J-20: Template Stamp and Inspect
| Step | Verify | Type | data-testids |
|------|--------|------|-------------|
| 1. Drag template from palette | TemplateGroup created (expanded) with green dashed border | browser | template-group-{id} |
| 2. Children visible | Read-only nodes inside group at 85% opacity | browser | ask-node-{childId} |
| 3. Double-click child | Read-only inspector with 🔒 banner, all fields disabled | browser | inspector-{childId} |
| 4. Collapse group | Children hidden, compact green card with TEMPLATE badge | browser | collapsed-group-{id}-template-badge |
| 5. Expand group | Children restored | browser | template-group-{id}-header |
| 6. Click Detach ⎘ | Confirmation → children become editable, green border removed | browser | template-group-{id}-detach-btn |

### J-22: Editor Failure Paths
| Step | Verify | Type | data-testids |
|------|--------|------|-------------|
| 1. Type mismatch | Red dashed edge | browser | edge-{id} |
| 2. Auto-save failure | Orange dot on save | browser | editor-toolbar-save-dirty |
| 3. Ctrl+Z | Previous state restored (including collapse state) | browser | editor-toolbar-undo |
| 4. Import malformed | Red toast with line number | browser | import-confirm-dialog |
| 5. Branch dual routing conflict [C-1] | `invalid_switch_function_config` error in validation panel | browser | validation-panel, branch-inspector-{id}-routing-section |

## data-testid Registry

editor-canvas, editor-canvas-empty, editor-canvas-loading, editor-canvas-error, editor-toolbar, editor-toolbar-save, editor-toolbar-save-dirty, editor-toolbar-undo, editor-toolbar-redo, editor-toolbar-validate, editor-toolbar-export, editor-toolbar-hand, editor-toolbar-select, editor-toolbar-zoom-in, editor-toolbar-zoom-out, editor-toolbar-zoom-fit, editor-menu-file, editor-menu-edit, editor-menu-view, editor-palette, editor-palette-ask, editor-palette-branch, editor-palette-plugin, editor-palette-templates-section, editor-palette-roles-section, ask-node-{id}, ask-node-{id}-header, ask-node-{id}-actor-slot, ask-node-{id}-actor-slot-empty, ask-node-{id}-actor-slot-filled, ask-node-{id}-summary, ask-node-{id}-context-keys, ask-node-{id}-artifact-key, ask-node-{id}-prompt-preview, ask-node-{id}-error-badge, branch-node-{id}, branch-node-{id}-header, branch-node-{id}-summary, branch-node-{id}-context-keys, branch-node-{id}-switch-label, branch-node-{id}-paths-list, switch-function-label, switch-function-label-preview, switch-function-label-conditions, switch-function-label-empty, plugin-node-{id}, plugin-node-{id}-header, plugin-node-{id}-status, phase-{id}, phase-{id}-header, phase-{id}-mode-badge, phase-{id}-collapse-btn, phase-{id}-detach-btn, phase-{id}-name, collapsed-group-{id}, collapsed-group-{id}-expand-btn, collapsed-group-{id}-mode-badge, collapsed-group-{id}-template-badge, collapsed-group-{id}-node-count, collapsed-group-{id}-detach-btn, template-group-{id}, template-group-{id}-header, template-group-{id}-collapse-btn, template-group-{id}-detach-btn, port-{nodeId}-{portName}, port-{nodeId}-{portName}-label, edge-{id}, edge-{id}-label, edge-{id}-transform-icon, inspector-{elementId}, inspector-{elementId}-titlebar, inspector-{elementId}-close, inspector-{elementId}-tether, inspector-{elementId}-readonly-banner, ask-inspector-{id}-actor-dropdown, ask-inspector-{id}-actor-inline-btn, ask-inspector-{id}-actor-fullEditor-btn, ask-inspector-{id}-prompt-editor, ask-inspector-{id}-output-schema, ask-inspector-{id}-context-keys-input, ask-inspector-{id}-artifact-key-input, ask-inspector-{id}-summary-input, ask-inspector-{id}-save-to-library-btn, ask-inspector-{id}-delete-btn, branch-inspector-{id}, branch-inspector-{id}-summary-input, branch-inspector-{id}-summary-label, branch-inspector-{id}-context-keys-input, branch-inspector-{id}-context-keys-label, branch-inspector-{id}-routing-section, branch-inspector-{id}-routing-mode-toggle, branch-inspector-{id}-switch-editor, branch-inspector-{id}-switch-function-code, branch-inspector-{id}-switch-port-hints, branch-inspector-{id}-condition-mode-hint, branch-inspector-{id}-merge-editor, branch-inspector-{id}-paths-list, branch-inspector-{id}-path-{index}, branch-inspector-{id}-path-{index}-name, branch-inspector-{id}-path-{index}-condition, branch-inspector-{id}-path-{index}-remove, branch-inspector-{id}-add-path-btn, branch-inspector-{id}-actions, branch-inspector-{id}-delete-btn, plugin-inspector-{id}-type-picker, plugin-inspector-{id}-config-form, plugin-inspector-{id}-delete-btn, phase-inspector-{id}-mode-select, phase-inspector-{id}-mode-config, phase-inspector-{id}-name-input, phase-inspector-{id}-save-template-btn, phase-inspector-{id}-detach-btn, phase-inspector-{id}-ungroup-btn, phase-inspector-{id}-delete-btn, edge-inspector-{id}, edge-inspector-{id}-transform-editor, edge-inspector-{id}-input-type, edge-inspector-{id}-output-type, edge-inspector-{id}-save-btn, edge-inspector-{id}-cancel-btn, validation-panel, validation-panel-issue-{index}, validation-panel-issue-{index}-goto, save-template-dialog, save-template-dialog-name, save-template-dialog-save-btn, import-confirm-dialog, import-confirm-dialog-confirm-btn, promotion-dialog, promotion-dialog-name, promotion-dialog-save-btn, selection-rectangle

## Cross-SF Interfaces

### SF-5 → SF-6 (Consumed by Editor)
- **Auth:** useAuth() hook, authenticated API client
- **Shell:** ExplorerLayout mounts editor in ContentArea
- **API:** GET/PUT /api/workflows/:id, POST /api/workflows/:id/validate
- **Components:** XPButton, Window, Card, Input, Toast, ModalPortal, ConfirmDialog
- **CSS:** windows-xp.css variables, BEM conventions

### SF-1 → SF-6 (Schema Contract) [C-2]
- **JSON Schema:** Generated by `iriai_compose.schema.WorkflowConfig.model_json_schema()` — canonical import path is `iriai_compose.schema` (NOT `iriai_compose.declarative.schema`)
- **TypeScript Types:** `yamlSchema.ts` mirrors SF-1 Pydantic models from `iriai_compose.schema`
- **Template refs:** `$template_ref` in YAML maps to TemplateGroup on canvas
- **BranchNode contract [C-1]:** `switch_function` field (Python expression → port name string), `PortDefinition.condition` field (Python predicate → bool), mutually exclusive per D-SF1-28

### SF-7 → SF-6 (Library Pickers)
- **RolePicker:** `{ assignedRole, onDrop, onCreateInline }` — GET /api/roles
- **SchemaPicker:** `{ value, onSelect }` — GET /api/schemas
- **PluginPicker:** `{ value, onSelect }` — GET /api/plugins
- **TemplateBrowser:** `{ templates[], onDrag }` — GET /api/templates → stamp-and-detach

### SF-6 → SF-7 (Editor Mutations)
- **onPromoteRole(inlineRole):** POST /api/roles → returns ID
- **onPromoteSchema(inlineSchema):** POST /api/schemas → returns ID
- **onSaveTemplate(selectedNodes, edges, ioInterface):** POST /api/templates → returns ID

### SF-6 → SF-7 (Store Factory) [H-5]
- **`createEditorStore(options?)`:** Exported factory function from `store/editorStore.ts`
- **SF-7 usage:** `const useTemplateStore = createEditorStore({ scopedMode: true })` — creates independent store for TaskTemplateEditorView
- **Scoped mode gates:** `stampTemplate`, `detachTemplateGroup`, Select tool mode — all disabled
- **Shared behavior:** All other actions (addNode, removeNodes, updateNodeData, addEdge, undo/redo, collapse, serialization) work identically

## Revision Log

| Rev | Change | Decision | Feedback |
|-----|--------|----------|----------|
| R1-1 | BranchInspector dual routing: switch_function (exclusive) vs per-port condition (non-exclusive) | D-SF6-8 | [C-1] |
| R1-2 | SwitchFunctionEditor explicitly edits `switch_function` field with port name hints | D-SF6-8 | [C-1] |
| R1-3 | OutputPathsEditor shows per-port condition inputs when switch_function is null | D-SF6-8 | [C-1] |
| R1-4 | SwitchFunctionLabel shows routing mode indicator on BranchNode card face | D-SF6-8 | [C-1] |
| R1-5 | Client validator adds `invalid_switch_function_config` rule | D-SF6-8 | [C-1] |
| R1-6 | Export `createEditorStore(options?)` factory from editorStore.ts | D-SF6-9 | [H-5] |
| R1-7 | Confirmed `iriai_compose.schema` as canonical import (not `.declarative.schema`) | D-SF6-6 updated | [C-2] |
| R1-8 | yamlSchema.ts header comments reference correct module path | D-SF6-6 updated | [C-2] |
| R1-9 | Added RISK-94 (dual routing mode data loss), RISK-95 (schema path), RISK-96 (store factory) | — | [C-1, C-2, H-5] |

---


---