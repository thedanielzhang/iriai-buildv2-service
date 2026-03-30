## Broad Artifact (design:broad)

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
### SF-1: Declarative Schema & Primitives

SF-1 now defines a single canonical wire contract for declarative workflows. `WorkflowConfig` is YAML-first and closed to `schema_version`, `workflow_version`, `name`, `description`, `metadata`, `actors`, `phases`, `edges`, `templates`, `plugins`, `types`, and `cost_config`; `workflow_version` is an `int`, and root `stores`, `plugin_instances`, `inputs`, `outputs`, or any other runtime registry are rejected. Executable structure lives only under `phases[].nodes` and `phases[].children`, while cross-phase wiring lives at `WorkflowConfig.edges`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:3] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:31] [decision: D-GR-22] [decision: D-GR-30]

The atomic node vocabulary is four types per D-GR-36: `AskNode`, `BranchNode`, `PluginNode`, and `ErrorNode`. `AskNode` uses `actor_ref` plus `prompt`; `BranchNode` uses `outputs: dict[str, BranchOutputPort]` with one `condition` expression per output port and optional `merge_function` for gather only; `PluginNode` is the explicit side-effect surface, so `artifact_key` is not part of the node schema. Universal typed ports use `PortDefinition` with `type_ref` XOR `schema_def`; only `BranchOutputPort` adds `condition`. `mode_config` is a single discriminated union, `HookPortEvent` is a distinct model, `WorkflowCostConfig` / `PhaseCostConfig` / `NodeCostConfig` replace the old shared `CostConfig`, and `/api/schema/workflow` is the only runtime schema source. `switch_function`, top-level `condition_type` / `condition` / `paths`, `context_text`, `fresh_sessions`, serialized `port_type`, and static `workflow-schema.json` as a runtime dependency are all rejected. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:17] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:24] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:101] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:108] [decision: D-GR-14] [decision: D-GR-16] [decision: D-GR-17] [decision: D-GR-35]

### Journey Annotations

#### J-6 — Define a nested declarative workflow from scratch

**Step Annotations:**
- Step 1: Authoring starts from the closed `WorkflowConfig` root. The top level carries versions, actors, phases, cross-phase edges, optional templates/plugins/types, and workflow cost metadata; it does not carry top-level nodes, root inputs/outputs, `stores`, or `plugin_instances`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:3] [decision: D-GR-30]
- Step 2: Actors serialize with `actor_type: agent | human` only. Agent actors carry provider/model/role/persistent/context semantics; human actors carry identity/channel semantics. `type: interaction` is rejected rather than treated as an alias. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:10] [decision: D-GR-30]
- Step 3: Every phase is authored as a `PhaseDefinition` with `children` as the only recursive field and a single `mode_config` union keyed by `mode`. Separate `map_config`, `fold_config`, `loop_config`, `sequential_config`, or `phases` aliases are invalid. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:31] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:38] [decision: D-GR-22]
- Step 4: General node and phase ports use ordered `PortDefinition` entries with `type_ref` XOR `schema_def`; YAML shorthand is desugared by the loader into the canonical port model before validation. Only branch outputs are keyed maps because the output key is itself the addressable port name. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:101] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:150] [decision: D-GR-16]
- Step 5: `AskNode` carries `actor_ref`, `prompt`, typed inputs/outputs/hooks, optional `context_keys`, and optional `NodeCostConfig`; prompt assembly is layered, but the only author-authored task text field is `prompt`. `output_type`, `input_type`, `input_schema`, `output_schema`, `task`, and `context_text` are all rejected in favor of typed ports. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:73] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:175] [decision: D-GR-14]
- Step 6: `BranchNode` is a gather-and-fan-out primitive. It may merge multiple inputs with optional `merge_function`, then evaluates each `BranchOutputPort.condition` independently; multiple outputs may fire. The node must define at least two outputs, and stale top-level `condition_type`, `condition`, `paths`, `output_field`, or `switch_function` fields are rejected. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:17] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:343] [decision: D-GR-35]
- Step 7: `PluginNode` is the explicit side-effect and external-capability surface. Persistence, storage, publication, or checkpoint behavior happens through plugins and edges, not implicit `artifact_key` writes or root registries. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:66] [decision: D-GR-14]
- Step 8: `EdgeDefinition` is the only serialized connection model. `source` and `target` use dot notation plus `$input` / `$output` boundary refs; hook-vs-data is inferred from the source port container; hook edges cannot define `transform_fn`; and serialized `port_type` is never valid. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:24] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:52] [decision: D-GR-22]
- Step 9: Validation runs before execution and enforces typed-port XOR rules, 10,000-character expression limits, stale-field rejection, nested containment, and cross-phase edge ownership. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:143] [decision: D-GR-17] [decision: D-GR-35]

**Error Path UX:** Validation errors identify the exact path and the exact rejected contract, for example: `actors.pm.actor_type must be 'agent' or 'human'`, `branches.review.switch_function is unsupported; use outputs.<port>.condition`, `phase.review.mode_config is required for mode='loop'`, `ports[1] must define exactly one of type_ref or schema_def`, or `artifact_key is not part of the declarative node contract`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115] [decision: D-GR-14] [decision: D-GR-35]

**Empty State UX:** The minimal valid starter is a `WorkflowConfig` with version fields, one actor, one sequential phase, and empty `nodes`, `children`, and `edges`. Ports may be omitted until needed, but any authored port must already satisfy the canonical typed-port contract. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:3] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:31]

**NOT Criteria:**
- The root document must NOT revive `stores`, `plugin_instances`, or root `inputs` / `outputs`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:3] [decision: D-GR-30]
- Authoring must NOT use `type: interaction`, `switch_function`, `condition_type`, `paths`, `context_text`, `artifact_key`, or serialized `port_type`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:10] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115] [decision: D-GR-14] [decision: D-GR-35]
- Phase definitions must NOT serialize nested phases under `phases` or mode details in four separate config fields. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:31] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:38]

#### J-7 — Translate `iriai-build-v2` planning and implementation patterns into the nested schema

**Step Annotations:**
- Step 1: Imperative planning, develop, and bugfix flows map to nested `PhaseDefinition` trees, not a flat workflow-level node graph. Iteration remains phase-owned through `map`, `fold`, and `loop`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:129] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:414] [decision: D-GR-22]
- Step 2: Review, approval, and retry logic map to `BranchNode.outputs` with per-port conditions and normal outgoing edges. Migration should preserve simultaneous fan-out when multiple branch predicates are true instead of forcing the old exclusive-route model. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:343] [decision: D-GR-35]
- Step 3: Human review steps serialize as `HumanActorDef`; migration does not preserve `interaction` as a wire value even if the imperative runtime still uses `InteractionActor` internally. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:10] [code: iriai-compose/iriai_compose/runner.py:53] [decision: D-GR-30]
- Step 4: Setup, artifact publication, checkpointing, or other side effects become explicit `PluginNode` invocations connected by ordinary data or hook edges; they do not piggyback on node completion fields. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:66] [decision: D-GR-14]
- Step 5: Templates used during migration are stored as full `TemplateDefinition` bodies with nested phase trees and typed inputs/outputs, so migrated reusable patterns stay saveable, importable, inline-creatable, and detachable. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:59] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:136]

**Error Path UX:** Migration failures stay concrete: `planning.review.paths is unsupported`, `actor_type interaction is unsupported`, `artifact_key is unsupported`, or `template body must be a full phase tree`. The validator does not silently coerce legacy shapes into ambiguous modern ones. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115] [decision: D-GR-14] [decision: D-GR-35]

**Empty State UX:** A migrated workflow may start as a skeletal phase tree with actors, types, templates, and plugins defined before all nodes are filled in; the document is still canonical as long as it uses the approved root shape and typed-port model. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:3] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:129]

**NOT Criteria:**
- Migration must NOT introduce extra atomic node types, fallback branch dialects, or runner-only registries to express existing workflows. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:17] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:129]
- Migration must NOT depend on implicit artifact writes or `TemplateRef`-only placeholders where a full reusable template definition is required. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:59] [decision: D-GR-14]

#### J-8 — Validation rejects stale or structurally invalid schema variants

**Step Annotations:**
- Step 1: Structural validation rejects flat top-level node placement, root `stores` / `plugin_instances`, missing `children`, mis-scoped cross-phase edges, and unresolved refs before any loader hydration occurs. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115] [decision: D-GR-22] [decision: D-GR-30]
- Step 2: Contract validation rejects stale actor and branch shapes: `type: interaction`, `switch_function`, `condition_type`, `condition`, top-level `paths`, and any attempt to treat branch routing as exclusive-by-default. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:10] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115] [decision: D-GR-35]
- Step 3: Port validation enforces that only `BranchOutputPort` may carry `condition`, while all other ports use plain `PortDefinition`; every typed port must define exactly one of `type_ref` or `schema_def`; and YAML shorthand must normalize to the same canonical result that verbose authoring would produce. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:101] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:150] [decision: D-GR-16]
- Step 4: Mode validation rejects `fresh_sessions` and any stale flat mode fields. Loop phases retain `condition_met` and `max_exceeded`; map/fold/loop behavior remains phase-owned rather than node-owned. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:38] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:45] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115]
- Step 5: Security validation rejects overlong expressions and keeps the sandbox contract explicit for branch conditions, edge transforms, and expression-backed mode settings. `merge_function` is not treated as a routing expression and is not a substitute for `switch_function`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:143] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:295] [decision: D-GR-17] [decision: D-GR-35]

**Error Path UX:** Failure copy names the stale field and the modern replacement, for example `PortDefinition.condition is unsupported; only BranchOutputPort.condition is valid`, `use phase.children instead of phase.phases`, or `replace output_type with outputs[].type_ref/schema_def`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:150] [decision: D-GR-35]

**Empty State UX:** Validation on a minimally populated workflow is quiet: empty `nodes`, `children`, `edges`, and optional template/plugin/type registries are acceptable so long as every present field is canonical. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:3] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:31]

**NOT Criteria:**
- Validation must NOT silently strip stale fields and continue. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115]
- Validation must NOT treat `merge_function` as a backdoor routing function or accept `PortDefinition.condition` outside branch outputs. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:143] [decision: D-GR-35]

#### J-9 — Composer consumes the live schema contract from `/api/schema/workflow`

**Step Annotations:**
- Step 1: The backend exports JSON Schema from `WorkflowConfig.model_json_schema()` and serves it at `/api/schema/workflow`; this endpoint, not a checked-in JSON file, is the composer-facing contract. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:108] [decision: D-GR-22]
- Step 2: The served schema must expose the current discriminators and data entities: closed workflow root, `actor_type`, `mode_config`, universal `PortDefinition`, `BranchOutputPort`, `TemplateDefinition`, and split workflow/phase/node cost config types. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:101] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:108] [decision: D-GR-35]
- Step 3: The editor derives inspectors and validation affordances from the live schema, including the branch-output map shape and the absence of `port_type`. Static `workflow-schema.json` remains build/test-only. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:108] [decision: D-GR-22]
- Step 4: Save/load round-trips must preserve the same contract the endpoint published; the frontend cannot maintain a second editor-only schema dialect. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:136] [decision: D-GR-30]

**Error Path UX:** Schema-source failures surface as contract failures, not silent fallback. A stale frontend schema that still expects `condition_type` / `paths`, `type: interaction`, or root registries is treated as broken integration. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:108] [decision: D-GR-22] [decision: D-GR-35]

**Empty State UX:** Even when no user workflow exists, the schema endpoint still returns the full current contract, including typed ports, branch output ports, mode configs, and validation rules. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:108]

**NOT Criteria:**
- Runtime consumers must NOT read `workflow-schema.json` as production truth. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:108] [decision: D-GR-22]
- The endpoint must NOT publish stale branch or actor variants just to preserve backward compatibility with rejected wire shapes. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:157] [decision: D-GR-30] [decision: D-GR-35]

#### J-10 — Author a loop with explicit success and safety-cap exits

**Step Annotations:**
- Step 1: Loop behavior is expressed on `PhaseDefinition`, not a dedicated node. `LoopModeConfig` carries the loop condition and optional iteration cap inside `mode_config`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:38] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:45]
- Step 2: The loop phase exposes `condition_met` and `max_exceeded` as independently routable phase outputs that participate in ordinary `EdgeDefinition` wiring. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:45] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:466]
- Step 3: Cross-phase loop exits live in `WorkflowConfig.edges`; intra-phase exits stay with the containing phase. No special control-flow collection is introduced just for loop routing. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:24] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:31] [decision: D-GR-22]

**Error Path UX:** Loop validation errors identify whether the failure is a missing `mode_config`, a malformed exit edge, or an invalid stale field such as `fresh_sessions`. The error names the loop phase path rather than flattening the loop into pseudo-node language. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:466]

**Empty State UX:** A loop may omit `max_iterations` and still remain canonical; `max_exceeded` stays part of the contract even when it is dormant. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:45] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:466]

**NOT Criteria:**
- Loop modeling must NOT reintroduce standalone loop nodes or flatten loop exits into ad hoc metadata. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:38] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:466]

#### J-11 — Composer detects schema-source drift or endpoint failure instead of silently using stale schema

**Step Annotations:**
- Step 1: If `/api/schema/workflow` is unavailable or malformed, consumers surface a schema-load failure rather than silently downgrading to a bundled file or a stale in-memory contract. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:480] [decision: D-GR-22]
- Step 2: Recovery is explicit: once the endpoint is healthy again, the consumer resumes against the live schema and the canonical branch/port model. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:488] [decision: D-GR-22]

**Error Path UX:** The visible failure is `schema source unavailable` or `schema contract drift detected`, not `fallback schema loaded`. This keeps stale field families from lingering in the editor. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:480] [decision: D-GR-30]

**Empty State UX:** There is no alternate empty-state schema source. A healthy endpoint always returns the current canonical contract; an unhealthy endpoint is a hard contract error. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:108] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:480]

**NOT Criteria:**
- Consumers must NOT continue authoring against a stale schema bundle after endpoint recovery. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:488] [decision: D-GR-22]

#### J-12 — Migration output using stale hook or branch fields fails fast and is corrected

**Step Annotations:**
- Step 1: Migration output is canonical only if it uses `children`, `mode_config`, `actor_type`, ordinary edges for hooks, explicit typed ports, and `BranchNode.outputs` with per-port conditions. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:496] [decision: D-GR-22] [decision: D-GR-35]
- Step 2: Validation rejects stale translation output containing `switch_function`, `condition_type`, `condition`, top-level `paths`, `port_type`, `artifact_key`, `context_text`, or `type: interaction`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:496] [decision: D-GR-14] [decision: D-GR-35]
- Step 3: Corrected migration output reuses the same `WorkflowConfig` contract the loader, editor, and runtime schema endpoint already understand. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:157] [decision: D-GR-30]

**Error Path UX:** Migration errors are prescriptive. They name the stale field and the replacement surface, for example `replace branch.paths with branch.outputs.<port>` or `replace actor.type with actor.actor_type`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115] [decision: D-GR-35]

**Empty State UX:** A partially migrated workflow can remain in-progress so long as every committed field is canonical; the schema does not support a tolerated legacy subset. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:122] [decision: D-GR-30]

**NOT Criteria:**
- Migration must NOT preserve a private compatibility dialect that only one downstream tool can read. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:157] [decision: D-GR-30]

### Component Vocabulary

#### CMP-1: WorkflowConfig
- **Status:** new
- **Location:** `iriai_compose/schema/workflow.py`
- **Description:** Canonical workflow envelope. Owns only the approved root fields, top-level `phases`, cross-phase `edges`, reusable `TemplateDefinition` entries, plugin/type registries, and `WorkflowCostConfig`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:3] [decision: D-GR-30]
- **Fields / Variants:** `schema_version: str`, `workflow_version: int`, `name`, `description?`, `metadata?`, `actors`, `phases`, `edges`, `templates?`, `plugins?`, `types?`, `cost_config?`
- **States:** `valid-root`, `schema-export`, `validation-error`

#### CMP-2: ActorDefinition
- **Status:** new
- **Location:** `iriai_compose/schema/actors.py`
- **Description:** Discriminated union for workflow actors. `AgentActorDef` includes provider/model/role/persistent/context data; `HumanActorDef` includes identity/channel data. `persistent` defaults to `false` when omitted. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:10] [decision: D-GR-30]
- **Fields / Variants:** `actor_type: 'agent' | 'human'`, agent fields, human fields
- **States:** `agent`, `human`, `validation-error`

#### CMP-3: PortDefinition
- **Status:** new
- **Location:** `iriai_compose/schema/ports.py`
- **Description:** Universal typed port model for node and phase `inputs`, `outputs`, and `hooks`. The canonical in-memory model is ordered and uses `type_ref` XOR `schema_def`; loader shorthand is normalized into this form before validation. `PortDefinition` does not carry `condition`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:101] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:150] [decision: D-GR-16]
- **Fields / Variants:** `name`, `type_ref?`, `schema_def?`, `description?`, `required?`
- **States:** `typed-by-ref`, `typed-by-inline-schema`, `shorthand-normalized`, `validation-error`

#### CMP-4: BranchOutputPort
- **Status:** new
- **Location:** `iriai_compose/schema/ports.py`
- **Description:** Specialized output-port model used only inside `BranchNode.outputs`. Each keyed port carries its own `condition` expression and the same XOR typing contract as any other port. The parent map key is the emitted port name. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:101] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:343] [decision: D-GR-35]
- **Fields / Variants:** `condition`, `type_ref?`, `schema_def?`, `description?`
- **States:** `active-port`, `inactive-port`, `validation-error`

#### CMP-5: AskNode
- **Status:** new
- **Location:** `iriai_compose/schema/nodes.py`
- **Description:** Atomic actor invocation node. Uses `actor_ref`, `prompt`, typed ports, optional `context_keys`, and optional `NodeCostConfig`; it does not use `task`, `context_text`, or `output_type` fields. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:17] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:73] [decision: D-GR-14]
- **Fields / Variants:** `type: 'ask'`, `id`, `name`, `actor_ref`, `prompt`, `inputs`, `outputs`, `hooks`, `context_keys?`, `cost?`
- **States:** `configured`, `response-port-wired`, `validation-error`

#### CMP-6: BranchNode
- **Status:** new
- **Location:** `iriai_compose/schema/nodes.py`
- **Description:** Gather-and-fan-out node. Accepts multiple typed inputs, optional `merge_function`, and a keyed `outputs` map of `BranchOutputPort` entries. Multiple branch outputs may fire in one evaluation pass. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:17] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:343] [decision: D-GR-35]
- **Fields / Variants:** `type: 'branch'`, `id`, `name`, `inputs`, `outputs`, `hooks`, `merge_function?`, `context_keys?`, `cost?`
- **States:** `gather`, `fan-out`, `validation-error`

