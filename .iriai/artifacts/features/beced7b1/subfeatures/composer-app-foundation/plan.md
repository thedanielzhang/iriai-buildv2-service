### SF-5: Composer App Foundation & Tools Hub

<!-- SF: composer-app-foundation -->


# Unified Technical Plan — iriai-compose Workflow Creator

## Decision Log

| ID | Decision | Source |
|----|----------|--------|
| D-A1 | Pure composition — 3 node types (Ask, Branch, Plugin), no mode field. Interview = loop phase with Ask+Ask+Branch. Gate = Ask(verdict)+Branch(approve/reject). | User choice (interview) |
| D-A2 | Plugin registry — Two-tier types+instances in UI, registry dict + entry-point discovery at runtime. Consuming project provides callables. | Design D-41 + SF-2 plan |
| D-A3 | Repo topology — `tools/compose/frontend` (Compose SPA), `tools/compose/backend` (FastAPI+PostgreSQL), `platform/toolshub/frontend` (static SPA, no backend). `iriai-compose` extended. `iriai-build-v2` additive changes. `tools/iriai-workflows` NOT used. | User correction (interview) + revision (compose under tools/) |
| D-A4 | iriai-build-v2 additive — Import declarative runner from iriai-compose, support loading and running YAML workflow definitions. | User clarification (interview) |
| D-A5 | PostgreSQL (not SQLite) — dedicated instance, isolated migration chain with `alembic_version_compose` table. | SF-5 PRD REQ-2, Broad Architecture BA-2 |
| D-A6 | Schema design — Unified port/edge models, 4-level context hierarchy, store model with dot-notation, expression-based conditions. All per SF-1 plan. | SF-1 plan (source of truth) |
| D-A7 | DAG runner — Single ExecutionGraph engine for workflow+phase+nested levels, entry-point plugin discovery, exec()-based transforms. Workflow-level inputs validated before execution, passed to first phase via `$input` port. All per SF-2 plan. | SF-2 plan (source of truth) |
| D-A8 | 3-category reclassification — Infrastructure→5 general plugins, transforms→8 inline edge transforms, computation→3 AskNodes. Per SF-4 plan. | SF-4 plan (source of truth) |
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

**Objective:** Build the FastAPI + PostgreSQL backend for the compose app with CRUD APIs, auth, database models, and seed data.

