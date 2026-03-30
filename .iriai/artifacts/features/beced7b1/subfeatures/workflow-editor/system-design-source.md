<!-- SF: workflow-editor -->
<section id="sf-workflow-editor" class="subfeature-section">
    <h2>SF-6 Workflow Editor &amp; Canvas</h2>
    <div class="provenance">Subfeature: <code>workflow-editor</code></div>

    <h3>Overview</h3>
    <div class="overview-text">SF-6 is the React Flow workflow editor mounted inside the accepted `tools/compose` application contract (`tools/compose/frontend` + `tools/compose/backend`). PostgreSQL 15 plus Alembic back only the five SF-5 foundation tables — `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates` — with no SQLite, no plugin-management tables, and no `workflow_entity_refs` at the foundation layer. SF-5 exposes workflow mutation hooks (create/update/delete lifecycle events) that SF-7 subscribes to for reference-index synchronization; the editor&#x27;s save and auto-save paths flow through SF-5 CRUD and validate endpoints only and carry no write dependency on `workflow_entity_refs` or SF-7 reference-index endpoints. The editor keeps a flat React Flow node and edge array as its internal Zustand store, but save, load, export, and validation always round-trip through the canonical nested YAML contract: WorkflowConfig.phases[] with per-phase nodes, children, and cross-phase edges. Hook wiring is serialized only as ordinary source and target edges whose hook-versus-data meaning is inferred from the source port container; there is no separate serialized hooks section and no persisted port_type. GET /api/schema/workflow remains the canonical schema source, explicit saves append immutable workflow_versions rows, idle auto-save updates the draft workflow row, and the editor does not assume foundation-level plugin CRUD, SQLite storage, or workflow_entity_refs indexing. BranchNode is standardized on the D-GR-35 per-port non-exclusive fan-out model across editor, schema, runner, and migration artifacts: each entry in the dict-keyed paths map carries its own condition expression string evaluated independently at runtime, and multiple paths can fire if their conditions are met. There is no node-level condition_type or condition field. output_field mode is fully removed from the BranchNode schema. switch_function is rejected. merge_function is valid for multi-input gather. Each path key becomes an output handle ID on the canvas and an edge source port name in YAML. Service ID aliases: sf1-backend = compose-backend (tools/compose/backend, schema/workflow API layer); sf5-shell = compose-frontend authenticated shell (SF-5, tools/compose/frontend); sf7-library = SF-7 library surface of compose-backend owning workflow_entity_refs.</div>

    <h3>Services</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Name</th><th>Kind</th><th>Description</th><th>Technology</th><th>Port</th><th>Journeys</th>
        </tr></thead>
        <tbody><tr>
            <td><code>SVC-69</code></td>
            <td><strong>WorkflowEditorPage</strong></td>
            <td><code>frontend</code></td>
            <td>Top-level route component at /workflows/:id/edit. Mounts canvas, toolbar, palette, inspectors, and validation panel inside the authenticated shell.</td>
            <td><code>React 18</code></td>
            <td>—</td>
            <td>J-16, J-22</td>
        </tr><tr>
            <td><code>SVC-70</code></td>
            <td><strong>ValidationPanel</strong></td>
            <td><code>frontend</code></td>
            <td>Floating panel listing structural and server-side validation issues with severity badges and go-to actions that focus the offending node or edge.</td>
            <td><code>React</code></td>
            <td>—</td>
            <td>J-22</td>
        </tr><tr>
            <td><code>SVC-71</code></td>
            <td><strong>EditorCanvas</strong></td>
            <td><code>frontend</code></td>
            <td>React Flow wrapper that renders visible nodes and edges, filters collapsed children, wires nodeTypes and edgeTypes, and derives hook-versus-data edge visuals from resolved source handles on the dot-grid canvas.</td>
            <td><code>React, @xyflow/react</code></td>
            <td>—</td>
            <td>J-16, J-17, J-20, J-22</td>
        </tr><tr>
            <td><code>SVC-72</code></td>
            <td><strong>SF-6 Canvas Primitives</strong></td>
            <td><code>frontend</code></td>
            <td>Canvas-only primitives owned by SF-6, primarily CollapsedGroupCard for collapsed phases and template groups.</td>
            <td><code>React</code></td>
            <td>—</td>
            <td>J-17, J-20</td>
        </tr><tr>
            <td><code>SVC-73</code></td>
            <td><strong>AskFlowNode</strong></td>
            <td><code>frontend</code></td>
            <td>Thin React Flow adapter wrapping SF-7&#x27;s AskNodePrimitive. Generates input and output Handles from dict-keyed ports, adds selection styling, and forwards all visual rendering to SF-7.</td>
            <td><code>React, @xyflow/react</code></td>
            <td>—</td>
            <td>J-16, J-18, J-20</td>
        </tr><tr>
            <td><code>SVC-74</code></td>
            <td><strong>BranchFlowNode</strong></td>
            <td><code>frontend</code></td>
            <td>Thin React Flow adapter wrapping SF-7&#x27;s BranchNodePrimitive. Generates one output Handle per entry in node.data.paths and uses the path key as both the Handle ID and serialized edge source port name. Displays per-port condition expression summary on each path handle and optional merge_function summary for gather; never shows switch_function, never shows output_field, and never exposes a node-level condition_type. Supports non-exclusive fan-out where multiple output handles can fire independently if their per-port conditions are met.</td>
            <td><code>React, @xyflow/react</code></td>
            <td>—</td>
            <td>J-16, J-20, J-22</td>
        </tr><tr>
            <td><code>SVC-75</code></td>
            <td><strong>PluginFlowNode</strong></td>
            <td><code>frontend</code></td>
            <td>Thin React Flow adapter wrapping SF-7&#x27;s PluginNodePrimitive. Generates Handles from dict-keyed inputs and outputs and delegates visual rendering to SF-7. Plugin nodes store workflow-local plugin_ref keys and inline plugin_config only; they do not depend on /api/plugins or foundation-managed plugin rows.</td>
            <td><code>React, @xyflow/react</code></td>
            <td>—</td>
            <td>J-16, J-20</td>
        </tr><tr>
            <td><code>SVC-76</code></td>
            <td><strong>TemplateGroup</strong></td>
            <td><code>frontend</code></td>
            <td>React Flow group node with green dashed border. Collapsed mode renders CollapsedGroupCard with template metadata; expanded mode renders stamped read-only child nodes.</td>
            <td><code>React, @xyflow/react</code></td>
            <td>—</td>
            <td>J-20</td>
        </tr><tr>
            <td><code>SVC-77</code></td>
            <td><strong>PhaseContainer</strong></td>
            <td><code>frontend</code></td>
            <td>React Flow group node for sequential, map, fold, and loop phases. Supports collapse and expand, nested children, and loop exit ports condition_met and max_exceeded.</td>
            <td><code>React, @xyflow/react</code></td>
            <td>—</td>
            <td>J-17</td>
        </tr><tr>
            <td><code>SVC-78</code></td>
            <td><strong>Edge Components</strong></td>
            <td><code>frontend</code></td>
            <td>DataEdge and HookEdge render typed data-flow and fire-and-forget hook connections. Edge kind is reconstructed from the resolved source port container rather than a persisted port_type; data edges surface type labels and mismatch warnings while hook edges stay dashed and unlabeled.</td>
            <td><code>React, @xyflow/react</code></td>
            <td>—</td>
            <td>J-16, J-22</td>
        </tr><tr>
            <td><code>SVC-79</code></td>
            <td><strong>Toolbar</strong></td>
            <td><code>frontend</code></td>
            <td>Paint-style menu bar and icon toolbar for save, undo, redo, validate, export, tool mode, and zoom controls.</td>
            <td><code>React</code></td>
            <td>—</td>
            <td>J-16, J-17, J-22</td>
        </tr><tr>
            <td><code>SVC-80</code></td>
            <td><strong>NodePalette + RolePalette</strong></td>
            <td><code>frontend</code></td>
            <td>Right-side drag source for Ask, Branch, Plugin, templates, and role chips. Dropping a Branch creates a node with two starter paths (keyed path_1 and path_2) each carrying a blank per-port condition expression, plus an empty inputs dict. Dropping a Plugin creates a node with a blank workflow-local plugin_ref and inline config placeholder rather than selecting a persisted plugin entity.</td>
            <td><code>React</code></td>
            <td>—</td>
            <td>J-16, J-20</td>
        </tr><tr>
            <td><code>SVC-81</code></td>
            <td><strong>Inspector Window System</strong></td>
            <td><code>frontend</code></td>
            <td>Portal-based manager for draggable XP-style inspectors with tether lines to canvas elements. Supports multiple inspectors, z-ordering, and read-only mode for template children.</td>
            <td><code>React, Portal</code></td>
            <td>—</td>
            <td>J-16, J-18, J-20</td>
        </tr><tr>
            <td><code>SVC-82</code></td>
            <td><strong>Node Inspectors</strong></td>
            <td><code>frontend</code></td>
            <td>Inspector content for Ask, Branch, Plugin, Phase, and Edge editing. Inspector field constraints and defaults are hydrated from GET /api/schema/workflow while keeping hand-authored XP layouts. BranchInspector edits per-port condition expressions in the named paths dict and optional merge_function for multi-input gather; each path row shows a name field and a condition expression editor. It never exposes switch_function, routing-mode toggles, output_field mode, or node-level condition_type or condition fields. PluginInspector edits a workflow-local plugin_ref plus inline plugin_config and never depends on a plugin registry API.</td>
            <td><code>React, @uiw/react-codemirror</code></td>
            <td>—</td>
            <td>J-16, J-18, J-20, J-22</td>
        </tr><tr>
            <td><code>SVC-83</code></td>
            <td><strong>Editor Dialogs</strong></td>
            <td><code>frontend</code></td>
            <td>Dialogs for import confirmation, inline-to-library promotion, and save-as-template flows.</td>
            <td><code>React</code></td>
            <td>—</td>
            <td>J-18, J-20, J-22</td>
        </tr><tr>
            <td><code>SVC-84</code></td>
            <td><strong>SelectionRectangle</strong></td>
            <td><code>frontend</code></td>
            <td>Marching-ants selection rectangle active in Select mode. Creates phases from enclosed editable nodes that share a parent boundary.</td>
            <td><code>React</code></td>
            <td>—</td>
            <td>J-17</td>
        </tr><tr>
            <td><code>SVC-85</code></td>
            <td><strong>User / Browser</strong></td>
            <td><code>external</code></td>
            <td>End user authoring workflows in the browser.</td>
            <td><code></code></td>
            <td>—</td>
            <td>J-16, J-17, J-18, J-20, J-22</td>
        </tr><tr>
            <td><code>SVC-86</code></td>
            <td><strong>editorStore</strong></td>
            <td><code>service</code></td>
            <td>Zustand single source of truth for flat React Flow nodes and edges, registries, collapse state, undo and redo stacks, inspectors, dirty state, and all editor mutations. Branch nodes store dict-keyed paths where each path entry contains its own per-port condition expression string; Ask and Plugin nodes store dict-keyed inputs, outputs, and hooks. No node-level condition_type or condition field is stored for Branch nodes.</td>
            <td><code>Zustand, TypeScript</code></td>
            <td>—</td>
            <td>J-16, J-17, J-18, J-20, J-22</td>
        </tr><tr>
            <td><code>SVC-87</code></td>
            <td><strong>undoMiddleware</strong></td>
            <td><code>service</code></td>
            <td>Higher-order mutation wrapper that snapshots workflow state with structuredClone before structural edits and caps undo and redo depth at 50 entries.</td>
            <td><code>TypeScript, structuredClone</code></td>
            <td>—</td>
            <td>J-16, J-22</td>
        </tr><tr>
            <td><code>SVC-88</code></td>
            <td><strong>selectors</strong></td>
            <td><code>service</code></td>
            <td>Stable Zustand selector helpers that avoid creating new array or object references inside selector bodies.</td>
            <td><code>TypeScript</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-89</code></td>
            <td><strong>Serialization Module</strong></td>
            <td><code>service</code></td>
            <td>Bidirectional conversion between flat React Flow nodes and edges and nested WorkflowConfig YAML trees using phases[].nodes and phases[].children. Hook wiring serializes as ordinary dot-notation edges whose hook-versus-data meaning is inferred from the source port container, so no serialized port_type is emitted. Branch nodes serialize dict-keyed paths where each path entry carries its own per-port condition expression string; each path key becomes an output Handle ID on canvas and an edge source port name in YAML. No node-level condition_type or condition field is emitted for Branch nodes. Template groups serialize as $template_ref blocks.</td>
            <td><code>js-yaml, TypeScript</code></td>
            <td>—</td>
            <td>J-16, J-20, J-22</td>
        </tr><tr>
            <td><code>SVC-90</code></td>
            <td><strong>autoLayout</strong></td>
            <td><code>service</code></td>
            <td>Recursive dagre layout for nested phases. Lays out leaf children first, then computes parent bounds and positions collapsed groups as fixed-size nodes.</td>
            <td><code>@dagrejs/dagre</code></td>
            <td>—</td>
            <td>J-16</td>
        </tr><tr>
            <td><code>SVC-91</code></td>
            <td><strong>workflowSchemaAdapters</strong></td>
            <td><code>service</code></td>
            <td>Runtime schema cache and TypeScript adapter layer built from GET /api/schema/workflow. Local interfaces are projections of the backend JSON Schema for inspector layout, defaults, and client validation, not a competing static source of truth.</td>
            <td><code>TypeScript, JSON Schema</code></td>
            <td>—</td>
            <td>J-16, J-22</td>
        </tr><tr>
            <td><code>SVC-92</code></td>
            <td><strong>clientValidator</strong></td>
            <td><code>service</code></td>
            <td>Debounced structural validation that detects dangling edges, duplicate IDs, cycles, missing required fields, BranchNode paths with blank or missing per-port condition expressions, too few branch paths (minimum 2), path-handle mismatches, and type mismatches between connected ports. Also flags stale BranchNode fields (condition_type, node-level condition, switch_function, output_field) as errors. Hook edges are identified from source-port container resolution rather than persisted port_type. Type mismatch stays a warning; invalid branch structure is an error.</td>
            <td><code>TypeScript</code></td>
            <td>—</td>
            <td>J-22</td>
        </tr><tr>
            <td><code>SVC-93</code></td>
            <td><strong>connectionValidator</strong></td>
            <td><code>service</code></td>
            <td>Synchronous isValidConnection callback for self-loop, duplicate-edge, read-only-target, and cycle checks during drag. It does not decide Branch runtime routing and does not block fan-out connections.</td>
            <td><code>TypeScript</code></td>
            <td>—</td>
            <td>J-16, J-22</td>
        </tr><tr>
            <td><code>SVC-94</code></td>
            <td><strong>Editor Hooks</strong></td>
            <td><code>service</code></td>
            <td>useAutoSave, useKeyboardShortcuts, and useDragAndDrop for idle auto-save, canvas-scoped commands, and palette and role drag behavior.</td>
            <td><code>React hooks</code></td>
            <td>—</td>
            <td>J-16, J-22</td>
        </tr><tr>
            <td><code>SVC-95</code></td>
            <td><strong>compose-backend</strong></td>
            <td><code>service</code></td>
            <td>FastAPI backend at tools/compose/backend/. Serves workflow CRUD, workflow versioning, validation, export, runtime schema delivery, and the SF-7 role/schema/template/tool routes consumed by the editor. Persists only the SF-5 foundation tables `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`; plugin keys remain workflow-local YAML data and workflow_entity_refs expansion is owned by SF-7 as a downstream extension.</td>
            <td><code>Python 3.11, FastAPI, SQLAlchemy 2.x, Alembic, auth-python</code></td>
            <td>8000</td>
            <td>J-16, J-18, J-20, J-22</td>
        </tr><tr>
            <td><code>SVC-96</code></td>
            <td><strong>compose-frontend</strong></td>
            <td><code>frontend</code></td>
            <td>React 18 + Vite SPA at tools/compose/frontend/. Hosts the Explorer shell, auth-react providers, shared XP chrome, and the /workflows/{id}/edit route that mounts WorkflowEditorPage.</td>
            <td><code>React 18, Vite, auth-react, React Router</code></td>
            <td>—</td>
            <td>J-16, J-17, J-18, J-20, J-22</td>
        </tr><tr>
            <td><code>SVC-97</code></td>
            <td><strong>compose-db</strong></td>
            <td><code>database</code></td>
            <td>PostgreSQL 15 database managed by Alembic for compose-backend. SF-5 foundation is limited to exactly five tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`. SQLite, plugin-management tables, tool tables, and workflow_entity_refs are not part of the SF-5 foundation slice; workflow_entity_refs is a SF-7 extension table added in a separate Alembic migration.</td>
            <td><code>PostgreSQL 15, Alembic</code></td>
            <td>—</td>
            <td>J-16, J-18, J-20, J-22</td>
        </tr><tr>
            <td><code>SVC-98</code></td>
            <td><strong>SF-7 Node Primitives</strong></td>
            <td><code>external</code></td>
            <td>Pure React visual primitives shared between SF-6 and SF-7. Exports AskNodePrimitive, BranchNodePrimitive, PluginNodePrimitive, NodePortDot, EdgeTypeLabel, and ActorSlot. BranchNodePrimitive receives paths (each path entry includes a per-port conditionSummary string) and optional mergeFunctionSummary; there is no SwitchFunctionLabel, no node-level conditionType prop, and no output_field rendering. Per-port condition summaries are rendered on each path handle.</td>
            <td><code>React</code></td>
            <td>—</td>
            <td>J-16, J-20</td>
        </tr><tr>
            <td><code>SVC-99</code></td>
            <td><strong>compose-backend (schema/workflow API layer)</strong></td>
            <td><code>service</code></td>
            <td>Alias for the tools/compose/backend FastAPI service as referenced in API endpoints and call-path steps (sf1-backend). SF-1 owns the WorkflowConfig schema that drives GET /api/schema/workflow, hence the naming. Persists only the five SF-5 foundation tables on PostgreSQL 15 via Alembic — no SQLite, no workflow_entity_refs, no plugin-management tables. Fires workflow mutation hooks (create/update/delete lifecycle events) that SF-7 subscribes to for reference-index synchronization; the editor interacts only with the direct CRUD/validate/schema endpoints.</td>
            <td><code>Python 3.11, FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL 15</code></td>
            <td>8000</td>
            <td>J-16, J-17, J-18, J-20, J-22</td>
        </tr><tr>
            <td><code>SVC-100</code></td>
            <td><strong>compose-frontend shell (SF-5)</strong></td>
            <td><code>frontend</code></td>
            <td>The tools/compose/frontend authenticated shell provided by the SF-5 foundation. Supplies auth context (auth-react), XP chrome, Explorer sidebar with Workflows/Roles/Schemas/Templates folders, and the /workflows/{id}/edit route mount point for WorkflowEditorPage. Backed entirely by PostgreSQL via compose-backend — no SQLite, no tools/iriai-workflows shell.</td>
            <td><code>React 18, Vite, auth-react, React Router</code></td>
            <td>—</td>
            <td>J-16, J-17, J-18, J-20, J-22</td>
        </tr><tr>
            <td><code>SVC-101</code></td>
            <td><strong>SF-7 Libraries &amp; Registries API</strong></td>
            <td><code>service</code></td>
            <td>SF-7 library and registries surface served by compose-backend. Provides role/schema/task-template/tool CRUD endpoints consumed by the editor pickers, inline-to-library promotion flows, and template browser. Primary owner of the workflow_entity_refs reference-index table and its Alembic migration. Subscribes to SF-5 workflow mutation hooks (create/update/delete) to keep entity references synchronized after editor save/create/delete flows. Plugin registry and reference-check affordances must remain non-blocking additive surfaces for the core editor; the editor core never depends on SF-7 endpoints for boot or save.</td>
            <td><code>Python 3.11, FastAPI, SQLAlchemy 2.x, PostgreSQL 15</code></td>
            <td>8000</td>
            <td>J-18, J-20</td>
        </tr></tbody>
    </table>

    <h3>Connections</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>To</th><th>Protocol</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>CONN-115</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Zustand</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-116</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React context</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-117</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST/HTTP</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-118</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React props</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-119</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React props</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-120</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React props</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-121</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React Portal</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-122</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React props</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-123</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Zustand selector</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-124</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-125</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React Flow nodeTypes</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-126</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React Flow nodeTypes</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-127</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React Flow nodeTypes</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-128</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React Flow nodeTypes</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-129</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React Flow nodeTypes</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-130</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React Flow edgeTypes</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-131</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Zustand action</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-132</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-133</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST/HTTP</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-134</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-135</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-136</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>type import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-137</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>closure / function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-138</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Zustand selector</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-139</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Zustand action</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-140</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST/HTTP</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-141</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Zustand action</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-142</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST/HTTP</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-143</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Zustand action</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-144</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Zustand selector</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-145</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST/HTTP</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-146</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Zustand action</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-147</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Zustand action</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-148</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST/HTTP</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-149</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Zustand selector</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-150</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-151</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-152</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-153</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-154</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React component</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-155</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-156</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React component</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-157</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-158</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Zustand action</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-159</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-160</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>internal event / background task</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>API Endpoints</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Method</th><th>Path</th><th>Service</th><th>Description</th><th>Auth</th>
        </tr></thead>
        <tbody><tr>
            <td><code>API-138</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/:id</code></td>
            <td><code></code></td>
            <td>Fetch workflow definition as YAML for editor initialization using the nested phase contract: workflow.phases with phase.nodes, phase.children, and cross-phase edges.</td>
            <td><code>Bearer token</code></td>
        </tr><tr>
            <td><code>API-139</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/workflows/:id</code></td>
            <td><code></code></td>
            <td>Persist serialized workflow YAML on manual save or idle auto-save. Saves nested phase children and edge-based hook wiring without a serialized port_type or separate hooks section. Branch nodes are serialized as dict-keyed paths with per-port condition expressions — no node-level condition_type, condition, switch_function, or output_field fields. Fires workflow mutation hook (update) for SF-7 reference-index synchronization.</td>
            <td><code>Bearer token</code></td>
        </tr><tr>
            <td><code>API-140</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/:id/validate</code></td>
            <td><code></code></td>
            <td>Run server-side validation against the canonical schema, including nested phase children, hook-edge inference from source ports, and BranchNode per-port condition expression and paths invariants. Explicitly rejects stale BranchNode fields: condition_type, node-level condition, switch_function, and output_field.</td>
            <td><code>Bearer token</code></td>
        </tr><tr>
            <td><code>API-141</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schema/workflow</code></td>
            <td><code></code></td>
            <td>Fetch the canonical composer JSON Schema generated from iriai-compose&#x27;s current WorkflowConfig model. The editor uses this runtime schema for inspector constraints, defaults, and validation; static workflow-schema.json is build and test only.</td>
            <td><code>Bearer token</code></td>
        </tr><tr>
            <td><code>API-142</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles</code></td>
            <td><code></code></td>
            <td>Fetch role definitions for role pickers and palette chips.</td>
            <td><code>Bearer token</code></td>
        </tr><tr>
            <td><code>API-143</code></td>
            <td><code>POST</code></td>
            <td><code>/api/roles</code></td>
            <td><code></code></td>
            <td>Promote an inline role to the shared library.</td>
            <td><code>Bearer token</code></td>
        </tr><tr>
            <td><code>API-144</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas</code></td>
            <td><code></code></td>
            <td>Fetch schema definitions for output schema pickers.</td>
            <td><code>Bearer token</code></td>
        </tr><tr>
            <td><code>API-145</code></td>
            <td><code>POST</code></td>
            <td><code>/api/schemas</code></td>
            <td><code></code></td>
            <td>Promote an inline schema to the shared library.</td>
            <td><code>Bearer token</code></td>
        </tr><tr>
            <td><code>API-146</code></td>
            <td><code>GET</code></td>
            <td><code>/api/plugins</code></td>
            <td><code></code></td>
            <td>Fetch plugin definitions and instance metadata for PluginInspector. Non-blocking additive surface; editor core never calls this on boot or save.</td>
            <td><code>Bearer token</code></td>
        </tr><tr>
            <td><code>API-147</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates</code></td>
            <td><code></code></td>
            <td>Fetch reusable task templates for the palette.</td>
            <td><code>Bearer token</code></td>
        </tr><tr>
            <td><code>API-148</code></td>
            <td><code>POST</code></td>
            <td><code>/api/templates</code></td>
            <td><code></code></td>
            <td>Persist a selected subgraph as a reusable task template.</td>
            <td><code>Bearer token</code></td>
        </tr><tr>
            <td><code>API-149</code></td>
            <td><code>POST</code></td>
            <td><code>/store/addNode</code></td>
            <td><code></code></td>
            <td>Add a node with type-specific defaults. Branch defaults to two starter paths keyed path_1 and path_2, each with a blank per-port condition expression string, plus an empty inputs dict. No condition_type or node-level condition field is added.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-150</code></td>
            <td><code>PATCH</code></td>
            <td><code>/store/updateNodeData</code></td>
            <td><code></code></td>
            <td>Apply partial node-data edits from inspectors with undo snapshot support.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-151</code></td>
            <td><code>POST</code></td>
            <td><code>/store/addEdge</code></td>
            <td><code></code></td>
            <td>Add a connection. For Branch nodes, sourceHandle must match a key in node.data.paths; multiple output paths can fire concurrently if their per-port conditions are met (non-exclusive fan-out). Does not block fan-out connections.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-152</code></td>
            <td><code>POST</code></td>
            <td><code>/store/toggleCollapse</code></td>
            <td><code></code></td>
            <td>Toggle collapse state for a phase or template group and snapshot child visibility state.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-153</code></td>
            <td><code>POST</code></td>
            <td><code>/store/stampTemplate</code></td>
            <td><code></code></td>
            <td>Stamp a template group with cloned read-only child nodes and edges at a canvas position.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-154</code></td>
            <td><code>POST</code></td>
            <td><code>/store/detachTemplateGroup</code></td>
            <td><code></code></td>
            <td>Convert stamped template children into independent editable nodes and remove the wrapper group.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-155</code></td>
            <td><code>POST</code></td>
            <td><code>/store/undo</code></td>
            <td><code></code></td>
            <td>Restore the previous workflow snapshot and push the current state to redo.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-156</code></td>
            <td><code>POST</code></td>
            <td><code>/store/redo</code></td>
            <td><code></code></td>
            <td>Restore the next workflow snapshot from redo.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-157</code></td>
            <td><code>POST</code></td>
            <td><code>/store/loadFromYaml</code></td>
            <td><code></code></td>
            <td>Deserialize nested workflow YAML into flat editor state. phase.nodes and phase.children are flattened into parentId-grouped React Flow nodes, and hook edges gain UI edge kind by resolving the source port container. BranchNode path keys become output Handle IDs and per-port condition expressions are extracted from each path entry.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-158</code></td>
            <td><code>GET</code></td>
            <td><code>/store/serializeToYaml</code></td>
            <td><code></code></td>
            <td>Serialize flat editor state back to nested WorkflowConfig YAML under phases[].nodes and phases[].children. Hook edges stay ordinary source and target refs with no serialized port_type. Branch nodes emit dict-keyed paths where each path entry carries its own per-port condition expression; no switch_function, no output_field, and no node-level condition_type or condition fields are emitted.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-159</code></td>
            <td><code>POST</code></td>
            <td><code>/store/initWorkflow</code></td>
            <td><code></code></td>
            <td>Initialize workflow identifiers, load YAML if present, and clear transient editor state.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-160</code></td>
            <td><code>POST</code></td>
            <td><code>/store/openInspector</code></td>
            <td><code></code></td>
            <td>Add an inspector window descriptor for a node, edge, phase, or template group.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-161</code></td>
            <td><code>DELETE</code></td>
            <td><code>/store/closeInspector</code></td>
            <td><code></code></td>
            <td>Close an inspector window by windowId.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-162</code></td>
            <td><code>GET</code></td>
            <td><code>/serialization/serializeToYaml</code></td>
            <td><code></code></td>
            <td>Walk flat React Flow nodes and edges into nested PhaseDefinition trees using phases[].nodes and phases[].children, then emit ordinary source and target refs for both data and hook edges with no serialized port_type. Branch nodes emit dict-keyed paths where each path entry carries a per-port condition expression; no node-level condition_type, condition, switch_function, or output_field fields are emitted.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-163</code></td>
            <td><code>POST</code></td>
            <td><code>/serialization/deserializeFromYaml</code></td>
            <td><code></code></td>
            <td>Parse YAML, flatten phase.nodes and phase.children into parentId-linked React Flow nodes, infer hook-versus-data edge kind from source port resolution, materialize BranchNode path keys as output Handle IDs, and extract per-port condition expressions from each path entry. Run auto-layout when positions are missing.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-164</code></td>
            <td><code>POST</code></td>
            <td><code>/validation/validateStructural</code></td>
            <td><code></code></td>
            <td>Check dangling edges, duplicate IDs, missing required fields, BranchNode per-port condition expressions (blank or missing per path is an error), minimum-2 paths invariant, path-handle mismatches, cycles, and type mismatches. Also rejects stale node-level condition_type, condition, switch_function, and output_field fields on Branch nodes.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-165</code></td>
            <td><code>GET</code></td>
            <td><code>/validation/isValidConnection</code></td>
            <td><code></code></td>
            <td>Synchronously block self-loops, duplicate edges, cycle creation, and connections to read-only targets during drag.</td>
            <td><code>—</code></td>
        </tr></tbody>
    </table>

    <h3>Call Paths</h3>
    <div class="call-path-block">
            <h4><code>CP-35</code>: User opens a workflow, adds an Ask node and a Branch node, configures the Branch with per-port condition expressions in the paths dict per the D-GR-35 model, wires the flow, and saves.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;workflow-editor-page&#x27;, &#x27;action&#x27;: &#x27;navigate to /workflows/:id/edit&#x27;, &#x27;description&#x27;: &#x27;User opens the editor route for a workflow.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;workflow-editor-page&#x27;, &#x27;to_service&#x27;: &#x27;sf5-shell&#x27;, &#x27;action&#x27;: &#x27;useAuth()&#x27;, &#x27;description&#x27;: &#x27;Page retrieves auth context and shared shell dependencies.&#x27;, &#x27;returns&#x27;: &#x27;auth token and shell state&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;workflow-editor-page&#x27;, &#x27;to_service&#x27;: &#x27;sf1-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/schema/workflow&#x27;, &#x27;description&#x27;: &#x27;Page hydrates the canonical runtime schema contract before initializing inspectors, defaults, and validation rules.&#x27;, &#x27;returns&#x27;: &#x27;JSON Schema document&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;workflow-editor-page&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;initWorkflow(id, name)&#x27;, &#x27;description&#x27;: &#x27;Store initializes workflow identity and transient editor state after schema hydration.&#x27;, &#x27;returns&#x27;: &#x27;empty or hydrated EditorState&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;editor-store&#x27;, &#x27;to_service&#x27;: &#x27;sf1-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/workflows/:id&#x27;, &#x27;description&#x27;: &#x27;Fetch existing workflow YAML from the backend.&#x27;, &#x27;returns&#x27;: &#x27;Workflow YAML string&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;editor-store&#x27;, &#x27;to_service&#x27;: &#x27;serialization&#x27;, &#x27;action&#x27;: &#x27;deserializeFromYaml(yaml)&#x27;, &#x27;description&#x27;: &#x27;Convert nested YAML phase.nodes and phase.children to flat React Flow nodes and edges. Hook edges are inferred from the source port container. BranchNode path keys become output Handle IDs and per-port condition expressions are extracted from each path entry.&#x27;, &#x27;returns&#x27;: &#x27;nodes, edges, registries&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;serialization&#x27;, &#x27;to_service&#x27;: &#x27;auto-layout&#x27;, &#x27;action&#x27;: &#x27;autoLayout(nodes, edges)&#x27;, &#x27;description&#x27;: &#x27;Compute initial positions for nodes missing saved coordinates.&#x27;, &#x27;returns&#x27;: &#x27;positioned nodes&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;select visible nodes and edges&#x27;, &#x27;description&#x27;: &#x27;Canvas derives visible elements after collapse-state filtering.&#x27;, &#x27;returns&#x27;: &#x27;visible nodes and edges&#x27;}</li><li>{&#x27;sequence&#x27;: 9, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;palette&#x27;, &#x27;action&#x27;: &#x27;drag Ask item onto canvas&#x27;, &#x27;description&#x27;: &#x27;User drops a new Ask node on the canvas.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 10, &#x27;from_service&#x27;: &#x27;palette&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &quot;addNode(type=&#x27;ask&#x27;)&quot;, &#x27;description&#x27;: &#x27;Store inserts an Ask node with default input and output ports and an undo snapshot.&#x27;, &#x27;returns&#x27;: &#x27;Ask node&#x27;}</li><li>{&#x27;sequence&#x27;: 11, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;action&#x27;: &#x27;double-click Ask node&#x27;, &#x27;description&#x27;: &#x27;User opens the Ask inspector.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 12, &#x27;from_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;to_service&#x27;: &#x27;inspector-system&#x27;, &#x27;action&#x27;: &#x27;openInspector(askNode)&#x27;, &#x27;description&#x27;: &#x27;Inspector manager renders AskInspector tethered to the node.&#x27;, &#x27;returns&#x27;: &#x27;AskInspector window&#x27;}</li><li>{&#x27;sequence&#x27;: 13, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;action&#x27;: &#x27;fill actor and prompt fields&#x27;, &#x27;description&#x27;: &#x27;User selects a role and edits the Ask prompt.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 14, &#x27;from_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;updateNodeData(askNodeId, data)&#x27;, &#x27;description&#x27;: &#x27;Store persists Ask edits and re-renders the card face.&#x27;, &#x27;returns&#x27;: &#x27;updated Ask node&#x27;}</li><li>{&#x27;sequence&#x27;: 15, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;palette&#x27;, &#x27;action&#x27;: &#x27;drag Branch item onto canvas&#x27;, &#x27;description&#x27;: &#x27;User drops a new Branch node on the canvas.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 16, &#x27;from_service&#x27;: &#x27;palette&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &quot;addNode(type=&#x27;branch&#x27;)&quot;, &#x27;description&#x27;: &#x27;Store inserts a Branch node with one input port and two starter paths keyed path_1 and path_2, each carrying a blank per-port condition expression string. No node-level condition_type or condition field is added.&#x27;, &#x27;returns&#x27;: &#x27;Branch node&#x27;}</li><li>{&#x27;sequence&#x27;: 17, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;action&#x27;: &#x27;configure Branch path conditions and names&#x27;, &#x27;description&#x27;: &quot;User opens BranchInspector, renames path_1 to &#x27;approved&#x27; and sets its per-port condition expression (e.g. output.verdict == &#x27;approved&#x27;), then renames path_2 to &#x27;rejected&#x27; and sets its condition expression (e.g. output.verdict != &#x27;approved&#x27;). Both paths are evaluated independently at runtime — non-exclusive fan-out means both could fire if both conditions are true. Branch output Handles update immediately because each path key is the Handle ID.&quot;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 18, &#x27;from_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;updateNodeData(branchNodeId, data)&#x27;, &#x27;description&#x27;: &#x27;Store saves the canonical D-GR-35 BranchNode contract: dict-keyed paths where each path entry carries its own condition expression string. No node-level condition_type or condition fields are written.&#x27;, &#x27;returns&#x27;: &#x27;updated Branch node&#x27;}</li><li>{&#x27;sequence&#x27;: 19, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;action&#x27;: &#x27;connect Ask output to Branch input&#x27;, &#x27;description&#x27;: &#x27;User draws a data edge from the Ask result port to the Branch input port.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 20, &#x27;from_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;addEdge(edgeDraft)&#x27;, &#x27;description&#x27;: &#x27;Store adds the edge and preserves sourceHandle and targetHandle IDs.&#x27;, &#x27;returns&#x27;: &#x27;data edge&#x27;}</li><li>{&#x27;sequence&#x27;: 21, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;toolbar&#x27;, &#x27;action&#x27;: &#x27;Ctrl+S&#x27;, &#x27;description&#x27;: &#x27;User saves the workflow.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 22, &#x27;from_service&#x27;: &#x27;toolbar&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;serializeToYaml()&#x27;, &#x27;description&#x27;: &#x27;Store rebuilds nested WorkflowConfig YAML under phases[].nodes and phases[].children, emits ordinary source and target refs for hook edges with no serialized port_type, and serializes BranchNode dict-keyed paths with per-port condition expressions — no node-level condition_type, condition, switch_function, or output_field.&#x27;, &#x27;returns&#x27;: &#x27;Workflow YAML string&#x27;}</li><li>{&#x27;sequence&#x27;: 23, &#x27;from_service&#x27;: &#x27;editor-store&#x27;, &#x27;to_service&#x27;: &#x27;sf1-backend&#x27;, &#x27;action&#x27;: &#x27;PUT /api/workflows/:id&#x27;, &#x27;description&#x27;: &#x27;Persist YAML and clear dirty state on success. SF-5 fires workflow update mutation hook for SF-7 reference-index synchronization after the PUT succeeds.&#x27;, &#x27;returns&#x27;: &#x27;saved workflow&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-36</code>: User creates a phase from a selection, changes its mode, collapses and expands it, then creates a nested loop phase inside it.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;toolbar&#x27;, &#x27;action&#x27;: &#x27;click Select tool&#x27;, &#x27;description&#x27;: &#x27;Toolbar switches to rectangle-selection mode.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;action&#x27;: &#x27;drag selection rectangle over nodes&#x27;, &#x27;description&#x27;: &#x27;SelectionRectangle renders a marching-ants overlay over the chosen nodes.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;selection-rectangle&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;createPhase(enclosedNodeIds, bounds)&#x27;, &#x27;description&#x27;: &#x27;Store creates a new phase container and assigns parentId on enclosed children.&#x27;, &#x27;returns&#x27;: &#x27;phase node&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;to_service&#x27;: &#x27;phase-container&#x27;, &#x27;action&#x27;: &#x27;render expanded phase&#x27;, &#x27;description&#x27;: &#x27;Canvas renders the new phase with mode-specific border styling and visible children.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;phase-container&#x27;, &#x27;action&#x27;: &#x27;open PhaseInspector and change mode&#x27;, &#x27;description&#x27;: &#x27;User sets the phase mode to fold in the inspector.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &quot;updateNodeData(phaseId, { mode: &#x27;fold&#x27; })&quot;, &#x27;description&#x27;: &#x27;Store updates phase mode and the border styling changes immediately.&#x27;, &#x27;returns&#x27;: &#x27;updated phase&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;phase-container&#x27;, &#x27;action&#x27;: &#x27;click collapse toggle&#x27;, &#x27;description&#x27;: &#x27;User collapses the phase.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;phase-container&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;toggleCollapse(phaseId)&#x27;, &#x27;description&#x27;: &#x27;Store hides child nodes from visible canvas state and preserves their positions.&#x27;, &#x27;returns&#x27;: &#x27;collapsedGroups&#x27;}</li><li>{&#x27;sequence&#x27;: 9, &#x27;from_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;recompute visible nodes&#x27;, &#x27;description&#x27;: &#x27;Canvas hides children and renders CollapsedGroupCard with mode badge and node count.&#x27;, &#x27;returns&#x27;: &#x27;visible nodes&#x27;}</li><li>{&#x27;sequence&#x27;: 10, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;action&#x27;: &#x27;expand collapsed phase&#x27;, &#x27;description&#x27;: &#x27;User restores the phase to expanded mode.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 11, &#x27;from_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;recompute visible nodes&#x27;, &#x27;description&#x27;: &#x27;Canvas restores child visibility and original positions.&#x27;, &#x27;returns&#x27;: &#x27;visible nodes&#x27;}</li><li>{&#x27;sequence&#x27;: 12, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;selection-rectangle&#x27;, &#x27;action&#x27;: &#x27;draw nested selection inside the phase&#x27;, &#x27;description&#x27;: &#x27;User encloses nodes that share the fold phase as parent.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 13, &#x27;from_service&#x27;: &#x27;selection-rectangle&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;createPhase(innerNodeIds, parentId=foldPhaseId)&#x27;, &#x27;description&#x27;: &#x27;Store creates a nested loop phase with extent set to parent.&#x27;, &#x27;returns&#x27;: &#x27;nested loop phase&#x27;}</li><li>{&#x27;sequence&#x27;: 14, &#x27;from_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;to_service&#x27;: &#x27;phase-container&#x27;, &#x27;action&#x27;: &#x27;render nested loop phase&#x27;, &#x27;description&#x27;: &#x27;Canvas renders the loop phase with dashed amber border and condition_met and max_exceeded exit ports.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-37</code>: User creates an inline role inside AskInspector and promotes it to the shared library.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;action&#x27;: &#x27;double-click Ask node&#x27;, &#x27;description&#x27;: &#x27;Open AskInspector for the selected node.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;inspector-system&#x27;, &#x27;to_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;action&#x27;: &#x27;render AskInspector&#x27;, &#x27;description&#x27;: &#x27;Inspector shows role picker and inline role controls.&#x27;, &#x27;returns&#x27;: &#x27;AskInspector&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;action&#x27;: &#x27;create inline role&#x27;, &#x27;description&#x27;: &#x27;User expands the inline role creator and fills the role fields.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;updateNodeData(nodeId, { inline_role })&#x27;, &#x27;description&#x27;: &#x27;Store saves inline role data and the Ask card reflects the assigned role.&#x27;, &#x27;returns&#x27;: &#x27;updated Ask node&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;action&#x27;: &#x27;click Save to Library&#x27;, &#x27;description&#x27;: &#x27;Promotion dialog opens with the role name pre-filled.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;editor-dialogs&#x27;, &#x27;to_service&#x27;: &#x27;sf7-library&#x27;, &#x27;action&#x27;: &#x27;POST /api/roles&#x27;, &#x27;description&#x27;: &#x27;Persist the inline role to the shared library via the SF-7 library surface.&#x27;, &#x27;returns&#x27;: &#x27;RoleDefinition&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;updateNodeData(nodeId, { actor, inline_role: undefined })&#x27;, &#x27;description&#x27;: &#x27;Ask node switches from inline role data to a library role reference.&#x27;, &#x27;returns&#x27;: &#x27;updated Ask node&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-38</code>: User stamps a task template, inspects a read-only child, and detaches the template group to edit the stamped nodes freely.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;palette&#x27;, &#x27;action&#x27;: &#x27;drag template from TemplateBrowser&#x27;, &#x27;description&#x27;: &#x27;Template item is dragged from the right-side palette.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;palette&#x27;, &#x27;to_service&#x27;: &#x27;sf7-library&#x27;, &#x27;action&#x27;: &#x27;GET /api/templates&#x27;, &#x27;description&#x27;: &#x27;Load the full template definition including nodes, edges, and interfaces from the SF-7 library surface.&#x27;, &#x27;returns&#x27;: &#x27;TemplateDefinition&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;palette&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;stampTemplate(templateId, dropPosition, templateData)&#x27;, &#x27;description&#x27;: &#x27;Store creates a template group and cloned read-only child nodes with new IDs.&#x27;, &#x27;returns&#x27;: &#x27;TemplateGroup and children&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;to_service&#x27;: &#x27;template-group&#x27;, &#x27;action&#x27;: &#x27;render expanded template group&#x27;, &#x27;description&#x27;: &#x27;Canvas shows the green dashed group and dimmed read-only child nodes.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;action&#x27;: &#x27;double-click read-only child&#x27;, &#x27;description&#x27;: &#x27;User opens a read-only inspector for a stamped child node.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;inspector-system&#x27;, &#x27;to_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;action&#x27;: &#x27;render read-only inspector&#x27;, &#x27;description&#x27;: &#x27;Inspector shows all fields disabled with a lock banner.&#x27;, &#x27;returns&#x27;: &#x27;read-only inspector&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;template-group&#x27;, &#x27;action&#x27;: &#x27;click Detach&#x27;, &#x27;description&#x27;: &#x27;User confirms that the stamped template should become editable.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;editor-dialogs&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;detachTemplateGroup(groupId)&#x27;, &#x27;description&#x27;: &#x27;Store removes read-only flags, converts positions to absolute coordinates, and deletes the wrapper group.&#x27;, &#x27;returns&#x27;: &#x27;detached nodes&#x27;}</li><li>{&#x27;sequence&#x27;: 9, &#x27;from_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;recompute visible nodes&#x27;, &#x27;description&#x27;: &#x27;Detached nodes render as normal editable Ask, Branch, and Plugin cards.&#x27;, &#x27;returns&#x27;: &#x27;visible nodes&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-39</code>: Covers invalid BranchNode structure, type mismatch edge warnings, auto-save failure, undo recovery, and malformed import handling.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;action&#x27;: &#x27;clear a Branch per-port condition or reduce paths below two&#x27;, &#x27;description&#x27;: &#x27;User edits a Branch node by clearing a per-port condition expression on one of its paths or deleting a path row to bring the total below the two-path minimum, creating an invalid structural state.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;node-inspectors&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;updateNodeData(branchNodeId, invalidData)&#x27;, &#x27;description&#x27;: &#x27;Store persists the edit so validation can evaluate it.&#x27;, &#x27;returns&#x27;: &#x27;updated Branch node&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;client-validator&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &quot;setValidationIssues([{ code: &#x27;invalid_branch_config&#x27; }])&quot;, &#x27;description&#x27;: &#x27;Validator flags blank or missing per-port condition expressions or insufficient paths (fewer than two). Branch card shows an error badge and ValidationPanel lists the issue.&#x27;, &#x27;returns&#x27;: &#x27;ValidationIssue[]&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;action&#x27;: &#x27;connect incompatible port types&#x27;, &#x27;description&#x27;: &#x27;User draws a data edge between incompatible types.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;addEdge(typeMismatchEdge)&#x27;, &#x27;description&#x27;: &#x27;Store creates the edge immediately because type mismatches are warnings, not connection blockers.&#x27;, &#x27;returns&#x27;: &#x27;data edge&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;client-validator&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &quot;setValidationIssues([{ code: &#x27;type_mismatch&#x27;, severity: &#x27;warning&#x27; }])&quot;, &#x27;description&#x27;: &#x27;Edge re-renders as a red dashed warning edge and ValidationPanel lists the warning.&#x27;, &#x27;returns&#x27;: &#x27;ValidationIssue[]&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;editor-hooks&#x27;, &#x27;to_service&#x27;: &#x27;sf1-backend&#x27;, &#x27;action&#x27;: &#x27;PUT /api/workflows/:id&#x27;, &#x27;description&#x27;: &#x27;Idle auto-save attempts to persist the workflow and the backend returns an error.&#x27;, &#x27;returns&#x27;: &#x27;HTTP 500&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;editor-store&#x27;, &#x27;to_service&#x27;: &#x27;toolbar&#x27;, &#x27;action&#x27;: &quot;set autoSaveStatus=&#x27;error&#x27;&quot;, &#x27;description&#x27;: &#x27;Toolbar shows the save error state and the workflow remains dirty.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 9, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;toolbar&#x27;, &#x27;action&#x27;: &#x27;Ctrl+Z&#x27;, &#x27;description&#x27;: &#x27;User undoes the last destructive edit.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 10, &#x27;from_service&#x27;: &#x27;undo-middleware&#x27;, &#x27;to_service&#x27;: &#x27;editor-store&#x27;, &#x27;action&#x27;: &#x27;restore previous snapshot&#x27;, &#x27;description&#x27;: &#x27;Undo restores the prior valid Branch path configuration and edge state.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowSnapshot&#x27;}</li><li>{&#x27;sequence&#x27;: 11, &#x27;from_service&#x27;: &#x27;user&#x27;, &#x27;to_service&#x27;: &#x27;editor-dialogs&#x27;, &#x27;action&#x27;: &#x27;import malformed YAML&#x27;, &#x27;description&#x27;: &#x27;User selects an invalid YAML file through the import flow.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 12, &#x27;from_service&#x27;: &#x27;serialization&#x27;, &#x27;to_service&#x27;: &#x27;editor-canvas&#x27;, &#x27;action&#x27;: &#x27;deserializeFromYaml fails&#x27;, &#x27;description&#x27;: &#x27;Editor catches the parse error, shows a toast, and leaves the existing canvas untouched.&#x27;, &#x27;returns&#x27;: &#x27;error toast&#x27;}</li></ol>
        </div>

    <h3>Entities</h3>
    <div class="entity-block">
            <h4><code>ENT-96</code>: WorkflowConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Workflow display name.</td>
                    </tr><tr>
                        <td><code>schema_version</code></td>
                        <td><code>string</code></td>
                        <td>Schema version string.</td>
                    </tr><tr>
                        <td><code>actors</code></td>
                        <td><code>Record&lt;string, ActorDefinition&gt;</code></td>
                        <td>Workflow-scoped actor registry.</td>
                    </tr><tr>
                        <td><code>types</code></td>
                        <td><code>Record&lt;string, TypeDefinition&gt;</code></td>
                        <td>Named type registry.</td>
                    </tr><tr>
                        <td><code>plugins</code></td>
                        <td><code>Record&lt;string, PluginInterface&gt;</code></td>
                        <td>Plugin type registry — workflow-local keys, not persisted plugin rows.</td>
                    </tr><tr>
                        <td><code>plugin_instances</code></td>
                        <td><code>Record&lt;string, PluginInstanceConfig&gt;</code></td>
                        <td>Concrete plugin instance registry.</td>
                    </tr><tr>
                        <td><code>stores</code></td>
                        <td><code>Record&lt;string, StoreDefinition&gt;</code></td>
                        <td>Store registry.</td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>string[]</code></td>
                        <td>Workflow-level context keys.</td>
                    </tr><tr>
                        <td><code>context_text</code></td>
                        <td><code>Record&lt;string, string&gt;</code></td>
                        <td>Workflow-level inline context text.</td>
                    </tr><tr>
                        <td><code>phases</code></td>
                        <td><code>PhaseDefinition[]</code></td>
                        <td>Top-level phase array. Each phase owns nested nodes and children. No top-level nodes collection.</td>
                    </tr><tr>
                        <td><code>edges</code></td>
                        <td><code>EdgeDefinition[]</code></td>
                        <td>Edges that connect top-level phases or workflow boundary ports.</td>
                    </tr><tr>
                        <td><code>cost</code></td>
                        <td><code>CostConfig</code></td>
                        <td>Optional cost metadata.</td>
                    </tr><tr>
                        <td><code>templates</code></td>
                        <td><code>Record&lt;string, TemplateRef&gt;</code></td>
                        <td>Referenced task templates.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-97</code>: PhaseDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>string</code></td>
                        <td>Unique phase identifier.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Display name.</td>
                    </tr><tr>
                        <td><code>mode</code></td>
                        <td><code>&#x27;sequential&#x27; | &#x27;map&#x27; | &#x27;fold&#x27; | &#x27;loop&#x27;</code></td>
                        <td>Execution mode.</td>
                    </tr><tr>
                        <td><code>mode_config</code></td>
                        <td><code>SequentialConfig | MapConfig | FoldConfig | LoopConfig</code></td>
                        <td>Mode-specific configuration.</td>
                    </tr><tr>
                        <td><code>nodes</code></td>
                        <td><code>NodeDefinition[]</code></td>
                        <td>Child nodes.</td>
                    </tr><tr>
                        <td><code>edges</code></td>
                        <td><code>EdgeDefinition[]</code></td>
                        <td>Child edges.</td>
                    </tr><tr>
                        <td><code>children</code></td>
                        <td><code>PhaseDefinition[]</code></td>
                        <td>Nested sub-phases serialized under phases[].children.</td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>string[]</code></td>
                        <td>Phase-level context keys.</td>
                    </tr><tr>
                        <td><code>context_text</code></td>
                        <td><code>Record&lt;string, string&gt;</code></td>
                        <td>Phase-level inline context text.</td>
                    </tr><tr>
                        <td><code>input_type</code></td>
                        <td><code>string</code></td>
                        <td>Named input type reference.</td>
                    </tr><tr>
                        <td><code>input_schema</code></td>
                        <td><code>Record&lt;string, unknown&gt;</code></td>
                        <td>Inline input schema.</td>
                    </tr><tr>
                        <td><code>output_type</code></td>
                        <td><code>string</code></td>
                        <td>Named output type reference.</td>
                    </tr><tr>
                        <td><code>output_schema</code></td>
                        <td><code>Record&lt;string, unknown&gt;</code></td>
                        <td>Inline output schema.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>Record&lt;string, PortDefinition&gt;</code></td>
                        <td>Phase input ports.</td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>Record&lt;string, PortDefinition&gt;</code></td>
                        <td>Phase output ports including loop exit ports condition_met and max_exceeded.</td>
                    </tr><tr>
                        <td><code>hooks</code></td>
                        <td><code>Record&lt;string, PortDefinition&gt;</code></td>
                        <td>Hook ports such as on_start and on_end.</td>
                    </tr><tr>
                        <td><code>position</code></td>
                        <td><code>{ x: number; y: number }</code></td>
                        <td>Canvas position for editor rendering.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-98</code>: NodeDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>string</code></td>
                        <td>Unique node identifier.</td>
                    </tr><tr>
                        <td><code>type</code></td>
                        <td><code>&#x27;ask&#x27; | &#x27;branch&#x27; | &#x27;plugin&#x27;</code></td>
                        <td>Node discriminator.</td>
                    </tr><tr>
                        <td><code>summary</code></td>
                        <td><code>string</code></td>
                        <td>Short human summary.</td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>string[]</code></td>
                        <td>Node-level context keys.</td>
                    </tr><tr>
                        <td><code>context_text</code></td>
                        <td><code>Record&lt;string, string&gt;</code></td>
                        <td>Node-level inline context text.</td>
                    </tr><tr>
                        <td><code>artifact_key</code></td>
                        <td><code>string</code></td>
                        <td>Artifact key for emitted results.</td>
                    </tr><tr>
                        <td><code>input_type</code></td>
                        <td><code>string</code></td>
                        <td>Named input type reference.</td>
                    </tr><tr>
                        <td><code>input_schema</code></td>
                        <td><code>Record&lt;string, unknown&gt;</code></td>
                        <td>Inline input schema.</td>
                    </tr><tr>
                        <td><code>output_type</code></td>
                        <td><code>string</code></td>
                        <td>Named output type reference.</td>
                    </tr><tr>
                        <td><code>output_schema</code></td>
                        <td><code>Record&lt;string, unknown&gt;</code></td>
                        <td>Inline output schema.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>Record&lt;string, PortDefinition&gt;</code></td>
                        <td>Input ports used by all node types.</td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>Record&lt;string, PortDefinition&gt;</code></td>
                        <td>Data output ports for Ask and Plugin nodes. Branch nodes use paths instead.</td>
                    </tr><tr>
                        <td><code>hooks</code></td>
                        <td><code>Record&lt;string, PortDefinition&gt;</code></td>
                        <td>Hook ports such as on_start and on_end.</td>
                    </tr><tr>
                        <td><code>position</code></td>
                        <td><code>{ x: number; y: number }</code></td>
                        <td>Canvas position.</td>
                    </tr><tr>
                        <td><code>actor</code></td>
                        <td><code>string</code></td>
                        <td>Role or actor reference.</td>
                    </tr><tr>
                        <td><code>inline_role</code></td>
                        <td><code>InlineRoleDefinition</code></td>
                        <td>Inline role configuration.</td>
                    </tr><tr>
                        <td><code>prompt</code></td>
                        <td><code>string</code></td>
                        <td>Prompt template.</td>
                    </tr><tr>
                        <td><code>paths</code></td>
                        <td><code>Record&lt;string, PathPortDefinition&gt;</code></td>
                        <td>Branch output paths. Each key is both a path name and an output handle ID. Each value extends PortDefinition with a required &#x27;condition&#x27; expression string (evaluated independently at runtime). Non-exclusive fan-out: multiple paths can fire if their respective condition expressions evaluate to true. No node-level condition_type or condition field exists; output_field mode is fully removed.</td>
                    </tr><tr>
                        <td><code>merge_function</code></td>
                        <td><code>string</code></td>
                        <td>Optional merge function for multi-input gather before fan-out evaluation.</td>
                    </tr><tr>
                        <td><code>plugin_ref</code></td>
                        <td><code>string</code></td>
                        <td>Plugin type reference — workflow-local, never a persisted plugin-management row.</td>
                    </tr><tr>
                        <td><code>instance_ref</code></td>
                        <td><code>string</code></td>
                        <td>Plugin instance reference.</td>
                    </tr><tr>
                        <td><code>plugin_config</code></td>
                        <td><code>Record&lt;string, unknown&gt;</code></td>
                        <td>Inline plugin config override.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-99</code>: PathPortDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>condition</code></td>
                        <td><code>string</code></td>
                        <td>Per-port condition expression evaluated independently at runtime. If true, this path fires. Multiple paths can fire simultaneously (non-exclusive fan-out). Bare eval against node output context; expression-only (no output_field shorthand).</td>
                    </tr><tr>
                        <td><code>direction</code></td>
                        <td><code>&#x27;output&#x27;</code></td>
                        <td>Port direction — always output for Branch path ports.</td>
                    </tr><tr>
                        <td><code>type_ref</code></td>
                        <td><code>string</code></td>
                        <td>Named type reference.</td>
                    </tr><tr>
                        <td><code>schema_def</code></td>
                        <td><code>Record&lt;string, unknown&gt;</code></td>
                        <td>Inline schema definition.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-100</code>: EdgeDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>source</code></td>
                        <td><code>string</code></td>
                        <td>Source node and port. For Branch nodes, port_name must match a paths key.</td>
                    </tr><tr>
                        <td><code>target</code></td>
                        <td><code>string</code></td>
                        <td>Target node and port.</td>
                    </tr><tr>
                        <td><code>transform_fn</code></td>
                        <td><code>string</code></td>
                        <td>Edge-level transform function. Not present on hook edges; absence signals hook semantics when combined with source-port container resolution.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-101</code>: PortDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>direction</code></td>
                        <td><code>&#x27;input&#x27; | &#x27;output&#x27;</code></td>
                        <td>Port direction.</td>
                    </tr><tr>
                        <td><code>type_ref</code></td>
                        <td><code>string</code></td>
                        <td>Named type reference.</td>
                    </tr><tr>
                        <td><code>schema_def</code></td>
                        <td><code>Record&lt;string, unknown&gt;</code></td>
                        <td>Inline schema definition.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-102</code>: ActorDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>string</code></td>
                        <td>Actor identifier.</td>
                    </tr><tr>
                        <td><code>model</code></td>
                        <td><code>string</code></td>
                        <td>Model identifier.</td>
                    </tr><tr>
                        <td><code>system_prompt</code></td>
                        <td><code>string</code></td>
                        <td>Actor system prompt.</td>
                    </tr><tr>
                        <td><code>tools</code></td>
                        <td><code>string[]</code></td>
                        <td>Tool references.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-103</code>: InlineRoleDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Inline role name.</td>
                    </tr><tr>
                        <td><code>model</code></td>
                        <td><code>string</code></td>
                        <td>Inline role model.</td>
                    </tr><tr>
                        <td><code>system_prompt</code></td>
                        <td><code>string</code></td>
                        <td>Inline role system prompt.</td>
                    </tr><tr>
                        <td><code>tools</code></td>
                        <td><code>string[]</code></td>
                        <td>Inline role tools.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-104</code>: TemplateRef</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>template_id</code></td>
                        <td><code>string</code></td>
                        <td>Library template identifier.</td>
                    </tr><tr>
                        <td><code>version_hash</code></td>
                        <td><code>string</code></td>
                        <td>Version hash used to detect drift.</td>
                    </tr><tr>
                        <td><code>position</code></td>
                        <td><code>{ x: number; y: number }</code></td>
                        <td>Canvas position.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-105</code>: ValidationIssue</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>code</code></td>
                        <td><code>string</code></td>
                        <td>Canonical issue code such as invalid_branch_config or type_mismatch.</td>
                    </tr><tr>
                        <td><code>path</code></td>
                        <td><code>string</code></td>
                        <td>Dot path to the offending entity.</td>
                    </tr><tr>
                        <td><code>message</code></td>
                        <td><code>string</code></td>
                        <td>Human-readable message.</td>
                    </tr><tr>
                        <td><code>nodeId</code></td>
                        <td><code>string</code></td>
                        <td>Node-level issue target.</td>
                    </tr><tr>
                        <td><code>edgeId</code></td>
                        <td><code>string</code></td>
                        <td>Edge-level issue target.</td>
                    </tr><tr>
                        <td><code>severity</code></td>
                        <td><code>&#x27;error&#x27; | &#x27;warning&#x27;</code></td>
                        <td>Severity level.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-106</code>: WorkflowSnapshot</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>nodes</code></td>
                        <td><code>Node[]</code></td>
                        <td>Frozen React Flow nodes, including BranchNode per-port paths with individual condition expressions. No node-level condition_type or condition.</td>
                    </tr><tr>
                        <td><code>edges</code></td>
                        <td><code>Edge[]</code></td>
                        <td>Frozen React Flow edges.</td>
                    </tr><tr>
                        <td><code>actors</code></td>
                        <td><code>Record&lt;string, ActorDef&gt;</code></td>
                        <td>Workflow actor registry snapshot.</td>
                    </tr><tr>
                        <td><code>types</code></td>
                        <td><code>Record&lt;string, TypeDef&gt;</code></td>
                        <td>Workflow type registry snapshot.</td>
                    </tr><tr>
                        <td><code>plugins</code></td>
                        <td><code>Record&lt;string, PluginDef&gt;</code></td>
                        <td>Workflow plugin registry snapshot.</td>
                    </tr><tr>
                        <td><code>pluginInstances</code></td>
                        <td><code>Record&lt;string, PluginInstanceDef&gt;</code></td>
                        <td>Plugin instance registry snapshot.</td>
                    </tr><tr>
                        <td><code>stores</code></td>
                        <td><code>Record&lt;string, StoreDef&gt;</code></td>
                        <td>Store registry snapshot.</td>
                    </tr><tr>
                        <td><code>contextKeys</code></td>
                        <td><code>string[]</code></td>
                        <td>Workflow context key snapshot.</td>
                    </tr><tr>
                        <td><code>collapsedGroups</code></td>
                        <td><code>Record&lt;string, boolean&gt;</code></td>
                        <td>Collapse state for phase and template groups.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-107</code>: EditorState</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>workflowId</code></td>
                        <td><code>string</code></td>
                        <td>Current workflow identifier.</td>
                    </tr><tr>
                        <td><code>workflowName</code></td>
                        <td><code>string</code></td>
                        <td>Current workflow name.</td>
                    </tr><tr>
                        <td><code>nodes</code></td>
                        <td><code>Node[]</code></td>
                        <td>Canonical node state. Branch nodes carry dict-keyed paths where each path entry contains a per-port condition expression string. No node-level condition_type or condition field.</td>
                    </tr><tr>
                        <td><code>edges</code></td>
                        <td><code>Edge[]</code></td>
                        <td>Canonical edge state with UI-only hook-versus-data decoration derived from source port resolution.</td>
                    </tr><tr>
                        <td><code>collapsedGroups</code></td>
                        <td><code>Record&lt;string, boolean&gt;</code></td>
                        <td>Collapsed group visibility state.</td>
                    </tr><tr>
                        <td><code>actors</code></td>
                        <td><code>Record&lt;string, ActorDef&gt;</code></td>
                        <td>Workflow actor registry.</td>
                    </tr><tr>
                        <td><code>types</code></td>
                        <td><code>Record&lt;string, TypeDef&gt;</code></td>
                        <td>Workflow type registry.</td>
                    </tr><tr>
                        <td><code>plugins</code></td>
                        <td><code>Record&lt;string, PluginDef&gt;</code></td>
                        <td>Workflow plugin registry — workflow-local keys only, not persisted plugin-management rows.</td>
                    </tr><tr>
                        <td><code>pluginInstances</code></td>
                        <td><code>Record&lt;string, PluginInstanceDef&gt;</code></td>
                        <td>Workflow plugin instance registry.</td>
                    </tr><tr>
                        <td><code>stores</code></td>
                        <td><code>Record&lt;string, StoreDef&gt;</code></td>
                        <td>Workflow store registry.</td>
                    </tr><tr>
                        <td><code>contextKeys</code></td>
                        <td><code>string[]</code></td>
                        <td>Workflow context keys.</td>
                    </tr><tr>
                        <td><code>undoStack</code></td>
                        <td><code>WorkflowSnapshot[]</code></td>
                        <td>Undo history.</td>
                    </tr><tr>
                        <td><code>redoStack</code></td>
                        <td><code>WorkflowSnapshot[]</code></td>
                        <td>Redo history.</td>
                    </tr><tr>
                        <td><code>validationIssues</code></td>
                        <td><code>ValidationIssue[]</code></td>
                        <td>Current validation results.</td>
                    </tr><tr>
                        <td><code>toolMode</code></td>
                        <td><code>&#x27;hand&#x27; | &#x27;select&#x27;</code></td>
                        <td>Canvas interaction mode.</td>
                    </tr><tr>
                        <td><code>autoSaveStatus</code></td>
                        <td><code>&#x27;clean&#x27; | &#x27;dirty&#x27; | &#x27;saving&#x27; | &#x27;error&#x27;</code></td>
                        <td>Auto-save state.</td>
                    </tr><tr>
                        <td><code>inspectors</code></td>
                        <td><code>InspectorState[]</code></td>
                        <td>Open inspector windows.</td>
                    </tr><tr>
                        <td><code>isDirty</code></td>
                        <td><code>boolean</code></td>
                        <td>Dirty flag for beforeunload protection.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-108</code>: InspectorState</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>windowId</code></td>
                        <td><code>string</code></td>
                        <td>Unique inspector window identifier.</td>
                    </tr><tr>
                        <td><code>elementId</code></td>
                        <td><code>string</code></td>
                        <td>Target node, edge, phase, or template-group identifier.</td>
                    </tr><tr>
                        <td><code>elementType</code></td>
                        <td><code>&#x27;node&#x27; | &#x27;edge&#x27; | &#x27;phase&#x27; | &#x27;template-group&#x27;</code></td>
                        <td>Target element type.</td>
                    </tr><tr>
                        <td><code>position</code></td>
                        <td><code>{ x: number; y: number }</code></td>
                        <td>Viewport position for the inspector window.</td>
                    </tr><tr>
                        <td><code>readOnly</code></td>
                        <td><code>boolean</code></td>
                        <td>True when inspecting a stamped template child or other locked entity.</td>
                    </tr></tbody>
            </table>
        </div>

    <h3>Entity Relations</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>Relation</th><th>To</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>ER-81</code></td>
            <td><code>workflow-config</code></td>
            <td></td>
            <td><code>phase-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-82</code></td>
            <td><code>workflow-config</code></td>
            <td></td>
            <td><code>edge-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-83</code></td>
            <td><code>workflow-config</code></td>
            <td></td>
            <td><code>actor-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-84</code></td>
            <td><code>workflow-config</code></td>
            <td></td>
            <td><code>template-ref</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-85</code></td>
            <td><code>phase-definition</code></td>
            <td></td>
            <td><code>node-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-86</code></td>
            <td><code>phase-definition</code></td>
            <td></td>
            <td><code>edge-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-87</code></td>
            <td><code>phase-definition</code></td>
            <td></td>
            <td><code>phase-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-88</code></td>
            <td><code>phase-definition</code></td>
            <td></td>
            <td><code>port-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-89</code></td>
            <td><code>node-definition</code></td>
            <td></td>
            <td><code>port-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-90</code></td>
            <td><code>node-definition</code></td>
            <td></td>
            <td><code>path-port-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-91</code></td>
            <td><code>node-definition</code></td>
            <td></td>
            <td><code>inline-role-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-92</code></td>
            <td><code>node-definition</code></td>
            <td></td>
            <td><code>actor-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-93</code></td>
            <td><code>editor-state</code></td>
            <td></td>
            <td><code>workflow-snapshot</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-94</code></td>
            <td><code>editor-state</code></td>
            <td></td>
            <td><code>inspector-state</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-95</code></td>
            <td><code>editor-state</code></td>
            <td></td>
            <td><code>validation-issue</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-96</code></td>
            <td><code>workflow-snapshot</code></td>
            <td></td>
            <td><code>node-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-97</code></td>
            <td><code>workflow-snapshot</code></td>
            <td></td>
            <td><code>edge-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-98</code></td>
            <td><code>template-ref</code></td>
            <td></td>
            <td><code>node-definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-99</code></td>
            <td><code>path-port-definition</code></td>
            <td></td>
            <td><code>port-definition</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>Decisions</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Decision</th></tr></thead>
        <tbody><tr>
            <td><code>D-94</code></td>
            <td>D-SF6-1: Phases and templates use the same expand-to-real-nodes pattern. Collapsed state renders a lightweight metadata card; expanded state renders real child React Flow nodes via parentId grouping.</td>
        </tr><tr>
            <td><code>D-95</code></td>
            <td>D-SF6-2: Undo and redo use full structuredClone snapshots capped at 50 entries instead of command-pattern inverses.</td>
        </tr><tr>
            <td><code>D-96</code></td>
            <td>D-SF6-3: React Flow flat node and edge arrays remain the internal editor store shape. The persisted workflow contract stays nested YAML via WorkflowConfig.phases with per-phase nodes and children, and the serializer reconstructs that tree only during save and load.</td>
        </tr><tr>
            <td><code>D-97</code></td>
            <td>D-SF6-4: Validation is hybrid. isValidConnection handles fast synchronous connection guards, while clientValidator performs debounced structural and type checks after mutations.</td>
        </tr><tr>
            <td><code>D-98</code></td>
            <td>D-SF6-5: Auto-layout uses recursive dagre because phase nesting requires child-first layout and explicit collapsed group bounds.</td>
        </tr><tr>
            <td><code>D-99</code></td>
            <td>D-SF6-6: YAML serialization uses js-yaml and preserves the canonical BranchNode contract while targeting the D-GR-22 schema baseline. The serializer emits nested phase.nodes and phase.children plus ordinary source and target refs for both data and hook edges; hook semantics are reconstructed from source-port resolution, so no serialized port_type is emitted.</td>
        </tr><tr>
            <td><code>D-100</code></td>
            <td>D-SF6-7: Templates use stamp-and-detach semantics. Dropping a template creates independent read-only copies until the user detaches them.</td>
        </tr><tr>
            <td><code>D-101</code></td>
            <td>D-SF6-8: inputs, outputs, hooks, and BranchNode.paths are all dict-keyed maps. Port and path names live in the map key, not as redundant nested fields. Branch paths use PathPortDefinition which extends PortDefinition with a required per-port condition expression.</td>
        </tr><tr>
            <td><code>D-102</code></td>
            <td>D-SF6-9: BranchNode adopts the D-GR-35 per-port non-exclusive fan-out model (superseding the prior exclusive routing rule and aligning with D-GR-12): each entry in the dict-keyed paths map carries its own condition expression string evaluated independently at runtime, and multiple paths can fire if their conditions are met. There is no node-level condition_type or condition field. output_field mode is fully removed from the BranchNode schema everywhere. switch_function is rejected and never a valid field. merge_function remains valid for multi-input gather before fan-out evaluation. The editor renders per-port condition summaries on each path handle; BranchInspector exposes per-port condition expression editors per path row, not a single node-level condition editor. connectionValidator does not block multi-fan-out connections from a Branch node.</td>
        </tr><tr>
            <td><code>D-103</code></td>
            <td>D-SF6-10: GET /api/schema/workflow is the canonical composer schema source. The editor hydrates runtime field contracts and defaults from that endpoint; static workflow-schema.json artifacts are build and test only.</td>
        </tr><tr>
            <td><code>D-104</code></td>
            <td>D-SF6-11: SF-5 (compose-backend, alias sf1-backend) exposes workflow mutation hooks for create, update, and delete lifecycle events. SF-7 (alias sf7-library) subscribes to these hooks to keep the workflow_entity_refs reference index synchronized. The editor&#x27;s save and auto-save paths target only SF-5 CRUD, validate, and schema endpoints and carry no direct write dependency on workflow_entity_refs or SF-7 reference-index endpoints. This boundary is enforced in both the FastAPI router layer (SF-5 endpoints do not return or accept workflow_entity_refs fields) and in the editor&#x27;s API client (no requests to /api/{entity}/references/{id} on the core boot or save paths).</td>
        </tr><tr>
            <td><code>D-105</code></td>
            <td>D-SF6-12: Service ID aliases in this design: sf1-backend = compose-backend (tools/compose/backend) serving schema and workflow endpoints; sf5-shell = compose-frontend (tools/compose/frontend) authenticated shell providing auth context and route mount; sf7-library = the SF-7 library and registries surface of compose-backend serving role/schema/template/tool CRUD and owning workflow_entity_refs. All three run in the tools/compose topology on PostgreSQL 15. No SQLite, no tools/iriai-workflows, no separate plugin-management service.</td>
        </tr><tr>
            <td><code>D-106</code></td>
            <td>D-U1: Phases use expand-to-real-nodes, not mini topology thumbnails.</td>
        </tr><tr>
            <td><code>D-107</code></td>
            <td>D-U2: Templates use the same expand-to-real-nodes pattern as phases.</td>
        </tr><tr>
            <td><code>D-108</code></td>
            <td>D-U3: Template children are read-only but fully inspectable. The inspector shows values but disables edits and destructive actions.</td>
        </tr><tr>
            <td><code>D-109</code></td>
            <td>D-U4: Detaching a template group removes read-only constraints and turns stamped nodes into normal editable nodes.</td>
        </tr><tr>
            <td><code>D-110</code></td>
            <td>nodeTypes and edgeTypes objects must be defined at module scope so React Flow receives stable references.</td>
        </tr><tr>
            <td><code>D-111</code></td>
            <td>Zustand selectors must not allocate new filtered or mapped collections; derived arrays belong in component-level memoization.</td>
        </tr><tr>
            <td><code>D-112</code></td>
            <td>The palette stays on the right side of the canvas and the editor has no version-history UI.</td>
        </tr><tr>
            <td><code>D-113</code></td>
            <td>Phase creation is driven by Select-tool rectangle grouping rather than a phase palette item.</td>
        </tr><tr>
            <td><code>D-114</code></td>
            <td>Auto-save runs after 30 seconds of inactivity and beforeunload warns only when the editor is dirty.</td>
        </tr><tr>
            <td><code>D-115</code></td>
            <td>CodeMirror loads lazily the first time an inspector needs code editing.</td>
        </tr><tr>
            <td><code>D-116</code></td>
            <td>Drag operations capture one undo snapshot on drag-stop rather than pushing snapshots for each pointer move.</td>
        </tr><tr>
            <td><code>D-117</code></td>
            <td>Cross-phase rectangle selection is rejected. A new phase may only contain nodes that already share the same parent boundary.</td>
        </tr><tr>
            <td><code>D-118</code></td>
            <td>Template groups serialize as $template_ref blocks instead of expanded child nodes. version_hash is used to detect drift between save and load.</td>
        </tr><tr>
            <td><code>D-119</code></td>
            <td>D-35: CollapsedGroupCard is a fixed-size metadata card rather than a mini-canvas preview. Performance benefit comes from not rendering nested canvases in collapsed state.</td>
        </tr><tr>
            <td><code>D-120</code></td>
            <td>D-58: Three-layer component ownership remains in force. SF-1 owns type definitions, SF-7 owns pure visual primitives, and SF-6 owns thin React Flow adapters. Branch visuals come from BranchNodePrimitive with per-port condition badges and named path handles, not from SF-6 re-implementations.</td>
        </tr></tbody>
    </table>

    <h3>Risks</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Risk</th></tr></thead>
        <tbody><tr>
            <td><code>RISK-49</code></td>
            <td>RISK-1 (high): Template round-trip fidelity depends on referenced library templates remaining available. Mitigation: persist version_hash and surface drift warnings on load.</td>
        </tr><tr>
            <td><code>RISK-50</code></td>
            <td>RISK-2 (medium): React Flow performance may degrade when many expanded phases and templates are visible at once. Mitigation: memoized node wrappers, collapsed-by-default loading, and fixed collapsed bounds.</td>
        </tr><tr>
            <td><code>RISK-51</code></td>
            <td>RISK-3 (medium): Debounced inspector writes can race with undo and redo. Mitigation: flush pending debounced writes before snapshot restoration and force inspectors to re-read store state after undo.</td>
        </tr><tr>
            <td><code>RISK-52</code></td>
            <td>RISK-4 (medium): Read-only template children could be mutated indirectly through edge creation or grouping actions. Mitigation: connectionValidator and all structural store actions reject edits against read-only targets.</td>
        </tr><tr>
            <td><code>RISK-53</code></td>
            <td>RISK-5 (low): Lazy-loaded code editing still adds a meaningful bundle chunk when first opened. Mitigation: load CodeMirror only on demand; per-port condition expression editors in BranchInspector share the same lazy chunk.</td>
        </tr><tr>
            <td><code>RISK-54</code></td>
            <td>RISK-6 (medium): SF-7 primitives and picker APIs may lag SF-6 implementation. Mitigation: lock the prop contract early (BranchNodePrimitive receives paths with per-port conditionSummary, not node-level conditionType), use temporary stubs that match final signatures, and reserve the swap to real primitives as the final integration step.</td>
        </tr><tr>
            <td><code>RISK-55</code></td>
            <td>RISK-7 (medium): Collapsed group dimensions must stay explicit for both layout and edge routing. Mitigation: persist fixed collapsed dimensions in node data and reuse them in layout passes.</td>
        </tr><tr>
            <td><code>RISK-56</code></td>
            <td>RISK-8 (medium): Branch path-key drift can break serialization if a Handle ID no longer matches the paths dict key. Mitigation: paths are the single source of truth, Handle IDs are derived from the dict keys, and client validation enforces path-handle parity.</td>
        </tr><tr>
            <td><code>RISK-57</code></td>
            <td>RISK-9 (high): If SF-1, SF-2, or SF-4 retains stale node-level condition_type, condition, switch_function, or output_field fields on BranchNode, editor-authored YAML could validate locally but execute differently downstream. Mitigation: this artifact standardizes the D-GR-35 per-port non-exclusive fan-out model; the validation endpoint explicitly rejects all four stale BranchNode fields (condition_type at node level, node-level condition, switch_function, output_field); migration fixtures must include per-port condition expression assertions and zero switch_function or output_field references.</td>
        </tr><tr>
            <td><code>RISK-58</code></td>
            <td>RISK-10 (medium): If runtime /api/schema/workflow changes while local adapter types or serializer assumptions still target older field names such as root nodes, phases, or static schema copies, the editor could render stale inspectors or emit invalid YAML. Mitigation: fetch schema on editor boot, keep adapter tests against the endpoint, and maintain round-trip fixtures that assert phases[].nodes, phases[].children, per-port Branch path conditions, and absence of serialized port_type.</td>
        </tr></tbody>
    </table>
</section>
<hr/>