#### CMP-7: PluginNode
- **Status:** new
- **Location:** `iriai_compose/schema/nodes.py`
- **Description:** Explicit external-capability node. References a plugin interface and config payload; all persistence and side effects stay explicit at this layer rather than being hidden in generic node fields. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:66] [decision: D-GR-14]
- **Fields / Variants:** `type: 'plugin'`, `id`, `name`, `plugin_ref`, `config?`, `inputs`, `outputs`, `hooks`, `context_keys?`, `cost?`
- **States:** `configured`, `edge-wired`, `validation-error`

#### CMP-8: PhaseDefinition
- **Status:** new
- **Location:** `iriai_compose/schema/phases.py`
- **Description:** Primary execution container. Owns ordered node lists, recursive `children`, local `edges`, typed phase ports, `mode_config`, `context_keys`, optional metadata, and `PhaseCostConfig`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:31] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:38]
- **Fields / Variants:** `id`, `name`, `mode`, `mode_config`, `inputs`, `outputs`, `hooks`, `nodes`, `children`, `edges`, `context_keys?`, `metadata?`, `cost?`
- **States:** `sequential`, `map`, `fold`, `loop`, `validation-error`

#### CMP-9: EdgeDefinition
- **Status:** new
- **Location:** `iriai_compose/schema/edges.py`
- **Description:** Single serialized edge type for data flow and hooks. Uses `source` / `target` refs plus optional `transform_fn`; hook semantics are inferred and hook edges may not transform payloads. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:24] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:52] [decision: D-GR-22]
- **Fields / Variants:** `source`, `target`, `transform_fn?`, `description?`
- **States:** `data-edge`, `hook-edge`, `cross-phase-edge`, `validation-error`

#### CMP-10: TemplateDefinition
- **Status:** new
- **Location:** `iriai_compose/schema/templates.py`
- **Description:** Reusable full-body template definition, not a lightweight pointer. Stores typed template inputs/outputs, actor slots, and a nested root phase tree so templates can round-trip through save/import/inline-create/detach flows. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:59] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:136]
- **Fields / Variants:** `id`, `name`, `description?`, `inputs`, `outputs`, `actor_slots?`, `root_phase`
- **States:** `library-definition`, `inline-use`, `validation-error`

#### CMP-11: HookPortEvent
- **Status:** new
- **Location:** `iriai_compose/schema/ports.py`
- **Description:** Supporting event model for hook execution observability. It records hook source, event kind, status, timing, optional cost, and error without introducing a separate author-authored hook section in workflow YAML. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:567] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:24] [decision: D-GR-22]
- **Fields / Variants:** `source_ref`, `event`, `status`, `timestamp`, `duration_ms?`, `cost_usd?`, `error?`, `result?`
- **States:** `started`, `completed`, `failed`

#### CMP-12: CostConfig Suite
- **Status:** new
- **Location:** `iriai_compose/schema/costs.py`
- **Description:** Three scope-specific pricing models: `WorkflowCostConfig`, `PhaseCostConfig`, and `NodeCostConfig`. These replace the old single shared `CostConfig` and keep cost metadata attached at the same granularity as execution structure. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:87] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:514] [decision: D-GR-35]
- **Fields / Variants:** workflow caps/alerts, phase caps/alerts, node unit-cost overrides
- **States:** `workflow-scope`, `phase-scope`, `node-scope`

### Verifiable States

#### CMP-1 (WorkflowConfig) States

| State | Recognizable Description |
|-------|--------------------------|
| valid-root | Root object contains only approved keys, `workflow_version` is numeric, `phases` is the only structural entry point for executable content, and `edges` contains only workflow-scope cross-phase connections. |
| schema-export | `model_json_schema()` output includes `actor_type`, `mode_config`, `BranchOutputPort`, `schema_def`, and split cost-config types; it does not expose `switch_function`, `port_type`, or root `stores`. |
| validation-error | Validation reports a rejected root key, wrong `workflow_version` type, or any attempt to move nodes, inputs, or outputs to the root. |

**Citations:** [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:3] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:108] [decision: D-GR-30]

#### CMP-3 / CMP-4 (PortDefinition / BranchOutputPort) States

| State | Recognizable Description |
|-------|--------------------------|
| typed-by-ref | Port defines `type_ref` only and resolves through the workflow type registry. |
| typed-by-inline-schema | Port defines `schema_def` only and participates in type-flow validation without a named type. |
| shorthand-normalized | Author-authored YAML shorthand has been expanded into canonical `PortDefinition` or `BranchOutputPort` data before validation or schema export. |
| validation-error | Port defines both typing mechanisms, neither typing mechanism, or `condition` outside a branch output port. |

**Citations:** [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:101] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:150] [decision: D-GR-16]

#### CMP-6 (BranchNode) States

| State | Recognizable Description |
|-------|--------------------------|
| gather | Branch node has multiple inputs and optional `merge_function`; the merged payload is evaluated once against each output-port condition. |
| fan-out | `outputs` contains at least two named `BranchOutputPort` entries and one evaluation may activate multiple named output ports. |
| validation-error | Node defines `switch_function`, stale top-level `condition_type` / `condition` / `paths`, fewer than two outputs, or an edge references an unknown branch output key. |

**Citations:** [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:17] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:343] [decision: D-GR-35]

#### CMP-8 (PhaseDefinition) States

| State | Recognizable Description |
|-------|--------------------------|
| sequential | `mode: sequential` with no parallel or accumulator semantics beyond ordered local execution. |
| map | `mode: map` and `mode_config` supplies collection + concurrency settings; no standalone map node exists. |
| fold | `mode: fold` and `mode_config` supplies collection + accumulator setup; child phases remain nested under `children`. |
| loop | `mode: loop` and the phase exposes `condition_met` plus `max_exceeded` as routable outputs. |
| validation-error | Phase uses `phases` instead of `children`, stale flat mode config fields, or loop metadata outside the canonical `mode_config` contract. |

**Citations:** [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:31] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:38] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:45]

#### CMP-9 (EdgeDefinition) States

| State | Recognizable Description |
|-------|--------------------------|
| data-edge | `source` resolves to a normal output port and the edge may optionally define `transform_fn`. |
| hook-edge | `source` resolves to `on_start` or `on_end`; the edge uses the same serialized shape as any other edge but cannot define `transform_fn`. |
| cross-phase-edge | Edge is serialized at `WorkflowConfig.edges` because its endpoints span phase boundaries. |
| validation-error | Edge includes `port_type`, malformed refs, a hook transform, or is stored in the wrong ownership scope. |

**Citations:** [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:24] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115] [decision: D-GR-22]

#### CMP-10 (TemplateDefinition) States

| State | Recognizable Description |
|-------|--------------------------|
| library-definition | Template stores a complete reusable nested phase tree plus typed template IO and actor slots. |
| inline-use | Workflow references a stored template body without changing the underlying schema dialect. |
| validation-error | Template body is reduced to a pointer-only stub, omits its phase tree, or uses stale node or port fields internally. |

**Citations:** [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:59] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:136]

### Cross-Cutting Design Notes

- **Validation and security:** Expression-backed fields are bounded by the schema contract itself: 10,000-character limit, sandboxed evaluation expectations, and explicit stale-field rejection. That includes branch output conditions, edge transforms, and expression-backed phase configs; it does not turn `merge_function` into a routing expression. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:143] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:295] [decision: D-GR-17] [decision: D-GR-35]
- **Backward compatibility:** SF-1 remains additive to the imperative API. The declarative package introduces new models and loaders without changing the existing runtime ABC shape such as `AgentRuntime.invoke(...)`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:122] [code: iriai-compose/iriai_compose/runner.py:36] [decision: D-GR-31]
- **Responsive behavior:** No direct end-user responsive surface is introduced in SF-1. The only frontend-facing contract is the live schema endpoint and its explicit failure mode. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:108] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:480]
- **Accessibility:** SF-1 has no direct UI, but its error codes and schema field names must stay stable and explicit so downstream editors can announce validation failures accessibly and map stable selectors to real schema concepts. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:115] [decision: D-GR-30]

### Alternatives Considered

1. **Rejected:** Keep the old exclusive branch model with top-level `condition_type`, `condition`, and `paths`, or reintroduce `switch_function`. Rejected because D-GR-35 makes per-port `outputs` plus non-exclusive fan-out authoritative. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:343] [decision: D-GR-35]
2. **Rejected:** Allow `PortDefinition.condition` everywhere. Rejected because only `BranchOutputPort` owns branch predicates; general ports stay purely typed. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:101] [decision: D-GR-35]
3. **Rejected:** Preserve root `stores`, `plugin_instances`, or node-level `artifact_key` as convenience surfaces. Rejected because the root is closed and side effects are explicit plugin behavior. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:3] [decision: D-GR-14] [decision: D-GR-30]
4. **Rejected:** Keep four separate mode-config fields or `PhaseDefinition.phases`. Rejected because the canonical phase contract is `children` plus a single discriminated `mode_config`. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:31] [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:38]
5. **Rejected:** Treat `workflow-schema.json` as a production fallback. Rejected because `/api/schema/workflow` is the only runtime contract surface. [code: .iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:108] [decision: D-GR-22]


---

## Subfeature: DAG Loader & Runner (dag-loader-runner)

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner

SF-2 is the contract-enforcement boundary between the YAML that SF-6 saves, the validation/API surfaces that SF-5 exposes, and the runtime entrypoints inside `iriai-compose`. This revision keeps the D-GR-22 nested-YAML contract, but replaces the stale trust-the-author posture with explicit guarded ingress and shared execution security: Ask nodes use `prompt`, Branch nodes use the D-GR-35 per-port `outputs` model with optional `merge_function`, and `run()`, `validate()`, and `load_workflow()` all depend on the same safe loader path instead of ad hoc parsing or bare `exec()` evaluation. [decision: D-GR-22] [decision: D-GR-35] [decision: D-GR-38] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:87] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:121] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:103]

The design adds five backend contract components that the stale plan/system-design were missing or mis-modeled: CMP-7 `ExpressionSandbox`, CMP-8 `LoaderInputGuard`, CMP-9 `BranchRouteEvaluator`, CMP-10 `PluginCapabilityScope`, and CMP-11 `ValidationErrorEnvelope`. Together they close the bare-`exec()` gap, block unsafe path and YAML ingress, align branch execution to non-exclusive per-port fan-out, scope plugin execution by capability and trust policy, and ensure every external failure is coded and sanitized before it crosses the SF-2 -> SF-5/SF-6 boundary. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:440] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:464] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:485] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:108] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:109] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:110]

<!-- SF: dag-loader-runner -->
## D-GR Compliance Checklist

| Decision | Status | Notes |
|----------|--------|-------|
| D-GR-17 | compliant | One 10,000-character expression limit is enforced at validation and execution ingress through CMP-7. |
| D-GR-22 | compliant | Nested `phases[].nodes` and `phases[].children`, ordinary-edge hook serialization, and `/api/schema/workflow` remain authoritative. |
| D-GR-23 | compliant | `AgentRuntime.invoke()` stays unchanged; SF-2 propagates node identity through runner-managed `ContextVar` state and hierarchical context merge order. |
| D-GR-35 | compliant | Branch execution uses per-port `outputs` with optional `merge_function` and non-exclusive fan-out; stale `condition_type` / `condition` / `paths` remain rejected. |
| D-GR-38 | compliant | Bare `exec()` is rejected; every executable expression flows through CMP-7 with AST allowlist, blocked builtins, size check, and timeout. |
| D-GR-42 | compliant | This artifact treats the D-GR log as canonical and records explicit alignment for the SF-2 decisions touched by this revision. |

<!-- SF: dag-loader-runner -->
### J-1 - SF-2: DAG Loader & Runner

**Step Annotations:**
- Step 1 uses CMP-8 `LoaderInputGuard` for both `validate()` and `run()`: normalize `str | Path` inputs, canonicalize against caller-approved roots, reject path traversal and symlink escape, then enforce YAML document-size, alias-expansion, and nested-phase-depth guards before model hydration. Safe ingress is identical for validation-only and runtime entrypoints. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:87] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:108] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:111] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:124] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:127]
- Step 2 hydrates only the canonical SF-2 wire shape: Ask nodes use `prompt`, not `task`; Branch nodes expose `outputs: dict[str, BranchOutputPort]`; `switch_function`, top-level `condition`, `condition_type`, `paths`, separate hook sections, and serialized `port_type` are rejected with coded validation records instead of silently normalized. [decision: D-GR-35] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:105] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:435] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:440]
- Step 3 routes every executable field through CMP-7 `ExpressionSandbox`: `BranchOutputPort.condition`, `BranchNode.merge_function`, data-edge `transform_fn`, and expression-backed phase mode config all get AST validation, blocked builtin and dunder checks, a 10,000-character precheck, and hard 5-second timeout enforcement before they can influence execution. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:121] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:469] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:105] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:106] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:107] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:121]
- Step 4 executes plugin nodes only through CMP-10 `PluginCapabilityScope`: plugins receive a minimal scoped context rather than raw runner/session/artifact/service handles, and entry-point discovery is gated by explicit allowlist or equivalent trust verification before a plugin becomes routable from workflow data. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:147] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:464] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:109] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:113] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:125]
- Step 5 emits outward-facing failures only through CMP-11 `ValidationErrorEnvelope`, so downstream API/editor consumers always receive `code`, `field_path` or node context, `message`, and `severity` without raw traceback frames or host-specific filesystem leakage. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:156] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:485] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:110] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:115]

**Error Path UX:** Validation and runtime failures remain actionable but scrubbed: `ValidationErrorEnvelope` identifies the failing field, branch port, path input, or plugin trust boundary with a stable `code`, while raw Python tracebacks, local values, and internal paths are stripped before the payload leaves SF-2. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:156] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:485]

**Empty State UX:** An empty workflow or an empty phase container still fails structural validation. SF-2 does not invent a flat root graph, default branch path, or placeholder phase in order to make an unsafe or non-canonical document executable. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:87]

**NOT Criteria:**
- SF-2 must NOT evaluate any workflow expression through bare `exec()`.
- SF-2 must NOT accept `task` on AskNode or `condition_type` / `condition` / `paths` on BranchNode as backward-compatible aliases.
- SF-2 must NOT read arbitrary filesystem paths outside allowed workflow roots.
- SF-2 must NOT provide plugins unrestricted runner, store, session, or service access.
- SF-2 must NOT forward unsanitized tracebacks to SF-5 or SF-6.

<!-- SF: dag-loader-runner -->
### J-2 - SF-2: DAG Loader & Runner

**Step Annotations:**
- Step 1 executes nested phases recursively from the authoritative `phases[].nodes` plus `phases[].children` structure, but now with an explicit max-depth guard applied before execution begins. The runner never flattens child phases into a global node list and never recurses indefinitely through malformed nesting. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:71] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:87] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:112]
- Step 2 routes branch execution through CMP-9 `BranchRouteEvaluator`: gather from one or more typed inputs, optionally apply `merge_function` inside CMP-7, then evaluate each output-port `condition` independently. Every truthy port may fire in the same execution; no implicit exclusive-routing fallback is synthesized when multiple conditions match. [decision: D-GR-35] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:440] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:446]
- Step 3 keeps hook edges inside the same ordinary-edge model as data edges. Hook-vs-data is inferred from the resolved source port container, hook edges may not carry `transform_fn`, and cross-phase routing still respects phase boundaries instead of bypassing them as a special execution channel. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:458] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:105]
- Step 4 preserves internal observability through `ExecutionResult` and `ExecutionHistory`, including which branch output ports fired, while keeping any externally surfaced derivative of that history behind CMP-11 sanitization rules. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:113] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:480]

**Error Path UX:** Nested execution failures identify the closest failing phase path and the specific branch port, hook edge, or plugin boundary that failed, but the external payload remains coded and sanitized so callers can distinguish `unsafe_expression`, `path_not_allowed`, `yaml_resource_limit`, `untrusted_plugin`, and `stale_branch_field` cases without internal stack leakage. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:485] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:110]

**Empty State UX:** A phase with no executable `nodes` and no `children` is invalid configuration, and a BranchNode with zero matching output-port conditions is treated as an explicit no-match runtime outcome rather than as a hidden default branch. [decision: D-GR-35] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:440]

**NOT Criteria:**
- The runner must NOT flatten `children` into the workflow root before validation or execution.
- Branch execution must NOT revert to exclusive `condition_type` / `condition` / `paths` routing.
- `merge_function` must NOT run outside CMP-7.
- Hook edges must NOT carry `transform_fn`.
- Phase recursion must NOT proceed past the configured depth guard.

<!-- SF: dag-loader-runner -->
### J-3 - SF-2: DAG Loader & Runner

**Step Annotations:**
- Step 1 keeps `validate()` runtime-free, but not weaker than `run()`: it traverses the same CMP-8 guarded ingress path, validates the same nested structure, and applies the same stale-field rejection and AST inspection rules before any runtime hydration can occur. `run()` is therefore a strict superset of `validate()` rather than a second permissive loader. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:87] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:105] [decision: D-GR-38]
- Step 2 treats validation as a first-class contract surface for downstream consumers: path-input failures, YAML resource limits, stale Ask/Branch shapes, missing typed hook ports, unknown branch output refs, unsafe expressions, and untrusted plugins all map to stable validation codes instead of generic string messages. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:105] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:147] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:485]
- Step 3 keeps `/api/schema/workflow` as the only composer-facing schema source. SF-2 never treats a bundled `workflow-schema.json` as an execution-time authority, so schema drift is surfaced as an endpoint failure or validation error instead of being hidden behind stale local metadata. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:353]

**Error Path UX:** Validation responses are machine-readable and editor-safe: the payload always includes `code`, `field_path`, `message`, and `severity`, and error text is written for direct display in SF-5/SF-6 without raw traceback cleanup work delegated downstream. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:156] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:485]

**Empty State UX:** N/A - validation surface only.

**NOT Criteria:**
- `validate()` must NOT require live runtimes, plugin execution, or agent invocation in order to reject an unsafe document.
- Validation must NOT silently rewrite deprecated fields into the canonical model.
- Validation must NOT omit the `code` field from surfaced errors.
- Composer boot must NOT fall back to a stale local schema bundle when `/api/schema/workflow` is unavailable.

<!-- SF: dag-loader-runner -->
### CMP-7: ExpressionSandbox

- **Status:** new
- **Location:** `iriai_compose/declarative/sandbox.py`
- **Description:** Shared evaluator and validator for every executable workflow expression. Owns AST allowlist walking, blocked builtin and dunder-attribute rejection, the 10,000-character precheck, and hard 5-second timeout enforcement. This component is the single source of truth for `BranchOutputPort.condition`, `BranchNode.merge_function`, data-edge `transform_fn`, and expression-backed phase-mode config; no other helper is allowed to execute workflow-authored Python directly.
- **Props/Variants:** `expression: str`, `scope: branch_condition | merge_function | transform_fn | mode_config`, `bindings: dict[str, Any]`, `timeout_seconds=5.0`, `max_chars=10000`
- **States:** ready, unsafe_ast, size_exceeded, timeout, evaluation_error
- **Citations:** [decision: D-GR-38] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:121] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:469] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:105] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:106] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:107] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:121]