**Source of Truth:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md`

**Scope:**
- Create: `tools/compose/backend/` (full FastAPI project)
  - `app/main.py` (FastAPI app, CORS, lifespan)
  - `app/config.py` (settings from env)
  - `app/auth.py` (JWKS JWT validation via auth-python)
  - `app/db.py` (SQLAlchemy async engine + sessionmaker)
  - `app/models/` (6 SQLAlchemy models: Workflow, Role, OutputSchema, TaskTemplate, PluginType, PluginInstance)
  - `app/routers/` (workflows, roles, schemas, templates, plugins, schema_export)
  - `app/seed.py` (idempotent seeding from SF-4 content)
  - `alembic/` (migrations with `alembic_version_compose` table)
  - `pyproject.toml` (deps: fastapi, sqlalchemy[asyncio], asyncpg, alembic, auth-python, iriai-compose)
  - `Dockerfile`
- Read: SF-4 pre-seeded content (workflows, roles, schemas, templates, plugins)
- Read: `iriai_compose/declarative/schema.py` (JSON Schema export for `/api/schema/workflow`)

**Database Models (per SF-5 PRD):**
- `Workflow`: id (UUID), name, yaml_content (TEXT), is_example, user_id, created_at, updated_at, deleted_at
- `Role`: id, name, model, system_prompt, tools (JSON), metadata (JSON), is_example, user_id
- `OutputSchema`: id, name, description, json_schema (JSON), is_example, user_id
- `TaskTemplate`: id, name, description, yaml_content, is_example, user_id
- `PluginType`: id, name, description, inputs (JSON), outputs (JSON), config_schema (JSON), category, is_builtin, user_id
- `PluginInstance`: id, name, plugin_type_id (FK), config (JSON), is_example, user_id

**API Endpoints:**
- `GET/POST /api/workflows`, `GET/PUT/DELETE /api/workflows/{id}`, `POST /api/workflows/{id}/validate`, `GET /api/workflows/{id}/export`
- `GET/POST /api/roles`, `GET/PUT/DELETE /api/roles/{id}`
- `GET/POST /api/schemas`, `GET/PUT/DELETE /api/schemas/{id}`
- `GET/POST /api/templates`, `GET/PUT/DELETE /api/templates/{id}`
- `GET/POST /api/plugins/types`, `GET/PUT/DELETE /api/plugins/types/{id}`
- `GET/POST /api/plugins/instances`, `GET/PUT/DELETE /api/plugins/instances/{id}`
- `GET /api/schema/workflow` (JSON Schema from iriai-compose — includes `inputs`/`outputs` fields)
- `GET /health`

**Auth (per SF-5 PRD + production bug patterns from MEMORY.md):**
- JWKS JWT validation (RS256) via auth-python
- Use `AUTH_SERVICE_PUBLIC_URL` env var for issuer validation (NOT internal Railway URL)
- All resources scoped by `user_id` from JWT `sub` claim
- Soft-delete with `deleted_at` timestamp, 30-day recovery
- Cursor-based pagination (20 default, 100 max)

---

### STEP-41: Compose Frontend Shell (SF-5 — Frontend)

**Objective:** Scaffold the React + Vite SPA with XP design system, ExplorerLayout, auth, routing, and CRUD views.

**Source of Truth:** `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md` + Design Decisions

**Scope:**
- Create: `tools/compose/frontend/` (full Vite React project)
  - Design system: XP components vendored from deploy-console (Button, Window, Card, Input, Toast)
  - `src/styles/windows-xp.css` (purple theme + design tokens from deploy-console)
  - Auth: `@homelocal/auth` integration with `compose_` token prefix
  - Layout: ExplorerLayout (sidebar + content), AddressBar, Toolbar, StatusBar
  - Sidebar: SidebarTree with entity-type folders (Workflows, Roles, Schemas, Templates, Plugins)
  - Content views: GridView, DetailsView, EmptyState, SkeletonLoader
  - CRUD: NewDropdown, ConfirmDialog, ContextMenu, inline rename
  - Routing: React Router with `/workflows`, `/roles`, `/schemas`, `/templates`, `/plugins`
  - State: Zustand stores for entities, sidebar, and UI state
  - API client: Axios with JWT interceptor
  - MobileBlockScreen at <768px [D-18]
  - `package.json`, `vite.config.ts`, `tsconfig.json`
  - `Dockerfile`

**Key Components (from design doc Section 4 — Compose App Shell):**
- CMP-1 through CMP-29 (XP primitives + Explorer shell)
- CMP-30 through CMP-47 (Node/port/phase visual primitives — implemented here, consumed by SF-6)

**Acceptance Criteria:**
- Navigate to compose.iriai.app → MobileBlockScreen on <768px, ExplorerLayout on >=768px
- Sidebar tree shows 5 entity-type folders
- Grid/Details view toggle works with localStorage persistence
- CRUD operations: create, rename, duplicate, delete (with ConfirmDialog)
- Search filters entities by name (300ms debounce)
- Auth flow: OAuth redirect → authenticated layout → 401 handling
- No HubNavBar, no tools hub link, no deploy access [D-30]

---

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

### STEP-44: Frontend Integration & Seed Data

**Objective:** Wire editor (SF-6) and libraries (SF-7) together via picker components, integrate with backend API, load SF-4 pre-seeded examples, and implement remaining cross-cutting concerns.

**Scope:**
- Modify: `tools/compose/frontend/src/features/editor/` — integrate library pickers (RolePicker, SchemaPicker, PluginPicker, TemplateBrowser)
- Modify: `tools/compose/backend/app/seed.py` — load SF-4 YAML/JSON seed data
- Create: `tools/compose/frontend/src/features/editor/hooks/` — useWorkflowExport (YAML), useWorkflowImport, useAutoSave
- Wire: onPromoteRole, onPromoteSchema, onSaveTemplate mutation flows
- Wire: workflow validation (client-side + POST /api/workflows/{id}/validate)
- Wire: YAML export (GET /api/workflows/{id}/export)

**Acceptance Criteria:**
- "Examples" section shows 3 pre-seeded workflows (Planning, Develop, Bugfix) with cyan badge
- Opening an example workflow renders all phases, nodes, edges correctly on canvas
- Example workflows display their declared `inputs` in the workflow-level inspector (e.g., planning shows `scope: ScopeOutput`)
- Role picker in Ask inspector shows all roles (library + inline)
- "Save to Library" promotes inline role → toast → immediately available in all pickers
- Validation runs on save and manual trigger → errors appear on canvas + panel
- YAML export downloads valid workflow file including `inputs`/`outputs` declarations

---

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
| RISK-83 | Inline Python transforms security (exec()) | Low | Acceptable risk — same agents that run transforms already have full code execution | STEP-37 |
| RISK-84 | Auth issuer mismatch (JWT validation URL) | High if misconfigured | Use AUTH_SERVICE_PUBLIC_URL env var, document in deployment checklist | STEP-40 |
| RISK-85 | PostgreSQL migration chain integrity | Medium | Isolated alembic_version_compose table, init script handles first-run | STEP-40 |
| RISK-86 | Cross-repo coordination — 5 repos must stay compatible | Medium | Pin iriai-compose version in consumers, schema version field in YAML | STEP-45 |

---

## Environment Variables

| Name | Service | Default | Purpose |
|------|---------|---------|---------|
| DATABASE_URL | compose-backend | — | PostgreSQL connection string |
| AUTH_SERVICE_PUBLIC_URL | compose-backend | — | Public URL for JWT issuer validation |
| AUTH_JWKS_URL | compose-backend | — | JWKS endpoint for RS256 key discovery |
| CORS_ORIGINS | compose-backend | `https://compose.iriai.app` | Allowed CORS origins |
| VITE_API_URL | compose-frontend | `/api` | Backend API base URL |
| VITE_AUTH_CLIENT_ID | compose-frontend | — | OAuth client ID |
| VITE_AUTH_URL | compose-frontend | — | OAuth authorization endpoint |
| VITE_AUTH_CLIENT_ID | toolshub-frontend | — | OAuth client ID (tools) |
| VITE_COMPOSE_URL | toolshub-frontend | `https://compose.iriai.app` | Compose app URL for tool card link |

---


---