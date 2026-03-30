## Broad Artifact (plan:broad)

None

---

## Decomposition

{
  "subfeatures": [
    {
      "id": "SF-1",
      "slug": "declarative-schema",
      "name": "Declarative Schema & Primitives",
      "description": "Define the YAML-primary DAG format in iriai-compose as Pydantic models and JSON Schema. Six primitive node types (Ask, Map, Fold, Loop, Branch, Plugin) with typed configuration. Typed edges with optional named transform references. Phase groupings with on_start/on_done hooks and skip conditions. Plugin interface declarations (inputs, outputs, config schema). Cost configuration metadata (budget caps, model pricing, alert thresholds per node/phase). Schema versioning field. No execution logic — this is pure data modeling and validation. Produces the schema that the loader, runner, testing framework, and composer UI all consume.",
      "rationale": "The schema is the foundational contract for the entire system. Everything else — runtime execution, testing, visual editing — depends on this format definition being stable and complete. Isolating it ensures the format is designed for all consumers, not biased toward any single one.",
      "requirement_ids": [
        "R1",
        "R2",
        "R3",
        "R4",
        "R5",
        "R6"
      ],
      "journey_ids": [
        "J-1",
        "J-2",
        "J-4"
      ]
    },
    {
      "id": "SF-2",
      "slug": "dag-loader-runner",
      "name": "DAG Loader & Runner",
      "description": "Build the YAML loader that hydrates declarative configs into executable DAG objects, and the top-level run() entry point in iriai-compose. Loader: parse YAML, validate against schema, resolve node references, build dependency graph, wire typed edges. Runner: topological sort for execution order, respect phase boundaries, execute nodes against provided AgentRuntime instances, manage artifact flow between nodes via edge transforms, resolve named transforms/hooks from a registry, handle Map (parallel fan-out), Fold (sequential accumulation), Loop (repeat-until), Branch (conditional routing), and Plugin (external service delegation). Extends existing DefaultWorkflowRunner infrastructure.",
      "rationale": "The loader and runner are tightly coupled — you can't meaningfully test loading without running, and the runner's needs (topological execution, artifact passing, transform resolution) directly inform how the loader hydrates the schema. Grouping them ensures the hydration format matches execution needs.",
      "requirement_ids": [
        "R7"
      ],
      "journey_ids": [
        "J-2"
      ]
    },
    {
      "id": "SF-3",
      "slug": "testing-framework",
      "name": "Testing Framework",
      "description": "Build iriai_compose.testing — a purpose-built testing module for declarative workflows. Schema validation: structural correctness, type flow across edges, required fields, cycle detection. Execution testing: mock/echo AgentRuntime that records calls and returns configurable responses, execution path assertions (assert node X reached before node Y, assert artifact produced at key K, assert branch took path P), snapshot testing for YAML round-trips. Test fixtures: helpers to build minimal valid workflows programmatically for unit tests. Extends existing MockAgentRuntime from conftest.py. This framework is used by SF-4 (migration) to prove the litmus test and by any future workflow developer for regression testing.",
      "rationale": "A dedicated testing framework is distinct from both the runtime (SF-2) and the migration (SF-4). It produces reusable infrastructure — mock runtimes, assertion helpers, fixtures — that the migration exercises but doesn't define. Keeping it separate ensures the framework is general-purpose, not migration-specific.",
      "requirement_ids": [
        "R8",
        "R23"
      ],
      "journey_ids": [
        "J-2"
      ]
    },
    {
      "id": "SF-4",
      "slug": "workflow-migration",
      "name": "Workflow Migration & Litmus Test",
      "description": "Translate iriai-build-v2's three workflows (planning, develop, bugfix) from imperative Python to declarative YAML. Planning: 6 phases (scoping, PM, design, architecture, plan review, task planning) with patterns including broad interview loops, decomposition with gate, per-subfeature Fold with tiered context assembly, integration review, gate-and-revise loops, compilation, and interview-based gate review. Develop: DAG execution groups (parallel within group, sequential across), per-group verification with retry, handover document compression, QA → review → user approval loop. Bugfix: linear 8-phase flow with parallel RCA (dual analyst), diagnosis-and-fix retry loop, preview server plugin integration. Register all required named transforms (tiered context builder, handover compression, feedback formatting, etc.) and hooks. Write comprehensive test suites using the SF-3 testing framework proving execution path equivalence.",
      "rationale": "The migration is both the completeness proof for the schema (SF-1) and the first real content in the system. It requires deep analysis of iriai-build-v2's imperative code — a fundamentally different skill from schema design or framework building. Keeping it separate lets the migration reveal schema gaps without being conflated with schema development.",
      "requirement_ids": [
        "R9"
      ],
      "journey_ids": [
        "J-2"
      ]
    },
    {
      "id": "SF-5",
      "slug": "composer-app-foundation",
      "name": "Composer App Foundation & Tools Hub",
      "description": "Scaffold the iriai-workflows webapp (React + FastAPI + SQLite) and the tools.iriai.app hub. Backend: FastAPI app structure, SQLAlchemy models for all 8 data entities (Workflow, WorkflowVersion, Role, OutputSchema, CustomTaskTemplate, PhaseTemplate, PluginConfig, TransformFunction), Alembic migrations, CRUD API endpoints for all entities, JWT auth via auth-python (JWKS validation), user_id scoping on all resources. Frontend: React app with routing, auth-react integration (login/logout, token management), Windows XP / MS Paint design system (purple gradients, 3D beveled effects, frosted glass taskbar — matching deploy-console), Workflows List landing page (grid/list of saved configs, create/duplicate/import/delete/search). Tools hub: minimal React SPA at tools.iriai.app reading dev_tier JWT claim, displaying tier-gated tool cards, linking to composer URL. Railway deployment configs for both apps.",
      "rationale": "The app foundation provides the infrastructure (auth, database, API, routing, design system) that both the editor (SF-6) and libraries (SF-7) build on. Including the tools hub here is natural — it's a single page sharing the same auth setup. This subfeature can be developed in parallel with the iriai-compose work (SF-1 through SF-4).",
      "requirement_ids": [
        "R10",
        "R11",
        "R12"
      ],
      "journey_ids": [
        "J-1"
      ]
    },
    {
      "id": "SF-6",
      "slug": "workflow-editor",
      "name": "Workflow Editor & Canvas",
      "description": "Build the primary workflow editing experience in iriai-workflows. React Flow DAG canvas as the main editing surface with drag-and-drop node placement. Node palette sidebar with all 6 primitives (Ask, Map, Fold, Loop, Branch, Plugin) plus custom task templates and phase templates from libraries. Collapsible YAML pane with bidirectional sync (canvas ↔ YAML, lossless round-trip). Node inspector panel with context-specific configuration: Ask (role picker/inline creator, prompt template editor with {{ variable }} interpolation, output schema selector, hooks, settings), Map/Fold/Loop (collection source, inline sub-canvas for body, max parallelism/iterations), Branch (condition type, named output paths). Edge inspector with transform selection and type annotations. Phase grouping as visual bounding boxes (select nodes → group into phase → configure hooks/skip conditions). Toolbar: save, export YAML, validate (type flow checking, required fields, error highlighting on canvas), version history access, undo/redo. Performance target: responsive with 50+ nodes.",
      "rationale": "The editor is the core user-facing deliverable — the visual canvas, node inspectors, YAML sync, and validation. It's the largest and most complex frontend subfeature. It consumes the schema (SF-1) to know what fields each node type needs, and consumes libraries (SF-7) for role/schema/template selection. Keeping it separate from libraries allows parallel development of the editing experience and the management surfaces.",
      "requirement_ids": [
        "R13",
        "R14",
        "R15"
      ],
      "journey_ids": [
        "J-1",
        "J-3",
        "J-4",
        "J-5"
      ]
    },
    {
      "id": "SF-7",
      "slug": "libraries-registries",
      "name": "Libraries & Registries",
      "description": "Build all six library/registry pages in iriai-workflows, plus the version history view. All follow a shared CRUD + list + detail/editor pattern. Roles Library: system prompt editor, tool selector, model picker, metadata fields, import/export CLAUDE.md format, inline-to-library promotion flow from the editor. Output Schemas Library: JSON Schema editor (raw editor, not visual field builder), name/description metadata, referenced by Ask nodes. Custom Task Templates: saved subgraph compositions with defined input/output interfaces, appear in node palette alongside primitives, expandable to inspect internal structure. Phases Library: saved phase templates (node groups + hooks + skip conditions), droppable into workflows as reusable units. Plugins Registry: browse available plugin types, configure instances with parameter schemas, see I/O type declarations, configured instances appear in node palette. Transforms & Hooks Library: named pure functions with input/output type signatures, code preview, used as edge transforms and node hooks. Version History: per-workflow version list, YAML diff between versions, restore to previous version.",
      "rationale": "All six libraries share the same UI pattern (list → detail → editor) and API pattern (CRUD endpoints scoped to user_id). Grouping them enables shared component extraction (list views, search/filter, editor chrome) and consistent UX. Individually each library is small; together they form a coherent subfeature of comparable complexity to the editor.",
      "requirement_ids": [
        "R16",
        "R17",
        "R18",
        "R19",
        "R20",
        "R21",
        "R22"
      ],
      "journey_ids": [
        "J-1",
        "J-2",
        "J-3",
        "J-5"
      ]
    }
  ],
  "edges": [
    {
      "from_subfeature": "SF-1",
      "to_subfeature": "SF-2",
      "interface_type": "python_import",
      "description": "Loader imports Pydantic schema models (WorkflowConfig, NodeDefinition, EdgeDefinition, PhaseDefinition, etc.) to parse and validate YAML into typed objects. Runner imports node type enums and config models to dispatch execution.",
      "data_contract": "iriai_compose.declarative.schema module exports: WorkflowConfig, AskNode, MapNode, FoldNode, LoopNode, BranchNode, PluginNode, Edge, Phase, CostConfig, TransformRef, HookRef. All are Pydantic BaseModel subclasses with JSON Schema generation via model_json_schema().",
      "owner": "SF-1",
      "citations": [
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/tasks.py",
          "excerpt": "Existing task types (Ask, Interview, Gate, Choose, Respond) as dataclass models",
          "reasoning": "New schema models follow the same pattern but as Pydantic models for YAML/JSON validation"
        }
      ]
    },
    {
      "from_subfeature": "SF-1",
      "to_subfeature": "SF-3",
      "interface_type": "python_import",
      "description": "Testing framework imports schema models to validate structural correctness and type flow. Uses model_json_schema() for schema-level validation, field accessors for type flow checking across edges.",
      "data_contract": "Same schema module as SF-2 consumes. Additionally uses Edge.transform_ref and Node.output_type for type flow analysis.",
      "owner": "SF-1",
      "citations": [
        {
          "type": "decision",
          "reference": "D-10",
          "excerpt": "Custom testing framework built as we develop the schema",
          "reasoning": "Testing framework validates schema correctness as a primary function"
        }
      ]
    },
    {
      "from_subfeature": "SF-2",
      "to_subfeature": "SF-3",
      "interface_type": "python_import",
      "description": "Testing framework uses the runner's run() function and DAG executor to run workflows against mock runtimes. Wraps run() with assertion hooks to track execution paths, artifact production, and branch decisions.",
      "data_contract": "iriai_compose.declarative.run(yaml_path, runtime, workspace, transform_registry, hook_registry) → ExecutionResult. ExecutionResult contains: nodes_executed (ordered list), artifacts (dict), branch_paths_taken (dict), cost_summary.",
      "owner": "SF-2",
      "citations": [
        {
          "type": "code",
          "reference": "iriai-compose/tests/conftest.py",
          "excerpt": "MockAgentRuntime records calls with role, prompt, output_type",
          "reasoning": "Testing framework extends this mock pattern to work with the new runner"
        }
      ]
    },
    {
      "from_subfeature": "SF-1",
      "to_subfeature": "SF-4",
      "interface_type": "yaml_schema",
      "description": "Migration produces YAML files conforming to the schema defined in SF-1. The schema must be expressive enough to represent all patterns found in iriai-build-v2's three workflows.",
      "data_contract": "YAML files validated against WorkflowConfig JSON Schema. Migration may surface schema gaps that require SF-1 revisions.",
      "owner": "SF-1",
      "citations": [
        {
          "type": "decision",
          "reference": "D-11",
          "excerpt": "Migration plan for converting existing iriai-build-v2 workflows",
          "reasoning": "Migration is the completeness test for the schema"
        }
      ]
    },
    {
      "from_subfeature": "SF-2",
      "to_subfeature": "SF-4",
      "interface_type": "python_import",
      "description": "Migration uses run() to execute translated YAML workflows and verify they produce equivalent behavior to the imperative Python versions.",
      "data_contract": "Same run() interface as SF-3 consumes. Migration also registers named transforms and hooks via TransformRegistry.register(name, fn) and HookRegistry.register(name, fn).",
      "owner": "SF-2",
      "citations": [
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py",
          "excerpt": "_build_subfeature_context(), _format_feedback(), to_str()",
          "reasoning": "These imperative helpers must be registered as named transforms for the runner to resolve"
        }
      ]
    },
    {
      "from_subfeature": "SF-3",
      "to_subfeature": "SF-4",
      "interface_type": "python_import",
      "description": "Migration writes test suites using the testing framework's assertion helpers, mock runtimes, and fixtures to prove execution path equivalence.",
      "data_contract": "iriai_compose.testing exports: MockRuntime (configurable responses per role/node), assert_node_reached(result, node_id), assert_artifact_produced(result, key, schema), assert_branch_taken(result, branch_id, path), WorkflowTestCase base class.",
      "owner": "SF-3",
      "citations": [
        {
          "type": "decision",
          "reference": "D-10",
          "excerpt": "Custom testing framework built as we develop the schema",
          "reasoning": "Migration is the primary consumer of the testing framework"
        }
      ]
    },
    {
      "from_subfeature": "SF-1",
      "to_subfeature": "SF-6",
      "interface_type": "json_schema",
      "description": "The workflow editor reads the JSON Schema (generated from SF-1's Pydantic models) to know what fields each node type requires, what edge types are valid, and what configuration options exist. The YAML pane serializes/deserializes using this schema. Validation uses it for type flow checking.",
      "data_contract": "JSON Schema published as a static artifact (e.g., workflow-schema.json) or fetched from a backend endpoint. Frontend uses it for: node inspector field generation, edge type validation, YAML syntax validation, export format.",
      "owner": "SF-1",
      "citations": [
        {
          "type": "decision",
          "reference": "D-15",
          "excerpt": "Dual-pane with visual graph editor primary, YAML secondary",
          "reasoning": "Both the canvas and YAML pane need to understand the schema for rendering and validation"
        }
      ]
    },
    {
      "from_subfeature": "SF-5",
      "to_subfeature": "SF-6",
      "interface_type": "api_and_components",
      "description": "App foundation provides: authenticated API client (axios with JWT interceptor), React router shell (editor is a route), design system components (XP-themed buttons, panels, inputs), database-backed workflow CRUD (save/load/export endpoints), and auth context (user_id for scoping).",
      "data_contract": "API endpoints: GET/PUT /api/workflows/:id (full YAML content), POST /api/workflows/:id/versions (save new version), POST /api/workflows/:id/validate (server-side validation). React context: useAuth() hook providing user, accessToken. Component library: XPButton, XPPanel, XPInput, XPToolbar, XPSidebar.",
      "owner": "SF-5",
      "citations": [
        {
          "type": "code",
          "reference": "platform/deploy-console/deploy-console-frontend/src/app/styles/windows-xp.css",
          "excerpt": "XP-style inset/outset borders, purple gradients, frosted glass taskbar",
          "reasoning": "Design system components from SF-5 are consumed by the editor"
        }
      ]
    },
    {
      "from_subfeature": "SF-5",
      "to_subfeature": "SF-7",
      "interface_type": "api_and_components",
      "description": "App foundation provides the same infrastructure as SF-6: authenticated API client, router shell (library pages are routes), design system components, and CRUD API endpoints for all 8 entity types.",
      "data_contract": "API endpoints: standard REST CRUD for /api/roles, /api/schemas, /api/templates, /api/phases, /api/plugins, /api/transforms. All scoped to authenticated user_id. Response format: { items: [...], total: int } for lists, individual entity for detail. Same React context and component library as SF-6.",
      "owner": "SF-5",
      "citations": [
        {
          "type": "decision",
          "reference": "D-14",
          "excerpt": "Screen map confirmed with workflows list as landing page",
          "reasoning": "Library pages are sibling routes to the workflows list, all sharing the app shell"
        }
      ]
    },
    {
      "from_subfeature": "SF-7",
      "to_subfeature": "SF-6",
      "interface_type": "react_components",
      "description": "Libraries expose picker/selector components consumed by the editor's node inspectors. Role picker for Ask nodes, schema selector for output_type, template browser for the node palette, plugin selector, transform picker for edge inspector.",
      "data_contract": "React components: RolePicker({ onSelect, onCreateInline }), SchemaPicker({ onSelect }), TemplateBrowser({ onDrag }), PluginPicker({ onSelect }), TransformPicker({ edgeType, onSelect }). Each fetches from its own API endpoint and renders in the XP design system.",
      "owner": "SF-7",
      "citations": [
        {
          "type": "decision",
          "reference": "D-18",
          "excerpt": "Inline + library hybrid for roles",
          "reasoning": "The editor needs picker components that bridge to the library data"
        }
      ]
    },
    {
      "from_subfeature": "SF-6",
      "to_subfeature": "SF-7",
      "interface_type": "callback_events",
      "description": "Editor triggers library mutations: inline role creation promotes to library, subgraph selection saves as custom task template, node group saves as phase template. Editor emits these as callbacks that library components handle.",
      "data_contract": "Callbacks: onPromoteRole(inlineRole) → creates Role via API, onSaveTemplate(selectedNodes, edges, interface) → creates CustomTaskTemplate via API, onSavePhase(selectedNodes, hooks, skipConditions) → creates PhaseTemplate via API. Returns created entity ID for the editor to reference.",
      "owner": "SF-6",
      "citations": [
        {
          "type": "decision",
          "reference": "D-18",
          "excerpt": "Inline + library hybrid for roles",
          "reasoning": "Inline-to-library promotion requires the editor to trigger library writes"
        }
      ]
    }
  ],
  "decomposition_rationale": "The feature splits naturally along two axes: iriai-compose runtime (schema → loader → testing → migration) and iriai-workflows visual app (foundation → editor → libraries). The iriai-compose side forms a strict dependency chain where each layer builds on the previous. The iriai-workflows side has a foundation layer feeding two parallel workstreams (editor and libraries) that integrate at the edges. The tools hub is absorbed into the app foundation since it's a single page sharing the same auth infrastructure. This yields 7 subfeatures of roughly comparable complexity, with clear boundaries and explicit interface contracts between them.",
  "complete": true
}

---

---

## Subfeature: Declarative Schema & Primitives (declarative-schema)

<!-- SF: declarative-schema -->
<section id="sf-declarative-schema" class="subfeature-section">
    <h2>SF-1 Declarative Schema &amp; Primitives</h2>
    <div class="provenance">Subfeature: <code>declarative-schema</code></div>

    <h3>Overview</h3>
    <div class="overview-text">SF-1 introduces iriai_compose/schema/, a pure-data Pydantic v2 subpackage defining the declarative workflow format for iriai-compose. Four key invariants: (1) actors discriminate on actor_type with only agent|human as valid values; (2) BranchNode uses per-port conditions on BranchOutputPort entries with optional merge_function for gather per D-GR-35 — switch_function is REJECTED; (3) WorkflowConfig root is closed to schema_version, workflow_version, name, description, metadata, actors, phases, edges, templates, plugins, types, cost_config, and context_keys only; (4) composer fetches JSON Schema at runtime from /api/schema/workflow while static workflow-schema.json is build/test-only. Four atomic node types: AskNode, BranchNode, PluginNode, and ErrorNode per D-GR-36. Cost config is split into three scoped types: WorkflowCostConfig (workflow.cost_config), PhaseCostConfig (phase.cost), NodeCostConfig (node.cost). AskNode.prompt is the sole canonical field for the task prompt string — not &#x27;task&#x27;, not &#x27;context_text&#x27;. Context injection uses context_keys at node/actor/phase/workflow levels. The schema module exports exactly: WorkflowConfig, PhaseDefinition, AskNode, BranchNode, PluginNode, ErrorNode, NodeDefinition, EdgeDefinition, ActorDefinition, AgentActorDef, HumanActorDef, RoleDefinition, PortDefinition, BranchOutputPort, WorkflowInputDefinition, WorkflowOutputDefinition, TypeDefinition, PluginInterface, TemplateDefinition, SequentialModeConfig, MapModeConfig, FoldModeConfig, LoopModeConfig, ModeConfig, WorkflowCostConfig, PhaseCostConfig, NodeCostConfig, ValidationError — and the functions load_workflow, dump_workflow, validate_workflow, validate_type_flow, detect_cycles, build_port_index, resolve_port_type, is_hook_source, parse_port_ref, generate_json_schema. MapNode, FoldNode, LoopNode, TransformRef, HookRef, and PluginInstanceConfig are phantom types that do not exist in this package.</div>

    <h3>Services</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Name</th><th>Kind</th><th>Description</th><th>Technology</th><th>Port</th><th>Journeys</th>
        </tr></thead>
        <tbody><tr>
            <td><code>SVC-1</code></td>
            <td><strong>iriai-compose-schema</strong></td>
            <td><code>service</code></td>
            <td>New iriai_compose/schema/ subpackage. Pure data layer: Pydantic v2 models defining the declarative workflow format. Exports exactly the canonical symbol set — no MapNode, FoldNode, LoopNode, TransformRef, or HookRef. CostConfig is three distinct types: WorkflowCostConfig, PhaseCostConfig, NodeCostConfig. Phase is PhaseDefinition. WorkflowConfig.context_keys: list[str] is a valid root field. AskNode.prompt is canonical (not task/context_text). No imports from runtime classes.</td>
            <td><code>Python 3.11+ / Pydantic v2</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3, J-4, J-5, J-6, J-7</td>
        </tr><tr>
            <td><code>SVC-2</code></td>
            <td><strong>iriai-compose-runtime</strong></td>
            <td><code>service</code></td>
            <td>Existing iriai_compose runtime package (runner.py, actors.py, tasks.py, workflow.py). SF-2 consumes SVC-1&#x27;s declarative models. run() canonical signature: run(workflow: WorkflowConfig, config: RuntimeConfig, *, inputs: dict | None = None) -&gt; ExecutionResult. NOT run(yaml_path, runtime, workspace, transform_registry, hook_registry). Maps human actor_type to InteractionActor runtime boundary. Nested children used as recursive phase containment.</td>
            <td><code>Python 3.11+</code></td>
            <td>—</td>
            <td>J-7</td>
        </tr><tr>
            <td><code>SVC-3</code></td>
            <td><strong>yaml-workflow-files</strong></td>
            <td><code>database</code></td>
            <td>.yaml workflow files on filesystem. WorkflowConfig root is closed; nodes serialize only under phases[].nodes; nested phases under phases[].children; BranchNode paths use name-keyed mappings; hook ports are ordinary edges with no port_type field.</td>
            <td><code>YAML on filesystem</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3, J-7</td>
        </tr><tr>
            <td><code>SVC-4</code></td>
            <td><strong>json-schema-artifact</strong></td>
            <td><code>database</code></td>
            <td>Build/test-only artifact. Static workflow-schema.json generated from model_json_schema() via python -m iriai_compose.schema.json_schema. Used for CI validation, offline tooling, and test fixtures. NOT the runtime schema source for the composer editor.</td>
            <td><code>JSON file</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-5</code></td>
            <td><strong>iriai-compose-testing</strong></td>
            <td><code>service</code></td>
            <td>SF-3 testing subpackage. Imports schema models and validation helpers from iriai_compose.schema. Test fixtures cover actor_type agent|human, BranchNode per-port condition routing per D-GR-35, path resolution, switch_function rejection, plugin_instances rejection, ErrorNode (D-GR-36), nested phase containment, context_keys at all hierarchy levels including WorkflowConfig root.</td>
            <td><code>Python 3.11+ / pytest</code></td>
            <td>—</td>
            <td>J-3, J-7</td>
        </tr><tr>
            <td><code>SVC-6</code></td>
            <td><strong>iriai-build-v2-workflows</strong></td>
            <td><code>external</code></td>
            <td>Existing imperative planning, develop, and bugfix workflows used as migration reference. Imperative gate/if-else/actor patterns translate into BranchNode condition_type/condition/paths plus nested phase containment; interaction actors translate to human actor_type.</td>
            <td><code>Python 3.11+</code></td>
            <td>—</td>
            <td>J-2, J-7</td>
        </tr><tr>
            <td><code>SVC-7</code></td>
            <td><strong>compose-backend</strong></td>
            <td><code>service</code></td>
            <td>SF-5 composer-app-foundation FastAPI backend at tools/compose/. Provides workflow CRUD API, schema delivery, YAML import/export, and mounts the SF-7 registries router. Uses load_workflow(), validate_workflow(), and WorkflowConfig.model_json_schema() from SVC-1. PostgreSQL 15+ for persistence.</td>
            <td><code>Python / FastAPI / PostgreSQL</code></td>
            <td>8000</td>
            <td>J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-8</code></td>
            <td><strong>compose-frontend</strong></td>
            <td><code>frontend</code></td>
            <td>SF-6 workflow-editor React SPA at tools/compose/frontend/. Fetches JSON Schema at runtime from GET /api/schema/workflow before rendering editor inspectors. Calls workflow CRUD endpoints on SVC-7 and registry CRUD endpoints on SVC-9. Surfaces explicit schema-load error on failure; no fallback to static workflow-schema.json.</td>
            <td><code>React 19 / React Flow / TypeScript</code></td>
            <td>—</td>
            <td>J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-9</code></td>
            <td><strong>compose-registries</strong></td>
            <td><code>service</code></td>
            <td>SF-7 libraries-registries logical module mounted into SVC-7 at /api/registries/ prefix. Owns role/tool CRUD, actor_slots persistence, workflow_entity_refs reference index, and delete preflight checks. Runs in the same process as SVC-7 but owns distinct route and DB-access responsibility.</td>
            <td><code>Python / FastAPI / PostgreSQL</code></td>
            <td>—</td>
            <td>J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-10</code></td>
            <td><strong>workflow-migration-cli</strong></td>
            <td><code>service</code></td>
            <td>SF-4 migration tooling CLI. Reads iriai-build-v2 imperative Python workflow classes and emits declarative WorkflowConfig YAML. Imports from iriai_compose.schema. Key contract: AskNode field is prompt (not task/context_text); WorkflowConfig.context_keys is a valid root field; Phase becomes PhaseDefinition.</td>
            <td><code>Python 3.11+</code></td>
            <td>—</td>
            <td>J-2, J-7</td>
        </tr></tbody>
    </table>

    <h3>Connections</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>To</th><th>Protocol</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>CONN-1</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python file I/O</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-2</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python file I/O</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-3</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python file I/O</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-4</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-5</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-6</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-7</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-8</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-9</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python file I/O</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-10</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTP file upload</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-11</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-12</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-13</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-14</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-15</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>API Endpoints</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Method</th><th>Path</th><th>Service</th><th>Description</th><th>Auth</th>
        </tr></thead>
        <tbody><tr>
            <td><code>API-1</code></td>
            <td><code>GET</code></td>
            <td><code>load_workflow(path)</code></td>
            <td><code></code></td>
            <td>Load YAML and deserialize to WorkflowConfig. Rejects stale fields. Hydrates BranchNode.paths, nested children, context_keys at all levels.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-2</code></td>
            <td><code>POST</code></td>
            <td><code>dump_workflow(config, path?)</code></td>
            <td><code></code></td>
            <td>Serialize WorkflowConfig to YAML string or file. Emits BranchNode.paths, actor_type: agent|human, nested phases[].children, context_keys, hook ports as ordinary edges. Never emits switch_function, stores, plugin_instances, or port_type.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-3</code></td>
            <td><code>POST</code></td>
            <td><code>validate_workflow(config)</code></td>
            <td><code></code></td>
            <td>Full structural validation: ref resolution, type flow, hook-edge constraints, BranchNode path validation, switch_function rejection, condition_type enforcement, actor_type validation (agent|human only), closed-root-field rejection (stores, plugin_instances, context_text), expression limits, nested containment.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-4</code></td>
            <td><code>POST</code></td>
            <td><code>validate_type_flow(config)</code></td>
            <td><code></code></td>
            <td>Edge type compatibility check only. Resolves source output types from typed port definitions including BranchNode path ports.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-5</code></td>
            <td><code>GET</code></td>
            <td><code>detect_cycles(config)</code></td>
            <td><code></code></td>
            <td>DFS cycle detection across each phase&#x27;s edge graph including nested children.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-6</code></td>
            <td><code>GET</code></td>
            <td><code>generate_json_schema(path?)</code></td>
            <td><code></code></td>
            <td>Export JSON Schema from Pydantic models via model_json_schema(). Build/test artifact only. Includes WorkflowConfig.context_keys, WorkflowCostConfig/PhaseCostConfig/NodeCostConfig, actor_type: agent|human union, BranchNode.paths. NOT the editor runtime source.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-7</code></td>
            <td><code>GET</code></td>
            <td><code>build_port_index(config)</code></td>
            <td><code></code></td>
            <td>Build index mapping node_id.port_name to {container, port}. BranchNode routeable outputs indexed from paths under container &#x27;paths&#x27;.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-8</code></td>
            <td><code>GET</code></td>
            <td><code>resolve_port_type(port, node?)</code></td>
            <td><code></code></td>
            <td>Resolve effective type for a typed port definition. Priority: explicit type_ref &gt; explicit schema_def &gt; node/phase-level fallback &gt; any.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-9</code></td>
            <td><code>GET</code></td>
            <td><code>is_hook_source(source_str, port_index)</code></td>
            <td><code></code></td>
            <td>Return true when the source port string resolves to a hooks-container port. Used by validation to enforce hook edge rules without requiring serialized port_type.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-10</code></td>
            <td><code>GET</code></td>
            <td><code>parse_port_ref(ref)</code></td>
            <td><code></code></td>
            <td>Split node_id.port_name into (node_id, port_name). For BranchNode, port_name resolution checks paths before outputs.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-11</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schema/workflow</code></td>
            <td><code></code></td>
            <td>Canonical runtime schema delivery endpoint for the composer frontend. Calls WorkflowConfig.model_json_schema() at request time. Returns live contract including WorkflowConfig.context_keys, WorkflowCostConfig, BranchNode.paths per-port conditions. Frontend blocks initialization until this succeeds.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-12</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows</code></td>
            <td><code></code></td>
            <td>List workflows for the authenticated user. Response: {workflows: WorkflowSummary[], total: int}. WorkflowSummary: {id, name, description, workflow_version, schema_version, updated_at, created_at}.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-13</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Fetch full workflow detail. Response: {workflow: WorkflowDetail}. WorkflowDetail extends WorkflowSummary and adds config: WorkflowConfigJSON (raw JSON matching WorkflowConfig schema).</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-14</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows</code></td>
            <td><code></code></td>
            <td>Create a new workflow. Body: {name: str, description?: str, config: WorkflowConfigJSON}. Runs validate_workflow(); returns 422 with ValidationError list on failure. Returns: {workflow: WorkflowDetail}.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-15</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Update workflow config. Body: {config: WorkflowConfigJSON}. Runs validate_workflow(); increments workflow_version. Returns: {workflow: WorkflowDetail}.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-16</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Delete a workflow. Calls SVC-9 purge_workflow_refs() before deletion. Returns 204 No Content.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-17</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/{id}/validate</code></td>
            <td><code></code></td>
            <td>Validate workflow config on demand without saving. Runs validate_workflow(), validate_type_flow(), detect_cycles(). Returns full error list.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-18</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/import</code></td>
            <td><code></code></td>
            <td>Import YAML workflow file. Multipart form: file field. Runs load_workflow() then validate_workflow(). Returns: {workflow: WorkflowDetail}.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-19</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/{id}/export</code></td>
            <td><code></code></td>
            <td>Export workflow as YAML file. Calls dump_workflow(). Content-Type: application/x-yaml. Content-Disposition: attachment; filename={slug}.yaml.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-20</code></td>
            <td><code>GET</code></td>
            <td><code>/api/registries/roles</code></td>
            <td><code></code></td>
            <td>List all roles in the registry. Response: {roles: RoleEntry[], total: int}. RoleEntry: {id, name, prompt, tools: string[], updated_at}.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-21</code></td>
            <td><code>POST</code></td>
            <td><code>/api/registries/roles</code></td>
            <td><code></code></td>
            <td>Create a role. Body: {name: str, prompt: str, tools: string[]}. Returns: RoleEntry.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-22</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/registries/roles/{id}</code></td>
            <td><code></code></td>
            <td>Update a role. Body: {name?, prompt?, tools?}. Returns: RoleEntry.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-23</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/registries/roles/{id}</code></td>
            <td><code></code></td>
            <td>Delete a role. Checks workflow_entity_refs for active references first. Returns 204 on success; 409 {conflicts: string[]} listing workflow IDs if role is in use.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-24</code></td>
            <td><code>GET</code></td>
            <td><code>/api/registries/roles/{id}/refs</code></td>
            <td><code></code></td>
            <td>Get reference count for a role. Returns: {count: int, workflow_ids: string[]}. Used by frontend to render delete preflight confirmation.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-25</code></td>
            <td><code>GET</code></td>
            <td><code>/api/registries/tools</code></td>
            <td><code></code></td>
            <td>List all tools in the registry. Response: {tools: ToolEntry[], total: int}. ToolEntry: {id, name, description, config_schema}.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-26</code></td>
            <td><code>POST</code></td>
            <td><code>/api/registries/tools</code></td>
            <td><code></code></td>
            <td>Create a tool. Body: {name: str, description?: str, config_schema?: object}. Returns: ToolEntry.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-27</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/registries/tools/{id}</code></td>
            <td><code></code></td>
            <td>Update a tool. Returns: ToolEntry.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-28</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/registries/tools/{id}</code></td>
            <td><code></code></td>
            <td>Delete a tool. Checks workflow_entity_refs. Returns 204 | 409 {conflicts: string[]}.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-29</code></td>
            <td><code>GET</code></td>
            <td><code>/api/registries/tools/{id}/refs</code></td>
            <td><code></code></td>
            <td>Get reference count for a tool. Returns: {count: int, workflow_ids: string[]}.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-30</code></td>
            <td><code>GET</code></td>
            <td><code>/api/registries/actor-slots/{workflow_id}</code></td>
            <td><code></code></td>
            <td>Get actor slots for a workflow. Returns: {slots: ActorSlot[]}. ActorSlot: {id, workflow_id, actor_key, actor_type: &#x27;agent&#x27;|&#x27;human&#x27;, role_id}.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-31</code></td>
            <td><code>POST</code></td>
            <td><code>/api/registries/actor-slots</code></td>
            <td><code></code></td>
            <td>Persist an actor slot. Body: {workflow_id, actor_key, actor_type, role_id?}. Upserts by (workflow_id, actor_key). Returns: ActorSlot.</td>
            <td><code>Bearer JWT</code></td>
        </tr><tr>
            <td><code>API-32</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/registries/actor-slots/{id}</code></td>
            <td><code></code></td>
            <td>Delete an actor slot by ID. Returns 204.</td>
            <td><code>Bearer JWT</code></td>
        </tr></tbody>
    </table>

    <h3>Call Paths</h3>
    <div class="call-path-block">
            <h4><code>CP-1</code>: Developer authors a YAML workflow using nested phases[].nodes, phases[].children, actor_type: agent|human, BranchNode condition_type/condition/paths, context_keys at all levels, and the closed WorkflowConfig root.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;developer&#x27;, &#x27;to_service&#x27;: &#x27;SVC-3&#x27;, &#x27;action&#x27;: &#x27;Write YAML file&#x27;, &#x27;description&#x27;: &quot;Author workflow definition. Nodes under phases[].nodes; nested phases under phases[].children; actors use actor_type: agent|human; AskNode uses &#x27;prompt&#x27; field (not &#x27;task&#x27;); context_keys valid at workflow/phase/actor/node levels.&quot;, &#x27;returns&#x27;: &#x27;.yaml on filesystem&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;load_workflow(path)&#x27;, &#x27;description&#x27;: &#x27;Parse YAML, desugar bare-string shorthand, hydrate BranchNode.paths, resolve nested children recursively, reject stale fields.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig or ValidationError&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;validate_workflow(config)&#x27;, &#x27;description&#x27;: &#x27;Full validation: actor_type (agent|human only), closed-root rejection, edge resolution, BranchNode per-port conditions, nested containment, hook-edge rules.&#x27;, &#x27;returns&#x27;: &#x27;list[ValidationError]&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-2</code>: Build tooling generates workflow-schema.json from Pydantic models for CI validation and offline tooling only. NOT the editor runtime source.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;build_script&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;generate_json_schema(path)&#x27;, &#x27;description&#x27;: &#x27;Invoke model_json_schema(). Artifact contains actor_type: agent|human union, BranchNode.paths per-port conditions, WorkflowConfig.context_keys, WorkflowCostConfig/PhaseCostConfig/NodeCostConfig, nested PhaseDefinition.children. No phantom types.&#x27;, &#x27;returns&#x27;: &#x27;JSON Schema dict&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-4&#x27;, &#x27;action&#x27;: &#x27;Write JSON file&#x27;, &#x27;description&#x27;: &#x27;Serialize schema dict to workflow-schema.json for CI. Must not be imported at runtime by the editor.&#x27;, &#x27;returns&#x27;: &#x27;Static JSON artifact&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-3</code>: Full validation pipeline covering identifiers, refs, edge resolution, hook constraints, phase boundaries, cycles, reachability, type flow, BranchNode per-port semantics (D-GR-35), actor_type enforcement, closed-root rejection, expression limits, and stale-field rejection.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;caller&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;validate_workflow(config)&#x27;, &#x27;description&#x27;: &#x27;Entry point. Builds port index including BranchNode.paths and dispatches all validation checks.&#x27;, &#x27;returns&#x27;: &#x27;list[ValidationError]&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_duplicate_ids()&#x27;, &#x27;description&#x27;: &#x27;Scan all phases and children recursively for node/phase ID collisions.&#x27;, &#x27;returns&#x27;: &#x27;duplicate_id errors&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_actor_refs_and_types()&#x27;, &#x27;description&#x27;: &quot;Verify each AskNode.actor references workflow.actors AND that every ActorDefinition uses actor_type in [&#x27;agent&#x27;,&#x27;human&#x27;]. Reject &#x27;interaction&#x27; alias.&quot;, &#x27;returns&#x27;: &#x27;invalid_actor_ref errors, invalid_actor_type errors&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_phase_configs()&#x27;, &#x27;description&#x27;: &#x27;Verify mode-specific mode_config presence, loop dual-exit port requirements, nested children containment.&#x27;, &#x27;returns&#x27;: &#x27;invalid_phase_mode_config errors&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_edges()&#x27;, &#x27;description&#x27;: &#x27;Resolve node_id.port_name references via dict lookup on inputs, outputs, hooks, or BranchNode.paths within the owning phase.&#x27;, &#x27;returns&#x27;: &#x27;dangling_edge errors&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_hook_edge_constraints()&#x27;, &#x27;description&#x27;: &#x27;Identify hook-sourced edges and enforce transform_fn=None.&#x27;, &#x27;returns&#x27;: &#x27;invalid_hook_edge_transform errors&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_cycles()&#x27;, &#x27;description&#x27;: &#x27;Detect cycles within each phase graph and nested children.&#x27;, &#x27;returns&#x27;: &#x27;cycle_detected errors&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_branch_per_port_conditions()&#x27;, &#x27;description&#x27;: &#x27;Verify each BranchNode.paths entry has valid per-port condition. Reject switch_function. merge_function valid only on gather (multi-input) BranchNodes per D-GR-35.&#x27;, &#x27;returns&#x27;: &#x27;invalid_branch_config errors&#x27;}</li><li>{&#x27;sequence&#x27;: 9, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_rejected_root_fields()&#x27;, &#x27;description&#x27;: &#x27;Reject stores, plugin_instances, context_text, and any field not in the PRD-canonical closed set (which NOW includes context_keys as a valid root field per D-GR-41).&#x27;, &#x27;returns&#x27;: &#x27;unsupported_root_field errors&#x27;}</li><li>{&#x27;sequence&#x27;: 10, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_expression_limits()&#x27;, &#x27;description&#x27;: &#x27;Enforce expression size limits on BranchOutputPort.condition strings and other expression-backed fields.&#x27;, &#x27;returns&#x27;: &#x27;expression_limit errors&#x27;}</li><li>{&#x27;sequence&#x27;: 11, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_type_flow()&#x27;, &#x27;description&#x27;: &#x27;Compare source output type versus target input type across data edges including BranchNode paths and cross-phase edges.&#x27;, &#x27;returns&#x27;: &#x27;type_mismatch errors&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-4</code>: Load a YAML fixture, serialize back to YAML, re-load, and verify structural equivalence. Nested children, BranchNode paths, actor_type, context_keys, type definitions, and cost configs survive the round-trip intact.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;test&#x27;, &#x27;to_service&#x27;: &#x27;SVC-3&#x27;, &#x27;action&#x27;: &#x27;Read fixture&#x27;, &#x27;description&#x27;: &#x27;Load raw YAML. BranchNode paths use name-keyed mapping; nested phases use children; actors use actor_type: agent|human; workflow has context_keys.&#x27;, &#x27;returns&#x27;: &#x27;YAML string&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;load_workflow(path)&#x27;, &#x27;description&#x27;: &#x27;Deserialize YAML into typed models. WorkflowCostConfig/PhaseCostConfig/NodeCostConfig are distinct types.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;dump_workflow(config)&#x27;, &#x27;description&#x27;: &#x27;Re-serialize to YAML. context_keys preserved at all levels. BranchNode paths preserved with per-port conditions.&#x27;, &#x27;returns&#x27;: &#x27;YAML string&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;test&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;load_workflow() on dumped string&#x27;, &#x27;description&#x27;: &#x27;Re-deserialize and assert structural equivalence including context_keys values, cost config types, Branch path keys, children nesting depth.&#x27;, &#x27;returns&#x27;: &#x27;Equivalent WorkflowConfig&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-5</code>: Migration tooling reads iriai-build-v2 workflows and emits declarative WorkflowConfig YAML. AskNode.prompt receives iriai-build-v2&#x27;s task/prompt value; context_text does not become an AskNode field; workflow.context_keys is populated from workflow-level context lists.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-10&#x27;, &#x27;to_service&#x27;: &#x27;SVC-6&#x27;, &#x27;action&#x27;: &#x27;Read Python classes&#x27;, &#x27;description&#x27;: &#x27;Analyze Phase, Task, Ask, Interview, Gate patterns. Note interaction actor usage, imperative branching, and context_text usage.&#x27;, &#x27;returns&#x27;: &#x27;Imperative workflow graph&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-10&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;Construct WorkflowConfig&#x27;, &#x27;description&#x27;: &quot;Map Phase nesting to phases[].children; map workflow-level context lists to WorkflowConfig.context_keys; map Ask.prompt to AskNode.prompt (NOT task field); map interaction actors to HumanActorDef actor_type=&#x27;human&#x27;; map gate/if-else to BranchNode per-port conditions per D-GR-35. context_text maps to context_keys entries, not AskNode fields.&quot;, &#x27;returns&#x27;: &#x27;WorkflowConfig instance&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;validate_workflow(config)&#x27;, &#x27;description&#x27;: &quot;Run structural validation. Confirm AskNode.prompt present, WorkflowConfig.context_keys populated, no &#x27;interaction&#x27; actor types.&quot;, &#x27;returns&#x27;: &#x27;list[ValidationError]&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-3&#x27;, &#x27;action&#x27;: &#x27;dump_workflow(config, path)&#x27;, &#x27;description&#x27;: &#x27;Write translated declarative workflow to YAML.&#x27;, &#x27;returns&#x27;: &#x27;.yaml on filesystem&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-6</code>: compose-frontend fetches the live workflow JSON Schema at editor initialization. Blocks rendering until schema is loaded.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-8&#x27;, &#x27;to_service&#x27;: &#x27;SVC-7&#x27;, &#x27;action&#x27;: &#x27;GET /api/schema/workflow&#x27;, &#x27;description&#x27;: &#x27;Editor requests live schema on mount with Bearer JWT. Shows loading state, blocks inspector rendering.&#x27;, &#x27;returns&#x27;: &#x27;HTTP request&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-7&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;WorkflowConfig.model_json_schema()&#x27;, &#x27;description&#x27;: &#x27;Backend calls model_json_schema() to produce live schema. Result reflects WorkflowConfig.context_keys, WorkflowCostConfig, BranchNode per-port conditions, nested PhaseDefinition.children. No phantom types.&#x27;, &#x27;returns&#x27;: &#x27;JSON Schema dict&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-7&#x27;, &#x27;to_service&#x27;: &#x27;SVC-8&#x27;, &#x27;action&#x27;: &#x27;Return JSON Schema&#x27;, &#x27;description&#x27;: &#x27;Backend returns live schema dict. Editor initializes inspectors and validation rules.&#x27;, &#x27;returns&#x27;: &#x27;200 OK with JSON Schema&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-8&#x27;, &#x27;to_service&#x27;: &#x27;SVC-8&#x27;, &#x27;action&#x27;: &#x27;Initialize editor from fetched schema&#x27;, &#x27;description&#x27;: &#x27;Editor renders BranchNode per-port condition editor, context_keys field editors, cost config fields from fetched schema. Does not import static workflow-schema.json at runtime.&#x27;, &#x27;returns&#x27;: &#x27;Editor ready state&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-7</code>: When /api/schema/workflow is unavailable, editor surfaces explicit schema-load error rather than falling back to stale bundled schema.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-8&#x27;, &#x27;to_service&#x27;: &#x27;SVC-7&#x27;, &#x27;action&#x27;: &#x27;GET /api/schema/workflow&#x27;, &#x27;description&#x27;: &#x27;Editor requests schema at startup. Endpoint unavailable or returns unexpected response.&#x27;, &#x27;returns&#x27;: &#x27;HTTP error or timeout&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-8&#x27;, &#x27;to_service&#x27;: &#x27;SVC-8&#x27;, &#x27;action&#x27;: &#x27;Surface schema-load error&#x27;, &#x27;description&#x27;: &#x27;Editor blocks initialization and shows explicit schema-unavailable error. Does not fall back to static workflow-schema.json or continue with stale schema.&#x27;, &#x27;returns&#x27;: &#x27;Error state displayed&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-8&#x27;, &#x27;to_service&#x27;: &#x27;SVC-7&#x27;, &#x27;action&#x27;: &#x27;Retry GET /api/schema/workflow&#x27;, &#x27;description&#x27;: &#x27;After backend restored, editor retries. Receives live schema and resumes normal initialization.&#x27;, &#x27;returns&#x27;: &#x27;Schema loaded, editor ready&#x27;}</li></ol>
        </div>

    <h3>Entities</h3>
    <div class="entity-block">
            <h4><code>ENT-1</code>: PortDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>type_ref</code></td>
                        <td><code>str | None</code></td>
                        <td>Named type reference to workflow.types or a built-in primitive.</td>
                    </tr><tr>
                        <td><code>schema_def</code></td>
                        <td><code>dict | None</code></td>
                        <td>Inline JSON Schema definition.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str | None</code></td>
                        <td>Human-readable port description.</td>
                    </tr><tr>
                        <td><code>required</code></td>
                        <td><code>bool | None</code></td>
                        <td>Whether the port must receive data.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-2</code>: NodeBase</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td>Stable node identifier.</td>
                    </tr><tr>
                        <td><code>type</code></td>
                        <td><code>Literal[&#x27;ask&#x27;,&#x27;branch&#x27;,&#x27;plugin&#x27;,&#x27;error&#x27;]</code></td>
                        <td>Node kind.</td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str] | None</code></td>
                        <td>Node-level runtime context selection keys.</td>
                    </tr><tr>
                        <td><code>cost</code></td>
                        <td><code>NodeCostConfig | None</code></td>
                        <td>Node-level cost budget (distinct type from WorkflowCostConfig/PhaseCostConfig).</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Name-keyed input ports.</td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Name-keyed output ports.</td>
                    </tr><tr>
                        <td><code>hooks</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Lifecycle hook ports. Hook behavior inferred from container; no port_type field.</td>
                    </tr><tr>
                        <td><code>summary</code></td>
                        <td><code>str | None</code></td>
                        <td>Human-readable summary.</td>
                    </tr><tr>
                        <td><code>position</code></td>
                        <td><code>dict[str, float] | None</code></td>
                        <td>Canvas layout metadata.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-3</code>: AskNode</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>type</code></td>
                        <td><code>Literal[&#x27;ask&#x27;]</code></td>
                        <td>Discriminator.</td>
                    </tr><tr>
                        <td><code>actor</code></td>
                        <td><code>str</code></td>
                        <td>Actor that receives the prompt. Must resolve to ActorDefinition with actor_type: agent|human.</td>
                    </tr><tr>
                        <td><code>prompt</code></td>
                        <td><code>str</code></td>
                        <td>Task prompt string. CANONICAL field name — NOT &#x27;task&#x27;, NOT &#x27;context_text&#x27;. iriai-build-v2 Ask.prompt maps directly here on migration. context_text is NOT an AskNode field; use context_keys at node/actor/phase/workflow level for context injection.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Single typed input port.</td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Ask outputs keyed by port name.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-4</code>: BranchNode</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>type</code></td>
                        <td><code>Literal[&#x27;branch&#x27;]</code></td>
                        <td>Discriminator.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>User-defined gather/join input ports.</td>
                    </tr><tr>
                        <td><code>paths</code></td>
                        <td><code>dict[str, BranchPathDefinition]</code></td>
                        <td>Authoritative Branch routing outputs. Each path port carries its own condition expression. Non-exclusive: multiple ports can fire. switch_function is rejected.</td>
                    </tr><tr>
                        <td><code>merge_function</code></td>
                        <td><code>str | None</code></td>
                        <td>Optional gather/merge function for multi-input BranchNodes. Used to combine gathered inputs before routing. Rejected on single-input BranchNodes per D-GR-35.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-5</code>: BranchPathDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>condition</code></td>
                        <td><code>str</code></td>
                        <td>Per-port condition expression per D-GR-35. Port fires when condition evaluates truthy. Non-exclusive: multiple ports can fire if multiple conditions are true.</td>
                    </tr><tr>
                        <td><code>type_ref</code></td>
                        <td><code>str | None</code></td>
                        <td>Named type reference for this path&#x27;s output data.</td>
                    </tr><tr>
                        <td><code>schema_def</code></td>
                        <td><code>dict | None</code></td>
                        <td>Inline JSON Schema for this path&#x27;s output data.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str | None</code></td>
                        <td>Human-readable path description.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-6</code>: PluginNode</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>type</code></td>
                        <td><code>Literal[&#x27;plugin&#x27;]</code></td>
                        <td>Discriminator.</td>
                    </tr><tr>
                        <td><code>plugin_ref</code></td>
                        <td><code>str</code></td>
                        <td>Reference to PluginInterface. No root plugin_instances registry.</td>
                    </tr><tr>
                        <td><code>config</code></td>
                        <td><code>dict | None</code></td>
                        <td>Instance-specific config.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Single typed input port.</td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Plugin output ports.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-6b</code>: ErrorNode</h4>
            <p>4th atomic node type per D-GR-36. Purpose is to RAISE errors (log an error, let it bubble up). Error ports on other nodes are the &quot;catch&quot; side; ErrorNode is the &quot;throw&quot; side.</p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>type</code></td>
                        <td><code>Literal[&#x27;error&#x27;]</code></td>
                        <td>Discriminator.</td>
                    </tr><tr>
                        <td><code>message</code></td>
                        <td><code>str</code></td>
                        <td>Jinja2 template for the error message. Receives node inputs as template variables.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Input ports providing data for the message template.</td>
                    </tr></tbody>
            </table>
            <p><strong>Constraints:</strong> ErrorNode has NO outputs and NO hooks. It is a terminal node in the DAG.</p>
        </div><div class="entity-block">
            <h4><code>ENT-7</code>: EdgeDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>source</code></td>
                        <td><code>str</code></td>
                        <td>Dot-notation source ref. BranchNode source ports resolve against paths keys. Hook-vs-data inferred from source port container; no port_type field.</td>
                    </tr><tr>
                        <td><code>target</code></td>
                        <td><code>str</code></td>
                        <td>Dot-notation target ref.</td>
                    </tr><tr>
                        <td><code>transform_fn</code></td>
                        <td><code>str | None</code></td>
                        <td>Optional data transform.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str | None</code></td>
                        <td>Human-readable edge description.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-8</code>: SequentialModeConfig</h4>
            <p></p>
            
        </div><div class="entity-block">
            <h4><code>ENT-9</code>: MapModeConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>collection</code></td>
                        <td><code>str</code></td>
                        <td>Expression resolving the iterable to fan out over.</td>
                    </tr><tr>
                        <td><code>max_parallelism</code></td>
                        <td><code>int | None</code></td>
                        <td>Optional concurrency limit.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-10</code>: FoldModeConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>collection</code></td>
                        <td><code>str</code></td>
                        <td>Expression resolving the iterable to process.</td>
                    </tr><tr>
                        <td><code>accumulator_init</code></td>
                        <td><code>str</code></td>
                        <td>Expression for the initial accumulator.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-11</code>: LoopModeConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>condition</code></td>
                        <td><code>str</code></td>
                        <td>Expression that determines loop exit (condition_met path).</td>
                    </tr><tr>
                        <td><code>max_iterations</code></td>
                        <td><code>int | None</code></td>
                        <td>Optional safety cap. When hit, max_exceeded exit port fires.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-12</code>: PhaseDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td>Phase identifier. Canonical class name is PhaseDefinition (NOT Phase).</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td>Human-readable phase name.</td>
                    </tr><tr>
                        <td><code>mode</code></td>
                        <td><code>Literal[&#x27;sequential&#x27;,&#x27;map&#x27;,&#x27;fold&#x27;,&#x27;loop&#x27;]</code></td>
                        <td>Execution mode.</td>
                    </tr><tr>
                        <td><code>mode_config</code></td>
                        <td><code>ModeConfig | None</code></td>
                        <td>Mode-specific configuration.</td>
                    </tr><tr>
                        <td><code>nodes</code></td>
                        <td><code>list[NodeDefinition]</code></td>
                        <td>Atomic nodes owned by this phase.</td>
                    </tr><tr>
                        <td><code>children</code></td>
                        <td><code>list[PhaseDefinition]</code></td>
                        <td>Nested child phases. Replaces stale &#x27;phases&#x27; field.</td>
                    </tr><tr>
                        <td><code>edges</code></td>
                        <td><code>list[EdgeDefinition]</code></td>
                        <td>Phase-local edges.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Phase input ports.</td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Phase output ports.</td>
                    </tr><tr>
                        <td><code>hooks</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Phase lifecycle hook ports.</td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str]</code></td>
                        <td>Phase-scoped runtime context selection keys.</td>
                    </tr><tr>
                        <td><code>cost</code></td>
                        <td><code>PhaseCostConfig | None</code></td>
                        <td>Phase-level cost budget (distinct type from WorkflowCostConfig/NodeCostConfig).</td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td>Arbitrary metadata.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-13</code>: RoleDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td>Role name.</td>
                    </tr><tr>
                        <td><code>prompt</code></td>
                        <td><code>str</code></td>
                        <td>Role system prompt.</td>
                    </tr><tr>
                        <td><code>tools</code></td>
                        <td><code>list[str]</code></td>
                        <td>Allowed tools.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-14</code>: ActorDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>actor_type</code></td>
                        <td><code>Literal[&#x27;agent&#x27;,&#x27;human&#x27;]</code></td>
                        <td>Actor kind. agent→AgentActorDef. human→HumanActorDef. Maps to runner&#x27;s InteractionActor boundary for human actors.</td>
                    </tr><tr>
                        <td><code>role</code></td>
                        <td><code>RoleDefinition | None</code></td>
                        <td>Agent role.</td>
                    </tr><tr>
                        <td><code>provider</code></td>
                        <td><code>str | None</code></td>
                        <td>Model provider.</td>
                    </tr><tr>
                        <td><code>model</code></td>
                        <td><code>str | None</code></td>
                        <td>Model override.</td>
                    </tr><tr>
                        <td><code>persistent</code></td>
                        <td><code>bool</code></td>
                        <td>Session continuity across nodes.</td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str]</code></td>
                        <td>Actor baseline runtime context selection keys.</td>
                    </tr><tr>
                        <td><code>identity</code></td>
                        <td><code>str | None</code></td>
                        <td>Human actor identity.</td>
                    </tr><tr>
                        <td><code>channel</code></td>
                        <td><code>str | None</code></td>
                        <td>Human interaction channel.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-15</code>: TypeDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td>Type name.</td>
                    </tr><tr>
                        <td><code>schema_def</code></td>
                        <td><code>dict</code></td>
                        <td>JSON Schema payload.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str | None</code></td>
                        <td>Human-readable type description.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-16</code>: WorkflowCostConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>max_tokens</code></td>
                        <td><code>int | None</code></td>
                        <td>Optional token budget at workflow level.</td>
                    </tr><tr>
                        <td><code>max_usd</code></td>
                        <td><code>float | None</code></td>
                        <td>Optional USD budget at workflow level.</td>
                    </tr><tr>
                        <td><code>track_by</code></td>
                        <td><code>Literal[&#x27;node&#x27;,&#x27;phase&#x27;,&#x27;workflow&#x27;]</code></td>
                        <td>Cost roll-up scope for workflow-level tracking.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-17</code>: PhaseCostConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>max_tokens</code></td>
                        <td><code>int | None</code></td>
                        <td>Optional token budget at phase level.</td>
                    </tr><tr>
                        <td><code>max_usd</code></td>
                        <td><code>float | None</code></td>
                        <td>Optional USD budget at phase level.</td>
                    </tr><tr>
                        <td><code>track_by</code></td>
                        <td><code>Literal[&#x27;node&#x27;,&#x27;phase&#x27;]</code></td>
                        <td>Cost roll-up scope for phase-level tracking.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-18</code>: NodeCostConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>max_tokens</code></td>
                        <td><code>int | None</code></td>
                        <td>Optional token budget at node level.</td>
                    </tr><tr>
                        <td><code>max_usd</code></td>
                        <td><code>float | None</code></td>
                        <td>Optional USD budget at node level.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-19</code>: PluginInterface</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td>Plugin identifier. Referenced by PluginNode.plugin_ref.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td>Plugin display name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str | None</code></td>
                        <td>Plugin description.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Plugin input interface.</td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Plugin output interface.</td>
                    </tr><tr>
                        <td><code>config_schema</code></td>
                        <td><code>dict | None</code></td>
                        <td>JSON Schema for plugin config.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-20</code>: TemplateDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td>Template identifier.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td>Template display name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str | None</code></td>
                        <td>Human-readable description.</td>
                    </tr><tr>
                        <td><code>phase</code></td>
                        <td><code>PhaseDefinition</code></td>
                        <td>Template body. Supports nodes, children, edges, port contracts identical to inline phases.</td>
                    </tr><tr>
                        <td><code>bind</code></td>
                        <td><code>dict | None</code></td>
                        <td>Parameter bindings applied at expansion time.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-21</code>: WorkflowInputDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td>Input identifier.</td>
                    </tr><tr>
                        <td><code>type_ref</code></td>
                        <td><code>str | None</code></td>
                        <td>Named type reference.</td>
                    </tr><tr>
                        <td><code>schema_def</code></td>
                        <td><code>dict | None</code></td>
                        <td>Inline JSON Schema.</td>
                    </tr><tr>
                        <td><code>required</code></td>
                        <td><code>bool</code></td>
                        <td>Whether the workflow input is required.</td>
                    </tr><tr>
                        <td><code>default</code></td>
                        <td><code>Any | None</code></td>
                        <td>Optional default value.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-22</code>: WorkflowOutputDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td>Output identifier. For BranchPathDefinition, the map key (not a field) is the port name.</td>
                    </tr><tr>
                        <td><code>type_ref</code></td>
                        <td><code>str | None</code></td>
                        <td>Named type reference.</td>
                    </tr><tr>
                        <td><code>schema_def</code></td>
                        <td><code>dict | None</code></td>
                        <td>Inline JSON Schema.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str | None</code></td>
                        <td>Human-readable output description.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-23</code>: WorkflowConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>schema_version</code></td>
                        <td><code>str</code></td>
                        <td>Schema format version.</td>
                    </tr><tr>
                        <td><code>workflow_version</code></td>
                        <td><code>int</code></td>
                        <td>Workflow content version.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td>Workflow name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str | None</code></td>
                        <td>Workflow description.</td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td>Arbitrary metadata.</td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str]</code></td>
                        <td>Workflow-level runtime context selection keys. Merged first in the workflow→phase→actor→node hierarchy. Valid root field — removed from D-SF1-23 rejected list by D-GR-41.</td>
                    </tr><tr>
                        <td><code>actors</code></td>
                        <td><code>dict[str, ActorDefinition]</code></td>
                        <td>Actor registry.</td>
                    </tr><tr>
                        <td><code>phases</code></td>
                        <td><code>list[PhaseDefinition]</code></td>
                        <td>Top-level phase list.</td>
                    </tr><tr>
                        <td><code>edges</code></td>
                        <td><code>list[EdgeDefinition]</code></td>
                        <td>Top-level edges.</td>
                    </tr><tr>
                        <td><code>plugins</code></td>
                        <td><code>dict[str, PluginInterface] | None</code></td>
                        <td>Plugin registry.</td>
                    </tr><tr>
                        <td><code>templates</code></td>
                        <td><code>dict[str, TemplateDefinition] | None</code></td>
                        <td>Template registry.</td>
                    </tr><tr>
                        <td><code>types</code></td>
                        <td><code>dict[str, TypeDefinition] | None</code></td>
                        <td>Named type registry.</td>
                    </tr><tr>
                        <td><code>cost_config</code></td>
                        <td><code>WorkflowCostConfig | None</code></td>
                        <td>Workflow-level cost config. Uses WorkflowCostConfig (NOT the removed CostConfig type).</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-24</code>: WorkflowSummary</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>string</code></td>
                        <td>Workflow UUID.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Workflow name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>string | null</code></td>
                        <td>Workflow description.</td>
                    </tr><tr>
                        <td><code>workflow_version</code></td>
                        <td><code>number</code></td>
                        <td>Monotonically incremented version.</td>
                    </tr><tr>
                        <td><code>schema_version</code></td>
                        <td><code>string</code></td>
                        <td>Schema format version string.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>string</code></td>
                        <td>Last update timestamp.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>string</code></td>
                        <td>Creation timestamp.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-25</code>: WorkflowDetail</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>string</code></td>
                        <td>Workflow UUID.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Workflow name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>string | null</code></td>
                        <td>Workflow description.</td>
                    </tr><tr>
                        <td><code>workflow_version</code></td>
                        <td><code>number</code></td>
                        <td>Version.</td>
                    </tr><tr>
                        <td><code>schema_version</code></td>
                        <td><code>string</code></td>
                        <td>Schema format version.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>string</code></td>
                        <td>Last update.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>string</code></td>
                        <td>Creation.</td>
                    </tr><tr>
                        <td><code>config</code></td>
                        <td><code>WorkflowConfigJSON</code></td>
                        <td>Full workflow config JSON blob.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-26</code>: RoleEntry</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>string</code></td>
                        <td>Role UUID.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Role name.</td>
                    </tr><tr>
                        <td><code>prompt</code></td>
                        <td><code>string</code></td>
                        <td>Role system prompt.</td>
                    </tr><tr>
                        <td><code>tools</code></td>
                        <td><code>string[]</code></td>
                        <td>Allowed tool IDs.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>string</code></td>
                        <td>Last update timestamp.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-27</code>: ToolEntry</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>string</code></td>
                        <td>Tool UUID.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Tool name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>string | null</code></td>
                        <td>Tool description.</td>
                    </tr><tr>
                        <td><code>config_schema</code></td>
                        <td><code>Record&lt;string, unknown&gt; | null</code></td>
                        <td>JSON Schema for tool config.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-28</code>: ActorSlot</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>string</code></td>
                        <td>Slot UUID.</td>
                    </tr><tr>
                        <td><code>workflow_id</code></td>
                        <td><code>string</code></td>
                        <td>Parent workflow UUID.</td>
                    </tr><tr>
                        <td><code>actor_key</code></td>
                        <td><code>string</code></td>
                        <td>Key in workflow.actors map.</td>
                    </tr><tr>
                        <td><code>actor_type</code></td>
                        <td><code>&#x27;agent&#x27; | &#x27;human&#x27;</code></td>
                        <td>Actor type discriminator.</td>
                    </tr><tr>
                        <td><code>role_id</code></td>
                        <td><code>string | null</code></td>
                        <td>Linked RoleEntry ID (agent actors only).</td>
                    </tr></tbody>
            </table>
        </div>

    <h3>Entity Relations</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>Relation</th><th>To</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>ER-1</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>actor_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-2</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>type_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-3</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>phase_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-4</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>edge</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-5</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>plugin_interface</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-6</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>workflow_cost_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-7</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>template_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-8</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>workflow_input_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-9</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>workflow_output_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-10</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>node_base</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-11</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>edge</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-12</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>phase_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-13</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>port_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-14</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>phase_cost_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-15</code></td>
            <td><code>node_base</code></td>
            <td></td>
            <td><code>port_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-16</code></td>
            <td><code>node_base</code></td>
            <td></td>
            <td><code>node_cost_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-17</code></td>
            <td><code>ask_node</code></td>
            <td></td>
            <td><code>node_base</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-18</code></td>
            <td><code>branch_node</code></td>
            <td></td>
            <td><code>node_base</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-19</code></td>
            <td><code>plugin_node</code></td>
            <td></td>
            <td><code>node_base</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-19b</code></td>
            <td><code>error_node</code></td>
            <td></td>
            <td><code>node_base</code></td>
            <td>ErrorNode extends NodeBase (D-GR-36). Has inputs but NO outputs, NO hooks.</td>
        </tr><tr>
            <td><code>ER-20</code></td>
            <td><code>branch_node</code></td>
            <td></td>
            <td><code>branch_path_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-21</code></td>
            <td><code>ask_node</code></td>
            <td></td>
            <td><code>actor_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-22</code></td>
            <td><code>plugin_node</code></td>
            <td></td>
            <td><code>plugin_interface</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-23</code></td>
            <td><code>plugin_interface</code></td>
            <td></td>
            <td><code>port_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-24</code></td>
            <td><code>actor_definition</code></td>
            <td></td>
            <td><code>role_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-25</code></td>
            <td><code>edge</code></td>
            <td></td>
            <td><code>port_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-26</code></td>
            <td><code>edge</code></td>
            <td></td>
            <td><code>branch_path_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-27</code></td>
            <td><code>port_definition</code></td>
            <td></td>
            <td><code>type_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-28</code></td>
            <td><code>workflow_input_definition</code></td>
            <td></td>
            <td><code>type_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-29</code></td>
            <td><code>workflow_output_definition</code></td>
            <td></td>
            <td><code>type_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-30</code></td>
            <td><code>ts_workflow_detail</code></td>
            <td></td>
            <td><code>ts_workflow_summary</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-31</code></td>
            <td><code>ts_actor_slot</code></td>
            <td></td>
            <td><code>ts_role_entry</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>Decisions</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Decision</th></tr></thead>
        <tbody><tr>
            <td><code>D-1</code></td>
            <td>D-SF1-1: Module lives at iriai_compose/schema/ with no intermediate declarative namespace.</td>
        </tr><tr>
            <td><code>D-2</code></td>
            <td>D-SF1-2: Routing model is split by node type. AskNode outputs are typed ports with no per-port conditions; conditional routing requires a downstream BranchNode. BranchNode uses per-port conditions on paths per D-GR-35.</td>
        </tr><tr>
            <td><code>D-3</code></td>
            <td>D-SF1-3: Interview is represented as a Loop phase plus Ask and Branch primitives.</td>
        </tr><tr>
            <td><code>D-4</code></td>
            <td>D-SF1-4: Phase I/O boundaries remain strict: external edges touch only phase ports and internal wiring uses $input/$output pseudo-ports.</td>
        </tr><tr>
            <td><code>D-5</code></td>
            <td>D-SF1-5: Loop exit logic stays on LoopModeConfig.condition, not on BranchNode.</td>
        </tr><tr>
            <td><code>D-6</code></td>
            <td>D-SF1-6: schema_version remains the string &#x27;1.0&#x27;.</td>
        </tr><tr>
            <td><code>D-7</code></td>
            <td>D-SF1-7: Pydantic v2 models drive validation and JSON Schema generation.</td>
        </tr><tr>
            <td><code>D-8</code></td>
            <td>D-SF1-8: YAML serialization stays on pyyaml.</td>
        </tr><tr>
            <td><code>D-9</code></td>
            <td>D-SF1-9: Node unions remain discriminated on the type field.</td>
        </tr><tr>
            <td><code>D-10</code></td>
            <td>D-SF1-10: One PortDefinition type serves inputs, outputs, and hooks. Container location determines semantics (hook vs data).</td>
        </tr><tr>
            <td><code>D-11</code></td>
            <td>D-SF1-11: NodeBase and PhaseDefinition keep identical default input, output, and hook signatures for non-Branch data ports.</td>
        </tr><tr>
            <td><code>D-12</code></td>
            <td>D-SF1-12: AskNode keeps one fixed input and 1+ typed outputs. Multi-output routing is expressed by a downstream BranchNode, not per-port conditions on AskNode outputs.</td>
        </tr><tr>
            <td><code>D-13</code></td>
            <td>D-SF1-13: BranchNode is the exclusive routing primitive per D-GR-35 per-port model: 1+ inputs, 2+ BranchPathDefinition entries in paths dict, each with its own condition (non-exclusive fan-out). Optional merge_function valid on gather (multi-input) BranchNodes only. switch_function is rejected everywhere.</td>
        </tr><tr>
            <td><code>D-14</code></td>
            <td>D-SF1-14: PluginNode keeps one fixed input and 0+ outputs; fire-and-forget plugins are represented by an empty outputs dict.</td>
        </tr><tr>
            <td><code>D-15</code></td>
            <td>D-SF1-15: Expression-bearing fields remain strings evaluated by the downstream runner. Per-port BranchNode.paths conditions are expressions subject to REQ-21 sandbox in SF-2.</td>
        </tr><tr>
            <td><code>D-16</code></td>
            <td>D-SF1-16: Phase modes (map, fold, loop) do not carry fresh_sessions semantics in the schema. Session continuity is a runner-level concern.</td>
        </tr><tr>
            <td><code>D-17</code></td>
            <td>D-SF1-17: PluginNode references plugins only via plugin_ref (references workflow.plugins). There is no root plugin_instances registry.</td>
        </tr><tr>
            <td><code>D-18</code></td>
            <td>D-SF1-18: type_ref/schema_def mutual exclusion applies on all port maps: node inputs/outputs/hooks, phase inputs/outputs/hooks, BranchPathDefinition, PluginInterface ports, and workflow-level I/O.</td>
        </tr><tr>
            <td><code>D-19</code></td>
            <td>D-SF1-19: PluginNode supports 0+ outputs.</td>
        </tr><tr>
            <td><code>D-20</code></td>
            <td>D-SF1-20: Async gather/barrier semantics for multi-input BranchNodes stay in the runner, not the schema.</td>
        </tr><tr>
            <td><code>D-21</code></td>
            <td>D-SF1-21: EdgeDefinition is the single connection model for data and hook edges; hook behavior is inferred from the source port container. No port_type field.</td>
        </tr><tr>
            <td><code>D-22</code></td>
            <td>D-SF1-22: Nested phases serialize under phases[].children. The stale &#x27;phases&#x27; field inside PhaseDefinition is replaced by &#x27;children&#x27;.</td>
        </tr><tr>
            <td><code>D-23</code></td>
            <td>D-SF1-23 (revised by D-GR-41): WorkflowConfig root is closed. Valid root fields are: schema_version, workflow_version, name, description, metadata, context_keys, actors, phases, edges, templates, plugins, types, cost_config only. Stores, plugin_instances, context_text, and any other unapproved addition must be rejected. NOTE: context_keys IS valid (removed from rejected list per D-GR-41).</td>
        </tr><tr>
            <td><code>D-24</code></td>
            <td>D-SF1-24: Context hierarchy remains workflow, phase, actor, and node, expressed through context_keys fields at each level. WorkflowConfig.context_keys is the workflow-level entry. No root stores registry.</td>
        </tr><tr>
            <td><code>D-25</code></td>
            <td>D-SF1-25: ActorDefinition uses actor_type as its discriminator with only &#x27;agent&#x27; and &#x27;human&#x27; as valid values. AgentActorDef carries provider/model/role/persistent/context_keys. HumanActorDef carries identity/channel. &#x27;interaction&#x27; is not a valid actor_type and must be rejected.</td>
        </tr><tr>
            <td><code>D-26</code></td>
            <td>D-SF1-26: context_keys fields on nodes, phases, actors, and workflows are runtime context selection keys. They do not reference a root stores registry.</td>
        </tr><tr>
            <td><code>D-27</code></td>
            <td>D-SF1-27: Artifact hosting is represented in DAG topology, not a stores configuration.</td>
        </tr><tr>
            <td><code>D-28</code></td>
            <td>D-SF1-28: switch_function is removed from the Branch contract everywhere. merge_function is valid only on gather (multi-input) BranchNodes per D-GR-35. Legacy configs with switch_function must migrate to per-port conditions in paths.</td>
        </tr><tr>
            <td><code>D-29</code></td>
            <td>D-SF1-29: artifact_key is a string storage-key identifier on NodeBase. It does not use dot-notation store-ref syntax.</td>
        </tr><tr>
            <td><code>D-30</code></td>
            <td>D-SF1-30: WorkflowConfig carries typed workflow-level inputs and outputs declarations.</td>
        </tr><tr>
            <td><code>D-31</code></td>
            <td>D-SF1-31: Every typed port (PortDefinition, WorkflowInputDefinition, WorkflowOutputDefinition, BranchPathDefinition) requires exactly one of type_ref or schema_def.</td>
        </tr><tr>
            <td><code>D-32</code></td>
            <td>D-SF1-32: The default effective port type is &#x27;any&#x27; when bare-string shorthand is used.</td>
        </tr><tr>
            <td><code>D-33</code></td>
            <td>D-SF1-33: YAML bare-string shorthand remains supported for typed port maps.</td>
        </tr><tr>
            <td><code>D-34</code></td>
            <td>D-SF1-34: Port maps stay dict[str, PortDefinition] for NodeBase, PhaseDefinition, and PluginInterface. BranchNode routeable outputs are dict[str, BranchPathDefinition] under paths.</td>
        </tr><tr>
            <td><code>D-35</code></td>
            <td>D-SF1-35: BranchNode route selection is non-exclusive per D-GR-35. Multiple path ports can fire if their per-port conditions are met. Fan-out from a fired path uses multiple edges from that path port.</td>
        </tr><tr>
            <td><code>D-36</code></td>
            <td>D-SF1-36: Editor, runner, and migration tooling must materialize Branch output handles from BranchNode.paths keys and must not expose or persist switch_function or any alternate routing surface.</td>
        </tr><tr>
            <td><code>D-37</code></td>
            <td>D-SF1-37: The composer editor&#x27;s runtime schema source is GET /api/schema/workflow. Static workflow-schema.json is a build/test-only artifact. The editor must not import or fall back to static workflow-schema.json at runtime.</td>
        </tr><tr>
            <td><code>D-38</code></td>
            <td>D-SF1-38: PhaseDefinition uses a single mode_config field (discriminated union) replacing previous separate config fields.</td>
        </tr><tr>
            <td><code>D-39</code></td>
            <td>D-SF1-39 (D-GR-41): AskNode.prompt is the sole canonical field for the task prompt string. &#x27;task&#x27; is NOT a valid AskNode field. &#x27;context_text&#x27; is NOT an AskNode field. iriai-build-v2 Ask.prompt maps directly to AskNode.prompt on migration. context_text usage in iriai-build-v2 maps to context_keys entries at the appropriate hierarchy level.</td>
        </tr><tr>
            <td><code>D-40</code></td>
            <td>D-SF1-40 (D-GR-41): CostConfig is split into three distinct scoped types: WorkflowCostConfig (used in WorkflowConfig.cost_config), PhaseCostConfig (used in PhaseDefinition.cost), NodeCostConfig (used in NodeBase.cost). The unified CostConfig type is removed. Migration tooling must use the correct scoped type at each level.</td>
        </tr><tr>
            <td><code>D-41</code></td>
            <td>D-SF1-41 (D-GR-35): BranchNode routing model is the D-GR-12/D-GR-35 per-port model. Each entry in paths carries its own condition expression. Routing is non-exclusive. merge_function is valid only on gather (2+ inputs) BranchNodes for combining collected inputs. output_field per-port mode is removed.</td>
        </tr><tr>
            <td><code>D-42</code></td>
            <td>Pure data layer: iriai_compose/schema/ imports nothing from iriai_compose.actors, .tasks, .runner, or .workflow.</td>
        </tr></tbody>
    </table>

    <h3>Risks</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Risk</th></tr></thead>
        <tbody><tr>
            <td><code>RISK-1</code></td>
            <td>RISK-1 (medium): Strict phase boundary rules may still be too rigid for some iriai-build-v2 patterns. Mitigation: keep migration fixtures comprehensive.</td>
        </tr><tr>
            <td><code>RISK-2</code></td>
            <td>RISK-2 (medium): Removing per-port PortDefinition.condition from AskNode outputs means multi-branch routing always requires an explicit BranchNode. Mitigation: SF-6 should provide quick-insert BranchNode affordance.</td>
        </tr><tr>
            <td><code>RISK-3</code></td>
            <td>RISK-3 (medium): Inline Python expression strings remain a security concern. Mitigation: SF-2 enforces the REQ-21 sandbox contract for all BranchNode per-port conditions.</td>
        </tr><tr>
            <td><code>RISK-4</code></td>
            <td>RISK-4 (low): JSON Schema output for BranchPathDefinition may need a thin editor adapter or custom form widget.</td>
        </tr><tr>
            <td><code>RISK-5</code></td>
            <td>RISK-5 (low): YAML key ordering may drift across round-trips. Mitigation: rely on Python insertion order and snapshot tests.</td>
        </tr><tr>
            <td><code>RISK-6</code></td>
            <td>RISK-6 (low): Requiring type_ref or schema_def on every BranchPathDefinition port can make YAML verbose. Mitigation: bare-string shorthand.</td>
        </tr><tr>
            <td><code>RISK-7</code></td>
            <td>RISK-7 (medium): Non-exclusive BranchNode routing (D-GR-35) may surprise users expecting exactly one path to fire. Mitigation: surface clear UI hint that multiple conditions can be true simultaneously.</td>
        </tr><tr>
            <td><code>RISK-8</code></td>
            <td>RISK-8 (low): Removing fresh_sessions from LoopModeConfig means session continuity can only be configured at runner level. Mitigation: document as runner concern in SF-2.</td>
        </tr><tr>
            <td><code>RISK-9</code></td>
            <td>RISK-9 (medium): Collection expression context may be insufficient for complex migration patterns. Mitigation: route preprocessing through plugins.</td>
        </tr><tr>
            <td><code>RISK-10</code></td>
            <td>RISK-10 (low): Path-aware port resolution slightly increases validation complexity because Branch paths use BranchPathDefinition not PortDefinition.</td>
        </tr><tr>
            <td><code>RISK-11</code></td>
            <td>RISK-11 (medium): Three distinct CostConfig types (WorkflowCostConfig/PhaseCostConfig/NodeCostConfig) require migration tooling to assign the correct type at each hierarchy level. Mitigation: D-SF1-40 is explicit; migration tooling tests must cover all three levels.</td>
        </tr><tr>
            <td><code>RISK-12</code></td>
            <td>RISK-12 (medium): Four-level context merge (workflow/phase/actor/node) via context_keys can produce unexpectedly large prompts. Mitigation: runner should track context size and surface it in the editor.</td>
        </tr><tr>
            <td><code>RISK-13</code></td>
            <td>RISK-13 (medium): Strict rejection of stores, plugin_instances, merge_function-on-single-input, switch_function, interaction actor type, and context_text may break older draft YAML. Mitigation: provide targeted migration errors in SF-3 and SF-4.</td>
        </tr><tr>
            <td><code>RISK-14</code></td>
            <td>RISK-14 (medium): /api/schema/workflow is a hard dependency for editor initialization. Mitigation: J-6 mandates explicit failure surfacing; implement retry with backoff in SF-6.</td>
        </tr><tr>
            <td><code>RISK-15</code></td>
            <td>RISK-15 (low): Renaming phases[].phases to phases[].children breaks existing YAML fixtures. Mitigation: validation rejects stale field with migration hint.</td>
        </tr><tr>
            <td><code>RISK-16</code></td>
            <td>RISK-16 (low): AskNode.prompt rename from iriai-build-v2 &#x27;task&#x27; field must be handled in all migration fixtures and documented prominently in D-SF1-39.</td>
        </tr><tr>
            <td><code>RISK-17</code></td>
            <td>RISK-17 (medium): merge_function valid-only-on-gather rule requires validation to count BranchNode inputs. Mitigation: _check_rejected_branch_fields() enforces this with a clear error distinguishing gather vs single-input context.</td>
        </tr></tbody>
    </table>
</section>
<hr/>


---

## Subfeature: DAG Loader & Runner (dag-loader-runner)

<!-- SF: dag-loader-runner -->
<section id="sf-dag-loader-runner" class="subfeature-section">
    <h2>SF-2: DAG Loader &amp; Runner — D-GR-41 Canonical Cross-SF Edge Contracts</h2>
    <div class="provenance">Subfeature: <code>dag-loader-runner</code></div>

    <h3>Overview</h3>
    <div class="overview-text">SF-2 treats the current SF-1 PRD as the only authoritative declarative wire contract. WorkflowConfig root fields are limited to schema_version, workflow_version, name, description, metadata, context_keys, actors, phases, edges, templates, plugins, types, and cost_config. The workflow-level context_keys field (added per D-GR-41) supplies the top layer of the hierarchical merge order workflow→phase→actor→node. All 10 cross-subfeature edge data contracts are defined per D-GR-41: (1) SF-1→SF-2: imports from iriai_compose.schema; 23 valid exports; phantom types MapNode/FoldNode/LoopNode/TransformRef/HookRef do not exist. (2) SF-2→SF-3: run(workflow, config: RuntimeConfig, *, inputs=None) — not the stale (yaml_path, runtime, workspace, transform_registry, hook_registry). (3) SF-1→SF-4: WorkflowConfig carries context_keys at workflow level; AskNode uses task: str (NOT prompt); context_text: str | None is supplementary inline context. (4) SF-5→SF-6: full Foundation REST and schema/validation endpoints with TypeScript response shapes. (5) SF-5→SF-7: in-process mutation hook interface (MutationEvent). (6) SF-7→SF-6: reference-check and tool-list APIs with TypeScript contracts. (7) SF-6→SF-7: indirect — editor saves through SF-5; SF-5 fires MutationEvent; SF-7 refreshes workflow_entity_refs; editor has no direct SF-7 write dependency.</div>

    <h3>Services</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Name</th><th>Kind</th><th>Description</th><th>Technology</th><th>Port</th><th>Journeys</th>
        </tr></thead>
        <tbody><tr>
            <td><code>SVC-11</code></td>
            <td><strong>Declarative Runtime API</strong></td>
            <td><code>service</code></td>
            <td>Additive iriai_compose.declarative entrypoints: load_workflow(), validate(), run(workflow, config: RuntimeConfig, *, inputs=None).</td>
            <td><code>Python 3.11+</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3</td>
        </tr><tr>
            <td><code>SVC-12</code></td>
            <td><strong>Workflow Loader</strong></td>
            <td><code>service</code></td>
            <td>Parses YAML against iriai_compose.schema models. Rejects stale fields.</td>
            <td><code>Python, PyYAML, Pydantic</code></td>
            <td>—</td>
            <td>J-1, J-3</td>
        </tr><tr>
            <td><code>SVC-13</code></td>
            <td><strong>Structural Validator</strong></td>
            <td><code>service</code></td>
            <td>Shared validation used by validate() and run(). Enforces nested containment, typed ports, stale-field rejection.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3</td>
        </tr><tr>
            <td><code>SVC-14</code></td>
            <td><strong>Recursive Graph Builder</strong></td>
            <td><code>service</code></td>
            <td>Builds container-local DAGs from phases[].nodes and phases[].children.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td>J-1, J-3</td>
        </tr><tr>
            <td><code>SVC-15</code></td>
            <td><strong>Phase Mode Runner</strong></td>
            <td><code>service</code></td>
            <td>Executes sequential/map/fold/loop phases recursively. Assembles hierarchical context via ContextVar: workflow→phase→actor→node.</td>
            <td><code>Python, asyncio</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-16</code></td>
            <td><strong>Node Executor</strong></td>
            <td><code>service</code></td>
            <td>Dispatches four atomic node types: Ask (uses task field), Branch (per-port BranchOutputPort.condition, non-exclusive fan-out — condition_type/condition/paths/switch_function REJECTED per D-GR-35), Plugin, and Error (Jinja2 message, terminal, no outputs — D-GR-36).</td>
            <td><code>Python, asyncio</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-17</code></td>
            <td><strong>Expression Sandbox</strong></td>
            <td><code>service</code></td>
            <td>AST allowlist evaluator for transform_fn, branch expressions, loop/map/fold mode configs.</td>
            <td><code>Python AST sandbox</code></td>
            <td>—</td>
            <td>J-1, J-3</td>
        </tr><tr>
            <td><code>SVC-18</code></td>
            <td><strong>Actor Hydrator</strong></td>
            <td><code>service</code></td>
            <td>Bridges agent/human actors to existing runtime ABCs. AgentRuntime.invoke() unchanged.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-19</code></td>
            <td><strong>Plugin Runtime</strong></td>
            <td><code>service</code></td>
            <td>Resolves WorkflowConfig.plugins to executable implementations via RuntimeConfig.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-20</code></td>
            <td><strong>SF-1 Declarative Schema</strong></td>
            <td><code>external</code></td>
            <td>Module iriai_compose.schema. 23 valid exports. Phantom types that do NOT exist: MapNode, FoldNode, LoopNode, TransformRef, HookRef. Correct names: PhaseDefinition, WorkflowCostConfig/PhaseCostConfig/NodeCostConfig.</td>
            <td><code>Python 3.11+, Pydantic v2</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3</td>
        </tr><tr>
            <td><code>SVC-21</code></td>
            <td><strong>SF-3 Testing Framework</strong></td>
            <td><code>service</code></td>
            <td>Fluent mock runtimes. Consumes run(workflow, config: RuntimeConfig, *, inputs=None) — NOT stale signature. Node matching via ContextVar.</td>
            <td><code>Python 3.11+, pytest</code></td>
            <td>—</td>
            <td>J-1, J-3</td>
        </tr><tr>
            <td><code>SVC-22</code></td>
            <td><strong>SF-4 Workflow Migration</strong></td>
            <td><code>service</code></td>
            <td>Translates iriai-build-v2 → declarative YAML. Ask.prompt → AskNode.task. WorkflowConfig carries context_keys at workflow level.</td>
            <td><code>Python 3.11+</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-23</code></td>
            <td><strong>Existing iriai-compose Runtime Interfaces</strong></td>
            <td><code>external</code></td>
            <td>AgentRuntime, InteractionRuntime, ArtifactStore, SessionStore, ContextProvider, WorkflowRunner.parallel(), DefaultWorkflowRunner. No breaking ABI changes.</td>
            <td><code>Python ABCs</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-24</code></td>
            <td><strong>Compose Backend (SF-5)</strong></td>
            <td><code>service</code></td>
            <td>FastAPI at tools/compose/backend. Five foundation tables. GET /api/schema/workflow, workflow CRUD, validation. Fires synchronous in-process MutationEvent after commits. Never creates workflow_entity_refs rows.</td>
            <td><code>FastAPI, PostgreSQL, Alembic</code></td>
            <td>443</td>
            <td>J-2, J-5</td>
        </tr><tr>
            <td><code>SVC-25</code></td>
            <td><strong>Compose Frontend / Workflow Editor (SF-6)</strong></td>
            <td><code>frontend</code></td>
            <td>React 18 + TypeScript at tools/compose/frontend. React Flow canvas. Flat internal store; normalizes to canonical nested YAML. Blocks on GET /api/schema/workflow failure. No direct SF-7 write dependency.</td>
            <td><code>React 18, TypeScript, React Flow, Zustand, Vite</code></td>
            <td>443</td>
            <td>J-2, J-5</td>
        </tr><tr>
            <td><code>SVC-26</code></td>
            <td><strong>Libraries &amp; Registries (SF-7)</strong></td>
            <td><code>service</code></td>
            <td>Owns workflow_entity_refs via own Alembic migration. Subscribes to SF-5 mutation hooks. Exposes GET /api/{entity}/references/{id}, tool CRUD, PATCH actor-slots.</td>
            <td><code>FastAPI, PostgreSQL, Alembic, React 18, TypeScript</code></td>
            <td>443</td>
            <td>J-5</td>
        </tr><tr>
            <td><code>SVC-27</code></td>
            <td><strong>Workflow YAML Files</strong></td>
            <td><code>database</code></td>
            <td>Canonical SF-1 YAML with phases[].nodes, phases[].children, cross-phase edges at root.</td>
            <td><code>YAML</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3</td>
        </tr><tr>
            <td><code>SVC-28</code></td>
            <td><strong>iriai-build-v2 Reference Workflows</strong></td>
            <td><code>external</code></td>
            <td>Existing planning/develop/bugfix workflows as the representability litmus test.</td>
            <td><code>Python workflows</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr></tbody>
    </table>

    <h3>Connections</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>To</th><th>Protocol</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>CONN-16</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-17</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-18</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-19</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-20</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-21</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-22</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>in-process callback</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-23</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-24</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>event-driven via SF-5 hooks</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-25</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>file I/O</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-26</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-27</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-28</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-29</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-30</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-31</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>in-memory data</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-32</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-33</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-34</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>runtime integration</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-35</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-36</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-37</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-38</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>async function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-39</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>async function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-40</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTP + Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-41</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-42</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>file generation</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>API Endpoints</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Method</th><th>Path</th><th>Service</th><th>Description</th><th>Auth</th>
        </tr></thead>
        <tbody><tr>
            <td><code>API-33</code></td>
            <td><code>GET</code></td>
            <td><code>iriai_compose.declarative.load_workflow</code></td>
            <td><code></code></td>
            <td>Parse YAML into WorkflowConfig from iriai_compose.schema. Rejects phantom types and stale fields.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-34</code></td>
            <td><code>POST</code></td>
            <td><code>iriai_compose.declarative.validate</code></td>
            <td><code></code></td>
            <td>Structural validation without live runtimes. Used by SF-3 and SF-4.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-35</code></td>
            <td><code>POST</code></td>
            <td><code>iriai_compose.declarative.run</code></td>
            <td><code></code></td>
            <td>Execute workflow. Canonical signature: run(workflow, config: RuntimeConfig, *, inputs=None). NOT run(yaml_path, runtime, workspace, transform_registry, hook_registry).</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-36</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schema/workflow</code></td>
            <td><code></code></td>
            <td>Live JSON Schema from WorkflowConfig.model_json_schema(). Only runtime schema source. Editor blocks on failure.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-37</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows</code></td>
            <td><code></code></td>
            <td>Cursor-paginated workflow list.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-38</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows</code></td>
            <td><code></code></td>
            <td>Create workflow + v1. Fires MutationEvent created.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-39</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Get workflow. Returns 404 for cross-user.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-40</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Update workflow. Fires MutationEvent updated.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-41</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete. Fires MutationEvent soft_deleted → SF-7 removes workflow_entity_refs.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-42</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/{id}/versions</code></td>
            <td><code></code></td>
            <td>Append immutable version snapshot.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-43</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/{id}/validate</code></td>
            <td><code></code></td>
            <td>Server-side deep validation via iriai_compose.declarative.validate(). Two-tier validation with SF-6 client checks.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-44</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/import</code></td>
            <td><code></code></td>
            <td>Import YAML. Rejects malformed (no rows created). Schema-invalid creates with is_valid=false.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-45</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/{id}/export</code></td>
            <td><code></code></td>
            <td>Export as canonical nested YAML. Content-Type: text/yaml.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-46</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/starter-templates</code></td>
            <td><code></code></td>
            <td>Built-in templates from iriai-build-v2 reference workflows.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-47</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles</code></td>
            <td><code></code></td>
            <td>List roles. Used by SF-6 actor picker.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-48</code></td>
            <td><code>POST</code></td>
            <td><code>/api/roles</code></td>
            <td><code></code></td>
            <td>Create role. Fires MutationEvent.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-49</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Update role. Fires MutationEvent updated.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-50</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete role. Caller must pre-check GET /api/roles/references/{id}. Fires MutationEvent soft_deleted.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-51</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas</code></td>
            <td><code></code></td>
            <td>List output schemas.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-52</code></td>
            <td><code>POST</code></td>
            <td><code>/api/schemas</code></td>
            <td><code></code></td>
            <td>Create output schema.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-53</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/schemas/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete schema. Pre-check GET /api/schemas/references/{id} first.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-54</code></td>
            <td><code>GET</code></td>
            <td><code>/api/task-templates</code></td>
            <td><code></code></td>
            <td>List task templates. actor_slots present only after SF-7 migration.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-55</code></td>
            <td><code>POST</code></td>
            <td><code>/api/task-templates</code></td>
            <td><code></code></td>
            <td>Create task template.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-56</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/task-templates/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete template. Pre-check references first.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-57</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles/references/{id}</code></td>
            <td><code></code></td>
            <td>Pre-delete reference check for role. Queries workflow_entity_refs.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-58</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas/references/{id}</code></td>
            <td><code></code></td>
            <td>Pre-delete reference check for output schema.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-59</code></td>
            <td><code>GET</code></td>
            <td><code>/api/task-templates/references/{id}</code></td>
            <td><code></code></td>
            <td>Pre-delete reference check for custom task template.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-60</code></td>
            <td><code>GET</code></td>
            <td><code>/api/tools</code></td>
            <td><code></code></td>
            <td>List tools. Used by SF-6 ToolChecklistProps.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-61</code></td>
            <td><code>POST</code></td>
            <td><code>/api/tools</code></td>
            <td><code></code></td>
            <td>Create tool.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-62</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/tools/{id}</code></td>
            <td><code></code></td>
            <td>Update tool.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-63</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/tools/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete tool. Role-backed protection: 409 { blocking_roles } if any Role.tools[] references this tool name.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-64</code></td>
            <td><code>PATCH</code></td>
            <td><code>/api/task-templates/{id}/actor-slots</code></td>
            <td><code></code></td>
            <td>Persist actor slots. SF-7 adds actor_slots JSONB column via its own Alembic migration.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-65</code></td>
            <td><code>HOOK</code></td>
            <td><code>sf5.services.register_mutation_hook(entity_type, callback)</code></td>
            <td><code></code></td>
            <td>In-process hook registration. Fires after successful commit. Failures logged but don&#x27;t rollback.</td>
            <td><code>N/A (in-process)</code></td>
        </tr></tbody>
    </table>

    <h3>Call Paths</h3>
    <div class="call-path-block">
            <h4><code>CP-8</code>: Platform engineer runs canonical nested YAML through the declarative runtime.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;workflow_yaml&#x27;, &#x27;to_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;action&#x27;: &#x27;run(workflow_path, config, inputs=None)&#x27;, &#x27;description&#x27;: &#x27;Start from YAML file.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionResult&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;to_service&#x27;: &#x27;sf2_loader&#x27;, &#x27;action&#x27;: &#x27;load_workflow — imports from iriai_compose.schema&#x27;, &#x27;description&#x27;: &#x27;Parse WorkflowConfig.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;sf2_loader&#x27;, &#x27;to_service&#x27;: &#x27;sf2_validator&#x27;, &#x27;action&#x27;: &#x27;validate nested structure and stale-field rejection&#x27;, &#x27;description&#x27;: &#x27;Reject port_type, switch_function, stores, plugin_instances.&#x27;, &#x27;returns&#x27;: &#x27;[] or field-path errors&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;to_service&#x27;: &#x27;sf2_graph_builder&#x27;, &#x27;action&#x27;: &#x27;build workflow graph&#x27;, &#x27;description&#x27;: &#x27;Build DAGs from nested phases.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionGraph&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;sf2_graph_builder&#x27;, &#x27;to_service&#x27;: &#x27;sf2_phase_runner&#x27;, &#x27;action&#x27;: &#x27;execute top-level phases recursively&#x27;, &#x27;description&#x27;: &#x27;Context assembled: WorkflowConfig.context_keys → phase → actor → node.&#x27;, &#x27;returns&#x27;: &#x27;phase outputs&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;sf2_phase_runner&#x27;, &#x27;to_service&#x27;: &#x27;sf2_node_executor&#x27;, &#x27;action&#x27;: &#x27;dispatch Ask/Branch/Plugin/Error — AskNode.task not prompt&#x27;, &#x27;description&#x27;: &#x27;Four atomic node types (D-GR-36 adds ErrorNode).&#x27;, &#x27;returns&#x27;: &#x27;node outputs&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;sf2_node_executor&#x27;, &#x27;to_service&#x27;: &#x27;sf2_actor_adapter&#x27;, &#x27;action&#x27;: &#x27;hydrate actor_type: agent|human&#x27;, &#x27;description&#x27;: &#x27;AgentRuntime.invoke() unchanged.&#x27;, &#x27;returns&#x27;: &#x27;runtime output&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;sf2_node_executor&#x27;, &#x27;to_service&#x27;: &#x27;sf2_expression_runtime&#x27;, &#x27;action&#x27;: &#x27;evaluate transforms and branch conditions&#x27;, &#x27;description&#x27;: &#x27;AST sandbox.&#x27;, &#x27;returns&#x27;: &#x27;transformed payloads&#x27;}</li><li>{&#x27;sequence&#x27;: 9, &#x27;from_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;to_service&#x27;: &#x27;imperative_runtime&#x27;, &#x27;action&#x27;: &#x27;assemble hierarchical context, collect observability&#x27;, &#x27;description&#x27;: &#x27;ExecutionResult with history and phase_metrics.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionResult&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-9</code>: Backend and editor share live schema from iriai_compose.schema.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/schema/workflow&#x27;, &#x27;description&#x27;: &#x27;Blocks on failure — no static fallback.&#x27;, &#x27;returns&#x27;: &#x27;JSON Schema&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;composer_backend&#x27;, &#x27;to_service&#x27;: &#x27;sf1_schema&#x27;, &#x27;action&#x27;: &#x27;WorkflowConfig.model_json_schema()&#x27;, &#x27;description&#x27;: &#x27;Same models SF-2 uses at runtime.&#x27;, &#x27;returns&#x27;: &#x27;JSON Schema&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_backend&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows/{id}/validate with canonical nested YAML&#x27;, &#x27;description&#x27;: &#x27;Normalized from flat canvas before submission.&#x27;, &#x27;returns&#x27;: &#x27;ValidationResponse&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;composer_backend&#x27;, &#x27;to_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;action&#x27;: &#x27;iriai_compose.declarative.validate(workflow)&#x27;, &#x27;description&#x27;: &#x27;Same nested contract.&#x27;, &#x27;returns&#x27;: &#x27;list[ValidationError]&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-10</code>: Stale YAML fails fast before execution.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;workflow_yaml&#x27;, &#x27;to_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;action&#x27;: &#x27;validate(workflow_path)&#x27;, &#x27;description&#x27;: &#x27;Stale YAML submitted.&#x27;, &#x27;returns&#x27;: &#x27;list[ValidationError]&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;to_service&#x27;: &#x27;sf2_loader&#x27;, &#x27;action&#x27;: &#x27;parse raw YAML fields&#x27;, &#x27;description&#x27;: &#x27;Expose raw field names for precise errors.&#x27;, &#x27;returns&#x27;: &#x27;parsed document&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;sf2_loader&#x27;, &#x27;to_service&#x27;: &#x27;sf2_validator&#x27;, &#x27;action&#x27;: &#x27;reject stale fields&#x27;, &#x27;description&#x27;: &#x27;port_type, switch_function, stores, plugin_instances, interaction alias blocked.&#x27;, &#x27;returns&#x27;: &#x27;field-path errors&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-11</code>: Editor blocks rather than falling back to stale schema.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/schema/workflow&#x27;, &#x27;description&#x27;: &#x27;Boot schema request.&#x27;, &#x27;returns&#x27;: &#x27;HTTP success or failure&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;composer_backend&#x27;, &#x27;to_service&#x27;: &#x27;composer_editor&#x27;, &#x27;action&#x27;: &#x27;return failure status&#x27;, &#x27;description&#x27;: &#x27;Surfaces unavailability.&#x27;, &#x27;returns&#x27;: &#x27;error response&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_editor&#x27;, &#x27;action&#x27;: &#x27;render schema-unavailable blocking state&#x27;, &#x27;description&#x27;: &#x27;No fallback to workflow-schema.json.&#x27;, &#x27;returns&#x27;: &#x27;blocked editor state&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-12</code>: SF-7 reference check prevents deletion of referenced library entities.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;sf7_libraries&#x27;, &#x27;action&#x27;: &#x27;GET /api/{entity}/references/{id}&#x27;, &#x27;description&#x27;: &#x27;Pre-fetch ReferenceCheckResult before rendering dialog.&#x27;, &#x27;returns&#x27;: &#x27;ReferenceCheckResult { can_delete, references, reference_count }&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_editor&#x27;, &#x27;action&#x27;: &#x27;render EntityDeleteDialog&#x27;, &#x27;description&#x27;: &#x27;Blocking message if can_delete: false.&#x27;, &#x27;returns&#x27;: &#x27;user decision&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_backend&#x27;, &#x27;action&#x27;: &#x27;DELETE /api/{entity}/{id}&#x27;, &#x27;description&#x27;: &#x27;Only if can_delete: true.&#x27;, &#x27;returns&#x27;: &#x27;204 No Content&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;composer_backend&#x27;, &#x27;to_service&#x27;: &#x27;sf7_libraries&#x27;, &#x27;action&#x27;: &#x27;MutationEvent { event: soft_deleted } via hook callback&#x27;, &#x27;description&#x27;: &#x27;SF-7 removes workflow_entity_refs rows.&#x27;, &#x27;returns&#x27;: &#x27;void&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-13</code>: Editor has no direct SF-7 write dependency — refresh flows through SF-5 mutation hooks.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_backend&#x27;, &#x27;action&#x27;: &#x27;PUT /api/workflows/{id} with canonical nested YAML&#x27;, &#x27;description&#x27;: &#x27;Flat canvas state serialized to nested YAML.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowRecord&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;composer_backend&#x27;, &#x27;to_service&#x27;: &#x27;composer_backend&#x27;, &#x27;action&#x27;: &#x27;persist to PostgreSQL&#x27;, &#x27;description&#x27;: &#x27;Update committed.&#x27;, &#x27;returns&#x27;: &#x27;committed row&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;composer_backend&#x27;, &#x27;to_service&#x27;: &#x27;sf7_libraries&#x27;, &#x27;action&#x27;: &#x27;MutationEvent { entity_type: workflow, event: updated } via hook callback&#x27;, &#x27;description&#x27;: &#x27;SF-7 diffs entity_refs, upserts/deletes workflow_entity_refs. Hook failure logged but does not rollback.&#x27;, &#x27;returns&#x27;: &#x27;void&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;sf7_libraries&#x27;, &#x27;to_service&#x27;: &#x27;composer_editor&#x27;, &#x27;action&#x27;: &#x27;next GET /api/{entity}/references/{id} returns fresh data&#x27;, &#x27;description&#x27;: &#x27;Reference index current for subsequent delete preflights.&#x27;, &#x27;returns&#x27;: &#x27;ReferenceCheckResult (current)&#x27;}</li></ol>
        </div>

    <h3>Entities</h3>
    <div class="entity-block">
            <h4><code>ENT-29</code>: WorkflowConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>schema_version</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>workflow_version</code></td>
                        <td><code>int</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str] | None</code></td>
                        <td>Top of workflow→phase→actor→node hierarchy. SF-4 migration must preserve this.</td>
                    </tr><tr>
                        <td><code>actors</code></td>
                        <td><code>dict[str, ActorDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>phases</code></td>
                        <td><code>list[PhaseDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>edges</code></td>
                        <td><code>list[EdgeDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>templates</code></td>
                        <td><code>dict[str, TemplateDefinition] | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>plugins</code></td>
                        <td><code>dict[str, PluginInterface] | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>types</code></td>
                        <td><code>dict[str, dict] | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>cost_config</code></td>
                        <td><code>WorkflowCostConfig | None</code></td>
                        <td>NOT dict. NOT CostConfig. Type is WorkflowCostConfig.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-30</code>: AskNode</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>type</code></td>
                        <td><code>Literal[&#x27;ask&#x27;]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>actor_ref</code></td>
                        <td><code>str</code></td>
                        <td>Key into WorkflowConfig.actors.</td>
                    </tr><tr>
                        <td><code>task</code></td>
                        <td><code>str</code></td>
                        <td>Primary prompt text. NOT named prompt. Migration: imperative Ask.prompt → declarative AskNode.task.</td>
                    </tr><tr>
                        <td><code>context_text</code></td>
                        <td><code>str | None</code></td>
                        <td>Supplementary inline context. Separate from task.</td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str] | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, WorkflowInputDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>dict[str, WorkflowOutputDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>hooks</code></td>
                        <td><code>dict[str, WorkflowOutputDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>artifact_key</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>cost</code></td>
                        <td><code>NodeCostConfig | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-40</code>: ErrorNode (D-GR-36)</h4>
            <p>4th atomic node type. Raises structured errors — terminal, no outputs, no hooks. When the runner encounters an ErrorNode, it renders the Jinja2 message template against inputs, creates an ExecutionError, and routes it via the error-port mechanism on the parent/caller per D-GR-4.</p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td>Unique node identifier.</td>
                    </tr><tr>
                        <td><code>type</code></td>
                        <td><code>Literal[&#x27;error&#x27;]</code></td>
                        <td>Discriminator. NodeDefinition = Annotated[AskNode | BranchNode | PluginNode | ErrorNode, Field(discriminator=&quot;type&quot;)].</td>
                    </tr><tr>
                        <td><code>message</code></td>
                        <td><code>str</code></td>
                        <td>Jinja2 template rendered with input data to produce the error message.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, WorkflowInputDefinition]</code></td>
                        <td>1+ input ports (inherits NodeBase default).</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-31</code>: WorkflowCostConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>budget_usd</code></td>
                        <td><code>float | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>alert_threshold_usd</code></td>
                        <td><code>float | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>track_tokens</code></td>
                        <td><code>bool</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-32</code>: PhaseCostConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>budget_usd</code></td>
                        <td><code>float | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>alert_threshold_usd</code></td>
                        <td><code>float | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-33</code>: NodeCostConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>budget_usd</code></td>
                        <td><code>float | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>max_tokens</code></td>
                        <td><code>int | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-34</code>: MutationEvent</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>entity_type</code></td>
                        <td><code>Literal[&#x27;workflow&#x27;,&#x27;role&#x27;,&#x27;output_schema&#x27;,&#x27;custom_task_template&#x27;]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>entity_id</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>event</code></td>
                        <td><code>Literal[&#x27;created&#x27;,&#x27;updated&#x27;,&#x27;soft_deleted&#x27;,&#x27;restored&#x27;]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>timestamp</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-35</code>: PhaseDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>mode</code></td>
                        <td><code>&#x27;sequential&#x27;|&#x27;map&#x27;|&#x27;fold&#x27;|&#x27;loop&#x27;</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>mode_config</code></td>
                        <td><code>ModeConfig | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, WorkflowInputDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>dict[str, WorkflowOutputDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>hooks</code></td>
                        <td><code>dict[str, WorkflowOutputDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>nodes</code></td>
                        <td><code>list[NodeDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>children</code></td>
                        <td><code>list[PhaseDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>edges</code></td>
                        <td><code>list[EdgeDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>cost</code></td>
                        <td><code>PhaseCostConfig | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-36</code>: EdgeDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>source</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>target</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>transform_fn</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-37</code>: RuntimeConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>agent_runtime</code></td>
                        <td><code>AgentRuntime</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>interaction_runtimes</code></td>
                        <td><code>dict[str, InteractionRuntime]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>artifacts</code></td>
                        <td><code>ArtifactStore</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>sessions</code></td>
                        <td><code>SessionStore | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>context_provider</code></td>
                        <td><code>ContextProvider</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>plugin_registry</code></td>
                        <td><code>dict[str, Any] | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>workspace</code></td>
                        <td><code>Workspace | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>feature</code></td>
                        <td><code>Feature | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>services</code></td>
                        <td><code>dict[str, Any] | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-38</code>: ExecutionResult</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>success</code></td>
                        <td><code>bool</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>output</code></td>
                        <td><code>Any</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>error</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>trace</code></td>
                        <td><code>dict[str, Any]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>history</code></td>
                        <td><code>list[ExecutionHistory]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>phase_metrics</code></td>
                        <td><code>dict[str, Any]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>cost_summary</code></td>
                        <td><code>dict[str, Any]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>hook_warnings</code></td>
                        <td><code>list[str]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>duration_ms</code></td>
                        <td><code>float</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-39</code>: ValidationError</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>field_path</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>message</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>severity</code></td>
                        <td><code>&#x27;error&#x27;|&#x27;warning&#x27;</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div>

    <h3>Entity Relations</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>Relation</th><th>To</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>ER-32</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>workflow_cost_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-33</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>phase_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-34</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>edge_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-35</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>phase_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-36</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>phase_cost_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-37</code></td>
            <td><code>ask_node</code></td>
            <td></td>
            <td><code>actor_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-38</code></td>
            <td><code>mutation_event</code></td>
            <td></td>
            <td><code>validation_error</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-39</code></td>
            <td><code>execution_result</code></td>
            <td></td>
            <td><code>validation_error</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>Decisions</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Decision</th></tr></thead>
        <tbody><tr>
            <td><code>D-43</code></td>
            <td>D-SF2-11: iriai_compose.schema is the canonical module path. Phantom types that do NOT exist: MapNode, FoldNode, LoopNode, TransformRef, HookRef. Correct names: PhaseDefinition, WorkflowCostConfig/PhaseCostConfig/NodeCostConfig. [decision: D-GR-41]</td>
        </tr><tr>
            <td><code>D-44</code></td>
            <td>D-SF2-12: Canonical run() signature is run(workflow: WorkflowConfig|Path|str, config: RuntimeConfig, *, inputs: dict|None = None). Stale signature run(yaml_path, runtime, workspace, transform_registry, hook_registry) is invalid. [decision: D-GR-41]</td>
        </tr><tr>
            <td><code>D-45</code></td>
            <td>D-SF2-13: WorkflowConfig carries context_keys: list[str]|None at workflow level completing the top layer of the workflow→phase→actor→node hierarchy. [decision: D-GR-41]</td>
        </tr><tr>
            <td><code>D-46</code></td>
            <td>D-SF2-14: AskNode uses task: str as the primary prompt field NOT prompt. context_text: str|None is supplementary. Migration: Ask(actor=x, prompt=&#x27;...&#x27;) → AskNode(type: ask, actor_ref: &#x27;x&#x27;, task: &#x27;...&#x27;). [decision: D-GR-41] [code: iriai-compose/iriai_compose/tasks.py:49-54]</td>
        </tr><tr>
            <td><code>D-47</code></td>
            <td>D-SF2-15: SF-5 mutation hook interface is the only write path from editor to reference index. Editor has no direct SF-7 write dependency. [decision: D-GR-41]</td>
        </tr><tr>
            <td><code>D-48</code></td>
            <td>D-SF2-16: SF-5 mutation hooks fire synchronously in-process after successful DB commit. Callback failures logged but do not rollback. [decision: D-GR-37]</td>
        </tr></tbody>
    </table>

    <h3>Risks</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Risk</th></tr></thead>
        <tbody><tr>
            <td><code>RISK-18</code></td>
            <td>RISK-7 (medium): SF-7 mutation hook subscriber fails — reference index becomes stale without transaction rollback. Mitigation: log all hook failures; provide re-index endpoint. [decision: D-GR-37]</td>
        </tr><tr>
            <td><code>RISK-19</code></td>
            <td>RISK-8 (low): Library entity deleted between editor page load and save causes stale YAML reference. Mitigation: POST /api/workflows/{id}/validate runs SF-2 validate() which catches stale refs before persistence.</td>
        </tr></tbody>
    </table>
</section>
<hr/>


---

## Subfeature: Testing Framework (testing-framework)

<!-- SF: testing-framework -->
<section id="sf-testing-framework" class="subfeature-section">
    <h2>SF-3 Testing Framework — System Design</h2>
    <div class="provenance">Subfeature: <code>testing-framework</code></div>

    <h3>Overview</h3>
    <div class="overview-text">`iriai_compose.testing` is a purpose-built Python testing subpackage within `iriai-compose` for validating declarative workflow definitions. It provides fluent mock runtimes, fixture builders, execution assertions, validation re-exports, and YAML snapshot helpers.

**Edge contracts established per D-GR-41:**

**SF-1→SF-3:** SF-3 imports from `iriai_compose.schema`: `WorkflowConfig`, `PhaseDefinition`, `AskNode`, `BranchNode`, `PluginNode`, `ErrorNode`, `ActorDefinition`, `AgentActorDef`, `HumanActorDef`, `Edge`, `PortDefinition`, `ValidationError`, `load_workflow()`, `dump_workflow()`, `validate_workflow()`, `validate_type_flow()`, `detect_cycles()`. Four atomic node types: Ask, Branch, Plugin, Error (per D-GR-36). Entity names are `PhaseDefinition` (not `Phase`) and `Edge` (not `EdgeDefinition`). Phantom exports `MapNode`, `FoldNode`, `LoopNode`, `TransformRef`, `HookRef` do not exist.

**SF-2→SF-3:** SF-3 imports from `iriai_compose.declarative`: `run()` with canonical signature `async def run(workflow: WorkflowConfig | str | Path, config: RuntimeConfig, *, inputs: dict[str, Any] | None = None) -&gt; ExecutionResult` — the stale `(yaml_path, runtime, workspace, transform_registry, hook_registry)` is permanently rejected. `ExecutionResult.nodes_executed` ordering is `(node_id, phase_id)`. `ExecutionResult.hook_warnings: list[str]` is a confirmed SF-2 field. `ExecutionHistory` is a confirmed SF-2 export (per D-GR-34). Additional imports: `RuntimeConfig`, `ExecutionHistory`, `ExecutionError`, `PluginRegistry`, `required_plugins()`, `load_runtime_config()`, `_current_node` ContextVar.

**ABI invariants:** `AgentRuntime.invoke(role, prompt, *, output_type, workspace, session_key)` is permanently frozen — no `node_id` parameter. Node identity propagates via `_current_node: ContextVar[str | None]` managed by the SF-2 runner. Hierarchical context merge order is `workflow → phase → actor → node`. All port containers are `dict[str, PortDefinition]` keyed by port name. Checkpoint/resume is excluded from SF-2&#x27;s published ABI.</div>

    <h3>Services</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Name</th><th>Kind</th><th>Description</th><th>Technology</th><th>Port</th><th>Journeys</th>
        </tr></thead>
        <tbody><tr>
            <td><code>SVC-29</code></td>
            <td><strong>iriai_compose.testing</strong></td>
            <td><code>service</code></td>
            <td>Testing subpackage installed via `pip install iriai-compose[testing]`. Provides `MockAgentRuntime`, `MockInteractionRuntime`, `MockPluginRuntime`, fixture builders, standalone assertions, YAML snapshot helpers, validation re-exports, and a thin `run_test()` wrapper. `MockAgentRuntime.when_node()` resolves against the SF-2 current-node `ContextVar` instead of requiring an `invoke(..., node_id=...)` signature change, and `run_test()` mirrors the SF-2-published ABI without inventing any checkpoint/resume surface.</td>
            <td><code>Python 3.11+, pytest</code></td>
            <td>—</td>
            <td>J-7, J-8, J-9, J-10</td>
        </tr><tr>
            <td><code>SVC-30</code></td>
            <td><strong>iriai_compose.schema</strong></td>
            <td><code>service</code></td>
            <td>SF-1 schema models and validation logic consumed by SF-3 builders, snapshot helpers, and validation re-exports. Canonical public exports from `iriai_compose.schema` (per D-GR-34/D-GR-41): `WorkflowConfig`, `PhaseDefinition`, `AskNode`, `BranchNode`, `PluginNode`, `ErrorNode`, `ActorDefinition`, `AgentActorDef`, `HumanActorDef`, `Edge`, `PortDefinition`, `ValidationError`, `load_workflow()`, `dump_workflow()`, `validate_workflow()`, `validate_type_flow()`, `detect_cycles()`. Four atomic node types: Ask, Branch, Plugin, Error (per D-GR-36). Phase execution modes (`MapConfig`, `FoldConfig`, `LoopConfig`, `SequentialConfig`) are separate config types attached to `PhaseDefinition.mode` — they are not node types. Phantom exports `MapNode`, `FoldNode`, `LoopNode`, `TransformRef`, and `HookRef` do not exist in `iriai_compose.schema` and must never be imported. Entity name is `PhaseDefinition` (not `Phase`), `Edge` (not `EdgeDefinition`). All port containers are `dict[str, PortDefinition]` keyed by port name.</td>
            <td><code>Python 3.11+, Pydantic v2</code></td>
            <td>—</td>
            <td>J-7, J-9</td>
        </tr><tr>
            <td><code>SVC-31</code></td>
            <td><strong>iriai_compose.declarative</strong></td>
            <td><code>service</code></td>
            <td>ABI Owner. SF-2 DAG loader and runner consumed by SF-3 as a downstream consumer. Canonical public exports from `iriai_compose.declarative` (per D-GR-34/D-GR-41): `run()`, `load_workflow()`, `load_runtime_config()`, `RuntimeConfig`, `ExecutionResult`, `ExecutionHistory`, `ExecutionError`, `PluginRegistry`, `required_plugins()`, plus error types `DeclarativeExecutionError`, `WorkflowLoadError`, `WorkflowInputError`, `ExpressionEvalError`, `PluginNotFoundError`. Runner-managed `_current_node: ContextVar[str | None]` is part of the published ABI. Published non-breaking contract: (1) `AgentRuntime.invoke(role, prompt, *, output_type, workspace, session_key)` — unchanged ABC, no `node_id` parameter ever; (2) `_current_node: ContextVar[str | None]` — set immediately before each Ask-node dispatch, reset to `None` after; (3) invocation context assembled in `workflow → phase → actor → node` merge order, deduplicated; (4) canonical run() signature: `async def run(workflow: WorkflowConfig | str | Path, config: RuntimeConfig, *, inputs: dict[str, Any] | None = None) -&gt; ExecutionResult` — the stale `(yaml_path, runtime, workspace, transform_registry, hook_registry)` signature is rejected; (5) `ExecutionResult.nodes_executed` is `list[tuple[str, str]]` ordered as `(node_id, phase_id)` (node first, containing phase second); `ExecutionResult.hook_warnings: list[str]` collects non-fatal hook warnings. Checkpoint/resume is excluded from the published ABI.</td>
            <td><code>Python 3.11+, pyyaml</code></td>
            <td>—</td>
            <td>J-8, J-10</td>
        </tr><tr>
            <td><code>SVC-32</code></td>
            <td><strong>Test Fixtures (filesystem)</strong></td>
            <td><code>external</code></td>
            <td>`tests/fixtures/workflows/` directory containing valid and invalid YAML workflow files. All fixture files serialize port containers as YAML maps (`inputs: {default: {type_ref: any}}`) to match the dict-based schema model.</td>
            <td><code>YAML files</code></td>
            <td>—</td>
            <td>J-7, J-9</td>
        </tr><tr>
            <td><code>SVC-33</code></td>
            <td><strong>SF-4 Workflow Migration Consumers</strong></td>
            <td><code>external</code></td>
            <td>SF-4 migration parity suites and litmus workflows consume `iriai_compose.testing` and `iriai_compose.declarative` against the SF-2-published runtime ABI. They must use the unchanged `AgentRuntime.invoke()`, current-node `ContextVar`, canonical `workflow -&gt; phase -&gt; actor -&gt; node` merge order, and must not assume any core checkpoint/resume API in SF-2.</td>
            <td><code>Python 3.11+, pytest</code></td>
            <td>—</td>
            <td>J-8, J-10</td>
        </tr><tr>
            <td><code>SVC-34</code></td>
            <td><strong>iriai_compose (core)</strong></td>
            <td><code>service</code></td>
            <td>Core iriai-compose library providing the unchanged `AgentRuntime` ABC (`invoke(role, prompt, *, output_type, workspace, session_key)`), `InteractionRuntime`, `PluginRuntime`, `InMemoryArtifactStore`, `InMemorySessionStore`, `DefaultContextProvider`, and the existing `ContextVar`-based runner pattern used today for phase state. SF-2 extends that pattern with a sibling current-node `ContextVar` without breaking the ABC surface.</td>
            <td><code>Python 3.11+</code></td>
            <td>—</td>
            <td>J-8, J-10</td>
        </tr></tbody>
    </table>

    <h3>Connections</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>To</th><th>Protocol</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>CONN-43</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-44</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-45</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>filesystem read</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-46</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-47</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-48</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>API Endpoints</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Method</th><th>Path</th><th>Service</th><th>Description</th><th>Auth</th>
        </tr></thead>
        <tbody><tr>
            <td><code>API-66</code></td>
            <td><code>CALL</code></td>
            <td><code>iriai_compose.runner.AgentRuntime.invoke(role, prompt, *, output_type=None, workspace=None, session_key=None)</code></td>
            <td><code></code></td>
            <td>Unchanged abstract runtime contract. Declarative execution must not add `node_id` here; it propagates current node identity through a `ContextVar` instead.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-67</code></td>
            <td><code>CALL</code></td>
            <td><code>iriai_compose.declarative.run(workflow, config, *, inputs=None)</code></td>
            <td><code></code></td>
            <td>Canonical SF-2 runtime ABI for SF-3 and SF-4 consumers. Canonical signature: `async def run(workflow: WorkflowConfig | str | Path, config: RuntimeConfig, *, inputs: dict[str, Any] | None = None) -&gt; ExecutionResult`. The stale `(yaml_path, runtime, workspace, transform_registry, hook_registry)` form is permanently rejected. `RuntimeConfig` carries runtime dependencies; node identity is conveyed through the runner-managed `_current_node` ContextVar; hierarchical context merge order is `workflow -&gt; phase -&gt; actor -&gt; node`; `ExecutionResult.nodes_executed` ordering is `(node_id, phase_id)` (node first).</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-68</code></td>
            <td><code>CALL</code></td>
            <td><code>iriai_compose.testing.runner.run_test(workflow, *, runtime=None, interaction=None, plugin_registry=None, artifacts=None, inputs=None, feature_id=&#x27;test&#x27;)</code></td>
            <td><code></code></td>
            <td>Thin async wrapper around the canonical SF-2 `run()` ABI. Assembles a `RuntimeConfig` from provided mocks: `runtime` → `config.agent_runtime`, `interaction` → `config.interaction_runtimes={&#x27;human&#x27;: interaction}` per D-GR-37, `plugin_registry` → `config.plugin_registry`, `artifacts` → `config.artifacts`. Delegates directly through `run(workflow, config, inputs=inputs)` and returns `ExecutionResult` unchanged. Must not inject a `node_id` kwarg into `AgentRuntime.invoke()`, synthesize checkpoint/resume behavior, swallow exceptions, or rewrite `ExecutionResult` fields. The interaction runtime key &#x27;default&#x27; is rejected per D-GR-37.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-69</code></td>
            <td><code>CALL</code></td>
            <td><code>iriai_compose.testing.validation.validate_workflow(config)</code></td>
            <td><code></code></td>
            <td>Re-export of SF-1 `validate_workflow()`. Validates full workflow config for structural errors.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-70</code></td>
            <td><code>CALL</code></td>
            <td><code>iriai_compose.testing.validation.validate_type_flow(config)</code></td>
            <td><code></code></td>
            <td>Re-export of SF-1 `validate_type_flow()`. Checks output type compatibility across edges.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-71</code></td>
            <td><code>CALL</code></td>
            <td><code>iriai_compose.testing.validation.detect_cycles(config)</code></td>
            <td><code></code></td>
            <td>Re-export of SF-1 `detect_cycles()`. Detects cyclic dependencies in the workflow DAG.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-72</code></td>
            <td><code>CALL</code></td>
            <td><code>iriai_compose.testing.assertions.assert_node_reached(result, node_id, *, before=None, after=None)</code></td>
            <td><code></code></td>
            <td>Assert that a node was executed, with optional ordering constraints. Reads node IDs from `result.nodes_executed` tuples in canonical `(node_id, phase_id)` order — node first, containing phase second, per SF-2 published ABI (D-GR-41). Unpack as `nid, pid = entry` and check `nid == node_id`.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-73</code></td>
            <td><code>CALL</code></td>
            <td><code>iriai_compose.testing.assertions.assert_branch_taken(result, branch_node_id, expected_port)</code></td>
            <td><code></code></td>
            <td>Assert that a specific Branch node recorded the expected output path in `result.branch_paths`.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-74</code></td>
            <td><code>CALL</code></td>
            <td><code>iriai_compose.testing.assertions.assert_loop_iterations(result, phase_id, expected_count)</code></td>
            <td><code></code></td>
            <td>Assert loop iterations through `result.history.loop_progress[phase_id].completed_iterations`; phase-mode metrics live on `ExecutionHistory` (added to `ExecutionResult.history` per D-GR-34), not as top-level `ExecutionResult` fields.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-75</code></td>
            <td><code>CALL</code></td>
            <td><code>iriai_compose.testing.snapshot.assert_yaml_round_trip(path)</code></td>
            <td><code></code></td>
            <td>Load a YAML workflow file, serialize back to YAML, reload, and assert structural equality. Port dicts must round-trip as dicts, not lists.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-76</code></td>
            <td><code>CALL</code></td>
            <td><code>iriai_compose.testing.fixtures.minimal_ask_workflow(*, actor, prompt, **kwargs)</code></td>
            <td><code></code></td>
            <td>Factory function returning a minimal single-Ask-node `WorkflowConfig`. Ports are dict-based: `inputs`, `outputs`, and `hooks` are all `dict[str, PortDefinition]` keyed by port name.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-77</code></td>
            <td><code>CALL</code></td>
            <td><code>MockAgentRuntime().when_node(node_id)</code></td>
            <td><code></code></td>
            <td>Fluent entry point for Strategy 1 matcher creation. The configured `node_id` is compared against the current-node `ContextVar` read inside `MockAgentRuntime.invoke()`, not against an `invoke(..., node_id=...)` parameter.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-78</code></td>
            <td><code>CALL</code></td>
            <td><code>MockAgentRuntime().when_role(name, *, prompt=None)</code></td>
            <td><code></code></td>
            <td>Fluent entry point for role-based matching. With `prompt` set, creates Strategy 2 (`role+prompt`); otherwise Strategy 3 (`role-only`).</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-79</code></td>
            <td><code>CALL</code></td>
            <td><code>NodeMatcher.respond_with(handler)</code></td>
            <td><code></code></td>
            <td>Terminal configuration for dynamic responses. The handler is called with `(prompt, context)` where `context` reflects the canonical hierarchical merge order `workflow -&gt; phase -&gt; actor -&gt; node`.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-80</code></td>
            <td><code>CALL</code></td>
            <td><code>MockInteractionRuntime().when_node(node_id)</code></td>
            <td><code></code></td>
            <td>Fluent entry point for interaction mock configuration keyed by the `Pending.node_id` value.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-81</code></td>
            <td><code>CALL</code></td>
            <td><code>MockPluginRuntime().when_ref(plugin_ref)</code></td>
            <td><code></code></td>
            <td>Fluent entry point for plugin mock configuration keyed by `plugin_ref`.</td>
            <td><code>—</code></td>
        </tr></tbody>
    </table>

    <h3>Call Paths</h3>
    <div class="call-path-block">
            <h4><code>CP-14</code>: Developer writes a test that intentionally constructs an invalid workflow and asserts the correct `ValidationError` is returned through SF-3 re-exports.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;WorkflowBuilder().add_ask_node(...).add_edge(&#x27;n1&#x27;, &#x27;missing&#x27;).build()&quot;, &#x27;description&#x27;: &#x27;Build a workflow with a dangling edge. Builder stores node/phase ports as dicts keyed by port name.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &#x27;validate_workflow(config)&#x27;, &#x27;description&#x27;: &#x27;Call the SF-1 validation function via the SF-3 re-export.&#x27;, &#x27;returns&#x27;: &#x27;list[ValidationError]&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;assert_validation_error(errors, code=&#x27;dangling_edge&#x27;)&quot;, &#x27;description&#x27;: &#x27;Assert the expected validation error is present.&#x27;, &#x27;returns&#x27;: &#x27;None&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-15</code>: Developer writes an end-to-end execution test using a fixture workflow, a fluent `MockAgentRuntime` configured via `when_node()`, and `run_test()`; SF-2 sets current node state through `_current_node` ContextVar before `invoke()`.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;minimal_ask_workflow(actor=&#x27;pm&#x27;)&quot;, &#x27;description&#x27;: &#x27;Create a minimal single-Ask-node `WorkflowConfig` with dict-based ports.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &#x27;mock = MockAgentRuntime()&#x27;, &#x27;description&#x27;: &#x27;Instantiate with no arguments.&#x27;, &#x27;returns&#x27;: &#x27;MockAgentRuntime&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;mock.when_node(&#x27;ask_1&#x27;).respond(&#x27;done&#x27;)&quot;, &#x27;description&#x27;: &#x27;Configure Strategy 1 matcher keyed by node ID.&#x27;, &#x27;returns&#x27;: &#x27;MockAgentRuntime&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &#x27;await run_test(wf, runtime=mock)&#x27;, &#x27;description&#x27;: &#x27;Invoke `run_test()` which builds `RuntimeConfig` and delegates directly to SF-2 `run()`.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionResult&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-3&#x27;, &#x27;action&#x27;: &#x27;run(workflow, config)&#x27;, &#x27;description&#x27;: &#x27;SF-2 executes the DAG, sets `_current_node` ContextVar to `ask_1` before Ask-node dispatch, assembles invocation context in `workflow -&gt; phase -&gt; actor -&gt; node` order.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionResult&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-SF3-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;MockAgentRuntime.invoke(role=Role(&#x27;pm&#x27;, ...), prompt=&#x27;...&#x27;)&quot;, &#x27;description&#x27;: &quot;Unchanged `invoke()` signature per SF-2 published ABI. `MockAgentRuntime` reads `_current_node` ContextVar (value: `ask_1`), resolves Strategy 1, records call, returns `&#x27;done&#x27;`.&quot;, &#x27;returns&#x27;: &quot;&#x27;done&#x27;&quot;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;assert_node_reached(result, &#x27;ask_1&#x27;)&quot;, &#x27;description&#x27;: &#x27;Assert that `ask_1` appears in `result.nodes_executed` (tuple order: `(node_id, phase_id)`).&#x27;, &#x27;returns&#x27;: &#x27;None&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-16</code>: Developer tests a loop-mode phase using `respond_sequence()` on `MockAgentRuntime` and `approve_sequence()` on `MockInteractionRuntime` to simulate draft -&gt; reject -&gt; revise -&gt; approve behavior.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;mock.when_node(&#x27;pm-draft&#x27;).respond_sequence([draft_v1, draft_v2])&quot;, &#x27;description&#x27;: &#x27;Configure sequential responses. Exhaustion raises `MockExhaustedError`.&#x27;, &#x27;returns&#x27;: &#x27;MockAgentRuntime&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;interaction.when_node(&#x27;user-gate&#x27;).approve_sequence([False, True])&quot;, &#x27;description&#x27;: &#x27;First iteration rejects, second approves.&#x27;, &#x27;returns&#x27;: &#x27;MockInteractionRuntime&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &#x27;await run_test(wf, runtime=mock, interaction=interaction)&#x27;, &#x27;description&#x27;: &#x27;Execute loop-mode workflow. SF-2 assembles context in `workflow -&gt; phase -&gt; actor -&gt; node` order and sets `_current_node` ContextVar on each draft-node invocation.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionResult&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF3-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &#x27;MockAgentRuntime.invoke(...) x 2&#x27;, &#x27;description&#x27;: &#x27;Invocation 1 reads `_current_node` = `pm-draft`, returns `draft_v1`. Invocation 2 reads same node ID on second iteration, returns `draft_v2`. No `node_id` parameter passed.&#x27;, &#x27;returns&#x27;: &#x27;draft_v1, then draft_v2&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;assert_loop_iterations(result, &#x27;review-phase&#x27;, 2)&quot;, &#x27;description&#x27;: &#x27;Verify loop ran exactly two iterations through `result.history.loop_progress`.&#x27;, &#x27;returns&#x27;: &#x27;None&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-17</code>: Developer asserts loading a YAML workflow fixture and re-serializing it produces an identical structure.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;assert_yaml_round_trip(&#x27;tests/fixtures/workflows/minimal_ask.yaml&#x27;)&quot;, &#x27;description&#x27;: &#x27;Entry point for round-trip testing.&#x27;, &#x27;returns&#x27;: &#x27;None&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-2&#x27;, &#x27;action&#x27;: &#x27;load_workflow(path)&#x27;, &#x27;description&#x27;: &#x27;SF-1 schema I/O parses YAML into a validated `WorkflowConfig` with dict-keyed port containers.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-2&#x27;, &#x27;action&#x27;: &#x27;dump_workflow(config)&#x27;, &#x27;description&#x27;: &#x27;Serialize back to YAML using map format for ports.&#x27;, &#x27;returns&#x27;: &#x27;str&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &#x27;yaml.safe_load(original) == yaml.safe_load(serialized)&#x27;, &#x27;description&#x27;: &#x27;Compare plain YAML structures for equality, show unified diff on failure.&#x27;, &#x27;returns&#x27;: &#x27;None&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-18</code>: Developer configures `MockAgentRuntime` with node, role+prompt, role-only, and default matchers, then verifies the fixed resolution priority `node_id &gt; role+prompt &gt; role-only &gt; default` under the SF-2 ContextVar contract.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;mock.default_response(&#x27;fallback&#x27;).when_role(&#x27;pm&#x27;).respond(&#x27;role-match&#x27;).when_role(&#x27;pm&#x27;, prompt=r&#x27;review.*&#x27;).respond(&#x27;role-prompt-match&#x27;).when_node(&#x27;ask_1&#x27;).respond(&#x27;node-match&#x27;)&quot;, &#x27;description&#x27;: &#x27;Register all four strategies in one fluent chain.&#x27;, &#x27;returns&#x27;: &#x27;MockAgentRuntime&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;test_file&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &#x27;await run_test(wf, runtime=mock)&#x27;, &#x27;description&#x27;: &#x27;Execute a workflow that exercises all four strategy levels.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionResult&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF3-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;invoke(role=Role(&#x27;pm&#x27;,...), prompt=&#x27;review the PRD&#x27;) with _current_node=&#x27;ask_1&#x27;&quot;, &#x27;description&#x27;: &#x27;Strategy 1 (`node_id`) wins — `MockAgentRuntime` reads `ask_1` from the SF-2-managed ContextVar before checking role strategies.&#x27;, &#x27;returns&#x27;: &quot;&#x27;node-match&#x27;&quot;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF3-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;invoke(role=Role(&#x27;pm&#x27;,...), prompt=&#x27;review the code&#x27;) with _current_node=&#x27;ask_2&#x27;&quot;, &#x27;description&#x27;: &#x27;No node matcher for `ask_2`; Strategy 2 (`role+prompt`) wins.&#x27;, &#x27;returns&#x27;: &quot;&#x27;role-prompt-match&#x27;&quot;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF3-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;invoke(role=Role(&#x27;pm&#x27;,...), prompt=&#x27;write docs&#x27;) with _current_node=&#x27;ask_3&#x27;&quot;, &#x27;description&#x27;: &#x27;No node match and no prompt-pattern match; Strategy 3 (`role-only`) wins.&#x27;, &#x27;returns&#x27;: &quot;&#x27;role-match&#x27;&quot;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-SF3-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF3-1&#x27;, &#x27;action&#x27;: &quot;invoke(role=Role(&#x27;designer&#x27;,...), prompt=&#x27;create mockup&#x27;) with _current_node=&#x27;ask_4&#x27;&quot;, &#x27;description&#x27;: &#x27;No specific matcher applies; default matcher resolves.&#x27;, &#x27;returns&#x27;: &quot;&#x27;fallback&#x27;&quot;}</li></ol>
        </div>

    <h3>Entities</h3>
    <div class="entity-block">
            <h4><code>ENT-40</code>: CurrentNodeContextVar</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>value</code></td>
                        <td><code>ContextVar[str | None]</code></td>
                        <td>Non-breaking node identity channel published by SF-2 and consumed by SF-3 mocks.</td>
                    </tr><tr>
                        <td><code>default</code></td>
                        <td><code>None</code></td>
                        <td>Prevents stale node IDs leaking across unrelated calls.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-41</code>: ExecutionResult</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>success</code></td>
                        <td><code>bool</code></td>
                        <td>Whether workflow execution completed successfully.</td>
                    </tr><tr>
                        <td><code>error</code></td>
                        <td><code>ExecutionError | None</code></td>
                        <td>Structured execution error when `success` is false.</td>
                    </tr><tr>
                        <td><code>nodes_executed</code></td>
                        <td><code>list[tuple[str, str]]</code></td>
                        <td>Execution order trace consumed by SF-3 `assert_node_reached()`. Ordering is `(node_id, phase_id)` — not reversed.</td>
                    </tr><tr>
                        <td><code>artifacts</code></td>
                        <td><code>dict[str, Any]</code></td>
                        <td>Artifacts produced during execution.</td>
                    </tr><tr>
                        <td><code>branch_paths</code></td>
                        <td><code>dict[str, str]</code></td>
                        <td>Recorded branch decisions consumed by SF-3 `assert_branch_taken()`.</td>
                    </tr><tr>
                        <td><code>cost_summary</code></td>
                        <td><code>dict[str, Any]</code></td>
                        <td>Aggregated token/cost data.</td>
                    </tr><tr>
                        <td><code>duration_ms</code></td>
                        <td><code>float</code></td>
                        <td>Total execution duration.</td>
                    </tr><tr>
                        <td><code>workflow_output</code></td>
                        <td><code>dict[str, Any] | Any | None</code></td>
                        <td>Final workflow output value resolved from the workflow&#x27;s output port(s).</td>
                    </tr><tr>
                        <td><code>hook_warnings</code></td>
                        <td><code>list[str]</code></td>
                        <td>Non-fatal hook execution warnings collected during the run. SF-3 tests may assert on this list for hook-failure-path coverage.</td>
                    </tr><tr>
                        <td><code>history</code></td>
                        <td><code>ExecutionHistory | None</code></td>
                        <td>Execution history holding phase-mode metrics. Added to ExecutionResult per D-GR-34 requirement.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-42</code>: MockAgentRuntime</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>matchers</code></td>
                        <td><code>list[ResponseMatcher]</code></td>
                        <td>Matcher registry built through `when_node()`, `when_role()`, and `default_response()`.</td>
                    </tr><tr>
                        <td><code>calls</code></td>
                        <td><code>list[dict[str, Any]]</code></td>
                        <td>Call history for assertions and debugging.</td>
                    </tr><tr>
                        <td><code>invoke(role, prompt, *, output_type, workspace, session_key)</code></td>
                        <td><code>async method -&gt; str | BaseModel</code></td>
                        <td>Resolves the appropriate matcher, records the call, and returns a response or raises the configured error.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-43</code>: ValidationError</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>code</code></td>
                        <td><code>str</code></td>
                        <td>Machine-readable error code.</td>
                    </tr><tr>
                        <td><code>path</code></td>
                        <td><code>str</code></td>
                        <td>JSON path to the offending structure.</td>
                    </tr><tr>
                        <td><code>message</code></td>
                        <td><code>str</code></td>
                        <td>Human-readable error description.</td>
                    </tr><tr>
                        <td><code>context</code></td>
                        <td><code>dict[str, Any]</code></td>
                        <td>Additional debugging context.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-44</code>: ErrorNode</h4>
            <p>Fourth atomic node type per D-GR-36. Raises errors within the workflow DAG (e.g., log an error but let it bubble up). Terminal node with no outputs and no hooks.</p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td>Unique node identifier within the phase.</td>
                    </tr><tr>
                        <td><code>type</code></td>
                        <td><code>"error"</code></td>
                        <td>Discriminator. Always <code>"error"</code>.</td>
                    </tr><tr>
                        <td><code>message</code></td>
                        <td><code>str</code></td>
                        <td>Jinja2 template for the error message. Rendered at execution time against the node's input context.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, PortDefinition]</code></td>
                        <td>Input ports for the error node. Provides data context for the Jinja2 message template.</td>
                    </tr></tbody>
            </table>
        </div>

    <h3>Entity Relations</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>Relation</th><th>To</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>ER-40</code></td>
            <td><code>ENT-MockAgentRuntime</code></td>
            <td></td>
            <td><code>ENT-CurrentNodeContextVar</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-41</code></td>
            <td><code>ENT-MockAgentRuntime</code></td>
            <td></td>
            <td><code>ENT-ExecutionResult</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>Decisions</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Decision</th></tr></thead>
        <tbody><tr>
            <td><code>D-49</code></td>
            <td>D-SF3-1: Assertion helpers remain standalone functions rather than fluent assertion objects so they compose naturally with pytest and operate directly on `ExecutionResult`.</td>
        </tr><tr>
            <td><code>D-50</code></td>
            <td>D-SF3-2: All mock runtimes use a fluent configuration API with no-argument constructors. Dict-based constructors are prohibited because they cannot express priority matching, response sequences, per-matcher exception injection, or per-matcher cost metadata.</td>
        </tr><tr>
            <td><code>D-51</code></td>
            <td>D-SF3-3: YAML workflow fixtures live under `tests/fixtures/workflows/` and serialize every port container as a YAML map matching the dict-based schema model.</td>
        </tr><tr>
            <td><code>D-52</code></td>
            <td>D-SF3-4: The testing module lives at `iriai_compose/testing/` and ships behind the `[testing]` extra rather than as a separate package.</td>
        </tr><tr>
            <td><code>D-53</code></td>
            <td>D-SF3-5: `AgentRuntime.invoke()` remains unchanged per SF-2 published ABI. SF-2 propagates current node identity through a runner-managed `_current_node: ContextVar[str | None]`, and `MockAgentRuntime.when_node()` resolves against that ContextVar during `invoke()`. Adding `node_id` to `invoke()` is permanently prohibited.</td>
        </tr><tr>
            <td><code>D-54</code></td>
            <td>D-SF3-5a: Any invocation context exposed to `respond_with()` handlers is assembled by SF-2 in the canonical order `workflow -&gt; phase -&gt; actor -&gt; node`, with deduplication in that order, matching D-GR-23. SF-3 handlers consume this context; they must never reassemble it.</td>
        </tr><tr>
            <td><code>D-55</code></td>
            <td>D-SF3-6: `run_test()` is a thin delegation wrapper around SF-2 `run()` and must not swallow exceptions, rewrite `ExecutionResult`, synthesize a parallel runtime contract, or add checkpoint/resume behavior.</td>
        </tr><tr>
            <td><code>D-56</code></td>
            <td>D-SF3-7: SF-1 owns validation logic; SF-3 only re-exports validation functions and adds assertion helpers on top.</td>
        </tr><tr>
            <td><code>D-57</code></td>
            <td>D-SF3-8: Sequential build order is assumed. SF-3 implementation depends on SF-1 schema types and SF-2 runtime types being available first.</td>
        </tr><tr>
            <td><code>D-58</code></td>
            <td>D-SF3-9: Snapshot comparison uses `pyyaml` plus `difflib` only; no `deepdiff` dependency is introduced.</td>
        </tr><tr>
            <td><code>D-59</code></td>
            <td>D-SF3-10: `respond_sequence()` never wraps around. Exhaustion raises `MockExhaustedError` so loop-count bugs fail loudly.</td>
        </tr><tr>
            <td><code>D-60</code></td>
            <td>D-SF3-11: Anti-patterns are prohibited: `MockAgentRuntime.__init__` must not accept configuration parameters; `when_node()` and `when_role()` must return dedicated matcher objects; terminal matcher methods must return the parent runtime; matcher resolution must not depend on dict ordering; `MockAgentRuntime.invoke()` must not grow a `node_id` parameter; handler context assembly must not use any merge order other than `workflow -&gt; phase -&gt; actor -&gt; node`.</td>
        </tr><tr>
            <td><code>D-61</code></td>
            <td>D-SF3-12: All port containers throughout SF-3 use `dict[str, PortDefinition]` keyed by port name. This applies to builder internals, fixture factories, YAML fixtures, and edge resolution.</td>
        </tr><tr>
            <td><code>D-62</code></td>
            <td>D-SF3-13: Stale list-based port patterns are prohibited. Port names must not be stored redundantly inside `PortDefinition`, and `add_edge()` must not search port lists by name.</td>
        </tr><tr>
            <td><code>D-63</code></td>
            <td>D-SF3-14: No core checkpoint/resume contract is part of the SF-2 ABI consumed by SF-3. Testing helpers may assert execution history already returned by SF-2, but they must not require `checkpoint`, `resume`, or equivalent runner entry points to exist in the core declarative surface.</td>
        </tr><tr>
            <td><code>D-64</code></td>
            <td>D-SF3-16: SF-1→SF-3 import boundary established per D-GR-41. Canonical imports from `iriai_compose.schema`: `WorkflowConfig`, `PhaseDefinition`, `AskNode`, `BranchNode`, `PluginNode`, `ErrorNode`, `ActorDefinition`, `AgentActorDef`, `HumanActorDef`, `Edge`, `PortDefinition`, `ValidationError`, `load_workflow()`, `dump_workflow()`, `validate_workflow()`, `validate_type_flow()`, `detect_cycles()`. Four atomic node types: Ask, Branch, Plugin, Error (per D-GR-36). Phantom exports `MapNode`, `FoldNode`, `LoopNode`, `TransformRef`, `HookRef` do not exist and are permanently prohibited. Entity names `PhaseDefinition` (not `Phase`) and `Edge` (not `EdgeDefinition`) are canonical. Phase execution modes (`MapConfig`, `FoldConfig`, `LoopConfig`, `SequentialConfig`) are config types on `PhaseDefinition.mode`, not node types.</td>
        </tr><tr>
            <td><code>D-65</code></td>
            <td>D-SF3-17: SF-2→SF-3 ABI edge established per D-GR-41. Canonical run() signature: `async def run(workflow: WorkflowConfig | str | Path, config: RuntimeConfig, *, inputs: dict[str, Any] | None = None) -&gt; ExecutionResult`. The stale `(yaml_path, runtime, workspace, transform_registry, hook_registry)` signature is permanently rejected. `ExecutionResult.nodes_executed` ordering is `(node_id, phase_id)` — node first, containing phase second. `ExecutionResult.hook_warnings: list[str]` is a confirmed SF-2 field SF-3 may assert against. `ExecutionHistory` is a confirmed SF-2 export (added per D-GR-34) and the type of `ExecutionResult.history`. `RuntimeConfig` fields: `agent_runtime: AgentRuntime`, `interaction_runtimes: dict[str, InteractionRuntime]`, `artifacts: ArtifactStore | None`, `sessions: SessionStore | None`, `context_provider: ContextProvider | None`, `plugin_registry: PluginRegistry | None`, `workspace: Workspace | None`, `feature: Feature | None`. `run_test()` wraps these fields into RuntimeConfig — `interaction` parameter maps to `interaction_runtimes={&#x27;human&#x27;: interaction}` per D-GR-37.</td>
        </tr></tbody>
    </table>

    <h3>Risks</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Risk</th></tr></thead>
        <tbody><tr>
            <td><code>RISK-20</code></td>
            <td>RISK-1 (HIGH): SF-1 schema models are unavailable or diverge from the dict-based port contract. This blocks `WorkflowBuilder`, fixture factories, snapshots, and validation re-exports.</td>
        </tr><tr>
            <td><code>RISK-21</code></td>
            <td>RISK-2 (HIGH): SF-2 exports `run()`, `RuntimeConfig`, `ExecutionResult`, `ExecutionHistory`, `ExecutionError`, or `PluginRegistry` are unavailable at build time or diverge from the canonical signatures documented in D-GR-41. This blocks `run_test()`, end-to-end execution assertions, and migration-consumer parity. Mitigation: D-SF3-17 locks the canonical SF-2 export list; SF-3 implementers should treat any missing export as a blocking gap to raise with SF-2.</td>
        </tr><tr>
            <td><code>RISK-22</code></td>
            <td>RISK-3 (MEDIUM): SF-2 forgets to set or reset the current-node `ContextVar` around Ask-node dispatch. `when_node()` matchers become ineffective or leak stale node IDs.</td>
        </tr><tr>
            <td><code>RISK-23</code></td>
            <td>RISK-4 (MEDIUM): SF-2 or downstream consumers assemble handler context in a different order than `workflow -&gt; phase -&gt; actor -&gt; node`. Mitigation: D-SF3-5a standardizes merge order across SF-2, SF-3, and SF-4.</td>
        </tr><tr>
            <td><code>RISK-24</code></td>
            <td>RISK-5 (MEDIUM): Implementers invert the fluent chain and return the wrong object, breaking API ergonomics.</td>
        </tr><tr>
            <td><code>RISK-25</code></td>
            <td>RISK-6 (MEDIUM): WorkflowBuilder edge assignment heuristics may be insufficient for nested phases or plugin-heavy flows.</td>
        </tr><tr>
            <td><code>RISK-26</code></td>
            <td>RISK-7 (LOW): YAML fixtures written with list-based ports instead of YAML maps will fail snapshot round-trip tests immediately.</td>
        </tr><tr>
            <td><code>RISK-27</code></td>
            <td>RISK-8 (MEDIUM): Downstream migration consumers drift back to stale `invoke(..., node_id=...)` or core checkpoint/resume assumptions despite the published SF-2 ABI.</td>
        </tr><tr>
            <td><code>RISK-28</code></td>
            <td>RISK-9 (HIGH): SF-3 assertion code written against the wrong `nodes_executed` tuple order `(phase_id, node_id)` instead of the canonical `(node_id, phase_id)`. Tests would silently pass when they should fail. Mitigation: D-GR-41 fixes the ordering contract explicitly; implementers must use `nid, pid = entry` and search by the first element.</td>
        </tr><tr>
            <td><code>RISK-29</code></td>
            <td>RISK-10 (MEDIUM): SF-2 does not export `ExecutionHistory` from `iriai_compose.declarative.__init__` after D-GR-34 rewrite, leaving SF-3 unable to type-annotate `result.history`. Mitigation: D-GR-41 explicitly lists `ExecutionHistory` as a required SF-2 export; SF-2 implementers must add it to `__init__.py`.</td>
        </tr><tr>
            <td><code>RISK-30</code></td>
            <td>RISK-11 (MEDIUM): `run_test()` builds `RuntimeConfig` with `interaction_runtimes={&#x27;human&#x27;: interaction}` but the workflow expects a different key. Mitigation: document that the canonical key is `&#x27;human&#x27;` per D-GR-37; SF-3 test fixtures should use `human` as the interaction runtime key. Key `&#x27;default&#x27;` is rejected.</td>
        </tr></tbody>
    </table>
</section>
<hr/>


---

## Subfeature: Workflow Migration & Litmus Test (workflow-migration)

<!-- SF: workflow-migration -->
<section id="sf-workflow-migration" class="subfeature-section">
    <h2>SF-4 Workflow Migration &amp; Litmus Test</h2>
    <div class="provenance">Subfeature: <code>workflow-migration</code></div>

    <h3>Overview</h3>
    <div class="overview-text">SF-4 migrates three imperative Python workflows (planning, develop, bugfix) into declarative YAML conforming to the SF-1 schema, reclassifying 12 specialized plugins into three categories: general plugin type instances (store/hosting/mcp/subprocess/http/config), inline edge transforms for pure data functions, and AskNodes for LLM-mediated operations. iriai-build-v2 serves as the runner application with minimal updates: a thin _declarative.py wrapper imports iriai_compose.declarative.run(), maps BootstrappedEnv services to RuntimeConfig via D-A4 bridge adapters, and adds a --declarative CLI flag. RuntimeConfig uses authoritative field names per PRD R5: agent_runtime (singular AgentRuntime, not a dict), plugin_registry (not plugins dict). This revision aligns SF-4 to SF-2&#x27;s canonical non-breaking runtime ABI: AgentRuntime.invoke() stays unchanged, node_id is propagated through runner-managed ContextVars, hierarchical context assembly is standardized to workflow -&gt; phase -&gt; actor -&gt; node, and core checkpoint/resume is not part of the SF-2 contract. The plan defines six general plugin type interfaces, seven Category B edge transforms, three Category C AskNode conversions, three reusable task templates, ~50-55 behavioral equivalence tests, and a JSON seed package for SF-5 database seeding. D-GR-41 corrections applied: iriai_compose.schema export list corrected (phantom MapNode/FoldNode/LoopNode/TransformRef/HookRef removed, CostConfig replaced by WorkflowCostConfig/PhaseCostConfig/NodeCostConfig, 10+ missing exports added); run() signature fixed to (workflow: WorkflowConfig, config: RuntimeConfig, *, inputs=None); AskNode.actor_ref canonical field and prompt-only contract clarified; ActorDefinition.actor_type enum corrected to agent|human.</div>

    <h3>Services</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Name</th><th>Kind</th><th>Description</th><th>Technology</th><th>Port</th><th>Journeys</th>
        </tr></thead>
        <tbody><tr>
            <td><code>SVC-35</code></td>
            <td><strong>iriai_compose.schema</strong></td>
            <td><code>service</code></td>
            <td>SF-1 declarative schema module. Canonical exports: WorkflowConfig, PhaseDefinition, AskNode, BranchNode, BranchOutputPort, PluginNode, ErrorNode, EdgeDefinition, PortDefinition, ActorDefinition, RoleDefinition, TypeDefinition, MapModeConfig, FoldModeConfig, LoopModeConfig, SequentialModeConfig, StoreDefinition, StoreKeyDefinition, PluginInterface, PluginInstanceConfig, TemplateDefinition, WorkflowInputDefinition, WorkflowOutputDefinition, HookPortEvent, WorkflowCostConfig, PhaseCostConfig, NodeCostConfig. NOT exported (phantoms removed): MapNode, FoldNode, LoopNode, TransformRef, HookRef. Replaced: CostConfig (split into WorkflowCostConfig/PhaseCostConfig/NodeCostConfig). Renamed: Edge→EdgeDefinition, TemplateRef→TemplateDefinition, MapConfig/FoldConfig/LoopConfig/SequentialConfig→MapModeConfig/FoldModeConfig/LoopModeConfig/SequentialModeConfig.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-36</code></td>
            <td><strong>iriai_compose.schema.validation</strong></td>
            <td><code>service</code></td>
            <td>Structural validation: validate_workflow(), validate_type_flow(), detect_cycles() — returns list[ValidationError] using 21 canonical error codes (H-3)</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-37</code></td>
            <td><strong>iriai_compose.schema.io</strong></td>
            <td><code>service</code></td>
            <td>YAML serialization: load_workflow(path: str | Path) -&gt; WorkflowConfig and dump_workflow(config: WorkflowConfig) -&gt; str</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-38</code></td>
            <td><strong>iriai_compose.declarative</strong></td>
            <td><code>service</code></td>
            <td>SF-2 DAG loader and runner. Canonical ABI: run(workflow: WorkflowConfig, config: RuntimeConfig, *, inputs: dict | None = None) -&gt; ExecutionResult. Also exports: load_workflow(path) -&gt; WorkflowConfig (convenience wrapper around schema_io.load_workflow), validate(source: WorkflowConfig, plugins: PluginRegistry | None = None) -&gt; list[ValidationIssue], RuntimeConfig, ExecutionResult, PluginRegistry, ErrorRoute, CostSummary. Evaluates AST-validated transform_fn on edge traversal, dispatches PluginNode execution by plugin_type + instance config, sets phase/node ContextVars for each node execution, assembles deduplicated context in workflow -&gt; phase -&gt; actor -&gt; node order before calling AgentRuntime.invoke() with its existing unchanged signature. Checkpoint/resume is NOT part of this ABI.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-39</code></td>
            <td><strong>iriai_compose.testing</strong></td>
            <td><code>service</code></td>
            <td>SF-3 testing framework (downstream consumer of SF-2 ABI): MockAgentRuntime, MockInteractionRuntime, MockPluginRuntime, fluent mock API (when_node/when_role/default_response), exception injection (raise_error/SimulatedCrash), 12+ assertion functions, 10 preset fixture factories, WorkflowTestCase, execution trace snapshots. Contract: MockAgentRuntime.invoke() matches AgentRuntime.invoke() unchanged signature; node_id is captured from ContextVar-backed execution scope (not invoke kwargs). Exports: run_test(workflow, mocks, initial_input) -&gt; ExecutionResult, assert_node_reached, assert_artifact, assert_branch_taken, assert_phase_executed, assert_loop_iterations, assert_fold_items_processed, assert_error_routed, assert_yaml_round_trip, assert_validation_error, assert_node_count.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-40</code></td>
            <td><strong>iriai_compose.plugins</strong></td>
            <td><code>service</code></td>
            <td>Plugin system root: register_plugin_types(registry: PluginRegistry) -&gt; None and register_instances(registry: PluginRegistry) -&gt; None. Includes adapters.py with D-A4 bridge (create_plugin_runtimes factory, Protocol-based structural typing).</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-41</code></td>
            <td><strong>iriai_compose.plugins.types</strong></td>
            <td><code>service</code></td>
            <td>6 general PluginInterface declarations: STORE_INTERFACE (operations: put, delete), HOSTING_INTERFACE (operations: push, update, collect_annotations, clear_feedback), MCP_INTERFACE (operations: call_tool), SUBPROCESS_INTERFACE (operations: execute), HTTP_INTERFACE (operations: request), CONFIG_INTERFACE (operations: resolve — for secret/env resolution per D-GR-10)</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-42</code></td>
            <td><strong>iriai_compose.plugins.instances</strong></td>
            <td><code>service</code></td>
            <td>Concrete PluginInstanceConfig entries: artifact_db (plugin_type: store, backend: postgres), artifact_mirror (plugin_type: store, backend: filesystem), doc_host (plugin_type: hosting, backend: iriai-feedback), preview (plugin_type: mcp, server: preview-service), playwright (plugin_type: mcp, server: playwright), git (plugin_type: subprocess, executable: git), feedback_notify (plugin_type: http, url: ${FEEDBACK_SERVICE_URL}/notify), env_overrides (plugin_type: config — secrets configured on instance per D-GR-10)</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-43</code></td>
            <td><strong>iriai_compose.plugins.transforms</strong></td>
            <td><code>service</code></td>
            <td>7 Category B edge transform Python string constants (AST-validated, pure functions): tiered_context_builder, handover_compress, feedback_formatter, id_renumberer, collect_files, normalize_review_slugs, build_task_prompt. build_env_overrides reclassified to config Plugin per D-GR-10 and NOT in this module.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-44</code></td>
            <td><strong>iriai_compose.plugins.adapters</strong></td>
            <td><code>service</code></td>
            <td>D-A4 runtime bridge adapters: StorePluginAdapter, HostingPluginAdapter, McpPluginAdapter, SubprocessPluginAdapter, HttpPluginAdapter, ConfigPluginAdapter. Protocol-based structural typing (D-SF4-25). create_plugin_runtimes(services: dict, feature_id: str, artifacts: ArtifactStore) -&gt; dict[str, PluginRuntime] factory maps consumer service objects to PluginRuntime instances without importing consumer types.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3</td>
        </tr><tr>
            <td><code>SVC-45</code></td>
            <td><strong>iriai-build-v2 (Runner Application)</strong></td>
            <td><code>service</code></td>
            <td>Thin declarative runner wrapper (D-SF4-26). workflows/_declarative.py (~100 lines): calls schema_io.load_workflow(yaml_path) -&gt; WorkflowConfig, then run(workflow=loaded_config, config=RuntimeConfig(agent_runtime=ClaudeAgentRuntime(...), plugin_registry=registry, ...), inputs=None). CLI app.py gains --declarative flag on plan/develop/bugfix commands. Additive only — existing PlanningWorkflow, FullDevelopWorkflow, BugFixWorkflow and TrackedWorkflowRunner untouched. Existing ClaudeAgentRuntime/CodexAgentRuntime invoke signatures remain unchanged.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3</td>
        </tr><tr>
            <td><code>SVC-46</code></td>
            <td><strong>planning.yaml</strong></td>
            <td><code>service</code></td>
            <td>6-phase planning workflow: scoping (loop) → PM (sequential) → design (sequential) → architecture (sequential) → plan_review (loop) → task_planning (sequential). Declares input_type: ScopeOutput. Artifact writes via explicit store PluginNodes (D-GR-14). All actor_refs use actor_type: agent or human (not interaction).</td>
            <td><code>YAML</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-47</code></td>
            <td><strong>develop.yaml</strong></td>
            <td><code>service</code></td>
            <td>7-phase development workflow: 6 planning phases (standalone, no cross-file $ref) + ImplementationPhase (loop with fold &gt; map &gt; loop nesting). Standalone — no cross-file $ref to planning.yaml. Artifact writes via explicit store PluginNodes (D-GR-14).</td>
            <td><code>YAML</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-48</code></td>
            <td><strong>bugfix.yaml</strong></td>
            <td><code>service</code></td>
            <td>8-phase bugfix workflow: intake (loop) → env_setup → baseline → reproduction → diagnosis_fix (loop, max 3) → regression → approval → cleanup. Declares input_type: BugReport. Uses env_overrides config plugin (D-GR-10) in env_setup phase. Artifact writes via explicit store PluginNodes (D-GR-14).</td>
            <td><code>YAML</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-49</code></td>
            <td><strong>Task Templates</strong></td>
            <td><code>service</code></td>
            <td>3 reusable actor-centric YAML task templates (TemplateDefinition): gate_and_revise (approval loop), broad_interview (single-actor interview-to-completion), interview_gate_review (compiled artifact review with revision routing and fresh_sessions: true)</td>
            <td><code>YAML</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-50</code></td>
            <td><strong>tests/migration/</strong></td>
            <td><code>service</code></td>
            <td>~50-55 behavioral equivalence tests: conftest.py + test_planning.py (15) + test_develop.py (15) + test_bugfix.py (12) + test_yaml_roundtrip.py (5) + test_plugin_instances.py (8) + test_edge_transforms.py (10) + contract tests for unchanged AgentRuntime.invoke(), ContextVar node_id propagation, and workflow -&gt; phase -&gt; actor -&gt; node context merge order.</td>
            <td><code>Python/pytest</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-51</code></td>
            <td><strong>Seed Data Package</strong></td>
            <td><code>service</code></td>
            <td>migration_seed.json (3 workflows, 10 roles, 11 schemas, 3 templates, 6 plugin types, 8 plugin instances, 7 edge transforms — all is_example: true) + idempotent seed_loader.py. CLI: python seed_loader.py [--database-url URL] [--seed-file PATH]. Prints inserted/updated/unchanged summary.</td>
            <td><code>Python/JSON</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-52</code></td>
            <td><strong>SF-5 Database (tools/compose)</strong></td>
            <td><code>database</code></td>
            <td>PostgreSQL database in tools/compose receiving seeded workflow, role, schema, template, plugin type, plugin instance, and edge transform records via seed_loader.py upsert. Tables: workflows, roles, schemas, templates, plugin_types, plugin_instances, edge_transforms (all with is_example column).</td>
            <td><code>Postgres</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-53</code></td>
            <td><strong>artifact_db (Postgres Store)</strong></td>
            <td><code>database</code></td>
            <td>Primary artifact persistence. Written via explicit store PluginNode (operation: put) per D-GR-14. Read via context_keys on nodes/phases — never via store PluginNode read operations.</td>
            <td><code>Postgres</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-54</code></td>
            <td><strong>doc_host (iriai-feedback)</strong></td>
            <td><code>external</code></td>
            <td>Document hosting service providing URL generation and feedback annotation collection. Triggered via on_end hook edges from store PluginNodes (D-GR-14).</td>
            <td><code>iriai-feedback</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-55</code></td>
            <td><strong>preview MCP Server</strong></td>
            <td><code>external</code></td>
            <td>Preview deployment MCP server invoked via mcp PluginNode. Tools: preview_deploy, preview_teardown.</td>
            <td><code>MCP</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-56</code></td>
            <td><strong>playwright MCP Server</strong></td>
            <td><code>external</code></td>
            <td>E2E testing MCP server. Tool: run_e2e. Used in bugfix baseline and regression phases.</td>
            <td><code>MCP</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-57</code></td>
            <td><strong>git CLI</strong></td>
            <td><code>external</code></td>
            <td>Git CLI for branch creation (checkout -b), commit, and push via subprocess PluginNode.</td>
            <td><code>Git</code></td>
            <td>—</td>
            <td></td>
        </tr><tr>
            <td><code>SVC-58</code></td>
            <td><strong>feedback_notify (HTTP)</strong></td>
            <td><code>external</code></td>
            <td>Browser refresh notification endpoint. POST ${FEEDBACK_SERVICE_URL}/notify triggered after hosting updates.</td>
            <td><code>HTTP</code></td>
            <td>—</td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>Connections</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>To</th><th>Protocol</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>CONN-49</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-50</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-51</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-52</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML file</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-53</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML file</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-54</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML file</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-55</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-56</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-57</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-58</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-59</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-60</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-61</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-62</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-63</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-64</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-65</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-66</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML file</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-67</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML/Python</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-68</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML ref</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-69</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML $ref</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-70</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>store plugin</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-71</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>hosting plugin</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-72</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML file</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-73</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML/Python</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-74</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML ref</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-75</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML $ref</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-76</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>store plugin</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-77</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>hosting plugin</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-78</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>subprocess plugin</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-79</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>MCP plugin</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-80</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML file</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-81</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML/Python</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-82</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML ref</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-83</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>store plugin</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-84</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>subprocess plugin</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-85</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>MCP plugin</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-86</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>MCP plugin</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-87</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTP</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-88</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>YAML ref</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-89</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-90</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-91</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-92</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-93</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-94</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>File read</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-95</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>File read</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-96</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>File read</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-97</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>File ref</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-98</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>File ref</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-99</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>File ref</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-100</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>DB/SQL</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>API Endpoints</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Method</th><th>Path</th><th>Service</th><th>Description</th><th>Auth</th>
        </tr></thead>
        <tbody><tr>
            <td><code>API-82</code></td>
            <td><code>POST</code></td>
            <td><code>/schema/validate_workflow</code></td>
            <td><code></code></td>
            <td>validate_workflow(config: WorkflowConfig) -&gt; list[ValidationError]. Empty list on success. Uses 21 canonical error codes (H-3). Strictly blocking — no view-only fallback per D-GR-38.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-83</code></td>
            <td><code>POST</code></td>
            <td><code>/schema/validate_type_flow</code></td>
            <td><code></code></td>
            <td>validate_type_flow(config: WorkflowConfig) -&gt; list[ValidationError]. Validates type_ref compatibility across all EdgeDefinition instances.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-84</code></td>
            <td><code>POST</code></td>
            <td><code>/schema/detect_cycles</code></td>
            <td><code></code></td>
            <td>detect_cycles(config: WorkflowConfig) -&gt; list[ValidationError]. Returns error per cycle found outside intentional loop semantics.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-85</code></td>
            <td><code>POST</code></td>
            <td><code>/schema/io/load_workflow</code></td>
            <td><code></code></td>
            <td>load_workflow(path: str | Path) -&gt; WorkflowConfig. Parses YAML, hydrates all iriai_compose.schema models. Raises SchemaError on unknown fields (strict mode).</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-86</code></td>
            <td><code>POST</code></td>
            <td><code>/schema/io/dump_workflow</code></td>
            <td><code></code></td>
            <td>dump_workflow(config: WorkflowConfig) -&gt; str. Serializes WorkflowConfig to YAML string. Round-trip safe: load_workflow(dump_workflow(config)) == config.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-87</code></td>
            <td><code>POST</code></td>
            <td><code>/declarative/run</code></td>
            <td><code></code></td>
            <td>run(workflow: WorkflowConfig, config: RuntimeConfig, *, inputs: dict | None = None) -&gt; ExecutionResult. Canonical SF-2 ABI. Caller must load WorkflowConfig separately via load_workflow() before calling run(). AgentRuntime.invoke() unchanged; node_id set via ContextVar per node. Context merged workflow -&gt; phase -&gt; actor -&gt; node. Checkpoint/resume not in this ABI.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-88</code></td>
            <td><code>POST</code></td>
            <td><code>/declarative/validate</code></td>
            <td><code></code></td>
            <td>validate(source: WorkflowConfig, plugins: PluginRegistry | None = None) -&gt; list[ValidationIssue]. Runtime correctness validation beyond schema structural checks.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-89</code></td>
            <td><code>POST</code></td>
            <td><code>/declarative/load_workflow</code></td>
            <td><code></code></td>
            <td>load_workflow(path: str | Path) -&gt; WorkflowConfig. Convenience wrapper around schema_io.load_workflow(). Caller should call this before run().</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-90</code></td>
            <td><code>POST</code></td>
            <td><code>/plugins/register_plugin_types</code></td>
            <td><code></code></td>
            <td>register_plugin_types(registry: PluginRegistry) -&gt; None. Registers 6 general PluginInterface declarations (store, hosting, mcp, subprocess, http, config) into a PluginRegistry instance.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-91</code></td>
            <td><code>POST</code></td>
            <td><code>/plugins/register_instances</code></td>
            <td><code></code></td>
            <td>register_instances(registry: PluginRegistry) -&gt; None. Registers 8 concrete PluginInstanceConfig entries (artifact_db, artifact_mirror, doc_host, preview, playwright, git, feedback_notify, env_overrides) into a PluginRegistry instance.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-92</code></td>
            <td><code>POST</code></td>
            <td><code>/plugins/adapters/create_plugin_runtimes</code></td>
            <td><code></code></td>
            <td>create_plugin_runtimes(services: dict, feature_id: str, artifacts: ArtifactStore) -&gt; dict[str, PluginRuntime]. D-A4 factory. Protocol-based structural typing — accepts any service matching the structural interface without importing consumer types.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-93</code></td>
            <td><code>POST</code></td>
            <td><code>/testing/run_test</code></td>
            <td><code></code></td>
            <td>run_test(workflow: WorkflowConfig, mocks: MockRuntimeBundle, initial_input: dict | None = None) -&gt; ExecutionResult. Executes workflow with mock runtimes. No live API calls. SF-2 ABI: run(workflow, config, inputs=None) internally.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-94</code></td>
            <td><code>POST</code></td>
            <td><code>/testing/assert_node_reached</code></td>
            <td><code></code></td>
            <td>assert_node_reached(result: ExecutionResult, node_id: str) -&gt; None. Reads result.nodes_executed trace (populated from ContextVar scope per SF-2 ABI). Raises AssertionError if node_id not found.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-95</code></td>
            <td><code>POST</code></td>
            <td><code>/testing/assert_artifact</code></td>
            <td><code></code></td>
            <td>assert_artifact(result: ExecutionResult, key: str, matcher: Any = None) -&gt; None. Asserts artifact written via explicit store PluginNode. Optional matcher for value comparison.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-96</code></td>
            <td><code>POST</code></td>
            <td><code>/testing/assert_branch_taken</code></td>
            <td><code></code></td>
            <td>assert_branch_taken(result: ExecutionResult, node_id: str, port_name: str) -&gt; None. Asserts specific BranchOutputPort was activated at a BranchNode.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-97</code></td>
            <td><code>POST</code></td>
            <td><code>/testing/assert_phase_executed</code></td>
            <td><code></code></td>
            <td>assert_phase_executed(result: ExecutionResult, phase_id: str) -&gt; None. Asserts named phase was executed in the workflow run.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-98</code></td>
            <td><code>POST</code></td>
            <td><code>/testing/assert_loop_iterations</code></td>
            <td><code></code></td>
            <td>assert_loop_iterations(result: ExecutionResult, phase_id: str, expected: int) -&gt; None. Reads result.loop_iterations[phase_id].</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-99</code></td>
            <td><code>POST</code></td>
            <td><code>/testing/assert_fold_items_processed</code></td>
            <td><code></code></td>
            <td>assert_fold_items_processed(result: ExecutionResult, phase_id: str, expected: int) -&gt; None. Reads result.fold_progress[phase_id].</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-100</code></td>
            <td><code>POST</code></td>
            <td><code>/testing/assert_error_routed</code></td>
            <td><code></code></td>
            <td>assert_error_routed(result: ExecutionResult, from_node: str, to_node: str) -&gt; None. Reads result.errors_routed.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-101</code></td>
            <td><code>POST</code></td>
            <td><code>/testing/assert_yaml_round_trip</code></td>
            <td><code></code></td>
            <td>assert_yaml_round_trip(path: str | Path) -&gt; None. Calls load_workflow(path), dump_workflow(config), load_workflow(yaml_str), then compares. Asserts structural equivalence — no data loss on serialization.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-102</code></td>
            <td><code>POST</code></td>
            <td><code>/testing/assert_validation_error</code></td>
            <td><code></code></td>
            <td>assert_validation_error(config: WorkflowConfig, error_code: str | None = None) -&gt; None. Calls validate_workflow(config) and asserts at least one error matches.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-103</code></td>
            <td><code>POST</code></td>
            <td><code>/testing/assert_node_count</code></td>
            <td><code></code></td>
            <td>assert_node_count(result: ExecutionResult, expected: int) -&gt; None. Asserts len(result.nodes_executed) == expected.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-104</code></td>
            <td><code>POST</code></td>
            <td><code>/seed/load</code></td>
            <td><code></code></td>
            <td>CLI: python seed_loader.py [--database-url URL] [--seed-file PATH]. Idempotently upserts all is_example: true records into SF-5 PostgreSQL database (tools/compose). Prints &#x27;{N} inserted, {M} updated, {K} unchanged&#x27;. Safe to re-run.</td>
            <td><code>—</code></td>
        </tr></tbody>
    </table>

    <h3>Call Paths</h3>
    <div class="call-path-block">
            <h4><code>CP-19</code>: CLI --declarative flag triggers _declarative.py wrapper: loads WorkflowConfig from YAML, bootstraps BootstrappedEnv services into RuntimeConfig, then calls iriai_compose.declarative.run(workflow=loaded_config, config=RuntimeConfig(...))</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;to_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;action&#x27;: &quot;CLI: iriai-build plan &#x27;test&#x27; --workspace /path --declarative planning.yaml&quot;, &#x27;description&#x27;: &#x27;Click command parses --declarative flag, calls _run() with yaml_path parameter passed to _declarative.run_declarative()&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;to_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;action&#x27;: &#x27;bootstrap(workspace_path) -&gt; BootstrappedEnv&#x27;, &#x27;description&#x27;: &#x27;Standard bootstrap: asyncpg pool, stores, services. Same path as imperative workflows.&#x27;, &#x27;returns&#x27;: &#x27;BootstrappedEnv&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;to_service&#x27;: &#x27;schema_io&#x27;, &#x27;action&#x27;: &#x27;load_workflow(yaml_path) -&gt; WorkflowConfig&#x27;, &#x27;description&#x27;: &#x27;Load and hydrate YAML into WorkflowConfig before calling run(). REQUIRED: run() accepts WorkflowConfig object, not a yaml_path string.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;to_service&#x27;: &#x27;plugins_adapters&#x27;, &#x27;action&#x27;: &#x27;create_plugin_runtimes(services=env_services, feature_id=feature.id, artifacts=env.artifacts) -&gt; dict[str, PluginRuntime]&#x27;, &#x27;description&#x27;: &#x27;D-A4 bridge maps BootstrappedEnv services to PluginRuntime instances via Protocol-based adapters. hosting-&gt;HostingPluginAdapter, preview-&gt;McpPluginAdapter, git-&gt;SubprocessPluginAdapter, etc.&#x27;, &#x27;returns&#x27;: &#x27;dict[str, PluginRuntime]&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;to_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;action&#x27;: &#x27;RuntimeConfig(agent_runtime=ClaudeAgentRuntime(session_store=env.sessions), plugin_registry=registry, artifacts=env.artifacts, sessions=env.sessions, workspace=workspace, feature=feature)&#x27;, &#x27;description&#x27;: &#x27;Assemble RuntimeConfig. agent_runtime is a singular AgentRuntime instance (not dict[str, AgentRuntime]). plugin_registry wraps type_interfaces + instances.&#x27;, &#x27;returns&#x27;: &#x27;RuntimeConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;to_service&#x27;: &#x27;iriai_compose_declarative&#x27;, &#x27;action&#x27;: &#x27;run(workflow=loaded_config, config=runtime_config, inputs=None) -&gt; ExecutionResult&#x27;, &#x27;description&#x27;: &#x27;Calls SF-2 canonical ABI with WorkflowConfig object (not yaml_path). The wrapper does NOT pass node_id into AgentRuntime.invoke(); declarative runner injects phase/node identity through ContextVars per SF-2 contract.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionResult&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-20</code>: Runner invokes planning.yaml with ScopeOutput input, executes all 6 phases sequentially with explicit store PluginNode writes and hosting hooks</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;iriai_compose_declarative&#x27;, &#x27;to_service&#x27;: &#x27;schema_validation&#x27;, &#x27;action&#x27;: &#x27;validate_workflow(config) -&gt; list[ValidationError]&#x27;, &#x27;description&#x27;: &#x27;Validate DAG structure, type flow, and cycles using 21 canonical codes. Strictly blocking per D-GR-38 — empty list required to proceed.&#x27;, &#x27;returns&#x27;: &#x27;list[ValidationError] (empty on success)&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;iriai_compose_declarative&#x27;, &#x27;to_service&#x27;: &#x27;planning_workflow&#x27;, &#x27;action&#x27;: &quot;execute ScopingPhase (mode: loop, exit_condition: &#x27;data.complete&#x27;)&quot;, &#x27;description&#x27;: &#x27;Run interview loop until Envelope.complete=true. AskNodes use actor_ref (not actor). actor_type: human for scope_interviewer interaction, agent for scope_resolver. Each node: SF-2 runner sets phase/node ContextVars, merges workflow-&gt;phase-&gt;actor-&gt;node context, calls AgentRuntime.invoke() unchanged.&#x27;, &#x27;returns&#x27;: &#x27;ScopeOutput&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;planning_workflow&#x27;, &#x27;to_service&#x27;: &#x27;artifact_db_store&#x27;, &#x27;action&#x27;: &quot;explicit store PluginNode: operation=put, key=&#x27;scope&#x27; (D-GR-14)&quot;, &#x27;description&#x27;: &#x27;Write via store PluginNode only. No artifact_key auto-write. plugin_ref: artifact_db, config: {operation: put, key: scope}.&#x27;, &#x27;returns&#x27;: &#x27;StoreWriteResult&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;planning_workflow&#x27;, &#x27;to_service&#x27;: &#x27;doc_host_service&#x27;, &#x27;action&#x27;: &#x27;hosting PluginNode PUSH (on_end hook edge from store PluginNode)&#x27;, &#x27;description&#x27;: &#x27;Hook EdgeDefinition from write_scope.on_end triggers doc_host PluginNode (fire-and-forget). D-GR-14: hooks fire from explicit store PluginNodes.&#x27;, &#x27;returns&#x27;: &#x27;hosted_url&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;iriai_compose_declarative&#x27;, &#x27;to_service&#x27;: &#x27;planning_workflow&#x27;, &#x27;action&#x27;: &#x27;execute PM -&gt; Design -&gt; Architecture phases (mode: sequential)&#x27;, &#x27;description&#x27;: &#x27;Each phase uses TemplateDefinition refs (broad_interview, interview_gate_review). tiered_context_builder edge transform_fn applied on fold accumulator edges. SF-2 ABI governs: unchanged invoke(), ContextVar node identity, fixed context merge order.&#x27;, &#x27;returns&#x27;: &#x27;PRD, DesignDecisions, TechnicalPlan, SystemDesign&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;iriai_compose_declarative&#x27;, &#x27;to_service&#x27;: &#x27;planning_workflow&#x27;, &#x27;action&#x27;: &#x27;execute PlanReviewPhase (mode: loop, max_iterations: 3)&#x27;, &#x27;description&#x27;: &#x27;Map sub-phase: 3 parallel reviewer AskNodes. BranchNode per D-GR-12/D-GR-35: each output port has its own condition (non-exclusive fan-out). all_approved port -&gt; exit. architect revises on failure. max_exceeded -&gt; human escalation.&#x27;, &#x27;returns&#x27;: &#x27;Verdict&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;iriai_compose_declarative&#x27;, &#x27;to_service&#x27;: &#x27;planning_workflow&#x27;, &#x27;action&#x27;: &#x27;execute TaskPlanningPhase (mode: sequential)&#x27;, &#x27;description&#x27;: &#x27;broad_interview TemplateDefinition for GlobalImplementationStrategy, fold sub-phase for ImplementationDAG, interview_gate_review TemplateDefinition&#x27;, &#x27;returns&#x27;: &#x27;ImplementationDAG&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;planning_workflow&#x27;, &#x27;to_service&#x27;: &#x27;artifact_db_store&#x27;, &#x27;action&#x27;: &#x27;explicit store PluginNode PUT final artifacts: prd, design, plan, system_design (D-GR-14)&#x27;, &#x27;description&#x27;: &#x27;All compiled artifacts explicitly written via store PluginNodes. id_renumberer edge transform_fn applied before writes.&#x27;, &#x27;returns&#x27;: &#x27;StoreWriteResult&#x27;}</li><li>{&#x27;sequence&#x27;: 9, &#x27;from_service&#x27;: &#x27;iriai_compose_declarative&#x27;, &#x27;to_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;action&#x27;: &#x27;return ExecutionResult&#x27;, &#x27;description&#x27;: &#x27;Returns: nodes_executed (ordered trace from ContextVar scope), artifacts, branch_paths, loop_iterations, fold_progress, map_fan_out, errors_routed, cost_summary, duration_ms, workflow_output, hook_warnings&#x27;, &#x27;returns&#x27;: &#x27;ExecutionResult&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-21</code>: ImplementationPhase executes a 3-level nested DAG: outer loop (user approval), fold over task groups, map over parallel tasks, inner retry loop per group</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;iriai_compose_declarative&#x27;, &#x27;to_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;action&#x27;: &quot;start ImplementationPhase (mode: loop, exit_condition: &#x27;data.user_approved&#x27;)&quot;, &#x27;description&#x27;: &#x27;Outer loop exits when user approves. max_exceeded -&gt; human escalation via BranchNode port.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;to_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;action&#x27;: &#x27;BranchNode: has_feedback condition on input port&#x27;, &#x27;description&#x27;: &#x27;Per D-GR-12/D-GR-35: each BranchOutputPort carries its own condition expression. rejection port fires when feedback present. no_feedback port fires otherwise. Non-exclusive fan-out.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;to_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;action&#x27;: &#x27;FoldModeConfig sub-phase over dag.execution_order (groups)&#x27;, &#x27;description&#x27;: &#x27;accumulator_init: \&#x27;{&quot;handover&quot;: None, &quot;all_files&quot;: []}\&#x27;. Each iteration processes one dependency group. reducer merges ImplementationResult into accumulator.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;to_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;action&#x27;: &#x27;EdgeDefinition: transform_fn = build_task_prompt&#x27;, &#x27;description&#x27;: &#x27;AST-validated pure Python transform constructs structured implementation prompt from task spec. No secrets in transform_fn sandbox (D-GR-10).&#x27;, &#x27;returns&#x27;: &#x27;prompt string&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;to_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;action&#x27;: &#x27;MapModeConfig sub-phase over group.tasks (parallel)&#x27;, &#x27;description&#x27;: &#x27;Each task gets unique actor_ref: implementer-g{idx}-t{idx} to prevent state collision. SF-2 runner wraps each parallel dispatch with ContextVar token reset.&#x27;, &#x27;returns&#x27;: &#x27;list[ImplementationResult]&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;to_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;action&#x27;: &#x27;EdgeDefinition: collect_files transform_fn -&gt; smoke_tester AskNode -&gt; RetryLoop (max_iterations: 2)&#x27;, &#x27;description&#x27;: &#x27;collect_files flattens ImplementationResult file lists. Retry: on failure fix AskNode re-verifies. condition_met port -&gt; next group. max_exceeded port -&gt; escalation.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;to_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;action&#x27;: &#x27;EdgeDefinition: handover_compress transform_fn between fold iterations&#x27;, &#x27;description&#x27;: &#x27;Compresses older completed_tasks, keeps last 3 uncompressed. NEVER touches failed_attempts. Prevents unbounded accumulator growth.&#x27;, &#x27;returns&#x27;: &#x27;compressed HandoverDoc&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;to_service&#x27;: &#x27;git_cli&#x27;, &#x27;action&#x27;: &quot;subprocess PluginNode: execute(command=[&#x27;git&#x27;, &#x27;commit&#x27;, ...]) fire-and-forget&quot;, &#x27;description&#x27;: &#x27;plugin_ref: git, config: {subcommand: commit}. outputs: [] = fire-and-forget (not blocking).&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 9, &#x27;from_service&#x27;: &#x27;develop_workflow&#x27;, &#x27;to_service&#x27;: &#x27;artifact_db_store&#x27;, &#x27;action&#x27;: &#x27;explicit store PluginNode PUT implementation artifacts (D-GR-14)&#x27;, &#x27;description&#x27;: &#x27;Writes ImplementationResult and updated HandoverDoc via explicit store PluginNodes.&#x27;, &#x27;returns&#x27;: &#x27;StoreWriteResult&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-22</code>: Diagnosis loop: parallel RCA from two perspectives -&gt; fix -&gt; verify. Repeats up to 3 times. max_exceeded routes to approval with failure context.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;iriai_compose_declarative&#x27;, &#x27;to_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;action&#x27;: &#x27;start DiagnosisAndFixPhase (mode: loop, max_iterations: 3)&#x27;, &#x27;description&#x27;: &quot;exit_condition: &#x27;not data.reproduced&#x27; (fix verified). max_exceeded BranchOutputPort routes to ApprovalPhase with failure context.&quot;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;to_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;action&#x27;: &#x27;MapModeConfig: 2 parallel RCA AskNodes&#x27;, &#x27;description&#x27;: &#x27;collection: [{role: symptoms}, {role: architecture}]. actor_refs: rca_symptoms_analyst, rca_architecture_analyst. Distinct prompt fields — no task or context_text fields. SF-2 runner issues separate ContextVar tokens per parallel task.&#x27;, &#x27;returns&#x27;: &#x27;list[RootCauseAnalysis]&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;to_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;action&#x27;: &quot;bug_fixer AskNode: actor_ref=bug_fixer, prompt=&#x27;Synthesize RCA analyses...&#x27;&quot;, &#x27;description&#x27;: &#x27;actor_type: agent, model: claude-sonnet-4-6. prompt is the canonical field (NOT task or context_text). SF-2 runner sets current node ContextVar before calling unchanged AgentRuntime.invoke().&#x27;, &#x27;returns&#x27;: &#x27;BugFixResult&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;to_service&#x27;: &#x27;git_cli&#x27;, &#x27;action&#x27;: &quot;subprocess PluginNode: execute(command=[&#x27;git&#x27;, &#x27;commit&#x27;, &#x27;-am&#x27;, &#x27;fix: {slug}&#x27;])&quot;, &#x27;description&#x27;: &#x27;plugin_ref: git. Fire-and-forget (outputs: []).&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;to_service&#x27;: &#x27;preview_mcp_server&#x27;, &#x27;action&#x27;: &quot;mcp PluginNode: call_tool(tool_name=&#x27;preview_deploy&#x27;, force=True)&quot;, &#x27;description&#x27;: &#x27;plugin_ref: preview. Redeploys to preview environment.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;to_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;action&#x27;: &quot;bug_reproducer AskNode: actor_ref=bug_reproducer, prompt=&#x27;Re-run reproduction steps against preview...&#x27;&quot;, &#x27;description&#x27;: &#x27;actor_type: agent. prompt field only — no task/context_text. Re-runs reproduction steps.&#x27;, &#x27;returns&#x27;: &#x27;ReproductionResult&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;to_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;action&#x27;: &quot;BranchNode: not_reproduced port condition: &#x27;not data.reproduced&#x27;&quot;, &#x27;description&#x27;: &#x27;Per D-GR-35: per-port condition. not_reproduced=True -&gt; condition_met exits loop. still_reproducing -&gt; EdgeDefinition with handover_compress transform_fn -&gt; loop back.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;to_service&#x27;: &#x27;artifact_db_store&#x27;, &#x27;action&#x27;: &#x27;explicit store PluginNode PUT: rca, fix_result (D-GR-14)&#x27;, &#x27;description&#x27;: &#x27;Explicit writes via artifact_db store PluginNodes. Reads happen via context_keys only.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 9, &#x27;from_service&#x27;: &#x27;bugfix_workflow&#x27;, &#x27;to_service&#x27;: &#x27;preview_mcp_server&#x27;, &#x27;action&#x27;: &quot;CleanupPhase: mcp PluginNode call_tool(tool_name=&#x27;preview_teardown&#x27;) fire-and-forget&quot;, &#x27;description&#x27;: &#x27;plugin_ref: preview. outputs: [] = fire-and-forget.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-23</code>: pytest runs ~50-55 behavioral equivalence tests across Tier 1 (schema validation), Tier 2 (MockRuntime execution), plugin instance config, edge transform correctness, YAML round-trips</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;migration_test_suite&#x27;, &#x27;to_service&#x27;: &#x27;schema_io&#x27;, &#x27;action&#x27;: &quot;load_workflow(FIXTURES_DIR / &#x27;planning.yaml&#x27;) -&gt; WorkflowConfig (x3)&quot;, &#x27;description&#x27;: &#x27;conftest.py fixtures load all 3 YAMLs. Returns WorkflowConfig objects with hydrated AskNode (actor_ref field), PhaseDefinition (mode enum), BranchNode (per-port conditions).&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig x3&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;migration_test_suite&#x27;, &#x27;to_service&#x27;: &#x27;iriai_compose_plugins&#x27;, &#x27;action&#x27;: &#x27;register_plugin_types(registry), register_instances(registry)&#x27;, &#x27;description&#x27;: &#x27;PluginRegistry populated with 6 type interfaces and 8 instance configs.&#x27;, &#x27;returns&#x27;: &#x27;PluginRegistry&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;migration_test_suite&#x27;, &#x27;to_service&#x27;: &#x27;schema_validation&#x27;, &#x27;action&#x27;: &#x27;validate_workflow(config), validate_type_flow(config), detect_cycles(config)&#x27;, &#x27;description&#x27;: &#x27;Tier 1. Each must return empty list[ValidationError]. Verifies: actor_ref resolves to ActorDefinition with actor_type=agent|human (not interaction), plugin_ref resolves to PluginInstanceConfig, AskNode has prompt field (not task/context_text), no phantom MapNode/FoldNode/LoopNode/TransformRef/HookRef nodes.&#x27;, &#x27;returns&#x27;: &#x27;list[ValidationError] (empty)&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;migration_test_suite&#x27;, &#x27;to_service&#x27;: &#x27;plugins_transforms&#x27;, &#x27;action&#x27;: &quot;compile(transform_fn_str, &#x27;&lt;string&gt;&#x27;, &#x27;exec&#x27;) for all 7 transforms&quot;, &#x27;description&#x27;: &#x27;test_edge_transforms.py verifies syntactic validity and purity. AST-validated per D-GR-5. No secrets accessed inside transform_fn (D-GR-10). build_env_overrides NOT in transforms catalog.&#x27;, &#x27;returns&#x27;: &#x27;compiled code objects&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;migration_test_suite&#x27;, &#x27;to_service&#x27;: &#x27;iriai_compose_testing&#x27;, &#x27;action&#x27;: &#x27;MockAgentRuntime + MockInteractionRuntime + MockPluginRuntime setup via when_node().respond_sequence()&#x27;, &#x27;description&#x27;: &#x27;Fluent mock API. MockAgentRuntime.invoke() matches AgentRuntime.invoke() unchanged signature. when_node() resolution reads current node_id from ContextVar-backed scope per SF-2 ABI.&#x27;, &#x27;returns&#x27;: &#x27;MockRuntimeBundle&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;migration_test_suite&#x27;, &#x27;to_service&#x27;: &#x27;iriai_compose_testing&#x27;, &#x27;action&#x27;: &#x27;run_test(workflow=loaded_config, mocks=mock_bundle, initial_input=scope_output) -&gt; ExecutionResult&#x27;, &#x27;description&#x27;: &#x27;Tier 2 execution. MockRuntime drives through all phases including loop/fold/map modes. Same workflow-&gt;phase-&gt;actor-&gt;node context assembly as SF-2 production ABI.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionResult&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;migration_test_suite&#x27;, &#x27;to_service&#x27;: &#x27;iriai_compose_testing&#x27;, &#x27;action&#x27;: &#x27;assert_node_reached, assert_artifact, assert_branch_taken, assert_phase_executed, assert_loop_iterations, assert_fold_items_processed, assert_error_routed&#x27;, &#x27;description&#x27;: &#x27;Verify correct nodes reached, artifacts written via explicit store PluginNodes, correct BranchOutputPort conditions fired, phases in order. node_id assertions read nodes_executed trace from ContextVar scope per SF-2 ABI.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;migration_test_suite&#x27;, &#x27;to_service&#x27;: &#x27;iriai_compose_testing&#x27;, &#x27;action&#x27;: &#x27;assert_yaml_round_trip for all 3 workflows and 3 TemplateDefinition files&#x27;, &#x27;description&#x27;: &#x27;test_yaml_roundtrip.py: load -&gt; dump -&gt; reload -&gt; compare. No data loss. Verifies TemplateDefinition (not TemplateRef), EdgeDefinition (not Edge), MapModeConfig/FoldModeConfig/LoopModeConfig (not MapConfig/FoldConfig/LoopConfig) survive round-trip.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-24</code>: seed_loader.py idempotently upserts all migration seed records into SF-5 PostgreSQL database (tools/compose)</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;seed_data_package&#x27;, &#x27;to_service&#x27;: &#x27;seed_data_package&#x27;, &#x27;action&#x27;: &#x27;parse migration_seed.json&#x27;, &#x27;description&#x27;: &#x27;Reads and validates JSON: 3 workflows, 10 roles, 11 schemas, 3 templates, 6 plugin types, 8 instances, 7 edge transforms.&#x27;, &#x27;returns&#x27;: &#x27;seed: dict&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;seed_data_package&#x27;, &#x27;to_service&#x27;: &#x27;sf5_database&#x27;, &#x27;action&#x27;: &#x27;check existing records by slug/name/instance_id across all 7 tables&#x27;, &#x27;description&#x27;: &#x27;Queries each table for existing records matching slug/name/instance_id to determine insert vs update.&#x27;, &#x27;returns&#x27;: &#x27;existing_records: dict&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;seed_data_package&#x27;, &#x27;to_service&#x27;: &#x27;sf5_database&#x27;, &#x27;action&#x27;: &#x27;upsert workflows (3 records, is_example: true)&#x27;, &#x27;description&#x27;: &#x27;INSERT INTO workflows ... ON CONFLICT (slug) DO UPDATE SET ... for planning, develop, bugfix.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;seed_data_package&#x27;, &#x27;to_service&#x27;: &#x27;sf5_database&#x27;, &#x27;action&#x27;: &#x27;upsert plugin_types (6 records) + plugin_instances (8 records)&#x27;, &#x27;description&#x27;: &#x27;6 general plugin type interfaces (store, hosting, mcp, subprocess, http, config) + 8 concrete instances including env_overrides. Replaces old 12 specialized plugin records.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;seed_data_package&#x27;, &#x27;to_service&#x27;: &#x27;sf5_database&#x27;, &#x27;action&#x27;: &#x27;upsert roles (10), schemas (11), templates (3), edge_transforms (7)&#x27;, &#x27;description&#x27;: &#x27;All remaining seed entities upserted in single transaction. Includes summarizer, extractor Category C actors, StoreWriteResult schema.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;seed_data_package&#x27;, &#x27;to_service&#x27;: &#x27;seed_data_package&#x27;, &#x27;action&#x27;: &#x27;print summary: N inserted, M updated, K unchanged&#x27;, &#x27;description&#x27;: &#x27;Transaction committed. Safe to run multiple times — never deletes records.&#x27;, &#x27;returns&#x27;: &#x27;{inserted: int, updated: int, unchanged: int}&#x27;}</li></ol>
        </div>

    <h3>Entities</h3>
    <div class="entity-block">
            <h4><code>ENT-44</code>: WorkflowConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>schema_version</code></td>
                        <td><code>str</code></td>
                        <td>e.g. &#x27;2.0&#x27;</td>
                    </tr><tr>
                        <td><code>workflow_version</code></td>
                        <td><code>int</code></td>
                        <td>Monotonic integer version for this workflow definition</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td>Free-form key/value metadata</td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str]</code></td>
                        <td>Workflow-level context layer merged first (order: workflow -&gt; phase -&gt; actor -&gt; node). Per D-GR-41 SF-1-&gt;SF-4 correction.</td>
                    </tr><tr>
                        <td><code>input_type</code></td>
                        <td><code>str</code></td>
                        <td>Name of expected input TypeDefinition for the workflow&#x27;s first phase</td>
                    </tr><tr>
                        <td><code>actors</code></td>
                        <td><code>dict[str, ActorDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>phases</code></td>
                        <td><code>list[PhaseDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>edges</code></td>
                        <td><code>list[EdgeDefinition]</code></td>
                        <td>Cross-phase edges at workflow root level</td>
                    </tr><tr>
                        <td><code>templates</code></td>
                        <td><code>dict[str, TemplateDefinition]</code></td>
                        <td>Intra-file reusable template definitions (not TemplateRef)</td>
                    </tr><tr>
                        <td><code>plugins</code></td>
                        <td><code>dict[str, PluginInstanceConfig]</code></td>
                        <td>Plugin instance configs keyed by local plugin_ref name</td>
                    </tr><tr>
                        <td><code>types</code></td>
                        <td><code>dict[str, JsonSchema]</code></td>
                        <td>TypeDefinition entries using JSON Schema Draft 2020-12</td>
                    </tr><tr>
                        <td><code>cost_config</code></td>
                        <td><code>WorkflowCostConfig | None</code></td>
                        <td>Workflow-level budget cap and alert threshold</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-45</code>: AskNode</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>type</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>actor_ref</code></td>
                        <td><code>str</code></td>
                        <td>Resolves to ActorDefinition key in workflow.actors. Field name is actor_ref — NOT actor.</td>
                    </tr><tr>
                        <td><code>prompt</code></td>
                        <td><code>str</code></td>
                        <td>Canonical prompt text field. ONLY valid prompt field — task and context_text are NOT valid AskNode fields per SF-1 PRD (D-GR-41 SF-1-&gt;SF-4 correction).</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, WorkflowInputDefinition]</code></td>
                        <td>Named input port definitions</td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>dict[str, WorkflowOutputDefinition]</code></td>
                        <td>Named output port definitions</td>
                    </tr><tr>
                        <td><code>hooks</code></td>
                        <td><code>dict[str, WorkflowOutputDefinition]</code></td>
                        <td>Hook port definitions (e.g. on_end)</td>
                    </tr><tr>
                        <td><code>artifact_key</code></td>
                        <td><code>str | None</code></td>
                        <td>READ from store — not a write operation (D-GR-14)</td>
                    </tr><tr>
                        <td><code>output_type</code></td>
                        <td><code>str</code></td>
                        <td>TypeDefinition name for output type checking</td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str]</code></td>
                        <td>Node-local context layer merged last after workflow, phase, actor layers</td>
                    </tr><tr>
                        <td><code>cost</code></td>
                        <td><code>NodeCostConfig | None</code></td>
                        <td>Node-level budget cap</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-46</code>: BranchNode</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>type</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>list[PortDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>merge_function</code></td>
                        <td><code>str</code></td>
                        <td>Optional AST-validated Python expression for gather semantics when multiple inputs arrive</td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>list[BranchOutputPort]</code></td>
                        <td>Per D-GR-12/D-GR-35: each output port carries its own condition. Non-exclusive fan-out — multiple ports can fire if conditions met.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-47</code>: BranchOutputPort</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td>Port name</td>
                    </tr><tr>
                        <td><code>condition</code></td>
                        <td><code>str</code></td>
                        <td>AST-validated Python expression. Non-exclusive: multiple ports fire if conditions met. output_field mode REMOVED per D-GR-35.</td>
                    </tr><tr>
                        <td><code>type_ref</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-48</code>: PluginNode</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>type</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>plugin_ref</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>config</code></td>
                        <td><code>dict</code></td>
                        <td>Operation-specific config: {operation: &#x27;put&#x27;, key: &#x27;scope&#x27;} for store, {tool_name: &#x27;preview_deploy&#x27;} for mcp, etc.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>list[PortDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>list[PortDefinition]</code></td>
                        <td>Empty list = fire-and-forget</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-49</code>: ErrorNode</h4>
            <p>4th atomic node type per D-GR-36. Terminal error sink — logs an error message and halts the current execution path. Used to handle error logging patterns from iriai-build-v2. NO outputs, NO hooks.</p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td>Unique node identifier</td>
                    </tr><tr>
                        <td><code>type</code></td>
                        <td><code>Literal[&#x27;error&#x27;]</code></td>
                        <td>Always &#x27;error&#x27; — 4th atomic type alongside ask, branch, plugin</td>
                    </tr><tr>
                        <td><code>message</code></td>
                        <td><code>str</code></td>
                        <td>Jinja2 template string for the error message. Resolved at execution time with node inputs as template context.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict</code></td>
                        <td>Input bindings available as Jinja2 template context for the message field</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-50</code>: PhaseDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>mode</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>loop_config</code></td>
                        <td><code>LoopModeConfig</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>fold_config</code></td>
                        <td><code>FoldModeConfig</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>map_config</code></td>
                        <td><code>MapModeConfig</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str]</code></td>
                        <td>Phase-local context layer merged after workflow and before actor/node</td>
                    </tr><tr>
                        <td><code>nodes</code></td>
                        <td><code>list[AskNode|BranchNode|PluginNode|ErrorNode]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>edges</code></td>
                        <td><code>list[EdgeDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>cost</code></td>
                        <td><code>PhaseCostConfig | None</code></td>
                        <td>Phase-level budget cap</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-51</code>: EdgeDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>source</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>target</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>transform_fn</code></td>
                        <td><code>str</code></td>
                        <td>Optional AST-validated inline Python expression for Category B transforms. No secrets access (D-GR-10).</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-52</code>: LoopModeConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>exit_condition</code></td>
                        <td><code>str</code></td>
                        <td>e.g. &#x27;data.complete&#x27; for Envelope pattern</td>
                    </tr><tr>
                        <td><code>max_iterations</code></td>
                        <td><code>int</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>fresh_sessions</code></td>
                        <td><code>bool</code></td>
                        <td>True for gate review loops to prevent auto-approval contamination (D-SF4-13)</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-53</code>: FoldModeConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>collection</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>accumulator_init</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>reducer</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>fresh_sessions</code></td>
                        <td><code>bool</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-54</code>: MapModeConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>collection</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-55</code>: SequentialModeConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>description</code></td>
                        <td><code>str</code></td>
                        <td>Optional documentation only — sequential mode has no behavioral config parameters</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-56</code>: ActorDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>actor_type</code></td>
                        <td><code>str</code></td>
                        <td>Discriminator field. Valid values: &#x27;agent&#x27; or &#x27;human&#x27; ONLY. &#x27;interaction&#x27; is NOT valid — rejected per SF-1 PRD and D-GR-34.</td>
                    </tr><tr>
                        <td><code>provider</code></td>
                        <td><code>str</code></td>
                        <td>e.g. &#x27;anthropic&#x27;</td>
                    </tr><tr>
                        <td><code>model</code></td>
                        <td><code>str</code></td>
                        <td>e.g. &#x27;claude-sonnet-4-6&#x27;</td>
                    </tr><tr>
                        <td><code>role</code></td>
                        <td><code>RoleDefinition</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>persistent</code></td>
                        <td><code>bool</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str]</code></td>
                        <td>Actor-level context merged between phase and node layers</td>
                    </tr><tr>
                        <td><code>identity</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>channel</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-57</code>: RoleDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>model</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>tools</code></td>
                        <td><code>list[str]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>system_prompt</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-58</code>: TypeDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>schema_def</code></td>
                        <td><code>dict</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-59</code>: TemplateDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>ref</code></td>
                        <td><code>str</code></td>
                        <td>Intra-file $ref path to a phase or node pattern</td>
                    </tr><tr>
                        <td><code>bind</code></td>
                        <td><code>dict</code></td>
                        <td>Parameter bindings for template instantiation</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-60</code>: StoreDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>keys</code></td>
                        <td><code>dict[str, StoreKeyDefinition]</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-61</code>: StoreKeyDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>type_ref</code></td>
                        <td><code>str</code></td>
                        <td>References a TypeDefinition name for type-checked store operations</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-62</code>: PortDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>type_ref</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>schema_def</code></td>
                        <td><code>dict</code></td>
                        <td>Inline JSON Schema for XOR enforcement when type_ref absent</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-63</code>: WorkflowInputDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>type_ref</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>required</code></td>
                        <td><code>bool</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-64</code>: WorkflowOutputDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>type_ref</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-65</code>: HookPortEvent</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>event</code></td>
                        <td><code>str</code></td>
                        <td>Lifecycle event that triggers the hook edge</td>
                    </tr><tr>
                        <td><code>port_name</code></td>
                        <td><code>str</code></td>
                        <td>Port name on the source node that emits the hook</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-66</code>: PluginInterface</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>list[PortDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>list[PortDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>config_schema</code></td>
                        <td><code>dict</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>operations</code></td>
                        <td><code>list[str]</code></td>
                        <td>e.g. [&#x27;put&#x27;, &#x27;delete&#x27;] for store, [&#x27;call_tool&#x27;] for mcp</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-67</code>: PluginInstanceConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>instance_id</code></td>
                        <td><code>str</code></td>
                        <td>e.g. &#x27;artifact_db&#x27;, &#x27;doc_host&#x27;</td>
                    </tr><tr>
                        <td><code>plugin_type</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>config</code></td>
                        <td><code>dict</code></td>
                        <td>Backend-specific config. Secrets configured here for config type — never in transform_fn sandbox (D-GR-10).</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-68</code>: WorkflowCostConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>max_cost_usd</code></td>
                        <td><code>float | None</code></td>
                        <td>Hard cap in USD for entire workflow execution</td>
                    </tr><tr>
                        <td><code>alert_threshold_usd</code></td>
                        <td><code>float | None</code></td>
                        <td>Soft alert threshold — logs warning when exceeded</td>
                    </tr><tr>
                        <td><code>enforce</code></td>
                        <td><code>bool</code></td>
                        <td>If true, raises CostLimitExceeded when max_cost_usd exceeded; if false, logs warning only</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-69</code>: PhaseCostConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>max_cost_usd</code></td>
                        <td><code>float | None</code></td>
                        <td>Hard cap in USD for this phase&#x27;s execution</td>
                    </tr><tr>
                        <td><code>alert_threshold_usd</code></td>
                        <td><code>float | None</code></td>
                        <td>Soft alert threshold for this phase</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-70</code>: NodeCostConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>max_cost_usd</code></td>
                        <td><code>float | None</code></td>
                        <td>Hard cap in USD for a single node invocation</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-71</code>: ExecutionResult</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>success</code></td>
                        <td><code>bool</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>error</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>nodes_executed</code></td>
                        <td><code>list[str]</code></td>
                        <td>Ordered node_id trace captured by SF-2 runner-managed ContextVar execution scope. NOT populated from invoke() kwargs.</td>
                    </tr><tr>
                        <td><code>artifacts</code></td>
                        <td><code>dict[str, Any]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>branch_paths</code></td>
                        <td><code>dict[str, list[str]]</code></td>
                        <td>node_id -&gt; list of fired BranchOutputPort names</td>
                    </tr><tr>
                        <td><code>loop_iterations</code></td>
                        <td><code>dict[str, int]</code></td>
                        <td>phase_id -&gt; iteration count</td>
                    </tr><tr>
                        <td><code>fold_progress</code></td>
                        <td><code>dict[str, int]</code></td>
                        <td>phase_id -&gt; items processed</td>
                    </tr><tr>
                        <td><code>map_fan_out</code></td>
                        <td><code>dict[str, int]</code></td>
                        <td>phase_id -&gt; parallel task count</td>
                    </tr><tr>
                        <td><code>errors_routed</code></td>
                        <td><code>list[ErrorRoute]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>cost_summary</code></td>
                        <td><code>CostSummary</code></td>
                        <td>Token counts and USD totals by workflow/phase/node</td>
                    </tr><tr>
                        <td><code>duration_ms</code></td>
                        <td><code>int</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>workflow_output</code></td>
                        <td><code>Any</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>hook_warnings</code></td>
                        <td><code>list[str]</code></td>
                        <td>Fire-and-forget hook failures that did not abort execution</td>
                    </tr><tr>
                        <td><code>history</code></td>
                        <td><code>list[NodeExecutionRecord]</code></td>
                        <td>Full ordered execution history per node per D-GR-34</td>
                    </tr><tr>
                        <td><code>phase_metrics</code></td>
                        <td><code>dict[str, PhaseMetrics]</code></td>
                        <td>Per-phase timing and cost breakdown per D-GR-34</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-72</code>: RuntimeConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>agent_runtime</code></td>
                        <td><code>AgentRuntime</code></td>
                        <td>Singular AgentRuntime per PRD R5 (NOT dict[str, AgentRuntime]). Uses unchanged AgentRuntime.invoke(role, prompt, *, output_type, workspace, session_key) SF-2 ABI. node_id is NOT an invoke() parameter.</td>
                    </tr><tr>
                        <td><code>interaction_runtimes</code></td>
                        <td><code>dict[str, InteractionRuntime]</code></td>
                        <td>Keyed by actor_ref or channel type</td>
                    </tr><tr>
                        <td><code>artifacts</code></td>
                        <td><code>ArtifactStore</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>sessions</code></td>
                        <td><code>SessionStore</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>context_provider</code></td>
                        <td><code>HierarchicalContext</code></td>
                        <td>Resolves deduplicated context in fixed SF-2 ABI order: workflow -&gt; phase -&gt; actor -&gt; node</td>
                    </tr><tr>
                        <td><code>plugin_registry</code></td>
                        <td><code>PluginRegistry</code></td>
                        <td>Authoritative name per PRD R5 (NOT &#x27;plugins&#x27; dict). Wraps type_interfaces + instances.</td>
                    </tr><tr>
                        <td><code>workspace</code></td>
                        <td><code>Workspace | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>feature</code></td>
                        <td><code>Feature</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>max_cost</code></td>
                        <td><code>float | None</code></td>
                        <td>Runtime-level override for cost cap</td>
                    </tr><tr>
                        <td><code>dry_run</code></td>
                        <td><code>bool</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-73</code>: PluginRegistry</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>type_interfaces</code></td>
                        <td><code>dict[str, PluginInterface]</code></td>
                        <td>Keyed by plugin type name: store, hosting, mcp, subprocess, http, config</td>
                    </tr><tr>
                        <td><code>instances</code></td>
                        <td><code>dict[str, PluginInstanceConfig]</code></td>
                        <td>Keyed by instance_id: artifact_db, doc_host, git, preview, playwright, artifact_mirror, feedback_notify, env_overrides</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-74</code>: ErrorRoute</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>from_node</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>to_node</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>error_type</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>phase_id</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-75</code>: MockAgentRuntime</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>responses</code></td>
                        <td><code>dict[tuple[str, str], Any]</code></td>
                        <td>Keyed by (node_id, role_name). node_id read from ContextVar-backed execution scope per SF-2 ABI — NOT from invoke() kwargs.</td>
                    </tr><tr>
                        <td><code>store</code></td>
                        <td><code>dict[str, Any]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>executed_nodes</code></td>
                        <td><code>list[str]</code></td>
                        <td>Ordered node_id trace from SF-2 ContextVar execution scope</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-76</code>: MockInteractionRuntime</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>responses</code></td>
                        <td><code>list[Any]</code></td>
                        <td>Scripted response sequence for human actor_type nodes</td>
                    </tr><tr>
                        <td><code>node_id_capture</code></td>
                        <td><code>str | None</code></td>
                        <td>node_id captured from SF-2 ContextVar scope for assertions; NOT passed via invoke()</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-77</code>: Envelope</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>complete</code></td>
                        <td><code>bool</code></td>
                        <td>Loop exit condition target: LoopModeConfig.exit_condition = &#x27;data.complete&#x27;</td>
                    </tr><tr>
                        <td><code>artifact_path</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>question</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>options</code></td>
                        <td><code>list | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>output</code></td>
                        <td><code>Any</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-78</code>: Verdict</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>approved</code></td>
                        <td><code>bool</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>feedback</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>annotations</code></td>
                        <td><code>dict | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-79</code>: ReviewOutcome</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>verdict</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>issues</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>suggestions</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-80</code>: HandoverDoc</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>completed_tasks</code></td>
                        <td><code>list</code></td>
                        <td>Compressed by handover_compress transform_fn after fold iteration (keeps last 3 uncompressed)</td>
                    </tr><tr>
                        <td><code>failed_attempts</code></td>
                        <td><code>list</code></td>
                        <td>handover_compress MUST NOT touch this field</td>
                    </tr><tr>
                        <td><code>context_summary</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-81</code>: StoreWriteResult</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>key</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>timestamp</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>success</code></td>
                        <td><code>bool</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-82</code>: ScopeOutput</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>feature_name</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>feature_description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>repos</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>codebase_root</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>target_branch</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-83</code>: ImplementationDAG</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>groups</code></td>
                        <td><code>list</code></td>
                        <td>Dependency groups for fold-over execution</td>
                    </tr><tr>
                        <td><code>execution_order</code></td>
                        <td><code>list</code></td>
                        <td>Ordered group IDs for fold collection expression</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-84</code>: ImplementationResult</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>files_created</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>files_modified</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>tests_added</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>summary</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-85</code>: BugReport</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>title</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>reproduction_steps</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>expected_behavior</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>actual_behavior</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>severity</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-86</code>: ReproductionResult</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>reproduced</code></td>
                        <td><code>bool</code></td>
                        <td>Loop exit guard: LoopModeConfig.exit_condition = &#x27;not data.reproduced&#x27;</td>
                    </tr><tr>
                        <td><code>steps_executed</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>evidence</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-87</code>: RootCauseAnalysis</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>root_cause</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>evidence</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>affected_files</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>confidence</code></td>
                        <td><code>float</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-88</code>: BugFixResult</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>fix_description</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>files_changed</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>tests_added</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>verification_status</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-89</code>: migration_seed.json</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>version</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>generated_from</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>workflows</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>roles</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>schemas</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>templates</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>plugin_types</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>plugin_instances</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>edge_transforms</code></td>
                        <td><code>list</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div>

    <h3>Entity Relations</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>Relation</th><th>To</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>ER-42</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>phase_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-43</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>actor_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-44</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>type_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-45</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>plugin_instance_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-46</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>template_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-47</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>edge_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-48</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>workflow_cost_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-49</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>ask_node</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-50</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>branch_node</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-51</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>plugin_node</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-52</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>error_node</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-53</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>edge_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-54</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>loop_mode_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-55</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>fold_mode_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-56</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>map_mode_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-57</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>phase_cost_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-58</code></td>
            <td><code>ask_node</code></td>
            <td></td>
            <td><code>actor_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-59</code></td>
            <td><code>ask_node</code></td>
            <td></td>
            <td><code>workflow_input_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-60</code></td>
            <td><code>ask_node</code></td>
            <td></td>
            <td><code>workflow_output_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-61</code></td>
            <td><code>ask_node</code></td>
            <td></td>
            <td><code>node_cost_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-62</code></td>
            <td><code>branch_node</code></td>
            <td></td>
            <td><code>branch_output_port</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-63</code></td>
            <td><code>actor_definition</code></td>
            <td></td>
            <td><code>role_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-64</code></td>
            <td><code>plugin_instance_config</code></td>
            <td></td>
            <td><code>plugin_interface</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-65</code></td>
            <td><code>store_definition</code></td>
            <td></td>
            <td><code>store_key_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-66</code></td>
            <td><code>store_key_definition</code></td>
            <td></td>
            <td><code>type_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-67</code></td>
            <td><code>plugin_registry</code></td>
            <td></td>
            <td><code>plugin_interface</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-68</code></td>
            <td><code>plugin_registry</code></td>
            <td></td>
            <td><code>plugin_instance_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-69</code></td>
            <td><code>mock_agent_runtime</code></td>
            <td></td>
            <td><code>mock_interaction_runtime</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-70</code></td>
            <td><code>scope_output</code></td>
            <td></td>
            <td><code>implementation_dag</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-71</code></td>
            <td><code>implementation_dag</code></td>
            <td></td>
            <td><code>implementation_result</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-72</code></td>
            <td><code>bug_report</code></td>
            <td></td>
            <td><code>reproduction_result</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-73</code></td>
            <td><code>reproduction_result</code></td>
            <td></td>
            <td><code>root_cause_analysis</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-74</code></td>
            <td><code>root_cause_analysis</code></td>
            <td></td>
            <td><code>bug_fix_result</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-75</code></td>
            <td><code>handover_doc</code></td>
            <td></td>
            <td><code>implementation_result</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-76</code></td>
            <td><code>execution_result</code></td>
            <td></td>
            <td><code>store_write_result</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-77</code></td>
            <td><code>execution_result</code></td>
            <td></td>
            <td><code>error_route</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-78</code></td>
            <td><code>seed_file</code></td>
            <td></td>
            <td><code>plugin_interface</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-79</code></td>
            <td><code>seed_file</code></td>
            <td></td>
            <td><code>plugin_instance_config</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>Decisions</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Decision</th></tr></thead>
        <tbody><tr>
            <td><code>D-66</code></td>
            <td>D-GR-41 (SF-1→SF-2): iriai_compose.schema canonical exports corrected — CostConfig replaced by WorkflowCostConfig/PhaseCostConfig/NodeCostConfig, phantoms MapNode/FoldNode/LoopNode/TransformRef/HookRef confirmed absent, renamed types applied (Edge→EdgeDefinition, TemplateRef→TemplateDefinition, MapConfig→MapModeConfig, FoldConfig→FoldModeConfig, LoopConfig→LoopModeConfig, SequentialConfig→SequentialModeConfig), 10+ missing exports added (BranchOutputPort, WorkflowInputDefinition, WorkflowOutputDefinition, HookPortEvent, TemplateDefinition, StoreKeyDefinition, WorkflowCostConfig, PhaseCostConfig, NodeCostConfig).</td>
        </tr><tr>
            <td><code>D-67</code></td>
            <td>D-GR-41 (SF-2→SF-3): run() canonical ABI signature is run(workflow: WorkflowConfig, config: RuntimeConfig, *, inputs: dict | None = None) -&gt; ExecutionResult. Caller must call load_workflow(path) -&gt; WorkflowConfig separately before calling run(). Deprecated signature (yaml_path, runtime, workspace, transform_registry, hook_registry) is not valid.</td>
        </tr><tr>
            <td><code>D-68</code></td>
            <td>D-GR-41 (SF-1→SF-4): AskNode uses actor_ref (not actor) and prompt (not task or context_text) as canonical fields. ActorDefinition.actor_type is Literal[&#x27;agent&#x27;, &#x27;human&#x27;] — &#x27;interaction&#x27; is not valid. WorkflowConfig has context_keys at root level (workflow-level context layer, merged first).</td>
        </tr><tr>
            <td><code>D-69</code></td>
            <td>D-GR-35: BranchNode uses BranchOutputPort per output port, each with its own condition expression. Non-exclusive fan-out — multiple ports fire if conditions met. merge_function valid for gather. switch_function and output_field removed entirely.</td>
        </tr><tr>
            <td><code>D-70</code></td>
            <td>D-GR-34: ActorDefinition.actor_type Literal[&#x27;agent&#x27;, &#x27;human&#x27;] only. No &#x27;interaction&#x27; alias. ExecutionResult includes history (list[NodeExecutionRecord]) and phase_metrics (dict[str, PhaseMetrics]) fields.</td>
        </tr><tr>
            <td><code>D-71</code></td>
            <td>D-SF4-1: Three-category reclassification of 12 specialized plugins: (A) infrastructure connectors → 6 general plugin type instances (store/hosting/mcp/subprocess/http/config), (B) pure data transforms → inline EdgeDefinition.transform_fn, (C) LLM-mediated computation → AskNodes.</td>
        </tr><tr>
            <td><code>D-72</code></td>
            <td>D-SF4-7: Store plugins are WRITE-ONLY (D-GR-14). context_keys = READ. PluginNode with plugin_type=store = WRITE. No artifact_key auto-write.</td>
        </tr><tr>
            <td><code>D-73</code></td>
            <td>D-SF4-10: Hierarchical context: workflow -&gt; phase -&gt; actor -&gt; node, deduplicated first-seen order. Authoritative SF-2 ABI shared by SF-3 and SF-4.</td>
        </tr><tr>
            <td><code>D-74</code></td>
            <td>D-SF4-22: iriai-build-v2 is read-only for all existing Python workflow classes. Only additive: workflows/_declarative.py wrapper + --declarative CLI flag.</td>
        </tr><tr>
            <td><code>D-75</code></td>
            <td>D-SF4-25: D-A4 bridge in iriai_compose/plugins/adapters.py uses Protocol-based structural typing. No consumer type imports.</td>
        </tr><tr>
            <td><code>D-76</code></td>
            <td>D-SF4-27: SF-2 dag-loader-runner owns canonical runtime ABI: AgentRuntime.invoke() unchanged, ContextVars publish phase_id/node_id, fixed context merge order, checkpoint/resume out of SF-2 boundary.</td>
        </tr></tbody>
    </table>

    <h3>Risks</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Risk</th></tr></thead>
        <tbody><tr>
            <td><code>RISK-31</code></td>
            <td>RISK-1 (medium): SF-1 schema module may still use stale names (Edge, TemplateRef, MapConfig/FoldConfig/LoopConfig, CostConfig) in implementation before D-GR-41 corrections land. Mitigation: SF-4&#x27;s import statements must use canonical names; add compatibility shim in _compat.py if SF-1 ships stale names. Affects STEP-1, STEP-3, STEP-4.</td>
        </tr><tr>
            <td><code>RISK-32</code></td>
            <td>RISK-2 (medium): Edge transform complexity — tiered_context_builder and build_task_prompt (~20 lines inline in YAML) are hard to read/debug. Mitigation: Catalog all 7 transforms as named constants in plugins/transforms.py; unit-test in test_edge_transforms.py. Affects STEP-1, STEP-3.</td>
        </tr><tr>
            <td><code>RISK-33</code></td>
            <td>RISK-3 (medium): Runner transform sandbox — inline Python in EdgeDefinition.transform_fn requires AST-validated exec per D-GR-5. No secrets in sandbox (D-GR-10). env_overrides is config Plugin, NOT a transform. Affects STEP-3, STEP-4, STEP-5.</td>
        </tr><tr>
            <td><code>RISK-34</code></td>
            <td>RISK-4 (low): Category C AskNode proliferation — 3 new actors (summarizer, extractor, sd_converter_agent). Mitigation: summarizer/extractor use claude-haiku; sd_converter_agent uses claude-sonnet-4-6. Actor defs shared across phases. Affects STEP-3, STEP-4.</td>
        </tr><tr>
            <td><code>RISK-35</code></td>
            <td>RISK-5 (medium): Develop-planning structural drift — planning phases in develop.yaml may diverge from planning.yaml. Mitigation: test_develop_planning_phases_match consistency tests in CI. Affects STEP-4, STEP-7.</td>
        </tr><tr>
            <td><code>RISK-36</code></td>
            <td>RISK-6 (medium): SF-2 runner may lag on nested phase modes, fresh_sessions, AST-validated transform eval, ContextVar node publication, error-port routing, history/phase_metrics fields per D-GR-34. Mitigation: Build order STEP-1/2 (no SF-2 dep) -&gt; STEP-3/4/5 (SF-1 structural only) -&gt; STEP-7 (SF-2 required). Affects STEP-7.</td>
        </tr><tr>
            <td><code>RISK-37</code></td>
            <td>RISK-7 (medium): Missing store writes — converting implicit artifacts.put() to explicit store PluginNodes (D-GR-14) may miss calls. Mitigation: grep audit all artifacts.put() in iriai-build-v2 read-only. test_store_writes_are_explicit verifies every artifact has a store PluginNode. Affects STEP-3, STEP-4, STEP-5.</td>
        </tr><tr>
            <td><code>RISK-38</code></td>
            <td>RISK-8 (low): YAML file size — develop.yaml with 60+ nodes, 7 phases, nested fold &gt; map &gt; loop, inline transform_fn. Mitigation: TemplateDefinitions absorb ~15 nodes each. Named transform constants reduce inline YAML. Affects STEP-4.</td>
        </tr><tr>
            <td><code>RISK-39</code></td>
            <td>RISK-9 (low): iriai-build-v2 integration timing — _declarative.py depends on SF-2 shipping run() + RuntimeConfig + PluginRuntime ABC. Mitigation: lazy import iriai_compose.declarative with graceful error if unavailable. AgentRuntime.invoke() unchanged so no Claude/Codex runtime updates needed. Affects STEP-10.</td>
        </tr><tr>
            <td><code>RISK-40</code></td>
            <td>RISK-10 (medium): ContextVar scope leakage across nested map/parallel execution could mis-attribute node_id. Mitigation: wrap every node dispatch in ContextVar token reset; SF-3/SF-4 contract tests cover ContextVar node propagation and workflow-&gt;phase-&gt;actor-&gt;node merge order. Affects STEP-7, STEP-8.</td>
        </tr></tbody>
    </table>
</section>
<hr/>


---

## Subfeature: Composer App Foundation & Tools Hub (composer-app-foundation)

<!-- SF: composer-app-foundation -->
<section id="sf-composer-app-foundation" class="subfeature-section">
    <h2>SF-5 Composer App Foundation &amp; Tools Hub</h2>
    <div class="provenance">Subfeature: <code>composer-app-foundation</code></div>

    <h3>Overview</h3>
    <div class="overview-text">SF-5 establishes the canonical compose foundation: `tools/compose/backend` for the FastAPI + PostgreSQL service, `tools/compose/frontend` for the authenticated compose SPA, and `platform/toolshub/frontend` for the static tools hub. The backend persists workflow definitions as canonical nested YAML where phases contain `nodes` and `children`, hook wiring is serialized only as ordinary edges (`source`, `target`, `transform_fn`) with no persisted `port_type`, and `GET /api/schema/workflow` is the only runtime schema contract the composer consumes. SF-5 owns exactly five foundation tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`. After every workflow write (create, import, version_saved, deleted) the backend fires a `WorkflowMutationHook(workflow_id, operation)` event so downstream SF-7 can own the `workflow_entity_refs` reference-index extension without coupling SF-5 to that table. The frontend may keep a flat React Flow store internally, but every load, import, validate, save, and export boundary converts through the same iriai-compose declarative models that execution uses. This system design also fully specifies all four cross-subfeature edge data contracts per D-GR-41: (1) SF-5→SF-6: complete TypeScript type interfaces exported from `tools/compose/frontend/src/types/index.ts` and the authoritative JSON Schema field contract including `context_keys` and AskNode `task`/`context_text`; (2) SF-5→SF-7: `WorkflowMutationHookRegistry` Python interface and SQLAlchemy ORM model export contract; (3) SF-7→SF-6: reference endpoint paths, TypeScript response shapes, and delete preflight 409 contract; (4) SF-6→SF-7: exclusive hook-driven index rebuild path and component prop interfaces for usage panels.</div>

    <h3>Services</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Name</th><th>Kind</th><th>Description</th><th>Technology</th><th>Port</th><th>Journeys</th>
        </tr></thead>
        <tbody><tr>
            <td><code>SVC-59</code></td>
            <td><strong>User / Browser</strong></td>
            <td><code>external</code></td>
            <td>Developer using the tools hub and composer in a browser.</td>
            <td><code>Browser</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3, J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-60</code></td>
            <td><strong>Tools Hub Frontend</strong></td>
            <td><code>frontend</code></td>
            <td>Static React/Vite app at `platform/toolshub/frontend/`. Reads `dev_tier` from JWT, renders a hardcoded developer-tools card catalog, and same-tab navigates to `compose.iriai.app` when the Workflow Composer card is clicked. No backend of its own.</td>
            <td><code>React 18, Vite, TypeScript, @homelocal/auth</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-61</code></td>
            <td><strong>Compose Frontend</strong></td>
            <td><code>frontend</code></td>
            <td>Authenticated React/Vite SPA at `tools/compose/frontend/` with the Explorer-style sidebar (four folders: Workflows, Roles, Output Schemas, Task Templates), workflows landing page with starter template cards, and the schema-driven editor shell consumed by SF-6. Exports all TypeScript type interfaces for API responses through `tools/compose/frontend/src/types/index.ts` (the canonical barrel export that SF-6 imports exclusively from). Configures the shared Axios API client at `tools/compose/frontend/src/api/client.ts` that both SF-5 routes and SF-7 extension routes use. No plugin, tool-library, or reference-check surfaces in SF-5.</td>
            <td><code>React 18, Vite, TypeScript, React Query, Zustand, @homelocal/auth, Axios</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3, J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-62</code></td>
            <td><strong>Compose Backend</strong></td>
            <td><code>service</code></td>
            <td>FastAPI service at `tools/compose/backend/` exposing workflow CRUD, versioning, validation, starter templates, schema export, and baseline library entity CRUD. Stores raw nested YAML and never invents a second schema contract. Exports `WorkflowMutationHookRegistry` singleton at `app/state.py::mutation_hook_registry` and SQLAlchemy ORM models at `app/models.py` for SF-7 to consume. After every workflow write commits, fires `WorkflowMutationHook(workflow_id, operation)` so SF-7 can register its reference-index refresh handler. Also hosts SF-7-owned router at `app/routers/entity_refs.py` and SF-7-owned delete preflight dependencies — these routes are physically served by this process but logically owned by SF-7.</td>
            <td><code>Python 3.11, FastAPI, SQLAlchemy 2.x (async), Alembic, homelocal-auth, structlog</code></td>
            <td>8000</td>
            <td>J-1, J-2, J-3, J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-63</code></td>
            <td><strong>iriai-compose Declarative Schema Subpackage</strong></td>
            <td><code>service</code></td>
            <td>New schema subpackage at `iriai-compose/iriai_compose/schema/` to be created by SF-1 (declarative-schema subfeature). Imported by SF-5&#x27;s compose backend via `from iriai_compose.schema import WorkflowConfig`. Exposes: `WorkflowConfig`, `PhaseDefinition`, `AgentNode`, `AskNode`, `BranchNode`, `ErrorNode`, `CustomNode`, `EvalNode`, `EdgeDefinition`, `PortDefinition`, `WorkflowCostConfig`, `PhaseCostConfig`, `NodeCostConfig`, `load_workflow()`, `validate_workflow()`. Does NOT export: MapNode, FoldNode, LoopNode, TransformRef, HookRef (these are phantom exports — they do not exist). The existing `iriai_compose` package independently exports `Phase` (ABC), `Workflow`, `Role`, and runtime primitives — these are distinct from the new declarative Pydantic models. Key field contracts: AskNode has fields `task` (required string) and `context_text` (optional string) — the field name `prompt` is not a valid AskNode field. WorkflowConfig has an optional `context_keys: list[str]` field at the workflow root. BranchNode uses per-port condition expressions per D-GR-35.</td>
            <td><code>Python 3.11, Pydantic v2, PyYAML</code></td>
            <td>—</td>
            <td>J-2, J-3, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-64</code></td>
            <td><strong>Auth Service</strong></td>
            <td><code>external</code></td>
            <td>Homelocal auth service issuing JWTs and serving JWKS for the FastAPI backend and OAuth flows for both SPAs. JWT `sub` becomes `user_id`; `dev_tier` claim gates tools-hub card visibility.</td>
            <td><code>OAuth 2.0, RS256 JWT, JWKS</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3, J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-65</code></td>
            <td><strong>PostgreSQL Database</strong></td>
            <td><code>database</code></td>
            <td>Dedicated PostgreSQL instance managed by SQLAlchemy 2.x and Alembic. Migration chain tracked by `alembic_version_compose` table (isolated from all other platform services). Stores exactly five SF-5 foundation tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`. The `workflow_entity_refs` table is not created by SF-5 migrations — it is added by a separate SF-7 Alembic revision chained after the SF-5 initial revision.</td>
            <td><code>PostgreSQL, SQLAlchemy 2.x, Alembic, psycopg (asyncpg driver)</code></td>
            <td>—</td>
            <td>J-2, J-3, J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-66</code></td>
            <td><strong>Starter Template Bundle</strong></td>
            <td><code>database</code></td>
            <td>Bundled JSON/YAML assets checked into `tools/compose/backend/app/data/` containing translated starter workflows derived from the iriai-build-v2 planning/develop/bugfix reference flows.</td>
            <td><code>JSON, YAML files</code></td>
            <td>—</td>
            <td>J-3</td>
        </tr><tr>
            <td><code>SVC-67</code></td>
            <td><strong>SF-6 Workflow Editor Canvas</strong></td>
            <td><code>frontend</code></td>
            <td>Workflow canvas module owned by SF-6, co-located in `tools/compose/frontend/src/editor/`. Imports all TypeScript types exclusively from `tools/compose/frontend/src/types/index.ts` (the SF-5 barrel export): Workflow, WorkflowVersion, Role, OutputSchema, CustomTaskTemplate, StarterTemplate, PaginatedList&lt;T&gt;, ValidationIssue, ValidationResult, ImportResult, WorkflowEntityRefsResponse, EntityUsageReport, DeletePreflightConflict. Uses the shared Axios client from SF-5 (`src/api/client.ts`) to call both SF-5 workflow CRUD endpoints and SF-7 extension endpoints. Calls GET /api/schema/workflow at mount to drive the node palette. Calls GET /api/roles/{id}/usage, GET /api/schemas/{id}/usage, GET /api/templates/{id}/usage before rendering delete confirmation dialogs. Calls GET /api/workflows/{id}/entity-refs for the editor reference panel. Does NOT call any dedicated SF-7 index-rebuild endpoint — reference index rebuilds happen exclusively through SF-5 save hooks.</td>
            <td><code>React 18, React Flow, Zustand, TypeScript, Axios (shared SF-5 client)</code></td>
            <td>—</td>
            <td>J-2, J-3, J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-68</code></td>
            <td><strong>SF-7 Libraries &amp; Registries Extension</strong></td>
            <td><code>service</code></td>
            <td>Backend and frontend extension module owned by SF-7. Backend: adds router at `tools/compose/backend/app/routers/entity_refs.py` with four read-only endpoints; injects `require_no_entity_refs` FastAPI dependency into SF-5&#x27;s delete endpoints for 409 preflight; registers `refresh_entity_refs` async handler into SF-5&#x27;s `WorkflowMutationHookRegistry` singleton during FastAPI lifespan startup; owns `workflow_entity_refs` DDL via separate SF-7 Alembic revision; optionally adds `actor_slots` column to `custom_task_templates` via additive migration. Imports `WorkflowORM`, `WorkflowVersionORM`, `RoleORM`, `OutputSchemaORM`, `CustomTaskTemplateORM` from `app/models.py` and `get_db` from `app/database.py` — never redefines session management. Frontend: adds `EntityUsagePanel` and `DeleteEntityDialog` React components consumed by SF-6 canvas.</td>
            <td><code>Python 3.11, FastAPI, SQLAlchemy 2.x (async), PyYAML (yaml_content scanning), React 18, TypeScript</code></td>
            <td>—</td>
            <td>J-2, J-4</td>
        </tr></tbody>
    </table>

    <h3>Connections</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>To</th><th>Protocol</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>CONN-101</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-102</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-103</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS/OAuth</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-104</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Browser navigation</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-105</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS/OAuth</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-106</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTP/REST</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-107</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS/JWKS</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-108</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>SQL</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-109</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-110</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Filesystem read</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-111</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>TypeScript import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-112</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-113</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTP/REST</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-114</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTP/REST</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>API Endpoints</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Method</th><th>Path</th><th>Service</th><th>Description</th><th>Auth</th>
        </tr></thead>
        <tbody><tr>
            <td><code>API-105</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schema/workflow</code></td>
            <td><code></code></td>
            <td>Returns the canonical WorkflowConfig JSON Schema used by the composer at runtime. Derived from `WorkflowConfig.model_json_schema()` in `iriai_compose.schema`. The schema exposes nested `phases[].nodes` and `phases[].children` plus edge-only hook wiring (`source`, `target`, `transform_fn`) and never ships a serialized `port_type` field. Key field constraints: WorkflowConfig root includes optional `context_keys: string[]` per D-GR-39; AskNode has required `task: string` and optional `context_text: string` (not `prompt`); BranchNode uses per-port condition expressions per D-GR-35 (no `switch_function` — REJECTED per D-GR-35); ErrorNode is a 4th atomic node type per D-GR-36 with `id`, `type: error`, `message` (Jinja2 template), `inputs` (dict), no outputs, no hooks; no `stores` or `plugin_instances` at WorkflowConfig root (REJECTED per D-GR-30); no `inputs` or `outputs` at root. SF-6 must fetch this endpoint at editor mount and must not use a static bundled schema for runtime behavior.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-106</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows</code></td>
            <td><code></code></td>
            <td>Lists the caller&#x27;s workflows with cursor pagination and search.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-107</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows</code></td>
            <td><code></code></td>
            <td>Creates a workflow from nested YAML or a minimal skeleton, atomically writes WorkflowVersion v1, then fires WorkflowMutationHook(workflow_id, &#x27;created&#x27;).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-108</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Returns one workflow, including its canonical nested YAML document and current version number.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-109</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Updates workflow metadata and current YAML snapshot without creating a new version row.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-110</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Soft-deletes a workflow by setting `deleted_at`, then fires WorkflowMutationHook(workflow_id, &#x27;deleted&#x27;) so SF-7 can purge its reference-index rows.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-111</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/{id}/duplicate</code></td>
            <td><code></code></td>
            <td>Duplicates an existing workflow, seeds a fresh WorkflowVersion v1 for the copy, and fires WorkflowMutationHook(new_workflow_id, &#x27;created&#x27;).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-112</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/import</code></td>
            <td><code></code></td>
            <td>Imports a YAML file or raw YAML body, validates it against the canonical schema contract, creates WorkflowVersion v1 on success, and fires WorkflowMutationHook(workflow_id, &#x27;imported&#x27;).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-113</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/{id}/versions</code></td>
            <td><code></code></td>
            <td>Validates and stores a new immutable WorkflowVersion snapshot, updates the workflow&#x27;s current YAML, then fires WorkflowMutationHook(workflow_id, &#x27;version_saved&#x27;).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-114</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/{id}/validate</code></td>
            <td><code></code></td>
            <td>Runs server-side validation against the same iriai-compose declarative models used for schema export and import. Returns path/message error details.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-115</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/{id}/export</code></td>
            <td><code></code></td>
            <td>Downloads the stored canonical YAML document for a workflow.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-116</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates/starters</code></td>
            <td><code></code></td>
            <td>Returns the bundled starter templates used on the Workflows landing page.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-117</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles</code></td>
            <td><code></code></td>
            <td>Lists saved role definitions for the authenticated user.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-118</code></td>
            <td><code>POST</code></td>
            <td><code>/api/roles</code></td>
            <td><code></code></td>
            <td>Creates a reusable role definition. Fields align with the iriai-compose Role contract: `prompt` (not `system_prompt`), `tools`, `model`, `effort`, and `metadata`.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-119</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Returns one role definition owned by the caller.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-120</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Updates an existing role definition.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-121</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Soft-deletes a role. In SF-5 the base handler performs the soft-delete; SF-7 injects a `require_no_entity_refs` FastAPI dependency that runs first and returns HTTP 409 if references exist. SF-6 must handle both 204 (success) and 409 (reference conflict).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-122</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas</code></td>
            <td><code></code></td>
            <td>Lists saved output schemas for the authenticated user.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-123</code></td>
            <td><code>POST</code></td>
            <td><code>/api/schemas</code></td>
            <td><code></code></td>
            <td>Creates a reusable JSON Schema document for node and workflow outputs.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-124</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas/{id}</code></td>
            <td><code></code></td>
            <td>Returns one output schema owned by the caller.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-125</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/schemas/{id}</code></td>
            <td><code></code></td>
            <td>Updates an output schema definition.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-126</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/schemas/{id}</code></td>
            <td><code></code></td>
            <td>Soft-deletes an output schema. SF-7 injects `require_no_entity_refs` dependency for 409 preflight; SF-6 handles both 204 and 409.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-127</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates</code></td>
            <td><code></code></td>
            <td>Lists saved task templates for the authenticated user.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-128</code></td>
            <td><code>POST</code></td>
            <td><code>/api/templates</code></td>
            <td><code></code></td>
            <td>Creates a reusable task template whose `subgraph_yaml` follows the same canonical nested contract as full workflows.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-129</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates/{id}</code></td>
            <td><code></code></td>
            <td>Returns one task template owned by the caller.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-130</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/templates/{id}</code></td>
            <td><code></code></td>
            <td>Updates a task template definition.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-131</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/templates/{id}</code></td>
            <td><code></code></td>
            <td>Soft-deletes a task template. SF-7 injects `require_no_entity_refs` dependency for 409 preflight; SF-6 handles both 204 and 409.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-132</code></td>
            <td><code>GET</code></td>
            <td><code>/health</code></td>
            <td><code></code></td>
            <td>Liveness probe for process-level availability.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-133</code></td>
            <td><code>GET</code></td>
            <td><code>/ready</code></td>
            <td><code></code></td>
            <td>Readiness probe that confirms the PostgreSQL database is reachable and Alembic migration is current against `alembic_version_compose`.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-134</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/{id}/entity-refs</code></td>
            <td><code></code></td>
            <td>SF-7-owned route served from the compose backend (app/routers/entity_refs.py). Returns the materialized reference index for a workflow — all library entities referenced by nodes in that workflow&#x27;s current yaml_content. SF-6 calls this for the editor reference panel. Index is rebuilt by SF-7&#x27;s WorkflowMutationHook handler on every save/import/create; last_indexed_at shows when the last rebuild completed.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-135</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles/{id}/usage</code></td>
            <td><code></code></td>
            <td>SF-7-owned route served from the compose backend. Returns all workflows that reference this role. SF-6 calls this before rendering the DeleteEntityDialog for a role — if total_references &gt; 0, the dialog shows a blocking warning with workflow links. Reads from the `workflow_entity_refs` materialized index.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-136</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas/{id}/usage</code></td>
            <td><code></code></td>
            <td>SF-7-owned route served from the compose backend. Returns all workflows that reference this output schema. SF-6 calls this before rendering the DeleteEntityDialog for a schema.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-137</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates/{id}/usage</code></td>
            <td><code></code></td>
            <td>SF-7-owned route served from the compose backend. Returns all workflows that reference this custom task template. SF-6 calls this before rendering the DeleteEntityDialog for a template.</td>
            <td><code>JWT Bearer</code></td>
        </tr></tbody>
    </table>

    <h3>Call Paths</h3>
    <div class="call-path-block">
            <h4><code>CP-25</code>: First-time user authenticates in the tools hub and navigates into the composer without introducing a second launcher contract.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-2&#x27;, &#x27;action&#x27;: &#x27;Open tools.iriai.app&#x27;, &#x27;description&#x27;: &#x27;The browser loads the tools hub from `platform/toolshub/frontend/` with a developer-tools card catalog.&#x27;, &#x27;returns&#x27;: &#x27;Tools hub shell&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-2&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-6&#x27;, &#x27;action&#x27;: &#x27;Start OAuth flow&#x27;, &#x27;description&#x27;: &#x27;The tools hub redirects to auth-service and receives a JWT after login; `dev_tier` claim is extracted for card gating.&#x27;, &#x27;returns&#x27;: &#x27;Access token&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-2&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;Same-tab navigation to compose.iriai.app&#x27;, &#x27;description&#x27;: &#x27;Clicking the Workflow Composer card routes the browser to the compose frontend in the same tab.&#x27;, &#x27;returns&#x27;: &#x27;Composer URL loaded&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-6&#x27;, &#x27;action&#x27;: &#x27;Ensure authenticated compose session&#x27;, &#x27;description&#x27;: &#x27;The compose frontend completes its own OAuth callback or validates the existing token for the compose domain.&#x27;, &#x27;returns&#x27;: &#x27;Authenticated compose session&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-1&#x27;, &#x27;action&#x27;: &#x27;Render Workflows landing page&#x27;, &#x27;description&#x27;: &#x27;The authenticated browser sees the workflows list shell with the four SF-5 Explorer folders (Workflows, Roles, Output Schemas, Task Templates) and starter template cards.&#x27;, &#x27;returns&#x27;: &#x27;Composer home page&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-26</code>: The frontend loads both the workflow record and the authoritative composer schema before handing control to the canvas editor.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;Open /workflows/{id}/edit&#x27;, &#x27;description&#x27;: &#x27;The browser enters the editor route after creating or selecting a workflow.&#x27;, &#x27;returns&#x27;: &#x27;Editor shell loading state&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;GET /api/schema/workflow&#x27;, &#x27;description&#x27;: &#x27;The frontend fetches the only runtime schema contract it is allowed to use.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig JSON Schema&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-5&#x27;, &#x27;action&#x27;: &#x27;WorkflowConfig.model_json_schema()&#x27;, &#x27;description&#x27;: &#x27;The backend derives the schema directly from iriai-compose declarative models and caches the result.&#x27;, &#x27;returns&#x27;: &#x27;Canonical schema dict&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;GET /api/workflows/{id}&#x27;, &#x27;description&#x27;: &#x27;The frontend fetches the stored YAML snapshot for the workflow.&#x27;, &#x27;returns&#x27;: &#x27;Workflow record&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;SELECT workflow row&#x27;, &#x27;description&#x27;: &#x27;The backend reads the workflow record and current YAML snapshot from PostgreSQL.&#x27;, &#x27;returns&#x27;: &#x27;Workflow row&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;Flatten nested YAML to editor state&#x27;, &#x27;description&#x27;: &#x27;The frontend converts canonical `phases[].nodes` and `phases[].children` into its flat React Flow store without mutating the stored contract.&#x27;, &#x27;returns&#x27;: &#x27;Canvas-ready node and edge arrays&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-27</code>: Creating a workflow from scratch inserts the workflow row and immutable version row in one transaction, then fires the mutation hook so SF-7 can refresh its reference index.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows&#x27;, &#x27;description&#x27;: &#x27;The frontend submits the workflow name and either a user-authored nested YAML snapshot or a minimal skeleton.&#x27;, &#x27;returns&#x27;: &#x27;Create request&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-6&#x27;, &#x27;action&#x27;: &#x27;Validate JWT&#x27;, &#x27;description&#x27;: &#x27;The backend verifies the bearer token and extracts the user ID from `sub`.&#x27;, &#x27;returns&#x27;: &#x27;Authenticated user&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-5&#x27;, &#x27;action&#x27;: &#x27;Parse and validate YAML contract&#x27;, &#x27;description&#x27;: &#x27;The backend validates the document against the same nested schema returned by `/api/schema/workflow`.&#x27;, &#x27;returns&#x27;: &#x27;Validated WorkflowConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;INSERT Workflow + WorkflowVersion v1&#x27;, &#x27;description&#x27;: &#x27;The backend writes the workflow record and immutable version 1 snapshot in a single transaction. No `workflow_entity_refs` rows are written by SF-5.&#x27;, &#x27;returns&#x27;: &#x27;Persisted workflow ID&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &quot;Fire WorkflowMutationHook(workflow_id, &#x27;created&#x27;)&quot;, &#x27;description&#x27;: &#x27;After the transaction commits, the backend fires the mutation hook. SF-7 registers its reference-index refresh handler here at application startup; SF-5 catches and logs any handler exceptions without rolling back.&#x27;, &#x27;returns&#x27;: &#x27;Hook handlers invoked&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;201 Created&#x27;, &#x27;description&#x27;: &#x27;The backend returns the created workflow with `current_version = 1`.&#x27;, &#x27;returns&#x27;: &#x27;Workflow&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-28</code>: The landing page fetches starter templates from the bundled asset set and creates a user-owned workflow copy on demand.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;GET /api/templates/starters&#x27;, &#x27;description&#x27;: &#x27;The frontend loads the built-in starter cards for the landing page.&#x27;, &#x27;returns&#x27;: &#x27;StarterTemplate[]&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-8&#x27;, &#x27;action&#x27;: &#x27;Read starter bundle&#x27;, &#x27;description&#x27;: &#x27;The backend reads the translated starter templates from checked-in assets at `tools/compose/backend/app/data/`.&#x27;, &#x27;returns&#x27;: &#x27;Starter template payloads&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows&#x27;, &#x27;description&#x27;: &quot;When the user clicks Use Template, the frontend creates a new workflow using the selected starter template&#x27;s canonical YAML.&quot;, &#x27;returns&#x27;: &#x27;Create request&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-5&#x27;, &#x27;action&#x27;: &#x27;Validate starter YAML&#x27;, &#x27;description&#x27;: &#x27;The backend validates the starter payload before persistence so the starter bundle cannot drift from the runtime contract.&#x27;, &#x27;returns&#x27;: &#x27;Validated WorkflowConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;Insert workflow copy and version 1&#x27;, &#x27;description&#x27;: &#x27;The backend persists the user-owned workflow and its first immutable version row. WorkflowMutationHook fires post-commit.&#x27;, &#x27;returns&#x27;: &#x27;Workflow copy&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-29</code>: Import uses the canonical nested contract and cleanly separates malformed YAML failures from schema-level warnings.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;Choose YAML file&#x27;, &#x27;description&#x27;: &#x27;The user selects a `.yaml` or `.yml` file from the import button.&#x27;, &#x27;returns&#x27;: &#x27;File payload&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows/import&#x27;, &#x27;description&#x27;: &#x27;The frontend uploads the file or raw YAML body.&#x27;, &#x27;returns&#x27;: &#x27;Import request&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-5&#x27;, &#x27;action&#x27;: &#x27;safe_load + model validation&#x27;, &#x27;description&#x27;: &#x27;The backend first parses YAML, then validates the nested contract and edge-only hook wiring against iriai-compose models.&#x27;, &#x27;returns&#x27;: &#x27;Validated WorkflowConfig or parse/validation errors&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;INSERT Workflow + WorkflowVersion v1 on success&#x27;, &#x27;description&#x27;: &#x27;Successful imports persist the workflow snapshot and version 1 in one transaction. No `workflow_entity_refs` rows are written by SF-5.&#x27;, &#x27;returns&#x27;: &#x27;Persisted workflow&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &quot;Fire WorkflowMutationHook(workflow_id, &#x27;imported&#x27;) on success&quot;, &#x27;description&#x27;: &#x27;After transaction commit, the backend fires the mutation hook for SF-7 handler invocation. Skipped entirely on validation failure.&#x27;, &#x27;returns&#x27;: &#x27;Hook handlers invoked&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;Return 201 or 400&#x27;, &#x27;description&#x27;: &#x27;Malformed YAML returns 400 with parser details; schema-valid imports return 201 and may include warning rows without blocking persistence.&#x27;, &#x27;returns&#x27;: &#x27;ImportResult or import error payload&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-30</code>: The editor serializes its flat state back to canonical nested YAML, validates it, stores an immutable version snapshot, and fires the mutation hook.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;Serialize flat canvas state&#x27;, &#x27;description&#x27;: &#x27;The frontend groups nodes by phase and emits canonical `phases[].nodes`, `phases[].children`, and `edges[]` with no persisted `port_type`.&#x27;, &#x27;returns&#x27;: &#x27;yaml_content&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows/{id}/validate&#x27;, &#x27;description&#x27;: &#x27;The frontend asks the backend to validate the serialized YAML before committing a version snapshot.&#x27;, &#x27;returns&#x27;: &#x27;ValidationResult: { valid, errors[] }&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-5&#x27;, &#x27;action&#x27;: &#x27;Validate via declarative models&#x27;, &#x27;description&#x27;: &#x27;The backend uses the same iriai-compose models and helpers that drive `/api/schema/workflow` and import.&#x27;, &#x27;returns&#x27;: &#x27;Validation result&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows/{id}/versions&#x27;, &#x27;description&#x27;: &#x27;After a valid response, the frontend submits the same YAML snapshot as a new immutable version.&#x27;, &#x27;returns&#x27;: &#x27;Version create request&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;INSERT WorkflowVersion and update workflow current snapshot&#x27;, &#x27;description&#x27;: &quot;The backend appends a version row and updates the workflow&#x27;s `yaml_content` and `current_version` in one transaction. No `workflow_entity_refs` rows are written by SF-5.&quot;, &#x27;returns&#x27;: &#x27;WorkflowVersion&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &quot;Fire WorkflowMutationHook(workflow_id, &#x27;version_saved&#x27;)&quot;, &#x27;description&#x27;: &quot;After the transaction commits, the backend fires the mutation hook. SF-7&#x27;s registered handler rebuilds the reference index for this workflow_id.&quot;, &#x27;returns&#x27;: &#x27;Hook handlers invoked&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-31</code>: Documents the complete SF-5→SF-6 edge: SF-5&#x27;s TypeScript barrel export provides all compile-time types; the runtime schema endpoint provides the authoritative JSON Schema with context_keys and correct AskNode fields.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Compile-time TypeScript barrel export&#x27;, &#x27;description&#x27;: &#x27;SF-5 exports from `tools/compose/frontend/src/types/index.ts`: Workflow, WorkflowVersion, Role, OutputSchema, CustomTaskTemplate, StarterTemplate, PaginatedList&lt;T&gt;, ValidationIssue, ValidationResult, ImportResult, WorkflowEntityRefsResponse, EntityUsageReport, DeletePreflightConflict. SF-6 imports exclusively from this path — no imports from deeper module paths.&#x27;, &#x27;returns&#x27;: &#x27;TypeScript interface types bound at compile time&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;GET /api/schema/workflow (editor mount)&#x27;, &#x27;description&#x27;: &#x27;SF-6 canvas fetches the authoritative WorkflowConfig JSON Schema using the shared Axios client from `src/api/client.ts`. This is the only schema source SF-6 may use — a bundled static schema is not permitted for runtime node palette construction.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig JSON Schema document&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-5&#x27;, &#x27;action&#x27;: &#x27;WorkflowConfig.model_json_schema() from iriai_compose.schema&#x27;, &#x27;description&#x27;: &#x27;Backend derives schema from the `iriai_compose.schema` subpackage (SF-1). Schema includes: optional `context_keys: string[]` at WorkflowConfig root; AskNode with required `task: string` and optional `context_text: string` (not `prompt`); BranchNode with per-port condition expressions (no `switch_function` — REJECTED per D-GR-35); ErrorNode as 4th atomic node type per D-GR-36; no `stores` or `plugin_instances` at WorkflowConfig root (REJECTED per D-GR-30); no `inputs` or `outputs` at root.&#x27;, &#x27;returns&#x27;: &#x27;Schema dict cached in application state&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Return WorkflowConfig JSON Schema&#x27;, &#x27;description&#x27;: &#x27;Backend returns the full JSON Schema. SF-6 must validate that the schema contains context_keys and AskNode.task before marking the editor as ready.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig JSON Schema (200)&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Map schema to node palette&#x27;, &#x27;description&#x27;: &#x27;SF-6 canvas maps the JSON Schema discriminated union to the editor node palette: AgentNode, AskNode (task+context_text fields), BranchNode (per-port conditions), ErrorNode (message Jinja2 template + inputs, no outputs/hooks — per D-GR-36), CustomNode, EvalNode. Caches schema; subscribes to version stamp for invalidation on backend redeploy.&#x27;, &#x27;returns&#x27;: &#x27;Node palette ready; editor unlocked for workflow load&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-32</code>: Documents the complete SF-5→SF-7 edge: SF-7 registers its reference-index handler into SF-5&#x27;s WorkflowMutationHookRegistry at app startup, then the chain fires on every workflow write to rebuild workflow_entity_refs.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-10&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;Register refresh_entity_refs handler at FastAPI lifespan startup&#x27;, &#x27;description&#x27;: &#x27;SF-7 calls `mutation_hook_registry.register(refresh_entity_refs)` during the FastAPI @asynccontextmanager lifespan startup block. The handler signature is `async def refresh_entity_refs(workflow_id: str, operation: WorkflowMutationOperation) -&gt; None`. SF-7 imports `mutation_hook_registry` from `app/state.py` and ORM models from `app/models.py`.&#x27;, &#x27;returns&#x27;: &#x27;Handler stored in registry&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows/{id}/versions { yaml_content, change_description }&#x27;, &#x27;description&#x27;: &#x27;SF-6 canvas submits a validated YAML snapshot as a new immutable version.&#x27;, &#x27;returns&#x27;: &#x27;Version create request&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;INSERT workflow_versions; UPDATE workflows.yaml_content in one transaction&#x27;, &#x27;description&#x27;: &#x27;SF-5 appends the version row and updates the workflow snapshot atomically.&#x27;, &#x27;returns&#x27;: &#x27;Committed transaction&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-10&#x27;, &#x27;action&#x27;: &quot;mutation_hook_registry.fire(workflow_id, &#x27;version_saved&#x27;) post-commit&quot;, &#x27;description&#x27;: &#x27;SF-5 invokes all registered handlers after the transaction commits. Handler exceptions are caught per-handler, logged via structlog, and never propagated to the HTTP response.&#x27;, &#x27;returns&#x27;: &#x27;Handler invocations dispatched (exceptions swallowed)&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-10&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;Parse yaml_content; DELETE prior refs; bulk INSERT workflow_entity_refs rows&#x27;, &#x27;description&#x27;: &#x27;SF-7 handler loads yaml_content via PyYAML, walks all PhaseDefinition.nodes extracting role_id, output_schema_id, and template_id references, deletes all existing workflow_entity_refs rows for this workflow_id, then inserts fresh rows. Operation is idempotent — safe to re-invoke.&#x27;, &#x27;returns&#x27;: &#x27;Reference index rebuilt; workflow_entity_refs current&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-33</code>: Documents the SF-7→SF-6 edge: SF-7 extension endpoints consumed by SF-6 for usage display and blocking delete preflights. Covers both the pre-dialog usage fetch and the 409 guard on the delete endpoint itself.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Click Delete Role (or schema / template)&#x27;, &#x27;description&#x27;: &#x27;User initiates delete from the library entity list. SF-6 intercepts the action before calling DELETE to fetch usage count.&#x27;, &#x27;returns&#x27;: &#x27;Delete intent&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;GET /api/roles/{id}/usage&#x27;, &#x27;description&#x27;: &#x27;SF-6 calls the SF-7-owned usage endpoint via the shared Axios client. No separate HTTP client is configured for SF-7 routes.&#x27;, &#x27;returns&#x27;: &#x27;GET usage request&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &quot;SELECT workflow_entity_refs WHERE entity_type=&#x27;role&#x27; AND entity_id=?&quot;, &#x27;description&#x27;: &#x27;SF-7 router handler queries the materialized reference index.&#x27;, &#x27;returns&#x27;: &#x27;Ref rows&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Return EntityUsageReport&#x27;, &#x27;description&#x27;: &quot;Backend returns: `{ entity_id, entity_type: &#x27;role&#x27;, referenced_by: [{ workflow_id, workflow_name, node_ids[] }], total_references: number }`.&quot;, &#x27;returns&#x27;: &#x27;EntityUsageReport (200)&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Render DeleteEntityDialog with usage&#x27;, &#x27;description&#x27;: &#x27;SF-6 renders DeleteEntityDialog (SF-7 component). If total_references &gt; 0, the dialog shows a blocking warning listing referenced workflows with links. The confirm button is disabled when blocking references exist — this is a blocking error state, not a dismissible warning.&#x27;, &#x27;returns&#x27;: &#x27;Dialog displayed; user decides&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;DELETE /api/roles/{id} (if user confirms with zero references)&#x27;, &#x27;description&#x27;: &quot;SF-6 calls the delete endpoint. SF-7&#x27;s `require_no_entity_refs` dependency runs first as a second line of enforcement — returns 409 if refs exist at delete time (race-condition guard). SF-6 handles 409 by re-rendering the blocking dialog.&quot;, &#x27;returns&#x27;: &#x27;204 No Content; or 409 DeletePreflightConflict: { detail: string, blocking_workflows: [{ id, name }] }&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-34</code>: Documents the SF-6→SF-7 edge for the editor reference panel: SF-6 fetches the workflow entity-refs index to display what library entities are used by the current workflow&#x27;s nodes. Index is already current because the hook chain (CP-SF5-8) ran on the last save.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;GET /api/workflows/{id}/entity-refs&#x27;, &#x27;description&#x27;: &#x27;SF-6 canvas calls the SF-7-owned entity-refs endpoint when the editor reference panel is opened. Uses the shared Axios client — no separate client for SF-7 routes.&#x27;, &#x27;returns&#x27;: &#x27;GET entity-refs request&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;SELECT workflow_entity_refs WHERE workflow_id=?&#x27;, &#x27;description&#x27;: &#x27;SF-7 router handler reads the materialized index for this workflow.&#x27;, &#x27;returns&#x27;: &#x27;Ref rows with entity_type, entity_id, node_id, node_type, context&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Return WorkflowEntityRefsResponse&#x27;, &#x27;description&#x27;: &#x27;Backend returns `{ workflow_id, refs: [{ entity_type, entity_id, node_id, node_type, context }], last_indexed_at }`.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowEntityRefsResponse (200)&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Render reference panel grouped by entity_type&#x27;, &#x27;description&#x27;: &#x27;SF-6 displays the refs grouped by entity_type (Roles, Output Schemas, Task Templates). Each entry links to the library entity detail. `last_indexed_at` is shown to indicate when the index was last rebuilt. If the workflow has unsaved changes, SF-6 shows a stale-index notice prompting the user to save first.&#x27;, &#x27;returns&#x27;: &#x27;Reference panel rendered&#x27;}</li></ol>
        </div>

    <h3>Entities</h3>
    <div class="entity-block">
            <h4><code>ENT-90</code>: Workflow</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>Stable workflow identifier.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>User-visible workflow name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>string | null</code></td>
                        <td>Optional summary shown in list views.</td>
                    </tr><tr>
                        <td><code>yaml_content</code></td>
                        <td><code>text</code></td>
                        <td>Canonical nested YAML document. Phases persist their own `nodes` and nested `children`; hook links remain normal edges with `source`, `target`, and optional `transform_fn` only. Optional workflow-level `context_keys` field included when present.</td>
                    </tr><tr>
                        <td><code>current_version</code></td>
                        <td><code>integer</code></td>
                        <td>Latest immutable version number; mirrors the latest `workflow_versions.version_number`.</td>
                    </tr><tr>
                        <td><code>is_valid</code></td>
                        <td><code>boolean</code></td>
                        <td>Current validation status from server-side schema checks.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>string</code></td>
                        <td>JWT `sub` for ownership scoping.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>datetime</code></td>
                        <td>Creation timestamp.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Most recent metadata or YAML update.</td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Soft-delete marker.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-91</code>: WorkflowVersion</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>Stable version identifier.</td>
                    </tr><tr>
                        <td><code>workflow_id</code></td>
                        <td><code>UUID</code></td>
                        <td>Parent workflow.</td>
                    </tr><tr>
                        <td><code>version_number</code></td>
                        <td><code>integer</code></td>
                        <td>Monotonic version number.</td>
                    </tr><tr>
                        <td><code>yaml_content</code></td>
                        <td><code>text</code></td>
                        <td>Exact nested YAML snapshot saved at that point in time.</td>
                    </tr><tr>
                        <td><code>change_description</code></td>
                        <td><code>string | null</code></td>
                        <td>Optional human change note.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>string</code></td>
                        <td>JWT `sub` of the user who created the snapshot.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>datetime</code></td>
                        <td>Snapshot timestamp.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-92</code>: Role</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>Stable role identifier.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Display name used by the role builder and pickers.</td>
                    </tr><tr>
                        <td><code>prompt</code></td>
                        <td><code>text</code></td>
                        <td>Role prompt content. Field name is `prompt`, not `system_prompt` — matches the iriai-compose Role contract.</td>
                    </tr><tr>
                        <td><code>model</code></td>
                        <td><code>string | null</code></td>
                        <td>Optional model override (e.g. `claude-sonnet-4-6`).</td>
                    </tr><tr>
                        <td><code>effort</code></td>
                        <td><code>string | null</code></td>
                        <td>Optional effort level hint passed to the AgentRuntime. Matches the iriai-compose Role `effort` field.</td>
                    </tr><tr>
                        <td><code>tools</code></td>
                        <td><code>json</code></td>
                        <td>Allowed tool identifiers as a JSON list of strings.</td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>json</code></td>
                        <td>Extensible role metadata as a JSON object.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>string</code></td>
                        <td>Owner JWT `sub`.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>datetime</code></td>
                        <td>Creation timestamp.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Most recent update.</td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Soft-delete marker.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-93</code>: OutputSchema</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>Stable schema identifier.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Display name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>string | null</code></td>
                        <td>Optional schema summary.</td>
                    </tr><tr>
                        <td><code>json_schema</code></td>
                        <td><code>json</code></td>
                        <td>Reusable JSON Schema document for node and workflow output contracts.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>string</code></td>
                        <td>Owner JWT `sub`.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>datetime</code></td>
                        <td>Creation timestamp.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Most recent update.</td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Soft-delete marker.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-94</code>: CustomTaskTemplate</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>Stable task template identifier.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Display name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>string | null</code></td>
                        <td>Optional summary.</td>
                    </tr><tr>
                        <td><code>subgraph_yaml</code></td>
                        <td><code>text</code></td>
                        <td>Canonical nested YAML subgraph using the same `nodes`, `children`, and edge-only hook contract as full workflows.</td>
                    </tr><tr>
                        <td><code>input_interface</code></td>
                        <td><code>json</code></td>
                        <td>Declared template input contract.</td>
                    </tr><tr>
                        <td><code>output_interface</code></td>
                        <td><code>json</code></td>
                        <td>Declared template output contract.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>string</code></td>
                        <td>Owner JWT `sub`.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>datetime</code></td>
                        <td>Creation timestamp.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Most recent update.</td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Soft-delete marker. SF-7 may extend this table with an `actor_slots` column via additive Alembic migration.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-95</code>: StarterTemplate</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>string</code></td>
                        <td>Stable starter identifier.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Starter display name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>string</code></td>
                        <td>Landing-page summary.</td>
                    </tr><tr>
                        <td><code>yaml_content</code></td>
                        <td><code>text</code></td>
                        <td>Canonical nested YAML starter payload derived from iriai-build-v2 reference workflows.</td>
                    </tr><tr>
                        <td><code>category</code></td>
                        <td><code>string</code></td>
                        <td>Landing-page grouping tag.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-96</code>: ErrorNode (D-GR-36 — 4th atomic node type)</h4>
            <p>ErrorNode is one of four atomic node types (Ask, Branch, Plugin, Error) per D-GR-36. It represents a terminal error state: when reached during execution, the workflow halts with the rendered message. ErrorNode has no outputs and no hooks — it is a dead-end by design. The DB node type enum must include <code>error</code> alongside <code>ask</code>, <code>branch</code>, and <code>plugin</code>. API validation must accept <code>type: error</code> in workflow YAML node definitions. The schema endpoint (<code>GET /api/schema/workflow</code>) must expose ErrorNode in the discriminated union so SF-6 can render it in the node palette.</p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>string</code></td>
                        <td>Unique node identifier within the workflow.</td>
                    </tr><tr>
                        <td><code>type</code></td>
                        <td><code>"error"</code></td>
                        <td>Discriminator value. Always <code>error</code>.</td>
                    </tr><tr>
                        <td><code>message</code></td>
                        <td><code>string (Jinja2 template)</code></td>
                        <td>Error message template rendered at execution time using Jinja2. May reference variables from the node's <code>inputs</code> dict.</td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict</code></td>
                        <td>Input data dictionary providing variables for the Jinja2 message template. Populated from incoming edges.</td>
                    </tr></tbody>
            </table>
            <p><strong>Constraints:</strong> NO <code>outputs</code> field — ErrorNode is a terminal dead-end. NO <code>hooks</code> (no <code>on_start</code>/<code>on_end</code>) — the node halts execution immediately. Outgoing edges from an ErrorNode are a validation error.</p>
        </div>

    <h3>Entity Relations</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>Relation</th><th>To</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>ER-80</code></td>
            <td><code>ENT-SF5-1</code></td>
            <td></td>
            <td><code>ENT-SF5-2</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>Decisions</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Decision</th></tr></thead>
        <tbody><tr>
            <td><code>D-77</code></td>
            <td>D-GR-22: `GET /api/schema/workflow` is the canonical composer schema source. Bundled `workflow-schema.json` is build/test-only and must never drive runtime editor behavior.</td>
        </tr><tr>
            <td><code>D-78</code></td>
            <td>D-SF5-1: Workflow persistence is canonical nested YAML. Phases store local `nodes` plus nested `children`; the frontend may flatten to React Flow state internally but never writes that shape to storage or over the API.</td>
        </tr><tr>
            <td><code>D-79</code></td>
            <td>D-SF5-2: Hook wiring is serialized only through `edges[]` rows using `source`, `target`, and optional `transform_fn`. No separate serialized hooks section and no persisted `port_type` field exist in backend contracts.</td>
        </tr><tr>
            <td><code>D-80</code></td>
            <td>D-SF5-3: The accepted repo topology is `tools/compose/backend` for the FastAPI service, `tools/compose/frontend` for the compose SPA, and `platform/toolshub/frontend` for the static tools hub. The `tools/iriai-workflows` path is not part of the approved implementation contract and must not appear in SF-5 artifacts.</td>
        </tr><tr>
            <td><code>D-81</code></td>
            <td>D-SF5-4: The backend derives schema export and YAML validation from the same iriai-compose declarative models (`iriai_compose.schema`, created by SF-1) to avoid contract drift between authoring and execution.</td>
        </tr><tr>
            <td><code>D-82</code></td>
            <td>D-SF5-5: PostgreSQL is the foundation database for SF-5, managed through SQLAlchemy 2.x (async) and Alembic. The migration chain is isolated to the `alembic_version_compose` version table and must not share a version table with deploy-console or any other platform service. SQLite is out of scope.</td>
        </tr><tr>
            <td><code>D-83</code></td>
            <td>D-SF5-6: WorkflowVersion is append-only and is created on workflow create, starter-template use, import, and duplicate (v1) and on explicit save-version (vN). Every workflow mutation has an auditable snapshot. Version-history UI is deferred to a later subfeature.</td>
        </tr><tr>
            <td><code>D-84</code></td>
            <td>D-SF5-7: SF-5 exposes a `WorkflowMutationHook(workflow_id: str, operation: Literal[&#x27;created&#x27;,&#x27;imported&#x27;,&#x27;version_saved&#x27;,&#x27;deleted&#x27;])` callable registry. After each workflow write transaction commits, SF-5 fires all registered handlers. SF-7 registers its reference-index refresh handler at application startup. SF-5 must never directly write or read `workflow_entity_refs` rows.</td>
        </tr><tr>
            <td><code>D-85</code></td>
            <td>D-SF5-8: Plugins remain runtime and YAML concerns owned by iriai-compose and consuming projects; SF-5 does not create plugin tables, plugin-management surfaces, or `/api/plugins` endpoints.</td>
        </tr><tr>
            <td><code>D-86</code></td>
            <td>D-SF5-9: SF-5 owns exactly five foundation tables (`workflows`, `workflow_versions`, `roles`, `output_schemas`, `custom_task_templates`). `workflow_entity_refs` is a SF-7-owned extension table. SF-5 migrations must not reference or create it. The five-table boundary is a hard constraint enforced at the Alembic level.</td>
        </tr><tr>
            <td><code>D-87</code></td>
            <td>D-EDGE-1 (SF-5→SF-6 TypeScript type contract): All SF-5→SF-6 type boundaries flow through a single barrel export at `tools/compose/frontend/src/types/index.ts`. SF-6 imports exclusively from this path — no direct imports from deeper module paths. Complete interface set: `interface Workflow { id: string; name: string; description: string | null; yaml_content: string; current_version: number; is_valid: boolean; user_id: string; created_at: string; updated_at: string | null; deleted_at: string | null }` — `interface WorkflowVersion { id: string; workflow_id: string; version_number: number; yaml_content: string; change_description: string | null; user_id: string; created_at: string }` — `interface Role { id: string; name: string; prompt: string; model: string | null; effort: string | null; tools: string[]; metadata: Record&lt;string,unknown&gt;; user_id: string; created_at: string; updated_at: string | null; deleted_at: string | null }` — `interface OutputSchema { id: string; name: string; description: string | null; json_schema: Record&lt;string,unknown&gt;; user_id: string; created_at: string; updated_at: string | null; deleted_at: string | null }` — `interface CustomTaskTemplate { id: string; name: string; description: string | null; subgraph_yaml: string; input_interface: Record&lt;string,unknown&gt;; output_interface: Record&lt;string,unknown&gt;; user_id: string; created_at: string; updated_at: string | null; deleted_at: string | null }` — `interface StarterTemplate { id: string; name: string; description: string; yaml_content: string; category: &#x27;starter&#x27; }` — `interface PaginatedList&lt;T&gt; { items: T[]; next_cursor: string | null; has_more: boolean }` — `interface ValidationIssue { path: string; message: string; severity: &#x27;error&#x27; | &#x27;warning&#x27; }` — `interface ValidationResult { valid: boolean; errors: ValidationIssue[] }` — `interface ImportResult { workflow: Workflow; validation_warnings?: ValidationIssue[] }`</td>
        </tr><tr>
            <td><code>D-88</code></td>
            <td>D-EDGE-2 (SF-5→SF-6 schema field contract): `GET /api/schema/workflow` derives from `WorkflowConfig.model_json_schema()` in `iriai_compose.schema` (SF-1&#x27;s new subpackage — not `iriai_compose.declarative` which does not exist). Authoritative field constraints for SF-6 node palette construction: (1) WorkflowConfig root includes optional `context_keys: string[]` per D-GR-39/SF-1→SF-4 contract — SF-6 must expose a workflow-level context_keys editor; (2) AskNode has required field `task: string` and optional field `context_text: string` — the field name `prompt` is not valid for AskNode and must not appear in SF-6 AskNode form fields; (3) BranchNode uses per-port conditions on BranchOutputPort per D-GR-35 — `switch_function` is REJECTED (D-GR-35), `output_field` is REJECTED; (4) ErrorNode is a 4th atomic node type per D-GR-36: `id`, `type: error`, `message` (Jinja2 template), `inputs` (dict), NO outputs, NO hooks; (5) WorkflowConfig root has no `stores` or `plugin_instances` fields (REJECTED per D-GR-30), no `inputs` or `outputs` fields; (6) existing iriai-compose exports `Phase` (ABC) and `Role` (dataclass) — these are runtime primitives distinct from the declarative `PhaseDefinition` and `WorkflowConfig` Pydantic models that SF-5 imports; (7) phantom exports MapNode, FoldNode, LoopNode, TransformRef, HookRef do not exist and must not appear in any SF-5 or SF-6 import statement.</td>
        </tr><tr>
            <td><code>D-89</code></td>
            <td>D-EDGE-3 (SF-5→SF-7 Python hook interface): SF-5 creates and exports a `WorkflowMutationHookRegistry` singleton at `tools/compose/backend/app/state.py::mutation_hook_registry`. Complete Python contract: `WorkflowMutationOperation = Literal[&#x27;created&#x27;, &#x27;imported&#x27;, &#x27;version_saved&#x27;, &#x27;deleted&#x27;]`; `MutationHandler = Callable[[str, WorkflowMutationOperation], Awaitable[None]]`; `class WorkflowMutationHookRegistry: def register(self, handler: MutationHandler) -&gt; None: ...; async def fire(self, workflow_id: str, operation: WorkflowMutationOperation) -&gt; None: ...`. `fire()` catches per-handler exceptions, logs via structlog, never propagates. `fire()` is called only after the primary workflow transaction has committed. SF-7 calls `mutation_hook_registry.register(refresh_entity_refs)` in a FastAPI `@asynccontextmanager` lifespan function. Handler signature: `async def refresh_entity_refs(workflow_id: str, operation: WorkflowMutationOperation) -&gt; None`. On `&#x27;deleted&#x27;` the handler purges all `workflow_entity_refs` rows for `workflow_id`. On `&#x27;created&#x27;`, `&#x27;imported&#x27;`, `&#x27;version_saved&#x27;` it scans `yaml_content` and upserts rows. Operation is idempotent.</td>
        </tr><tr>
            <td><code>D-90</code></td>
            <td>D-EDGE-4 (SF-5→SF-7 ORM model contract): SF-5 exposes the following SQLAlchemy 2.x async ORM models from `tools/compose/backend/app/models.py`: `WorkflowORM` (table: `workflows`), `WorkflowVersionORM` (table: `workflow_versions`), `RoleORM` (table: `roles`), `OutputSchemaORM` (table: `output_schemas`), `CustomTaskTemplateORM` (table: `custom_task_templates`). SF-7 imports these models and the `get_db` AsyncSession factory from `tools/compose/backend/app/database.py`. SF-7 must not redefine session management, re-declare table DDL, or modify the five foundation tables in its own migrations. SF-7&#x27;s only DDL rights are: (1) CREATE TABLE `workflow_entity_refs`, (2) ALTER TABLE `custom_task_templates` ADD COLUMN `actor_slots` — both via separate SF-7 Alembic revision files chained after the SF-5 initial revision in the `alembic_version_compose` chain.</td>
        </tr><tr>
            <td><code>D-91</code></td>
            <td>D-EDGE-5 (SF-7→SF-6 reference endpoint contract): SF-7 adds four read-only endpoints to the compose backend router at `app/routers/entity_refs.py`. All four require JWT Bearer auth and are called by SF-6 via the shared Axios client at `tools/compose/frontend/src/api/client.ts` — no separate HTTP client setup. TypeScript response shapes exported from `tools/compose/frontend/src/types/index.ts`: `interface WorkflowEntityRefsResponse { workflow_id: string; refs: Array&lt;{ entity_type: &#x27;role&#x27; | &#x27;output_schema&#x27; | &#x27;custom_task_template&#x27;; entity_id: string; node_id: string; node_type: string; context: string }&gt;; last_indexed_at: string }` — `interface EntityUsageReport { entity_id: string; entity_type: &#x27;role&#x27; | &#x27;output_schema&#x27; | &#x27;custom_task_template&#x27;; referenced_by: Array&lt;{ workflow_id: string; workflow_name: string; node_ids: string[] }&gt;; total_references: number }`. Endpoint paths: `GET /api/workflows/{id}/entity-refs → WorkflowEntityRefsResponse`; `GET /api/roles/{id}/usage → EntityUsageReport`; `GET /api/schemas/{id}/usage → EntityUsageReport`; `GET /api/templates/{id}/usage → EntityUsageReport`.</td>
        </tr><tr>
            <td><code>D-92</code></td>
            <td>D-EDGE-6 (SF-7→SF-6 delete preflight guard contract): SF-7 injects a FastAPI dependency `require_no_entity_refs(entity_id, entity_type)` into SF-5&#x27;s `DELETE /api/roles/{id}`, `DELETE /api/schemas/{id}`, `DELETE /api/templates/{id}` handlers. On reference conflict (`total_references &gt;0`), returns HTTP 409 with: `interface DeletePreflightConflict { detail: string; blocking_workflows: Array&lt;{ id: string; name: string }&gt; }`. The `detail` string format is: `&quot;&lt;EntityType&gt; &#x27;&lt;name&gt;&#x27; is referenced by &lt;N&gt; workflow(s). Remove all references before deleting.&quot;` SF-6 component `DeleteEntityDialog` prop interface: `interface DeleteEntityDialogProps { entityId: string; entityType: &#x27;role&#x27; | &#x27;output_schema&#x27; | &#x27;custom_task_template&#x27;; entityName: string; usage: EntityUsageReport; onConfirm: () =&gt; void; onCancel: () =&gt; void }`. Dialog must: render a blocking error state (not a dismissible warning) when `usage.total_references &gt; 0`; display `referenced_by` list with links to each workflow; disable the confirm button when blocking references exist. The 409 guard is the authoritative server-side enforcement layer; the pre-dialog usage fetch is a UX convenience only.</td>
        </tr><tr>
            <td><code>D-93</code></td>
            <td>D-EDGE-7 (SF-6→SF-7 index rebuild path): SF-6 never calls a dedicated SF-7 index-rebuild endpoint. Reference index rebuilds are triggered exclusively by SF-6&#x27;s save operations flowing through SF-5&#x27;s WorkflowMutationHook chain: `POST /api/workflows` fires `&#x27;created&#x27;`; `POST /api/workflows/import` fires `&#x27;imported&#x27;`; `POST /api/workflows/{id}/versions` fires `&#x27;version_saved&#x27;`; `DELETE /api/workflows/{id}` fires `&#x27;deleted&#x27;`. SF-7&#x27;s `refresh_entity_refs` handler: on `&#x27;deleted&#x27;` executes `DELETE FROM workflow_entity_refs WHERE workflow_id = ?`; on `&#x27;created&#x27;`/`&#x27;imported&#x27;`/`&#x27;version_saved&#x27;` loads `workflows.yaml_content`, parses via PyYAML `safe_load`, walks all `phases[].nodes` extracting `role_id`, `output_schema_id`, and `template_id` fields, deletes all prior `workflow_entity_refs` rows for this `workflow_id`, and bulk-inserts fresh rows. The rebuild is idempotent and safe to re-invoke on duplicate hook fires. SF-6 calls SF-7 endpoints only for user-facing reference display (`GET /api/roles/{id}/usage` etc.) — never to trigger index mutations.</td>
        </tr></tbody>
    </table>

    <h3>Risks</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Risk</th></tr></thead>
        <tbody><tr>
            <td><code>RISK-41</code></td>
            <td>RISK-1 (high): Flat React Flow state may serialize back to nested YAML incorrectly, especially for deeply nested phases or hook edges. Mitigation: make import/export round-trip tests mandatory and validate every save against the same iriai-compose models used by import.</td>
        </tr><tr>
            <td><code>RISK-42</code></td>
            <td>RISK-2 (medium): Frontend schema caching can drift after backend deploys if `/api/schema/workflow` changes and stale local data survives. Mitigation: refetch schema on app boot, attach an ETag or version stamp, and invalidate cached editor metadata whenever the schema changes.</td>
        </tr><tr>
            <td><code>RISK-43</code></td>
            <td>RISK-3 (medium): PostgreSQL connection pool exhaustion under bursty concurrent write patterns (repeated explicit saves, parallel imports). Mitigation: configure SQLAlchemy async pool with `pool_size=5, max_overflow=10`; keep transactions short; avoid holding connections across validation calls; use asyncpg driver for efficient connection reuse.</td>
        </tr><tr>
            <td><code>RISK-44</code></td>
            <td>RISK-4 (medium): WorkflowMutationHook handlers registered by SF-7 could raise exceptions, causing silent reference-index staleness. Mitigation: SF-5 hook dispatcher must catch and log all handler exceptions without rolling back the primary workflow transaction; SF-7 handlers must be idempotent and safe to re-invoke; add observability metrics for hook invocation failures.</td>
        </tr><tr>
            <td><code>RISK-45</code></td>
            <td>RISK-5 (low): Starter template assets can drift from iriai-build-v2 translations or the canonical schema. Mitigation: validate the bundled starter payloads against iriai-compose `WorkflowConfig` during CI and on backend startup.</td>
        </tr><tr>
            <td><code>RISK-46</code></td>
            <td>RISK-6 (medium): SF-7&#x27;s `refresh_entity_refs` handler scans `yaml_content` via PyYAML on every workflow save. For large workflows with many nodes, this adds latency to the post-commit hook path. Mitigation: handler runs post-commit and asynchronously (exception-swallowed), so it does not block the HTTP response. Add a timeout guard to the handler so a slow YAML scan does not hold the database connection indefinitely.</td>
        </tr><tr>
            <td><code>RISK-47</code></td>
            <td>RISK-7 (low): The SF-7 delete preflight 409 guard and the pre-dialog usage fetch can race if another user saves a workflow that adds a reference between the usage fetch and the delete call. Mitigation: the server-side 409 guard is the authoritative enforcement; SF-6&#x27;s pre-dialog fetch is UX-only. The race window results in a 409 error after the user confirmed — SF-6 must handle this gracefully by re-rendering the blocking dialog with fresh usage data.</td>
        </tr><tr>
            <td><code>RISK-48</code></td>
            <td>RISK-8 (low): `iriai_compose.schema` module (SF-1) does not exist yet — SF-5&#x27;s backend has a hard dependency on SF-1 completing first. Mitigation: SF-5 backend implementation is blocked until SF-1 exports `WorkflowConfig` from `iriai_compose.schema`. This dependency must be reflected in the implementation task DAG.</td>
        </tr></tbody>
    </table>
</section>
<hr/>


---

## Subfeature: Workflow Editor & Canvas (workflow-editor)

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
            <td><code>SVC-75b</code></td>
            <td><strong>ErrorFlowNode</strong></td>
            <td><code>frontend</code></td>
            <td>Thin React Flow adapter for the ErrorNode atomic type (D-GR-36). Renders a red-themed (#ef4444) terminal node card with a Jinja2 message template preview. Generates input Handles from dict-keyed inputs only. Has NO output Handles and NO hook ports. Placed from the palette alongside Ask, Branch, and Plugin.</td>
            <td><code>React, @xyflow/react</code></td>
            <td>&mdash;</td>
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
            <td>Right-side drag source for Ask, Branch, Plugin, Error, templates, and role chips. Dropping a Branch creates a node with two starter paths (keyed path_1 and path_2) each carrying a blank per-port condition expression, plus an empty inputs dict. Dropping a Plugin creates a node with a blank workflow-local plugin_ref and inline config placeholder rather than selecting a persisted plugin entity. Dropping an Error creates a red-themed terminal node with one input port, no outputs, no hooks, and a blank message template (D-GR-36).</td>
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
            <td>Inspector content for Ask, Branch, Plugin, Error, Phase, and Edge editing. Inspector field constraints and defaults are hydrated from GET /api/schema/workflow while keeping hand-authored XP layouts. BranchInspector edits per-port condition expressions in the named paths dict and optional merge_function for multi-input gather; each path row shows a name field and a condition expression editor. It never exposes switch_function, routing-mode toggles, output_field mode, or node-level condition_type or condition fields. ErrorInspector edits a Jinja2 message template; ErrorNode has no outputs or hooks (D-GR-36). PluginInspector edits a workflow-local plugin_ref plus inline plugin_config and never depends on a plugin registry API.</td>
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
            <td>Pure React visual primitives shared between SF-6 and SF-7. Exports AskNodePrimitive, BranchNodePrimitive, PluginNodePrimitive, ErrorNodePrimitive, NodePortDot, EdgeTypeLabel, and ActorSlot. BranchNodePrimitive receives paths (each path entry includes a per-port conditionSummary string) and optional mergeFunctionSummary; there is no SwitchFunctionLabel, no node-level conditionType prop, and no output_field rendering. Per-port condition summaries are rendered on each path handle. ErrorNodePrimitive renders a red-themed terminal card with message preview, input ports only, and no output or hook handles (D-GR-36).</td>
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
                        <td><code>&#x27;ask&#x27; | &#x27;branch&#x27; | &#x27;plugin&#x27; | &#x27;error&#x27;</code></td>
                        <td>Node discriminator. D-GR-36: ErrorNode is the 4th atomic type.</td>
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
                    </tr><tr>
                        <td><code>message</code></td>
                        <td><code>string</code></td>
                        <td>Jinja2 template for error message. Error-specific (D-GR-36). ErrorNode is a terminal node with inputs only — no outputs, no hooks.</td>
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


---

## Subfeature: Libraries & Registries (libraries-registries)

<!-- SF: libraries-registries -->
<section id="sf-libraries-registries" class="subfeature-section">
    <h2>SF-7 Libraries &amp; Registries - System Design</h2>
    <div class="provenance">Subfeature: <code>libraries-registries</code></div>

    <h3>Overview</h3>
    <div class="overview-text">SF-7 adds four library pages (Roles, Output Schemas, Task Templates, Tools) and three picker components that integrate with the SF-6 workflow editor across a React 19 frontend and FastAPI backend. The sidebar contains exactly 5 entity-type folders: Workflows, Roles, Schemas, Templates, Tools per PRD REQ-2.

SF-7 is the exclusive owner of three follow-on Alembic migrations that extend SF-5&#x27;s 5-table foundation: `workflow_entity_refs` (reference index), `tools` (custom tool registry), and `actor_slots` (task-template actor-slot definitions). SF-5&#x27;s five foundation tables — `workflows`, `workflow_versions`, `roles`, `output_schemas`, `custom_task_templates` — are not modified by SF-7. SF-5 exposes a post-commit mutation hook interface (REQ-18) that fires typed events (`created`, `updated`, `soft_deleted`, `restored`) on all four foundation entity types after each successful database commit. SF-7 subscribes to the `Workflow` hook slot at application startup (FastAPI lifespan event), registering `refresh_entity_refs(workflow_id, user_id)` for `updated` events and `purge_entity_refs(workflow_id)` for `soft_deleted` events. The callbacks maintain the `workflow_entity_refs` materialized index in a separate SF-7-owned database transaction, enabling O(1) delete preflight checks — a single indexed lookup per entity ID regardless of workflow count.

`GET /api/{entity}/references/{id}` reads from `workflow_entity_refs`, and library delete dialogs plus DELETE guards use those indexed rows instead of delete-time YAML scans. To guard against stale-index drift when a post-commit callback fails (SF-5 has already committed; the SF-7 transaction fails independently), an APScheduler reconciliation job runs periodically within compose-backend and can also be triggered manually via `POST /api/admin/reconcile-entity-refs`. The reconciliation job performs a full resync of `workflow_entity_refs` against actual workflow `yaml_content`, providing a deterministic recovery path for any missed hook events. The Tool Library (REQ-4) combines hardcoded built-in Claude tools with user-registered custom tools from the `tools` table, while tool delete protection remains a Role `tools` array check because tools are referenced by roles rather than by workflows. `actor_slots` rows extend `custom_task_templates` with named slot definitions so that actor-slot assignments survive reloads and remain reusable across workflows.</div>

    <h3>Services</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Name</th><th>Kind</th><th>Description</th><th>Technology</th><th>Port</th><th>Journeys</th>
        </tr></thead>
        <tbody><tr>
            <td><code>SVC-102</code></td>
            <td><strong>compose-frontend</strong></td>
            <td><code>frontend</code></td>
            <td>React SPA hosted at tools/compose/frontend that contains the SF-7 library pages and picker components alongside the SF-6 workflow editor. SF-7 additions live under features/libraries/ and include 4 library pages (Roles, Schemas, Templates, Tools), 3 pickers, a promotion dialog, shared hooks, and delete dialogs that preflight GET /api/{entity}/references/{id} before destructive actions.</td>
            <td><code>React 19, Vite, React Router, Zustand, React Flow, CodeMirror 6</code></td>
            <td>—</td>
            <td>J-5, J-23, J-24, J-25, J-27, J-28, J-29</td>
        </tr><tr>
            <td><code>SVC-103</code></td>
            <td><strong>Libraries Feature (SF-7)</strong></td>
            <td><code>frontend</code></td>
            <td>Sub-module of compose-frontend providing the 4 library pages (RolesLibraryPage, SchemasLibraryPage, TemplatesLibraryPage, ToolsLibraryPage), 3 picker components (RolePicker, SchemaPicker, TemplatePicker), ToolChecklistGrid (consumed by Role editor), PromotionDialog, EntityDeleteDialog, shared hooks, and 6 node visual primitives owned by SF-7 per D-GR-11 (AskNodePrimitive, BranchNodePrimitive, PluginNodePrimitive, ErrorNodePrimitive, NodePortDot, EdgeTypeLabel). The four atomic node types are Ask, Branch, Plugin, and Error (D-GR-36). ErrorNodePrimitive renders a red terminal-state card with no outputs and no hooks. Delete flows call the dedicated references endpoint and render a blocking dialog before DELETE when workflows still reference the selected role, schema, or template.</td>
            <td><code>React 19, Zustand, TanStack Query</code></td>
            <td>—</td>
            <td>J-5, J-23, J-24, J-25, J-27, J-28, J-29</td>
        </tr><tr>
            <td><code>SVC-104</code></td>
            <td><strong>Editor Feature (SF-6)</strong></td>
            <td><code>frontend</code></td>
            <td>SF-6 workflow editor sub-module containing EditorCanvas, AskNode, BranchNode, ErrorNode, inspectors, and palette. The four atomic node types on the palette are Ask, Branch, Plugin, and Error (D-GR-36). SF-6 wraps SF-7-owned visual primitives (AskNodePrimitive, BranchNodePrimitive, PluginNodePrimitive, ErrorNodePrimitive) in thin React Flow adapter components. Hosts pickers from libraries-feature inside inspectors and emits promotion callbacks (onPromoteRole, onPromoteSchema, onSaveTemplate). When a library role, schema, or template is attached to workflow content, the editor persists the library UUID in workflow data so that SF-5&#x27;s workflow save fires the post-commit hook that SF-7 uses to refresh workflow_entity_refs.</td>
            <td><code>React Flow, editorStore (Zustand factory)</code></td>
            <td>—</td>
            <td>J-5, J-16, J-18, J-20, J-27, J-29</td>
        </tr><tr>
            <td><code>SVC-105</code></td>
            <td><strong>compose-backend</strong></td>
            <td><code>service</code></td>
            <td>FastAPI backend at tools/compose/backend. SF-7 adds library entity CRUD extensions (Roles, Schemas, Templates, Tools, ActorSlots), duplicate name validation, JSON Schema server-side validation (Draft 2020-12), idempotent inline-to-library promotion, and reference-checking for delete dialogs and DELETE guards. SF-7&#x27;s Alembic migrations create `workflow_entity_refs`, `tools`, and `actor_slots` as downstream extensions after SF-5&#x27;s five-table foundation; those migrations run in SF-7&#x27;s own revision chain within the shared alembic_version_compose history. At application startup (FastAPI lifespan event), SF-7 subscribes to SF-5&#x27;s REQ-18 post-commit mutation hook interface by registering `refresh_entity_refs(workflow_id, user_id)` against the Workflow `updated` slot and `purge_entity_refs(workflow_id)` against the Workflow `soft_deleted` slot. The callbacks execute synchronously in-process but in separate database transactions from SF-5&#x27;s commit; if a callback fails, SF-5&#x27;s transaction is already committed and the workflow save is not rolled back. To recover from stale-index drift caused by callback failures, an APScheduler BackgroundScheduler job (`reconcile_entity_refs_all`) runs on a configurable interval (default: 15 minutes) and can be triggered on-demand via POST /api/admin/reconcile-entity-refs. GET /api/{entity}/references/{id} and role/schema/template DELETE endpoints query workflow_entity_refs. Tool delete protection remains a role scan because custom tools are referenced from Role.tools arrays, not directly from workflows.</td>
            <td><code>FastAPI, SQLAlchemy async, asyncpg, Alembic, APScheduler, homelocal-auth, jsonschema</code></td>
            <td>—</td>
            <td>J-5, J-23, J-24, J-27, J-28, J-29</td>
        </tr><tr>
            <td><code>SVC-106</code></td>
            <td><strong>compose-db</strong></td>
            <td><code>database</code></td>
            <td>PostgreSQL 15+ database managed by Alembic (alembic_version_compose table). SF-5 owns exactly five foundation tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`. SF-7 extends the schema with three additional tables via SF-7-owned Alembic migrations: `workflow_entity_refs` (materialized reference index for O(1) delete preflight; maintained by SF-5 post-commit mutation hook callbacks registered by SF-7), `tools` (user-registered custom tools; built-in tools remain backend constants and are never stored), and `actor_slots` (named actor-slot definitions per custom_task_templates row, enabling reusable slot assignments across workflows). SF-7 migrations depend on SF-5&#x27;s initial migration revision and must run after it.</td>
            <td><code>PostgreSQL 15+, Alembic migrations</code></td>
            <td>—</td>
            <td>J-5, J-23, J-24, J-27, J-28, J-29</td>
        </tr></tbody>
    </table>

    <h3>Connections</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>To</th><th>Protocol</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>CONN-161</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST/HTTPS - JWT Bearer</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-162</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>SQL / asyncpg</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-163</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-164</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Callback / React prop</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-165</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST/HTTPS - JWT Bearer</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-166</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST/HTTPS - JWT Bearer</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>API Endpoints</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Method</th><th>Path</th><th>Service</th><th>Description</th><th>Auth</th>
        </tr></thead>
        <tbody><tr>
            <td><code>API-166</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles/check-name</code></td>
            <td><code></code></td>
            <td>Check role name uniqueness for the authenticated user.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-167</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas/check-name</code></td>
            <td><code></code></td>
            <td>Check output schema name uniqueness for the authenticated user.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-168</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates/check-name</code></td>
            <td><code></code></td>
            <td>Check task template name uniqueness for the authenticated user.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-169</code></td>
            <td><code>GET</code></td>
            <td><code>/api/tools/check-name</code></td>
            <td><code></code></td>
            <td>Check tool name uniqueness for the authenticated user. Query param: ?name=mcp__github__create_issue.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-170</code></td>
            <td><code>GET</code></td>
            <td><code>/api/tools</code></td>
            <td><code></code></td>
            <td>List all tools: hardcoded built-in tools (source=built_in, no id) merged with user-registered custom tools from the SF-7-owned tools table (source=mcp or custom_function, with id). Used by both ToolsLibraryPage and the Role editor&#x27;s ToolChecklistGrid (REQ-4).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-171</code></td>
            <td><code>GET</code></td>
            <td><code>/api/tools/{id}</code></td>
            <td><code></code></td>
            <td>Get a single custom tool by UUID. Returns 404 for non-existent or deleted tools.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-172</code></td>
            <td><code>GET</code></td>
            <td><code>/api/{entity}/references/{id}</code></td>
            <td><code></code></td>
            <td>Return total and referenced_by[] for a role, schema, or template by querying the SF-7-owned workflow_entity_refs table joined to active SF-5 workflow rows. Single indexed lookup — O(1) per entity ID. Used by EntityDeleteDialog before delete and by DELETE handlers for re-checks. Entity must be one of: roles, schemas, templates.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-173</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>SF-5-owned endpoint. Persists workflow YAML containing library role/schema/template UUIDs. After SF-5 commits the workflow update, SF-5&#x27;s Workflow post-commit hook fires the `updated` event; SF-7&#x27;s registered refresh_entity_refs callback re-fetches the workflow&#x27;s yaml_content and replaces that workflow&#x27;s workflow_entity_refs rows in a separate SF-7 transaction.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-174</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>SF-5-owned endpoint. Soft-deletes a workflow. After SF-5 commits the soft-delete, SF-5&#x27;s Workflow post-commit hook fires the `soft_deleted` event; SF-7&#x27;s registered purge_entity_refs callback removes that workflow&#x27;s workflow_entity_refs rows in a separate SF-7 transaction.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-175</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete a role with a workflow_entity_refs re-check. Returns 409 with referencing workflow names if any active workflow still references the role UUID in the SF-7-owned workflow_entity_refs table (O(1) indexed lookup).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-176</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/schemas/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete an output schema with a workflow_entity_refs re-check. Blocked if referenced by any active workflow.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-177</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/templates/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete a task template with a workflow_entity_refs re-check. Blocked if referenced by any active workflow.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-178</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/tools/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete a custom tool from the SF-7-owned tools table with role-reference checking. Scans non-deleted Roles&#x27; tools JSON arrays for the tool&#x27;s name and returns 409 with referencing role names if found. Built-in tools cannot be deleted because they have no id.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-179</code></td>
            <td><code>POST</code></td>
            <td><code>/api/roles</code></td>
            <td><code></code></td>
            <td>Create a new role with optional idempotent promotion flag. Returns the existing record on duplicate name when promote=true. Writes to SF-5-owned roles table.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-180</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Update an existing role in the SF-5-owned roles table, including tool selections sourced from GET /api/tools.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-181</code></td>
            <td><code>POST</code></td>
            <td><code>/api/schemas</code></td>
            <td><code></code></td>
            <td>Create a new output schema in the SF-5-owned output_schemas table. Validates json_schema against JSON Schema Draft 2020-12 before persisting.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-182</code></td>
            <td><code>POST</code></td>
            <td><code>/api/tools</code></td>
            <td><code></code></td>
            <td>Register a new custom tool in the SF-7-owned tools table. Returns 409 on duplicate name. Name max 200 chars, description max 500 chars, input_schema max 256KB (REQ-8).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-183</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/tools/{id}</code></td>
            <td><code></code></td>
            <td>Update a custom tool in the SF-7-owned tools table. Same body fields as POST. Returns 404 for non-existent or deleted tools. If the name changes, the UI warns that roles using the old string must be updated manually.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-184</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates/{id}/actor-slots</code></td>
            <td><code></code></td>
            <td>List all actor slot definitions for a task template, reading from the SF-7-owned actor_slots table. Returns each slot_key with its current default_role_id and role display name.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-185</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/templates/{id}/actor-slots/{slot_key}</code></td>
            <td><code></code></td>
            <td>Upsert a named actor slot definition for a task template in the SF-7-owned actor_slots table. slot_key must be unique per template. default_role_id null defines an unassigned slot. Used to persist actor-slot definitions so they survive reloads and remain reusable across workflow instances.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-186</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/templates/{id}/actor-slots/{slot_key}</code></td>
            <td><code></code></td>
            <td>Remove a named actor slot definition from the SF-7-owned actor_slots table. Returns 204. Does not block on role references because slot definitions are template-level metadata, not reference-tracked entities.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-187</code></td>
            <td><code>POST</code></td>
            <td><code>/api/admin/reconcile-entity-refs</code></td>
            <td><code></code></td>
            <td>Manually trigger a full reconciliation of the workflow_entity_refs materialized index against actual workflow yaml_content. Invokes the same reconcile_entity_refs_all() function used by the APScheduler periodic job. For each non-deleted workflow, re-parses yaml_content, computes the diff against current workflow_entity_refs rows, and atomically reconciles mismatches (DELETE stale rows + INSERT missing rows in a single transaction per workflow). Idempotent — safe to run at any time. Returns a summary of rows added, rows removed, and workflows scanned. Intended for operator use when post-commit hook failures are suspected.</td>
            <td><code>JWT Bearer</code></td>
        </tr></tbody>
    </table>

    <h3>Call Paths</h3>
    <div class="call-path-block">
            <h4><code>CP-40</code>: User creates a new library role, immediately uses it in the workflow editor, and saves the workflow. The workflow save triggers SF-5&#x27;s post-commit Workflow updated event; SF-7&#x27;s registered refresh_entity_refs callback rebuilds the materialized workflow_entity_refs rows so later delete checks are O(1) table lookups rather than YAML scans.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;Navigate to /roles, click &#x27;+ New Role&#x27;&quot;, &#x27;description&#x27;: &#x27;User opens the Roles library page and triggers the RoleEditorView render.&#x27;, &#x27;returns&#x27;: &#x27;RoleEditorView renders&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/roles/check-name?name=test-pm&#x27;, &#x27;description&#x27;: &#x27;Frontend validates role name uniqueness before allowing save.&#x27;, &#x27;returns&#x27;: &#x27;{ available: true }&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;POST /api/roles { name, model, system_prompt, tools, metadata }&#x27;, &#x27;description&#x27;: &#x27;Create the new role record in the SF-5-owned roles table.&#x27;, &#x27;returns&#x27;: &#x27;201 { id, name, ... }&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &#x27;Invalidate library list and picker caches&#x27;, &#x27;description&#x27;: &#x27;Role appears in both the library grid and RolePicker with its canonical UUID and display name.&#x27;, &#x27;returns&#x27;: &#x27;UI updated with new role option&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;editor-feature&#x27;, &#x27;action&#x27;: &#x27;Select the new role from RolePicker in AskInspector&#x27;, &#x27;description&#x27;: &#x27;The editor stores the role UUID on the Ask node while showing the role name in the UI.&#x27;, &#x27;returns&#x27;: &#x27;Node now references the library role&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;editor-feature&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;PUT /api/workflows/{id} { yaml_content: ...roleId... }&#x27;, &#x27;description&#x27;: &#x27;SF-5 workflow save endpoint persists the role UUID inside the serialized workflow definition and commits the transaction.&#x27;, &#x27;returns&#x27;: &#x27;200 { id, updated_at }&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;SF-5 post-commit hook fires Workflow updated event; SF-7 refresh_entity_refs callback re-fetches yaml_content and executes: DELETE FROM workflow_entity_refs WHERE workflow_id=:wid; INSERT INTO workflow_entity_refs (role/schema/template refs parsed from yaml)&#x27;, &#x27;description&#x27;: &quot;SF-7&#x27;s registered refresh_entity_refs callback runs in its own database transaction after SF-5&#x27;s commit. It re-fetches the workflow&#x27;s yaml_content, parses entity UUID references, and atomically replaces the materialized reference rows. Result is immediately available for O(1) delete preflight lookups.&quot;, &#x27;returns&#x27;: &#x27;Updated workflow_entity_refs rows (separate transaction)&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;editor-feature&#x27;, &#x27;action&#x27;: &#x27;Return workflow save success (step 6 already returned 200; ref refresh is post-commit)&#x27;, &#x27;description&#x27;: &quot;Editor save completes with 200 from SF-5. The workflow_entity_refs rows are refreshed asynchronously in SF-7&#x27;s post-commit callback.&quot;, &#x27;returns&#x27;: &#x27;200 workflow saved&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-41</code>: User promotes an inline role defined within a workflow node to the shared Roles library. The node switches from inline role data to the canonical library role UUID, and the next workflow save triggers SF-5&#x27;s Workflow updated post-commit event for SF-7 to refresh workflow_entity_refs.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;editor-feature&#x27;, &#x27;to_service&#x27;: &#x27;libraries-feature&#x27;, &#x27;action&#x27;: &#x27;Open PromotionDialog with inline role data&#x27;, &#x27;description&#x27;: &#x27;Workflow editor emits onPromoteRole callback; PromotionDialog renders with pre-filled inline role fields.&#x27;, &#x27;returns&#x27;: &#x27;PromotionDialog renders&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/roles/check-name?name=promoted-pm&#x27;, &#x27;description&#x27;: &#x27;Validate proposed library name is not already taken.&#x27;, &#x27;returns&#x27;: &#x27;{ available: true }&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;POST /api/roles { ...inlineRole, promote: true }&#x27;, &#x27;description&#x27;: &#x27;Idempotent promotion. Backend returns the existing record if the same role name was already promoted. Writes to SF-5-owned roles table.&#x27;, &#x27;returns&#x27;: &#x27;201 { id, name, ... } or 200 existing record&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;libraries-feature&#x27;, &#x27;to_service&#x27;: &#x27;editor-feature&#x27;, &#x27;action&#x27;: &#x27;onSave callback -&gt; updateNodeData(nodeId, { actor: role.id, actorLabel: role.name, inline_role: undefined })&#x27;, &#x27;description&#x27;: &#x27;Node in the workflow editor switches from inline role content to the canonical library role UUID while keeping the display label.&#x27;, &#x27;returns&#x27;: &#x27;Node reference updated in workflow graph&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;editor-feature&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;PUT /api/workflows/{id} { yaml_content: ...roleId... }&#x27;, &#x27;description&#x27;: &#x27;SF-5 workflow save endpoint persists the promoted role UUID in the serialized workflow definition and commits.&#x27;, &#x27;returns&#x27;: &#x27;200 { id, updated_at }&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;SF-5 post-commit hook fires Workflow updated event; SF-7 refresh_entity_refs callback re-fetches yaml_content and atomically replaces workflow_entity_refs rows in a separate transaction&#x27;, &#x27;description&#x27;: &quot;SF-7&#x27;s refresh_entity_refs callback runs after SF-5&#x27;s commit. It re-fetches the workflow YAML, parses entity refs including the newly promoted role UUID, and rebuilds the workflow&#x27;s materialized reference rows.&quot;, &#x27;returns&#x27;: &#x27;Updated workflow_entity_refs rows (separate transaction)&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;editor-feature&#x27;, &#x27;action&#x27;: &#x27;Return workflow save success (step 5 already returned 200; ref refresh is post-commit)&#x27;, &#x27;description&#x27;: &#x27;The node now references the reusable library role. Future delete checks resolve from workflow_entity_refs via O(1) indexed lookup.&#x27;, &#x27;returns&#x27;: &#x27;200 workflow saved&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-42</code>: User attempts to delete a role that is still referenced by at least one workflow. The frontend first calls the dedicated references endpoint, and both the pre-delete dialog and the DELETE guard read the SF-7-owned workflow_entity_refs table with a single O(1) indexed lookup instead of parsing workflow YAML.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &#x27;Click delete on a role card&#x27;, &#x27;description&#x27;: &#x27;User initiates deletion for a library role.&#x27;, &#x27;returns&#x27;: &#x27;Delete flow starts&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/roles/references/{id}&#x27;, &#x27;description&#x27;: &#x27;Frontend preflights the dedicated reference endpoint before showing a destructive confirmation.&#x27;, &#x27;returns&#x27;: &#x27;Pending reference lookup&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &quot;SELECT DISTINCT workflows.id, workflows.name FROM workflow_entity_refs JOIN workflows ON workflows.id = workflow_entity_refs.workflow_id WHERE workflow_entity_refs.entity_type=&#x27;role&#x27; AND workflow_entity_refs.entity_id=:role_id AND workflow_entity_refs.user_id=:uid AND workflows.deleted_at IS NULL&quot;, &#x27;description&#x27;: &quot;Backend resolves referencing workflows from the SF-7-owned workflow_entity_refs junction table joined to SF-5&#x27;s active workflow rows. Single indexed lookup — O(1) per entity ID.&quot;, &#x27;returns&#x27;: &#x27;Referencing workflow rows&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;200 { total: 1, referenced_by: [{ workflow_id, workflow_name: &#x27;Planning Workflow&#x27; }] }&quot;, &#x27;description&#x27;: &#x27;Reference endpoint returns the blocked-delete details needed by the dialog.&#x27;, &#x27;returns&#x27;: &#x27;Reference payload&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;Browser&#x27;, &#x27;action&#x27;: &quot;EntityDeleteDialog shows &#x27;Cannot delete - referenced by 1 workflow&#x27;&quot;, &#x27;description&#x27;: &#x27;Frontend renders the blocked-delete state with workflow names and no destructive confirm action.&#x27;, &#x27;returns&#x27;: &#x27;User sees blocked state&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;DELETE /api/roles/{id}&#x27;, &#x27;description&#x27;: &#x27;If a stale client or manual request still submits DELETE, the backend re-checks workflow_entity_refs before mutating data.&#x27;, &#x27;returns&#x27;: &#x27;Pending server-side delete guard&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &quot;SELECT 1 FROM workflow_entity_refs WHERE entity_type=&#x27;role&#x27; AND entity_id=:role_id AND user_id=:uid LIMIT 1&quot;, &#x27;description&#x27;: &#x27;DELETE re-check uses the same SF-7-owned workflow_entity_refs index (O(1) lookup) to prevent races between preflight and mutation. No YAML parsing occurs.&#x27;, &#x27;returns&#x27;: &#x27;Matching ref row still exists&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;409 { error: &#x27;reference_conflict&#x27;, total: 1, referenced_by: [{ workflow_id, workflow_name: &#x27;Planning Workflow&#x27; }] }&quot;, &#x27;description&#x27;: &#x27;Backend blocks the delete without any workflow YAML parsing.&#x27;, &#x27;returns&#x27;: &#x27;Blocked delete response&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-43</code>: User registers a custom MCP tool in the SF-7-owned tools table, then opens a Role editor where the tool appears in the ToolChecklistGrid. The user selects the tool and saves the role with the custom tool in its tools array.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;Navigate to /tools, click &#x27;New Tool&#x27;&quot;, &#x27;description&#x27;: &quot;User opens the Tools Library page. Two sections are visible: &#x27;Built-in Tools&#x27; (read-only cards, no DB rows) and &#x27;My Tools&#x27; (user-registered, from SF-7-owned tools table).&quot;, &#x27;returns&#x27;: &#x27;ToolEditorView renders (create mode)&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/tools/check-name?name=mcp__github__create_issue&#x27;, &#x27;description&#x27;: &#x27;Frontend validates tool name uniqueness before allowing save.&#x27;, &#x27;returns&#x27;: &#x27;{ available: true }&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &quot;POST /api/tools { name, description, source: &#x27;mcp&#x27;, input_schema }&quot;, &#x27;description&#x27;: &#x27;Create the new tool record in the SF-7-owned tools table.&#x27;, &#x27;returns&#x27;: &#x27;201 { id, name, source, ... }&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &#x27;Success toast + navigate to /tools list view&#x27;, &#x27;description&#x27;: &quot;Cache invalidation triggers a refetch; the tool appears in the &#x27;My Tools&#x27; section with an &#x27;MCP&#x27; badge.&quot;, &#x27;returns&#x27;: &#x27;UI updated with new tool in list&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;Navigate to /roles, open &#x27;code-reviewer&#x27; role editor, scroll to Tools section&quot;, &#x27;description&#x27;: &#x27;User opens the Role editor; the Tools section renders ToolChecklistGrid.&#x27;, &#x27;returns&#x27;: &#x27;RoleEditorView renders with Tools section&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/tools&#x27;, &#x27;description&#x27;: &#x27;ToolChecklistGrid fetches all tools. Response merges hardcoded built-in tools (backend constants) with user-registered tools from the SF-7-owned tools table.&#x27;, &#x27;returns&#x27;: &quot;{ tools: [ ...built-in, { id, name: &#x27;mcp__github__create_issue&#x27;, source: &#x27;mcp&#x27;, ... } ] }&quot;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &#x27;Check Read, Grep, Glob, and mcp__github__create_issue, then save role&#x27;, &#x27;description&#x27;: &quot;ToolChecklistGrid shows &#x27;Built-in&#x27; and &#x27;Registered&#x27; groups. User selects tools from both groups.&quot;, &#x27;returns&#x27;: &quot;Role saved with tools: [&#x27;Read&#x27;, &#x27;Grep&#x27;, &#x27;Glob&#x27;, &#x27;mcp__github__create_issue&#x27;]&quot;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &quot;PUT /api/roles/{id} { tools: [&#x27;Read&#x27;, &#x27;Grep&#x27;, &#x27;Glob&#x27;, &#x27;mcp__github__create_issue&#x27;] }&quot;, &#x27;description&#x27;: &#x27;Role entity is updated in the SF-5-owned roles table with the new tools array. Tool references remain role-local and do not participate in workflow_entity_refs.&#x27;, &#x27;returns&#x27;: &#x27;200 { id, name, tools: [...], ... }&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-44</code>: User attempts to delete a custom tool from the SF-7-owned tools table that is referenced by at least one Role&#x27;s tools array. Tool delete protection intentionally remains a Role scan and does not use workflow_entity_refs, because tools are not referenced directly by workflows.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;DELETE /api/tools/{id}&#x27;, &#x27;description&#x27;: &quot;User clicks delete on a custom tool card in the &#x27;My Tools&#x27; section; frontend sends the delete request.&quot;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;SELECT name FROM tools WHERE id=:tool_id AND deleted_at IS NULL&#x27;, &#x27;description&#x27;: &quot;Fetch the tool&#x27;s name from the SF-7-owned tools table so the backend can check Role tools arrays for that exact identifier.&quot;, &#x27;returns&#x27;: &quot;{ name: &#x27;mcp__github__create_issue&#x27; }&quot;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;SELECT id, name, tools FROM roles WHERE user_id=:uid AND deleted_at IS NULL&#x27;, &#x27;description&#x27;: &#x27;Backend fetches all non-deleted roles from the SF-5-owned roles table to scan their tools JSON arrays.&#x27;, &#x27;returns&#x27;: &#x27;Role rows with tools JSON arrays&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &quot;Scan each role&#x27;s tools array for tool name match&quot;, &#x27;description&#x27;: &#x27;In-memory role scan identifies referencing role names. This flow is separate from the workflow reference endpoint.&#x27;, &#x27;returns&#x27;: &#x27;List of referencing role names&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;409 { error: &#x27;reference_conflict&#x27;, details: [{ role_id, role_name }] }&quot;, &#x27;description&#x27;: &#x27;Backend returns blocked-delete response listing referencing roles.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;Browser&#x27;, &#x27;action&#x27;: &quot;EntityDeleteDialog shows &#x27;Cannot delete - referenced by 2 roles&#x27;&quot;, &#x27;description&#x27;: &#x27;Frontend renders the blocked-delete state with role names so the user understands the dependency.&#x27;, &#x27;returns&#x27;: &#x27;User sees blocked state&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-45</code>: User opens a task template in the library and defines named actor slots (e.g., &#x27;pm&#x27;, &#x27;reviewer&#x27;) with optional default role assignments. SF-7&#x27;s actor_slots table persists these definitions so they survive reloads and are reusable across workflow instances.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;Navigate to /templates, open a template, click &#x27;Edit Actor Slots&#x27;&quot;, &#x27;description&#x27;: &#x27;User opens the ActorSlotsEditor panel within the TemplatesLibraryPage.&#x27;, &#x27;returns&#x27;: &#x27;ActorSlotsEditor renders&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/templates/{id}/actor-slots&#x27;, &#x27;description&#x27;: &#x27;Load existing slot definitions from the SF-7-owned actor_slots table for this template.&#x27;, &#x27;returns&#x27;: &#x27;{ slots: [{ slot_key, default_role_id?, role_name? }] }&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;Add new slot &#x27;pm&#x27;, select a default role from RolePicker, click Save&quot;, &#x27;description&#x27;: &#x27;User defines a new named actor slot with an optional default role assignment.&#x27;, &#x27;returns&#x27;: &#x27;Pending slot upsert&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;PUT /api/templates/{id}/actor-slots/pm { default_role_id: roleUUID }&#x27;, &#x27;description&#x27;: &quot;Upsert the slot definition in the SF-7-owned actor_slots table. slot_key &#x27;pm&#x27; is unique per template.&quot;, &#x27;returns&#x27;: &quot;{ slot_key: &#x27;pm&#x27;, default_role_id: roleUUID, role_name: &#x27;Product Manager&#x27;, updated_at }&quot;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;Browser&#x27;, &#x27;action&#x27;: &quot;ActorSlotsEditor shows &#x27;pm → Product Manager&#x27; in the slot list&quot;, &#x27;description&#x27;: &quot;Slot definition is persisted and displayed. Future workflow instances referencing this template can resolve the &#x27;pm&#x27; slot to the assigned role.&quot;, &#x27;returns&#x27;: &#x27;Slot persisted and visible&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-46</code>: The APScheduler reconciliation job (or manual operator trigger via POST /api/admin/reconcile-entity-refs) resyncs the materialized workflow_entity_refs index against actual workflow yaml_content. Handles stale-index recovery for scenarios where SF-7&#x27;s post-commit hook callbacks failed after a SF-5 commit. The job is idempotent and produces the same result regardless of how many times it runs.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;APScheduler&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;Fire reconcile_entity_refs_all() on configured interval (default: 15 minutes) or via POST /api/admin/reconcile-entity-refs&#x27;, &#x27;description&#x27;: &#x27;Scheduler triggers the reconciliation function. Manual trigger path calls the same reconcile_entity_refs_all() function.&#x27;, &#x27;returns&#x27;: &#x27;Reconciliation started&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;SELECT id, user_id, yaml_content FROM workflows WHERE deleted_at IS NULL&#x27;, &#x27;description&#x27;: &#x27;Fetch all non-deleted workflows to inspect their current yaml_content. No lock held — reads are snapshot-consistent.&#x27;, &#x27;returns&#x27;: &#x27;List of active workflow rows&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;For each workflow: parse yaml_content to extract all library UUID refs (roles, schemas, templates)&#x27;, &#x27;description&#x27;: &#x27;In-process YAML parse extracts all entity_type / entity_id pairs from the workflow definition. Same parser used by refresh_entity_refs callback.&#x27;, &#x27;returns&#x27;: &#x27;Expected set of (entity_type, entity_id) pairs per workflow_id&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;SELECT workflow_id, entity_type, entity_id FROM workflow_entity_refs WHERE workflow_id = ANY(:workflow_ids)&#x27;, &#x27;description&#x27;: &#x27;Batch-fetch the current materialized rows for all active workflows in a single query.&#x27;, &#x27;returns&#x27;: &#x27;Current set of indexed (workflow_id, entity_type, entity_id) rows&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;Compute diff per workflow: missing_rows = expected − current; stale_rows = current − expected&#x27;, &#x27;description&#x27;: &#x27;Set arithmetic identifies which rows need to be inserted (missed by failed hooks) and which rows are stale (orphaned by failed purge hooks).&#x27;, &#x27;returns&#x27;: &#x27;Per-workflow diff maps&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;For each workflow with non-empty diff: BEGIN; DELETE stale rows; INSERT missing rows; COMMIT&#x27;, &#x27;description&#x27;: &#x27;Atomic reconcile transaction per workflow. Only workflows with diffs are touched. Workflows already in sync incur no write.&#x27;, &#x27;returns&#x27;: &#x27;Updated workflow_entity_refs rows; rows_added and rows_removed counters accumulated&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;Return { workflows_scanned, rows_added, rows_removed, duration_ms }&#x27;, &#x27;description&#x27;: &#x27;Summary returned to the manual trigger caller (POST /api/admin/reconcile-entity-refs response body) or logged by the scheduler job.&#x27;, &#x27;returns&#x27;: &#x27;Reconciliation summary&#x27;}</li></ol>
        </div>

    <h3>Entities</h3>
    <div class="entity-block">
            <h4><code>ENT-109</code>: Workflow</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-5-owned table. Referenced by SF-7 for workflow_entity_refs refresh via post-commit hooks.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>yaml_content</code></td>
                        <td><code>TEXT</code></td>
                        <td>Serialized workflow definition containing library role, schema, and template UUID references. SF-7&#x27;s refresh_entity_refs callback and the reconciliation job both re-fetch this field to rebuild workflow_entity_refs rows.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-110</code>: Role</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-5-owned table (roles). Canonical library role identifier returned by RolePicker and persisted into workflow YAML content.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>model</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>system_prompt</code></td>
                        <td><code>TEXT</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>tools</code></td>
                        <td><code>JSON</code></td>
                        <td>Role-local tool references used by ToolChecklistGrid and tool delete protection. Not materialized into workflow_entity_refs.</td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>JSON</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>is_example</code></td>
                        <td><code>BOOLEAN</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-111</code>: OutputSchema</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-5-owned table (output_schemas). Canonical library output schema identifier persisted into workflow YAML content.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>TEXT</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>json_schema</code></td>
                        <td><code>JSON</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>is_example</code></td>
                        <td><code>BOOLEAN</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-112</code>: TaskTemplate</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-5-owned table (custom_task_templates). Canonical library task template identifier. SF-7 extends this entity with actor_slots rows via a separate SF-7-owned migration.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>TEXT</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>subgraph_yaml</code></td>
                        <td><code>TEXT</code></td>
                        <td>Serialized task template subgraph used when stamping or saving template refs.</td>
                    </tr><tr>
                        <td><code>input_interface</code></td>
                        <td><code>JSON</code></td>
                        <td>Declared input interface for the template subgraph.</td>
                    </tr><tr>
                        <td><code>output_interface</code></td>
                        <td><code>JSON</code></td>
                        <td>Declared output interface for the template subgraph.</td>
                    </tr><tr>
                        <td><code>is_example</code></td>
                        <td><code>BOOLEAN</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-113</code>: Tool</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-7-owned table (tools). Created by SF-7&#x27;s Alembic migration as the first downstream extension after SF-5&#x27;s five foundation tables. Primary key for custom tools only; built-in tools have no DB rows.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td>From JWT sub claim; scopes tools per user.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>VARCHAR(200)</code></td>
                        <td>Tool identifier, for example mcp__github__create_issue. Max 200 chars.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>VARCHAR(500)</code></td>
                        <td>What the tool does. Shown in Tool Library cards and the Role editor checklist.</td>
                    </tr><tr>
                        <td><code>source</code></td>
                        <td><code>VARCHAR</code></td>
                        <td>Tool origin type. &#x27;mcp&#x27; for MCP server tools, &#x27;custom_function&#x27; for custom definitions.</td>
                    </tr><tr>
                        <td><code>input_schema</code></td>
                        <td><code>JSON</code></td>
                        <td>Optional JSON Schema describing the tool&#x27;s input parameters.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td>Set on creation, immutable on update.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td>Set on every update.</td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td>Soft-delete pattern. Partial unique index excludes deleted rows.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-114</code>: WorkflowEntityRef</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-7-owned table (workflow_entity_refs). Created exclusively by SF-7&#x27;s Alembic migration — not part of SF-5&#x27;s five foundation tables. Stable row identifier for the materialized reference entry.</td>
                    </tr><tr>
                        <td><code>workflow_id</code></td>
                        <td><code>UUID</code></td>
                        <td>References the SF-5-owned workflows table. On SF-5 workflow soft-delete, SF-7&#x27;s purge_entity_refs post-commit callback removes rows; ON DELETE CASCADE is a safety net for hard-deletes and missed hook events.</td>
                    </tr><tr>
                        <td><code>entity_type</code></td>
                        <td><code>VARCHAR</code></td>
                        <td>Library entity class referenced by the workflow.</td>
                    </tr><tr>
                        <td><code>entity_id</code></td>
                        <td><code>UUID</code></td>
                        <td>UUID of the referenced Role, OutputSchema, or TaskTemplate (all in SF-5-owned tables). Indexed for O(1) delete preflight lookups by entity_id.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td>Owner scope used by the references endpoint and delete guards.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td>Timestamp when the current materialized ref row was created by SF-7&#x27;s refresh_entity_refs callback or the reconciliation job.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-115</code>: ActorSlot</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-7-owned table (actor_slots). Created by SF-7&#x27;s Alembic migration as an extension to SF-5&#x27;s custom_task_templates. Stable row identifier for one named actor-slot definition.</td>
                    </tr><tr>
                        <td><code>template_id</code></td>
                        <td><code>UUID</code></td>
                        <td>References the SF-5-owned custom_task_templates table. Cascade ensures slot definitions are removed when a template is hard-deleted.</td>
                    </tr><tr>
                        <td><code>slot_key</code></td>
                        <td><code>VARCHAR(100)</code></td>
                        <td>Symbolic slot identifier used by workflow nodes (e.g. &#x27;pm&#x27;, &#x27;reviewer&#x27;). Unique per template.</td>
                    </tr><tr>
                        <td><code>default_role_id</code></td>
                        <td><code>UUID</code></td>
                        <td>Default library role UUID for this slot. Null means unassigned. References the SF-5-owned roles table.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>VARCHAR(500)</code></td>
                        <td>Human-readable description of what actor should fill this slot.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td>Owner scope from JWT sub claim. Scopes slot definitions per user.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td>Set on creation.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td>Set on every update.</td>
                    </tr></tbody>
            </table>
        </div>

    <h3>Entity Relations</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>Relation</th><th>To</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>ER-100</code></td>
            <td><code>workflow</code></td>
            <td></td>
            <td><code>workflow-entity-ref</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-101</code></td>
            <td><code>workflow</code></td>
            <td></td>
            <td><code>role</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-102</code></td>
            <td><code>workflow</code></td>
            <td></td>
            <td><code>output-schema</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-103</code></td>
            <td><code>workflow</code></td>
            <td></td>
            <td><code>task-template</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-104</code></td>
            <td><code>role</code></td>
            <td></td>
            <td><code>tool</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-105</code></td>
            <td><code>task-template</code></td>
            <td></td>
            <td><code>actor-slot</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-106</code></td>
            <td><code>actor-slot</code></td>
            <td></td>
            <td><code>role</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>Decisions</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Decision</th></tr></thead>
        <tbody><tr>
            <td><code>D-121</code></td>
            <td>D-SF7-1: TaskTemplateEditorView reuses SF-6 editor components (EditorCanvas, AskNode, BranchNode, ErrorNode, inspectors) directly with no shared canvas abstraction layer. The palette exposes four atomic node types: Ask, Branch, Plugin, Error (D-GR-36). Each editor creates its own editorStore instance via factory function. Phase creation tools are disabled via a noPhaseTools prop.</td>
        </tr><tr>
            <td><code>D-122</code></td>
            <td>D-SF7-2 [REQ-18, D-GR-37]: Reference checking for role, schema, and template deletes subscribes to SF-5&#x27;s REQ-18 post-commit mutation hook interface and maintains the SF-7-owned materialized workflow_entity_refs index to provide O(1) delete preflight checks (single indexed lookup by entity_id, regardless of workflow count). The prior approach of parsing all workflow YAML at delete time (O(n×parse) per entity delete) is explicitly rejected. SF-7 registers refresh_entity_refs(workflow_id, user_id) against the Workflow `updated` hook slot and purge_entity_refs(workflow_id) against the Workflow `soft_deleted` slot at FastAPI lifespan startup. Both GET /api/{entity}/references/{id} and DELETE handler re-checks read the same workflow_entity_refs index — no YAML parsing occurs at delete time. Recovery from stale-index drift is handled by the periodic reconciliation job per D-SF7-8.</td>
        </tr><tr>
            <td><code>D-123</code></td>
            <td>D-SF7-3: SF-7 is the EXCLUSIVE owner of the workflow_entity_refs table and its Alembic migration. SF-5&#x27;s five-table foundation (workflows, workflow_versions, roles, output_schemas, custom_task_templates) does NOT include workflow_entity_refs. SF-5&#x27;s REQ-18 post-commit mutation hook interface fires typed events (created, updated, soft_deleted, restored) on all four foundation entity types after each successful database commit. SF-7 registers refresh_entity_refs(workflow_id, user_id) and purge_entity_refs(workflow_id) callbacks against the Workflow hook slot at application startup (FastAPI lifespan event). Those callbacks run in a separate SF-7-owned database transaction and do not re-enter SF-5&#x27;s transaction. SF-5 never creates or updates workflow_entity_refs rows — all library-facing backend flows (duplicate name validation, JSON Schema server-side validation, inline-to-library promotion, pre-delete reference dialogs, delete guard responses) remain SF-7 scope.</td>
        </tr><tr>
            <td><code>D-124</code></td>
            <td>D-SF7-4 [REQ-2]: Sidebar contains exactly 5 entity-type folders: Workflows, Roles, Schemas, Templates, Tools. Phases and Plugins are NOT sidebar folders.</td>
        </tr><tr>
            <td><code>D-125</code></td>
            <td>D-SF7-5 [REQ-4, D-GR-7]: Tool Library uses a two-tier data strategy: built-in Claude tools are hardcoded in the backend and always included in GET /api/tools responses (no DB rows), while user-registered custom tools are stored in the SF-7-owned tools table. Tool deletion intentionally remains a Role.tools array check and does not use workflow_entity_refs because tools are not referenced directly by workflows.</td>
        </tr><tr>
            <td><code>D-126</code></td>
            <td>D-GR-26: workflow_entity_refs is the canonical reference-tracking model behind GET /api/{entity}/references/{id}. Pre-delete dialogs use that endpoint, and DELETE handlers re-check the same SF-7-owned table to guard against stale clients or concurrent workflow edits.</td>
        </tr><tr>
            <td><code>D-127</code></td>
            <td>D-SF7-6: SF-5&#x27;s REQ-18 post-commit hook interface exposes two relevant slot types for SF-7: Workflow updated (covers create, import, duplicate, save-version) and Workflow soft_deleted. Both pass the workflow_id and user_id. SF-7&#x27;s refresh_entity_refs callback handles the updated event by opening a new AsyncSession, re-fetching the workflow&#x27;s yaml_content, parsing all library UUID references (roles, schemas, templates), and atomically replacing the workflow&#x27;s workflow_entity_refs rows (DELETE WHERE workflow_id=:wid + bulk INSERT). SF-7&#x27;s purge_entity_refs callback handles the soft_deleted event by deleting all workflow_entity_refs rows for that workflow_id. Both callbacks are registered synchronously during the FastAPI lifespan startup event by calling SF-5&#x27;s hook registration API, with SF-5 never importing SF-7 modules. The callbacks execute synchronously in-process but in separate database transactions from SF-5&#x27;s commit — if a callback fails, SF-5&#x27;s transaction is already committed and the workflow save is not rolled back. Stale-index recovery is owned by the reconciliation job (D-SF7-8).</td>
        </tr><tr>
            <td><code>D-128</code></td>
            <td>D-SF7-7: actor_slots persistence is SF-7-owned. SF-7&#x27;s Alembic migration creates the actor_slots table as an extension to SF-5&#x27;s custom_task_templates (FK: actor_slots.template_id -&gt; custom_task_templates.id ON DELETE CASCADE). Each row stores a named slot_key unique per template, an optional default_role_id pointing to the SF-5-owned roles table, and a description. The API surface is /api/templates/{id}/actor-slots with GET (list), PUT /{slot_key} (upsert), and DELETE /{slot_key} (remove). Actor slot definitions are not reference-tracked in workflow_entity_refs.</td>
        </tr><tr>
            <td><code>D-129</code></td>
            <td>D-GR-11 [D-SF7-11]: SF-7 is the exclusive owner of 6 node visual primitives: AskNodePrimitive, BranchNodePrimitive, PluginNodePrimitive, ErrorNodePrimitive, NodePortDot, and EdgeTypeLabel. These are pure presentational React components with no React Flow dependency. SF-6 wraps them in thin React Flow adapter components. The unidirectional dependency ensures SF-6 depends on SF-7 primitives, never the reverse. ErrorNodePrimitive renders a red terminal-state card reflecting the Error atomic type (D-GR-36).</td>
        </tr><tr>
            <td><code>D-130</code></td>
            <td>D-GR-36 (ErrorNode as 4th atomic type): ErrorNode is the 4th atomic node type alongside Ask, Branch, and Plugin. Entity shape: `id` (UUID), `type: error`, `message` (Jinja2 template string), `inputs` (dict). ErrorNode has NO outputs, NO hooks. It represents a terminal error state in the workflow graph. The ErrorNodePrimitive visual component is owned by SF-7; the ErrorNode React Flow adapter is owned by SF-6.</td>
        </tr><tr>
            <td><code>D-131</code></td>
            <td>D-SF7-8 [D-GR-37, RISK-1]: A periodic reconciliation job runs within compose-backend (APScheduler BackgroundScheduler, default interval: 15 minutes, configurable via RECONCILE_JOB_INTERVAL_MINUTES env var, disableable via RECONCILE_JOB_ENABLED=false) to resync workflow_entity_refs from workflow yaml_content in scenarios where SF-7&#x27;s post-commit hook callbacks fail (the SF-5 commit succeeds but the SF-7 separate transaction does not). The reconciliation function reconcile_entity_refs_all(): (1) queries all non-deleted workflows in a single SELECT, (2) batch-fetches current workflow_entity_refs rows for those workflow IDs, (3) for each workflow, parses yaml_content using the same entity-ref extractor as refresh_entity_refs, (4) computes the diff (missing inserts, stale deletes), and (5) for each workflow with a non-empty diff, opens an atomic transaction: DELETE stale rows + INSERT missing rows. Workflows already in sync incur no write. The job is idempotent — running it multiple times produces the same result. A manual trigger endpoint POST /api/admin/reconcile-entity-refs invokes the same reconcile_entity_refs_all() function and returns { workflows_scanned, rows_added, rows_removed, duration_ms }. The job is registered via FastAPI lifespan startup using the same startup hook that registers the mutation callbacks.</td>
        </tr></tbody>
    </table>

    <h3>Risks</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Risk</th></tr></thead>
        <tbody><tr>
            <td><code>RISK-59</code></td>
            <td>RISK-1 (Medium): workflow_entity_refs can drift from the true set of workflow entity references if SF-7&#x27;s post-commit callback fails (e.g., database error in the separate SF-7 transaction after SF-5 has already committed). Because SF-5 has already committed, the workflow save succeeds but the reference index is stale until the next workflow save or reconciliation run. Mitigation: (a) D-SF7-8 reconciliation job (APScheduler, 15-minute interval) deterministically resyncs the index from yaml_content, providing a bounded-time recovery path with no operator intervention; (b) a manual trigger endpoint (POST /api/admin/reconcile-entity-refs) allows on-demand resync when drift is suspected; (c) SF-7&#x27;s DELETE re-checks on role/schema/template delete provide a second safety net; (d) the ON DELETE CASCADE FK on workflow_entity_refs.workflow_id ensures rows are always cleaned up on hard-delete even if the callback was missed.</td>
        </tr><tr>
            <td><code>RISK-60</code></td>
            <td>RISK-2 (Medium): Sharing SF-6 editor components in TaskTemplateEditorView may cause state leaks between the workflow editor and the template editor. Mitigation: create isolated editorStore instances via a factory function per editor mount.</td>
        </tr><tr>
            <td><code>RISK-61</code></td>
            <td>RISK-3 (Low): Picker data freshness - a role or schema created in the library page may not appear in editor pickers until the next fetch cycle. Mitigation: TanStack Query cache invalidation on successful mutations.</td>
        </tr><tr>
            <td><code>RISK-62</code></td>
            <td>RISK-4 (Low): Inline-to-library promotion race condition if a user has two windows open simultaneously creating the same role name. Mitigation: backend idempotent promotion path returns the existing record on duplicate name when promote=true.</td>
        </tr><tr>
            <td><code>RISK-63</code></td>
            <td>RISK-5 (Medium): Tool name-based references in Role.tools JSON arrays are fragile - renaming a custom tool via PUT /api/tools/{id} does not automatically update roles that reference the old name. Mitigation: UI warning banner on rename and Role editor refetch from GET /api/tools so stale names become visible when edited.</td>
        </tr><tr>
            <td><code>RISK-64</code></td>
            <td>RISK-6 (Low): Tool deletion reference checking still scans all user roles in memory. Complexity is O(N*M), where N=roles and M=average tools per role. Mitigation: typical user counts are small, and the query stays scoped to the current user&#x27;s non-deleted roles.</td>
        </tr><tr>
            <td><code>RISK-65</code></td>
            <td>RISK-7 (Low): SF-7&#x27;s Alembic migration must declare its dependency on SF-5&#x27;s initial migration revision. If SF-5&#x27;s revision ID changes (e.g., squashed migration), SF-7&#x27;s down_revision reference breaks. Mitigation: treat SF-5&#x27;s initial revision ID as a stable anchor; document the dependency explicitly in SF-7&#x27;s migration file header.</td>
        </tr><tr>
            <td><code>RISK-66</code></td>
            <td>RISK-8 (Low): The reconciliation job (D-SF7-8) scans all non-deleted workflows on every run. At high workflow counts this may cause a momentary read spike. Mitigation: the batch SELECT reads yaml_content once; writes only touch workflows with diffs; the job interval (default 15 min) is tunable via RECONCILE_JOB_INTERVAL_MINUTES; the job can be disabled entirely via RECONCILE_JOB_ENABLED=false if the hook failure rate is negligible in production.</td>
        </tr></tbody>
    </table>
</section>
<hr/>


    <footer style="text-align: center; color: var(--muted); font-size: 0.85rem; margin-top: 3rem; padding: 1rem;">
        Generated by artifact compiler. All content preserved from source subfeature artifacts.
    </footer>
</body>
</html>