<!-- SF: dag-loader-runner -->
### CMP-8: LoaderInputGuard

- **Status:** new
- **Location:** `iriai_compose/declarative/loader.py`
- **Description:** Shared ingress guard used by `load_workflow()`, `validate()`, and `run()` whenever the caller supplies raw text or a path-like input. It canonicalizes paths against approved roots, rejects traversal and symlink escape, enforces YAML document-size and alias-expansion limits before parse, and rejects phase trees that exceed configured nesting depth.
- **Props/Variants:** `source: WorkflowConfig | str | Path`, `allowed_roots`, `max_document_bytes`, `max_aliases`, `max_phase_depth`
- **States:** allowed, blocked_path, document_too_large, alias_limit_exceeded, depth_limit_exceeded
- **Citations:** [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:87] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:464] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:108] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:111] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:124] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:127]

<!-- SF: dag-loader-runner -->
### CMP-9: BranchRouteEvaluator

- **Status:** new
- **Location:** `iriai_compose/declarative/executors.py`
- **Description:** Runtime helper that makes the D-GR-35 BranchNode contract concrete. It gathers one or more typed inputs, optionally applies `merge_function` through CMP-7, evaluates each output-port `condition` independently, and returns the full set of fired output port names for downstream edge dispatch. No exclusive routing mode, top-level `condition`, or `paths` compatibility layer exists inside this evaluator.
- **Props/Variants:** `inputs: dict[str, Any]`, `outputs: dict[str, BranchOutputPort]`, `merge_function: str | None`
- **States:** single_match, multi_match, no_match, error
- **Citations:** [decision: D-GR-35] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:440] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:446] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:114]

<!-- SF: dag-loader-runner -->
### CMP-10: PluginCapabilityScope

- **Status:** new
- **Location:** `iriai_compose/declarative/plugins.py`
- **Description:** Capability envelope passed into plugin execution. It limits each plugin node to the minimum runtime services explicitly granted by configuration or plugin category, and it pairs execution scoping with entry-point trust enforcement so discovered plugins are inert until they satisfy the configured allowlist or equivalent trust policy.
- **Props/Variants:** `plugin_name`, `allowed_capabilities`, `trusted_plugin_ids`, `scoped_artifacts`, `scoped_sessions`, `scoped_services`
- **States:** scoped, missing_capability, untrusted_plugin
- **Citations:** [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:147] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:464] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:109] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:113] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:125]

<!-- SF: dag-loader-runner -->
### CMP-11: ValidationErrorEnvelope

- **Status:** new
- **Location:** `iriai_compose/declarative/validation.py` and `iriai_compose/declarative/errors.py`
- **Description:** Structured outward-facing error contract for both validation and execution failures. The envelope makes `code` mandatory, preserves actionable `field_path` or node context plus `severity`, and applies sanitization before the payload is exposed to API or editor consumers. This is the design source for the system-design fix that adds `code` to `validation_error` and strips raw tracebacks from external responses.
- **Props/Variants:** `code`, `field_path`, `message`, `severity`, `node_id | None`, `phase_path | None`
- **States:** validation_error, execution_error, sanitized
- **Citations:** [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:156] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:485] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:110] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:115]

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner

N/A for rendered responsive UI. The only user-facing presentation contract in SF-2 is the shape of the schema and error payloads consumed by SF-5 and SF-6: `/api/schema/workflow` stays authoritative, and surfaced failures stay coded, field-scoped, and sanitized so downstream UI can announce them accessibly without reverse-engineering raw exceptions. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:353] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:156]

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner

1. Bare `exec()` with restricted `__builtins__` only - rejected because PRD REQ-62 and the cycle-7 review require AST allowlist validation, size limits, and timeout enforcement before execution begins. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:121] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:105]
2. Exclusive BranchNode routing through `condition_type` / `condition` / `paths` - rejected because D-GR-35 makes per-port `outputs` plus non-exclusive fan-out authoritative and the stale shape must fail validation, not remain as a compatibility mode. [decision: D-GR-35] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:440]
3. Trusting arbitrary `Path` inputs and unbounded YAML documents - rejected because `run()` and `validate()` must share guarded ingress with traversal, symlink, size, alias, and depth protection. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:87] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:108] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:127]
4. Giving plugins raw runner/session/artifact access and auto-loading every installed entry point - rejected because REQ-65 requires capability scoping and trust-bounded discovery before plugins become executable from workflow data. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:147] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:109] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:113]
5. Returning raw tracebacks in validation or execution payloads - rejected because REQ-66 requires stable codes plus sanitized messages only. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:156] [code: .iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:110]

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner

This revision keeps SF-2 additive to the existing imperative runtime surface: `AgentRuntime.invoke()` remains unchanged, concurrent execution still builds on the existing runner model, and composer-facing schema delivery still comes from live `model_json_schema()` output instead of a bundled snapshot. The meaningful change is at the trust boundary: one guarded loader path in, one auditable expression sandbox in the middle, one coded and sanitized error envelope out. That is the design basis the SF-2 plan and system design need in order to replace stale BranchNode routing, add `validation_error.code`, and close the current security gaps without inventing new workflow semantics. [decision: D-GR-23] [decision: D-GR-38] [code: iriai-compose/iriai_compose/runner.py:36] [code: iriai-compose/iriai_compose/runner.py:42] [code: iriai-compose/iriai_compose/runner.py:106] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:353]


---

## Subfeature: Testing Framework (testing-framework)

<!-- SF: testing-framework -->
### SF-3: Testing Framework

SF-3 is a Python testing package, not an end-user UI. This rewrite resets the design to the current canonical runtime/schema contract so the plan and system design can be rewritten without stale interfaces. The package centers on fluent mock runtimes, builder/fixture helpers that emit canonical nested workflows with dict-keyed ports, assertion helpers over `ExecutionResult` + `ExecutionHistory`, snapshot helpers, and a thin `run_test()` wrapper that mirrors SF-2 instead of defining a parallel ABI.

The contract is fixed here: `AgentRuntime.invoke()` stays unchanged, current node identity is resolved from the runner-owned `_current_node` `ContextVar`, prompt context arrives in `workflow -> phase -> actor -> node` order, `run_test()` delegates to `run(workflow, config, *, inputs=None)`, convenience human interaction wiring uses the existing `"human"` runtime key, and checkpoint/resume is out of scope for SF-3. Legacy `MockRuntime`, `MockInteraction`, dict-based constructors, list-based port containers, and any `history=` / resume surface are explicitly rejected. The stale `node_id`-kwarg decision is removed outright, and `D-SF3-16` remains retired rather than being reassigned.

**Citations:**
- [decision] `D-GR-23` — unchanged `invoke()`, ContextVar node propagation, canonical merge order.
- [decision] `D-GR-24` — observability stays in `ExecutionResult` / `ExecutionHistory`; checkpoint/resume is outside the core SF-2 contract.
- [code] `iriai-compose/iriai_compose/runner.py:36-59` — existing production ABCs already exclude a `node_id` kwarg.
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:3-47` — REQ-65 through REQ-70 define the mandatory SF-3 contract.
- [code] `.iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:268-272` — Cycle 7 records the stale `node_id` kwarg, dict-constructor API, and missing ContextVar usage that this rewrite removes.

### J-1 — SF-3: Testing Framework

**PRD Reference:** `J-18 — Run a Node-Aware Test Against the Published SF-2 ABI`

**Step Annotations:**
- Step 1 — Configure mocks: `MockAgentRuntime()` is always created with no constructor args. Agent matching is authored through `.when_node()`, `.when_role(prompt=...)`, `.when_role()`, and `.default_response()` with fixed priority `node > role+prompt > role-only > default`. `MockInteractionRuntime()` and `MockPluginRuntime()` use the same fluent, no-arg pattern for human and plugin paths.
- Step 2 — Execute: `run_test()` assembles `RuntimeConfig` and delegates directly to `run(workflow, config, *, inputs=None)`. The convenience `interaction=` argument maps to `interaction_runtimes={"human": interaction}` to match the existing runner/test vocabulary. Node-aware routing in agent, interaction, and plugin mocks reads `_current_node` during `invoke()` / `resolve()` / plugin execution instead of widening those signatures.
- Step 3 — Assert: execution helpers read `ExecutionResult` plus `result.history` only. Phase-mode helpers are `assert_loop_iterations()`, `assert_fold_items_processed()`, `assert_map_fan_out()`, and `assert_error_routed()`. `respond_sequence()` exhaustion raises `MockExhaustedError` rather than silently recycling responses. Cost assertions consume the `with_cost()` metadata attached to the matched mock call.

**Error Path UX:** Unmatched calls raise `MockConfigurationError` that lists the current node id, role, prompt excerpt, and configured matchers. Exhausted sequences raise `MockExhaustedError` with the matcher id and exhausted call index. `raise_error()` and `then_crash()` surface the configured exception unchanged so failure-path tests stay explicit.

**Empty State UX:** A test with only `default_response()` configured still executes deterministically and records that the default matcher won. If no matcher or default exists, the first unmatched call fails immediately with a configuration error instead of returning `None`.

**NOT Criteria:**
- `AgentRuntime.invoke()` must NOT gain `node_id`.
- `run_test()` must NOT accept `history`, `resume`, or any checkpoint surface.
- Convenience human interaction wiring must NOT use `"default"` as the runtime key.
- Builder fixtures must NOT emit list-based `inputs`, `outputs`, or `hooks`.

**Citations:**
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:53-98` — AC-51 through AC-56 define the expected success and failure behavior.
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:104-131` — J-18 and J-19 are the journey-level contract this design annotates.
- [code] `iriai-compose/tests/test_runner.py:45-49` — existing runner tests use `"human"` as the interaction-runtime key.
- [code] `iriai-compose/iriai_compose/pending.py:9-21` — `Pending` currently carries no `node_id`, so node-aware interaction matching must come from runner context rather than a new payload field.

### J-2 — SF-3: Testing Framework

**PRD Reference:** `J-19 — Remove Stale Consumer Assumptions Before Implementation`

**Step Annotations:**
- Step 1 — Contract audit: before implementation starts, review plan and system design for the stale `invoke(..., node_id=...)` blocks, any reused `D-SF3-16` identifier, any `RuntimeConfig(history=...)` or `run_test(..., history=...)` path, any `"default"` human-runtime key, and any list-based port containers.
- Step 2 — Rewrite surface: export only `MockAgentRuntime`, `MockInteractionRuntime`, and `MockPluginRuntime`; place CMP-1 and CMP-2 in `iriai_compose/testing/mock_runtime.py`; place CMP-3 in `iriai_compose/testing/mock_plugin.py`; align builder, assertion, and runner sections to dict-keyed ports and `ExecutionHistory`-based phase metrics.
- Step 3 — Gate before implementation: do not proceed to STEP-20 until the contract audit passes and plan/system design explicitly show the 4-strategy matcher priority, `MockExhaustedError`, the new phase-mode assertion helpers, and `run_test()` delegating to the canonical SF-2 signature.

**Error Path UX:** Contract drift is treated as a blocking design failure, not a warning. The gate result must list each remaining stale assumption with file or section reference so the architect can correct it before coding starts.

**Empty State UX:** When the audit passes, the gate result is a short checklist confirming ABI alignment, mock naming, port shape, interaction key, and no-resume scope.

**NOT Criteria:**
- The stale `node_id`-kwarg decision must NOT remain anywhere in the implementation artifacts.
- `D-SF3-16` must NOT remain in the plan or be reused for a different decision.
- Plan and system design must NOT mix current and legacy class names.
- Implementation must NOT start while any stale ABI text remains.

**Citations:**
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:27-32` — REQ-68 requires removal of the stale `node_id` decision and ABC block.
- [code] `.iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:275` — Cycle 7 documents the `D-SF3-16` ID collision across PRD and system design.
- [code] `.iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:281-282` — Cycle 7 documents the wrong interaction-runtime key and missing PRD journey traceability.
- [code] `.iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:309-315` — Cycle 7 requires a verification gate before STEP-20 so stale ABI text cannot survive into implementation.

## Component Hierarchy

```text
iriai_compose.testing
├── __init__.py
├── mock_runtime.py       # CMP-1, CMP-2
├── mock_plugin.py        # CMP-3
├── fixtures.py           # CMP-4
├── assertions.py         # CMP-5
├── snapshot.py           # CMP-6
├── base.py               # CMP-7
├── runner.py             # CMP-8
└── validation.py         # SF-1 validation re-exports only
```

**State requirements:**
- CMP-1, CMP-2, and CMP-3 depend on runner-owned `_current_node` state, matcher registries, call recording, and optional cost metadata.
- CMP-4 depends on the canonical SF-1 wire shape: nested phases plus dict-keyed `inputs`, `outputs`, and `hooks`.
- CMP-5 depends on `ExecutionResult` plus `ExecutionHistory`; it must not synthesize resume state or widen the runtime result contract.
- CMP-8 is a convenience entrypoint only; it creates `RuntimeConfig`, passes through exceptions, and uses `"human"` as the default interaction-runtime key.

**Citations:**
- [decision] `D-GR-23` — `_current_node` and canonical merge order are runner-owned.
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:525-563` — canonical node, phase, and port shapes are dict-keyed and nested.
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:113-118` — observability belongs in `ExecutionResult` plus `ExecutionHistory`.

## Component Definitions

### CMP-1: MockAgentRuntime

- **Status:** new
- **Location:** `iriai_compose/testing/mock_runtime.py`
- **Description:** Fluent `AgentRuntime` test double with a zero-argument constructor. `.when_node()` resolves against the runner-owned `_current_node`, `.when_role(prompt=...)` adds a prompt-aware fallback tier, `.when_role()` adds a role-only tier, and `.default_response()` is the final fallback. `respond_with(prompt, context)` receives already-merged context in `workflow -> phase -> actor -> node` order; `on_call()` scopes a matcher to a specific invocation; `with_cost()` records cost metadata for later assertions.
- **Props/Variants:** `when_node | when_role | default_response ; respond | respond_sequence | respond_with | raise_error | then_crash | on_call | with_cost`
- **States:** `node_match`, `role_prompt_match`, `role_match`, `default_match`, `no_match`, `sequence_exhausted`, `error_injected`, `crash_injected`
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:3-8` — REQ-65 requires fluent no-arg API and ContextVar-based node matching.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:11-24` — REQ-66 and REQ-67 lock merge order and ABI ownership.
  - [code] `iriai-compose/iriai_compose/runner.py:36-50` — `invoke()` currently has no `node_id` kwarg and is the contract this mock must preserve.
  - [code] `.iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:276,290,319` — Cycle 7 calls out the required 4-strategy fluent matcher API and the gap in the stale plan.

### CMP-2: MockInteractionRuntime

- **Status:** new
- **Location:** `iriai_compose/testing/mock_runtime.py`
- **Description:** Fluent human-interaction test double that keeps the current `InteractionRuntime.resolve(pending)` signature intact. `.when_node()` uses the runner-owned `_current_node` for routing because `Pending` currently does not expose `node_id`. Default helpers keep the human flow simple for common tests, while `approve_sequence()`, `respond_with(pending)`, `script()`, `raise_error()`, and `then_crash()` support richer gating and review loops.
- **Props/Variants:** `when_node ; default_approve | default_choose | default_respond ; approve_sequence | respond_with | script | raise_error | then_crash`
- **States:** `node_match`, `default_human`, `scripted`, `sequence_exhausted`, `error_injected`, `crash_injected`
- **Citations:**
  - [code] `iriai-compose/iriai_compose/pending.py:9-21` — `Pending` has no `node_id`, so node-aware matching cannot depend on a payload field today.
  - [code] `iriai-compose/tests/test_runner.py:45-49` — the existing interaction-runtime map uses the `"human"` key.
  - [code] `.iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:321` — Cycle 7 lists the required fluent `MockInteractionRuntime` methods that were missing from the stale plan.

### CMP-3: MockPluginRuntime

- **Status:** new
- **Location:** `iriai_compose/testing/mock_plugin.py`
- **Description:** Plugin-side test double exposed from `iriai_compose.testing` and accepted by `run_test()` as the mock plugin boundary. Routing is keyed by `plugin_ref` through `.when_ref()`, while call records also capture the current node id from `_current_node` for diagnostics and cost assertions. Plugin callbacks use `respond_with(inputs, context)` so tests can model plugin-side transforms without adding a parallel runtime protocol.
- **Props/Variants:** `when_ref | default_response ; respond | respond_sequence | respond_with | raise_error | then_crash | with_cost`
- **States:** `ref_match`, `default_match`, `no_match`, `sequence_exhausted`, `error_injected`, `crash_injected`
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/plan-review-discussion-7.md:37` — the approved SF-3 rewrite explicitly adds `MockPluginRuntime` at `iriai_compose/testing/mock_plugin.py`.
  - [code] `.iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:273,291,317` — Cycle 7 documents CMP-3 as a required design/system-design component with missing plan coverage.

### CMP-4: WorkflowBuilder & Fixture Factories

- **Status:** new
- **Location:** `iriai_compose/testing/fixtures.py`
- **Description:** Test authoring surface for valid and intentionally invalid declarative workflows. It emits canonical nested phases, dict-keyed `inputs` / `outputs` / `hooks`, dict-keyed `BranchNode.outputs`, and actor refs using `agent` / `human` only. Preset factories cover the core parity cases: minimal ask, gate-and-revise, fold with accumulator, map fan-out, routed error path, plugin flow, YAML round-trip baseline, and validation failure cases.
- **Props/Variants:** `minimal | loop | fold | map | error_route | plugin | invalid_fixture`
- **States:** `minimal_valid`, `phase_mode_fixture`, `invalid_for_validation`, `snapshot_ready`
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:525-563` — AskNode, PhaseDefinition, BranchOutputPort, EdgeDefinition, and PortDefinition are all dict-keyed in the canonical wire shape.
  - [decision] `D-GR-35` — Branch outputs are per-port conditions with non-exclusive fan-out; fixtures must use the `outputs` map shape.
  - [code] `.iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:277` — Cycle 7 explicitly rejects list-based port containers in SF-3.

### CMP-5: Assertions Suite

