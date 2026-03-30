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

### SF-1: Declarative Schema & Primitives

<!-- SF: declarative-schema -->



## Architecture

### Decision Log

| ID | Decision | Rationale | Citation |
|----|----------|-----------|----------|
| D-SF1-1 | **Canonical module path: `iriai_compose/schema/`** (no `declarative` intermediate). All downstream SFs import from `iriai_compose.schema`. The design doc's reference to `iriai_compose.declarative.schema` is superseded by this decision — `iriai_compose.schema` is the single authoritative import path for all schema models, validation, YAML I/O, and JSON Schema generation. | Cleaner import path; no other schema concept to disambiguate from. Confirmed as canonical per [C-2] integration review. | [decision: user Q2; decision: C-2 — canonical path confirmation] |
| D-SF1-2 | **Per-port condition routing model per D-GR-35**: BranchNode uses `outputs: dict[str, BranchOutputPort]` where each output port carries its own `condition` expression. Routing is non-exclusive — multiple ports can fire simultaneously if multiple conditions evaluate truthy. `switch_function` is REJECTED (D-GR-35). `merge_function` is orthogonal — it merges multi-input data before condition evaluation on gather (multi-input) BranchNodes only. | Per-port conditions handle all data-driven branching (Gate approved/rejected, multi-path fan-out). `switch_function` was rejected by D-GR-35 in favor of per-port conditions. `merge_function` is not a routing mechanism. | [decision: user — port routing model; decision: D-GR-35 — switch_function rejected] |
| D-SF1-3 | Interview = Loop phase + Ask nodes (composed from primitives) | Keeps 3-node-type model pure; verbose but maximally composable | [decision: user Q3] |
| D-SF1-4 | Strict phase I/O boundary — first node input wired to `$input`, last node output wired to `$output` | External edges only touch phase ports; phase mode controls iteration on output | [decision: user Q4] |
| D-SF1-5 | Loop exit condition is Python expression on phase output, not BranchNode | Phase evaluates `exit_condition` against output; true = exit via `condition_met`, false = re-execute | [decision: user Q4 derivative] |
| D-SF1-6 | Schema version as string `"1.0"` | Standard practice; simple semver string | [research: JSON Schema $schema patterns] |
| D-SF1-7 | Pydantic v2 models with `model_json_schema()` for JSON Schema generation | Matches existing iriai-compose dependency (pydantic>=2.0) | [code: iriai-compose/pyproject.toml] |
| D-SF1-8 | YAML serialization via `pyyaml` (already transitive via pydantic) | No `ruamel.yaml` dependency needed for SF-1; round-trip preservation is SF-3's concern | [code: iriai-compose/pyproject.toml] |
| D-SF1-9 | Discriminated union on `type` field for nodes | Enables JSON Schema `oneOf` with discriminator for UI consumption | [code: iriai-compose/iriai_compose/tasks.py — Task is ABC] |
| D-SF1-10 | Single `PortDefinition` type for ALL ports — data inputs, data outputs, hooks | Ports are ports. The container field (inputs/outputs/hooks) determines role. Hooks are visually identical 12px circles [D-22]. No HookDefinition. | [decision: user feedback on plan] |
| D-SF1-11 | NodeBase and PhaseDefinition share identical default port signatures | Both default to `[PortDefinition(name="input")]` for inputs, `[PortDefinition(name="output")]` for outputs, and `[PortDefinition(name="on_start"), PortDefinition(name="on_end")]` for hooks. All four node types (Ask, Branch, Plugin, Error per D-GR-36) inherit these defaults from NodeBase and then specialize via validators where needed. ErrorNode overrides to have NO outputs and NO hooks. This ensures every element in the DAG has connectable ports from the moment it is created. | [decision: user feedback — consistency fix] |
| D-SF1-12 | AskNode: 1 fixed input, user-defined outputs (1+), mutually exclusive data-driven routing | Actor produces output, conditions on output ports evaluate against it. Replaces `options` field. Port names ARE the options. | [decision: user — entity hardening] |
| D-SF1-13 | BranchNode: user-defined inputs (1+) for gather/join, user-defined outputs (2+) as `dict[str, BranchOutputPort]`, non-exclusive per-port conditions [D-GR-35] | Branch is the DAG coordination primitive — where workflows converge (gather multiple inputs) AND diverge (dispatch to parallel paths). Only node type with user-configurable inputs. Each `BranchOutputPort` carries its own `condition` expression. Non-exclusive fan-out: all ports whose condition evaluates truthy fire simultaneously. `switch_function` is REJECTED. `merge_function` valid only on gather (multi-input) BranchNodes. | [decision: user — Branch as gather/dispatch; decision: D-GR-35 — per-port conditions, switch_function rejected] |
| D-SF1-14 | PluginNode: 1 fixed input, user-defined outputs (0+), mutually exclusive | Same routing model as Ask for output port conditions, but outputs can be empty (0 ports = fire-and-forget side effect). Think of a plugin as an API call — it may or may not return data. | [decision: user — plugin fire-and-forget] |
| D-SF1-15 | Expression fields use `str` with documented evaluation contexts | Python expression strings are consistent across all evaluable fields (conditions, transforms, exit_condition, reducer, accumulator_init, merge_function, collection). Each documents available variables. `str` is sufficient — runtime sandboxing is SF-2's concern. Typed alternatives (AST, structured conditions) rejected as too restrictive for the patterns found in iriai-build-v2. | [decision: hardening pass — PhaseConfig typing analysis] |
| D-SF1-16 | `fresh_sessions: bool = False` on LoopConfig and FoldConfig for phase-iteration session management | Session clearing happens at loop iteration boundaries in iriai-build-v2 (`_clear_agent_session` called at start of each `while True` iteration in `interview_gate_review`). Session keys are actor-scoped (`{actor.name}:{feature.id}`), but clearing is triggered by phase iteration lifecycle. InteractionActor uses Pending objects with no session_key, so blanket clearing only affects AgentActor (safe). Same actor can participate in both persistent-session contexts (sequential phase) and fresh-session contexts (loop phase with `fresh_sessions: true`) without needing duplicate actor entries. | [code: iriai-build-v2/workflows/_common/_helpers.py — _clear_agent_session at loop iteration boundary; code: iriai-compose/iriai_compose/actors.py:30 — AgentActor.persistent; decision: user feedback — fresh_sessions is phase iteration concern] |
| D-SF1-17 | **PluginNode uses `plugin_ref` only — no `instance_ref`, no root `plugin_instances` registry.** PluginNode references a plugin type via `plugin_ref` (references `workflow.plugins` key) with optional inline `config`. The root `plugin_instances` registry is REJECTED per D-GR-35 closed-root contract. `instance_ref` is not a valid PluginNode field. | Clean separation: plugin types defined in `workflow.plugins`, inline config on each PluginNode. No need for a pre-configured instance registry. | [decision: D-GR-35 — closed root; decision: PRD REQ-33 — no root plugin_instances] |
| D-SF1-18 | `output_type` and `output_schema` are mutually exclusive on NodeBase and PhaseDefinition; `input_type` and `input_schema` are mutually exclusive on NodeBase and PhaseDefinition | All four fields live on NodeBase (inherited by Ask, Branch, Plugin) and on PhaseDefinition. Each pair defines the element's I/O data structure — one by reference to `workflow.types`, the other inline. Having both in a pair is ambiguous. Validators enforce at most one per pair. Moving these from AskNode to NodeBase makes type declarations uniform: PluginNodes declare their output structure (e.g., `collect_files` returns a file list), BranchNodes declare their merged output shape, and ALL nodes can declare expected input structure for edge type-checking. | [code: iriai-compose/iriai_compose/tasks.py:54 — Ask.output_type is singular; decision: user — I/O type fields on all nodes and phases] |
| D-SF1-19 | PluginNode outputs 0+ (fire-and-forget allowed) | Plugins like `git_commit_push`, `preview_cleanup`, `doc_hosting.push` perform side effects without meaningful return data. Allowing `outputs: []` means no downstream edge is required. Other node types (Ask, Branch) retain 1+ outputs. | [decision: user — plugin I/O clarification] |
| D-SF1-20 | Async gather/barrier is runner responsibility, not schema | When BranchNode has multiple input ports, the runner (SF-2) waits for all connected input ports to receive data before firing the node. The schema declares the ports; the runner implements the barrier. This matches iriai-build-v2's `runner.parallel()` + `asyncio.gather()` pattern but at the DAG edge level. SF-1 makes no claims about execution order or async behavior. | [decision: user — async semantics boundary] |
| D-SF1-21 | Single `Edge` type for all connections — data edges AND hook edges | Edges are edges. The source port's container field (outputs vs hooks) determines whether the edge is a data edge or a hook edge, just as PortDefinition's container determines port role [D-SF1-10]. Hook edges are simply edges where the source resolves to a port in a node/phase's `hooks` list. Validation enforces `transform_fn=None` for hook-sourced edges. The UI renders hook-sourced edges as dashed purple [D-22] and data-sourced edges as solid with type labels — determined at render time by checking the source port's container, not by a schema-level type distinction. This eliminates `HookEdge` as a separate model and `hook_edges` as a separate list on `PhaseDefinition` and `WorkflowConfig`. All edges live in a single `edges` list. | [decision: user — merge HookEdge into Edge, matching PortDefinition unification] |
| D-SF1-22 | `input_type`/`input_schema` and `output_type`/`output_schema` on NodeBase and PhaseDefinition — not AskNode-specific | All nodes and phases declare their expected input and produced output data structures. `input_type: str` references `workflow.types`; `input_schema: dict` is inline JSON Schema. Same for `output_type`/`output_schema`. Each pair is mutually exclusive (validator enforced). This enables: (1) edge type-checking — source output type vs target input type, (2) self-documenting nodes at the schema level, (3) UI showing "expects: PRD" and "produces: TechnicalPlan" on any node, (4) PluginNodes declaring typed outputs (`collect_files` → file list), (5) BranchNodes declaring merged output shape. `PortDefinition.type_ref` remains for per-port granularity on multi-port nodes — port-level `type_ref` takes precedence over node-level type in type resolution. | [decision: user — I/O type fields on all nodes and phases] |
| D-SF1-23 | Store registry — `stores: dict[str, StoreDefinition]` on WorkflowConfig | Named stores declare the interface (keys, types, descriptions). Runner instantiates implementations. Three modes: typed keys (with type_ref), untyped keys (no type_ref, accepts Any), open stores (no keys dict, any key accepted). Dot notation for references: `"store_name.key_name"`. No implementation details (no Postgres/filesystem config). | [code: iriai-compose/storage.py:ArtifactStore; code: iriai-build-v2/storage/artifacts.py:PostgresArtifactStore; decision: user — store as schema entity] |
| D-SF1-24 | Context hierarchy — `context_keys` + `context_text` at workflow, phase, actor, node levels | Four-level context: WorkflowConfig (global), PhaseDefinition (phase-scoped), ActorDefinition (actor baseline), NodeBase (per-node). Runtime merges workflow → phase → actor → node (deduped). `context_keys: list[str]` for store refs via dot notation. `context_text: dict[str, str]` for inline named text snippets. Separate fields — not unified list. | [code: iriai-compose/runner.py:225-262 — resolve() merges actor + task keys; decision: user — 1B separate fields] |
| D-SF1-25 | Context store bindings on ActorDefinition — `context_store` + `handover_key` | `context_store: str \| None` declares which named store for context resolution. `handover_key: str \| None` is dot-notation store ref for actor's handover document. Store bindings only — context management strategy (compaction, summarization) is SF-2 runtime config. | [code: iriai-build-v2/_helpers.py — HandoverDoc at artifacts.put("handover",...); decision: user — 2B store bindings in schema] |
| D-SF1-26 | Dot notation for all store references — `"store_name.key_name"` | All store refs use dot notation: `artifact_key`, `context_keys`, `handover_key`. No separate `store` field on nodes — store target embedded in namespace prefix. No dot = references first declared store (implicit default). Validation parses and checks store existence. | [decision: user — dot notation, no separate store field] |
| D-SF1-27 | Artifact hosting as DAG topology — no schema config | Artifact hosting (HostedInterview pattern) represented as: (1) node writes to store via `artifact_key`, (2) `on_end` hook fires to `doc_hosting` PluginNode, (3) plugin reads from store and pushes to hosting. No `mirror_path` or implementation details on StoreDefinition. Runner handles store implementation. | [code: iriai-build-v2/_common/_tasks.py:HostedInterview; decision: user — no mirror_path] |
| D-SF1-28 | **`switch_function` is REJECTED per D-GR-35.** BranchNode uses only per-port `condition` expressions on `BranchOutputPort` entries for routing. Routing is non-exclusive — multiple output ports may fire simultaneously if multiple conditions evaluate truthy. `merge_function` is orthogonal — it merges multi-input data BEFORE condition evaluation on gather (multi-input) BranchNodes only. Validation MUST reject any BranchNode that includes a `switch_function` field, with guidance to migrate to per-port conditions. This supersedes D-28's "programmatic switch" concept — all routing is expressed through per-port conditions. | [decision: D-GR-35 — switch_function rejected; per-port conditions authoritative] |
| D-SF1-29 | `artifact_key` auto-write semantics — runner writes node output to store automatically | When a node has `artifact_key` set (e.g., `"artifacts.prd"`), the runner (SF-2) automatically writes the node's output to that store key after execution. Execution order: (1) node executes → produces output, (2) if `artifact_key` set, runner writes output to store at that key, (3) output port conditions evaluate (per-port BranchOutputPort.condition), (4) matching ports fire with the output data (optionally transformed via edge `transform_fn`). This replaces explicit `runner.artifacts.put(key, value, feature=feature)` calls from iriai-build-v2. Impact: SF-4 migration needs fewer PluginNodes — simple artifact storage is implicit via `artifact_key`, not requiring explicit store-write Plugin nodes. Only side-effect operations (hosting, MCP calls, git) still need Plugin nodes. | [decision: C-4 — artifact_key auto-write clarification; code: iriai-build-v2/_helpers.py — explicit runner.artifacts.put() calls] |
| D-SF1-30 | Workflow-level I/O definitions — `inputs` and `outputs` on WorkflowConfig | `inputs: list[WorkflowInputDefinition]` declares what the workflow expects as input (name, type_ref, required, default). `outputs: list[WorkflowOutputDefinition]` declares what the workflow produces (name, type_ref, description). SF-2 uses these for workflow-level I/O validation: verifying all required inputs are provided at `run()` time, and all declared outputs are produced by the time execution completes. `type_ref` references `workflow.types` keys. Validation checks type refs resolve. | [decision: H-2 — workflow I/O for SF-2 validation] |

### Entity Hardening: iriai-build-v2 Validation Results

This section documents the systematic validation of every schema entity against the iriai-build-v2 codebase (planning workflow: 6 phases ~50 nodes, develop workflow: 7 phases ~60 nodes, bugfix workflow: 8 phases ~35 nodes). Every imperative pattern was traced to its declarative equivalent.

#### Task Type Mapping (Existing → Declarative)

| Existing (iriai_compose) | Declarative | Validation Notes |
|--------------------------|-------------|------------------|
| `Ask` (actor, prompt, output_type) | `AskNode` (single output) | Direct 1:1 mapping. `Ask.actor` → `AskNode.actor` ref. `Ask.output_type: type[BaseModel]` → `AskNode.output_type: str` (name ref, inherited from NodeBase) or `AskNode.output_schema: dict` (inline, inherited from NodeBase). `Ask.prompt` → `AskNode.prompt`. Verified against 40+ Ask usages across all workflows. [code: iriai-compose/tasks.py:49-63] |
| `Interview` (questioner, responder, initial_prompt, done) | Loop phase { AskNode(questioner) → AskNode(responder) → $output } | Two actors mapped as two AskNodes inside a Loop phase. `done: Callable` → `LoopConfig.exit_condition` (Python expression on $output). iriai-build-v2 universally uses `envelope_done` which checks `data.complete` — maps to `exit_condition: "data.complete"`. First iteration uses `initial_prompt`; subsequent use response — requires prompt template with `{{ $input }}` variable. Verified against HostedInterview, broad_interview, per_subfeature_loop patterns. [code: iriai-build-v2/workflows/_common/_helpers.py:envelope_done] |
| `Gate` (approver, prompt) → `True \| False \| str` | AskNode with `approved`/`rejected` output ports with conditions | Gate returns True (approved), False (rejected), or str (feedback). Maps to AskNode with interaction actor. Output routing: `condition: "data is True"` on approved port, `condition: "data is not True"` on rejected port. The `kind="approve"` dispatch is a runtime concern — the runner recognizes InteractionActor and uses the approval protocol. Verified against gate_and_revise pattern (used 15+ times). [code: iriai-compose/tasks.py:104-116] |
| `Choose` (chooser, prompt, options) → `str` | AskNode with N output ports (one per option), conditions matching option string | Options become output port names. Conditions: `condition: "data == 'option_name'"`. The actor selects one option; the matching port fires. `kind="choose"` is runner-level dispatch. Verified against Choose usages in workflows. [code: iriai-compose/tasks.py:119-133] |
| `Respond` (responder, prompt) → `str` | AskNode with single output, no conditions | Simple pass-through. The responder produces free-form text. Maps directly. `kind="respond"` is runner-level. [code: iriai-compose/tasks.py:136-148] |
| `HostedInterview` (Interview + file artifacts + doc hosting) | Loop phase { AskNode + PluginNode(doc_hosting) } with on_start/on_end hooks | HostedInterview wraps Interview with: (1) on_start injects artifact paths → PluginNode on on_start hook, (2) on_done pushes to hosting → PluginNode on on_end hook, (3) file-aware done predicate → Plugin in exit_condition evaluation chain. The most complex mapping — requires 2 Ask nodes + 2 Plugin nodes + hook edges. Hook edges are standard `Edge` instances where the source references a hook port (e.g., `"node_id.on_start"`) [D-SF1-21]. | [code: iriai-build-v2/workflows/_common/_tasks.py:HostedInterview] |

#### Pattern Mapping: Complex Workflow Patterns

| iriai-build-v2 Pattern | Declarative Representation | Verified Against |
|------------------------|---------------------------|------------------|
| `per_subfeature_loop` (sequential iteration with tiered context) | Fold phase: `collection: "ctx['decomposition'].subfeatures"`, `accumulator_init: "{'artifacts': {}, 'summaries': {}}"`. Each iteration: tiered_context_builder Plugin → AskNode(interview) → AskNode(gate) → AskNode(gate_revise). Accumulator tracks completed artifacts/summaries for tiered context on next iteration. | PMPhase, DesignPhase, ArchitecturePhase, TaskPlanningPhase [code: _helpers.py:per_subfeature_loop] |
| `gate_and_revise` (approval loop) | Loop phase: AskNode(producer) → AskNode(approver with gate conditions) → Branch(approved/rejected). Exit: `exit_condition: "data.approved is True"`. On rejection: edge to revision AskNode → loop back. | Used in every artifact gating step (~15 occurrences) |
| `interview_gate_review` (compiled artifact review) | Loop phase with `fresh_sessions: true` in loop_config: AskNode(actor=`reviewer`) → AskNode(user gate). The `fresh_sessions: true` on the loop_config clears all agent actor sessions before each iteration, preventing auto-approval contamination across rejection→revision→re-review iterations. On rejection: extract RevisionPlan → Fold(targeted revisions) → compile Plugin → loop back. | PlanReviewPhase Step 2 [code: _helpers.py:interview_gate_review] |
| `runner.parallel([Ask, Ask, Ask])` (parallel reviews) | Map phase: `collection: "ctx['review_targets']"`. Each item = one reviewer Ask. All run concurrently. Results gathered at phase output. | PlanReviewPhase Step 1 (3 parallel reviewers), DiagnosisAndFixPhase (2 parallel RCA analysts) [code: plan_review.py:52, diagnosis_fix.py:39] |
| `compile_artifacts` (multi-artifact merge) | AskNode(compiler) with context_keys pointing to all per-subfeature artifacts + a file-writing Plugin. Or a dedicated compile Plugin that reads from artifact store and writes merged output. | Used after every per_subfeature_loop |
| `_build_subfeature_context` (tiered context) | `tiered_context_builder` Plugin: receives current slug, decomposition edges, completed artifacts/summaries. Returns formatted context string. Runs before each fold iteration's main Ask. | Every per_subfeature_loop iteration |
| DAG group execution (parallel tasks within sequential groups) | Nested: Fold(over groups) > Map(parallel tasks within group) > AskNode(implementer). Fold accumulates handover. Map fans out tasks. | ImplementationPhase [code: implementation.py:_implement_dag] |
| Retry with max iterations + handover | Loop phase: `max_iterations: 3`, `exit_condition: "not data.reproduced"`. Accumulator carries HandoverDoc for "do not repeat" context. `max_exceeded` port routes to approval gate. | DiagnosisAndFixPhase [code: diagnosis_fix.py:25-188] |

#### Issues Found and Resolved

| Issue | Resolution |
|-------|-----------|
| **Missing `fresh_sessions`**: iriai-build-v2's `_clear_agent_session()` clears session data between gate review iterations. Without this, agents auto-approve based on prior context. Session clearing operates at loop iteration boundaries (called at start of each `while True` iteration). | Added `fresh_sessions: bool = False` to LoopConfig and FoldConfig [D-SF1-16]. Session clearing is a phase iteration concern — same actor participates in both persistent-session and fresh-session contexts without duplication. |
| **No root `plugin_instances` registry**: PluginNode references plugin types via `plugin_ref` with optional inline `config`. Root `plugin_instances` is REJECTED per closed-root contract. `instance_ref` is not a valid PluginNode field. | PluginNode uses `plugin_ref` only (references `workflow.plugins` key) with optional inline `config`. No `instance_ref` field. No root `plugin_instances` registry. [D-SF1-17, D-GR-35] |
| **`output_type` + `output_schema` ambiguity**: Both define output structure. Having both is undefined behavior. | Added model_validator enforcing mutual exclusion on NodeBase (inherited by all node types) and PhaseDefinition [D-SF1-18, D-SF1-22] |
| **`input_type` + `input_schema` missing**: No way to declare expected input data structure for edge type-checking, self-documentation, or UI display. | Added `input_type: str \| None` and `input_schema: dict \| None` to NodeBase and PhaseDefinition with mutual exclusion validator [D-SF1-22]. Enables edge type-checking (source output vs target input) and self-documenting nodes. |
| **`output_type`/`output_schema` only on AskNode**: PluginNodes produce typed outputs (e.g., `collect_files` returns a file list, `tiered_context_builder` returns formatted context) but had no way to declare this. BranchNodes with `merge_function` produce merged data with a specific shape. | Moved `output_type`/`output_schema` from AskNode to NodeBase [D-SF1-22]. All four node types (Ask, Branch, Plugin, Error) and PhaseDefinition now declare their output structure uniformly. |
| **`ActorDefinition` incomplete validation**: Agent type requires `role`, interaction type requires `resolver`. No enforcement at schema level. | Added model_validator |
| **Phase `$input` semantics for Map/Fold undocumented**: Inside a Map phase, `$input` = current item. Inside a Fold, `$input` = `{item, accumulator}`. | Added Expression Evaluation Contexts section with per-mode documentation [D-SF1-15] |
| **No way to express "parallel safe" actor copies**: iriai-build-v2's `_make_parallel_actor()` creates unique-named copies for `runner.parallel()`. Map phases need the same. | Runner responsibility — Map phase runner auto-creates unique actor instances per iteration. Documented in interface contract. |
| **Async gather semantics undefined in schema**: BranchNode with multiple inputs needs barrier behavior, but schema is a static data model. | Runner responsibility (SF-2). When a BranchNode has N input ports, the runner awaits data on all N before firing. Schema declares port definitions only. [D-SF1-20] |
| **Resume/checkpoint pattern not in schema**: iriai-build-v2 checks artifact store before executing (skip if already done). | Runner responsibility — SF-2 runner checks `artifact_key` in store before executing node. Documented in interface contract. |
| **State model (BuildState/BugFixState) not represented**: Phases read/write typed state fields. | Artifact store IS the state. `context_keys` on actors/nodes replace state field reads. `artifact_key` on nodes replaces state field writes. |
| **`done: Callable` not directly representable**: Interview's `done` is `Callable[[Any], bool]`. | Replaced by `LoopConfig.exit_condition` (Python expression). Universal pattern is `envelope_done` → `"data.complete"`. |
| **HookEdge as separate type creates unnecessary type explosion**: PortDefinition was already unified [D-SF1-10] — hooks are just ports in the `hooks` container. Having a separate `HookEdge` type contradicted this unification. | Merged into single `Edge` type [D-SF1-21]. Hook edges are edges where the source port lives in a `hooks` list. Validation enforces `transform_fn=None` for hook-sourced edges. UI determines rendering style (dashed purple vs solid) by checking the source port's container at render time. |
| **Store not represented as entity**: `artifact_key` and `context_keys` referenced opaque strings with no schema-level store declaration. iriai-build-v2 uses `PostgresArtifactStore` + `PostgresSessionStore` — two distinct stores — but schema had no way to declare or differentiate them. | Added `stores: dict[str, StoreDefinition]` on WorkflowConfig [D-SF1-23]. Named stores declare interface (keys, types). Runner instantiates implementations. Dot notation for references [D-SF1-26]. Validated against 25+ artifact key patterns from iriai-build-v2. |
| **Context hierarchy missing phase/workflow levels**: Actor `context_keys` existed but phase-level and workflow-level context did not. Actors carried heavy key lists (e.g., `["project", "scope", "prd", "design", "plan", "system-design"]`) that should be phase-scoped. | Added `context_keys` + `context_text` at all 4 levels: WorkflowConfig, PhaseDefinition, ActorDefinition, NodeBase [D-SF1-24]. Runtime merges workflow → phase → actor → node. |
| **No inline text context mechanism**: iriai-build-v2 manually injects handover context, tiered subfeature context, and format instructions as literal strings in prompts. No schema-level field for inline text. | Added `context_text: dict[str, str]` at all 4 levels [D-SF1-24]. Named text snippets injected alongside resolved store keys. |
| **Actor context bindings missing**: No way to declare which store an actor reads context from, or where its handover doc lives. All done imperatively in Python. | Added `context_store` and `handover_key` to ActorDefinition [D-SF1-25]. Declarative store bindings — strategy deferred to SF-2. |
| **Artifact hosting modeled as store config**: Initial proposal had `mirror_path` on StoreDefinition. But hosting implementation (Postgres vs filesystem vs web) is a runner concern. | Removed `mirror_path`. Artifact hosting represented as DAG topology: node → on_end hook → doc_hosting plugin [D-SF1-27]. Already representable with existing primitives. |
| **Exclusive routing superseded by per-port conditions**: D-28 in the design doc specified "Branch = programmatic switch, returns path name string" but D-GR-35 supersedes this — `switch_function` is REJECTED. All routing uses per-port `BranchOutputPort.condition` expressions with non-exclusive fan-out. | `switch_function` REJECTED per D-GR-35. BranchNode uses `outputs: dict[str, BranchOutputPort]` where each port has its own `condition` expression. Multiple ports may fire simultaneously. `merge_function` valid only for multi-input gather. Validation rejects `switch_function` with migration guidance. [D-SF1-28 revised by D-GR-35] |
| **Explicit artifact writes are verbose**: iriai-build-v2 patterns like `await runner.artifacts.put("prd", prd, feature=feature)` after every task execution are boilerplate. No schema-level way to declare "this node writes to store." | Clarified `artifact_key` auto-write semantics [D-SF1-29]. Runner auto-writes node output to store at `artifact_key` after execution, before routing. Fewer PluginNodes needed in SF-4 migration. |
| **Workflow-level I/O not declared**: No way for SF-2 to validate that a workflow receives its expected inputs or produces its expected outputs. | Added `inputs: list[WorkflowInputDefinition]` and `outputs: list[WorkflowOutputDefinition]` to WorkflowConfig [D-SF1-30]. SF-2 validates at run time. |

### Expression Evaluation Contexts [D-SF1-15]

All expression fields are Python `str` values evaluated at runtime by SF-2's runner. Each expression type documents the variables available in its evaluation scope:

| Expression Field | Location | Available Variables | Returns | Example |
|-----------------|----------|-------------------|---------|---------|
| `BranchOutputPort.condition` | BranchNode output ports (`outputs: dict[str, BranchOutputPort]`) | `data` = node's merged/passthrough input value | `bool` — port fires when truthy | `"data.verdict == 'approved'"`, `"data is True"` |
| `BranchNode.merge_function` | BranchNode body (gather only — multi-input) | `inputs` = `dict[str, Any]` mapping port_name → received data | merged data `dict` | `"{'combined': list(inputs.values())}"` |
| `Edge.transform_fn` | Data edges (NOT hook edges) | `data` = source port's output value | transformed value | `"data['key']"`, `"{'summary': data.summary}"` |
| `LoopConfig.exit_condition` | Loop phase, after each iteration | `data` = phase's `$output` value | `bool` — True exits loop | `"data.complete"`, `"not data.reproduced"` |
| `MapConfig.collection` | Map phase, once before iterations | `ctx` = resolved context keys + phase input | `Iterable` | `"ctx['decomposition'].subfeatures"` |
| `FoldConfig.collection` | Fold phase, once before iterations | `ctx` = same as Map | `Iterable` | `"ctx['decomposition'].subfeatures"` |
| `FoldConfig.accumulator_init` | Fold phase, once before first iteration | (no variables) | `Any` — initial accumulator | `"{'artifacts': {}, 'summaries': {}}"` |
| `FoldConfig.reducer` | Fold phase, after each iteration | `accumulator` = current value, `result` = iteration $output | `Any` — new accumulator | `"{**accumulator, result['slug']: result['text']}"` |

**Why `str` is sufficient [D-SF1-15]:**

1. **Consistency**: All evaluable fields use the same pattern — Python expression strings. No mixed paradigm.
2. **Expressiveness**: iriai-build-v2's actual patterns require full Python expressiveness: `all(v.approved for v in data.values())`, `not data.reproduced`, dict comprehensions in reducers. Structured condition types (e.g., `{field: "verdict", op: "==", value: "approved"}`) cannot express these.
3. **Security boundary**: Expression sandboxing is SF-2's runner responsibility, not the schema's. The schema stores opaque strings — the runner evaluates them in a restricted context (no imports, no side effects, only the documented variables).
4. **UI generation**: SF-6 can provide expression builders for common patterns while preserving the string representation underneath.

**Phase `$input` semantics by mode:**

| Mode | `$input` contents | `$output` handling |
|------|-------------------|-------------------|
| Sequential | Whatever the external edge provides | Passes through to phase output port |
| Map | Current collection item (one per parallel execution) | All iteration outputs collected into list at phase output |
| Fold | `{"item": current_collection_item, "accumulator": current_accumulator_value}` | Passed to `reducer` expression; result becomes next accumulator |
| Loop | First iteration: external input. Subsequent: previous iteration's `$output` | Evaluated by `exit_condition`; if truthy → `condition_met` port, if falsy → feed back as next `$input` |

### Unified Port Model [D-SF1-10, D-SF1-11]

Everything is a `PortDefinition`. The field it lives in determines its role:

| Container field | Visual position | Port role | Edge syntax |
|----------------|-----------------|-----------|-------------|
| `inputs` | Left edge (blue) | Data input | `"node_id.port_name"` target |
| `outputs` | Right edge (green) | Data output | `"node_id.port_name"` source |
| `hooks` | Bottom (gray) | Lifecycle hook (fire-and-forget) | `"node_id.hook_name"` source |

All three use the same `PortDefinition(name, type_ref, description, condition)` type. There is no separate `HookDefinition`.

The `condition` field is only meaningful on output ports [D-SF1-2]. It holds a Python predicate string that receives `data` (the node's output) and returns a boolean.

**Output port routing behaviors [D-SF1-2, D-GR-35]:**

1. **Single output port with no condition** — always fires (pass-through).
2. **Multiple output ports on BranchNode with per-port conditions** — non-exclusive fan-out. Each `BranchOutputPort.condition` is evaluated independently; all ports whose condition evaluates truthy fire simultaneously. [D-GR-35]
3. **BranchNode with `merge_function`** — gather (multi-input) node merges inputs first, then evaluates per-port conditions against merged data.
4. **Validation constraint:** `switch_function` MUST be rejected on any BranchNode. Stale top-level `condition_type`, `condition`, `paths` (the old exclusive-routing shape) MUST also be rejected. Validation emits `rejected_branch_field` error with guidance to use per-port `outputs` model. [D-GR-35]

**Default ports (shared between NodeBase and PhaseDefinition) [D-SF1-11]:**
- `inputs`: `[PortDefinition(name="input")]`
- `outputs`: `[PortDefinition(name="output")]`
- `hooks`: `[PortDefinition(name="on_start"), PortDefinition(name="on_end")]`

### I/O Type Model [D-SF1-22]

Nodes and phases declare their expected input and produced output data structures via four optional fields on NodeBase and PhaseDefinition:

| Field | Type | Default | Constraint | Purpose |
|-------|------|---------|------------|---------|
| `input_type` | `str \| None` | `None` | Mutual exclusion with `input_schema` | Reference to `workflow.types` key — expected input structure |
| `input_schema` | `dict \| None` | `None` | Mutual exclusion with `input_type` | Inline JSON Schema — expected input structure |
| `output_type` | `str \| None` | `None` | Mutual exclusion with `output_schema` | Reference to `workflow.types` key — produced output structure |
| `output_schema` | `dict \| None` | `None` | Mutual exclusion with `output_type` | Inline JSON Schema — produced output structure |

**Relationship with PortDefinition.type_ref:**

`PortDefinition.type_ref` provides per-port type annotation for multi-port nodes. Node-level `input_type`/`output_type` provides a simpler mechanism for the common single-port case. Type resolution priority:

1. **Port-level `type_ref` takes precedence** — most specific, always wins
2. **Node-level type applies to the default port** — if a node has `output_type: "PRD"` and a single output port with `type_ref: None`, the resolved type for that port is `"PRD"`
3. **Neither set** — no type constraint, any data accepted

This means simple single-port workflows use node-level types, while complex multi-port nodes use port-level `type_ref`. No conflict is possible because port-level always wins.

**Type resolution helper** (used by validation and downstream consumers):

```python
def resolve_port_type(element, port: PortDefinition, is_input: bool) -> str | None:
    """Resolve the effective type for a port, considering node/phase-level fallbacks."""
    # Port-level type_ref always takes precedence
    if port.type_ref is not None:
        return port.type_ref
    # Fall back to element-level type for single-port elements
    if is_input and len(element.inputs) == 1:
        return element.input_type  # str | None
    if not is_input and len(element.outputs) == 1:
        return element.output_type  # str | None
    return None
```

**Why all nodes and phases need I/O types [D-SF1-22]:**

| Node Type | Input type use case | Output type use case |
|-----------|--------------------|--------------------|
| AskNode | Declares expected input shape for edge type-checking (e.g., "expects SubfeatureDecomposition") | Tells runtime what structured output the agent should produce (existing `Ask.output_type` pattern) |
| BranchNode | Declares expected shape for each input port on gather nodes | Declares merged output shape from `merge_function` |
| PluginNode | Declares expected input shape (e.g., `tiered_context_builder` expects specific context dict) | Declares output shape (e.g., `collect_files` returns a file list) |
| PhaseDefinition | Declares what the phase expects via its `$input` boundary | Declares what the phase produces via its `$output` boundary |

### Store Model [D-SF1-23, D-SF1-26]

Workflows declare named stores that the runner instantiates. Schema declares the interface — runner provides the implementation (Postgres, filesystem, in-memory).

```yaml
stores:
  artifacts:
    description: "Primary artifact store"
    keys:
      prd: { type_ref: "PRD", description: "Product requirements" }
      design: { type_ref: "DesignDecisions" }
      handover: { description: "Cumulative handover doc" }
      # Keys without type_ref accept Any
  sessions:
    description: "Agent session persistence"
    # No keys dict = open store, any key accepted
```

**Three key typing modes:**

| Mode | YAML | Meaning |
|------|------|---------|
| **Typed** | `type_ref: "PRD"` | Value must conform to `workflow.types["PRD"]`. Validation checks writer `output_type` matches. |
| **Untyped** | Key declared, no `type_ref` | Key declared (self-documenting) but accepts `Any`. Generic output allowed. |
| **Open store** | `keys` omitted | Store accepts any key. No validation of key references. For dynamic patterns like `"{prefix}:{slug}"`. |

**Dot notation for all references [D-SF1-26]:**
- `artifact_key: "artifacts.prd"` — node writes output to `prd` key in `artifacts` store
- `context_keys: ["artifacts.prd", "artifacts.design"]` — node reads from `artifacts` store
- `handover_key: "artifacts.handover_pm"` — actor's handover doc location
- No dot = implicit first declared store

**Artifact hosting as DAG topology [D-SF1-27]:**
```yaml
# Node writes to store
- id: write_prd
  type: ask
  artifact_key: "artifacts.prd"
  hooks: [on_start, on_end]

# Hook edge triggers hosting plugin
edges:
  - source: "write_prd.on_end"
    target: "host_prd.input"

# Plugin reads from store and hosts
- id: host_prd
  type: plugin
  plugin_ref: doc_hosting
  config:
    store_key: "artifacts.prd"
```

No `mirror_path` or implementation details on stores — runner handles persistence strategy.

### Context Hierarchy Model [D-SF1-24, D-SF1-25]

Context is set at four levels, each with two fields:

| Level | `context_keys: list[str]` | `context_text: dict[str, str]` |
|-------|--------------------------|-------------------------------|
| **WorkflowConfig** | Global baseline — all nodes inherit | Global inline text snippets |
| **PhaseDefinition** | Phase-scoped — nodes in this phase inherit | Phase-scoped inline text |
| **ActorDefinition** | Actor baseline — every invocation of this actor | Actor-specific inline text |
| **NodeBase** | Per-node specific | Per-node inline text |

**Runtime merge order:** workflow → phase → actor → node (deduplicated, preserving order).

**`context_keys`** references store keys via dot notation. Resolved by runner's ContextProvider (`DefaultContextProvider.resolve()`). Each key resolved to formatted markdown section.

**`context_text`** provides inline named text snippets injected alongside resolved store keys. Not store references — literal strings embedded in YAML. Used for handover injection, format instructions, etc.

**Actor store bindings [D-SF1-25]:**
- `context_store: str | None` — which store for this actor's context resolution (default: first store)
- `handover_key: str | None` — dot-notation reference to handover doc in store (e.g., `"artifacts.handover_pm"`)
- Actual context management strategy (compaction, summarization, token budgets) is SF-2 runner config

### Unified Edge Model [D-SF1-21]

All connections between ports use a single `Edge` type. The source port's container determines whether an edge is a data edge or a hook edge:

| Source port container | Edge semantics | `transform_fn` | UI rendering | Example |
|----------------------|----------------|-----------------|--------------|---------|
| `outputs` | Data edge | Allowed (optional) | Solid line, type label at midpoint, ⚡ if transform | `source: "ask_1.output"` → `target: "branch_1.input"` |
| `hooks` | Hook edge (fire-and-forget) | Must be `None` (validated) | Dashed purple (#a78bfa), no type label | `source: "ask_1.on_end"` → `target: "plugin_1.input"` |

**Why a single type [D-SF1-21]:**

Just as `PortDefinition` is unified [D-SF1-10] (no `HookDefinition`), edges are unified (no `HookEdge`). The port's container field already carries the semantic distinction. A separate `HookEdge` type:
- Duplicates the `source`/`target`/`description` fields identically
- Only differs by *not having* `transform_fn` — a constraint easily enforced by validation
- Forces two separate `edges` lists on PhaseDefinition/WorkflowConfig, complicating edge traversal and DAG algorithms
- Contradicts the design principle that ports determine role, not the edge connecting them

**Validation rule:** If `edge.source` resolves to a port in the `hooks` container of any node or phase, then `edge.transform_fn` must be `None`. Violation produces `invalid_hook_edge_transform` error.

### Phase I/O Boundary Model [D-SF1-4]

```
External ──→ [Phase Input Port] ──→ $input ──→ FirstNode ──→ ... ──→ LastNode ──→ $output ──→ [Phase Output Port] ──→ External
                                                                                      │
                                                                                      ↓ (loop mode only)
                                                                              exit_condition(output)
                                                                              ├── True  → condition_met port → External
                                                                              └── False → feed back to $input (next iteration)
```

### Phase Iteration Session Model [D-SF1-16]

Session clearing is a phase iteration concern, not an actor property. This matches the runtime implementation where:
- Session key = `{actor.name}:{feature.id}` — actor-scoped [code: iriai-compose/runner.py:250-251]
- `_clear_agent_session()` is called at the start of each loop iteration in `interview_gate_review` [code: iriai-build-v2/_helpers.py:1333-1344]
- InteractionActor uses Pending objects with no session_key — clearing only affects AgentActor (safe for blanket boolean)
- Map phases already create isolated sessions per parallel execution via runner actor deduplication
- Sequential phases: sessions persist naturally across nodes (the default)

**Default:** Sessions persist for the duration of task execution. Same actor across multiple nodes shares a session (key = `{actor.name}:{feature.id}`).

**Loop/Fold with `fresh_sessions: true`:** Runner clears ALL agent actor sessions used within the phase before each iteration. InteractionActor unaffected (uses Pending, not sessions).

**Map:** Runner auto-creates unique actor instances per parallel execution. Sessions inherently isolated. No `fresh_sessions` field needed.

**Sequential:** Sessions persist naturally. No iteration boundaries.

**`persistent` (actor) vs `fresh_sessions` (phase) interaction:**

| `persistent` (actor) | `fresh_sessions` (phase) | Behavior |
|---|---|---|
| true (default) | false (default) | Session persists across workflow run. Full continuity. |
| true | true | Session persisted to store but cleared before each phase iteration. Audit trail maintained. |
| false | false | Ephemeral session, runtime manages lifecycle. |
| false | true | Ephemeral and cleared per iteration. |

**Example: Same actor, different session behavior by phase:**
```yaml
actors:
  reviewer:
    type: agent
    role: { name: reviewer, prompt: "Review...", model: claude-opus-4-20250514 }
    # No fresh_sessions here — same actor used everywhere

phases:
  - id: interview_phase
    mode: sequential
    # reviewer maintains session continuity here (default)

  - id: gate_review_loop
    mode: loop
    loop_config:
      exit_condition: "data.approved"
      fresh_sessions: true  # reviewer gets fresh session each iteration
```

---

## System Design

### Services

| ID | Name | Kind | Technology | Description | Journeys |
|----|------|------|------------|-------------|----------|
| SVC-1 | iriai-compose-schema | service | Python 3.11+ / Pydantic v2 | New `iriai_compose/schema/` subpackage (canonical import: `iriai_compose.schema` [C-2]). Pydantic v2 models defining the declarative workflow format with four atomic node types (Ask, Branch, Plugin, Error per D-GR-36), per-port BranchNode conditions (D-GR-35, `switch_function` REJECTED), workflow-level I/O [D-SF1-30], structural validation (21 error codes [H-3]), YAML I/O, and JSON Schema generation. Pure data layer with zero runtime dependencies. | J-1, J-2, J-3, J-4, J-5, J-6 |
| SVC-2 | iriai-compose-runtime | service | Python 3.11+ | Existing `iriai_compose` package — `WorkflowRunner`, `AgentRuntime`, `InteractionRuntime`, `Task`, `Phase`, `Workflow`. SF-1 does NOT modify this; SF-2 bridges schema→runtime. | J-8 |
| SVC-3 | yaml-workflow-files | database | YAML on filesystem | `.yaml` files authored by developers or exported from Compose UI. | J-1, J-9, J-11, J-12, J-13 |
| SVC-4 | json-schema-artifact | database | JSON file | Static `workflow-schema.json` generated via `model_json_schema()`. | J-3 |
| SVC-5 | iriai-compose-testing | service | Python 3.11+ / pytest | SF-3 `iriai_compose.testing` subpackage. | J-7, J-8, J-9, J-10 |
| SVC-6 | iriai-build-v2-workflows | external | Python 3.11+ | Existing imperative workflows. Read-only reference for SF-4. | J-11, J-12, J-13, J-14 |
| SVC-7 | iriai-workflows-backend | service | Python / FastAPI / SQLite | SF-5/SF-6/SF-7 visual builder backend. | J-15, J-16 |
| SVC-8 | iriai-workflows-frontend | frontend | React / React Flow | SF-5/SF-6/SF-7 visual builder UI. | J-3, J-15, J-16 |

### Connections

| From | To | Label | Protocol | Journeys |
|------|----|-------|----------|----------|
| SVC-1 | SVC-3 | `dump_workflow()` serializes config to YAML | Python file I/O | J-1, J-11, J-12, J-13 |
| SVC-3 | SVC-1 | `load_workflow()` deserializes YAML to config | Python file I/O | J-1, J-2, J-7, J-8, J-9 |
| SVC-1 | SVC-4 | `generate_json_schema()` exports schema | Python file I/O | J-3 |
| SVC-1 | SVC-2 | Schema models imported by loader/runner (SF-2) | Python import | J-8 |
| SVC-1 | SVC-5 | Schema models + validation imported by testing (SF-3) | Python import | J-7, J-8, J-9, J-10 |
| SVC-6 | SVC-3 | SF-4 migration translates imperative → YAML | Python file I/O | J-11, J-12, J-13, J-14 |
| SVC-4 | SVC-8 | JSON Schema drives inspector field rendering | Static import / HTTP | J-3, J-16 |
| SVC-3 | SVC-7 | YAML import/export via API | HTTP file upload | J-15, J-16 |
| SVC-7 | SVC-1 | Backend uses `load_workflow()`/`validate_workflow()` | Python import | J-15 |

### API Endpoints

SF-1 is a Python library — it exposes no HTTP endpoints. Its "API" is the Python import surface:

| Method | Path | Service | Description | Request | Response | Auth |
|--------|------|---------|-------------|---------|----------|------|
| Python | `load_workflow(path)` | SVC-1 | Load YAML file → WorkflowConfig | `str \| Path` | `WorkflowConfig` | N/A |
| Python | `load_workflow_lenient(path)` | SVC-1 | Load YAML + structural validation | `str \| Path` | `(WorkflowConfig, list[ValidationError])` | N/A |
| Python | `dump_workflow(config, path?)` | SVC-1 | Serialize config to YAML string/file | `WorkflowConfig` | `str` | N/A |
| Python | `validate_workflow(config)` | SVC-1 | Full structural validation | `WorkflowConfig` | `list[ValidationError]` | N/A |
| Python | `validate_type_flow(config)` | SVC-1 | Edge type compatibility only | `WorkflowConfig` | `list[ValidationError]` | N/A |
| Python | `detect_cycles(config)` | SVC-1 | DFS cycle detection only | `WorkflowConfig` | `list[ValidationError]` | N/A |
| Python | `generate_json_schema(path?)` | SVC-1 | Export JSON Schema from models | `str \| Path \| None` | `dict` | N/A |
| CLI | `python -m iriai_compose.schema.json_schema [path]` | SVC-1 | CLI JSON Schema generator | argv path | JSON file | N/A |

### Call Paths

#### CP-1: Author YAML Workflow (J-1)

| Seq | From | To | Action | Description | Returns |
|-----|------|----|--------|-------------|---------|
| 1 | Developer | SVC-3 | Write YAML file | Author workflow definition in YAML | `.yaml` on filesystem |
| 2 | SVC-3 | SVC-1 | `load_workflow(path)` | Parse YAML → Pydantic models | `WorkflowConfig` or raises |
| 3 | SVC-1 | SVC-1 | `validate_workflow(config)` | Structural validation checks | `list[ValidationError]` (empty = valid) |

#### CP-2: Generate JSON Schema for UI (J-3)

| Seq | From | To | Action | Description | Returns |
|-----|------|----|--------|-------------|---------|
| 1 | Build script | SVC-1 | `generate_json_schema(path)` | Pydantic `model_json_schema()` | JSON Schema dict |
| 2 | SVC-1 | SVC-4 | Write JSON file | Serialize to `workflow-schema.json` | Static JSON artifact |
| 3 | SVC-4 | SVC-8 | Import at build time | UI bundles schema for inspector rendering | Inspector field definitions |

#### CP-3: Validate Workflow Structurally (J-5)

| Seq | From | To | Action | Description | Returns |
|-----|------|----|--------|-------------|---------|
| 1 | Caller | SVC-1 | `validate_workflow(config)` | Entry point | Aggregated error list |
| 2 | SVC-1 | SVC-1 | `_check_duplicate_ids()` | Scan all phases for ID collisions | `duplicate_node_id` / `duplicate_phase_id` errors |
| 3 | SVC-1 | SVC-1 | `_check_actor_refs()` | Verify node actor refs exist in `workflow.actors` | `invalid_actor_ref` errors |
| 4 | SVC-1 | SVC-1 | `_check_phase_configs()` | Verify mode-specific config presence | `invalid_phase_mode_config` errors |
| 5 | SVC-1 | SVC-1 | `_check_edges()` | Resolve `node_id.port_name` references, classify as data or hook by source port container | `dangling_edge` errors |
| 6 | SVC-1 | SVC-1 | `_check_hook_edge_constraints()` | Verify hook-sourced edges have `transform_fn=None` [D-SF1-21] | `invalid_hook_edge_transform` errors |
| 7 | SVC-1 | SVC-1 | `_check_phase_boundaries()` | Verify `$input`/`$output` wiring [D-SF1-4] | `phase_boundary_violation` errors |
| 8 | SVC-1 | SVC-1 | `_check_cycles()` | DFS within each phase's edge graph | `cycle_detected` errors |
| 9 | SVC-1 | SVC-1 | `_check_reachability()` | Find nodes with no incoming edges | `unreachable_node` errors |
| 10 | SVC-1 | SVC-1 | `_check_type_flow()` | Compare source output type ↔ target input type using `resolve_port_type()` [D-SF1-22] | `type_mismatch` errors |
| 11 | SVC-1 | SVC-1 | `_check_branch_configs()` | Verify Branch has ≥1 input + ≥1 output ports | `invalid_branch_config` errors |
| 12 | SVC-1 | SVC-1 | `_check_plugin_refs()` | Verify plugin_ref exists in `workflow.plugins`; reject `instance_ref` | `invalid_plugin_ref` errors |
| 13 | SVC-1 | SVC-1 | `_check_output_port_conditions()` | Warn on ambiguous multi-port conditions | `missing_output_condition` warnings |
| 14 | SVC-1 | SVC-1 | `_check_io_configs()` | Verify input_type/input_schema and output_type/output_schema mutual exclusion on all nodes and phases [D-SF1-22] | `invalid_io_config` errors |
| 15 | SVC-1 | SVC-1 | `_check_type_refs()` | Verify input_type/output_type reference valid keys in `workflow.types` | `invalid_type_ref` errors |
| 16 | SVC-1 | SVC-1 | `_check_store_refs()` | Verify all dot-notation references (`artifact_key`, `context_keys`, `handover_key`) have valid store name prefix [D-SF1-26] | `invalid_store_ref` errors |
| 17 | SVC-1 | SVC-1 | `_check_store_key_refs()` | For non-open stores, verify referenced keys exist in store definition [D-SF1-23] | `invalid_store_key_ref` errors |
| 18 | SVC-1 | SVC-1 | `_check_store_key_types()` | For typed store keys, verify node `output_type` matches store key `type_ref` when writing [D-SF1-23] | `store_type_mismatch` errors |
| 19 | SVC-1 | SVC-1 | `_check_rejected_branch_fields()` | Reject `switch_function`, stale `condition_type`/`condition`/`paths` on BranchNode. Emit guidance to use per-port `outputs` model [D-GR-35] | `rejected_branch_field` errors |
| 20 | SVC-1 | SVC-1 | `_check_workflow_io_refs()` | Verify `type_ref` on WorkflowInputDefinition/WorkflowOutputDefinition resolves to `workflow.types` keys [D-SF1-30] | `invalid_workflow_io_ref` errors |
| 21 | SVC-1 | SVC-1 | `_check_required_fields()` | Catch required field violations not caught by Pydantic in lenient loading paths | `missing_required_field` errors |

#### CP-4: YAML Round-Trip (J-9)

| Seq | From | To | Action | Description | Returns |
|-----|------|----|--------|-------------|---------|
| 1 | Test | SVC-3 | Read fixture | Load raw YAML from `tests/fixtures/` | YAML string |
| 2 | SVC-3 | SVC-1 | `load_workflow(path)` | Deserialize to typed objects | `WorkflowConfig` |
| 3 | SVC-1 | SVC-1 | `dump_workflow(config)` | Reserialize to YAML string | YAML string |
| 4 | Test | SVC-1 | `load_workflow()` on dumped string | Verify re-deserialization | Equivalent `WorkflowConfig` |

#### CP-5: Migrate Imperative Workflow (J-11, J-12, J-13)

| Seq | From | To | Action | Description | Returns |
|-----|------|----|--------|-------------|---------|
| 1 | SF-4 script | SVC-6 | Read Python classes | Analyze Phase/Task definitions | Class structure |
| 2 | SF-4 script | SVC-1 | Construct `WorkflowConfig` | Build declarative equivalent programmatically | `WorkflowConfig` instance |
| 3 | SVC-1 | SVC-1 | `validate_workflow(config)` | Verify structural validity | Errors → J-14 gap if any |
| 4 | SVC-1 | SVC-3 | `dump_workflow(config, path)` | Write YAML file | `.yaml` on filesystem |

### Entities

#### PortDefinition [D-SF1-10]

Single type for ALL ports — data inputs, data outputs, hooks. The container field determines the port's role.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | — | required | Port identifier (e.g., `"input"`, `"output"`, `"on_start"`, `"approved"`) |
| `type_ref` | `str \| None` | `None` | Must reference `workflow.types` key if set | Type name for edge type-checking. Takes precedence over node-level `input_type`/`output_type` [D-SF1-22]. |
| `description` | `str \| None` | `None` | — | Human-readable description |
| `condition` | `str \| None` | `None` | Python expression string | Output port routing predicate. Receives `data` (node output). Returns `bool`. Only meaningful on output ports [D-SF1-2]. |

#### NodeBase [D-SF1-11, D-SF1-22]

Abstract base for all node types. Provides default port signatures shared with PhaseDefinition. Provides I/O type declarations shared by all node types.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `id` | `str` | — | required, unique within phase | Node identifier |
| `type` | `Literal["ask", "branch", "plugin", "error"]` | — | required | Discriminator for union [D-SF1-9, D-GR-36] |
| `summary` | `str \| None` | `None` | max ~120 chars | 1–2 line description on card face [D-32] |
| `context_keys` | `list[str]` | `[]` | — | Artifact store keys to inject into prompt context |
| `context_text` | `dict[str, str]` | `{}` | — | Inline text snippets for this node's context [D-SF1-24] |
| `artifact_key` | `str \| None` | `None` | — | Output artifact key. Also used as output port label [D-32]. |
| `input_type` | `str \| None` | `None` | mutual exclusion with `input_schema` [D-SF1-22] | Reference to `workflow.types` key — expected input structure |
| `input_schema` | `dict \| None` | `None` | mutual exclusion with `input_type` [D-SF1-22] | Inline JSON Schema — expected input structure |
| `output_type` | `str \| None` | `None` | mutual exclusion with `output_schema` [D-SF1-22] | Reference to `workflow.types` key — produced output structure |
| `output_schema` | `dict \| None` | `None` | mutual exclusion with `output_type` [D-SF1-22] | Inline JSON Schema — produced output structure |
| `inputs` | `list[PortDefinition]` | `[PortDefinition(name="input")]` | — | Data input ports [D-SF1-11] |
| `outputs` | `list[PortDefinition]` | `[PortDefinition(name="output")]` | — | Data output ports [D-SF1-11] |
| `hooks` | `list[PortDefinition]` | `[PortDefinition(name="on_start"), PortDefinition(name="on_end")]` | — | Lifecycle hook ports [D-SF1-11] |
| `position` | `dict[str, float] \| None` | `None` | `{"x": float, "y": float}` | Canvas position (UI-only) |

**Validators:**
- `_check_input_config`: Raises if both `input_type` and `input_schema` are set. [D-SF1-22]
- `_check_output_config`: Raises if both `output_type` and `output_schema` are set. [D-SF1-18, D-SF1-22]

#### AskNode (extends NodeBase, type="ask") [D-SF1-12]

Agent invocation node. 1 fixed input, user-defined outputs (1+), mutually exclusive routing.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `actor` | `str` | — | required, must reference `workflow.actors` key | Actor to invoke |
| `prompt` | `str` | — | required | Prompt template. `{{ $input }}`, `{{ ctx.key }}` variables. |

**Validators:**
- `_fix_input_ports`: Always overrides inputs to single `[PortDefinition(name="input")]`. User cannot customize. [D-SF1-12]

**Inherited from NodeBase [D-SF1-22]:** `input_type`, `input_schema`, `output_type`, `output_schema` with mutual exclusion validators. `output_type` tells the runtime what structured output the agent should produce (maps directly from existing `Ask.output_type: type[BaseModel]`).

**NOT fields:** No `fresh_session`, no `options`, no `kind`. Session clearing is on phase configs [D-SF1-16].

#### BranchNode (extends NodeBase, type="branch") [D-SF1-13, D-SF1-20, D-GR-35]

DAG coordination primitive — gather (multiple inputs) and dispatch (multiple outputs). Uses per-port `BranchOutputPort.condition` expressions with non-exclusive fan-out per D-GR-35. `switch_function` is REJECTED.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `outputs` | `dict[str, BranchOutputPort]` | — | min 2 entries required [D-GR-35] | Keyed map of output ports. Each port carries its own `condition` expression. Multiple ports may fire simultaneously (non-exclusive fan-out). |
| `merge_function` | `str \| None` | `None` | Python expression; valid only on gather (multi-input) BranchNodes [D-GR-35] | Merges multi-input data. Receives `inputs: dict[str, Any]`. Returns merged dict. Runs before condition evaluation. [D-SF1-15] |

**Validators:**
- `_validate_branch_ports`: Enforces min 1 input port, min 2 output ports (BranchOutputPort entries). [D-SF1-13, D-GR-35]
- `_validate_rejected_fields`: REJECTS `switch_function`, `condition_type`, `condition`, `paths`, `output_field` if present. Raises `ValueError` with guidance to use per-port `outputs` model. [D-GR-35]
- `_validate_merge_function_gather_only`: If `merge_function` is set, requires 2+ input ports (gather semantics). Raises on single-input BranchNode with `merge_function`. [D-GR-35]

**Inherited from NodeBase [D-SF1-22]:** `input_type`, `input_schema`, `output_type`, `output_schema`. `output_type` declares the shape of data produced by `merge_function` (or the passthrough shape if no merge). `input_type` declares what each input port expects (useful for gather nodes receiving typed data).

**NOT fields:** No `actor` [D-28]. No `switch_function` [D-GR-35]. No `condition_type`, `condition`, `paths`, `output_field` [D-GR-35].

**Port routing [D-SF1-2, D-GR-35]:**
- **Per-port conditions (canonical):** Non-exclusive — each `BranchOutputPort.condition` is evaluated independently. All ports whose condition evaluates truthy fire simultaneously.
- **With `merge_function` (gather):** Inputs merged first → conditions evaluated against merged data.
- **No conditions set:** All output ports fire (broadcast). Validation emits `missing_output_condition` warning if >1 output port.
- **`switch_function` REJECTED:** Validation rejects any BranchNode containing `switch_function` with guidance to use per-port conditions. [D-GR-35]

#### PluginNode (extends NodeBase, type="plugin") [D-SF1-14, D-SF1-17, D-SF1-19]

Side-effect execution node. 1 fixed input, 0+ outputs (fire-and-forget allowed).

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `plugin_ref` | `str` | — | required; references `workflow.plugins` key [D-SF1-17] | Reference to `workflow.plugins` key |
| `config` | `dict \| None` | `None` | — | Inline config for plugin type |

**Validators:**
- `_fix_input_ports`: Always single `[PortDefinition(name="input")]`. [D-SF1-14]
- `_check_plugin_ref`: `plugin_ref` must be set and reference a valid `workflow.plugins` key. `instance_ref` is REJECTED — no root `plugin_instances` registry exists. [D-SF1-17]

**Inherited from NodeBase [D-SF1-22]:** `input_type`, `input_schema`, `output_type`, `output_schema`. `output_type` declares the plugin's output structure (e.g., `collect_files` returns a file list). `input_type` declares what data the plugin expects.

**Outputs:** Allows `outputs: []` (empty list) for fire-and-forget plugins (e.g., `git_commit_push`). [D-SF1-19]

#### ErrorNode (extends NodeBase, type="error") [D-GR-36]

Error-raising node. 4th atomic node type per D-GR-36. Purpose is to RAISE errors (e.g., log an error message, but still let it bubble up). Error ports on other nodes are the "catch" side; ErrorNode is the "throw" side.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `message` | `str` | — | required; Jinja2 template | Error message template. Receives node inputs as template variables. |
| `inputs` | `dict[str, PortDefinition]` | `{}` | — | Input ports providing data for the message template |

**Constraints:** ErrorNode has NO outputs and NO hooks. It is a terminal node in the DAG — once it fires, the error propagates upward.

#### NodeDefinition (discriminated union) [D-SF1-9]

```python
NodeDefinition = Annotated[AskNode | BranchNode | PluginNode | ErrorNode, Field(discriminator="type")]
```

#### Edge [D-SF1-15, D-SF1-21]

Single edge type for ALL connections — data edges and hook edges. The source port's container field determines semantics. When the source resolves to a port in a `hooks` list, the edge is a hook edge (fire-and-forget, no transform). When the source resolves to a port in an `outputs` list, the edge is a data edge (optional transform).

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `source` | `str` | — | required | `"node_id.port_name"` or `"$input"` (phase boundary). Port may be in `outputs` (data edge) or `hooks` (hook edge). |
| `target` | `str` | — | required | `"node_id.port_name"` or `"$output"` (phase boundary). Always resolves to an `inputs` port on the target. |
| `transform_fn` | `str \| None` | `None` | Must be `None` when source is a hook port [D-SF1-21] | Inline transform. Receives `data`. Returns transformed value. [D-21] |
| `description` | `str \| None` | `None` | — | Human-readable |

**Hook edge identification at runtime/render time [D-SF1-21]:**
1. Resolve `edge.source` to a port: parse `"node_id.port_name"` → find node → check if `port_name` is in `node.hooks`
2. If yes → hook edge. Render as dashed purple. No transform allowed.
3. If no → data edge. Render as solid with type label. Transform optional.

**Validation rule:** `_check_hook_edge_constraints()` iterates all edges, resolves source ports, and produces `invalid_hook_edge_transform` errors for any hook-sourced edge with non-None `transform_fn`.

#### SequentialConfig

No additional fields. Phase nodes execute in edge-determined order.

#### MapConfig

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `collection` | `str` | — | required, Python expression | Evaluates to `Iterable`. Receives `ctx` (context keys + phase input). [D-SF1-15] |
| `max_parallelism` | `int \| None` | `None` | positive int | Concurrency limit. `None` = unlimited. |

**Session behavior:** Runner auto-creates unique actor instances per parallel execution. Sessions inherently isolated. No `fresh_sessions` field. [D-SF1-16]

#### FoldConfig

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `collection` | `str` | — | required, Python expression | Evaluates to `Iterable`. Receives `ctx`. [D-SF1-15] |
| `accumulator_init` | `str` | — | required, Python expression | Initial accumulator value. No variables available. [D-SF1-15] |
| `reducer` | `str` | — | required, Python expression | Combines `accumulator` + `result` (iteration output). [D-SF1-15] |
| `fresh_sessions` | `bool` | `False` | — | When True, runner clears all agent actor sessions before each iteration. [D-SF1-16] |

#### LoopConfig [D-SF1-5]

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `exit_condition` | `str` | — | required, Python expression | Evaluates against `data` ($output). True → exit via `condition_met`. False → re-execute. [D-SF1-5, D-SF1-15] |
| `max_iterations` | `int \| None` | `None` | positive int | Enables `max_exceeded` exit port when set. |
| `fresh_sessions` | `bool` | `False` | — | When True, runner clears all agent actor sessions before each iteration. [D-SF1-16] |

#### PhaseDefinition [D-SF1-4, D-SF1-11, D-SF1-21, D-SF1-22]

Primary container for DAG execution. 4 modes with strict I/O boundary. Single `edges` list contains both data edges and hook edges [D-SF1-21]. I/O type declarations for phase-level type-checking [D-SF1-22].

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `id` | `str` | — | required, unique within workflow | Phase identifier |
| `mode` | `Literal["sequential", "map", "fold", "loop"]` | — | required | Execution mode |
| `sequential_config` | `SequentialConfig \| None` | `None` | required when mode="sequential" | Sequential mode config |
| `map_config` | `MapConfig \| None` | `None` | required when mode="map" | Map mode config |
| `fold_config` | `FoldConfig \| None` | `None` | required when mode="fold" | Fold mode config |
| `loop_config` | `LoopConfig \| None` | `None` | required when mode="loop" | Loop mode config |
| `nodes` | `list[NodeDefinition]` | `[]` | — | Internal nodes |
| `edges` | `list[Edge]` | `[]` | — | ALL internal edges — both data edges and hook edges [D-SF1-21] |
| `phases` | `list[PhaseDefinition]` | `[]` | — | Nested phases (recursive) |
| `input_type` | `str \| None` | `None` | mutual exclusion with `input_schema` [D-SF1-22] | Reference to `workflow.types` key — expected phase input structure |
| `input_schema` | `dict \| None` | `None` | mutual exclusion with `input_type` [D-SF1-22] | Inline JSON Schema — expected phase input structure |
| `output_type` | `str \| None` | `None` | mutual exclusion with `output_schema` [D-SF1-22] | Reference to `workflow.types` key — produced phase output structure |
| `output_schema` | `dict \| None` | `None` | mutual exclusion with `output_type` [D-SF1-22] | Inline JSON Schema — produced phase output structure |
| `inputs` | `list[PortDefinition]` | `[PortDefinition(name="input")]` | — | Phase input ports [D-SF1-11] |
| `outputs` | `list[PortDefinition]` | `[PortDefinition(name="output")]` | — | Phase output ports [D-SF1-11] |
| `hooks` | `list[PortDefinition]` | `[PortDefinition(name="on_start"), PortDefinition(name="on_end")]` | — | Phase hook ports [D-SF1-11] |
| `summary` | `str \| None` | `None` | max ~120 chars | 1–2 line description [D-32] |
| `context_keys` | `list[str]` | `[]` | dot notation refs to stores | Phase-scoped context keys inherited by child nodes [D-SF1-24] |
| `context_text` | `dict[str, str]` | `{}` | — | Phase-scoped inline text snippets [D-SF1-24] |
| `position` | `dict[str, float] \| None` | `None` | `{"x": float, "y": float}` | Canvas position (UI-only) |

**Validators:**
- Mode requires corresponding config (e.g., `mode="fold"` requires `fold_config` non-None).
- Loop mode auto-creates dual exit ports: `condition_met` (green) + `max_exceeded` (amber) on outputs. [J-6]
- `_check_input_config`: Raises if both `input_type` and `input_schema` are set. [D-SF1-22]
- `_check_output_config`: Raises if both `output_type` and `output_schema` are set. [D-SF1-22]

**Edge classification [D-SF1-21]:** The single `edges` list contains all edges. To determine if an edge is a hook edge, resolve `edge.source` and check if the port lives in the source element's `hooks` container. This is done by validation (`_check_hook_edge_constraints`) and by the UI at render time.

#### RoleDefinition

Mirrors `iriai_compose.actors.Role` exactly.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | — | required | Role name |
| `prompt` | `str` | — | required | System prompt (markdown) |
| `tools` | `list[str]` | `[]` | — | Tool names (Read, Edit, Write, Bash, etc.) |
| `model` | `str \| None` | `None` | — | Model identifier (e.g., `claude-opus-4-20250514`) |
| `effort` | `Literal["low", "medium", "high", "max"] \| None` | `None` | — | Effort level hint |
| `metadata` | `dict[str, Any]` | `{}` | — | Arbitrary key-value metadata |

#### ActorDefinition [D-SF1-16]

Agent or interaction actor. Session persistence is an actor property; session clearing is a phase property.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `type` | `Literal["agent", "interaction"]` | — | required | Actor type |
| `role` | `RoleDefinition \| None` | `None` | required when type="agent" | Agent role definition |
| `resolver` | `str \| None` | `None` | required when type="interaction" | Interaction runtime resolver name |
| `context_keys` | `list[str]` | `[]` | — | Default context keys injected into every invocation |
| `persistent` | `bool` | `True` | — | Whether session survives workflow restarts (actor property) |
| `context_text` | `dict[str, str]` | `{}` | — | Inline text snippets for this actor's context [D-SF1-24] |
| `context_store` | `str \| None` | `None` | must reference `workflow.stores` key | Default store for context resolution [D-SF1-25] |
| `handover_key` | `str \| None` | `None` | dot notation store ref | Store location for actor's handover document [D-SF1-25] |

**Validators:**
- `_check_actor_type`: agent requires `role` non-None; interaction requires `resolver` non-None.

**NOT fields:** No `fresh_session` or `fresh_sessions`. Session clearing is on LoopConfig/FoldConfig [D-SF1-16].

#### TypeDefinition

Named output type definition with JSON Schema.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | — | required | Type name (referenced by `NodeBase.input_type`/`output_type`, `PortDefinition.type_ref`) |
| `schema_def` | `dict` | — | required, valid JSON Schema Draft 2020-12 | JSON Schema for the type |
| `description` | `str \| None` | `None` | — | Human-readable |

#### CostConfig

Workflow-level cost tracking configuration.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `max_tokens` | `int \| None` | `None` | positive int | Token budget limit |
| `max_usd` | `float \| None` | `None` | positive float | USD budget limit |
| `track_by` | `Literal["node", "phase", "workflow"]` | `"workflow"` | — | Cost tracking granularity |

#### PluginInterface [D-SF1-11]

Plugin type definition. Defines the interface (I/O, config schema) that instances implement.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `id` | `str` | — | required | Plugin type identifier |
| `name` | `str` | — | required | Human-readable name |
| `description` | `str \| None` | `None` | — | Description |
| `inputs` | `list[PortDefinition]` | `[PortDefinition(name="input")]` | — | Input port interface [D-SF1-11] |
| `outputs` | `list[PortDefinition]` | `[PortDefinition(name="output")]` | — | Output port interface [D-SF1-11] |
| `config_schema` | `dict \| None` | `None` | valid JSON Schema | Schema for instance config |
| `category` | `Literal["service", "mcp", "cli", "plugin"] \| None` | `None` | — | Plugin category |

#### ~~PluginInstanceConfig~~ [REJECTED]

`PluginInstanceConfig` and root `plugin_instances` registry are REJECTED. PluginNode references plugin types directly via `plugin_ref` with optional inline `config`. There is no pre-configured instance registry. `instance_ref` is not a valid PluginNode field. Validation MUST reject root `plugin_instances` with guidance that plugin configuration is inline on each PluginNode.

#### TemplateRef

Reference to a reusable task template with actor slot bindings.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `template_id` | `str` | — | required | Reference to external template |
| `bindings` | `dict[str, str]` | `{}` | — | Maps template actor slot names → workflow actor names |

#### StoreDefinition [D-SF1-23]

Named store declaration. Runner instantiates the actual implementation.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `description` | `str \| None` | `None` | — | Human-readable store description |
| `keys` | `dict[str, StoreKeyDefinition] \| None` | `None` | None = open store | Declared keys with optional types. None means open store — any key accepted. |

#### StoreKeyDefinition [D-SF1-23]

Individual key declaration within a store.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `type_ref` | `str \| None` | `None` | Must reference `workflow.types` key if set | Expected value type. None = untyped (accepts Any). |
| `description` | `str \| None` | `None` | — | Human-readable key description |

#### WorkflowInputDefinition [D-SF1-30]

Declares an expected input to the workflow. SF-2 validates all required inputs are provided at `run()` time.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | — | required | Input parameter name |
| `type_ref` | `str \| None` | `None` | Must reference `workflow.types` key if set | Expected input type. None = untyped (accepts Any). |
| `required` | `bool` | `True` | — | Whether this input must be provided at run time |
| `default` | `Any` | `None` | Only valid when `required=False` | Default value when input not provided |
| `description` | `str \| None` | `None` | — | Human-readable description |

#### WorkflowOutputDefinition [D-SF1-30]

Declares an expected output from the workflow. SF-2 validates all declared outputs are produced by execution completion.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | — | required | Output parameter name |
| `type_ref` | `str \| None` | `None` | Must reference `workflow.types` key if set | Expected output type. None = untyped. |
| `description` | `str \| None` | `None` | — | Human-readable description |

#### WorkflowConfig (root model) [D-SF1-6, D-SF1-21, D-SF1-30]

Top-level workflow definition. Everything referenced by name (actors, types, plugins) uses dict keys. Single `edges` list for all cross-phase connections (both data and hook edges). Workflow-level I/O declarations for SF-2 validation.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `schema_version` | `str` | `"1.0"` | semver string [D-SF1-6] | Schema format version |
| `name` | `str` | — | required | Workflow name |
| `description` | `str \| None` | `None` | — | Workflow description |
| `inputs` | `list[WorkflowInputDefinition]` | `[]` | — | Workflow-level input declarations. SF-2 validates required inputs provided at run time. [D-SF1-30] |
| `outputs` | `list[WorkflowOutputDefinition]` | `[]` | — | Workflow-level output declarations. SF-2 validates all declared outputs produced. [D-SF1-30] |
| `actors` | `dict[str, ActorDefinition]` | `{}` | — | Named actor definitions |
| `types` | `dict[str, TypeDefinition]` | `{}` | — | Named output type definitions |
| `phases` | `list[PhaseDefinition]` | `[]` | — | Top-level phases |
| `edges` | `list[Edge]` | `[]` | — | ALL top-level (cross-phase) edges — both data and hook [D-SF1-21] |
| `plugins` | `dict[str, PluginInterface]` | `{}` | — | Plugin type definitions |
| `templates` | `dict[str, TemplateRef]` | `{}` | — | Template references with bindings |
| `cost_config` | `WorkflowCostConfig \| None` | `None` | — | Workflow-level cost config (uses `WorkflowCostConfig`, not the removed generic `CostConfig`) |
| `context_keys` | `list[str]` | `[]` | — | Workflow-level runtime context selection keys [D-GR-41] |

**REJECTED root fields** (validation MUST reject with guidance):
- `plugin_instances` — no root plugin instance registry; use `plugin_ref` + inline `config` on PluginNode
- `stores` — no root stores registry
- `context_text` — use `context_keys` instead
- `inputs` / `outputs` (workflow-root I/O) — declared via `WorkflowInputDefinition` / `WorkflowOutputDefinition` lists above
- `switch_function` — not a root field; not valid on any entity

### Entity Relations

| From Entity | To Entity | Kind | Label |
|-------------|-----------|------|-------|
| WorkflowConfig | ActorDefinition | one-to-many | `actors` dict values |
| WorkflowConfig | TypeDefinition | one-to-many | `types` dict values |
| WorkflowConfig | PhaseDefinition | one-to-many | `phases` list |
| WorkflowConfig | Edge | one-to-many | top-level `edges` (data + hook) |
| WorkflowConfig | PluginInterface | one-to-many | `plugins` dict values |
| WorkflowConfig | WorkflowCostConfig | one-to-one | `cost_config` |
| PhaseDefinition | NodeDefinition | one-to-many | internal `nodes` |
| PhaseDefinition | Edge | one-to-many | internal `edges` (data + hook) |
| PhaseDefinition | PhaseDefinition | one-to-many | nested `children` (recursive) |
| PhaseDefinition | PortDefinition | one-to-many | `inputs`, `outputs`, `hooks` |
| NodeBase | PortDefinition | one-to-many | `inputs`, `outputs`, `hooks` |
| NodeBase | TypeDefinition | many-to-many | `input_type`/`output_type` reference `types` dict keys [D-SF1-22] |
| PhaseDefinition | TypeDefinition | many-to-many | `input_type`/`output_type` reference `types` dict keys [D-SF1-22] |
| AskNode | ActorDefinition | many-to-many | `actor` references `actors` dict key |
| PluginNode | PluginInterface | many-to-many | `plugin_ref` references `plugins` dict key |
| BranchNode | BranchOutputPort | one-to-many | `outputs` dict values [D-GR-35] |
| ErrorNode | PortDefinition | one-to-many | `inputs` dict (no outputs, no hooks) [D-GR-36] |
| PluginInterface | PortDefinition | one-to-many | `inputs`, `outputs` |
| ActorDefinition | RoleDefinition | one-to-one | `role` (for agent type) |
| Edge | PortDefinition | many-to-many | `source`/`target` reference port names via dot notation — source may be `outputs` (data) or `hooks` (hook) |
| BranchOutputPort | TypeDefinition | many-to-many | `type_ref` references `types` dict keys |
| WorkflowConfig | WorkflowInputDefinition | one-to-many | `inputs` list [D-SF1-30] |
| WorkflowConfig | WorkflowOutputDefinition | one-to-many | `outputs` list [D-SF1-30] |
| WorkflowInputDefinition | TypeDefinition | many-to-many | `type_ref` references `types` dict keys [D-SF1-30] |
| WorkflowOutputDefinition | TypeDefinition | many-to-many | `type_ref` references `types` dict keys [D-SF1-30] |

### Architecture Decisions

1. **Pure data layer — zero runtime coupling.** The `iriai_compose/schema/` package imports nothing from `iriai_compose.actors`, `iriai_compose.tasks`, `iriai_compose.runner`, or `iriai_compose.workflow`. Standalone Pydantic v2 models that mirror field names from the runtime classes. [code: iriai-compose/iriai_compose/actors.py:8-16]
2. **Single PortDefinition type eliminates port-type explosion.** [D-SF1-10]
3. **Single Edge type eliminates edge-type explosion.** [D-SF1-21] — mirrors PortDefinition unification. Hook vs data determined by source port container, not edge type.
4. **Four node types + four phase modes = complete representation.** AskNode, BranchNode, PluginNode, and ErrorNode (D-GR-36). Validated against all 145+ nodes across 3 workflows. [D-SF1-3, D-SF1-12, D-GR-36]
5. **Per-port condition routing only — `switch_function` REJECTED.** BranchNode uses `outputs: dict[str, BranchOutputPort]` with per-port `condition` expressions and non-exclusive fan-out per D-GR-35. `switch_function`, `condition_type`, `condition`, `paths` (stale top-level branch fields) are all rejected. `merge_function` is valid only for multi-input gather. [D-SF1-2, D-GR-35]
6. **Strict phase I/O boundary enforces encapsulation.** [D-SF1-4]
7. **Expression strings with documented evaluation contexts.** [D-SF1-15]
8. **`fresh_sessions` on LoopConfig/FoldConfig for phase-iteration session management.** Session clearing is a phase lifecycle concern. Same actor participates in both persistent and fresh contexts depending on which phase it's in. [D-SF1-16]
9. **PluginNode 0+ outputs for fire-and-forget.** Side-effect plugins (git_commit_push, preview_cleanup) don't produce data. Empty outputs list eliminates spurious dangling-edge validation errors and makes the DAG semantically accurate. [D-SF1-19]
10. **Async gather is runner-only.** The schema declares ports as static data. The runner (SF-2) implements the barrier/join behavior when a BranchNode has multiple input ports. This separation keeps SF-1 as a pure data layer. [D-SF1-20]
11. **I/O type declarations on NodeBase and PhaseDefinition — not AskNode-specific.** All nodes and phases can declare their expected input and produced output data structures. Port-level `type_ref` takes precedence for multi-port granularity. Type resolution is a helper function used by validation (`_check_type_flow`) and downstream consumers (SF-2, SF-6). [D-SF1-22]
12. **Store as schema-level entity — pure interface, no implementation.** The `stores` dict declares named stores with keys and types. The runner instantiates actual implementations (Postgres, filesystem, in-memory). No `mirror_path` or other implementation config in schema. [D-SF1-23]
13. **Dot notation for all store references.** `artifact_key`, `context_keys`, `handover_key` all use `"store_name.key_name"` format. No separate `store` field on nodes. [D-SF1-26]
14. **Four-level context hierarchy — workflow → phase → actor → node.** `context_keys` (store refs) and `context_text` (inline text) at each level. Merged at runtime with deduplication. [D-SF1-24]
15. **Context store bindings on actors — declarative, not strategic.** `context_store` and `handover_key` declare WHERE an actor reads/writes. HOW (compaction strategy) is SF-2. [D-SF1-25]
16. **Artifact hosting as DAG topology.** Node → on_end hook → doc_hosting plugin. No schema-level hosting config. [D-SF1-27]
17. **`artifact_key` auto-write semantics.** Runner auto-writes node output to store at `artifact_key` after execution — before output port routing. Replaces explicit `artifacts.put()` calls. Fewer PluginNodes needed for simple artifact storage. [D-SF1-29]
18. **Workflow-level I/O declarations.** `inputs` and `outputs` on WorkflowConfig declare what the workflow expects and produces. SF-2 validates at run time. `type_ref` references `workflow.types`. [D-SF1-30]
19. **`switch_function` is REJECTED per D-GR-35.** All BranchNode routing uses per-port `BranchOutputPort.condition` expressions with non-exclusive fan-out. Validation rejects `switch_function` on any BranchNode with migration guidance. `merge_function` is orthogonal (input merging for gather nodes only). [D-GR-35]

### Architecture Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-1 | Phase boundary model may be too strict for some iriai-build-v2 patterns (e.g., PlanReviewPhase has two conceptual steps that map to two sequential phases) | medium | Start strict; relax if SF-4 discovers gaps (J-14). `per_subfeature_loop`'s cross-iteration data access validated as representable via Fold accumulator. | STEP-4, STEP-6 |
| RISK-2 | Per-port condition strings may be error-prone for users to author | low | SF-6 provides condition builder. SF-2 validates syntax at execution. Common patterns validated: `"data.complete"`, `"data is True"`, `"data.verdict == 'approved'"`. | STEP-1, STEP-2 |
| RISK-3 | Inline Python in all expression fields is a security concern for untrusted workflows | medium | SF-1 stores opaque strings. SF-2 runner evaluates in restricted context (no imports, no side effects, only documented variables). | STEP-1–4 |
| RISK-4 | Pydantic v2 model_json_schema() output may need post-processing for React JSON Schema Form | low | SF-6 may need thin adapter. | STEP-5, STEP-7 |
| RISK-5 | YAML key ordering with pyyaml sort_keys=False may not be perfectly stable | low | Acceptable for SF-1. SF-3 snapshot tests use ruamel.yaml if needed. | STEP-7 |
| RISK-6 | Default ports serialize verbose YAML (every node emits inputs/outputs/hooks) | low | Use `exclude_defaults=False` for correctness. Consider `exclude_defaults=True` if verbosity is an issue. | STEP-7, STEP-8 |
| RISK-7 | Branch non-exclusive fan-out may create unexpected parallel execution if conditions overlap | medium | Validation warns when conditions missing. Documentation explicit. Validated against `runner.parallel()` pattern. Runner implements barrier for multi-input gather [D-SF1-20]. | STEP-2, STEP-6 |
| RISK-8 | Phase-level `fresh_sessions` is blanket — clears ALL agent sessions in the phase per iteration, even if only one actor needs it | low | In practice, `_clear_agent_session` in iriai-build-v2 is always called at the start of the loop for all agents that will be invoked. InteractionActors are unaffected (uses Pending, not sessions). If selective clearing is needed later, can evolve to `fresh_sessions: list[str]` (actor names). Start simple. | STEP-4 |
| RISK-9 | Collection expression `ctx` may not contain all needed data for complex patterns | medium | Complex patterns use Plugin pre-processing before Map/Fold phase. Validated: `per_subfeature_loop`'s collection accessible via context key. | STEP-4 |
| RISK-10 | Hook-sourced edge identification requires port resolution at validation and render time | low | Port resolution is already needed for all edge validation (dangling edge checks, type flow). Hook classification is an O(1) lookup per edge once the port index is built. No additional traversal cost. | STEP-6 |
| RISK-11 | Node-level `input_type`/`output_type` and port-level `type_ref` could create confusion when both are set on a single-port node | low | Clear precedence rule: port-level `type_ref` always wins. `resolve_port_type()` helper enforces this. Validation can optionally warn if both are set and disagree. | STEP-1, STEP-6 |
| RISK-12 | Open stores allow any key — no compile-time validation of dynamic key references like `"{prefix}:{slug}"` | low | Open stores are intentionally unconstrained for dynamic patterns. Validation only checks store name prefix, not key existence. Runtime errors surface at execution time. | STEP-5, STEP-6 |
| RISK-13 | Four-level context merge may create unexpectedly large prompts if workflow + phase + actor + node all inject context | medium | Runner should track total context size. Context strategy (compaction, summarization) deferred to SF-2 runner config. | STEP-1, STEP-4 |
| RISK-14 | Dot notation parsing may conflict with keys containing dots in their names | low | Store keys should not contain dots. Validation can enforce this. First dot is always the store/key separator. | STEP-6 |
| RISK-15 | `switch_function` is REJECTED (D-GR-35). Legacy YAML containing `switch_function` must be migrated to per-port conditions. | low | Validation rejects `switch_function` with guidance to use per-port `BranchOutputPort.condition` expressions. Migration tooling (SF-4) handles conversion. | STEP-2, STEP-6 |
| RISK-16 | `artifact_key` auto-write may cause unexpected store writes if node output is large or structured differently than expected | low | Same risk as explicit `artifacts.put()` in iriai-build-v2 — the schema declares intent, the runner writes. Store key `type_ref` validation (if set) catches type mismatches at validation time. | STEP-1, STEP-6 |
| RISK-17 | Workflow I/O validation at run time may reject valid workflows where outputs are produced conditionally (not all branches reach all output-producing nodes) | medium | SF-2 runner should treat `outputs` as "expected when workflow succeeds" — not "guaranteed on every path". Documentation clarifies. Conditional outputs can be marked optional (future extension). | STEP-5, STEP-6 |

---

## Implementation Steps

### STEP-1: Package Scaffold & Base Models

**Objective:** Create `iriai_compose/schema/` package with `PortDefinition` (including `condition`), `NodeBase` (default ports [D-SF1-11], I/O type fields [D-SF1-22]), `ActorDefinition` (with type validation and `persistent`), `RoleDefinition`, and `TypeDefinition`.

**Scope:**
- `iriai_compose/schema/__init__.py` — create
- `iriai_compose/schema/base.py` — create
- `iriai_compose/schema/types.py` — create
- `iriai_compose/schema/actors.py` — create
- `iriai_compose/pyproject.toml` — modify (add pyyaml, testing extra)
- `iriai_compose/iriai_compose/actors.py` — read
- `iriai_compose/iriai_compose/tasks.py` — read

**Instructions:**

1. Create `iriai_compose/schema/__init__.py` with docstring and placeholder `__all__`.

2. Create `iriai_compose/schema/base.py`: `PortDefinition` (name, type_ref, description, condition), `_default_inputs()`, `_default_outputs()`, `_default_hooks()`, `NodeBase` (id, type, summary, context_keys, context_text, artifact_key, input_type, input_schema, output_type, output_schema, inputs, outputs, hooks, position). All as documented in Architecture entities section.

   NodeBase validators:
   - `_check_input_config`: model_validator that raises `ValueError` if both `input_type` and `input_schema` are set. Message: `"input_type and input_schema are mutually exclusive"`.
   - `_check_output_config`: model_validator that raises `ValueError` if both `output_type` and `output_schema` are set. Message: `"output_type and output_schema are mutually exclusive"`.

3. Create `iriai_compose/schema/actors.py`: `RoleDefinition` (name, prompt, tools, model, effort, metadata — matching `iriai_compose.actors.Role` exactly), `ActorDefinition` (type, role, resolver, context_keys, persistent) with:
   - `persistent: bool = True` — when True, sessions survive workflow restarts. This is an actor property.
   - `_check_actor_type` model_validator that enforces: agent requires role, interaction requires resolver.

4. Create `iriai_compose/schema/types.py`: `TypeDefinition` (name, schema_def, description).

5. Add `pyyaml>=6.0` to `pyproject.toml` main dependencies. Add `testing = ["pyyaml>=6.0"]` optional dependency group.

**Acceptance Criteria:**
- `from iriai_compose.schema.base import NodeBase, PortDefinition` succeeds
- `RoleDefinition` fields exactly match `iriai_compose.actors.Role` (name, prompt, tools, model, effort, metadata)
- `NodeBase.hooks` uses `PortDefinition` — no `HookDefinition` type exists [D-SF1-10]
- `PortDefinition` includes `condition: str | None = None` [D-SF1-2]
- `NodeBase()` defaults match [D-SF1-11]
- `NodeBase` has `context_text: dict[str, str]` defaulting to `{}` [D-SF1-24]
- NodeBase has `input_type`, `input_schema`, `output_type`, `output_schema` — all `str | None` or `dict | None` defaulting to `None` [D-SF1-22]
- `NodeBase(input_type="PRD", input_schema={"type": "object"})` raises ValidationError (mutual exclusion)
- `NodeBase(output_type="PRD", output_schema={"type": "object"})` raises ValidationError (mutual exclusion)
- `NodeBase(input_type="PRD", output_type="TechnicalPlan")` succeeds — cross-pair is fine
- `ActorDefinition(type="agent")` without role raises ValidationError
- `ActorDefinition(type="interaction")` without resolver raises ValidationError
- ActorDefinition fields: type, role, resolver, context_keys, persistent (NO `fresh_session` — session clearing is on phase configs [D-SF1-16])
- `pip install -e .` succeeds with pyyaml

**Counterexamples:**
- Do NOT create `HookDefinition` [D-SF1-10]
- Do NOT create `HookEdge` [D-SF1-21]
- Do NOT import from `iriai_compose.actors` at runtime
- Do NOT add `ruamel.yaml`
- Do NOT add `kind` field to `PortDefinition`
- Do NOT default inputs/outputs to empty lists [D-SF1-11]
- Do NOT put `fresh_session` or `fresh_sessions` on ActorDefinition — session clearing is a phase iteration concern on LoopConfig/FoldConfig [D-SF1-16]
- Do NOT put `output_type`/`output_schema` on AskNode — they are on NodeBase, inherited by all types [D-SF1-22]

---

### STEP-2: Node Type Models (Ask, Branch, Plugin, Error)

**Objective:** Four node types as discriminated union per D-GR-36. All inherit `input_type`/`input_schema`/`output_type`/`output_schema` from NodeBase [D-SF1-22]. PluginNode with `plugin_ref` only (no `instance_ref`, no root `plugin_instances`) and 0+ outputs for fire-and-forget [D-SF1-19]. BranchNode with per-port `BranchOutputPort.condition` expressions and non-exclusive fan-out per D-GR-35. `switch_function` REJECTED. ErrorNode as 4th atomic type per D-GR-36.

**Scope:**
- `iriai_compose/schema/nodes.py` — create
- `iriai_compose/schema/base.py` — read

**Instructions:**

Create `iriai_compose/schema/nodes.py` with `AskNode`, `BranchNode`, `PluginNode`, `ErrorNode`, and `NodeDefinition` discriminated union as documented in Architecture entities section. Include all validators:
- AskNode: `_fix_input_ports` (fixed single input). No output_type/output_schema fields on AskNode itself — these are inherited from NodeBase [D-SF1-22].
- BranchNode: `_validate_branch_ports` (min 1 input, min 2 BranchOutputPort entries in `outputs`) [D-GR-35]. Gather semantics when >1 input (runner implements barrier [D-SF1-20]). `merge_function: str | None = None` for input merging — valid only on gather (multi-input) BranchNodes [D-GR-35]. `_validate_rejected_fields` model_validator: REJECTS `switch_function`, `condition_type`, `condition`, `paths`, `output_field` if present. Raises `ValueError("switch_function is rejected per D-GR-35; use per-port BranchOutputPort.condition expressions")`. `_validate_merge_function_gather_only`: if `merge_function` is set, requires 2+ input ports.
- PluginNode: `_fix_input_ports` (fixed single input), `_check_plugin_ref` (`plugin_ref` must be set; `instance_ref` is REJECTED). Outputs allow empty dict (0+) for fire-and-forget [D-SF1-19] — no `_validate_min_outputs`.
- ErrorNode: `type="error"`, `message` (required, Jinja2 template), `inputs` (dict), NO outputs, NO hooks [D-GR-36].

**Acceptance Criteria:**
- AskNode default ports work [D-SF1-11]
- AskNode inherits input_type/input_schema/output_type/output_schema from NodeBase [D-SF1-22]
- `AskNode(id="a", type="ask", actor="pm", prompt="...", output_type="PRD")` works — output_type inherited from NodeBase
- `AskNode(id="a", type="ask", actor="pm", prompt="...", output_type="PRD", output_schema={"type":"object"})` raises — mutual exclusion from NodeBase
- AskNode input always fixed [D-SF1-12]
- BranchNode min 1 input, min 2 BranchOutputPort outputs enforced [D-GR-35]
- BranchNode has NO actor field [D-28]
- BranchNode does NOT have `switch_function` — it is REJECTED [D-GR-35]
- BranchNode has `merge_function: str | None = None` (valid only on gather/multi-input)
- `BranchNode(id="b", type="branch", outputs={"approved": BranchOutputPort(condition="data.verdict == 'approved'"), "rejected": BranchOutputPort(condition="data.verdict != 'approved'")})` validates — per-port conditions [D-GR-35]
- BranchNode with `switch_function` field raises `ValueError` — rejected per D-GR-35
- BranchNode inherits output_type/output_schema from NodeBase — can declare merged output shape
- PluginNode requires `plugin_ref` (no `instance_ref`) [D-SF1-17]
- PluginNode input always fixed [D-SF1-14]
- PluginNode with `outputs: {}` (empty) validates — fire-and-forget [D-SF1-19]
- PluginNode inherits output_type from NodeBase — can declare plugin output shape (e.g., `output_type: "FileList"`)
- ErrorNode has `message` (required), `inputs` (dict), NO `outputs`, NO `hooks` [D-GR-36]
- `ErrorNode(id="e", type="error", message="Failed: {{ reason }}", inputs={"reason": PortDefinition(type_ref="str")})` validates
- NodeDefinition discriminated union includes all four types and round-trips

**Counterexamples:**
- Do NOT add actor to BranchNode [D-28]
- Do NOT add options to AskNode [D-SF1-12]
- Do NOT create Map/Fold/Loop node types [D-SF1-4]
- Do NOT add output_type/output_schema/input_type/input_schema as AskNode-specific fields — they are on NodeBase [D-SF1-22]
- Do NOT add `switch_function` to BranchNode — it is REJECTED per D-GR-35
- Do NOT add `instance_ref` to PluginNode — root `plugin_instances` is REJECTED
- Do NOT add outputs or hooks to ErrorNode — it is a terminal error-raising node [D-GR-36]

---

### STEP-3: Edge Model (Unified Data + Hook)

**Objective:** Define a single `Edge` model that serves for both data edges (with optional inline transform) and hook edges (fire-and-forget, no transform). No separate `HookEdge` type [D-SF1-21].

**Scope:**
- `iriai_compose/schema/edges.py` — create
- `iriai_compose/schema/base.py` — read

**Instructions:**

Create `iriai_compose/schema/edges.py` with a single `Edge` model as documented in the Architecture entities section. The `Edge` has: `source`, `target`, `transform_fn` (optional), `description` (optional).

Add a module-level docstring explaining the unified edge model:
- Data edges: source is `"node_id.port_name"` where port_name resolves to a port in the source element's `outputs` list. `transform_fn` is optional.
- Hook edges: source is `"node_id.hook_name"` where hook_name resolves to a port in the source element's `hooks` list. `transform_fn` must be `None`. The validation module (STEP-6) enforces this constraint.
- `$input` and `$output` are pseudo-ports for phase boundary wiring.

Add a helper function `is_hook_source(source_str: str, port_index: dict) -> bool` that takes a source string and a pre-built port index (mapping `"node_id.port_name"` → container name like `"outputs"` or `"hooks"`) and returns `True` if the source resolves to a hook port. This helper is used by validation (STEP-6) and can be used by downstream consumers (SF-2 runner, SF-6 UI).

Also add a helper function `parse_port_ref(ref: str) -> tuple[str, str]` that splits `"node_id.port_name"` into `(node_id, port_name)`. Returns `("$input", "")` or `("$output", "")` for phase boundary pseudo-ports.

**Acceptance Criteria:**
- `Edge` with `transform_fn` stores inline Python [D-21]
- `Edge` without `transform_fn` (None) is valid — used for hook edges
- `$input`/$`$output` pseudo-ports parse correctly via `parse_port_ref`
- `is_hook_source` returns True for hook port refs, False for output port refs
- No `HookEdge` class exists anywhere [D-SF1-21]

**Counterexamples:**
- Do NOT create `HookEdge` as a separate model [D-SF1-21]
- Do NOT add `transform_ref` [D-21]
- Do NOT add `edge_type` or `is_hook` discriminator field to `Edge` — the source port determines semantics [D-SF1-21]
- Do NOT add model-level validation of `transform_fn` on `Edge` itself — the Edge model doesn't know which ports are hooks. That's the validation module's job (STEP-6), which has access to the full workflow context.

---

### STEP-4: Phase Definition Model

**Objective:** PhaseDefinition with 4 modes, single `edges` list (no separate `hook_edges` [D-SF1-21]), I/O type fields [D-SF1-22]. All PhaseConfig expression fields include description with eval context documentation [D-SF1-15]. Loop auto-creates dual exit ports. `fresh_sessions: bool = False` on LoopConfig and FoldConfig [D-SF1-16].

**Scope:**
- `iriai_compose/schema/phases.py` — create
- `iriai_compose/schema/base.py` — read
- `iriai_compose/schema/nodes.py` — read
- `iriai_compose/schema/edges.py` — read

**Instructions:** Create SequentialConfig, MapConfig, FoldConfig, LoopConfig, PhaseDefinition as documented in Architecture entities. All expression fields (collection, accumulator_init, reducer, exit_condition) have Field descriptions documenting evaluation contexts per the Expression Evaluation Contexts table. Add `fresh_sessions: bool = False` to LoopConfig and FoldConfig — when True, runner clears all agent actor sessions used within the phase before each iteration [D-SF1-16].

PhaseDefinition has a single `edges: list[Edge]` field — no `hook_edges` field. Both data edges and hook edges go in this list [D-SF1-21].

PhaseDefinition has `input_type`, `input_schema`, `output_type`, `output_schema` with the same mutual exclusion validators as NodeBase [D-SF1-22].

**Acceptance Criteria:**
- Loop auto-creates condition_met + max_exceeded ports [J-6]
- Fold without fold_config raises
- Phase ports match NodeBase defaults [D-SF1-11]
- FoldConfig, LoopConfig, MapConfig expression fields have description with eval context [D-SF1-15]
- LoopConfig with `fresh_sessions=True` stores correctly [D-SF1-16]
- FoldConfig with `fresh_sessions=True` stores correctly [D-SF1-16]
- PhaseDefinition has ONE `edges` field, not separate `edges` + `hook_edges` [D-SF1-21]
- PhaseDefinition has `input_type`, `input_schema`, `output_type`, `output_schema` [D-SF1-22]
- `PhaseDefinition(id="p", mode="fold", fold_config=..., input_type="SubfeatureList", output_type="ProcessedResult")` works
- `PhaseDefinition(id="p", mode="fold", fold_config=..., input_type="X", input_schema={"type":"object"})` raises (mutual exclusion)
- A PhaseDefinition with edges whose sources reference hook ports is valid at the model level (constraint enforcement is in STEP-6 validation)

**Counterexamples:**
- Do NOT create separate phase subclasses per mode
- Do NOT put loop exit logic in BranchNode [D-SF1-5]
- Do NOT put `fresh_sessions` on SequentialConfig or MapConfig — sequential has no iterations, map already isolates sessions [D-SF1-16]
- Do NOT add `hook_edges: list[Edge]` or `hook_edges: list[HookEdge]` field [D-SF1-21]

---

### STEP-5: WorkflowConfig Root Model & JSON Schema Generation

**Objective:** Root WorkflowConfig with single `edges` list [D-SF1-21], WorkflowCostConfig/PhaseCostConfig/NodeCostConfig, PluginInterface, TemplateRef, WorkflowInputDefinition, WorkflowOutputDefinition [D-SF1-30]. Populate `__init__.py` with all re-exports including ErrorNode. No `PluginInstanceConfig` — root `plugin_instances` is REJECTED.

**Scope:**
- `iriai_compose/schema/workflow.py` — create
- `iriai_compose/schema/cost.py` — create
- `iriai_compose/schema/templates.py` — create
- `iriai_compose/schema/plugins.py` — create
- `iriai_compose/schema/__init__.py` — modify

**Instructions:** Create all models as documented in Architecture entities. PluginInterface uses PortDefinition with same defaults as NodeBase [D-SF1-11]. WorkflowConfig has a single `edges: list[Edge]` field for all top-level connections [D-SF1-21]. WorkflowConfig root is CLOSED — only approved fields per D-GR-22. Root `plugin_instances`, `stores`, `context_text` are REJECTED. Create `WorkflowInputDefinition` and `WorkflowOutputDefinition` in `workflow.py` [D-SF1-30] — add `inputs: list[WorkflowInputDefinition] = []` and `outputs: list[WorkflowOutputDefinition] = []` to WorkflowConfig. Update `__init__.py` with complete `__all__` exports including ErrorNode [D-GR-36] and BranchOutputPort.

**Acceptance Criteria:**
- `from iriai_compose.schema import WorkflowConfig, WorkflowInputDefinition, WorkflowOutputDefinition, ErrorNode` succeeds
- `WorkflowConfig.model_json_schema()` produces valid JSON Schema
- No `HookEdge`, `hook_edges`, TransformRef, options, `switch_function`, `plugin_instances`, or `stores` in schema
- `switch_function` NOT present on BranchNode in schema — REJECTED [D-GR-35]
- `ErrorNode` present in NodeDefinition discriminated union in schema [D-GR-36]
- WorkflowConfig has ONE `edges` field (type `list[Edge]`), not `edges` + `hook_edges` [D-SF1-21]
- WorkflowConfig has `inputs: list[WorkflowInputDefinition]` and `outputs: list[WorkflowOutputDefinition]` [D-SF1-30]
- `WorkflowInputDefinition` has fields: `name` (str, required), `type_ref` (str|None), `required` (bool, default True), `default` (Any, default None), `description` (str|None)
- `WorkflowOutputDefinition` has fields: `name` (str, required), `type_ref` (str|None), `description` (str|None)
- `WorkflowConfig(name="test", inputs=[WorkflowInputDefinition(name="feature", type_ref="Feature", required=True)])` works
- JSON Schema shows a single Edge definition (no HookEdge definition)
- JSON Schema shows `input_type`, `input_schema`, `output_type`, `output_schema` on NodeBase (inherited by all node types) AND on PhaseDefinition [D-SF1-22]
- JSON Schema shows `inputs` and `outputs` arrays on WorkflowConfig with proper definitions [D-SF1-30]
- JSON Schema does NOT show `output_type`/`output_schema` as AskNode-specific fields — they are on the base
- JSON Schema does NOT show `plugin_instances` on WorkflowConfig — REJECTED

**Counterexamples:**
- Do NOT add transforms registry [D-21]
- Do NOT add version history [D-17]
- Do NOT import from iriai_compose.tasks or .actors
- Do NOT add `hook_edges` field to WorkflowConfig [D-SF1-21]
- Do NOT add `plugin_instances` or `stores` to WorkflowConfig — REJECTED per closed root
- Do NOT add `switch_function` to BranchNode — REJECTED per D-GR-35

---

### STEP-6: Structural Validation

**Objective:** Validation functions returning `list[ValidationError]`. 21 checks including hook edge constraint enforcement [D-SF1-21], I/O config mutual exclusion [D-SF1-22], type reference validation, type flow checking with `resolve_port_type()` helper, rejected branch field validation (D-GR-35 — `switch_function`, stale top-level fields), workflow I/O type ref validation [D-SF1-30], and lenient required field checking.

**Scope:**
- `iriai_compose/schema/validation.py` — create
- All prior schema modules — read

**Instructions:** Create `ValidationError` dataclass and `validate_workflow`, `validate_type_flow`, `detect_cycles` functions. Private `_check_*` helpers for each validation.

**Authoritative error codes (21 total) [H-3]:**

| # | Code | Description | Emitted by |
|---|------|-------------|------------|
| 1 | `dangling_edge` | Edge references nonexistent node/port | `_check_edges` |
| 2 | `duplicate_node_id` | Two nodes share ID within a phase | `_check_duplicate_ids` |
| 3 | `duplicate_phase_id` | Two phases share ID | `_check_duplicate_ids` |
| 4 | `invalid_actor_ref` | Node actor not in `workflow.actors` | `_check_actor_refs` |
| 5 | `invalid_phase_mode_config` | Missing mode-specific config | `_check_phase_configs` |
| 6 | `invalid_hook_edge_transform` | Hook-sourced edge has `transform_fn` | `_check_hook_edge_constraints` |
| 7 | `phase_boundary_violation` | `$input`/`$output` wiring errors | `_check_phase_boundaries` |
| 8 | `cycle_detected` | DAG cycle found | `_check_cycles` |
| 9 | `unreachable_node` | No incoming edges, not phase entry | `_check_reachability` |
| 10 | `type_mismatch` | Edge source output type ≠ target input type | `_check_type_flow` |
| 11 | `invalid_branch_config` | Branch missing min ports (min 2 BranchOutputPort entries) | `_check_branch_configs` |
| 12 | `invalid_plugin_ref` | plugin_ref not found in `workflow.plugins` | `_check_plugin_refs` |
| 13 | `missing_output_condition` | Multi-port BranchNode without conditions (warning) | `_check_output_port_conditions` |
| 14 | `invalid_io_config` | Both input_type and input_schema set (or both output_*) | `_check_io_configs` |
| 15 | `invalid_type_ref` | Type reference not in `workflow.types` | `_check_type_refs` |
| 16 | `unsupported_root_field` | Root `stores`, `plugin_instances`, `context_text`, or other rejected field present | `_check_rejected_root_fields` |
| 17 | `rejected_node_field` | Node uses `artifact_key`, `input_type`/`input_schema`/`output_type`/`output_schema` (stale flat fields), or other rejected fields | `_check_rejected_node_fields` |
| 18 | `rejected_branch_field` | BranchNode uses `switch_function`, `condition_type`, `condition`, `paths`, or `output_field` [D-GR-35] | `_check_rejected_branch_fields` |
| 19 | `rejected_actor_field` | ActorDefinition uses `interaction` or other rejected actor_type values | `_check_rejected_actor_fields` |
| 20 | `invalid_workflow_io_ref` | Workflow input/output `type_ref` not in `workflow.types` [D-SF1-30] | `_check_workflow_io_refs` |
| 21 | `missing_required_field` | Required field missing in lenient loading path | `_check_required_fields` |

This list is authoritative for SF-3's test fixtures. Every code must have at least one test fixture that triggers it.

Port resolution [D-SF1-10]: Build a port index mapping `"node_id.port_name"` → `{"container": "outputs"|"inputs"|"hooks", "port": PortDefinition}`. For BranchNode, output ports are `BranchOutputPort` entries. Use this index for all edge validation.

Hook edge constraint [D-SF1-21]: `_check_hook_edge_constraints()` iterates all edges (both phase-internal and top-level), resolves each `edge.source` via the port index. If the source port's container is `"hooks"` and `edge.transform_fn is not None`, emit `invalid_hook_edge_transform` error with path and message like `"Hook edge from '{source}' must not have transform_fn — hook edges are fire-and-forget"`.

Phase boundary [D-SF1-4]: verify $input and $output wiring.
Plugin refs [D-SF1-17]: check plugin_ref in workflow.plugins. `instance_ref` and root `plugin_instances` are REJECTED.

I/O config validation [D-SF1-22]: `_check_io_configs()` iterates all nodes and phases. For each, checks that `input_type` and `input_schema` are not both set, and `output_type` and `output_schema` are not both set. Note: the Pydantic model validators on NodeBase/PhaseDefinition already enforce this at construction time, but `_check_io_configs` catches it for lenient loading paths.

Type reference validation [D-SF1-22]: `_check_type_refs()` iterates all nodes and phases. For each `input_type` or `output_type` value, verifies it exists as a key in `workflow.types`. For each `PortDefinition.type_ref`, also verifies. Emits `invalid_type_ref` error if not found.

Type flow with resolution [D-SF1-22]: `_check_type_flow()` uses `resolve_port_type()` helper to determine effective types at each end of a data edge. The helper implements the precedence rule: port-level `type_ref` > node-level `input_type`/`output_type` for single-port elements > None.

Rejected branch field validation [D-GR-35]: `_check_rejected_branch_fields()` iterates all BranchNodes. Rejects `switch_function`, `condition_type`, `condition`, `paths` (stale top-level branch fields), and `output_field` with guidance: `"switch_function is unsupported; use per-port BranchOutputPort.condition expressions"`, `"condition_type/condition/paths are stale; use outputs: dict[str, BranchOutputPort]"`. Also validates `merge_function` is only present on gather (multi-input) BranchNodes.

Workflow I/O validation [D-SF1-30]: `_check_workflow_io_refs()` iterates `workflow.inputs` and `workflow.outputs`. For each with `type_ref is not None`, verifies the type_ref exists as a key in `workflow.types`. Emits `invalid_workflow_io_ref` error if not found, with path like `"inputs[0].type_ref"` or `"outputs[1].type_ref"`.

Required field validation: `_check_required_fields()` catches required field violations not caught by Pydantic in lenient loading paths. Checks: AskNode has `actor` and `prompt`, ErrorNode has `message`, PhaseDefinition has `id` and `mode`, loop_config has `condition`, fold_config has `collection`/`accumulator_init`. Emits `missing_required_field` errors.

Also export:
- `build_port_index(config: WorkflowConfig) -> dict[str, PortIndexEntry]` helper for use by downstream consumers (SF-2, SF-6).
- `resolve_port_type(element, port: PortDefinition, is_input: bool) -> str | None` helper for type resolution [D-SF1-22].

**Acceptance Criteria:**
- Valid config → empty list
- Each error code has clear path and message
- Never raises — always returns list
- Port references resolve across all three lists (inputs, outputs, hooks) [D-SF1-10]
- Hook-sourced edge with `transform_fn` → `invalid_hook_edge_transform` error [D-SF1-21]
- Hook-sourced edge with `transform_fn=None` → no error
- Data-sourced edge with `transform_fn` → no error
- `build_port_index` correctly classifies ports by container (including BranchOutputPort entries)
- Node with both `input_type` and `input_schema` → `invalid_io_config` error [D-SF1-22]
- Node with both `output_type` and `output_schema` → `invalid_io_config` error [D-SF1-22]
- Node with `output_type: "NonexistentType"` → `invalid_type_ref` error [D-SF1-22]
- `resolve_port_type` returns port-level `type_ref` when set, falls back to node-level type for single-port elements, returns None otherwise
- Edge from node with `output_type: "PRD"` to node with `input_type: "TechnicalPlan"` → `type_mismatch` error
- Edge from node with `output_type: "PRD"` to node with `input_type: "PRD"` → no error
- Edge from node with `output_type: "PRD"` to node with no input_type → no error (untyped is compatible)
- BranchNode with `switch_function` field → `rejected_branch_field` error with guidance [D-GR-35]
- BranchNode with stale `condition_type`/`condition`/`paths` → `rejected_branch_field` error [D-GR-35]
- BranchNode with per-port `BranchOutputPort.condition` expressions → no error (canonical model)
- Root `plugin_instances` field → `unsupported_root_field` error
- Root `stores` field → `unsupported_root_field` error
- `WorkflowInputDefinition(name="x", type_ref="NonexistentType")` in `workflow.inputs` → `invalid_workflow_io_ref` error [D-SF1-30]
- `WorkflowOutputDefinition(name="y", type_ref="ValidType")` where "ValidType" in `workflow.types` → no error
- AskNode without `actor` field in lenient load → `missing_required_field` error

**Counterexamples:**
- Do NOT raise from validate_workflow [J-5]
- Do NOT validate Python syntax of expression fields — syntax is SF-2's concern
- Do NOT block saving
- Do NOT add `edge_type` or `is_hook` to Edge model — classification happens via port index lookup [D-SF1-21]
- Do NOT accept `switch_function` on BranchNode — REJECTED per D-GR-35
- Do NOT accept `plugin_instances` as a root field — REJECTED per closed root

---

### STEP-7: YAML Serialization & JSON Schema Export

**Objective:** YAML load/dump and JSON Schema CLI export. Final `__init__.py` updates.

**Scope:**
- `iriai_compose/schema/yaml_io.py` — create
- `iriai_compose/schema/json_schema.py` — create
- `iriai_compose/schema/__init__.py` — modify

**Instructions:** Create `load_workflow`, `load_workflow_lenient`, `dump_workflow` in yaml_io.py. Create `generate_json_schema` with `__main__` CLI in json_schema.py. Update `__init__.py` to export yaml_io, json_schema, and validation functions.

YAML serialization writes all edges (data + hook) into a single `edges` list per phase and at the workflow level [D-SF1-21]. No `hook_edges` key in YAML output.

**Acceptance Criteria:**
- YAML round-trip preserves all fields including `input_type`, `input_schema`, `output_type`, `output_schema` on all node types and phases
- YAML round-trip preserves BranchNode `outputs: dict[str, BranchOutputPort]` with per-port conditions [D-GR-35]
- YAML round-trip preserves ErrorNode `message` and `inputs` [D-GR-36]
- YAML round-trip preserves `inputs` and `outputs` on WorkflowConfig [D-SF1-30]
- JSON Schema includes `input_type`/`input_schema`/`output_type`/`output_schema` on NodeBase (inherited by all node discriminated union members) AND on PhaseDefinition [D-SF1-22]
- JSON Schema does NOT include `switch_function` on BranchNode — REJECTED [D-GR-35]
- JSON Schema includes ErrorNode as 4th discriminated union member [D-GR-36]
- JSON Schema includes `inputs` (array of WorkflowInputDefinition) and `outputs` (array of WorkflowOutputDefinition) on WorkflowConfig [D-SF1-30]
- JSON Schema does NOT include `plugin_instances` on WorkflowConfig — REJECTED
- JSON Schema has ONE `Edge` definition — no `HookEdge` [D-SF1-21]
- `python -m iriai_compose.schema.json_schema` creates file
- YAML output does NOT sort keys
- YAML output has `edges:` key but NOT `hook_edges:` key on phases and workflow [D-SF1-21]
- YAML round-trip preserves `output_type` on PluginNode (e.g., `output_type: "FileList"`) and `input_type` on BranchNode

**Counterexamples:**
- Do NOT use ruamel.yaml [D-SF1-8]
- Do NOT include TransformRef or HookEdge in schema [D-21, D-SF1-21]
- Do NOT serialize `hook_edges` as a separate YAML key [D-SF1-21]

---

### STEP-8: Tests & Fixtures

**Objective:** Comprehensive pytest tests and YAML fixtures covering all entities, validators, validation, YAML round-trip, and JSON Schema. Tests verify all hardening additions: I/O type fields on NodeBase and PhaseDefinition [D-SF1-22], `fresh_sessions` on LoopConfig/FoldConfig [D-SF1-16], instance_ref [D-SF1-17], ActorDefinition validation, expression eval context documentation [D-SF1-15], unified edge model [D-SF1-21], `switch_function` on BranchNode [D-SF1-28], workflow-level I/O [D-SF1-30], and all 21 validation error codes [H-3].

**Scope:**
- `tests/test_schema_models.py` — create
- `tests/test_schema_validation.py` — create
- `tests/test_schema_yaml.py` — create
- `tests/test_schema_json.py` — create
- `tests/fixtures/workflows/minimal_ask.yaml` — create
- `tests/fixtures/workflows/gate_pattern.yaml` — create
- `tests/fixtures/workflows/minimal_branch.yaml` — create
- `tests/fixtures/workflows/branch_gather_dispatch.yaml` — create
- `tests/fixtures/workflows/minimal_plugin.yaml` — create
- `tests/fixtures/workflows/minimal_error.yaml` — create
- `tests/fixtures/workflows/loop_interview.yaml` — create
- `tests/fixtures/workflows/fold_phase.yaml` — create
- `tests/fixtures/workflows/map_phase.yaml` — create
- `tests/fixtures/workflows/loop_fresh_sessions.yaml` — create
- `tests/fixtures/workflows/plugin_fire_and_forget.yaml` — create
- `tests/fixtures/workflows/branch_gather_only.yaml` — create
- `tests/fixtures/workflows/hook_edges.yaml` — create
- `tests/fixtures/workflows/typed_io.yaml` — create
- `tests/fixtures/workflows/invalid/dangling_edge.yaml` — create
- `tests/fixtures/workflows/invalid/missing_fold_config.yaml` — create
- `tests/fixtures/workflows/invalid/boundary_violation.yaml` — create
- `tests/fixtures/workflows/invalid/dual_output_config.yaml` — create
- `tests/fixtures/workflows/invalid/hook_edge_with_transform.yaml` — create
- `tests/fixtures/workflows/invalid/dual_input_config.yaml` — create
- `tests/fixtures/workflows/invalid/invalid_type_ref.yaml` — create
- `tests/fixtures/workflows/invalid/rejected_switch_function.yaml` — create
- `tests/fixtures/workflows/invalid/rejected_plugin_instances.yaml` — create
- `tests/fixtures/workflows/invalid/invalid_workflow_io.yaml` — create
- `tests/fixtures/workflows/workflow_io.yaml` — create

**Instructions:**

1. **Model tests** (`test_schema_models.py`):
   - **NodeBase I/O type fields [D-SF1-22]:** Test that `input_type`, `input_schema`, `output_type`, `output_schema` exist on NodeBase and are inherited by all four node types. Test mutual exclusion: `input_type` + `input_schema` raises. `output_type` + `output_schema` raises. Cross-pair allowed: `input_type` + `output_type` works. All four None by default.
   - **AskNode:** Test that AskNode has NO `output_type`/`output_schema` as its own fields — verify they come from NodeBase. Test `AskNode(output_type="PRD")` works (inherited). Test defaults, fixed inputs.
   - **BranchNode:** Test 1+ inputs, 2+ BranchOutputPort outputs, no actor, merge_function (gather only), default inputs. Test `BranchNode(output_type="MergedResult")` works (inherited). Test `BranchNode(input_type="ReviewData")` works. Test per-port conditions [D-GR-35]: `BranchNode(outputs={"approved": BranchOutputPort(condition="data.verdict == 'approved'"), "rejected": BranchOutputPort(condition="data.verdict != 'approved'")})` validates. Test `switch_function` REJECTED: any BranchNode with `switch_function` raises ValueError per D-GR-35. Test `merge_function` valid only on gather (2+ inputs).
   - **PluginNode:** Test plugin_ref required (no instance_ref), fixed inputs, 0 outputs (fire-and-forget) validates [D-SF1-19]. Test `PluginNode(output_type="FileList")` works (inherited). Test `PluginNode(input_type="ContextDict")` works.
   - **ErrorNode [D-GR-36]:** Test ErrorNode has `message` (required), `inputs` (dict), NO `outputs`, NO `hooks`. Test `ErrorNode(id="e", type="error", message="Failed: {{ reason }}", inputs={"reason": PortDefinition(type_ref="str")})` validates. Test ErrorNode is included in NodeDefinition discriminated union.
   - **PhaseDefinition:** Test loop/fold modes. Test `input_type`/`input_schema`/`output_type`/`output_schema` with mutual exclusion [D-SF1-22]. Test `PhaseDefinition(input_type="X", output_type="Y")` works.
   - Discriminated union. WorkflowConfig round-trip. PortDefinition is only port type. Port consistency. ActorDefinition validation. `fresh_sessions` on LoopConfig and FoldConfig. PluginInterface defaults.
   - **WorkflowConfig I/O [D-SF1-30]:** Test `WorkflowInputDefinition` construction with all fields. Test `WorkflowOutputDefinition` construction. Test `WorkflowConfig(name="test", inputs=[WorkflowInputDefinition(name="feature", type_ref="Feature")], outputs=[WorkflowOutputDefinition(name="result", type_ref="Result")])` round-trips. Test defaults: `required=True`, `default=None`. Test optional input: `WorkflowInputDefinition(name="x", required=False, default={"key": "value"})`. Test empty `inputs`/`outputs` (defaults).
   - **Edge model tests:** single Edge type used for both data and hook connections; no HookEdge class importable [D-SF1-21]. Test `parse_port_ref` helper. Test `is_hook_source` helper. PhaseDefinition has `edges` but no `hook_edges` field. WorkflowConfig has `edges` but no `hook_edges` field.

2. **Validation tests** (`test_schema_validation.py`):
   - **Every error code** (all 21 per [H-3]) including `invalid_hook_edge_transform` [D-SF1-21], `invalid_io_config` [D-SF1-22], `invalid_type_ref` [D-SF1-22], `rejected_branch_field` [D-GR-35], `unsupported_root_field`, `invalid_workflow_io_ref` [D-SF1-30], `missing_required_field`.
   - Port resolution across all three lists (inputs, outputs, hooks) including BranchOutputPort entries.
   - PluginNode plugin_ref validation (no instance_ref).
   - **Rejected branch field validation [D-GR-35]:** BranchNode with `switch_function` → `rejected_branch_field`. BranchNode with stale `condition_type`/`condition`/`paths` → `rejected_branch_field`. BranchNode with per-port `BranchOutputPort.condition` → no error (canonical model).
   - **Rejected root field validation:** Root `plugin_instances` → `unsupported_root_field`. Root `stores` → `unsupported_root_field`.
   - **Workflow I/O validation [D-SF1-30]:** `WorkflowInputDefinition(type_ref="NonexistentType")` → `invalid_workflow_io_ref`. `WorkflowOutputDefinition(type_ref="ValidType")` with "ValidType" in types → no error. Workflow with no inputs/outputs → no error. Workflow input with `type_ref=None` → no error (untyped).
   - **Required field validation:** AskNode without actor → `missing_required_field`. Phase without mode → `missing_required_field`.
   - **I/O config validation [D-SF1-22]:** Node with both `input_type` and `input_schema` → `invalid_io_config`. Node with both `output_type` and `output_schema` → `invalid_io_config`. Phase with both `input_type` and `input_schema` → `invalid_io_config`.
   - **Type reference validation [D-SF1-22]:** Node `input_type: "NonexistentType"` → `invalid_type_ref`. Node `output_type: "NonexistentType"` → `invalid_type_ref`. Node with valid `output_type` referencing existing `workflow.types` key → no error. Phase `input_type`/`output_type` validated similarly.
   - **Type flow with resolution [D-SF1-22]:** Edge from `output_type: "PRD"` node to `input_type: "TechnicalPlan"` node → `type_mismatch`. Edge from `output_type: "PRD"` to `input_type: "PRD"` → no error. Edge from `output_type: "PRD"` to untyped node → no error. Edge where port-level `type_ref` overrides node-level type → port type used.
   - **`resolve_port_type` tests:** returns port-level `type_ref` when set; returns node-level type for single-port nodes when port has no `type_ref`; returns None for multi-port nodes without port-level types.
   - Hook edge constraint tests. `build_port_index` classifies ports correctly.

3. **YAML tests** (`test_schema_yaml.py`): All fixtures load. Round-trip preserves. Error cases raise correctly. `typed_io.yaml` fixture demonstrates `input_type`/`output_type` on Ask, Branch, Plugin nodes and phases [D-SF1-22]. `hook_edges.yaml` demonstrates hook edges in single `edges` list. Verify YAML output has `edges:` but NOT `hook_edges:`.

4. **JSON Schema tests** (`test_schema_json.py`): Valid JSON. Discriminator present. No banned fields. `input_type`/`input_schema`/`output_type`/`output_schema` present on NodeBase definition (NOT as AskNode-specific) and on PhaseDefinition [D-SF1-22]. `fresh_sessions` on LoopConfig/FoldConfig. Expression descriptions present. No `HookEdge` definition [D-SF1-21]. Single `Edge` definition in `$defs`.

5. **Fixtures**: 18 valid + 7 invalid YAML fixtures as listed in scope.
   - `typed_io.yaml` NEW: Demonstrates I/O type fields on all node types and a phase. Contains: an AskNode with `input_type` and `output_type` set, a PluginNode with `output_type` set, a BranchNode with `input_type` and `output_type` set, a PhaseDefinition with `input_type` and `output_type` set. Types reference entries in `workflow.types`.
   - `invalid/dual_input_config.yaml` NEW: Node with both `input_type: "PRD"` and `input_schema: {type: object}` — should produce `invalid_io_config` validation error.
   - `invalid/dual_output_config.yaml` UPDATED: Node with both `output_type` and `output_schema` — now tests NodeBase-level mutual exclusion (not AskNode-specific).
   - `invalid/invalid_type_ref.yaml` NEW: Node with `output_type: "NonexistentType"` where that type is not in `workflow.types` — should produce `invalid_type_ref` validation error.
   - `invalid/rejected_switch_function.yaml` NEW: BranchNode with `switch_function: "data.path"` — should produce `rejected_branch_field` validation error [D-GR-35]. Guidance: use per-port `BranchOutputPort.condition` expressions.
   - `invalid/rejected_plugin_instances.yaml` NEW: WorkflowConfig with root `plugin_instances` field — should produce `unsupported_root_field` validation error. Guidance: use `plugin_ref` + inline `config` on PluginNode.
   - `invalid/invalid_workflow_io.yaml` NEW: WorkflowConfig with `inputs: [{name: "x", type_ref: "NonexistentType"}]` — should produce `invalid_workflow_io_ref` validation error [D-SF1-30].
   - `minimal_error.yaml` NEW: Valid workflow with an ErrorNode using `message: "Validation failed: {{ reason }}"` and one input port. Demonstrates 4th atomic node type [D-GR-36].
   - `workflow_io.yaml` NEW: WorkflowConfig with `inputs` (required + optional with default) and `outputs` declarations. Demonstrates workflow-level I/O [D-SF1-30].
   - All other fixtures as previously listed.

**Acceptance Criteria:**
- `pytest tests/test_schema_*.py` all pass
- All 21 validation error codes tested [H-3], including `invalid_hook_edge_transform` [D-SF1-21], `invalid_io_config` [D-SF1-22], `invalid_type_ref` [D-SF1-22], `rejected_branch_field` [D-GR-35], `unsupported_root_field`, `invalid_workflow_io_ref` [D-SF1-30], `missing_required_field`
- Every new feature (D-SF1-15 through D-SF1-30) tested
- ErrorNode tests verify message, inputs, no outputs, no hooks [D-GR-36]
- No test references HookEdge, hook_edges, options, output_paths, switch_function (as implementation)
- No test puts `fresh_sessions` on ActorDefinition or any node type
- No test puts `output_type`/`output_schema` as AskNode-specific fields — they are on NodeBase [D-SF1-22]
- `typed_io.yaml` fixture has `input_type`/`output_type` on multiple node types and a phase [D-SF1-22]
- `invalid/dual_input_config.yaml` triggers `invalid_io_config` error
- `invalid/invalid_type_ref.yaml` triggers `invalid_type_ref` error
- JSON Schema tests verify `input_type`/`input_schema`/`output_type`/`output_schema` are on NodeBase, not AskNode [D-SF1-22]
- `minimal_error.yaml` fixture has ErrorNode with `message` and `inputs` [D-GR-36]
- `workflow_io.yaml` fixture has `inputs` and `outputs` on WorkflowConfig [D-SF1-30]
- `invalid/rejected_switch_function.yaml` triggers `rejected_branch_field` error [D-GR-35]
- `invalid/rejected_plugin_instances.yaml` triggers `unsupported_root_field` error
- `invalid/invalid_workflow_io.yaml` triggers `invalid_workflow_io_ref` error
- JSON Schema tests verify `switch_function` is NOT present on BranchNode — REJECTED [D-GR-35]
- JSON Schema tests verify ErrorNode is in NodeDefinition union [D-GR-36]
- JSON Schema tests verify `inputs`/`outputs` on WorkflowConfig with proper sub-definitions [D-SF1-30]

**Counterexamples:**
- Do NOT test runtime execution (SF-2/SF-3)
- Do NOT test UI rendering (SF-6)
- Do NOT use iriai_compose.testing (doesn't exist yet)
- Do NOT expect empty inputs on any node [D-SF1-11]
- Do NOT put `fresh_sessions` on any actor or node in fixtures or tests
- Do NOT create any `HookEdge` instances in tests [D-SF1-21]
- Do NOT reference `hook_edges` field in any fixture YAML [D-SF1-21]
- Do NOT put `output_type`/`output_schema` as AskNode-only fields in tests — use NodeBase inheritance [D-SF1-22]
- Do NOT use `switch_function` in any valid fixture — REJECTED per D-GR-35
- Do NOT use `plugin_instances` as a root field in any valid fixture — REJECTED per closed root
- Do NOT use `instance_ref` on PluginNode in any fixture — REJECTED

---

## File Manifest

| Path | Action |
|------|--------|
| `iriai_compose/schema/__init__.py` | create |
| `iriai_compose/schema/base.py` | create |
| `iriai_compose/schema/nodes.py` | create |
| `iriai_compose/schema/edges.py` | create |
| `iriai_compose/schema/phases.py` | create |
| `iriai_compose/schema/actors.py` | create |
| `iriai_compose/schema/types.py` | create |
| `iriai_compose/schema/cost.py` | create |
| `iriai_compose/schema/plugins.py` | create |
| `iriai_compose/schema/templates.py` | create |
| `iriai_compose/schema/stores.py` | create |
| `iriai_compose/schema/workflow.py` | create |
| `iriai_compose/schema/validation.py` | create |
| `iriai_compose/schema/yaml_io.py` | create |
| `iriai_compose/schema/json_schema.py` | create |
| `pyproject.toml` | modify |
| `tests/test_schema_models.py` | create |
| `tests/test_schema_validation.py` | create |
| `tests/test_schema_yaml.py` | create |
| `tests/test_schema_json.py` | create |
| `tests/fixtures/workflows/minimal_ask.yaml` | create |
| `tests/fixtures/workflows/gate_pattern.yaml` | create |
| `tests/fixtures/workflows/minimal_branch.yaml` | create |
| `tests/fixtures/workflows/branch_gather_dispatch.yaml` | create |
| `tests/fixtures/workflows/minimal_plugin.yaml` | create |
| `tests/fixtures/workflows/minimal_error.yaml` | create |
| `tests/fixtures/workflows/loop_interview.yaml` | create |
| `tests/fixtures/workflows/fold_phase.yaml` | create |
| `tests/fixtures/workflows/map_phase.yaml` | create |
| `tests/fixtures/workflows/loop_fresh_sessions.yaml` | create |
| `tests/fixtures/workflows/plugin_fire_and_forget.yaml` | create |
| `tests/fixtures/workflows/branch_gather_only.yaml` | create |
| `tests/fixtures/workflows/hook_edges.yaml` | create |
| `tests/fixtures/workflows/typed_io.yaml` | create |
| `tests/fixtures/workflows/store_registry.yaml` | create |
| `tests/fixtures/workflows/context_hierarchy.yaml` | create |
| `tests/fixtures/workflows/artifact_hosting.yaml` | create |
| `tests/fixtures/workflows/invalid/dangling_edge.yaml` | create |
| `tests/fixtures/workflows/invalid/missing_fold_config.yaml` | create |
| `tests/fixtures/workflows/invalid/boundary_violation.yaml` | create |
| `tests/fixtures/workflows/invalid/dual_output_config.yaml` | create |
| `tests/fixtures/workflows/invalid/hook_edge_with_transform.yaml` | create |
| `tests/fixtures/workflows/invalid/dual_input_config.yaml` | create |
| `tests/fixtures/workflows/invalid/invalid_type_ref.yaml` | create |
| `tests/fixtures/workflows/invalid/rejected_switch_function.yaml` | create |
| `tests/fixtures/workflows/invalid/rejected_plugin_instances.yaml` | create |
| `tests/fixtures/workflows/invalid/invalid_workflow_io.yaml` | create |
| `tests/fixtures/workflows/workflow_io.yaml` | create |
| `tests/fixtures/workflows/invalid/undeclared_store_ref.yaml` | create |
| `tests/fixtures/workflows/invalid/invalid_store_key.yaml` | create |
| `iriai_compose/__init__.py` | read |
| `iriai_compose/actors.py` | read |
| `iriai_compose/tasks.py` | read |
| `iriai_compose/runner.py` | read |
| `iriai_compose/storage.py` | read |

---

## Interface Contracts

### SF-1 → SF-2 (Python Import)
```python
from iriai_compose.schema import (
    WorkflowConfig, AskNode, BranchNode, PluginNode, ErrorNode, NodeDefinition,
    PhaseDefinition, Edge, PortDefinition, BranchOutputPort, ActorDefinition,
    WorkflowCostConfig, PhaseCostConfig, NodeCostConfig,
    PluginInterface, TemplateDefinition,
    WorkflowInputDefinition, WorkflowOutputDefinition,
    load_workflow, dump_workflow,
)
from iriai_compose.schema.edges import parse_port_ref, is_hook_source
from iriai_compose.schema.validation import build_port_index, resolve_port_type
```
**Canonical import path [C-2]:** All imports use `iriai_compose.schema`, NOT `iriai_compose.declarative.schema`.

SF-2 loader uses `load_workflow()`. Runner dispatches on `NodeDefinition.type` (four types: ask, branch, plugin, error). Port resolution uniform across inputs/outputs/hooks. All nodes except ErrorNode guaranteed at least one input port [D-SF1-11]. ErrorNode has inputs but NO outputs and NO hooks [D-GR-36].

**I/O type fields [D-SF1-22]:** All nodes and phases have `input_type`, `input_schema`, `output_type`, `output_schema` (inherited from NodeBase for nodes). The runner uses `output_type` on AskNode to tell the agent what structured output to produce (same as existing `Ask.output_type`). For PluginNode, the runner can use `output_type` to validate plugin return data. For BranchNode, `output_type` describes the merge output. `input_type` on any node enables runtime input validation before execution.

**Unified edge model [D-SF1-21]:** All edges are `Edge` instances in a single list. The runner uses `build_port_index()` and `is_hook_source()` to classify edges:
- **Data edge** (source in `outputs`): Runner delivers data, optionally applying `transform_fn`.
- **Hook edge** (source in `hooks`): Runner triggers the target node as a fire-and-forget side effect. No transform. The runner uses the `hooks` container classification to determine this — no `edge_type` field needed.

**Type flow resolution [D-SF1-22]:** The runner can use `resolve_port_type()` for runtime type checking. Resolution priority: port-level `type_ref` > node/phase-level `input_type`/`output_type` for single-port elements > None. This enables the runner to validate data at edge boundaries.

**Output port routing [D-SF1-2, D-GR-35]:**
- **BranchNode** = non-exclusive fan-out on per-port `BranchOutputPort.condition`. Each output port's condition is evaluated independently; all ports whose condition evaluates truthy fire simultaneously. [D-GR-35]
- **BranchNode with `merge_function` (gather)** = inputs merged first, then per-port conditions evaluated against merged data.
- **PluginNode** may have 0 output ports (fire-and-forget) — runner executes the plugin but does not deliver data downstream [D-SF1-19].
- **ErrorNode** has NO output ports — it raises an error and terminates [D-GR-36].
- **`switch_function` REJECTED** — runner MUST NOT implement `switch_function` routing. All BranchNode routing uses per-port conditions only. [D-GR-35]

**Async gather/barrier [D-SF1-20]:** When a BranchNode has N>1 input ports, the runner awaits data on ALL connected input ports before firing the node. This is the DAG-level equivalent of `asyncio.gather()`. The runner tracks port satisfaction state per node. If `merge_function` is set, the runner evaluates it against `inputs = {port_name: data, ...}` to produce a single merged dict before evaluating output port conditions. If `merge_function` is None, the runner passes the `inputs` dict directly.

**Workflow I/O validation [D-SF1-30]:** `workflow.inputs` declares expected inputs. At `run()` time, the runner validates: (1) all inputs with `required=True` are provided, (2) provided inputs with `type_ref` match the declared type. `workflow.outputs` declares expected outputs. At workflow completion, the runner can optionally validate: all declared outputs exist in the appropriate store. The runner should apply `default` values for optional inputs not provided. Workflow inputs are available in the initial context/data passed to the first phase.

**Plugin resolution [D-SF1-17]:** `plugin_ref` → lookup in `workflow.plugins`. No `instance_ref` or `plugin_instances` — REJECTED.

**Map actor deduplication:** Runner auto-creates unique actor instances per iteration (like `_make_parallel_actor`).

**Expression evaluation [D-SF1-15]:** All expressions evaluated in restricted Python context with only documented variables.

### SF-1 → SF-3 (Python Import)
```python
from iriai_compose.schema import (
    WorkflowConfig, validate_workflow, ValidationError, ErrorNode,
    WorkflowInputDefinition, WorkflowOutputDefinition,
    load_workflow, dump_workflow,
)
from iriai_compose.schema.validation import resolve_port_type
```
SF-3's test fixtures must cover all 21 validation error codes [H-3]. The `minimal_error.yaml` and `workflow_io.yaml` fixtures test the new features. Invalid fixtures `rejected_switch_function.yaml`, `rejected_plugin_instances.yaml`, and `invalid_workflow_io.yaml` test the corresponding rejection error codes.

### SF-1 → SF-4 (YAML Schema)
SF-4 produces `.yaml` files conforming to `WorkflowConfig`. Inputs omittable on AskNode/PluginNode (validators enforce defaults). Expression fields use documented eval contexts. Hook edges go in the same `edges` list as data edges [D-SF1-21] — the source port determines semantics. Nodes can declare `input_type`/`output_type` for documentation and type-checking [D-SF1-22].

**`switch_function` REJECTED in migration [D-GR-35]:** All routing MUST use per-port `BranchOutputPort.condition` expressions. Gate-style routing (approved/rejected) maps to BranchNode with per-port conditions on each output. Most iriai-build-v2 patterns use imperative `if/else` control flow — these map to per-port conditions. `switch_function` MUST NOT appear in migration output.

**Workflow I/O [D-SF1-30]:** Each migrated workflow should declare `inputs` (e.g., Feature, workspace config) and `outputs` (e.g., final artifacts) on WorkflowConfig for SF-2 validation.

### SF-1 → SF-6 (JSON Schema)
```python
from iriai_compose.schema import generate_json_schema
schema = generate_json_schema()
```
Generated once at build time. Single `PortDefinition` type — UI renders differently by container. Single `Edge` type — UI renders differently by source port classification [D-SF1-21]:
- Source port in `outputs` → solid line with type label at midpoint.
- Source port in `hooks` → dashed purple (#a78bfa) line, no type label, no transform editing.

The UI builds a port index from the loaded workflow and uses `is_hook_source()` to determine edge rendering style. No `edge_type` field on the Edge model — the classification is derived at render time.

**I/O type fields on all nodes and phases [D-SF1-22]:** The UI can display "expects: PRD" and "produces: TechnicalPlan" on ANY node card — not just Ask. Inspector fields for `input_type`/`input_schema`/`output_type`/`output_schema` render on all node type inspectors and phase inspectors. The `output_schema` builder (inline field-by-field) works for ALL node types, not just Ask. JSON Schema shows these fields on NodeBase (discriminated union base), so the UI does not need per-type special-casing.

Every node has at least one input (except ErrorNode which has inputs but NO outputs). AskNode/BranchNode have 1+ outputs; PluginNode may have 0 outputs (fire-and-forget — card shows no output port, no outgoing edge handle) [D-SF1-19]. ErrorNode has NO outputs and NO hooks — it is a terminal error-raising node [D-GR-36]. Expression `description` values provide inline docs.

**BranchNode in BranchInspector [D-GR-35]:** JSON Schema includes `outputs: dict[str, BranchOutputPort]` on BranchNode. The BranchInspector renders per-port condition editors for each `BranchOutputPort.condition` expression. Multiple ports may fire simultaneously (non-exclusive fan-out). `switch_function` is REJECTED and MUST NOT appear in the BranchInspector. The card face shows the output port names with their conditions.

**ErrorNode in NodeInspector [D-GR-36]:** JSON Schema includes ErrorNode as 4th discriminated union member. The NodeInspector renders a `message` template editor (Jinja2) and input port editors. No output ports or hooks are rendered.

**Workflow I/O in JSON Schema [D-SF1-30]:** `WorkflowInputDefinition` and `WorkflowOutputDefinition` appear in the schema. The UI may use these for workflow-level configuration panels (e.g., "Workflow Inputs" section in workflow inspector showing required vs optional inputs with types).

---

## D-GR Compliance Checklist

Every plan step MUST satisfy these canonical decisions. Implementers should verify compliance before marking a step complete.

| D-GR ID | Rule | Affected Steps |
|---------|------|----------------|
| D-GR-14 | AskNode uses `prompt` (not `task`). PluginNode for explicit side effects. No `artifact_key`. | STEP-1, STEP-2 |
| D-GR-22 | WorkflowConfig root is closed. Nested phases under `children`. Hooks edge-based. `/api/schema/workflow` is canonical runtime source. | STEP-4, STEP-5, STEP-7 |
| D-GR-30 | `actor_type: agent|human` only. No `interaction`. | STEP-1, STEP-6 |
| D-GR-35 | BranchNode uses per-port `BranchOutputPort.condition` with non-exclusive fan-out. `switch_function` REJECTED. Node-level `condition_type`, `condition`, `paths` REJECTED. `merge_function` valid only for multi-input gather. | STEP-2, STEP-6, STEP-8 |
| D-GR-36 | ErrorNode IS a 4th atomic node type (Ask, Branch, Plugin, Error). ErrorNode entity: `id`, `type: error`, `message` (Jinja2 template), `inputs` (dict), NO outputs, NO hooks. | STEP-2, STEP-5, STEP-8 |
| D-GR-41 | Correct model names: EdgeDefinition, MapModeConfig, FoldModeConfig, LoopModeConfig, SequentialModeConfig. WorkflowConfig.context_keys is valid root field. | STEP-3, STEP-4, STEP-5 |
| D-GR-42 | This D-GR compliance checklist is required in every plan. | ALL STEPS |

---


---

---

## Subfeature: DAG Loader & Runner (dag-loader-runner)

### SF-2: DAG Loader & Runner

<!-- SF: dag-loader-runner -->




## D-GR Compliance Checklist

| Decision | Status | Notes |
|----------|--------|-------|
| D-GR-2 (ExecutionResult with history) | ✅ Compliant | `ExecutionResult.history: ExecutionHistory` with `phase_metrics`, `map_fan_out`, `fold_progress`, `loop_progress`, `error_routes` |
| D-GR-4 (error bubbling-only-when-unhandled) | ✅ Compliant | STEP-18 implements node-level error-port routing, phase-level fail-fast + bubble, mode-specific behavior |
| D-GR-5 (AST-validated exec) | ✅ Compliant | STEP-10 `sandbox.py`: `ASTAllowlistVisitor`, `SAFE_BUILTINS`, blocked patterns rejected at load time |
| D-GR-12 (BranchNode gather+fan-out) | ✅ Compliant | Per-port `BranchOutputPort.condition` evaluation, non-exclusive fan-out, optional `merge_function` for gather |
| D-GR-13 (ErrorNode 4th type) | ✅ Compliant | `execute_error_node` in STEP-16, Jinja2 message rendering, terminal (no outputs), error bubbles per D-GR-4 |
| D-GR-14 (ArtifactPlugin, no auto-write) | ✅ Compliant | `artifact_key` removed from NodeBase. Built-in `ArtifactPlugin` in `plugins/artifact_write.py`. No auto-write in runner. |
| D-GR-17 (10,000 char expression limit) | ✅ Compliant | `MAX_EXPRESSION_LENGTH = 10_000` in `sandbox.py`, enforced in both `validate()` and `evaluate_expression()` |
| D-GR-19 (file_first_resolve built-in) | ✅ Compliant | `plugins/file_first_resolve.py` built-in plugin registered in `PluginRegistry` |
| D-GR-22 (nested YAML, edge hooks, /api/schema) | ✅ Compliant | Loader parses `phases[].nodes` + `phases[].children`. Hook-vs-data inferred from source port container. No `port_type`. |
| D-GR-23 (invoke unchanged, ContextVar, merge order) | ✅ Compliant | `AgentRuntime.invoke()` unchanged. `_current_node_var: ContextVar[str | None]`. Merge: workflow→phase→actor→node. |
| D-GR-24 (no core checkpoint/resume) | ✅ Compliant | No CheckpointStore, no resume logic in `run()`. `ExecutionHistory` for observability only. |
| D-GR-30 (agent\|human, closed root, reject stores/plugin_instances/interaction) | ✅ Compliant | `validate()` rejects all 14 stale fields per REQ-60. Actor hydration rejects `interaction`. |
| D-GR-35 (per-port BranchNode) | ✅ Compliant | `outputs: dict[str, BranchOutputPort]`, non-exclusive fan-out, `merge_function` valid, `switch_function` rejected |
| D-GR-36 (ErrorNode as 4th atomic type) | ✅ Compliant | `execute_error_node` in STEP-15, Jinja2 message rendering, terminal (no outputs), error bubbles per D-GR-4. ErrorNode entity: `id`, `type: error`, `message` (Jinja2), `inputs` (dict), NO outputs, NO hooks. |
| D-GR-38 (AST allowlist, not bare exec) | ✅ Compliant | `ExpressionSandbox` class with AST visitor, 5s timeout via `asyncio.wait_for`, 10k char limit |
| D-GR-41 (edge contracts rewritten) | ✅ Compliant | Interfaces section uses correct module paths, signatures, field names |
| D-GR-42 (D-GR canonical, checklist present) | ✅ Compliant | This checklist |

## Architecture Overview

SF-2 adds an `iriai_compose/declarative/` subpackage providing a YAML-first workflow execution path alongside the existing imperative Python API. It consumes SF-1's Pydantic models directly (no schema snapshots), validates against the canonical SF-1 PRD wire contract, builds recursive DAGs from nested phases, and executes against provided `AgentRuntime` instances. The runner is additive — it must not break `DefaultWorkflowRunner`, `WorkflowRunner.parallel()`, current storage ABCs, or existing imperative workflows (REQ-63).

### Module Layout

```
iriai_compose/
├── declarative/
│   ├── __init__.py          # Public API: run, validate, load_workflow, RuntimeConfig, etc.
│   ├── loader.py            # Thin wrapper: imports SF-1's load_workflow + runtime validation
│   ├── validation.py        # validate() standalone + stale-field rejection (REQ-58, REQ-60)
│   ├── runner.py            # Top-level run() + workflow-level DAG orchestration
│   ├── graph.py             # DAG construction, topological sort, reachability
│   ├── executors.py         # Node executors: ask, branch, plugin, error
│   ├── modes.py             # Phase mode strategies: sequential, map, fold, loop
│   ├── sandbox.py           # AST-validated expression evaluation (D-GR-5/D-GR-38)
│   ├── context.py           # HierarchicalContext lifecycle (D-GR-23)
│   ├── plugins.py           # Plugin ABC, PluginRegistry, CategoryExecutor
│   ├── config.py            # RuntimeConfig dataclass
│   ├── actors.py            # ActorDefinition hydration (agent|human only)
│   ├── hooks.py             # Hook edge execution (fire-and-forget)
│   ├── cost.py              # CostSummary, NodeCost, budget enforcement
│   └── errors.py            # All error types
├── plugins/                 # Built-in plugin implementations
│   ├── __init__.py          # register_builtins()
│   ├── artifact_write.py    # ArtifactPlugin (D-GR-14)
│   └── file_first_resolve.py # file_first_resolve (D-GR-19)
```

### Key Architectural Principles

**Uniform DAG Execution:** A single `ExecutionGraph` type and `_execute_dag` function operate at every level. Workflow-level elements = phases. Phase-level elements = nodes + sub-phases. Element discrimination: `hasattr(element, 'mode')` → phase; `element.type` → node. No separate `WorkflowGraph` type.

**Expression Sandbox (D-GR-5/D-GR-38):** All expressions evaluated through `ExpressionSandbox` with: AST allowlist visitor rejecting `import`, `__import__`, `__class__`, `__bases__`, `__subclasses__`, `eval()`, `exec()`, `compile()`, `Lambda`; SAFE_BUILTINS whitelist; 10,000-character limit (D-GR-17); 5-second timeout; restricted `__builtins__`. Validation at load time, enforcement at runtime.

**HierarchicalContext (D-GR-23):** Context merge order: `workflow → phase → actor → node`. Node identity propagated via `_current_node_var: ContextVar[str | None]`. `AgentRuntime.invoke()` unchanged — no breaking ABI change.

**No Core Checkpoint/Resume (D-GR-24):** Runner does not own checkpoint writes or resume logic. `ExecutionHistory` provides observability (phase metrics, node trace, error routes). Consuming apps model resumability explicitly with plugin nodes.

**Error Propagation (D-GR-4):** Bubbling-only-when-unhandled. Node error → check `error` output port edge → handled (phase continues) or unhandled (bubble to phase). Phase fail-fast → check phase error port → bubble to parent. ErrorNode (D-GR-13) is a terminal node that deliberately raises structured errors.

**Stale-Field Rejection (REQ-60):** `validate()` rejects 14 stale contract variants with actionable `ValidationError` objects. `run()` calls `validate()` first — never executes documents that `validate()` would reject (REQ-58).

### Public API

```python
from iriai_compose.declarative import (
    run,                    # async def run(workflow, config: RuntimeConfig, *, inputs=None) -> ExecutionResult
    validate,               # def validate(workflow) -> list[ValidationError]
    load_workflow,          # Re-exported from SF-1's iriai_compose.schema.yaml_io
    RuntimeConfig,          # Dataclass: all runtime wiring
    PluginRegistry,         # Plugin name→implementation registry
    ExecutionResult,        # Execution outcome + history
    ExecutionHistory,       # Phase metrics, node trace, error routes
    ValidationError,        # Structured validation error
)
```

**`validate(workflow: WorkflowConfig | str | Path) -> list[ValidationError]`** — Structural validation without live runtimes. Checks schema conformance, stale-field rejection (14 items), type flow, expression AST validation, cycle detection, reference integrity, hook edge constraints.

**`run(workflow: WorkflowConfig | str | Path, config: RuntimeConfig, *, inputs: dict[str, Any] | None = None) -> ExecutionResult`** — Load, validate, hydrate, execute. Calls `validate()` first; rejects if any errors. Returns `ExecutionResult` with full `ExecutionHistory`.

## Schema Entity Reference (SF-1 PRD Canonical — D-GR-30/D-GR-42)

All types defined by SF-1 (`iriai_compose/schema/`). The runner MUST NOT assume fields beyond this reference. Any field not listed here that appears in a workflow document must be rejected by `validate()` per REQ-60.

### Node Type Hierarchy

```
NodeBase (abstract)
├── AskNode    (type="ask")    — agent invocation, prompt + actor_ref
├── BranchNode (type="branch") — gather + non-exclusive fan-out (D-GR-35)
├── PluginNode (type="plugin") — side effects via plugin execution
└── ErrorNode  (type="error")  — terminal structured error (D-GR-13)

NodeDefinition = Annotated[AskNode | BranchNode | PluginNode | ErrorNode, Field(discriminator="type")]
```

### NodeBase (Abstract)

All four node types inherit these fields.

| Field | Type | Default | Runner Use |
|-------|------|---------|------------|
| `id` | `str` | required | Element ID in `ExecutionGraph.elements` |
| `type` | `Literal["ask","branch","plugin","error"]` | required | Discriminator |
| `summary` | `str \| None` | `None` | UI only |
| `context_keys` | `list[str]` | `[]` | Resolved via HierarchicalContext at node scope |
| `inputs` | `list[PortDefinition]` | `[PortDefinition(name="input")]` | Port model for edge resolution |
| `outputs` | `list[PortDefinition]` | `[PortDefinition(name="output")]` | Port model for edge resolution |
| `hooks` | `list[PortDefinition]` | `[PortDefinition(name="on_start"), PortDefinition(name="on_end")]` | Hook edge identification |
| `cost` | `NodeCostConfig \| None` | `None` | Budget enforcement |
| `metadata` | `dict` | `{}` | Pass-through |
| `position` | `dict[str, float] \| None` | `None` | UI only |

**Removed from NodeBase (D-GR-14, D-GR-30):** `artifact_key` (writes are explicit via ArtifactPlugin), `context_text`, `input_type`, `input_schema`, `output_type`, `output_schema` (use `PortDefinition.type_ref`/`schema_def` instead).

### AskNode (type="ask")

| Field | Type | Default | Runner Use |
|-------|------|---------|------------|
| `actor_ref` | `str` | required | Key into `workflow.actors` → hydrated actor |
| `prompt` | `str` | required | Jinja2 template with `{{ $input }}`, `{{ ctx.key }}` |

**Constraints:** `inputs` always `[PortDefinition(name="input")]` (SF-1 validator enforces). `outputs` 1+ ports. `actor_ref` validated against `workflow.actors`.

### BranchNode (type="branch") [D-GR-12, D-GR-35]

| Field | Type | Default | Runner Use |
|-------|------|---------|------------|
| `inputs` | `list[PortDefinition]` | (overrides NodeBase) | 1+ typed input ports for gather |
| `outputs` | `dict[str, BranchOutputPort]` | required | Per-port conditions, non-exclusive fan-out |
| `merge_function` | `str \| None` | `None` | Expression combining multiple gathered inputs before condition eval |

**Per-port execution model (D-GR-35):**
1. **Gather:** If multiple inputs, wait for all (async barrier). If `merge_function` set, evaluate to combine `inputs: dict[str, Any]` → single merged value. If single input, pass through.
2. **Fan-out:** Evaluate each `BranchOutputPort.condition` independently with `data` = merged input. All truthy ports fire simultaneously (non-exclusive). If no conditions are truthy, no ports fire (warning logged).

**Permanently rejected fields:** `switch_function`, `output_field`, `condition_type`, `condition` (top-level), `paths`. Validator produces `stale_branch_field` error.

**Degenerate cases:** 1 input + 1 output = passthrough. N inputs + 1 output = pure gather/merge. 1 input + N outputs = pure dispatch.

### BranchOutputPort [D-GR-35]

| Field | Type | Default | Runner Use |
|-------|------|---------|------------|
| `condition` | `str` | required | Expression evaluated with `data` = merged input → `bool` |
| `type_ref` | `str \| None` | `None` | XOR with `schema_def` (REQ-38) |
| `schema_def` | `dict \| None` | `None` | XOR with `type_ref` (REQ-38) |
| `description` | `str \| None` | `None` | UI only |

### ErrorNode (type="error") [D-GR-13]

| Field | Type | Default | Runner Use |
|-------|------|---------|------------|
| `message` | `str` | required | Jinja2 template rendered with input data |
| `error_code` | `str \| None` | `None` | Structured error identification |

**Constraints:** `inputs` 1+ ports (inherits NodeBase default). `outputs` MUST be `[]` (terminal — no outgoing data). Reaching an ErrorNode raises a `WorkflowError` that bubbles per D-GR-4.

### PluginNode (type="plugin")

| Field | Type | Default | Runner Use |
|-------|------|---------|------------|
| `plugin_ref` | `str` | required | Key into `workflow.plugins` or concrete registry name |
| `config` | `dict \| None` | `None` | Plugin-specific config |

**Constraints:** `inputs` always `[PortDefinition(name="input")]` (SF-1 enforces). `outputs` 0+ ports (`outputs: []` valid for fire-and-forget). No `instance_ref` (D-GR-30: no root `plugin_instances`).

### PortDefinition

| Field | Type | Default | Runner Use |
|-------|------|---------|------------|
| `name` | `str` | required | Edge resolution (`"node_id.port_name"`) |
| `type_ref` | `str \| None` | `None` | XOR with `schema_def` (REQ-38) |
| `schema_def` | `dict \| None` | `None` | XOR with `type_ref` (REQ-38) |
| `description` | `str \| None` | `None` | UI only |

**No `condition` field.** Conditions exist only on `BranchOutputPort`. YAML dict shorthand (`name: "type"`) expanded by loader (D-GR-16).

### EdgeDefinition

| Field | Type | Default | Runner Use |
|-------|------|---------|------------|
| `source` | `str` | required | `"node_id.port_name"` or `"$input.port_name"` |
| `target` | `str` | required | `"node_id.port_name"` or `"$output.port_name"` |
| `transform_fn` | `str \| None` | `None` | Expression: `data` → transformed. Must be `None` for hook edges. |
| `description` | `str \| None` | `None` | -- |

**Hook vs data edge:** Determined at graph-build time by checking whether the source port lives in the `hooks` list of the source element. No `port_type` field — rejected if present.

### PhaseDefinition [D-GR-22]

| Field | Type | Default | Runner Use |
|-------|------|---------|------------|
| `id` | `str` | required | Element ID |
| `name` | `str \| None` | `None` | Logging/tracking |
| `mode` | `Literal["sequential","map","fold","loop"]` | required | Mode executor dispatch |
| `mode_config` | `ModeConfig` | required | Discriminated union per mode |
| `nodes` | `list[NodeDefinition]` | `[]` | Internal node elements |
| `children` | `list[PhaseDefinition]` | `[]` | Nested sub-phases (recursive) |
| `edges` | `list[EdgeDefinition]` | `[]` | Phase-local edges (data + hook) |
| `inputs` | `list[PortDefinition]` | `[PortDefinition(name="input")]` | Phase I/O boundary |
| `outputs` | `list[PortDefinition]` | `[PortDefinition(name="output")]` | Phase I/O boundary |
| `hooks` | `list[PortDefinition]` | `[on_start, on_end]` | Hook edge identification |
| `context_keys` | `list[str]` | `[]` | Phase-scoped context |
| `cost` | `PhaseCostConfig \| None` | `None` | Budget enforcement |
| `metadata` | `dict` | `{}` | Pass-through |
| `summary` | `str \| None` | `None` | UI only |
| `position` | `dict[str, float] \| None` | `None` | UI only |

**Key changes from stale plan:** `children` (not `phases`). Single `mode_config` discriminated union (not 4 separate fields). No `context_text`. No `artifact_key`. No `fresh_sessions`. Loop auto-ports (`condition_met` + `max_exceeded`) created by SF-1 validator on `outputs`.

### Mode Configs (Discriminated Union)

**SequentialModeConfig:** No fields.

**MapModeConfig:** `collection` (str, expression → iterable), `max_parallelism` (int|None).

**FoldModeConfig:** `collection` (str), `accumulator_init` (str, no-variable expression), `reducer` (str, receives `accumulator` + `result`).

**LoopModeConfig:** `condition` (str, receives `data` = iteration output, True exits), `max_iterations` (int|None).

**Removed:** `exit_condition` (renamed to `condition`), `fresh_sessions` (removed from all mode configs).

### WorkflowConfig [D-GR-30]

| Field | Type | Default | Runner Use |
|-------|------|---------|------------|
| `schema_version` | `str` | `"1.0"` | Version check |
| `workflow_version` | `int` | `1` | Content version (REQ-37) |
| `name` | `str` | required | Logging/tracking |
| `description` | `str \| None` | `None` | -- |
| `metadata` | `dict` | `{}` | Pass-through |
| `actors` | `dict[str, ActorDefinition]` | `{}` | Actor hydration |
| `phases` | `list[PhaseDefinition]` | `[]` | Workflow-level DAG elements |
| `edges` | `list[EdgeDefinition]` | `[]` | Cross-phase edges only |
| `templates` | `dict[str, TemplateDefinition]` | `{}` | Template definitions |
| `plugins` | `dict[str, PluginInterface]` | `{}` | Plugin type definitions |
| `types` | `dict[str, TypeDefinition]` | `{}` | Type definitions |
| `cost_config` | `WorkflowCostConfig \| None` | `None` | Budget |
| `context_keys` | `list[str]` | `[]` | Workflow-scoped context (D-GR-41) |

**Closed root set.** `validate()` rejects any field not in this list. Specifically rejected: `stores`, `plugin_instances`, `context_text`, `inputs`, `outputs`.

### ActorDefinition [D-GR-30]

Discriminated union on `actor_type`:

**AgentActorDef** (`actor_type: "agent"`): `provider` (str|None), `model` (str|None), `role` (RoleDefinition), `persistent` (bool, default True), `context_keys` (list[str]).

**HumanActorDef** (`actor_type: "human"`): `identity` (str|None), `channel` (str|None).

**Rejected:** `type: "interaction"` — validator produces `invalid_actor_type` error.

### Supporting Types

**RoleDefinition:** `name`, `prompt`, `tools` (list[str]), `model` (str|None), `effort` (Literal|None), `metadata` (dict).

**TypeDefinition:** `name`, `schema_def` (dict, JSON Schema), `description`.

**PluginInterface:** `id`, `name`, `description`, `inputs`/`outputs` (list[PortDefinition]), `config_schema` (dict|None), `category` (str|None: "service"|"mcp"|"cli"|"plugin").

**TemplateDefinition:** `id`, `name`, `description`, `inputs` (list[PortDefinition]), `outputs` (list[PortDefinition]), `actor_slots`, `mode`, `nodes`, `children`, `edges`.

**WorkflowCostConfig / PhaseCostConfig / NodeCostConfig:** `max_tokens` (int|None), `max_usd` (float|None). Split types per SF-1 PRD.

**HookPortEvent:** Lifecycle event payload delivered through ordinary edges.

### Expression Evaluation Contexts

| Expression | Location | Variables | Returns |
|------------|----------|-----------|---------|
| `BranchOutputPort.condition` | BranchNode output ports | `data` = merged input | `bool` |
| `BranchNode.merge_function` | BranchNode | `inputs: dict[str, Any]` | merged value |
| `EdgeDefinition.transform_fn` | Data edges only | `data` = source port output | transformed value |
| `LoopModeConfig.condition` | Loop phase | `data` = iteration `$output` | `bool` (True exits) |
| `MapModeConfig.collection` | Map phase | `ctx` = context + phase input | `Iterable` |
| `FoldModeConfig.collection` | Fold phase | `ctx` = context + phase input | `Iterable` |
| `FoldModeConfig.accumulator_init` | Fold phase | **(no variables)** | `Any` |
| `FoldModeConfig.reducer` | Fold phase | `accumulator`, `result` | `Any` |

**Removed:** `switch_function` (D-GR-35 — permanently rejected).

### Stale-Field Rejection List (REQ-60 — 14 Items)

`validate()` MUST reject all of the following with `ValidationError(code=...)` and actionable guidance:

| # | Field/Pattern | Error Code | Guidance |
|---|--------------|------------|----------|
| 1 | `stores` at WorkflowConfig root | `unsupported_root_field` | Remove `stores` — not in closed root set |
| 2 | `plugin_instances` at root | `unsupported_root_field` | Remove — plugins declared in `workflow.plugins` |
| 3 | Top-level `nodes` outside phases | `unsupported_root_field` | Move nodes into `phases[].nodes` |
| 4 | `actor_type: "interaction"` | `invalid_actor_type` | Use `actor_type: "human"` |
| 5 | Missing typed hook ports (on_start/on_end) | `missing_hook_ports` | Add `on_start`/`on_end` to hooks |
| 6 | `switch_function` on BranchNode | `stale_branch_field` | Use per-port conditions on `outputs` |
| 7 | `condition_type` on BranchNode | `stale_branch_field` | Use per-port `BranchOutputPort.condition` |
| 8 | `condition` (top-level) on BranchNode | `stale_branch_field` | Use per-port conditions |
| 9 | `paths` on BranchNode | `stale_branch_field` | Use `outputs: dict[str, BranchOutputPort]` |
| 10 | `output_field` on BranchNode | `stale_branch_field` | Removed — use expression conditions |
| 11 | Unknown branch output port in edges | `unknown_branch_port` | Edge target port must exist in `BranchNode.outputs` |
| 12 | `port_type` on EdgeDefinition | `unsupported_edge_field` | Remove — hook-vs-data inferred from source port |
| 13 | Separate `hooks` section (non-port) | `invalid_hook_section` | Wire hooks as ordinary edges from `on_start`/`on_end` ports |
| 14 | Hook edge with `transform_fn` set | `hook_edge_transform` | Remove `transform_fn` from hook edges |

**`merge_function` is valid and MUST NOT be rejected.**

## Built-in Plugins

SF-2 provides two built-in plugins, auto-registered in the `PluginRegistry` at construction time.

### `artifact_write` — ArtifactPlugin (D-GR-14)

**Purpose:** Explicit artifact persistence. Per D-GR-14, artifact writes are explicit Plugin behavior — there is no auto-write on node completion. Workflows that need to persist node output to the artifact store must wire an `artifact_write` PluginNode.

```python
class ArtifactPlugin(Plugin):
    """Writes input data to the artifact store at a configured key.
    Returns input data unchanged (pass-through for downstream edges).

    Config:
        key (str, required): The artifact store key to write to.
    """
    async def execute(self, input_data: Any, *, context: ExecutionContext) -> Any:
        key = context.config["key"]
        await context.artifacts.put(key, input_data, feature=context.feature)
        return input_data
```

**YAML usage:**
```yaml
nodes:
  - id: pm_task
    type: ask
    actor_ref: pm
    prompt: "Write the PRD for this feature"
  - id: save_prd
    type: plugin
    plugin_ref: artifact_write
    config:
      key: artifacts.prd
edges:
  - source: pm_task.output
    target: save_prd.input
```

### `file_first_resolve` — Resume-Safe Artifact Caching (D-GR-19)

**Purpose:** Checks the artifact store for an existing value before passing data through. Enables resume-safety at the workflow level without core checkpoint/resume (D-GR-24). Wired before expensive Ask nodes.

```python
class FileFirstResolvePlugin(Plugin):
    """Checks artifact store for cached result. Returns cached value if present,
    otherwise passes through the fallback input unchanged.

    Config:
        artifact_key (str, required): Store key to check.
    """
    async def execute(self, input_data: Any, *, context: ExecutionContext) -> Any:
        key = context.config["artifact_key"]
        cached = await context.artifacts.get(key, feature=context.feature)
        if cached is not None:
            return cached
        return input_data
```

**YAML usage:**
```yaml
nodes:
  - id: check_cache
    type: plugin
    plugin_ref: file_first_resolve
    config:
      artifact_key: artifacts.prd
  - id: pm_task
    type: ask
    actor_ref: pm
    prompt: "Write the PRD"
  - id: save_result
    type: plugin
    plugin_ref: artifact_write
    config:
      key: artifacts.prd
edges:
  - source: $input.input
    target: check_cache.input
  - source: check_cache.output
    target: pm_task.input
  - source: pm_task.output
    target: save_result.input
```

## Data Flow Architecture

Data moves through a declarative workflow via three layers.

### Layer 1: Element-to-Element (Edges)

Within a phase, elements (nodes + sub-phases) pass data through edges. The `ExecutionGraph` treats both uniformly.

1. Each element produces a return value stored in `element_outputs[element_id]`.
2. Edges route values between elements. `_gather_inputs()` collects upstream values, applying source port resolution and edge transforms.
3. Collected input arrives as `data` for nodes or `phase_input` for sub-phases.

Same engine at workflow level (elements = phases).

### Layer 2: Context Channel (HierarchicalContext — D-GR-23)

Nodes receive context via `HierarchicalContext` with merge order: **workflow → phase → actor → node**.

```python
class HierarchicalContext:
    """Manages layered context resolution."""
    def __init__(self, context_provider: ContextProvider, feature: Feature):
        self._provider = context_provider
        self._feature = feature
        self._scopes: list[list[str]] = []  # stack of context_keys per scope

    def push_scope(self, context_keys: list[str]) -> None: ...
    def pop_scope(self) -> None: ...

    async def resolve(self) -> str:
        """Resolve all scoped context_keys, deduplicated in merge order."""
        all_keys = list(dict.fromkeys(
            key for scope in self._scopes for key in scope
        ))
        return await self._provider.resolve(all_keys, feature=self._feature)
```

**Scope lifecycle:**
1. Workflow start: `push_scope(workflow.context_keys)`
2. Phase entry: `push_scope(phase.context_keys)`
3. Node execution: `push_scope(node.context_keys)` + `push_scope(actor.context_keys)`
4. After node: pop node + actor scopes
5. After phase: pop phase scope

**Node identity propagation:** `_current_node_var: ContextVar[str | None]` set before each node execution. `AgentRuntime.invoke()` is NOT modified (D-GR-23). SF-3 MockAgentRuntime reads `_current_node_var` to route mock responses.

### Layer 3: Phase Mode Input Injection

| Mode | `$input` receives | `$output` produces |
|------|-------------------|--------------------|  
| Sequential | `phase_input` | Phase output |
| Map | Current collection item | List of all item outputs |
| Fold | `{"item": item, "accumulator": acc}` | Fed to `reducer`; final accumulator |
| Loop | First: `phase_input`; subsequent: previous `$output` | Evaluated by `condition`; True → `("condition_met", output)` |

**Expression contexts:** `collection` receives `ctx` (resolved context + phase input), NOT `data`. `accumulator_init` receives NO variables. `reducer` receives `accumulator` + `result`. `condition` receives `data` = iteration output.

### Phase Port Routing: `$input` and `$output`

Phases enforce strict I/O boundaries via `$input`/`$output` pseudo-nodes.

**`$input` priority:** (1) Explicit `$input` edge → named port from `phase_input`. (2) Fired upstream edges. (3) Entry element with no edge → `phase_input` directly.

**`$output`:** (1) Single `$output` edge → value with source port resolution. (2) Multiple `$output` edges → `{port_name: data}` dict. (3) No `$output` edges → last exit element's output.

### Source Port Resolution

```python
def _resolve_source_port(data: Any, source_port: str) -> Any:
    if source_port in ("output", "default"):
        return data
    if isinstance(data, dict) and source_port in data:
        return data[source_port]
    return data
```

### Cross-Boundary Connections

All node↔phase edge combinations work because `build_execution_graph` includes both `phase.nodes` and `phase.children` as elements, `_gather_inputs` uses source port resolution uniformly, and `_dispatch_element` checks `mode` vs `type`.

### No-Edges Fallback

When `edges` is empty, `run()` and `execute_phase` implement sequential fallback OUTSIDE `_execute_dag` — threading each element's output as the next element's input.

## Branch Node Execution Model (D-GR-35)

BranchNode is the only node type with user-configurable input ports and per-port output conditions.

### Port Routing Contrast

| Node Type | Input Ports | Output Routing |
|-----------|-------------|----------------|
| AskNode | 1 fixed (`input`) | All outgoing edges fire |
| PluginNode | 1 fixed (`input`) | All outgoing edges fire (`outputs: []` = fire-and-forget) |
| ErrorNode | 1+ (`input`) | No outputs (terminal) |
| BranchNode | 1+ user-defined | **Non-exclusive per-port conditions** — all truthy ports fire simultaneously |

### Gather Barrier

`_branch_inputs_ready` defers execution until all connected input ports have fired. Returns True for all non-branch elements (no barrier needed). Deferred queue with 2× safety cap for deadlock detection.

### Merge + Evaluate Conditions

```python
async def execute_branch_node(
    node: BranchNode, data: Any, *, sandbox: ExpressionSandbox
) -> tuple[dict[str, bool], Any]:
    # Step 1: Merge multi-input
    if node.merge_function and isinstance(data, dict) and len(data) > 1:
        merged = sandbox.evaluate(node.merge_function, inputs=data)
    elif isinstance(data, dict) and len(data) == 1:
        merged = next(iter(data.values()))
    else:
        merged = data

    # Step 2: Evaluate per-port conditions (non-exclusive)
    port_fires: dict[str, bool] = {}
    for port_name, branch_port in node.outputs.items():
        port_fires[port_name] = sandbox.evaluate_predicate(
            branch_port.condition, data=merged
        )

    return port_fires, merged
```

**No switch_function.** The exclusive routing model is permanently rejected per D-GR-35. All routing is through per-port `BranchOutputPort.condition` expressions.

### `_activate_outgoing_edges`

Three dispatch models:
1. **BranchNode** (`type=="branch"` + `port_fires` dict): Non-exclusive. All truthy ports' edges fire.
2. **Loop exit** (`_is_loop_exit`): `edge_matches_exit_path` selects edges.
3. **Default** (Ask, Plugin, phases): All outgoing edges from all output ports fire.

### Error Port Routing

When any node execution raises an exception, `_execute_dag` checks if the node has an output port named `error` with outgoing edges. If yes, error data is routed to handler nodes, and the phase continues. If no, the error bubbles to the containing phase per D-GR-4. See Error Propagation Model section.

## Error Propagation Model (D-GR-4, D-GR-13)

Error propagation uses a **bubbling-only-when-unhandled** model.

### Node-Level Error Routing

1. Node execution raises an exception.
2. Runner checks: does this node have an output port named `error` with outgoing edges?
3. **Handled:** Error data (structured `ErrorInfo` with message, traceback, node_id) routed through `error` port edges to handler node(s). Phase continues normally — the handler's output is treated as if the failed node completed.
4. **Unhandled:** No `error` port or no outgoing edges → error bubbles to containing phase.

### Phase-Level Error Routing

1. Phase receives unhandled node error → **fail-fast** (cancel/stop remaining nodes).
2. Runner checks: does this phase have an output port named `error` with outgoing edges?
3. **Handled:** Error data routed through phase's `error` port. Workflow continues.
4. **Unhandled:** Bubbles to parent phase. At workflow level, sets `ExecutionResult.success = False`.

### Mode-Specific Behavior

| Mode | Handled Error | Unhandled Error |
|------|--------------|----------------|
| Sequential | Phase continues to next node | Phase fail-fast, bubble |
| Map | Other branches unaffected | Phase fail-fast, cancel siblings |
| Fold | Fold continues to next item | Fold stops, partial accumulator preserved |
| Loop | Loop continues next iteration | Loop stops, `error` port is 3rd exit path |

### ErrorNode Execution (D-GR-13)

When execution reaches an ErrorNode:
1. Render `message` Jinja2 template with input data.
2. Raise `WorkflowError(message=rendered, error_code=node.error_code, node_id=node.id)`.
3. Error bubbles per standard D-GR-4 propagation.

Use cases: validation dead-ends (`Branch → ErrorNode` on invalid path), multi-step error handling (`error` port → cleanup → `ErrorNode` to re-raise).

### Recording

Every error-port routing event recorded as `ErrorRoute(from_id, to_id, error, level='node'|'phase')` in `ExecutionHistory.error_routes`.

### ExecutionResult and ExecutionHistory (D-GR-2)

```python
@dataclass
class ExecutionResult:
    success: bool
    output: Any = None
    error: ExecutionError | None = None
    nodes_executed: list[tuple[str, str]] = field(default_factory=list)  # (node_id, node_type)
    branch_paths: dict[str, list[str]] = field(default_factory=dict)    # node_id → list of fired port names
    cost_summary: CostSummary | None = None
    duration_ms: float = 0.0
    history: ExecutionHistory = field(default_factory=ExecutionHistory)

@dataclass
class ExecutionHistory:
    phase_metrics: dict[str, PhaseMetrics] = field(default_factory=dict)  # keyed by phase id
    node_trace: list[NodeTraceEntry] = field(default_factory=list)
    error_routes: list[ErrorRoute] = field(default_factory=list)
    map_fan_out: dict[str, int] = field(default_factory=dict)       # phase_id → branch count
    fold_progress: dict[str, FoldProgress] = field(default_factory=dict)
    loop_progress: dict[str, LoopProgress] = field(default_factory=dict)

@dataclass
class ErrorRoute:
    from_id: str
    to_id: str
    error: str
    level: Literal["node", "phase"]
```

## Plugin System Architecture

The PluginRegistry supports two registration modes. The three-tier model from the stale plan (with instance_ref and plugin_instances) is removed per D-GR-30.

### Two-Tier Plugin Resolution

| Tier | Registration | Resolution | Dispatch |
|------|-------------|------------|----------|
| **Concrete** | `registry.register(name, plugin: Plugin)` | `registry.get(name)` → `Plugin` instance | Direct: `plugin.execute(input_data, context=ctx)` |
| **Category** | `registry.register_category_executor(cat, executor)` | Workflow-declared `PluginInterface` → category → executor | Category: `executor.execute(interface, config, input_data, context=ctx)` |

### Plugin Executor Resolution Flow

```python
async def execute_plugin_node(node, input_data, *, registry, workflow, context):
    # 1. Try concrete plugin (built-ins, app-registered)
    if registry.has(node.plugin_ref):
        plugin = registry.get(node.plugin_ref)
        return await plugin.execute(input_data, context=context)

    # 2. Try workflow-declared type → category dispatch
    if node.plugin_ref in workflow.plugins:
        interface = workflow.plugins[node.plugin_ref]
        category = interface.category or "plugin"
        executor = registry.get_category_executor(category)
        if not executor:
            raise PluginNotFoundError(f"No category executor for '{category}'")
        config = node.config or {}
        return await executor.execute(interface, config, input_data, context=context)

    raise PluginNotFoundError(node.plugin_ref)
```

### CategoryExecutor ABC

```python
class CategoryExecutor(ABC):
    @abstractmethod
    async def execute(
        self, interface: PluginInterface, config: dict[str, Any],
        input_data: Any, *, context: ExecutionContext,
    ) -> Any: ...
```

SF-2 provides no built-in category executors. Consuming projects (iriai-build-v2) register MCP, CLI, service executors.

### ExecutionContext

```python
@dataclass
class ExecutionContext:
    config: dict[str, Any]             # Plugin-specific config from node
    artifacts: ArtifactStore
    sessions: SessionStore
    context_provider: ContextProvider
    feature: Feature
    workspace: Workspace | None
    runner: Any                         # Reference for nested DAGs (D-SF2-34)
    services: dict[str, Any]
```

## Resume and Checkpoint (Out of Scope — D-GR-24)

The core declarative runtime does NOT own checkpoint or resume. `ExecutionHistory` and phase metrics provide observability. Consuming apps that need resumability must model it explicitly:

- **`file_first_resolve` plugin (D-GR-19):** Checks artifact store for cached results before expensive operations.
- **`artifact_write` plugin (D-GR-14):** Explicitly persists results to artifact store.
- **App-level orchestration:** iriai-build-v2 can implement resume by checking artifact store keys before invoking `run()`.

## Post-Event Callbacks → Hook Edges

iriai-build-v2 `post_update`/`post_compile` callbacks map to hook edges: `node.on_end → plugin_node.input` (fire-and-forget per D-GR-22). Hook targets are commonly PluginNodes performing side effects. Hook edges are ordinary `EdgeDefinition` entries whose source resolves to a hook port (`on_start` or `on_end`). `transform_fn` must be `None` on hook edges — validated by `validate()` (REQ-60 item 14).

## Nested Dynamic DAGs → Plugin Nodes

iriai-build-v2's `_implement_dag` maps to a Plugin node with runner access via `ExecutionContext`. Static outer workflow, data-driven inner execution per D-SF2-34.

---

## Store Abstractions

Stable, no modifications. `ArtifactStore`, `SessionStore`, `ContextProvider` ABCs in `iriai_compose/storage.py`.

**Citation:** [code: iriai_compose/storage.py:13-24] — `ArtifactStore` with `get`, `put`, `delete` methods scoped by `Feature`.
**Citation:** [code: iriai_compose/storage.py:44-49] — `ContextProvider` with `resolve(keys, feature)` → prompt-ready string.
**Citation:** [code: iriai_compose/storage.py:80-101] — `DefaultContextProvider` resolves keys from `static_files` first, then `artifacts.get()`.

---

## Decision Log

| ID | Decision | Choice | Rationale |
|----|----------|--------|-----------|  
| D-SF2-1 | Module placement | `iriai_compose/declarative/` | Clean separation from imperative API |
| D-SF2-2 | Runner architecture | Standalone `run()` + `validate()` | REQ-58: separate structural and runtime validation |
| D-SF2-3 | Multi-runtime routing | `RuntimeConfig.agent_runtimes: dict[str, AgentRuntime]` | Provider-keyed, per D-GR-2 |
| D-SF2-4 | Expression security | AST-validated exec() with sandbox | D-GR-5/D-GR-38: allowlist + 5s timeout + 10k limit |
| D-SF2-5 | YAML library | pyyaml via SF-1 | SF-1 owns parsing; SF-2 delegates |
| D-SF2-6 | Template rendering | Jinja2 SandboxedEnvironment | For AskNode.prompt and ErrorNode.message |
| D-SF2-7 | Branch routing | Per-port conditions only, non-exclusive | D-GR-35: switch_function rejected |
| D-SF2-8 | Artifact writes | Explicit via ArtifactPlugin | D-GR-14: no auto-write on NodeBase |
| D-SF2-9 | Context propagation | HierarchicalContext + ContextVar | D-GR-23: merge workflow→phase→actor→node |
| D-SF2-10 | Checkpoint/resume | Out of scope | D-GR-24: app-level concern |
| D-SF2-11 | Stale-field rejection | validate() rejects 14 items | REQ-60: comprehensive rejection list |
| D-SF2-12 | Plugin resolution | Two-tier: concrete + category | D-GR-30: no plugin_instances/instance_ref |
| D-SF2-13 | Error propagation | Bubbling-only-when-unhandled | D-GR-4: node error→port→handled or bubble |
| D-SF2-14 | ErrorNode | 4th atomic type, terminal | D-GR-13: raises structured error |
| D-SF2-15 | Actor hydration | agent\|human only | D-GR-30: interaction rejected |
| D-SF2-16 | Phase model | children (not phases), mode_config union | D-GR-22: nested containment |
| D-SF2-17 | Invoke ABI | Unchanged | D-GR-23: no breaking change |
| D-SF2-18 | ExecutionResult | With ExecutionHistory + phase_metrics | D-GR-2: map_fan_out on history |
| D-SF2-19 | Uniform DAG | Single `ExecutionGraph` + `_execute_dag` | Same engine at workflow and phase levels |
| D-SF2-20 | Loop exit | `condition_met` + `max_exceeded` ports | Read from SF-1 validated model |
| D-SF2-21 | Hook edges | Ordinary edges, port-container inference | D-GR-22: no port_type, no hook sections |
| D-SF2-22 | `run()` signature | `run(workflow, config, *, inputs=None)` | REQ-58 |
| D-SF2-23 | No-edges fallback | Sequential outside `_execute_dag` | Backward compat with simple workflows |
| D-SF2-24 | Source port resolution | `_resolve_source_port` at both levels | Unified |
| D-SF2-25 | Branch barrier | Deferred queue with 2× safety cap | Per D-GR-12 gather semantics |

## Implementation Steps

### STEP-9: Dependencies and Subpackage Skeleton

**Objective:** Add dependencies and create stubs for all modules including built-in plugins.

**Scope:**
| Path | Action |
|------|--------|
| `pyproject.toml` | modify |
| `iriai_compose/declarative/__init__.py` | create |
| `iriai_compose/declarative/loader.py` | create |
| `iriai_compose/declarative/validation.py` | create |
| `iriai_compose/declarative/runner.py` | create |
| `iriai_compose/declarative/graph.py` | create |
| `iriai_compose/declarative/executors.py` | create |
| `iriai_compose/declarative/modes.py` | create |
| `iriai_compose/declarative/sandbox.py` | create |
| `iriai_compose/declarative/context.py` | create |
| `iriai_compose/declarative/plugins.py` | create |
| `iriai_compose/declarative/config.py` | create |
| `iriai_compose/declarative/actors.py` | create |
| `iriai_compose/declarative/hooks.py` | create |
| `iriai_compose/declarative/cost.py` | create |
| `iriai_compose/declarative/errors.py` | create |
| `iriai_compose/plugins/__init__.py` | create |
| `iriai_compose/plugins/artifact_write.py` | create |
| `iriai_compose/plugins/file_first_resolve.py` | create |

**Instructions:**
- Add `pyyaml>=6.0,<7.0` and `jinja2>=3.1,<4.0` to `dependencies` in `pyproject.toml` (NOT optional).
- Create all stub files with docstrings. All stubs importable but raise `NotImplementedError`.
- `iriai_compose/plugins/__init__.py` exports `register_builtins(registry)` registering both `ArtifactPlugin` and `FileFirstResolvePlugin`.

**Acceptance Criteria:**
- `pip install -e .` succeeds
- `from iriai_compose.declarative import run, validate` works (stubs)
- `from iriai_compose.plugins.artifact_write import ArtifactPlugin` works
- `from iriai_compose.plugins.file_first_resolve import FileFirstResolvePlugin` works
- Existing tests pass unchanged

**Counterexamples:**
- Do NOT add pyyaml/jinja2 as optional dependencies
- Do NOT import SF-1 models yet (they may not exist)
- Do NOT implement any logic — stubs only

**Requirement IDs:** REQ-63 | **Journey IDs:** J-13

---

### STEP-10: Expression Sandbox (D-GR-5/D-GR-38)

**Objective:** AST-validated expression evaluation with allowlist visitor, blocked builtins, 10,000-char limit, and 5-second timeout. This is the security foundation for all expression execution.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/sandbox.py` | modify |
| `iriai_compose/declarative/errors.py` | modify |

**Instructions:**

**Constants (importable by SF-1 for schema validation):**
```python
MAX_EXPRESSION_LENGTH = 10_000  # D-GR-17
MAX_AST_NODES = 200
EXPRESSION_TIMEOUT_SECONDS = 5.0
SAFE_BUILTINS = {
    'None': None, 'True': True, 'False': False,
    'len': len, 'range': range, 'list': list, 'dict': dict, 'set': set,
    'tuple': tuple, 'str': str, 'int': int, 'float': float, 'bool': bool,
    'isinstance': isinstance, 'sorted': sorted, 'reversed': reversed,
    'enumerate': enumerate, 'zip': zip, 'map': map, 'filter': filter,
    'any': any, 'all': all, 'min': min, 'max': max, 'sum': sum,
    'abs': abs, 'round': round, 'hasattr': hasattr, 'getattr': getattr,
}
```

**`ASTAllowlistVisitor(ast.NodeVisitor)`:**
- Reject `Import`, `ImportFrom` nodes
- Reject `Call` nodes where func is `eval`, `exec`, `compile`, `__import__`, `type`, `open`, `globals`, `locals`, `vars`, `dir`, `delattr`, `setattr`
- Reject `Attribute` access to `__class__`, `__bases__`, `__subclasses__`, `__mro__`, `__globals__`, `__code__`, `__builtins__`
- Reject `Lambda` nodes
- Count all nodes; raise if > `MAX_AST_NODES`
- Raise `ExpressionSecurityError(expression, violation_description)` on any rejection

**`validate_expression(expr: str) -> None`:**
1. Check `len(expr) > MAX_EXPRESSION_LENGTH` → raise `ExpressionSizeError`
2. `ast.parse(expr, mode='exec')` → catch `SyntaxError` → raise `ExpressionSyntaxError`
3. `ASTAllowlistVisitor().visit(tree)` → may raise `ExpressionSecurityError`

**`evaluate_expression(expr: str, timeout: float = EXPRESSION_TIMEOUT_SECONDS, **variables) -> Any`:**
1. Call `validate_expression(expr)` (AST check)
2. Build namespace: `{'__builtins__': SAFE_BUILTINS, **variables}`
3. Execute via `exec(compiled, namespace)` inside `asyncio.wait_for` with `timeout`
4. If the expression is a single expression (not statements), use the `_result_` wrapper: compile `_result_ = (expr)`, then return `namespace['_result_']`
5. For multi-line bodies: the last expression's value is captured via `_result_` assignment appended
6. Raise `ExpressionTimeoutError` if timeout exceeded
7. Raise `ExpressionEvalError(expression, node_id, original_exception)` on execution failure

**Helper functions (all call `evaluate_expression` internally):**
- `eval_predicate(expr, *, data, **kwargs) -> bool` — variables: `data` + kwargs
- `eval_transform(expr, *, data, node_id) -> Any` — variables: `data`
- `eval_merge(expr, *, inputs, node_id) -> Any` — variables: `inputs`
- `eval_expression(expr, **variables) -> Any` — general-purpose
- `eval_collection(expr, *, ctx) -> Iterable` — variables: `ctx`

**Acceptance Criteria:**
- `validate_expression('import os')` raises `ExpressionSecurityError`
- `validate_expression('x.__class__.__bases__')` raises `ExpressionSecurityError`
- `validate_expression('eval("1+1")')` raises `ExpressionSecurityError`
- `validate_expression('lambda x: x')` raises `ExpressionSecurityError`
- `validate_expression('a' * 10001)` raises `ExpressionSizeError`
- `evaluate_expression('data > 5', data=10)` returns `True`
- `evaluate_expression('while True: pass', timeout=0.1)` raises `ExpressionTimeoutError`
- `eval_predicate('len(data) > 0', data=[1,2])` returns `True`
- `eval_merge('inputs["a"] + inputs["b"]', inputs={'a': 1, 'b': 2})` returns `3`
- `SAFE_BUILTINS`, `MAX_EXPRESSION_LENGTH`, `MAX_AST_NODES` importable from `sandbox`

**Counterexamples:**
- Do NOT use bare `exec()` without AST validation
- Do NOT include `type()` constructor in SAFE_BUILTINS
- Do NOT skip timeout enforcement
- Do NOT allow `Lambda` AST nodes

**Requirement IDs:** REQ-62, REQ-44 | **Journey IDs:** J-16

---

### STEP-11: HierarchicalContext (D-GR-23)

**Objective:** Context lifecycle management with merge order workflow→phase→actor→node. ContextVar for node identity propagation.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/context.py` | modify |
| `iriai_compose/runner.py` | read |

**Instructions:**

```python
_current_node_var: ContextVar[str | None] = ContextVar('_current_node', default=None)

class HierarchicalContext:
    def __init__(self, context_provider: ContextProvider, feature: Feature):
        self._provider = context_provider
        self._feature = feature
        self._scopes: list[list[str]] = []

    def push_scope(self, context_keys: list[str]) -> None:
        self._scopes.append(context_keys)

    def pop_scope(self) -> None:
        self._scopes.pop()

    async def resolve(self) -> str:
        all_keys = list(dict.fromkeys(
            key for scope in self._scopes for key in scope
        ))
        if not all_keys:
            return ""
        return await self._provider.resolve(all_keys, feature=self._feature)

    @contextmanager
    def scope(self, context_keys: list[str]):
        self.push_scope(context_keys)
        try:
            yield
        finally:
            self.pop_scope()
```

- `_current_node_var` exported from `iriai_compose.declarative.context` for SF-3 consumption.
- `resolve()` deduplicates keys in merge order preserving first occurrence.
- Pattern in `runner.py`: import `_current_phase_var` from existing `iriai_compose.runner` — reuse for phase tracking.

**Acceptance Criteria:**
- `HierarchicalContext` with scopes `[['a','b'], ['b','c']]` resolves keys `['a','b','c']` (deduped)
- `_current_node_var.get()` returns `None` by default
- Context manager `scope()` pushes on entry, pops on exit (including on exception)

**Counterexamples:**
- Do NOT modify `AgentRuntime.invoke()` signature (D-GR-23)
- Do NOT use a flat context dict — must be scoped stack

**Requirement IDs:** REQ-57 | **Journey IDs:** J-13

---

### STEP-12: Validation and Loader (REQ-58, REQ-60)

**Objective:** `validate()` standalone function with stale-field rejection for all 14 REQ-60 items. Loader delegates to SF-1's `load_workflow()` with runtime validation layer.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/validation.py` | modify |
| `iriai_compose/declarative/loader.py` | modify |
| `iriai_compose/declarative/errors.py` | modify |
| `iriai_compose/schema/yaml_io.py` | read |

**Instructions:**

**`validate(workflow: WorkflowConfig | str | Path) -> list[ValidationError]`:**
1. If str/Path, load via SF-1's `load_workflow()` — catch parse errors as `ValidationError`.
2. **Stale-field rejection (14 items per REQ-60):** Check every item in the Stale-Field Rejection List (see Schema Entity Reference). Produce `ValidationError(field_path, message, code, severity='error')` for each.
3. **Structural validation:** Verify actor_refs resolve, plugin_refs resolve, type_refs resolve, port type_ref XOR schema_def, no cycles (outside loops), phase mode_config matches mode.
4. **Expression AST validation:** For every expression (BranchOutputPort.condition, merge_function, transform_fn, mode config expressions), call `validate_expression()` from sandbox.py.
5. **Hook edge constraints:** Verify no `transform_fn` on hook edges.
6. **Typed port validation:** Verify XOR type_ref/schema_def on all ports including BranchOutputPort.
7. Return all errors (do not short-circuit).

**`ValidationError` dataclass:**
```python
@dataclass
class ValidationError:
    field_path: str      # e.g., "phases[0].nodes[1].switch_function"
    message: str         # Human-readable guidance
    code: str            # Machine-readable (see rejection list)
    severity: str = "error"  # "error" or "warning"
```

**Loader (`loader.py`):**
- Import `load_workflow` from `iriai_compose.schema.yaml_io`.
- Re-export as `iriai_compose.declarative.load_workflow`.
- Add `validate_runtime_requirements(workflow, config: RuntimeConfig) -> list[str]`:
  - Verify all human actors have registered interaction runtimes
  - Verify all plugin_refs can be resolved (concrete or workflow-declared)
  - Verify agent_runtimes has entries for referenced providers

**Acceptance Criteria:**
- `validate(workflow_with_stores)` returns `ValidationError(code='unsupported_root_field')`
- `validate(workflow_with_switch_function)` returns `ValidationError(code='stale_branch_field')`
- `validate(workflow_with_interaction_actor)` returns `ValidationError(code='invalid_actor_type')`
- `validate(workflow_with_port_type_on_edge)` returns `ValidationError(code='unsupported_edge_field')`
- `validate(workflow_with_hook_transform)` returns `ValidationError(code='hook_edge_transform')`
- `validate(valid_workflow)` returns `[]`
- `validate(workflow_with_merge_function)` returns `[]` — merge_function MUST NOT be rejected
- All 14 stale items rejected with correct error codes
- Expression AST errors include field_path to the expression

**Counterexamples:**
- Do NOT duplicate YAML parsing — SF-1 owns that
- Do NOT reject `merge_function` — it is valid (D-GR-35)
- Do NOT skip any of the 14 rejection items
- validate() must NOT require live runtimes

**Requirement IDs:** REQ-58, REQ-60, REQ-48, REQ-51 | **Journey IDs:** J-14, J-15, J-16

---

### STEP-13: RuntimeConfig, Plugin Registry, and Built-in Plugins

**Objective:** `RuntimeConfig`, `PluginRegistry` (two-tier: concrete + category), `Plugin` ABC, `CategoryExecutor` ABC, `ExecutionContext`, and both built-in plugins.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/config.py` | modify |
| `iriai_compose/declarative/plugins.py` | modify |
| `iriai_compose/plugins/__init__.py` | modify |
| `iriai_compose/plugins/artifact_write.py` | modify |
| `iriai_compose/plugins/file_first_resolve.py` | modify |

**Instructions:**

**RuntimeConfig:**
```python
@dataclass
class RuntimeConfig:
    agent_runtimes: dict[str, AgentRuntime]     # Keyed by provider. "default" for unspecified.
    interaction_runtimes: dict[str, InteractionRuntime] = field(default_factory=dict)
    artifacts: ArtifactStore | None = None       # None → InMemoryArtifactStore
    sessions: SessionStore | None = None         # None → InMemorySessionStore
    context_provider: ContextProvider | None = None  # None → DefaultContextProvider(artifacts)
    plugin_registry: PluginRegistry | None = None    # None → default with builtins
    workspace: Workspace | None = None
    feature: Feature | None = None
```

**PluginRegistry (two-tier — no instance registration per D-GR-30):**
```python
class PluginRegistry:
    def __init__(self, *, auto_register_builtins: bool = True): ...

    # Tier 1: Concrete Plugin instances (built-ins + app-registered)
    def register(self, name: str, plugin: Plugin) -> None: ...
    def get(self, name: str) -> Plugin: ...
    def has(self, name: str) -> bool: ...

    # Category-based dispatch (for workflow-declared PluginInterface types)
    def register_category_executor(self, category: str, executor: CategoryExecutor) -> None: ...
    def get_category_executor(self, category: str) -> CategoryExecutor | None: ...
```

**Built-in plugins:** See Built-in Plugins section. `ArtifactPlugin` and `FileFirstResolvePlugin` registered by `register_builtins()`.

**Acceptance Criteria:**
- `PluginRegistry()` auto-registers `artifact_write` and `file_first_resolve`
- `registry.get('artifact_write')` returns `ArtifactPlugin` instance
- `ArtifactPlugin.execute()` writes to store, returns input unchanged
- `FileFirstResolvePlugin.execute()` returns cached value if present, else input
- `registry.register_category_executor('mcp', mcp_exec)` stores executor
- No `register_type()`, `register_instance()`, `get_type()`, `get_instance()` methods

**Counterexamples:**
- Do NOT implement three-tier instance resolution (D-GR-30: no plugin_instances)
- Do NOT allow duplicate plugin names (raise on conflict)
- Category executors NOT auto-registered — consuming projects register them

**Requirement IDs:** REQ-47 | **Journey IDs:** J-13

---

### STEP-14: DAG Builder (Unified `ExecutionGraph`)

**Objective:** `build_execution_graph(container, *, is_workflow=False)`. One type, one builder. Elements include both `nodes` and `children` (sub-phases). Source port resolution.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/graph.py` | modify |
| `iriai_compose/declarative/errors.py` | modify |

**Instructions:**
- At workflow level: elements = `{p.id: p for p in container.phases}`, edges = `container.edges`.
- At phase level: elements = `{n.id: n for n in container.nodes}` + `{sp.id: sp for sp in container.children}`.
- Separate `$input`/`$output`/hook/data edges. Hook identification: source port in `hooks` list.
- Topological sort via Kahn's algorithm. Raise `CycleDetectedError` with cycle path.
- Raise `DuplicateElementError` on ID collision.
- `_resolve_source_port(data, source_port)`: `"output"`/`"default"` → as-is; dict + key match → extract; else as-is.

**Acceptance Criteria:**
- Phase with `nodes` + `children` produces single `ExecutionGraph` with all elements
- Field is `children` (not `phases`) for sub-phases
- Cycle in non-loop phase raises `CycleDetectedError`
- Duplicate element IDs raise `DuplicateElementError`
- Same `ExecutionGraph` type at both workflow and phase levels

**Counterexamples:**
- No `WorkflowGraph` type — single unified `ExecutionGraph`
- Field name is `children` not `phases` (D-GR-22)

**Requirement IDs:** REQ-52 | **Journey IDs:** J-13

---

### STEP-15: Node Executors

**Objective:** `execute_ask_node`, `execute_branch_node` (per-port non-exclusive, D-GR-35), `execute_plugin_node` (two-tier), `execute_error_node` (D-GR-13).

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/executors.py` | modify |
| `iriai_compose/declarative/errors.py` | modify |
| `iriai_compose/runner.py` | read |
| `iriai_compose/storage.py` | read |

**Instructions:**

**Ask executor:**
1. Look up actor from `workflow.actors[node.actor_ref]`. Hydrate via STEP-18.
2. Build context: push node scope (`node.context_keys`) + actor scope (`actor.context_keys`) onto HierarchicalContext.
3. Resolve context string via `hierarchical_context.resolve()`.
4. Render prompt via Jinja2 SandboxedEnvironment: `{{ $input }}` → input data, `{{ ctx.KEY }}` → context values.
5. Assemble: `f"{context_str}\n\n## Task\n{rendered_prompt}"` if context_str else just rendered_prompt.
6. Set `_current_node_var` to `node.id`.
7. Look up runtime: `config.agent_runtimes[actor.provider or 'default']`.
8. Call `runtime.invoke(role, prompt, output_type=..., workspace=..., session_key=...)` — **unchanged signature** (D-GR-23).
9. Pop node + actor context scopes.
10. Return raw output.

**Branch executor (D-GR-35):**
1. Gather: if multiple inputs, wait for all (barrier in `_execute_dag`).
2. Merge: if `merge_function` and multi-input dict, `sandbox.eval_merge(fn, inputs=data)`.
3. Single input: unwrap from dict.
4. Evaluate per-port conditions (non-exclusive): for each `port_name, branch_port` in `node.outputs.items()`: `sandbox.eval_predicate(branch_port.condition, data=merged)` → `port_fires[port_name] = result`.
5. Return `(port_fires, merged)`. NO switch_function.

**Plugin executor (two-tier):**
1. Try `registry.get(node.plugin_ref)` → concrete `Plugin.execute()`.
2. Else try `workflow.plugins[node.plugin_ref]` → category dispatch.
3. Build `ExecutionContext`.
4. Handle `outputs: []` (fire-and-forget).

**Error executor (D-GR-13):**
1. Render `node.message` via Jinja2 with `{"input": input_data, "error": input_data}` context.
2. Raise `WorkflowError(message=rendered, error_code=node.error_code, node_id=node.id)`.

**Acceptance Criteria:**
- Ask: calls `runtime.invoke()` with unchanged signature (verify via mock)
- Ask: context assembled in order workflow→phase→actor→node
- Branch: non-exclusive — multiple ports CAN fire simultaneously
- Branch: `merge_function` combines multi-input before condition eval
- Branch: no switch_function execution path exists
- Plugin: concrete resolution first, then workflow-declared + category
- Plugin: `outputs: []` executes without error
- Error: reaching ErrorNode raises `WorkflowError` with rendered message
- `_current_node_var` set to node.id before invoke, readable by SF-3 mocks

**Counterexamples:**
- Do NOT implement switch_function — permanently rejected (D-GR-35)
- Do NOT auto-write to artifact store after execution (D-GR-14)
- Do NOT add node_id parameter to invoke() (D-GR-23)
- Do NOT resolve instance_ref — field does not exist (D-GR-30)

**Requirement IDs:** REQ-54, REQ-55, REQ-57 | **Journey IDs:** J-13, J-16

---

### STEP-16: Phase Mode Executors + Unified `_execute_dag`

**Objective:** `_execute_dag`, all four mode strategies, `_dispatch_element`, `_activate_outgoing_edges`, branch barrier.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/modes.py` | modify |
| `iriai_compose/declarative/runner.py` | modify |

**Instructions:**

**`_dispatch_element`:** `hasattr(element, 'mode')` → `execute_phase()`. `element.type` → node executor. BranchNode returns `(port_fires, merged)` tuple.

**`_activate_outgoing_edges` — three models:**
1. BranchNode: fire edges for all truthy ports in `port_fires`. Non-exclusive.
2. Loop exit: `edge_matches_exit_path` selects edges based on exit path name.
3. Default: all outgoing edges fire.

**`_execute_dag`:** Unified engine. Branch nodes handled specially (tuple return). Deferred queue with 2× safety cap for barrier deadlock detection.

**Mode implementations via `mode_config` discriminated union:**
- **Sequential:** Execute elements in topo order, thread `$output` to next `$input`.
- **Map:** `eval_collection(config.collection, ctx=...)` → items. `asyncio.Semaphore(config.max_parallelism)` when set. Unique actor instances per parallel branch. Collect outputs into list.
- **Fold:** `eval_collection(config.collection, ctx=...)` → items. `eval_expression(config.accumulator_init)` → initial accumulator (NO variables). Each item: `{"item": item, "accumulator": acc}` as `$input`. After each: `eval_expression(config.reducer, accumulator=acc, result=iteration_output)`.
- **Loop:** `condition` receives `data` = iteration `$output`. `True` → exit via `condition_met`. `max_iterations` → exit via `max_exceeded`. Loop error → 3rd exit path via `error` port (D-GR-4).

**Phase context lifecycle:** Before executing phase internals, push `phase.context_keys` onto `HierarchicalContext`. Pop after phase completes.

**Acceptance Criteria:**
- Map `max_parallelism=2` limits concurrent executions
- Fold `accumulator_init` evaluated with no variables
- Fold `reducer` evaluated with `accumulator` + `result`
- Loop auto-ports `condition_met`/`max_exceeded` read from validated model
- Branch barrier defers until all inputs ready; 2× cap → `DeadlockError`
- Same `_execute_dag` at workflow and phase levels
- `collection` receives `ctx` not `data`

**Counterexamples:**
- No separate workflow execution loop — reuse `_execute_dag`
- Mode config accessed via `phase.mode_config` (discriminated union), NOT 4 separate fields
- Sub-phases accessed via `phase.children`, NOT `phase.phases`
- Do NOT create loop exit ports — read from SF-1 validated model

**Requirement IDs:** REQ-52, REQ-56 | **Journey IDs:** J-13

---

### STEP-17: Error Propagation (D-GR-4)

**Objective:** Node-level error-port routing, phase-level fail-fast + bubble, mode-specific error behavior, ErrorRoute recording.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/runner.py` | modify |
| `iriai_compose/declarative/modes.py` | modify |
| `iriai_compose/declarative/errors.py` | modify |

**Instructions:**
- In `_execute_dag`, wrap each node dispatch in try/except.
- On exception: check if element has output port named `error` with outgoing edges in the graph.
- **Handled:** Create `ErrorInfo(message, traceback_str, node_id)`. Route through `error` port edges. Store `ErrorRoute` in `ExecutionHistory.error_routes`. Continue phase.
- **Unhandled:** Re-raise wrapped in `PhaseExecutionError`. In containing scope, check phase's `error` port → handle or bubble.
- **Map mode:** On unhandled node error → cancel sibling branches via `asyncio.Task.cancel()`. Record partial results.
- **Fold mode:** On unhandled → stop fold, preserve partial accumulator in `FoldProgress`.
- **Loop mode:** On unhandled → stop loop. If loop phase has `error` output port → 3rd exit path (alongside `condition_met` and `max_exceeded`).

**Acceptance Criteria:**
- Node with `error` port + outgoing edge: error routed to handler, phase continues
- Node without `error` port: error bubbles to phase
- Phase with `error` port: error handled at workflow level
- Map mode: sibling cancellation on unhandled error
- `ExecutionHistory.error_routes` records every routing event
- ErrorNode (type="error") triggers this propagation when reached

**Counterexamples:**
- Do NOT swallow errors silently
- Do NOT fail-fast the entire workflow on a handled error
- Error ports are regular output ports named `error` — no special edge type

**Requirement IDs:** REQ-54 (via D-GR-4) | **Journey IDs:** J-13

---

### STEP-18: Actor Hydration

**Objective:** Bridge `ActorDefinition` (agent|human) to runtime actors. Provider-based runtime routing.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/actors.py` | modify |
| `iriai_compose/actors.py` | read |

**Instructions:**
- `ActorDefinition` with `actor_type='agent'` → `AgentActor(name=key, role=Role(...), context_keys=def.context_keys, persistent=def.persistent)`.
- `ActorDefinition` with `actor_type='human'` → `InteractionActor(name=key, resolver=def.channel or def.identity or key)`.
- `actor_type='interaction'` → raise `ActorHydrationError` (should be caught by validate(), but defend).
- Runtime lookup: `config.agent_runtimes[actor_def.provider or 'default']`.
- Actor `context_keys` merged at execution time in `execute_ask_node` (STEP-15), not during hydration.

**Acceptance Criteria:**
- `AgentActorDef(actor_type='agent', role=...)` → `AgentActor` with matching fields
- `HumanActorDef(actor_type='human', channel='slack')` → `InteractionActor(resolver='slack')`
- `actor_type='interaction'` raises `ActorHydrationError`
- Provider-based runtime lookup works: `provider='claude'` → `config.agent_runtimes['claude']`

**Counterexamples:**
- Do NOT merge actor context_keys during hydration — that happens at execution time
- Do NOT accept `interaction` as a valid actor_type

**Requirement IDs:** REQ-49 | **Journey IDs:** J-13, J-15

---

### STEP-19: Hook Execution

**Objective:** Fire-and-forget hook edges. Hook identification from port container. `transform_fn=None` enforcement.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/hooks.py` | modify |

**Instructions:**
- Hook edges identified at graph-build time (STEP-14) by checking if source port is in the element's `hooks` list.
- Hook target executes with the triggering element's output as input.
- `transform_fn` must be `None` — validated by `validate()` (STEP-12) and asserted defensively here.
- Fire-and-forget: failures caught, logged as warnings, stored in `ExecutionResult.history` as `HookWarning`.

**Acceptance Criteria:**
- `on_end → plugin_node.input` fires after element completes
- Hook failure does NOT abort workflow
- Hook with `transform_fn` set raises at graph-build time

**Requirement IDs:** REQ-53 | **Journey IDs:** J-13

---

### STEP-20: Cost Tracking and Top-level `run()`

**Objective:** `CostSummary`, `NodeCost`, cost-tracking wrapper. `run()` implementation with full `ExecutionResult` + `ExecutionHistory`.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/cost.py` | modify |
| `iriai_compose/declarative/runner.py` | modify |
| `iriai_compose/declarative/__init__.py` | modify |

**Instructions:**

**Cost tracking:**
```python
@dataclass
class NodeCost:
    node_id: str
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0

@dataclass
class CostSummary:
    total_tokens: int = 0
    total_usd: float = 0.0
    by_node: list[NodeCost] = field(default_factory=list)
```

**`run()` implementation:**
```python
async def run(
    workflow: WorkflowConfig | str | Path,
    config: RuntimeConfig,
    *,
    inputs: dict[str, Any] | None = None,
) -> ExecutionResult:
```
1. Load if str/Path via SF-1 loader.
2. Call `validate(workflow)` — if errors, raise `WorkflowValidationError(errors)`.
3. Initialize stores (InMemory defaults when None).
4. Initialize PluginRegistry (with builtins) if not provided.
5. Hydrate actors (STEP-18). Validate runtime requirements.
6. Create `HierarchicalContext`, push workflow scope (`workflow.context_keys`).
7. Build `ExecutionGraph` from workflow (phases as elements, `is_workflow=True`).
8. No-edges fallback: sequential execution.
9. Execute via `_execute_dag` with `phase_input=inputs or {}`.
10. Build `ExecutionResult` with `ExecutionHistory` (phase_metrics, node_trace, error_routes, map_fan_out, fold_progress, loop_progress).
11. Return result.

**`branch_paths`:** `dict[str, list[str]]` — maps branch node ID to list of fired port names (supports multiple per D-GR-12 non-exclusive).

**Acceptance Criteria:**
- `run(yaml_path, config)` loads, validates, executes, returns `ExecutionResult`
- `run(valid_workflow, config, inputs={'project': '...'})` passes inputs as `$input`
- Invalid workflow → `WorkflowValidationError` before execution
- `ExecutionResult.history.phase_metrics` populated per phase
- `ExecutionResult.history.error_routes` populated for error-port routing events
- `ExecutionResult.branch_paths` maps to `list[str]` (multiple fired ports)
- `run()` signature: `run(workflow, config: RuntimeConfig, *, inputs=None)`

**Counterexamples:**
- Do NOT execute workflows that `validate()` would reject
- Do NOT auto-write artifacts (D-GR-14)
- Do NOT implement checkpoint/resume (D-GR-24)
- Signature MUST be `run(workflow, config: RuntimeConfig, *, inputs=None)` — no other ordering

**Requirement IDs:** REQ-58, REQ-61 | **Journey IDs:** J-13, J-14

---

### STEP-21: Public Exports

**Objective:** Wire all public API symbols into `iriai_compose/declarative/__init__.py`.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/__init__.py` | modify |

**Instructions:**
```python
from iriai_compose.declarative.runner import run, ExecutionResult, ExecutionHistory
from iriai_compose.declarative.validation import validate, ValidationError
from iriai_compose.schema.yaml_io import load_workflow
from iriai_compose.declarative.config import RuntimeConfig
from iriai_compose.declarative.plugins import (
    PluginRegistry, Plugin, ExecutionContext, CategoryExecutor,
)
from iriai_compose.declarative.sandbox import (
    ExpressionSandbox, validate_expression, evaluate_expression,
    SAFE_BUILTINS, MAX_EXPRESSION_LENGTH, MAX_AST_NODES,
    EXPRESSION_TIMEOUT_SECONDS,
)
from iriai_compose.declarative.context import (
    HierarchicalContext, _current_node_var,
)
from iriai_compose.declarative.cost import CostSummary, NodeCost
from iriai_compose.declarative.errors import (
    DeclarativeExecutionError, WorkflowLoadError, WorkflowValidationError,
    WorkflowError, ExpressionEvalError, ExpressionSecurityError,
    ExpressionSizeError, ExpressionTimeoutError, ExpressionSyntaxError,
    PluginNotFoundError, PluginConfigError, CycleDetectedError,
    DuplicateElementError, HookEdgeError, DeadlockError,
    ActorHydrationError, PhaseExecutionError,
)
```

**Acceptance Criteria:**
- All listed symbols importable from `iriai_compose.declarative`
- `load_workflow` is SF-1's function, not duplicated
- `validate` importable alongside `run`
- No circular imports

**Requirement IDs:** REQ-63 | **Journey IDs:** J-13

---

### STEP-22: Integration Tests

**Objective:** Comprehensive tests covering unified engine, per-port branch routing, error propagation, expression sandbox, HierarchicalContext, plugin dispatch, all modes, and stale-field rejection.

**Scope:**
| Path | Action |
|------|--------|
| `tests/declarative/__init__.py` | create |
| `tests/declarative/test_sandbox.py` | create |
| `tests/declarative/test_validation.py` | create |
| `tests/declarative/test_engine.py` | create |
| `tests/declarative/test_branch.py` | create |
| `tests/declarative/test_error_propagation.py` | create |
| `tests/declarative/test_modes.py` | create |
| `tests/declarative/test_context.py` | create |
| `tests/declarative/test_hooks.py` | create |
| `tests/declarative/test_plugins.py` | create |
| `tests/declarative/fixtures/` | create |

**Instructions:**

**Sandbox tests (10):**
1. `import os` rejected by AST visitor
2. `__class__.__bases__` rejected
3. `eval('...')` rejected
4. `lambda x: x` rejected
5. Expression > 10,000 chars rejected
6. > 200 AST nodes rejected
7. Timeout enforcement (infinite loop)
8. Valid expression with `data` variable
9. Valid predicate returning bool
10. Valid merge with `inputs` dict

**Validation tests (16 — one per stale item + 2 positive):**
1-14. One test per stale-field rejection item (stores, plugin_instances, top-level nodes, interaction actor, missing hooks, switch_function, condition_type, condition, paths, output_field, unknown branch port, port_type, hook section, hook transform_fn)
15. Valid workflow returns `[]`
16. `merge_function` NOT rejected

**Engine tests (5):**
1. Single-phase sequential: Ask → Ask → $output
2. Multi-phase workflow: phase_A → phase_B → phase_C
3. Nested phases: phase with children sub-phase
4. No-edges fallback
5. Workflow-level edges

**Branch tests (8 — per-port only, no switch_function):**
1. Single-input single-output passthrough
2. Multi-input gather with merge_function
3. Non-exclusive: multiple ports fire simultaneously
4. Barrier defers until all inputs ready
5. No-match: no conditions truthy → warning logged
6. Degenerate: 1 input, 1 output (pure gather)
7. Barrier deadlock → DeadlockError
8. merge_function combines inputs correctly

**Error propagation tests (6):**
1. Node with error port → handled, phase continues
2. Node without error port → bubbles to phase
3. Phase with error port → handled at workflow level
4. Map mode: unhandled → cancel siblings
5. Fold mode: unhandled → partial accumulator preserved
6. Loop mode: unhandled → error exit path
7. ErrorNode reached → WorkflowError raised and bubbles

**Mode tests (8):**
1. Sequential basic
2. Map with collection expression using `ctx`
3. Map with `max_parallelism=2`
4. Fold with accumulator_init (no variables)
5. Fold reducer with `accumulator` + `result`
6. Loop with condition (True exits via `condition_met`)
7. Loop with max_iterations (exits via `max_exceeded`)
8. Nested phase mode execution

**Context tests (4):**
1. HierarchicalContext merge order: workflow→phase→actor→node
2. Deduplication preserves first occurrence
3. `_current_node_var` set during node execution
4. Scope push/pop lifecycle (including exception cleanup)

**Hook tests (2):**
1. on_end hook fires after element, target receives output
2. Hook failure → warning, not abort

**Plugin tests (5):**
1. ArtifactPlugin writes and returns input unchanged
2. FileFirstResolvePlugin returns cached when present
3. FileFirstResolvePlugin returns input when not cached
4. Concrete plugin resolution
5. Category dispatch via workflow-declared PluginInterface

**Requirement IDs:** REQ-64 | **Journey IDs:** J-13, J-14, J-15, J-16, J-17

## iriai-build-v2 Pattern Verification

| # | Pattern | Status | Notes |
|---|---------|--------|-------|
| 1 | broad_interview | **WORKS** | Loop + Ask. Explicit `artifact_write` plugin for persistence. `file_first_resolve` for resume-safety. |
| 2 | gate_and_revise | **WORKS** | Loop + AskNode + BranchNode per-port conditions + hook edges. Verdict routing via `BranchOutputPort.condition`. |
| 3 | per_subfeature_loop | **WORKS** | Fold + `ctx` collection + cross-boundary edges. Explicit `artifact_write` per iteration. |
| 4 | parallel execution | **WORKS** | Map + `max_parallelism` + unique actor names per parallel branch. |
| 5 | DAG execution groups | **WORKS** | Fold > Map nesting + handover accumulator. |
| 6 | interview_gate_review | **WORKS** | Loop + mixed nodes/phases + hook edges. |
| 7 | integration_review | **WORKS** | BranchNode multi-input gather + `merge_function` + barrier. Per-port conditions for dispatch. |
| 8 | HostedInterview | **WORKS** | AskNode + `on_end` hook → `artifact_write` Plugin for persistence → doc_hosting Plugin. |
| 9 | Session management | **WORKS** | `persistent` on ActorDefinition. |
| 10 | Resume/checkpoint | **OUT OF SCOPE** | Use `file_first_resolve` + `artifact_write` for workflow-level resume. D-GR-24. |
| 11 | Parameterized workflows | **WORKS** | `inputs` parameter on `run()`. |
| 12 | Notification fan-out | **WORKS** | BranchNode non-exclusive per-port conditions — multiple ports fire simultaneously. |
| 13 | Ambient services | **WORKS** | `ExecutionContext.services` |
| 14 | Nested impl DAG | **WORKS** | Plugin node with runner access via `ExecutionContext.runner`. |
| 15 | Cost tracking | **WORKS** | CostSummary + NodeCost. |
| 16 | Fire-and-forget plugins | **WORKS** | `outputs: []` on PluginNode. |
| 17 | Artifact persistence | **WORKS** | Explicit `artifact_write` PluginNode. No auto-write. |
| 18 | Programmatic routing | **WORKS** | BranchNode per-port conditions for all routing. Binary decisions: `"data.approved"` on approved port, `"not data.approved"` on rejected port. |
| 19 | Category-based plugins | **WORKS** | `PluginRegistry.register_category_executor()` for MCP/CLI/service dispatch. |
| 20 | Error handling | **WORKS** | Error output ports for handled errors. ErrorNode for validation dead-ends. D-GR-4 bubbling. |

**Pattern note: routing without switch_function.** Where iriai-build-v2 uses `if verdict.approved: ...`, the declarative equivalent is:
```yaml
- id: review_gate
  type: branch
  outputs:
    approved:
      condition: "data.approved"
    rejected:
      condition: "not data.approved"
```
Per-port conditions are equally expressive. Non-exclusive fan-out also supports patterns where multiple paths activate simultaneously.

## Interfaces to Other Subfeatures

### SF-1 → SF-2
**Contract:** All entity types per Schema Entity Reference above. Runner depends on SF-1 validators (port validation, BranchNode output constraints, loop auto-ports, stale-field rejection). `import from iriai_compose.schema` (NOT `iriai_compose.declarative.schema`).

**Loader delegation:** SF-2's `loader.py` imports `load_workflow` from `iriai_compose.schema.yaml_io`. No YAML parsing duplication.

**Expression sandbox constants:** SF-1 imports `MAX_EXPRESSION_LENGTH`, `MAX_AST_NODES` from `iriai_compose.declarative.sandbox` for schema-level size validation.

**23 valid SF-1 exports:** `WorkflowConfig`, `AskNode`, `BranchNode`, `BranchOutputPort`, `PluginNode`, `ErrorNode`, `PhaseDefinition`, `EdgeDefinition`, `PortDefinition`, `ActorDefinition`, `AgentActorDef`, `HumanActorDef`, `RoleDefinition`, `TypeDefinition`, `PluginInterface`, `TemplateDefinition`, `SequentialModeConfig`, `MapModeConfig`, `FoldModeConfig`, `LoopModeConfig`, `WorkflowCostConfig`, `PhaseCostConfig`, `NodeCostConfig`.

**Phantom types that do NOT exist:** `MapNode`, `FoldNode`, `LoopNode`, `TransformRef`, `HookRef`, `StoreDefinition`, `PluginInstanceConfig`.

### SF-2 → SF-3
**Contract:**
- `run(workflow: WorkflowConfig, config: RuntimeConfig, *, inputs=None) -> ExecutionResult`
- `validate(workflow) -> list[ValidationError]`
- `RuntimeConfig(agent_runtimes={...}, interaction_runtimes={...}, artifacts=..., ...)`
- `ExecutionResult.history: ExecutionHistory` with `phase_metrics`, `error_routes`, `map_fan_out`, `fold_progress`, `loop_progress`
- `ExecutionResult.nodes_executed: list[tuple[str, str]]` (node_id, node_type)
- `ExecutionResult.branch_paths: dict[str, list[str]]` (node_id → fired port names)
- `_current_node_var: ContextVar[str | None]` from `iriai_compose.declarative.context`
- `validate_expression()`, `SAFE_BUILTINS`, sandbox constants from `sandbox.py`

**ContextVar-based mock routing:** SF-3's `MockAgentRuntime` reads `_current_node_var.get()` during `invoke()` to match `when_node()` responses. No `node_id` kwarg on `invoke()` (D-GR-23).

### SF-2 → SF-4
**Contract:** `run()` executes migrated YAML. `PluginRegistry.register()` for concrete plugins. `PluginRegistry.register_category_executor()` for MCP/CLI/service dispatch. No `instance_ref`. CLI uses `--declarative` flag (D-GR-18). Transforms must be AST-safe expressions (D-GR-5).

### SF-2 → SF-5
**Contract:** SF-5 provides store implementations and may expose validation results in UI. `/api/schema/workflow` served from SF-1's `model_json_schema()` — SF-2 validates against the same models.

## Architectural Risks

| ID | Description | Severity | Mitigation | Steps |
|----|-------------|----------|------------|-------|
| RISK-18 | SF-1 schema not finalized | high | Start with STEP-9 skeleton | 9-12 |
| RISK-19 | AST sandbox bypass | medium | Comprehensive blocklist + timeout + size limit. Future: restricted subprocess. | 10 |
| RISK-20 | Nested phase recursion depth | low | Max ~5 levels. Stack overflow protection. | 14,16 |
| RISK-21 | Plugin discovery failures | low | try/except with clear error messages | 13 |
| RISK-22 | Branch merge malformed input | medium | Port-keyed dict guaranteed by barrier | 15,16 |
| RISK-23 | Expression timeout bypass | medium | asyncio.wait_for may not interrupt CPU-bound exec | 10 |
| RISK-24 | Map actor collision | high | Unique actor instances per parallel branch | 16 |
| RISK-25 | Error propagation complexity | medium | Comprehensive test suite (STEP-22) | 17 |
| RISK-26 | Hook failures invisible | medium | Recorded in ExecutionHistory | 19 |
| RISK-27 | Missing InteractionRuntime | medium | Pre-flight validation in loader | 12 |
| RISK-28 | HierarchicalContext scope leak | medium | Context manager with finally cleanup | 11 |
| RISK-29 | iriai-build-v2 drift | low | Pattern verification + SF-4 litmus | All |
| RISK-30 | Branch barrier deadlock | medium | 2× safety cap → DeadlockError | 16 |
| RISK-31 | Non-exclusive fan-out ordering | medium | Topo order, no parallelism on fan-out | 16 |
| RISK-32 | ErrorNode message template injection | low | Jinja2 SandboxedEnvironment | 15 |
| RISK-33 | Loop exit routing ambiguity | medium | `edge_matches_exit_path` | 16 |
| RISK-34 | Cycle detection in loops | low | Loops exempt from cycle check | 14 |
| RISK-35 | Large expression bodies | low | 10k char + 200 AST node limits | 10 |
| RISK-36 | Category executor missing | medium | Clear PluginNotFoundError message | 15 |
| RISK-37 | Stale-field false negatives | medium | Exhaustive test per REQ-60 item | 12,22 |
| RISK-38 | ContextVar concurrent safety | low | ContextVar is async-safe by design | 11 |

## New Dependencies

| Package | Version | Purpose | Added to |
|---------|---------|---------|----------|
| pyyaml | >=6.0,<7.0 | YAML parsing (via SF-1) | `pyproject.toml` dependencies |
| jinja2 | >=3.1,<4.0 | Template rendering (AskNode.prompt, ErrorNode.message) | `pyproject.toml` dependencies |



---

## Subfeature: Testing Framework (testing-framework)

### SF-3: Testing Framework

<!-- SF: testing-framework -->




## D-GR Compliance Checklist

| Decision | Status | Notes |
|----------|--------|-------|
| D-GR-2 (ExecutionResult shape, ExecutionHistory) | ✅ Compliant | Assertions wrap `result.history` for loop/fold/map metrics per D-GR-2 |
| D-GR-8 (SF-3 full PRD scope) | ✅ Compliant | 3 mock classes, 13 assertion functions, 10 fixture factories, WorkflowTestCase, snapshots, stale-item cleanup |
| D-GR-12 (BranchNode gather+fan-out) | ✅ Compliant | WorkflowBuilder supports per-port BranchOutputPort conditions |
| D-GR-13 (ErrorNode 4th type) | ✅ Compliant | `add_error_node()` on builder; `error_port_workflow` fixture |
| D-GR-14 (ArtifactPlugin, no artifact_key) | ✅ Compliant | `artifact_key` removed from builder AskNode construction |
| D-GR-16 (list[PortDefinition] with name field) | ✅ Compliant | Builder produces `list[PortDefinition]`; dict shorthand is loader (SF-2) concern |
| D-GR-22 (nested YAML, edge hooks) | ✅ Compliant | Fixtures use `phases[].nodes` / `children`; hook edges in `hook_edge_workflow` |
| D-GR-23 (invoke unchanged, ContextVar, merge order) | ✅ Compliant | `invoke()` signature unchanged; mocks read `_current_node` ContextVar; merge `workflow→phase→actor→node` |
| D-GR-24 (no core checkpoint/resume) | ✅ Compliant | No checkpoint/resume in `run_test()` or assertions; `ExecutionHistory` for observability only |
| D-GR-30 (agent\|human, closed root set) | ✅ Compliant | Builder uses `actor_type: agent\|human`; no `stores`/`plugin_instances` at root |
| D-GR-35 (per-port BranchNode) | ✅ Compliant | BranchNode `outputs: dict[str, BranchOutputPort]` with per-port conditions |
| D-GR-36 (ErrorNode as 4th atomic type) | ✅ Compliant | ErrorNode entity: `id`, `type: error`, `message` (Jinja2 template), `inputs` (dict), NO outputs, NO hooks. `add_error_node()` on builder; `error_port_workflow` fixture |
| D-GR-37 (interaction key = "human") | ✅ Compliant | `run_test()` maps `interaction` kwarg to `interaction_runtimes={"human": interaction}`. Key "default" is rejected. |
| D-GR-40 (MockPluginRuntime, assert_loop_iterations, fluent methods) | ✅ Compliant | `MockPluginRuntime` in `mocks/plugin.py`; `assert_loop_iterations` wraps `result.history`; full fluent terminal set |
| D-GR-42 (D-GR canonical authority) | ✅ Compliant | This checklist present |

### Stale Items Removed

| Removed Item | Reason |
|---|---|
| D-SF3-5 (old: `node_id` kwarg on `invoke()`) | Violates D-GR-23; permanently prohibited |
| D-SF3-16 (old: invoke owns node routing via `node_id`) | Violates D-GR-23 / REQ-68; permanently prohibited |
| `MockRuntime` class name | Renamed to `MockAgentRuntime` per CMP-7 |
| `MockInteraction` class name | Renamed to `MockInteractionRuntime` per CMP-8 |
| Dict-based mock constructor (`responses={}`) | Replaced with fluent no-arg API per D-SF3-2 |
| `artifact_key` on AskNode in builder | Removed per D-GR-14 |
| `stores`, `plugin_instances` on WorkflowConfig root | Removed per D-GR-30 |
| `type: "interaction"` actor discriminator | Replaced with `actor_type: "human"` per D-GR-30 |
| `switch_function` references | Rejected per D-GR-35 |
| Store-related validation codes | Removed per D-GR-30 (no stores at root) |
| `invalid_switch_function_config` code | Removed per D-GR-35 |
| `invalid_workflow_io_ref` code | Removed per D-GR-30 |
| TransformRegistry / HookRegistry references | Transforms inline; hooks edge-based |

## Architecture

### Decision Log

| ID | Decision | Rationale | Citation |
|----|----------|-----------|----------|
| D-SF3-1 | Assertion API — standalone functions | Matches existing iriai-compose test style with plain `assert` + helpers. Composes with pytest introspection. | [code: iriai-compose/tests/conftest.py] |
| D-SF3-2 | All mock runtimes use fluent no-arg constructor API. Dict-based constructors prohibited. | Dict constructors cannot express priority matching, response sequences, per-matcher exception injection, or cost metadata. Fluent API enables chaining and is self-documenting. | [decision: D-GR-40], [decision: D-GR-23] |
| D-SF3-3 | Snapshot testing — fixtures directory `tests/fixtures/workflows/` | Multi-line YAML unreadable as inline strings. Fixtures directory is inspectable and diffable. | [research: pytest snapshot patterns] |
| D-SF3-4 | Module location — `iriai_compose/testing/` with nested subpackages (`mocks/`, `assertions/`, `builders/`, `snapshots/`), installed via `pip install iriai-compose[testing]` | Tight dependency on schema models/runner. Co-location ensures version coherence. Nested structure per cycle 2 mandate. | [code: iriai-compose/pyproject.toml], [decision: D-GR-8] |
| D-SF3-5 | `AgentRuntime.invoke()` UNCHANGED per SF-2 published ABI. Node identity propagates via runner-managed `_current_node: ContextVar[str \| None]`. `MockAgentRuntime.when_node()` resolves against that ContextVar during `invoke()`. **Adding `node_id` to `invoke()` is PERMANENTLY PROHIBITED.** | D-GR-23 mandates non-breaking invoke() signature. SF-2 already uses ContextVar pattern (`_current_phase_var`). ContextVar is the correct mechanism for ambient node state. | [decision: D-GR-23], [code: iriai-compose/iriai_compose/runner.py:32-33] |
| D-SF3-5a | Invocation context to `respond_with()` handlers assembled by SF-2 in canonical order: `workflow → phase → actor → node`, deduplicated in that order. SF-3 handlers consume; must NOT reassemble. | D-GR-23 defines the merge order. SF-3 is a consumer, not an assembler. | [decision: D-GR-23] |
| D-SF3-6 | `run_test()` is a thin convenience wrapper — constructs `RuntimeConfig`, delegates to SF-2's `run(workflow, config, inputs=inputs)`. No exception swallowing. No checkpoint/resume synthesis. | Match implementation principle. SF-3 is a consumer of SF-2's ABI. | [decision: D-GR-24] |
| D-SF3-7 | SF-1 owns validation logic in `iriai_compose/schema/validation.py`. SF-3 re-exports via `iriai_compose.testing` and adds `assert_validation_error()` as test-specific assertion. | No duplication. SF-1 owns building-context validation; SF-3 owns testing-context assertion. | [decision: D-GR-8] |
| D-SF3-8 | Sequential build — all steps assume SF-1 and SF-2 exist at implementation time. | SF-3 sits after SF-1 and SF-2 in the dependency graph. No stubs needed. | [decision: D-GR-42] |
| D-SF3-9 | `pyyaml` for snapshot testing, `deepdiff` NOT included — use unified diff for YAML comparison. | pyyaml already in SF-2 dependencies. Custom diff with difflib is lighter and produces pytest-friendly output. | [code: iriai-compose/pyproject.toml] |
| D-SF3-10 | `respond_sequence()` never wraps. Exhaustion raises `MockExhaustedError` (loop-count bugs fail loudly). | Silent recycling hides infinite-loop bugs in loop-mode tests. Loud failure is the correct behavior. | [decision: D-GR-40] |
| D-SF3-11 | Anti-patterns prohibited: (1) `MockAgentRuntime.__init__` must NOT accept config params; (2) `when_node()`/`when_role()` return dedicated matcher objects; (3) terminal methods return parent runtime; (4) matcher resolution is NOT dict-order-dependent; (5) `invoke()` must NOT grow `node_id`; (6) handler context must follow `workflow → phase → actor → node` order only. | Enforcement of D-GR-23 and fluent API contract. | [decision: D-GR-23], [decision: D-GR-40] |
| D-SF3-12 | `run_test()` calls `run(workflow, config, inputs=inputs)` matching SF-2's exact signature. `Feature` passed via `RuntimeConfig.feature` field, not as separate argument. `interaction` kwarg maps to `config.interaction_runtimes={'human': interaction}` per D-GR-37. | SF-2 is ABI owner per D-GR-23. SF-3 is consumer only. | [decision: D-GR-23], [decision: D-GR-34], [decision: D-GR-37] |
| D-SF3-13 | Port containers: builder produces `list[PortDefinition]` with `name` as field per D-GR-16. BranchNode outputs use `dict[str, BranchOutputPort]` per D-GR-35. | D-GR-16 is canonical for general ports. D-GR-35 overrides specifically for BranchNode outputs. | [decision: D-GR-16], [decision: D-GR-35] |
| D-SF3-14 | No core checkpoint/resume in SF-2 ABI consumed by SF-3. Helpers may assert `ExecutionHistory` already returned by SF-2, but must NOT require `checkpoint`, `resume`, or equivalent in core declarative surface. | D-GR-24 removes checkpoint/resume from core. ExecutionHistory is observability only. | [decision: D-GR-24] |

**Removed decisions:**

| ID | Was | Reason for Removal |
|----|-----|--------------------|
| ~~D-SF3-5 (old)~~ | "SF-2 adds optional `node_id: str \| None = None` to `AgentRuntime.invoke()`" | **Permanently prohibited** by D-GR-23. The production `invoke()` signature has no `node_id` parameter. Node identity propagates via ContextVar. |
| ~~D-SF3-16~~ | "AgentRuntime.invoke() explicitly owns node routing via `node_id` kwarg" | **Permanently prohibited** by D-GR-23 / REQ-68. This decision never existed in the plan but was referenced in stale artifacts. Explicitly excluded here. |

### Prerequisites from Other Subfeatures

**SF-1 (Declarative Schema) must provide:**
- `iriai_compose.schema` package-level re-exports: `WorkflowConfig`, `AskNode`, `BranchNode`, `PluginNode`, `ErrorNode`, `PhaseDefinition`, `Edge`, `PortDefinition`, `BranchOutputPort`, `NodeDefinition`, `ActorDefinition`, `AgentActorDef`, `HumanActorDef`, `RoleDefinition`, `TypeDefinition`, `MapModeConfig`, `FoldModeConfig`, `LoopModeConfig`, `SequentialModeConfig`
- `iriai_compose.schema.validation` module with: `validate_workflow()`, `validate_type_flow()`, `detect_cycles()` returning `list[ValidationError]`
- `iriai_compose.schema` package-level re-exports (from `yaml_io.py`): `load_workflow()`, `dump_workflow()`
  - Preferred import: `from iriai_compose.schema import load_workflow, dump_workflow`
  - Also valid: `from iriai_compose.schema.yaml_io import load_workflow, dump_workflow`
  - **NOT valid:** `from iriai_compose.schema.io import ...` — no `io.py` module exists
- `ValidationError` dataclass with `code`, `path`, `message`, `context` fields
- Authoritative validation error codes (per D-GR-30, D-GR-35 — stores/switch_function codes removed):
  - `dangling_edge` — Edge references nonexistent node/port
  - `duplicate_node_id` — Two nodes share ID within a phase
  - `duplicate_phase_id` — Two phases share ID
  - `invalid_actor_ref` — Node actor_ref not in `workflow.actors`
  - `invalid_phase_mode_config` — Missing mode-specific config
  - `invalid_hook_edge_transform` — Hook-sourced edge has non-None `transform_fn`
  - `phase_boundary_violation` — `$input`/`$output` wiring errors
  - `cycle_detected` — DAG cycle found
  - `unreachable_node` — No incoming edges, not phase entry
  - `type_mismatch` — Edge source output type ≠ target input type
  - `invalid_branch_config` — Branch missing minimum 2 output ports
  - `invalid_plugin_ref` — plugin_ref not found in declared plugins
  - `missing_output_condition` — BranchNode output port without condition expression (warning-level)
  - `invalid_type_ref` — Type reference not in `workflow.types`
  - `missing_required_field` — Required field missing in lenient loading path
  - `unsupported_root_field` — Stale field at WorkflowConfig root (stores, plugin_instances, etc.)
  - `stale_branch_field` — Rejected BranchNode field (switch_function, condition_type, paths)
  - ~~`invalid_store_ref`~~ — REMOVED: no stores at root per D-GR-30
  - ~~`invalid_store_key_ref`~~ — REMOVED: no stores per D-GR-30
  - ~~`store_type_mismatch`~~ — REMOVED: no stores per D-GR-30
  - ~~`invalid_switch_function_config`~~ — REMOVED: switch_function rejected per D-GR-35
  - ~~`invalid_workflow_io_ref`~~ — REMOVED: no root inputs/outputs per D-GR-30
  - ~~`invalid_transform_ref`~~ — REMOVED: transforms are inline Python on edges per D-21

**SF-2 (DAG Loader & Runner) must provide:**
- `iriai_compose.declarative` module with: `run()`, `RuntimeConfig`, `ExecutionResult`, `ExecutionHistory`, `ExecutionError`, `PluginRegistry`, `PluginRuntime` (ABC), `load_workflow()` (re-export from SF-1)
- `_current_node: ContextVar[str | None]` — published by SF-2 runner, set before Ask-node dispatch, reset after. This is the **sole** mechanism for node identity propagation. **NOT** a parameter on `invoke()`. [D-SF3-5, D-GR-23]
- `run()` signature [D-SF3-12]:
  ```python
  async def run(
      workflow: WorkflowConfig | str | Path,
      config: RuntimeConfig,
      *,
      inputs: dict[str, Any] | None = None,
  ) -> ExecutionResult
  ```
- `RuntimeConfig` dataclass [D-SF3-12]:
  ```python
  @dataclass
  class RuntimeConfig:
      agent_runtime: AgentRuntime
      interaction_runtimes: dict[str, InteractionRuntime] = field(default_factory=dict)
      artifacts: ArtifactStore | None = None           # None → InMemoryArtifactStore
      sessions: SessionStore | None = None             # None → InMemorySessionStore
      context_provider: ContextProvider | None = None   # None → DefaultContextProvider
      plugin_registry: PluginRegistry | None = None     # None → default with builtins
      workspace: Workspace | None = None
      feature: Feature | None = None                    # None → auto-created
  ```
- `ExecutionResult` dataclass:
  - `success: bool`
  - `error: ExecutionError | None`
  - `nodes_executed: list[tuple[str, str]]` — ordering: `(node_id, phase_id)` (node first, phase second)
  - `artifacts: dict[str, Any]`
  - `branch_paths: dict[str, list[str]]` — supports multiple active paths per branch (D-GR-35 non-exclusive fan-out)
  - `cost_summary: dict[str, Any]`
  - `duration_ms: float`
  - `workflow_output: dict[str, Any] | Any | None`
  - `hook_warnings: list[str]`
  - `history: ExecutionHistory | None` — phase-mode metrics per D-GR-2/D-GR-34
- `ExecutionHistory` dataclass:
  - `loop_progress: dict[str, LoopProgress]` — keyed by phase_id
  - `fold_progress: dict[str, FoldProgress]` — keyed by phase_id
  - `map_fan_out: dict[str, int]` — keyed by phase_id, per D-GR-2
  - `error_routes: list[ErrorRoute]` — per D-GR-4
- `AgentRuntime.invoke()` signature — **UNCHANGED from production** [D-SF3-5, D-GR-23]:
  ```python
  @abstractmethod
  async def invoke(
      self,
      role: Role,
      prompt: str,
      *,
      output_type: type[BaseModel] | None = None,
      workspace: Workspace | None = None,
      session_key: str | None = None,
  ) -> str | BaseModel: ...
  ```
  **NO `node_id` parameter. Never. This is permanently frozen.**
- `PluginRuntime` ABC — for `MockPluginRuntime` to extend:
  ```python
  class PluginRuntime(ABC):
      name: str
      @abstractmethod
      async def execute(self, config: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]: ...
  ```
- `pyyaml>=6.0` as a project dependency (not optional)

## Module Structure

```
iriai_compose/
├── testing/
│   ├── __init__.py          # Public API re-exports (single import path)
│   ├── mocks/
│   │   ├── __init__.py      # Re-exports MockAgentRuntime, MockInteractionRuntime, MockPluginRuntime
│   │   ├── agent.py         # MockAgentRuntime, NodeMatcherBuilder, RoleMatcherBuilder
│   │   ├── interaction.py   # MockInteractionRuntime, InteractionNodeMatcher
│   │   ├── plugin.py        # MockPluginRuntime, PluginRefMatcher
│   │   └── types.py         # MockCall, MockExhaustedError, SimulatedCrash, CostMetadata, ResponseConfig
│   ├── assertions.py        # All 13 assertion functions (execution, validation, phase-mode, cost, error)
│   ├── fixtures.py          # WorkflowBuilder, 10 factory functions
│   ├── base.py              # WorkflowTestCase base class
│   ├── snapshot.py          # assert_yaml_round_trip, assert_yaml_equals, yaml_diff
│   ├── runner.py            # run_test (thin wrapper around SF-2 run())
│   └── validation.py        # Re-exports from iriai_compose.schema.validation
├── schema/                  # SF-1 (exists at build time)
│   ├── __init__.py          # Re-exports models, validation, yaml_io functions
│   ├── models.py
│   ├── validation.py        # validate_workflow, validate_type_flow, detect_cycles — OWNED BY SF-1
│   └── yaml_io.py           # load_workflow, dump_workflow — OWNED BY SF-1
└── declarative/             # SF-2 (exists at build time)
    ├── __init__.py           # Re-exports run, RuntimeConfig, ExecutionResult, ExecutionHistory, _current_node
    ├── runner.py            # run(), _current_node ContextVar — OWNED BY SF-2
    ├── config.py            # RuntimeConfig — OWNED BY SF-2
    └── ...
```

### Test Files

```
tests/
├── fixtures/
│   └── workflows/
│       ├── minimal_ask.yaml
│       ├── minimal_branch.yaml
│       ├── minimal_plugin.yaml
│       ├── sequential_phase.yaml
│       ├── map_phase.yaml
│       ├── fold_phase.yaml
│       ├── loop_phase.yaml
│       ├── multi_phase.yaml
│       ├── hook_edge.yaml
│       ├── nested_phases.yaml
│       ├── error_port.yaml
│       ├── gate_and_revise.yaml
│       └── invalid/
│           ├── dangling_edge.yaml
│           ├── cycle_detected.yaml
│           ├── type_mismatch.yaml
│           ├── invalid_actor_ref.yaml
│           ├── duplicate_node_id.yaml
│           ├── invalid_phase_mode_config.yaml
│           ├── invalid_hook_edge_transform.yaml
│           ├── unsupported_root_field.yaml
│           └── stale_branch_field.yaml
├── testing/
│   ├── __init__.py
│   ├── test_mock_agent.py
│   ├── test_mock_interaction.py
│   ├── test_mock_plugin.py
│   ├── test_builder.py
│   ├── test_assertions.py
│   ├── test_validation_reexport.py
│   ├── test_snapshots.py
│   ├── test_runner.py
│   └── test_base.py
└── conftest.py                   # Existing — unchanged
```

## Public API Contract

### `iriai_compose.testing.__init__`

```python
"""iriai_compose.testing — Purpose-built testing module for declarative workflows.

Install: pip install iriai-compose[testing]
Import:  from iriai_compose.testing import MockAgentRuntime, WorkflowBuilder, run_test, ...
"""

# Mock runtimes — fluent no-arg constructors, ContextVar-based node routing [D-SF3-5]
from iriai_compose.testing.mocks import (
    MockAgentRuntime,
    MockInteractionRuntime,
    MockPluginRuntime,
)

# Mock support types
from iriai_compose.testing.mocks.types import (
    MockCall,
    MockExhaustedError,
    SimulatedCrash,
    CostMetadata,
)

# Workflow construction
from iriai_compose.testing.fixtures import (
    WorkflowBuilder,
    minimal_ask_workflow,
    minimal_branch_workflow,
    minimal_plugin_workflow,
    gate_and_revise,
    fold_with_accumulator,
    parallel_fan_out,
    loop_with_exit,
    nested_phases,
    error_port_workflow,
    hook_edge_workflow,
)

# Base class
from iriai_compose.testing.base import WorkflowTestCase

# Execution
from iriai_compose.testing.runner import run_test

# Assertions — execution path
from iriai_compose.testing.assertions import (
    assert_node_reached,
    assert_artifact,
    assert_branch_taken,
    assert_node_count,
    assert_phase_executed,
    assert_hook_warning,
)

# Assertions — phase-mode metrics (wrap result.history per D-GR-2)
from iriai_compose.testing.assertions import (
    assert_loop_iterations,
    assert_fold_items_processed,
    assert_map_fan_out,
)

# Assertions — error routing
from iriai_compose.testing.assertions import assert_error_routed

# Assertions — cost
from iriai_compose.testing.assertions import (
    assert_node_cost,
    assert_total_cost_under,
)

# Assertions — validation (test-specific, operates on list[ValidationError])
from iriai_compose.testing.assertions import assert_validation_error

# Snapshot testing
from iriai_compose.testing.snapshot import assert_yaml_round_trip, assert_yaml_equals

# Re-exports from SF-1 for ergonomic imports
from iriai_compose.testing.validation import validate_workflow, validate_type_flow, detect_cycles

# Re-exports from SF-2 for use in test assertions
from iriai_compose.declarative import ExecutionResult, ExecutionHistory

# Re-export from SF-1 for use in validation assertions
from iriai_compose.schema.validation import ValidationError
```

## Component Specifications

### Mock Support Types

**File:** `iriai_compose/testing/mocks/types.py`

```python
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from iriai_compose.actors import Role


class MockExhaustedError(Exception):
    """Raised when respond_sequence() runs out of responses [D-SF3-10]."""

    def __init__(self, matcher_desc: str, call_count: int) -> None:
        super().__init__(
            f"Mock response sequence exhausted for {matcher_desc} "
            f"after {call_count} calls. Configure more responses or "
            f"check for unexpected loop iterations."
        )
        self.matcher_desc = matcher_desc
        self.call_count = call_count


class SimulatedCrash(Exception):
    """Raised by then_crash() to simulate agent/plugin failure."""

    def __init__(self, message: str = "Simulated crash") -> None:
        super().__init__(message)


@dataclass
class CostMetadata:
    """Token cost metadata attached to mock responses via with_cost()."""
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = "mock"


@dataclass
class MockCall:
    """Recorded invocation on a mock runtime.

    node_id is read from SF-2's _current_node ContextVar during invoke() —
    NOT from an invoke() parameter [D-SF3-5, D-GR-23].
    """
    node_id: str | None
    role: Role
    prompt: str
    output_type: type[BaseModel] | None = None
    response: str | BaseModel | None = None
    cost: CostMetadata | None = None
    matched_by: str = "default"  # "node_id", "role_prompt", "role", "default"
    timestamp: float = field(default_factory=time.time)


@dataclass
class ResponseConfig:
    """Internal: holds a matcher's configured response behavior."""
    response: str | BaseModel | None = None
    sequence: list[str | BaseModel] | None = None
    handler: Callable | None = None
    error: Exception | None = None
    crash: bool = False
    crash_error: Exception | None = None
    cost: CostMetadata | None = None
    call_index: int | None = None  # None = all calls; int = specific call number
    _sequence_pos: int = 0

    def get_response(self, prompt: str, context: dict[str, Any] | None = None) -> str | BaseModel:
        """Resolve the response for this config. Raises on error/crash/exhaustion."""
        if self.crash:
            raise self.crash_error or SimulatedCrash()
        if self.error is not None:
            raise self.error
        if self.handler is not None:
            return self.handler(prompt, context or {})
        if self.sequence is not None:
            if self._sequence_pos >= len(self.sequence):
                raise MockExhaustedError("sequence", self._sequence_pos)
            val = self.sequence[self._sequence_pos]
            self._sequence_pos += 1
            return val
        if self.response is not None:
            return self.response
        return "mock response"
```

### MockAgentRuntime (CMP-7)

**File:** `iriai_compose/testing/mocks/agent.py`

```python
from __future__ import annotations

import re
from typing import Any, Callable

from pydantic import BaseModel

from iriai_compose.runner import AgentRuntime
from iriai_compose.actors import Role
from iriai_compose.workflow import Workspace
from iriai_compose.testing.mocks.types import (
    MockCall, MockExhaustedError, SimulatedCrash, CostMetadata, ResponseConfig,
)


class MockAgentRuntime(AgentRuntime):
    """Fluent mock AgentRuntime for testing declarative workflows.

    No-arg constructor [D-SF3-2]. Node identity read from SF-2's
    _current_node ContextVar — NOT from invoke() parameters [D-SF3-5, D-GR-23].

    4-strategy matcher priority [CP-18]:
      1. node_id match (via ContextVar)     → when_node()
      2. role + prompt regex match          → when_role(name, prompt=r"...")
      3. role-only match                    → when_role(name)
      4. default_response                   → default_response()

    Usage:
        mock = (MockAgentRuntime()
            .when_node("ask_1").respond("node-specific")
            .when_role("pm", prompt=r"review.*").respond("role-prompt")
            .when_role("pm").respond("role-only")
            .default_response("fallback"))
    """

    name = "test-mock"

    def __init__(self) -> None:
        """No-arg constructor. All configuration via fluent methods [D-SF3-2, D-SF3-11]."""
        self._node_matchers: dict[str, ResponseConfig] = {}
        self._role_prompt_matchers: list[tuple[str, str, ResponseConfig]] = []  # (role, pattern, config)
        self._role_matchers: dict[str, ResponseConfig] = {}
        self._default: ResponseConfig | None = None
        self.calls: list[MockCall] = []

    def when_node(self, node_id: str) -> NodeMatcherBuilder:
        """Configure response for a specific node (Strategy 1).

        node_id is matched against _current_node ContextVar at invoke() time.
        """
        return NodeMatcherBuilder(self, node_id=node_id)

    def when_role(self, name: str, *, prompt: str | None = None) -> RoleMatcherBuilder:
        """Configure response for a role (Strategy 2 with prompt, Strategy 3 without).

        prompt: regex pattern matched against the full prompt string.
        """
        return RoleMatcherBuilder(self, role_name=name, prompt_pattern=prompt)

    def default_response(self, response: str | BaseModel) -> MockAgentRuntime:
        """Set fallback response for unmatched calls (Strategy 4)."""
        self._default = ResponseConfig(response=response)
        return self

    async def invoke(
        self,
        role: Role,
        prompt: str,
        *,
        output_type: type[BaseModel] | None = None,
        workspace: Workspace | None = None,
        session_key: str | None = None,
    ) -> str | BaseModel:
        """Resolve mock response. Signature UNCHANGED from AgentRuntime [D-GR-23].

        Reads node_id from SF-2's _current_node ContextVar — NOT from parameters.
        """
        # Import ContextVar from SF-2 runner
        from iriai_compose.declarative.runner import _current_node
        node_id = _current_node.get(None)

        call = MockCall(
            node_id=node_id,
            role=role,
            prompt=prompt,
            output_type=output_type,
        )

        config, matched_by = self._resolve(node_id, role.name, prompt)
        response = config.get_response(prompt)
        call.response = response
        call.matched_by = matched_by
        call.cost = config.cost
        self.calls.append(call)
        return response

    def _resolve(self, node_id: str | None, role_name: str, prompt: str) -> tuple[ResponseConfig, str]:
        # Strategy 1: node_id match
        if node_id and node_id in self._node_matchers:
            return self._node_matchers[node_id], "node_id"
        # Strategy 2: role + prompt regex match
        for r_name, pattern, config in self._role_prompt_matchers:
            if r_name == role_name and re.search(pattern, prompt):
                return config, "role_prompt"
        # Strategy 3: role-only match
        if role_name in self._role_matchers:
            return self._role_matchers[role_name], "role"
        # Strategy 4: default
        if self._default:
            return self._default, "default"
        raise MockConfigurationError(
            node_id=node_id, role_name=role_name, prompt_excerpt=prompt[:80],
            matchers=self._describe_matchers(),
        )

    def _describe_matchers(self) -> list[str]:
        desc = []
        for nid in self._node_matchers:
            desc.append(f"when_node('{nid}')")
        for r_name, pattern, _ in self._role_prompt_matchers:
            desc.append(f"when_role('{r_name}', prompt=r'{pattern}')")
        for r_name in self._role_matchers:
            desc.append(f"when_role('{r_name}')")
        if self._default:
            desc.append("default_response(...)")
        return desc


class MockConfigurationError(Exception):
    """No matcher found for an invocation. Lists ContextVar node_id and configured matchers."""

    def __init__(self, *, node_id: str | None, role_name: str,
                 prompt_excerpt: str, matchers: list[str]) -> None:
        lines = [
            f"No mock matcher for invocation:",
            f"  node_id (from ContextVar): {node_id!r}",
            f"  role: {role_name!r}",
            f"  prompt: {prompt_excerpt!r}...",
            f"Configured matchers ({len(matchers)}):",
        ]
        for m in matchers:
            lines.append(f"  - {m}")
        if not matchers:
            lines.append("  (none — call .default_response() or .when_node()/.when_role())")
        super().__init__("\n".join(lines))


class NodeMatcherBuilder:
    """Fluent builder for node-specific response configuration.

    Terminal methods (respond, respond_sequence, etc.) return the parent
    MockAgentRuntime for chaining. Modifier methods (on_call, with_cost)
    return self for further configuration.
    """

    def __init__(self, parent: MockAgentRuntime, *, node_id: str) -> None:
        self._parent = parent
        self._node_id = node_id
        self._config = ResponseConfig()

    def respond(self, response: str | BaseModel) -> MockAgentRuntime:
        """Return a fixed response when this node is active."""
        self._config.response = response
        self._parent._node_matchers[self._node_id] = self._config
        return self._parent

    def respond_sequence(self, responses: list[str | BaseModel]) -> MockAgentRuntime:
        """Return responses in order. Raises MockExhaustedError when exhausted [D-SF3-10]."""
        self._config.sequence = list(responses)
        self._parent._node_matchers[self._node_id] = self._config
        return self._parent

    def respond_with(self, handler: Callable[[str, dict[str, Any]], str | BaseModel]) -> MockAgentRuntime:
        """Dynamic response via callback. handler(prompt, context) [D-SF3-5a]."""
        self._config.handler = handler
        self._parent._node_matchers[self._node_id] = self._config
        return self._parent

    def raise_error(self, error: Exception) -> MockAgentRuntime:
        """Raise an exception when this node is active."""
        self._config.error = error
        self._parent._node_matchers[self._node_id] = self._config
        return self._parent

    def then_crash(self, error: Exception | None = None) -> MockAgentRuntime:
        """Simulate a crash (SimulatedCrash or provided exception)."""
        self._config.crash = True
        self._config.crash_error = error
        self._parent._node_matchers[self._node_id] = self._config
        return self._parent

    def on_call(self, n: int) -> NodeMatcherBuilder:
        """Configure response for the nth invocation of this node only."""
        self._config.call_index = n
        return self

    def with_cost(self, input_tokens: int, output_tokens: int, *, model: str = "mock") -> NodeMatcherBuilder:
        """Attach cost metadata to the response."""
        self._config.cost = CostMetadata(input_tokens=input_tokens, output_tokens=output_tokens, model=model)
        return self


class RoleMatcherBuilder:
    """Fluent builder for role-based response configuration.

    Same terminal methods as NodeMatcherBuilder.
    """

    def __init__(self, parent: MockAgentRuntime, *, role_name: str, prompt_pattern: str | None) -> None:
        self._parent = parent
        self._role_name = role_name
        self._prompt_pattern = prompt_pattern
        self._config = ResponseConfig()

    def _register(self) -> None:
        if self._prompt_pattern:
            self._parent._role_prompt_matchers.append(
                (self._role_name, self._prompt_pattern, self._config)
            )
        else:
            self._parent._role_matchers[self._role_name] = self._config

    def respond(self, response: str | BaseModel) -> MockAgentRuntime:
        self._config.response = response
        self._register()
        return self._parent

    def respond_sequence(self, responses: list[str | BaseModel]) -> MockAgentRuntime:
        self._config.sequence = list(responses)
        self._register()
        return self._parent

    def respond_with(self, handler: Callable[[str, dict[str, Any]], str | BaseModel]) -> MockAgentRuntime:
        self._config.handler = handler
        self._register()
        return self._parent

    def raise_error(self, error: Exception) -> MockAgentRuntime:
        self._config.error = error
        self._register()
        return self._parent

    def then_crash(self, error: Exception | None = None) -> MockAgentRuntime:
        self._config.crash = True
        self._config.crash_error = error
        self._register()
        return self._parent

    def on_call(self, n: int) -> RoleMatcherBuilder:
        self._config.call_index = n
        return self

    def with_cost(self, input_tokens: int, output_tokens: int, *, model: str = "mock") -> RoleMatcherBuilder:
        self._config.cost = CostMetadata(input_tokens=input_tokens, output_tokens=output_tokens, model=model)
        return self
```

### MockInteractionRuntime (CMP-8)

**File:** `iriai_compose/testing/mocks/interaction.py`

```python
from __future__ import annotations

from typing import Any, Callable

from iriai_compose.runner import InteractionRuntime
from iriai_compose.pending import Pending
from iriai_compose.testing.mocks.types import (
    MockExhaustedError, SimulatedCrash, ResponseConfig,
)


class MockInteractionRuntime(InteractionRuntime):
    """Fluent mock InteractionRuntime for testing.

    No-arg constructor [D-SF3-2]. Node-aware matching uses _current_node
    ContextVar through Pending.node_id (if available) [D-GR-23].

    Usage:
        interaction = (MockInteractionRuntime()
            .when_node("user-gate").approve_sequence([False, True])
            .default_approve(True))
    """

    name = "test-mock-interaction"

    def __init__(self) -> None:
        self._node_configs: dict[str, InteractionConfig] = {}
        self._default_approve: bool | str = True
        self._default_choose: str = ""
        self._default_respond: str = "mock input"
        self.calls: list[Pending] = []

    def when_node(self, node_id: str) -> InteractionNodeMatcher:
        """Configure interaction behavior for a specific node."""
        return InteractionNodeMatcher(self, node_id=node_id)

    def default_approve(self, approve: bool | str) -> MockInteractionRuntime:
        self._default_approve = approve
        return self

    def default_choose(self, choice: str) -> MockInteractionRuntime:
        self._default_choose = choice
        return self

    def default_respond(self, response: str) -> MockInteractionRuntime:
        self._default_respond = response
        return self

    async def resolve(self, pending: Pending) -> str | bool:
        self.calls.append(pending)

        # Read current node from ContextVar
        from iriai_compose.declarative.runner import _current_node
        node_id = _current_node.get(None)

        if node_id and node_id in self._node_configs:
            return self._node_configs[node_id].resolve(pending)

        # Default behavior
        if pending.kind == "approve":
            return self._default_approve
        if pending.kind == "choose":
            return self._default_choose or (pending.options or [""])[0]
        return self._default_respond


class InteractionConfig:
    """Internal: holds per-node interaction configuration."""

    def __init__(self) -> None:
        self.approve_seq: list[bool | str] | None = None
        self.respond_handler: Callable | None = None
        self.script: list[str | bool] | None = None
        self.error: Exception | None = None
        self.crash: bool = False
        self._seq_pos: int = 0

    def resolve(self, pending: Pending) -> str | bool:
        if self.crash:
            raise SimulatedCrash("Simulated interaction crash")
        if self.error:
            raise self.error
        if self.script is not None:
            if self._seq_pos >= len(self.script):
                raise MockExhaustedError("interaction script", self._seq_pos)
            val = self.script[self._seq_pos]
            self._seq_pos += 1
            return val
        if self.approve_seq is not None and pending.kind == "approve":
            if self._seq_pos >= len(self.approve_seq):
                raise MockExhaustedError("approve_sequence", self._seq_pos)
            val = self.approve_seq[self._seq_pos]
            self._seq_pos += 1
            return val
        if self.respond_handler:
            return self.respond_handler(pending)
        return True


class InteractionNodeMatcher:
    def __init__(self, parent: MockInteractionRuntime, *, node_id: str) -> None:
        self._parent = parent
        self._node_id = node_id
        self._config = InteractionConfig()

    def approve_sequence(self, approvals: list[bool | str]) -> MockInteractionRuntime:
        self._config.approve_seq = list(approvals)
        self._parent._node_configs[self._node_id] = self._config
        return self._parent

    def respond_with(self, handler: Callable[[Pending], str | bool]) -> MockInteractionRuntime:
        self._config.respond_handler = handler
        self._parent._node_configs[self._node_id] = self._config
        return self._parent

    def script(self, responses: list[str | bool]) -> MockInteractionRuntime:
        self._config.script = list(responses)
        self._parent._node_configs[self._node_id] = self._config
        return self._parent

    def raise_error(self, error: Exception) -> MockInteractionRuntime:
        self._config.error = error
        self._parent._node_configs[self._node_id] = self._config
        return self._parent

    def then_crash(self, error: Exception | None = None) -> MockInteractionRuntime:
        self._config.crash = True
        self._parent._node_configs[self._node_id] = self._config
        return self._parent
```

### MockPluginRuntime (CMP-9)

**File:** `iriai_compose/testing/mocks/plugin.py`

```python
from __future__ import annotations

from typing import Any, Callable

from iriai_compose.declarative import PluginRuntime
from iriai_compose.testing.mocks.types import (
    MockExhaustedError, SimulatedCrash, CostMetadata, ResponseConfig,
)


class MockPluginRuntime(PluginRuntime):
    """Fluent mock PluginRuntime for testing [CMP-9, D-GR-40].

    No-arg constructor. Routes by plugin_ref.

    Usage:
        plugin_mock = (MockPluginRuntime()
            .when_ref("artifact_write").respond({"status": "ok"})
            .when_ref("checkpoint").respond({"saved": True}))
    """

    name = "test-mock-plugin"

    def __init__(self) -> None:
        self._ref_configs: dict[str, ResponseConfig] = {}
        self._default: ResponseConfig | None = None
        self.calls: list[dict[str, Any]] = []

    def when_ref(self, plugin_ref: str) -> PluginRefMatcher:
        return PluginRefMatcher(self, plugin_ref=plugin_ref)

    def default_response(self, response: dict[str, Any]) -> MockPluginRuntime:
        self._default = ResponseConfig(response=response)
        return self

    async def execute(
        self, plugin_ref: str, config: dict[str, Any], inputs: dict[str, Any],
    ) -> dict[str, Any]:
        from iriai_compose.declarative.runner import _current_node
        node_id = _current_node.get(None)

        call = {"plugin_ref": plugin_ref, "config": config,
                "inputs": inputs, "node_id": node_id}
        self.calls.append(call)

        if plugin_ref in self._ref_configs:
            return self._ref_configs[plugin_ref].get_response(str(inputs))
        if self._default:
            return self._default.get_response(str(inputs))
        return {"status": "mock_ok"}


class PluginRefMatcher:
    def __init__(self, parent: MockPluginRuntime, *, plugin_ref: str) -> None:
        self._parent = parent
        self._ref = plugin_ref
        self._config = ResponseConfig()

    def respond(self, response: dict[str, Any]) -> MockPluginRuntime:
        self._config.response = response
        self._parent._ref_configs[self._ref] = self._config
        return self._parent

    def respond_sequence(self, responses: list[dict[str, Any]]) -> MockPluginRuntime:
        self._config.sequence = list(responses)
        self._parent._ref_configs[self._ref] = self._config
        return self._parent

    def raise_error(self, error: Exception) -> MockPluginRuntime:
        self._config.error = error
        self._parent._ref_configs[self._ref] = self._config
        return self._parent

    def then_crash(self, error: Exception | None = None) -> MockPluginRuntime:
        self._config.crash = True
        self._config.crash_error = error
        self._parent._ref_configs[self._ref] = self._config
        return self._parent

    def with_cost(self, input_tokens: int, output_tokens: int, *, model: str = "mock") -> PluginRefMatcher:
        self._config.cost = CostMetadata(input_tokens=input_tokens, output_tokens=output_tokens, model=model)
        return self
```

### WorkflowBuilder (CMP-139)

**File:** `iriai_compose/testing/fixtures.py`

Updated for D-GR-30 (agent|human, no stores/plugin_instances), D-GR-35 (per-port BranchNode), D-GR-14 (no artifact_key), D-GR-13 (ErrorNode), D-GR-16 (list[PortDefinition]).

Key API changes from previous version:
- `add_actor(name, ..., actor_type="agent"|"human")` — uses `actor_type` discriminator, not `type`
- `add_ask_node(node_id, *, phase, actor_ref, prompt)` — `actor_ref` not `actor`; no `artifact_key` param
- `add_branch_node(node_id, *, phase, outputs)` — `outputs` is `dict[str, str|None]` mapping port name → condition expression (per D-GR-35). `merge_function` optional kwarg.
- `add_error_node(node_id, *, phase, message)` — NEW per D-GR-13
- `add_plugin_node(node_id, *, phase, plugin_ref)` — no `instance_ref` param
- `add_store()` — REMOVED per D-GR-30
- `build()` — constructs WorkflowConfig without `stores` or `plugin_instances` at root
- Phases use `children` not `phases` for nesting, with discriminated-union `mode_config`

All 10 factory functions:
1. `minimal_ask_workflow(actor, prompt, node_id, phase_id)` — single Ask
2. `minimal_branch_workflow(outputs, phase_id)` — Ask → Branch → 2 Ask paths
3. `minimal_plugin_workflow(plugin_ref, phase_id)` — Ask → Plugin
4. `gate_and_revise()` — Ask → Branch(approve/revise) with revise looping back
5. `fold_with_accumulator(collection, reducer)` — Fold phase with Ask inside
6. `parallel_fan_out(item_count)` — Map phase for parallel execution
7. `loop_with_exit(max_iterations)` — Loop phase with exit condition check
8. `nested_phases()` — Sequential phase containing child phases
9. `error_port_workflow()` — Node with error port edge to ErrorNode
10. `hook_edge_workflow()` — Ask with on_end hook edge to Plugin

### WorkflowTestCase

**File:** `iriai_compose/testing/base.py`

```python
from __future__ import annotations

import unittest
from typing import Any

from iriai_compose.testing.mocks import (
    MockAgentRuntime, MockInteractionRuntime, MockPluginRuntime,
)
from iriai_compose.testing.runner import run_test
from iriai_compose.declarative import ExecutionResult


class WorkflowTestCase(unittest.IsolatedAsyncioTestCase):
    """Base test class with auto-created mock runtimes.

    Subclass and configure mocks in setUp(). Call self.execute() to run.
    """

    mock_agent: MockAgentRuntime
    mock_interaction: MockInteractionRuntime
    mock_plugin: MockPluginRuntime

    def setUp(self) -> None:
        self.mock_agent = MockAgentRuntime()
        self.mock_interaction = MockInteractionRuntime()
        self.mock_plugin = MockPluginRuntime()

    async def execute(self, workflow, *, inputs: dict[str, Any] | None = None) -> ExecutionResult:
        return await run_test(
            workflow,
            runtime=self.mock_agent,
            interaction=self.mock_interaction,
            plugin_runtime=self.mock_plugin,
            inputs=inputs,
        )
```

### Assertions (CMP-140 through CMP-145)

**File:** `iriai_compose/testing/assertions.py`

13 assertion functions total. All raise `AssertionError` on failure with diagnostic messages.

**Execution-path assertions** (from previous plan, updated for `(node_id, phase_id)` tuple order):
- `assert_node_reached(result, node_id, *, before=None, after=None)` — uses `result.nodes_executed`; unpacks as `nid, pid = entry` (node first per D-GR-41)
- `assert_artifact(result, key, *, matches=None, equals=_SENTINEL)` — uses `result.artifacts`
- `assert_branch_taken(result, branch, path)` — uses `result.branch_paths`; path can be `str` or checked in `list[str]` (D-GR-35 non-exclusive fan-out: `branch_paths` is `dict[str, list[str]]`)
- `assert_node_count(result, expected)` — `len(result.nodes_executed)`
- `assert_phase_executed(result, phase_id)` — checks `{pid for _, pid in result.nodes_executed}`
- `assert_hook_warning(result, pattern)` — regex match against `result.hook_warnings`

**Phase-mode assertions** (NEW — wrap `result.history` per D-GR-2):
- `assert_loop_iterations(result, phase_id, expected_count)` — reads `result.history.loop_progress[phase_id].completed_iterations`
- `assert_fold_items_processed(result, phase_id, expected_count)` — reads `result.history.fold_progress[phase_id].items_processed`
- `assert_map_fan_out(result, phase_id, expected_branches)` — reads `result.history.map_fan_out[phase_id]`

**Error-routing assertion** (NEW — per D-GR-4):
- `assert_error_routed(result, from_node, to_node)` — checks `result.history.error_routes` for `ErrorRoute(from_id=from_node, to_id=to_node)`

**Cost assertions** (NEW):
- `assert_node_cost(result, node_id, *, max_input_tokens=None, max_output_tokens=None)` — reads `result.cost_summary`
- `assert_total_cost_under(result, max_usd)` — reads `result.cost_summary`

**Validation assertion** (unchanged):
- `assert_validation_error(errors, *, code=None, path=None)` — operates on `list[ValidationError]`

### Snapshot Testing (CMP-148, CMP-149)

**File:** `iriai_compose/testing/snapshot.py`

Unchanged from previous plan except import path verified: `from iriai_compose.schema import load_workflow, dump_workflow` (package-level re-export, NOT `iriai_compose.schema.io`).

Functions: `assert_yaml_round_trip(path)`, `assert_yaml_equals(actual, expected)`, `yaml_diff(a, b)`.

### Test Runner (CMP-147)

**File:** `iriai_compose/testing/runner.py`

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from iriai_compose.declarative import (
    run, RuntimeConfig, ExecutionResult, PluginRegistry,
)
from iriai_compose.schema.models import WorkflowConfig
from iriai_compose.workflow import Feature
from iriai_compose.testing.mocks import (
    MockAgentRuntime, MockInteractionRuntime, MockPluginRuntime,
)


async def run_test(
    workflow: WorkflowConfig | str | Path,
    *,
    runtime: MockAgentRuntime | None = None,
    interaction: MockInteractionRuntime | dict[str, MockInteractionRuntime] | None = None,
    plugin_runtime: MockPluginRuntime | None = None,
    plugins: PluginRegistry | None = None,
    inputs: dict[str, Any] | None = None,
    feature_id: str = "test",
) -> ExecutionResult:
    """Execute a workflow against mock runtimes and return the result.

    Thin wrapper around SF-2's run() [D-SF3-6, D-SF3-12]. Constructs RuntimeConfig
    with provided mocks. Exceptions propagate unmodified.

    Args:
        workflow: WorkflowConfig object, YAML file path, or YAML string.
        runtime: MockAgentRuntime. Defaults to MockAgentRuntime() (no-arg).
        interaction: MockInteractionRuntime or dict. Defaults to auto-approve.
        plugin_runtime: MockPluginRuntime for plugin nodes.
        plugins: Plugin registry. Defaults to None (auto-created with builtins).
        inputs: Workflow input values.
        feature_id: Deterministic Feature ID for test isolation.
    """
    if runtime is None:
        runtime = MockAgentRuntime()

    if interaction is None:
        interaction_runtimes = {"human": MockInteractionRuntime().default_approve(True)}
    elif isinstance(interaction, dict):
        interaction_runtimes = interaction
    else:
        interaction_runtimes = {"human": interaction}

    feature = Feature(
        id=feature_id,
        name=f"Test: {feature_id}",
        slug=feature_id,
        workflow_name="test",
        workspace_id="test",
    )

    config = RuntimeConfig(
        agent_runtime=runtime,
        interaction_runtimes=interaction_runtimes,
        plugin_registry=plugins,
        feature=feature,
    )

    # Exact call per D-SF3-12: run(workflow, config, inputs=inputs)
    return await run(workflow, config, inputs=inputs)
```

### Validation Re-exports

**File:** `iriai_compose/testing/validation.py`

Unchanged from previous plan. Re-exports `validate_workflow`, `validate_type_flow`, `detect_cycles` from `iriai_compose.schema.validation`.

## Implementation Steps

### STEP-20: pyproject.toml `[testing]` Extra + Subpackage Skeleton

**Objective:** Add the `testing` optional dependency group to `pyproject.toml` and create the `iriai_compose/testing/` subpackage with nested `mocks/` subpackage and all module files as importable stubs.

**Scope:**
| Path | Action |
|------|--------|
| `iriai-compose/pyproject.toml` | modify |
| `iriai_compose/testing/__init__.py` | create |
| `iriai_compose/testing/mocks/__init__.py` | create |
| `iriai_compose/testing/mocks/agent.py` | create |
| `iriai_compose/testing/mocks/interaction.py` | create |
| `iriai_compose/testing/mocks/plugin.py` | create |
| `iriai_compose/testing/mocks/types.py` | create |
| `iriai_compose/testing/assertions.py` | create |
| `iriai_compose/testing/fixtures.py` | create |
| `iriai_compose/testing/base.py` | create |
| `iriai_compose/testing/snapshot.py` | create |
| `iriai_compose/testing/runner.py` | create |
| `iriai_compose/testing/validation.py` | create |
| `iriai_compose/pyproject.toml` | read |
| `iriai_compose/tests/conftest.py` | read |

**Instructions:**

1. In `pyproject.toml`, add a `testing` optional dependency group under `[project.optional-dependencies]`:
   ```toml
   testing = [
       "pytest>=7.0",
       "pytest-asyncio>=0.23",
   ]
   ```
   Note: `pyyaml` is already a project dependency (added by SF-2). Do NOT add it to the testing extras.

2. Create `iriai_compose/testing/__init__.py` with the full public API docstring and placeholder comment `# Imports populated in subsequent steps`.

3. Create `iriai_compose/testing/mocks/__init__.py` with placeholder re-exports comment.

4. Create all 10 module files (`mocks/agent.py`, `mocks/interaction.py`, `mocks/plugin.py`, `mocks/types.py`, `assertions.py`, `fixtures.py`, `base.py`, `snapshot.py`, `runner.py`, `validation.py`) with module docstrings and `# Implementation in STEP-N` comments. Each must be importable without error.

5. Verify `pip install -e ".[testing]"` succeeds and `import iriai_compose.testing` works.

**Acceptance Criteria:**
- `pip install -e ".[testing]"` completes without error
- `python -c "import iriai_compose.testing"` succeeds
- `python -c "import iriai_compose.testing.mocks"` succeeds
- `python -c "import iriai_compose.testing.mocks.agent"` succeeds
- All 10 submodule files importable
- Existing tests (`pytest tests/`) pass unchanged

**Counterexamples:**
- Do NOT import SF-1 or SF-2 types in this step — they may not exist yet
- Do NOT add `pyyaml` to testing extras — it is already a core dependency
- Do NOT add `deepdiff` or `ruamel.yaml` [D-SF3-9]
- Do NOT modify `tests/conftest.py`

**Requirement IDs:** REQ-65, REQ-67 | **Journey IDs:** J-18, J-19

---

### STEP-21: MockAgentRuntime (Fluent API + ContextVar)

**Objective:** Implement `MockAgentRuntime` with fluent no-arg constructor, 4-strategy matcher priority, and ContextVar-based node routing. This is the core mock class for declarative workflow testing.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/mocks/types.py` | modify |
| `iriai_compose/testing/mocks/agent.py` | modify |
| `iriai_compose/testing/mocks/__init__.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/runner.py` | read |
| `iriai_compose/actors.py` | read |

**Instructions:**

1. Implement shared types in `mocks/types.py`: `MockCall`, `MockExhaustedError`, `SimulatedCrash`, `CostMetadata`, `ResponseConfig` — exactly as specified in Component Specifications.

2. Implement `MockAgentRuntime(AgentRuntime)` in `mocks/agent.py` exactly as specified. Key behaviors:
   - **No-arg constructor** [D-SF3-2, D-SF3-11]: `__init__(self) -> None`. No `responses` dict, no `default_response` param, no `handler` param.
   - **`invoke()` signature UNCHANGED** [D-SF3-5, D-GR-23]: `invoke(self, role, prompt, *, output_type=None, workspace=None, session_key=None)`. **NO `node_id` parameter.**
   - **ContextVar node routing**: Inside `invoke()`, read `_current_node` from `iriai_compose.declarative.runner` via deferred import. Use for Strategy 1 matching.
   - **4-strategy priority**: (1) `node_id` from ContextVar → `when_node()`, (2) `role.name` + prompt regex → `when_role(name, prompt=...)`, (3) `role.name` only → `when_role(name)`, (4) → `default_response()`
   - **MockConfigurationError** on no match: includes ContextVar-derived node_id, role name, prompt excerpt, list of configured matchers.

3. Implement `NodeMatcherBuilder` and `RoleMatcherBuilder` with terminal methods:
   - `respond(response)` → returns parent `MockAgentRuntime`
   - `respond_sequence(responses)` → returns parent; raises `MockExhaustedError` when exhausted [D-SF3-10]
   - `respond_with(handler)` → handler receives `(prompt, context)` [D-SF3-5a]
   - `raise_error(error)` → returns parent
   - `then_crash(error=None)` → returns parent; raises `SimulatedCrash` or provided exception
   - `on_call(n)` → returns self (modifier, not terminal)
   - `with_cost(input_tokens, output_tokens, *, model)` → returns self (modifier)

4. Update `mocks/__init__.py` to re-export `MockAgentRuntime`.

5. Update `testing/__init__.py` to re-export `MockAgentRuntime`, `MockCall`, `MockExhaustedError`, `SimulatedCrash`, `CostMetadata`.

**Acceptance Criteria:**
- `MockAgentRuntime()` with no args creates a functional mock
- `MockAgentRuntime().when_node("ask_1").respond("done")` configures Strategy 1
- `MockAgentRuntime().when_role("pm", prompt=r"review.*").respond("review")` configures Strategy 2
- `MockAgentRuntime().when_role("pm").respond("role")` configures Strategy 3
- `MockAgentRuntime().default_response("fallback")` configures Strategy 4
- When invoked with `_current_node` ContextVar set to `"ask_1"`, Strategy 1 wins over Strategies 2–4
- `respond_sequence(["a", "b"])` returns "a" then "b" then raises `MockExhaustedError`
- `then_crash()` raises `SimulatedCrash`
- `MockAgentRuntime().calls` records all invocations with `node_id` from ContextVar
- `invoke()` signature has exactly: `role, prompt, *, output_type, workspace, session_key` — **NO `node_id`**

**Counterexamples:**
- Do NOT add `node_id` to `invoke()` signature — **permanently prohibited** [D-GR-23, D-SF3-5]
- Do NOT accept `responses` dict in constructor — use fluent `when_node()`/`when_role()` [D-SF3-2]
- Do NOT accept `default_response` in constructor — use fluent `.default_response()` method [D-SF3-2]
- Do NOT accept `handler` in constructor — use `.respond_with()` on a matcher [D-SF3-2]
- Do NOT name the class `MockRuntime` — it is `MockAgentRuntime` [CMP-7]
- Do NOT inherit from existing `MockAgentRuntime` in `tests/conftest.py`
- Do NOT define a competing ContextVar — read SF-2's `_current_node` [D-SF3-11]

**Requirement IDs:** REQ-65, REQ-67, REQ-70 | **Journey IDs:** J-18

---

### STEP-22: MockInteractionRuntime (Fluent API)

**Objective:** Implement `MockInteractionRuntime` with fluent no-arg constructor and node-aware matching via ContextVar.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/mocks/interaction.py` | modify |
| `iriai_compose/testing/mocks/__init__.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/runner.py` | read |
| `iriai_compose/pending.py` | read |

**Instructions:**

1. Implement `MockInteractionRuntime(InteractionRuntime)` in `mocks/interaction.py` as specified. Key behaviors:
   - **No-arg constructor** [D-SF3-2]: fluent configuration only
   - **`resolve()` signature unchanged**: `resolve(self, pending: Pending) -> str | bool`
   - **ContextVar node routing**: read `_current_node` inside `resolve()` for per-node matching
   - Default behavior: approve=True for approve, first option for choose, "mock input" for respond

2. Implement `InteractionNodeMatcher` with terminal methods:
   - `approve_sequence(approvals)` → sequential approve/reject responses
   - `respond_with(handler)` → dynamic callback receiving `Pending`
   - `script(responses)` → sequential responses for any kind
   - `raise_error(error)` / `then_crash(error=None)`

3. Update re-exports.

**Acceptance Criteria:**
- `MockInteractionRuntime()` with no args auto-approves all pending requests
- `MockInteractionRuntime().when_node("gate").approve_sequence([False, True])` rejects first, approves second
- Calls recorded in `.calls` list
- ContextVar-based node matching works when SF-2 sets `_current_node`

**Counterexamples:**
- Do NOT name the class `MockInteraction` — it is `MockInteractionRuntime` [CMP-8]
- Do NOT accept `approve`, `choose`, `respond` in constructor — use fluent defaults [D-SF3-2]

**Requirement IDs:** REQ-65 | **Journey IDs:** J-18

---

### STEP-23: MockPluginRuntime

**Objective:** Implement `MockPluginRuntime` with fluent no-arg constructor and per-ref matching. This is the NEW mock class required by D-GR-40.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/mocks/plugin.py` | modify |
| `iriai_compose/testing/mocks/__init__.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/declarative/__init__.py` | read |

**Instructions:**

1. Implement `MockPluginRuntime(PluginRuntime)` in `mocks/plugin.py` as specified. Key behaviors:
   - **No-arg constructor** [D-SF3-2]
   - Routes by `plugin_ref` via `when_ref()`
   - Records calls with `node_id` from ContextVar for observability
   - Default returns `{"status": "mock_ok"}`

2. Implement `PluginRefMatcher` with terminal methods:
   - `respond(response)`, `respond_sequence(responses)`, `raise_error(error)`, `then_crash(error=None)`, `with_cost(...)`

3. Update re-exports.

**Acceptance Criteria:**
- `MockPluginRuntime()` with no args returns default response
- `MockPluginRuntime().when_ref("artifact_write").respond({"key": "val"})` routes by ref
- `.calls` records `plugin_ref`, `config`, `inputs`, `node_id`

**Counterexamples:**
- Do NOT use dict-based constructor
- Do NOT name the class `MockPlugin` — it is `MockPluginRuntime` [CMP-9]

**Requirement IDs:** REQ-65 | **Journey IDs:** J-18

---

### STEP-24: WorkflowBuilder + Factory Fixtures

**Objective:** Implement the fluent `WorkflowBuilder` and 10 factory functions. Updated for D-GR-30 (agent|human, no stores), D-GR-35 (per-port BranchNode), D-GR-14 (no artifact_key), D-GR-13 (ErrorNode).

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/fixtures.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/schema/models.py` | read |

**Instructions:**

1. Implement `WorkflowBuilder` in `fixtures.py`. Key changes from previous version:
   - `add_actor(name, ..., actor_type="agent"|"human")` — `actor_type` discriminator per D-GR-30. `"human"` creates `HumanActorDef`, `"agent"` creates `AgentActorDef`.
   - `add_ask_node(node_id, *, phase, actor_ref, prompt)` — field is `actor_ref` not `actor`. No `artifact_key` parameter [D-GR-14].
   - `add_branch_node(node_id, *, phase, outputs, merge_function=None)` — `outputs` is `dict[str, str|None]` mapping port name → condition expression (per D-GR-35). Minimum 2 output ports. Produces `BranchOutputPort` objects.
   - `add_error_node(node_id, *, phase, message)` — NEW per D-GR-13. Terminal node, no outputs.
   - `add_plugin_node(node_id, *, phase, plugin_ref)` — no `instance_ref` param.
   - `add_phase(phase_id, mode, **mode_config)` — mode_config produces discriminated-union `mode_config` field.
   - `build()` — produces `WorkflowConfig` with `children` (not `phases`) for nested phases. No `stores` or `plugin_instances` at root [D-GR-30].
   - **REMOVED:** `add_store()` method [D-GR-30]

2. Implement 10 factory functions as listed in Component Specifications.

3. Implement `WorkflowTestCase` in `base.py`.

4. Update `__init__.py` re-exports.

**Acceptance Criteria:**
- `WorkflowBuilder().add_ask_node("n", phase="p", actor_ref="pm", prompt="x").build()` returns valid `WorkflowConfig`
- `add_branch_node("b", phase="p", outputs={"yes": "data.ok", "no": "not data.ok"})` creates per-port conditions
- `add_error_node("err", phase="p", message="Invalid input")` creates ErrorNode
- `minimal_ask_workflow()` returns `WorkflowConfig` with 1 phase, 1 node, 1 actor
- All 10 factories pass Pydantic validation on `build()`
- No `stores` field on built WorkflowConfig
- No `artifact_key` on any built AskNode

**Counterexamples:**
- Do NOT include `add_store()` method [D-GR-30]
- Do NOT include `artifact_key` parameter on `add_ask_node()` [D-GR-14]
- Do NOT use `type: "interaction"` for human actors — use `actor_type: "human"` [D-GR-30]
- Do NOT use `actor` as AskNode field — use `actor_ref` [D-GR-41]
- Do NOT use `phases` for nested phases — use `children` [D-GR-22]
- Do NOT include `switch_function` on BranchNode [D-GR-35]
- `build()` must NOT call `validate_workflow()` — only Pydantic model-level validation

**Requirement IDs:** REQ-65, REQ-66 | **Journey IDs:** J-18

---

### STEP-25: Validation Re-exports + `assert_validation_error`

**Objective:** Wire up the validation re-export layer from SF-1 and implement `assert_validation_error`. Validation codes aligned to updated SF-1 code list (stores/switch_function codes removed per D-GR-30/D-GR-35).

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/validation.py` | modify |
| `iriai_compose/testing/assertions.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/schema/validation.py` | read |

**Instructions:**

1. Implement `testing/validation.py` as pure re-export module from `iriai_compose.schema.validation`.

2. Implement `assert_validation_error(errors, *, code=None, path=None)` in `assertions.py`:
   - Takes `list[ValidationError]`
   - Requires at least one of `code` or `path`
   - Raises `AssertionError` with diagnostic on no match
   - Docstring includes updated code list (no store/switch_function codes)

3. Update `__init__.py` re-exports.

**Acceptance Criteria:**
- `assert_validation_error([...], code="cycle_detected")` passes for matching error
- `assert_validation_error([], code="dangling_edge")` raises `AssertionError`
- `assert_validation_error([...], code="unsupported_root_field")` works for new stale-field code
- `assert_validation_error([...], code="stale_branch_field")` works for new branch rejection code

**Counterexamples:**
- Do NOT use old code names: `cycle`, `missing_actor`, `duplicate_ids`, `invalid_phase_mode`, `hook_with_transform`
- Do NOT reference removed codes: `invalid_store_ref`, `store_type_mismatch`, `invalid_switch_function_config`, `invalid_workflow_io_ref`
- Do NOT duplicate validation logic — only re-export from SF-1

**Requirement IDs:** REQ-65, REQ-69 | **Journey IDs:** J-18

---

### STEP-26: Execution + Phase-Mode + Cost + Error Assertions

**Objective:** Implement all 12 non-validation assertion functions: 5 execution-path, 3 phase-mode (wrapping `result.history` per D-GR-2), 1 error-routing, 2 cost, 1 hook-warning.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/assertions.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/declarative/__init__.py` | read |

**Instructions:**

1. **Execution-path assertions** (updated tuple order `(node_id, phase_id)` per D-GR-41):
   - `assert_node_reached(result, node_id, *, before=None, after=None)` — extracts node IDs as `[nid for nid, _ in result.nodes_executed]`
   - `assert_artifact(result, key, *, matches=None, equals=_SENTINEL)`
   - `assert_branch_taken(result, branch, path)` — `result.branch_paths` is `dict[str, list[str]]` per D-GR-35; assert `path in result.branch_paths[branch]`
   - `assert_node_count(result, expected)`
   - `assert_phase_executed(result, phase_id)` — `{pid for _, pid in result.nodes_executed}`
   - `assert_hook_warning(result, pattern)` — regex against `result.hook_warnings`

2. **Phase-mode assertions** (NEW — wrap `result.history` per D-GR-2):
   - `assert_loop_iterations(result, phase_id, expected_count)`:
     ```python
     def assert_loop_iterations(result, phase_id, expected_count):
         if result.history is None:
             raise AssertionError("ExecutionResult.history is None — SF-2 did not populate it")
         progress = result.history.loop_progress.get(phase_id)
         if progress is None:
             raise AssertionError(f"No loop progress for phase '{phase_id}'. Available: {list(result.history.loop_progress.keys())}")
         actual = progress.completed_iterations
         if actual != expected_count:
             raise AssertionError(f"Phase '{phase_id}': expected {expected_count} loop iterations, got {actual}")
     ```
   - `assert_fold_items_processed(result, phase_id, expected_count)` — analogous via `result.history.fold_progress`
   - `assert_map_fan_out(result, phase_id, expected_branches)` — via `result.history.map_fan_out`

3. **Error-routing assertion** (NEW — per D-GR-4):
   - `assert_error_routed(result, from_node, to_node)` — checks `result.history.error_routes`

4. **Cost assertions** (NEW):
   - `assert_node_cost(result, node_id, *, max_input_tokens=None, max_output_tokens=None)`
   - `assert_total_cost_under(result, max_usd)`

5. Update `__init__.py` re-exports.

**Acceptance Criteria:**
- `assert_loop_iterations(result, "review", 3)` passes when loop ran 3 times
- `assert_fold_items_processed(result, "collect", 5)` passes when fold processed 5 items
- `assert_map_fan_out(result, "parallel", 4)` passes when map spawned 4 branches
- `assert_error_routed(result, "risky_node", "error_handler")` passes when error was routed
- `assert_branch_taken(result, "gate", "approved")` works with `list[str]` branch_paths values
- Phase-mode assertions raise clear error when `result.history` is None
- All assertions raise `AssertionError` (not custom types) for pytest compatibility

**Counterexamples:**
- Phase-mode assertions must NOT access top-level `ExecutionResult` fields — use `result.history` [D-GR-2]
- `assert_branch_taken` must handle `dict[str, list[str]]` not `dict[str, str]` [D-GR-35]
- Assertions must NOT return bool — they raise on failure, return None on success

**Requirement IDs:** REQ-65, REQ-69 | **Journey IDs:** J-18

---

### STEP-27: `run_test()` Wrapper

**Objective:** Implement the thin convenience wrapper that constructs `RuntimeConfig` and delegates to SF-2's `run()`. Updated to include `MockPluginRuntime` parameter and use correct interaction key `"human"` per D-GR-37.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/runner.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/declarative/__init__.py` | read |
| `iriai_compose/declarative/config.py` | read |

**Instructions:**

1. Implement `run_test()` in `runner.py` as specified in Component Specifications. Key behaviors:
   - Accepts `plugin_runtime: MockPluginRuntime | None` parameter (NEW)
   - `interaction` maps to `interaction_runtimes={"human": interaction}` [D-SF3-12, D-GR-37]
   - RuntimeConfig auto-creates stores when None
   - Delegates to `run(workflow, config, inputs=inputs)` — exact signature
   - Does NOT catch exceptions [D-SF3-6]

2. Update `__init__.py` re-exports.

**Acceptance Criteria:**
- `await run_test(minimal_ask_workflow())` returns `ExecutionResult` with `success=True`
- `await run_test(wf, runtime=MockAgentRuntime().default_response("x"))` uses custom runtime
- `await run_test(wf, plugin_runtime=MockPluginRuntime().when_ref("p").respond({...}))` passes plugin mock
- Exceptions from `run()` propagate unmodified

**Counterexamples:**
- Do NOT catch or wrap exceptions [D-SF3-6]
- Do NOT manually construct InMemoryArtifactStore/SessionStore — pass None [D-SF3-12]
- Do NOT inject `node_id` into any call

**Requirement IDs:** REQ-65, REQ-67 | **Journey IDs:** J-18

---

### STEP-28: Snapshot Testing Functions

**Objective:** Implement `assert_yaml_round_trip` and `assert_yaml_equals` for verifying YAML serialization fidelity.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/snapshot.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/schema/__init__.py` | read |
| `iriai_compose/schema/yaml_io.py` | read |

**Instructions:**

Implement exactly as specified in previous plan's snapshot section. Import from `iriai_compose.schema` (package-level re-export). Use `yaml.safe_load()` for structural comparison. `difflib.unified_diff` for human-readable output.

**Acceptance Criteria:**
- `assert_yaml_round_trip("tests/fixtures/workflows/minimal_ask.yaml")` passes for well-formed fixture
- Key ordering differences do NOT cause false failures
- Import uses `from iriai_compose.schema import load_workflow, dump_workflow`

**Counterexamples:**
- Do NOT use `ruamel.yaml` or `deepdiff` [D-SF3-9]
- Do NOT import from `iriai_compose.schema.io` — no such module exists

**Requirement IDs:** REQ-65 | **Journey IDs:** J-18

---

### STEP-29: YAML Fixture Files + Self-Tests

**Objective:** Create YAML fixtures (valid + invalid, filenames aligned to SF-1 codes), plus comprehensive self-tests for every testing module.

**Scope:**
| Path | Action |
|------|--------|
| `tests/fixtures/workflows/minimal_ask.yaml` | create |
| `tests/fixtures/workflows/minimal_branch.yaml` | create |
| `tests/fixtures/workflows/minimal_plugin.yaml` | create |
| `tests/fixtures/workflows/sequential_phase.yaml` | create |
| `tests/fixtures/workflows/map_phase.yaml` | create |
| `tests/fixtures/workflows/fold_phase.yaml` | create |
| `tests/fixtures/workflows/loop_phase.yaml` | create |
| `tests/fixtures/workflows/multi_phase.yaml` | create |
| `tests/fixtures/workflows/hook_edge.yaml` | create |
| `tests/fixtures/workflows/nested_phases.yaml` | create |
| `tests/fixtures/workflows/error_port.yaml` | create |
| `tests/fixtures/workflows/gate_and_revise.yaml` | create |
| `tests/fixtures/workflows/invalid/dangling_edge.yaml` | create |
| `tests/fixtures/workflows/invalid/cycle_detected.yaml` | create |
| `tests/fixtures/workflows/invalid/type_mismatch.yaml` | create |
| `tests/fixtures/workflows/invalid/invalid_actor_ref.yaml` | create |
| `tests/fixtures/workflows/invalid/duplicate_node_id.yaml` | create |
| `tests/fixtures/workflows/invalid/invalid_phase_mode_config.yaml` | create |
| `tests/fixtures/workflows/invalid/invalid_hook_edge_transform.yaml` | create |
| `tests/fixtures/workflows/invalid/unsupported_root_field.yaml` | create |
| `tests/fixtures/workflows/invalid/stale_branch_field.yaml` | create |
| `tests/testing/__init__.py` | create |
| `tests/testing/test_mock_agent.py` | create |
| `tests/testing/test_mock_interaction.py` | create |
| `tests/testing/test_mock_plugin.py` | create |
| `tests/testing/test_builder.py` | create |
| `tests/testing/test_assertions.py` | create |
| `tests/testing/test_validation_reexport.py` | create |
| `tests/testing/test_snapshots.py` | create |
| `tests/testing/test_runner.py` | create |
| `tests/testing/test_base.py` | create |

**Instructions:**

1. **Valid YAML fixtures** (12 files): Each conforms to SF-1 schema. Uses `actor_type: agent|human` (not `type: interaction`), `children` for nested phases (not `phases`), per-port conditions on BranchNode outputs (not `switch_function`), no `stores`/`plugin_instances` at root, no `artifact_key` on AskNodes. New fixtures: `error_port.yaml` (ErrorNode + error port edge), `gate_and_revise.yaml` (gate pattern).

2. **Invalid YAML fixtures** (9 files): One per validation error code. **New fixtures:**
   - `unsupported_root_field.yaml` — WorkflowConfig with `stores: {}` at root → `unsupported_root_field`
   - `stale_branch_field.yaml` — BranchNode with `switch_function: "..."` → `stale_branch_field`

3. **Self-tests** (9 test files):
   - `test_mock_agent.py`: Fluent API, 4-strategy priority, ContextVar routing, `respond_sequence` exhaustion (`MockExhaustedError`), `then_crash` (`SimulatedCrash`), `with_cost`, `MockConfigurationError` on no match, call recording with `matched_by` field
   - `test_mock_interaction.py`: Fluent API, `approve_sequence`, `script`, default behavior
   - `test_mock_plugin.py`: Fluent API, `when_ref` routing, `respond_sequence`, `then_crash`
   - `test_builder.py`: All 4 node types (Ask/Branch/Plugin/Error), per-port BranchNode conditions, `actor_type` discriminator, no stores/artifact_key, 10 factories, `WorkflowTestCase`
   - `test_assertions.py`: All 13 assertions with passing+failing cases. Phase-mode assertions test `result.history` access. Error routing assertion. Cost assertions. `assert_branch_taken` with `list[str]` paths.
   - `test_validation_reexport.py`: Re-exports point to SF-1, `assert_validation_error` with updated codes
   - `test_snapshots.py`: Round-trip, yaml_equals, import from `iriai_compose.schema`
   - `test_runner.py`: `run_test` with minimal workflow, custom runtime, plugin_runtime, exception propagation
   - `test_base.py`: `WorkflowTestCase` auto-creates mocks, `execute()` delegates correctly

**Acceptance Criteria:**
- All 12 valid fixtures load via `load_workflow()` without errors
- All 9 invalid fixtures produce the expected `ValidationError` code
- `pytest tests/testing/` passes — all self-tests green
- `pytest tests/` passes — existing tests unaffected
- No fixtures use `stores`, `plugin_instances`, `artifact_key`, `switch_function`, or `type: interaction`

**Counterexamples:**
- Do NOT use old fixture names: `cycle.yaml`, `missing_actor.yaml`, `duplicate_ids.yaml`
- Do NOT create fixture for removed codes: `invalid_store_ref`, `invalid_switch_function_config`
- Do NOT use `type: interaction` in actor definitions — use `actor_type: human`
- Do NOT include `switch_function` in valid branch fixtures
- Do NOT import from `iriai_compose.schema.io`
- Invalid fixtures must trigger exactly one error each

**Requirement IDs:** REQ-65, REQ-66, REQ-69 | **Journey IDs:** J-18, J-19

## Interfaces to Other Subfeatures

### SF-1 → SF-3 (Python Import)

SF-3 imports from `iriai_compose.schema` (package-level re-exports from `__init__.py`):
- **Models:** `WorkflowConfig`, `AskNode`, `BranchNode`, `PluginNode`, `ErrorNode`, `PhaseDefinition`, `Edge`, `PortDefinition`, `BranchOutputPort`, `ActorDefinition`, `AgentActorDef`, `HumanActorDef`, `RoleDefinition`, `TypeDefinition`, `MapModeConfig`, `FoldModeConfig`, `LoopModeConfig`, `SequentialModeConfig`, `NodeDefinition`
- **Validation:** `validate_workflow()`, `validate_type_flow()`, `detect_cycles()` → `list[ValidationError]`
- **I/O:** `load_workflow()`, `dump_workflow()` — from `iriai_compose.schema` (re-exported from `yaml_io.py`)
  - Preferred: `from iriai_compose.schema import load_workflow, dump_workflow`
  - Also valid: `from iriai_compose.schema.yaml_io import load_workflow, dump_workflow`
  - **NOT valid:** `from iriai_compose.schema.io import ...` — no `io.py` module
- **Types:** `ValidationError` dataclass

**Entity names per D-GR-41:** `PhaseDefinition` (not `Phase`), `Edge` (not `EdgeDefinition`). Phantom exports (`MapNode`, `FoldNode`, `LoopNode`, `TransformRef`, `HookRef`) do NOT exist. Phase modes are config types on `PhaseDefinition.mode_config`, NOT node types.

### SF-2 → SF-3 (Python Import)

SF-3 imports from `iriai_compose.declarative`:
- **Execution:** `run(workflow, config: RuntimeConfig, *, inputs=None) → ExecutionResult` [D-SF3-12]
- **Config:** `RuntimeConfig` — fields: `agent_runtime`, `interaction_runtimes`, `artifacts`, `sessions`, `context_provider`, `plugin_registry`, `workspace`, `feature`
- **Result:** `ExecutionResult` — fields: `success`, `error`, `nodes_executed: list[tuple[str, str]]` (node_id first, phase_id second), `artifacts`, `branch_paths: dict[str, list[str]]`, `cost_summary`, `duration_ms`, `workflow_output`, `hook_warnings`, `history: ExecutionHistory | None`
- **History:** `ExecutionHistory` — fields: `loop_progress`, `fold_progress`, `map_fan_out`, `error_routes` [D-GR-2]
- **Plugins:** `PluginRegistry`, `PluginRuntime` (ABC)
- **ContextVar:** `_current_node: ContextVar[str | None]` — read-only by SF-3 mocks inside `invoke()`/`resolve()`/`execute()` [D-GR-23]

**ABI invariants [D-GR-23]:**
- `AgentRuntime.invoke(role, prompt, *, output_type, workspace, session_key)` — permanently frozen, NO `node_id`
- Node identity via `_current_node` ContextVar
- Context merge order: `workflow → phase → actor → node`
- Checkpoint/resume excluded from ABI

SF-3's `run_test()` calls `run(workflow, config, inputs=inputs)` — exact signature match [D-SF3-6, D-SF3-12].

### SF-3 → SF-4 (Python Import)

SF-4 (migration test suites) imports from `iriai_compose.testing`:
- `MockAgentRuntime`, `MockInteractionRuntime`, `MockPluginRuntime` — fluent no-arg mock runtimes
- `run_test` — for executing migrated YAML workflows
- `assert_node_reached`, `assert_artifact`, `assert_branch_taken`, `assert_node_count`, `assert_phase_executed` — execution assertions
- `assert_loop_iterations`, `assert_fold_items_processed`, `assert_map_fan_out` — phase-mode assertions
- `assert_error_routed` — error routing assertion
- `assert_node_cost`, `assert_total_cost_under` — cost assertions
- `validate_workflow`, `assert_validation_error` — validation assertions
- `assert_yaml_round_trip` — serialization fidelity
- `WorkflowBuilder` — programmatic workflow construction
- `WorkflowTestCase` — base class with auto-created mocks
- `MockExhaustedError`, `SimulatedCrash` — exception types for error-injection testing

### Existing Test Infrastructure

The existing `tests/conftest.py` with `MockAgentRuntime` and `MockInteractionRuntime` (the old test-only implementations) remains untouched. Existing tests in `tests/test_*.py` continue to use the old mocks. New tests targeting declarative workflows use `iriai_compose.testing` exclusively. No migration of existing tests required.

## Revision Change Summary

### Full Rewrite per D-GR-8, D-GR-23, D-GR-40, D-GR-42

This plan is a complete rewrite of the pre-R18 stale SF-3 artifact. Every section has been updated to comply with D-GR decisions.

### [D-GR-23] invoke() Unchanged + ContextVar Node Routing

| Old (stale plan) | New (this plan) | Affected |
|---|---|---|
| D-SF3-5: "SF-2 adds `node_id: str \| None = None` to `invoke()`" | D-SF3-5: "`invoke()` UNCHANGED. Node identity via `_current_node` ContextVar" | All mock classes, all steps |
| `MockRuntime.invoke(..., node_id=...)` | `MockAgentRuntime.invoke(role, prompt, *, output_type, workspace, session_key)` — reads ContextVar | STEP-21, Component Specs |
| Dict-based response routing `responses={(node_id, role_name): response}` | Fluent `when_node(id).respond(response)` with ContextVar matching | STEP-21, Component Specs |
| D-SF3-16 existed | D-SF3-16 permanently removed | Decision Log |

### [D-GR-40] New Components Added

| Component | Status | Location |
|---|---|---|
| `MockPluginRuntime` (CMP-9) | NEW | `mocks/plugin.py`, STEP-23 |
| `assert_loop_iterations` | NEW | `assertions.py`, STEP-26 |
| `assert_fold_items_processed` | NEW | `assertions.py`, STEP-26 |
| `assert_map_fan_out` | NEW | `assertions.py`, STEP-26 |
| `assert_error_routed` | NEW | `assertions.py`, STEP-26 |
| `assert_node_cost` | NEW | `assertions.py`, STEP-26 |
| `assert_total_cost_under` | NEW | `assertions.py`, STEP-26 |
| `assert_hook_warning` | NEW | `assertions.py`, STEP-26 |
| `WorkflowTestCase` | NEW | `base.py`, STEP-24 |
| 7 additional factory fixtures | NEW | `fixtures.py`, STEP-24 |

### [D-GR-30/D-GR-35/D-GR-14/D-GR-13] Schema Alignment

| Old | New | Decision |
|---|---|---|
| `type: "interaction"` actor | `actor_type: "human"` | D-GR-30 |
| `stores`, `plugin_instances` on root | Removed | D-GR-30 |
| `artifact_key` on AskNode | Removed | D-GR-14 |
| BranchNode with `switch_function` | Per-port conditions on `BranchOutputPort` | D-GR-35 |
| 3 node types (Ask/Branch/Plugin) | 4 node types (+ErrorNode) | D-GR-13 |
| `MockRuntime` class name | `MockAgentRuntime` | CMP-7 |
| `MockInteraction` class name | `MockInteractionRuntime` | CMP-8 |

### [D-SF3-10] Validation Code Updates

Codes removed per D-GR-30/D-GR-35: `invalid_store_ref`, `invalid_store_key_ref`, `store_type_mismatch`, `invalid_switch_function_config`, `invalid_workflow_io_ref`.

Codes added: `unsupported_root_field`, `stale_branch_field`.

Invalid fixture filenames updated accordingly.

### Import Path Verification

All imports use `from iriai_compose.schema import ...` (package-level re-export). No references to non-existent `iriai_compose.schema.io`. `load_workflow`/`dump_workflow` sourced from `yaml_io.py` via `__init__.py`.

### run_test() Signature Alignment [D-SF3-12]

Call is `run(workflow, config, inputs=inputs)`. RuntimeConfig auto-creates stores when None. Feature via `RuntimeConfig.feature`. Interaction maps to `interaction_runtimes={"human": interaction}` per D-GR-37. New `plugin_runtime` parameter for MockPluginRuntime.

## Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-64 | SF-1 schema models not available at SF-3 build time | high | Sequential build order enforced by dependency graph. If SF-1 is delayed, STEP-24+ cannot start. | STEP-24, STEP-25, STEP-28, STEP-29 |
| RISK-65 | SF-2 `run()` / `ExecutionResult` / `ExecutionHistory` not available at SF-3 build time | high | Sequential build order. If SF-2 is delayed, STEP-26, STEP-27 cannot start. STEP-21 (MockAgentRuntime) can proceed since it only depends on existing `AgentRuntime` ABC from `runner.py`. | STEP-26, STEP-27, STEP-29 |
| RISK-66 | SF-2 does not publish `_current_node: ContextVar[str \| None]` or does not set/reset it around Ask-node dispatch | high | Documented as prerequisite [D-SF3-5, D-GR-23]. MockAgentRuntime degrades gracefully: `_current_node.get(None)` returns None, falls through to Strategy 2/3/4. Functional but Strategy 1 matchers ineffective. SF-2 implementer must be notified if ContextVar is missing. | STEP-21, STEP-22, STEP-23 |
| RISK-67 | YAML fixture files don't match SF-1 schema changes during development | low | Fixtures are simple. Self-tests (STEP-29) catch schema drift immediately. | STEP-29 |
| RISK-68 | `WorkflowBuilder.build()` edge assignment heuristic too naive for nested phases | medium | Current heuristic uses node ID prefix matching. If insufficient, add explicit `phase` parameter to `add_edge()`. SF-4 migration tests will surface quickly. | STEP-24 |
| RISK-69 | SF-2's `run()` signature changes | medium | Documented exact contract [D-SF3-12]. If SF-2 changes, only STEP-27 (`runner.py`) needs updating — single callsite. | STEP-27 |
| RISK-70 | SF-2 does not export `ExecutionHistory` from `iriai_compose.declarative.__init__` | medium | D-GR-41 explicitly lists as required export. SF-2 must add to `__init__.py`. If missing, phase-mode assertions (STEP-26) cannot type-annotate `result.history`. | STEP-26, STEP-29 |
| RISK-71 | SF-3 assertion code written against wrong tuple order `(phase_id, node_id)` instead of canonical `(node_id, phase_id)` | high | D-GR-41 explicitly fixes ordering. All assertions unpack as `nid, pid = entry`. Self-tests must verify correct element extraction. | STEP-26, STEP-29 |
| RISK-72 | `run_test()` builds `RuntimeConfig` with `interaction_runtimes={"human": interaction}` but workflow expects different key | medium | Document canonical key is `"human"` per D-GR-37. SF-3 fixtures and factories use `"human"` as interaction key. Key `"default"` is rejected. | STEP-27 |
| RISK-73 | SF-2 `PluginRuntime` ABC not available for `MockPluginRuntime` to extend | medium | MockPluginRuntime depends on SF-2's `PluginRuntime` ABC. If delayed, STEP-23 cannot proceed. Can define a temporary Protocol as stopgap. | STEP-23 |
| RISK-74 | Downstream SF-4 consumers drift back to stale `invoke(..., node_id=...)` or dict-based mock constructors | medium | SF-3→SF-4 interface contract explicitly lists correct class names and fluent API. Self-tests in STEP-29 verify correct usage patterns. | STEP-29 |



---

## Subfeature: Workflow Migration & Litmus Test (workflow-migration)

### SF-4: Workflow Migration & Litmus Test

<!-- SF: workflow-migration -->



## Architecture

### Revision Summary (v3)

Full plan rewrite enforcing D-GR-37, D-GR-18, D-GR-32, D-GR-10, D-GR-5, and D-GR-41:

1. **[D-GR-37] Explicit store PluginNodes — C-4 artifact_key auto-write REJECTED:** Every artifact write uses an explicit `store` PluginNode (plugin_ref: artifact_db, operation: put). No `artifact_key` field on any node. Hosting PluginNodes hook from the store PluginNode's `on_end`, not from producing AskNodes. Node counts restored to pre-C-4 levels: planning ~50, develop ~60, bugfix ~35.

2. **[D-GR-18/D-GR-32] Narrow bridge scope with --declarative flag:** iriai-build-v2 bridge is `_declarative.py` (~100 lines) + `--declarative` CLI flag. Calls `run_declarative()` which loads YAML, maps BootstrappedEnv to RuntimeConfig via Protocol-based adapters, calls SF-2's canonical `run()`. No seed_loader, no plugin HTTP surfaces, no DB seeding.

3. **[D-GR-10] build_env_overrides as config Plugin:** Reclassified from Category B edge transform to 6th plugin type (`config`). Secrets configured on plugin instance, resolved via ConfigPluginAdapter — never read inside expression sandbox.

4. **[D-GR-5] AST-compatible transforms:** All 7 remaining Category B transforms rewritten as import-free, multi-line exec()-compatible code. No `import`, `__import__`, `eval()`, `exec()`, or `compile()` AST nodes. The `id_renumberer` transform uses pure string operations instead of `import re`.

5. **[D-GR-41] Correct schema model names:** All imports updated — `EdgeDefinition` (not Edge), `MapModeConfig`/`FoldModeConfig`/`LoopModeConfig`/`SequentialModeConfig` (not MapConfig/FoldConfig/LoopConfig/SequentialConfig), `TemplateDefinition` (not TemplateRef), `WorkflowCostConfig`/`PhaseCostConfig`/`NodeCostConfig` (not CostConfig). AskNode uses `actor_ref` (not `actor`). ActorDefinition uses `actor_type: agent|human` (not `type: interaction`).

6. **[REQ-73] iriai-build-v2 bridge implementation added:** New STEP-35 implements `_declarative.py` wrapper and `plugins/adapters.py` with 6 Protocol-based adapter classes (Store, Hosting, Mcp, Subprocess, Http, Config) plus `create_plugin_runtimes()` factory.

### System Design Corrections

The system design JSON is already aligned with D-GR-14 (explicit store PluginNodes) and D-GR-41 (correct model names). The following corrections have been applied:

- **iriai_build_v2_runner service description (SVC-45):** `--yaml flag` → `--declarative flag` per D-GR-18 ✅
- **CP-19 heading and step 1:** `--yaml planning.yaml` → `--declarative planning.yaml` ✅
- **D-74 decision:** `--yaml CLI flag` → `--declarative CLI flag` ✅
- **ENT-49 ErrorNode:** Updated per D-GR-36 — 4th atomic node type with `message` (Jinja2 template), `inputs` (dict), NO outputs, NO hooks ✅
- **seed_data_package service:** Mark as out of SF-4 scope per D-GR-32 (seed data is SF-5 responsibility)
- **sf5_database service:** Remove from SF-4 system design (not SF-4 scope)

### Decision Log

| ID | Decision | Rationale | Citation |
|----|----------|-----------|----------|
| D-SF4-1 | Three-category reclassification of the original 12 specialized plugins: (A) infrastructure connectors → general plugin type instances, (B) pure data transforms → inline edge transforms via `transform_fn`, (C) agent-mediated computation → AskNodes | Eliminates 12 bespoke Plugin ABC classes. Category A = side-effect writes to external systems (6 general plugin types including config). Category B = pure functions on data (7 edge transforms — build_env_overrides moved to Category A config plugin per D-GR-10). Category C = LLM reasoning → AskNodes with specific actors. | [decision: Q1 — three-category reclassification], [decision: D-GR-10] |
| D-SF4-2 | Resume semantics are app-layer responsibility, not core SF-2 contract | Per D-GR-24, core declarative runtime does not own checkpoint/resume. iriai-build-v2's existing `FeatureState.completed_phases` handles phase-level skip at the app layer. ExecutionHistory provides observability. | [decision: D-GR-24], [code: iriai-build-v2 interfaces/slack/orchestrator.py:490] |
| D-SF4-3 | Task templates for reusable compound patterns: `gate_and_revise`, `broad_interview`, `interview_gate_review` | Three helpers account for ~410 lines of imperative code reused 15+ times across workflows. Templates are self-contained phases with parameterized inputs/outputs, referenced via `TemplateDefinition` in WorkflowConfig.templates. | [decision: D-GR-20 — TemplateDefinition model] |
| D-SF4-4 | Output types defined as TypeDefinition entries in workflow YAML `types:` sections using JSON Schema Draft 2020-12 | TypeDefinition.schema_def uses JSON Schema. All output models from iriai-build-v2 become `types:` entries. Type references via `output_type` on nodes and `type_ref` on ports enable edge type-checking. | [code: SF-1 PRD — TypeDefinition with schema_def] |
| D-SF4-5 | Behavioral equivalence test suite with ~50-60 tests across 3 workflows | Tests verify the migrated YAML produces equivalent control flow and artifacts to the imperative Python. MockAgentRuntime/MockInteractionRuntime/MockPluginRuntime (SF-3) with scripted responses exercises all branch paths. No live API calls in the default test suite. | [code: SF-3 PRD — MockAgentRuntime, MockInteractionRuntime, MockPluginRuntime] |
| D-SF4-6 | 6 general plugin types replace all Category A infrastructure connectors: `store`, `hosting`, `mcp`, `subprocess`, `http`, `config` | Each type defines a PluginInterface (inputs, outputs, config_schema, operations). The 6th type `config` handles secret/env resolution per D-GR-10. Concrete instances declared in workflow YAML with `plugin_type` + instance config. | [decision: D-GR-10 — config plugin type], [decision: Q2 — general plugin types] |
| D-SF4-7 | **[REVISED per D-GR-37]** All artifact writes use explicit store PluginNodes. No artifact_key auto-write. | D-GR-37 rejected C-4 artifact_key auto-write. Every artifact persistence requires an explicit `store` PluginNode (plugin_ref: artifact_db, config: {operation: put, key: ...}). Reads remain via `context_keys`. Hosting PluginNodes hook from the store PluginNode's `on_end` port. | [decision: D-GR-37 — explicit store PluginNodes], [decision: D-GR-14 — ArtifactPlugin for writes] |
| D-SF4-8 | `develop.yaml` is standalone — no cross-file `$ref` to `planning.yaml` | SF-1 schema supports `$ref` only for intra-file template references. The 6 planning phases in develop.yaml are structurally identical but independently defined. Consistency tests verify structural equivalence. | [code: SF-1 PRD — TemplateDefinition for intra-file refs only] |
| D-SF4-10 | Context hierarchy uses 4-level additive merge: workflow `context_keys` + phase `context_keys` + actor `context_keys` + node `context_keys` | Per D-GR-23, merge order is workflow→phase→actor→node with deduplication. Phase-level context_keys eliminate per-node redundancy. Node identity propagated via ContextVar (not invoke kwarg). | [decision: D-GR-23 — ContextVar propagation, merge order] |
| D-SF4-11 | Tiered context becomes an inline edge transform (`transform_fn`), NOT a plugin | `tiered_context_builder` is a pure data transform: reads decomposition edges + completed artifacts/summaries from fold accumulator, produces formatted context string. No side effects, no imports, no external I/O. Reclassified as Category B. | [decision: D-GR-5 — AST-compatible transforms] |
| D-SF4-12 | **[REVISED per D-GR-37]** Hook edges (`on_end`) fire from the store PluginNode, not from the producing AskNode | With D-GR-37 explicit store PluginNodes: AskNode → store PluginNode (write) → hosting PluginNode (push) via on_end hook. The store PluginNode is the persistence boundary; hosting hooks from its completion. | [decision: D-GR-37], [decision: D-GR-14] |
| D-SF4-13 | `fresh_sessions: true` on loop-mode phases for gate review loops | The `interview_gate_review` pattern requires fresh agent sessions per iteration. `fresh_sessions` is on LoopModeConfig (not on actors). | [code: SF-1 PRD — LoopModeConfig] |
| D-SF4-14 | Test fixtures in `tests/fixtures/workflows/migration/` with `conftest.py` shared fixtures | Migration test fixtures are isolated in a `migration/` subdirectory. Shared fixtures in `tests/migration/conftest.py` provide common setup. | [code: SF-3 PRD — fixtures directory] |
| D-SF4-15 | External service integrations declared as PluginInterface only — instances of general types, no implementation in SF-4 | `preview` (mcp type), `git` (subprocess type), `feedback_notify` (http type) are external service integrations. SF-4 declares instance config but does not implement underlying services. Mock implementations via MockPluginRuntime for testing. | [decision: Q2 — general plugin types with instances] |
| D-SF4-16 | Envelope[T] pattern uses LoopModeConfig condition `"data.complete"` | The universal `envelope_done` predicate in iriai-build-v2 checks `data.complete`. Maps directly to `LoopModeConfig.condition: "data.complete"`. | [code: SF-1 PRD — LoopModeConfig] |
| D-SF4-17 | Workflow invocation is a runner concern — no trigger/listener nodes in the workflow schema | The workflow declares expected input via WorkflowConfig's `input_type` field. How/when invoked is determined by the runner application (CLI, webhook, etc.). The first phase receives input from the runner's invocation context. | [code: SF-1 PRD — WorkflowConfig.input_type] |
| D-SF4-18 | `generate_summary` becomes an AskNode with `actor_ref: summarizer, model: claude-haiku` | Category C reclassification. Producing summaries requires LLM reasoning. AskNode with dedicated summarizer actor. | [decision: Q1 — Category C → AskNode] |
| D-SF4-19 | `extract_revision_plan` becomes an AskNode with extraction prompt | Category C reclassification. Extracting structured RevisionPlan from prose requires LLM reasoning. AskNode with `actor_ref: extractor`. | [decision: Q1 — Category C → AskNode] |
| D-SF4-20 | `sd_converter` becomes a Branch → edge transform / AskNode hybrid | Category C reclassification with Branch optimization. Try JSON parse as Branch condition; success → edge transform renders HTML; failure → AskNode converts markdown. | [decision: Q1 — Category C → Branch + AskNode] |
| D-SF4-21 | **[REVISED per D-GR-10]** Category B edge transforms use `transform_fn` with AST-compatible inline Python. 7 transforms (not 8 — `build_env_overrides` moved to config Plugin). | 7 edge transforms: `handover_compress`, `feedback_formatter`, `id_renumberer`, `collect_files`, `normalize_review_slugs`, `build_task_prompt`, `tiered_context_builder`. All are pure functions, import-free, AST-validated per D-GR-5. `build_env_overrides` is now a config Plugin per D-GR-10. | [decision: D-GR-10 — secrets on plugin instances], [decision: D-GR-5 — AST-validated exec] |
| D-SF4-23 | PluginRegistry API for type + instance metadata registration | SF-2's PluginRegistry exposes `register_type(name, interface)` for PluginInterface metadata and `register_instance(name, config)` for PluginInstanceConfig entries. SF-4 uses these for declaring plugin infrastructure. | [code: SF-2 PRD — PluginRegistry] |
| D-SF4-24 | SF-1's canonical validation error codes used in all test assertions | All SF-4 test assertions use SF-1's canonical error codes: `dangling_edge`, `invalid_actor_ref`, `invalid_plugin_ref`, `invalid_phase_mode_config`, `phase_boundary_violation`, etc. | [code: SF-1 PRD — validation error codes] |
| D-SF4-25 | **[NEW per D-GR-10]** `build_env_overrides` as config Plugin with ConfigPluginAdapter | Secrets and environment variables never read inside transform sandbox. `env_overrides` plugin instance (plugin_type: config) resolves environment variables via ConfigPluginAdapter. Adapter reads from os.environ at bridge construction time. | [decision: D-GR-10], [code: iriai-build-v2 workflows/bugfix/phases/env_setup.py — _build_env_overrides()] |
| D-SF4-26 | **[NEW per D-GR-32/REQ-73]** iriai-build-v2 bridge: `_declarative.py` + `plugins/adapters.py` | Thin bridge (~100 lines): loads YAML via `load_workflow()`, maps BootstrappedEnv services to RuntimeConfig via 6 Protocol-based adapters (`create_plugin_runtimes()` factory), calls `run(workflow, config, inputs=None)`. CLI gains `--declarative` flag per D-GR-18. Additive only — existing imperative workflows untouched. | [decision: D-GR-32 — narrow bridge scope], [decision: D-GR-18 — --declarative flag] |
| D-SF4-27 | **[NEW per D-GR-5]** All transform_fn strings are AST-compatible: no import, no __import__, no eval/exec/compile | id_renumberer rewritten with pure string operations (no `import re`). All transforms assign to `result` variable. Runner exec()'s the code with `data` and `ctx` in local scope, reads `result`. 10,000-char limit, 200 AST node limit, 5s timeout enforced by SF-2 sandbox. | [decision: D-GR-5 — AST-validated exec], [decision: D-GR-17 — 10,000 char limit] |

### Prerequisites from Other Subfeatures

**SF-1 (Declarative Schema) must provide:**
- `iriai_compose.schema` module with: `WorkflowConfig`, `AskNode`, `BranchNode`, `BranchOutputPort`, `PluginNode`, `ErrorNode`, `PhaseDefinition`, `EdgeDefinition`, `PortDefinition`, `NodeDefinition`, `ActorDefinition`, `RoleDefinition`, `TypeDefinition`, `MapModeConfig`, `FoldModeConfig`, `LoopModeConfig`, `SequentialModeConfig`, `StoreDefinition`, `StoreKeyDefinition`, `PluginInterface`, `PluginInstanceConfig`, `TemplateDefinition`, `WorkflowCostConfig`, `PhaseCostConfig`, `NodeCostConfig`, `HookPortEvent`
- `iriai_compose.schema.validation` module with: `validate_workflow()`, `validate_type_flow()`, `detect_cycles()` returning `list[ValidationError]` using canonical error codes [D-SF4-24]
- `iriai_compose.schema.io` module with: `load_workflow()`, `dump_workflow()`
- **EdgeDefinition `transform_fn` support:** optional inline Python string on EdgeDefinition model for Category B transforms (AST-validated per D-GR-5)
- **WorkflowConfig `input_type`:** declares expected input structure for the workflow's first phase [D-SF4-17]
- **No `artifact_key` on NodeBase** — removed per D-GR-37/D-GR-14. All writes via explicit store PluginNodes.
- **BranchOutputPort with per-port `condition`** — per D-GR-35. Non-exclusive fan-out.
- **ActorDefinition `actor_type: agent|human`** — per D-GR-30. No 'interaction'.

**SF-2 (DAG Loader & Runner) must provide:**
- `iriai_compose.declarative` module with: `run()`, `load_workflow()`, `validate()`, `RuntimeConfig`, `ExecutionResult`, `PluginRegistry`
- `run(workflow: WorkflowConfig, config: RuntimeConfig, *, inputs: dict | None = None) -> ExecutionResult` — canonical signature
- `RuntimeConfig` with: `agent_runtime` (singular AgentRuntime), `interaction_runtimes`, `plugin_registry`, `artifacts`, `sessions`, `context_provider`
- `ExecutionResult` with: `success`, `error`, `nodes_executed: list[tuple[str, str]]`, `artifacts`, `branch_paths`, `cost_summary`, `duration_ms`, `workflow_output`, `hook_warnings`, `history`, `phase_metrics`
- `PluginRegistry` with: `register_type(name, interface)`, `register_instance(name, config)`, `has_type()`, `get_type()`, `has_instance()`, `get_instance()`
- **EdgeDefinition transform execution:** runner evaluates `transform_fn` Python strings via AST-validated exec() during edge traversal [D-GR-5]
- **PluginNode execution:** runner dispatches PluginNode operations based on `plugin_type` + instance config via PluginRegistry
- **AgentRuntime.invoke() unchanged** — node identity via ContextVar [D-GR-23]
- **No core checkpoint/resume** [D-GR-24]

**SF-3 (Testing Framework) must provide:**
- `iriai_compose.testing` module with: `MockAgentRuntime`, `MockInteractionRuntime`, `MockPluginRuntime`, `WorkflowBuilder`, `run_test`
- Assertions: `assert_node_reached`, `assert_artifact`, `assert_branch_taken`, `assert_validation_error`, `assert_node_count`, `assert_phase_executed`, `assert_loop_iterations`, `assert_fold_items_processed`, `assert_error_routed`
- Snapshot: `assert_yaml_round_trip`, `assert_yaml_equals`
- Validation re-exports: `validate_workflow`, `validate_type_flow`, `detect_cycles`
- **MockAgentRuntime uses ContextVar-based node matching** — when_node()/when_role()/default_response() fluent API [D-GR-23]

### D-GR Compliance Checklist

| Decision | Status | Notes |
|----------|--------|-------|
| D-GR-5 (AST-validated exec) | ✅ Compliant | All transform_fn strings are import-free, multi-line exec()-compatible code. No import/eval/exec/compile AST nodes. |
| D-GR-10 (build_env_overrides as config Plugin) | ✅ Compliant | Reclassified from Category B edge transform to 6th plugin type (config). ConfigPluginAdapter in adapters.py. |
| D-GR-14 (ArtifactPlugin, no auto-write) | ✅ Compliant | All artifact writes via explicit store PluginNodes. artifact_key removed from all nodes. |
| D-GR-18 (--declarative CLI flag) | ✅ Compliant | Bridge uses --declarative flag, not --yaml. |
| D-GR-22 (nested YAML, edge hooks, /api/schema) | ✅ Compliant | Workflows use phases[].nodes/children nesting. Hooks are edge-based. No separate hooks section. |
| D-GR-23 (invoke unchanged, ContextVar) | ✅ Compliant | AgentRuntime.invoke() signature untouched. Node identity via ContextVar. Context merge: workflow→phase→actor→node. |
| D-GR-24 (no core checkpoint/resume) | ✅ Compliant | No checkpoint/resume dependency on SF-2. Resume is app-layer concern. |
| D-GR-30 (agent|human, closed root set) | ✅ Compliant | All actors use actor_type: agent or human. No 'interaction'. No stores/plugin_instances at root. |
| D-GR-32 (narrow scope: run_declarative/--declarative) | ✅ Compliant | Bridge is _declarative.py + --declarative CLI flag. No seed_loader, no plugin HTTP surfaces. |
| D-GR-35 (per-port BranchNode) | ✅ Compliant | BranchNodes use per-port conditions with non-exclusive fan-out. merge_function for gather. No switch_function. |
| D-GR-37 (explicit store PluginNodes, no artifact_key auto-write) | ✅ Compliant | C-4 fully removed. All artifact writes use explicit store PluginNodes. Hosting hooks from store PluginNode on_end. |
| D-GR-41 (correct schema model names) | ✅ Compliant | EdgeDefinition (not Edge), MapModeConfig/FoldModeConfig/LoopModeConfig (not MapConfig/FoldConfig/LoopConfig), TemplateDefinition (not TemplateRef), WorkflowCostConfig/PhaseCostConfig/NodeCostConfig (not CostConfig). |
| D-GR-36 (ErrorNode as 4th atomic type) | ✅ Compliant | ErrorNode is a 4th atomic node type (alongside Ask, Branch, Plugin). Entity: id, type: error, message (Jinja2 template), inputs (dict), NO outputs, NO hooks. SF-1 exports include ErrorNode. Migration handles error logging patterns from iriai-build-v2 using ErrorNode. |
| D-GR-42 (D-GR canonical authority) | ✅ Compliant | This checklist present. All D-GR decisions treated as hard requirements. |

## Implementation Steps

### STEP-28: Plugin Type Interfaces, Instance Configs, Edge Transform Catalog, and Adapters Module

**Objective:** Define the 6 general plugin type interfaces (store, hosting, mcp, subprocess, http, config) per D-GR-10. Catalog all 7 Category B edge transforms as AST-compatible strings per D-GR-5. Implement the `plugins/adapters.py` module with 6 Protocol-based adapter classes and `create_plugin_runtimes()` factory per REQ-73. Document all Category C → AskNode conversions.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/plugins/__init__.py` | modify |
| `iriai_compose/plugins/types.py` | create |
| `iriai_compose/plugins/instances.py` | create |
| `iriai_compose/plugins/transforms.py` | create |
| `iriai_compose/plugins/adapters.py` | create |
| `iriai_compose/declarative/plugins.py` | read |
| `iriai_compose/schema/nodes.py` | read |

**Instructions:**

1. **`iriai_compose/plugins/types.py`** — 6 general plugin type PluginInterface declarations:

   **`store` type** — KV persistence (explicit writes only per D-GR-37):
   ```python
   STORE_INTERFACE = PluginInterface(
       name="store",
       category="store",
       description="Key-value persistence. All artifact writes require explicit store PluginNodes per D-GR-37.",
       inputs=[PortDefinition(name="data", type_ref="Any", description="Data to persist")],
       outputs=[PortDefinition(name="confirmation", type_ref="StoreWriteResult", description="Write confirmation")],
       config_schema={
           "type": "object",
           "properties": {
               "operation": {"type": "string", "enum": ["put", "delete"]},
               "key": {"type": "string"},
               "namespace": {"type": "string"}
           },
           "required": ["operation", "key"]
       },
       operations=["put", "delete"]
   )
   ```

   **`hosting` type** — Content hosting + URL generation + annotation collection:
   ```python
   HOSTING_INTERFACE = PluginInterface(
       name="hosting", category="service",
       description="Content hosting with URL generation and feedback annotation collection.",
       inputs=[PortDefinition(name="content", type_ref="Any")],
       outputs=[PortDefinition(name="hosted_url", type_ref="string")],
       config_schema={...},  # operation: push|update|collect_annotations|clear_feedback
       operations=["push", "update", "collect_annotations", "clear_feedback"]
   )
   ```

   **`mcp` type** — MCP tool invocation:
   ```python
   MCP_INTERFACE = PluginInterface(
       name="mcp", category="mcp",
       inputs=[PortDefinition(name="tool_input", type_ref="Any")],
       outputs=[PortDefinition(name="tool_output", type_ref="Any")],
       config_schema={...},  # tool_name, server, timeout_ms
       operations=["call_tool"]
   )
   ```

   **`subprocess` type** — CLI command execution:
   ```python
   SUBPROCESS_INTERFACE = PluginInterface(
       name="subprocess", category="cli",
       inputs=[PortDefinition(name="args", type_ref="Any")],
       outputs=[PortDefinition(name="result", type_ref="SubprocessResult")],
       config_schema={...},  # command, subcommand, working_dir, timeout_ms
       operations=["execute"]
   )
   ```

   **`http` type** — Generic HTTP API calls:
   ```python
   HTTP_INTERFACE = PluginInterface(
       name="http", category="service",
       inputs=[PortDefinition(name="payload", type_ref="Any")],
       outputs=[PortDefinition(name="response", type_ref="HttpResponse")],
       config_schema={...},  # method, url, headers, timeout_ms
       operations=["request"]
   )
   ```

   **`config` type [D-GR-10 NEW]** — Secret/environment variable resolution:
   ```python
   CONFIG_INTERFACE = PluginInterface(
       name="config", category="config",
       description="Resolves secrets and environment variables. Secrets configured on plugin instance, never read inside expression sandbox per D-GR-10.",
       inputs=[PortDefinition(name="request", type_ref="Any", description="Resolution request")],
       outputs=[PortDefinition(name="resolved", type_ref="dict", description="Resolved key-value pairs")],
       config_schema={
           "type": "object",
           "properties": {
               "keys": {"type": "array", "items": {"type": "string"}, "description": "Env var names to resolve"},
               "defaults": {"type": "object", "description": "Default values if env vars missing"}
           },
           "required": ["keys"]
       },
       operations=["resolve"]
   )

   ALL_PLUGIN_TYPES = [STORE_INTERFACE, HOSTING_INTERFACE, MCP_INTERFACE, SUBPROCESS_INTERFACE, HTTP_INTERFACE, CONFIG_INTERFACE]
   ```

2. **`iriai_compose/plugins/instances.py`** — 8 instance configurations:
   - `artifact_db` (store) — Primary artifact persistence
   - `artifact_mirror` (store) — Filesystem mirror for local dev
   - `doc_host` (hosting) — iriai-feedback backend
   - `preview` (mcp) — Preview deployment MCP server
   - `playwright` (mcp) — E2E testing via Playwright MCP
   - `git` (subprocess) — Git CLI operations
   - `feedback_notify` (http) — Browser refresh notification
   - `env_overrides` (config) [D-GR-10 NEW] — Environment variable resolution. `config: {keys: ["RAILWAY_TOKEN", "ROBOT_ACCOUNT_GITHUB_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY"]}`

3. **`iriai_compose/plugins/transforms.py`** — 7 Category B edge transforms as AST-compatible Python strings [D-GR-5]:

   All transforms follow exec() sandbox convention: `data` and `ctx` in local scope, assign to `result`.

   **`TIERED_CONTEXT_BUILDER_TRANSFORM`** (~20 lines):
   ```python
   # Reads decomposition + completed artifacts from fold accumulator
   # Produces formatted context string for next fold iteration
   accumulator = ctx.get('accumulator', {})
   completed = accumulator.get('completed_artifacts', {})
   summaries = accumulator.get('completed_summaries', {})
   current = data  # current subfeature slug
   parts = []
   for slug, summary in summaries.items():
       parts.append(f"## {slug} (completed)\n{summary}")
   if completed:
       parts.append(f"\n---\nCompleted: {len(completed)} subfeatures")
   result = '\n\n'.join(parts) if parts else 'No prior context.'
   ```

   **`HANDOVER_COMPRESS_TRANSFORM`** (~15 lines):
   ```python
   doc = data
   if isinstance(doc, dict):
       tasks = doc.get('completed_tasks', [])
       if len(tasks) > 3:
           compressed = [{'summary': t.get('summary', str(t))} for t in tasks[:-3]]
           doc = {**doc, 'completed_tasks': compressed + tasks[-3:]}
   result = doc
   # NEVER touches failed_attempts
   ```

   **`FEEDBACK_FORMATTER_TRANSFORM`** (~12 lines):
   ```python
   verdict = data
   parts = []
   if isinstance(verdict, dict):
       if verdict.get('feedback'):
           parts.append('Reviewer feedback: ' + str(verdict['feedback']))
       if verdict.get('annotations'):
           for key, note in verdict['annotations'].items():
               parts.append('  [' + str(key) + ']: ' + str(note))
   if ctx.get('hosted_url'):
       parts.append('Hosted artifact: ' + str(ctx['hosted_url']))
   result = '\n'.join(parts) if parts else 'No specific feedback provided.'
   ```

   **`ID_RENUMBERER_TRANSFORM`** [D-GR-5 revised — no `import re`] (~20 lines):
   ```python
   text = str(data)
   prefixes = ['REQ', 'AC', 'J', 'STEP', 'CMP']
   for prefix in prefixes:
       search = prefix + '-'
       found = set()
       idx = 0
       while idx < len(text):
           pos = text.find(search, idx)
           if pos == -1:
               break
           start = pos + len(search)
           end = start
           while end < len(text) and text[end].isdigit():
               end += 1
           if end > start:
               found.add(int(text[start:end]))
           idx = pos + 1
       for new_num, old_num in enumerate(sorted(found), 1):
           text = text.replace(prefix + '-' + str(old_num), prefix + '-' + str(new_num))
   result = text
   ```

   **`COLLECT_FILES_TRANSFORM`**, **`NORMALIZE_REVIEW_SLUGS_TRANSFORM`**, **`BUILD_TASK_PROMPT_TRANSFORM`** — Pure string/dict operations, import-free, ~5-15 lines each. Same logic as v1 but with `result = ...` assignment convention.

   **Note:** `build_env_overrides` is NOT in this module — it is a config Plugin per D-GR-10.

4. **`iriai_compose/plugins/adapters.py`** [D-SF4-26 NEW — REQ-73] — Protocol-based bridge adapters:

   ```python
   from typing import Protocol, Any

   class PluginRuntime(Protocol):
       """Protocol that all plugin adapters must satisfy."""
       async def execute(self, operation: str, *, config: dict, inputs: dict) -> Any: ...

   class StorePluginAdapter:
       """Maps ArtifactStore to store plugin operations (put/delete)."""
       def __init__(self, artifact_store: Any, feature_id: str): ...
       async def execute(self, operation: str, *, config: dict, inputs: dict) -> Any:
           if operation == "put":
               await self._store.put(config["key"], inputs["data"], feature=self._feature)
               return {"key": config["key"], "written": True}
           elif operation == "delete":
               await self._store.delete(config["key"], feature=self._feature)
               return {"deleted": True}

   class HostingPluginAdapter:
       """Maps FeedbackService to hosting operations (push/update/collect_annotations)."""
       def __init__(self, feedback_service: Any): ...
       async def execute(self, operation: str, *, config: dict, inputs: dict) -> Any: ...

   class McpPluginAdapter:
       """Maps MCP service (preview/playwright) to call_tool operations."""
       def __init__(self, mcp_service: Any): ...
       async def execute(self, operation: str, *, config: dict, inputs: dict) -> Any: ...

   class SubprocessPluginAdapter:
       """Executes CLI commands via asyncio.create_subprocess_exec."""
       async def execute(self, operation: str, *, config: dict, inputs: dict) -> Any: ...

   class HttpPluginAdapter:
       """Generic HTTP client for webhook/notification endpoints."""
       async def execute(self, operation: str, *, config: dict, inputs: dict) -> Any: ...

   class ConfigPluginAdapter:
       """Resolves environment variables per D-GR-10. Secrets on instance, not in sandbox."""
       def __init__(self, env_vars: dict[str, str]):
           self._env_vars = env_vars
       async def execute(self, operation: str, *, config: dict, inputs: dict) -> Any:
           if operation == "resolve":
               defaults = config.get("defaults", {})
               return {k: self._env_vars.get(k, defaults.get(k, "")) for k in config["keys"]}

   def create_plugin_runtimes(
       services: dict[str, Any],
       feature_id: str,
       artifacts: Any,
   ) -> dict[str, "PluginRuntime"]:
       """D-A4 factory: maps BootstrappedEnv services to PluginRuntime instances.
       Protocol-based structural typing — no consumer type imports."""
       import os
       return {
           "artifact_db": StorePluginAdapter(artifacts, feature_id),
           "artifact_mirror": StorePluginAdapter(services.get("artifact_mirror"), feature_id),
           "doc_host": HostingPluginAdapter(services.get("feedback")),
           "preview": McpPluginAdapter(services.get("preview")),
           "playwright": McpPluginAdapter(services.get("playwright")),
           "git": SubprocessPluginAdapter(),
           "feedback_notify": HttpPluginAdapter(),
           "env_overrides": ConfigPluginAdapter(os.environ.copy()),
       }
   ```

5. **Update `iriai_compose/plugins/__init__.py`**:
   ```python
   from iriai_compose.plugins.types import ALL_PLUGIN_TYPES
   from iriai_compose.plugins.instances import ALL_PLUGIN_INSTANCES
   from iriai_compose.plugins.adapters import create_plugin_runtimes

   def register_plugin_types(registry: "PluginRegistry") -> None:
       for interface in ALL_PLUGIN_TYPES:
           registry.register_type(interface.name, interface)

   def register_instances(registry: "PluginRegistry") -> None:
       for instance in ALL_PLUGIN_INSTANCES:
           registry.register_instance(instance.instance_id, instance)

   def register_builtins(registry: "PluginRegistry") -> None:
       register_plugin_types(registry)
       register_instances(registry)
   ```

**Acceptance Criteria:**
- `from iriai_compose.plugins.types import ALL_PLUGIN_TYPES` returns 6 interfaces (store, hosting, mcp, subprocess, http, config)
- CONFIG_INTERFACE has `operations=["resolve"]` and config_schema with `keys` array [D-GR-10]
- `from iriai_compose.plugins.adapters import create_plugin_runtimes` succeeds
- `create_plugin_runtimes(services, feature_id, artifacts)` returns dict with 8 entries including `env_overrides: ConfigPluginAdapter`
- All 7 edge transform strings pass `compile(code, '<string>', 'exec')` — no SyntaxError
- No transform string contains `import `, `__import__`, `eval(`, `exec(`, or `compile(` [D-GR-5]
- `ID_RENUMBERER_TRANSFORM` uses string.find() instead of `import re` [D-GR-5]
- `build_env_overrides` is NOT in transforms.py — it is a config Plugin [D-GR-10]
- STORE_INTERFACE description does NOT mention artifact_key auto-write [D-GR-37]

**Counterexamples:**
- Do NOT include `build_env_overrides` as an edge transform — it is a config Plugin [D-GR-10]
- Do NOT use `import` statements inside any transform_fn string [D-GR-5]
- Do NOT reference `artifact_key` auto-write anywhere [D-GR-37]
- Do NOT implement Plugin ABC classes for the 6 types — they are PluginInterface declarations [D-SF4-1]
- Do NOT put side-effect operations in edge transforms — transforms must be pure [D-SF4-21]
- Do NOT import consumer types (BootstrappedEnv, etc.) in adapters.py — Protocol-based structural typing [D-SF4-26]

**Requirement IDs:** REQ-34, REQ-53, REQ-54, REQ-73 | **Journey IDs:** J-20, J-22

### STEP-29: Output Type Definitions

**Objective:** Define all output model types as `TypeDefinition` entries for use in workflow YAML `types:` sections. Each type uses JSON Schema Draft 2020-12 format per SF-1 `TypeDefinition.schema_def`. Types are defined as reusable YAML fragments included in each workflow file's `types:` section.

**Scope:**
| Path | Action |
|------|--------|
| `tests/fixtures/workflows/migration/types/common.yaml` | create |
| `tests/fixtures/workflows/migration/types/planning.yaml` | create |
| `tests/fixtures/workflows/migration/types/develop.yaml` | create |
| `tests/fixtures/workflows/migration/types/bugfix.yaml` | create |

**Instructions:**

Same type definitions as v1, with the following name corrections per D-GR-41:
- All type references in YAML use `type_ref` pointing to TypeDefinition names
- `StoreWriteResult` type remains in common.yaml — needed for all explicit `store` PluginNode outputs [D-GR-37]
- PortDefinition uses `type_ref` XOR `schema_def` (not both)

**Acceptance Criteria:**
- All type YAML files parse without error
- Every TypeDefinition has `name`, `description`, `schema_def` (JSON Schema Draft 2020-12)
- `StoreWriteResult` type defined with `key: string`, `timestamp: string`, `written: boolean`
- All `schema_def` values are valid JSON Schema
- No TypeDefinition references stale names (MapConfig, FoldConfig, LoopConfig, Edge, TemplateRef, CostConfig)

**Counterexamples:**
- Do NOT use `MapConfig` — use `MapModeConfig` [D-GR-41]
- Do NOT use `Edge` — use `EdgeDefinition` [D-GR-41]
- Do NOT use `TemplateRef` — use `TemplateDefinition` [D-GR-41]
- Do NOT use `CostConfig` — use `WorkflowCostConfig`/`PhaseCostConfig`/`NodeCostConfig` [D-GR-41]

**Requirement IDs:** REQ-34, REQ-54 | **Journey IDs:** J-20, J-22

### STEP-30: Planning Workflow YAML (`planning.yaml`)

**Objective:** Translate the planning workflow's 6 phases into a single YAML file conforming to the SF-1 schema. **[D-GR-37]** All artifact writes use explicit `store` PluginNodes (plugin_ref: artifact_db). No `artifact_key` field on any node. Hosting PluginNodes hook from store PluginNode's `on_end`. ~50 nodes.

**Scope:**
| Path | Action |
|------|--------|
| `tests/fixtures/workflows/migration/planning.yaml` | create |
| `tests/fixtures/workflows/migration/types/common.yaml` | read |
| `tests/fixtures/workflows/migration/types/planning.yaml` | read |
| `tests/fixtures/workflows/templates/gate_and_revise.yaml` | read |
| `tests/fixtures/workflows/templates/broad_interview.yaml` | read |
| `tests/fixtures/workflows/templates/interview_gate_review.yaml` | read |

**Instructions:**

1. **Workflow-level structure:**
   ```yaml
   schema_version: "1.0"
   name: planning
   description: "Planning workflow — scoping through task planning"
   input_type: "ScopeOutput"
   context_keys: ["project"]

   plugins:
     artifact_db:
       plugin_type: store
       config: { backend: postgres }
     doc_host:
       plugin_type: hosting
       config: { backend: iriai-feedback }

   stores:
     artifacts:
       description: "Primary artifact store"
       keys:
         scope: { type_ref: "ScopeOutput" }
         prd: { type_ref: "PRD" }
         design: { type_ref: "DesignDecisions" }
         plan: { type_ref: "TechnicalPlan" }
         system_design: { type_ref: "SystemDesign" }

   types: { ... }  # Include all common + planning types
   actors: { ... }  # ~10 roles, all with actor_type: agent or human [D-GR-30]
   templates: { ... }  # gate_and_revise, broad_interview, interview_gate_review (TemplateDefinition)
   ```

2. **[D-GR-37] Explicit store PluginNode pattern** — The canonical write pattern for every artifact:

   ```yaml
   # 3 nodes: AskNode → store PluginNode → hosting PluginNode
   - id: scope_resolver
     type: ask
     actor_ref: pm             # [D-GR-41] actor_ref, not actor
     prompt: "Resolve scope..." # [D-GR-41] prompt, not task
     output_type: "ScopeOutput"
   - id: write_scope
     type: plugin
     plugin_ref: artifact_db
     config: { operation: put, key: scope }
   - id: host_scope
     type: plugin
     plugin_ref: doc_host
     config: { operation: push, artifact_key: scope }
     outputs: []  # fire-and-forget
   edges:
     - source: scope_resolver.output
       target: write_scope.input
     - source: write_scope.on_end    # [D-GR-37] Hook from STORE PluginNode
       target: host_scope.input
   ```

3. **Phase definitions (6 phases):**

   **ScopingPhase** (`mode: loop`):
   - First phase — receives workflow input from runner invocation context [D-SF4-17]
   - `mode_config: {mode: loop, condition: "data.complete"}` (Envelope pattern) [D-SF4-16]
   - Nodes: `scope_interviewer` AskNode (actor_ref: user, actor_type: human) → `scope_resolver` AskNode (actor_ref: pm, actor_type: agent) → `write_scope` store PluginNode → `host_scope` hosting PluginNode (hooked from `write_scope.on_end`)
   - BranchNode with per-port conditions: `complete` port (condition: `data.complete`), `continue` port (condition: `not data.complete`) [D-GR-35]
   - Phase-level context_keys: `["project"]`

   **PMPhase** (`mode: sequential`):
   - Sub-phases and nodes:
     1. `broad_interview` TemplateDefinition (bind: {lead_actor: lead_pm, output_type: PRD})
     2. `decompose_and_gate` sub-phase: Ask (decomposer) → `write_decomposition` store PluginNode → `gate_and_revise` TemplateDefinition
     3. `per_subfeature_fold` sub-phase (`mode_config: {mode: fold, collection: "ctx['decomposition'].subfeatures", accumulator_init: "{'completed_artifacts': {}, 'completed_summaries': {}}"}`)
        - EdgeDefinition with `tiered_context_builder` transform_fn → Ask (pm interview) → `write_artifact` store PluginNode → `host_artifact` hosting PluginNode (from `write_artifact.on_end`) → `generate_summary` AskNode → `write_summary` store PluginNode
     4. `integration_review` AskNode (actor_ref: lead_pm)
     5. `compile_artifacts` AskNode → EdgeDefinition with `id_renumberer` transform_fn → `write_compiled` store PluginNode
     6. `interview_gate_review` TemplateDefinition
   - Hosting: PluginNodes hooked from store PluginNode `on_end` ports [D-GR-37]
   - Phase-level context_keys: `["scope", "decomposition"]`

   **DesignPhase**, **ArchitecturePhase** (`mode: sequential`): Same structural pattern as PMPhase with D-GR-37 explicit store writes.

   **PlanReviewPhase** (`mode: loop`):
   - `mode_config: {mode: loop, condition: "all(r['approved'] for r in data.values())", max_iterations: 3}`
   - Map sub-phase (`mode_config: {mode: map, collection: "ctx['review_targets']"}`, 3 parallel reviewer AskNodes)
   - BranchNode per D-GR-35: per-port conditions, non-exclusive fan-out
   - `condition_met` and `max_exceeded` exit ports on LoopModeConfig

   **TaskPlanningPhase** (`mode: sequential`): Same pattern with explicit store writes.

4. **Actor definitions** (~10 roles) — All with `actor_type: agent` or `actor_type: human` [D-GR-30]. No `type: interaction`.

5. **Workflow-level edges** connecting phases in sequence with typed ports. All edges use `EdgeDefinition` (not `Edge`) [D-GR-41].

**Acceptance Criteria:**
- `load_workflow("planning.yaml")` succeeds without errors
- `validate_workflow(config)` returns empty error list
- 6 phases with correct modes: scoping=loop, pm=sequential, design=sequential, architecture=sequential, plan_review=loop, task_planning=sequential
- **[D-GR-37]** Every artifact write uses an explicit `store` PluginNode (plugin_ref: artifact_db)
- **[D-GR-37]** No node has an `artifact_key` field
- **[D-GR-37]** Hosting PluginNodes hook from store PluginNode's `on_end`, NOT from AskNode
- **[D-GR-37]** `artifact_db` IS declared as a plugin instance (required for explicit writes)
- All actors use `actor_type: agent` or `actor_type: human` — no `interaction` [D-GR-30]
- All AskNodes use `actor_ref` (not `actor`) and `prompt` (not `task`) [D-GR-41]
- BranchNodes use per-port `condition` on BranchOutputPort [D-GR-35]
- `tiered_context_builder` and `id_renumberer` as `transform_fn` on EdgeDefinition [D-SF4-21]
- Node count ~50 (explicit store PluginNodes present for every write)
- `fresh_sessions: true` on interview_gate_review LoopModeConfig [D-SF4-13]
- No `artifact_key` field on ANY node [D-GR-37]

**Counterexamples:**
- Do NOT use `artifact_key` on any node — all writes via explicit store PluginNodes [D-GR-37]
- Do NOT hook hosting PluginNodes from AskNode `on_end` — hook from store PluginNode `on_end` [D-GR-37]
- Do NOT use `actor` field — use `actor_ref` [D-GR-41]
- Do NOT use `task` field — use `prompt` [D-GR-41]
- Do NOT use `type: interaction` on actors — use `actor_type: human` [D-GR-30]
- Do NOT use `switch_function` on BranchNodes [D-GR-35]
- Do NOT use compound nodes (FoldNode, MapNode, LoopNode) — use phase modes [D-GR-30]
- Do NOT add resume Branch nodes — resume is app-layer concern [D-GR-24]
- Do NOT put side-effect operations in `transform_fn` [D-SF4-21]
- Do NOT use cross-file `$ref` [D-SF4-8]
- Do NOT add trigger/listener nodes [D-SF4-17]
- Do NOT use `import` in any `transform_fn` [D-GR-5]

**Requirement IDs:** REQ-34, REQ-40, REQ-53, REQ-54 | **Journey IDs:** J-20

### STEP-31: Develop Workflow YAML (`develop.yaml`)

**Objective:** Translate the develop workflow as a standalone YAML file containing all 7 phases. **[D-GR-37]** All artifact writes use explicit store PluginNodes. ~60 nodes. Self-contained — no cross-file refs [D-SF4-8]. Runner handles invocation [D-SF4-17].

**Scope:**
| Path | Action |
|------|--------|
| `tests/fixtures/workflows/migration/develop.yaml` | create |
| `tests/fixtures/workflows/migration/planning.yaml` | read |
| `tests/fixtures/workflows/migration/types/common.yaml` | read |
| `tests/fixtures/workflows/migration/types/develop.yaml` | read |

**Instructions:**

1. **Workflow-level structure:** Same as planning.yaml but with 7 phases, additional types, additional plugins (git, preview), and `artifact_db` declared.

   ```yaml
   schema_version: "1.0"
   name: develop
   description: "Development workflow — planning + implementation"
   input_type: "ScopeOutput"
   context_keys: ["project"]

   plugins:
     artifact_db:
       plugin_type: store
       config: { backend: postgres }
     doc_host:
       plugin_type: hosting
       config: { backend: iriai-feedback }
     git:
       plugin_type: subprocess
       config: { command: git }
     preview:
       plugin_type: mcp
       config: { server: preview-service }
   ```

2. **Planning phases (1-6):** Structurally identical to planning.yaml with explicit store PluginNodes for every artifact write [D-GR-37]. Same modes, same TemplateDefinition bindings, same edge transforms. Independent definitions — no cross-file `$ref` [D-SF4-8].

3. **ImplementationPhase** (phase 7, `mode_config: {mode: loop, condition: "data.user_approved is True"}`):
   - Loop body:
     1. **BranchNode** (`has_feedback`): per-port conditions [D-GR-35] — `has_feedback` port → fix path, `no_feedback` port → DAG execution path
     2. **Fix path**: AskNode (actor_ref: `implementer`) → `write_fix` store PluginNode [D-GR-37]
     3. **DAG execution path** — Fold > Map > Loop nesting (3 levels):
        - **Fold sub-phase** (`mode_config: {mode: fold}`) over `dag.execution_order`:
          - Each group iteration body:
            - EdgeDefinition with `build_task_prompt` transform_fn [D-SF4-21, D-GR-5]
            - **Map sub-phase** (`mode_config: {mode: map}`) — parallel tasks:
              - AskNode (actor_ref: `implementer-g{idx}-t{idx}`) → `write_impl` store PluginNode [D-GR-37]
            - EdgeDefinition with `collect_files` transform_fn
            - Verification AskNode (actor_ref: `smoke_tester`)
            - **Retry loop sub-phase** (`mode_config: {mode: loop, max_iterations: 2}`)
            - EdgeDefinition with `handover_compress` transform_fn
     4. **Sequential chain**: QA AskNode → Code Review AskNode → User Gate (AskNode with actor_type: human)
   - `condition_met` port: user approved → workflow complete

4. **Additional actors:** `implementer`, `qa`/`smoke_tester`, `code_reviewer` — all `actor_type: agent` [D-GR-30].

**Acceptance Criteria:**
- `load_workflow("develop.yaml")` succeeds
- `validate_workflow(config)` returns empty list
- 7 phases present: 6 planning + implementation
- Planning phases structurally match planning.yaml with explicit store PluginNodes [D-GR-37]
- **[D-GR-37]** All artifact writes via store PluginNodes — no `artifact_key` on any node
- **[D-GR-37]** Hosting hooks from store PluginNode `on_end`
- Node count ~60
- `build_task_prompt`, `collect_files`, `handover_compress` as `transform_fn` on EdgeDefinition [D-SF4-21]
- All transforms import-free [D-GR-5]
- BranchNodes use per-port conditions [D-GR-35]
- Fold > Map > Loop nesting correctly structured
- No cross-file `$ref` [D-SF4-8]

**Counterexamples:**
- Do NOT use `artifact_key` on any node [D-GR-37]
- Do NOT hook hosting from AskNode `on_end` — hook from store PluginNode `on_end` [D-GR-37]
- Do NOT use cross-file `$ref` to planning.yaml [D-SF4-8]
- Do NOT branch on iteration count — branch on rejection feedback
- Do NOT use `import` in edge transforms [D-GR-5]
- Do NOT add trigger/listener nodes [D-SF4-17]

**Requirement IDs:** REQ-34, REQ-40, REQ-53, REQ-54 | **Journey IDs:** J-20

### STEP-32: Bugfix Workflow YAML (`bugfix.yaml`)

**Objective:** Translate the bugfix workflow's 8 linear phases into a single YAML file. **[D-GR-37]** All artifact writes use explicit store PluginNodes. **[D-GR-10]** Environment variables resolved via `env_overrides` config Plugin (not edge transform). ~35 nodes.

**Scope:**
| Path | Action |
|------|--------|
| `tests/fixtures/workflows/migration/bugfix.yaml` | create |
| `tests/fixtures/workflows/migration/types/common.yaml` | read |
| `tests/fixtures/workflows/migration/types/bugfix.yaml` | read |

**Instructions:**

1. **Workflow-level structure:**
   ```yaml
   schema_version: "1.0"
   name: bugfix
   description: "Bugfix workflow — intake through cleanup"
   input_type: "BugReport"
   context_keys: ["project"]

   plugins:
     artifact_db:
       plugin_type: store
       config: { backend: postgres }
     doc_host:
       plugin_type: hosting
       config: { backend: iriai-feedback }
     git:
       plugin_type: subprocess
       config: { command: git }
     preview:
       plugin_type: mcp
       config: { server: preview-service }
     playwright:
       plugin_type: mcp
       config: { server: playwright }
     env_overrides:               # [D-GR-10] Config plugin for secrets
       plugin_type: config
       config:
         keys: ["RAILWAY_TOKEN", "ROBOT_ACCOUNT_GITHUB_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY"]
         defaults: {}
   ```

2. **Phase definitions (8 phases):**

   **BugIntakePhase** (`mode_config: {mode: loop, condition: "data.complete"}`):
   - Ask (bug_interviewer, actor_type: human) → `gate_and_revise` TemplateDefinition
   - `write_bug_report` store PluginNode (artifact_db, put, bug_report) [D-GR-37]
   - Hosting: doc_host hooked from `write_bug_report.on_end` [D-GR-37]

   **EnvironmentSetupPhase** (`mode_config: {mode: sequential}`):
   - `subprocess` PluginNode (plugin_ref: git, branch creation) — fire-and-forget
   - **[D-GR-10]** `env_overrides` config PluginNode (plugin_ref: env_overrides, operation: resolve) → resolves env vars
   - `mcp` PluginNode (plugin_ref: preview, deploy) — receives resolved env vars from config plugin output
   - `write_preview_url` store PluginNode (artifact_db, put, preview_url) [D-GR-37] — PluginNode output needs explicit store

   **BaselinePhase** (`mode_config: {mode: sequential}`):
   - `mcp` PluginNode (plugin_ref: playwright, run_e2e)
   - `write_baseline` store PluginNode [D-GR-37] — PluginNode output
   - `smoke_tester` AskNode

   **BugReproductionPhase** (`mode_config: {mode: sequential}`):
   - `bug_reproducer` AskNode (actor_ref: bug_reproducer)
   - `write_reproduction` store PluginNode [D-GR-37]

   **DiagnosisAndFixPhase** (`mode_config: {mode: loop, condition: "not data.reproduced", max_iterations: 3}`):
   - Loop body:
     1. Map sub-phase (2 parallel RCA analyst AskNodes with distinct prompts)
     2. `bug_fixer` AskNode → `write_fix` store PluginNode [D-GR-37]
     3. `subprocess` PluginNodes (git commit, git push) — fire-and-forget
     4. `mcp` PluginNode (preview redeploy) — fire-and-forget
     5. `bug_reproducer` AskNode (verification)
     6. BranchNode per D-GR-35: `not_reproduced` port (condition: `not data.reproduced`) → condition_met, `still_reproducing` port (condition: `data.reproduced`) → EdgeDefinition with `handover_compress` transform_fn → loop back
   - `max_exceeded` port routes to ApprovalPhase with failure context

   **RegressionPhase** (`mode_config: {mode: sequential}`):
   - `mcp` PluginNode (playwright E2E) → `smoke_tester` AskNode

   **ApprovalPhase** (`mode_config: {mode: sequential}`):
   - Gate AskNode (actor_ref: approver, actor_type: human)

   **CleanupPhase** (`mode_config: {mode: sequential}`):
   - `mcp` PluginNode (preview teardown) — fire-and-forget

**Acceptance Criteria:**
- `load_workflow("bugfix.yaml")` succeeds
- `validate_workflow(config)` returns empty list
- 8 phases in correct order
- **[D-GR-37]** All artifact writes via explicit store PluginNodes — no `artifact_key` on any node
- **[D-GR-37]** Hosting hooks from store PluginNode `on_end`
- **[D-GR-10]** `env_overrides` config PluginNode resolves environment variables (not edge transform)
- **[D-GR-10]** No `build_env_overrides` edge transform in bugfix.yaml
- Node count ~35
- `artifact_db` declared as plugin (required for explicit writes) [D-GR-37]
- Parallel RCA uses map sub-phase with 2 analysts
- DiagnosisAndFixPhase has `max_iterations: 3`
- BranchNodes use per-port conditions [D-GR-35]
- All transforms import-free [D-GR-5]

**Counterexamples:**
- Do NOT use `artifact_key` on any node [D-GR-37]
- Do NOT use `build_env_overrides` as edge transform — use config PluginNode [D-GR-10]
- Do NOT read os.environ inside any transform_fn sandbox [D-GR-10]
- Do NOT hook hosting from AskNode `on_end` [D-GR-37]
- Do NOT give both RCA analysts identical prompts
- Do NOT compress `failed_attempts` in handover_compress
- Do NOT use `import` in transforms [D-GR-5]

**Requirement IDs:** REQ-34, REQ-40, REQ-53, REQ-54 | **Journey IDs:** J-20

### STEP-33: Task Template YAML Files

**Objective:** Create three actor-centric task template YAML files using TemplateDefinition [D-GR-41]. **[D-GR-37]** Templates use explicit store PluginNodes for all artifact writes. Hosting hooks from store PluginNode `on_end`. All transform_fn strings are AST-compatible [D-GR-5].

**Scope:**
| Path | Action |
|------|--------|
| `tests/fixtures/workflows/templates/gate_and_revise.yaml` | create |
| `tests/fixtures/workflows/templates/broad_interview.yaml` | create |
| `tests/fixtures/workflows/templates/interview_gate_review.yaml` | create |

**Instructions:**

1. **`gate_and_revise.yaml`** — Approval loop pattern with explicit store PluginNodes:
   ```yaml
   name: gate_and_revise
   description: "Actor-centric template: approval loop with revision on rejection"
   parameters:
     artifact_key: { type: string }
     producer_actor: { type: string }
     approver_actor: { type: string }
     output_type: { type: string }
     label: { type: string }
   phase:
     mode_config:
       mode: loop
       condition: "data is True or data.approved is True"
     nodes:
       - id: present_artifact
         type: ask
         actor_ref: "{{ approver_actor }}"
         prompt: "Review the {{ label }}. Approve or reject with feedback."
         context_keys: ["{{ artifact_key }}"]
       - id: revise
         type: ask
         actor_ref: "{{ producer_actor }}"
         prompt: "Revise based on feedback: {{ $input }}"
         output_type: "{{ output_type }}"
       - id: write_revision          # [D-GR-37] Explicit store PluginNode
         type: plugin
         plugin_ref: artifact_db
         config: { operation: put, key: "{{ artifact_key }}" }
       - id: host_revision
         type: plugin
         plugin_ref: doc_host
         config: { operation: push, artifact_key: "{{ artifact_key }}" }
         outputs: []
     edges:
       - source: "$input"
         target: "present_artifact.input"
       - source: "present_artifact.approved"
         target: "$output"
       - source: "present_artifact.rejected"
         target: "revise.input"
         transform_fn: |
           verdict = data
           parts = []
           if isinstance(verdict, dict):
               if verdict.get('feedback'):
                   parts.append('Reviewer feedback: ' + str(verdict['feedback']))
               if verdict.get('annotations'):
                   for key, note in verdict['annotations'].items():
                       parts.append('  [' + str(key) + ']: ' + str(note))
           if ctx.get('hosted_url'):
               parts.append('Hosted artifact: ' + str(ctx['hosted_url']))
           result = '\n'.join(parts) if parts else 'No specific feedback provided.'
       - source: "revise.output"
         target: "write_revision.input"   # [D-GR-37] Write goes to store PluginNode
       - source: "write_revision.on_end"   # [D-GR-37] Hook from store, not AskNode
         target: "host_revision.input"
       - source: "write_revision.output"
         target: "$output"
   ```

2. **`broad_interview.yaml`** — Single-actor interview-to-completion with explicit store:
   ```yaml
   name: broad_interview
   parameters:
     lead_actor: { type: string }
     output_type: { type: string }
     artifact_key: { type: string }
     initial_prompt: { type: string }
   phase:
     mode_config:
       mode: loop
       condition: "data.complete"
     nodes:
       - id: interview_ask
         type: ask
         actor_ref: "{{ lead_actor }}"
         prompt: "{{ initial_prompt }}\n\nPrevious context: {{ $input }}"
         output_type: "Envelope"
       - id: write_artifact           # [D-GR-37] Explicit store PluginNode
         type: plugin
         plugin_ref: artifact_db
         config: { operation: put, key: "{{ artifact_key }}" }
       - id: host_artifact
         type: plugin
         plugin_ref: doc_host
         config: { operation: push, artifact_key: "{{ artifact_key }}" }
         outputs: []
     edges:
       - source: "$input"
         target: "interview_ask.input"
       - source: "interview_ask.output"
         target: "write_artifact.input"   # [D-GR-37]
       - source: "write_artifact.on_end"   # [D-GR-37]
         target: "host_artifact.input"
       - source: "interview_ask.output"
         target: "$output"
   ```

3. **`interview_gate_review.yaml`** — Compiled artifact review with explicit store and AST-compatible id_renumberer:
   ```yaml
   name: interview_gate_review
   parameters:
     lead_actor: { type: string }
     compiler_actor: { type: string }
     compiled_key: { type: string }
     output_type: { type: string }
   phase:
     mode_config:
       mode: loop
       condition: "data.approved is True"
       fresh_sessions: true   # [D-SF4-13]
     nodes:
       - id: review_ask
         type: ask
         actor_ref: "{{ lead_actor }}"
         prompt: "Review the compiled {{ output_type }}."
         context_keys: ["{{ compiled_key }}"]
       - id: extract_revisions
         type: ask
         actor_ref: extractor
         prompt: "Extract structured revision plan..."
         output_type: "RevisionPlan"
       - id: revision_fold
         type: phase
         mode_config:
           mode: map
           collection: "ctx['revision_plan'].requests"
       - id: recompile
         type: ask
         actor_ref: "{{ compiler_actor }}"
         prompt: "Recompile {{ output_type }} incorporating all revisions."
         output_type: "{{ output_type }}"
       - id: write_compiled            # [D-GR-37] Explicit store PluginNode
         type: plugin
         plugin_ref: artifact_db
         config: { operation: put, key: "{{ compiled_key }}" }
       - id: host_compiled
         type: plugin
         plugin_ref: doc_host
         config: { operation: push, artifact_key: "{{ compiled_key }}" }
         outputs: []
     edges:
       - source: "$input"
         target: "review_ask.input"
       - source: "review_ask.approved"
         target: "$output"
       - source: "review_ask.needs_revision"
         target: "extract_revisions.input"
       - source: "extract_revisions.output"
         target: "revision_fold.input"
       - source: "revision_fold.output"
         target: "recompile.input"
         transform_fn: |             # [D-GR-5] AST-compatible, no import
           text = str(data)
           prefixes = ['REQ', 'AC', 'J', 'STEP', 'CMP']
           for prefix in prefixes:
               search = prefix + '-'
               found = set()
               idx = 0
               while idx < len(text):
                   pos = text.find(search, idx)
                   if pos == -1:
                       break
                   start = pos + len(search)
                   end = start
                   while end < len(text) and text[end].isdigit():
                       end += 1
                   if end > start:
                       found.add(int(text[start:end]))
                   idx = pos + 1
               for new_num, old_num in enumerate(sorted(found), 1):
                   text = text.replace(prefix + '-' + str(old_num), prefix + '-' + str(new_num))
           result = text
       - source: "recompile.output"
         target: "write_compiled.input"  # [D-GR-37]
       - source: "write_compiled.on_end"  # [D-GR-37]
         target: "host_compiled.input"
       - source: "write_compiled.output"
         target: "$output"
   ```

**Acceptance Criteria:**
- All 3 template files parse as valid YAML
- **[D-GR-37]** Every template has explicit `store` PluginNodes for artifact writes — no `artifact_key` on AskNodes
- **[D-GR-37]** Hosting hooks from store PluginNode `on_end`, not from AskNode
- **[D-GR-5]** id_renumberer transform uses string.find() — no `import re`
- **[D-GR-5]** All transform_fn strings are import-free
- `gate_and_revise`: feedback_formatter as EdgeDefinition transform_fn
- `broad_interview`: loop-mode with `condition: "data.complete"`
- `interview_gate_review`: `fresh_sessions: true` on LoopModeConfig [D-SF4-13]

**Counterexamples:**
- Do NOT use `artifact_key` on AskNodes [D-GR-37]
- Do NOT hook hosting from AskNode `on_end` [D-GR-37]
- Do NOT use `import re` or any import in transform_fn [D-GR-5]
- Do NOT omit `fresh_sessions: true` on interview_gate_review [D-SF4-13]

**Requirement IDs:** REQ-34, REQ-40, REQ-53, REQ-54 | **Journey IDs:** J-20

### STEP-34: Behavioral Equivalence Test Suite (~55-60 Tests)

**Objective:** Write comprehensive behavioral equivalence tests verifying D-GR-37 explicit store PluginNode patterns, D-GR-10 config Plugin usage, D-GR-5 AST-compatible transforms, and D-GR-41 correct model names. Uses SF-3 MockAgentRuntime/MockInteractionRuntime/MockPluginRuntime with ContextVar-based node matching [D-GR-23].

**Scope:**
| Path | Action |
|------|--------|
| `tests/migration/__init__.py` | create |
| `tests/migration/conftest.py` | create |
| `tests/migration/test_planning.py` | create |
| `tests/migration/test_develop.py` | create |
| `tests/migration/test_bugfix.py` | create |
| `tests/migration/test_yaml_roundtrip.py` | create |
| `tests/migration/test_plugin_instances.py` | create |
| `tests/migration/test_edge_transforms.py` | create |
| `tests/migration/test_bridge.py` | create |

**Instructions:**

1. **`tests/migration/conftest.py`** — Shared fixtures:

   ```python
   import pytest
   from pathlib import Path
   from iriai_compose.testing import MockAgentRuntime, MockInteractionRuntime, MockPluginRuntime, run_test
   from iriai_compose.declarative import load_workflow, PluginRegistry
   from iriai_compose.plugins import register_plugin_types, register_instances

   FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "workflows" / "migration"

   @pytest.fixture
   def planning_workflow():
       return load_workflow(FIXTURES_DIR / "planning.yaml")

   @pytest.fixture
   def develop_workflow():
       return load_workflow(FIXTURES_DIR / "develop.yaml")

   @pytest.fixture
   def bugfix_workflow():
       return load_workflow(FIXTURES_DIR / "bugfix.yaml")

   @pytest.fixture
   def plugin_registry():
       registry = PluginRegistry()
       register_plugin_types(registry)   # 6 types including config
       register_instances(registry)      # 8 instances including env_overrides
       return registry
   ```

2. **`tests/migration/test_plugin_instances.py`** (~10 tests):

   **Registry validation (4 tests):**
   - `test_six_plugin_types_registered` — `registry.has_type("store")`, `has_type("config")` etc. for all 6 types [D-GR-10]
   - `test_config_type_has_resolve_operation` — `registry.get_type("config").operations == ["resolve"]` [D-GR-10]
   - `test_eight_instances_registered` — all 8 including `env_overrides` [D-GR-10]
   - `test_instance_references_valid_type` — every instance's `plugin_type` matches a registered type

   **[D-GR-37] Explicit store pattern validation (3 tests):**
   - `test_all_artifact_writes_use_store_plugin_nodes` — scan all 3 workflows: every artifact write goes through a PluginNode with `plugin_ref: artifact_db` and `config.operation: put`
   - `test_no_artifact_key_on_any_node` — scan all 3 workflows: NO node has an `artifact_key` field [D-GR-37]
   - `test_hosting_hooks_from_store_not_ask` — all hosting PluginNodes' input edges originate from a store PluginNode's `on_end` port, NOT from AskNode `on_end` [D-GR-37]

   **[D-GR-10] Config plugin validation (3 tests):**
   - `test_bugfix_env_overrides_is_config_plugin` — bugfix.yaml has `env_overrides` PluginNode with `plugin_type: config`
   - `test_no_build_env_overrides_transform` — no workflow has `build_env_overrides` as a `transform_fn` string [D-GR-10]
   - `test_env_overrides_config_has_keys` — `env_overrides` instance config specifies `keys` array

3. **`tests/migration/test_edge_transforms.py`** (~10 tests):

   **[D-GR-5] AST compatibility (3 tests):**
   - `test_all_transforms_compile` — all 7 transform strings pass `compile(code, '<string>', 'exec')`
   - `test_no_transforms_use_import` — no transform contains `import ` or `__import__`
   - `test_no_transforms_use_dangerous_builtins` — no `eval(`, `exec(`, `compile(` in any transform

   **Transform correctness (7 tests):** One per transform — `tiered_context_builder`, `handover_compress`, `feedback_formatter`, `id_renumberer`, `collect_files`, `normalize_review_slugs`, `build_task_prompt`. Each verifies correct output given test input data.

   **Purity tests (2 tests):**
   - `test_handover_compress_never_touches_failed_attempts`
   - `test_all_transforms_are_pure` — no side effects detected

4. **`tests/migration/test_planning.py`** (~15 tests):

   **Schema validation (5 tests):**
   - `test_planning_loads_without_error`
   - `test_planning_validates_cleanly`
   - `test_planning_has_six_phases`
   - `test_planning_actor_refs_resolve` — no `invalid_actor_ref` errors [D-SF4-24]
   - `test_planning_plugin_refs_resolve` — no `invalid_plugin_ref` errors

   **[D-GR-37] Store pattern (3 tests):**
   - `test_all_planning_artifacts_have_store_plugin_nodes` — scope, prd, design, plan, system_design each have a corresponding store PluginNode
   - `test_no_artifact_key_in_planning` — no AskNode has `artifact_key`
   - `test_hosting_hooks_from_store_in_planning` — all hosting PluginNodes hook from store PluginNode `on_end`

   **Phase execution order (2 tests):** Unchanged.
   **Branch paths (2 tests):** BranchNodes use per-port conditions [D-GR-35].
   **Fold/accumulator (2 tests):** Unchanged.
   **Fresh sessions (1 test):** `interview_gate_review` has `fresh_sessions: true` [D-SF4-13].

5. **`tests/migration/test_develop.py`** (~15 tests): Same pattern as planning with:
   - 7 phases (not 6)
   - Consistency tests verifying planning phases match structurally
   - Implementation phase nesting verification (fold > map > loop)
   - **[D-GR-37]** All explicit store pattern tests

6. **`tests/migration/test_bugfix.py`** (~12 tests): Same pattern with:
   - 8 phases in correct order
   - **[D-GR-10]** `env_overrides` config PluginNode present in EnvironmentSetupPhase
   - **[D-GR-37]** All explicit store pattern tests
   - Diagnosis loop has `max_iterations: 3`
   - Parallel RCA uses map sub-phase

7. **`tests/migration/test_yaml_roundtrip.py`** (~5 tests):
   - Round-trip: load → dump → reload → compare for all 3 workflows + templates
   - Verifies TemplateDefinition (not TemplateRef), EdgeDefinition (not Edge), MapModeConfig (not MapConfig) survive round-trip [D-GR-41]

8. **`tests/migration/test_bridge.py`** (~5 tests) [REQ-73 NEW]:
   - `test_create_plugin_runtimes_returns_all_adapters` — factory returns 8 entries
   - `test_config_adapter_resolves_env_vars` — ConfigPluginAdapter.execute("resolve") reads from env dict [D-GR-10]
   - `test_store_adapter_calls_artifact_store_put` — StorePluginAdapter calls put() on ArtifactStore
   - `test_bridge_assembles_runtime_config` — `run_declarative()` produces valid RuntimeConfig
   - `test_bridge_uses_declarative_flag` — CLI `--declarative` routes to `run_declarative()`

**Acceptance Criteria:**
- `pytest tests/migration/` passes — all ~55-60 tests green
- **[D-GR-37]** Store pattern tests verify: all writes via store PluginNodes, no `artifact_key` on nodes, hosting hooks from store `on_end`
- **[D-GR-10]** Config plugin tests verify: `env_overrides` PluginNode present, no `build_env_overrides` transform
- **[D-GR-5]** AST tests verify: all transforms compile, no imports, no dangerous builtins
- **[D-GR-41]** Round-trip tests use correct model names
- **[REQ-73]** Bridge tests verify adapter factory and RuntimeConfig assembly
- All mock class names correct: `MockAgentRuntime`, `MockInteractionRuntime`, `MockPluginRuntime` [D-GR-23]
- ContextVar-based node matching in mocks — no `node_id` on invoke() [D-GR-23]

**Counterexamples:**
- Do NOT assert artifact_key auto-write patterns — C-4 is rejected [D-GR-37]
- Do NOT use `MockRuntime` or `MockInteraction` — use correct SF-3 class names [D-GR-23]
- Do NOT use non-canonical validation error codes [D-SF4-24]
- Do NOT test core checkpoint/resume — not in SF-2 scope [D-GR-24]
- Do NOT use stale model names in assertions (Edge, MapConfig, etc.) [D-GR-41]

**Requirement IDs:** REQ-34, REQ-40, REQ-53, REQ-54, REQ-73 | **Journey IDs:** J-20, J-21, J-22, J-23

### STEP-35: iriai-build-v2 Declarative Bridge

**Objective:** Implement the `run_declarative()` function, `--declarative` CLI flag [D-GR-18], and wire the bridge to iriai-build-v2's existing infrastructure. This is the additive consumer path per D-GR-32 — existing imperative workflows are untouched. Uses `create_plugin_runtimes()` from STEP-28 adapters module.

**Scope:**
| Path | Action |
|------|--------|
| `iriai-build-v2/src/iriai_build_v2/workflows/_declarative.py` | create |
| `iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py` | modify |
| `iriai_compose/plugins/adapters.py` | read |
| `iriai-build-v2/src/iriai_build_v2/interfaces/_bootstrap.py` | read |
| `iriai-build-v2/src/iriai_build_v2/runtimes/__init__.py` | read |

**Instructions:**

1. **`_declarative.py`** (~100 lines) — Thin wrapper:

   ```python
   """Declarative workflow execution bridge.

   Loads YAML workflow configs, maps BootstrappedEnv services to
   RuntimeConfig via Protocol-based adapters (D-A4), and calls
   SF-2's canonical run() function. Additive only — does not
   modify existing imperative workflow infrastructure.
   """
   from pathlib import Path
   from typing import Any

   from iriai_compose.schema.io import load_workflow
   from iriai_compose.declarative import run, RuntimeConfig, PluginRegistry, ExecutionResult
   from iriai_compose.plugins import register_plugin_types, register_instances
   from iriai_compose.plugins.adapters import create_plugin_runtimes


   async def run_declarative(
       yaml_path: str | Path,
       env: Any,  # BootstrappedEnv — no type import to avoid coupling
       *,
       feature: Any,  # Feature model
       agent_runtime_name: str = "claude",
       inputs: dict | None = None,
   ) -> ExecutionResult:
       """Execute a declarative YAML workflow through SF-2's run() ABI.

       Args:
           yaml_path: Path to workflow YAML file
           env: BootstrappedEnv from bootstrap()
           feature: Feature model from create_feature()
           agent_runtime_name: "claude" or "codex"
           inputs: Optional initial workflow inputs

       Returns:
           ExecutionResult from SF-2's run()
       """
       # 1. Load and validate workflow config
       workflow = load_workflow(yaml_path)

       # 2. Build plugin registry with all 6 types + 8 instances
       registry = PluginRegistry()
       register_plugin_types(registry)   # store, hosting, mcp, subprocess, http, config
       register_instances(registry)

       # 3. Map BootstrappedEnv services to PluginRuntime adapters
       plugin_runtimes = create_plugin_runtimes(
           services={
               "artifacts": env.artifacts,
               "feedback": env.feedback_service,
               "preview": env.preview_service,
               "playwright": env.playwright_service,
               "artifact_mirror": env.artifact_mirror,
               "workspace_manager": env.workspace_manager,
           },
           feature_id=feature.id,
           artifacts=env.artifacts,
       )

       # 4. Build RuntimeConfig per SF-2 canonical contract
       from iriai_build_v2.runtimes import create_agent_runtime
       agent_runtime = create_agent_runtime(
           agent_runtime_name,
           session_store=env.sessions,
       )

       config = RuntimeConfig(
           agent_runtime=agent_runtime,       # singular AgentRuntime, NOT dict
           interaction_runtimes={},            # populated by caller if needed
           plugin_registry=registry,
           plugin_runtimes=plugin_runtimes,
           artifacts=env.artifacts,
           sessions=env.sessions,
           context_provider=env.context_provider,
       )

       # 5. Execute via SF-2's canonical run() — no extensions
       return await run(workflow, config, inputs=inputs)
   ```

2. **CLI integration** — Modify `app.py` to add `--declarative` flag [D-GR-18]:

   ```python
   # In plan/develop/bugfix Click commands, add:
   @click.option("--declarative", type=click.Path(exists=True), default=None,
                 help="Path to declarative YAML workflow file. Replaces imperative execution.")

   # In _run() function, after bootstrap:
   if declarative:
       from iriai_build_v2.workflows._declarative import run_declarative
       result = await run_declarative(
           yaml_path=declarative,
           env=env,
           feature=feature,
           agent_runtime_name=agent_runtime,
           inputs=initial_inputs,
       )
       # Print ExecutionResult summary
       return
   # ... existing imperative workflow code unchanged ...
   ```

3. **Key constraints:**
   - `_declarative.py` does NOT import `BootstrappedEnv` or `Feature` types — uses `Any` to avoid coupling
   - Existing `PlanningWorkflow`, `FullDevelopWorkflow`, `BugFixWorkflow` classes untouched
   - Existing `TrackedWorkflowRunner` untouched
   - `AgentRuntime.invoke()` signature unchanged [D-GR-23]
   - No checkpoint/resume in the bridge [D-GR-24]
   - `ClaudeAgentRuntime` and `CodexAgentRuntime` used as-is — no modifications

**Acceptance Criteria:**
- `iriai-build plan 'test' --workspace /path --declarative planning.yaml` routes to `run_declarative()` [D-GR-18]
- `run_declarative()` calls SF-2's `run(workflow, config, inputs=None)` with correct signature
- `RuntimeConfig.agent_runtime` is a singular AgentRuntime (not dict) [D-GR-41]
- Plugin registry contains 6 types and 8 instances after registration
- `create_plugin_runtimes()` returns dict with `env_overrides: ConfigPluginAdapter` [D-GR-10]
- Existing `iriai-build plan 'test' --workspace /path` (no --declarative) still works unchanged
- No modifications to any existing workflow/phase/runner files
- `_declarative.py` does NOT import consumer-specific types [D-SF4-26]

**Counterexamples:**
- Do NOT use `--yaml` flag — use `--declarative` [D-GR-18]
- Do NOT modify existing TrackedWorkflowRunner or DefaultWorkflowRunner
- Do NOT add checkpoint/resume to the bridge [D-GR-24]
- Do NOT modify AgentRuntime.invoke() signature [D-GR-23]
- Do NOT import BootstrappedEnv type in _declarative.py [D-SF4-26]
- Do NOT create seed_loader.py or migration_seed.json — out of SF-4 scope [D-GR-32]
- Do NOT add plugin HTTP surfaces [D-GR-32]

**Requirement IDs:** REQ-53, REQ-54, REQ-73 | **Journey IDs:** J-20, J-22, J-23

## Interfaces to Other Subfeatures

### SF-1 → SF-4 (Python Import)

SF-4 imports from `iriai_compose.schema`:
- **Models:** `WorkflowConfig`, `AskNode`, `BranchNode`, `BranchOutputPort`, `PluginNode`, `ErrorNode`, `PhaseDefinition`, `EdgeDefinition`, `PortDefinition`, `ActorDefinition`, `RoleDefinition`, `TypeDefinition`, `SequentialModeConfig`, `MapModeConfig`, `FoldModeConfig`, `LoopModeConfig`, `StoreDefinition`, `PluginInterface`, `PluginInstanceConfig`, `TemplateDefinition`, `WorkflowCostConfig`, `PhaseCostConfig`, `NodeCostConfig`, `HookPortEvent`
- **Validation:** `validate_workflow()`, `validate_type_flow()`, `detect_cycles()` returning `list[ValidationError]` with canonical error codes [D-SF4-24]
- **I/O:** `load_workflow()`, `dump_workflow()`

**SF-1 requirements from SF-4:**
- EdgeDefinition `transform_fn` field for inline Python transforms [D-SF4-21]
- PluginInstanceConfig model for general type instances [D-SF4-6]
- WorkflowConfig `input_type` field [D-SF4-17]
- BranchOutputPort with per-port `condition` [D-GR-35]
- ActorDefinition with `actor_type: agent|human` [D-GR-30]
- **No `artifact_key` on NodeBase** — removed per D-GR-37/D-GR-14

### SF-2 → SF-4 (Python Import)

SF-4 imports from `iriai_compose.declarative`:
- **Execution:** `run(workflow: WorkflowConfig, config: RuntimeConfig, *, inputs: dict | None = None) -> ExecutionResult`
- **Config:** `RuntimeConfig` — `agent_runtime` (singular AgentRuntime), `interaction_runtimes`, `plugin_registry`, `plugin_runtimes`, `artifacts`, `sessions`, `context_provider`
- **Plugins:** `PluginRegistry` with `register_type()`, `register_instance()`, `has_type()`, `get_type()`, `has_instance()`, `get_instance()`
- **Loader:** `load_workflow()` for YAML parsing
- **Results:** `ExecutionResult` with `success`, `error`, `nodes_executed: list[tuple[str, str]]`, `artifacts`, `branch_paths`, `cost_summary`, `duration_ms`, `workflow_output`, `hook_warnings`, `history`, `phase_metrics`

**SF-2 requirements from SF-4:**
- Runner evaluates `transform_fn` Python strings via AST-validated exec() [D-GR-5]
- Runner dispatches PluginNode execution based on `plugin_type` + instance config
- Runner sets ContextVar for node identity during execution [D-GR-23]
- Runner accepts initial input and passes to first phase `$input` port [D-SF4-17]
- **No artifact_key auto-write** [D-GR-37]
- **No core checkpoint/resume** [D-GR-24]

### SF-3 → SF-4 (Python Import)

SF-4 imports from `iriai_compose.testing`:
- **Mocks:** `MockAgentRuntime`, `MockInteractionRuntime`, `MockPluginRuntime` [D-GR-23]
- **Execution:** `run_test(workflow, mocks, initial_input) -> ExecutionResult`
- **Assertions:** `assert_node_reached`, `assert_artifact`, `assert_branch_taken`, `assert_validation_error`, `assert_node_count`, `assert_phase_executed`, `assert_loop_iterations`, `assert_fold_items_processed`, `assert_error_routed`
- **Snapshot:** `assert_yaml_round_trip`
- **Builder:** `WorkflowBuilder` for programmatic workflow construction in tests

### SF-4 → iriai-build-v2 (Bridge)

SF-4 produces `_declarative.py` and modifies `app.py`:
- `run_declarative(yaml_path, env, *, feature, agent_runtime_name, inputs)` — thin wrapper calling SF-2's `run()`
- `--declarative` CLI flag on plan/develop/bugfix commands [D-GR-18]
- `create_plugin_runtimes()` factory maps BootstrappedEnv services to PluginRuntime adapters [D-SF4-26]
- **Additive only** — existing imperative workflows untouched

### SF-4 → SF-1 (Schema Gap Feedback)

**Known extensions required:**
- EdgeDefinition `transform_fn` for inline Python [D-SF4-21]
- PluginInstanceConfig for general type instances [D-SF4-6]
- WorkflowConfig `input_type` [D-SF4-17]
- **`artifact_key` NOT needed on NodeBase** — all writes via explicit store PluginNodes [D-GR-37]

## Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-71 | Schema gaps — SF-1 may not yet have EdgeDefinition.transform_fn, PluginInstanceConfig, or BranchOutputPort models | medium | Document as required SF-1 extensions. If SF-1 lags, SF-4 defines provisional models in `_compat.py`. | STEP-28, STEP-30, STEP-31, STEP-32 |
| RISK-72 | Edge transform complexity — `tiered_context_builder` (~20 lines of Python in YAML string) is harder to read/debug than a named function | medium | Catalog all transforms in `iriai_compose/plugins/transforms.py` as named constants. Unit-tested in test_edge_transforms.py. | STEP-28, STEP-30 |
| RISK-73 | [D-GR-5] Transform AST compatibility — some transforms may need stdlib modules (e.g., `re`) not available in SAFE_BUILTINS | medium | All transforms written with pure string operations and builtins only. `id_renumberer` uses `str.find()` instead of `re`. If SF-2 exposes `re` in SAFE_BUILTINS, transforms can be simplified later. | STEP-28, STEP-33 |
| RISK-74 | Category C AskNode proliferation — 3 new actors (summarizer, extractor, sd_converter_agent) | low | Cheap models (haiku/sonnet). Actor definitions shared across phases. | STEP-30, STEP-31 |
| RISK-75 | Develop-planning structural drift — planning phases must match between planning.yaml and develop.yaml | medium | Consistency tests in CI. `test_develop_planning_phases_match` catches differences. | STEP-31, STEP-34 |
| RISK-76 | [D-GR-37] Increased node count from explicit store PluginNodes — ~30% more nodes than C-4 version | low | Nodes are simple PluginNode declarations. DAG topology is more explicit and debuggable. Templates absorb repeated patterns. | STEP-30, STEP-31, STEP-32 |
| RISK-77 | [D-GR-37] Missing store PluginNodes — some AskNode outputs may not have a downstream store PluginNode when they should | medium | Systematic audit: grep all `artifacts.put()` in iriai-build-v2, cross-reference with store PluginNodes in YAML. `test_all_artifact_writes_use_store_plugin_nodes` verifies coverage. | STEP-30, STEP-31, STEP-32 |
| RISK-78 | [REQ-73] Bridge adapter impedance mismatch — BootstrappedEnv service interfaces may not map cleanly to PluginRuntime Protocol | medium | Protocol-based structural typing avoids import coupling. Each adapter is thin (~20 lines). Test via `test_bridge.py`. If interface changes, only adapter needs updating. | STEP-28, STEP-35 |
| RISK-79 | [D-GR-10] Config plugin discovery — bugfix workflow must resolve env vars before preview deployment | low | EnvironmentSetupPhase sequences config PluginNode before mcp PluginNode via explicit edges. Edge ordering enforces dependency. | STEP-32 |
| RISK-80 | SF-2 runner not supporting all features at SF-4 build time (PluginNode dispatch, transform execution) | medium | Structural tests (Tier 1) need only SF-1. Execution tests (Tier 2) need SF-2. Build order preserves independence. | STEP-34 |
| RISK-81 | [D-GR-41] Model name drift — SF-1 may ship with slightly different names than documented | low | All model references centralized in conftest.py imports. Single grep+replace updates all references. | STEP-34 |

## File Manifest

| Path | Action |
|------|--------|
| `iriai_compose/plugins/__init__.py` | modify |
| `iriai_compose/plugins/types.py` | create |
| `iriai_compose/plugins/instances.py` | create |
| `iriai_compose/plugins/transforms.py` | create |
| `iriai_compose/plugins/adapters.py` | create |
| `tests/fixtures/workflows/migration/types/common.yaml` | create |
| `tests/fixtures/workflows/migration/types/planning.yaml` | create |
| `tests/fixtures/workflows/migration/types/develop.yaml` | create |
| `tests/fixtures/workflows/migration/types/bugfix.yaml` | create |
| `tests/fixtures/workflows/migration/planning.yaml` | create |
| `tests/fixtures/workflows/migration/develop.yaml` | create |
| `tests/fixtures/workflows/migration/bugfix.yaml` | create |
| `tests/fixtures/workflows/templates/gate_and_revise.yaml` | create |
| `tests/fixtures/workflows/templates/broad_interview.yaml` | create |
| `tests/fixtures/workflows/templates/interview_gate_review.yaml` | create |
| `tests/migration/__init__.py` | create |
| `tests/migration/conftest.py` | create |
| `tests/migration/test_planning.py` | create |
| `tests/migration/test_develop.py` | create |
| `tests/migration/test_bugfix.py` | create |
| `tests/migration/test_yaml_roundtrip.py` | create |
| `tests/migration/test_plugin_instances.py` | create |
| `tests/migration/test_edge_transforms.py` | create |
| `tests/migration/test_bridge.py` | create |
| `iriai-build-v2/src/iriai_build_v2/workflows/_declarative.py` | create |
| `iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py` | modify |



---

## Subfeature: Composer App Foundation & Tools Hub (composer-app-foundation)

### SF-5: Composer App Foundation & Tools Hub

<!-- SF: composer-app-foundation -->


# Unified Technical Plan — iriai-compose Workflow Creator

## Decision Log

| ID | Decision | Source |
|----|----------|--------|
| D-A1 | Pure composition — 3 node types (Ask, Branch, Plugin), no mode field. Interview = loop phase with Ask+Ask+Branch. Gate = Ask(verdict)+Branch(approve/reject). | User choice (interview) |
| D-A2 | Plugin registry — runtime concept only. Plugin types declared in schema (SF-1), instances resolved by consuming project's PluginRegistry at runtime (SF-2). **No PluginType/PluginInstance database tables in SF-5** [D-GR-29]. Tool library page in SF-7 [D-GR-7]. | Design D-41 + SF-2 plan + D-GR-29 |
| D-A3 | Repo topology — `tools/compose/frontend` (Compose SPA), `tools/compose/backend` (FastAPI+PostgreSQL), `platform/toolshub/frontend` (static SPA, no backend). `iriai-compose` extended. `iriai-build-v2` additive changes. `tools/iriai-workflows` NOT used. [D-GR-27, D-GR-36] | User correction (interview) + D-GR-27/D-GR-36 |
| D-A4 | iriai-build-v2 additive — Import declarative runner from iriai-compose, support loading and running YAML workflow definitions. | User clarification (interview) |
| D-A5 | PostgreSQL (not SQLite) — dedicated instance, **psycopg3 driver** with `postgresql://` → `postgresql+psycopg://` normalization, isolated migration chain with `alembic_version_compose` table. [D-GR-28] | SF-5 PRD REQ-2, D-GR-28, Broad Architecture BA-2 |
| D-A6 | Schema design — Unified port/edge models, 4-level context hierarchy, store model with dot-notation, expression-based conditions. All per SF-1 plan. | SF-1 plan (source of truth) |
| D-A7 | DAG runner — Single ExecutionGraph engine for workflow+phase+nested levels, entry-point plugin discovery, AST-validated exec()-based transforms [D-GR-5]. Workflow-level inputs validated before execution, passed to first phase via `$input` port. All per SF-2 plan. | SF-2 plan (source of truth) |
| D-A8 | 3-category reclassification — Infrastructure→5 general plugins (schema-level PluginNode references), transforms→8 inline edge transforms, computation→3 AskNodes. Per SF-4 plan. | SF-4 plan (source of truth) |
| D-A9 | Workflow invocation is a runner concern — workflow declares expected inputs via `WorkflowConfig.inputs` (`list[WorkflowInputDefinition]`); runner validates and passes them to the first phase's `$input` port. No trigger/listener nodes in the schema. [D-SF4-17] | SF-2 plan + SF-4 plan D-SF4-17 |

## Architecture Overview

This plan unifies 7 subfeatures across 5 repositories into a single implementation sequence. Each step references the authoritative SF-level plan where detailed specifications live.

### Repository Map

| Repo | Local Path | Action | SF Coverage |
|------|-----------|--------|-------------|
| iriai-compose | `~/src/iriai/iriai-compose` | Extend | SF-1, SF-2, SF-3, SF-4 (YAML files + tests) |
| compose-frontend | `~/src/iriai/tools/compose/frontend` | Create | SF-5 (compose shell), SF-6 (editor), SF-7 (libraries) |
| compose-backend | `~/src/iriai/tools/compose/backend` | Create | SF-5 (API + DB) |
| toolshub-frontend | `~/src/iriai/platform/toolshub/frontend` | Create | SF-5 (tools hub SPA) |
| iriai-build-v2 | `~/src/iriai/iriai-build-v2` | Additive | SF-4 (runner integration) |

### Dependency Graph & Parallelism

```
Track A (Python — strictly sequential):
  STEP-36 (SF-1 Schema) → STEP-37 (SF-2 Loader/Runner) → STEP-38 (SF-3 Testing) → STEP-39 (SF-4 Migration)

Track B (Web App — foundation then parallel):
  STEP-40 (SF-5 Backend) → STEP-42 (SF-6 Editor) ─┐
  STEP-41 (SF-5 Frontend Shell) → ─────────────────┤→ STEP-44 (Integration)
  STEP-43 (SF-7 Libraries) ────────────────────────┘

Track C (iriai-build-v2 — after SF-2):
  STEP-37 (SF-2) → STEP-45 (iriai-build-v2 additive)

Tools Hub (independent):
  STEP-46 (SF-5 Tools Hub) — can run anytime after STEP-41
```

---

## Implementation Steps

### STEP-36: Declarative Schema & Primitives (SF-1)

**Objective:** Define the complete Pydantic 2.x schema for the declarative workflow YAML format in `iriai_compose/declarative/schema.py`. This is the foundational data model that all other subfeatures consume. Includes SF-2 upstream additions (`WorkflowInputDefinition`, `WorkflowOutputDefinition`) that must be part of the schema from the start.

**Source of Truth:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/plan.md`

**Scope:**
- Create: `iriai_compose/declarative/__init__.py`
- Create: `iriai_compose/declarative/schema.py` (~27 Pydantic models + SF-2 upstream additions)
- Modify: `iriai_compose/__init__.py` (re-export declarative subpackage)
- Modify: `pyproject.toml` (add `pyyaml>=6.0` dependency)
- Read: `iriai_compose/actors.py` (existing Role, Actor models — schema must align)
- Read: `iriai_compose/tasks.py` (existing Task types — pure composition means no mode field on AskNode)
- Read: `iriai_compose/workflow.py` (existing Phase, Workflow ABCs)

**Key Models (per SF-1 plan + SF-2 upstream additions):**
- `WorkflowConfig`: Top-level container with name, schema_version, actors, types, phases, edges, plugins, plugin_instances, stores, context_keys, context_text, cost, templates, **`inputs: list[WorkflowInputDefinition]`**, **`outputs: list[WorkflowOutputDefinition]`** [D-A9]
- `WorkflowInputDefinition` **[SF-2 addition]**: name, type_ref (str|None), schema_def (dict|None, mutually exclusive with type_ref), description, required (bool, default True), default (Any, only when not required). Declares expected input structure for workflow invocation [D-SF4-17, D-A9]
- `WorkflowOutputDefinition` **[SF-2 addition]**: name, type_ref (str|None), schema_def (dict|None, mutually exclusive with type_ref), description. Declares expected output structure.
- `PhaseDefinition`: id, mode (sequential|map|fold|loop), mode-specific config objects, nodes, edges, phases (nested), input/output types, default ports + hooks
- `NodeBase` → `AskNode`, `BranchNode`, `PluginNode`: id, type discriminator, summary, context_keys, artifact_key, input/output types and schemas, ports, hooks, position
- `PortDefinition`: id, direction (input|output), type_ref, condition (Python expression for output routing)
- `Edge`: source, target, transform_fn (inline Python), type annotations
- `ActorDefinition`: type (agent|interaction), role fields, resolver, context_keys, persistent, context_store, handover_key
- `StoreDefinition`: description, keys dict with typed/untyped/open modes, dot-notation references
- `PluginInterface` + `PluginInstanceConfig`: type definitions + per-instance config
- `CostConfig`, `HookRef`, `TemplateRef`
- Phase mode configs: `SequentialConfig`, `MapConfig`, `FoldConfig`, `LoopConfig`

**Design Constraints (from SF-1 plan decisions):**
- D-SF1-10: Single `PortDefinition` for ALL port types (data + hooks)
- D-SF1-11: Default ports on all nodes/phases: `inputs=[input]`, `outputs=[output]`, `hooks=[on_start, on_end]`
- D-SF1-15: All expression fields are Python strings with documented evaluation contexts
- D-SF1-16: `fresh_sessions` on LoopConfig and FoldConfig only (NOT on actors)
- D-SF1-21: Single `Edge` type for data AND hook connections
- D-SF1-22: I/O type system with mutual exclusion (input_type XOR input_schema)
- D-SF1-23: Store model with dot-notation references (`"store_name.key_name"`)
- D-SF1-24: 4-level context hierarchy (workflow → phase → actor → node)
- D-SF1-26: Three store typing modes (typed, untyped, open)

**Acceptance Criteria:**
- `from iriai_compose.declarative.schema import WorkflowConfig, WorkflowInputDefinition, WorkflowOutputDefinition` succeeds
- `WorkflowConfig.model_json_schema()` produces valid JSON Schema including `inputs` and `outputs` array fields
- A minimal YAML file with `inputs:` section round-trips: parse → serialize → parse produces identical model
- `WorkflowInputDefinition` enforces mutual exclusion: `type_ref` XOR `schema_def`
- `WorkflowInputDefinition` enforces `default` only allowed when `required: false`
- All 27+ schema entities validate against the 145+ nodes identified in SF-1's entity hardening audit
- Existing `iriai_compose` public API (`from iriai_compose import Ask, Phase, ...`) unchanged

**Counterexamples:**
- Do NOT add a `mode` field to AskNode — pure composition means Interview/Gate patterns are composed from primitives
- Do NOT create separate edge types for data vs hook — unified `Edge` model per D-SF1-21
- Do NOT put session management on actors — `fresh_sessions` is phase config per D-SF1-16
- Do NOT implement any runtime behavior in this step — schema is pure data modeling
- Do NOT add trigger/listener node types — workflow invocation is a runner concern [D-SF4-17, D-A9]

---

### STEP-37: DAG Loader & Runner (SF-2)

**Objective:** Implement the YAML loader and DAG execution engine in `iriai_compose/declarative/`. This turns static YAML into executable workflows via a single `run()` entry point. The runner validates workflow-level inputs before execution and passes them to the first phase via the `$input` port mechanism [D-A9].

**Source of Truth:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/plan.md`

**Scope:**
- Create: `iriai_compose/declarative/loader.py` (YAML → WorkflowConfig)
- Create: `iriai_compose/declarative/runner.py` (run() entry point + workflow-level orchestration + input validation)
- Create: `iriai_compose/declarative/graph.py` (DAG construction, topological sort, reachability)
- Create: `iriai_compose/declarative/executors.py` (AskNode, BranchNode, PluginNode dispatch)
- Create: `iriai_compose/declarative/modes.py` (sequential, map, fold, loop strategies)
- Create: `iriai_compose/declarative/transforms.py` (inline transform/expression eval via exec())
- Create: `iriai_compose/declarative/plugins.py` (Plugin ABC, PluginRegistry, entry-point discovery)
- Create: `iriai_compose/declarative/config.py` (RuntimeConfig dataclass — bundles agent_runtime, interaction_runtimes, plugin_registry, workspace, feature)
- Create: `iriai_compose/declarative/actors.py` (ActorDefinition → Actor hydration)
- Create: `iriai_compose/declarative/hooks.py` (hook edge execution)
- Create: `iriai_compose/declarative/errors.py` (WorkflowInputError, ExecutionError)
- Modify: `pyproject.toml` (add entry-point group for plugins)
- Read: `iriai_compose/runner.py` (existing DefaultWorkflowRunner — runner must compose with it)
- Read: `iriai_compose/storage.py` (ArtifactStore, ContextProvider — stores must integrate)

**Key Architecture (per SF-2 plan):**
- Single `ExecutionGraph` engine used at workflow, phase, AND nested phase levels
- Phase modes implemented as strategies: `sequential_executor`, `map_executor`, `fold_executor`, `loop_executor`
- Plugin dispatch via registry with `setuptools` entry-point discovery (`iriai_compose.plugins` group)
- Transform execution via `exec()` on Python function body strings
- Resume support via DAG engine preserving node execution history

**Workflow Input Flow [D-A9, D-SF4-17]:**
1. `run()` receives optional `inputs: dict[str, Any]`
2. `_validate_workflow_inputs()` checks required fields from `WorkflowConfig.inputs`, applies defaults, type-checks against `type_ref`/`schema_def`. Raises `WorkflowInputError` before any execution if validation fails
3. Validated inputs passed to first phase as `phase_input` via `_execute_dag()`
4. First phase's entry elements receive data via `$input` port (same `_gather_inputs` mechanism used at all levels)
5. `_validate_workflow_outputs()` warns on missing declared outputs after execution — **never raises** (don't lose execution results)

**Phase Mode Input Injection (per SF-2 plan):**
| Mode | `$input` receives | `$output` produces |
|------|-------------------|--------------------|
| Sequential | `phase_input` directly | Phase output |
| Map | Current collection item | List of all item outputs |
| Fold | `{"item": item, "accumulator": acc}` | Fed to reducer; final accumulator = phase output |
| Loop | First iteration: `phase_input`; subsequent: previous `$output` | Evaluated by `exit_condition`; True → `("condition_met", output)` |

**Entry Point (per SF-2 plan — `RuntimeConfig` as positional arg):**
```python
async def run(
    workflow: WorkflowConfig | str | Path,
    config: RuntimeConfig,
    *,
    inputs: dict[str, Any] | None = None,
) -> ExecutionResult
```

Where `RuntimeConfig` bundles: `agent_runtime`, `interaction_runtimes`, `plugin_registry`, `workspace`, `feature`, and runner-level settings.

**Acceptance Criteria:**
- `from iriai_compose.declarative import run, load_workflow, RuntimeConfig, WorkflowInputError` succeeds
- `load_workflow("path/to/workflow.yaml")` returns validated `WorkflowConfig`
- `run()` with MockRuntime executes a minimal sequential workflow and returns `ExecutionResult`
- `run()` with `inputs={"scope": data}` passes validated inputs to first phase via `$input` port
- `run()` with missing required input raises `WorkflowInputError` before any node executes
- `run()` with optional input absent applies default value from `WorkflowInputDefinition.default`
- Phase modes work: sequential (ordered), map (parallel fan-out), fold (accumulator), loop (exit condition)
- Plugin dispatch resolves `plugin_ref` strings via PluginRegistry
- Edge transforms execute inline Python and pass transformed data to target nodes
- Hook edges fire on_start/on_end without transforms
- `ExecutionResult` includes `workflow_output` (collected from final phase) and `hook_warnings`
- Existing `DefaultWorkflowRunner` API unchanged

**Counterexamples:**
- Do NOT create separate executors for workflow vs phase level — single `ExecutionGraph` per SF-2 plan
- Do NOT use eval() for transforms — use exec() with controlled namespace per SF-2 plan
- Do NOT hardcode plugin implementations — registry-based with entry-point discovery
- Do NOT modify any existing `iriai_compose` core classes (Task, Phase, Workflow, Runner)
- Do NOT add trigger/listener logic inside the runner — invocation is the caller's responsibility [D-SF4-17]
- Do NOT let `_validate_workflow_outputs()` raise — it warns only, to avoid losing execution results

---

### STEP-38: Testing Framework (SF-3)

**Objective:** Build `iriai_compose/testing/` subpackage with MockRuntime, assertions, fixtures, snapshot testing, and validation helpers.

**Source of Truth:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/plan.md`

**Scope:**
- Create: `iriai_compose/testing/__init__.py` (public API re-exports)
- Create: `iriai_compose/testing/mock_runtime.py` (MockRuntime with response map)
- Create: `iriai_compose/testing/assertions.py` (assert_node_reached, assert_artifact, etc.)
- Create: `iriai_compose/testing/validation.py` (validate_workflow, detect_cycles, validate_type_flow)
- Create: `iriai_compose/testing/fixtures.py` (WorkflowBuilder, minimal_ask_workflow, minimal_branch_workflow)
- Create: `iriai_compose/testing/snapshot.py` (assert_yaml_round_trip, assert_yaml_equals)
- Create: `iriai_compose/testing/runner.py` (run_test wrapper → ExecutionResult)
- Create: `tests/fixtures/workflows/*.yaml` (minimal examples + invalid cases)
- Modify: `pyproject.toml` (add `[testing]` extra with pytest deps)

**Key Components (per SF-3 plan):**
- `MockRuntime`: extends MockAgentRuntime with `responses` dict keyed by `(node_id, role_name)`, `handler` callback, call recording
- `WorkflowBuilder`: fluent API for programmatic workflow construction with auto-generation of undefined actors/phases. Supports `.add_input(name, type_ref=..., required=True)` for declaring workflow inputs
- `ValidationError`: dataclass with code, path, message, context fields
- Validation codes: dangling_edge, cycle_detected, type_mismatch, missing_required_field, duplicate_node_id, unreachable_node, invalid_actor_ref, invalid_phase_mode_config, invalid_hook_ref, invalid_transform_ref
- `run_test()` accepts optional `inputs` kwarg that passes through to `run()` for workflow input testing

**Acceptance Criteria:**
- `pip install iriai-compose[testing]` installs testing extras
- `from iriai_compose.testing import MockRuntime, WorkflowBuilder, validate_workflow` succeeds
- `WorkflowBuilder().add_phase(...).add_ask_node(...).build()` produces valid WorkflowConfig
- `WorkflowBuilder().add_input("scope", type_ref="ScopeOutput").build()` includes workflow-level input declaration
- `validate_workflow(wf)` returns `list[ValidationError]` (empty for valid, populated for invalid)
- `run_test(wf, runtime=rt, inputs={"scope": data})` passes inputs through to `run()`
- `assert_yaml_round_trip(path)` passes for all fixture YAML files
- All assertions raise `AssertionError` with diagnostic messages (not bool returns)

---

### STEP-39: Workflow Migration & Litmus Test (SF-4)

**Objective:** Translate iriai-build-v2's 3 workflows (planning, develop, bugfix) to declarative YAML format, create task templates, and write behavioral equivalence tests. Produce pre-seeded content for the compose app. Each workflow declares its expected inputs via `WorkflowInputDefinition` [D-SF4-17, D-A9].

**Source of Truth:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-migration/plan.md`

**Scope:**
- Create: `iriai_compose/declarative/workflows/planning.yaml` (6 phases, ~50 nodes)
- Create: `iriai_compose/declarative/workflows/develop.yaml` (7 phases, ~60 nodes)
- Create: `iriai_compose/declarative/workflows/bugfix.yaml` (8 phases, ~35 nodes)
- Create: `iriai_compose/declarative/workflows/templates/gate_and_revise.yaml`
- Create: `iriai_compose/declarative/workflows/templates/broad_interview.yaml`
- Create: `iriai_compose/declarative/workflows/templates/interview_gate_review.yaml`
- Create: `iriai_compose/declarative/workflows/types/*.yaml` (output type definitions)
- Create: `iriai_compose/declarative/workflows/plugins/*.yaml` (5 plugin type interfaces + 7 instances)
- Create: `tests/fixtures/workflows/migration/*.yaml` (copies for snapshot tests)
- Create: `tests/test_migration_planning.py` (~15 tests)
- Create: `tests/test_migration_develop.py` (~15 tests)
- Create: `tests/test_migration_bugfix.py` (~12 tests)
- Create: `tests/test_migration_plugins.py` (~8 tests)
- Create: `tests/test_migration_transforms.py` (~10 tests)
- Read: `iriai-build-v2/src/iriai_build_v2/workflows/` (all workflow Python files — reference implementation)

**Key Migration Patterns (per SF-4 plan):**
- 3-category reclassification: infrastructure → 5 general plugin types (store, hosting, mcp, subprocess, http), transforms → 8 inline edge transforms, computation → 3 AskNodes
- Store read/write separation: reads via `context_keys`, writes via explicit `store` PluginNode
- Hook edges for artifact hosting: `write_prd.on_end → host_prd.input`
- Fresh sessions on gate review loops: `fresh_sessions: true` on LoopConfig
- **Workflow input declaration [D-SF4-17, D-A9]:** Each workflow declares `inputs` with `WorkflowInputDefinition`. Runner validates and passes to first phase's `$input` port. No trigger/listener nodes. Specific inputs per workflow:
  - `planning.yaml`: `inputs: [{name: "scope", type_ref: "ScopeOutput", required: true}]`
  - `develop.yaml`: `inputs: [{name: "scope", type_ref: "ScopeOutput", required: true}]`
  - `bugfix.yaml`: `inputs: [{name: "bug_report", type_ref: "BugReport", required: true}]`
- First phase (e.g., ScopingPhase in planning, BugIntakePhase in bugfix) receives workflow input from runner invocation context via `$input` port [D-SF4-17]

**Acceptance Criteria:**
- All 3 YAML workflows pass `validate_workflow()` with zero errors
- All 3 YAML workflows declare `inputs` with correct `WorkflowInputDefinition` entries [D-A9]
- `assert_yaml_round_trip()` passes for all 3 workflows
- ~50 behavioral equivalence tests pass with MockRuntime, using `inputs=` kwarg to provide workflow-level data
- Planning workflow: 6 phases execute in order, ScopingPhase receives `ScopeOutput` via `$input`, fold produces per-subfeature artifacts, gate loops until approved
- Develop workflow: 7 phases, receives `ScopeOutput` input, implementation loop retries on rejection, DAG groups execute fold>map
- Bugfix workflow: 8 phases, receives `BugReport` input, diagnosis loop exits at max_iterations=3, dual RCA map produces 2 analyses
- Pre-seeded content package exports: 3 workflows, ~8 roles, ~10 schemas, 3 task templates, 5 plugin types, 7 plugin instances

**Counterexamples:**
- Do NOT add trigger or listener node types to represent workflow invocation — invocation is the runner's responsibility [D-SF4-17]
- Do NOT hardcode initial data inside the workflow YAML — workflows declare `inputs` and the runner provides values at invocation time

---

### STEP-40: Compose Backend (SF-5 — Backend)

**Objective:** Build the FastAPI + PostgreSQL backend for the compose app with exactly 5 foundation tables [D-GR-29], CRUD APIs for all 4 entity types, canonical schema export [D-GR-22], in-process mutation hooks on all 4 entity types, per-user rate limiting, structured JSON logging, and starter template seeding via Alembic data migration.

**Source of Truth:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md`

**Scope:**
- Create: `tools/compose/backend/` (full FastAPI project) [D-GR-27, D-GR-36]
  - `app/main.py` — FastAPI app, lifespan (startup/shutdown), middleware registration, CORS, exception handlers
  - `app/config.py` — Pydantic Settings: DATABASE_URL, AUTH_SERVICE_PUBLIC_URL, AUTH_JWKS_URL, CORS_ORIGINS, RATE_LIMIT_ENABLED, LOG_LEVEL
  - `app/database.py` — SQLAlchemy 2.x engine + sessionmaker, `postgresql://` → `postgresql+psycopg://` normalization [D-GR-28], pool_size=5, max_overflow=15 (effective max 20), pool_pre_ping=True
  - `app/models/__init__.py` — Base + all model imports
  - `app/models/workflow.py` — Workflow + WorkflowVersion models
  - `app/models/role.py` — Role model
  - `app/models/output_schema.py` — OutputSchema model
  - `app/models/custom_task_template.py` — CustomTaskTemplate model
  - `app/schemas/workflow.py` — WorkflowCreate, WorkflowUpdate, WorkflowResponse, WorkflowImport Pydantic schemas
  - `app/schemas/role.py` — RoleCreate, RoleUpdate, RoleResponse Pydantic schemas
  - `app/schemas/output_schema.py` — OutputSchemaCreate, OutputSchemaUpdate, OutputSchemaResponse Pydantic schemas
  - `app/schemas/custom_task_template.py` — TemplateCreate, TemplateUpdate, TemplateResponse Pydantic schemas
  - `app/schemas/common.py` — PaginatedResponse, ErrorResponse, entity name regex validator (`^[\w\s\-\.]{1,255}$`)
  - `app/routers/workflows.py` — Workflow CRUD + import + duplicate + versions + export + validate + templates + restore
  - `app/routers/roles.py` — Role CRUD + restore
  - `app/routers/schemas.py` — OutputSchema CRUD + restore
  - `app/routers/templates.py` — CustomTaskTemplate CRUD + restore
  - `app/routers/schema_export.py` — `GET /api/schema/workflow` [D-GR-22]
  - `app/routers/health.py` — `GET /health`, `GET /ready`
  - `app/dependencies/auth.py` — JWKS JWT validation (RS256) via auth-python; `__system__` sentinel guard
  - `app/dependencies/rate_limit.py` — slowapi per-user rate limiting (JWT sub or IP fallback)
  - `app/middleware/logging_middleware.py` — structlog JSON logging + x-correlation-id binding
  - `app/middleware/security.py` — CSP, X-Frame-Options: DENY, X-Content-Type-Options: nosniff
  - `app/hooks.py` — EntityMutationHook interface + MutationHookRegistry
  - `alembic/env.py` — postgresql+psycopg URL normalization (matching database.py), `version_table='alembic_version_compose'`
  - `alembic/versions/0001_initial_schema.py` — 5 foundation tables with partial unique indexes
  - `alembic/versions/0002_seed_starter_templates.py` — Data migration: 3 starter workflows + associated roles/schemas as `user_id='__system__'` rows
  - `pyproject.toml` — deps: fastapi, uvicorn, sqlalchemy, psycopg[binary], alembic, pydantic-settings, pyjwt[crypto], structlog, slowapi, pyyaml, iriai-compose, httpx
  - `Dockerfile`
  - `ROLLBACK.md` — Documents `alembic downgrade` procedure for each migration
- Read: `iriai_compose/declarative/schema.py` (JSON Schema export for `/api/schema/workflow`)
- Read: `platform/deploy-console/deploy-console-service/app/database.py` (psycopg + pool pattern) [code: platform/deploy-console/deploy-console-service/app/database.py:13]
- Read: `platform/deploy-console/deploy-console-service/app/middleware/rate_limit.py` (slowapi pattern) [code: platform/deploy-console/deploy-console-service/app/middleware/rate_limit.py:16]
- Read: `platform/deploy-console/deploy-console-service/app/logging_config.py` (structlog pattern)

**Database Models — 5 Tables Only [D-GR-29]:**

| Table | Model | Key Fields | Constraints |
|-------|-------|------------|-------------|
| `workflows` | Workflow | `id` (UUID pk), `name` (varchar 255), `description` (text null), `yaml_content` (text), `current_version` (int default 1), `is_valid` (bool default false), `user_id` (varchar 255), `created_at` (timestamptz), `updated_at` (timestamptz null), `deleted_at` (timestamptz null) | Partial unique: `(user_id, name) WHERE deleted_at IS NULL`; Index: `(user_id, deleted_at)` |
| `workflow_versions` | WorkflowVersion | `id` (UUID pk), `workflow_id` (UUID FK→workflows.id), `version_number` (int), `yaml_content` (text), `change_description` (varchar 500 null), `user_id` (varchar 255), `created_at` (timestamptz) | Unique: `(workflow_id, version_number)`; Append-only |
| `roles` | Role | `id` (UUID pk), `name` (varchar 255), **`prompt`** (text), `tools` (JSON default []), `model` (varchar 100 null), `effort` (varchar 50 null), `metadata` (JSON default {}), `user_id`, `created_at`, `updated_at`, `deleted_at` | Partial unique: `(user_id, name) WHERE deleted_at IS NULL` |
| `output_schemas` | OutputSchema | `id` (UUID pk), `name` (varchar 255), `description` (text null), `json_schema` (JSON), `user_id`, `created_at`, `updated_at`, `deleted_at` | Partial unique: `(user_id, name) WHERE deleted_at IS NULL` |
| `custom_task_templates` | CustomTaskTemplate | `id` (UUID pk), `name` (varchar 255), `description` (text null), **`subgraph_yaml`** (text), `input_interface` (JSON), `output_interface` (JSON), `user_id`, `created_at`, `updated_at`, `deleted_at` | Partial unique: `(user_id, name) WHERE deleted_at IS NULL`; SF-7 may extend with `actor_slots` JSONB column |

**NOT in SF-5 scope:** PluginType, PluginInstance, workflow_entity_refs, tools tables. [D-GR-29]

**Starter Templates — DB Rows [PRD REQ-85]:**
- Seeded via Alembic data migration (revision `0002_seed_starter_templates`) — NOT seed.py, NOT filesystem assets
- 3 rows in `workflows` table: planning, develop, bugfix workflows
- `user_id = '__system__'` sentinel value
- `deleted_at = NULL` always — system rows never soft-deleted by user actions
- YAML content embedded directly in migration revision file
- Returned ONLY by `GET /api/workflows/templates` (excluded from user-scoped `GET /api/workflows`)
- Guard: reject any JWT with `sub='__system__'` at auth dependency level → 403

**API Endpoints:**

*Workflow CRUD + extras:*
- `GET /api/workflows` — cursor-paginated list, user-scoped (excludes `__system__` rows and soft-deleted), 20 default / 100 max
- `POST /api/workflows` — create workflow + WorkflowVersion v1 atomically; fires `created` hook
- `GET /api/workflows/{id}` — retrieve single (user-scoped)
- `PUT /api/workflows/{id}` — update; fires `updated` hook; if `yaml_content` changed → auto-create new WorkflowVersion
- `DELETE /api/workflows/{id}` — soft-delete (set `deleted_at`); fires `soft_deleted` hook
- `PATCH /api/workflows/{id}/restore` — clear `deleted_at`; fires `restored` hook
- `POST /api/workflows/import` — parse YAML (`yaml.safe_load`), validate, create + version v1; fires `created` hook; 422 on invalid YAML [D-SF5-R5: collection-level path]
- `POST /api/workflows/{id}/duplicate` — deep-copy with new name; fires `created` hook
- `POST /api/workflows/{id}/versions` — append new version; does NOT fire workflow mutation hook
- `GET /api/workflows/{id}/export` — download canonical nested YAML
- `POST /api/workflows/{id}/validate` — validate against `WorkflowConfig` schema; return path/message error details
- `GET /api/workflows/templates` — all `user_id='__system__'` rows (no user-scoping)

*Role CRUD:*
- `GET /api/roles` — paginated, user-scoped
- `POST /api/roles` — create; fires `created` hook
- `GET /api/roles/{id}` — retrieve
- `PUT /api/roles/{id}` — update; fires `updated` hook
- `DELETE /api/roles/{id}` — soft-delete; fires `soft_deleted` hook
- `PATCH /api/roles/{id}/restore` — restore; fires `restored` hook

*OutputSchema CRUD:*
- `GET /api/schemas` — paginated, user-scoped
- `POST /api/schemas` — create; fires `created` hook
- `GET /api/schemas/{id}` — retrieve
- `PUT /api/schemas/{id}` — update; fires `updated` hook
- `DELETE /api/schemas/{id}` — soft-delete; fires `soft_deleted` hook
- `PATCH /api/schemas/{id}/restore` — restore; fires `restored` hook

*CustomTaskTemplate CRUD:*
- `GET /api/templates` — paginated, user-scoped
- `POST /api/templates` — create; fires `created` hook
- `GET /api/templates/{id}` — retrieve
- `PUT /api/templates/{id}` — update; fires `updated` hook
- `DELETE /api/templates/{id}` — soft-delete; fires `soft_deleted` hook
- `PATCH /api/templates/{id}/restore` — restore; fires `restored` hook

*Schema & Health:*
- `GET /api/schema/workflow` — `WorkflowConfig.model_json_schema()` from iriai-compose [D-GR-22]
- `GET /health` — public, 200 if process up
- `GET /ready` — public, 200 with `{"database": "ok"}`; 503 if DB unreachable

**NOT in SF-5 endpoints:** `/api/plugins/*`, `/api/tools/*`, `/api/{entity}/references/{id}` [D-GR-29]

**Mutation Hook Interface [PRD REQ-92, D-GR-29]:**

File: `app/hooks.py`

```python
class EntityType(str, Enum):
    WORKFLOW = "workflow"
    ROLE = "role"
    OUTPUT_SCHEMA = "output_schema"
    CUSTOM_TASK_TEMPLATE = "custom_task_template"

class MutationEvent(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    SOFT_DELETED = "soft_deleted"
    RESTORED = "restored"

@dataclass
class MutationHookPayload:
    entity_type: EntityType
    event: MutationEvent
    entity_id: uuid.UUID
    user_id: str

MutationCallback = Callable[[MutationHookPayload], None]

class MutationHookRegistry:
    def register(self, callback: MutationCallback) -> None: ...
    def fire(self, payload: MutationHookPayload) -> None: ...
```

- **All 4 entity types** emit hooks: Workflow, Role, OutputSchema, CustomTaskTemplate
- **Exactly 4 event kinds**: `created`, `updated`, `soft_deleted`, `restored`
- **NOT valid events**: `imported` (maps to `created`), `version_saved` (no hook), `deleted` (use `soft_deleted`)
- Hooks invoked synchronously AFTER successful DB commit
- `fire()` catches and logs callback exceptions — never fails the request
- SF-7 registers refresh callbacks at FastAPI lifespan startup without modifying SF-5 code
- SF-5 NEVER creates/updates `workflow_entity_refs` rows [D-GR-29]

**Rate Limiting [PRD REQ-83]:**
- Library: slowapi (matches deploy-console pattern) [code: platform/deploy-console/deploy-console-service/app/middleware/rate_limit.py:16]
- Key: JWT `sub` claim when present; fallback to request remote address
- Limits: 100/min standard, 30/min create/update, 10/min import
- Response: HTTP 429 with `Retry-After` header + structured JSON body
- Rate-limit events logged via structlog
- Toggleable via `RATE_LIMIT_ENABLED` env var (disable for dev/test)

**Structured Logging [PRD REQ-83]:**
- Library: structlog with JSONRenderer (matches deploy-console) [code: platform/deploy-console/deploy-console-service/app/logging_config.py]
- Service name: `compose-backend` bound globally
- Request correlation: `x-correlation-id` header bound to structlog context per request (generate UUID if absent)
- Event coverage: auth validation, import operations, soft-delete, restore, rate-limit events, hook invocation success/failure
- **FORBIDDEN**: raw workflow YAML content, prompt body text, sensitive user data

**Security:**
- JWKS JWT validation (RS256) via auth-python; issuer vs `AUTH_SERVICE_PUBLIC_URL` [MEMORY.md production bug pattern]
- All resources scoped by `user_id` from JWT `sub` claim
- `__system__` sentinel guard: reject JWT with `sub='__system__'` → 403
- Soft-delete with `deleted_at`; 30-day recovery window
- Cursor-based pagination (20 default, 100 max)
- YAML safety: `yaml.safe_load()` only, 5MB document size limit, alias expansion protection
- Payload size limits: 2MB standard, 5MB import
- Entity name sanitization: regex `^[\w\s\-\.]{1,255}$` in Pydantic schemas
- Security headers: CSP, X-Frame-Options: DENY, X-Content-Type-Options: nosniff

**Acceptance Criteria:**
- `GET /ready` → 200 `{"database": "ok"}`; with DB down → 503
- `POST /api/workflows` with valid name + YAML → 201 with `current_version: 1`; WorkflowVersion row created atomically
- `POST /api/workflows/import` with valid YAML → 201, fires `created` hook; invalid YAML → 422 with path/message errors
- `GET /api/workflows/templates` → 200 with 3 starter templates (user_id=`__system__`)
- `POST /api/workflows/{id}/duplicate` → 201 with "Copy of {name}"
- `DELETE /api/workflows/{id}` → 200, `deleted_at` set; `PATCH /api/workflows/{id}/restore` → 200, `deleted_at` cleared
- `PUT /api/workflows/{id}` with changed yaml_content → `current_version` incremented, new WorkflowVersion row
- `GET /api/schema/workflow` → 200 JSON Schema from `WorkflowConfig.model_json_schema()` [D-GR-22]
- All CRUD on all 4 entity types fires correct mutation hook event
- Registered callback receives `MutationHookPayload` with correct entity_type, event, entity_id, user_id
- 101st request/min from same user → 429 with Retry-After
- All log entries: structured JSON with service, correlation_id, level, timestamp
- `alembic upgrade head` → 5 tables created; `alembic downgrade -1` → reversed (ROLLBACK.md)
- JWT with `sub='__system__'` → 403

**Counterexamples:**
- Do NOT create PluginType or PluginInstance tables — 5 tables only [D-GR-29]
- Do NOT create/update `workflow_entity_refs` rows — SF-7 responsibility [D-GR-29]
- Do NOT use `asyncpg` — use `psycopg[binary]` with `postgresql+psycopg://` normalization [D-GR-28]
- Do NOT serve starter templates from filesystem — DB rows with `user_id='__system__'` via Alembic data migration
- Do NOT use seed.py or init script for template seeding — Alembic data migration only
- Do NOT fire hooks on WorkflowVersion creation
- Do NOT use `system_prompt` on Role — field is `prompt`
- Do NOT use `yaml_content` on CustomTaskTemplate — field is `subgraph_yaml`
- Do NOT use `is_example` field — starters identified by `user_id='__system__'`
- Do NOT create `/api/plugins/*` endpoints [D-GR-29]
- Do NOT serve static `workflow-schema.json` — runtime `WorkflowConfig.model_json_schema()` only [D-GR-22]
- Do NOT log raw YAML or prompt text [PRD REQ-83]

### STEP-41: Compose Frontend Shell (SF-5 — Frontend)

**Objective:** Scaffold the React 18 + Vite SPA with XP-inspired design system (non-purple brand per AC-15), ExplorerLayout with 4-folder sidebar [D-GR-29], auth with deep link preservation, schema bootstrap gate (CMP-18) [D-GR-33, D-GR-38], YAML contract error panel (CMP-19), and 429 rate-limit handling.

**Source of Truth:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md` + Design Decisions

**Scope:**
- Create: `tools/compose/frontend/` (full Vite React 18 project) [D-GR-27, D-GR-36]
  - Design system: XP-inspired components vendored from deploy-console (Button, Window, Card, Input, Toast) [D-GR-6]
  - `src/styles/compose-theme.css` — XP-inspired theme with compose-specific brand tokens (NOT deploy-console purple per AC-15)
  - Auth: `@homelocal/auth` with `compose_` token prefix; deep link preservation via sessionStorage
  - Layout: ExplorerLayout (sidebar + content), AddressBar, Toolbar, StatusBar
  - Sidebar: SidebarTree with **4 entity-type folders only**: Workflows, Roles, Output Schemas, Task Templates [D-GR-29: no Plugins folder]
  - Content views: GridView, DetailsView, EmptyState, SkeletonLoader
  - CRUD: NewDropdown, ConfirmDialog, ContextMenu, inline rename
  - Routing: React Router with `/workflows`, `/roles`, `/schemas`, `/templates` — **NO `/plugins` route** [D-GR-29]
  - State: Zustand stores for entities, sidebar, UI state (stable selectors — no .filter()/.map()/[] per MEMORY.md)
  - API client: Axios with JWT interceptor + 429 rate-limit handling (exponential backoff, toast notification)
  - Schema bootstrap: `src/components/EditorSchemaBootstrapGate.tsx` (CMP-18) [D-GR-33, D-GR-38]
  - Error surfaces: `src/components/YAMLContractErrorPanel.tsx` (CMP-19)
  - MobileBlockScreen at <768px [D-18]
  - `package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`
  - `Dockerfile`
- Read: `platform/deploy-console/deploy-console-frontend/` (XP component patterns to vendor)

**CMP-18: EditorSchemaBootstrapGate [D-GR-33, D-GR-38]:**
- File: `src/components/EditorSchemaBootstrapGate.tsx`
- data-testid: `schema-bootstrap-gate`, `schema-bootstrap-loading`, `schema-bootstrap-error`, `schema-bootstrap-retry-btn`, `schema-bootstrap-back-btn`
- Route-level blocking gate between Explorer shell and editor host
- Fetches `GET /api/schema/workflow` on mount; caches successful response per session
- **States:**
  - `loading`: Full-panel card "Loading workflow schema…" with spinner. Editor canvas does NOT render. `data-testid="schema-bootstrap-loading"`
  - `error`: Red bordered panel "Can't load workflow schema" with Retry (primary) and Back to Workflows (secondary). **Zero canvas, palette, or inspector rendering.** `data-testid="schema-bootstrap-error"`
  - `ready`: Gate unmounts, hands off to editor host. No warning chrome remains.
- **Strictly blocking — no view-only fallback [D-GR-38]**
- Retry triggers new fetch; success removes gate and mounts editor
- Back to Workflows navigates to `/workflows` without altering workflow record

**CMP-19: YAMLContractErrorPanel:**
- File: `src/components/YAMLContractErrorPanel.tsx`
- data-testid: `yaml-error-panel`, `yaml-error-panel-warning`, `yaml-error-panel-error`, `yaml-error-row`, `yaml-error-dismiss-btn`
- Shared error/warning surface for import failures and save/validation messages
- **Tones:**
  - `warning` (amber): "Imported with warnings" + expandable path/message rows. `data-testid="yaml-error-panel-warning"`
  - `error` (red): Validation failure with path-specific rows. `data-testid="yaml-error-panel-error"`
  - `dismissed`: User closes panel
- Cases: separate hooks sections, serialized port_type, root-level nodes, cross-phase edges inside phases

**429 Rate-Limit Handling:**
- Axios response interceptor catches 429 status
- Reads `Retry-After` header
- Shows toast: "Too many requests. Please wait {seconds}s."
- Auto-retries after delay with exponential backoff + jitter (max 3 retries)
- Does NOT silently swallow the error

**Deep Link Preservation:**
- On 401 / auth redirect: store current path in sessionStorage
- After successful re-authentication: restore path from sessionStorage
- Clear stored path after restoration

**Key data-testid assignments:**
- `sidebar-tree`, `sidebar-folder-workflows`, `sidebar-folder-roles`, `sidebar-folder-schemas`, `sidebar-folder-templates`
- `explorer-layout`, `address-bar`, `toolbar`, `status-bar`
- `grid-view`, `details-view`, `empty-state`, `skeleton-loader`
- `new-dropdown`, `confirm-dialog`, `context-menu`
- `mobile-block-screen`
- `schema-bootstrap-gate`, `schema-bootstrap-loading`, `schema-bootstrap-error`
- `yaml-error-panel`, `yaml-error-panel-warning`, `yaml-error-panel-error`

**Acceptance Criteria:**
- Navigate to compose.iriai.app → MobileBlockScreen on <768px, ExplorerLayout on >=768px
- Sidebar tree shows **4 entity-type folders** (Workflows, Roles, Output Schemas, Task Templates) — NO Plugins folder
- Grid/Details view toggle with localStorage persistence
- CRUD: create, rename, duplicate, delete (with ConfirmDialog) on all 4 entity types
- Search filters entities by name (300ms debounce)
- Auth: OAuth redirect → authenticated layout → 401 triggers deep link preservation → re-auth → path restored
- Navigate to `/workflows/:id/edit` → EditorSchemaBootstrapGate blocks until `/api/schema/workflow` succeeds
- If schema fetch fails → red error panel with Retry + Back to Workflows; NO canvas renders [D-GR-38]
- After 429 → toast shows retry guidance → auto-retry on backoff
- Brand color is NOT deploy-console purple [AC-15]

**Counterexamples:**
- Do NOT create `/plugins` route or Plugins sidebar folder [D-GR-29]
- Do NOT implement CMP-30 through CMP-47 (node/port/phase visual primitives) — SF-7 owns these [D-GR-11]
- Do NOT use purple (`#7c3aed` or similar) as primary brand color [AC-15]
- Do NOT provide view-only fallback when schema bootstrap fails [D-GR-38]
- Do NOT use React 19 — use React 18
- Do NOT silently swallow 429 errors — show user feedback
- Do NOT use Zustand selectors with `.filter()`, `.map()`, or `[]` — creates infinite re-renders [MEMORY.md]

### STEP-42: Workflow Editor & Canvas (SF-6)

**Objective:** Build the React Flow DAG canvas with all node types, phase containers, port connections, inspectors, and toolbar.

**Source of Truth:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/design-decisions.md` + Design Decisions Sections 4, 7, 8, 12

**Scope:**
- Create: `tools/compose/frontend/src/features/editor/` (all editor components)
  - Canvas: ReactFlowCanvas with custom node types (AskNode, BranchNode, PluginNode, TemplateNode, PhaseContainer)
  - Edges: DataEdge (type label, ⚡ transform indicator), HookEdge (dashed purple)
  - Palette: NodePalette (48px right strip), PaletteItem (draggable icons)
  - Inspectors: InspectorWindowManager, AskInspector, BranchInspector, PluginInspector, PhaseInspector, EdgeInspector
  - Inspector features: TetherLines, InlineRoleCreator, PromptTemplateEditor, InlineOutputSchemaCreator, OutputPathsEditor
  - Toolbar: PaintMenuBar, IconToolbar, tool mode toggle (Hand/Select)
  - Phase: SelectionRectangle (marching ants), collapse/expand, mini-canvas thumbnails
  - Validation: ValidationPanel, ErrorBadge, red glow states
  - Dialogs: SaveAsTemplateDialog, ImportConfirmDialog
  - Undo/Redo: 50-depth stack
  - Auto-save: 30s inactivity debounce

**Key Interactions (from design doc Section 8):**
- Node placement: drag from palette → one-shot drop
- Phase creation: Select tool → drag rectangle → enclosed nodes grouped
- Port connection: drag from 12px output → rubber band → drop on input
- Double-click → floating XP inspector with tether line
- Actor assignment: drag role chip from palette → drop on 12px actor slot
- Edge transform: double-click → ~520×440px CodeMirror modal [D-21]

**Acceptance Criteria:**
- Empty canvas shows dot grid + hint text
- Drag Ask/Branch/Plugin from palette creates correctly-styled 260px cards
- AskNode shows actor slot (empty/filled), summary, context keys, artifact key, prompt preview
- BranchNode shows switch function preview + output path rows (min 2 paths)
- Phase creation via select-tool rectangle encloses nodes
- Phase modes update border style (solid/double/dotted/dashed) immediately
- Loop phases show dual exit ports (condition_met + max_exceeded)
- Collapsed phases show mini-canvas thumbnail with mode border preserved
- Edge inspector modal opens on double-click with CodeMirror Python editor
- Validation errors show as red badges, dashed edges, inspector field errors
- Ctrl+S saves, auto-save triggers at 30s inactivity
- Undo/Redo tracks all mutations including actor slots and switch functions

---

### STEP-43: Libraries & Registries (SF-7)

**Objective:** Build the 4 library pages (Roles, Schemas, Templates, Plugins) with creation wizards, editors, and picker components for SF-6 integration.

**Source of Truth:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md`

**Scope:**
- Create: `tools/compose/frontend/src/features/libraries/` (all library components)
  - Shared: LibraryGrid, LibraryCard, LibraryToolbar, EmptyState with "Try an Example"
  - Roles: RoleEditorView (4-step: Identity → System Prompt → Tools → Metadata), ModelPicker, ToolChecklistGrid
  - Schemas: SchemaEditorView (dual-pane CodeMirror + SchemaPreviewTree)
  - Templates: TaskTemplateEditorView (mini React Flow canvas + SidePanel), WizardDialog (3-step)
  - Plugins: PluginTypesGrid, PluginTypeDetailView, PluginInstanceForm, PluginTypeEditor, ImplementationBanner
  - Pickers (consumed by SF-6): RolePicker, SchemaPicker, PluginPicker, TemplateBrowser
  - Promotion: PromotionDialog (name + preview + save-to-library)

**Key Interactions (from design doc Section 8 — Library Interactions):**
- List → Editor [D-36]: click entity → list collapses to sidebar, editor fills content
- Role creation: multi-step content panel (not modal) [D-23]
- Schema creation: inline-first in Ask inspector, library is secondary [D-26]
- Templates: read-only on canvas, edit in library only [D-25]
- Plugins: two-level types + instances [D-41], ImplementationBanner on all types
- Soft delete with reference protection: blocked if referenced, 30-day soft-delete after

---

### STEP-44: Frontend Integration & Starter Templates

**Objective:** Wire editor (SF-6) and libraries (SF-7) together via picker components, integrate with backend API, verify starter templates render correctly from DB rows, and implement remaining cross-cutting concerns.

**Scope:**
- Modify: `tools/compose/frontend/src/features/editor/` — integrate library pickers (RolePicker, SchemaPicker, TemplateBrowser) — **NO PluginPicker** [D-GR-29]
- Create: `tools/compose/frontend/src/features/editor/hooks/useWorkflowExport.ts` — YAML download via `GET /api/workflows/{id}/export`
- Create: `tools/compose/frontend/src/features/editor/hooks/useWorkflowImport.ts` — YAML upload via `POST /api/workflows/import` with YAMLContractErrorPanel (CMP-19) integration
- Create: `tools/compose/frontend/src/features/editor/hooks/useAutoSave.ts` — 30s debounced auto-save with 401/429 error handling
- Wire: onPromoteRole, onPromoteSchema, onSaveTemplate mutation flows
- Wire: workflow validation (client-side + `POST /api/workflows/{id}/validate`)

**Acceptance Criteria:**
- `GET /api/workflows/templates` returns 3 starter templates (Planning, Develop, Bugfix) identified by `user_id='__system__'`
- "Duplicate" on a starter template creates user-owned workflow + WorkflowVersion v1
- Opening a duplicated workflow renders all phases, nodes, edges correctly on canvas
- Starter templates display their declared `inputs` in the workflow-level inspector (e.g., planning shows `scope: ScopeOutput`)
- Role picker in Ask inspector shows all roles (library + inline)
- "Save to Library" promotes inline role → toast → immediately available in all pickers
- Validation runs on save and manual trigger → errors appear on canvas + YAMLContractErrorPanel (CMP-19)
- YAML export downloads valid workflow file including `inputs`/`outputs` declarations
- Auto-save: 30s inactivity debounce, handles 401 (deep link + re-auth) and 429 (rate limit toast + retry) gracefully

**Counterexamples:**
- Do NOT reference PluginPicker — no plugin UI in scope [D-GR-29]
- Do NOT use `is_example` flag — starter templates identified by `user_id='__system__'`
- Do NOT use a seed.py script — starter data seeded via Alembic data migration (revision 0002)
- Do NOT allow editing system-owned starter template rows — user must duplicate first

### STEP-45: iriai-build-v2 Additive Integration (SF-4)

**Objective:** Add declarative runner support to iriai-build-v2 so it can load and execute YAML workflow definitions alongside its existing Python workflows. The integration layer must bridge iriai-build-v2's invocation context to the declarative runner's `inputs` parameter [D-A9].

**Source of Truth:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-migration/plan.md`

**Scope:**
- Modify: `iriai-build-v2/pyproject.toml` — add `iriai-compose` dependency (pin to version with declarative module)
- Create: `iriai-build-v2/src/iriai_build_v2/declarative/` — thin integration layer
  - `runner.py` — wraps `iriai_compose.declarative.run()` with iriai-build-v2's TrackedWorkflowRunner, ClaudeAgentRuntime, services dict. Maps iriai-build-v2 invocation context (feature, state) to `run(workflow, config, inputs={"scope": state.scope_output})` [D-A9]
  - `plugins.py` — registers iriai-build-v2's services as plugins (ArtifactMirror → store, DocHostingService → hosting, etc.)
  - `__init__.py` — public API: `run_declarative_workflow(yaml_path, feature, state)`
- Read: `iriai-build-v2/src/iriai_build_v2/workflows/_runner.py` (TrackedWorkflowRunner)
- Read: `iriai-build-v2/src/iriai_build_v2/runtimes/claude.py` (ClaudeAgentRuntime)
- Read: `iriai-build-v2/src/iriai_build_v2/services/` (all service implementations)

**Acceptance Criteria:**
- `from iriai_build_v2.declarative import run_declarative_workflow` succeeds
- A minimal YAML workflow executes via iriai-build-v2's ClaudeAgentRuntime
- `run_declarative_workflow()` maps iriai-build-v2's state to `inputs=` dict matching the workflow's `WorkflowInputDefinition` declarations
- iriai-build-v2 services (ArtifactMirror, DocHostingService, WorkspaceManager) registered as plugins
- Existing Python workflow API completely unchanged — declarative is additive only

**Counterexamples:**
- Do NOT modify any existing workflow files (planning.py, develop.py, bugfix.py)
- Do NOT change TrackedWorkflowRunner — wrap it
- Do NOT remove or deprecate the Python subclass API
- Do NOT hardcode input mapping — read `WorkflowConfig.inputs` to determine expected keys

---

### STEP-46: Tools Hub SPA (SF-5)

**Objective:** Build the lightweight static SPA for tools.iriai.app with navy blue XP theme and tool cards.

**Source of Truth:** Design Decisions Section 11 — Tools Hub

**Scope:**
- Create: `platform/toolshub/frontend/` (Vite React project)
  - Layout: ToolsHubLayout (split two-panel, navy blue)
  - Components: BrandingPanel (left 45%), ToolCardGrid + ToolCard (right 55%)
  - Auth: `@homelocal/auth` with `tools_` token prefix
  - MobileBlockScreen at <768px
  - `package.json`, `vite.config.ts`, `Dockerfile`

**Design (per D-19, D-20, D-30):**
- Navy blue theme: `--hub-bg: #0f172a`, `--hub-accent: #3b82f6`
- Clone deploy-console LandingPage.tsx flex layout
- Tool cards: Workflow Composer (links to compose.iriai.app), future tools
- No deploy platform access, no back-link from compose

**Acceptance Criteria:**
- Navigate to tools.iriai.app → navy split-pane layout
- "Workflow Composer" card links to compose.iriai.app (one-way)
- Locked tools show at 50% opacity with padlock
- Responsive: <768px blocked, 768-1023px stacked, >=1024px side-by-side

---

## Architectural Risks

| ID | Risk | Severity | Mitigation | Affected Steps |
|----|------|----------|------------|----------------|
| RISK-81 | Schema expressiveness — some iriai-build-v2 patterns may not map cleanly to 3 node types + 4 phase modes | High | SF-1 plan validated 145+ nodes; SF-4 migration tests as litmus test | STEP-36, STEP-39 |
| RISK-82 | React Flow performance with 35-60 nodes + nested phases | Medium | Virtualization, collapsed phases reduce visible nodes, lazy rendering | STEP-42 |
| RISK-83 | Inline Python transforms security (exec()) | Medium | AST allowlist + 5s timeout + 10,000 char limit per D-GR-5 (NOT bare exec) | STEP-37 |
| RISK-84 | Auth issuer mismatch (JWT validation URL) | High if misconfigured | Use `AUTH_SERVICE_PUBLIC_URL` env var, document in deployment checklist | STEP-40 |
| RISK-85 | PostgreSQL migration chain integrity with psycopg3 | Medium | Isolated `alembic_version_compose` table, `ROLLBACK.md` documents downgrade, init script handles first-run | STEP-40 |
| RISK-86 | Cross-repo coordination — 5 repos must stay compatible | Medium | Pin iriai-compose version in consumers, schema_version field in YAML | STEP-45 |
| RISK-87 | `__system__` sentinel user_id spoofing via crafted JWT | High if unguarded | Auth dependency rejects JWT with `sub='__system__'` → 403 | STEP-40 |
| RISK-88 | Starter template YAML becomes stale vs iriai-compose schema evolution | Medium | Templates seeded via versioned Alembic migration; schema changes require new migration to update | STEP-40 |
| RISK-89 | Mutation hook callback failures silently lost | Low | Hook registry catches + logs exceptions via structlog; SF-7 reconciliation job re-syncs stale refs [D-GR-39] | STEP-40, STEP-43 |
| RISK-90 | Rate limiting false positives for legitimate heavy users | Low | Per-user keying (JWT sub, not IP), reasonable limits (100/min), 429 with Retry-After guidance | STEP-40, STEP-41 |

## Environment Variables

| Name | Service | Default | Purpose |
|------|---------|---------|--------|
| DATABASE_URL | compose-backend | — | PostgreSQL connection (auto-normalized: `postgresql://` → `postgresql+psycopg://`) [D-GR-28] |
| AUTH_SERVICE_PUBLIC_URL | compose-backend | — | Public URL for JWT issuer validation (NOT internal Railway URL) |
| AUTH_JWKS_URL | compose-backend | — | JWKS endpoint for RS256 key discovery |
| CORS_ORIGINS | compose-backend | `https://compose.iriai.app` | Allowed CORS origins (comma-separated) |
| RATE_LIMIT_ENABLED | compose-backend | `true` | Toggle rate limiting (disable for dev/test) |
| LOG_LEVEL | compose-backend | `INFO` | structlog minimum log level |
| VITE_API_URL | compose-frontend | `/api` | Backend API base URL |
| VITE_AUTH_CLIENT_ID | compose-frontend | — | OAuth client ID |
| VITE_AUTH_URL | compose-frontend | — | OAuth authorization endpoint |
| VITE_AUTH_CLIENT_ID | toolshub-frontend | — | OAuth client ID (tools hub) |
| VITE_COMPOSE_URL | toolshub-frontend | `https://compose.iriai.app` | Compose app URL for tool card link |


---

## D-GR Compliance Checklist

| Decision | Status | Notes |
|----------|--------|-------|
| D-GR-22 (/api/schema/workflow canonical) | ✅ Compliant | STEP-40: `GET /api/schema/workflow` serves `WorkflowConfig.model_json_schema()`; no static workflow-schema.json |
| D-GR-27 (tools/compose/ topology) | ✅ Compliant | All file paths use `tools/compose/backend` and `tools/compose/frontend` |
| D-GR-28 (PostgreSQL + Alembic + psycopg) | ✅ Compliant | STEP-40: psycopg[binary] driver, `postgresql+psycopg://` normalization, `alembic_version_compose` table |
| D-GR-29 (5 tables only) | ✅ Compliant | STEP-40: workflows, workflow_versions, roles, output_schemas, custom_task_templates. No PluginType/PluginInstance. workflow_entity_refs delegated to SF-7 |
| D-GR-30 (closed root set, agent\|human actors) | ✅ Compliant | Schema served by /api/schema/workflow reflects canonical SF-1 PRD wire shape from iriai-compose |
| D-GR-33 (blocking schema gate) | ✅ Compliant | STEP-41: CMP-18 EditorSchemaBootstrapGate blocks editor until schema loads successfully |
| D-GR-36 (tools/compose/ canonical path) | ✅ Compliant | All paths under tools/compose/ (same as D-GR-27) |
| D-GR-38 (no view-only fallback) | ✅ Compliant | STEP-41: CMP-18 error state shows blocking retry panel — zero canvas, palette, or inspector rendering |
| D-GR-42 (compliance checklist) | ✅ Compliant | This checklist |
| D-GR-6 (@iriai/ui deferred) | ✅ Compliant | STEP-41: XP components vendored from deploy-console, no shared @iriai/ui package |
| D-GR-11 (SF-7 owns primitives) | ✅ Compliant | STEP-41 does NOT implement CMP-30–47 visual primitives |
| D-GR-1 (Design+Plan authoritative) | ✅ Compliant | Plan aligned to PRD and Design; D-GR decisions applied as hard requirements |
| D-GR-5 (AST-validated exec) | N/A | SF-2 concern; SF-5 serves schema from iriai-compose without modification |
| D-GR-13 (ErrorNode 4th type) | N/A | Schema-level concern; served via /api/schema/workflow from iriai-compose |
| D-GR-14 (ArtifactPlugin, no artifact_key) | N/A | Schema-level concern; served via /api/schema/workflow from iriai-compose |
| D-GR-35 (per-port BranchNode) | N/A | Schema-level concern; served via /api/schema/workflow from iriai-compose |

**SF-5 Mutation Hook Compliance:**
- ✅ All 4 entity types emit hooks (Workflow, Role, OutputSchema, CustomTaskTemplate)
- ✅ Exactly 4 event kinds: `created`, `updated`, `soft_deleted`, `restored`
- ✅ `imported` maps to `created` (not a separate event)
- ✅ `version_saved` does NOT trigger hooks
- ✅ SF-5 never creates/updates `workflow_entity_refs` rows

**SF-5 Starter Template Compliance:**
- ✅ Seeded as DB rows via Alembic data migration (not seed.py, not filesystem)
- ✅ Identified by `user_id='__system__'` (not `is_example` flag)
- ✅ Returned by `GET /api/workflows/templates` only
- ✅ System rows never soft-deleted by user actions
- ✅ `__system__` sentinel guarded against JWT spoofing (403)



---

## Subfeature: Workflow Editor & Canvas (workflow-editor)

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
| D-SF6-8 | BranchNode uses ONLY D-GR-35 per-port non-exclusive fan-out — `switch_function` is REJECTED | D-GR-35 is authoritative: each entry in the dict-keyed `outputs` (or `paths`) map carries its own `condition` expression string evaluated independently at runtime. Multiple paths can fire simultaneously if their conditions are met. There is NO dual routing model, NO `switch_function` field, NO `SwitchFunctionEditor`, NO routing-mode toggle. `switch_function`, `output_field`, and node-level `condition_type`/`condition` are rejected completely — they must never appear as implementation instructions, only as rejection rules in validation. `merge_function` remains valid for multi-input gather. BranchInspector shows per-port condition expression editors via `OutputPathsEditor` + `PortConditionRow` — no alternative mode exists. | [decision: D-GR-35 per-port non-exclusive fan-out]; [decision: D-28 Branch = programmatic switch] |
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
│   │   ├── ConditionBadge.tsx     # STEP-49: Amber pill — "⑂ per-port conditions" label for BranchNode card face
│   │   ├── StatusIndicator.tsx  # STEP-49: Dot + status text
│   │   ├── ErrorBadge.tsx       # STEP-49: Red circle with error count
│   │   └── CollapsedGroupCard.tsx   # STEP-49: Shared collapsed card for phases + templates
│   ├── AskNode.tsx              # STEP-50: Purple Ask node component
│   ├── BranchNode.tsx           # STEP-50: Amber Branch node component — per-port conditions only
│   ├── PluginNode.tsx           # STEP-50: Gray Plugin node component
│   ├── ErrorNode.tsx            # STEP-50: Red Error node component (D-GR-36 4th atomic type)
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
│   ├── BranchInspector.tsx      # STEP-55: Amber titlebar, per-port conditions only
│   ├── PluginInspector.tsx      # STEP-55: Gray titlebar, plugin config
│   ├── PhaseInspector.tsx       # STEP-55: Mode-colored, mode config
│   ├── EdgeInspector.tsx        # STEP-56: Data ~500px / Hook ~280px
│   ├── InspectorActions.tsx     # STEP-55: Footer action buttons
│   ├── PromptTemplateEditor.tsx # STEP-55: {{ }} autocomplete
│   ├── InlineRoleCreator.tsx    # STEP-55: Tier 1 role editor
│   ├── InlineOutputSchemaCreator.tsx  # STEP-55: Field-by-field schema
│   ├── OutputPathsEditor.tsx    # STEP-55: Branch paths → ports, per-port condition editors (D-GR-35)
│   ├── ErrorInspector.tsx       # STEP-55: Red titlebar, message template editor (D-GR-36)
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
│   ├── clientValidator.ts       # STEP-57: Structural validation incl. stale-field rejection (switch_function, output_field, plugin_instances)
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
  type_ref?: string;
  schema_def?: Record<string, unknown>;
  description?: string;
  required?: boolean;
}

export interface BranchOutputPort extends PortDefinition {
  condition: string; // Per-port condition expression (D-GR-35) — evaluated independently at runtime
}

export interface EdgeDefinition {
  source: string;       // "node_id.port_name"
  target: string;       // "node_id.port_name"
  transform_fn?: string; // inline Python — D-19
}

export interface NodeDefinition {
  id: string;
  type: 'ask' | 'branch' | 'plugin' | 'error'; // D-GR-36: ErrorNode is the 4th atomic type
  summary?: string;
  context_keys?: string[];
  context_text?: Record<string, string>;
  artifact_key?: string;
  input_type?: string;
  input_schema?: Record<string, unknown>;
  output_type?: string;
  output_schema?: Record<string, unknown>;
  inputs?: Record<string, PortDefinition>;    // dict-keyed
  outputs?: Record<string, PortDefinition>;   // dict-keyed (Ask/Plugin)
  hooks?: Record<string, PortDefinition>;     // dict-keyed
  position?: { x: number; y: number };

  // Ask-specific
  actor?: string;
  inline_role?: InlineRoleDefinition;
  prompt?: string;

  // Branch-specific (D-GR-35: per-port conditions ONLY — NO switch_function, NO dual routing)
  paths?: Record<string, BranchOutputPort>;   // dict-keyed branch output paths with per-port conditions
  merge_function?: string | null;             // Python expression merging multi-port inputs (gather only)
  // REJECTED: switch_function, output_field, node-level condition_type, node-level condition

  // Error-specific (D-GR-36: ErrorNode is a terminal node)
  message?: string; // Jinja2 template for error message

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
  // pluginInstances removed — plugin_instances is rejected at root level; plugin config is inline on PluginNode.plugin_config
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
    // pluginInstances removed — not a valid root field
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
  // pluginInstances removed — plugin_instances is rejected at root level; plugin config is inline on PluginNode.plugin_config
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
      // pluginInstances removed — not a valid root field
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
- BranchNode serialization preserves dict-keyed `paths` with per-port `condition` fields; `switch_function` is never emitted
- ErrorNode serialization emits `type: 'error'`, `message`, and dict-keyed `inputs` with NO `outputs` and NO `hooks` (D-GR-36)

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
  error: ErrorNodePlaceholder,       // D-GR-36: 4th atomic type
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
| `features/editor/nodes/shared/ConditionBadge.tsx` | create |
| `features/editor/nodes/shared/StatusIndicator.tsx` | create |
| `features/editor/nodes/shared/ErrorBadge.tsx` | create |
| `features/editor/nodes/shared/CollapsedGroupCard.tsx` | create |

**Instructions:**

Components 1-7 and 9-10 (NodeCard, SocketPort, ActorSlot, NodeSummary, ContextKeys, ArtifactKey, PromptPreview, StatusIndicator, ErrorBadge) remain identical to the original plan — see original STEP-49 for full specifications. All are `React.memo` wrapped with `data-testid` attributes.

**8. ConditionBadge.tsx — Per-port condition summary badge**

This component shows a compact per-port condition summary on the BranchNode card face. There is only one routing model (D-GR-35 per-port conditions), so this badge shows either "per-port conditions" when at least one output has a condition set, or "no conditions set" when outputs exist but none have conditions yet.

```tsx
import React from 'react';

interface ConditionBadgeProps {
  hasAnyCondition: boolean;
  conditionCount: number;
  pathCount: number;
}

export const ConditionBadge = React.memo<ConditionBadgeProps>(
  function ConditionBadge({ hasAnyCondition, conditionCount, pathCount }) {
    if (hasAnyCondition) {
      return (
        <div
          data-testid="condition-badge"
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 4,
            background: 'rgba(245, 158, 11, 0.08)', borderRadius: 4,
            padding: '2px 8px', fontSize: '0.6875rem',
            color: '#92400e',
          }}
        >
          <span style={{ fontWeight: 600 }}>⑂</span>
          <span>{conditionCount}/{pathCount} conditions set</span>
        </div>
      );
    }

    return (
      <div
        data-testid="condition-badge-empty"
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 4,
          background: 'rgba(245, 158, 11, 0.06)', borderRadius: 4,
          padding: '2px 8px', fontSize: '0.6875rem',
          color: '#9ca3af', fontStyle: 'italic',
        }}
      >
        no conditions set
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
- ConditionBadge shows "N/M conditions set" when per-port conditions exist, "no conditions set" when none are configured

**Counterexamples:**
- DO NOT use MiniTopologyPreview — it does not exist in this plan [D-U1, D-U2]
- DO NOT make SocketPort labels hover-only [D-49]
- DO NOT render ActorSlot as rectangular dashed box [D-51]
- DO NOT show output schema on NodeCard face [D-50]
- DO NOT create a SwitchFunctionLabel component — `switch_function` is REJECTED per D-GR-35

**Citations:**
- [decision: D-29/D-47] All nodes 260-280px rectangular cards
- [decision: D-49] All ports uniform 12px, always-visible labels
- [decision: D-U1] Phases use collapsed card, not thumbnail
- [decision: D-U2] Templates use same collapsed card pattern
- [decision: D-GR-35] Per-port non-exclusive fan-out only

---

### STEP-50: Custom Node Components (Ask, Branch, Plugin) + TemplateGroup

**Objective:** Build the 4 atomic node components + the TemplateGroup collapsible container. Atomic nodes use STEP-49 primitives and are memoized components registered in `nodeTypes`. TemplateGroup is a React Flow group node that renders as CollapsedGroupCard when collapsed and as a green-bordered container with read-only children when expanded. BranchNode card face shows per-port condition summaries (D-GR-35 only). ErrorNode is the 4th atomic type per D-GR-36, rendered as a red-themed terminal node.

**Requirement IDs:** REQ-13
**Journey IDs:** J-16, J-18, J-19

**Scope:**
| Path | Action |
|------|--------|
| `features/editor/nodes/AskNode.tsx` | create |
| `features/editor/nodes/BranchNode.tsx` | create |
| `features/editor/nodes/PluginNode.tsx` | create |
| `features/editor/nodes/ErrorNode.tsx` | create |
| `features/editor/nodes/TemplateGroup.tsx` | create |
| `features/editor/nodes/nodeTypes.ts` | modify |

**Instructions:**

**1. AskNode, 3. PluginNode** — identical to original plan STEP-50 specifications.

**2. BranchNode.tsx — Amber Branch node with per-port condition indicators (D-GR-35)**

```tsx
function BranchNodeComponent({ id, data, selected }: NodeProps) {
  const isReadOnly = data._readOnly === true;

  // Per-port conditions only — NO switch_function, NO dual routing (D-GR-35)
  const paths = data.paths ?? { path_1: { condition: '' }, path_2: { condition: '' } };
  const pathEntries = Object.entries(paths);
  const conditionCount = pathEntries.filter(([, port]) => port.condition?.trim()).length;
  const hasAnyCondition = conditionCount > 0;

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

          {/* Per-port condition badge (D-GR-35 — only model) */}
          <ConditionBadge
            hasAnyCondition={hasAnyCondition}
            conditionCount={conditionCount}
            pathCount={pathEntries.length}
          />

          {/* Output paths list — shows port names + truncated condition previews */}
          <div data-testid={`branch-node-${id}-paths-list`} style={{ marginTop: 4 }}>
            {pathEntries.map(([pathKey, port]) => (
              <div key={pathKey} style={{
                display: 'flex', alignItems: 'center', gap: 4,
                padding: '2px 0', fontSize: '0.6875rem',
              }}>
                <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#f59e0b', flexShrink: 0 }} />
                <span style={{ color: '#1e293b', fontWeight: 500 }}>{pathKey}</span>
                {port.condition?.trim() && (
                  <span style={{
                    color: '#9ca3af', fontFamily: 'monospace', fontSize: '0.625rem',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    maxWidth: 120,
                  }}>
                    if {port.condition}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Multiple input ports (left) — data only, dict-keyed */}
        {Object.entries(data.inputs ?? { input: {} }).map(([portKey]) => (
          <SocketPort key={portKey} id={`${id}-${portKey}-in`} position="left" portType="data-in" label={portKey} />
        ))}

        {/* Output ports (right) — one per path key = handle ID = edge source port name */}
        {pathEntries.map(([pathKey]) => (
          <SocketPort key={pathKey} id={`${id}-${pathKey}-out`} position="right" portType="data-out" label={pathKey} />
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

**Key D-GR-35 details:**
- Card face shows `ConditionBadge` with condition count summary
- Per-port condition mode is the ONLY mode: output paths always show name + truncated condition preview ("if data.approved")
- No SwitchFunctionLabel, no switch_function, no routing-mode toggle
- Each path key is both the output Handle ID and the serialized edge source port name

**3b. ErrorNode.tsx — Red Error terminal node (D-GR-36)**

```tsx
function ErrorNodeComponent({ id, data, selected }: NodeProps) {
  const isReadOnly = data._readOnly === true;

  return (
    <div data-testid={`error-node-${id}`} data-type="error">
      <NodeCard
        id={id}
        type="error"
        name={data.label || data.name || 'Error'}
        headerColor="#ef4444"
        selected={selected}
        errorCount={data.ui?.validationErrors?.length}
      >
        <div style={{ opacity: isReadOnly ? 0.85 : 1, pointerEvents: isReadOnly ? 'none' : 'auto' }}>
          {/* Message template preview */}
          {data.message && (
            <div
              data-testid={`error-node-${id}-message-preview`}
              style={{
                fontFamily: 'monospace', fontSize: '0.6875rem',
                color: '#991b1b', background: 'rgba(239, 68, 68, 0.06)',
                padding: '4px 6px', borderRadius: 4,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                maxWidth: '100%',
              }}
            >
              {data.message.length > 60 ? data.message.slice(0, 57) + '...' : data.message}
            </div>
          )}
          {!data.message && (
            <div style={{ fontSize: '0.625rem', color: '#9ca3af', fontStyle: 'italic' }}>
              No error message configured
            </div>
          )}
        </div>

        {/* Input ports (left) — data only, dict-keyed */}
        {Object.entries(data.inputs ?? { input: {} }).map(([portKey]) => (
          <SocketPort key={portKey} id={`${id}-${portKey}-in`} position="left" portType="data-in" label={portKey} />
        ))}

        {/* NO output ports — ErrorNode is a terminal node (D-GR-36) */}
        {/* NO hook ports — ErrorNode has no hooks (D-GR-36) */}
      </NodeCard>
    </div>
  );
}

export const ErrorNode = React.memo(ErrorNodeComponent, (prev, next) =>
  prev.data === next.data && prev.selected === next.selected
);
```

**4. TemplateGroup.tsx** — Unchanged from original plan.

**5. Update nodeTypes.ts** — Unchanged from original plan.

**5. Update nodeTypes.ts** — Register all 4 atomic types plus containers:

```typescript
export const nodeTypes: NodeTypes = {
  ask: AskNode,
  branch: BranchNode,
  plugin: PluginNode,
  error: ErrorNode,           // D-GR-36: 4th atomic type
  phase: PhaseContainer,
  'template-group': TemplateGroup,
};
```

**Acceptance Criteria:**
- All original acceptance criteria from STEP-50 remain valid
- BranchNode card face shows `ConditionBadge` with "N/M conditions set" when per-port conditions are configured
- BranchNode card face shows "no conditions set" when no conditions are configured yet
- Each output path row shows name + truncated condition preview for paths that have conditions
- Output port Handle IDs on BranchNode match the dict keys from `paths`
- ErrorNode renders as a red-themed (#ef4444) terminal card with message preview (D-GR-36)
- ErrorNode has input ports but NO output ports and NO hook ports
- ErrorNode appears in palette alongside Ask, Branch, Plugin
- `nodeTypes` registers all 4 atomic types: ask, branch, plugin, error

**Counterexamples:**
- DO NOT use MiniTopologyPreview [D-U1, D-U2]
- DO NOT render Branch as diamond [D-47]
- DO NOT show actor slot on Branch or Plugin [D-28]
- DO NOT show output_schema on card face [D-50]
- DO NOT allow editing of _readOnly nodes [D-U3]
- DO NOT maintain a live link between template-group and library template [D-SF6-7]
- DO NOT create SwitchFunctionLabel or any switch_function UI — `switch_function` is REJECTED per D-GR-35
- DO NOT show any routing-mode toggle on BranchNode — only per-port conditions exist
- DO NOT add output ports or hook ports to ErrorNode — it is a terminal node (D-GR-36)

**Citations:**
- [decision: D-SF6-7] Stamp-and-detach templates
- [decision: D-GR-35] Per-port non-exclusive fan-out only
- [decision: D-GR-36] ErrorNode is the 4th atomic type
- [decision: D-U2] Templates expand to real nodes
- [decision: D-U3] Read-only but inspectable
- [decision: D-47] All nodes rectangular cards
- [decision: D-50] Card face metadata
- [decision: D-28/D-46] Branch = programmatic switch, no actor

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

**Objective:** Build inspector content for all 4 node types + Error + phase. Each renders inside InspectorWindow with type-colored titlebar. All field changes debounced 500ms → push undo snapshot → update node data. Read-only mode disables all fields when `inspector.readOnly === true`. BranchInspector uses ONLY the D-GR-35 per-port condition model: `OutputPathsEditor` with `PortConditionRow` per path, plus optional `MergeFunctionEditor` for multi-input gather. There is NO `SwitchFunctionEditor`, NO routing-mode toggle. ErrorInspector edits the Jinja2 message template (D-GR-36).

**Requirement IDs:** REQ-13, REQ-14
**Journey IDs:** J-16, J-17, J-18

**Scope:**
| Path | Action |
|------|--------|
| `features/editor/inspectors/AskInspector.tsx` | create |
| `features/editor/inspectors/BranchInspector.tsx` | create |
| `features/editor/inspectors/PluginInspector.tsx` | create |
| `features/editor/inspectors/ErrorInspector.tsx` | create |
| `features/editor/inspectors/PhaseInspector.tsx` | create |
| `features/editor/inspectors/InspectorActions.tsx` | create |
| `features/editor/inspectors/PromptTemplateEditor.tsx` | create |
| `features/editor/inspectors/InlineRoleCreator.tsx` | create |
| `features/editor/inspectors/InlineOutputSchemaCreator.tsx` | create |
| `features/editor/inspectors/OutputPathsEditor.tsx` | create |
| `features/editor/inspectors/CodeEditor.tsx` | create |

**Instructions:**

All inspectors accept `readOnly: boolean` prop from InspectorWindow. When `true`, all form elements are disabled and InspectorActions is not rendered.

**AskInspector** (~280px, purple), **PluginInspector** (~280px, gray), **PhaseInspector** (mode-colored) — unchanged from original plan.

**CodeEditor** — unchanged from original plan (shared `@uiw/react-codemirror` wrapper, lazy-loaded).

**BranchInspector.tsx (~280px, amber titlebar) — Per-port conditions only (D-GR-35)**

BranchInspector uses ONLY the D-GR-35 per-port non-exclusive fan-out model. There is NO `SwitchFunctionEditor`, NO routing-mode toggle, NO `switch_function` field. The inspector shows `OutputPathsEditor` with one `PortConditionRow` per dict-keyed path, and optional `MergeFunctionEditor` for multi-input gather.

```tsx
import React, { useCallback } from 'react';
import { OutputPathsEditor } from './OutputPathsEditor';
import { MergeFunctionEditor } from './MergeFunctionEditor';
import { InspectorActions } from './InspectorActions';

interface BranchInspectorProps {
  nodeId: string;
  data: BranchNodeData;
  readOnly: boolean;
  onUpdateData: (patch: Partial<BranchNodeData>) => void;
}

export function BranchInspector({ nodeId, data, readOnly, onUpdateData }: BranchInspectorProps) {
  // D-GR-35: per-port conditions ONLY — no switch_function, no routing-mode toggle
  const inputCount = Object.keys(data.inputs ?? {}).length;

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

      {/* Per-port condition hint (D-GR-35 — only routing model) */}
      <div data-testid={`branch-inspector-${nodeId}-routing-section`} style={{ marginTop: 12, borderTop: '1px solid #e5e7eb', paddingTop: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
          <span style={{ fontWeight: 600, fontSize: '0.75rem', color: '#92400e' }}>Output Paths</span>
        </div>

        <div
          data-testid={`branch-inspector-${nodeId}-condition-mode-hint`}
          style={{ fontSize: '0.625rem', color: '#6b7280', marginBottom: 6, fontStyle: 'italic' }}
        >
          Each port evaluates its condition independently — all matching ports fire.
        </div>
      </div>

      {/* Output Paths Editor — per-port conditions always shown (D-GR-35) */}
      <OutputPathsEditor
        nodeId={nodeId}
        paths={data.paths ?? { path_1: { condition: '' }, path_2: { condition: '' } }}
        readOnly={readOnly}
        onChange={(paths) => onUpdateData({ paths })}
      />

      {/* Merge function — only for multi-input gather (D-GR-35) */}
      {inputCount > 1 && (
        <MergeFunctionEditor
          nodeId={nodeId}
          mergeFunction={data.merge_function ?? ''}
          inputPorts={data.inputs ?? {}}
          onChange={(val) => onUpdateData({ merge_function: val })}
          readOnly={readOnly}
        />
      )}

      {/* NO actor field [D-28] */}
      {/* NO SwitchFunctionEditor — switch_function is REJECTED per D-GR-35 */}
      {/* NO routing-mode toggle — only per-port conditions exist */}

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

**ErrorInspector.tsx (~280px, red titlebar) — Error message template editor (D-GR-36)**

ErrorInspector edits the Jinja2 message template and dict-keyed inputs on an ErrorNode. ErrorNode has no outputs and no hooks.

```tsx
import React from 'react';
import { CodeEditor } from './CodeEditor';
import { InspectorActions } from './InspectorActions';

interface ErrorInspectorProps {
  nodeId: string;
  data: ErrorNodeData;
  readOnly: boolean;
  onUpdateData: (patch: Partial<ErrorNodeData>) => void;
}

export function ErrorInspector({ nodeId, data, readOnly, onUpdateData }: ErrorInspectorProps) {
  return (
    <div data-testid={`error-inspector-${nodeId}`}>
      {/* Message template (Jinja2) */}
      <label data-testid={`error-inspector-${nodeId}-message-label`}>Error Message Template</label>
      <CodeEditor
        data-testid={`error-inspector-${nodeId}-message-editor`}
        value={data.message ?? ''}
        onChange={(val) => onUpdateData({ message: val })}
        readOnly={readOnly}
        language="jinja2"
        placeholder="Error: {{ reason }}"
        height={100}
      />

      <div style={{ fontSize: '0.625rem', color: '#6b7280', marginTop: 4, fontStyle: 'italic' }}>
        Jinja2 template — input variables are available as template context.
      </div>

      {/* NO output ports section — ErrorNode is terminal (D-GR-36) */}
      {/* NO hooks section — ErrorNode has no hooks (D-GR-36) */}

      {!readOnly && (
        <InspectorActions
          nodeId={nodeId}
          nodeType="error"
          data-testid={`error-inspector-${nodeId}-actions`}
        />
      )}
    </div>
  );
}
```

**OutputPathsEditor.tsx — Dict-keyed output paths with per-port condition editors (D-GR-35)**

This component manages the dict-keyed output paths on a BranchNode. Per-port condition editors are ALWAYS shown (there is no alternative mode). Each path entry is rendered as a `PortConditionRow` with path name editor + condition expression editor.

```tsx
import React, { useCallback } from 'react';

interface OutputPathsEditorProps {
  nodeId: string;
  paths: Record<string, BranchOutputPort>;
  readOnly: boolean;
  onChange: (paths: Record<string, BranchOutputPort>) => void;
}

export function OutputPathsEditor({
  nodeId, paths, readOnly, onChange,
}: OutputPathsEditorProps) {
  const pathEntries = Object.entries(paths);

  const handleAddPath = useCallback(() => {
    const newKey = `path_${pathEntries.length + 1}`;
    onChange({ ...paths, [newKey]: { condition: '' } });
  }, [paths, pathEntries.length, onChange]);

  const handleRemovePath = useCallback((key: string) => {
    if (pathEntries.length <= 2) return; // BranchNode requires min 2 output paths
    const { [key]: _, ...rest } = paths;
    onChange(rest);
  }, [paths, pathEntries.length, onChange]);

  const handleRenamePath = useCallback((oldKey: string, newKey: string) => {
    if (newKey === oldKey) return;
    const entries = Object.entries(paths).map(([k, v]) =>
      k === oldKey ? [newKey, v] : [k, v]
    );
    onChange(Object.fromEntries(entries));
  }, [paths, onChange]);

  const handleConditionChange = useCallback((key: string, condition: string) => {
    onChange({ ...paths, [key]: { ...paths[key], condition } });
  }, [paths, onChange]);

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

      {pathEntries.map(([pathKey, port], index) => (
        <div
          key={pathKey}
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
              value={pathKey}
              onChange={(e) => handleRenamePath(pathKey, e.target.value)}
              disabled={readOnly}
              style={{
                flex: 1, fontWeight: 500, fontSize: '0.75rem',
                border: 'none', background: 'transparent', padding: '2px 4px',
                color: '#1e293b',
              }}
              placeholder="path_name"
            />
            {!readOnly && pathEntries.length > 2 && (
              <button
                data-testid={`branch-inspector-${nodeId}-path-${index}-remove`}
                onClick={() => handleRemovePath(pathKey)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#ef4444', fontSize: '0.75rem', padding: 0 }}
                aria-label={`Remove path ${pathKey}`}
              >
                ×
              </button>
            )}
          </div>

          {/* Per-port condition editor — ALWAYS shown (D-GR-35, no alternative mode) */}
          <div style={{ marginTop: 4 }}>
            <label style={{ fontSize: '0.5625rem', color: '#6b7280', display: 'block', marginBottom: 2 }}>
              Condition (Python expression -> bool)
            </label>
            <input
              data-testid={`branch-inspector-${nodeId}-path-${index}-condition`}
              value={port.condition ?? ''}
              onChange={(e) => handleConditionChange(pathKey, e.target.value)}
              disabled={readOnly}
              style={{
                width: '100%', fontFamily: 'monospace', fontSize: '0.6875rem',
                padding: '4px 6px', border: '1px solid #d1d5db', borderRadius: 4,
                background: readOnly ? '#f9fafb' : '#fff',
              }}
              placeholder='data.verdict == "approved"'
            />
          </div>
        </div>
      ))}

      {pathEntries.length < 2 && (
        <div style={{ color: '#ef4444', fontSize: '0.625rem', marginTop: 4 }}>
          Branch requires at least 2 output paths
        </div>
      )}
    </div>
  );
}
```

**Acceptance Criteria:**
- AskInspector shows actor controls, prompt editor, inline schema builder
- BranchInspector shows `OutputPathsEditor` with per-port condition editors for each dict-keyed path (D-GR-35)
- BranchInspector shows `MergeFunctionEditor` only when 2+ inputs exist
- BranchInspector has NO `SwitchFunctionEditor`, NO routing-mode toggle
- OutputPathsEditor manages dict-keyed paths (add, rename, reorder, remove) with condition editor per path
- ErrorInspector shows Jinja2 message template editor with no output or hook sections (D-GR-36)
- Phase mode change updates border immediately
- All fields disabled in read-only mode
- 500ms debounce on field changes
- NO actor field on BranchInspector [D-28]

**Counterexamples:**
- DO NOT put actor field on BranchInspector [D-28]
- DO NOT show output schema on card face [D-32]
- DO NOT eagerly load CodeMirror — lazy-load [RISK-91]
- DO NOT show action buttons in read-only inspectors [D-U3]
- DO NOT create SwitchFunctionEditor — `switch_function` is REJECTED per D-GR-35
- DO NOT create a routing-mode toggle — only per-port conditions exist (D-GR-35)
- DO NOT add output ports or hooks to ErrorInspector — ErrorNode is terminal (D-GR-36)

**Citations:**
- [decision: D-23] Two-tier role editing
- [decision: D-26] Inline output schema
- [decision: D-28] Branch = switch, no actor
- [decision: D-GR-35] Per-port non-exclusive fan-out only
- [decision: D-GR-36] ErrorNode is the 4th atomic type
- [decision: D-U3] Read-only inspectable

---

### STEP-56: Edge Inspector + CodeMirror Transform Editor

Unchanged from original plan.

---

### STEP-57: Client Validation + ValidationPanel + Server Integration

**Objective:** Build client-side structural validator, ValidationPanel UI, and wire Validate button to server. Includes `rejected_stale_branch_field` rule that flags any BranchNode carrying `switch_function`, `output_field`, node-level `condition_type`, or node-level `condition` as an error. Also includes `rejected_plugin_instances` rule that flags root-level `plugin_instances` as invalid.

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
- `missing_required_field` — Ask without actor, Branch < 2 output paths, ErrorNode without message
- `cycle_detected` — DFS cycle in edges
- `rejected_stale_branch_field` — BranchNode carries ANY of `switch_function`, `output_field`, node-level `condition_type`, or node-level `condition`. Error message: `"Branch '{nodeId}' contains rejected field '{field}'. Only per-port conditions on paths are valid (D-GR-35)."` These fields must NEVER appear as implementation — only as rejection targets.
- `rejected_plugin_instances` — Workflow root carries `plugin_instances` as a root field. Error message: `"Root-level 'plugin_instances' is not a valid workflow field."` `plugin_instances` must only appear as a rejection rule, not as implementation.
- `blank_branch_condition` — BranchNode has a path entry with blank or missing `condition` expression. Warning severity.
- `error_node_has_outputs` — ErrorNode has output ports or hooks defined. Error severity (D-GR-36).

```typescript
// In clientValidator.ts — stale field rejection (D-GR-35)

const REJECTED_BRANCH_FIELDS = ['switch_function', 'output_field', 'condition_type', 'condition'] as const;

function validateBranchNode(node: Node): ValidationIssue[] {
  if (node.type !== 'branch') return [];
  const issues: ValidationIssue[] = [];

  // D-GR-35: reject stale fields — switch_function, output_field, condition_type, condition at node level
  for (const field of REJECTED_BRANCH_FIELDS) {
    if (node.data[field] != null && String(node.data[field]).trim() !== '') {
      issues.push({
        code: 'rejected_stale_branch_field',
        path: `nodes.${node.id}.${field}`,
        message: `Branch '${node.data.label || node.id}' contains rejected field '${field}'. Only per-port conditions on paths are valid (D-GR-35).`,
        nodeId: node.id,
        severity: 'error',
      });
    }
  }

  // Min 2 output paths
  const pathCount = Object.keys(node.data.paths ?? {}).length;
  if (pathCount < 2) {
    issues.push({
      code: 'missing_required_field',
      path: `nodes.${node.id}.paths`,
      message: `Branch '${node.data.label || node.id}' requires at least 2 output paths (has ${pathCount}).`,
      nodeId: node.id,
      severity: 'error',
    });
  }

  // Blank conditions warning
  for (const [pathKey, port] of Object.entries(node.data.paths ?? {})) {
    if (!port.condition?.trim()) {
      issues.push({
        code: 'blank_branch_condition',
        path: `nodes.${node.id}.paths.${pathKey}.condition`,
        message: `Branch '${node.data.label || node.id}' path '${pathKey}' has no condition expression.`,
        nodeId: node.id,
        severity: 'warning',
      });
    }
  }

  return issues;
}

// D-GR-36: ErrorNode terminal validation
function validateErrorNode(node: Node): ValidationIssue[] {
  if (node.type !== 'error') return [];
  const issues: ValidationIssue[] = [];

  if (!node.data.message?.trim()) {
    issues.push({
      code: 'missing_required_field',
      path: `nodes.${node.id}.message`,
      message: `Error node '${node.data.label || node.id}' requires a message template.`,
      nodeId: node.id,
      severity: 'error',
    });
  }

  if (node.data.outputs && Object.keys(node.data.outputs).length > 0) {
    issues.push({
      code: 'error_node_has_outputs',
      path: `nodes.${node.id}.outputs`,
      message: `Error node '${node.data.label || node.id}' must not have output ports (terminal node).`,
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
- BranchNode carrying `switch_function` → `rejected_stale_branch_field` error shown
- BranchNode carrying `output_field` → `rejected_stale_branch_field` error shown
- BranchNode with valid per-port conditions → no errors
- BranchNode with blank conditions → `blank_branch_condition` warning per path
- ErrorNode without message → `missing_required_field` error
- ErrorNode with output ports → `error_node_has_outputs` error
- Root-level `plugin_instances` → `rejected_plugin_instances` error

**Counterexamples:**
- Validation must NOT block saving [J-5 NOT]
- Client validation must NOT call server [D-SF6-4]
- DO NOT treat `switch_function` as a valid field — always reject it as a stale artifact (D-GR-35)
- DO NOT treat `plugin_instances` as a valid root field — always reject it

**Citations:**
- [decision: D-SF6-4] Hybrid validation
- [decision: D-GR-35] Per-port non-exclusive fan-out only; switch_function rejected
- [decision: D-GR-36] ErrorNode is the 4th atomic type

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
| RISK-94 | Stale `switch_function` or `output_field` data in imported YAML — imported workflows from older schema versions may contain these rejected fields. | medium | Client validator flags `rejected_stale_branch_field` on any Branch node carrying stale fields. Import normalization strips `switch_function`, `output_field`, `condition_type`, and node-level `condition` during deserialization and shows a migration warning toast. | STEP-47, STEP-57, STEP-58 |
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
| 6. Add Branch node | Amber card appears with "no conditions set" badge | browser | branch-node-{id}, condition-badge-empty |
| 7. Configure per-port conditions | OutputPathsEditor shows condition inputs per path, card shows "N/M conditions set" | browser | branch-inspector-{id}-paths-list, condition-badge |
| 8. Ctrl+S | Green toast | browser | editor-toolbar-save |
| 9. Reload | All restored including per-port conditions | browser+api | editor-canvas |

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
| 5. Branch stale field rejection | `rejected_stale_branch_field` error for imported YAML with switch_function | browser | validation-panel, branch-inspector-{id}-routing-section |

## data-testid Registry

editor-canvas, editor-canvas-empty, editor-canvas-loading, editor-canvas-error, editor-toolbar, editor-toolbar-save, editor-toolbar-save-dirty, editor-toolbar-undo, editor-toolbar-redo, editor-toolbar-validate, editor-toolbar-export, editor-toolbar-hand, editor-toolbar-select, editor-toolbar-zoom-in, editor-toolbar-zoom-out, editor-toolbar-zoom-fit, editor-menu-file, editor-menu-edit, editor-menu-view, editor-palette, editor-palette-ask, editor-palette-branch, editor-palette-plugin, editor-palette-templates-section, editor-palette-roles-section, ask-node-{id}, ask-node-{id}-header, ask-node-{id}-actor-slot, ask-node-{id}-actor-slot-empty, ask-node-{id}-actor-slot-filled, ask-node-{id}-summary, ask-node-{id}-context-keys, ask-node-{id}-artifact-key, ask-node-{id}-prompt-preview, ask-node-{id}-error-badge, branch-node-{id}, branch-node-{id}-header, branch-node-{id}-summary, branch-node-{id}-context-keys, branch-node-{id}-paths-list, condition-badge, condition-badge-empty, error-node-{id}, error-node-{id}-message-preview, plugin-node-{id}, plugin-node-{id}-header, plugin-node-{id}-status, phase-{id}, phase-{id}-header, phase-{id}-mode-badge, phase-{id}-collapse-btn, phase-{id}-detach-btn, phase-{id}-name, collapsed-group-{id}, collapsed-group-{id}-expand-btn, collapsed-group-{id}-mode-badge, collapsed-group-{id}-template-badge, collapsed-group-{id}-node-count, collapsed-group-{id}-detach-btn, template-group-{id}, template-group-{id}-header, template-group-{id}-collapse-btn, template-group-{id}-detach-btn, port-{nodeId}-{portName}, port-{nodeId}-{portName}-label, edge-{id}, edge-{id}-label, edge-{id}-transform-icon, inspector-{elementId}, inspector-{elementId}-titlebar, inspector-{elementId}-close, inspector-{elementId}-tether, inspector-{elementId}-readonly-banner, ask-inspector-{id}-actor-dropdown, ask-inspector-{id}-actor-inline-btn, ask-inspector-{id}-actor-fullEditor-btn, ask-inspector-{id}-prompt-editor, ask-inspector-{id}-output-schema, ask-inspector-{id}-context-keys-input, ask-inspector-{id}-artifact-key-input, ask-inspector-{id}-summary-input, ask-inspector-{id}-save-to-library-btn, ask-inspector-{id}-delete-btn, branch-inspector-{id}, branch-inspector-{id}-summary-input, branch-inspector-{id}-summary-label, branch-inspector-{id}-context-keys-input, branch-inspector-{id}-context-keys-label, branch-inspector-{id}-routing-section, branch-inspector-{id}-condition-mode-hint, branch-inspector-{id}-merge-editor, error-inspector-{id}, error-inspector-{id}-message-label, error-inspector-{id}-message-editor, error-inspector-{id}-actions, branch-inspector-{id}-paths-list, branch-inspector-{id}-path-{index}, branch-inspector-{id}-path-{index}-name, branch-inspector-{id}-path-{index}-condition, branch-inspector-{id}-path-{index}-remove, branch-inspector-{id}-add-path-btn, branch-inspector-{id}-actions, branch-inspector-{id}-delete-btn, plugin-inspector-{id}-type-picker, plugin-inspector-{id}-config-form, plugin-inspector-{id}-delete-btn, phase-inspector-{id}-mode-select, phase-inspector-{id}-mode-config, phase-inspector-{id}-name-input, phase-inspector-{id}-save-template-btn, phase-inspector-{id}-detach-btn, phase-inspector-{id}-ungroup-btn, phase-inspector-{id}-delete-btn, edge-inspector-{id}, edge-inspector-{id}-transform-editor, edge-inspector-{id}-input-type, edge-inspector-{id}-output-type, edge-inspector-{id}-save-btn, edge-inspector-{id}-cancel-btn, validation-panel, validation-panel-issue-{index}, validation-panel-issue-{index}-goto, save-template-dialog, save-template-dialog-name, save-template-dialog-save-btn, import-confirm-dialog, import-confirm-dialog-confirm-btn, promotion-dialog, promotion-dialog-name, promotion-dialog-save-btn, selection-rectangle

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
- **BranchNode contract (D-GR-35):** dict-keyed `paths: Record<string, BranchOutputPort>` with per-port `condition` expression per path entry; `switch_function` is REJECTED; `merge_function` valid for multi-input gather
- **ErrorNode contract (D-GR-36):** 4th atomic type with `type: 'error'`, `message` (Jinja2 template), dict-keyed `inputs`, NO `outputs`, NO `hooks`

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

## D-GR Compliance Checklist

| Decision | Requirement | Plan Compliance |
|----------|-------------|-----------------|
| D-GR-35 (BranchNode) | Per-port conditions on `BranchOutputPort.condition` with non-exclusive fan-out. `switch_function` REJECTED completely. No dual routing model. No `SwitchFunctionEditor`. No routing-mode toggle. `merge_function` valid only for multi-input gather. | COMPLIANT: `SwitchFunctionEditor.tsx` and `SwitchFunctionLabel.tsx` removed from file tree. BranchInspector shows only `OutputPathsEditor` + `PortConditionRow`. `switch_function` appears only as a rejection target in `clientValidator.ts` (`rejected_stale_branch_field`). NodeDefinition uses `paths: Record<string, BranchOutputPort>` with per-port `condition`. No dual routing code paths exist. |
| D-GR-36 (ErrorNode) | ErrorNode IS a 4th atomic node type (Ask, Branch, Plugin, Error). Placed from palette. Entity: `id`, `type: error`, `message` (Jinja2), `inputs` (dict), NO outputs, NO hooks. Red-themed terminal node. | COMPLIANT: `ErrorNode.tsx` in `nodes/`, `ErrorInspector.tsx` in `inspectors/`. `nodeTypes.ts` registers `error: ErrorNode`. NodeDefinition type union includes `'error'`. Palette lists 4 atomic types. Validator enforces no outputs/hooks via `error_node_has_outputs`. |
| D-GR-30 | `actor_type: agent|human` only. No `interaction`. | COMPLIANT: ActorDefinition uses `actor_type` field; no `interaction` field in schema types. |
| D-GR-22 | Nested YAML with workflow-level cross-phase edges. No separate hooks section, no serialized `port_type`. | COMPLIANT: Serialization emits `phases[].nodes`, `phases[].children`, and `workflow.edges`. Hook edges are ordinary `source`/`target` refs. No `port_type` in serialized output. |

## Revision Log

| Rev | Change | Decision | Feedback |
|-----|--------|----------|----------|
| R1-1 | BranchInspector uses ONLY D-GR-35 per-port conditions — no SwitchFunctionEditor, no dual routing | D-GR-35 | — |
| R1-2 | SwitchFunctionEditor.tsx and SwitchFunctionLabel.tsx REMOVED — switch_function is rejected | D-GR-35 | — |
| R1-3 | OutputPathsEditor uses dict-keyed `paths: Record<string, BranchOutputPort>` with per-port conditions always shown | D-GR-35 | — |
| R1-4 | ConditionBadge replaces SwitchFunctionLabel on BranchNode card face | D-GR-35 | — |
| R1-5 | Client validator adds `rejected_stale_branch_field` rule for switch_function/output_field/condition_type/condition | D-GR-35 | — |
| R1-6 | Export `createEditorStore(options?)` factory from editorStore.ts | D-SF6-9 | [H-5] |
| R1-7 | Confirmed `iriai_compose.schema` as canonical import (not `.declarative.schema`) | D-SF6-6 updated | [C-2] |
| R1-8 | yamlSchema.ts header comments reference correct module path | D-SF6-6 updated | [C-2] |
| R1-9 | RISK-94 updated (stale field import), RISK-95 (schema path), RISK-96 (store factory) | — | [C-2, H-5] |
| R2-1 | ErrorNode added as 4th atomic type (Ask, Branch, Plugin, Error) per D-GR-36 | D-GR-36 | — |
| R2-2 | ErrorNode.tsx, ErrorInspector.tsx added to file tree and STEP-50/STEP-55 scopes | D-GR-36 | — |
| R2-3 | nodeTypes.ts registers `error: ErrorNode`; palette shows 4 atomic node types | D-GR-36 | — |
| R2-4 | Client validator adds `error_node_has_outputs` and ErrorNode `missing_required_field` rules | D-GR-36 | — |
| R2-5 | `plugin_instances` removed from implementation — only appears as rejection in validation | D-GR-35 | — |

---


---

---

## Subfeature: Libraries & Registries (libraries-registries)

### SF-7: Libraries & Registries

<!-- SF: libraries-registries -->



## D-GR Compliance Checklist

| Decision | Status | Notes |
|----------|--------|-------|
| D-GR-7 (Tool Library full CRUD) | ✅ Compliant | STEP-63 and STEP-68 cover backend + frontend list/detail/editor CRUD, including `/api/tools`, `/api/tools/{id}`, `/api/tools/{id}/references`, and role-checklist integration. |
| D-GR-11 (SF-7 owns node visual primitives) | ✅ Compliant | STEP-67 remains the owner for Ask/Branch/Plugin/Error primitives plus shared port/edge visuals; SF-6 only wraps them. |
| D-GR-26 (GET /api/{entity}/references/{id}) | ✅ Compliant | STEP-63 implements indexed role/schema/template preflight reads from `workflow_entity_refs`; STEP-64 consumes them before any DELETE call. |
| D-GR-27 (tools/compose topology) | ✅ Compliant | All backend and frontend paths are rooted under `tools/compose/backend` and `tools/compose/frontend/src`. |
| D-GR-28 (PostgreSQL + Alembic + psycopg) | ✅ Compliant | STEP-63 extends the compose PostgreSQL chain with two new tables and one JSONB column migration; no SQLite paths or drivers remain. |
| D-GR-29 (SF-7 owns reference-index extension) | ✅ Compliant | `workflow_entity_refs`, `tools`, and `custom_task_templates.actor_slots` are SF-7 follow-on migrations; SF-5 stays at five foundation tables. |
| D-GR-39 (Materialized refs + mutation hooks) | ✅ Compliant | Workflow delete preflight is table-backed, hook-driven, and repairable via scheduled/manual reconciliation; delete-time YAML parsing is removed. |
| D-GR-40 (CMP-134 / CMP-137 / CMP-138 coverage) | ✅ Compliant | STEP-64 and STEP-69 explicitly implement `LibraryCollectionPage` (CMP-134), `ActorSlotsEditor` (CMP-137), and `ResourceStateCard` (CMP-138) using the canonical design IDs. |
| REQ-114 (name sanitization + 256KB JSON limit) | ✅ Compliant | STEP-63 applies allowlist regex validation, path-scoped 256KB guards, and matching 413/422 handling across roles, schemas, templates, and tools. |
| REQ-115 (No Plugins Library / PluginPicker) | ✅ Compliant | No plugin entity types, routes, pages, pickers, or dialogs remain in SF-7 scope; STEP-70 is library-picker only. |

## Decision Log

| ID | Decision | Source |
|----|----------|--------|
| D-SF7-1 | Roles, Schemas, and Templates use SF-7's materialized `workflow_entity_refs` index for delete preflight. SF-7 subscribes to SF-5's `MutationHookRegistry` in `tools/compose/backend/app/hooks.py` at FastAPI lifespan startup and maps `created` / `updated` / `restored` workflow payloads to refresh, `soft_deleted` to purge. | D-GR-39, D-GR-29, [code: `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/plan.md:395`] |
| D-SF7-2 | SF-7 owns exactly three additive persistence changes after the SF-5 foundation: `workflow_entity_refs` table, `tools` table, and `custom_task_templates.actor_slots` JSONB column. A separate `actor_slots` table is rejected. | REQ-108, REQ-111, D-GR-29 |
| D-SF7-3 | Delete discovery is always non-destructive. Roles / schemas / templates preflight with `GET /api/{entity}/references/{id}`; tools preflight with `GET /api/tools/{id}/references}`. `EntityDeleteDialog` never uses DELETE to discover blockers. | D-GR-26, REQ-109 |
| D-SF7-4 | Tool Library uses a two-tier catalog: built-in tool constants are returned by the backend on every `GET /api/tools`; custom tools live in the SF-7-owned `tools` table. Tool delete protection scans persisted `Role.tools` arrays, not `workflow_entity_refs`. | D-GR-7, [code: `iriai-compose/iriai_compose/actors.py:13`] |
| D-SF7-5 | Canonical design component IDs are used directly in the plan: EntityDeleteDialog = CMP-133, LibraryCollectionPage = CMP-134, RoleEditorForm = CMP-135, ToolEditorForm = CMP-136, ActorSlotsEditor = CMP-137, ResourceStateCard = CMP-138. | RR-7, [code: `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:84`] |
| D-SF7-6 | `RoleEditorForm` is a single scrollable form, not a wizard. `ToolChecklistGrid` always fetches `/api/tools` and groups built-in plus custom tools from the response rather than hardcoding the entire list locally. | REQ-110, RR-7 |
| D-SF7-7 | `ActorSlotsEditor` edits the embedded `actor_slots` array on the template draft. Template create / update responses are the only persistence API; per-slot CRUD endpoints under `/api/templates/{id}/actor-slots/*` are rejected. | REQ-111, RR-7 |
| D-SF7-8 | SF-7 launch scope is limited to Roles, Schemas, Templates, Tools, three library pickers, shared delete / state surfaces, and node primitives. Promotion dialogs, plugin libraries, plugin endpoints, and PluginPicker remain out of scope. | REQ-115, [code: `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:4`] |
| D-SF7-9 | All new SF-7 endpoints reuse SF-5's auth and slowapi infrastructure: JWT Bearer auth on every endpoint, 404 for cross-user access, per-user route limits, and `require_admin` on `POST /api/admin/reconcile-entity-refs`. | REQ-113, REQ-114, [code: `first-party-apps/events/events-backend/app/dependencies/auth.py:31`], [code: `platform/deploy-console/deploy-console-service/app/middleware/rate_limit.py:34`] |
| D-SF7-10 | Library list data uses React Query stale-while-revalidate semantics: cached rows remain visible during background refetch, and create / update / delete mutations invalidate the relevant list plus dependent pickers or checklists. | REQ-112 |
| D-SF7-11 | SF-7 continues to own Ask / Branch / Plugin / Error primitives plus `NodePortDot` and `EdgeTypeLabel`. This ownership is visual-only; no schema or runtime contracts move into SF-7. | D-GR-11 |

## File Structure

### Backend (SF-7-owned extensions to compose-backend)

```text
tools/compose/backend/
├── alembic/versions/
│   ├── 002_sf7_workflow_entity_refs.py      # create — workflow_entity_refs table
│   ├── 003_sf7_tools.py                     # create — tools table
│   └── 004_sf7_template_actor_slots.py      # create — ALTER custom_task_templates ADD COLUMN actor_slots JSONB
├── app/
│   ├── models/
│   │   ├── workflow_entity_ref.py           # create — WorkflowEntityRefORM
│   │   ├── tool.py                          # create — ToolORM
│   │   └── custom_task_template.py          # modify — add actor_slots JSONB field
│   ├── routers/
│   │   ├── roles.py                         # modify — check-name + reference-safe delete
│   │   ├── schemas.py                       # modify — check-name + reference-safe delete
│   │   ├── templates.py                     # modify — actor_slots embedded in create/update payloads + reference-safe delete
│   │   ├── tools.py                         # create — full CRUD + tool reference preflight
│   │   ├── references.py                    # create — GET /api/{entity}/references/{id}
│   │   └── admin.py                         # create — POST /api/admin/reconcile-entity-refs
│   ├── services/
│   │   ├── entity_ref_service.py            # create — refresh / purge / reconcile workflow_entity_refs
│   │   ├── yaml_ref_parser.py               # create — workflow YAML -> persisted entity refs
│   │   ├── tool_reference_service.py        # create — role-backed tool reference checks
│   │   └── ref_index_subscription.py        # create — hook registry subscription + scheduler wiring helpers
│   ├── schemas/
│   │   ├── reference.py                     # create — role/schema/template + tool reference responses
│   │   ├── tool.py                          # create — Tool create/update/list schemas
│   │   ├── custom_task_template.py          # modify — actor_slots embedded models + validation
│   │   ├── role.py                          # modify — name validator reuse
│   │   ├── output_schema.py                 # modify — name validator reuse
│   │   └── common.py                        # modify — shared allowlist validators / 413 helpers
│   ├── dependencies/
│   │   ├── auth.py                          # modify — reuse get_current_user / require_admin on SF-7 routes
│   │   └── rate_limit.py                    # read — reuse SF-5 limiter instance
│   ├── hooks.py                             # read — MutationHookRegistry / MutationHookPayload contract from SF-5
│   ├── database.py                          # read — AsyncSession factory / psycopg normalization
│   └── main.py                              # modify — register SF-7 hook subscriber + reconciliation scheduler + routers
```

### Frontend

```text
tools/compose/frontend/src/features/libraries/
├── index.ts
├── types.ts
├── hooks/
│   ├── useLibraryList.ts
│   ├── useLibraryEntity.ts
│   ├── useReferenceCheck.ts
│   └── useDuplicateNameCheck.ts
├── shared/
│   ├── LibraryCollectionPage.tsx           # create — CMP-134
│   ├── LibraryCard.tsx
│   ├── LibraryToolbar.tsx
│   ├── LibraryEmptyState.tsx
│   ├── TipCallout.tsx
│   ├── EntityDeleteDialog.tsx              # create — CMP-133
│   └── ResourceStateCard.tsx               # create — CMP-138
├── roles/
│   ├── RolesListPage.tsx
│   └── RoleEditorForm.tsx                  # create — CMP-135
├── schemas/
│   ├── SchemasListPage.tsx
│   ├── SchemaEditorView.tsx
│   ├── DualPaneLayout.tsx
│   ├── SchemaPreviewTree.tsx
│   └── PropertyNode.tsx
├── templates/
│   ├── TemplatesListPage.tsx
│   ├── TaskTemplateEditorView.tsx
│   ├── TemplateWizardDialog.tsx
│   ├── SidePanel.tsx
│   ├── ActorSlotsEditor.tsx                # create — CMP-137
│   ├── IOInterfaceEditor.tsx
│   ├── IOPort.tsx
│   ├── ScaleBadge.tsx
│   └── MiniToolbar.tsx
├── tools/
│   ├── ToolsListPage.tsx
│   ├── ToolEditorForm.tsx                  # create — CMP-136
│   └── ToolChecklistGrid.tsx
├── pickers/
│   ├── RolePicker.tsx
│   ├── SchemaPicker.tsx
│   └── TemplateBrowser.tsx
├── primitives/
│   ├── index.ts
│   ├── AskNodePrimitive.tsx
│   ├── BranchNodePrimitive.tsx
│   ├── PluginNodePrimitive.tsx
│   ├── ErrorNodePrimitive.tsx
│   ├── NodePortDot.tsx
│   └── EdgeTypeLabel.tsx
└── styles/
    └── library-shell.css                   # create — SF-7-specific layout refinements if theme overrides are needed
```

## Implementation Steps

### STEP-63: Backend — Migrations, Indexed Reference Preflight, Tool CRUD, Hook Subscription, Reconciliation

**Objective:** Add the SF-7 backend extension layer on top of the SF-5 compose foundation. This step creates two new SF-7 tables, adds the `actor_slots` JSONB column to `custom_task_templates`, maintains `workflow_entity_refs` from SF-5 workflow mutation hooks, exposes dedicated preflight read endpoints before delete, implements full custom-tool CRUD, and wires scheduled plus manual repair paths for stale reference rows.

**Requirement IDs:** REQ-107, REQ-108, REQ-109, REQ-110, REQ-111, REQ-112, REQ-113, REQ-114, REQ-115
**Journey IDs:** J-39, J-40, J-41, J-42, J-43

**Scope:**
| Path | Action |
|------|--------|
| `tools/compose/backend/alembic/versions/002_sf7_workflow_entity_refs.py` | create |
| `tools/compose/backend/alembic/versions/003_sf7_tools.py` | create |
| `tools/compose/backend/alembic/versions/004_sf7_template_actor_slots.py` | create |
| `tools/compose/backend/app/models/workflow_entity_ref.py` | create |
| `tools/compose/backend/app/models/tool.py` | create |
| `tools/compose/backend/app/models/custom_task_template.py` | modify |
| `tools/compose/backend/app/services/entity_ref_service.py` | create |
| `tools/compose/backend/app/services/yaml_ref_parser.py` | create |
| `tools/compose/backend/app/services/tool_reference_service.py` | create |
| `tools/compose/backend/app/services/ref_index_subscription.py` | create |
| `tools/compose/backend/app/routers/references.py` | create |
| `tools/compose/backend/app/routers/tools.py` | create |
| `tools/compose/backend/app/routers/admin.py` | create |
| `tools/compose/backend/app/routers/roles.py` | modify |
| `tools/compose/backend/app/routers/schemas.py` | modify |
| `tools/compose/backend/app/routers/templates.py` | modify |
| `tools/compose/backend/app/schemas/reference.py` | create |
| `tools/compose/backend/app/schemas/tool.py` | create |
| `tools/compose/backend/app/schemas/custom_task_template.py` | modify |
| `tools/compose/backend/app/schemas/role.py` | modify |
| `tools/compose/backend/app/schemas/output_schema.py` | modify |
| `tools/compose/backend/app/schemas/common.py` | modify |
| `tools/compose/backend/app/main.py` | modify |
| `tools/compose/backend/app/hooks.py` | read |
| `tools/compose/backend/app/dependencies/auth.py` | read |
| `tools/compose/backend/app/dependencies/rate_limit.py` | read |
| `tools/compose/backend/app/database.py` | read |
| `tools/compose/backend/app/models/workflow.py` | read |
| `tools/compose/backend/app/models/role.py` | read |
| `tools/compose/backend/app/models/output_schema.py` | read |

**Instructions:**

1. **Add the three SF-7 Alembic revisions.**
- `002_sf7_workflow_entity_refs.py` creates `workflow_entity_refs` with `id`, `workflow_id`, `entity_type`, `entity_id`, `user_id`, `created_at`, a uniqueness constraint on `(workflow_id, entity_type, entity_id)`, and indexed lookup paths for `(entity_type, entity_id, user_id)` and `workflow_id`.
- `003_sf7_tools.py` creates `tools` with soft-delete support, `source in ('mcp', 'custom_function')`, and a partial unique index on `(user_id, name)` where `deleted_at IS NULL`.
- `004_sf7_template_actor_slots.py` alters `custom_task_templates` to add `actor_slots JSONB NOT NULL DEFAULT '[]'::jsonb`. This is the only accepted actor-slot persistence model in SF-7; do not create an `actor_slots` table.

2. **Model `actor_slots` as embedded JSON, not row-level child records.**
- In `app/models/custom_task_template.py`, add an `actor_slots` JSONB column mapped to `list[dict[str, Any]]` with an empty-list default.
- In `app/schemas/custom_task_template.py`, define `ActorSlotDefinition` with `slot_key`, `description`, `allowed_actor_types`, and `default_role_id`. Validate unique `slot_key` values per template, reject blank keys, and reject `default_role_id` values the current user does not own.
- Template create and update handlers in `app/routers/templates.py` must read and write the entire `actor_slots` array on the template payload; remove any per-slot sub-route design.

3. **Implement the YAML ref parser once and reuse it everywhere reference rows are rebuilt.**
- `yaml_ref_parser.py` must use `yaml.safe_load()`, cap accepted workflow YAML size to the existing compose workflow limits, walk nested `phases[].nodes` plus `phases[].children`, and extract role / output schema / template UUID references from the persisted YAML shape only.
- Exclude synthetic root containers and unsaved editor state by definition: the parser only sees persisted workflow `yaml_content` rows.
- Reuse the same parser in both hook-driven refresh and manual reconciliation so drift behavior stays deterministic.

4. **Subscribe to SF-5 mutation hooks at FastAPI lifespan startup.**
- Read the current contract from `tools/compose/backend/app/hooks.py` and register a single SF-7 subscriber via a helper in `app/services/ref_index_subscription.py`.
- Refresh on `MutationHookPayload(entity_type=WORKFLOW, event in {CREATED, UPDATED, RESTORED})`.
- Purge on `SOFT_DELETED`.
- Do not invent `imported`, `version_saved`, or `deleted` hook kinds; SF-7 must consume the exact four-event contract published by SF-5.

5. **Maintain `workflow_entity_refs` in separate SF-7 transactions.**
- `entity_ref_service.py` must expose `refresh_workflow_entity_refs(payload)`, `purge_workflow_entity_refs(payload)`, `get_entity_references(entity_type, entity_id, user_id)`, and `reconcile_all_workflow_entity_refs()`.
- `refresh_workflow_entity_refs` opens a new AsyncSession, re-reads the workflow row, computes the expected ref set, deletes that workflow's previous rows, and bulk-inserts the replacement set atomically.
- `purge_workflow_entity_refs` deletes all rows for the workflow id.
- Both operations must be idempotent.

6. **Expose dedicated GET preflight endpoints before any destructive action.**
- `app/routers/references.py` implements `GET /api/{entity}/references/{id}` for `roles`, `schemas`, and `templates` only. Each request is JWT-authenticated with `Depends(get_current_user)` and limited to `60/minute` per user.
- Add `GET /api/tools/{id}/references` for tool delete preflight. It returns role names by scanning current-user `Role.tools` arrays through `tool_reference_service.py`. It must not read `workflow_entity_refs`.
- Response shape for both endpoints: `total`, `blocked_by`, and a list of typed ref records with ids and display names.

7. **Implement the custom-tool CRUD router.**
- `GET /api/tools` returns the built-in tool catalog plus the caller's non-deleted custom tools in one list. Built-ins are response objects only; they never receive DB ids.
- `GET /api/tools/{id}` returns one current-user custom tool or 404.
- `POST /api/tools` creates one custom tool, validates the allowlist name regex, 500-char description max, 256KB `input_schema` max, and returns 201. Apply `20/minute`.
- `PUT /api/tools/{id}` updates name, description, source-locked metadata, and `input_schema`; if the name changes, include a warning about existing `Role.tools` string references. Apply `20/minute`.
- `DELETE /api/tools/{id}` rechecks tool references through `tool_reference_service.py`, returns 409 with role names if blocked, otherwise soft-deletes the row. Apply `20/minute`.
- `GET /api/tools/check-name` validates availability for the current user. Apply `60/minute`.

8. **Extend SF-5 entity routers rather than adding duplicate SF-7 CRUD surfaces.**
- `roles.py`, `schemas.py`, and `templates.py` each gain `GET /check-name` plus reference-safe DELETE behavior.
- DELETE handlers must query `workflow_entity_refs` first, return 409 with referencing workflow names when blocked, and never parse YAML inline.
- `templates.py` create / update / detail responses must include `actor_slots` in the canonical payload so reloads round-trip the persisted JSONB column.

9. **Wire auth, rate limiting, and request-size enforcement through existing compose infrastructure.**
- Reuse `get_current_user` from `app/dependencies/auth.py` on every new read/write route and `require_admin` on `POST /api/admin/reconcile-entity-refs`.
- Reuse the compose `limiter` from `app/dependencies/rate_limit.py` rather than creating a second limiter instance.
- Add a path-scoped 256KB JSON body guard for `/api/roles`, `/api/schemas`, `/api/templates`, and `/api/tools`. Return 413 with a structured body before persistence. Re-check at Pydantic level in case `Content-Length` is absent.
- Apply allowlist validators in shared schemas: roles / schemas / templates use `^[A-Za-z][A-Za-z0-9_. -]{0,199}$`; tools use `^[A-Za-z_][A-Za-z0-9_.-]{0,199}$`.
- Cross-user access must resolve as 404, not 403, on all library resources.

10. **Add scheduled plus manual stale-index repair.**
- In `app/main.py`, create an `AsyncIOScheduler` during lifespan startup, register `reconcile_all_workflow_entity_refs()` on the configurable interval, and shut the scheduler down on lifespan exit.
- `POST /api/admin/reconcile-entity-refs` calls the same reconciliation function, requires `require_admin`, and is limited to `5/hour`.
- Return `{ workflows_scanned, rows_added, rows_removed, duration_ms }` from both scheduler logs and the admin route.

**Acceptance Criteria:**
- Run `alembic upgrade head` and inspect the compose database: `workflow_entity_refs` and `tools` tables exist, and `custom_task_templates` has a non-null `actor_slots` JSONB column with default `[]`.
- Run `alembic downgrade -1` three times: both tables are removed and the `actor_slots` column is dropped cleanly.
- Save a workflow that references a role, schema, or template, then query `workflow_entity_refs`: the expected materialized rows exist for that workflow id.
- Call `GET /api/roles/references/{id}` or `GET /api/templates/references/{id}` with a referenced entity: the response lists blocking workflow names before any DELETE request is sent.
- Call `GET /api/tools/{id}/references` with a referenced custom tool: the response lists blocking role names before any DELETE request is sent.
- Delete an unreferenced role, schema, template, or tool: the endpoint returns 204 and soft-deletes the row.
- Update a template with an `actor_slots` array, reload the template, and observe the same `actor_slots` values in the detail response.
- Submit a library JSON payload larger than 256KB: the request is rejected with 413 and no DB mutation occurs.
- Call `POST /api/admin/reconcile-entity-refs` with an admin token: the route returns a reconciliation summary. Call the same route without admin authorization: the request is rejected by `require_admin`.
- Request another user's tool or template by UUID: the response is 404 and does not reveal ownership metadata.

**Counterexamples:**
- Do NOT parse workflow YAML during delete preflight; only the hook refresh and reconciliation job may parse persisted workflow YAML.
- Do NOT create an `actor_slots` table or `/api/templates/{id}/actor-slots/*` endpoints.
- Do NOT create any plugin tables, plugin routes, or `/api/plugins` endpoints.
- Do NOT create a second auth or rate-limiter stack inside SF-7; reuse the compose foundation dependencies.
- Do NOT allow `POST /api/admin/reconcile-entity-refs` for ordinary authenticated users.
- Do NOT treat built-in tools as database rows.

**Citations:**
- [decision: D-GR-39] — Materialized `workflow_entity_refs` with mutation-hook refresh; delete-time YAML parsing rejected.
- [decision: D-GR-29] — SF-7 owns the reference-index extension on top of SF-5's five-table foundation.
- [decision: D-GR-7] — Tool Library CRUD and role-checklist integration remain in SF-7 scope.
- [code: `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/plan.md:395`] — Canonical `MutationHookRegistry` / `MutationHookPayload` contract.
- [code: `first-party-apps/events/events-backend/app/dependencies/auth.py:31`] — Existing `get_current_user` / `require_admin` auth dependency pattern.
- [code: `platform/deploy-console/deploy-console-service/app/middleware/rate_limit.py:34`] — Existing slowapi limiter pattern for per-user route limits.
- [code: `platform/deploy-console/deploy-console-service/app/main.py:388`] — Existing `AsyncIOScheduler` lifespan pattern.
- [code: `iriai-compose/iriai_compose/actors.py:13`] — `Role.tools` is a persisted string list, so tool delete protection is role-backed.

### STEP-64: Shared Library Infrastructure — Query Hooks, CMP-133 / CMP-134 / CMP-138, Shared Shell States

**Objective:** Build the shared frontend layer that every SF-7 library page uses. This step creates the canonical reusable list shell `LibraryCollectionPage` (CMP-134), the shared `EntityDeleteDialog` (CMP-133), the route-level `ResourceStateCard` (CMP-138), and the query hooks that keep library screens fast via stale-while-revalidate behavior while enforcing non-destructive preflight delete flows.

**Requirement IDs:** REQ-108, REQ-109, REQ-110, REQ-112, REQ-113, REQ-114, REQ-115
**Journey IDs:** J-39, J-40, J-41, J-42, J-43

**Scope:**
| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/libraries/types.ts` | create |
| `tools/compose/frontend/src/features/libraries/index.ts` | create |
| `tools/compose/frontend/src/features/libraries/hooks/useLibraryList.ts` | create |
| `tools/compose/frontend/src/features/libraries/hooks/useLibraryEntity.ts` | create |
| `tools/compose/frontend/src/features/libraries/hooks/useReferenceCheck.ts` | create |
| `tools/compose/frontend/src/features/libraries/hooks/useDuplicateNameCheck.ts` | create |
| `tools/compose/frontend/src/features/libraries/shared/LibraryCollectionPage.tsx` | create |
| `tools/compose/frontend/src/features/libraries/shared/LibraryCard.tsx` | create |
| `tools/compose/frontend/src/features/libraries/shared/LibraryToolbar.tsx` | create |
| `tools/compose/frontend/src/features/libraries/shared/LibraryEmptyState.tsx` | create |
| `tools/compose/frontend/src/features/libraries/shared/TipCallout.tsx` | create |
| `tools/compose/frontend/src/features/libraries/shared/EntityDeleteDialog.tsx` | create |
| `tools/compose/frontend/src/features/libraries/shared/ResourceStateCard.tsx` | create |
| `tools/compose/frontend/src/api/client.ts` | read |
| `tools/compose/frontend/src/components/ConfirmDialog.tsx` | read |
| `tools/compose/frontend/src/styles/compose-theme.css` | read |

**Instructions:**

1. **Create canonical shared library types with no plugin union members.**
- `RoleEntity` uses `prompt`, not `system_prompt`, and includes `tools`, `model`, `effort`, and `metadata`.
- `TaskTemplateEntity` includes `actor_slots: ActorSlotDefinition[]` from the embedded template payload.
- `ToolEntity` supports built-in response rows with no DB id plus custom rows with `id`, `source in ('built_in', 'mcp', 'custom_function')`, and optional `input_schema`.
- `EntityType` is exactly `'role' | 'schema' | 'template' | 'tool'`.

2. **Use React Query for stale-while-revalidate list and detail data.**
- `useLibraryList` keeps previous data visible during refetch via `placeholderData`, sets `staleTime` to 30 seconds, and invalidates dependent caches after create / update / delete.
- `useLibraryEntity` loads one detail route at a time and classifies 404, 413/422, and generic request errors for `ResourceStateCard`.
- `useDuplicateNameCheck` debounces availability checks by 300ms and reuses the shared authenticated API client.

3. **Implement `useReferenceCheck` around dedicated GET preflight routes only.**
- For roles / schemas / templates, call `GET /api/{plural}/references/{id}` when a delete dialog opens.
- For tools, call `GET /api/tools/{id}/references` when the delete dialog opens.
- Surface `checkState` values `loading`, `blocked-workflows`, `blocked-roles`, `ready`, and `error` so `EntityDeleteDialog` never needs to infer state from HTTP status codes alone.
- DELETE requests remain a second step after the dialog reaches `ready`.

4. **Implement `LibraryCollectionPage` (CMP-134) as the shared list shell.**
- The component owns toolbar placement, search, create CTA, grid vs list switching, cached loading treatment, and inline empty/error states.
- It must preserve route chrome during refetch and avoid full-page blanking.
- All four library routes reuse the same shell; only entity labels, icons, and card metadata differ.

5. **Implement `EntityDeleteDialog` (CMP-133) with the full five-state contract.**
- `loading`: reserved-height modal with spinner and no destructive CTA.
- `blocked-workflows`: warning copy with saved workflow names and Close only.
- `blocked-roles`: warning copy with saved role names and Close only.
- `ready`: neutral confirm-delete state with Cancel + Delete.
- `error`: retryable reference-check failure state with Retry + Close.
- Always use `role='alertdialog'`, trap focus, and return focus to the invoking button.

6. **Implement `ResourceStateCard` (CMP-138) for route-level loading / not-found / error / validation states.**
- `loading` is used while a detail route fetch is in flight.
- `not-found` is the only cross-user failure presentation; do not show forbidden messaging.
- `validation` is used for 413 / 422 request rejections coming back from save attempts.

**Acceptance Criteria:**
- Opening `/roles`, `/schemas`, `/templates`, or `/tools` with warm cache keeps the prior list visible while refetch happens in the background.
- Opening delete on a referenced role issues `GET /api/roles/references/{id}` before any DELETE call and renders the blocked-workflows state.
- Opening delete on a referenced tool issues `GET /api/tools/{id}/references` before any DELETE call and renders the blocked-roles state.
- A cross-user detail route renders `ResourceStateCard` in the `not-found` state instead of showing 403 copy.
- A 413 or 422 library save response renders `ResourceStateCard` or inline validation using the `validation` state rather than a generic toast only.
- No plugin entity types or plugin picker data appear anywhere in shared library types, hooks, or route shells.

**Counterexamples:**
- Do NOT call DELETE to discover blockers.
- Do NOT include `plugin_type`, `plugin_instance`, or any plugin-specific variant in `EntityType` or shared type maps.
- Do NOT blank the entire page during background refetch.
- Do NOT render 403 / access-denied copy for cross-user resource lookups.

**data-testid assignments:**
- `library-collection-page`, `library-collection-page-loading`, `library-collection-page-empty`, `library-collection-page-error`
- `library-grid`, `library-grid-card-{id}`
- `library-toolbar`, `library-toolbar-new-btn`, `library-toolbar-search`, `library-toolbar-view-toggle`
- `library-empty-state`, `library-empty-state-new-btn`
- `tip-callout`
- `entity-delete-dialog`, `entity-delete-dialog-checking`, `entity-delete-dialog-blocked-workflows`, `entity-delete-dialog-blocked-roles`, `entity-delete-dialog-ready`, `entity-delete-dialog-error`, `entity-delete-dialog-ref-list`, `entity-delete-dialog-retry-btn`, `entity-delete-dialog-close-btn`, `entity-delete-dialog-delete-btn`
- `resource-state-card`, `resource-state-card-loading`, `resource-state-card-not-found`, `resource-state-card-error`, `resource-state-card-validation`, `resource-state-card-action-btn`

**Citations:**
- [decision: D-GR-26] — Canonical GET reference preflight for persisted workflow refs.
- [decision: D-GR-39] — Materialized refs, not delete-time YAML parsing.
- [decision: REQ-115] — No plugins or PluginPicker in SF-7 scope.
- [code: `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:84`] — CMP-133 EntityDeleteDialog states.
- [code: `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:96`] — CMP-134 LibraryCollectionPage contract.
- [code: `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:142`] — CMP-138 ResourceStateCard contract.

### STEP-65: Roles Library — CMP-135 Form Editor with `/api/tools`-Backed Checklist

**Objective:** Build the Roles library route as a list + detail workflow powered by the shared library shell. The main content surface is the canonical form-based `RoleEditorForm` (CMP-135), and its tool section always reads the merged built-in/custom catalog from `GET /api/tools` so role configuration stays aligned with the Tool Library.

**Requirement IDs:** REQ-110, REQ-112, REQ-113, REQ-114
**Journey IDs:** J-39, J-41

**Scope:**
| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/libraries/roles/RolesListPage.tsx` | create |
| `tools/compose/frontend/src/features/libraries/roles/RoleEditorForm.tsx` | create |
| `tools/compose/frontend/src/features/libraries/tools/ToolChecklistGrid.tsx` | create |
| `tools/compose/frontend/src/features/libraries/shared/LibraryCollectionPage.tsx` | read |
| `tools/compose/frontend/src/features/libraries/hooks/useLibraryList.ts` | read |
| `tools/compose/frontend/src/features/libraries/hooks/useLibraryEntity.ts` | read |
| `tools/compose/frontend/src/features/libraries/hooks/useDuplicateNameCheck.ts` | read |
| `tools/compose/frontend/src/features/libraries/shared/TipCallout.tsx` | read |

**Instructions:**

1. **Render `/roles` through the shared list shell.**
- `RolesListPage.tsx` uses `useLibraryList('role')`, maps cards to role name, model, tool count, and dirty selection state, and routes the detail pane to `/roles/:id`.
- Empty, error, and loading treatment must come from `LibraryCollectionPage` rather than custom one-off role-page states.

2. **Implement `RoleEditorForm` (CMP-135) as a single scrollable form.**
- Required fields: `name`, `prompt`, and any role-specific selector fields the compose role schema already supports (`model`, `effort`, `metadata`, `tools`).
- The form is never stepped or wizard-driven; all editable sections are visible in one surface with a sticky save bar.
- Use the shared allowlist regex for role names and show inline errors plus a summary banner when invalid.
- Save state values are exactly `draft`, `invalid`, `saving`, and `saved`.

3. **Fetch tool options from the Tool Library, not local constants.**
- `ToolChecklistGrid` calls `GET /api/tools`, groups the response into Built-in Tools and Custom Tools, and renders both groups every time the form mounts.
- Built-in tools are selectable catalog items, not read-only ghost entries; custom tools come from current-user rows and disappear after invalidation if deleted.
- The grid must not hardcode the custom-tool section or merge all tools into one unlabeled list.

4. **Keep detail-route fallbacks consistent with shared error handling.**
- If `/roles/:id` resolves to 404, render `ResourceStateCard` through the detail-pane wrapper.
- If a role save returns 413 or 422, stay on the form, focus the first invalid field, and keep the unsaved draft visible.

**Acceptance Criteria:**
- Opening `/roles` shows `LibraryCollectionPage` with search, create CTA, and role cards.
- Clicking `+ New Role` opens `RoleEditorForm` with all fields visible at once.
- Blurring an invalid or duplicate name surfaces inline validation and keeps Save disabled.
- Saving a valid role sends `prompt` in the payload, not `system_prompt`, and the role appears in both the list and `RolePicker` after cache invalidation.
- `ToolChecklistGrid` shows Built-in Tools and Custom Tools from the `/api/tools` response.
- Deleting a custom tool from the Tools Library removes it from the role checklist after invalidation.

**Counterexamples:**
- Do NOT build a stepper or wizard.
- Do NOT send or store `system_prompt`; use the compose role contract field `prompt`.
- Do NOT hardcode the full tool list in `ToolChecklistGrid`.
- Do NOT merge built-in and custom tools into one unlabeled section.

**data-testid assignments:**
- `roles-list-page`
- `role-editor-form`, `role-editor-form-draft`, `role-editor-form-invalid`, `role-editor-form-saving`, `role-editor-form-saved`
- `role-name-input`, `role-name-error`
- `role-model-picker`, `role-effort-picker`
- `role-prompt-editor`
- `role-tools-grid`, `role-tools-builtin-section`, `role-tools-custom-section`, `role-tool-chip-{name}`
- `role-metadata-editor`
- `role-save-btn`, `role-cancel-btn`, `role-delete-btn`, `role-validation-banner`

**Citations:**
- [code: `iriai-compose/iriai_compose/actors.py:8`] — Canonical role shape uses `prompt`, `tools`, `model`, `effort`, and `metadata`.
- [decision: D-GR-7] — Tool Library is the source of truth for role-checklist options.
- [code: `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:108`] — CMP-135 RoleEditorForm is form-based with `draft` / `invalid` / `saving` / `saved` states.

### STEP-66: Output Schemas Library — Dual-Pane JSON Schema Editor

**Objective:** Build the Output Schemas library page (`/schemas`) with the dual-pane SchemaEditorView (CodeMirror JSON Schema left, SchemaPreviewTree right), live validation with 500ms debounce, and inline-first messaging that reinforces D-26 (output schemas are primarily created inline in the Ask inspector).

**Requirement IDs:** REQ-112, REQ-113, REQ-114
**Journey IDs:** J-39

**Scope:**
| Path | Action |
|------|--------|
| `features/libraries/schemas/SchemasListPage.tsx` | create |
| `features/libraries/schemas/SchemaEditorView.tsx` | create |
| `features/libraries/schemas/DualPaneLayout.tsx` | create |
| `features/libraries/schemas/SchemaPreviewTree.tsx` | create |
| `features/libraries/schemas/PropertyNode.tsx` | create |
| `features/editor/inspector/CodeEditor.tsx` | read — shared CodeMirror wrapper from SF-6 [C-2 confirmed path] |

**Instructions:**

**1. `SchemaEditorView.tsx` — Dual-pane editor**

Top bar: name input + description input + Validate button + Save button. Below: DualPaneLayout with CodeMirror (left) and SchemaPreviewTree (right).

Import the CodeMirror wrapper from SF-6's confirmed path: `features/editor/inspector/CodeEditor.tsx` [C-2]. Configure with JSON mode, dark theme (#1e1e2e), line numbers.

Live validation: 500ms debounce after typing stops. Client-side: try `JSON.parse()` — if fails, show red banner "Invalid JSON" with parse error position. If valid JSON, try to interpret as JSON Schema — render preview tree. Server validates Draft 2020-12 on save.

**2. `DualPaneLayout.tsx` — Resizable split pane**

Flex row with draggable divider. Default split: 50/50. Min pane width: 200px. Preserves split ratio in localStorage.

**3. `SchemaPreviewTree.tsx` — Property tree**

Renders tree of PropertyNode components from parsed JSON Schema. Invalid schema → red banner. Tree renders partial valid portion. Must NOT crash on bad JSON.

**Acceptance Criteria:**
- Navigate to `/schemas` → list page with inline-first messaging [D-26]
- Click "+ New Schema" → dual-pane editor opens
- Type valid JSON Schema → preview tree renders within 500ms, green "Valid" badge
- Type invalid JSON → red banner with line number, preview at last valid state
- Save with invalid schema → server returns 422
- Save with valid schema → 201 → appears in list + SchemaPicker

**Counterexamples:**
- Do NOT build a visual field-builder — raw JSON Schema editor
- Do NOT crash on invalid JSON — graceful degradation
- Do NOT validate on every keystroke — 500ms debounce
- Do NOT import from `features/editor/inspectors/` (plural) — use `features/editor/inspector/` [C-2]

**data-testid assignments:**
- `schemas-list-page` — page container
- `schema-editor` — editor content panel
- `schema-name-input` — name input
- `schema-description-input` — description input
- `schema-validate-btn` — validate button
- `schema-save-btn` — save button
- `schema-code-editor` — CodeMirror pane
- `schema-preview-tree` — preview tree pane
- `schema-preview-property-{name}` — property node
- `schema-preview-valid-badge` — green valid indicator
- `schema-preview-error-banner` — red error banner
- `schema-dual-pane-divider` — resize handle

**Citations:**
- [decision: D-26] — Inline-first messaging for schemas
- [code: features/editor/inspector/CodeEditor.tsx] — SF-6 confirmed CodeMirror wrapper [C-2]

### STEP-67: Node Visual Primitives [D-GR-11]

**Objective:** Build the 6 node visual primitives that SF-7 owns per D-GR-11: AskNodePrimitive, BranchNodePrimitive, PluginNodePrimitive, ErrorNodePrimitive, NodePortDot, and EdgeTypeLabel. These are pure presentational React components with no React Flow dependency. SF-6 wraps them in ~30-line React Flow adapter components. The unidirectional dependency ensures SF-6 depends on SF-7 primitives, never the reverse.

**Requirement IDs:** REQ-107
**Journey IDs:** J-39 (indirectly — primitives enable the editor which enables role assignment)

**Scope:**
| Path | Action |
|------|--------|
| `features/libraries/primitives/index.ts` | create |
| `features/libraries/primitives/AskNodePrimitive.tsx` | create |
| `features/libraries/primitives/BranchNodePrimitive.tsx` | create |
| `features/libraries/primitives/PluginNodePrimitive.tsx` | create |
| `features/libraries/primitives/ErrorNodePrimitive.tsx` | create |
| `features/libraries/primitives/NodePortDot.tsx` | create |
| `features/libraries/primitives/EdgeTypeLabel.tsx` | create |
| `src/styles/windows-xp.css` | read — XP design tokens |

**Instructions:**

**1. `AskNodePrimitive.tsx` — Ask node visual card**

```typescript
interface AskNodePrimitiveProps {
  label: string;
  actorName?: string;
  outputType?: string;
  selected?: boolean;
  isValid?: boolean;
  ports: { inputs: PortDef[]; outputs: PortDef[] };
}
```

Purple-themed card (#8b5cf6 accent). Shows label, actor badge, output type badge. No React Flow handles — those are added by SF-6's AskNode wrapper. Uses XP card styling.

**2. `BranchNodePrimitive.tsx` — Branch node visual card**

Amber-themed card (#f59e0b accent). Shows label, number of output paths, merge indicator if merge_function is set. Per-port condition badges.

**3. `PluginNodePrimitive.tsx` — Plugin node visual card**

Green-themed card (#10b981 accent). Shows label, plugin_ref badge, I/O port count.

**4. `ErrorNodePrimitive.tsx` — Error node visual card [D-GR-13]**

Red-themed card (#ef4444 accent). Shows label, error message preview. Terminal indicator (no output ports).

**5. `NodePortDot.tsx` — Port dot primitive**

```typescript
interface NodePortDotProps {
  direction: 'input' | 'output';
  portType: 'data' | 'hook' | 'error';
  connected?: boolean;
  size?: number;  // default 12px
}
```

Circular dot with direction-based positioning. Colors: data=#6366f1, hook=#f59e0b, error=#ef4444. Connected = filled, unconnected = hollow.

**6. `EdgeTypeLabel.tsx` — Edge type label primitive**

```typescript
interface EdgeTypeLabelProps {
  edgeType: 'data' | 'hook';
  label?: string;
}
```

Small pill badge rendered at edge midpoint. Data = purple pill, Hook = amber pill.

**7. `index.ts` — Re-exports**

```typescript
export { AskNodePrimitive } from './AskNodePrimitive';
export { BranchNodePrimitive } from './BranchNodePrimitive';
export { PluginNodePrimitive } from './PluginNodePrimitive';
export { ErrorNodePrimitive } from './ErrorNodePrimitive';
export { NodePortDot } from './NodePortDot';
export { EdgeTypeLabel } from './EdgeTypeLabel';
```

All primitives are pure presentation — no network calls, no store access, no React Flow imports.

**Acceptance Criteria:**
- Import `AskNodePrimitive` from `features/libraries/primitives` → renders purple card with label and actor badge
- Import `BranchNodePrimitive` → renders amber card with output path count
- Import `ErrorNodePrimitive` → renders red card with terminal indicator [D-GR-13]
- Import `NodePortDot` with `portType='error'` → renders red dot
- All primitives render without React Flow context (pure presentational)
- SF-6's AskNode.tsx imports AskNodePrimitive and wraps it with React Flow Handle components

**Counterexamples:**
- Do NOT import React Flow in any primitive — SF-6 adds handles in its wrapper
- Do NOT include any network/API logic — pure presentational components
- Do NOT create plugin-related primitives — only Ask, Branch, Plugin, Error [REQ-115]
- Do NOT duplicate primitives in SF-6 — SF-6 imports from SF-7 [D-GR-11]

**data-testid assignments:**
- `ask-node-primitive` — Ask card container
- `ask-node-primitive-label` — label text
- `ask-node-primitive-actor-badge` — actor name badge
- `branch-node-primitive` — Branch card container
- `branch-node-primitive-label` — label text
- `branch-node-primitive-paths-count` — output paths badge
- `plugin-node-primitive` — Plugin card container
- `plugin-node-primitive-label` — label text
- `error-node-primitive` — Error card container
- `error-node-primitive-label` — label text
- `error-node-primitive-terminal` — terminal indicator
- `node-port-dot` — port dot
- `edge-type-label` — edge type pill

**Citations:**
- [decision: D-GR-11] — SF-7 owns node visual primitives
- [decision: D-GR-13] — ErrorNode as 4th atomic type

### STEP-68: Tools Library — CMP-136 CRUD, Dedicated Tool Preflight, Built-in + Custom Catalog

**Objective:** Build the Tools library as a full CRUD route with list, detail, and editor states backed by SF-7's tool endpoints. The page must clearly separate built-in tool constants from user-owned custom tools, reuse `EntityDeleteDialog` for reference-safe delete flows, and feed the Role editor through the shared `/api/tools` catalog.

**Requirement IDs:** REQ-110, REQ-112, REQ-113, REQ-114
**Journey IDs:** J-39, J-41

**Scope:**
| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/libraries/tools/ToolsListPage.tsx` | create |
| `tools/compose/frontend/src/features/libraries/tools/ToolEditorForm.tsx` | create |
| `tools/compose/frontend/src/features/libraries/tools/ToolChecklistGrid.tsx` | read |
| `tools/compose/frontend/src/features/libraries/shared/LibraryCollectionPage.tsx` | read |
| `tools/compose/frontend/src/features/libraries/shared/EntityDeleteDialog.tsx` | read |
| `tools/compose/frontend/src/features/libraries/shared/ResourceStateCard.tsx` | read |
| `tools/compose/frontend/src/features/libraries/hooks/useReferenceCheck.ts` | read |
| `tools/compose/frontend/src/features/libraries/hooks/useLibraryEntity.ts` | read |

**Instructions:**

1. **Render the two-tier catalog on `/tools`.**
- Built-in tool cards always render first from the `/api/tools` response, are visually distinct, and do not expose edit or delete actions.
- Custom tools render in the reusable list shell with create, select, edit, and delete flows.
- The empty state applies only to the custom section; the built-in section remains visible even for a first-time user.

2. **Implement `ToolEditorForm` (CMP-136).**
- Fields: `name`, `source`, `description`, and optional `input_schema`.
- `source` is immutable after create; edit mode may change name, description, and input schema only.
- Use the tool-specific allowlist regex and 256KB JSON guards from STEP-63.
- Use the same four save states as the design: `draft`, `invalid`, `saving`, `saved`.

3. **Use a dedicated GET preflight before tool delete.**
- Opening delete on a custom tool must call `GET /api/tools/{id}/references`.
- If blocked, show role names in the `blocked-roles` state of `EntityDeleteDialog`.
- If ready, show the confirm-delete state and perform the DELETE only after explicit confirmation.
- After successful delete, invalidate `/api/tools` and any role-form queries that depend on it.

4. **Keep role-backed tool references explicit in the editor UX.**
- If the user renames a custom tool, surface the backend warning that existing role `tools` strings are not rewritten automatically.
- `ToolChecklistGrid` must update after invalidation so deleted tools are no longer selectable and renamed tools show their current metadata.

**Acceptance Criteria:**
- Opening `/tools` shows a built-in section and a custom section in the same route.
- Creating a custom tool persists it to the custom section and makes it available in `ToolChecklistGrid` without a full page reload.
- Opening delete on a referenced custom tool issues `GET /api/tools/{id}/references` first and renders the blocked-by-roles state.
- Deleting an unreferenced custom tool removes it from `/tools` and from future role checklist queries after invalidation.
- Opening `/tools/:id` for another user's tool renders the `not-found` `ResourceStateCard` state.

**Counterexamples:**
- Do NOT use `workflow_entity_refs` for tool delete protection.
- Do NOT send DELETE first and interpret 409 as discovery.
- Do NOT allow edits to built-in tool cards.
- Do NOT keep deleted custom tools visible in the role checklist after refetch.

**data-testid assignments:**
- `tools-list-page`, `tools-builtin-section`, `tools-custom-section`
- `tools-builtin-card-{name}`, `tools-custom-card-{id}`
- `tool-editor-form`, `tool-editor-form-draft`, `tool-editor-form-invalid`, `tool-editor-form-saving`, `tool-editor-form-saved`
- `tool-name-input`, `tool-name-error`, `tool-source-radio`, `tool-description-input`, `tool-input-schema-editor`
- `tool-save-btn`, `tool-delete-btn`, `tool-cancel-btn`
- `tool-checklist-grid`, `tool-checklist-builtin-group`, `tool-checklist-custom-group`, `tool-checklist-item-{name}`, `tool-checklist-browse-link`

**Citations:**
- [decision: D-GR-7] — Full Tool Library CRUD is required.
- [code: `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:119`] — CMP-136 ToolEditorForm contract.
- [code: `iriai-compose/iriai_compose/actors.py:13`] — Tool usage is persisted on `Role.tools`, so tool delete blockers are role-backed.

### STEP-69: Task Templates Library — Scoped Canvas + CMP-137 ActorSlotsEditor on Embedded `actor_slots`

**Objective:** Build the Task Templates library route on top of SF-6's scoped editor primitives while persisting actor-slot definitions in the template record itself. This step keeps the isolated editor-store requirement, implements `ActorSlotsEditor` (CMP-137) as a reusable component, and round-trips `actor_slots` through template create/update/detail payloads backed by the `custom_task_templates.actor_slots` JSONB column.

**Requirement IDs:** REQ-111, REQ-112, REQ-113, REQ-114
**Journey IDs:** J-42, J-43

**Scope:**
| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/libraries/templates/TemplatesListPage.tsx` | create |
| `tools/compose/frontend/src/features/libraries/templates/TaskTemplateEditorView.tsx` | create |
| `tools/compose/frontend/src/features/libraries/templates/TemplateWizardDialog.tsx` | create |
| `tools/compose/frontend/src/features/libraries/templates/SidePanel.tsx` | create |
| `tools/compose/frontend/src/features/libraries/templates/ActorSlotsEditor.tsx` | create |
| `tools/compose/frontend/src/features/libraries/templates/IOInterfaceEditor.tsx` | create |
| `tools/compose/frontend/src/features/libraries/templates/IOPort.tsx` | create |
| `tools/compose/frontend/src/features/libraries/templates/ScaleBadge.tsx` | create |
| `tools/compose/frontend/src/features/libraries/templates/MiniToolbar.tsx` | create |
| `tools/compose/frontend/src/features/editor/store/editorStore.ts` | read |
| `tools/compose/frontend/src/features/editor/canvas/EditorCanvas.tsx` | read |
| `tools/compose/frontend/src/features/editor/serialization/deserializeFromYaml.ts` | read |
| `tools/compose/frontend/src/features/editor/serialization/serializeToYaml.ts` | read |
| `tools/compose/backend/app/schemas/custom_task_template.py` | read |

**Instructions:**

1. **Keep the isolated-store template editor pattern.**
- `TaskTemplateEditorView` must create its own store instance via `createEditorStore({ scopedMode: true })` and never import the workflow editor singleton.
- `scopedMode: true` removes phase-creation affordances and keeps the template canvas limited to the task-template authoring surface.

2. **Implement `ActorSlotsEditor` (CMP-137) as a reusable section component.**
- The component edits an array of slot definitions on the template draft, not a remote per-row resource.
- Required validation: unique `slot_key`, non-empty slot names, allowed actor-type selection present, and valid current-user default role ids.
- Save states are `empty`, `populated`, `invalid`, and `saved`.

3. **Persist `actor_slots` through template create / update / detail payloads.**
- Template GET, POST, and PUT payloads include `actor_slots` as an array of embedded definitions.
- Saving a template serializes the current canvas YAML plus the current `actor_slots` array in one request.
- Reloading the template rehydrates the editor and the actor-slot section from the same response body.

4. **Keep actor-slot UX local until the user saves the template.**
- Local edits can be added and removed in the side panel without issuing per-row network calls.
- The persisted response from template create / update is the source of truth after save.
- Show a compact success note when the saved response matches the current local slot list.

**Acceptance Criteria:**
- Opening a template creates an isolated editor-store instance and never leaks node state into the workflow editor.
- Adding actor slots and saving the template persists the `actor_slots` array on the template record.
- Refreshing the page and reopening the same template restores the saved `actor_slots` values.
- Entering duplicate slot keys or an invalid default role blocks save and renders the `invalid` state of `ActorSlotsEditor`.
- No actor-slot-specific REST route is required to make the saved slots survive reload.

**Counterexamples:**
- Do NOT create or call `/api/templates/{id}/actor-slots/*` endpoints.
- Do NOT back actor slots with a separate `actor_slots` table.
- Do NOT import the workflow editor's singleton store.
- Do NOT treat unsaved local actor-slot edits as persisted state.

**data-testid assignments:**
- `templates-list-page`
- `template-editor`, `template-canvas`, `template-scale-badge`, `template-mini-toolbar`, `template-side-panel`, `template-save-btn`
- `template-side-panel-metadata`, `template-side-panel-actor-slots`, `template-side-panel-io`
- `template-wizard-dialog`, `template-wizard-name-input`, `template-wizard-create-btn`
- `actor-slots-editor`, `actor-slots-editor-empty`, `actor-slots-editor-populated`, `actor-slots-editor-invalid`, `actor-slots-editor-saved`
- `actor-slots-add-btn`, `actor-slots-row-{index}`, `actor-slots-row-{index}-name`, `actor-slots-row-{index}-actor-type`, `actor-slots-row-{index}-role-picker`, `actor-slots-row-{index}-delete-btn`, `actor-slots-validation-banner`

**Citations:**
- [decision: D-SF7-7] — `actor_slots` persist as embedded template JSON rather than a separate table/API.
- [decision: D-SF7-4] — Template editor must use `createEditorStore({ scopedMode: true })`.
- [code: `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:130`] — CMP-137 ActorSlotsEditor contract.

### STEP-70: Picker Components + Cross-Feature Integration

**Objective:** Build the three SF-7 picker components that the SF-6 editor consumes: `RolePicker`, `SchemaPicker`, and `TemplateBrowser`. This step is limited to library selection and discovery; promotion dialogs, save-to-library affordances, and all plugin-picker surfaces remain out of scope.

**Requirement IDs:** REQ-110, REQ-112, REQ-115
**Journey IDs:** J-39

**Scope:**
| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/libraries/pickers/RolePicker.tsx` | create |
| `tools/compose/frontend/src/features/libraries/pickers/SchemaPicker.tsx` | create |
| `tools/compose/frontend/src/features/libraries/pickers/TemplateBrowser.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspector/AskInspector.tsx` | modify |
| `tools/compose/frontend/src/features/editor/ui/NodePalette.tsx` | modify |

**Instructions:**

1. **Implement `RolePicker` as a library-selection control.**
- Load the current user's library roles from `GET /api/roles`.
- Support three visible states: empty selection, assigned library role, and request error.
- Provide a deep link back to `/roles` for library management, but do not add inline role creation or promotion UI in SF-7.

2. **Implement `SchemaPicker` as the output-schema selection control.**
- Load current-user schemas from `GET /api/schemas`.
- Show the assigned schema label when present and a management link back to `/schemas`.
- Keep selection limited to existing library schemas; promotion UI stays out of scope.

3. **Implement `TemplateBrowser` for the node palette.**
- Load task templates from `GET /api/templates` and render them as draggable palette entries.
- Dragging a template stamps a library-backed template group into the SF-6 canvas.
- Empty and error states are inline to the palette section and do not block the rest of the editor.

4. **Wire the pickers into SF-6 without reintroducing plugin or promotion surfaces.**
- Modify `AskInspector.tsx` to mount `RolePicker` in the actor section and `SchemaPicker` in the output-schema section.
- Modify `NodePalette.tsx` to render `TemplateBrowser` below the primitive node list.
- Do not create `PromotionDialog`, `PromotionPreview`, `PluginPicker`, or any `Save to Library` affordance in this step.

**Acceptance Criteria:**
- `RolePicker` shows current-user library roles in `AskInspector` and writes the chosen role id into the node draft.
- `SchemaPicker` shows current-user schemas in `AskInspector` and writes the chosen schema id into the node draft.
- `TemplateBrowser` appears in the node palette and allows stamping library templates into the canvas.
- No plugin-picker or promotion UI appears anywhere in the SF-7 picker surface.

**Counterexamples:**
- Do NOT create `PromotionDialog` or `PromotionPreview`.
- Do NOT create `PluginPicker` or integrate with any plugin inspector.
- Do NOT treat library pickers as delete-preflight surfaces; they are read-only selection surfaces.

**data-testid assignments:**
- `role-picker`, `role-picker-dropdown`, `role-picker-option-{id}`, `role-picker-assigned`, `role-picker-clear-btn`, `role-picker-open-library-link`, `role-picker-error`
- `schema-picker`, `schema-picker-dropdown`, `schema-picker-option-{id}`, `schema-picker-assigned`, `schema-picker-clear-btn`, `schema-picker-open-library-link`, `schema-picker-error`
- `template-browser`, `template-browser-item-{id}`, `template-browser-empty`, `template-browser-error`, `template-browser-manage-link`

**Citations:**
- [decision: REQ-115] — No plugin library or PluginPicker surfaces.
- [code: `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:176`] — J-39 requires selecting a library role from `RolePicker` in the editor.

    <h3>Risks</h3>
    <table class='data-table'>
        <thead><tr><th>ID</th><th>Risk</th></tr></thead>
        <tbody><tr>
            <td><code>RISK-59</code></td>
            <td>Materialized ref rows can drift if an SF-7 post-commit refresh or purge callback fails after the SF-5 workflow transaction has already committed. Mitigation: idempotent refresh/purge logic, periodic scheduler repair, and the admin-only manual reconcile endpoint.</td>
        </tr><tr>
            <td><code>RISK-60</code></td>
            <td>Renaming a custom tool leaves stale string references in <code>Role.tools</code>. Mitigation: backend rename warnings, explicit role-backed delete blockers, and shared query invalidation so stale names become visible and fixable in role forms.</td>
        </tr><tr>
            <td><code>RISK-61</code></td>
            <td>Embedded <code>actor_slots</code> JSONB may become inconsistent if template handlers or serializers bypass the shared slot schema. Mitigation: validate on both template create and update, and reuse the same slot schema across frontend and backend.</td>
        </tr><tr>
            <td><code>RISK-62</code></td>
            <td>The admin reconciliation endpoint scans all active workflows and is heavier than ordinary library routes. Mitigation: <code>require_admin</code>, <code>5/hour</code> rate limiting, and structured audit logging of every reconcile run.</td>
        </tr><tr>
            <td><code>RISK-63</code></td>
            <td>Picker data can briefly lag after library mutations. Mitigation: React Query invalidation on successful create/update/delete and stale-while-revalidate list rendering instead of cached staleness.</td>
        </tr><tr>
            <td><code>RISK-64</code></td>
            <td>The template editor can leak state into the workflow editor if it accidentally reuses the singleton store. Mitigation: enforce <code>createEditorStore({ scopedMode: true })</code> in the template route and reject singleton imports in code review.</td>
        </tr></tbody>
    </table>

## Journey Verifications

### J-39: Create and Use a Role from the Roles Library

| Step | Action | Verify |
|------|--------|--------|
| 1 | Navigate to `/roles`. | **Browser:** `expect: "[data-testid='roles-list-page'] and [data-testid='library-collection-page'] are visible"` |
| 2 | Click `+ New Role`. | **Browser:** `expect: "[data-testid='role-editor-form'] and [data-testid='role-editor-form-draft'] are visible"` |
| 3 | Enter a role name and blur the field. | **API:** `expect: "GET /api/roles/check-name?name=test-pm returns { available: true }"` |
| 4 | Fill prompt and choose tools. | **Browser:** `expect: "[data-testid='role-prompt-editor'], [data-testid='role-tools-builtin-section'], and [data-testid='role-tools-custom-section'] are visible"` |
| 5 | Save the role. | **API:** `expect: "POST /api/roles returns 201 with prompt, tool list, and role id"` |
| 6 | Open the workflow editor and select the new role. | **Browser:** `expect: "[data-testid='role-picker-option-{id}'] is visible in AskInspector"` |

### J-40: Delete a Role Referenced by Saved Workflows

| Step | Action | Verify |
|------|--------|--------|
| 1 | Open delete on a referenced role. | **API:** `expect: "GET /api/roles/references/{id} returns { total: 1, blocked_by: 'workflows' }"` |
| 2 | Observe the dialog while preflight resolves. | **Browser:** `expect: "[data-testid='entity-delete-dialog-checking'] becomes [data-testid='entity-delete-dialog-blocked-workflows'] without exposing a delete button"` |
| 3 | Remove the saved workflow reference and persist the workflow. | **Database:** `query: "SELECT COUNT(*) FROM workflow_entity_refs WHERE entity_type='role' AND entity_id=:role_id" -> 0 after the persisted workflow save completes` |
| 4 | Reopen delete. | **API:** `expect: "GET /api/roles/references/{id} returns { total: 0 }"` |
| 5 | Confirm delete. | **API:** `expect: "DELETE /api/roles/{id} returns 204"` |

### J-41: Delete a Tool Referenced by Roles (PRD J-3)

| Step | Action | Verify |
|------|--------|--------|
| 1 | Open delete on a referenced custom tool. | **API:** `expect: "GET /api/tools/{id}/references returns { total: 1, blocked_by: 'roles' }"` |
| 2 | Observe the blocked dialog state. | **Browser:** `expect: "[data-testid='entity-delete-dialog-blocked-roles'] is visible and does not list workflow names"` |
| 3 | Remove the tool from the blocking roles and save those roles. | **API:** `expect: "PUT /api/roles/{id} persists tools without the deleted tool name"` |
| 4 | Retry delete. | **API:** `expect: "DELETE /api/tools/{id} returns 204"` |
| 5 | Reopen a role editor. | **Browser:** `expect: "[data-testid='tool-checklist-item-{name}'] is no longer present after refetch"` |

### J-42: Persist Actor Slots in a Task Template (PRD J-4)

| Step | Action | Verify |
|------|--------|--------|
| 1 | Open a template and add an actor slot. | **Browser:** `expect: "[data-testid='actor-slots-row-0-name'] accepts a slot key and [data-testid='actor-slots-row-0-role-picker'] accepts a default role"` |
| 2 | Save the template. | **API:** `expect: "PUT /api/templates/{id} returns actor_slots in the response body"` |
| 3 | Inspect persistence. | **Database:** `query: "SELECT actor_slots FROM custom_task_templates WHERE id=:template_id" -> JSON contains the saved slot_key` |
| 4 | Refresh and reopen the template. | **Browser:** `expect: "[data-testid='actor-slots-editor-populated'] is visible with the saved slot"` |

### J-43: Reject Invalid Actor Slot Definitions (PRD J-5)

| Step | Action | Verify |
|------|--------|--------|
| 1 | Enter duplicate slot keys or an invalid default role. | **Browser:** `expect: "[data-testid='actor-slots-editor-invalid'] and [data-testid='actor-slots-validation-banner'] are visible"` |
| 2 | Attempt save. | **API:** `expect: "PUT /api/templates/{id} with invalid actor_slots returns 422"` |
| 3 | Correct the slot definitions and save again. | **API:** `expect: "PUT /api/templates/{id} returns 200 with only the corrected actor_slots array"` |
| 4 | Reload the template. | **Browser:** `expect: "[data-testid='actor-slots-editor-populated'] shows only corrected persisted slots"` |

## Test ID Registry

```text
# Shared list shell (CMP-134)
library-collection-page
library-collection-page-loading
library-collection-page-empty
library-collection-page-error
library-grid
library-grid-card-{id}
library-toolbar
library-toolbar-new-btn
library-toolbar-search
library-toolbar-view-toggle
library-empty-state
library-empty-state-new-btn
tip-callout

# EntityDeleteDialog (CMP-133)
entity-delete-dialog
entity-delete-dialog-checking
entity-delete-dialog-blocked-workflows
entity-delete-dialog-blocked-roles
entity-delete-dialog-ready
entity-delete-dialog-error
entity-delete-dialog-ref-list
entity-delete-dialog-retry-btn
entity-delete-dialog-close-btn
entity-delete-dialog-delete-btn

# ResourceStateCard (CMP-138)
resource-state-card
resource-state-card-loading
resource-state-card-not-found
resource-state-card-error
resource-state-card-validation
resource-state-card-action-btn

# Roles Library / RoleEditorForm (CMP-135)
roles-list-page
role-editor-form
role-editor-form-draft
role-editor-form-invalid
role-editor-form-saving
role-editor-form-saved
role-name-input
role-name-error
role-model-picker
role-effort-picker
role-prompt-editor
role-tools-grid
role-tools-builtin-section
role-tools-custom-section
role-tool-chip-{name}
role-metadata-editor
role-save-btn
role-cancel-btn
role-delete-btn
role-validation-banner

# Output Schemas Library
schemas-list-page
schema-editor
schema-name-input
schema-description-input
schema-validate-btn
schema-save-btn
schema-code-editor
schema-preview-tree
schema-preview-property-{name}
schema-preview-valid-badge
schema-preview-error-banner
schema-dual-pane-divider

# Node Visual Primitives
ask-node-primitive
ask-node-primitive-label
ask-node-primitive-actor-badge
branch-node-primitive
branch-node-primitive-label
branch-node-primitive-paths-count
plugin-node-primitive
plugin-node-primitive-label
error-node-primitive
error-node-primitive-label
error-node-primitive-terminal
node-port-dot
edge-type-label

# Tools Library / ToolEditorForm (CMP-136)
tools-list-page
tools-builtin-section
tools-custom-section
tools-builtin-card-{name}
tools-custom-card-{id}
tool-editor-form
tool-editor-form-draft
tool-editor-form-invalid
tool-editor-form-saving
tool-editor-form-saved
tool-name-input
tool-name-error
tool-source-radio
tool-description-input
tool-input-schema-editor
tool-save-btn
tool-delete-btn
tool-cancel-btn
tool-checklist-grid
tool-checklist-builtin-group
tool-checklist-custom-group
tool-checklist-item-{name}
tool-checklist-browse-link

# Task Templates / ActorSlotsEditor (CMP-137)
templates-list-page
template-editor
template-wizard-dialog
template-wizard-name-input
template-wizard-create-btn
template-canvas
template-scale-badge
template-mini-toolbar
template-side-panel
template-side-panel-metadata
template-side-panel-actor-slots
template-side-panel-io
template-save-btn
actor-slots-editor
actor-slots-editor-empty
actor-slots-editor-populated
actor-slots-editor-invalid
actor-slots-editor-saved
actor-slots-add-btn
actor-slots-row-{index}
actor-slots-row-{index}-name
actor-slots-row-{index}-actor-type
actor-slots-row-{index}-role-picker
actor-slots-row-{index}-delete-btn
actor-slots-validation-banner

# Library pickers
role-picker
role-picker-dropdown
role-picker-option-{id}
role-picker-assigned
role-picker-clear-btn
role-picker-open-library-link
role-picker-error
schema-picker
schema-picker-dropdown
schema-picker-option-{id}
schema-picker-assigned
schema-picker-clear-btn
schema-picker-open-library-link
schema-picker-error
template-browser
template-browser-item-{id}
template-browser-empty
template-browser-error
template-browser-manage-link
```