- **Status:** new
- **Location:** `iriai_compose/testing/assertions.py`
- **Description:** Standalone pytest-friendly assertion helpers. Core helpers cover node reachability, branch outcomes, validation errors, YAML round-trip, and cost boundaries. Branch assertions treat fan-out outcomes as collections rather than exclusive scalars. Phase-mode helpers wrap `result.history` instead of widening `ExecutionResult`: `assert_loop_iterations()`, `assert_fold_items_processed()`, `assert_map_fan_out()`, and `assert_error_routed()` are required. Error messages must list expected vs actual values plus the available phases, routes, or matcher ids relevant to the failing assertion.
- **Props/Variants:** `execution | validation | phase_mode | error_route | cost`
- **States:** `pass`, `mismatch`, `missing_history`, `missing_metric`, `missing_error_route`, `cost_limit_exceeded`
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:35-39` — REQ-69 requires SF-3 assertions to rely on `ExecutionResult` and `ExecutionHistory`, not checkpoint/resume.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:113-118` — SF-2 publishes observability through `ExecutionResult` plus `ExecutionHistory`.
  - [code] `.iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:274,292,318` — Cycle 7 calls out the missing phase-mode assertion helpers that this design now makes mandatory.

### CMP-6: Snapshot Helpers

- **Status:** new
- **Location:** `iriai_compose/testing/snapshot.py`
- **Description:** YAML and execution snapshot helpers that produce unified diffs instead of opaque binary outputs. Snapshot helpers validate canonical nested serialization and are designed for code review readability on ordinary terminals.
- **Props/Variants:** `yaml_round_trip | yaml_equals | execution_snapshot`
- **States:** `matched`, `diff_failed`, `round_trip_failed`
- **Citations:**
  - [decision] `D-GR-8` — full SF-3 scope includes snapshot coverage, not only runtime mocks.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:555-563` — snapshot helpers must validate the canonical edge and port serialization contract.

### CMP-7: WorkflowTestCase

- **Status:** new
- **Location:** `iriai_compose/testing/base.py`
- **Description:** Convenience base class that auto-creates `MockAgentRuntime`, `MockInteractionRuntime`, and `MockPluginRuntime` so parity suites and migration tests start from a deterministic default harness. Subclasses can override any runtime or fixture while keeping the common contract surface.
- **Props/Variants:** `auto_mocks | custom_overrides`
- **States:** `ready`, `overridden`, `failure_passthrough`
- **Citations:**
  - [decision] `D-GR-8` — WorkflowTestCase is part of the full testing-framework scope.
  - [code] `.iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:273-274` — Cycle 7 review calls out missing component and assertion coverage that the shared base class must expose cleanly.

### CMP-8: run_test

- **Status:** new
- **Location:** `iriai_compose/testing/runner.py`
- **Description:** Thin convenience wrapper around SF-2 `run()`. It accepts a workflow object or path, assembles `RuntimeConfig`, injects the agent mock, maps the convenience human interaction arg to `interaction_runtimes={"human": ...}`, passes through plugin mocks or registries, and returns `ExecutionResult` unchanged. It never swallows exceptions, rewrites history, or adds resume inputs.
- **Props/Variants:** `runtime | interaction | plugin_runtime | plugin_registry | inputs | feature_id`
- **States:** `defaults_injected`, `custom_human_runtime`, `custom_plugin_runtime`, `surface_exception`
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:79-88` — SF-2 owns the canonical `run(workflow, config, *, inputs=None)` boundary.
  - [code] `iriai-compose/tests/test_runner.py:45-49` — `"human"` is the established interaction-runtime key in the current runtime/test surface.
  - [decision] `D-GR-24` — `run_test()` must not add checkpoint/resume behavior.
  - [code] `.iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:281,312-313` — Cycle 7 documents the stale `run_test()` key and parameter mismatches that this wrapper must correct.

## Verifiable States

### CMP-1 — MockAgentRuntime

| State | Semantic Description |
|-------|----------------------|
| `node_match` | The call record includes a non-empty `node_id` read from `_current_node`, `matched_by="node"`, and the node rule wins even if role-based rules also exist. |
| `role_prompt_match` | No node rule matched; the matched rule records `matched_by="role_prompt"` and identifies the role plus prompt-pattern matcher. |
| `role_match` | No node or role+prompt rule matched; the role-only matcher wins and is recorded explicitly. |
| `default_match` | No specific matcher matched and the default fallback returns the response. |
| `no_match` | `MockConfigurationError` lists node id, role, prompt excerpt, and the configured matchers. |
| `sequence_exhausted` | `MockExhaustedError` names the exhausted matcher and call index; the sequence does not wrap. |
| `error_injected` | The configured exception is raised exactly when the matched rule fires. |
| `crash_injected` | `then_crash()` raises the crash exception without mutating the runtime ABI. |

### CMP-2 — MockInteractionRuntime

| State | Semantic Description |
|-------|----------------------|
| `node_match` | `_current_node` resolves to a configured `when_node()` rule and the pending request uses that rule. |
| `default_human` | No node-specific rule exists, so the runtime returns its default approve/choose/respond behavior through the `"human"` channel. |
| `scripted` | The runtime returns the next scripted value from `approve_sequence()` or `script()` and advances the internal index. |
| `sequence_exhausted` | `MockExhaustedError` is raised when a configured approval or response sequence runs out. |
| `error_injected` | `raise_error()` surfaces the configured exception for the matching pending request. |
| `crash_injected` | `then_crash()` raises the crash exception for the matching request. |

### CMP-3 — MockPluginRuntime

| State | Semantic Description |
|-------|----------------------|
| `ref_match` | The call record captures `plugin_ref`, current `node_id`, input payload, and any attached cost metadata before returning the configured response. |
| `default_match` | No ref-specific rule exists and `default_response()` supplies the fallback plugin result. |
| `no_match` | `MockConfigurationError` names the missing `plugin_ref` and lists known refs. |
| `sequence_exhausted` | `MockExhaustedError` is raised after the configured plugin response sequence is consumed. |
| `error_injected` | `raise_error()` surfaces the configured plugin exception unchanged. |
| `crash_injected` | `then_crash()` raises the configured crash exception unchanged. |

### CMP-4 — WorkflowBuilder & Fixture Factories

| State | Semantic Description |
|-------|----------------------|
| `minimal_valid` | Produces a nested workflow with dict-keyed ports and no stale root fields or actor aliases. |
| `phase_mode_fixture` | Emits loop, fold, and map fixtures whose phase structure and ports are ready for `ExecutionHistory`-based assertions. |
| `invalid_for_validation` | Intentionally emits a stale or structurally invalid workflow so validation helpers can assert precise error codes. |
| `snapshot_ready` | Emits canonical YAML that round-trips without port-shape or hook-serialization drift. |

### CMP-5 — Assertions Suite

| State | Semantic Description |
|-------|----------------------|
| `pass` | The helper returns without mutation and leaves pytest to report the enclosing test result normally. |
| `mismatch` | `AssertionError` includes expected vs actual values plus the available nodes, phases, or routes relevant to the check. |
| `missing_history` | `AssertionError` says `result.history` is required for the requested phase-mode assertion. |
| `missing_metric` | `AssertionError` names the missing phase id or metric key and lists the available ones. |
| `missing_error_route` | `AssertionError` shows the recorded `error_routes` entries when the requested route was not observed. |
| `cost_limit_exceeded` | `AssertionError` shows the actual cost payload recorded by `with_cost()` and the configured threshold. |

### CMP-8 — run_test

| State | Semantic Description |
|-------|----------------------|
| `defaults_injected` | `run_test()` creates default agent and human interaction mocks when none are provided. |
| `custom_human_runtime` | `interaction=` is wrapped under `interaction_runtimes={"human": interaction}` rather than a generic default key. |
| `custom_plugin_runtime` | The provided plugin mock or registry is passed through to `RuntimeConfig` unchanged. |
| `surface_exception` | Exceptions from `run()` or any mock propagate unchanged to the caller. |
| `no_resume_surface` | Passing any resume-style input such as `history` is invalid because the helper does not define a resumability API. |

## Interaction Patterns

- Fluent matcher authoring is the primary authoring model. Constructors take no behavior parameters; matchers and terminal behaviors are added through chained methods only.
- Agent matcher resolution order is fixed and documented: `when_node()` first, `when_role(prompt=...)` second, `when_role()` third, `default_response()` last.
- `on_call()` and `with_cost()` are modifiers, not terminal actions. They refine the next terminal behavior without changing the runtime surface.
- `respond_sequence()` is always finite and fail-loud. Exhaustion is an assertion signal, not a silent loop fallback.
- `respond_with()` consumes already-assembled context; SF-3 must never reimplement context merging.
- Human interaction convenience uses the `"human"` key because that matches existing runner wiring and actor terminology.
- Phase-mode and routed-error assertions always read `result.history`; they must not invent top-level loop, fold, map, or error fields.
- The pre-implementation contract audit is mandatory. STEP-20 must stay blocked until stale ABI text, stale class names, stale interaction keys, and resume references are gone from plan and system design.

**NOT Criteria:**
- Fluent APIs must NOT fall back to dict-based constructor configuration.
- Matcher resolution must NOT depend on insertion order outside the documented 4-strategy priority.
- Assertions must NOT widen `ExecutionResult` or `RuntimeConfig` to make testing more convenient.
- SF-3 must NOT define a second ContextVar or a second runtime ABI.

**Citations:**
- [code] `.iriai/artifacts/features/beced7b1/plan-review-cycle-7.md:276,284,319-321` — Cycle 7 explicitly calls out the required 4-strategy priority, `MockExhaustedError`, and fluent interaction/runtime methods.
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:35-43` — history-based observability is in scope, while consumer-owned node carriers are forbidden.
- [decision] `D-GR-23` — matcher context must consume the runner-owned merge order and node propagation model.

## Surface / Accessibility Notes

SF-3 has no browser or mobile layout. The design target is readable plain-text output in terminals, CI logs, and code review diffs.

- Failure messages must be understandable without color; text must carry the full meaning.
- Assertion failures must list expected vs actual values and the available matchers, phases, or routes needed to debug the mismatch.
- Snapshot failures must use unified text diffs rather than opaque serialized blobs.
- Error text should use the existing runtime vocabulary (`human`, `node_id`, `plugin_ref`, `history`) so developers do not translate between test names and runtime names.

**Citations:**
- [code] `iriai-compose/tests/conftest.py:21-79` — existing test doubles and pytest usage are plain-text and call-record driven.
- [code] `iriai-compose/tests/test_runner.py:45-49` — existing runtime vocabulary already distinguishes `human` and `auto` interaction channels.


---

## Subfeature: Workflow Migration & Litmus Test (workflow-migration)

<!-- SF: workflow-migration -->
### SF-4: Workflow Migration & Litmus Test

SF-4 now treats migration as a declarative packaging problem with two reviewable boundaries: persistence is always explicit in the YAML graph, and consumer execution stays additive to iriai-build-v2 rather than mutating the existing imperative runtime. Every artifact-producing path must show a visible `store` PluginNode write before any hosting or notification hook, and iriai-build-v2 may enter declarative execution only through `_declarative.py` + `run_declarative()` + the CLI `--declarative` flag. Stale alternatives such as `artifact_key` auto-write, `--yaml`, seed-loading, or consumer-owned runtime shims are design errors, not equivalent variants. [decision: D-GR-14] [decision: D-GR-18] [decision: D-GR-32] [decision: D-GR-37] [code: iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/architecture.py:171] [code: iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py:14]

The migration proof surface is intentionally redundant so architects and implementers can verify it from either the YAML or the evidence suite. The YAMLs and task templates expose explicit store chains, canonical field names (`actor_ref`, `TemplateDefinition`, `EdgeDefinition`, `MapModeConfig`, `FoldModeConfig`, `LoopModeConfig`, `SequentialModeConfig`), consistent `schema_version`, and bugfix-only `config` plugin wiring for secret resolution. The evidence suite then proves the same contract with structural counts, negative transform-security tests, and an explicit AC-24 hygiene review that rejects any language implying SF-4 co-owns the SF-2 ABI or that the core runtime owns checkpoint/resume. [decision: D-GR-10] [decision: D-GR-23] [decision: D-GR-24] [decision: D-GR-38] [code: iriai-compose/iriai_compose/runner.py:5] [code: iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/env_setup.py:53]

<!-- SF: workflow-migration -->
### J-20 — Translate Planning Workflow Against The Canonical SF-2 ABI

**Step Annotations:**
- Step 1 — Author YAML and templates: planning, develop, and bugfix translations all expose persistence as `producer -> write_* store PluginNode -> optional hosting/notify hook from write_*.on_end`. Reviewers should be able to identify the persistence boundary by the presence of `plugin_ref: artifact_db` plus `config.operation: put`; `artifact_key` on AskNodes, template bodies, or hook sources is a blocking defect.
- Step 2 — Normalize migration vocabulary: Ask nodes use `actor_ref`; branch routing stays on per-port conditions rather than `switch_function`; task templates use `TemplateDefinition`; imports and docs use `EdgeDefinition` plus the four `*ModeConfig` names consistently. These names appear identically in YAML, tests, and bridge documentation.
- Step 3 — Prove execution with Tier 2 evidence: structural and behavioral tests use `MockAgentRuntime`, `MockInteractionRuntime`, and `MockPluginRuntime`, scan for 8 plugin instances and 7 transforms, assert consistent `schema_version`, and include negative cases for blocked builtins, oversized expressions, and stale `build_env_overrides` transform usage.
- Step 4 — Hand off to the bridge: migration is only considered complete when the translated YAML executes through `_declarative.py` / `run_declarative()` / `--declarative` without consumer-side runtime shims or bridge-local ABI extensions.

**Error Path UX:** Contract drift is surfaced as a review failure before parity signoff. The failure is recognizable by any of the following visible markers: an AskNode still carries `artifact_key`, a hosting edge sources from a producer `on_end` instead of a `write_*` node, the test bundle still imports `MockRuntime` or `MockInteraction`, or the bridge examples still show `--yaml`.

**Empty State UX:** A newly translated workflow remains in an incomplete review state until every expected persisted artifact has a named `write_*` store node. A phase with producer nodes but no downstream store node is treated as structurally unfinished rather than implicitly persisted.

**NOT Criteria:**
- Migration YAML must NOT imply auto-write persistence through `artifact_key`.
- Planning, develop, and bugfix translations must NOT use stale type names, stale mock class names, or `switch_function`.
- The handoff to execution must NOT rely on `--yaml`, seed-loading, or consumer-defined runtime shims.

**Citations:** [decision: D-GR-18] [decision: D-GR-35] [decision: D-GR-37] [decision: D-GR-38] [code: iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/architecture.py:171] [code: iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:91]

<!-- SF: workflow-migration -->
### J-21 — Remove A Stale Consumer Assumption

**Step Annotations:**
- Step 1 — Review consumer artifacts: any occurrence of `--yaml`, `run(yaml_path, ...)`, `MockRuntime`, `MockInteraction`, `MapConfig`, `TemplateRef`, `Edge`, `artifact_key`, or `build_env_overrides` as a transform is treated as visible contract drift.
- Step 2 — Rewrite to canonical surfaces: stale terms are replaced with `_declarative.py`, `run_declarative()`, `--declarative`, `MockAgentRuntime`, `MockInteractionRuntime`, `MockPluginRuntime`, explicit store PluginNodes, and the 6 plugin types / 8 instances / 7 transforms model.
- Step 3 — Re-run hygiene checks: AC-24 review confirms the corrected artifact now treats SF-2 as the ABI owner, consumes `ExecutionResult` / `ExecutionHistory` only as published outputs, and leaves the imperative path untouched.

**Error Path UX:** Drift is called out as a compatibility fault, not papered over with aliases. Review notes should name the stale token directly so the engineer can remove it rather than preserving a second accepted spelling.

**Empty State UX:** Once stale tokens are removed, no extra compatibility wrapper remains visible. The absence of aliasing is itself the success state.

**NOT Criteria:**
- Review must NOT silently alias `--yaml` to `--declarative` in docs, tests, or CLI copy.
- Bridge and test artifacts must NOT carry both stale and canonical names at the same time.
- AC-24 signoff must NOT pass while any consumer artifact still implies SF-4 co-owns the runtime ABI.

**Citations:** [decision: D-GR-23] [decision: D-GR-24] [decision: D-GR-32] [decision: D-GR-37] [code: iriai-compose/iriai_compose/runner.py:42] [code: iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py:119]

<!-- SF: workflow-migration -->
### J-22 — Run Declarative Bridge Without A Core Resume Contract

**Step Annotations:**
- Step 1 — Enter through the additive bridge: the declarative path starts after the normal iriai-build-v2 bootstrap, then passes into `_declarative.py`. The bridge accepts a BootstrappedEnv-shaped service bundle but does not widen the existing runtime ABCs or import consumer-specific types into `iriai-compose`.
- Step 2 — Build runtime adapters explicitly: `create_plugin_runtimes()` maps store, hosting, mcp, subprocess, http, and config adapters from the bootstrapped services. `env_overrides` is resolved by the config adapter before preview/playwright/git work begins, keeping secrets out of transform code.
- Step 3 — Execute through the canonical SF-2 entry: the bridge loads YAML, assembles `RuntimeConfig`, then calls `run(workflow, config, *, inputs=None)` and observes `ExecutionResult` / `ExecutionHistory` / phase metrics. No resume flag, checkpoint store contract, or direct `node_id` parameter is added to get through the run.
- Step 4 — Preserve the imperative fallback: invoking the CLI without `--declarative` remains the unchanged imperative path, which is a separate verifiable state and part of AC-24 hygiene.

**Error Path UX:** Bridge failures are explicit and local. A missing adapter, unsupported plugin type, or bad YAML path fails the declarative entry path without mutating the imperative entry path or suggesting a hidden resume feature.

**Empty State UX:** When no declarative path is provided, the CLI remains in the existing imperative mode. There is no partial bridge state and no half-configured declarative runtime.

**NOT Criteria:**
- The bridge must NOT expose `--yaml` as an accepted flag.
- `_declarative.py` must NOT add checkpoint/resume arguments or a consumer-local `invoke(..., node_id=...)` shim.
- SF-4 must NOT add seed loading, plugin-execution HTTP surfaces, or other non-bridge scope to make the declarative path appear complete.

**Citations:** [decision: D-GR-18] [decision: D-GR-23] [decision: D-GR-24] [decision: D-GR-32] [code: iriai-build-v2/src/iriai_build_v2/interfaces/_bootstrap.py:31] [code: iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py:36] [code: iriai-compose/iriai_compose/runner.py:42]

<!-- SF: workflow-migration -->
### J-23 — Consumer Expects Core Checkpoint/Resume From SF-2

**Step Annotations:**
- Step 1 — Detect the mismatch: any plan note, bridge helper, or test that treats checkpoint/resume as part of the core declarative ABI is flagged before parity review proceeds.
- Step 2 — Reframe recovery explicitly: if a consuming app still needs recovery behavior, it must model that need visibly with workflow-level persistence or app-level orchestration rather than by extending the SF-2 bridge surface.
- Step 3 — Record hygiene completion: AC-24 review confirms the declarative bridge remains additive, the imperative path still behaves identically without `--declarative`, and no consumer artifact reintroduces hidden runtime ownership.

**Error Path UX:** Resume drift is surfaced as scope violation. Review output should name the offending API assumption directly, such as a checkpoint store parameter on the bridge or a test harness that injects history into runtime config.

**Empty State UX:** Not applicable. This journey is a contract-correction path, not a data-empty path.

**NOT Criteria:**
- The bridge must NOT add a core checkpoint/resume contract just to make consumer parity tests easier.
- Migration tests must NOT require resume-specific parameters to call `run()`.
- AC-24 review must NOT pass while imperative and declarative ownership boundaries remain blurred.

**Citations:** [decision: D-GR-23] [decision: D-GR-24] [decision: D-GR-32] [code: iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py:103] [code: iriai-compose/iriai_compose/runner.py:57]

<!-- SF: workflow-migration -->
### Component Hierarchy

```text
MigrationPackage
├── Workflow YAMLs
│   ├── planning.yaml
│   ├── develop.yaml
│   └── bugfix.yaml
├── TemplateDefinitions
│   ├── gate_and_revise
│   ├── broad_interview
│   └── interview_gate_review
├── Plugin Catalog
│   ├── 6 PluginInterface types
│   ├── 8 PluginInstance configs
│   └── 7 transform entries
├── Runtime Bridge
│   ├── _declarative.py
│   ├── register_plugin_types()
│   ├── register_instances()
│   └── create_plugin_runtimes()
└── Evidence Suite
    ├── structural parity tests
    ├── behavioral equivalence tests
    ├── negative security tests
    └── AC-24 hygiene review
```

**State requirements:**
- Workflow YAMLs need consistent `schema_version`, canonical field names, explicit store PluginNodes for every persisted artifact, and no stale `artifact_key` or `build_env_overrides` transform usage.
- TemplateDefinitions need sanitized Jinja2 parameters for keys, actor refs, and labels before materialization, plus the same explicit store pattern as the top-level workflow YAMLs.
- The Runtime Bridge needs a bootstrapped service bundle, 6 adapter classes, a complete 8-instance registry, and one execution path that terminates at SF-2's canonical `run()` signature.
- The Evidence Suite needs structural counts, negative security checks, parity assertions, and an AC-24 artifact-hygiene checkpoint before migration is marked complete.

**Citations:** [decision: D-GR-10] [decision: D-GR-18] [decision: D-GR-32] [decision: D-GR-37] [decision: D-GR-38] [code: iriai-build-v2/src/iriai_build_v2/interfaces/_bootstrap.py:31]

<!-- SF: workflow-migration -->
### CMP-10: Explicit Artifact Persistence Chain

- **Status:** extending
- **Location:** `planning.yaml`, `develop.yaml`, `bugfix.yaml`, and task template YAML files
- **Description:** The visible persistence chain for every migrated artifact is `producer -> write_* store PluginNode -> optional hosting/notify hook from write_*.on_end`. The chain is the primary review surface for proving that imperative `runner.artifacts.put(...)` calls were translated without relying on `artifact_key` auto-write.
- **Props/Variants:** `store-only | store+hosting | store+hosting+notify`
- **States:** canonical, missing-store, wrong-hook-source
- **Citations:**
  - [decision: D-GR-14] — Artifact writes are explicit plugin behavior.
  - [decision: D-GR-37] — The workflow-migration persistence model resolves in favor of explicit store PluginNodes.
  - [code: iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/architecture.py:171] — The imperative planning flow currently persists `plan` explicitly.
  - [code: iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py:91] — The imperative develop flow persists `implementation` explicitly.

### CMP-11: Tier 2 Mock And Parity Evidence

- **Status:** extending
- **Location:** `tests/migration/` consuming `iriai_compose.testing`
- **Description:** The parity surface combines structural, behavioral, security-negative, and hygiene checks using `MockAgentRuntime`, `MockInteractionRuntime`, and `MockPluginRuntime`. It proves the translated workflows use the right names, counts, and execution boundary without reintroducing stale mocks or consumer-owned ABI extensions.
- **Props/Variants:** `planning | develop | bugfix | security-negative | hygiene`
- **States:** configured, parity-pass, hygiene-fail
- **Citations:**
  - [decision: D-GR-23] — SF-4 consumes the SF-2 ABI and does not widen `invoke()`.
  - [decision: D-GR-38] — Expression security is enforced by AST allowlist + timeout + size limit.
  - [code: iriai-compose/tests/conftest.py:21] — The existing repo already exposes `MockAgentRuntime` rather than a `node_id`-widened runtime.

### CMP-12: Consumer Integration Boundary

- **Status:** extending
- **Location:** `iriai-build-v2/src/iriai_build_v2/workflows/_declarative.py` and `iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py`
- **Description:** Additive bridge entry that loads declarative YAML, assembles the plugin registry and adapter runtimes, and invokes SF-2 through `run_declarative()` and the CLI `--declarative` flag. This component exists to make the declarative path visible without changing the imperative default path.
- **Props/Variants:** `CLI | programmatic`
- **States:** ready, executing, stale-flag-error
- **Citations:**
  - [decision: D-GR-18] — The CLI flag is `--declarative`.
  - [decision: D-GR-32] — SF-4 scope is the additive bridge, not seed loading or plugin HTTP surfaces.
  - [code: iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py:119] — The existing CLI already has stable entry points that the declarative path must extend rather than replace.
  - [code: iriai-build-v2/src/iriai_build_v2/interfaces/_bootstrap.py:48] — The bridge should reuse the normal bootstrapped service bundle.

### CMP-13: Config Plugin Resolution Chain

- **Status:** new
- **Location:** `iriai-compose/iriai_compose/plugins/types.py`, `iriai-compose/iriai_compose/plugins/instances.py`, and `bugfix.yaml`
- **Description:** The bugfix workflow resolves environment-sensitive preview inputs through an explicit `config` plugin instance (`env_overrides`) before any preview, MCP, or subprocess work runs. This keeps secret resolution visible in the graph and outside transform code.
- **Props/Variants:** `resolve-only | resolve+defaults`
- **States:** configured, missing-key, leaked-transform
- **Citations:**
  - [decision: D-GR-10] — Secrets and environment variables live on plugin instances, not in transforms.
  - [code: iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/env_setup.py:53] — The imperative workflow currently builds env overrides from environment variables and needs visible declarative replacement.

### CMP-14: Transform Catalog And Template Sanitization

- **Status:** new
- **Location:** `iriai-compose/iriai_compose/plugins/transforms.py` and workflow/template YAML bindings
- **Description:** The migration catalog contains exactly 7 transform entries. Each entry must either remain AST-validated `exec`-compatible code inside SF-2's sandbox or move into a named helper; none of them may depend on `eval()`, `import`, or secret access. Jinja2 template bindings for keys, actor refs, and labels are sanitized before materialization so template expansion does not become a second expression escape hatch.
- **Props/Variants:** `inline transform | named helper | sanitized template binding`
- **States:** valid, blocked-builtin, unsanitized-parameter, oversize
- **Citations:**
  - [decision: D-GR-5] — Multi-line transform bodies are allowed only through restricted, AST-validated execution.
  - [decision: D-GR-38] — Bare `exec()` without the allowlist/timeout/size model is rejected.
  - [decision: D-GR-10] — `build_env_overrides` is excluded because env access belongs to the config plugin.

### CMP-15: AC-24 Hygiene Review Bundle

- **Status:** new
- **Location:** `tests/migration/test_bridge.py` and migration review checklist sections
- **Description:** Final signoff bundle that verifies the declarative bridge remains additive, the imperative path still works without `--declarative`, stale vocabulary is gone, counts align at 6 plugin types / 8 instances / 7 transforms, and no artifact language implies SF-4 owns the SF-2 runtime ABI.
- **Props/Variants:** `structural | behavioral | security | hygiene`
- **States:** pass, imperative-regression, contract-drift
- **Citations:**
  - [decision: D-GR-23] — SF-2 remains the ABI owner.
  - [decision: D-GR-24] — Checkpoint/resume is out of SF-2 core scope.
  - [decision: D-GR-32] — The approved SF-4 bridge scope stays narrow and additive.
  - [code: iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py:103] — The imperative execution path must remain intact when declarative mode is not requested.

<!-- SF: workflow-migration -->
### CMP-10 (Explicit Artifact Persistence Chain) States

| State | Visual Description |
|-------|-------------------|
| canonical | An artifact-producing node is followed by a `write_*` PluginNode with `plugin_ref: artifact_db` and `config.operation: put`, and any publish/hosting step sources from `write_*.on_end`. |
| missing-store | A producer node has no downstream `write_*` store PluginNode for an artifact that is known to persist in the imperative workflow. |
| wrong-hook-source | A hosting or notify node receives its hook from `producer.on_end` instead of `write_*.on_end`, making the persistence boundary ambiguous. |

### CMP-11 (Tier 2 Mock And Parity Evidence) States

| State | Visual Description |
|-------|-------------------|
| configured | The migration suite imports `MockAgentRuntime`, `MockInteractionRuntime`, and `MockPluginRuntime`, checks 8 plugin instances / 7 transforms, and includes negative security coverage. |
| parity-pass | Structural, behavioral, and bridge tests all pass without any stale class names, stale flags, or ABI extensions. |
| hygiene-fail | AC-24 review finds lingering `--yaml`, `MockRuntime`, `artifact_key`, or consumer-owned runtime language even if some behavioral tests pass. |

### CMP-12 (Consumer Integration Boundary) States

| State | Visual Description |
|-------|-------------------|
| ready | `_declarative.py` exists, `run_declarative()` is the documented entry point, and CLI help/examples show `--declarative`. |
| executing | The bridge has loaded YAML, assembled adapter runtimes from the bootstrapped services, and is calling canonical `run(workflow, config, *, inputs=None)`. |
| stale-flag-error | Docs, call paths, or examples still show `--yaml` or another non-approved bridge entry surface. |

### CMP-13 (Config Plugin Resolution Chain) States

| State | Visual Description |
|-------|-------------------|
| configured | Bugfix YAML includes an explicit `env_overrides` PluginNode of `plugin_type: config` before preview-related nodes. |
| missing-key | The config plugin exists but its configured key list is incomplete for the expected preview/deploy secret set. |
| leaked-transform | `build_env_overrides` still appears as a transform or transform code still reads from environment variables directly. |

### CMP-14 (Transform Catalog And Template Sanitization) States

| State | Visual Description |
|-------|-------------------|
| valid | The workflow set declares exactly 7 transform entries, each compiles under the AST sandbox or resolves through a named helper, and template bindings materialize only sanitized key/label values. |
| blocked-builtin | Negative tests show the transform or template surface correctly rejects `import`, `__import__`, `eval`, `exec`, or `compile`. |
| unsanitized-parameter | A template binding allows an unsafe key, label, or actor-ref interpolation pattern and is blocked before parity review. |
| oversize | The transform exceeds the agreed size bound and is rejected instead of silently truncating or bypassing sandbox checks. |

### CMP-15 (AC-24 Hygiene Review Bundle) States

| State | Visual Description |
|-------|-------------------|
| pass | The bridge is additive, the imperative path is unchanged without `--declarative`, counts align, and no stale runtime-ownership language remains. |
| imperative-regression | Running without `--declarative` shows changed behavior or documentation no longer preserves the original imperative path. |
| contract-drift | The final review still finds stale flags, stale type names, stale mock names, or implied bridge ownership of checkpoint/resume. |

<!-- SF: workflow-migration -->
### Responsive Behavior

| Breakpoint | Layout Change |
|------------|---------------|
| < 768px | If migration diagrams or review checklists are rendered in docs, persistence chains and bridge steps stack vertically. Each state label stays in text so reviewers do not have to infer meaning from connector color alone. |
| 768-1024px | YAML excerpt + explanation pairs may render in two columns, but the `write_*`, `plugin_type`, and `--declarative` labels remain visible without hover or truncation. |
| > 1024px | Default artifact review layouts may show YAML, bridge flow, and evidence checklist side by side because the same labels remain readable in a single scan. |

**Citations:** [decision: D-GR-32] [decision: D-GR-37] [decision: D-GR-38]

<!-- SF: workflow-migration -->
### Interaction Patterns

**Explicit Persistence Pattern:** Artifact production does not transition to "saved" until a downstream store PluginNode is visible. Optional hosting and notify actions are fire-and-forget hooks from the store node, not alternative write paths. [decision: D-GR-14] [decision: D-GR-37]

**Bridge Invocation Pattern:** Declarative execution always enters through `run_declarative()` and `--declarative`, reusing the normal bootstrapped services and imperative CLI shell. [decision: D-GR-18] [decision: D-GR-32] [code: iriai-build-v2/src/iriai_build_v2/interfaces/_bootstrap.py:48]

**Transform And Template Safety Pattern:** Transform logic remains inside the AST-validated sandbox or becomes a named helper; template bindings are sanitized before materialization; env access is pushed into the config plugin. [decision: D-GR-5] [decision: D-GR-10] [decision: D-GR-38]

**Verification Pattern:** Structural parity, behavioral equivalence, negative security checks, and AC-24 hygiene review must all pass before a migrated workflow is treated as representative of the imperative source. [decision: D-GR-23] [decision: D-GR-24] [decision: D-GR-38]

**NOT Criteria:**
- The design must NOT accept hidden persistence, hidden resume ownership, or hidden flag aliases.
- The bridge must NOT blur the boundary between the unchanged imperative path and the additive declarative path.
- Security review must NOT rely on positive-path tests alone; negative transform and template cases are required.

<!-- SF: workflow-migration -->
### Accessibility Notes

- Review artifacts must name critical states in text (`write_plan`, `plugin_type: config`, `--declarative`, `contract-drift`) rather than relying on connector color, badge color, or diagram position alone. [decision: D-GR-37] [decision: D-GR-38]
- CLI examples and call-path diagrams should spell out `--declarative` in full so screen readers and copy/paste workflows preserve the accepted bridge flag exactly. [decision: D-GR-18]
- Evidence tables should present pass/fail reasons in reading order and explicitly call out the stale token that triggered failure (`artifact_key`, `--yaml`, `MockRuntime`, `MapConfig`, etc.). [decision: D-GR-32] [decision: D-GR-37]
- Template and transform safety failures should use explicit blocked-language text so the reviewer does not need traceback details to understand what was rejected. [decision: D-GR-38]


---

## Subfeature: Composer App Foundation & Tools Hub (composer-app-foundation)

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub

SF-5 treats `/api/schema/workflow` as the only runtime schema handshake between the compose app and `iriai-compose`. The Explorer shell still loads independently, but editor entry, import validation, save/export behavior, and stale-contract error messaging all align to the same persisted workflow contract: nested phase containment (`phases[].nodes`, `phases[].children`) and edge-only hook serialization with no separate serialized `port_type`. The foundation therefore owns the schema bootstrap/loading/error experience and must never treat a bundled `workflow-schema.json` as the runtime source of truth.

SF-5 also owns the definition of four contract requirements that cascade downstream to SF-6's implementation. These are non-negotiable persistence/bootstrap requirements, not SF-6 design choices: (1) Synthetic root phase normalization — every workflow the editor opens must have at least one phase; if the stored payload has no phases, the load path wraps content in a synthetic root phase before the canvas mounts. (2) Four atomic node types per D-GR-36 — Ask, Branch, Plugin, and Error nodes are directly placeable in the editor canvas; no SwitchFunctionEditor surfaces in the palette, inspector, or serialization format (switch_function is REJECTED per D-GR-35). ErrorNode is a terminal raise node with message (Jinja2), inputs, no outputs, no hooks. (3) Cross-phase edges at workflow root — edges that connect nodes in different phases are stored in the workflow-root `edges` array, never inside a phase definition. (4) Blocking schema gate — the schema bootstrap gate has no view-only fallback; when schema is unavailable, the editor shows the blocking error panel and nothing else.

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
- The editor canvas surfaces four atomic node types for direct placement per D-GR-36: Ask, Branch, Plugin, and Error. SwitchFunctionEditor does not appear in the palette, inspector, or canvas (switch_function is REJECTED per D-GR-35). ErrorNode is a terminal raise node (message Jinja2 template, inputs, no outputs, no hooks).
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
### J-7 — SF-5: Composer App Foundation & Tools Hub

**PRD Reference:** `J-32 — Tools hub session is missing and the user must authenticate first`

**Step Annotations:**
- Tools hub entry stays auth-aware: protected cards remain visible but inert until auth state resolves, and missing-session recovery uses the normal auth redirect rather than a broken or partially interactive shell.
- If the user is redirected to login from either tools hub or compose, the current path is preserved so successful authentication returns them to the same tools-hub or compose route instead of a generic landing page.
- After auth succeeds, the developer-tools catalog rehydrates first and same-tab navigation into compose resumes without requiring a manual refresh.

**Error Path UX:** Missing or expired session state resolves through redirect-based auth recovery with preserved deep link. The UI never exposes raw 401 payloads in-shell; the user either returns to the intended route or stays on the authenticated tools catalog.

**Empty State UX:** Not applicable; this is an auth-gating path, not a data-empty path.

**NOT Criteria:**
- Protected tools-hub cards must NOT appear actionable before auth state is known.
- Re-authentication must NOT drop the user on an unrelated default page when a deeper compose route was requested.
- Session failure must NOT surface raw 401 JSON or strand the user on a broken intermediate route.
- Tools hub to compose navigation must NOT open a new tab before or after auth recovery.

### J-8 — SF-5: Composer App Foundation & Tools Hub

**PRD Reference:** `J-27 — User imports a valid workflow YAML`

**Step Annotations:**
- Import is a workflow-collection action on the Workflows landing view, not an editor-side mutation. The file picker or dialog stays anchored to the list shell and submits only to `POST /api/workflows/import`.
- During upload and validation, the import affordance shows a submitting state and blocks repeat submits while leaving the existing workflow grid visible for context.
- Successful import creates a new user-owned workflow plus `WorkflowVersion` v1 before navigation. The success transition routes into the same editor bootstrap flow as a newly created workflow.
- If the backend returns non-blocking contract warnings, the new workflow card uses the GridCard warning variant and the response details are available through YAMLContractErrorPanel-backed messaging rather than inline raw YAML output.

**Error Path UX:** Parse, validation, or transport failures keep the user on the Workflows landing view. Parse and contract issues surface as structured path/message feedback; transient network failures surface as retryable toast plus preserved list context. No partial card insertion is allowed.

**Empty State UX:** Import remains a primary action even when the user has zero workflows, appearing alongside New Workflow and starter templates instead of behind secondary navigation.

**NOT Criteria:**
- Import must NOT navigate before the API confirms workflow creation.
- Import must NOT require an existing workflow id or route through `POST /api/workflows/{id}/import`.
- Warning treatment must NOT expose raw YAML contents in toast copy.
- Successful import must NOT omit `WorkflowVersion` v1 or require a full page refresh before the new workflow appears.

### J-9 — SF-5: Composer App Foundation & Tools Hub

**PRD Reference:** `J-28 — User starts from a starter template`

**Step Annotations:**
- The Workflows landing view includes a dedicated starter-template cluster ahead of or alongside the user's workflow grid. Each starter card uses the same GridCard shell but with a clear system-owned badge and a primary `Use template` action.
- Starter templates are presented as read-only system content. The visible actions are preview/open-context and duplicate; inline rename, delete, and direct save affordances are intentionally absent.
- Choosing a starter template duplicates it into a new user-owned workflow with `WorkflowVersion` v1 and then routes into the standard editor bootstrap flow for that new copy.
- Once duplicated, the copy appears in the user's normal workflow collection without the starter badge, so system-owned and user-owned records stay visually distinct.

**Error Path UX:** Duplicate failures keep the starter-template cards in place and surface retryable inline or toast feedback without mutating the source template card. The source template never appears partially claimed or partially copied.

**Empty State UX:** Starter templates remain visible when the user has no workflows and act as first-run onboarding content, not as a hidden secondary tab.

**NOT Criteria:**
- Starter templates must NOT be edited in place or silently converted into user-owned rows without an explicit duplicate action.
- The UI must NOT expose the raw `__system__` sentinel or present starter templates as if they belong to the current user.
- Template retrieval must NOT imply request-time filesystem reads or a separate starter-template entity type.
- Duplicating a template must NOT route the editor back to the system-owned source row.

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
- **Location:** `tools/compose/frontend — Workflows landing view`
- **Description:** Shared card shell for both user-owned workflows and system-owned starter templates. User workflow cards support selection and validation-warning treatments; starter-template cards add a visible system badge plus a primary duplicate action while hiding user-owned destructive actions.
- **Props/Variants:** `variant: default | warning | selected | starter_template`
- **States:** default, warning, selected, starter_template
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:303` — "User imports a valid workflow YAML" — Imported workflows can arrive with non-blocking warnings, so the shared card needs a warning treatment for newly created user-owned workflows.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:319` — "User starts from a starter template" — Starter templates are surfaced in the same landing experience but must remain visibly system-owned and duplicate-only.

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
| default | Standard workflow card shows workflow name, short description, and open/edit affordance with no warning or system badges. |
| warning | Workflow card shows an amber warning badge or icon plus brief validation-warning copy while remaining selectable and openable. |
| selected | Active card has stronger border/background contrast and visible focus treatment to confirm current selection. |
| starter_template | Card shows a `Starter template` badge, template summary such as Planning / Develop / Bugfix, and a primary `Use template` action; rename/delete affordances are absent. |

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

Four atomic node types per D-GR-36. The editor canvas surfaces exactly four atomic node types for direct placement: Ask, Branch, Plugin, and Error. SwitchFunctionEditor does not exist in the palette, inspector, or serialization format (switch_function is REJECTED per D-GR-35). Branching behavior is expressed through Branch nodes with per-port BranchOutputPort.condition expressions and non-exclusive fan-out. ErrorNode is a terminal raise node with message (Jinja2 template), inputs (dict), no outputs, no hooks — its purpose is to explicitly raise errors (e.g., log an error but let it bubble up). Error ports on Ask/Branch/Plugin handle the "catch" side; ErrorNode handles the "raise" side. Save and export reject any node type outside these four from the persisted phase structure.

Cross-phase edge storage. Edges that connect nodes belonging to different phases are stored in the workflow-root `edges` array, not inside any phase definition. On load, the editor reconstructs phase membership from node containment metadata. On save, any edge whose endpoints belong to different phases is lifted to the workflow-root array before serialization. Phase-level edge arrays, if present in any loaded payload, are treated as stale-contract violations and surfaced through the YAMLContractErrorPanel.

Save / export / import contract. All four operations speak the same persisted shape: nested `phases[].nodes` and `phases[].children`, only Ask/Branch/Plugin/Error node types inside phases, cross-phase edges at workflow root, and hook connections in the edges array with no serialized `port_type`. Any internal editor-only `port_type` concept is reconstructed from port resolution and stripped before persistence. Import distinguishes parse errors from stale-contract validation failures; both keep the user in recoverable shell states and never partially write invalid workflows.

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
6. ErrorFlowNode as a routing mechanism — originally rejected in Cycle 5 because error routing was expressed through error ports. **REVERSED per D-GR-36:** ErrorNode IS a 4th atomic node type. Error ports on Ask/Branch/Plugin handle the "catch" side (receiving errors), but ErrorNode handles the "raise" side — its purpose is to explicitly raise errors (e.g., log an error but let it bubble up). ErrorNode entity: `id`, `type: error`, `message` (Jinja2 template), `inputs` (dict), NO outputs, NO hooks. This is distinct from the rejected ErrorFlowNode routing concept — ErrorNode is a terminal raise node, not a routing mechanism.
7. Store cross-phase edges inside the destination or source phase definition. Rejected because phase-level edge arrays create ambiguity about edge ownership when phases are reordered or re-parented. Workflow-root edge storage is unambiguous and mirrors the iriai-compose runner's edge resolution model.

<!-- SF: composer-app-foundation -->
### SF-5: Composer App Foundation & Tools Hub

The prior SF-5 design artifact was still centered on an older plugin-management contradiction. Cycle 4 made a different contract the real blocker: where the composer gets its schema, what shape persisted YAML has, and how hook edges are represented. D-GR-22 settles those together. SF-5 therefore needs a design that keeps the Explorer shell stable, treats `/api/schema/workflow` as the runtime authority, and makes stale-contract failures explicit at editor boot, import, save, and export boundaries instead of silently normalizing legacy formats.

The Cycle 5 revision feedback adds four specific contract requirements that SF-5's design must make explicit so they cascade as hard requirements into SF-6: (1) the schema gate is strictly blocking with no view-only fallback; (2) synthetic root phase normalization must run before every editor canvas mount; (3) only Ask, Branch, and Plugin node types are directly placeable — no SwitchFunctionEditor or ErrorFlowNode; (4) cross-phase edges are stored at the workflow root, not inside phase definitions. These are persistence/bootstrap contract requirements owned by SF-5, not editor implementation choices left to SF-6. Documenting them in SF-5's design gives SF-6 a clear, unambiguous contract to implement and prevents stale patterns (view-only fallback, fourth node type, phase-level cross-phase edges) from re-entering through the editor layer.


---

## Subfeature: Workflow Editor & Canvas (workflow-editor)

<!-- SF: workflow-editor -->
### SF-6: Workflow Editor & Canvas

The workflow editor is the React Flow authoring surface inside `tools/compose/frontend`. This revision makes the design the clear source for the SF-6 rewrite: left palette, center canvas, floating inspectors, blocking runtime-schema bootstrap, dict-keyed typed ports, synthetic-root normalization, cross-phase edge routing at `workflow.edges`, auth-aware background calls, safe YAML handling, export redaction, and a strict no-local-execution boundary for inline Python.

The direct-placement node set remains Ask, Branch, and Plugin only. `AskFlowNode`, `BranchFlowNode`, and `PluginFlowNode` are thin SF-6 React Flow adapters around SF-7 primitives per D-58. Branch authoring uses only the D-GR-35 per-port model: `outputs: Record<string, BranchOutputPort>`, one `PortConditionRow` per output, and optional `MergeFunctionEditor` for multi-input gather. `SwitchFunctionEditor`, `output_field`, and stale node-level branch predicates are explicitly rejected. `SchemaBootstrapGate` (CMP-69) blocks the route until both the workflow payload and `GET /api/schema/workflow` succeed, and there is no view-only or static-schema fallback.

**Citations:**
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:9` — REQ-1 defines the left palette, centered canvas, and floating inspectors.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:16` — REQ-2 limits direct placement to Ask, Branch, and Plugin and requires thin SF-6 wrappers around SF-7 primitives.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:23` — REQ-3 locks branch authoring to the per-port model.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:47` — REQ-6 makes synthetic-root normalization and cross-phase edge routing mandatory.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:62` — REQ-8 requires authenticated validate/save/auto-save handling with recoverable `401` and `429` behavior.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:94` — REQ-12 hardens YAML import and export.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:109` — REQ-14 forbids browser-side execution of inline Python.
- [decision] `D-GR-35` — Per-port branch fan-out is authoritative; `switch_function` is rejected and `merge_function` remains valid.
- [decision] `D-58` — SF-6 owns thin React Flow adapters, while SF-7 owns visual primitives.

### J-1 — Build A Workflow From Scratch

**PRD Reference:** `J-1: Build A Workflow From Scratch` (`/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:226`)

**Step Annotations:**
- Step 1 — Open editor: `SchemaBootstrapGate` (CMP-69) owns the full viewport until the workflow record and `/api/schema/workflow` both succeed. The left palette, canvas, inspectors, and toolbar do not mount behind a partial shell.
- Step 2 — Drag Ask node: `AskFlowNode` (CMP-35) drops from the left palette into the synthetic root phase when it has no explicit parent. The node wrapper renders `inputs`, `outputs`, and `hooks` from `Record<string, PortDefinition>` so `schema_def`-backed ports remain visible and round-trip-safe.
- Step 3 — Add Branch and connect: `BranchFlowNode` (CMP-36) renders `outputs: Record<string, BranchOutputPort>` and seeds two starter outputs. `OutputPathsEditor` (CMP-64) owns the list, `PortConditionRow` (CMP-66) owns each condition row, and `MergeFunctionEditor` (CMP-65) is collapsed or disabled until the branch has 2+ inputs. Type-mismatch edges are created immediately and rendered as `DataEdge` warnings rather than blocked.
- Step 4 — Draw phase, validate, save: a named `PhaseContainer` (CMP-68) wraps the selected nodes while the synthetic root remains invisible. Save and auto-save normalize loose nodes into the synthetic root, keep intra-phase edges local, route cross-phase edges to `workflow.edges`, preserve `schema_def`, `merge_function`, positions, and `collapsedGroups`, and send the normalized payload through the authenticated compose API client.

**Error Path UX:** Save blocks only when the editor cannot emit canonical YAML: duplicate IDs, unresolved containment, invalid branch output rows, broken hook refs, or other structural failures from merged client/server validation. Failed auto-save leaves the canvas dirty and shows a recoverable save-warning state instead of discarding work.

**Empty State UX:** After bootstrap succeeds, the canvas shows a blank grid, a left palette with Ask, Branch, and Plugin only, and helper copy for dropping the first node. The synthetic root exists structurally but has no visible boundary.

**NOT Criteria:**
- `SwitchFunctionEditor` must NOT exist.
- The palette must NOT move to the right side.
- The editor must NOT serialize array-based ports or top-level loose nodes.
- Save must NOT serialize `port_type`, `from_port`, `to_port`, or a separate hooks section.
- Inline Python fields must NOT execute through `eval`, `Function`, web workers, Pyodide, or any preview/run affordance.

**Citations:**
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:127` — AC-2 anchors Ask placement from the left palette.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:136` — AC-3 requires per-output rows and no `SwitchFunctionEditor`.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:172` — AC-7 preserves synthetic-root normalization and `schema_def` across save.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:537` — `BranchOutputPort` defines per-output conditions plus typed-port fields.
- [decision] `D-GR-35`
- [decision] `D-58`

### J-2 — Model Nested Fold And Loop Phases

**PRD Reference:** `J-2: Model Nested Fold And Loop Phases` (`/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:241`)

**Step Annotations:**
- Step 1 — Arrange loose nodes: ungrouped Ask, Branch, and Plugin nodes stay in the synthetic root until the user deliberately creates a named phase.
- Step 2 — Create outer fold phase: the selection-rectangle gesture creates a visible `PhaseContainer` (CMP-68) with fold controls in the phase inspector. Nodes moved into it leave the synthetic root but keep their positions in the flat store.
- Step 3 — Create nested loop phase: the inner phase serializes to the parent `children[]` collection, preserves parent containment, and exposes distinct `condition_met` and `max_exceeded` exits on its boundary.
- Step 4 — Collapse and restore: collapsing swaps the visible phase shell to `CollapsedGroupCard`; undo and redo restore both topology and `collapsedGroups` state.

**Error Path UX:** Phase creation rejects containment cycles, duplicate membership between parent and child phases, or any selection including read-only template children. The pre-selection canvas state is preserved and the rejection reason is shown inline.

**Empty State UX:** A newly created named phase with no children renders as a tinted boundary with its mode badge, title, and boundary ports. The synthetic root never renders as a visible phase shell.

**NOT Criteria:**
- The synthetic root must NOT render as a `PhaseContainer`.
- Nested export must NOT duplicate nodes into both parent `nodes[]` and child `children[]` phases.
- Collapse/expand must NOT fall out of undo/redo history.
- Loop phases must NOT collapse their two exits into a single handle.

**Citations:**
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:145` — AC-5 defines fold and nested loop behavior.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:183` — AC-8 makes `collapsedGroups` part of undo/redo.
- [decision] `D-9`
- [decision] `D-27`
- [decision] `D-35`

### J-3 — Import Malformed Or Unsafe YAML

**PRD Reference:** `J-3: Import Malformed Or Unsafe YAML` (`/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:256`)

**Step Annotations:**
- Step 1 — Confirm replacement: the import dialog makes replacement explicit and keeps the current canvas untouched until confirmation.
- Step 2 — Run safety checks before hydration: import enforces `.yaml`/`.yml` extension, YAML content type when present, 5MB maximum size, safe-YAML mode, and alias-expansion/bomb limits before the editor mutates any graph state.
- Step 3 — Normalize and hydrate: parseable YAML is normalized into the editor store by restoring dict-keyed ports, assigning loose nodes to the synthetic root, and rebuilding hook-vs-data visuals from source handles. Parseable-but-invalid workflows open with explicit validation warnings and no local execution of inline Python.

**Error Path UX:** Malformed YAML, oversize files, unsafe alias expansion, or normalization failures leave the current canvas untouched and show a targeted error state. Failure never drops the user into partial hydration or read-only mode.

**Empty State UX:** Importing a valid workflow with only empty named phases renders those phase shells and headers instead of the generic blank-canvas state.

**NOT Criteria:**
- Import must NOT partially hydrate the canvas before safety checks and normalization finish.
- Import must NOT use an unsafe YAML parser or execute custom tags.
- Import must NOT discard the current canvas on failure.
- Import must NOT evaluate `transform_fn`, `merge_function`, or branch conditions locally.

**Citations:**
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:256` — J-3 covers malformed or unsafe YAML.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:80` — Import requires pre-parse extension, size, and safe-loader checks.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:89` — `yaml.safe_load(...)` is the accepted baseline.
- [decision] `D-28`

### J-4 — Schema Endpoint Unavailable On Editor Load

**PRD Reference:** `J-4: Schema Endpoint Unavailable On Editor Load` (`/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:271`)

**Step Annotations:**
- Step 1 — Open editor while schema is down: `SchemaBootstrapGate` is the only visible surface. It shows a blocking error card with retry and back-navigation actions; the canvas, palette, and inspectors do not render.
- Step 2 — Retry after recovery: once `/api/schema/workflow` succeeds, the gate unmounts and the normal left palette, canvas, and inspector flow appear.

**Error Path UX:** Schema failures, timeouts, `401`, and `429` remain visually distinct. `401` delegates to shell-level re-auth while preserving in-memory graph state; `429` shows retry guidance and keeps the route blocked until a retry is possible.

**Empty State UX:** None. When schema bootstrap fails, the editor never reaches a blank-canvas state.

**NOT Criteria:**
- The editor must NOT show a view-only canvas.
- The editor must NOT fall back to bundled `workflow-schema.json` in production.
- Partial initialization must NOT expose editable surfaces against an unknown schema contract.

**Citations:**
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:199` — AC-10 defines blocking schema failure with retry and back navigation.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/broad/architecture.md:229` — `401` triggers session-expired recovery in the compose client.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:246` — `429` responses include retry guidance.
- [decision] `D-GR-22`

### J-5 — Core Editor Works On The Five-table Compose Foundation

**PRD Reference:** `J-5: Core Editor Works On The Five-table Compose Foundation` (`/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:285`)

**Step Annotations:**
- Step 1 — Boot in compose: the route mounts inside the authenticated `tools/compose/frontend` shell and boots only from workflow/version CRUD, roles, output schemas, custom task templates, `POST /validate`, and `GET /api/schema/workflow`.
- Step 2 — Use library-backed controls: role, output-schema, and task-template pickers load from compose foundation endpoints and do not depend on `/api/plugins` or `workflow_entity_refs`.
- Step 3 — Save and validate: manual save, auto-save, and validate use the same authenticated compose API client and preserve versioned workflow history plus canonical nested YAML.

**Error Path UX:** If a foundation-backed picker fails, only that picker shows a scoped unavailable state; the canvas and core save/validate surfaces remain usable.

**Empty State UX:** Empty role, schema, or template libraries show inline empty-state cards explaining that the workflow can still be edited and saved.

**NOT Criteria:**
- Core boot or save must NOT depend on `tools/iriai-workflows`, SQLite, `/api/plugins`, or `workflow_entity_refs`.
- The editor must NOT route core CRUD or validation through any client other than the compose authenticated API client.

**Citations:**
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:207` — AC-11 requires foundation-only core flows.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:102` — REQ-13 defines the allowed dependency boundary.
- [decision] `D-SF5-R1`
- [decision] `D-SF5-R2`

### J-6 — Background Save Or Validation Hits Auth Or Rate-limit Failure

**PRD Reference:** `J-6: Background Save Or Validation Hits Auth Or Rate-limit Failure` (`/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:299`)

**Step Annotations:**
- Step 1 — Trigger auto-save or Validate: the request is sent through the authenticated compose API client with the bearer token attached.
- Step 2 — Hit `401`: the editor preserves the in-memory graph, shows session-expired recovery, and lets shell-level re-auth restore the route instead of clearing the current workflow.
- Step 3 — Hit `429`: the editor shows retry guidance, keeps the workflow dirty, and lets the user continue editing until retry is appropriate.

**Error Path UX:** Auth and rate-limit failures are recoverable states, not destructive resets. The toolbar or validation surface shows the failure, but the canvas remains intact and editable.

**Empty State UX:** None. The user is already editing an existing graph when this journey occurs.

**NOT Criteria:**
- Requests must NOT be sent anonymously.
- `401` and `429` must NOT clear the graph or incorrectly mark the workflow clean.
- Failures must NOT be silent.

**Citations:**
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:215` — AC-12 requires authenticated calls and recoverable `401`/`429` handling.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/broad/architecture.md:228` — The compose API client attaches bearer auth.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/broad/architecture.md:229` — `401` triggers session-expired recovery.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:246` — `429` responses include retry guidance.

### Editor State & Serialization Contract

- `PortDefinition` in the editor TypeScript layer mirrors the canonical typed-port contract: `type_ref?: string`, `schema_def?: Record<string, unknown>`, `description?: string`, `required?: boolean`. Every editor-facing `inputs`, `outputs`, and `hooks` collection is `Record<string, PortDefinition>` keyed by stable port name.
- `BranchOutputPort` extends `PortDefinition` with required `condition: string`. `BranchFlowNode` stores `outputs: Record<string, BranchOutputPort>` and optional `merge_function?: string`; `switch_function`, `output_field`, and node-level branch predicates do not exist.
- `WorkflowSnapshot` includes `nodes`, `edges`, and `collapsedGroups`, so undo and redo restore both topology and collapse state. Snapshots exclude live schema payloads, auth/session tokens, transient validation results, and open inspector windows.
- Synthetic-root normalization is mandatory on save, auto-save, import, export, and reload. Any node without a named phase parent is assigned to the implicit root phase before YAML emission.
- Cross-phase routing is deterministic: edges whose endpoints share the same nearest named phase ancestor stay with that phase; any edge crossing a phase boundary lives on `workflow.edges`.
- `serializeForExport()` runs after normalization and removes auth/session material plus secret-marked values before download.
- All inline Python-bearing fields (`transform_fn`, `merge_function`, `BranchOutputPort.condition`) are stored as text only. Client-side validation is structural and lexical; executable semantics stay on the backend/runtime side.

**Citations:**
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:333` — `BranchNode` uses dict-keyed outputs and rejects stale branch fields.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:336` — `BranchOutputPort` carries `condition`, `type_ref`, and `schema_def`.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:351` — `WorkflowEditorState` includes `ui.paletteSide`, `ui.collapsedGroups`, and schema-gate state.
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:357` — `WorkflowSnapshot` requires `collapsedGroups` and excludes auth/session data.
- [decision] `D-GR-22`
- [decision] `D-GR-10`

### Components

#### CMP-35: AskFlowNode
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/nodes/AskNode.tsx`
- **Description:** Thin React Flow adapter over `AskNodePrimitive` (CMP-102) plus `NodePortDot` (CMP-105). It renders dict-keyed `inputs`, `outputs`, and `hooks`, surfaces `schema_def`-backed ports as first-class handles, and keeps all visual styling delegated to SF-7.
- **Props/Variants:** `nodeData`, `selected`, `validationState`, `readOnly`
- **States:** default, selected, error, warning
- **Citations:** [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:16`; [decision] `D-58`

#### CMP-36: BranchFlowNode
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/nodes/BranchNode.tsx`
- **Description:** Thin React Flow adapter over `BranchNodePrimitive` (CMP-103). It renders `inputs: Record<string, PortDefinition>` and `outputs: Record<string, BranchOutputPort>`, maps output keys directly to handle IDs, shows per-output condition badges, and never exposes a routing-mode toggle or `SwitchFunctionEditor`.
- **Props/Variants:** `nodeData`, `selected`, `validationState`, `readOnly`
- **States:** default, gathering, selected, error
- **Citations:** [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:23`; [decision] `D-GR-35`; [decision] `D-58`

#### CMP-37: PluginFlowNode
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/nodes/PluginNode.tsx`
- **Description:** Thin React Flow adapter over `PluginNodePrimitive` (CMP-104). It renders dict-keyed ports, accepts both data and hook edges, and stays inside the SF-5 compose foundation dependency boundary for core flows.
- **Props/Variants:** `nodeData`, `selected`, `validationState`, `readOnly`
- **States:** configured, unconfigured, selected, error
- **Citations:** [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:102`; [decision] `D-58`

#### CMP-42: DataEdge
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/edges/DataEdge.tsx`
- **Description:** React Flow edge that composes SF-7 `EdgeTypeLabel` (CMP-106), renders type labels and transform badges, and shows red dashed warning styling for type mismatches that are allowed but not clean.
- **Props/Variants:** `sourceType`, `targetType`, `hasTransform`, `validationState`
- **States:** default, with-transform, mismatch-warning, selected
- **Citations:** [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:31`; [decision] `D-GR-22`; [decision] `D-58`

#### CMP-43: HookEdge
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/edges/HookEdge.tsx`
- **Description:** Visual-only React Flow edge for `on_start` and `on_end` lifecycle wiring. Hook-ness is derived from source-handle resolution; the serialized edge remains ordinary `source`/`target` with no separate hooks block and no `transform_fn`.
- **Props/Variants:** `sourceHandle`, `selected`
- **States:** default
- **Citations:** [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:31`; [decision] `D-GR-22`

#### CMP-64: OutputPathsEditor
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/inspectors/OutputPathsEditor.tsx`
- **Description:** Branch-inspector section that owns the ordered rendering of `outputs: Record<string, BranchOutputPort>`. It supports add, rename, reorder, and remove actions while delegating each output row to `PortConditionRow`.
- **Props/Variants:** `outputs`, `onRenameOutput`, `onChangeOutput`, `onAddOutput`, `onRemoveOutput`, `readOnly`
- **States:** default, two-path-starter, validation-error
- **Citations:** [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:333`; [decision] `D-GR-35`

#### CMP-65: MergeFunctionEditor
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/inspectors/MergeFunctionEditor.tsx`
- **Description:** Optional code editor for `merge_function`. It is available only when a branch has 2+ inputs and remains a text-only editor with helper copy listing available input-port variables.
- **Props/Variants:** `mergeFunction`, `inputPorts`, `onChange`, `readOnly`
- **States:** collapsed, expanded, disabled
- **Citations:** [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:23`; [decision] `D-GR-35`; [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:109`

#### CMP-66: PortConditionRow
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/inspectors/PortConditionRow.tsx`
- **Description:** Single editable row for one `BranchOutputPort`. It owns the output key label, rename action, condition-expression editor, remove action, and row-scoped validation messaging.
- **Props/Variants:** `outputKey`, `port`, `onRename`, `onChange`, `onRemove`, `readOnly`
- **States:** default, empty-required, server-invalid
- **Citations:** [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:336`; [decision] `D-GR-35`

#### CMP-68: PhaseContainer
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/phases/PhaseContainer.tsx`
- **Description:** React Flow group node for named sequential, map, fold, and loop phases. Expanded mode renders child nodes inside the boundary; collapsed mode swaps to `CollapsedGroupCard`. The synthetic root never renders as a user-visible phase shell.
- **Props/Variants:** `mode`, `expanded`, `hasNestedChildren`, `validationState`
- **States:** expanded, collapsed, loop-exits-visible, error
- **Citations:** [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:39`; [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:80`; [decision] `D-27`

#### CMP-69: SchemaBootstrapGate
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/editor/schema/SchemaBootstrapGate.tsx`
- **Description:** Route-level loading and failure shell that blocks the editor until both the workflow payload and the canonical schema payload are ready. It is also the enforcement point for the no-fallback rule when schema bootstrap fails.
- **Props/Variants:** `status`, `schemaVersionLabel`, `onRetry`, `onBack`
- **States:** loading, ready, error
- **Citations:** [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:55`; [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:199`; [decision] `D-GR-22`

### Verifiable States

#### CMP-35 (AskFlowNode)
| State | Visual Description |
|-------|-------------------|
| default | White node card with visible data handles and dashed hook handles. Ports using `schema_def` show an explicit inline-schema badge. |
| selected | Cyan selection ring while handle positions remain fixed. |
| error | Red border plus issue badge on the offending port or missing actor binding. |

#### CMP-36 (BranchFlowNode)
| State | Visual Description |
|-------|-------------------|
| default | White card with one output handle per key in `outputs`, each showing the output name and a short condition summary. No switch label or routing-mode badge exists. |
| gathering | Two or more input handles appear on the left edge and a compact `merge(...)` pill appears when `merge_function` is set. |
| error | Red border plus row-level issue badges when an output is missing its required condition or typed-port contract. |

#### CMP-42 (DataEdge)
| State | Visual Description |
|-------|-------------------|
| default | Solid curved line with midpoint type label. |
| with-transform | Solid curved line with midpoint label plus transform badge. |
| mismatch-warning | Red dashed line with warning chip at the midpoint; edge remains selectable and saveable. |

#### CMP-64 (OutputPathsEditor)
| State | Visual Description |
|-------|-------------------|
| default | Ordered list of output rows with name, condition editor, and remove action. No routing-mode toggle or condition-type dropdown is present. |
| two-path-starter | Fresh branch node shows two starter outputs with placeholder names and empty condition editors. |
| validation-error | Header and offending rows show error treatment when outputs are missing required conditions or typed-port rules fail. |

#### CMP-65 (MergeFunctionEditor)
| State | Visual Description |
|-------|-------------------|
| collapsed | Compact “Add merge function” action with no open editor surface. |
| expanded | Inline code editor with helper copy listing available input variables. |
| disabled | Muted helper text explains that `merge_function` is unavailable until the branch has at least two inputs. |

#### CMP-66 (PortConditionRow)
| State | Visual Description |
|-------|-------------------|
| default | Output name plus inline code editor for the condition expression. |
| empty-required | Condition field outlined with warning/error treatment and helper copy explaining that the output is invalid until a condition is provided. |
| server-invalid | Row shows a red validation badge and inline message sourced from merged client/server validation results. |

#### CMP-68 (PhaseContainer)
| State | Visual Description |
|-------|-------------------|
| expanded | Tinted phase boundary with visible child nodes and boundary ports. Nested phases render inside the parent boundary. |
| collapsed | `CollapsedGroupCard` with phase title, mode badge, and node count; no mini-canvas. |
| loop-exits-visible | Separate `condition_met` and `max_exceeded` handles appear on the boundary. |
| error | Red outline and issue badge when containment or serialization validation fails. |

#### CMP-69 (SchemaBootstrapGate)
| State | Visual Description |
|-------|-------------------|
| loading | Full-editor blocking shell with progress indicator and schema-loading copy. No palette, canvas, or inspector surface is visible behind it. |
| ready | Blocking shell disappears and the full editor chrome mounts. |
| error | Blocking error card with failure summary, Retry primary action, and Back to Workflows secondary action. |

### Responsive Behavior

The editor stays desktop-first. Under `768px`, the route remains blocked instead of attempting a compressed authoring surface. From `768px` upward, the palette stays on the left edge at every supported breakpoint; it may compress to an icon rail on narrower tablets, but it never moves to the right side. Floating inspectors may collapse into a single docked inspector column below `1280px`, but the schema gate, synthetic-root behavior, and per-port branch model do not change.

**Citations:**
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/reviews/design-gate-review.md:38` — Compose is desktop-first and blocks mobile.
- [decision] `D-2`

### Interaction Patterns

- **Schema bootstrap and auth:** `SchemaBootstrapGate` is the entry point for workflow load plus schema load. `/api/schema/workflow`, save, auto-save, and validate all use the same authenticated compose API client. `401` delegates to shell-level re-auth and preserves in-memory edits; `429` shows retry guidance and keeps the workflow dirty.
- **Branch authoring:** `OutputPathsEditor` plus `PortConditionRow` are the only branch-routing UI. `MergeFunctionEditor` is the only branch-level function editor and appears only for multi-input gather. `SwitchFunctionEditor`, routing-mode toggles, `output_field`, and node-level branch predicates do not exist.
- **Safe YAML import/export:** import enforces safe-YAML mode, a 5MB limit, alias-expansion limits, and no partial hydration. Export runs after normalization and strips auth/session material plus secret-marked values.
- **No local execution:** the editor never evaluates `transform_fn`, `merge_function`, or branch conditions in the browser. Code editors are syntax-highlighted text fields only; executable semantics stay behind backend `validate()` and the runtime.

**Citations:**
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:62`
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:94`
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/broad/architecture.md:228`
- [decision] `D-GR-35`
- [decision] `D-GR-10`

### Accessibility

- `SchemaBootstrapGate` uses `role="status"` while loading and `role="alert"` on failure; Retry receives initial focus in the failure state.
- The left palette, node cards, and output rows remain keyboard reachable in source order. `PortConditionRow` labels each condition editor with the visible output name.
- Hook edges use both color and dashed stroke; mismatch warnings on `DataEdge` use iconography plus text, not color alone.
- Import, save, and validate failures announce the blocking issue in a live region before focus moves to the offending field, node, phase, or edge.
- Collapsed phases expose an accessible name including title, mode, and collapsed/expanded state. The synthetic root has no accessible phase boundary because it is never rendered.

**Citations:**
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:31`
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:80`
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:109`

### Rejected Alternatives

1. Introduce `SwitchFunctionEditor` or any dual routing mode. Rejected because the canonical branch contract is per-port conditions only and the current PRD explicitly rejects stale branch fields.
2. Keep the palette on the right side. Rejected because the accepted canvas contract is left palette plus centered canvas.
3. Offer a view-only or static-schema fallback when schema bootstrap fails. Rejected because the user must never edit against a stale or unknown runtime contract.
4. Store ports as arrays in the editor store. Rejected because the editor needs stable dict keys for handle IDs, `schema_def` round-trip fidelity, and deterministic output-row editing.
5. Execute inline Python locally for previews or validation hints. Rejected because REQ-14 treats expressions as text data in the editor and keeps execution semantics on the backend/runtime side.
6. Export raw YAML exactly as held in memory, including secret-like values or auth/session material. Rejected because exported YAML can leave the platform and must be redacted.

**Citations:**
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:23`
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:94`
- [code] `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:109`
- [decision] `D-2`
- [decision] `D-GR-22`
- [decision] `D-GR-35`

### Revision Summary

This rewrite removes the stale design assumptions that were still leaking into the plan and system design: no `SwitchFunctionEditor`, no phantom `J-8`, no right-side palette, no array-based ports, no fallback schema boot, and no local execution. The journey set now matches the current PRD `J-1` through `J-6`, `SchemaBootstrapGate` (CMP-69) is a first-class blocking component, `MergeFunctionEditor` (CMP-65) and `PortConditionRow` (CMP-66) are defined with explicit states, and the editor-state contract now calls out dict-keyed typed ports, `schema_def` fidelity, `collapsedGroups` snapshots, auth-aware background requests, safe YAML handling, and export redaction.


---

## Subfeature: Libraries & Registries (libraries-registries)

<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries

SF-7 is the compose library layer that extends SF-5's five-table foundation inside `tools/compose`. Launch scope is limited to Roles, Output Schemas, Task Templates, and Tools. Plugins Registry pages, PluginPicker flows, and delete-time YAML parsing are out of scope. Roles, schemas, and templates use the materialized `workflow_entity_refs` index for delete preflight; tools use persisted role references and stay outside that index. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:12` [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:31` [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:74` [decision] `D-GR-39`

`LibraryCollectionPage` (CMP-134) is the shared list shell. Detail panes use `ResourceStateCard` (CMP-138) for loading, not-found, validation, rate-limited, and generic failure states. Destructive flows always start in `EntityDeleteDialog` (CMP-133) with a GET-based preflight, never a speculative DELETE. `RoleEditorForm` (CMP-135) is a single form, not a wizard. `ToolChecklistGrid` always fetches `GET /api/tools`, and `ActorSlotsEditor` (CMP-137) edits the `actor_slots` JSON array stored on `custom_task_templates`, not a separate per-slot table. Library pages load independently of SF-6 bootstrap state; only persisted workflow saves or deletes refresh reference rows. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:40` [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:49` [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:65` [code] `.iriai/artifacts/features/beced7b1/plan-review-discussion-7.md:97`

---
## Journey Annotations

### J-1 - Create and Use a Role from the Roles Library
**PRD Reference:** `J-39`

**Step Annotations:**
- Step 1 renders `LibraryCollectionPage` in roles mode with search, a primary `New role` action, and stale-while-revalidate list loading. Warm-cache content remains visible while refetch happens in the background.
- Step 2 opens `RoleEditorForm` in the detail pane. The editor is a single scrollable form with sections for name, model, prompt, metadata, and `ToolChecklistGrid`. The checklist groups `Built-in tools` above `Custom tools` and loads both groups from `GET /api/tools`.
- Step 3 confirms success only after the role mutation returns. The roles list, RolePicker data, and relevant editor queries are invalidated together so the saved role becomes selectable without a hard refresh.

**Error Path UX:** `413`, `422`, and `429` keep the editor open. Field-level problems stay inline, while payload-size and rate-limit issues also appear in a summary banner. `401` uses the compose re-auth path rather than a local auth card.

**Empty State UX:** The first-use state shows a concise explainer, a `Create your first role` CTA, and a secondary link toward the Tools Library so the checklist has clear context.

**NOT Criteria:**
- The roles list must NOT blank during background refetch.
- The role editor must NOT become a multi-step wizard.
- `ToolChecklistGrid` must NOT hardcode built-in tools without also loading custom tools from `GET /api/tools`.

**Citations:**
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:31`
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:49`
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:167`
- [code] `.iriai/artifacts/features/beced7b1/plan-review-discussion-7.md:97`

### J-2 - Delete a Role, Schema, or Template Referenced by Saved Workflows
**PRD Reference:** `J-40`

**Step Annotations:**
- Step 1 opens `EntityDeleteDialog` immediately in `checking-references` state and fires `GET /api/{entity}/references/{id}`. The modal reserves list space up front so the body does not jump when the response arrives.
- Step 2 renders the blocked state from `workflow_entity_refs`. Copy always says `saved workflows`, because only persisted workflow saves update reference rows. Unsaved editor state, the synthetic root phase, and removed node types never count as active references.
- Step 3 reruns the same preflight on every open and retry. If a DELETE race still returns a conflict, the dialog rehydrates into the same blocked-workflows presentation instead of switching to a generic toast.

**Error Path UX:** Transport failures keep the modal open with Retry and Close actions. The UI never falls back to browser-side YAML parsing; stale data is recovered through retry or backend reconciliation, not local inspection.

**Empty State UX:** A zero-reference response switches the modal to a normal recoverable delete confirmation with Cancel and Delete actions and no reference list.

**NOT Criteria:**
- The UI must NOT call DELETE merely to discover references.
- The blocked state must NOT mention `open workflows`, the synthetic root phase, or plugin-specific warnings.
- The frontend must NOT parse workflow YAML to compensate for missing reference data.

**Citations:**
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:12`
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:21`
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:182`
- [decision] `D-GR-26`
- [decision] `D-GR-39`

### J-3 - Delete a Tool Referenced by Roles
**PRD Reference:** `J-41`

**Step Annotations:**
- Step 1 uses the same `EntityDeleteDialog` shell, but the preflight resolves persisted role usage for the selected tool rather than `workflow_entity_refs`. The blocked state lists role names only.
- Step 2 keeps tool delete semantics symmetrical with workflow-backed deletes: the dialog still starts with a read-only preflight and only reveals the destructive confirm when persisted role references are gone.
- Step 3 invalidates the tools list and any cached role-editor checklist queries so the removed tool disappears from both the Tools page and later role-edit sessions.

**Error Path UX:** Tool preflight failures show a retryable inline error in the modal. Tool save or delete failures surface an editor banner plus the shared toast pattern. `429` never discards in-progress form values.

**Empty State UX:** The Tools route remains useful even before custom tools exist: the page shows built-in tools in a read-only section and a `Register custom tool` CTA for the user-owned section.

**Verification Cues:**
- Blocked state heading reads `Can't delete tool yet`.
- The reference list contains role names only.
- No workflow names or workflow validation chips appear anywhere in the modal.

**NOT Criteria:**
- Tool deletion must NOT query `workflow_entity_refs`.
- Tool deletion must NOT show workflow names.
- `ToolChecklistGrid` must NOT continue showing deleted custom tools after the invalidate-and-refetch cycle finishes.

**Citations:**
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:31`
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:121`
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:199`
- [code] `.iriai/artifacts/features/beced7b1/plan-review-discussion-7.md:97`

### J-4 - Persist Actor Slots in a Task Template
**PRD Reference:** `J-42`

**Step Annotations:**
- Step 1 renders `ActorSlotsEditor` beside the task-template canvas summary as a standalone, reusable repeater. Each row contains slot key, actor-type constraint, and optional default role in one horizontal unit with add and remove controls.
- Step 2 saves the full `actor_slots` JSON array as part of the template persistence flow. Success is confirmed from the returned template payload and again on a full refresh path.
- Step 3 keeps the editor local while the user is drafting, but the server response is always the source of truth for the persisted slot list.

**Error Path UX:** Duplicate names, blank names, invalid default-role bindings, `413`, and `422` all keep the editor in place. Row errors render inline and the section header shows a summary banner with the issue count.

**Empty State UX:** A new template with no slots renders a dashed empty panel titled `No actor slots defined yet`, a primary `Add actor slot` action, and helper text explaining where slot bindings are reused.

**Verification Cues:**
- Saving and refreshing reopens the template with the same slot rows.
- Success copy explicitly mentions that actor slots were saved to the reusable template.
- The returned detail payload includes `actor_slots`, not only canvas data.

**NOT Criteria:**
- Actor slots must NOT exist only in local browser state.
- The persistence model must NOT imply a separate per-row CRUD table.
- Save success must NOT omit `actor_slots` from the follow-up detail response.

**Citations:**
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:40`
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:98`
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:353`
- [code] `.iriai/artifacts/features/beced7b1/plan-review-discussion-7.md:97`

### J-5 - Reject Invalid Actor Slot Definitions
**PRD Reference:** `J-43`

**Step Annotations:**
- Step 1 validates slot rows as the user types and again before save. Duplicate keys, blank names, and invalid default bindings are shown on the affected rows immediately.
- Step 2 leaves the draft intact after any rejected request so the user can fix the exact rows that failed.
- Step 3 clears the error summary only after a successful save response or an explicit row correction.

**Error Path UX:** Server-returned `422` and `413` errors are mapped back to the offending rows when possible; otherwise they appear in a section banner and, for full-route failures, in `ResourceStateCard`.

**Empty State UX:** No separate empty state beyond J-4; invalid-save recovery happens inside the same editor context.

**Verification Cues:**
- Duplicate names show inline error text such as `Slot names must be unique`.
- Save remains disabled while invalid rows are present.
- After correction, the next save succeeds and refresh reopens only the corrected slot data.

**NOT Criteria:**
- Invalid slot rows must NOT appear visually valid.
- The server must NOT silently normalize or drop bad slot entries.
- A failed save must NOT partially persist the slot array.

**Citations:**
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:224`
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:230`
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:65`
- [research] `OWASP Input Validation Cheat Sheet`

---
## Component Hierarchy

- Library routes use `LibraryCollectionPage` for list chrome, `ResourceStateCard` for route fallback, and `EntityDeleteDialog` for delete preflight.
- Role detail panes use `RoleEditorForm` plus `ToolChecklistGrid`.
- Schema and template detail panes reuse the same list shell and delete dialog; template detail adds `ActorSlotsEditor`.
- Tool detail panes use `ToolEditorForm`; built-in tools stay read-only while custom tools open the editor.
- `useLibraryList` owns stale-while-revalidate list state, `useReferenceCheck` owns GET-based delete preflight, and `ToolChecklistGrid` always reads `GET /api/tools`.

**Citations:**
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:31`
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:49`
- [decision] `D-GR-39`

---
## Component Definitions

### CMP-133: EntityDeleteDialog
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/libraries/shared/EntityDeleteDialog.tsx`
- **Description:** Shared delete modal for roles, schemas, templates, and tools. Opens in a read-only preflight state, resolves to blocked or confirm-delete, and uses the same visual shell for workflow-backed and role-backed conflicts while keeping their data sources distinct.
- **Props / Variants:** `entityType ('roles' | 'schemas' | 'templates' | 'tools'), entityName, isOpen, preflightState ('loading' | 'blocked-workflows' | 'blocked-roles' | 'ready' | 'error'), references[], onRetry, onClose, onConfirmDelete`
- **States:** `checking-references`, `blocked-by-workflows`, `blocked-by-roles`, `confirm-delete`, `reference-check-error`
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:21`
  - [decision] `D-GR-26`
  - [decision] `D-GR-39`

### CMP-134: LibraryCollectionPage
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/libraries/shared/LibraryCollectionPage.tsx`
- **Description:** Reusable list shell for Roles, Output Schemas, Task Templates, and Tools. Owns search, create action, stale-while-revalidate loading treatment, route-level empty states, and selected-row continuity while detail panes change.
- **Props / Variants:** `entityType ('roles' | 'schemas' | 'templates' | 'tools'), viewMode ('grid' | 'list'), items[], queryState ('loading' | 'empty' | 'error' | 'rate-limited' | 'ready'), selectedId?, onCreate, onSelect, onSearch`
- **States:** `loading`, `empty`, `error`, `rate-limited`, `populated`
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:31`
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:49`
  - [code] `.iriai/artifacts/features/beced7b1/plan-review-discussion-7.md:97`

### CMP-135: RoleEditorForm
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/libraries/roles/RoleEditorForm.tsx`
- **Description:** Single-page form editor for role name, model, prompt, metadata, and grouped tool selection. Uses the shared entity-name regex `^[a-zA-Z_][a-zA-Z0-9_. -]{0,199}$` with real-time feedback and never hides required inputs behind step navigation.
- **Props / Variants:** `mode ('create' | 'edit'), roleDraft, toolCatalog, validationState ('idle' | 'invalid' | 'saving' | 'saved' | 'rate-limited'), onSave, onDelete, onCancel`
- **States:** `draft`, `invalid`, `saving`, `saved`, `rate-limited`
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:31`
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:65`
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:419`

### CMP-136: ToolEditorForm
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/libraries/tools/ToolEditorForm.tsx`
- **Description:** Full CRUD editor for custom tools. Built-in tools render through the same page shell but remain read-only. Tool-name guidance uses `^[a-zA-Z_][a-zA-Z0-9_.-]*$`, and payload-size feedback matches the shared 256KB JSON limit.
- **Props / Variants:** `mode ('create' | 'edit'), toolDraft, validationState ('idle' | 'invalid' | 'saving' | 'saved' | 'rate-limited'), onSave, onDelete, onCancel`
- **States:** `draft`, `invalid`, `saving`, `saved`, `rate-limited`
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:31`
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:65`
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:377`

### CMP-137: ActorSlotsEditor
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/libraries/templates/ActorSlotsEditor.tsx`
- **Description:** Standalone reusable repeater for the `actor_slots` JSON array on `custom_task_templates`. Each row edits slot key, actor-type constraint, and optional default role. Persistence still happens through the template save contract.
- **Props / Variants:** `slots[], availableRoles[], editorState ('empty' | 'editing' | 'invalid' | 'saving' | 'saved'), onAddSlot, onUpdateSlot, onRemoveSlot`
- **States:** `empty`, `populated`, `invalid`, `saving`, `saved`
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:40`
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:353`
  - [code] `.iriai/artifacts/features/beced7b1/plan-review-discussion-7.md:97`

### CMP-138: ResourceStateCard
- **Status:** new
- **Location:** `tools/compose/frontend/src/features/libraries/shared/ResourceStateCard.tsx`
- **Description:** Route-level fallback surface for list and detail panes. Distinguishes loading, not-found, validation, rate-limited, and generic error outcomes without leaking cross-user existence or collapsing the surrounding library shell.
- **Props / Variants:** `tone ('neutral' | 'warning' | 'error'), state ('loading' | 'not-found' | 'validation' | 'rate-limited' | 'error'), title, body, actionLabel?, onAction?`
- **States:** `loading`, `not-found`, `validation`, `rate-limited`, `error`
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:57`
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:65`
  - [code] `.iriai/artifacts/features/beced7b1/plan-review-discussion-7.md:97`

### Supporting Components
- `ToolChecklistGrid` lives in `tools/compose/frontend/src/features/libraries/tools/ToolChecklistGrid.tsx` and always fetches `GET /api/tools`, grouping built-in and custom tools under separate headings.
- Library `EntityType` unions and type maps are limited to `roles`, `schemas`, `templates`, and `tools`; no plugin entry is valid in SF-7.

**Citations:**
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:31`
- [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:74`
- [code] `.iriai/artifacts/features/beced7b1/plan-review-discussion-8.md:325`

---
## Verifiable States

### CMP-133 - EntityDeleteDialog

| State | Visual Description |
|------|---------------------|
| checking-references | Modal titled `Delete {entityName}?` with a spinner row labeled `Checking references...`; destructive action hidden or disabled; list height reserved. |
| blocked-by-workflows | Warning modal titled `Can't delete yet` with copy that says `saved workflows`, followed by a stacked list of workflow names; footer shows only Close. |
| blocked-by-roles | Warning modal titled `Can't delete tool yet` with a stacked list of role names and no workflow names; footer shows only Close. |
| confirm-delete | Neutral confirmation modal with recoverability copy and Cancel plus Delete actions; no reference list rendered. |
| reference-check-error | Modal contains an inline error banner `Couldn't verify references`, Retry and Close actions, and no destructive action. |

### CMP-134 - LibraryCollectionPage

| State | Visual Description |
|------|---------------------|
| loading | Toolbar remains visible and list area shows skeleton cards or rows; surrounding shell stays interactive. |
| empty | Centered empty panel with entity-specific heading, one primary CTA, and one short helper sentence. |
| error | Inline error panel inside the content area with retry action; sidebar and toolbar remain visible. |
| rate-limited | Warning panel with retry guidance and visible `Retry after` copy when supplied. |
| populated | Search field, create action, and at least one selectable entity card or row render together. |

### CMP-135 - RoleEditorForm

| State | Visual Description |
|------|---------------------|
| draft | All role fields are visible in one form and the tools section shows grouped built-in and custom checklists. |
| invalid | One or more fields show inline helper text and a summary banner states how many issues must be fixed before save. |
| saving | Sticky footer save action shows spinner text such as `Saving role...` while inputs are temporarily disabled. |
| saved | Success toast appears and the dirty indicator is cleared. |
| rate-limited | Top banner explains the temporary limit, keeps draft values intact, and leaves Save disabled until retry is allowed. |

### CMP-136 - ToolEditorForm

| State | Visual Description |
|------|---------------------|
| draft | Tool form shows name, description, source, and input schema fields; delete action is visible only for custom tools. |
| invalid | Name or schema validation renders inline; allowed-character guidance is visible next to the name field. |
| saving | Save action shows spinner text and the current form stays in place. |
| saved | Success toast appears and the list row reflects the latest tool metadata after refetch. |
| rate-limited | Warning banner preserves the current draft and explains when to retry. |

### CMP-137 - ActorSlotsEditor

| State | Visual Description |
|------|---------------------|
| empty | Dashed panel titled `No actor slots defined yet` with a primary `Add actor slot` action and helper text. |
| populated | Table-like list of rows showing slot key, actor-type constraint, optional default role, and a row remove action. |
| invalid | Row-level errors such as `Slot names must be unique` are visible and the section header repeats the issue count. |
| saving | Section save affordance shows pending text while the row list remains visible. |
| saved | Inline success note confirms `Actor slots saved to template` and the saved rows remain visible after refresh. |

### CMP-138 - ResourceStateCard

| State | Visual Description |
|------|---------------------|
| loading | Neutral card with spinner and route-specific loading copy such as `Loading tool details...`. |
| not-found | Neutral card with a `not found` heading, short explanation, and a back-to-library action; no forbidden messaging. |
| validation | Warning card with a `Request rejected` heading and a list of `413` or `422` issues. |
| rate-limited | Warning card with retry guidance and visible wait messaging derived from the response. |
| error | Error card with `Request failed`, concise retry guidance, and a primary Retry action. |

---
## Responsive Behavior

SF-7 stays desktop-first and inherits the compose shell's supported-screen policy instead of defining a separate mobile CRUD flow. Within the supported desktop layout, list routes stay usable at narrower widths by collapsing to a single content column, while wide desktop uses a list-plus-detail workspace. `EntityDeleteDialog` grows modestly on wider viewports, but its content model does not change. `ActorSlotsEditor` keeps each row on one line when space allows and stacks row controls only when the existing compose detail pane becomes narrow. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:49`

---
## Interaction Patterns

- **Data fetching:** Library pages use stale-while-revalidate behavior. Cached lists render immediately, background refetches update silently, and route chrome stays mounted during list refresh. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:49`
- **Delete safety:** Roles, schemas, and templates use `GET /api/{entity}/references/{id}` before any DELETE. DELETE rechecks the same persisted model to protect against races. Tools use the same dialog pattern but with role-backed preflight data instead of `workflow_entity_refs`. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:21` [decision] `D-GR-39`
- **Reference freshness:** SF-7 relies on SF-5 post-commit mutation hooks to refresh or purge `workflow_entity_refs`, plus periodic reconciliation and an admin-only manual reconcile endpoint. User-facing flows never expose those controls and never compensate by parsing YAML in the browser. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:514` [decision] `D-GR-39`
- **Tool catalog consumption:** `ToolChecklistGrid` reads `GET /api/tools`; built-in and custom tools are visually separated but originate from one canonical catalog response. Tool mutations invalidate both Tools-page caches and role-editor checklist caches. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:31`
- **Validation and limits:** Roles, schemas, and templates use the shared regex `^[a-zA-Z_][a-zA-Z0-9_. -]{0,199}$`; tools use `^[a-zA-Z_][a-zA-Z0-9_.-]*$`. JSON bodies cap at 256KB and rejected requests surface `413` or `422` without clearing current drafts. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:65` [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:377` [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:419`
- **Auth and tenancy:** `401` follows the shared compose auth recovery flow; cross-user fetches resolve as `404` and render the neutral not-found state rather than a forbidden state. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:57`

---
## Accessibility Notes

- `EntityDeleteDialog` uses `role='alertdialog'`, moves focus to the title on open, traps focus while active, and returns focus to the invoking delete control on close.
- Reference results render as semantic lists so assistive tech can count blocked workflows or blocked roles.
- `ToolChecklistGrid` uses grouped fieldsets with visible legends for `Built-in tools` and `Custom tools`.
- Inline validation is duplicated with row-level helper text and a summary banner so invalid actor-slot rows and invalid names are not conveyed by color alone.
- `ResourceStateCard` headings distinguish `not found`, `request rejected`, `too many requests`, and generic failure states semantically, not just chromatically.
- Retry actions for validation-free failures receive focus after the error state renders so keyboard users can recover immediately. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:57` [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:65`

---
## Alternatives Considered

1. Keep YAML parsing on delete. Rejected because the accepted architecture is a materialized `workflow_entity_refs` index maintained from mutation hooks, not delete-time parsing. [decision] `D-GR-39`
2. Keep Plugins Registry or PluginPicker surfaces. Rejected because SF-7 launch scope is limited to Roles, Output Schemas, Task Templates, and Tools. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:74`
3. Preserve the old multi-step role editor. Rejected because the role editor needs one visible form surface with inline validation and live tool grouping. [code] `.iriai/artifacts/features/beced7b1/plan-review-discussion-7.md:97`
4. Store actor slots in a separate table with per-slot CRUD endpoints. Rejected because the accepted contract is a JSON array on `custom_task_templates`. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:353`
5. Let `ToolChecklistGrid` use built-in constants only. Rejected because registered tools must come from the same `GET /api/tools` catalog used by the Tools Library. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:31`
6. Use DELETE `409` as the first reference-discovery mechanism. Rejected because delete preflight must be read-only and explicit. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:21`

---
## Rationale

RR-7 is about making the library UX honest. Users must see reference conflicts before destructive actions, understand that only saved workflows count toward delete blocking, and manage tools and actor slots through explicit library surfaces rather than stale plugin concepts or hidden side tables. The revised design therefore makes three boundaries visible: workflow-backed references come from `workflow_entity_refs`, tool references come from saved role usage, and actor slots persist with the template record itself. [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:12` [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:31` [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:353`

This replacement also reanchors the artifact to the accepted component vocabulary. `CMP-133` through `CMP-138` are the canonical SF-7 UI components, `ToolChecklistGrid` consumes `GET /api/tools`, and all paths point at `tools/compose/frontend/src/`. The result gives the plan and system-design rewrites a single UX contract to implement without reintroducing plugin scope, YAML scans, or mismatched component IDs. [code] `.iriai/artifacts/features/beced7b1/plan-review-discussion-7.md:97`

---
## Component Summary

| ID | Name | Subfeature | Status | Location |
|----|------|------------|--------|----------|
| CMP-133 | EntityDeleteDialog | libraries-registries | new | `tools/compose/frontend/src/features/libraries/shared/EntityDeleteDialog.tsx` |
| CMP-134 | LibraryCollectionPage | libraries-registries | new | `tools/compose/frontend/src/features/libraries/shared/LibraryCollectionPage.tsx` |
| CMP-135 | RoleEditorForm | libraries-registries | new | `tools/compose/frontend/src/features/libraries/roles/RoleEditorForm.tsx` |
| CMP-136 | ToolEditorForm | libraries-registries | new | `tools/compose/frontend/src/features/libraries/tools/ToolEditorForm.tsx` |
| CMP-137 | ActorSlotsEditor | libraries-registries | new | `tools/compose/frontend/src/features/libraries/templates/ActorSlotsEditor.tsx` |
| CMP-138 | ResourceStateCard | libraries-registries | new | `tools/compose/frontend/src/features/libraries/shared/ResourceStateCard.tsx` |

