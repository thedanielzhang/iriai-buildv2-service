# Compiled Technical Plan: iriai-compose Declarative Workflow System

<!-- This document is a faithful union of 7 subfeature plan artifacts. -->
<!-- All detail is preserved: every step, AC, counterexample, citation, instruction, risk, journey verification, and file manifest entry. -->
<!-- Step IDs and Risk IDs have been globally renumbered for uniqueness. Original IDs are preserved in HTML comments. -->

## Decomposition Overview

### Subfeatures

| ID | Slug | Name | Requirement IDs | Journey IDs |
|----|------|------|-----------------|-------------|
| SF-1 | declarative-schema | Declarative Schema & Primitives | R1, R2, R3, R4, R5, R6 | J-1, J-2, J-4 |
| SF-2 | dag-loader-runner | DAG Loader & Runner | R7 | J-2 |
| SF-3 | testing-framework | Testing Framework | R8, R23 | J-2 |
| SF-4 | workflow-migration | Workflow Migration & Litmus Test | R9 | J-2 |
| SF-5 | composer-app-foundation | Composer App Foundation & Tools Hub | R10, R11, R12 | J-1 |
| SF-6 | workflow-editor | Workflow Editor & Canvas | R13, R14, R15 | J-1, J-3, J-4, J-5 |
| SF-7 | libraries-registries | Libraries & Registries | R16, R17, R18, R19, R20, R21, R22 | J-1, J-2, J-3, J-5 |

**SF-1: Declarative Schema & Primitives**
- Description: Define the YAML-primary DAG format in iriai-compose as Pydantic models and JSON Schema. Six primitive node types (Ask, Map, Fold, Loop, Branch, Plugin) with typed configuration. Typed edges with optional named transform references. Phase groupings with on_start/on_done hooks and skip conditions. Plugin interface declarations (inputs, outputs, config schema). Cost configuration metadata (budget caps, model pricing, alert thresholds per node/phase). Schema versioning field. No execution logic — this is pure data modeling and validation. Produces the schema that the loader, runner, testing framework, and composer UI all consume.
- Rationale: The schema is the foundational contract for the entire system. Everything else — runtime execution, testing, visual editing — depends on this format definition being stable and complete. Isolating it ensures the format is designed for all consumers, not biased toward any single one.

**SF-2: DAG Loader & Runner**
- Description: Build the YAML loader that hydrates declarative configs into executable DAG objects, and the top-level run() entry point in iriai-compose. Loader: parse YAML, validate against schema, resolve node references, build dependency graph, wire typed edges. Runner: topological sort for execution order, respect phase boundaries, execute nodes against provided AgentRuntime instances, manage artifact flow between nodes via edge transforms, resolve named transforms/hooks from a registry, handle Map (parallel fan-out), Fold (sequential accumulation), Loop (repeat-until), Branch (conditional routing), and Plugin (external service delegation). Extends existing DefaultWorkflowRunner infrastructure.
- Rationale: The loader and runner are tightly coupled — you can't meaningfully test loading without running, and the runner's needs (topological execution, artifact passing, transform resolution) directly inform how the loader hydrates the schema. Grouping them ensures the hydration format matches execution needs.

**SF-3: Testing Framework**
- Description: Build iriai_compose.testing — a purpose-built testing module for declarative workflows. Schema validation: structural correctness, type flow across edges, required fields, cycle detection. Execution testing: mock/echo AgentRuntime that records calls and returns configurable responses, execution path assertions (assert node X reached before node Y, assert artifact produced at key K, assert branch took path P), snapshot testing for YAML round-trips. Test fixtures: helpers to build minimal valid workflows programmatically for unit tests. Extends existing MockAgentRuntime from conftest.py. This framework is used by SF-4 (migration) to prove the litmus test and by any future workflow developer for regression testing.
- Rationale: A dedicated testing framework is distinct from both the runtime (SF-2) and the migration (SF-4). It produces reusable infrastructure — mock runtimes, assertion helpers, fixtures — that the migration exercises but doesn't define. Keeping it separate ensures the framework is general-purpose, not migration-specific.

**SF-4: Workflow Migration & Litmus Test**
- Description: Translate iriai-build-v2's three workflows (planning, develop, bugfix) from imperative Python to declarative YAML. Planning: 6 phases (scoping, PM, design, architecture, plan review, task planning) with patterns including broad interview loops, decomposition with gate, per-subfeature Fold with tiered context assembly, integration review, gate-and-revise loops, compilation, and interview-based gate review. Develop: DAG execution groups (parallel within group, sequential across), per-group verification with retry, handover document compression, QA → review → user approval loop. Bugfix: linear 8-phase flow with parallel RCA (dual analyst), diagnosis-and-fix retry loop, preview server plugin integration. Register all required named transforms (tiered context builder, handover compression, feedback formatting, etc.) and hooks. Write comprehensive test suites using the SF-3 testing framework proving execution path equivalence.
- Rationale: The migration is both the completeness proof for the schema (SF-1) and the first real content in the system. It requires deep analysis of iriai-build-v2's imperative code — a fundamentally different skill from schema design or framework building. Keeping it separate lets the migration reveal schema gaps without being conflated with schema development.

**SF-5: Composer App Foundation & Tools Hub**
- Description: Scaffold the iriai-workflows webapp (React + FastAPI + SQLite) and the tools.iriai.app hub. Backend: FastAPI app structure, SQLAlchemy models for all 8 data entities (Workflow, WorkflowVersion, Role, OutputSchema, CustomTaskTemplate, PhaseTemplate, PluginConfig, TransformFunction), Alembic migrations, CRUD API endpoints for all entities, JWT auth via auth-python (JWKS validation), user_id scoping on all resources. Frontend: React app with routing, auth-react integration (login/logout, token management), Windows XP / MS Paint design system (purple gradients, 3D beveled effects, frosted glass taskbar — matching deploy-console), Workflows List landing page (grid/list of saved configs, create/duplicate/import/delete/search). Tools hub: minimal React SPA at tools.iriai.app reading dev_tier JWT claim, displaying tier-gated tool cards, linking to composer URL. Railway deployment configs for both apps.
- Rationale: The app foundation provides the infrastructure (auth, database, API, routing, design system) that both the editor (SF-6) and libraries (SF-7) build on. Including the tools hub here is natural — it's a single page sharing the same auth setup. This subfeature can be developed in parallel with the iriai-compose work (SF-1 through SF-4).

**SF-6: Workflow Editor & Canvas**
- Description: Build the primary workflow editing experience in iriai-workflows. React Flow DAG canvas as the main editing surface with drag-and-drop node placement. Node palette sidebar with all 6 primitives (Ask, Map, Fold, Loop, Branch, Plugin) plus custom task templates and phase templates from libraries. Collapsible YAML pane with bidirectional sync (canvas ↔ YAML, lossless round-trip). Node inspector panel with context-specific configuration: Ask (role picker/inline creator, prompt template editor with {{ variable }} interpolation, output schema selector, hooks, settings), Map/Fold/Loop (collection source, inline sub-canvas for body, max parallelism/iterations), Branch (condition type, named output paths). Edge inspector with transform selection and type annotations. Phase grouping as visual bounding boxes (select nodes → group into phase → configure hooks/skip conditions). Toolbar: save, export YAML, validate (type flow checking, required fields, error highlighting on canvas), version history access, undo/redo. Performance target: responsive with 50+ nodes.
- Rationale: The editor is the core user-facing deliverable — the visual canvas, node inspectors, YAML sync, and validation. It's the largest and most complex frontend subfeature. It consumes the schema (SF-1) to know what fields each node type needs, and consumes libraries (SF-7) for role/schema/template selection. Keeping it separate from libraries allows parallel development of the editing experience and the management surfaces.

**SF-7: Libraries & Registries**
- Description: Build all six library/registry pages in iriai-workflows, plus the version history view. All follow a shared CRUD + list + detail/editor pattern. Roles Library: system prompt editor, tool selector, model picker, metadata fields, import/export CLAUDE.md format, inline-to-library promotion flow from the editor. Output Schemas Library: JSON Schema editor (raw editor, not visual field builder), name/description metadata, referenced by Ask nodes. Custom Task Templates: saved subgraph compositions with defined input/output interfaces, appear in node palette alongside primitives, expandable to inspect internal structure. Phases Library: saved phase templates (node groups + hooks + skip conditions), droppable into workflows as reusable units. Plugins Registry: browse available plugin types, configure instances with parameter schemas, see I/O type declarations, configured instances appear in node palette. Transforms & Hooks Library: named pure functions with input/output type signatures, code preview, used as edge transforms and node hooks. Version History: per-workflow version list, YAML diff between versions, restore to previous version.
- Rationale: All six libraries share the same UI pattern (list → detail → editor) and API pattern (CRUD endpoints scoped to user_id). Grouping them enables shared component extraction (list views, search/filter, editor chrome) and consistent UX. Individually each library is small; together they form a coherent subfeature of comparable complexity to the editor.

### Dependency Edges

#### SF-1 -> SF-2 (python_import)
- **Description:** Loader imports Pydantic schema models (WorkflowConfig, NodeDefinition, EdgeDefinition, PhaseDefinition, etc.) to parse and validate YAML into typed objects. Runner imports node type enums and config models to dispatch execution.
- **Data Contract:** iriai_compose.declarative.schema module exports: WorkflowConfig, AskNode, MapNode, FoldNode, LoopNode, BranchNode, PluginNode, Edge, Phase, CostConfig, TransformRef, HookRef. All are Pydantic BaseModel subclasses with JSON Schema generation via model_json_schema().
- **Owner:** SF-1
- **Citations:**
- **[code]** `iriai-compose/iriai_compose/tasks.py`
  - Excerpt: Existing task types (Ask, Interview, Gate, Choose, Respond) as dataclass models
  - Reasoning: New schema models follow the same pattern but as Pydantic models for YAML/JSON validation

#### SF-1 -> SF-3 (python_import)
- **Description:** Testing framework imports schema models to validate structural correctness and type flow. Uses model_json_schema() for schema-level validation, field accessors for type flow checking across edges.
- **Data Contract:** Same schema module as SF-2 consumes. Additionally uses Edge.transform_ref and Node.output_type for type flow analysis.
- **Owner:** SF-1
- **Citations:**
- **[decision]** `D-10`
  - Excerpt: Custom testing framework built as we develop the schema
  - Reasoning: Testing framework validates schema correctness as a primary function

#### SF-2 -> SF-3 (python_import)
- **Description:** Testing framework uses the runner's run() function and DAG executor to run workflows against mock runtimes. Wraps run() with assertion hooks to track execution paths, artifact production, and branch decisions.
- **Data Contract:** iriai_compose.declarative.run(yaml_path, runtime, workspace, transform_registry, hook_registry) → ExecutionResult. ExecutionResult contains: nodes_executed (ordered list), artifacts (dict), branch_paths_taken (dict), cost_summary.
- **Owner:** SF-2
- **Citations:**
- **[code]** `iriai-compose/tests/conftest.py`
  - Excerpt: MockAgentRuntime records calls with role, prompt, output_type
  - Reasoning: Testing framework extends this mock pattern to work with the new runner

#### SF-1 -> SF-4 (yaml_schema)
- **Description:** Migration produces YAML files conforming to the schema defined in SF-1. The schema must be expressive enough to represent all patterns found in iriai-build-v2's three workflows.
- **Data Contract:** YAML files validated against WorkflowConfig JSON Schema. Migration may surface schema gaps that require SF-1 revisions.
- **Owner:** SF-1
- **Citations:**
- **[decision]** `D-11`
  - Excerpt: Migration plan for converting existing iriai-build-v2 workflows
  - Reasoning: Migration is the completeness test for the schema

#### SF-2 -> SF-4 (python_import)
- **Description:** Migration uses run() to execute translated YAML workflows and verify they produce equivalent behavior to the imperative Python versions.
- **Data Contract:** Same run() interface as SF-3 consumes. Migration also registers named transforms and hooks via TransformRegistry.register(name, fn) and HookRegistry.register(name, fn).
- **Owner:** SF-2
- **Citations:**
- **[code]** `iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py`
  - Excerpt: _build_subfeature_context(), _format_feedback(), to_str()
  - Reasoning: These imperative helpers must be registered as named transforms for the runner to resolve

#### SF-3 -> SF-4 (python_import)
- **Description:** Migration writes test suites using the testing framework's assertion helpers, mock runtimes, and fixtures to prove execution path equivalence.
- **Data Contract:** iriai_compose.testing exports: MockRuntime (configurable responses per role/node), assert_node_reached(result, node_id), assert_artifact_produced(result, key, schema), assert_branch_taken(result, branch_id, path), WorkflowTestCase base class.
- **Owner:** SF-3
- **Citations:**
- **[decision]** `D-10`
  - Excerpt: Custom testing framework built as we develop the schema
  - Reasoning: Migration is the primary consumer of the testing framework

#### SF-1 -> SF-6 (json_schema)
- **Description:** The workflow editor reads the JSON Schema (generated from SF-1's Pydantic models) to know what fields each node type requires, what edge types are valid, and what configuration options exist. The YAML pane serializes/deserializes using this schema. Validation uses it for type flow checking.
- **Data Contract:** JSON Schema published as a static artifact (e.g., workflow-schema.json) or fetched from a backend endpoint. Frontend uses it for: node inspector field generation, edge type validation, YAML syntax validation, export format.
- **Owner:** SF-1
- **Citations:**
- **[decision]** `D-15`
  - Excerpt: Dual-pane with visual graph editor primary, YAML secondary
  - Reasoning: Both the canvas and YAML pane need to understand the schema for rendering and validation

#### SF-5 -> SF-6 (api_and_components)
- **Description:** App foundation provides: authenticated API client (axios with JWT interceptor), React router shell (editor is a route), design system components (XP-themed buttons, panels, inputs), database-backed workflow CRUD (save/load/export endpoints), and auth context (user_id for scoping).
- **Data Contract:** API endpoints: GET/PUT /api/workflows/:id (full YAML content), POST /api/workflows/:id/versions (save new version), POST /api/workflows/:id/validate (server-side validation). React context: useAuth() hook providing user, accessToken. Component library: XPButton, XPPanel, XPInput, XPToolbar, XPSidebar.
- **Owner:** SF-5
- **Citations:**
- **[code]** `platform/deploy-console/deploy-console-frontend/src/app/styles/windows-xp.css`
  - Excerpt: XP-style inset/outset borders, purple gradients, frosted glass taskbar
  - Reasoning: Design system components from SF-5 are consumed by the editor

#### SF-5 -> SF-7 (api_and_components)
- **Description:** App foundation provides the same infrastructure as SF-6: authenticated API client, router shell (library pages are routes), design system components, and CRUD API endpoints for all 8 entity types.
- **Data Contract:** API endpoints: standard REST CRUD for /api/roles, /api/schemas, /api/templates, /api/phases, /api/plugins, /api/transforms. All scoped to authenticated user_id. Response format: { items: [...], total: int } for lists, individual entity for detail. Same React context and component library as SF-6.
- **Owner:** SF-5
- **Citations:**
- **[decision]** `D-14`
  - Excerpt: Screen map confirmed with workflows list as landing page
  - Reasoning: Library pages are sibling routes to the workflows list, all sharing the app shell

#### SF-7 -> SF-6 (react_components)
- **Description:** Libraries expose picker/selector components consumed by the editor's node inspectors. Role picker for Ask nodes, schema selector for output_type, template browser for the node palette, plugin selector, transform picker for edge inspector.
- **Data Contract:** React components: RolePicker({ onSelect, onCreateInline }), SchemaPicker({ onSelect }), TemplateBrowser({ onDrag }), PluginPicker({ onSelect }), TransformPicker({ edgeType, onSelect }). Each fetches from its own API endpoint and renders in the XP design system.
- **Owner:** SF-7
- **Citations:**
- **[decision]** `D-18`
  - Excerpt: Inline + library hybrid for roles
  - Reasoning: The editor needs picker components that bridge to the library data

#### SF-6 -> SF-7 (callback_events)
- **Description:** Editor triggers library mutations: inline role creation promotes to library, subgraph selection saves as custom task template, node group saves as phase template. Editor emits these as callbacks that library components handle.
- **Data Contract:** Callbacks: onPromoteRole(inlineRole) → creates Role via API, onSaveTemplate(selectedNodes, edges, interface) → creates CustomTaskTemplate via API, onSavePhase(selectedNodes, hooks, skipConditions) → creates PhaseTemplate via API. Returns created entity ID for the editor to reference.
- **Owner:** SF-6
- **Citations:**
- **[decision]** `D-18`
  - Excerpt: Inline + library hybrid for roles
  - Reasoning: Inline-to-library promotion requires the editor to trigger library writes

### Decomposition Rationale

The feature splits naturally along two axes: iriai-compose runtime (schema → loader → testing → migration) and iriai-workflows visual app (foundation → editor → libraries). The iriai-compose side forms a strict dependency chain where each layer builds on the previous. The iriai-workflows side has a foundation layer feeding two parallel workstreams (editor and libraries) that integrate at the edges. The tools hub is absorbed into the app foundation since it's a single page sharing the same auth infrastructure. This yields 7 subfeatures of roughly comparable complexity, with clear boundaries and explicit interface contracts between them.

---

## SF-1: Declarative Schema & Primitives
<!-- SF: declarative-schema -->

### Architecture

# Technical Plan: SF-1 Declarative Schema & Primitives (Rev 4 — PRD-Canonical)

## Architecture

This revision makes the SF-1 PRD the single authoritative wire shape and eliminates every divergence introduced by prior plan revisions. Five stale divergences are corrected in this pass:

**1. Actor union wire shape.** The PRD specifies `actor_type` as the discriminator field name (not `type`) with exactly two valid values: `'agent'` and `'human'` (not `'interaction'`). The union is a proper Pydantic discriminated union on `actor_type`. `AgentActorDef` carries `provider`, `model`, `role`, `persistent`, and `context_keys` semantics; `HumanActorDef` carries `identity` and `channel` semantics. Neither embeds environment credentials or a `resolver` string — `resolver` is a runtime concept owned by `InteractionActor` in the existing `iriai_compose.actors` module. Mapping `HumanActorDef(actor_type='human', identity=..., channel=...)` to `InteractionActor(name=..., resolver=...)` is the SF-2 loader/runner boundary's responsibility, not the schema contract's. The declarative schema wire format must not leak the runtime's `resolver` dispatch mechanism into YAML.

**2. BranchNode shape.** The PRD specifies `condition_type / condition / paths` as the complete and closed Branch contract. `merge_function` is not in the PRD, is not approved, and must be rejected by validation. Branch routing uses `paths: dict[str, WorkflowOutputDefinition]` as the sole routable output surface; any downstream merging of parallel branch results is expressed through phase-level edge wiring downstream of the branch, not through a branch-embedded merge callback.

**3. WorkflowConfig closed root.** The PRD explicitly closes the root field set: `schema_version`, `workflow_version`, `name`, `description`, `metadata`, `actors`, `phases`, `edges`, `templates`, `plugins`, `types`, `cost_config`. The fields `stores` and `plugin_instances` are not in this set. Both are rejected by validation with a clear unapproved-root-field error. WorkflowConfig must use `model_config = ConfigDict(extra='forbid')` to surface these as Pydantic errors, and the custom validation layer must additionally emit the canonical error code with migration guidance.

**4. EdgeDefinition naming.** The PRD entity is `EdgeDefinition` (not `Edge`). All references in this plan use `EdgeDefinition` to match the canonical PRD wire-shape name. The `source`/`target` dot-notation contract, optional `transform_fn`, optional `description`, and no `port_type` field remain unchanged.

**5. No `stores.py` schema primitive.** The PRD does not include a `stores` key anywhere in `WorkflowConfig` or `PhaseDefinition`. The `stores.py` module created by Rev 3 is removed from plan scope. `StoreDefinition` and `StoreKeyDefinition` are not part of the SF-1 schema contract. Cost configuration (`cost.py`) is retained because it is referenced explicitly in `WorkflowConfig.cost_config`, `PhaseDefinition.cost`, and `NodeDefinition.cost`.

**Stability.** The accepted baseline from prior revisions remains in force where not contradicted: additive `iriai-compose/iriai_compose/schema/` package placement, dict-keyed port maps, `type_ref`/`schema_def` strict XOR (typed ports are mandatory everywhere — neither field may be omitted on any port including hooks and branch paths — `PortDefinition()` with no args is a validation error), nested phase containment under `phases[].nodes` and `phases[].children`, edge-only hook serialization, YAML shorthand normalization, and backward compatibility with the existing imperative `iriai-compose` API.

**REQ-23 canonical status.** This plan implements the PRD contract exactly. No consumer of this schema module may add alternate actor discriminators or values, extra root registries, alternate branch routing fields, `resolver` on human actors, or runtime `workflow-schema.json` consumption without a later approved decision.

**External APIs.** Implementation is constrained to official doc-verified APIs: Pydantic v2 `model_json_schema()` and `Field(discriminator=...)` discriminated unions, plus PyYAML `safe_load`/`safe_dump`.

### Implementation Steps

#### STEP-1: Create the shared schema foundation under `iriai-compose/iriai_compose/schema/` with the PRD-canonical actor union (`actor_type: agent|human`), strict typed ports (XOR required everywhere), and cost primitives. This step removes `stores.py` from scope and establishes `AgentActorDef`/`HumanActorDef` as the two-variant discriminated union the PRD mandates, with `HumanActorDef` using `identity`/`channel` semantics rather than a `resolver` string.
<!-- SF: declarative-schema | Original: STEP-1 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-compose/iriai_compose/schema/__init__.py` | create |
| `iriai-compose/iriai_compose/schema/base.py` | create |
| `iriai-compose/iriai_compose/schema/actors.py` | create |
| `iriai-compose/iriai_compose/schema/types.py` | create |
| `iriai-compose/iriai_compose/schema/cost.py` | create |
| `iriai-compose/pyproject.toml` | modify |
| `iriai-compose/iriai_compose/actors.py` | read |
| `iriai-compose/iriai_compose/runner.py` | read |

**Instructions:**

1. Create `iriai-compose/iriai_compose/schema/base.py` with the shared low-level port models:
   - `PortDefinition(BaseModel)` with `type_ref: str | None = None`, `schema_def: dict | None = None`, `description: str | None = None`, `required: bool | None = None`, and a `model_validator(mode='after')` enforcing strict XOR: if both `type_ref` and `schema_def` are `None`, raise; if both are set, raise. The error message for the no-type case must say 'Exactly one of type_ref or schema_def is required; untyped ports are not permitted.'.
   - `WorkflowInputDefinition(BaseModel)` with `type_ref: str | None = None`, `schema_def: dict | None = None`, `description: str | None = None`, `required: bool = True`, and the same XOR validator.
   - `WorkflowOutputDefinition(BaseModel)` with `type_ref: str | None = None`, `schema_def: dict | None = None`, `description: str | None = None`, and the same XOR validator.
   - Default factory helpers `_default_inputs() -> dict[str, WorkflowInputDefinition]`, `_default_outputs() -> dict[str, WorkflowOutputDefinition]`, `_default_hooks() -> dict[str, WorkflowOutputDefinition]` — each returns a single-key dict with `type_ref='any'` so default port collections are typed and satisfy the XOR rule. Default input key: `'input'`. Default output key: `'output'`. Default hook keys: `'on_start'` and `'on_end'`.
   - No `name` field on any of these models; the dict key in the parent mapping is the port's name.
   - BUILTIN_TYPE_NAMES: `frozenset({'string', 'int', 'float', 'bool', 'dict', 'list', 'any'})` for downstream type-ref validation.

2. Create `iriai-compose/iriai_compose/schema/actors.py` with the PRD-canonical discriminated union:
   - `RoleDefinition(BaseModel)` mirroring the existing `iriai_compose.actors.Role` field-for-field: `name: str`, `prompt: str`, `tools: list[str]`, `model: str | None`, `effort: Literal['low','medium','high','max'] | None`, `metadata: dict`.
   - `AgentActorDef(BaseModel)` with `actor_type: Literal['agent']`, `role: str | RoleDefinition`, `provider: str | None = None`, `model: str | None = None`, `persistent: bool = False`, `context_keys: list[str]`. The `role` field accepts either a string key referencing a named role in the workflow's roles registry or an inline `RoleDefinition`.
   - `HumanActorDef(BaseModel)` with `actor_type: Literal['human']`, `identity: str | None = None`, `channel: str | None = None`. No `resolver` field. Module docstring must state: 'Discriminator field is `actor_type`, not `type`. Valid values are `agent` and `human` only. `HumanActorDef` carries abstract identity/channel semantics. Mapping to `InteractionActor.resolver` for runtime dispatch is the SF-2 loader's responsibility and does not belong in the schema contract.'
   - `ActorDefinition = Annotated[Union[AgentActorDef, HumanActorDef], Field(discriminator='actor_type')]` using Pydantic v2 discriminated union syntax.

3. Create `iriai-compose/iriai_compose/schema/types.py` with `TypeDefinition(BaseModel)` having `schema_def: dict` and `description: str | None`, plus re-export of `BUILTIN_TYPE_NAMES` from `base.py`.

4. Create `iriai-compose/iriai_compose/schema/cost.py` with `WorkflowCostConfig`, `PhaseCostConfig`, and `NodeCostConfig` as pure data models only — no runtime logic. Typical fields: `budget_usd: float | None`, `alert_threshold_usd: float | None`, `max_tokens: int | None`.

5. Do NOT create `stores.py`. The PRD does not include a `stores` key in `WorkflowConfig` or `PhaseDefinition`. Any reference to `StoreDefinition` or `StoreKeyDefinition` in downstream SFs must be resolved against a later approved decision.

6. Update `iriai-compose/pyproject.toml` to add `pyyaml>=6.0` to the main dependency list only. Do not add a second YAML library.

7. Update `iriai-compose/iriai_compose/schema/__init__.py` to export: `PortDefinition`, `WorkflowInputDefinition`, `WorkflowOutputDefinition`, `ActorDefinition`, `AgentActorDef`, `HumanActorDef`, `RoleDefinition`, `TypeDefinition`, `WorkflowCostConfig`, `PhaseCostConfig`, `NodeCostConfig`, and the default factory helpers.

**Acceptance Criteria:**

- Run `python -c "from iriai_compose.schema import PortDefinition, WorkflowInputDefinition, WorkflowOutputDefinition, ActorDefinition, AgentActorDef, HumanActorDef, RoleDefinition"`; the import succeeds.
- Instantiate `PortDefinition(type_ref='string')` and `PortDefinition(schema_def={'type': 'object'})`; both validate. Instantiate `PortDefinition()` (no type) and `PortDefinition(type_ref='string', schema_def={'type': 'object'})` (both set); both fail with XOR validation errors containing 'type_ref or schema_def is required'.
- Instantiate `AgentActorDef(actor_type='agent', role=RoleDefinition(name='pm', prompt='You are a PM'))`; it validates. Instantiate `HumanActorDef(actor_type='human', identity='reviewer', channel='slack')`; it validates.
- Attempt `HumanActorDef(actor_type='human', resolver='slack.reply')`; Pydantic raises because `resolver` is not a field on `HumanActorDef`.
- Attempt `ActorDefinition.model_validate({'actor_type': 'interaction', 'resolver': 'x'})`; Pydantic raises a discriminator error because `'interaction'` is not a valid `actor_type` value.
- Verify `ActorDefinition.__get_validators__` or Pydantic's discriminated-union resolution returns `AgentActorDef` for `actor_type='agent'` and `HumanActorDef` for `actor_type='human'`, and that the discriminator field name is `actor_type` not `type`.
- Instantiate `WorkflowInputDefinition(type_ref='string')` and `WorkflowOutputDefinition(schema_def={'type': 'array'})`; both validate and expose no `name` field. Instantiate `WorkflowInputDefinition()` (no type); it fails.
- Run `python -c "import importlib.metadata as m; print('pyyaml' in {r.metadata['Name'].lower() for r in m.distributions()})"` in the editable environment and observe `True`.

**Counterexamples:**

- Do NOT use `type` as the discriminator field name. The PRD-canonical discriminator is `actor_type`.
- Do NOT use `'interaction'` as an actor_type value — the only valid values are `'agent'` and `'human'`.
- Do NOT add a `resolver` field to `HumanActorDef`. Resolver mapping belongs to the SF-2 loader, not the schema.
- Do NOT create `stores.py`, `StoreDefinition`, or `StoreKeyDefinition` in this step. They are not in the PRD schema.
- Do NOT add a `name` field onto `PortDefinition`, `WorkflowInputDefinition`, or `WorkflowOutputDefinition`.
- Do NOT allow both `type_ref` and `schema_def` to be `None` — typed ports are mandatory everywhere and `PortDefinition()` must fail.
- Do NOT add `ruamel.yaml` or a second YAML dependency.

**Requirement IDs:** REQ-2, REQ-15, REQ-22, REQ-23

**Journey IDs:** J-1, J-3

**Citations:**

- **[code]** `iriai-compose/iriai_compose/actors.py:8-36`
  - Excerpt: class AgentActor(Actor): role: Role; class InteractionActor(Actor): resolver: str
  - Reasoning: The runtime uses `resolver` on `InteractionActor`, confirming it is a runtime dispatch mechanism. The schema's `HumanActorDef` must not leak this into YAML; `identity` and `channel` are the PRD-canonical declarative fields.
- **[code]** `iriai-compose/iriai_compose/runner.py:214-282`
  - Excerpt: _resolve_interaction_runtime(resolver) routes by string prefix; resolve() dispatches InteractionActor via actor.resolver
  - Reasoning: The resolver-to-runtime mapping is wired in DefaultWorkflowRunner, not in the actor definition. HumanActorDef.channel becomes the resolver key at the loader boundary — this is SF-2 work, not SF-1 schema.
- **[decision]** `D-GR-23`
  - Excerpt: Keep AgentRuntime.invoke() unchanged and propagate node_id via ContextVar
  - Reasoning: Non-breaking runtime contract. HumanActorDef with identity/channel satisfies this because resolver mapping happens at the loader layer, not in the schema class.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:REQ-2`
  - Excerpt: ActorDefinition MUST use actor_type as its discriminator with only agent and human as valid values. HumanActorDef carries identity/channel semantics without embedding environment-specific credentials or reviving interaction as a serialized alias.
  - Reasoning: PRD is the canonical wire-shape authority per REQ-23. The actor union must exactly match this specification.

#### STEP-2: Implement the canonical execution-shape models: `NodeDefinition` (three atomic types with typed hook ports), `EdgeDefinition`, nested `PhaseDefinition`, closed-root `WorkflowConfig`, plugin/template interfaces, and JSON Schema export. This step makes every PRD-mandated constraint concrete: no `merge_function` on `BranchNode`, no `stores`/`plugin_instances` at workflow root, `EdgeDefinition` as the edge model name, and `hooks: dict[str, WorkflowOutputDefinition]` on nodes typed identically to output ports.
<!-- SF: declarative-schema | Original: STEP-2 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-compose/iriai_compose/schema/nodes.py` | create |
| `iriai-compose/iriai_compose/schema/edges.py` | create |
| `iriai-compose/iriai_compose/schema/phases.py` | create |
| `iriai-compose/iriai_compose/schema/workflow.py` | create |
| `iriai-compose/iriai_compose/schema/plugins.py` | create |
| `iriai-compose/iriai_compose/schema/templates.py` | create |
| `iriai-compose/iriai_compose/schema/json_schema.py` | create |
| `iriai-compose/iriai_compose/schema/__init__.py` | modify |
| `iriai-compose/iriai_compose/__init__.py` | modify |
| `.iriai/artifacts/features/beced7b1/plan-review-discussion-4.md` | read |
| `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md` | read |
| `.iriai/artifacts/features/beced7b1/broad/architecture.md` | read |
| `iriai-compose/iriai_compose/tasks.py` | read |
| `iriai-compose/iriai_compose/workflow.py` | read |

**Instructions:**

1. Create `iriai-compose/iriai_compose/schema/edges.py` with `EdgeDefinition(BaseModel)`: `source: str`, `target: str`, `transform_fn: str | None = None`, `description: str | None = None`. No `port_type`, no `hook_edges`, no `from_node`/`to_node`, no `edge_type`. Add small helpers `parse_port_ref(ref: str) -> tuple[str, str]` (splits `'phase.port'`) and `is_boundary_ref(ref: str) -> bool` (returns True for `'$input'` and `'$output'`). Class name is `EdgeDefinition`, not `Edge`.

2. Create `iriai-compose/iriai_compose/schema/nodes.py`:
   - `NodeBase(BaseModel)` with `id: str`, `inputs: dict[str, WorkflowInputDefinition]`, `outputs: dict[str, WorkflowOutputDefinition]`, `hooks: dict[str, WorkflowOutputDefinition]`, `artifact_key: str | None = None`, `cost: NodeCostConfig | None = None`, `context_keys: list[str]`. Hooks use `WorkflowOutputDefinition` — the same type as outputs — because hooks are typed output ports whose lifecycle behavior is inferred from port container membership, not from a special type annotation.
   - `AskNode(NodeBase)` with `type: Literal['ask']`, `actor: str` (actor name reference), `summary: str | None = None`, `task: str | None = None`, `context_text: str | None = None`. The layered prompt model (workflow context → actor role → task prompt → edge-delivered input) is preserved through `actor`, `task`, and `context_text`; see REQ-11.
   - `PluginNode(NodeBase)` with `type: Literal['plugin']`, `plugin_ref: str | None = None`, `instance_ref: str | None = None`, inline `config: dict | None = None`, and a `model_validator` enforcing XOR between `plugin_ref` and `instance_ref`.
   - `BranchNode(NodeBase)` with `type: Literal['branch']`, `condition_type: Literal['expression', 'output_field']`, `condition: str`, `paths: dict[str, WorkflowOutputDefinition]`. `paths` must have at least two keys. NO `merge_function` field. NO `switch_function` field. The path keys become the output port names used by downstream edges (`branch_id.approved`, `branch_id.rejected`, etc.). Leave `hooks` inherited from `NodeBase` so lifecycle edges work.
   - `NodeDefinition = Annotated[Union[AskNode, BranchNode, PluginNode], Field(discriminator='type')]` using Pydantic v2 discriminator syntax on the `type` field.

3. Create `iriai-compose/iriai_compose/schema/phases.py`:
   - `SequentialConfig(BaseModel)` — may be empty or carry optional sequencing metadata.
   - `MapConfig(BaseModel)` with `collection: str`, `max_parallelism: int | None = None`.
   - `FoldConfig(BaseModel)` with `collection: str`, `accumulator_init: dict | None = None`.
   - `LoopConfig(BaseModel)` with `condition: str`, `max_iterations: int | None = None`. Loop exit ports `condition_met` and `max_exceeded` are injected into phase `outputs` during model construction via a `model_validator(mode='after')` that adds them if not already present, using `type_ref='any'`.
   - `PhaseDefinition(BaseModel)` with `id: str`, `name: str`, `mode: Literal['sequential','map','fold','loop']`, `sequential_config: SequentialConfig | None`, `map_config: MapConfig | None`, `fold_config: FoldConfig | None`, `loop_config: LoopConfig | None`, `inputs: dict[str, WorkflowInputDefinition]`, `outputs: dict[str, WorkflowOutputDefinition]`, `hooks: dict[str, WorkflowOutputDefinition]`, `context_keys: list[str]`, `nodes: list[NodeDefinition]`, `children: list[PhaseDefinition]` (recursive, using `model_rebuild()`), `edges: list[EdgeDefinition]`, `metadata: dict | None = None`, `cost: PhaseCostConfig | None = None`. Field is `children`, never `phases`.

4. Create `iriai-compose/iriai_compose/schema/plugins.py` and `templates.py`:
   - `PluginInterface(BaseModel)` with `id: str`, `name: str`, `description: str | None`, `inputs: dict[str, WorkflowInputDefinition]`, `outputs: dict[str, WorkflowOutputDefinition]`, `config_schema: dict`.
   - `PluginInstanceConfig(BaseModel)` with `plugin_ref: str`, `config: dict`.
   - `TemplateDefinition(BaseModel)` with `id: str`, `name: str`, `description: str | None`, `phase: PhaseDefinition`, `bind: dict | None`. Templates expand into the same nested phase contract as inline phases.
   - `TemplateRef(BaseModel)` with `template_ref: str`, `bind: dict | None`.

5. Create `iriai-compose/iriai_compose/schema/workflow.py` with `WorkflowConfig` as the root model:
   ```
   model_config = ConfigDict(extra='forbid')  # closes root against unapproved fields
   schema_version: str
   workflow_version: int
   name: str
   description: str | None = None
   metadata: dict | None = None
   actors: dict[str, ActorDefinition]
   phases: list[PhaseDefinition]
   edges: list[EdgeDefinition]  # cross-phase wiring only
   templates: dict[str, TemplateDefinition] | None = None
   plugins: dict[str, PluginInterface] | None = None
   types: dict[str, TypeDefinition] | None = None
   cost_config: WorkflowCostConfig | None = None
   ```
   The fields `stores` and `plugin_instances` are NOT present. `extra='forbid'` causes Pydantic to reject them automatically; the validation layer in STEP-3 additionally emits the `unapproved_root_field` error code. Top-level nodes are also not present — nodes live inside `phases[*].nodes` only.

6. Create `iriai-compose/iriai_compose/schema/json_schema.py`:
   - `generate_json_schema() -> dict[str, Any]` returns `WorkflowConfig.model_json_schema()` directly.
   - Small CLI entry point: `if __name__ == '__main__': import sys, json; path = sys.argv[1]; open(path,'w').write(json.dumps(generate_json_schema(), indent=2))`.
   - Module docstring must state: 'For build/test use only. Composer consumers must fetch schema at runtime from GET /api/schema/workflow, which returns generate_json_schema() or WorkflowConfig.model_json_schema() directly. Do not bundle workflow-schema.json as a runtime static file.'

7. Update exports in `schema/__init__.py` and `iriai_compose/__init__.py` to add `EdgeDefinition`, `NodeDefinition`, `AskNode`, `BranchNode`, `PluginNode`, `PhaseDefinition`, `WorkflowConfig`, `PluginInterface`, `TemplateDefinition`, and `generate_json_schema`. Reserve `load_workflow`, `dump_workflow`, `validate_workflow` for STEP-3.

**Acceptance Criteria:**

- Instantiate `EdgeDefinition(source='review.on_end', target='notify.input')`; the model validates and exposes no `port_type`, `hook_edges`, `from_node`, or `to_node` fields. Confirm class name is `EdgeDefinition` not `Edge`.
- Instantiate `BranchNode(type='branch', id='gate', condition_type='output_field', condition='approved', paths={'approved': {'type_ref': 'bool'}, 'rejected': {'type_ref': 'bool'}})`; the model validates with `paths` as the only routable output surface.
- Attempt `BranchNode(type='branch', id='gate', condition_type='expression', condition='x>0', paths={'a': {'type_ref': 'bool'}}, merge_function='sum')`; Pydantic raises because `merge_function` is not a field on `BranchNode`.
- Attempt `BranchNode(type='branch', id='gate', condition_type='expression', condition='x>0', paths={'a': {'type_ref': 'bool'}}, switch_function='fn')`; Pydantic raises because `switch_function` is not a field on `BranchNode`.
- Instantiate a `PhaseDefinition` with a nested child phase under `children`; the model validates and the child is available at `phase.children[0]`. Confirm `PhaseDefinition.model_json_schema()` has `children` but no `phases` property.
- Instantiate `AskNode(type='ask', id='ask1', actor='pm', inputs={'input': {'type_ref': 'string'}}, outputs={'output': {'type_ref': 'string'}}, hooks={'on_start': {'type_ref': 'any'}, 'on_end': {'type_ref': 'any'}})`; the `hooks` field is `dict[str, WorkflowOutputDefinition]`, same type as `outputs`.
- Instantiate `WorkflowConfig(schema_version='1.0', workflow_version=1, name='wf', actors={}, phases=[], edges=[])`; it validates. Add `stores={}` to the kwargs; Pydantic raises an extra-fields error because `model_config = ConfigDict(extra='forbid')`.
- Run `python -c "from iriai_compose.schema.json_schema import generate_json_schema; import json; json.dumps(generate_json_schema())"`; JSON serialization succeeds. Confirm the generated schema has `BranchNode.condition_type`, `BranchNode.condition`, `BranchNode.paths` and no `merge_function`, `switch_function`, `port_type`, or `hook_edges`.
- Run `python -m iriai_compose.schema.json_schema /tmp/workflow-schema.json`; the file is written for build/test use but the same dict can be returned from a backend route without modification.

**Counterexamples:**

- Do NOT add `merge_function` to `BranchNode`. It is not in the PRD and must be rejected at both the Pydantic model and the validation layer.
- Do NOT add root-level `stores` or `plugin_instances` to `WorkflowConfig`. Both fields are explicitly excluded by the PRD's closed root set.
- Do NOT name the edge model `Edge`. The PRD canonical name is `EdgeDefinition`.
- Do NOT add `port_type`, `hook_edges`, or any separate hook-routing section to the serialized schema.
- Do NOT add a `hooks` field typed differently from `outputs` — hook ports are `dict[str, WorkflowOutputDefinition]` because they follow the same typed-port contract.
- Do NOT store nested phases under `PhaseDefinition.phases`; the canonical recursive field is `children`.
- Do NOT put nodes directly on `WorkflowConfig`; only `phases[*].nodes` owns nodes.
- Do NOT treat a checked-in `workflow-schema.json` file as the canonical runtime source for the composer.

**Requirement IDs:** REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8, REQ-10, REQ-14, REQ-15, REQ-16, REQ-22, REQ-23

**Journey IDs:** J-1, J-2, J-4, J-5

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: YAML stays nested under phases[].nodes and phases[].children, hook behavior is serialized only through ordinary edges, and /api/schema/workflow is the canonical schema delivery path.
  - Reasoning: Cycle 4 locked the nested-phase, edge-only hook, and runtime-schema-delivery contract.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:REQ-1`
  - Excerpt: WorkflowConfig MUST remain YAML-first and include only schema_version, workflow_version, name, description, metadata, actors, phases, edges, templates, plugins, types, and cost_config at the root. Unapproved root additions such as stores and plugin_instances are invalid.
  - Reasoning: The PRD defines the exact closed root field set. extra='forbid' on WorkflowConfig enforces this at the Pydantic level.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:REQ-3`
  - Excerpt: BranchNode uses only condition_type + condition + typed paths for exclusive routing. switch_function, merge_function, and alternate branch-routing surfaces are not part of the declarative contract.
  - Reasoning: PRD explicitly calls out merge_function as a rejected field alongside switch_function.
- **[research]** `Pydantic v2 docs — Field(discriminator=...) for Annotated union`
  - Excerpt: Discriminated unions use a common field to select the model variant.
  - Reasoning: NodeDefinition and ActorDefinition both use this pattern for type-based dispatch.

#### STEP-3: Add recursive validation and YAML I/O that enforce the canonical PRD wire shape all the way through load, dump, and lint paths. This step carries the explicit rejection surface for every stale variant the PRD calls out: `merge_function` on branches, `type: interaction` actor aliases, `stores`/`plugin_instances` at root, `phase.phases`, serialized `port_type`, `hook_edges`, and `switch_function` cannot silently survive a load/validate cycle.
<!-- SF: declarative-schema | Original: STEP-3 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-compose/iriai_compose/schema/validation.py` | create |
| `iriai-compose/iriai_compose/schema/yaml_io.py` | create |
| `iriai-compose/iriai_compose/schema/__init__.py` | modify |
| `iriai-compose/iriai_compose/__init__.py` | modify |
| `iriai-compose/iriai_compose/schema/edges.py` | modify |
| `iriai-compose/iriai_compose/schema/phases.py` | read |
| `iriai-compose/iriai_compose/schema/workflow.py` | read |
| `iriai-compose/iriai_compose/schema/nodes.py` | read |
| `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md` | read |
| `.iriai/artifacts/features/beced7b1/plan-review-discussion-4.md` | read |

**Instructions:**

1. Create `iriai-compose/iriai_compose/schema/validation.py` with a non-raising validation surface:
   - `ValidationError` dataclass with `code: str`, `field_path: str`, `message: str`, `severity: Literal['error','warning']`, and optional `context: dict`.
   - `validate_workflow()`, `validate_type_flow()`, and `detect_cycles()` all return `list[ValidationError]` and never raise for ordinary schema defects.
   - Central recursive walkers must traverse `WorkflowConfig.phases[*]` and `PhaseDefinition.children[*]`. All walkers use a single shared helper `_walk_phases(phases: list[PhaseDefinition]) -> Generator[PhaseDefinition, None, None]` that yields all phases recursively via `children`. No walker may reference `phase.phases`.

2. Implement and document the 29 structural error codes used by this revision:
   - Existing 26: `dangling_edge`, `duplicate_node_id`, `duplicate_phase_id`, `invalid_actor_ref`, `invalid_phase_mode_config`, `invalid_hook_edge_transform`, `phase_boundary_violation`, `cycle_detected`, `unreachable_node`, `type_mismatch`, `invalid_branch_paths`, `invalid_branch_condition_type`, `unsupported_switch_function`, `invalid_branch_routing_surface`, `invalid_io_config`, `invalid_type_ref`, `invalid_store_ref`, `invalid_store_key_ref`, `store_type_mismatch`, `invalid_workflow_io_ref`, `port_type_mutual_exclusion`, `invalid_schema_def`, `legacy_port_type_field`, `legacy_hook_edges_field`, `invalid_nested_phase_field`, `expression_limit_exceeded`.
   - New code 27: `unsupported_merge_function` — emitted when a branch dict contains `merge_function`. Message: 'BranchNode does not support merge_function. Use downstream phase-level edges for result merging.'.
   - New code 28: `invalid_actor_type_value` — emitted when actor data uses `type: interaction` instead of `actor_type: agent|human`, or when `actor_type` has an unrecognized value. Message includes: 'Use actor_type field with values agent or human. The value interaction is not a valid actor_type.'.
   - New code 29: `unapproved_root_field` — emitted when a raw workflow dict contains `stores`, `plugin_instances`, or any other unapproved root key not in the PRD-canonical root field set. Message must name the rejected key: 'Root field {key!r} is not in the approved WorkflowConfig schema. Unapproved root registries are not permitted.'.

3. `build_port_index()` must recursively index node input/output/hook ports plus `BranchNode.paths` under a distinct `container='paths'` classification. `is_hook_source()` must determine hook semantics from indexed container membership (container is `'hooks'`), never from a serialized field.

4. Stale-surface rejection rules:
   - `merge_function` key in branch dict → `unsupported_merge_function`.
   - `switch_function` key in branch dict → `unsupported_switch_function`.
   - `port_type` key in any edge dict → `legacy_port_type_field`.
   - `hook_edges` key anywhere → `legacy_hook_edges_field`.
   - `phases:` key inside a phase dict → `invalid_nested_phase_field` with guidance to rename to `children`.
   - `stores:` or `plugin_instances:` at workflow root dict → `unapproved_root_field`.
   - Actor data with `type: interaction` or unknown `actor_type` value → `invalid_actor_type_value`.
   - Hook edge with `transform_fn` set → `invalid_hook_edge_transform`.

5. Create `iriai-compose/iriai_compose/schema/yaml_io.py`:
   - `load_workflow(source: str | Path) -> WorkflowConfig` uses `yaml.safe_load`, then checks for unapproved root keys before calling `WorkflowConfig.model_validate()`, raising a structured error for any pre-validation rejection.
   - `load_workflow_lenient(source: str | Path) -> tuple[WorkflowConfig | None, list[ValidationError]]` returns `(None, errors)` for structurally stale or invalid YAML instead of raising.
   - `dump_workflow(config: WorkflowConfig) -> str` uses `yaml.safe_dump` and must emit `children:` for nested phases, one ordinary `edges:` list, and never emit `port_type:` or `hook_edges:`.
   - YAML shorthand: a bare string in any dict-keyed port map (`inputs`, `outputs`, `hooks`, `paths`) is normalized to `{type_ref: <string>}` before model validation. This applies recursively through `phases[*]` and `children[*]`.

6. Update both `__init__` files to export: `load_workflow`, `load_workflow_lenient`, `dump_workflow`, `validate_workflow`, `validate_type_flow`, `detect_cycles`.

7. Expression-length checks apply only when `condition_type='expression'`. `output_field` is a declarative path lookup and skips all sandbox and size checks.

**Acceptance Criteria:**

- Load a YAML workflow containing `phases[0].children[0]`; `load_workflow()` returns a `WorkflowConfig` whose first phase contains a non-empty `children` list.
- Run `dump_workflow(load_workflow(path))` on a nested fixture; the output YAML contains `children:`, `edges:`, no `port_type:`, and no `hook_edges:` keys anywhere.
- Call `load_workflow_lenient()` on YAML containing `merge_function` on a branch; the result is `(None, [ValidationError(code='unsupported_merge_function', ...)])`.
- Call `load_workflow_lenient()` on YAML containing `type: interaction` on an actor; the result is `(None, [ValidationError(code='invalid_actor_type_value', ...)])` with a message directing toward `actor_type: agent|human`.
- Call `load_workflow_lenient()` on YAML containing `stores: {}` at workflow root; result includes `ValidationError(code='unapproved_root_field', ...)` naming the key `stores`.
- Call `load_workflow_lenient()` on YAML containing `plugin_instances: {}` at workflow root; result includes `ValidationError(code='unapproved_root_field', ...)` naming `plugin_instances`.
- Call `load_workflow_lenient()` on YAML with a nested `phases:` key inside a phase; result includes `ValidationError(code='invalid_nested_phase_field', ...)` with guidance to use `children`.
- Validate YAML with a hook edge that also sets `transform_fn`; `validate_workflow()` returns `invalid_hook_edge_transform`.
- Validate YAML with `switch_function` on a branch; `validate_workflow()` returns `unsupported_switch_function`.
- Validate a correct nested fixture with hook edges, Branch paths, and loop exits; `validate_workflow()` returns `[]`.

**Counterexamples:**

- Do NOT use `yaml.load()` or any unsafe loader.
- Do NOT walk `phase.phases`; all recursive traversal must use `children` via `_walk_phases()`.
- Do NOT infer hook semantics from serialized `port_type`; hook detection is index-based and source-port-driven.
- Do NOT silently coerce stale `merge_function`, `hook_edges`, or `phase.phases` fields without emitting migration errors.
- Do NOT apply expression sandbox checks to `condition_type='output_field'` values.
- Do NOT accept `type: interaction` as a backward-compatible actor alias — emit `invalid_actor_type_value` and block.

**Requirement IDs:** REQ-4, REQ-15, REQ-16, REQ-17, REQ-21, REQ-22, REQ-23

**Journey IDs:** J-1, J-2, J-3, J-5

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: YAML stays nested, hook behavior serialized only through ordinary edges, /api/schema/workflow is the canonical delivery path.
  - Reasoning: This step enforces D-GR-22 at load and validation time, blocking any stale variant from surviving a load cycle.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:REQ-17`
  - Excerpt: Validation MUST reject stale contract variants including … unapproved root additions such as stores or plugin_instances, and rejected branch fields such as switch_function or merge_function.
  - Reasoning: PRD explicitly mandates rejection of merge_function and root registries alongside the older stale fields.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:AC-25`
  - Excerpt: Developer adds switch_function or merge_function to a branch definition — Validation fails with an error directing the author back to condition_type, condition, and paths.
  - Reasoning: AC-25 is the acceptance criterion for the unsupported_merge_function rejection code.
- **[research]** `PyYAML docs — yaml.safe_load / yaml.safe_dump`
  - Excerpt: Use safe_load for parsing untrusted YAML content safely.
  - Reasoning: Schema module consumes arbitrary workflow YAML from developers; safe loader is mandatory.

#### STEP-4: Prove the revised PRD-canonical contract with schema-focused tests and fixtures that exercise every mandatory wire-shape rule: `actor_type: agent|human`, no `merge_function`, closed WorkflowConfig root, nested phase `children`, edge-only hook serialization, Branch path routing, and the iriai-build-v2 litmus patterns. These tests are the regression safety net against future drift back toward stale actor aliases, `merge_function`, flat phase trees, or runtime static-schema loading.
<!-- SF: declarative-schema | Original: STEP-4 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-compose/tests/schema/test_models.py` | create |
| `iriai-compose/tests/schema/test_validation.py` | create |
| `iriai-compose/tests/schema/test_yaml_io.py` | create |
| `iriai-compose/tests/schema/test_json_schema.py` | create |
| `iriai-compose/tests/fixtures/schema/minimal_workflow.yaml` | create |
| `iriai-compose/tests/fixtures/schema/nested_children.yaml` | create |
| `iriai-compose/tests/fixtures/schema/hook_edges.yaml` | create |
| `iriai-compose/tests/fixtures/schema/branch_paths.yaml` | create |
| `iriai-compose/tests/fixtures/schema/loop_exits.yaml` | create |
| `iriai-compose/tests/fixtures/schema/pm_fold_map_loop.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_switch_function.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_merge_function.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_actor_interaction.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_root_stores.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_phase_phases_field.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_port_type.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_hook_edges_section.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_hook_transform.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_branch_unknown_path.yaml` | create |
| `iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py` | read |
| `iriai-compose/iriai_compose/runner.py` | read |

**Instructions:**

1. Add model tests in `iriai-compose/tests/schema/test_models.py` covering:
   - Dict-keyed port maps and XOR rules (both None fails, both set fails, one set passes).
   - `ActorDefinition` discriminated union: `actor_type='agent'` routes to `AgentActorDef`, `actor_type='human'` routes to `HumanActorDef`. Verify `HumanActorDef` has `identity` and `channel` fields, not `resolver`. Verify `actor_type='interaction'` fails discriminator resolution.
   - `BranchNode` has `condition_type`/`condition`/`paths`; instantiating with `merge_function=...` raises Pydantic validation error; instantiating with `switch_function=...` raises Pydantic validation error.
   - `PhaseDefinition.children` recursion; no `phases` field on `PhaseDefinition.model_json_schema()`.
   - `WorkflowConfig` with `extra='forbid'`: adding `stores={}` or `plugin_instances={}` raises Pydantic extra-field error.
   - `EdgeDefinition` class name and absence of `port_type`, `from_node`, `to_node`.
   - `generate_json_schema()` / `WorkflowConfig.model_json_schema()` parity.
   - `NodeBase.hooks` field is typed as `dict[str, WorkflowOutputDefinition]` (same type as `outputs`).

2. Add validation tests in `iriai-compose/tests/schema/test_validation.py` covering every D-GR-22-plus-PRD-sensitive failure mode, asserting on the exact `ValidationError.code`:
   - `unsupported_merge_function` from branch with `merge_function`.
   - `unsupported_switch_function` from branch with `switch_function`.
   - `invalid_actor_type_value` from actor with `type: interaction`.
   - `unapproved_root_field` from workflow root with `stores`.
   - `unapproved_root_field` from workflow root with `plugin_instances`.
   - `invalid_nested_phase_field` from phase with `phases:` key.
   - `legacy_port_type_field` from serialized `port_type`.
   - `legacy_hook_edges_field` from `hook_edges` key.
   - `invalid_hook_edge_transform` from hook edge with `transform_fn`.

3. Add YAML round-trip tests in `iriai-compose/tests/schema/test_yaml_io.py`:
   - Valid nested fixtures must round-trip with `children:` intact.
   - Hook edges round-trip through ordinary `edges:` only; no `port_type:` appears in serialized output.
   - Actor definitions round-trip with `actor_type: agent` and `actor_type: human`; no `type: interaction` appears.
   - Lenient loads return migration errors for all stale negative fixtures without throwing.

4. Add JSON Schema tests in `iriai-compose/tests/schema/test_json_schema.py`:
   - `children` exists on phase shape; `phases` property absent from phase shape.
   - `actor_type` is the discriminator; `agent` and `human` are the only variants; `interaction` is absent.
   - `merge_function`, `switch_function`, `port_type`, `hook_edges` absent from all node and edge shapes.
   - `stores`, `plugin_instances` absent from WorkflowConfig root shape.
   - Branch inspector fields (`condition_type`, `condition`, `paths`) present.
   - Schema dict is JSON-serializable and suitable for direct backend route response.

5. Add fixtures that reflect litmus-test patterns from `iriai-build-v2`:
   - `pm_fold_map_loop.yaml` — nested fold + map + loop phases using `actors` with `actor_type: agent` and `actor_type: human`, representing planning/develop patterns. Must have no `merge_function` and no `stores` at root.
   - `loop_exits.yaml` — loop phase with `condition_met` and `max_exceeded` exits wired via ordinary `edges:`.
   - `hook_edges.yaml` — lifecycle edges from `on_start`/`on_end` ports using ordinary edge contract.
   - `branch_paths.yaml` — `BranchNode` with `condition_type: expression` and typed `paths`, no `merge_function`.
   - `minimal_workflow.yaml` — smallest valid `WorkflowConfig` using the closed root field set.

6. Add negative fixtures that are intentionally stale:
   - `invalid_merge_function.yaml` — branch with `merge_function`; expects `unsupported_merge_function`.
   - `invalid_actor_interaction.yaml` — actor with `type: interaction`; expects `invalid_actor_type_value`.
   - `invalid_root_stores.yaml` — workflow root with `stores: {}`; expects `unapproved_root_field`.
   - `invalid_switch_function.yaml` — expects `unsupported_switch_function`.
   - `invalid_phase_phases_field.yaml` — expects `invalid_nested_phase_field`.
   - `invalid_port_type.yaml` — expects `legacy_port_type_field`.
   - `invalid_hook_edges_section.yaml` — expects `legacy_hook_edges_field`.
   - `invalid_hook_transform.yaml` — expects `invalid_hook_edge_transform`.
   - `invalid_branch_unknown_path.yaml` — expects path-reference error.

**Acceptance Criteria:**

- Run `pytest iriai-compose/tests/schema -q`; all schema tests pass.
- Confirm `test_models.py` contains an explicit test verifying `HumanActorDef` fields are `identity` and `channel` (not `resolver`), and that `ActorDefinition.model_validate({'actor_type': 'interaction'})` raises.
- Confirm `test_models.py` contains an explicit test verifying `BranchNode(... merge_function='x')` raises Pydantic validation error.
- Confirm `test_models.py` contains an explicit test verifying `WorkflowConfig(... stores={})` raises Pydantic extra-fields error.
- Open `pm_fold_map_loop.yaml`, load it, dump it, reload it; actor definitions use `actor_type:` (not `type:`), no `merge_function` appears anywhere, no `stores:` at root.
- Run the validation test suite against all negative fixtures; each returns exactly the expected error code with no exception thrown.
- Inspect the generated JSON Schema in `test_json_schema.py`; it contains `BranchNode.condition_type`, `BranchNode.condition`, `BranchNode.paths` while omitting `merge_function`, `switch_function`, `port_type`, `hook_edges`, `stores`, and `plugin_instances`.
- Load `pm_fold_map_loop.yaml` and validate it successfully, demonstrating that fold/map/loop nested phase composition, `actor_type: agent|human` actors, and no-merge-function branches coexist in a valid document.

**Counterexamples:**

- Do NOT write valid fixtures with `type: interaction` on actors — use `actor_type: human` instead.
- Do NOT write valid fixtures with `merge_function` on branch nodes; `merge_function` belongs only in `invalid_merge_function.yaml`.
- Do NOT add `stores:` or `plugin_instances:` to any valid fixture's workflow root.
- Do NOT write fixtures with nested `phases:` under a phase except in `invalid_phase_phases_field.yaml`.
- Do NOT add any test that treats `workflow-schema.json` as a runtime fetch source for the editor.
- Do NOT encode hook edges with `port_type` or a separate `hook_edges` section in valid fixtures.
- Do NOT flatten nested phase fixtures into top-level workflow nodes.

**Requirement IDs:** REQ-3, REQ-15, REQ-16, REQ-17, REQ-18, REQ-19, REQ-22, REQ-23

**Journey IDs:** J-1, J-2, J-3, J-4, J-5

**Citations:**

- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:AC-13`
  - Excerpt: Developer defines both an agent actor and a human actor using actor_type — Both validate without requiring environment-specific runtime secrets. The schema accepts type: interaction as a wire alias — NOT criteria.
  - Reasoning: AC-13 is the litmus test that makes type='interaction' a regression target in test_models.py.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:AC-25`
  - Excerpt: Developer adds switch_function or merge_function to a branch definition — Validation fails with an error directing the author back to condition_type, condition, and paths.
  - Reasoning: AC-25 drives both invalid_switch_function.yaml and invalid_merge_function.yaml negative fixtures.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md:AC-27`
  - Excerpt: Developer adds stores or plugin_instances to the workflow root and validates — Validation fails with an unsupported-root-field error naming the rejected key.
  - Reasoning: AC-27 drives invalid_root_stores.yaml and the unapproved_root_field test in test_validation.py.
- **[code]** `iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py:58-103`
  - Excerpt: Gate-and-revise helpers with approval branches
  - Reasoning: pm_fold_map_loop.yaml must represent these gate-branch patterns without merge_function or switch_function.
- **[code]** `iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py:25-39`
  - Excerpt: Bounded retry loops and parallel analysis
  - Reasoning: loop_exits.yaml and nested-children patterns must cover these retry structures via phase modes, not compound node types.

### Journey Verifications

**Journey J-1:**

- Step 1:
  - [api] `load_workflow('tests/fixtures/schema/nested_children.yaml')` returns a `WorkflowConfig` whose first phase contains both `nodes` and a non-empty `children` list. Actors in the document use `actor_type: agent` or `actor_type: human`.
- Step 2:
  - [api] `validate_workflow(config)` on the nested fixture returns `[]`, confirming nested containment, typed ports, ordinary edge wiring, and actor_type discriminator coexist in one valid document.
- Step 3:
  - [api] `dump_workflow(config)` emits YAML with `children:`, `actor_type:`, and `edges:`, while containing no `port_type:`, `hook_edges:`, `merge_function:`, `stores:`, `plugin_instances:`, or `type: interaction` anywhere.

**Journey J-2:**

- Step 1:
  - [api] `load_workflow('tests/fixtures/schema/pm_fold_map_loop.yaml')` returns nested phases representing fold, map, and loop structure through `children`. Actors use `actor_type: agent` and `actor_type: human`.
- Step 2:
  - [api] `validate_workflow(config)` on `pm_fold_map_loop.yaml` returns `[]`. Branch nodes expose `condition_type`, `condition`, and `paths` with no `switch_function` or `merge_function`.
- Step 3:
  - [api] Round-tripping `pm_fold_map_loop.yaml` preserves nested `children`, single-list `edges:` for hooks, `actor_type:` discriminator, and no stale fields.

**Journey J-3:**

- Step 1:
  - [api] `load_workflow_lenient('tests/fixtures/schema/invalid_merge_function.yaml')` returns `(None, [ValidationError(code='unsupported_merge_function', ...)])` with a message directing to `condition_type`, `condition`, and `paths`.
- Step 2:
  - [api] `load_workflow_lenient('tests/fixtures/schema/invalid_actor_interaction.yaml')` returns `(None, [ValidationError(code='invalid_actor_type_value', ...)])` with a message stating valid values are `agent` and `human` only.
- Step 3:
  - [api] `load_workflow_lenient('tests/fixtures/schema/invalid_root_stores.yaml')` returns `ValidationError(code='unapproved_root_field', ...)` naming `stores` as the rejected key.
- Step 4:
  - [api] `load_workflow_lenient('tests/fixtures/schema/invalid_phase_phases_field.yaml')` returns `ValidationError(code='invalid_nested_phase_field', ...)` with guidance to rename the field to `children`.
- Step 5:
  - [api] `validate_workflow(load_workflow('tests/fixtures/schema/invalid_hook_transform.yaml'))` returns `ValidationError(code='invalid_hook_edge_transform', ...)`.

**Journey J-4:**

- Step 1:
  - [api] `generate_json_schema()` and `WorkflowConfig.model_json_schema()` return equivalent JSON-serializable dicts. The dict is the same one a backend route returns from `GET /api/schema/workflow`.
- Step 2:
  - [api] The generated JSON Schema exposes `PhaseDefinition.children`, `BranchNode.condition_type`, `BranchNode.condition`, `BranchNode.paths`, and `actor_type: agent|human` discriminator while omitting `merge_function`, `switch_function`, `port_type`, `hook_edges`, `stores`, `plugin_instances`, and `type: interaction`.

**Journey J-5:**

- Step 1:
  - [api] `load_workflow('tests/fixtures/schema/loop_exits.yaml')` yields a loop phase whose `outputs` include both `condition_met` and `max_exceeded`.
- Step 2:
  - [api] `validate_workflow(config)` on `loop_exits.yaml` returns `[]`. Outgoing edges from `review_loop.condition_met` and `review_loop.max_exceeded` resolve through ordinary `EdgeDefinition` dot-notation.

### Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-1 | A recursive walker that still traverses `phase.phases` instead of `phase.children` will silently drop nested phase content during validation or serialization. Any future implementer that copies the old pattern bypasses all nesting tests. | high | Use one shared `_walk_phases()` helper in `validation.py` and lock it with dedicated nested-phase round-trip fixtures and tests asserting `children` key presence. | STEP-2, STEP-3, STEP-4 |
| RISK-2 | Branch routing can drift if implementers treat generic `NodeBase.outputs` and `BranchNode.paths` as co-equal routing surfaces after the schema is deployed. | high | Keep `paths` as the only routable Branch output surface, reject `switch_function` and `merge_function` at both Pydantic model and validation layers, index `paths` explicitly in `build_port_index()`, and lock with `invalid_merge_function.yaml` and `invalid_switch_function.yaml` regression fixtures. | STEP-2, STEP-3, STEP-4 |
| RISK-3 | Downstream composer work may regress to fetching or vendoring a static `workflow-schema.json` file at runtime instead of serving live schema through `/api/schema/workflow`. | medium | Keep CLI/file generation explicitly marked build-test-only in `json_schema.py` module docstring, verify the generated dict is directly suitable for backend route responses without post-processing, and add a test asserting `generate_json_schema()` and `WorkflowConfig.model_json_schema()` produce the same dict. | STEP-2, STEP-4 |
| RISK-4 | The SF-2 loader must correctly map `HumanActorDef(actor_type='human', identity=..., channel=...)` to `InteractionActor(name=..., resolver=...)` at the loader boundary. If the loader treats `human` as an unknown type or maps `channel` to the wrong resolver key, interactive actors will fail to dispatch through `DefaultWorkflowRunner._resolve_interaction_runtime()`. | medium | Document the mapping contract clearly in `actors.py` module docstring: 'The loader (SF-2) maps HumanActorDef.channel to InteractionActor.resolver using the same prefix-match routing already in DefaultWorkflowRunner._resolve_interaction_runtime()'. STEP-3 and STEP-4 surface unknown actor types as `invalid_actor_type_value` so integration failures are not silent. | STEP-1, STEP-3 |
| RISK-5 | Context7 was unavailable for API doc lookup in this environment, so official upstream docs were used directly for Pydantic v2 discriminated union syntax and PyYAML safe_load APIs. | low | Keep implementation limited to the officially documented `Field(discriminator='actor_type')` Pydantic v2 pattern and `yaml.safe_load`/`yaml.safe_dump`, and back both with smoke tests that fail on signature drift. | STEP-1, STEP-2, STEP-3 |

### File Manifest

| Path | Action |
|------|--------|
| `iriai-compose/iriai_compose/schema/__init__.py` | create |
| `iriai-compose/iriai_compose/schema/base.py` | create |
| `iriai-compose/iriai_compose/schema/actors.py` | create |
| `iriai-compose/iriai_compose/schema/types.py` | create |
| `iriai-compose/iriai_compose/schema/cost.py` | create |
| `iriai-compose/iriai_compose/schema/nodes.py` | create |
| `iriai-compose/iriai_compose/schema/edges.py` | create |
| `iriai-compose/iriai_compose/schema/phases.py` | create |
| `iriai-compose/iriai_compose/schema/workflow.py` | create |
| `iriai-compose/iriai_compose/schema/plugins.py` | create |
| `iriai-compose/iriai_compose/schema/templates.py` | create |
| `iriai-compose/iriai_compose/schema/validation.py` | create |
| `iriai-compose/iriai_compose/schema/yaml_io.py` | create |
| `iriai-compose/iriai_compose/schema/json_schema.py` | create |
| `iriai-compose/iriai_compose/__init__.py` | modify |
| `iriai-compose/pyproject.toml` | modify |
| `iriai-compose/tests/schema/test_models.py` | create |
| `iriai-compose/tests/schema/test_validation.py` | create |
| `iriai-compose/tests/schema/test_yaml_io.py` | create |
| `iriai-compose/tests/schema/test_json_schema.py` | create |
| `iriai-compose/tests/fixtures/schema/minimal_workflow.yaml` | create |
| `iriai-compose/tests/fixtures/schema/nested_children.yaml` | create |
| `iriai-compose/tests/fixtures/schema/hook_edges.yaml` | create |
| `iriai-compose/tests/fixtures/schema/branch_paths.yaml` | create |
| `iriai-compose/tests/fixtures/schema/loop_exits.yaml` | create |
| `iriai-compose/tests/fixtures/schema/pm_fold_map_loop.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_switch_function.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_merge_function.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_actor_interaction.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_root_stores.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_phase_phases_field.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_port_type.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_hook_edges_section.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_hook_transform.yaml` | create |
| `iriai-compose/tests/fixtures/schema/invalid_branch_unknown_path.yaml` | create |
| `iriai-compose/iriai_compose/actors.py` | read |
| `iriai-compose/iriai_compose/runner.py` | read |
| `iriai-compose/iriai_compose/tasks.py` | read |
| `iriai-compose/iriai_compose/workflow.py` | read |
| `.iriai/artifacts/features/beced7b1/plan-review-discussion-4.md` | read |
| `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md` | read |
| `.iriai/artifacts/features/beced7b1/broad/architecture.md` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py` | read |

---

## SF-2: DAG Loader & Runner
<!-- SF: dag-loader-runner -->


### SF-2: DAG Loader & Runner

<!-- SF: dag-loader-runner -->



## Architecture Overview

SF-2 adds a `iriai_compose/declarative/` subpackage that provides a YAML-first workflow execution path alongside the existing imperative Python API. A standalone `run()` function loads a workflow YAML (delegating to SF-1's `load_workflow()`), validates it against SF-1 schema models, builds a recursive DAG (workflow-level for phases, phase-level for nodes and sub-phases), and executes against provided `AgentRuntime` instances via the existing `DefaultWorkflowRunner`.

### Module Layout

```
iriai_compose/
├── declarative/
│   ├── __init__.py       # Public API: run, load_workflow, RuntimeConfig, PluginRegistry
│   ├── loader.py         # Thin wrapper: imports SF-1's load_workflow + runtime-specific validation
│   ├── runner.py         # Top-level run() + workflow-level DAG orchestration
│   ├── graph.py          # DAG construction, topological sort, reachability
│   ├── executors.py      # Node executors: ask, branch, plugin
│   ├── modes.py          # Phase mode strategies: sequential, map, fold, loop
│   ├── transforms.py     # Inline transform/expression eval via exec()
│   ├── plugins.py        # Plugin ABC, PluginRegistry (concrete + type/instance), CategoryExecutor, entry-point discovery
│   ├── config.py         # RuntimeConfig dataclass + YAML loader
│   ├── actors.py         # ActorDefinition hydration to AgentActor/InteractionActor
│   ├── hooks.py          # Hook edge execution (fire-and-forget)
│   └── errors.py         # DeclarativeExecutionError, WorkflowLoadError, etc.
├── plugins/              # Built-in plugin implementations
│   ├── __init__.py       # Auto-registration of built-in plugins
│   └── artifact_write.py # ArtifactWritePlugin — writes data to ArtifactStore at a specified key
```

### Uniform DAG Execution (Single Engine, All Levels)

A single `ExecutionGraph` type and a single set of functions — `_gather_inputs`, `_activate_outgoing_edges`, `_collect_output`, `_execute_dag` — operate at **every level** of the hierarchy. There is no `WorkflowGraph` type, no `_gather_workflow_phase_input`, no separate workflow-level execution loop. The same code that executes nodes within a phase also executes phases within a workflow.

This works because all elements at every level share the same port model (SF-1 D-SF1-10/D-SF1-11):

```
Workflow-Level DAG (elements = phases):
    [$input] ──→ [phase_A] ──→ [phase_B] ──→ [phase_C] ──→ [$output]

Phase-Level DAG (elements = nodes + sub-phases):
    [$input] ──→ [ask_node] ──→ [plugin_node] ──→ [$output]
```

**Element type discrimination:** Elements are distinguished by attribute presence, NOT by a shared discriminator:
- **Phases**: have `mode` field, never `type`. Dispatched via `execute_phase()`.
- **Nodes**: have `type` field (`"ask"` | `"branch"` | `"plugin"`), never `mode`. Dispatched via node executors.
- At **workflow level**, all elements are phases (all have `mode`). At **phase level**, elements can be either nodes or sub-phases.

**No-edges fallback:** When `edges` is empty, `run()` and `execute_phase` implement a sequential fallback OUTSIDE `_execute_dag` — threading each element's output as the next element's `phase_input`. This matches the imperative API's behavior.

**Future divergence guard:** If workflow-level execution ever needs behavior that doesn't apply to phase-level, implement as pre/post hooks around `_execute_dag`, NOT as a parallel engine.

### Public API

```python
from iriai_compose.declarative import (
    run,                    # async def run(workflow, config: RuntimeConfig, *, inputs=None) -> ExecutionResult
    load_workflow,          # Re-exported from SF-1's iriai_compose.schema.yaml_io
    RuntimeConfig,          # Dataclass: all runtime wiring
    load_runtime_config,    # def load_runtime_config(path, registry) -> RuntimeConfig
    PluginRegistry,         # Plugin name→implementation registry (concrete + type/instance)
    ExecutionResult,        # Dataclass: execution outcome
    required_plugins,       # def required_plugins(workflow) -> list[PluginRequirement]
)
```

---

## Schema Entity Reference (SF-1 Authoritative, SF-2 Additions Marked)

This section is the canonical reference for every entity type the runner operates on. All types are defined by SF-1 (`iriai_compose/schema/`) unless marked **[SF-2 addition]**. The runner MUST NOT assume any fields beyond what is listed here.

### Node Type Hierarchy

```
NodeBase (abstract)
├── AskNode    (type="ask")    — agent invocation, 1 fixed input, 1+ outputs (mutually exclusive)
├── BranchNode (type="branch") — gather/dispatch, 1+ inputs, 1+ outputs (exclusive via switch_function OR non-exclusive via per-port conditions)
└── PluginNode (type="plugin") — side effects, 1 fixed input, 0+ outputs (fire-and-forget ok)

NodeDefinition = Annotated[AskNode | BranchNode | PluginNode, Field(discriminator="type")]
```

### NodeBase [D-SF1-11, D-SF1-22]

Abstract base. All three node types inherit these fields.

| Field | Type | Default | Runner Use |
|-------|------|---------|-----------|
| `id` | `str` | required | Element ID in `ExecutionGraph.elements` |
| `type` | `Literal["ask","branch","plugin"]` | required | Discriminator for dispatch |
| `summary` | `str \| None` | `None` | UI only |
| `context_keys` | `list[str]` | `[]` | Resolved by ContextProvider, prepended to prompt context |
| `context_text` | `dict[str, str]` | `{}` | Inline text merged into context |
| `artifact_key` | `str \| None` | `None` | **Dual read+write**: (1) Before execution, READS the existing value from the store and injects into context. (2) After execution, AUTO-WRITES the node's output to the store at this key. [D-SF1-29] |
| `input_type` | `str \| None` | `None` | Type checking (mutual exclusion with `input_schema`) |
| `input_schema` | `dict \| None` | `None` | Type checking (mutual exclusion with `input_type`) |
| `output_type` | `str \| None` | `None` | Type checking (mutual exclusion with `output_schema`) |
| `output_schema` | `dict \| None` | `None` | Type checking (mutual exclusion with `output_type`) |
| `inputs` | `list[PortDefinition]` | `[PortDefinition(name="input")]` | Port model for edge resolution |
| `outputs` | `list[PortDefinition]` | `[PortDefinition(name="output")]` | Port model for edge resolution + condition routing |
| `hooks` | `list[PortDefinition]` | `[PortDefinition(name="on_start"), PortDefinition(name="on_end")]` | Hook edge identification |
| `position` | `dict[str, float] \| None` | `None` | UI only |

**`artifact_key` semantics (dual read+write per D-SF1-29, confirmed by feedback [C-4]):**

1. **READ (before execution):** The existing value at `artifact_key` is fetched from the store and injected into context. For AskNodes, it is prepended to the agent's prompt alongside any `context_keys`. For BranchNodes, it is available as the `artifact` variable in merge/condition/switch expressions. For PluginNodes, it is available via `context.artifact` in the `ExecutionContext`.

2. **WRITE (after execution):** The node's output is automatically written to the store at `artifact_key`. This happens AFTER execution but BEFORE output port routing. This replaces the need for explicit `artifact_write` PluginNodes in most cases.

3. **`artifact_write` PluginNode still exists** for: writing to a DIFFERENT key than the node's `artifact_key`, custom write logic, or writing from nodes that don't have `artifact_key` set.

**Context resolution order:** `artifact_key` is resolved **first**, followed by `context_keys`, followed by actor-level `context_keys`. This gives the primary artifact prominence in the prompt. Deduplication preserves this order.

### AskNode (extends NodeBase, type="ask") [D-SF1-12]

| Field | Type | Default | Runner Use |
|-------|------|---------|-----------|
| `actor` | `str` | required | Key into `workflow.actors` → hydrated to AgentActor/InteractionActor |
| `prompt` | `str` | required | Template with `{{ $input }}`, `{{ ctx.key }}` |

**Constraints the runner relies on:**
- `inputs` always `[PortDefinition(name="input")]` — SF-1 validator `_fix_input_ports` enforces. Runner can assume single fixed input.
- `outputs` 1+ ports — at least the default `output` port. Routing: **mutually exclusive first-match** on `PortDefinition.condition`.
- No `actor` field issues — SF-1 validation ensures `actor` references a valid key in `workflow.actors`.

**Context assembly for AskNode:**
```python
all_keys = list(dict.fromkeys(
    ([node.artifact_key] if node.artifact_key else []) +  # Primary artifact first
    node.context_keys +                                    # Node-level context
    actor.context_keys                                     # Actor baseline context
))
context_str = await context_provider.resolve(all_keys, feature=feature)
full_prompt = f"{context_str}\n\n## Task\n{rendered_prompt}" if context_str else rendered_prompt
```

**Auto-write after execution:**
```python
result = await runtime.invoke(role, full_prompt, ...)
if node.artifact_key:
    await artifacts.put(node.artifact_key, result, feature=feature)
# Then route to output ports
```

### BranchNode (extends NodeBase, type="branch") [D-SF1-13, D-SF1-28]

| Field | Type | Default | Runner Use |
|-------|------|---------|-----------|
| `switch_function` | `str \| None` | `None` | **Exclusive routing** [D-SF1-28]: `eval_switch(fn, data)` → returns port name string → only that port fires. Mutually exclusive with per-port `condition` on outputs. |
| `merge_function` | `str \| None` | `None` | `eval_merge(fn, inputs_dict)` when multi-input. Orthogonal to routing — runs before either routing strategy. |

**Dual routing model [D-SF1-2, D-SF1-28]:**

| Mode | When | Behavior |
|------|------|----------|
| **Exclusive (switch_function)** | `node.switch_function` is set | Evaluate function with `data` → returns port name string → fire ONLY that port's outgoing edges |
| **Non-exclusive (per-port conditions)** | `node.switch_function` is NOT set | Evaluate each output port's `condition` → all truthy ports fire simultaneously |

**Validation constraint:** `switch_function` and per-port `condition` are mutually exclusive on a given BranchNode. SF-1 validator produces `invalid_switch_function_config` error if both are present.

**Constraints the runner relies on:**
- `inputs` **1+ user-defined ports** — SF-1 validator `_validate_branch_ports` enforces min 1. Can be exactly 1 (no barrier needed) or N (barrier required).
- `outputs` **1+ user-defined ports** — min 1. Can be exactly 1 (unconditional passthrough).
- **NO `actor` field.**

**`artifact_key` on BranchNode:** When set, the resolved artifact value is available as `artifact` in the evaluation context for `merge_function`, `switch_function`, and port `condition` expressions. After branch execution, the merged data is auto-written to the store at `artifact_key`.

**Degenerate cases:**
- 1 input, 1 unconditional output → passthrough (no merge, no condition eval)
- 1 input, N conditional outputs → dispatch-only (no merge, evaluate conditions)
- N inputs, 1 unconditional output → gather-only (merge, no condition eval)
- N inputs, N conditional outputs → full gather + dispatch
- switch_function with 1 output → function must return that port's name (validated at runtime)

### PluginNode (extends NodeBase, type="plugin") [D-SF1-14, D-SF1-17, D-SF1-19]

| Field | Type | Default | Runner Use |
|-------|------|---------|-----------|
| `plugin_ref` | `str \| None` | `None` | Key into `workflow.plugins` + inline `config` |
| `instance_ref` | `str \| None` | `None` | Key into `workflow.plugin_instances` (pre-configured) |
| `config` | `dict \| None` | `None` | Only valid with `plugin_ref` |

**Constraints the runner relies on:**
- `inputs` always `[PortDefinition(name="input")]` — SF-1 validator `_fix_input_ports` enforces.
- `outputs` **0+ ports** — uniquely, PluginNode allows empty `outputs: []` for fire-and-forget side effects (e.g., `git_commit_push`, `preview_cleanup`). When 0 outputs, node executes but produces no data for downstream edges. `_activate_outgoing_edges` simply has no edges to fire.
- Exactly one of `plugin_ref` or `instance_ref` must be set — SF-1 validator enforces.
- Output routing: **mutually exclusive first-match** (same as Ask), but in practice most plugins have 0 or 1 output port.

**`artifact_key` on PluginNode:** When set: (1) READ: the resolved artifact value is available via `context.artifact` in the `ExecutionContext` passed to the plugin's `execute()` method. (2) WRITE: the plugin's return value is auto-written to the store at `artifact_key` after execution.

**Plugin resolution (3-tier) [H-4]:**
1. **Concrete Plugin** — `PluginRegistry.get(name)` returns a `Plugin` ABC instance → call `plugin.execute()` directly.
2. **Registered type** — `PluginRegistry.get_type(name)` returns a `PluginInterface` → look up `category` → dispatch to `CategoryExecutor`.
3. **Registered instance** — `PluginRegistry.get_instance(name)` returns a `PluginInstanceConfig` → resolve `plugin_type` → get `PluginInterface` → category → dispatch.

### PortDefinition [D-SF1-10]

Single type for ALL ports — data inputs, data outputs, hooks. Container field determines role.

| Field | Type | Default | Runner Use |
|-------|------|---------|-----------|
| `name` | `str` | required | Edge resolution (`"node_id.port_name"`) |
| `type_ref` | `str \| None` | `None` | Edge type checking (takes precedence over node-level `input_type`/`output_type`) |
| `description` | `str \| None` | `None` | UI only |
| `condition` | `str \| None` | `None` | Output port routing predicate. Receives `data`. Returns `bool`. Only meaningful on output ports. |

### Edge [D-SF1-21]

Single type for all connections — data edges AND hook edges. No `HookEdge` class.

| Field | Type | Default | Runner Use |
|-------|------|---------|-----------|
| `source` | `str` | required | `"node_id.port_name"` or `"$input.port_name"` |
| `target` | `str` | required | `"node_id.port_name"` or `"$output.port_name"` |
| `transform_fn` | `str \| None` | `None` | `eval_transform(fn, data)`. Must be `None` for hook edges. |
| `description` | `str \| None` | `None` | -- |

**Hook vs data edge:** Determined at graph-build time by checking whether the source port lives in the `hooks` container of the source element. No `port_type` field on Edge — this was in the design decisions document but NOT in the SF-1 schema.

### PhaseDefinition [D-SF1-4, D-SF1-11, D-SF1-22]

Shares the **identical** default port signatures as NodeBase. Both nodes and phases are valid elements in a DAG.

| Field | Type | Default | Runner Use |
|-------|------|---------|-----------|
| `id` | `str` | required | Element ID in `ExecutionGraph.elements` |
| `mode` | `Literal["sequential","map","fold","loop"]` | required | Mode executor dispatch |
| `sequential_config` | `SequentialConfig \| None` | `None` | Required when mode="sequential" |
| `map_config` | `MapConfig \| None` | `None` | Required when mode="map" |
| `fold_config` | `FoldConfig \| None` | `None` | Required when mode="fold" |
| `loop_config` | `LoopConfig \| None` | `None` | Required when mode="loop" |
| `nodes` | `list[NodeDefinition]` | `[]` | Internal elements |
| `edges` | `list[Edge]` | `[]` | Single list: data + hook edges |
| `phases` | `list[PhaseDefinition]` | `[]` | Nested sub-phases (recursive) |
| `input_type` | `str \| None` | `None` | Phase-level type checking |
| `input_schema` | `dict \| None` | `None` | Phase-level type checking |
| `output_type` | `str \| None` | `None` | Phase-level type checking |
| `output_schema` | `dict \| None` | `None` | Phase-level type checking |
| `inputs` | `list[PortDefinition]` | `[PortDefinition(name="input")]` | Same defaults as NodeBase |
| `outputs` | `list[PortDefinition]` | `[PortDefinition(name="output")]` | Same defaults as NodeBase |
| `hooks` | `list[PortDefinition]` | `[PortDefinition(name="on_start"), PortDefinition(name="on_end")]` | Same defaults as NodeBase |
| `summary` | `str \| None` | `None` | UI only |
| `context_keys` | `list[str]` | `[]` | Phase-scoped context |
| `context_text` | `dict[str, str]` | `{}` | Phase-scoped inline text |
| `artifact_key` | `str \| None` | `None` | **Dual read+write**: (1) READ at phase entry — resolved from store, added to phase-scoped context, inherited by children. (2) WRITE at phase exit — phase output auto-written to store. [D-SF1-29] |
| `position` | `dict[str, float] \| None` | `None` | UI only |

**Key distinction from NodeBase:** PhaseDefinition has `mode` (never `type`). NodeBase has `type` (never `mode`). This is how `_dispatch_element` discriminates.

**Loop auto-ports:** SF-1 validator automatically creates dual exit ports `condition_met` + `max_exceeded` on `outputs` when `mode="loop"`. The runner does NOT create these — it reads them from the validated model.

**Phase `artifact_key` dual semantics:**
- **READ at entry:** Resolved once, added to phase-scoped context. All child elements inherit via context hierarchy (workflow → phase → actor → node).
- **WRITE at exit:** Phase output auto-written to store at `artifact_key` after phase execution completes, before routing to downstream elements.

### Mode Configs

**SequentialConfig:** No fields. Empty model. Mode = execute elements in edge-determined order.

**MapConfig [D-SF1-16]:**

| Field | Type | Default | Runner Use |
|-------|------|---------|-----------|
| `collection` | `str` | required | `eval_expression(expr, ctx=...)` → iterable |
| `max_parallelism` | `int \| None` | `None` | Concurrency limit for `asyncio.Semaphore`. `None` = unlimited. |

No `fresh_sessions` — Map auto-creates unique actor instances per parallel execution.

**FoldConfig [D-SF1-16]:**

| Field | Type | Default | Runner Use |
|-------|------|---------|-----------|
| `collection` | `str` | required | `eval_expression(expr, ctx=...)` → iterable |
| `accumulator_init` | `str` | required | `eval_expression(expr)` — NO variables |
| `reducer` | `str` | required | `eval_expression(expr, accumulator=..., result=...)` |
| `fresh_sessions` | `bool` | `False` | Clear agent sessions before each iteration |

**LoopConfig [D-SF1-5, D-SF1-16]:**

| Field | Type | Default | Runner Use |
|-------|------|---------|-----------|
| `exit_condition` | `str` | required | `eval_predicate(expr, data=iteration_output)` → `True` exits |
| `max_iterations` | `int \| None` | `None` | Safety cap. Enables `max_exceeded` port when set. |
| `fresh_sessions` | `bool` | `False` | Clear agent sessions before each iteration |

### WorkflowConfig [D-SF1-6, D-SF1-21]

| Field | Type | Default | Runner Use |
|-------|------|---------|-----------|
| `schema_version` | `str` | `"1.0"` | Version check |
| `name` | `str` | required | Logging/tracking |
| `description` | `str \| None` | `None` | -- |
| `actors` | `dict[str, ActorDefinition]` | `{}` | Actor hydration |
| `types` | `dict[str, TypeDefinition]` | `{}` | Type checking |
| `phases` | `list[PhaseDefinition]` | `[]` | Workflow-level DAG elements |
| `edges` | `list[Edge]` | `[]` | Workflow-level edges (data + hook) |
| `plugins` | `dict[str, PluginInterface]` | `{}` | Plugin type definitions |
| `plugin_instances` | `dict[str, PluginInstanceConfig]` | `{}` | Pre-configured instances |
| `cost` | `CostConfig \| None` | `None` | Budget enforcement |
| `templates` | `dict[str, TemplateRef]` | `{}` | Template references |
| `stores` | `dict[str, StoreDefinition]` | `{}` | Store declarations |
| `context_keys` | `list[str]` | `[]` | Global context |
| `context_text` | `dict[str, str]` | `{}` | Global inline text |
| `inputs` | `list[WorkflowInputDefinition]` | `[]` | **[SF-2 addition]** |
| `outputs` | `list[WorkflowOutputDefinition]` | `[]` | **[SF-2 addition]** |

**SF-1 does NOT define `inputs`/`outputs` on WorkflowConfig.** These are SF-2 additions that must be upstreamed to SF-1 as additive changes (empty list defaults, no breaking changes).

### Supporting Types (Runner-Relevant Fields Only)

**ActorDefinition [D-SF1-16, D-SF1-25]:** `type` ("agent"|"interaction"), `role` (RoleDefinition, required for agent), `resolver` (str, required for interaction), `context_keys`, `context_text`, `persistent` (bool, default True), `context_store` (str|None), `handover_key` (str|None).

**RoleDefinition:** `name`, `prompt`, `tools` (list[str]), `model` (str|None), `effort` (Literal|None), `metadata` (dict).

**TypeDefinition:** `name`, `schema_def` (dict, JSON Schema Draft 2020-12), `description`.

**CostConfig:** `max_tokens` (int|None), `max_usd` (float|None), `track_by` ("node"|"phase"|"workflow", default "workflow").

**StoreDefinition [D-SF1-23]:** `description`, `keys` (dict[str, StoreKeyDefinition]|None — None=open store).

**StoreKeyDefinition:** `type_ref` (str|None), `description`.

**PluginInterface:** `id`, `name`, `description`, `inputs`/`outputs` (list[PortDefinition]), `config_schema` (dict|None), `category` ("service"|"mcp"|"cli"|"plugin"|None).

**PluginInstanceConfig [D-SF1-17]:** `id`, `name`, `plugin_type` (str, references PluginInterface.id), `config` (dict).

**TemplateRef:** `template_id` (str), `bindings` (dict[str, str]).

**WorkflowInputDefinition [SF-2 addition]:** `name`, `type_ref` (str|None), `schema_def` (dict|None, mutually exclusive with type_ref), `description`, `required` (bool, default True), `default` (Any, only when not required).

**WorkflowOutputDefinition [SF-2 addition]:** `name`, `type_ref` (str|None), `schema_def` (dict|None, mutually exclusive with type_ref), `description`.

### Expression Evaluation Contexts (SF-1 D-SF1-15 — Authoritative)

| Expression | Location | Variables | Returns |
|------------|----------|-----------|---------|
| `PortDefinition.condition` | Output ports | `data` = node output, `artifact` = resolved artifact_key value (if set) | `bool` |
| `BranchNode.switch_function` | BranchNode body (exclusive routing) [D-SF1-28] | `data` = node's merged/passthrough input, `artifact` = resolved artifact_key value (if set) | `str` — output port name |
| `BranchNode.merge_function` | BranchNode | `inputs: dict[str, Any]`, `artifact` = resolved artifact_key value (if set) | merged value |
| `Edge.transform_fn` | Data edges | `data` = source port output | transformed value |
| `LoopConfig.exit_condition` | Loop phase | `data` = phase `$output` | `bool` (True exits) |
| `MapConfig.collection` | Map phase | `ctx` = context keys + phase input | `Iterable` |
| `FoldConfig.collection` | Fold phase | `ctx` = context keys + phase input | `Iterable` |
| `FoldConfig.accumulator_init` | Fold phase | **(no variables)** | `Any` |
| `FoldConfig.reducer` | Fold phase | `accumulator`, `result` | `Any` |

---

## Built-in Plugins

SF-2 provides one built-in plugin that ships with iriai-compose. It is auto-registered in the `PluginRegistry` at construction time.

### `artifact_write` — Write Data to ArtifactStore at a Specified Key

**Purpose:** Explicitly persists data to the ArtifactStore at a key that is DIFFERENT from the node's own `artifact_key`. Most artifact writes happen automatically via `artifact_key` auto-write [D-SF1-29]. This plugin is for cases where:
- A node needs to write to a key other than its own `artifact_key`
- A PluginNode without `artifact_key` needs to persist data
- Custom write logic is needed (e.g., merging with existing data)

**Plugin Interface:**

```python
class ArtifactWritePlugin(Plugin):
    """Writes input data to the artifact store at a configured key.

    Config:
        key (str, required): The artifact store key to write to.

    Behavior:
        - Reads input data from the upstream edge
        - Writes it to artifact_store.put(key, data, feature=feature)
        - Returns the input data unchanged (pass-through for downstream edges)
    """

    async def execute(self, input_data: Any, *, context: ExecutionContext) -> Any:
        key = context.config["key"]
        await context.artifacts.put(key, input_data, feature=context.feature)
        return input_data
```

**YAML usage (explicit write to a different key):**

```yaml
nodes:
  - id: pm_task
    type: ask
    actor: pm
    prompt: "Write the PRD for this feature"
    artifact_key: artifacts.prd      # AUTO-WRITE: output stored as "prd" after execution
                                     # AUTO-READ: existing "prd" value injected into context
    context_keys: [artifacts.scope]  # READ: includes "scope" in context

  - id: save_prd_copy
    type: plugin
    plugin_ref: artifact_write
    config:
      key: artifacts.prd_backup      # EXPLICIT WRITE to a DIFFERENT key

edges:
  - source: pm_task.output
    target: save_prd_copy.input
```

**Key design properties:**
- **Pass-through:** Returns input unchanged, so it can be placed inline in the DAG without breaking data flow.
- **Complementary to auto-write:** Most writes use `artifact_key`. `artifact_write` handles the cases where auto-write is insufficient.
- **Fire-and-forget variant:** When only persistence is needed and no downstream nodes consume the data, use `outputs: []` on the PluginNode.
- **No dynamic keys in SF-2:** The `key` config is a static string. Dynamic key computation requires a future enhancement.
- **Built-in, not auto-registered in workflow YAML:** The plugin implementation is auto-registered in the `PluginRegistry`, but the workflow YAML must still declare `artifact_write` in its `plugins` section for schema validation.

---

## Data Flow Architecture

Data moves through a declarative workflow via **four layers**. Layers 1 and 2 use the **same execution engine** at different scales.

### Layer 1: Element-to-Element (Intra-Phase)

Within a single phase, **elements** (nodes and sub-phases) pass data to each other through edges. The `ExecutionGraph` treats both uniformly.

1. Each element execution produces a return value stored in `element_outputs[element_id]`.
2. If the element has `artifact_key`, the output is auto-written to the store BEFORE port routing.
3. Edges route values between elements. `_gather_inputs()` collects values from upstream elements, applying source port resolution and edge transforms.
4. The collected input arrives as `data` for nodes or `phase_input` for sub-phases.

### Layer 2: Phase-to-Phase (Workflow-Level DAG)

**Same engine as Layer 1.** `run()` builds an `ExecutionGraph` where elements = phases, then calls `_execute_dag()` with `phase_input=validated_inputs`.

### Layer 3: Artifact-Mediated (Context Channel)

Nodes interact with the ArtifactStore through `artifact_key` (dual read+write) and `context_keys` (read-only), in parallel with port connections. Artifacts are the persistent knowledge layer; port data is the transient task-passing layer.

**Read (context injection) — before execution:**
- **`artifact_key` read:** If set, the EXISTING value at this key is fetched from the store and injected into context. For AskNodes, prepended to prompt. For BranchNodes, available as `artifact` variable. For PluginNodes, available via `context.artifact`.
- **`context_keys` read:** Additional store keys resolved and injected into context.
- **Phase-level read:** `artifact_key` on a phase is resolved once at entry and added to the phase's scoped context, inherited by all child elements.

**Write (auto-write) — after execution [D-SF1-29]:**
- **`artifact_key` auto-write:** If set, the node/phase output is automatically written to the store at this key AFTER execution, BEFORE output port routing.
- **Explicit plugin write:** The `artifact_write` plugin can write to DIFFERENT keys than `artifact_key`. Used for secondary copies, backups, or writes from nodes without `artifact_key`.

**Scope:** Artifacts persist across restarts and are scoped per-Feature. Port data does not persist.

**Context hierarchy** (D-SF1-24): workflow → phase → actor → node. Runner's `ContextProvider.resolve()` merges and deduplicates. The `artifact_key` at each level contributes to its respective scope.

### Layer 4: Phase Mode Input Injection

| Mode | `$input` receives | `$output` produces |
|------|-------------------|--------------------|
| Sequential | `phase_input` | Phase output |
| Map | Current collection item | List of all item outputs |
| Fold | `{"item": item, "accumulator": acc}` | Fed to `reducer`; final accumulator = phase output |
| Loop | First: `phase_input`; subsequent: previous `$output` | Evaluated by `exit_condition`; True → `("condition_met", output)` |

**Collection expression context**: `MapConfig.collection` and `FoldConfig.collection` receive `ctx` — resolved context keys + phase input. NOT `data`.

**`accumulator_init`**: Evaluated with NO variables. Pure expression.

**`reducer`**: Evaluated with `accumulator` and `result` (iteration `$output`).

**`exit_condition`**: Evaluated with `data` = iteration `$output`.

### Phase Port Routing: `$input` and `$output`

Per D-SF1-4, phases enforce strict I/O boundaries. Internal elements receive/produce data through `$input`/`$output` pseudo-nodes. Same mechanism at workflow level.

**`$input` priority in `_gather_inputs`:**
1. Explicit `$input` edge targeting this element → resolves named port from `phase_input`
2. Fired upstream data edges → normal gathering from `element_outputs` with source port resolution
3. Entry element with no `$input` edge → `phase_input` directly

**`$output` via `_collect_output`:**
1. Single `$output` edge → single value (with source port resolution + optional transform)
2. Multiple `$output` edges → `{port_name: data, ...}` dict
3. No `$output` edges → last exit element's output

### Source Port Resolution

When an element produces multi-port output (e.g., `{"plan": ..., "system_design": ...}`), downstream edges referencing `architecture.plan` need to extract just that key.

```python
def _resolve_source_port(data: Any, source_port: str) -> Any:
    if source_port in ("output", "default"):
        return data
    if isinstance(data, dict) and source_port in data:
        return data[source_port]
    return data
```

Called in `_gather_inputs` and `_collect_output`. Nowhere else.

**Source port vs exit path dual semantics:** For loop elements, the source port name (e.g., `condition_met`) is a routing signal consumed by `_activate_outgoing_edges`, NOT a data key. Loop exit tuples are unwrapped before storage, so `_resolve_source_port` receives plain data and returns it as-is.

### Cross-Boundary Connections

All eight combinations of node↔phase edges work with the same abstractions because `build_execution_graph` includes both `phase.nodes` and `phase.phases` as elements, `_gather_inputs` uses source port resolution uniformly, and `_dispatch_element` checks `mode` vs `type`.

### Typical Artifact Read+Write Pattern

The following shows how a workflow reads and writes artifacts using the dual read+write semantics:

```yaml
phases:
  - id: scoping
    mode: sequential
    nodes:
      - id: scope_lead
        type: ask
        actor: pm
        prompt: "Analyze the project scope"
        artifact_key: artifacts.scope       # READ existing "scope" + AUTO-WRITE output as "scope"
        context_keys: [artifacts.project]   # READ: also fetches "project"

    edges:
      - source: $input.input
        target: scope_lead.input

  - id: design
    mode: sequential
    nodes:
      - id: designer
        type: ask
        actor: designer
        prompt: "Create the design"
        artifact_key: artifacts.design      # READ existing "design" + AUTO-WRITE output as "design"
        context_keys: [artifacts.scope]     # READ: fetches "scope" (written by previous phase)
```

**No explicit `artifact_write` PluginNode needed** for simple read→process→write patterns. The `artifact_key` field handles both directions.

---

## Branch Node Execution Model

BranchNode is the only node type with user-configurable input ports AND two routing strategies.

### Port Routing Contrast

| Node Type | Input Ports | Output Routing | Key Constraint |
|-----------|-------------|----------------|----------------|
| AskNode | 1 fixed (`input`) | **Mutually exclusive first-match** on port conditions | SF-1 `_fix_input_ports` enforces single input |
| PluginNode | 1 fixed (`input`) | **Mutually exclusive first-match** on port conditions | `outputs: []` allowed (fire-and-forget) [D-SF1-19] |
| BranchNode (switch_function) | 1+ user-defined | **Exclusive** — `switch_function` returns port name → only that port fires [D-SF1-28] | Mutually exclusive with per-port conditions |
| BranchNode (per-port conditions) | 1+ user-defined | **Non-exclusive** — all truthy condition ports fire simultaneously | Default when no `switch_function` |

### Gather Barrier

Runner implements barrier per D-SF1-20. `_branch_inputs_ready` defers execution until all connected input ports have fired upstream edges. Returns True for all non-branch elements (no-op at workflow level).

### Merge + Route

```python
async def execute_branch_node(node, data, *, node_id: str, artifact_value: Any = None) -> tuple[dict[str, bool], Any]:
    # Step 1: Merge multi-input
    if node.merge_function and isinstance(data, dict) and len(data) > 1:
        merged = eval_merge(node.merge_function, data, node_id=node_id, artifact=artifact_value)
    elif isinstance(data, dict) and len(data) == 1:
        merged = next(iter(data.values()))
    else:
        merged = data

    # Step 2: Route — two mutually exclusive strategies
    port_fires = {}

    if node.switch_function:
        # Exclusive routing: function returns port name string
        selected_port = eval_switch(node.switch_function, merged, node_id=node_id, artifact=artifact_value)
        valid_ports = {p.name for p in node.outputs}
        if selected_port not in valid_ports:
            raise ExpressionEvalError(
                f"switch_function returned '{selected_port}' but available ports are {sorted(valid_ports)}",
                node_id=node_id, expression=node.switch_function
            )
        port_fires = {p.name: (p.name == selected_port) for p in node.outputs}
    else:
        # Non-exclusive per-port conditions
        for port in node.outputs:
            if port.condition:
                port_fires[port.name] = eval_predicate(port.condition, merged, node_id=node_id, artifact=artifact_value)
            else:
                port_fires[port.name] = True

    return port_fires, merged
```

**`artifact_value` parameter:** When the BranchNode has `artifact_key` set, the resolved artifact is passed to `execute_branch_node` and made available as the `artifact` variable in merge, switch, and condition expressions.

### `_activate_outgoing_edges`

Five dispatch models:
1. **BranchNode with switch_function** (`type=="branch"` + `switch_function` + `port_fires`): Exclusive. Only the selected port's edges fire. [D-SF1-28]
2. **BranchNode with per-port conditions** (`type=="branch"` + no `switch_function` + `port_fires`): Non-exclusive. All truthy ports' edges fire.
3. **Ask/Plugin with conditions** (`_has_port_conditions` + no `mode`): Mutually exclusive first-match. First truthy port's edges fire.
4. **Loop exit** (`_is_loop_exit`): `edge_matches_exit_path` selects edges.
5. **Default** (single output, no conditions, phases at workflow level): All outgoing edges fire.

**Implementation note:** Models 1 and 2 are implemented identically in `_activate_outgoing_edges` — both use the `port_fires` dict. The difference is in how `execute_branch_node` produces the dict (switch selects one, conditions evaluate all).

### `_execute_dag`

Single engine for both levels. Branch nodes handled specially (different return type). Deferred queue with 2× safety cap for barrier scheduling.

**Auto-write integration:** After dispatching any element and obtaining its output, if `element.artifact_key` is set, auto-write the output to the artifact store. For BranchNodes, the merged data (not the `port_fires` dict) is written.

---

## Workflow-Level Inputs and Outputs

### Schema Additions [SF-2 → SF-1 upstream]

**`WorkflowInputDefinition`:** `name`, `type_ref|schema_def` (mutually exclusive), `description`, `required` (default True), `default` (only when not required).

**`WorkflowOutputDefinition`:** `name`, `type_ref|schema_def` (mutually exclusive), `description`.

On `WorkflowConfig`: `inputs: list[WorkflowInputDefinition] = []`, `outputs: list[WorkflowOutputDefinition] = []`.

### `run()` API

```python
async def run(workflow: WorkflowConfig | str | Path, config: RuntimeConfig,
              *, inputs: dict[str, Any] | None = None) -> ExecutionResult:
```

### Input Validation

`_validate_workflow_inputs`: Checks required fields, applies defaults, type-checks. Raises `WorkflowInputError` before execution.

### Output Validation

`_validate_workflow_outputs`: Warns on missing declared outputs. Type-checks values. **Never raises** — don't lose execution results.

### `ExecutionResult`

```python
@dataclass
class ExecutionResult:
    success: bool
    error: ExecutionError | None = None
    nodes_executed: list[tuple[str, str]] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    branch_paths: dict[str, str] = field(default_factory=dict)
    cost_summary: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    workflow_output: dict[str, Any] | Any = None    # [SF-2]
    hook_warnings: list[str] = field(default_factory=list)  # [SF-2]
```

**`artifacts` field:** Populated by reading ALL keys from the ArtifactStore after execution completes. This captures everything written via `artifact_key` auto-writes AND by `artifact_write` plugins during the workflow run. The runner tracks written keys via a write-through wrapper on the ArtifactStore that records `put()` calls.

### `build_execution_graph` (Unified)

```python
def build_execution_graph(container, *, is_workflow=False) -> ExecutionGraph:
    if is_workflow:
        elements = {p.id: p for p in container.phases}
        edges = container.edges
    else:
        elements = {}
        for n in container.nodes:
            elements[n.id] = n
        for sp in (container.phases or []):
            elements[sp.id] = sp
        edges = container.edges
    # Separate $input/$output/hook/data edges, build adjacency, topo sort
    ...
```

---

## Plugin System Architecture [H-4]

The PluginRegistry supports three registration modes to handle the full spectrum of plugin types:

### Three-Tier Plugin Resolution

| Tier | Registration | Resolution | Dispatch |
|------|-------------|------------|----------|
| **Concrete** | `registry.register(name, plugin: Plugin)` | `registry.get(name)` → `Plugin` instance | Direct: `plugin.execute(input_data, context=ctx)` |
| **Type** | `registry.register_type(name, interface: PluginInterface)` | `registry.get_type(name)` → `PluginInterface` | Category: `category_executor.execute(interface, config, input_data, context=ctx)` |
| **Instance** | `registry.register_instance(name, config: PluginInstanceConfig)` | `registry.get_instance(name)` → `PluginInstanceConfig` → resolve type → `PluginInterface` | Category: same as Type, with instance config |

### CategoryExecutor ABC

```python
class CategoryExecutor(ABC):
    """Handles execution for a category of plugins (service, mcp, cli, plugin)."""

    @abstractmethod
    async def execute(
        self,
        interface: PluginInterface,
        config: dict[str, Any],
        input_data: Any,
        *,
        context: ExecutionContext,
    ) -> Any: ...
```

**Built-in category executors in SF-2:** None. Category executors are registered by consuming projects (e.g., iriai-build-v2 registers MCP and CLI executors). SF-2 provides the framework.

### Plugin Executor Resolution Flow

```python
async def execute_plugin_node(node, input_data, *, registry, workflow, context):
    # Resolve plugin reference
    if node.plugin_ref:
        # Try concrete first
        if registry.has(node.plugin_ref):
            plugin = registry.get(node.plugin_ref)
            return await plugin.execute(input_data, context=context)

        # Try type-based
        if registry.has_type(node.plugin_ref):
            interface = registry.get_type(node.plugin_ref)
            config = node.config or {}
            return await _dispatch_category(registry, interface, config, input_data, context)

        # Try workflow-declared type
        if node.plugin_ref in workflow.plugins:
            interface = workflow.plugins[node.plugin_ref]
            config = node.config or {}
            return await _dispatch_category(registry, interface, config, input_data, context)

        raise PluginNotFoundError(node.plugin_ref)

    elif node.instance_ref:
        # Resolve instance → type → category
        if registry.has_instance(node.instance_ref):
            instance_config = registry.get_instance(node.instance_ref)
        elif node.instance_ref in workflow.plugin_instances:
            instance_config = workflow.plugin_instances[node.instance_ref]
        else:
            raise PluginNotFoundError(node.instance_ref)

        type_name = instance_config.plugin_type
        interface = registry.get_type(type_name) if registry.has_type(type_name) else workflow.plugins.get(type_name)
        if not interface:
            raise PluginNotFoundError(f"Plugin type '{type_name}' for instance '{node.instance_ref}'")

        config = instance_config.config
        return await _dispatch_category(registry, interface, config, input_data, context)


async def _dispatch_category(registry, interface, config, input_data, context):
    category = interface.category or "plugin"
    executor = registry.get_category_executor(category)
    if not executor:
        raise PluginNotFoundError(f"No category executor registered for '{category}'")
    return await executor.execute(interface, config, input_data, context=context)
```

---

## Resume and Checkpoint (Out of Scope)

Every phase in iriai-build-v2 checks the artifact store before execution to skip. This is critical for production but out of scope for SF-2. The runner is store-agnostic.

**Future path:** Pre-dispatch hook in `_execute_dag` checks the ArtifactStore for the `artifact_key` value. If present and a resume flag is set, skip execution and use the stored value. This is simplified by `artifact_key` being both the read and write key.

---

## Post-Event Callbacks → Hook Edges

iriai-build-v2 `post_update`/`post_compile` callbacks map to hook edges: `node.on_end → plugin_node.input` (fire-and-forget per D-SF1-21). Hook targets are commonly PluginNodes that perform side effects like hosting or notifications.

---

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
| D-SF2-2 | Runner architecture | Standalone `run()` | Cleaner consumer API |
| D-SF2-3 | Multi-runtime routing | RuntimeConfig | Single config object |
| D-SF2-4 | Runtime configuration | Hybrid: RuntimeConfig + YAML loader | Flexibility + convenience |
| D-SF2-5 | Transform execution | Full exec(), trust author | Same trust as .py files |
| D-SF2-6 | YAML library | pyyaml | Lighter than ruamel |
| D-SF2-7 | Plugin architecture | Package + entry-point + type/instance/category | Extensibility across concrete, metadata, and category-dispatched plugins |
| D-SF2-8 | Plugin portability | YAML `package` field | Clear install errors |
| D-SF2-9 | Database | Postgres for compose app only | Runner is store-agnostic |
| D-SF2-10 | Intra-phase data flow | `element_outputs` + edge transforms | Unified DAG |
| D-SF2-11 | Inter-phase data flow | Dual: port + artifact | Port = task data; artifact = background context |
| D-SF2-12 | Selective routing | `fired_edges` with per-type conditions | Same mechanism, different semantics |
| D-SF2-13 | Phase input routing | `$input` edges | Explicit + implicit fallback |
| D-SF2-14 | Phase output routing | `$output` edges | Single/multi/fallback |
| D-SF2-15 | Workflow-level DAG | Same engine | Falls back to sequential |
| D-SF2-16 | Fold `$input` | `{"item", "accumulator"}` | Per D-SF1-4 |
| D-SF2-17 | Loop threading | `$output` → next `$input` | Gate feedback loop |
| D-SF2-18 | Source port resolution | `_resolve_source_port` | Unified at both levels |
| D-SF2-19 | Uniform element model | Nodes + sub-phases as elements | `mode` vs `type` discrimination |
| D-SF2-20 | Loop exit routing | `(exit_path, output)` tuple | `edge_matches_exit_path` |
| D-SF2-21 | Branch barrier | Deferred queue | Per D-SF1-20 |
| D-SF2-22 | Branch output routing (dual) | switch_function → exclusive; per-port conditions → non-exclusive | Per D-SF1-2 and D-SF1-28 |
| D-SF2-23 | Branch merge_function | `eval_merge(fn, inputs)` | Per D-SF1-15 |
| D-SF2-24 | Workflow inputs | `inputs` on WorkflowConfig [SF-2] | Parameterized workflows |
| D-SF2-25 | Input validation | `_validate_workflow_inputs` | Fail-fast |
| D-SF2-26 | Workflow `$input`/`$output` | Same engine | No separate types |
| D-SF2-27 | Ask/Plugin conditions | Mutually exclusive first-match | Per D-SF1-2 |
| D-SF2-28 | Unified DAG engine | Single `ExecutionGraph` + `_execute_dag` | Eliminates duplicates |
| D-SF2-29 | Source port resolution | `_resolve_source_port` everywhere | Fixes phase-level gap |
| D-SF2-30 | Workflow outputs | `outputs` on WorkflowConfig [SF-2] | Symmetric with inputs |
| D-SF2-31 | Output validation | Warn only, never raise | Don't lose results |
| D-SF2-32 | Resume/checkpoint | Out of scope | Future: pre-dispatch hook |
| D-SF2-33 | Post-event callbacks | Hook edges | `on_end` → Plugin |
| D-SF2-34 | Nested dynamic DAGs | Plugin with runner access | Static outer, data-driven inner |
| D-SF2-35 | `collection` context | `ctx` not `data` | Per D-SF1-15 |
| D-SF2-36 | No-edges fallback | Sequential outside `_execute_dag` | Output→input threading |
| D-SF2-37 | PluginNode fire-and-forget | `outputs: []` allowed | Per D-SF1-19. No outgoing edges to fire. |
| D-SF2-38 | MapConfig.max_parallelism | `asyncio.Semaphore(N)` or unlimited | Per MapConfig field. None=unlimited. |
| D-SF2-39 | Element type discrimination | `mode` (phase) vs `type` (node) — mutually exclusive | Never both on same element. `hasattr` check. |
| D-SF2-40 | `artifact_key` is dual read+write | Before execution: read existing value for context. After execution: auto-write output to store. | Per D-SF1-29: "Runner auto-writes node output to store at `artifact_key` after execution, before routing." Matches iriai-build-v2 where `artifact_key` is the store key for both reads via `context_keys` and writes via `runner.artifacts.put()`. Eliminates need for explicit `artifact_write` PluginNodes in most cases. |
| D-SF2-41 | `artifact_write` plugin for explicit/different-key writes | `artifact_write` plugin: pass-through that persists to ArtifactStore at a DIFFERENT key | Complements auto-write. Used when writing to a key other than `artifact_key`, for secondary copies, or custom write logic. |
| D-SF2-42 | `artifact_key` context priority | Resolved before `context_keys` and actor keys | Primary artifact gets prominence in prompt. Order: `artifact_key` → node `context_keys` → actor `context_keys`. |
| D-SF2-43 | `artifact_key` available in expressions | Resolved value passed as `artifact` variable to condition/merge/switch expressions | Enables BranchNode conditions and switch functions to route based on stored artifacts. |
| D-SF2-44 | Phase `artifact_key` dual semantics | READ at entry (context injection), WRITE at exit (auto-write output) | Phase-level read+write mirrors node-level semantics in the uniform DAG. |
| D-SF2-45 | No dynamic artifact keys in SF-2 | Auto-write key is static `artifact_key` string | Dynamic keys (per-iteration in folds) require custom plugin. Future enhancement. |
| D-SF2-46 | Loader delegates to SF-1 | `loader.py` imports SF-1's `load_workflow()` from `iriai_compose.schema.yaml_io`, adds runtime-specific validation | Eliminates code duplication [H-1]. SF-1 owns YAML parsing + Pydantic validation. SF-2 adds runtime checks. |
| D-SF2-47 | `switch_function` exclusive routing on BranchNode | `eval_switch(fn, data)` → port name string → only that port fires | Per D-SF1-28. Dual routing: `switch_function` (exclusive) vs per-port conditions (non-exclusive). Mutually exclusive on same node. |
| D-SF2-48 | PluginRegistry three-tier: concrete + type + instance | `register()` for Plugin ABC instances, `register_type()` for PluginInterface metadata, `register_instance()` for PluginInstanceConfig, `register_category_executor()` for category dispatch | Per [H-4]. Supports both direct plugin execution and category-based dispatch via PluginInterface metadata. |
| D-SF2-49 | `run()` signature | `run(workflow, config: RuntimeConfig, *, inputs=None)` | Per [C-3]. Confirmed as source of truth. |

---

## Implementation Steps

### STEP-5: Dependencies and Subpackage Skeleton

**Objective:** Add `pyyaml` dependency and create stubs for all modules including built-in plugins.

**Scope:**
| Path | Action |
|------|--------|
| `pyproject.toml` | modify |
| `iriai_compose/declarative/__init__.py` | create |
| `iriai_compose/declarative/loader.py` | create |
| `iriai_compose/declarative/runner.py` | create |
| `iriai_compose/declarative/graph.py` | create |
| `iriai_compose/declarative/executors.py` | create |
| `iriai_compose/declarative/modes.py` | create |
| `iriai_compose/declarative/transforms.py` | create |
| `iriai_compose/declarative/plugins.py` | create |
| `iriai_compose/declarative/config.py` | create |
| `iriai_compose/declarative/actors.py` | create |
| `iriai_compose/declarative/hooks.py` | create |
| `iriai_compose/declarative/errors.py` | create |
| `iriai_compose/plugins/__init__.py` | create |
| `iriai_compose/plugins/artifact_write.py` | create |

**Instructions:**
- Add `pyyaml>=6.0,<7.0` to `dependencies` in `pyproject.toml` (NOT optional).
- Create all stub files with docstrings describing their purpose.
- `iriai_compose/plugins/__init__.py` must export a `register_builtins(registry)` function that registers `ArtifactWritePlugin`.
- `iriai_compose/plugins/artifact_write.py` contains the `ArtifactWritePlugin` class stub.
- All stubs should be importable but raise `NotImplementedError` on any actual execution.

**Acceptance Criteria:**
- `pip install -e .` succeeds
- `from iriai_compose.declarative import run` works (stub)
- `from iriai_compose.plugins.artifact_write import ArtifactWritePlugin` works
- Existing tests pass unchanged

**Counterexamples:**
- Do NOT add pyyaml as optional dependency
- Do NOT import SF-1 models yet (they may not exist)
- Do NOT implement any logic — stubs only

**Requirement IDs:** R7 | **Journey IDs:** J-2

---

### STEP-6: YAML Loader (Thin Wrapper over SF-1)

**Objective:** `load_workflow()` delegation to SF-1 with runtime-specific validation. Eliminates duplication per [H-1].

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/loader.py` | modify |
| `iriai_compose/declarative/errors.py` | modify |
| `iriai_compose/schema/yaml_io.py` | read |

**Instructions:**
- Import `load_workflow` from `iriai_compose.schema.yaml_io` — SF-1's loader handles YAML parsing, `yaml.safe_load()`, Pydantic validation, and file path resolution.
- Re-export it as `iriai_compose.declarative.load_workflow` for convenience.
- Add `validate_runtime_requirements(workflow: WorkflowConfig) -> list[str]` for runtime-specific checks:
  - Verify all interaction actors have registered runtimes (based on `resolver` field)
  - Verify all `plugin_ref`/`instance_ref` references can be resolved
  - Check that `inputs`/`outputs` fields (SF-2 additions) are structurally valid
- If SF-1's `load_workflow()` raises, catch and wrap in `WorkflowLoadError` with additional context.
- `WorkflowLoadError` defined in `errors.py` with fields for: `source` (file path or "string"), `parse_errors` (list), `validation_errors` (list).

**Acceptance Criteria:**
- `load_workflow("path/to/file.yaml")` delegates to SF-1's loader and returns `WorkflowConfig`
- `load_workflow(yaml_string)` also works (SF-1 handles string detection)
- Invalid YAML raises `WorkflowLoadError` wrapping SF-1's error
- `validate_runtime_requirements()` returns warnings for unresolvable plugins
- `inputs`/`outputs` fields parsed correctly when present; default to empty lists when absent

**Counterexamples:**
- Do NOT duplicate YAML parsing or Pydantic validation — SF-1 owns that [H-1]
- Do NOT silently swallow validation errors
- Do NOT re-implement `yaml.safe_load()` — delegate entirely to SF-1

**Requirement IDs:** R7 | **Journey IDs:** J-2, J-8

**Citations:**
- [code: SF-1 plan STEP-11 — `iriai_compose/schema/yaml_io.py` defines `load_workflow`, `load_workflow_lenient`, `dump_workflow`]
- [decision: H-1 — "Refactor: import SF-1's function, then add any runtime-specific validation"]

---

### STEP-7: RuntimeConfig, Plugin Registry (Three-Tier), and Built-in Plugins

**Objective:** `RuntimeConfig`, `PluginRegistry` with concrete/type/instance registration, `Plugin` ABC, `CategoryExecutor` ABC, `ExecutionContext`, `required_plugins()`, and `ArtifactWritePlugin`.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/config.py` | modify |
| `iriai_compose/declarative/plugins.py` | modify |
| `iriai_compose/plugins/__init__.py` | modify |
| `iriai_compose/plugins/artifact_write.py` | modify |

**Instructions:**

**RuntimeConfig:**
```python
@dataclass
class RuntimeConfig:
    agent_runtime: AgentRuntime
    interaction_runtimes: dict[str, InteractionRuntime] = field(default_factory=dict)
    artifacts: ArtifactStore | None = None           # None → InMemoryArtifactStore
    sessions: SessionStore | None = None             # None → InMemorySessionStore
    context_provider: ContextProvider | None = None  # None → DefaultContextProvider(artifacts)
    plugin_registry: PluginRegistry | None = None    # None → default with builtins
    workspace: Workspace | None = None
    feature: Feature | None = None                   # None → auto-created from workflow name
```

**Plugin ABC:**
```python
class Plugin(ABC):
    @abstractmethod
    async def execute(self, input_data: Any, *, context: ExecutionContext) -> Any: ...

@dataclass
class ExecutionContext:
    config: dict[str, Any]          # Plugin-specific config from node
    artifacts: ArtifactStore        # Direct store access
    sessions: SessionStore          # Session management
    context_provider: ContextProvider  # Context resolution
    feature: Feature                # Current feature
    workspace: Workspace | None     # Current workspace
    runner: Any                     # Reference to declarative runner (for nested DAGs, D-SF2-34)
    services: dict[str, Any]        # Ambient services
    artifact: Any | None = None     # Resolved artifact_key value (READ — existing value before execution)
```

**CategoryExecutor ABC [H-4]:**
```python
class CategoryExecutor(ABC):
    """Handles execution for a category of plugins (service, mcp, cli, plugin)."""

    @abstractmethod
    async def execute(
        self,
        interface: PluginInterface,
        config: dict[str, Any],
        input_data: Any,
        *,
        context: ExecutionContext,
    ) -> Any: ...
```

**PluginRegistry (three-tier) [H-4]:**
```python
class PluginRegistry:
    def __init__(self, *, auto_register_builtins: bool = True):
        self._plugins: dict[str, Plugin] = {}                      # Concrete instances
        self._types: dict[str, PluginInterface] = {}                # Type metadata
        self._instances: dict[str, PluginInstanceConfig] = {}       # Instance configs
        self._category_executors: dict[str, CategoryExecutor] = {}  # Category dispatchers
        if auto_register_builtins:
            from iriai_compose.plugins import register_builtins
            register_builtins(self)

    # Tier 1: Concrete Plugin instances
    def register(self, name: str, plugin: Plugin) -> None: ...
    def get(self, name: str) -> Plugin: ...         # Raises PluginNotFoundError
    def has(self, name: str) -> bool: ...

    # Tier 2: Plugin type metadata (PluginInterface)
    def register_type(self, name: str, interface: PluginInterface) -> None: ...
    def get_type(self, name: str) -> PluginInterface: ...
    def has_type(self, name: str) -> bool: ...

    # Tier 3: Plugin instance configs (PluginInstanceConfig)
    def register_instance(self, name: str, config: PluginInstanceConfig) -> None: ...
    def get_instance(self, name: str) -> PluginInstanceConfig: ...
    def has_instance(self, name: str) -> bool: ...

    # Category-based dispatch
    def register_category_executor(self, category: str, executor: CategoryExecutor) -> None: ...
    def get_category_executor(self, category: str) -> CategoryExecutor | None: ...

    # Discovery
    def discover_entry_points(self) -> None: ...    # pkg_resources/importlib.metadata
```

**ArtifactWritePlugin:**
```python
class ArtifactWritePlugin(Plugin):
    """Writes input data to the artifact store at a configured key. Returns input unchanged (pass-through).

    Use when writing to a DIFFERENT key than the node's artifact_key.
    For same-key writes, use artifact_key auto-write instead.
    """

    async def execute(self, input_data: Any, *, context: ExecutionContext) -> Any:
        key = context.config.get("key")
        if not key:
            raise PluginConfigError("artifact_write requires 'key' in config")
        await context.artifacts.put(key, input_data, feature=context.feature)
        return input_data
```

**`register_builtins(registry)`:**
```python
def register_builtins(registry: PluginRegistry) -> None:
    from iriai_compose.plugins.artifact_write import ArtifactWritePlugin
    registry.register("artifact_write", ArtifactWritePlugin())
```

**Acceptance Criteria:**
- `PluginRegistry()` auto-registers `artifact_write` plugin
- `registry.get("artifact_write")` returns `ArtifactWritePlugin` instance
- `registry.register_type("mcp_server", interface)` stores type metadata
- `registry.register_instance("my_preview", config)` stores instance config
- `registry.register_category_executor("mcp", mcp_executor)` stores category handler
- `registry.get_type("mcp_server")` returns the registered `PluginInterface`
- `registry.get_instance("my_preview")` returns the registered `PluginInstanceConfig`
- `registry.get_category_executor("mcp")` returns the registered executor
- `ArtifactWritePlugin.execute()` writes to `context.artifacts` and returns input unchanged
- `ArtifactWritePlugin.execute()` raises `PluginConfigError` when `key` missing from config
- `required_plugins(workflow)` returns list of plugin names referenced by PluginNodes
- `ExecutionContext.artifact` is populated from resolved `artifact_key` (READ value) when set, `None` otherwise

**Counterexamples:**
- Do NOT make built-in registration optional by default (always auto-register unless explicitly disabled)
- Do NOT allow duplicate plugin registration (raise on conflict)
- `ArtifactWritePlugin` must NOT modify input data before returning — strict pass-through
- Do NOT conflate concrete Plugin registration with type registration — they are separate namespaces
- Category executors must NOT be auto-registered — consuming projects register them

**Requirement IDs:** R7 | **Journey IDs:** J-2

**Citations:**
- [decision: H-4 — "PluginRegistry must support both concrete Plugin ABC instances and PluginInterface + PluginInstanceConfig metadata with category-based dispatch"]
- [code: SF-1 plan — PluginInterface has `category` field: "service"|"mcp"|"cli"|"plugin"|None]

---

### STEP-8: DAG Builder (Unified `ExecutionGraph`)

**Objective:** `build_execution_graph(container, *, is_workflow=False)`. One type, one builder. Source port resolution.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/graph.py` | modify |
| `iriai_compose/declarative/errors.py` | modify |

**Instructions:**
- Include both `phase.nodes` AND `phase.phases` as elements.
- Separate `$input`/`$output`/hook/data edges.
- Hook edge identification: check source port's container (`hooks` vs `outputs`) per D-SF1-21.
- `_resolve_source_port(data, source_port)`:
  - `"output"` or `"default"` → return data as-is
  - Dict with matching key → return `data[source_port]`
  - Non-dict or no match → return data as-is (loop exit case)
- Topological sort via Kahn's algorithm. Raise `CycleDetectedError` with cycle path.
- Raise `DuplicateElementError` on element ID collision.

**Acceptance Criteria:**
- `_resolve_source_port({"plan": x}, "plan")` → `x`
- `_resolve_source_port(data, "output")` → `data`
- `_resolve_source_port(non_dict, "condition_met")` → `non_dict` (loop exit case)
- Phase and workflow levels return same `ExecutionGraph` type
- Cycle detection raises with cycle path in error
- Duplicate element IDs raise with both IDs in error

**Counterexamples:**
- No `WorkflowGraph` type
- No `_gather_workflow_phase_input`
- No `_resolve_phase_output_port`
- Do NOT create separate graph types for workflow vs phase level

**Requirement IDs:** R7 | **Journey IDs:** J-2

---

### STEP-9: Transform and Expression Execution

**Objective:** `eval_transform`, `eval_predicate`, `eval_expression`, `eval_merge`, and `eval_switch`. Contexts MUST match SF-1 D-SF1-15 exactly (see Expression Evaluation Contexts table above), with `artifact` variable available where specified.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/transforms.py` | modify |
| `iriai_compose/declarative/errors.py` | modify |

**Instructions:**
- All eval functions use `exec()` with restricted `__builtins__` (standard library math, string ops, None/True/False/len/range/list/dict/set/tuple/str/int/float/bool/isinstance/type/sorted/reversed/enumerate/zip/map/filter/any/all/min/max/sum).
- `eval_transform(fn_body: str, data: Any, *, node_id: str) -> Any` — variables: `data`.
- `eval_predicate(expr: str, data: Any, *, node_id: str, artifact: Any = None) -> bool` — variables: `data`, `artifact` (when provided and not None).
- `eval_expression(expr: str, **variables) -> Any` — arbitrary named variables.
- `eval_merge(fn_body: str, inputs: dict[str, Any], *, node_id: str, artifact: Any = None) -> Any` — variables: `inputs`, `artifact` (when provided and not None).
- `eval_switch(fn_body: str, data: Any, *, node_id: str, artifact: Any = None) -> str` — variables: `data`, `artifact` (when provided and not None). Returns port name string. Raises `ExpressionEvalError` if return value is not a `str`. [D-SF1-28, C-1]
- All raise `ExpressionEvalError` with node_id, expression, and original exception on failure.
- Timeout: no explicit timeout (same trust as .py files per D-SF2-5).
- When `artifact` is `None`, do NOT include it in the eval namespace — omit entirely so expressions don't accidentally reference an undefined `artifact` variable.

**Acceptance Criteria:**
- `eval_expression("ctx['decomposition'].subfeatures", ctx={...})` works
- `eval_expression("{'artifacts': {}}")` works with no variables
- `eval_merge(fn, {"a": 1})` evaluates with `inputs` variable
- `eval_predicate("artifact and artifact.get('approved')", data, artifact={"approved": True})` works
- `eval_predicate("data > 5", 10)` works without artifact (artifact=None, not in namespace)
- `eval_switch("'approved' if data.verdict == 'approved' else 'rejected'", data_obj)` returns `"approved"` or `"rejected"`
- `eval_switch("data.next_step", SimpleNamespace(next_step="design"))` returns `"design"`
- `eval_switch("42", data)` raises `ExpressionEvalError` (non-string return)
- `eval_switch` with `artifact` available works: `eval_switch("'done' if artifact else 'pending'", data, artifact=some_value)`
- Expression errors include node_id and original traceback

**Counterexamples:**
- `collection` gets `ctx` not `data`
- `accumulator_init` gets NO variables
- Do NOT include `artifact` in expression context when it is `None` — omit from namespace entirely
- `eval_switch` must ALWAYS return `str` — raise `ExpressionEvalError` if not

**Requirement IDs:** R7 | **Journey IDs:** J-2, J-4

**Citations:**
- [code: SF-1 plan D-SF1-28 — `switch_function` receives `data`, returns port name string]
- [decision: C-1 — "Add exclusive switch-function routing to BranchNode execution"]

---

### STEP-10: Node Executors

**Objective:** `execute_ask_node`, `execute_branch_node`, `execute_plugin_node` — with dual artifact_key semantics (read before, auto-write after) and switch_function routing.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/executors.py` | modify |
| `iriai_compose/declarative/errors.py` | modify |
| `iriai_compose/storage.py` | read |
| `iriai_compose/runner.py` | read |

**Instructions:**

**Ask executor:**
1. **READ artifact_key:** If `node.artifact_key` is set, include it in context resolution keys. `ContextProvider.resolve()` fetches the existing value.
2. Build context keys list with priority order: `artifact_key` first (if set), then `node.context_keys`, then `actor.context_keys`. Deduplicate with `dict.fromkeys()`.
3. Resolve all context via `context_provider.resolve(all_keys, feature=feature)` — matching the existing imperative pattern in [code: iriai_compose/runner.py:237-248].
4. Render prompt template with `{{ $input }}` and `{{ ctx.key }}` substitution.
5. Assemble full prompt: `f"{context_str}\n\n## Task\n{rendered_prompt}"`.
6. Invoke runtime with role, prompt, output_type, workspace, session_key.
7. **WRITE artifact_key:** If `node.artifact_key` is set, auto-write the result to the store: `await artifacts.put(node.artifact_key, result, feature=feature)`. [D-SF1-29, C-4]
8. Return raw output.

**Branch executor:**
1. **READ artifact_key:** If `node.artifact_key` is set, resolve the existing artifact value from ArtifactStore via `artifacts.get(key, feature=feature)`.
2. Merge multi-input via `eval_merge(fn, inputs, artifact=artifact_value)` — passing resolved artifact.
3. **Route (dual strategy per D-SF1-28, C-1):**
   - If `node.switch_function` is set: `eval_switch(fn, merged, artifact=artifact_value)` → returns port name → only that port fires (exclusive).
   - If `node.switch_function` is NOT set: evaluate per-port conditions (non-exclusive) via `eval_predicate(condition, merged, artifact=artifact_value)`.
4. **WRITE artifact_key:** If `node.artifact_key` is set, auto-write the merged data to the store. [D-SF1-29, C-4]
5. Return `(port_fires, merged)`.

**Plugin executor:**
1. Resolve `plugin_ref` OR `instance_ref` using three-tier resolution (D-SF2-48, H-4):
   - Try concrete Plugin via `registry.get()` → direct `plugin.execute()`.
   - Try registered type via `registry.get_type()` → category dispatch.
   - Try workflow-declared type via `workflow.plugins` → category dispatch.
   - For `instance_ref`: resolve instance → type → category dispatch.
2. **READ artifact_key:** If `node.artifact_key` is set, resolve the existing artifact value.
3. Build `ExecutionContext` with `artifact=resolved_value` (or `None` if no artifact_key).
4. Call plugin execution (concrete or category-dispatched).
5. Handle `outputs: []` fire-and-forget case — node executes, returns `None`, no downstream data.
6. **WRITE artifact_key:** If `node.artifact_key` is set, auto-write the plugin's return value to the store. [D-SF1-29, C-4]

**Acceptance Criteria:**
- Ask: `artifact_key` value is included in prompt context (verify by checking MockAgentRuntime.calls)
- Ask: Calls `artifact_store.put()` with node output AFTER execution when `artifact_key` is set
- Ask: Does NOT call `artifact_store.put()` when `artifact_key` is not set
- Ask: Context ordering is `[artifact_key, ...context_keys, ...actor.context_keys]`
- Branch with `switch_function`: returns `(port_fires, merged)` with exactly one truthy port
- Branch with `switch_function`: invalid port name from function raises `ExpressionEvalError`
- Branch without `switch_function`: non-exclusive routing — multiple ports can fire
- Branch: `artifact` variable available in switch/condition/merge expressions
- Branch: `artifact_key` auto-writes merged data after execution
- Plugin: Three-tier resolution: concrete → type → workflow-declared
- Plugin: Category dispatch via `CategoryExecutor` when no concrete plugin found
- Plugin: `instance_ref` resolves through instance → type → category chain
- Plugin: `ExecutionContext.artifact` populated when `artifact_key` set
- Plugin: `outputs: []` executes without error, returns `None`
- Plugin: `artifact_key` auto-writes plugin output after execution

**Counterexamples:**
- Branch routing with `switch_function` IS mutually exclusive (only selected port fires)
- Branch routing without `switch_function` is NOT mutually exclusive (all truthy fire)
- `switch_function` and per-port `condition` must NOT coexist — SF-1 validator prevents this, but executor should assert defensively
- Auto-write happens AFTER execution, BEFORE port routing in `_execute_dag`
- Do NOT skip `artifact_key` resolution when the key doesn't exist in the store — `ContextProvider.resolve()` already handles missing keys gracefully

**Requirement IDs:** R7 | **Journey IDs:** J-2, J-8

**Citations:**
- [decision: C-1 — switch_function exclusive routing]
- [decision: C-4 — artifact_key auto-write semantics]
- [decision: H-4 — three-tier plugin resolution]
- [code: SF-1 plan D-SF1-28 — BranchNode.switch_function]
- [code: SF-1 plan D-SF1-29 — artifact_key auto-write]

---

### STEP-11: Phase Mode Executors + Unified `_execute_dag`

**Objective:** `_execute_dag`, all four modes, `_dispatch_element`, `_activate_outgoing_edges`, branch barrier, artifact_key dual read+write at all levels.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/modes.py` | modify |
| `iriai_compose/declarative/runner.py` | modify |
| `iriai_compose/declarative/graph.py` | read |

**Instructions:**

**Artifact_key integration in `_execute_dag`:**
After dispatching any element and obtaining its output:
1. **Auto-write:** If `element.artifact_key` is set, write the output to the store.
   - For BranchNodes: write the `merged` data (second element of the tuple), not `port_fires`.
   - For all other elements: write the output directly.
   - Auto-write happens BEFORE `_activate_outgoing_edges`.
2. The auto-write for nodes is handled inside the executor (STEP-10). The `_execute_dag` level handles auto-write for phases (since phases don't go through node executors).

**Phase artifact_key dual semantics [D-SF2-44]:**
- **READ at entry:** Before executing a phase's internal DAG, resolve the phase's `artifact_key` from the ArtifactStore. Add the resolved value to the phase-scoped context. Child elements inherit this through the context hierarchy.
- **WRITE at exit:** After phase execution completes, auto-write the phase output to the store at `artifact_key`.

**`_activate_outgoing_edges` — five models:**
1. **BranchNode with `switch_function`** + `port_fires`: Only selected port's edges fire. (Exclusive.) [D-SF1-28]
2. **BranchNode without `switch_function`** + `port_fires`: All truthy ports' edges fire. (Non-exclusive.)
3. **Ask/Plugin with conditions**: Mutually exclusive first-match.
4. **Loop exit**: `edge_matches_exit_path` selects edges.
5. **Default**: All outgoing edges fire.

Note: Models 1 and 2 use the same code path (fire edges for truthy ports in `port_fires` dict). The difference is in how `execute_branch_node` produces the dict.

**Map mode**: Respect `max_parallelism` — use `asyncio.Semaphore(max_parallelism)` when set, unlimited `asyncio.gather` when None. Auto-create unique actor instances per parallel execution.

**Fold mode**: `collection` receives `ctx` (resolved context keys + phase input). `accumulator_init` receives NO variables. `reducer` receives `accumulator` + `result`.

**Loop mode**: `exit_condition` receives `data` = iteration `$output`. `fresh_sessions` clears sessions before each iteration. Loop exit produces `("condition_met", output)` or `("max_exceeded", output)`.

**`_dispatch_element`**: Check `hasattr(element, 'mode')` → `execute_phase()`. Check `element.type` → node executor. BranchNode handled specially in `_execute_dag` (different return type).

**Acceptance Criteria:**
- Map with `max_parallelism=2` limits concurrent executions
- Fold `accumulator_init` with no variables
- PluginNode `outputs: []` → node executes, no edges fire, no error
- Loop auto-ports `condition_met`/`max_exceeded` read from validated model (not created by runner)
- Phase with `artifact_key="artifacts.prd"`:
  - Before execution: "prd" value resolved from store and added to phase context
  - After execution: phase output auto-written to store as "prd"
- Element `artifact_key` auto-write happens AFTER dispatch, BEFORE `_activate_outgoing_edges`
- Branch `switch_function` with 2 output ports → only selected port's edges fire
- Branch without `switch_function` with 2 conditional ports → both can fire

**Counterexamples:**
- No separate workflow loop — same `_execute_dag` at both levels
- `collection` gets `ctx` not `data`
- `accumulator_init` gets NO variables
- Don't create loop exit ports in the runner — read from validated model
- Phase `artifact_key` READ at entry, WRITE at exit — not either alone
- Do NOT resolve `artifact_key` per-iteration in fold/map — resolve once at phase entry (read), write once at phase exit

**Requirement IDs:** R7 | **Journey IDs:** J-2, J-6

---

### STEP-12: Actor Hydration

**Objective:** Bridge `ActorDefinition` to runtime. Handles `context_store`, `handover_key`, `persistent` per D-SF1-25.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/actors.py` | modify |
| `iriai_compose/actors.py` | read |

**Instructions:**
- Hydrate `ActorDefinition` → `AgentActor` or `InteractionActor` (matching iriai-compose's existing actor model in [code: iriai_compose/actors.py]).
- `AgentActor.context_keys` populated from `ActorDefinition.context_keys`. These are MERGED with node-level `artifact_key` and `context_keys` at execution time (per STEP-10).
- `persistent=True` (default) → reuse session across invocations.
- `persistent=False` → no session loading/saving.
- `context_store` → resolved from `workflow.stores`, used for actor-scoped context.
- `handover_key` → artifact key for session handover documents.

**Acceptance Criteria:**
- `ActorDefinition(type="agent", role=..., context_keys=["project"])` → `AgentActor` with matching context_keys
- `ActorDefinition(type="interaction", resolver="human")` → `InteractionActor`
- `persistent=False` actors don't load/save sessions

**Counterexamples:**
- Do NOT merge actor context_keys with node context_keys during hydration — merging happens at execution time in the executor

**Requirement IDs:** R7 | **Journey IDs:** J-2

---

### STEP-13: Hook Execution

**Objective:** Fire-and-forget hook edges. Hook identification: source port in `hooks` container per D-SF1-21. Enforce `transform_fn=None`.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/hooks.py` | modify |
| `iriai_compose/declarative/errors.py` | modify |

**Instructions:**
- Hook edges identified at graph-build time (STEP-8) by checking if source port is in the `hooks` container.
- Hook target executes with the triggering element's output as input.
- `transform_fn` must be `None` for hook edges — raise `HookEdgeError` if set.
- Fire-and-forget: hook failures are caught, logged as warnings, and stored in `ExecutionResult.hook_warnings`.
- Hook targets are typically PluginNodes (e.g., `artifact_write` to persist a result at a different key, or a notification plugin).

**Acceptance Criteria:**
- Hook edge from `ask_node.on_end` to `plugin_node.input` fires after ask_node completes
- Hook with `transform_fn` set raises `HookEdgeError` at graph-build time
- Hook failure does NOT abort workflow — warning stored in result
- Hook target receives the triggering element's output as input

**Counterexamples:**
- Hook failures must NOT raise — they are fire-and-forget
- Do NOT allow transforms on hook edges

**Requirement IDs:** R7 | **Journey IDs:** J-2, J-19

---

### STEP-14: Top-level `run()` Function

**Objective:** Input validation → `build_execution_graph` → `_execute_dag` → artifact tracking → output validation → `ExecutionResult`.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/runner.py` | modify |
| `iriai_compose/declarative/__init__.py` | modify |

**Instructions:**
1. Accept `WorkflowConfig | str | Path` — load if string/path (delegates to SF-1 via STEP-6 loader).
2. **Signature:** `async def run(workflow: WorkflowConfig | str | Path, config: RuntimeConfig, *, inputs: dict[str, Any] | None = None) -> ExecutionResult` [C-3].
3. Validate workflow inputs via `_validate_workflow_inputs`.
4. Initialize stores (InMemory defaults when None).
5. Initialize PluginRegistry (with builtins) if not provided.
6. Wrap the ArtifactStore with a `TrackingArtifactStore` that records all `put()` keys during execution — this captures both auto-writes from `artifact_key` AND explicit writes from `artifact_write` plugins.
7. Build `ExecutionGraph` from workflow (phases as elements, `is_workflow=True`).
8. Handle no-edges fallback: sequential execution, threading each element's output to next element's `phase_input`.
9. Execute via `_execute_dag` with `phase_input=validated_inputs`.
10. After execution, read back all tracked artifact keys from the store to populate `ExecutionResult.artifacts`.
11. Validate workflow outputs (warn only, never raise per D-SF2-31).
12. Return `ExecutionResult` with `workflow_output`, `artifacts`, `nodes_executed`, `branch_paths`, `cost_summary`, `hook_warnings`.

**`TrackingArtifactStore` implementation:**
```python
class TrackingArtifactStore(ArtifactStore):
    """Wraps an ArtifactStore to track put() calls for ExecutionResult.artifacts."""
    def __init__(self, inner: ArtifactStore):
        self._inner = inner
        self.written_keys: list[str] = []

    async def get(self, key, *, feature): return await self._inner.get(key, feature=feature)
    async def put(self, key, value, *, feature):
        self.written_keys.append(key)
        await self._inner.put(key, value, feature=feature)
    async def delete(self, key, *, feature): await self._inner.delete(key, feature=feature)
```

**Acceptance Criteria:**
- `run(yaml_path, config)` loads, validates, executes, returns `ExecutionResult`
- `run(workflow_config, config)` works with pre-loaded config
- `run(workflow, config, inputs={"project": "..."})` passes inputs correctly [C-3]
- Input validation raises `WorkflowInputError` for missing required inputs
- Output validation warns but does not raise
- `$input`/`$output` routing works at workflow level
- No-edges fallback executes phases sequentially
- `ExecutionResult.artifacts` contains all keys written by `artifact_key` auto-writes AND `artifact_write` plugins during execution
- Backward compat: workflows without `inputs`/`outputs` work (defaults to empty lists)
- `hook_warnings` populated when hook edges fail

**Counterexamples:**
- No separate workflow execution loop — reuses `_execute_dag`
- Don't raise on output validation
- Don't modify ArtifactStore ABC — use the TrackingArtifactStore wrapper
- Signature MUST be `run(workflow, config: RuntimeConfig, *, inputs=None)` — NOT any other ordering [C-3]

**Requirement IDs:** R7 | **Journey IDs:** J-2, J-8

**Citations:**
- [decision: C-3 — "Confirm your `run()` signature: `run(workflow, config: RuntimeConfig, *, inputs=None)`"]

---

### STEP-15: Update Public Exports

**Objective:** Wire all public API symbols into `iriai_compose/declarative/__init__.py`.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/__init__.py` | modify |

**Instructions:**
```python
from iriai_compose.declarative.runner import run
from iriai_compose.schema.yaml_io import load_workflow          # Re-export SF-1's loader
from iriai_compose.declarative.config import RuntimeConfig, load_runtime_config
from iriai_compose.declarative.plugins import (
    PluginRegistry, Plugin, ExecutionContext, CategoryExecutor, required_plugins,
)
from iriai_compose.declarative.runner import ExecutionResult
from iriai_compose.declarative.errors import (
    DeclarativeExecutionError, WorkflowLoadError, WorkflowInputError,
    ExpressionEvalError, PluginNotFoundError, PluginConfigError,
    CycleDetectedError, DuplicateElementError, HookEdgeError, DeadlockError,
)
```

**Acceptance Criteria:**
- All public API symbols importable from `iriai_compose.declarative`
- `load_workflow` is the SF-1 function, not a duplicate
- `CategoryExecutor` importable from `iriai_compose.declarative`
- No circular imports

**Requirement IDs:** R7 | **Journey IDs:** J-2

---

### STEP-16: Integration Tests

**Objective:** Comprehensive integration tests covering the unified engine, artifact dual read+write, switch_function routing, three-tier plugin dispatch, and all edge cases.

**Scope:**
| Path | Action |
|------|--------|
| `tests/declarative/__init__.py` | create |
| `tests/declarative/test_engine.py` | create |
| `tests/declarative/test_branch.py` | create |
| `tests/declarative/test_workflow_io.py` | create |
| `tests/declarative/test_expressions.py` | create |
| `tests/declarative/test_cross_boundary.py` | create |
| `tests/declarative/test_hooks.py` | create |
| `tests/declarative/test_artifact_flow.py` | create |
| `tests/declarative/test_plugins.py` | create |
| `tests/declarative/fixtures/` | create |

**Instructions:**

**Unified engine tests (5):**
1. Single-phase sequential: Ask → Ask → $output
2. Multi-phase workflow: phase_A → phase_B → phase_C
3. Nested phases: phase containing sub-phase
4. No-edges fallback: phases without edges execute sequentially
5. Workflow-level edges route between phases

**Source port resolution tests (4):**
1. Dict output + named port → extracts key
2. "output" port → returns full data
3. Non-dict output + named port → returns as-is
4. Phase-level source port resolution

**Branch tests (14):**
1. Single-input single-output passthrough
2. Multi-input gather with merge_function
3. Non-exclusive output routing (multiple ports fire) — no switch_function
4. Branch barrier defers until all inputs ready
5. Degenerate: 1 input, 1 unconditional output
6. Branch with `artifact_key` — artifact available in conditions
7. No-match warning when no conditions are truthy (no switch_function)
8. Barrier deadlock detection (2× safety cap)
9. Conditionally-skipped branch source → DeadlockError
10. Merge with `artifact` variable in expression
11. **switch_function: exclusive routing — only selected port fires** [C-1]
12. **switch_function: invalid port name returns ExpressionEvalError** [C-1]
13. **switch_function: with artifact variable available** [C-1]
14. **switch_function with single output port — must return that port's name** [C-1]

**Workflow I/O tests (13):**
1. Required input present → validates
2. Required input missing → WorkflowInputError
3. Optional input with default → applied
4. Type checking on inputs
5. Output validation warns on missing
6. Output validation warns on type mismatch
7. Output validation never raises
8. $input routing at workflow level
9. $output routing at workflow level
10. No inputs/outputs → backward compat
11. Multi-output workflow
12. Input type via schema_def (not type_ref)
13. Default value applied for optional input

**Expression context tests (5):**
1. `collection` receives `ctx` (not `data`)
2. `accumulator_init` receives NO variables
3. `reducer` receives `accumulator` + `result`
4. Port conditions receive `data` + `artifact` (when set)
5. **`switch_function` receives `data` + `artifact` (when set), returns `str`** [C-1]

**Cross-boundary tests (11):**
1. Node → phase edge
2. Phase → node edge
3. Node → node (same phase)
4. Phase → phase (workflow level)
5. Node inside phase → $output
6. $input → node inside phase
7. Nested phase boundaries
8. Cross-boundary with transform
9. Phase exit routing with conditions
10. Loop exit across boundary
11. Hook edge across boundary

**Hook edge tests (2):**
1. Hook fires after element completes, target receives output
2. Hook failure doesn't abort workflow

**Artifact flow tests (12):**
1. `artifact_key` on AskNode → existing artifact content included in prompt (verify via MockAgentRuntime.calls)
2. `artifact_key` on AskNode with missing artifact → gracefully skipped (empty string for that key)
3. **`artifact_key` on AskNode → output auto-written to store after execution (verify via `store.get()`)** [C-4]
4. `artifact_write` plugin → data persisted to ArtifactStore at a DIFFERENT key (verify via `store.get()`)
5. `artifact_write` plugin → returns input unchanged (pass-through verified)
6. **End-to-end: AskNode(artifact_key=X) auto-writes output → downstream AskNode(artifact_key=X) reads it back from store** [C-4]
7. `artifact_key` on BranchNode → `artifact` variable available in condition/switch expressions
8. **`artifact_key` on BranchNode → merged data auto-written to store** [C-4]
9. `artifact_key` on PluginNode → `context.artifact` populated AND output auto-written
10. **Phase `artifact_key` → child nodes inherit via context (READ) + phase output auto-written (WRITE)** [C-4]
11. `artifact_write` without `key` config → `PluginConfigError`
12. **`ExecutionResult.artifacts` contains all auto-written AND plugin-written keys** [C-4]

**Plugin tests (7):**
1. `PluginRegistry()` auto-registers `artifact_write`
2. `artifact_write` plugin end-to-end (write to different key + verify store)
3. Fire-and-forget PluginNode (`outputs: []`) executes without error
4. `instance_ref` resolves from `workflow.plugin_instances`
5. **Concrete Plugin registration and dispatch** [H-4]
6. **Type registration + category executor dispatch** [H-4]
7. **Instance registration → type resolution → category dispatch** [H-4]

**Map parallelism tests (2):**
1. `max_parallelism=2` limits concurrent executions
2. `max_parallelism=None` → unlimited

**Key test group: Switch function end-to-end (test 11 detail):**
```python
# Workflow: gather_node (Branch, switch_function="'approved' if data.approved else 'rejected'")
#   with outputs: [approved, rejected]
#   → approved_handler (Ask)
#   → rejected_handler (Ask)
# 1. Input with approved=True → only approved_handler executes
# 2. Input with approved=False → only rejected_handler executes
# 3. Verify branch_paths in ExecutionResult
```

**Key test group: Artifact auto-write end-to-end (test 6 detail):**
```python
# Workflow: scope_task (Ask, artifact_key=artifacts.scope) → design_task (Ask, artifact_key=artifacts.design, context_keys=[artifacts.scope])
# 1. Run workflow
# 2. Assert scope_task output auto-written to store as "artifacts.scope"
# 3. Assert design_task prompt includes scope content (read from store)
# 4. Assert design_task output auto-written to store as "artifacts.design"
# 5. Assert ExecutionResult.artifacts contains both "artifacts.scope" and "artifacts.design"
```

**Requirement IDs:** R7, R8 | **Journey IDs:** J-2, J-7, J-8

---

## iriai-build-v2 Pattern Verification

| # | Pattern | Status | Notes |
|---|---------|--------|-------|
| 1 | broad_interview | **WORKS** | Loop + Ask. `artifact_key` reads existing context AND auto-writes output. Resume OUT OF SCOPE. |
| 2 | gate_and_revise | **WORKS** | Loop + AskNode first-match conditions + hook edges (D-SF2-33). Gate verdict auto-written via `artifact_key`. Revision Ask reads verdict via `artifact_key`. |
| 3 | per_subfeature_loop | **WORKS** | Fold + `ctx` collection + cross-boundary edges. `artifact_key` auto-writes each iteration's output. Static keys only in SF-2 (dynamic keys = future). Resume OUT OF SCOPE. |
| 4 | parallel execution | **WORKS** | Map + `max_parallelism` (D-SF2-38) + unique actor names. Each parallel branch's nodes auto-write via `artifact_key`. |
| 5 | DAG execution groups | **WORKS** | Fold > Map nesting + handover accumulator. `artifact_key` on fold phase auto-writes final accumulator. |
| 6 | interview_gate_review | **WORKS** | Loop + mixed nodes/phases + `fresh_sessions` + hook edges. Each loop iteration's Ask reads prior artifacts via `artifact_key` context injection. |
| 7 | integration_review | **WORKS** | BranchNode 3-input gather + merge_function + barrier. `artifact` variable available in merge expression via `artifact_key`. |
| 8 | HostedInterview | **WORKS** | AskNode with `artifact_key` auto-writes output to store. `on_end` hook → doc_hosting Plugin reads from store and pushes to hosting. |
| 9 | Session management | **WORKS** | `fresh_sessions` on LoopConfig/FoldConfig |
| 10 | Resume/checkpoint | **OUT OF SCOPE** | Future: pre-dispatch hook |
| 11 | Parameterized workflows | **WORKS** | `inputs`/`outputs` on WorkflowConfig [SF-2] |
| 12 | Notification fan-out | **WORKS** | BranchNode non-exclusive conditions (no switch_function) |
| 13 | Ambient services | **WORKS** | `ExecutionContext.services` |
| 14 | Nested impl DAG | **WORKS** | Plugin node with runner access (D-SF2-34) |
| 15 | Cost tracking | **WORKS** | Greenfield. CostConfig. |
| 16 | Fire-and-forget plugins | **WORKS** | `outputs: []` on PluginNode (D-SF2-37) |
| 17 | Artifact persistence | **WORKS** | `artifact_key` auto-write replaces most explicit `artifacts.put()` calls. `artifact_write` plugin for different-key writes. |
| 18 | Programmatic routing | **WORKS** | BranchNode `switch_function` for exclusive routing [D-SF1-28, C-1]. Verdict-based if/else maps directly. |
| 19 | Category-based plugins | **WORKS** | PluginRegistry `register_type()` + `register_category_executor()` enable MCP/CLI/service dispatch [H-4]. |

**Pattern note on artifact flow (simplified by auto-write):** In the imperative API, a typical pattern is:
```python
result = await runner.run(Ask(actor=pm, prompt="Write PRD"), feature)
await runner.artifacts.put("prd", result, feature=feature)
```

In the declarative API, this becomes just:
```yaml
- id: pm_write
  type: ask
  actor: pm
  prompt: "Write the PRD"
  artifact_key: artifacts.prd     # AUTO-READ existing value + AUTO-WRITE output
  context_keys: [artifacts.project]  # READ: includes "project" context
```

No separate `artifact_write` PluginNode needed. The `artifact_key` field handles both context injection AND persistence — matching iriai-build-v2's pattern where the same key is used for both `artifacts.get()` (context) and `artifacts.put()` (persist).

**Pattern note on switch_function:** In iriai-build-v2, verdict-based routing like:
```python
if verdict.approved:
    # ... approved path
else:
    # ... rejected path
```

Maps to:
```yaml
- id: review_gate
  type: branch
  switch_function: "'approved' if data.approved else 'rejected'"
  outputs:
    - name: approved
    - name: rejected
```

This is cleaner than per-port conditions for binary decisions and directly represents the D-28 design decision ("Branch = programmatic switch").

---

## Interfaces to Other Subfeatures

### SF-1 → SF-2
**Contract:** All entity types per Schema Entity Reference section above. Runner depends on SF-1 validators (`_fix_input_ports`, `_validate_branch_ports`, loop auto-ports, `invalid_switch_function_config`). **SF-2 schema additions** (must be upstreamed): `WorkflowInputDefinition`, `WorkflowOutputDefinition`, `inputs`/`outputs` on `WorkflowConfig`.

**Loader delegation [H-1]:** SF-2's `loader.py` imports `load_workflow` from `iriai_compose.schema.yaml_io`. No duplication of YAML parsing or Pydantic validation.

**`artifact_key` semantic alignment [D-SF1-29, C-4]:** SF-1 defines `artifact_key` as a field on NodeBase and PhaseDefinition. SF-2 implements dual read+write semantics: read existing value for context injection before execution, auto-write output after execution. SF-1 schema validation does not need to change.

**`switch_function` alignment [D-SF1-28, C-1]:** SF-1 defines `switch_function` on BranchNode with `invalid_switch_function_config` validation error. SF-2 implements exclusive routing via `eval_switch()`.

### SF-2 → SF-3
**Contract:** `run`, `load_workflow` (re-exported from SF-1), `RuntimeConfig`, `ExecutionResult` (with `workflow_output`, `hook_warnings`), `WorkflowInputError`, `ArtifactWritePlugin`, `CategoryExecutor`.

**Testing note:** SF-3's `assert_artifact(result, key="prd", matches=...)` works by checking `ExecutionResult.artifacts` dict, which is populated from the ArtifactStore after execution. With auto-write, test workflows that have `artifact_key` set will automatically populate the artifact store — no separate `artifact_write` PluginNodes needed in most test cases.

### SF-2 → SF-4
**Contract:** `run()` executes migrated YAML. `PluginRegistry.register()` for concrete plugins. `PluginRegistry.register_type()` for MCP/CLI/service plugins. `artifact_key` auto-write eliminates most explicit `artifact_write` nodes. Resume NOT supported.

**Migration note (simplified by auto-write):** SF-4 migrated workflows can use `artifact_key` wherever iriai-build-v2 has `runner.artifacts.put()` after a task. Most intermediate `artifact_write` PluginNodes from the previous plan are now unnecessary. SF-4 should only use `artifact_write` when writing to a key DIFFERENT from the node's `artifact_key`.

**Migration note (switch_function):** iriai-build-v2's verdict-based routing (if/else control flow) maps to `switch_function` on BranchNode for binary decisions. Per-port conditions are better for fan-out patterns. SF-4 should use `switch_function` for gate approvals and `per-port conditions` for parallel dispatch.

### SF-2 → SF-5
**Contract:** SF-5 provides store implementations. May expose workflow inputs/outputs in UI.

**UI note:** The `artifact_key` field on node cards identifies both what the node READS and WRITES. SF-5/SF-6 UI may want to show `artifact_key` with a bidirectional indicator (↕ or similar) to convey dual semantics.

---

## Architectural Risks

| ID | Description | Severity | Mitigation | Steps |
|----|-------------|----------|------------|-------|
| RISK-6 | SF-1 schema not finalized | high | Start with STEP-5-3 | 2,6,7 |
| RISK-7 | Full exec() trust | medium | Author = operator | 5 |
| RISK-8 | Nested recursion | low | Max ~3 levels | 7 |
| RISK-9 | Plugin discovery | low | try/except | 3 |
| RISK-10 | Branch merge malformed | medium | Port-keyed dict guaranteed | 5,6 |
| RISK-11 | eval_predicate trust | medium | Same as transforms | 5,6 |
| RISK-12 | Map actor collision | high | `_make_parallel_actors()` | 7 |
| RISK-13 | fresh_sessions custom stores | low | Logs warning | 7 |
| RISK-14 | Hook failures invisible | medium | `hook_warnings` on result | 8,9 |
| RISK-15 | Missing InteractionRuntime | medium | Pre-flight validation | 7.5,9 |
| RISK-16 | Plugin version conflicts | low | Future: constraints | 3 |
| RISK-17 | iriai-build-v2 drift | low | Pattern verification + SF-4 | All |
| RISK-18 | Undeclared stores | low | Auto-create InMemory | 9 |
| RISK-19 | Missing store dot-notation | medium | Raise | 6 |
| RISK-20 | Branch-skipped elements | medium | `fired_edges` | 7,9 |
| RISK-21 | Loop exit routing | medium | `edge_matches_exit_path` | 7,9 |
| RISK-22 | Workflow cycles | low | Kahn's raises | 4 |
| RISK-23 | Missing port data | medium | Returns None | 7 |
| RISK-24 | `$input` targets non-entry | low | `$input` wins | 7 |
| RISK-25 | Element ID collision | medium | Raise on duplicate | 4 |
| RISK-26 | Loop exit tuple leaks | medium | Unwrap before storing | 7 |
| RISK-27 | Branch barrier deadlock | medium | 2× safety cap → DeadlockError | 7 |
| RISK-28 | Non-exclusive sequential fan-out | medium | Topo order, no parallelism | 6,7 |
| RISK-29 | Input type needs jsonschema | low | Pydantic dep or skip | 9 |
| RISK-30 | `$input` undeclared port | low | Validation | 4,9 |
| RISK-31 | SF-1 schema additions | medium | Additive only | 2 |
| RISK-32 | Conditionally-skipped branch source | medium | DeadlockError + validation warning | 7 |
| RISK-33 | Source port on non-dict | low | Returns as-is | 4,7 |
| RISK-34 | Output validation blocks results | low | Warns only | 9 |
| RISK-35 | `is_workflow` flag smell | low | Two thin wrappers alt. | 4 |
| RISK-36 | Source port vs exit path dual semantics | medium | Documented | 4,7 |
| RISK-37 | Resume blocks SF-4 litmus | medium | Correctness not efficiency | 9 |
| RISK-38 | `collection` wrong context | medium | Must pass `ctx` | 7 |
| RISK-39 | `accumulator_init` variables | low | Empty namespace | 7 |
| RISK-40 | PluginNode `outputs: []` confuses routing | low | No outgoing edges, no-op in `_activate_outgoing_edges` | 6,7 |
| RISK-41 | `instance_ref` ignored in plugin executor | medium | Three-tier resolution: concrete → type → instance → category | 6 |
| RISK-42 | `max_parallelism` ignored in Map | medium | Must use Semaphore when set | 7 |
| RISK-43 | `artifact_key` auto-write may cause unexpected store writes | low | Same risk as explicit `artifacts.put()` in iriai-build-v2. Store key `type_ref` validation catches type mismatches. [D-SF1-29] | 6,7,9 |
| RISK-44 | Auto-write overwrites existing artifact value | medium | Intentional — same as imperative `artifacts.put()` with `ON CONFLICT UPDATE`. If overwrites are problematic, use `artifact_write` with a versioned key instead. | 6,11 |
| RISK-45 | Dynamic artifact keys not supported in SF-2 | low | Static keys cover most patterns. Dynamic keys (per-iteration in folds) require custom plugin. Document as future enhancement. | 3,6 |
| RISK-46 | `artifact_key` resolution latency | low | `ContextProvider.resolve()` is async and may hit external storage. Single resolution per element (not per-retry). Cache within execution if needed. | 6,7 |
| RISK-47 | Tracking artifact writes for ExecutionResult | low | TrackingArtifactStore wrapper records `put()` keys from BOTH auto-writes and explicit plugin writes. | 9 |
| RISK-48 | `switch_function` returning unknown port name | medium | Runtime error with clear diagnostic: "switch_function returned 'X' but available ports are [...]". SF-1 validation ensures at least 1 output port. [D-SF1-28, RISK-20 in SF-1] | 5,6 |
| RISK-49 | `switch_function` and per-port conditions both present | low | SF-1 validator produces `invalid_switch_function_config` error. Runner asserts defensively. | 6 |
| RISK-50 | Category executor not registered for plugin's category | medium | `PluginNotFoundError` with "No category executor registered for 'mcp'" message. Clear action: register executor. | 3,6 |
| RISK-51 | Loader duplication if SF-1 changes `load_workflow` signature | low | SF-2's loader is a thin wrapper — changes propagate automatically. Runtime validation is additive. | 2 |

---

## New Dependencies

| Package | Version | Purpose | Added to |
|---------|---------|---------|----------|
| pyyaml | >=6.0,<7.0 | YAML parsing | `pyproject.toml` dependencies |

---


---

---

---

## SF-3: Testing Framework
<!-- SF: testing-framework -->

### Architecture

# Technical Plan: SF-3 Testing Framework (Cycle 4 ABI-Aligned Revision)

## Architecture

### Revision Focus

This revision applies the Cycle 4 canonical ABI decision: SF-2 is the sole runtime contract owner. The previous plan's D-SF3-16 (node routing via an explicit `node_id` kwarg on `AgentRuntime.invoke()`) violated that contract and is replaced. The previous D-SF3-18 (`RuntimeConfig.history` as resume carrier for `run_test()`) is dropped because checkpoint/resume is removed from SF-2's core runtime scope. SF-3 is a pure consumer: it reads SF-2's published ContextVar, it calls `run()` with the unchanged signature, and it derives observability from `ExecutionResult` and `ExecutionHistory` as published outputs.

### Decision Log

| ID | Decision | Rationale | Citation |
|----|----------|-----------|----------|
| D-SF3-1 | Assertion API remains standalone functions | Matches existing `pytest` style in `iriai-compose/tests/` and keeps the testing surface lightweight. | [code: iriai-compose/tests/conftest.py:1-73] |
| D-SF3-2 | MockRuntime keeps the fluent no-arg builder API | PRD R17 AC-2 prohibits dict constructors; node-specific routing reads from ContextVar, not from an invoke() kwarg. | [code: iriai-compose/tests/conftest.py:21-54], [research: .iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:32] |
| D-SF3-12 | Canonical `run()` signature is `run(workflow, config, *, inputs=None)` | SF-2 PRD and plan both make `RuntimeConfig` the single runtime bundle; `run()` has no `history=` kwarg. | [research: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/plan.md:60], [research: .iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:34] |
| D-SF3-15 | `ExecutionResult` stays data-only; SF-3 computes helper views locally | `node_ids()` / `node_index()` are test-only conveniences that must not widen the production contract. | [code: iriai-compose/iriai_compose/runner.py:36-50] |
| D-SF3-16 | Node identity propagated via SF-2-published `current_node_var: ContextVar[str]`; `AgentRuntime.invoke()` stays unchanged | Adding `node_id` to the ABC is a breaking change. SF-2 already uses ContextVar for phase tracking (`_current_phase_var`); the same pattern is used for node identity. MockRuntime reads the ContextVar inside `invoke()` — the ABC stays clean. | [code: iriai-compose/iriai_compose/runner.py:33], [research: .iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:21-23] |
| D-SF3-17 | Canonical execution-trace fields are `nodes_executed: list[tuple[str, str]]` in `(phase_id, node_id)` order and `branch_paths: dict[str, str]` | Matches SF-2 plan-level dataclass; fixes tuple destructuring in `assert_phase_executed()`. | [research: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/plan.md:60-68] |
| D-SF3-19 | Checkpoint/resume is not in SF-2's core runtime scope; `run_test()` does not accept `history=`; `RuntimeConfig` has no `history` field in this cycle | R17 PRD AC-4 and REQ-3 explicitly prohibit a built-in checkpoint/resume contract or `history=` `run()` kwarg. `ExecutionResult.history` remains as an observability output only. | [research: .iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:34] |
| D-SF3-20 | `MockRuntime` API uses `when_node(node_id)` and `when_role(role_name)` as separate fluent entry points | R17 PRD data entity defines `when_node()` and `when_role()` as distinct methods; this avoids the ambiguous combined `when(node_id=..., role=...)` pattern and aligns to the published PRD entity model. | [research: .iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:81-94] |

### Verified Cross-Subfeature Contract

**SF-1 provides** schema models, YAML I/O, and validation functions consumed directly by SF-3.

**SF-2 must provide** the following exact runtime interface for SF-3 to target:

```python
# iriai_compose/declarative/__init__.py — published ABI
async def run(
    workflow: WorkflowConfig | str | Path,
    config: RuntimeConfig,
    *,
    inputs: dict[str, Any] | None = None,
) -> ExecutionResult: ...
```

```python
# iriai_compose/declarative/runner.py — runner-owned ContextVar, exported from declarative.__init__
from contextvars import ContextVar
current_node_var: ContextVar[str] = ContextVar("_current_node", default="")
```

```python
@dataclass
class RuntimeConfig:
    agent_runtime: AgentRuntime
    interaction_runtimes: dict[str, InteractionRuntime] = field(default_factory=dict)
    artifacts: ArtifactStore | None = None
    sessions: SessionStore | None = None
    context_provider: ContextProvider | None = None
    plugin_registry: PluginRegistry | None = None
    workspace: Workspace | None = None
    feature: Feature | None = None
    # NOTE: no history field — checkpoint/resume is not in SF-2's core runtime scope
```

```python
@dataclass
class ExecutionResult:
    success: bool
    error: ExecutionError | None = None
    nodes_executed: list[tuple[str, str]] = field(default_factory=list)  # (phase_id, node_id)
    artifacts: dict[str, Any] = field(default_factory=dict)
    branch_paths: dict[str, str] = field(default_factory=dict)
    cost_summary: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    workflow_output: dict[str, Any] | Any = None
    hook_warnings: list[str] = field(default_factory=list)
    history: ExecutionHistory | None = None   # observability output only, not a resume input
    errors_routed: list[ErrorRoute] = field(default_factory=list)
```

```python
# AgentRuntime — UNCHANGED from iriai_compose/runner.py:36-50
class AgentRuntime(ABC):
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

### Context Merge Order

SF-2 assembles hierarchical context in `workflow → phase → actor → node` order before calling `invoke()`. Keys are collected in this order, then deduplicated with `dict.fromkeys()` (first-wins, preserving the order most foundational context appears first in the prompt). SF-3 assertion helpers and prompt-aware mock handlers must assume this merge order when inspecting prompts.

### Implications for SF-3 Modules

- `iriai_compose/testing/mock_runtime.py` must NOT add `node_id` to `invoke()`. It must import `current_node_var` from `iriai_compose.declarative` and call `.get("")` inside `invoke()` to obtain the current node identity.
- `iriai_compose/testing/assertions.py` must not call `ExecutionResult.node_ids()` or `ExecutionResult.node_index()`. It must treat every `nodes_executed` entry as `(phase_id, node_id)` and read branch outcomes from `branch_paths`.
- `iriai_compose/testing/runner.py` must build `RuntimeConfig(agent_runtime=runtime, ...)` with no `history` field and call `run(workflow, config, inputs=inputs)` — no `history=` kwarg.
- SF-3 tests must verify `branch_paths`, not `branch_paths_taken`.


### Implementation Steps

#### STEP-17: Add the `testing` optional dependency group to `iriai-compose/pyproject.toml` and scaffold the `iriai-compose/iriai_compose/testing/` package so the new testing surface is importable before the concrete implementations land. This keeps the package layout stable for later steps and isolates the testing extras from the production dependency set.
<!-- SF: testing-framework | Original: STEP-1 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-compose/pyproject.toml` | modify |
| `iriai-compose/iriai_compose/testing/__init__.py` | create |
| `iriai-compose/iriai_compose/testing/mock_runtime.py` | create |
| `iriai-compose/iriai_compose/testing/fixtures.py` | create |
| `iriai-compose/iriai_compose/testing/assertions.py` | create |
| `iriai-compose/iriai_compose/testing/snapshot.py` | create |
| `iriai-compose/iriai_compose/testing/runner.py` | create |
| `iriai-compose/iriai_compose/testing/validation.py` | create |
| `iriai-compose/tests/conftest.py` | read |

**Instructions:**

1. In `iriai-compose/pyproject.toml`, add a `testing` extras group under `[project.optional-dependencies]` with `pytest>=7.0` and `pytest-asyncio>=0.23`. Do not duplicate core dependencies there.
2. Create `iriai-compose/iriai_compose/testing/__init__.py` with a package docstring describing the new test support surface and a temporary placeholder comment indicating later steps will populate the exports.
3. Create the six implementation modules as importable stubs with only module docstrings and `# implemented in STEP-N` comments.
4. Verify editable install and imports work before any SF-1/SF-2 dependent symbols are imported.

**Acceptance Criteria:**

- `pip install -e "iriai-compose[testing]"` succeeds from the repo root
- `python -c "import iriai_compose.testing"` succeeds
- `python -c "import iriai_compose.testing.mock_runtime"` succeeds before SF-1/SF-2 dependent code is added
- Existing `iriai-compose/tests/` imports remain unchanged

**Counterexamples:**

- Do NOT add `pyyaml` to the testing extras; SF-2 owns that core dependency
- Do NOT import schema or declarative symbols in the stub step
- Do NOT modify `iriai-compose/tests/conftest.py`

**Requirement IDs:** REQ-3

**Journey IDs:** J-1, J-2

**Citations:**

- **[code]** `iriai-compose/pyproject.toml:1-20`
  - Excerpt: [project.optional-dependencies] already defines terminal and dev groups
  - Reasoning: The testing extras should follow the existing packaging pattern in the repo.
- **[code]** `iriai-compose/tests/conftest.py:1-73`
  - Excerpt: Existing test mocks live in the repo-level test support module today
  - Reasoning: The new testing package should supplement, not disturb, the current test harness.

#### STEP-18: Implement `MockRuntime` and `MockInteraction` as first-class test doubles for declarative workflow execution. This step consumes SF-2's published `current_node_var` ContextVar for node identity rather than adding a breaking `node_id` kwarg to `AgentRuntime.invoke()`, keeping the ABC unchanged and making node-specific routing deterministic.
<!-- SF: testing-framework | Original: STEP-2 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-compose/iriai_compose/testing/mock_runtime.py` | modify |
| `iriai-compose/iriai_compose/testing/__init__.py` | modify |
| `iriai-compose/iriai_compose/runner.py` | read |
| `iriai-compose/iriai_compose/actors.py` | read |
| `iriai-compose/iriai_compose/pending.py` | read |
| `iriai-compose/tests/conftest.py` | read |

**Instructions:**

1. At the top of `iriai-compose/iriai_compose/testing/mock_runtime.py`, import `current_node_var` from `iriai_compose.declarative` — this is SF-2's published runner-owned ContextVar. Do not declare a competing ContextVar in SF-3.
2. Implement a private `_WhenNodeClause` builder with `__slots__ = ("_mock", "_node_id")`, and a `_WhenRoleClause` builder with `__slots__ = ("_mock", "_role")`. Both expose a `.respond(value)` method that registers a routing rule on the parent `MockRuntime` and returns the parent instance.
3. Implement `MockRuntime(AgentRuntime)` with a zero-argument constructor and three fluent entry points:
   - `.when_node(node_id: str) -> _WhenNodeClause` — exact node match regardless of role
   - `.when_role(role_name: str) -> _WhenRoleClause` — role-only fallback when no node rule matches
   - `.default_response(value) -> MockRuntime` — fallback when neither node nor role rule matches
4. Implement `invoke()` with the **exact unchanged ABC signature**: `async def invoke(self, role, prompt, *, output_type=None, workspace=None, session_key=None) -> str | BaseModel`. Inside the body, read the current node identity with `node_id = current_node_var.get("")`. Apply routing priority: exact node_id match first, then role-name fallback, then handler, then default response.
5. Record every call as a `MockCall` dict containing: `node_id` (read from ContextVar), `role`, `prompt`, `output_type`, `workspace`, `session_key`, and `matched` (which rule matched).
6. Implement `MockInteraction(InteractionRuntime)` by lifting behavior from `iriai-compose/tests/conftest.py:57-79` into the package module. Keep canned `approve`, `choose`, and `respond` behaviors and record all `Pending` instances on `self.calls`.
7. Re-export `MockRuntime` and `MockInteraction` from `iriai-compose/iriai_compose/testing/__init__.py`.

**Acceptance Criteria:**

- `from iriai_compose.testing import MockRuntime, MockInteraction` works
- `MockRuntime().when_node("ask_1").respond("x")` returns `"x"` when the runner sets `current_node_var` to `"ask_1"` before calling `invoke()`
- `MockRuntime().when_role("pm").respond("y")` matches any node whose node_id has no explicit node rule registered for the pm role
- `MockRuntime().calls[-1]["node_id"]` contains the value read from `current_node_var` for every invocation, not a value passed as a kwarg
- `MockInteraction(approve=False).resolve(Pending(kind="approve", ...))` returns `False`
- `MockRuntime.invoke()` signature matches the unchanged `AgentRuntime` ABC exactly — no `node_id` parameter

**Counterexamples:**

- Do NOT add `node_id` as a keyword argument to `MockRuntime.invoke()` or any subclass of `AgentRuntime`
- Do NOT declare a new ContextVar in SF-3; import `current_node_var` from `iriai_compose.declarative`
- Do NOT accept a dict-based `responses=` constructor argument
- Do NOT use the combined `when(node_id=..., role=...)` API; the separate `when_node()` / `when_role()` entry points are the correct interface
- Do NOT modify `iriai-compose/iriai_compose/runner.py` in this step

**Requirement IDs:** REQ-1, REQ-3

**Journey IDs:** J-1

**Citations:**

- **[code]** `iriai-compose/iriai_compose/runner.py:33`
  - Excerpt: _current_phase_var: ContextVar[str] = ContextVar("_current_phase", default="")
  - Reasoning: SF-2 already uses the ContextVar pattern for phase tracking; the node identity ContextVar follows the same design published by SF-2 alongside it.
- **[code]** `iriai-compose/iriai_compose/runner.py:42-50`
  - Excerpt: AgentRuntime.invoke() has no node_id parameter — the ABC must stay unchanged
  - Reasoning: Reading from ContextVar inside invoke() is the only way to add node awareness without a breaking ABC change.
- **[code]** `iriai-compose/tests/conftest.py:21-54`
  - Excerpt: Existing MockAgentRuntime records calls and returns canned responses without a node_id kwarg
  - Reasoning: The new package-level mock extends this pattern with ContextVar-based routing.
- **[research]** `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:21-23`
  - Excerpt: REQ-1: MockAgentRuntime must keep the fluent no-argument API and perform node-specific matching from the current-node ContextVar published by SF-2 rather than from a changed invoke(node_id=...) signature.
  - Reasoning: The R17 PRD is the authoritative source for this contract change.

#### STEP-19: Implement the fluent `WorkflowBuilder` and convenience factories for building valid declarative workflows in tests. The builder stays aligned with SF-1's schema primitives so fixtures created here are valid inputs to both loader and runner tests.
<!-- SF: testing-framework | Original: STEP-3 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-compose/iriai_compose/testing/fixtures.py` | modify |
| `iriai-compose/iriai_compose/testing/__init__.py` | modify |
| `iriai-compose/iriai_compose/schema/base.py` | read |
| `iriai-compose/iriai_compose/schema/nodes.py` | read |
| `iriai-compose/iriai_compose/schema/edges.py` | read |
| `iriai-compose/iriai_compose/schema/phases.py` | read |
| `iriai-compose/iriai_compose/schema/actors.py` | read |
| `iriai-compose/iriai_compose/schema/stores.py` | read |
| `iriai-compose/iriai_compose/schema/workflow.py` | read |

**Instructions:**

1. Implement `WorkflowBuilder` in `iriai-compose/iriai_compose/testing/fixtures.py` as a fluent builder around SF-1's schema models. Keep `add_phase()`, `add_ask_node()`, `add_branch_node()`, `add_plugin_node()`, `add_edge()`, `add_store()`, `add_actor()`, `add_type()`, `set_context()`, and `build()`.
2. Auto-create a sequential phase and a minimal agent actor when a node references them before explicit declaration.
3. Convert string lists for inputs/outputs into `PortDefinition` instances, and keep all stored edges as the single SF-1 `Edge` type.
4. Add `minimal_ask_workflow()`, `minimal_branch_workflow()`, and `minimal_plugin_workflow()` helpers and re-export them from `__init__.py`.

**Acceptance Criteria:**

- `WorkflowBuilder().add_ask_node("n1", phase="main", actor="pm", prompt="x").build()` returns a valid `WorkflowConfig`
- `minimal_ask_workflow()` produces one sequential phase and one ask node
- `minimal_branch_workflow()` produces an ask -> branch -> ask/ask graph with branch port conditions
- `minimal_plugin_workflow()` produces an ask -> plugin graph

**Counterexamples:**

- Do NOT call `validate_workflow()` from `build()`; rely on model construction here
- Do NOT introduce non-schema node types or hook-specific edge classes
- Do NOT require callers to pass boilerplate actors or phases for simple fixtures

**Requirement IDs:** REQ-3

**Journey IDs:** J-1

**Citations:**

- **[decision]** `D-SF3-1`
  - Reasoning: The builder exists to keep tests concise while still producing real schema objects.
- **[research]** `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:80-94`
  - Excerpt: MockAgentRuntime, WorkflowBuilder, and minimal_* factories appear as core data entities in the R17 PRD.
  - Reasoning: Fixture generation must stay pinned to the actual declarative schema.

#### STEP-20: Expose SF-1 validation APIs through `iriai_compose.testing` and provide a test-specific `assert_validation_error()` helper. This keeps structural validation in one implementation while making it ergonomic to use from SF-3 and SF-4 tests.
<!-- SF: testing-framework | Original: STEP-4 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-compose/iriai_compose/testing/validation.py` | modify |
| `iriai-compose/iriai_compose/testing/assertions.py` | modify |
| `iriai-compose/iriai_compose/testing/__init__.py` | modify |
| `iriai-compose/iriai_compose/schema/validation.py` | read |

**Instructions:**

1. Implement `iriai-compose/iriai_compose/testing/validation.py` as a pure re-export of `validate_workflow`, `validate_type_flow`, and `detect_cycles` from `iriai_compose.schema.validation`.
2. Implement `assert_validation_error(errors, *, code=None, path=None)` in `assertions.py`. Require at least one of `code` or `path`, return silently on match, and raise `AssertionError` with a diagnostic summary when no match exists.
3. Re-export the validation functions, `assert_validation_error`, and `ValidationError` from `iriai_compose/iriai_compose/testing/__init__.py`.

**Acceptance Criteria:**

- `from iriai_compose.testing import validate_workflow, ValidationError` works
- `assert_validation_error([ValidationError(code="dangling_edge", ...)], code="dangling_edge")` passes
- `assert_validation_error([], code="dangling_edge")` raises `AssertionError` with a readable error summary

**Counterexamples:**

- Do NOT duplicate validation logic in SF-3
- Do NOT return bool from `assert_validation_error()`
- Do NOT silently accept the case where both `code` and `path` are omitted

**Requirement IDs:** REQ-3, REQ-5

**Journey IDs:** J-1

**Citations:**

- **[code]** `iriai-compose/iriai_compose/schema/validation.py`
  - Excerpt: SF-1 owns the validation implementation.
  - Reasoning: SF-3 should import and re-export validation instead of forking it.
- **[decision]** `D-SF3-1`
  - Reasoning: Assertion helpers are small, focused functions that sit on top of the authoritative validation output.

#### STEP-21: Implement execution assertions against the canonical SF-2 `ExecutionResult` contract without widening the runtime API. This step removes helper-method assumptions by deriving node and phase views locally from the canonical `nodes_executed` tuples and `branch_paths` mapping.
<!-- SF: testing-framework | Original: STEP-5 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-compose/iriai_compose/testing/assertions.py` | modify |
| `iriai-compose/iriai_compose/testing/__init__.py` | modify |
| `iriai-compose/iriai_compose/declarative/__init__.py` | read |

**Instructions:**

1. In `iriai-compose/iriai_compose/testing/assertions.py`, add private helpers that derive execution views from the canonical data contract:
   - `_executed_node_ids(result) -> list[str]` returns `[node_id for phase_id, node_id in result.nodes_executed]`
   - `_node_index(result, node_id) -> int` returns the first matching position from `_executed_node_ids`
   - `_executed_phase_ids(result) -> set[str]` returns `{phase_id for phase_id, node_id in result.nodes_executed}`
2. Rewrite `assert_node_reached()` to use those helpers instead of `result.node_ids()` / `result.node_index()`.
3. Keep `assert_artifact()` as a direct `result.artifacts` assertion helper.
4. Keep `assert_branch_taken()` pinned to `result.branch_paths` and never to `branch_paths_taken`.
5. Rewrite `assert_node_count()` diagnostics to print the locally derived node-id list.
6. Fix `assert_phase_executed()` to treat each tuple as `(phase_id, node_id)` and collect the phase IDs via `{phase_id for phase_id, _ in result.nodes_executed}`.
7. Re-export all execution assertion helpers from `iriai-compose/iriai_compose/testing/__init__.py`.

**Acceptance Criteria:**

- Given `ExecutionResult(nodes_executed=[("phase_a", "n1"), ("phase_a", "n2")], branch_paths={"gate": "approved"})`, `assert_node_reached(result, "n1", before="n2")` passes
- Given the same result, `assert_phase_executed(result, "phase_a")` passes because the tuple order is treated as `(phase_id, node_id)`
- Given `branch_paths={"gate": "approved"}`, `assert_branch_taken(result, "gate", "approved")` passes by reading `result.branch_paths`
- A missing node raises `AssertionError` with the derived execution order in the message

**Counterexamples:**

- Do NOT call `ExecutionResult.node_ids()` or `ExecutionResult.node_index()`; those helper methods are not part of the canonical SF-2 contract
- Do NOT destructure `nodes_executed` as `(_, phase_id)` anywhere in SF-3
- Do NOT read `result.branch_paths_taken`; that field name is stale

**Requirement IDs:** REQ-5

**Journey IDs:** J-1

**Citations:**

- **[research]** `.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/plan.md:60-68`
  - Excerpt: SF-2 defines nodes_executed: list[tuple[str, str]] and branch_paths: dict[str, str] on ExecutionResult.
  - Reasoning: SF-3 assertion code must match the actual produced data contract.
- **[research]** `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:112-120`
  - Excerpt: ExecutionResult/ExecutionHistory are the published observability surface for SF-3/SF-4 consumers.
  - Reasoning: Assertions must consume the observability surface as published, not extend it.

#### STEP-22: Implement `run_test()` as a thin wrapper over the canonical SF-2 `run()` interface. The wrapper owns convenience setup only; it preserves the exact public contract by passing no `history=` kwarg to `run()` and constructing `RuntimeConfig` without a `history` field since checkpoint/resume is not in SF-2's core runtime scope.
<!-- SF: testing-framework | Original: STEP-6 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-compose/iriai_compose/testing/runner.py` | modify |
| `iriai-compose/iriai_compose/testing/__init__.py` | modify |
| `iriai-compose/iriai_compose/declarative/__init__.py` | read |
| `iriai-compose/iriai_compose/declarative/config.py` | read |
| `iriai-compose/iriai_compose/workflow.py` | read |

**Instructions:**

1. Implement `run_test()` in `iriai-compose/iriai_compose/testing/runner.py` with the signature `async def run_test(workflow, *, runtime=None, interaction=None, plugins=None, inputs=None, feature_id="test") -> ExecutionResult`. There is no `history=` parameter — checkpoint/resume is not in SF-2's core runtime scope.
2. Default `runtime` to `MockRuntime()` and the auto interaction runtime to `MockInteraction(approve=True)`.
3. Create a synthetic `Feature` for test isolation using the real `Feature` model from `iriai_compose/iriai_compose/workflow.py`.
4. Construct `RuntimeConfig(agent_runtime=runtime, interaction_runtimes=interaction_runtimes, plugin_registry=plugins, feature=feature)`. Do not pass a `history` argument — the field does not exist on `RuntimeConfig` in this cycle.
5. Delegate with the exact canonical call `return await run(workflow, config, inputs=inputs)`.
6. Re-export `run_test` and `ExecutionResult` from `iriai-compose/iriai_compose/testing/__init__.py`.

**Acceptance Criteria:**

- `await run_test(minimal_ask_workflow())` returns an `ExecutionResult`
- `await run_test(minimal_ask_workflow(), runtime=MockRuntime().when_role("pm").respond("answer"))` routes through the fluent mock runtime
- `run_test` raises `TypeError` if called with a `history=` keyword argument — the parameter must not exist
- Exceptions raised by SF-2's `run()` propagate unchanged through `run_test()`

**Counterexamples:**

- Do NOT add a `history=` parameter to `run_test()`
- Do NOT pass `history=` to `RuntimeConfig()`
- Do NOT call `run(..., history=...)` under any circumstances
- Do NOT add `transform_registry` or `hook_registry` parameters to `run_test()`
- Do NOT catch and wrap execution exceptions inside `run_test()`

**Requirement IDs:** REQ-3, REQ-5

**Journey IDs:** J-1

**Citations:**

- **[research]** `.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/plan.md:60`
  - Excerpt: run() signature is run(workflow, config, *, inputs=None) — no history kwarg
  - Reasoning: The test wrapper must align to the authoritative ABI, not to stale plan notes.
- **[research]** `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:34`
  - Excerpt: AC-4: Tests must not depend on a built-in SF-2 checkpoint/resume ABI, a synthetic history= run() kwarg, or any consumer-owned resumability contract.
  - Reasoning: The R17 PRD explicitly prohibits the history= parameter in run_test().
- **[code]** `iriai-compose/iriai_compose/workflow.py:18-27`
  - Excerpt: The Feature model defines the runtime identity fields used by the existing imperative runner.
  - Reasoning: The test wrapper should build a real Feature, not a testing-only stand-in.

#### STEP-23: Implement YAML round-trip snapshot helpers for fixture-level regression testing. These helpers stay focused on schema serialization and avoid conflating YAML snapshots with execution-trace assertions.
<!-- SF: testing-framework | Original: STEP-7 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-compose/iriai_compose/testing/snapshot.py` | modify |
| `iriai-compose/iriai_compose/testing/__init__.py` | modify |
| `iriai-compose/iriai_compose/schema/__init__.py` | read |
| `iriai-compose/iriai_compose/schema/yaml_io.py` | read |

**Instructions:**

1. Implement `assert_yaml_round_trip(path)` by loading the workflow through `load_workflow()`, dumping it through `dump_workflow()`, and comparing the parsed YAML structures via `yaml.safe_load()`.
2. Implement `assert_yaml_equals(actual, expected)` and `yaml_diff()` using `difflib.unified_diff`.
3. Re-export `assert_yaml_round_trip` and `assert_yaml_equals` from `__init__.py`.

**Acceptance Criteria:**

- `assert_yaml_round_trip("iriai-compose/tests/fixtures/workflows/minimal_ask.yaml")` passes for a valid fixture
- Structure-preserving round-trip comparisons ignore key-order differences by comparing parsed YAML values
- A mismatch raises `AssertionError` containing a unified diff

**Counterexamples:**

- Do NOT use `ruamel.yaml` or `deepdiff`
- Do NOT import YAML I/O from a nonexistent `schema.io` module

**Requirement IDs:** REQ-3

**Journey IDs:** J-1

**Citations:**

- **[research]** `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:136-138`
  - Excerpt: Open question about whether snapshots should be JSON-only or include YAML — defaulting to YAML snapshots aligned with the primary workflow format.
  - Reasoning: pyyaml + difflib remains the right choice given the schema's YAML-first orientation.

#### STEP-24: Add fixtures and self-tests that lock the SF-2 → SF-3 edge contract in place after the Cycle 4 ABI revision. These tests verify ContextVar-based node routing, unchanged `invoke()` signature, canonical `run()` usage without `history=`, tuple ordering in `nodes_executed`, and the `branch_paths` field name so future revisions cannot silently reintroduce the same drift.
<!-- SF: testing-framework | Original: STEP-8 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-compose/tests/fixtures/workflows/minimal_ask.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/minimal_branch.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/minimal_plugin.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/sequential_phase.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/map_phase.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/fold_phase.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/loop_phase.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/multi_phase.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/hook_edge.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/nested_phases.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/ask_gate_pattern.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/ask_choose_pattern.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/store_dot_notation.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/dangling_edge.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/cycle_detected.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/type_mismatch.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_actor_ref.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/duplicate_node_id.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_phase_mode_config.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_hook_edge_transform.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_store_ref.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_switch_function_config.yaml` | create |
| `iriai-compose/tests/testing/__init__.py` | create |
| `iriai-compose/tests/testing/test_mock_runtime.py` | create |
| `iriai-compose/tests/testing/test_builder.py` | create |
| `iriai-compose/tests/testing/test_assertions.py` | create |
| `iriai-compose/tests/testing/test_validation_reexport.py` | create |
| `iriai-compose/tests/testing/test_snapshots.py` | create |
| `iriai-compose/tests/testing/test_runner.py` | create |

**Instructions:**

1. Add the valid and invalid YAML fixtures under `iriai-compose/tests/fixtures/workflows/` as planned so schema and runner tests have portable test data.
2. In `iriai-compose/tests/testing/test_mock_runtime.py`, test ContextVar-based routing: set `current_node_var` (imported from `iriai_compose.declarative`) to a specific node id using its `.set()` method, then call `MockRuntime().when_node("ask_1").respond("x").invoke(role, prompt)` and assert the node-specific response is returned. Verify that the recorded call's `node_id` field matches what was set on the ContextVar, not what was passed as a kwarg. Verify that the `invoke()` method signature does not accept a `node_id` keyword argument (call `invoke(..., node_id="x")` and assert `TypeError` is raised).
3. In `iriai-compose/tests/testing/test_assertions.py`, build `ExecutionResult` values whose `nodes_executed` entries are `(phase_id, node_id)` tuples. Verify that assertion helpers pass without calling any `ExecutionResult` helper methods and that `assert_phase_executed()` reads the phase ID from the first tuple slot.
4. In `iriai-compose/tests/testing/test_runner.py`, verify `run_test()` delegates with the canonical call shape `run(workflow, config, inputs=inputs)` and does NOT accept a `history=` keyword argument. Add a test that calls `run_test(workflow, history="anything")` and asserts `TypeError` is raised.
5. In `iriai-compose/tests/testing/test_assertions.py` and `test_runner.py`, verify `branch_paths` is the consumed field name and that any stale `branch_paths_taken` references would fail the tests.
6. Keep the snapshot and validation tests from the prior plan revision, updating their fixture paths to the repo-correct `iriai-compose/tests/...` locations.

**Acceptance Criteria:**

- `pytest iriai-compose/tests/testing/test_mock_runtime.py` verifies that setting `current_node_var` before invoking produces node-specific routing and that `invoke(node_id=...)` raises `TypeError`
- `pytest iriai-compose/tests/testing/test_assertions.py` verifies `nodes_executed` tuple order is `(phase_id, node_id)` and that `branch_paths` is the canonical branch field
- `pytest iriai-compose/tests/testing/test_runner.py` verifies `run_test()` has no `history=` parameter and raises `TypeError` when called with one
- `pytest iriai-compose/tests/testing/` passes
- Existing `pytest iriai-compose/tests/` coverage remains green

**Counterexamples:**

- Do NOT write any test that calls `ExecutionResult.node_ids()` or `ExecutionResult.node_index()`
- Do NOT build assertion fixtures that treat `nodes_executed` as `(node_id, phase_id)`
- Do NOT reference `branch_paths_taken` in test code
- Do NOT pass `history=` to `run()` or `run_test()` — not as a kwarg test, not in any helper
- Do NOT declare a new ContextVar in the test files; import `current_node_var` from `iriai_compose.declarative`

**Requirement IDs:** REQ-1, REQ-3, REQ-4, REQ-5

**Journey IDs:** J-1, J-2

**Citations:**

- **[research]** `.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:31-34`
  - Excerpt: AC-1 through AC-4 define the canonical acceptance tests for the R17 ABI revision.
  - Reasoning: The self-tests in STEP-8 directly map to the PRD acceptance criteria.
- **[code]** `iriai-compose/iriai_compose/runner.py:33`
  - Excerpt: _current_phase_var: ContextVar[str] = ContextVar("_current_phase", default="")
  - Reasoning: Tests for node ContextVar routing mirror the existing phase-var pattern.

### Journey Verifications

**Journey J-1:**

- Step 1:
  - [api] Create `MockRuntime()` and call `.when_node("ask_1").respond("x")` and `.when_role("pm").respond("y")` — neither method errors, both return the parent `MockRuntime`. No `node_id` kwarg path exists on the API.
- Step 2:
  - [api] Set `current_node_var.set("ask_1")` then call `mock.invoke(pm_role, "prompt")` — the node-specific rule for `ask_1` fires and returns `"x"`. Calling `mock.invoke(pm_role, "other_prompt")` without setting the ContextVar (or with a different node_id) returns the role fallback `"y"`.
  - [api] Confirm `mock.invoke` signature is `invoke(role, prompt, *, output_type=None, workspace=None, session_key=None)` — calling `mock.invoke(role, prompt, node_id="x")` raises `TypeError`.
- Step 3:
  - [api] After a completed run, `assert_node_reached(result, "ask_1")` passes given `nodes_executed=[("main", "ask_1")]`. `assert_phase_executed(result, "main")` passes reading the phase from tuple position 0. `assert_branch_taken(result, "gate", "approved")` passes reading from `result.branch_paths`. None of these assertions call `result.node_ids()` or `result.node_index()`.
  - [api] `run_test(workflow)` called without a `history=` kwarg returns `ExecutionResult`. Calling `run_test(workflow, history=None)` raises `TypeError`.

**Journey J-2:**

- Step 1:
  - [api] Grep `iriai-compose/iriai_compose/testing/` for `node_id` as a parameter name in any `invoke()` method definition — zero matches. Grep for `branch_paths_taken` — zero matches. Grep for `history=` as a `run()` kwarg or `RuntimeConfig` field — zero matches.
- Step 2:
  - [api] Grep `iriai-compose/tests/testing/` for `invoke.*node_id` — zero matches. Grep for `run_test.*history` — zero matches. Grep for `branch_paths_taken` — zero matches. All three grep commands return empty results confirming stale assumptions were not reintroduced.

### Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-52 | SF-1 schema modules are not available yet when SF-3 implementation starts. | high | STEP-1 can land independently, but STEP-3, STEP-4, STEP-7, and STEP-8 must wait for SF-1 artifacts to exist because they import real schema modules. | STEP-3, STEP-4, STEP-7, STEP-8 |
| RISK-53 | SF-2 implementers do not export `current_node_var` from `iriai_compose.declarative`, leaving MockRuntime with no published ContextVar to import. | high | STEP-2 has a hard import dependency on `from iriai_compose.declarative import current_node_var`. If the export is missing, STEP-2 will fail immediately with ImportError, making the gap visible before any tests run. SF-2 must treat this export as a first-class ABI commitment. | STEP-2, STEP-8 |
| RISK-54 | SF-2 does not set `current_node_var` inside the declarative runner before calling `invoke()`, so MockRuntime always reads an empty string and node-specific routing never fires. | high | STEP-8 adds a direct test that sets `current_node_var` manually and calls `invoke()` — this validates the ContextVar plumbing path. A complementary integration test with the real SF-2 runner verifies the runner sets the ContextVar before each node execution. Both must pass before SF-3 is considered complete. | STEP-2, STEP-6, STEP-8 |
| RISK-55 | SF-3 reintroduces helper-method assumptions on `ExecutionResult`, causing runtime `AttributeError` when assertions run against the plain dataclass from SF-2. | medium | STEP-5 derives node IDs and indices locally and explicitly forbids calls to `result.node_ids()` or `result.node_index()`. STEP-8 adds regression tests that build plain dataclass instances and assert helpers work without those methods. | STEP-5, STEP-8 |
| RISK-56 | Tuple order or branch-field naming drifts again across SF-2 and SF-3, silently corrupting assertion behavior. | medium | This plan locks `nodes_executed` to `(phase_id, node_id)` and `branch_paths` to the canonical field name, then adds explicit regression tests in STEP-8 for both. | STEP-5, STEP-8 |

### File Manifest

| Path | Action |
|------|--------|
| `iriai-compose/pyproject.toml` | modify |
| `iriai-compose/iriai_compose/testing/__init__.py` | create |
| `iriai-compose/iriai_compose/testing/mock_runtime.py` | create |
| `iriai-compose/iriai_compose/testing/fixtures.py` | create |
| `iriai-compose/iriai_compose/testing/assertions.py` | create |
| `iriai-compose/iriai_compose/testing/snapshot.py` | create |
| `iriai-compose/iriai_compose/testing/runner.py` | create |
| `iriai-compose/iriai_compose/testing/validation.py` | create |
| `iriai-compose/tests/fixtures/workflows/minimal_ask.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/minimal_branch.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/minimal_plugin.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/sequential_phase.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/map_phase.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/fold_phase.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/loop_phase.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/multi_phase.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/hook_edge.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/nested_phases.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/ask_gate_pattern.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/ask_choose_pattern.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/store_dot_notation.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/dangling_edge.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/cycle_detected.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/type_mismatch.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_actor_ref.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/duplicate_node_id.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_phase_mode_config.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_hook_edge_transform.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_store_ref.yaml` | create |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_switch_function_config.yaml` | create |
| `iriai-compose/tests/testing/__init__.py` | create |
| `iriai-compose/tests/testing/test_mock_runtime.py` | create |
| `iriai-compose/tests/testing/test_builder.py` | create |
| `iriai-compose/tests/testing/test_assertions.py` | create |
| `iriai-compose/tests/testing/test_validation_reexport.py` | create |
| `iriai-compose/tests/testing/test_snapshots.py` | create |
| `iriai-compose/tests/testing/test_runner.py` | create |
| `iriai-compose/tests/conftest.py` | read |
| `iriai-compose/iriai_compose/runner.py` | read |
| `iriai-compose/iriai_compose/actors.py` | read |
| `iriai-compose/iriai_compose/pending.py` | read |
| `iriai-compose/iriai_compose/workflow.py` | read |
| `iriai-compose/iriai_compose/schema/validation.py` | read |
| `iriai-compose/iriai_compose/schema/__init__.py` | read |
| `iriai-compose/iriai_compose/schema/yaml_io.py` | read |
| `iriai-compose/iriai_compose/schema/base.py` | read |
| `iriai-compose/iriai_compose/schema/nodes.py` | read |
| `iriai-compose/iriai_compose/schema/edges.py` | read |
| `iriai-compose/iriai_compose/schema/phases.py` | read |
| `iriai-compose/iriai_compose/schema/actors.py` | read |
| `iriai-compose/iriai_compose/schema/stores.py` | read |
| `iriai-compose/iriai_compose/schema/workflow.py` | read |
| `iriai-compose/iriai_compose/declarative/__init__.py` | read |
| `iriai-compose/iriai_compose/declarative/config.py` | read |

---

## SF-4: Workflow Migration & Litmus Test
<!-- SF: workflow-migration -->

### Architecture

# Technical Plan: SF-4 Workflow Migration & Litmus Test (Revised v8)

## Architecture

### Revision Summary (v8)

This revision treats SF-2 as the canonical owner of the runtime ABI consumed by SF-4. The change request resolved the remaining ambiguity, so no extra clarification questions were required.

1. **[D-SF4-34] Canonical ABI ownership:** SF-4 now cites SF-2's dag-loader-runner PRD/plan as the source of truth for declarative runtime behavior instead of restating its own competing contract. The published ABI keeps `AgentRuntime.invoke()` unchanged, propagates current node identity through `ContextVar`, and defines hierarchical context merge order as `workflow -> phase -> actor -> node`. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:28-30] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/plan.md:5]
2. **[D-SF4-35] Consumer alignment:** SF-4 tests, migrated YAML authoring, and iriai-build-v2 integration now explicitly consume the SF-2 ABI. Any stale SF-3/SF-4 assumption about `invoke(..., node_id=...)` is treated as non-compliant drift. [decision: D-GR-23] [code: .iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:19-22]
3. **[D-SF4-36] Canonical merge order:** effective context stays first-wins and additive in `workflow -> phase -> actor -> node` order; lower scopes may add keys but must not override earlier duplicates. [decision: D-GR-23] [code: .iriai/artifacts/features/beced7b1/plan-review-discussion-4.md:28-34] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-migration/prd.md:21-23]
4. **[D-SF4-37] No core checkpoint/resume dependency:** SF-4 now consumes only SF-2's observability surface (`ExecutionResult`, `ExecutionHistory`, phase metrics). Migration tests and build-v2 integration must not assume a built-in checkpoint store, resume flag, or `history=` execution ABI in SF-2. [decision: D-GR-24] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:30] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:47]
5. **[Carried forward]** D-A4 runtime bridge adapters, D-A8 three-category migration, D-GR-10 `env_config`, D-GR-14 explicit ArtifactPlugin writes, D-SF4-31 result-field alignment, and D-SF4-32 phase-mode metrics remain unchanged.

### Canonical Runtime ABI Published By SF-2

- `AgentRuntime.invoke(role, prompt, *, output_type=None, workspace=None, session_key=None)` remains unchanged.
- SF-2 publishes current phase/node identity through runtime `ContextVar`s during Ask-node execution; SF-3/SF-4 consumers observe that state instead of extending the runtime ABC.
- Hierarchical `context_keys` merge in `workflow -> phase -> actor -> node` order with first-wins deduplication.
- SF-2 publishes execution observability through `ExecutionResult`, `ExecutionHistory`, and phase-mode metrics; SF-4 must not invent a core checkpoint/resume contract on top of that ABI.
- YAML authoring in SF-4 must assume additive inheritance. Repeating a workflow key at node scope does not override the earlier workflow-level value.

### Cross-Subfeature Contract Used By SF-4

**SF-1 must provide** schema models, YAML I/O, validation, `input_type`, inline `transform_fn`, and a node model without `artifact_key` auto-write.

**SF-2 publishes the canonical runtime ABI that SF-4 consumes**:
- `iriai_compose.declarative.run(workflow, config, *, inputs=None)` and `load_workflow()`. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:29-30]
- `RuntimeConfig`, `ExecutionResult`, `ExecutionHistory`, `PluginRegistry`, the built-in `artifact` plugin, and phase-mode metrics required by SF-4. [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:30]
- A current-node `ContextVar` carrier/reader helper colocated with the existing runtime-context precedent so Ask-node execution can publish node identity without changing `AgentRuntime.invoke()`. [code: iriai-compose/iriai_compose/runner.py:32-50] [decision: D-GR-23]
- Hierarchical context assembly that merges `context_keys` in `workflow -> phase -> actor -> node` order with first-wins dedup. [decision: D-GR-23] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:28]
- No mandatory core checkpoint/resume API or wrapper-owned resume ABI. [decision: D-GR-24] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:30]

**SF-3 must provide**:
- `MockRuntime`, `MockInteraction`, `WorkflowBuilder`, `run_test`, and the 5 base assertions used by SF-4.
- Mock runtime behavior that reads current node identity from the shared `ContextVar` during `invoke()` and records that resolved node id in call history, rather than introducing a `node_id` kwarg on the runtime ABC. [code: .iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:19-21]
- Test helpers that consume SF-2's observability surface directly and do not require a core checkpoint/resume contract. [code: .iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:112-114] [decision: D-GR-24]

**SF-4 authoring rules derived from the contract**:
- Migrated YAML must rely on additive context inheritance, not local overrides through duplicated keys.
- Tests and consumer integration must never introduce a compatibility shim that changes `AgentRuntime.invoke()` or adds a synthetic `node_id` kwarg.
- Migration coverage may assert against `ExecutionResult`, `ExecutionHistory`, and phase metrics, but it must not require a built-in checkpoint store, resume flag, or separate `history=` execution call.

### Decision Log

| ID | Decision | Rationale | Citation |
|----|----------|-----------|----------|
| D-A4 | Runtime bridge adapter in `iriai_compose/plugins/adapters.py` enables any consumer to load and run declarative YAML workflows through existing runtime infrastructure. | iriai-build-v2 already boots the needed services; the bridge maps them into the plugin runtime registry expected by the declarative runner. | [code: iriai-build-v2/src/iriai_build_v2/interfaces/_bootstrap.py:126-156] |
| D-A8 | Three-category reclassification remains the organizing principle for migration: infrastructure connectors -> general plugins, pure transforms -> edge `transform_fn`, LLM work -> Ask nodes. | Keeps migrated YAML declarative while still representing existing imperative behavior. | [code: iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py] |
| D-SF4-7 | Artifact persistence is explicit through the built-in `artifact` plugin. | D-GR-14 removed `artifact_key` auto-write from node contracts, so YAML must encode Ask -> Artifact -> Hosting chains explicitly. | [decision: D-GR-14] |
| D-SF4-10 | Hierarchical context uses a 4-level additive merge order: `workflow -> phase -> actor -> node`. | This matches the canonical D-GR-23 runtime context contract and the migration PRD's additive context requirements. | [decision: D-GR-23] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-migration/prd.md:698] |
| D-SF4-26 | iriai-build-v2 integration stays additive through a thin `_declarative.py` wrapper and optional `--yaml` flag. | The litmus test is integration smoothness without modifying existing Python workflow classes. | [code: iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py:14-58] |
| D-SF4-31 | ExecutionResult field names remain aligned to SF-2's authoritative contract: `workflow_output`, `branch_paths`, and tuple-based `nodes_executed`. | SF-4 tests and wrappers must not drift from the runner's published result model. | [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/plan.md:657-668] |
| D-SF4-32 | Phase-mode metrics remain a blocking prerequisite from SF-2. | SF-4's loop/fold/map/error-routing equivalence tests still depend on explicit execution metrics. | [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-migration/prd.md:176-210] |
| D-SF4-33 | SF-4 owns the extra migration-specific assertion helpers that SF-3 does not provide. | Migration equivalence needs phase-mode and error-route assertions beyond SF-3's base helpers. | [code: .iriai/artifacts/features/beced7b1/subfeatures/testing-framework/plan.md:1-120] |
| D-SF4-34 | SF-4 cites SF-2 as the sole ABI owner. Node identity propagation is ContextVar-based and non-breaking. | The ABI boundary must have a single owner. Existing core runtime code already uses `ContextVar` for phase propagation; reusing that pattern avoids an unnecessary ABC break and keeps iriai-build-v2 runtimes unchanged. | [code: iriai-compose/iriai_compose/runner.py:5-50] [decision: D-GR-23] |
| D-SF4-35 | Duplicate `context_keys` preserve first occurrence in `workflow -> phase -> actor -> node` order. | This gives a deterministic prompt assembly contract for migrated YAML and prevents lower scopes from silently overriding broader context. | [decision: D-GR-23] [code: .iriai/artifacts/features/beced7b1/plan-review-discussion-4.md:30-34] |
| D-SF4-36 | The migration litmus suite must verify ContextVar propagation and merge ordering explicitly. | Without direct regression tests, future plan or implementation drift could reintroduce the stale `node_id` kwarg contract or a different merge order. | [decision: D-GR-23] |
| D-SF4-37 | SF-4 consumes only SF-2's published observability surface; it does not assume a built-in checkpoint store, resume flag, or `history=` execution ABI in SF-2. | D-GR-24 removes core checkpoint/resume from SF-2. Making this explicit in SF-4 prevents migration tests or the build-v2 wrapper from re-introducing it as an implicit dependency. | [decision: D-GR-24] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:30,47] |

### Implementation Steps

#### STEP-25: Define the reusable plugin/type/transform catalog that the migrated workflows reference. Keep the D-A8 reclassification stable so later YAML authoring can focus on workflow structure instead of bespoke imperative helpers.
<!-- SF: workflow-migration | Original: STEP-1 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai_compose/plugins/__init__.py` | modify |
| `iriai_compose/plugins/types.py` | create |
| `iriai_compose/plugins/instances.py` | create |
| `iriai_compose/plugins/transforms.py` | create |
| `iriai_compose/declarative/plugins.py` | read |

**Instructions:**

Create the 6 general plugin interfaces (`store`, `hosting`, `mcp`, `subprocess`, `http`, `env_config`), the 8 reusable plugin instances, and the 7 named pure edge transforms used by the migrated workflows. Register them through SF-2's PluginRegistry API exactly as runtime-visible catalog entries; do not add a user-defined `artifact` plugin type because the runner owns that built-in. Keep `env_config` as the only secret-bearing operation and keep all transform bodies side-effect free.

**Acceptance Criteria:**

- The plugin catalog exposes 6 types, 8 instances, and 7 edge transforms with no `build_env_overrides` transform entry.
- Registration functions use the runner's type/instance registration API rather than ad hoc maps.
- All transform constants compile as valid Python and do not perform I/O or environment reads.

**Counterexamples:**

- Do NOT add an `artifact` plugin definition to the catalog.
- Do NOT move secret access into transform code.
- Do NOT reintroduce bespoke per-operation plugin ABCs.

**Requirement IDs:** REQ-22, REQ-23, REQ-24, REQ-25, REQ-26, REQ-27, REQ-28, REQ-29, REQ-30, REQ-31

**Journey IDs:** J-1, J-2, J-3, J-4

**Citations:**

- **[decision]** `D-A8`
  - Reasoning: The plugin and transform catalog follows the three-category migration model.
- **[decision]** `D-GR-10`
  - Reasoning: `env_config` replaces the old environment transform.
- **[decision]** `D-GR-14`
  - Reasoning: Artifact persistence is runner-owned, not a user-defined plugin type.

#### STEP-26: Extract the iriai-build-v2 output contracts into reusable YAML `types:` definitions. These types are the schema backbone for the migrated planning, develop, and bugfix workflows.
<!-- SF: workflow-migration | Original: STEP-2 -->

**Scope:**

| Path | Action |
|------|--------|
| `tests/fixtures/workflows/migration/types/common.yaml` | create |
| `tests/fixtures/workflows/migration/types/planning.yaml` | create |
| `tests/fixtures/workflows/migration/types/develop.yaml` | create |
| `tests/fixtures/workflows/migration/types/bugfix.yaml` | create |
| `iriai-build-v2/src/iriai_build_v2/models/outputs.py` | read |

**Instructions:**

Translate the Pydantic output models used by the existing workflows into JSON-Schema-based type entries split across shared, planning, develop, and bugfix YAML files. Keep each workflow self-sufficient: no cross-file `$ref` chains between type files.

**Acceptance Criteria:**

- Each migrated workflow can resolve every referenced output type from the local type bundle it loads.
- The shared `Envelope` definition preserves `complete`, `output`, and `question` semantics used by iterative loops.
- All type files parse as valid YAML and remain compatible with Draft 2020-12 JSON Schema.

**Counterexamples:**

- Do NOT rely on Python annotations inside the YAML type files.
- Do NOT create cross-workflow type dependencies through external `$ref`.

**Requirement IDs:** REQ-1, REQ-11, REQ-16, REQ-32

**Journey IDs:** J-1, J-2, J-3

**Citations:**

- **[code]** `iriai-build-v2/src/iriai_build_v2/models/outputs.py`
  - Reasoning: The existing workflow outputs define the authoritative payload shapes to preserve.

#### STEP-27: Translate the planning workflow into a declarative YAML equivalent that preserves artifact writes, hosting side effects, templates, and additive context flow. The planning workflow is the first litmus-test workflow and the baseline for later develop parity checks.
<!-- SF: workflow-migration | Original: STEP-3 -->

**Scope:**

| Path | Action |
|------|--------|
| `tests/fixtures/workflows/migration/planning.yaml` | create |
| `tests/fixtures/workflows/templates/gate_and_revise.yaml` | read |
| `tests/fixtures/workflows/templates/broad_interview.yaml` | read |
| `tests/fixtures/workflows/templates/interview_gate_review.yaml` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/planning/workflow.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/pm.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/scoping.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/design.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/architecture.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/task_planning.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/plan_review.py` | read |

**Instructions:**

Author `planning.yaml` with 6 phases, explicit Ask -> Artifact -> Hosting chains where artifacts persist, and the same template decomposition used by the imperative workflow. Keep workflow-level and phase-level `context_keys` minimal and rely on the canonical D-GR-23 merge order `workflow -> phase -> actor -> node`; do not duplicate upstream keys at lower scopes to force precedence. Preserve loop exit behavior through `Envelope.complete`, explicit fresh-session gate loops, and the tiered-context transform for subfeature synthesis.

**Acceptance Criteria:**

- `load_workflow('planning.yaml')` succeeds and the schema validator returns no issues.
- All persisted planning artifacts are produced by explicit ArtifactPlugin nodes and hosted by separate downstream HostingPlugin nodes.
- Planning phases declare context keys in a way that is consistent with first-wins merge ordering across workflow, phase, actor, and node scopes.

**Counterexamples:**

- Do NOT add `artifact_key` fields to migrated nodes.
- Do NOT attach hosting behavior directly to Ask-node hooks.
- Do NOT repeat workflow or phase context keys at node scope expecting a lower-level override.

**Requirement IDs:** REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8, REQ-9, REQ-10, REQ-32, REQ-33, REQ-34

**Journey IDs:** J-1

**Citations:**

- **[code]** `iriai-build-v2/src/iriai_build_v2/workflows/planning/workflow.py`
  - Reasoning: The imperative planning workflow is the migration source of truth.
- **[decision]** `D-GR-14`
  - Reasoning: Artifact writes must be explicit plugin nodes.
- **[decision]** `D-GR-23`
  - Reasoning: Context-key authoring must assume workflow -> phase -> actor -> node merge ordering.

#### STEP-28: Translate the develop workflow as a standalone 7-phase YAML workflow, including the nested DAG execution phase. Preserve the planning-phase structure while keeping the implementation-specific fold/map/loop composition intact.
<!-- SF: workflow-migration | Original: STEP-4 -->

**Scope:**

| Path | Action |
|------|--------|
| `tests/fixtures/workflows/migration/develop.yaml` | create |
| `tests/fixtures/workflows/migration/planning.yaml` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py` | read |

**Instructions:**

Create `develop.yaml` with the same 6 planning phases as `planning.yaml` plus the looped ImplementationPhase. Preserve the nested fold -> map -> loop structure for DAG task execution, use the named pure transforms for prompt building and handover shaping, and keep context inheritance additive so group-level and task-level prompts rely on the canonical workflow -> phase -> actor -> node merge order instead of local key duplication.

**Acceptance Criteria:**

- `load_workflow('develop.yaml')` succeeds and validates cleanly.
- The 6 planning phases remain structurally equivalent to the standalone planning workflow.
- Implementation prompts and retries consume context through hierarchical additive merge rather than duplicated lower-scope keys.

**Counterexamples:**

- Do NOT use cross-file `$ref` back into `planning.yaml`.
- Do NOT collapse the fold/map/loop nesting into a flatter control-flow shape.
- Do NOT duplicate broader context keys at node scope to simulate ordering overrides.

**Requirement IDs:** REQ-11, REQ-12, REQ-13, REQ-14, REQ-15, REQ-45, REQ-49

**Journey IDs:** J-2

**Citations:**

- **[code]** `iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py`
  - Reasoning: The imperative implementation phase defines the nesting and branching behavior that must survive migration.
- **[decision]** `D-SF4-8`
  - Reasoning: The develop workflow remains a standalone YAML artifact.
- **[decision]** `D-GR-23`
  - Reasoning: Hierarchical prompt context is additive and ordered.

#### STEP-29: Translate the bugfix workflow's 8 phases into declarative YAML while preserving environment setup, preview/playwright integrations, RCA fan-out, and bounded diagnosis retries. This workflow proves the migration can represent the most integration-heavy runtime path.
<!-- SF: workflow-migration | Original: STEP-5 -->

**Scope:**

| Path | Action |
|------|--------|
| `tests/fixtures/workflows/migration/bugfix.yaml` | create |
| `iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/env_setup.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/bugfix/workflow.py` | read |

**Instructions:**

Author `bugfix.yaml` with explicit plugin instances for preview, playwright, git, hosting, artifact storage, and `env_overrides`. Keep diagnosis-and-fix as a bounded loop containing the dual-analyst map stage, preserve environment secrets through the `env_config` plugin only, and rely on additive hierarchical context instead of node-level duplication when bug reports, reproduction evidence, and RCA outputs flow into fix/verify prompts.

**Acceptance Criteria:**

- `load_workflow('bugfix.yaml')` succeeds and validates cleanly.
- Environment setup uses the `env_overrides` plugin instance rather than transform code or direct `os.environ` access in YAML.
- Diagnosis and verification prompts inherit workflow, phase, actor, and node context in canonical order without relying on duplicate keys for precedence.

**Counterexamples:**

- Do NOT reintroduce a `build_env_overrides` transform.
- Do NOT read secrets from the environment inside transform functions.
- Do NOT encode context precedence by repeating broader keys at lower scopes.

**Requirement IDs:** REQ-16, REQ-17, REQ-18, REQ-19, REQ-20, REQ-21, REQ-31

**Journey IDs:** J-3

**Citations:**

- **[code]** `iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/env_setup.py:53-74`
  - Reasoning: The existing environment-override helper defines the migration target for `env_config`.
- **[decision]** `D-GR-10`
  - Reasoning: Environment overrides are plugin-driven, not transform-driven.
- **[decision]** `D-GR-23`
  - Reasoning: Bugfix prompt assembly must follow the canonical hierarchical merge order.

#### STEP-30: Encode the repeated helper patterns as reusable declarative templates so the migrated workflows stay maintainable. Template behavior must preserve explicit artifact persistence and consistent additive context behavior.
<!-- SF: workflow-migration | Original: STEP-6 -->

**Scope:**

| Path | Action |
|------|--------|
| `tests/fixtures/workflows/templates/gate_and_revise.yaml` | create |
| `tests/fixtures/workflows/templates/broad_interview.yaml` | create |
| `tests/fixtures/workflows/templates/interview_gate_review.yaml` | create |

**Instructions:**

Create the three reusable templates (`gate_and_revise`, `broad_interview`, `interview_gate_review`) as self-contained declarative phase fragments. Keep ArtifactPlugin and HostingPlugin behavior explicit, retain fresh-session behavior for review loops, and author template `context_keys` assuming the same workflow -> phase -> actor -> node merge contract as the concrete workflows.

**Acceptance Criteria:**

- All three template YAML files parse and validate without schema errors.
- Each template encodes explicit Ask -> Artifact -> Hosting sequencing where persisted outputs exist.
- Template prompts rely on additive context inheritance and do not depend on lower-scope duplicate keys overriding broader context.

**Counterexamples:**

- Do NOT embed artifact auto-write assumptions into template nodes.
- Do NOT collapse artifact persistence and hosting into one plugin step.
- Do NOT duplicate broader context keys in node definitions to force ordering changes.

**Requirement IDs:** REQ-35, REQ-36, REQ-37

**Journey IDs:** J-1, J-2, J-3

**Citations:**

- **[decision]** `D-GR-14`
  - Reasoning: Templates must use explicit artifact persistence nodes.
- **[decision]** `D-SF4-13`
  - Reasoning: Review loops still require fresh sessions.
- **[decision]** `D-GR-23`
  - Reasoning: Template context assembly follows the same canonical merge order as the workflows.

#### STEP-31: Bridge iriai-build-v2's existing services into the declarative plugin runtime model without coupling the library to build-v2 internals. This makes the migrated YAML executable by downstream consumers that already have equivalent services.
<!-- SF: workflow-migration | Original: STEP-7 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai_compose/plugins/adapters.py` | create |
| `iriai_compose/plugins/__init__.py` | modify |
| `iriai_compose/declarative/plugins.py` | read |
| `iriai-build-v2/src/iriai_build_v2/interfaces/_bootstrap.py` | read |

**Instructions:**

Implement protocol-based adapters for artifact stores, hosting, MCP services, subprocess execution, HTTP calls, and `env_config` secrets. Export `create_plugin_runtimes()` so iriai-build-v2 can register its existing services with the declarative runtime. Keep the adapter boundary focused on plugin execution; node identity propagation remains the runner's ContextVar responsibility and must not leak into adapter method signatures.

**Acceptance Criteria:**

- `create_plugin_runtimes()` returns a plugin-runtime map consumable by `RuntimeConfig`.
- Adapters do not import iriai-build-v2 concrete classes directly and do not require a changed `AgentRuntime.invoke()` signature.
- The returned plugin map omits the built-in `artifact` runtime, which remains runner-owned.

**Counterexamples:**

- Do NOT add iriai-build-v2 type imports to the adapter module.
- Do NOT pass node identity through plugin adapter interfaces.
- Do NOT register a user-defined artifact plugin from the adapter factory.

**Requirement IDs:** REQ-22, REQ-23, REQ-46

**Journey IDs:** J-1, J-2, J-3

**Citations:**

- **[decision]** `D-A4`
  - Reasoning: The adapter bridge is the chosen consumer-integration mechanism.
- **[decision]** `D-SF4-25`
  - Reasoning: Protocol typing keeps the adapter surface decoupled.
- **[code]** `iriai-build-v2/src/iriai_build_v2/interfaces/_bootstrap.py:126-156`
  - Reasoning: build-v2 already materializes the services that the adapters must wrap.

#### STEP-32: Build the migration test suite that proves declarative YAML is behaviorally equivalent to the imperative workflows and that the runtime ABI published by SF-2 is preserved. This is the core litmus step for SF-4.
<!-- SF: workflow-migration | Original: STEP-8 -->

**Scope:**

| Path | Action |
|------|--------|
| `tests/migration/__init__.py` | create |
| `tests/migration/assertions.py` | create |
| `tests/migration/conftest.py` | create |
| `tests/migration/test_planning.py` | create |
| `tests/migration/test_develop.py` | create |
| `tests/migration/test_bugfix.py` | create |
| `tests/migration/test_yaml_roundtrip.py` | create |
| `tests/migration/test_plugin_instances.py` | create |
| `tests/migration/test_edge_transforms.py` | create |
| `tests/migration/test_runtime_bridge.py` | create |
| `tests/migration/test_runtime_context.py` | create |
| `tests/migration/test_litmus.py` | create |
| `tests/migration/test_phase_modes.py` | create |
| `tests/migration/test_error_ports.py` | create |
| `tests/migration/test_context_hierarchy.py` | create |
| `tests/migration/test_templates.py` | create |
| `tests/migration/test_artifact_writes.py` | create |
| `tests/migration/test_live_smoke.py` | create |

**Instructions:**

Create the migration test harness around SF-3's testing package plus local SF-4 assertions. Keep the existing structural, equivalence, phase-mode, error-route, template, artifact, and smoke tests, and add explicit runtime-context coverage in `test_runtime_context.py`: verify that the current node id is observed through the shared ContextVar during Ask-node execution, that no test or mock requires `invoke(..., node_id=...)`, and that context-key merge order is `workflow -> phase -> actor -> node` with first-wins dedup. In `test_context_hierarchy.py`, assert that duplicate keys at lower scopes do not override earlier scopes and that Jinja/task-context rendering reflects the canonical merge order. Treat SF-2's published observability surface as the only runtime boundary: assert against `ExecutionResult`, `ExecutionHistory`, and phase metrics as exposed by SF-2, and do not add any migration-only dependency on a built-in checkpoint store, resume flag, or `history=` execution call.

**Acceptance Criteria:**

- `pytest tests/migration/` passes with the full migration suite green.
- Runtime-context tests prove node-aware mocks work through ContextVar propagation without a changed `AgentRuntime.invoke()` signature.
- Hierarchy tests prove `context_keys` merge in `workflow -> phase -> actor -> node` order with deterministic first-wins dedup.
- Equivalence tests continue to validate ArtifactPlugin chains, transform behavior, phase-mode metrics, error routing, and the published execution history surface without requiring a core checkpoint/resume API.

**Counterexamples:**

- Do NOT call `AgentRuntime.invoke(..., node_id=...)` anywhere in the SF-4 test suite.
- Do NOT infer merge order by duplicating keys and expecting node scope to override workflow scope.
- Do NOT drop the litmus tests down to schema-only coverage.
- Do NOT move migration-specific assertion helpers into the SF-3 package contract.
- Do NOT add migration tests that require a built-in checkpoint store, resume flag, or `history=` execution kwarg.

**Requirement IDs:** REQ-34, REQ-39, REQ-40, REQ-42, REQ-43, REQ-45, REQ-46

**Journey IDs:** J-1, J-2, J-3, J-4

**Citations:**

- **[decision]** `D-SF4-31`
  - Reasoning: ExecutionResult field usage remains aligned in the test suite.
- **[decision]** `D-SF4-32`
  - Reasoning: Phase-mode metric assertions still depend on explicit runner output.
- **[decision]** `D-SF4-33`
  - Reasoning: Migration-specific assertion helpers stay local to SF-4.
- **[decision]** `D-GR-23`
  - Reasoning: The migration suite must verify ContextVar-based node propagation and canonical merge order.
- **[decision]** `D-SF4-37`
  - Reasoning: The migration suite must consume SF-2 observability only and reject any core checkpoint/resume dependency.
- **[code]** `iriai-compose/iriai_compose/runner.py:32-50`
  - Reasoning: The existing runtime already establishes the ContextVar precedent that the tests now lock in.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:30,47`
  - Reasoning: SF-2 publishes observability without a mandatory core checkpoint/resume API, so SF-4 tests must not invent one.

#### STEP-33: Package the migrated workflows and their supporting catalog entries into reusable seed data for the composer app. This ensures the SF-4 migration immediately shows up as example content in the product surface.
<!-- SF: workflow-migration | Original: STEP-9 -->

**Scope:**

| Path | Action |
|------|--------|
| `tests/fixtures/seed/migration_seed.json` | create |
| `tests/fixtures/seed/seed_loader.py` | create |

**Instructions:**

Emit a seed bundle with the 3 workflows, reusable roles, schemas, templates, plugin types, instances, and transform records needed to browse the migrated content in the app. Mark every seeded record as example content and keep the counts aligned to the explicit ArtifactPlugin node expansion introduced by D-GR-14.

**Acceptance Criteria:**

- The seed JSON is valid, idempotently loadable, and marked `is_example: true` for every seeded record.
- Plugin seed content includes `env_config` and excludes the removed environment transform.
- Workflow seed content reflects the expanded node counts caused by explicit artifact persistence nodes.

**Counterexamples:**

- Do NOT seed a `build_env_overrides` transform entry.
- Do NOT use the pre-D-GR-14 lower node counts.

**Requirement IDs:** REQ-32, REQ-47

**Journey IDs:** J-1, J-2, J-3

**Citations:**

- **[decision]** `D-GR-14`
  - Reasoning: Explicit artifact nodes increase the workflow node counts that the seed file must report.
- **[decision]** `D-GR-10`
  - Reasoning: Seed data must reflect the final plugin/transform catalog.

#### STEP-34: Add the minimal iriai-build-v2 integration wrapper that loads migrated YAML through the declarative runtime without disturbing existing imperative workflow execution. The wrapper must consume SF-2's published ABI unchanged.
<!-- SF: workflow-migration | Original: STEP-10 -->

**Scope:**

| Path | Action |
|------|--------|
| `iriai-build-v2/src/iriai_build_v2/workflows/_declarative.py` | create |
| `iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py` | modify |
| `iriai-build-v2/src/iriai_build_v2/interfaces/_bootstrap.py` | read |
| `iriai_compose/plugins/adapters.py` | read |
| `iriai_compose/iriai_compose/runner.py` | read |

**Instructions:**

Create `_declarative.py` as a thin wrapper that lazy-imports the declarative runtime, loads YAML, builds plugin runtimes through STEP-7, and calls `run(workflow, config, inputs=None)`. Keep all existing iriai-build-v2 agent runtimes untouched: do not add shims or adapters that change `AgentRuntime.invoke()` or add a `node_id` kwarg. The wrapper only passes the existing runtime instances into `RuntimeConfig`; ContextVar-based node identity publication stays inside SF-2's runner, and any returned observability data is consumed as published by SF-2. Do not add runner-owned checkpoint/resume orchestration, a checkpoint store dependency, or any wrapper-specific `history=`/resume ABI. Add an optional `--yaml` CLI branch that uses the wrapper while leaving the existing imperative path unchanged.

**Acceptance Criteria:**

- `iriai-build ... --yaml <path>` executes the declarative workflow through the new wrapper without instantiating the legacy Python workflow classes.
- Running the same command without `--yaml` still uses the existing imperative workflow path unchanged.
- The wrapper passes existing agent runtimes through unchanged and does not introduce any `invoke(..., node_id=...)` compatibility layer.
- Result logging and output handling use the aligned ExecutionResult fields (`workflow_output`, `branch_paths`, tuple-based `nodes_executed`).
- The wrapper does not add a checkpoint store, resume flag, or wrapper-specific `history=` ABI on top of `run()`.

**Counterexamples:**

- Do NOT modify existing workflow classes or tracked runner implementations.
- Do NOT make `iriai_compose.declarative` a required top-level import in iriai-build-v2.
- Do NOT wrap `ClaudeAgentRuntime` or other agent runtimes with a new `node_id`-aware signature.
- Do NOT add a CLI-only shim that changes context merge ordering or prompt assembly.
- Do NOT add wrapper-owned checkpoint/resume behavior or a `run(..., history=...)` compatibility path.

**Requirement IDs:** REQ-46, REQ-47, REQ-53

**Journey IDs:** J-1, J-2, J-3

**Citations:**

- **[decision]** `D-SF4-26`
  - Reasoning: The integration path stays intentionally thin and additive.
- **[decision]** `D-SF4-31`
  - Reasoning: The wrapper must consume the canonical ExecutionResult fields.
- **[decision]** `D-GR-23`
  - Reasoning: The wrapper must not introduce a breaking runtime signature or alternate context contract.
- **[decision]** `D-SF4-37`
  - Reasoning: The wrapper must consume SF-2 observability only and must not add a core checkpoint/resume ABI.
- **[code]** `iriai-compose/iriai_compose/runner.py:32-50`
  - Reasoning: The existing core runtime shows the non-breaking ContextVar precedent the wrapper must preserve.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:29-30,47`
  - Reasoning: SF-2 publishes `run(workflow, config, *, inputs=None)` plus observability output, but no mandatory core checkpoint/resume API, so the wrapper must stay inside that boundary.

### Journey Verifications

_No journey verifications defined for this subfeature._

### Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-57 | Schema expressiveness gaps in SF-1 or SF-2 still prevent one or more iriai-build-v2 workflow patterns from being represented declaratively. | high | STEP-8 keeps litmus-equivalence coverage across planning, develop, and bugfix workflows so gaps surface immediately. | STEP-3, STEP-4, STEP-5, STEP-8 |
| RISK-58 | ABI drift after SF-2 publishes the runtime boundary reintroduces a breaking `invoke(..., node_id=...)` assumption in SF-3, SF-4, or build-v2 integration code. | high | The architecture now names SF-2 as sole ABI owner, STEP-8 adds runtime-context regression tests, and STEP-10 forbids wrapper-level signature shims. | STEP-8, STEP-10 |
| RISK-59 | Hierarchical context merge order drifts from `workflow -> phase -> actor -> node`, causing prompts to differ from the expected migrated behavior. | high | STEP-3 through STEP-6 author YAML against the canonical order, and STEP-8 adds merge-order and duplicate-key regression tests. | STEP-3, STEP-4, STEP-5, STEP-6, STEP-8 |
| RISK-60 | Missing ArtifactPlugin nodes or incorrect Ask -> Artifact -> Hosting sequencing causes silent artifact or hosting regressions in migrated YAML. | medium | The workflow authoring steps keep artifact persistence explicit and STEP-8 verifies completeness and ordering. | STEP-3, STEP-4, STEP-5, STEP-6, STEP-8 |
| RISK-61 | SF-2's published observability surface is incomplete, leaving SF-4 without the phase metrics or execution-history detail needed to prove equivalence after removing core checkpoint/resume assumptions. | high | D-SF4-32 and D-SF4-37 keep SF-4 pinned to phase metrics plus execution history, and STEP-8 adds direct coverage over that published observability surface. | STEP-8 |
| RISK-62 | Adapter/runtime impedance mismatch prevents downstream consumers from wiring their existing services into the declarative plugin model cleanly. | medium | STEP-7 uses protocol-based adapters and STEP-8 includes runtime-bridge tests against mocked services. | STEP-7, STEP-8 |
| RISK-63 | Standalone `develop.yaml` planning phases drift structurally from `planning.yaml`, breaking the intended migration parity. | medium | STEP-4 preserves the standalone file while STEP-8 adds parity assertions between the shared planning segments. | STEP-4, STEP-8 |
| RISK-64 | Consumer integration scope expands beyond the intended thin wrapper and starts altering build-v2's existing imperative workflow path. | medium | STEP-10 keeps the wrapper additive, lazy-imported, and CLI-gated behind an optional `--yaml` flag. | STEP-10 |
| RISK-65 | Migration tests or the build-v2 wrapper reintroduce a core checkpoint/resume dependency even though SF-2 excludes it from the canonical ABI. | high | D-SF4-37 makes the boundary explicit, STEP-8 forbids checkpoint/resume-only tests, and STEP-10 forbids wrapper-owned resume shims. | STEP-8, STEP-10 |

### File Manifest

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
| `tests/migration/assertions.py` | create |
| `tests/migration/conftest.py` | create |
| `tests/migration/test_planning.py` | create |
| `tests/migration/test_develop.py` | create |
| `tests/migration/test_bugfix.py` | create |
| `tests/migration/test_yaml_roundtrip.py` | create |
| `tests/migration/test_plugin_instances.py` | create |
| `tests/migration/test_edge_transforms.py` | create |
| `tests/migration/test_runtime_bridge.py` | create |
| `tests/migration/test_runtime_context.py` | create |
| `tests/migration/test_litmus.py` | create |
| `tests/migration/test_phase_modes.py` | create |
| `tests/migration/test_error_ports.py` | create |
| `tests/migration/test_context_hierarchy.py` | create |
| `tests/migration/test_templates.py` | create |
| `tests/migration/test_artifact_writes.py` | create |
| `tests/migration/test_live_smoke.py` | create |
| `tests/fixtures/seed/migration_seed.json` | create |
| `tests/fixtures/seed/seed_loader.py` | create |
| `iriai-build-v2/src/iriai_build_v2/workflows/_declarative.py` | create |
| `iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py` | modify |

---

## SF-5: Composer App Foundation & Tools Hub
<!-- SF: composer-app-foundation -->

### Architecture

SF-5 Compose App Foundation rebased to canonical tools/compose + PostgreSQL contract. Five foundation tables only: workflows, workflow_versions, roles, output_schemas, custom_task_templates. In-process MutationHookRegistry exposes post-commit event hooks for SF-7 to subscribe to without SF-5 owning any reference-index rows. Plugin surfaces, SQLite, tools/iriai-workflows, and workflow_entity_refs are all out of scope for SF-5.

### Implementation Steps

#### STEP-35: Build the FastAPI + PostgreSQL backend at tools/compose/backend/ with exactly five foundation tables (workflows, workflow_versions, roles, output_schemas, custom_task_templates), canonical workflow CRUD + versioning, baseline library CRUD, runtime schema endpoint, mutation hook interface, and production-ready auth/logging/rate-limiting. No plugin tables, tools tables, or workflow_entity_refs rows are created here.
<!-- SF: composer-app-foundation | Original: STEP-40 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/backend/app/main.py` | create |
| `tools/compose/backend/app/config.py` | create |
| `tools/compose/backend/app/database.py` | create |
| `tools/compose/backend/app/auth.py` | create |
| `tools/compose/backend/app/dependencies/auth.py` | create |
| `tools/compose/backend/app/middleware/logging.py` | create |
| `tools/compose/backend/app/middleware/rate_limit.py` | create |
| `tools/compose/backend/app/models/workflow.py` | create |
| `tools/compose/backend/app/models/workflow_version.py` | create |
| `tools/compose/backend/app/models/role.py` | create |
| `tools/compose/backend/app/models/output_schema.py` | create |
| `tools/compose/backend/app/models/custom_task_template.py` | create |
| `tools/compose/backend/app/schemas/workflow.py` | create |
| `tools/compose/backend/app/services/hooks.py` | create |
| `tools/compose/backend/app/services/workflow_service.py` | create |
| `tools/compose/backend/app/services/role_service.py` | create |
| `tools/compose/backend/app/services/schema_service.py` | create |
| `tools/compose/backend/app/services/template_service.py` | create |
| `tools/compose/backend/app/routers/workflows.py` | create |
| `tools/compose/backend/app/routers/roles.py` | create |
| `tools/compose/backend/app/routers/schemas.py` | create |
| `tools/compose/backend/app/routers/templates.py` | create |
| `tools/compose/backend/app/routers/schema_export.py` | create |
| `tools/compose/backend/app/routers/health.py` | create |
| `tools/compose/backend/app/seed.py` | create |
| `tools/compose/backend/alembic/versions/0001_foundation_tables.py` | create |
| `tools/compose/backend/pyproject.toml` | create |
| `tools/compose/backend/Dockerfile` | create |
| `platform/deploy-console/deploy-console-service/app/database.py` | read |
| `iriai_compose/declarative/schema.py` | read |

**Instructions:**

1. Create tools/compose/backend/ with the canonical service layout: app/main.py, app/config.py, app/database.py (async SQLAlchemy engine, postgresql+psycopg:// normalized), app/auth.py (JWKS RS256 via auth-python using AUTH_SERVICE_PUBLIC_URL), app/dependencies/, app/middleware/ (correlation ID, structured JSON logging, per-user rate limiting), app/models/ (5 ORM models), app/schemas/ (Pydantic request/response), app/services/ (business logic + hooks.py), app/routers/, app/seed.py, alembic/. 2. Models: Workflow (id, name, description, yaml_content, current_version, is_valid, user_id, timestamps+soft-delete), WorkflowVersion (id, workflow_id FK cascade, version_number, yaml_content immutable, change_description, user_id, created_at — append-only, no soft-delete), Role (id, name, prompt TEXT, tools JSON list[str], model, effort, metadata JSON, user_id, timestamps+soft-delete), OutputSchema (id, name, description, json_schema JSON, user_id, timestamps+soft-delete), CustomTaskTemplate (id, name, description, subgraph_yaml TEXT, input_interface JSON, output_interface JSON, user_id, timestamps+soft-delete). 3. Alembic: single initial migration creates all 5 tables; version_table='alembic_version_compose'; include working downgrade(). 4. Implement MutationHookRegistry in app/services/hooks.py: frozen EntityMutationEvent dataclass (entity_kind, event_kind, entity_id, user_id), MutationCallback type alias, registry.register() + registry.emit() — module-level singleton mutation_hooks. Each service method calls mutation_hooks.emit() after await session.commit(). 5. API endpoints: full workflow CRUD + POST /versions + GET /versions + import + duplicate + export + validate; baseline role/schema/template CRUD with restore; GET /api/schema/workflow (live WorkflowConfig.model_json_schema()); GET /health + GET /ready. 6. Auth: AUTH_SERVICE_PUBLIC_URL for issuer; user_id from sub; cross-user access → 404; no raw yaml_content in logs. 7. System-seeded starters use user_id='__system__'; returned by ?starter=true query parameter.

**Acceptance Criteria:**

- Inspect Alembic migration: exactly 5 tables created (workflows, workflow_versions, roles, output_schemas, custom_task_templates); alembic_version_compose tracks the chain; no plugin tables, tools table, or workflow_entity_refs anywhere in the migration
- POST /api/workflows with valid YAML body returns 201 with workflow id; GET /api/workflows/{id} returns current_version: 1 and a workflow_versions row exists
- POST /api/workflows/{id}/versions with valid YAML appends an immutable WorkflowVersion row and increments current_version; previous version row unchanged
- GET /api/schema/workflow returns JSON Schema from WorkflowConfig.model_json_schema() at request time — includes inputs and outputs array definitions
- POST /api/workflows/{id}/validate with schema-invalid YAML returns 422 with {valid: false, errors: [{path, message}]}
- POST /api/workflows/{id}/import with unparseable YAML returns 422 with parse errors; zero database rows created
- GET /api/workflows/{id} for another user's workflow returns 404
- Exceeding per-user rate limit returns 429 with Retry-After header; structured log omits raw yaml_content
- mutation_hooks.register(spy) before POST /api/workflows → spy receives EntityMutationEvent(entity_kind='workflow', event_kind='created') after commit
- GET /health returns 200 without auth; GET /ready returns 503 when DB is unavailable
- SF-5 service code contains zero references to workflow_entity_refs table or model

**Counterexamples:**

- Do NOT create PluginType, PluginInstance, tools, workflow_entity_refs, or phase-template tables in the SF-5 migration — violates REQ-4
- Do NOT use SQLite — postgresql+psycopg:// exclusively
- Do NOT build the service under tools/iriai-workflows — canonical path is tools/compose/backend
- Do NOT emit mutation hook events before await session.commit() — premature emission on a rolled-back transaction feeds stale data to SF-7
- Do NOT add is_example column to base tables — system starters use user_id='__system__' and ?starter=true query param
- Do NOT treat GET /api/schema/workflow as a static file — must call WorkflowConfig.model_json_schema() live
- Do NOT add /api/plugins, /api/tools, or /api/{entity}/references/{id} — SF-7 surfaces
- Do NOT store Role.tools as UUIDs — plain string identifiers per iriai-compose Role contract

**Requirement IDs:** REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8, REQ-9, REQ-10, REQ-11, REQ-12, REQ-13, REQ-14, REQ-18

**Citations:**

- **[code]** `subfeatures/composer-app-foundation/prd.md:26`
  - Excerpt: SF-5 database scope is exactly five foundation tables: workflows, workflow_versions, roles, output_schemas, and custom_task_templates.
  - Reasoning: Direct PRD requirement for 5-table scope; rules out PluginType, PluginInstance, workflow_entity_refs
- **[code]** `subfeatures/composer-app-foundation/prd.md:40`
  - Excerpt: SF-5 must never create or update workflow_entity_refs rows — that responsibility belongs entirely to SF-7 via this hook interface.
  - Reasoning: Foundation for mutation hook interface design and SF-7 ownership boundary
- **[decision]** `D-SF5-R1`
  - Reasoning: Enforces 5-table scope as the canonical SF-5 database contract
- **[decision]** `D-SF5-R3`
  - Reasoning: MutationHookRegistry pattern decouples SF-5 persistence from SF-7 reference-index without tight coupling
- **[code]** `platform/deploy-console/deploy-console-service/app/database.py:13`
  - Reasoning: Async engine pattern reference for postgresql+psycopg:// normalization

#### STEP-36: Scaffold the React + Vite SPA at tools/compose/frontend/ with XP design system, ExplorerLayout, auth, routing, and baseline CRUD views for exactly four foundation entity types (Workflows, Roles, Output Schemas, Task Templates). No Plugins folder, plugin route, or plugin Zustand store.
<!-- SF: composer-app-foundation | Original: STEP-41 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/layouts/ExplorerLayout.tsx` | create |
| `tools/compose/frontend/src/components/sidebar/SidebarTree.tsx` | create |
| `tools/compose/frontend/src/components/sidebar/SidebarFolder.tsx` | create |
| `tools/compose/frontend/src/views/GridView.tsx` | create |
| `tools/compose/frontend/src/views/DetailsView.tsx` | create |
| `tools/compose/frontend/src/components/NewDropdown.tsx` | create |
| `tools/compose/frontend/src/components/ConfirmDialog.tsx` | create |
| `tools/compose/frontend/src/components/ContextMenu.tsx` | create |
| `tools/compose/frontend/src/components/MobileBlockScreen.tsx` | create |
| `tools/compose/frontend/src/components/EmptyState.tsx` | create |
| `tools/compose/frontend/src/components/SkeletonLoader.tsx` | create |
| `tools/compose/frontend/src/stores/entitiesStore.ts` | create |
| `tools/compose/frontend/src/stores/sidebarStore.ts` | create |
| `tools/compose/frontend/src/stores/uiStore.ts` | create |
| `tools/compose/frontend/src/api/client.ts` | create |
| `tools/compose/frontend/src/styles/windows-xp.css` | create |
| `tools/compose/frontend/package.json` | create |
| `tools/compose/frontend/vite.config.ts` | create |
| `tools/compose/frontend/tsconfig.json` | create |
| `tools/compose/frontend/Dockerfile` | create |

**Instructions:**

1. Create tools/compose/frontend/ with full Vite React project: vendored XP design system from deploy-console, src/styles/windows-xp.css with purple theme, @homelocal/auth with compose_ token prefix. 2. Layout: ExplorerLayout (sidebar + content pane), AddressBar, Toolbar, StatusBar components. 3. Sidebar: SidebarTree with exactly 4 foundation folders: Workflows, Roles, Output Schemas, Task Templates. No Plugins or Tools folder. 4. Routes: /workflows, /roles, /schemas, /templates only. /plugins route must not exist. 5. Zustand stores: entities (workflows, roles, schemas, templates), sidebar state, UI state. No plugin store. 6. Content: GridView, DetailsView with localStorage toggle, EmptyState, SkeletonLoader. 7. CRUD: NewDropdown, ConfirmDialog for soft-delete, ContextMenu, inline rename. 8. Search: 300ms debounce on name filter. 9. MobileBlockScreen at <768px. 10. Axios client with JWT Bearer interceptor matching STEP-40 API surface. 11. Add data-testid to every rendered element per coverage list.

**Acceptance Criteria:**

- Navigate to compose.iriai.app: MobileBlockScreen shown at <768px; ExplorerLayout shown at >=768px
- Sidebar tree shows exactly 4 foundation folders: Workflows, Roles, Output Schemas, Task Templates — no Plugins, no Tools
- Grid/Details view toggle works and preference persists in localStorage
- All 4 entity types support: create, rename, duplicate, soft-delete (ConfirmDialog shown before DELETE request)
- Search input filters by name with 300ms debounce
- Auth: unauthenticated -> OAuth redirect -> authenticated layout -> 401 token-expiry handling
- Navigating to /plugins returns 404 or redirect — the route does not exist

**Counterexamples:**

- Do NOT add a Plugins folder, /plugins route, or plugin Zustand store — plugin surfaces are SF-7
- Do NOT add Tools or Reference Checks folder — out of scope for SF-5
- Do NOT skip ConfirmDialog on soft-delete — direct DELETE without confirmation is a UX regression
- Do NOT cache workflow YAML in localStorage

**Requirement IDs:** REQ-1, REQ-15, REQ-16, REQ-17

**Citations:**

- **[code]** `subfeatures/composer-app-foundation/prd.md:38`
  - Excerpt: exactly four foundation folders in the Explorer-style sidebar: Workflows, Roles, Output Schemas, and Task Templates. Plugin pages, Tool Library pages, and reference-check UI do not ship in SF-5.
  - Reasoning: Direct PRD requirement; drives removal of Plugins folder and route from SF-5 shell
- **[decision]** `D-SF5-R1`
  - Reasoning: 5-table scope boundary enforced in frontend — only 4 entity types surfaced in sidebar

### Journey Verifications

_No journey verifications defined for this subfeature._

### Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-68 | Auth issuer mismatch — JWT validation uses internal Railway URL instead of public URL, causing all token verifications to fail in production | high | Use AUTH_SERVICE_PUBLIC_URL env var exclusively for issuer claim validation; document in deployment checklist; see MEMORY.md Internal vs Public URL mismatch production bug pattern | STEP-40 |
| RISK-69 | SF-5/SF-7 migration ordering — SF-7's workflow_entity_refs migration must run after SF-5's 5-table initial migration; running out of order breaks FK constraints or leaves reference index table absent when hooks fire | medium | SF-7 migration is a separate Alembic revision with explicit down_revision pointing to SF-5's initial revision ID; alembic_version_compose tracks the full chain; init script applies both in sequence | STEP-40, STEP-43 |
| RISK-71 | Mutation hook blocking — SF-7 callbacks fire synchronously after commit on the request thread; slow or erroring callbacks block API response or silently swallow exceptions | low | SF-7 callbacks must be fast in-memory index operations; emit() wraps each callback in try/except and logs errors without re-raising; heavy async work queued inside callback, not awaited inline | STEP-40, STEP-43 |
| RISK-65 | Schema expressiveness — some iriai-build-v2 patterns may not map cleanly to 3 node types + 4 phase modes | high | SF-1 plan validated 145+ nodes; SF-4 migration tests as litmus test | STEP-36, STEP-39 |
| RISK-66 | React Flow performance with 35-60 nodes + nested phases | medium | Virtualization, collapsed phases reduce visible nodes, lazy rendering | STEP-42 |
| RISK-67 | Inline Python transforms security (exec()) | low | Acceptable risk — same agents that run transforms already have full code execution | STEP-37 |
| RISK-70 | Cross-repo coordination — 5 repos must stay compatible | medium | Pin iriai-compose version in consumers, schema version field in YAML | STEP-45 |

### File Manifest

| Path | Action |
|------|--------|
| `tools/compose/backend/app/main.py` | create |
| `tools/compose/backend/app/config.py` | create |
| `tools/compose/backend/app/database.py` | create |
| `tools/compose/backend/app/auth.py` | create |
| `tools/compose/backend/app/dependencies/auth.py` | create |
| `tools/compose/backend/app/middleware/logging.py` | create |
| `tools/compose/backend/app/middleware/rate_limit.py` | create |
| `tools/compose/backend/app/models/workflow.py` | create |
| `tools/compose/backend/app/models/workflow_version.py` | create |
| `tools/compose/backend/app/models/role.py` | create |
| `tools/compose/backend/app/models/output_schema.py` | create |
| `tools/compose/backend/app/models/custom_task_template.py` | create |
| `tools/compose/backend/app/schemas/workflow.py` | create |
| `tools/compose/backend/app/services/hooks.py` | create |
| `tools/compose/backend/app/services/workflow_service.py` | create |
| `tools/compose/backend/app/services/role_service.py` | create |
| `tools/compose/backend/app/services/schema_service.py` | create |
| `tools/compose/backend/app/services/template_service.py` | create |
| `tools/compose/backend/app/routers/workflows.py` | create |
| `tools/compose/backend/app/routers/roles.py` | create |
| `tools/compose/backend/app/routers/schemas.py` | create |
| `tools/compose/backend/app/routers/templates.py` | create |
| `tools/compose/backend/app/routers/schema_export.py` | create |
| `tools/compose/backend/app/routers/health.py` | create |
| `tools/compose/backend/app/seed.py` | create |
| `tools/compose/backend/alembic/versions/0001_foundation_tables.py` | create |
| `tools/compose/backend/pyproject.toml` | create |
| `tools/compose/backend/Dockerfile` | create |
| `tools/compose/frontend/src/layouts/ExplorerLayout.tsx` | create |
| `tools/compose/frontend/src/components/sidebar/SidebarTree.tsx` | create |
| `tools/compose/frontend/src/components/sidebar/SidebarFolder.tsx` | create |
| `tools/compose/frontend/src/views/GridView.tsx` | create |
| `tools/compose/frontend/src/views/DetailsView.tsx` | create |
| `tools/compose/frontend/src/components/NewDropdown.tsx` | create |
| `tools/compose/frontend/src/components/ConfirmDialog.tsx` | create |
| `tools/compose/frontend/src/components/ContextMenu.tsx` | create |
| `tools/compose/frontend/src/components/MobileBlockScreen.tsx` | create |
| `tools/compose/frontend/src/components/EmptyState.tsx` | create |
| `tools/compose/frontend/src/components/SkeletonLoader.tsx` | create |
| `tools/compose/frontend/src/stores/entitiesStore.ts` | create |
| `tools/compose/frontend/src/stores/sidebarStore.ts` | create |
| `tools/compose/frontend/src/stores/uiStore.ts` | create |
| `tools/compose/frontend/src/api/client.ts` | create |
| `tools/compose/frontend/src/styles/windows-xp.css` | create |
| `tools/compose/frontend/package.json` | create |
| `tools/compose/frontend/vite.config.ts` | create |
| `tools/compose/frontend/tsconfig.json` | create |
| `tools/compose/frontend/Dockerfile` | create |
| `platform/toolshub/frontend/` | create |
| `platform/deploy-console/deploy-console-service/app/database.py` | read |
| `iriai_compose/declarative/schema.py` | read |

---

## SF-6: Workflow Editor & Canvas
<!-- SF: workflow-editor -->

### Architecture

# Technical Plan: SF-6 Workflow Editor & Canvas

## Repository Context

- The canonical home for the workflow editor SPA is `tools/compose/frontend/`, as established by SF-5 REQ-1 and REQ-15. `tools/iriai-workflows` is the stale placeholder repo that Cycle 5 retired; no editor source files belong there. All editor implementation files live under `tools/compose/frontend/src/features/editor/`.
- The SF-5 backend scaffold lands in `tools/compose/backend/` as a FastAPI + PostgreSQL + Alembic service. SQLite is not part of the compose stack at any layer. SF-5 uses PostgreSQL, normalizes connection strings to `postgresql+psycopg://`, tracks migrations in `alembic_version_compose`, and creates exactly five foundation tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`. No `workflow_entity_refs` table exists in SF-5.
- `iriai-compose` currently exposes only the imperative orchestration API from `iriai_compose.__init__`, `workflow.py`, and `runner.py`; the declarative workflow path remains additive and must not break `Workflow`, `Phase`, `Task`, or `DefaultWorkflowRunner`.
- `iriai-build-v2` remains the litmus-test workload: planning, develop, and bugfix workflows still build phase lists imperatively and must round-trip through the editor's nested YAML serializer without losing execution structure.

## Workflow Mutation Hook Boundary (SF-6 → SF-7)

SF-5 exposes an in-process mutation hook interface (REQ-18) on its service layer: after any successful database commit to `workflows`, `roles`, `output_schemas`, or `custom_task_templates`, SF-5 synchronously invokes registered typed callbacks (`created`, `updated`, `soft_deleted`, `restored`). SF-7 registers at service startup to maintain its `workflow_entity_refs` reference-index table via those hooks without modifying SF-5 code.

SF-6 (the frontend editor) mirrors this boundary at the store level: `editorStore` exposes a `subscribeSaved(fn: (workflowId: string) => void): () => void` method. After any successful `PUT /api/workflows/:id` response — whether triggered by manual save or auto-save — all registered subscriber callbacks receive the saved workflow ID. SF-7 frontend panels subscribe via `subscribeSaved` to invalidate and refresh their entity-reference queries. SF-6 never creates, updates, queries, or renders `workflow_entity_refs` data in any store action, API call, selector, or UI component.

## Contract Baseline Applied

- D-GR-22 is authoritative for this revision: YAML persists nested phase containment as `workflow.phases[]`, and each phase serializes `nodes`, `edges`, and `children`.
- Hook wiring is serialized only as ordinary edges using dot-notation endpoints such as `phase_pm.on_start` or `ask_pm.on_end`; there is no separate serialized hooks section and no serialized `port_type` field.
- `GET /api/schema/workflow` is the canonical composer schema source. Runtime editor validation, import preflight, and schema-aware inspectors read that endpoint instead of a bundled `workflow-schema.json`. Static schema snapshots are test fixtures only.

## Architecture Decisions

- The editor keeps a flat React Flow node and edge store keyed by `parentId` because that matches React Flow's rendering model and keeps canvas interactions cheap, but save/load always traverse a recursive phase tree to emit or hydrate nested YAML.
- Edge ownership is resolved by the lowest common containing phase of the source and target endpoints. That rule determines whether an edge belongs to `workflow.edges` or a specific `phase.edges`, and it is the core round-trip invariant for cross-phase connections.
- Hook-versus-data behavior is always derived from the source handle's container (`outputs` vs `hooks`). The client may cache a derived render kind on edge data for styling, but serializer, backend payloads, and validation inputs never persist that field.
- Editor bootstrap waits for both workflow content and `/api/schema/workflow` before enabling save, import, or validate. If schema fetch fails, the page stays view-only with a retry banner instead of silently falling back to a stale bundled schema.
- Collapsed phases and collapsed template groups remain lightweight metadata cards. Expanded groups still render real child nodes with `parentId`, so inspectors, selection, and auto-layout all operate on one real graph rather than nested mini-canvases.

### Implementation Steps

#### STEP-37: Build the editor's core data contract, schema loader, flat store, and nested YAML serializer around the D-GR-22 contract. Establishes the only source-of-truth boundaries: flat React Flow state in memory, nested phase YAML on save/load, `/api/schema/workflow` for schema awareness, and the subscribeSaved/notifyWorkflowSaved boundary hook for SF-7.
<!-- SF: workflow-editor | Original: STEP-1 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/store/editorStore.ts` | create |
| `tools/compose/frontend/src/features/editor/store/undoMiddleware.ts` | create |
| `tools/compose/frontend/src/features/editor/store/selectors.ts` | create |
| `tools/compose/frontend/src/features/editor/schema/schemaClient.ts` | create |
| `tools/compose/frontend/src/features/editor/schema/workflowContract.ts` | create |
| `tools/compose/frontend/src/features/editor/serialization/serializeWorkflow.ts` | create |
| `tools/compose/frontend/src/features/editor/serialization/deserializeWorkflow.ts` | create |
| `tools/compose/frontend/src/features/editor/serialization/autoLayout.ts` | create |
| `tools/compose/frontend/src/features/editor/validation/validationTypes.ts` | create |
| `iriai-compose/iriai_compose/__init__.py` | read |
| `iriai-compose/iriai_compose/workflow.py` | read |
| `iriai-compose/iriai_compose/runner.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/planning/workflow.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/develop/workflow.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/bugfix/workflow.py` | read |

**Instructions:**

Create `workflowContract.ts` with TypeScript helper types for the canonical persisted shape: `WorkflowDefinition` has `phases` and `edges`; `PhaseDefinition` has `nodes`, `edges`, and recursive `children`; `EdgeDefinition` has only `source`, `target`, `transform_fn?`, and `description?`. Keep the existing dict-shaped port model (`Record<string, PortDefinition>`) and remove any schema-facing `port_type` field. Add `schemaClient.ts` with `fetchWorkflowSchema(): Promise<JsonSchema>` that calls `GET /api/schema/workflow`, caches the latest successful response in store state, and exposes `schemaStatus` (`loading|ready|error`). In `editorStore.ts`, keep flat `nodes` and `edges` plus `collapsedGroups`, `openInspectors`, dirty state, and undo history. Add a `subscribeSaved(fn: (workflowId: string) => void): () => void` method that maintains a `Set<(id: string) => void>` of active subscribers and returns an unsubscribe function; expose a companion internal `notifyWorkflowSaved(workflowId: string): void` action that iterates the subscriber set. SF-7 frontend panels call `subscribeSaved` to invalidate their entity-reference query caches after a workflow is persisted. SF-6 never creates, updates, queries, or renders `workflow_entity_refs` data in any store action, selector, API call, or component. In `serializeWorkflow.ts`, walk the flat graph, build a recursive phase tree from `parentId`, place leaf nodes into `phase.nodes`, nested phase groups into `phase.children`, and store each edge at the lowest common containing phase. `undoMiddleware.ts` keeps full snapshot undo via `structuredClone` with a 50-entry cap. `autoLayout.ts` uses recursive dagre traversing `children`.

**Acceptance Criteria:**

- Export a workflow containing a nested phase inside another phase; the YAML shows `phases[0].nodes` for atomic children and `phases[0].children[0]` for the nested phase, with no sibling `phases` key inside that phase object.
- Export a workflow containing both data edges and hook edges; every serialized edge uses only `source`, `target`, optional `transform_fn`, and optional `description`, and no serialized edge contains `port_type`.
- Load the exported YAML back into the editor; the canvas restores the same node count, phase nesting, and edge endpoints, and hook edges re-render as dashed hook edges by inference from the source handle.
- Open the editor on a healthy backend; `GET /api/schema/workflow` completes before save/import/validate become enabled, and the UI exposes a ready schema status.
- Create two editor store instances via the factory; mutating one store does not change nodes, edges, or dirty state in the other store.

**Counterexamples:**

- DO NOT keep `yamlSchema.ts` or any other hand-maintained frontend file as the authoritative schema source.
- DO NOT serialize nested phases through a stale `phase.phases` property; persisted nesting is `children`.
- DO NOT serialize hook metadata into a separate hooks section or an edge `port_type` field.
- DO NOT store serialization-only hierarchy in React Flow state; keep the in-memory graph flat and derive nesting at save/load.
- DO NOT write, read, or reference `workflow_entity_refs` in any store action, API call, or selector; that table is owned entirely by SF-7.

**Requirement IDs:** REQ-13, REQ-14, REQ-15

**Journey IDs:** J-16, J-22

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: YAML nesting, edge-based hook serialization, and `/api/schema/workflow` as canonical schema delivery are authoritative.
  - Reasoning: This step is where the stale contract is fully removed from store, serializer, and schema-loading behavior.
- **[decision]** `SF-5 REQ-1 and REQ-15`
  - Excerpt: SF-5 must follow the accepted repo topology: `tools/compose/backend` for the FastAPI backend and `tools/compose/frontend` for the compose SPA. `tools/iriai-workflows` is not part of the approved implementation path.
  - Reasoning: All editor source files are new work in the compose frontend scaffold that SF-5 creates; the stale `tools/iriai-workflows` placeholder is not the implementation target.
- **[decision]** `SF-5 REQ-18`
  - Excerpt: SF-5 must expose a stable, in-process mutation hook interface; SF-5 must never create or update `workflow_entity_refs` rows — that responsibility belongs entirely to SF-7 via this hook interface.
  - Reasoning: The frontend store's `subscribeSaved` API mirrors the backend mutation hook contract so SF-7 panels can react to workflow persistence events without SF-6 touching the reference index.
- **[code]** `iriai-compose/iriai_compose/__init__.py:1-62`
  - Excerpt: The package exports the imperative `Workflow`, `Phase`, `Task`, and runner APIs today.
  - Reasoning: The declarative editor contract must stay additive and cannot replace the existing subclass API.
- **[research]** `https://reactflow.dev/learn/layouting/sub-flows`
  - Excerpt: React Flow uses parent-child relations for sub-flows rather than nested canvases.
  - Reasoning: That matches the flat store plus recursive serializer design.
- **[research]** `https://www.npmjs.com/package/js-yaml`
  - Excerpt: The package exposes `load` and `dump` for YAML parsing and emission.
  - Reasoning: The serializer and importer use the standard YAML tooling rather than inventing a custom emitter.

#### STEP-38: Implement the React Flow canvas shell, connection validation, and derived hook-edge behavior without reintroducing any serialized edge type field.
<!-- SF: workflow-editor | Original: STEP-2 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/canvas/EditorCanvas.tsx` | create |
| `tools/compose/frontend/src/features/editor/canvas/connectionValidator.ts` | create |
| `tools/compose/frontend/src/features/editor/canvas/canvasStyles.css` | create |
| `tools/compose/frontend/src/features/editor/nodes/nodeTypes.ts` | create |
| `tools/compose/frontend/src/features/editor/edges/edgeTypes.ts` | create |
| `tools/compose/frontend/src/features/editor/schema/workflowContract.ts` | read |
| `tools/compose/frontend/src/features/editor/store/editorStore.ts` | read |

**Instructions:**

Register `nodeTypes` and `edgeTypes` at module scope. `EditorCanvas.tsx` owns the `ReactFlow` instance, derives visible nodes by filtering descendants of collapsed groups, and wires `onConnect`, `onNodesChange`, `onEdgesChange`, selection mode, and viewport controls. In `onConnect`, resolve the source handle's port container first; if the handle belongs to `hooks`, stamp `edge.data.kind = 'hook'`, otherwise `edge.data.kind = 'data'`. That render hint stays client-only and is never included in serialized YAML or API payloads. `connectionValidator.ts` must reject cycles, reject connections into read-only template children, and reject data-to-hook mismatches. Keep hook/data inference centralized in one helper.

**Acceptance Criteria:**

- Draw a connection from `on_end` to another node's input; the canvas creates a dashed hook edge even though the underlying edge payload still serializes to the ordinary dot-notation edge shape.
- Draw a connection from a normal output to a normal input; the canvas creates a solid data edge with a type label and never assigns any serialized `port_type` field.
- Collapse a phase or template group; all descendant nodes and internal edges disappear from the visible React Flow arrays while remaining in store state for expand, save, and undo.
- Attempt a cycle or a connection into a read-only template child; the edge is rejected immediately.

**Counterexamples:**

- DO NOT persist `edge.data.kind` to YAML or send it to backend save/validate endpoints.
- DO NOT define `nodeTypes` or `edgeTypes` inside a React component body.
- DO NOT add a parallel hook-configuration UI to the canvas.

**Requirement IDs:** REQ-13

**Journey IDs:** J-16, J-17

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: Hook wiring remains edge-based with no separate serialized `port_type`.
  - Reasoning: Canvas connect behavior is where that contract is enforced live.
- **[research]** `https://reactflow.dev/api-reference/types/is-valid-connection`
  - Excerpt: `isValidConnection` is the synchronous validation hook used while connecting edges.
  - Reasoning: The step relies on React Flow's built-in connection validation path.

#### STEP-39: Implement the canvas-only collapsed and error overlays that remain valid under the new nested phase contract.
<!-- SF: workflow-editor | Original: STEP-3 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/nodes/ErrorBadge.tsx` | create |
| `tools/compose/frontend/src/features/editor/nodes/CollapsedGroupCard.tsx` | create |
| `tools/compose/frontend/src/features/editor/store/selectors.ts` | read |

**Instructions:**

Create `ErrorBadge.tsx` as a memoized top-right overlay that shows aggregated error counts from validation issues. Create `CollapsedGroupCard.tsx` as the shared collapsed representation for both phases and template groups. The phase variant must display the phase mode badge, group name, and descendant atomic node count derived from the recursive `children` tree; the template variant must display TEMPLATE metadata and descendant count. The card is a summary only: it does not render any hidden child topology.

**Acceptance Criteria:**

- A phase collapsed from the canvas renders as a fixed-size metadata card showing its mode, name, and child count, with no mini-canvas content inside it.
- A template group collapsed from the canvas renders as a compact TEMPLATE card showing the stamped child count.
- Nodes and groups with 10 or more issues show `9+` in the error badge instead of overflowing the badge.

**Counterexamples:**

- DO NOT render child node schematics or hook target summaries inside the collapsed card.
- DO NOT make the collapsed card responsible for serialization.

**Requirement IDs:** REQ-13

**Journey IDs:** J-17, J-20, J-22

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: Nested phase containment is the YAML contract; the collapsed card is only a UI projection of that tree.
  - Reasoning: The count and summary behavior depend on the recursive phase structure.
- **[decision]** `SF-5 REQ-1`
  - Excerpt: `tools/compose/frontend` is the canonical home for the compose SPA.
  - Reasoning: Both components are new surface area in the compose frontend under SF-5's scaffold; `tools/iriai-workflows` is not the target.

#### STEP-40: Create the thin React Flow node adapters that expose dict-shaped ports and derived hook handles while keeping visual rendering isolated from serialization logic.
<!-- SF: workflow-editor | Original: STEP-4 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/nodes/AskFlowNode.tsx` | create |
| `tools/compose/frontend/src/features/editor/nodes/BranchFlowNode.tsx` | create |
| `tools/compose/frontend/src/features/editor/nodes/PluginFlowNode.tsx` | create |
| `tools/compose/frontend/src/features/editor/nodes/TemplateFlowNode.tsx` | create |
| `tools/compose/frontend/src/features/editor/nodes/nodeTypes.ts` | modify |

**Instructions:**

Implement the four React Flow wrappers. Each wrapper maps `inputs`, `outputs`, and `hooks` through `Object.entries()` to create React Flow `Handle` components. Hook handles render on the bottom edge and must not serialize any separate hook metadata. Template wrapper nodes stay read-only and green-accented but expose the same port container semantics. Keep wrappers focused on handles, selection state, read-only state, and error overlays.

**Acceptance Criteria:**

- Ask, Branch, Plugin, and Template wrappers all render handles from dict-shaped port containers, and hook handles appear on the bottom edge with unique IDs.
- Selecting or validating a wrapped node updates selection and error overlays without changing the wrapper's serialized data shape.
- Read-only stamped template children render with disabled styling but still open read-only inspectors.

**Counterexamples:**

- DO NOT use array-only port iteration or `port.name` lookups; the port name is the dict key.
- DO NOT serialize any wrapper-only classes, IDs, or render hints into persisted YAML.

**Requirement IDs:** REQ-13

**Journey IDs:** J-16, J-18, J-20

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: Hook edges are inferred from hook ports rather than a serialized edge field.
  - Reasoning: The node wrappers must expose hook handles consistently so the rest of the editor can derive edge kind.
- **[research]** `https://reactflow.dev/api-reference/components/handle`
  - Excerpt: The `Handle` component defines connectable source and target ports on custom nodes.
  - Reasoning: The wrappers are thin `Handle` adapters over the visual primitives.

#### STEP-41: Implement recursive phase containers and nested phase editing so the UI's grouping behavior matches the persisted `nodes` plus `children` model.
<!-- SF: workflow-editor | Original: STEP-5 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/phases/PhaseContainer.tsx` | create |
| `tools/compose/frontend/src/features/editor/phases/PhaseLabelBar.tsx` | create |
| `tools/compose/frontend/src/features/editor/phases/LoopExitPorts.tsx` | create |
| `tools/compose/frontend/src/features/editor/serialization/serializeWorkflow.ts` | modify |
| `tools/compose/frontend/src/features/editor/serialization/deserializeWorkflow.ts` | modify |

**Instructions:**

Create `PhaseContainer.tsx` as the React Flow group node for sequential, map, fold, and loop phases. Expanded state renders a real bounded group with child nodes via `parentId`; collapsed state swaps to `CollapsedGroupCard`. Atomic Ask/Branch/Plugin nodes serialize into `phase.nodes`; nested phase containers serialize into `phase.children`. Update the serializer's edge placement algorithm to use the lowest common containing phase: edges wholly inside a phase land in that phase's `edges`, edges between sibling child phases land in the parent's `edges`, and workflow-level phase-to-phase edges stay in `workflow.edges`.

**Acceptance Criteria:**

- Create a phase inside another phase, save, and inspect the YAML; the inner phase is emitted in the outer phase's `children` array and not flattened into the workflow root.
- Connect a child node in one nested phase to a child node in a sibling nested phase; the saved edge lands in the parent phase's `edges` list.
- Collapse and re-expand nested phases independently; their child positions and boundary edges are preserved.
- Loop phases still show `condition_met` and `max_exceeded` exit ports, and their outgoing edges serialize as ordinary edges without `port_type`.

**Counterexamples:**

- DO NOT serialize nested phases through a stale `phase.phases` field.
- DO NOT flatten all edges into `workflow.edges`.
- DO NOT introduce a special loop-edge schema shape.

**Requirement IDs:** REQ-13, REQ-14

**Journey IDs:** J-17, J-21

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: YAML remains nested with `phases[].nodes` and `phases[].children`.
  - Reasoning: Phase creation, collapse, and serialization all directly implement that nesting contract.
- **[code]** `iriai-build-v2/src/iriai_build_v2/workflows/planning/workflow.py:24-56`
  - Excerpt: Planning workflow builds a concrete phase sequence today.
  - Reasoning: The editor must preserve phase boundaries faithfully enough to translate those existing workflows.

#### STEP-42: Render data and hook edges from the same serialized edge model and keep edge editing aligned to dot-notation endpoints only.
<!-- SF: workflow-editor | Original: STEP-6 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/edges/DataEdge.tsx` | create |
| `tools/compose/frontend/src/features/editor/edges/HookEdge.tsx` | create |
| `tools/compose/frontend/src/features/editor/edges/edgeTypes.ts` | modify |
| `tools/compose/frontend/src/features/editor/inspectors/EdgeInspector.tsx` | create |

**Instructions:**

Implement `DataEdge.tsx` and `HookEdge.tsx` as separate renderers over the same persisted edge shape. Both resolve behavior from derived `edge.data.kind` or source-handle lookup, not from serialized `port_type`. `EdgeInspector.tsx` edits only `transform_fn` and `description`. For hook edges, the inspector is read-only and explains that hook behavior is inferred from the source hook port.

**Acceptance Criteria:**

- Click a data edge; the edge inspector allows editing transform text and description, and saving writes only `transform_fn` and `description`.
- Click a hook edge; the inspector shows a read-only hook explanation and no transform editor.
- Export YAML after editing either edge type; no serialized edge includes `port_type`, hook arrays, or alternate edge classes.

**Counterexamples:**

- DO NOT persist hook-vs-data state as a schema field.
- DO NOT show transform editors for hook edges.
- DO NOT create a second hook serialization path outside ordinary `edges` arrays.

**Requirement IDs:** REQ-13

**Journey IDs:** J-16, J-22

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: Edge-based hook serialization with no serialized `port_type` is authoritative.
  - Reasoning: Edge renderers and the edge inspector are the most visible place that stale field could accidentally reappear.

#### STEP-43: Build the editor toolbar and palettes with explicit runtime-schema awareness so the user can see when schema-dependent actions are blocked.
<!-- SF: workflow-editor | Original: STEP-7 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/toolbar/PaintMenuBar.tsx` | create |
| `tools/compose/frontend/src/features/editor/toolbar/IconToolbar.tsx` | create |
| `tools/compose/frontend/src/features/editor/toolbar/ToolbarButton.tsx` | create |
| `tools/compose/frontend/src/features/editor/toolbar/ToolModeToggle.tsx` | create |
| `tools/compose/frontend/src/features/editor/palette/NodePalette.tsx` | create |
| `tools/compose/frontend/src/features/editor/palette/PaletteItem.tsx` | create |
| `tools/compose/frontend/src/features/editor/palette/RolePalette.tsx` | create |

**Instructions:**

Implement the toolbar and palette components under the `tools/compose/frontend` SPA scaffold created by SF-5. The save, import, and validate controls must read `schemaStatus` from store; when schema is loading they show a disabled/loading state, and when schema fails they remain disabled next to a retry affordance. Palette node creation uses dict-shaped default ports.

**Acceptance Criteria:**

- Load the editor before `/api/schema/workflow` returns; save, import, and validate remain disabled and a loading schema state is visible.
- Cause the schema endpoint to fail; the toolbar shows a retry affordance and does not silently enable schema-dependent actions.
- Create a new Ask, Branch, or Plugin node from the palette; each new node starts with dict-shaped default inputs, outputs, and hooks.

**Counterexamples:**

- DO NOT keep a hidden bundled `workflow-schema.json` fallback for production runtime use.
- DO NOT allow save or validate to run against an unknown schema state.

**Requirement IDs:** REQ-13

**Journey IDs:** J-16

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: `/api/schema/workflow` is the canonical composer schema source.
  - Reasoning: Toolbar enablement must respect that runtime dependency.
- **[decision]** `SF-5 REQ-15`
  - Excerpt: The compose frontend must live in `tools/compose/frontend` as a React 18 + TypeScript + Vite SPA.
  - Reasoning: All toolbar and palette UI files are new work in the compose frontend SPA scaffold; `tools/iriai-workflows` is not the target directory.

#### STEP-44: Implement the floating inspector window system so schema-driven editors, tethering, and z-ordering remain decoupled from the canvas render tree.
<!-- SF: workflow-editor | Original: STEP-8 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/inspectors/InspectorWindowManager.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/InspectorWindow.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/TetherLine.tsx` | create |

**Instructions:**

Keep the existing floating-inspector architecture but implement it under the actual `tools/compose/frontend` SPA. Inspector windows render in a portal, track z-order independently from React Flow state, and expose a consistent `readOnly` mode for stamped template children. The window manager must not introduce any schema or hook serialization logic.

**Acceptance Criteria:**

- Open multiple inspectors for nodes and edges; they can all stay open, be dragged independently, and maintain correct z-order.
- Pan or zoom the canvas; tether lines update their anchor positions without forcing full canvas rerenders.
- Open an inspector for a read-only template child; all fields display in disabled state with a read-only banner.

**Counterexamples:**

- DO NOT couple inspector window position to node serialization metadata.
- DO NOT close other inspectors automatically when a new one opens.

**Requirement IDs:** REQ-13

**Journey IDs:** J-16, J-20

**Citations:**

- **[decision]** `SF-5 REQ-15`
  - Excerpt: The compose frontend must live in `tools/compose/frontend` as a React 18 + TypeScript + Vite SPA.
  - Reasoning: The floating inspector system lives entirely within the compose frontend SPA scaffold established by SF-5.
- **[code]** `platform/deploy-console/deploy-console-frontend/src/app/stores/windowStore.ts:1-161`
  - Excerpt: The existing repo already uses a dedicated Zustand window store for draggable UI windows.
  - Reasoning: That local pattern supports the editor's portal-based inspector window manager.

#### STEP-45: Implement all inspector contents against the runtime schema contract and remove stale hook-configuration assumptions from forms.
<!-- SF: workflow-editor | Original: STEP-9 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/inspectors/AskInspector.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/BranchInspector.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/PluginInspector.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/PhaseInspector.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/InspectorActions.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/PromptTemplateEditor.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/InlineRoleCreator.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/InlineOutputSchemaCreator.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/OutputPathsEditor.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/SwitchFunctionEditor.tsx` | create |
| `tools/compose/frontend/src/features/editor/schema/schemaClient.ts` | read |

**Instructions:**

Build the node and phase inspectors pulling schema metadata from the schema client. Remove any stale hooks-section UI or hook target multi-selects from Ask, Plugin, Phase, or Template inspectors; hooks remain configurable only through canvas ports and edges. `PhaseInspector.tsx` edits the recursive phase container's own metadata and mode config. If schema status is not ready, inspector fields that depend on schema-derived constraints show loading/error states.

**Acceptance Criteria:**

- Open Ask, Plugin, and Phase inspectors; none of them shows a separate hooks section or hook target checklist.
- Open an edge inspector for a hook edge; it shows read-only hook behavior text instead of editable hook config.
- Bring the schema endpoint down and open an inspector that needs schema-derived field hints; the relevant fields show an explicit loading/error state.

**Counterexamples:**

- DO NOT add a `Hooks` tab, hook multiselect, or any other parallel hook authoring mechanism to inspectors.
- DO NOT silently fall back to a bundled static schema file when schema-derived inspector fields are needed.
- DO NOT let phase inspectors mutate child-node membership directly.

**Requirement IDs:** REQ-13, REQ-14

**Journey IDs:** J-16, J-17, J-18

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: Remove stale separate-hooks-section assumptions and use `/api/schema/workflow` as the canonical schema source.
  - Reasoning: Inspector forms were one of the stale-artifact areas explicitly called out by the feedback.

#### STEP-46: Implement client-side schema validation and server validation plumbing against the runtime endpoint contract.
<!-- SF: workflow-editor | Original: STEP-10 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/validation/schemaValidator.ts` | create |
| `tools/compose/frontend/src/features/editor/validation/clientValidator.ts` | create |
| `tools/compose/frontend/src/features/editor/validation/ValidationPanel.tsx` | create |
| `tools/compose/frontend/src/features/editor/store/editorStore.ts` | modify |

**Instructions:**

Use AJV plus the fetched `/api/schema/workflow` payload for Tier 1 client validation. Compile the validator only after schema load succeeds. Tier 1 validates the nested YAML payload produced by `serializeWorkflow.ts`. Tier 2 posts the same serialized YAML to `POST /api/workflows/:id/validate` and merges backend issues into the same validation panel. Add explicit client-side legacy-contract checks: reject imported YAML containing `port_type`, separate `hooks` blocks outside normal port containers, or stale `phases` keys where `children` is required.

**Acceptance Criteria:**

- Click Validate on a valid workflow after schema load; client and server validation both run against the same nested YAML payload and populate the same panel.
- Import or validate YAML containing an edge `port_type` field; the validation panel shows a targeted migration error.
- Import or validate YAML containing a stale separate hooks section; the panel shows a targeted migration error.
- Click Go to for an issue; the canvas centers the relevant node, edge, or phase.

**Counterexamples:**

- DO NOT compile AJV against a checked-in `workflow-schema.json` at runtime.
- DO NOT validate raw React Flow edge objects that still contain client-only render hints.
- DO NOT silently discard stale `port_type` or separate hooks fields during import.

**Requirement IDs:** REQ-15

**Journey IDs:** J-22

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: Static `workflow-schema.json` is build/test only.
  - Reasoning: Validation must compile against the runtime schema endpoint, not a bundled file.

#### STEP-47: Implement save, auto-save, import, and export around the nested YAML payload and runtime schema readiness checks. Fires post-save notifications for SF-7 reference-index refresh. SF-6 never touches `workflow_entity_refs`.
<!-- SF: workflow-editor | Original: STEP-11 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/hooks/useAutoSave.ts` | create |
| `tools/compose/frontend/src/features/editor/dialogs/ImportConfirmDialog.tsx` | create |
| `tools/compose/frontend/src/features/editor/serialization/serializeWorkflow.ts` | read |
| `tools/compose/frontend/src/features/editor/schema/schemaClient.ts` | read |

**Instructions:**

Keep manual save and auto-save, but make both paths depend on a ready schema state and the nested YAML serializer. Save and auto-save always serialize the current canvas to nested YAML before `PUT /api/workflows/:id`. Export uses the same serialized YAML string. Import shows a confirmation dialog, parses YAML, runs the legacy-contract preflight from STEP-10, validates against the runtime-fetched schema, then hydrates the canvas. If the user imports an old file containing `port_type`, a top-level hooks section, or stale nested-phase keys, reject it with a targeted migration message. After each successful `PUT /api/workflows/:id` response, call `store.notifyWorkflowSaved(workflowId)` to invoke all registered `subscribeSaved` callbacks so that SF-7 frontend panels can invalidate their entity-reference query caches. The save request body and response body must never include or process any `workflow_entity_refs` field; reference-index maintenance is entirely SF-7's responsibility via its `subscribeSaved` subscription.

**Acceptance Criteria:**

- Save a workflow containing nested phases and hook edges; the backend receives nested YAML, and reloading the page restores the same hierarchy and hook wiring.
- Export a workflow and inspect the file; nested phases use `children`, hook edges remain ordinary edges, and no `port_type` field appears anywhere.
- Import a stale file with `port_type`; the editor refuses to replace the canvas and shows a migration error dialog or toast.
- Leave the editor idle for 30 seconds after a change; auto-save serializes the same nested YAML payload used by manual save.
- After a successful manual save, any component subscribed via `store.subscribeSaved()` receives the saved workflow ID, and no `workflow_entity_refs` field appears in the PUT `/api/workflows/:id` request body or response body.

**Counterexamples:**

- DO NOT keep a separate export serializer path that can drift from save/import/validate.
- DO NOT accept legacy contract fields by silently dropping them.
- DO NOT allow save or import to proceed before schema readiness is established.
- DO NOT write, send, or expect `workflow_entity_refs` fields in any save request or response; that table is SF-7's exclusive domain.

**Requirement IDs:** REQ-13, REQ-15

**Journey IDs:** J-16, J-22

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: Nested phase containment and edge-based hook serialization are the persisted contract.
  - Reasoning: All persistence paths must use the same emitted YAML shape.
- **[decision]** `SF-5 REQ-18`
  - Excerpt: SF-5 must expose a stable, in-process mutation hook interface; SF-5 must never create or update `workflow_entity_refs` rows.
  - Reasoning: The frontend store's `notifyWorkflowSaved` call after PUT success mirrors the backend mutation hook boundary so SF-7 can maintain the reference index without SF-6 touching it.
- **[code]** `iriai-compose/iriai_compose/workflow.py:33-70`
  - Excerpt: The existing imperative API models workflows as phase sequences.
  - Reasoning: Save/load round-trip must preserve that structure when translated into declarative YAML.

#### STEP-48: Implement selection-rectangle phase creation and regrouping so newly created phases immediately fit the nested serializer's `nodes` and `children` rules.
<!-- SF: workflow-editor | Original: STEP-12 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/canvas/SelectionRectangle.tsx` | create |
| `tools/compose/frontend/src/features/editor/store/editorStore.ts` | modify |

**Instructions:**

Create the marching-ants rectangle for select mode. When the drag ends with one or more enclosed items, create a phase group node, assign enclosed atomic nodes and enclosed nested phases to that new parent's `parentId`, and convert their positions to phase-relative coordinates. Reject selections that mix read-only template children with editable nodes. Ensure the new parent/child relationships are exactly what the serializer later maps to `phase.nodes` and `phase.children`.

**Acceptance Criteria:**

- Draw a selection around several nodes on the canvas; a new phase appears immediately and those nodes serialize under that phase on save.
- Draw a selection around a nested phase and some sibling nodes inside the same parent phase; the new wrapper phase preserves the nested phase as a child group rather than flattening it.
- Attempt to include read-only template children in a new phase selection; the action is rejected.

**Counterexamples:**

- DO NOT flatten nested phase children into atomic nodes during regrouping.
- DO NOT allow regrouping of read-only template children.

**Requirement IDs:** REQ-13, REQ-14

**Journey IDs:** J-17

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: Nested phase containment is the canonical persisted shape.
  - Reasoning: Phase creation must manipulate the same parent/child structure the serializer emits.

#### STEP-49: Integrate templates, library promotion, and drag-and-drop with the same nested YAML and hook-edge rules as the main canvas.
<!-- SF: workflow-editor | Original: STEP-13 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/hooks/useDragAndDrop.ts` | create |
| `tools/compose/frontend/src/features/editor/dialogs/PromotionDialog.tsx` | create |
| `tools/compose/frontend/src/features/editor/dialogs/SaveAsTemplateDialog.tsx` | create |

**Instructions:**

Implement drag-and-drop for primitives, roles, and templates. Template stamping creates a read-only group node plus cloned child nodes with `parentId` and the same ordinary port containers; hook edges inside stamped content remain ordinary edges with derived kind. `SaveAsTemplateDialog` must serialize selected content using `nodes`, `edges`, and `children` where needed, never as a flat node dump with a parallel hooks block.

**Acceptance Criteria:**

- Drag a template onto the canvas; the stamped group expands to real read-only child nodes, and saving the workflow preserves their hook edges through the ordinary edge list.
- Select a subgraph containing a nested phase and save it as a template; the template payload preserves nested `children` rather than flattening the phase.
- Detach a stamped template group; its child nodes become editable without changing the persisted edge contract.

**Counterexamples:**

- DO NOT serialize template content through a separate hooks block or a `port_type` edge field.
- DO NOT flatten nested phase content when saving a template.

**Requirement IDs:** REQ-13, REQ-14

**Journey IDs:** J-20

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: Edge-based hook serialization and nested phase containment are the canonical shape everywhere.
  - Reasoning: Template save and template stamp flows must use the same shape as the main workflow serializer.

#### STEP-50: Add keyboard shortcuts and focus handling that respect schema load state, floating inspectors, and read-only template children.
<!-- SF: workflow-editor | Original: STEP-14 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/hooks/useKeyboardShortcuts.ts` | create |

**Instructions:**

Implement canvas-scoped shortcuts for save, undo, redo, delete, zoom, and validate. Save and validate shortcuts must respect the same schema readiness checks as the toolbar buttons. When a text input or code editor inside an inspector has focus, shortcut handling passes through to the field. Delete must skip read-only template children and collapsed-group placeholders.

**Acceptance Criteria:**

- Press Ctrl+S when schema is ready; the same save flow as the toolbar button runs.
- Press Ctrl+S when schema loading failed; the shortcut does not bypass the disabled state.
- Press Delete with a read-only template child selected; nothing is removed.

**Counterexamples:**

- DO NOT let keyboard shortcuts bypass schema readiness or read-only protections.
- DO NOT steal Ctrl+Z or Delete from focused text inputs inside inspectors.

**Requirement IDs:** REQ-13

**Journey IDs:** J-16, J-22

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: Schema readiness is part of the canonical runtime contract for the editor.
  - Reasoning: Shortcuts must respect the same schema gates as visible UI controls.

#### STEP-51: Assemble the workflow editor page in the `tools/compose/frontend` SPA and wire the schema endpoint into page bootstrap.
<!-- SF: workflow-editor | Original: STEP-15 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/WorkflowEditorPage.tsx` | create |
| `tools/compose/frontend/src/features/editor/schema/schemaClient.ts` | read |
| `tools/compose/frontend/src/features/editor/store/editorStore.ts` | read |

**Instructions:**

Build `WorkflowEditorPage.tsx` as the route mounted at `/workflows/:id/edit` inside the authenticated `tools/compose/frontend` shell created by SF-5. On mount, fetch workflow YAML and `GET /api/schema/workflow` in parallel. Hold the page in loading state until both complete; on schema failure, keep the canvas view-only and show retry controls plus a clear schema-source message. Once both are ready, hydrate the editor store, mount toolbar, palette, canvas, inspectors, and validation panel, and wire save/validate/import/export actions to the nested serializer and schema-aware validator.

**Acceptance Criteria:**

- Navigate to `/workflows/:id/edit` while authenticated; the page loads workflow data and `/api/schema/workflow`, then renders the fully interactive editor.
- Navigate to the page when the schema endpoint fails; the page shows a schema error banner and retry control, and save/import/validate stay disabled.
- Navigate while unauthenticated; auth flow redirects the user before editor data requests run.

**Counterexamples:**

- DO NOT enable editing controls before the schema endpoint resolves.
- DO NOT embed hard-coded schema JSON into the page bundle as a production fallback.
- DO NOT use `tools/iriai-workflows` as the frontend host; the canonical SPA is `tools/compose/frontend`.

**Requirement IDs:** REQ-13

**Journey IDs:** J-16

**Citations:**

- **[decision]** `D-GR-22`
  - Excerpt: `/api/schema/workflow` is the canonical composer schema source.
  - Reasoning: Page bootstrap must fetch that endpoint before enabling authoring behavior.
- **[decision]** `SF-5 REQ-1 and REQ-15`
  - Excerpt: The compose frontend must live in `tools/compose/frontend` as a React 18 + TypeScript + Vite SPA using `@homelocal/auth`.
  - Reasoning: The editor page sits behind the existing auth-react integration in the compose SPA and depends on the shell that SF-5 creates.

### Journey Verifications

**Journey J-16:**

- Step 1:
  - [api] GET /api/schema/workflow returns 200 with JSON Schema document before save/import/validate controls become enabled
  - [browser] Element [data-testid='editor-schema-status-ready'] is visible before [data-testid='editor-toolbar-save'] becomes enabled
  - Test IDs: editor-schema-status-ready, editor-toolbar-save
- Step 2:
  - [browser] Element [data-testid='editor-canvas'] is visible and dropping [data-testid='editor-palette-ask'] creates Element [data-testid='ask-node-{id}']
  - Test IDs: editor-canvas, editor-palette-ask, ask-node-{id}
- Step 3:
  - [browser] Double-clicking the node opens Element [data-testid='inspector-{id}'] with tether [data-testid='inspector-{id}-tether']
  - Test IDs: inspector-{id}, inspector-{id}-tether
- Step 4:
  - [browser] Connecting a normal output to a normal input creates Element [data-testid='edge-{id}'] with [data-testid='edge-{id}-type-label']
  - Test IDs: edge-{id}, edge-{id}-type-label
- Step 5:
  - [browser] Connecting from hook port [data-testid='port-{nodeId}-on_end'] creates Element [data-testid='edge-{id}'] with hook styling and no type label
  - Test IDs: port-{nodeId}-on_end, edge-{id}
- Step 6:
  - [browser] Pressing Ctrl+S enables green save toast and clears [data-testid='editor-toolbar-save-dirty']
  - Test IDs: editor-toolbar-save, editor-toolbar-save-dirty
- Step 7:
  - [api] GET /api/workflows/:id/export returns nested YAML with ordinary edge entries and no `port_type` fields
  - [browser] After reload, Element [data-testid='editor-canvas'] restores all previously saved nodes and edges
  - Test IDs: editor-canvas

**Journey J-17:**

- Step 1:
  - [browser] Element [data-testid='selection-rectangle'] appears while dragging in select mode
  - Test IDs: selection-rectangle
- Step 2:
  - [browser] Creating a phase from selected nodes renders Element [data-testid='phase-{id}'] with Element [data-testid='phase-{id}-mode-badge']
  - Test IDs: phase-{id}, phase-{id}-mode-badge
- Step 3:
  - [browser] Nesting another phase inside the first renders Element [data-testid='phase-{inner-id}'] inside [data-testid='phase-{id}']
  - Test IDs: phase-{id}, phase-{inner-id}
- Step 4:
  - [browser] Collapsing the outer phase shows [data-testid='collapsed-group-{id}'] and hides descendant phase DOM nodes
  - Test IDs: collapsed-group-{id}, collapsed-group-{id}-node-count
- Step 5:
  - [api] Saving the workflow emits nested YAML where the inner phase is stored in the outer phase's `children` array
  - Test IDs: phase-{id}, phase-{inner-id}

**Journey J-20:**

- Step 1:
  - [browser] Dropping a template creates Element [data-testid='template-node-{id}'] and visible read-only child nodes
  - Test IDs: template-node-{id}, ask-node-{childId}
- Step 2:
  - [browser] Read-only child inspector shows [data-testid='inspector-{childId}-readonly-banner'] and disabled fields
  - Test IDs: inspector-{childId}, inspector-{childId}-readonly-banner
- Step 3:
  - [browser] Collapsing the template group shows [data-testid='collapsed-group-{id}-template-badge']
  - Test IDs: collapsed-group-{id}, collapsed-group-{id}-template-badge
- Step 4:
  - [api] Saving the workflow preserves the stamped template group's internal hook and data edges through the ordinary edge lists with no `port_type` field
  - Test IDs: template-node-{id}

**Journey J-22:**

- Step 1:
  - [browser] Element [data-testid='validation-panel'] shows schema or structural issues after clicking [data-testid='editor-toolbar-validate']
  - Test IDs: validation-panel, editor-toolbar-validate
- Step 2:
  - [browser] Importing stale YAML with `port_type` shows [data-testid='import-confirm-dialog-error'] and does not replace the canvas
  - Test IDs: import-confirm-dialog, import-confirm-dialog-error
- Step 3:
  - [browser] Importing stale YAML with a separate hooks section shows a migration issue in [data-testid='validation-panel']
  - Test IDs: validation-panel
- Step 4:
  - [browser] When `/api/schema/workflow` fails, [data-testid='editor-schema-status-error'] and [data-testid='editor-schema-retry'] are visible while save/import/validate controls stay disabled
  - Test IDs: editor-schema-status-error, editor-schema-retry, editor-toolbar-save, editor-toolbar-validate

### Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-73 | Nested phase edge ownership can be miscomputed, causing cross-phase wires to jump scopes or duplicate on round-trip. | high | Centralize lowest-common-ancestor edge ownership in the serializer and verify it against planning, develop, and bugfix fixture graphs before wiring save/import. | STEP-1, STEP-5, STEP-11 |
| RISK-74 | Derived hook-edge inference can drift between canvas rendering, validation, and serialization if different helpers resolve port containers differently. | high | Use a single `resolvePortContainer` helper everywhere and forbid any serialized edge kind or `port_type` fallback. | STEP-1, STEP-2, STEP-6, STEP-10 |
| RISK-75 | The canonical schema endpoint may be unavailable, leaving the editor without authoritative runtime schema data. | high | Block save/import/validate until `/api/schema/workflow` loads successfully and surface explicit retry UX instead of silently using bundled schema. | STEP-1, STEP-7, STEP-10, STEP-11, STEP-15 |
| RISK-76 | Large expanded workflows can become sluggish because the builder has to render many grouped nodes and derived edge labels at once. | medium | Keep wrappers memoized, filter collapsed descendants from the visible canvas, and maintain collapsed-group cards as lightweight metadata only. | STEP-2, STEP-3, STEP-4, STEP-5 |
| RISK-77 | Undo/redo can desynchronize with floating inspectors when debounced form edits race snapshot creation. | medium | Snapshot only committed mutations, flush pending inspector edits before undo, and have inspectors always reread node state from store after undo/redo. | STEP-1, STEP-8, STEP-9, STEP-14 |
| RISK-78 | Read-only template children may still be mutated indirectly through regrouping, keyboard shortcuts, or connection creation. | medium | Enforce read-only checks in connection validation, selection regrouping, delete shortcuts, and store mutation helpers. | STEP-2, STEP-4, STEP-12, STEP-14 |
| RISK-79 | Sibling artifact drift could reintroduce stale contract language if SF-1, SF-2, or SF-5 are not rewritten consistently to D-GR-22. | medium | Treat `/api/schema/workflow` and the nested edge contract as hard external dependencies and add contract-level acceptance checks in editor bootstrap and validation. | STEP-1, STEP-7, STEP-10, STEP-15 |
| RISK-80 | The editor depends on the SF-5 compose foundation scaffold (`tools/compose/frontend` + `tools/compose/backend` with PostgreSQL) existing before any editor files can compile or integrate. Any residual `tools/iriai-workflows` scaffolding in the environment creates a parallel stale app tree. | high | Sequence the editor strictly after the `tools/compose/frontend` + `tools/compose/backend` scaffold that SF-5 creates. Verify the compose Vite + React + auth-react entry point and the `GET /api/schema/workflow` endpoint are healthy before starting any SF-6 work. Confirm no stale `tools/iriai-workflows` application code exists alongside the compose scaffold. | STEP-1, STEP-2, STEP-7, STEP-15 |
| RISK-81 | Legacy YAML files with `port_type`, separate hooks sections, or stale nesting keys may still exist in user projects and fail import unexpectedly. | medium | Reject those files with targeted migration messaging during import and manual validation instead of silently attempting a lossy coercion. | STEP-10, STEP-11 |

### File Manifest

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/editor/store/editorStore.ts` | create |
| `tools/compose/frontend/src/features/editor/store/undoMiddleware.ts` | create |
| `tools/compose/frontend/src/features/editor/store/selectors.ts` | create |
| `tools/compose/frontend/src/features/editor/schema/schemaClient.ts` | create |
| `tools/compose/frontend/src/features/editor/schema/workflowContract.ts` | create |
| `tools/compose/frontend/src/features/editor/serialization/serializeWorkflow.ts` | create |
| `tools/compose/frontend/src/features/editor/serialization/deserializeWorkflow.ts` | create |
| `tools/compose/frontend/src/features/editor/serialization/autoLayout.ts` | create |
| `tools/compose/frontend/src/features/editor/validation/validationTypes.ts` | create |
| `tools/compose/frontend/src/features/editor/validation/schemaValidator.ts` | create |
| `tools/compose/frontend/src/features/editor/validation/clientValidator.ts` | create |
| `tools/compose/frontend/src/features/editor/validation/ValidationPanel.tsx` | create |
| `tools/compose/frontend/src/features/editor/canvas/EditorCanvas.tsx` | create |
| `tools/compose/frontend/src/features/editor/canvas/connectionValidator.ts` | create |
| `tools/compose/frontend/src/features/editor/canvas/canvasStyles.css` | create |
| `tools/compose/frontend/src/features/editor/canvas/SelectionRectangle.tsx` | create |
| `tools/compose/frontend/src/features/editor/nodes/nodeTypes.ts` | create |
| `tools/compose/frontend/src/features/editor/nodes/ErrorBadge.tsx` | create |
| `tools/compose/frontend/src/features/editor/nodes/CollapsedGroupCard.tsx` | create |
| `tools/compose/frontend/src/features/editor/nodes/AskFlowNode.tsx` | create |
| `tools/compose/frontend/src/features/editor/nodes/BranchFlowNode.tsx` | create |
| `tools/compose/frontend/src/features/editor/nodes/PluginFlowNode.tsx` | create |
| `tools/compose/frontend/src/features/editor/nodes/TemplateFlowNode.tsx` | create |
| `tools/compose/frontend/src/features/editor/phases/PhaseContainer.tsx` | create |
| `tools/compose/frontend/src/features/editor/phases/PhaseLabelBar.tsx` | create |
| `tools/compose/frontend/src/features/editor/phases/LoopExitPorts.tsx` | create |
| `tools/compose/frontend/src/features/editor/edges/DataEdge.tsx` | create |
| `tools/compose/frontend/src/features/editor/edges/HookEdge.tsx` | create |
| `tools/compose/frontend/src/features/editor/edges/edgeTypes.ts` | create |
| `tools/compose/frontend/src/features/editor/toolbar/PaintMenuBar.tsx` | create |
| `tools/compose/frontend/src/features/editor/toolbar/IconToolbar.tsx` | create |
| `tools/compose/frontend/src/features/editor/toolbar/ToolbarButton.tsx` | create |
| `tools/compose/frontend/src/features/editor/toolbar/ToolModeToggle.tsx` | create |
| `tools/compose/frontend/src/features/editor/palette/NodePalette.tsx` | create |
| `tools/compose/frontend/src/features/editor/palette/PaletteItem.tsx` | create |
| `tools/compose/frontend/src/features/editor/palette/RolePalette.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/InspectorWindowManager.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/InspectorWindow.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/TetherLine.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/AskInspector.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/BranchInspector.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/PluginInspector.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/PhaseInspector.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/EdgeInspector.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/InspectorActions.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/PromptTemplateEditor.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/InlineRoleCreator.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/InlineOutputSchemaCreator.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/OutputPathsEditor.tsx` | create |
| `tools/compose/frontend/src/features/editor/inspectors/SwitchFunctionEditor.tsx` | create |
| `tools/compose/frontend/src/features/editor/hooks/useAutoSave.ts` | create |
| `tools/compose/frontend/src/features/editor/hooks/useKeyboardShortcuts.ts` | create |
| `tools/compose/frontend/src/features/editor/hooks/useDragAndDrop.ts` | create |
| `tools/compose/frontend/src/features/editor/dialogs/ImportConfirmDialog.tsx` | create |
| `tools/compose/frontend/src/features/editor/dialogs/PromotionDialog.tsx` | create |
| `tools/compose/frontend/src/features/editor/dialogs/SaveAsTemplateDialog.tsx` | create |
| `tools/compose/frontend/src/features/editor/WorkflowEditorPage.tsx` | create |
| `iriai-compose/iriai_compose/__init__.py` | read |
| `iriai-compose/iriai_compose/workflow.py` | read |
| `iriai-compose/iriai_compose/runner.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/planning/workflow.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/develop/workflow.py` | read |
| `iriai-build-v2/src/iriai_build_v2/workflows/bugfix/workflow.py` | read |
| `templates/fastapi-postgres/app/main.py` | read |
| `first-party-apps/events/events-backend/app/dependencies/auth.py` | read |
| `PACKAGES.md` | read |

---

## SF-7: Libraries & Registries
<!-- SF: libraries-registries -->

### Architecture

SF-7 libraries-registries owns the `workflow_entity_refs` reference-index extension end-to-end: a single follow-on Alembic revision (`0002_sf7_libraries_registry_extensions.py`) that sits above SF-5's five-table base and adds `tools`, `workflow_entity_refs`, and the `actor_slots` column on `custom_task_templates`; a `workflow_reference_index` service that atomically refreshes that table on every workflow mutation; and a `references` router that all pre-delete preflight checks query. SF-5's `workflows.py` router is the only SF-5 file SF-7 touches — it receives the reindex hook call from the shared service so that reference state stays in sync inside the same transaction. SF-5 is bounded to exactly five tables (`workflows`, `workflow_versions`, `roles`, `output_schemas`, `custom_task_templates`), uses `tools/compose/backend` and `tools/compose/frontend` topology, and never sees `workflow_entity_refs`, `tools`, `actor_slots`, plugin tables, or SQLite. All library UI (roles, schemas, templates, tools) and the picker exports (RolePicker, SchemaPicker, TemplateBrowser) live inside `tools/compose/frontend`. Tool delete checks remain role-backed because `Role.tools` is `list[str]`; workflow-backed entity ref tracking covers roles, schemas, and task templates only. Actor slots are persisted end-to-end: migration column in STEP-1, API contract in STEP-2, editor surface in STEP-6.

### Implementation Steps

#### STEP-52: Extend the compose backend with the SF-7 persistence layer without violating the SF-5 foundation contract. This step adds the libraries-owned database objects in a follow-on Alembic revision so SF-5 still owns exactly five base tables while SF-7 owns `tools`, `workflow_entity_refs`, and `actor_slots` persistence.
<!-- SF: libraries-registries | Original: STEP-1 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/backend/alembic/versions/0002_sf7_libraries_registry_extensions.py` | create |
| `tools/compose/backend/app/models/tool.py` | create |
| `tools/compose/backend/app/models/workflow_entity_ref.py` | create |
| `tools/compose/backend/app/models/custom_task_template.py` | modify |
| `tools/compose/backend/app/models/__init__.py` | modify |

**Instructions:**

Create a single SF-7 Alembic revision after the SF-5 initial migration that adds the `tools` table, the `workflow_entity_refs` table, and the `actor_slots` JSONB column on `custom_task_templates`. Keep the migration PostgreSQL-only (`postgresql+psycopg://`), track revisions in `alembic_version_compose`, include a working `downgrade()` that removes only the SF-7 additions, and leave SF-5's initial five-table migration unchanged so its acceptance checks remain valid. `workflow_entity_refs` columns: `id UUID PK`, `workflow_id UUID FK workflows(id) ON DELETE CASCADE`, `entity_type VARCHAR(32)` (`role`|`schema`|`template`), `entity_id UUID NOT NULL`, `phase_id VARCHAR(255)`, `node_id VARCHAR(255)`, `user_id UUID NOT NULL`, `created_at TIMESTAMPTZ`. Unique constraint on `(workflow_id, entity_type, entity_id, node_id)`. `tools` columns: `id UUID PK`, `name VARCHAR(255) UNIQUE per user`, `description TEXT`, `source VARCHAR(32)` (`builtin`|`custom`), `user_id UUID`, `is_deleted BOOL DEFAULT FALSE`, `created_at`/`updated_at TIMESTAMPTZ`. `actor_slots` on `custom_task_templates`: `JSONB NOT NULL DEFAULT '[]'`.

**Acceptance Criteria:**

- Run `alembic upgrade head` for `tools/compose/backend`; PostgreSQL contains the original SF-5 tables plus `tools`, `workflow_entity_refs`, and `custom_task_templates.actor_slots`, with no plugin or SQLite-only tables created.
- Create a task template with actor slots through the API, reload it, and observe that `actor_slots` is returned unchanged from persisted storage.

**Counterexamples:**

- Do not back-edit SF-5's initial migration to add SF-7 tables or columns.
- Do not create `plugin_types`, `plugin_instances`, or any `tools/iriai-workflows` migration path.
- Do not use SQLite dialect or `sqlite://` connection strings anywhere in the migration or models.

**Requirement IDs:** REQ-1, REQ-3, REQ-4

**Journey IDs:** J-2, J-3, J-4

**Citations:**

- **[decision]** `D-GR-29`
  - Excerpt: SF-5 stays at exactly five foundation tables; workflow_entity_refs moves to SF-7-owned downstream scope.
  - Reasoning: The migration boundary in this step preserves the accepted ownership split instead of pushing SF-7 tables into the foundation.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:22`
  - Excerpt: Use PostgreSQL with SQLAlchemy 2.x and Alembic, and create exactly 5 SF-5 tables.
  - Reasoning: The SF-5 base migration must remain limited to the five canonical foundation tables.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:22`
  - Excerpt: `custom_task_templates` must persist `actor_slots` through an Alembic migration and API support.
  - Reasoning: Actor-slot persistence is a required SF-7 schema extension.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:138`
  - Excerpt: WorkflowEntityRef (new).
  - Reasoning: The PRD explicitly introduces `workflow_entity_refs` as an SF-7 data entity.

#### STEP-53: Centralize workflow reference indexing and pre-delete reference APIs on the compose backend. This step makes SF-7 responsible for refreshing `workflow_entity_refs` on workflow mutations and for exposing one consistent persisted reference contract for roles, schemas, task templates, and tools.
<!-- SF: libraries-registries | Original: STEP-2 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/backend/app/services/workflow_reference_index.py` | create |
| `tools/compose/backend/app/schemas/reference.py` | create |
| `tools/compose/backend/app/schemas/tool.py` | create |
| `tools/compose/backend/app/schemas/task_template.py` | modify |
| `tools/compose/backend/app/routers/workflows.py` | modify |
| `tools/compose/backend/app/routers/references.py` | create |
| `tools/compose/backend/app/routers/roles.py` | modify |
| `tools/compose/backend/app/routers/schemas.py` | modify |
| `tools/compose/backend/app/routers/task_templates.py` | modify |
| `tools/compose/backend/app/routers/tools.py` | create |

**Instructions:**

Create `workflow_reference_index.py` with a single async function `reindex_workflow_refs(session: AsyncSession, workflow_id: UUID, yaml_content: str, user_id: UUID) -> None` that parses the workflow's saved YAML to extract role, schema, and task-template IDs from node definitions, deletes the existing `workflow_entity_refs` rows for that workflow_id, and inserts fresh rows — all within the caller's transaction. Modify `routers/workflows.py` (SF-5 file) to import and call `reindex_workflow_refs` from every workflow create, import, duplicate, save-version, and soft-delete code path. Create `routers/references.py` (owned by SF-7) with `GET /api/roles/references/{id}`, `GET /api/schemas/references/{id}`, `GET /api/task-templates/references/{id}`, and `GET /api/tools/references/{id}`. The first three query `workflow_entity_refs` by entity_type and entity_id and return `{ total, items: [{ workflow_id, workflow_name }] }`. The tools endpoint queries `roles.tools` JSON array and returns `{ total, items: [{ role_id, role_name }] }`. Create `routers/tools.py` with full CRUD for custom tools and a read-only list for built-ins. Modify role/schema/task-template DELETE handlers to re-check the same reference service before applying soft-delete and return `409` with the reference summary if references still exist. Use `/api/task-templates` not `/api/templates`.

**Acceptance Criteria:**

- Create, import, duplicate, save, and delete workflows that reference roles, schemas, or task templates; `GET /api/{entity}/references/{id}` always reflects the latest saved workflow state without delete-time YAML scanning.
- Attempt `DELETE /api/roles/{id}`, `DELETE /api/schemas/{id}`, `DELETE /api/task-templates/{id}`, or `DELETE /api/tools/{id}` while references exist; the API returns `409` with the same persisted reference summary that the preflight GET endpoint returns.

**Counterexamples:**

- Do not parse workflow YAML inside delete handlers or in the references router to discover current references.
- Do not add tool rows to `workflow_entity_refs` while `Role.tools` remains a string list.
- Do not route workflow mutations through a separate API call to trigger reindexing — call the service function in-transaction.

**Requirement IDs:** REQ-1, REQ-2, REQ-3, REQ-4

**Journey IDs:** J-2, J-3, J-4

**Citations:**

- **[code]** `.iriai/artifacts/features/beced7b1/plan-review-discussion-5.md:24`
  - Excerpt: Primary owner SF-7 libraries-registries: own the workflow_entity_refs reference-index extension.
  - Reasoning: The workflow mutation hooks and reference endpoints belong to SF-7 in the revised ownership model.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:19`
  - Excerpt: Roles, Schemas, and Task Templates must use a pre-delete reference check backed by the canonical `workflow_entity_refs` junction table.
  - Reasoning: This step implements the persisted reference contract required by the SF-7 PRD.
- **[code]** `.iriai/artifacts/features/beced7b1/reviews/system-design-gate-review.md:157`
  - Excerpt: CRUD for /api/roles, /api/schemas, /api/task-templates, /api/plugins.
  - Reasoning: The accepted route contract uses `/api/task-templates`; the stale `/api/templates` path should not be carried forward.
- **[code]** `iriai-compose/iriai_compose/actors.py:13`
  - Excerpt: tools: list[str]
  - Reasoning: Tool delete checks must stay role-backed until the runtime contract stops storing tool identifiers as strings.

#### STEP-54: Apply one consistent security and validation layer across all library APIs. This step adds shared request-size and name-sanitization enforcement so roles, schemas, task templates, tools, and reference endpoints all share the same JWT, 404-on-cross-user, and 413/422 error behavior.
<!-- SF: libraries-registries | Original: STEP-3 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/backend/app/dependencies/library_validation.py` | create |
| `tools/compose/backend/app/middleware/request_size.py` | create |
| `tools/compose/backend/app/main.py` | modify |
| `tools/compose/backend/app/routers/roles.py` | modify |
| `tools/compose/backend/app/routers/schemas.py` | modify |
| `tools/compose/backend/app/routers/task_templates.py` | modify |
| `tools/compose/backend/app/routers/tools.py` | modify |

**Instructions:**

Add a compose-backend request-size middleware for library POST/PUT payloads (max 512 KB) and shared validators for entity-name sanitization and structured 413/422 failures. Reuse the SF-5 JWT auth dependency on every library and references route, scope every lookup by authenticated `user_id` from the JWT `sub` claim, and return the existing `{ error, error_description, details }` platform envelope for validation and authorization failures. Return `404` (not `403`) for cross-user access attempts so record existence is not leaked.

**Acceptance Criteria:**

- Send an oversized library create or update request; the backend returns `413` in the standard error envelope and no role, schema, task template, or tool record is created.
- Request another user's role, schema, task template, or tool by id with a valid JWT; the backend returns `404` rather than leaking existence.

**Counterexamples:**

- Do not rely on frontend validation alone for payload size limits or invalid entity names.
- Do not leave `GET /api/tools`, `GET /api/{entity}/references/{id}`, or DELETE handlers outside the shared auth and user-scope path.

**Requirement IDs:** REQ-6, REQ-7

**Journey IDs:** J-1, J-4

**Citations:**

- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:24`
  - Excerpt: All library API endpoints require JWT Bearer auth, scope data to the authenticated user, and return 404 rather than 403.
  - Reasoning: The validation layer must preserve the PRD's ownership and auth semantics for every library endpoint.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:26`
  - Excerpt: Provide standardized error responses in the `{ error, error_description, details }` shape.
  - Reasoning: SF-7 should reuse the compose foundation error envelope instead of inventing a second API error shape.

#### STEP-55: Create the shared frontend library plumbing for delete preflight, cache invalidation, and the rebased compose topology. This step moves all library UI to `tools/compose/frontend`, preserves SF-5's four top-level shell folders, and makes delete dialogs fetch persisted reference data before any destructive request.
<!-- SF: libraries-registries | Original: STEP-4 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/libraries/api/libraryClient.ts` | create |
| `tools/compose/frontend/src/features/libraries/hooks/useReferenceCheck.ts` | create |
| `tools/compose/frontend/src/features/libraries/shared/EntityDeleteDialog.tsx` | create |
| `tools/compose/frontend/src/features/libraries/shared/useLibraryInvalidation.ts` | create |
| `tools/compose/frontend/src/features/libraries/LibrarySecondaryNav.tsx` | create |
| `tools/compose/frontend/src/App.tsx` | modify |

**Instructions:**

Build a shared library API client (`libraryClient.ts`) wrapping the authenticated compose backend base URL. Implement `useReferenceCheck(entityType, id)` with `staleTime: 0` — it calls `GET /api/{entityType}/references/{id}` on open and returns `{ isLoading, isBlocked, references }`. Build `EntityDeleteDialog` that uses this hook, renders `[data-testid='entity-delete-dialog-loading']` until preflight resolves, shows `[data-testid='entity-delete-dialog-reference-list']` and hides the delete button when blocked, and handles late DELETE `409` responses by rehydrating into the blocked state. Build `useLibraryInvalidation` to invalidate only the mutated entity query after save/delete without full-page reloads. Mount the SF-7 library routes inside the existing `tools/compose/frontend` app router via `LibrarySecondaryNav`, making the Tool Library reachable without adding a fifth SF-5 shell folder.

**Acceptance Criteria:**

- Open delete for a role, schema, or task template; the dialog shows a loading state, fetches `/api/{entity}/references/{id}`, and blocks or confirms before any DELETE request is sent.
- Open the compose shell after SF-7 is installed; the existing top-level sidebar still shows only Workflows, Roles, Output Schemas, and Task Templates, while the Tools view is reachable without restoring a fifth SF-5 foundation folder.

**Counterexamples:**

- Do not probe DELETE to learn whether an entity is referenced.
- Do not restore a Plugins page, PluginPicker, or a top-level Tools folder in the SF-5 shell.
- Do not reuse the list-query stale window for delete preflight — `staleTime: 0` is required.

**Requirement IDs:** REQ-1, REQ-2, REQ-3, REQ-5

**Journey IDs:** J-2, J-3

**Citations:**

- **[decision]** `D-GR-27`
  - Excerpt: compose lives under tools/compose; stale tools/iriai-workflows topology is rejected.
  - Reasoning: All frontend scope in this revision moves to the accepted compose repository path.
- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:20`
  - Excerpt: `EntityDeleteDialog` and `useReferenceCheck` call `GET /api/{entity}/references/{id}` before any DELETE request.
  - Reasoning: The shared dialog and hook implement the PRD's non-destructive delete flow.

#### STEP-56: Deliver the Roles and Tools library user flows on the rebased compose frontend. This step keeps tool selection and tool deletion consistent with the runtime `Role.tools` contract while making custom tools fully manageable through SF-7 without reintroducing plugin registry scope.
<!-- SF: libraries-registries | Original: STEP-5 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/libraries/roles/RolesListPage.tsx` | create |
| `tools/compose/frontend/src/features/libraries/roles/RoleEditorView.tsx` | create |
| `tools/compose/frontend/src/features/libraries/roles/ToolChecklistGrid.tsx` | create |
| `tools/compose/frontend/src/features/libraries/tools/ToolsListPage.tsx` | create |
| `tools/compose/frontend/src/features/libraries/tools/ToolDetailView.tsx` | create |
| `tools/compose/frontend/src/features/libraries/tools/ToolEditorView.tsx` | create |

**Instructions:**

Build the Roles list/editor with a four-step flow (name, model, system prompt, tool checklist). `ToolChecklistGrid` loads `GET /api/tools` and renders separate `[data-testid='role-tools-builtins-section']` and `[data-testid='role-tools-custom-section']` groups. Persist tool identifiers as strings in the role payload — never as UUIDs. Route every role delete action through `EntityDeleteDialog`. Build the Tool Library list (`ToolsListPage`), detail (`ToolDetailView`), and editor (`ToolEditorView`) for custom tools only; keep built-in tools read-only; treat custom tool names as immutable after creation so existing `Role.tools` string references remain valid; show a rename-warning banner in `ToolEditorView` explaining that role references remain manual; invalidate role-adjacent queries after tool edits or deletes so the checklist refreshes immediately.

**Acceptance Criteria:**

- Create a role with built-in and custom tools selected, save it, and observe that the saved role appears in the library and becomes selectable from the exported role picker.
- Edit a custom tool description, reopen a role that uses it, and observe the refreshed tool metadata in the checklist; attempt to delete a referenced tool and observe a blocking role list until those role references are removed.

**Counterexamples:**

- Do not switch `Role.tools` to UUID storage or silently rename a custom tool in a way that breaks existing role references.
- Do not allow editing or deleting built-in tools, and do not create plugin registry pages or plugin-backed tool flows.

**Requirement IDs:** REQ-3, REQ-5

**Journey IDs:** J-1, J-3

**Citations:**

- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:21`
  - Excerpt: The Tool Library remains a full CRUD library page with list, detail, and editor views.
  - Reasoning: This step implements the required Tool Library surface and its role-editor integration.
- **[code]** `iriai-compose/iriai_compose/actors.py:13`
  - Excerpt: tools: list[str]
  - Reasoning: The frontend must preserve the string-based tool identifier contract when saving roles.

#### STEP-57: Finish the schema and task-template library surfaces, including actor-slot editing and SF-6 picker exports. This step keeps schema and template delete safety on the same preflight contract while preserving the isolated task-template editor store required to avoid leaking state into the workflow editor.
<!-- SF: libraries-registries | Original: STEP-6 -->

**Scope:**

| Path | Action |
|------|--------|
| `tools/compose/frontend/src/features/libraries/schemas/SchemasListPage.tsx` | create |
| `tools/compose/frontend/src/features/libraries/schemas/SchemaEditorView.tsx` | create |
| `tools/compose/frontend/src/features/libraries/templates/TemplatesListPage.tsx` | create |
| `tools/compose/frontend/src/features/libraries/templates/TaskTemplateEditorView.tsx` | create |
| `tools/compose/frontend/src/features/libraries/pickers/RolePicker.tsx` | create |
| `tools/compose/frontend/src/features/libraries/pickers/SchemaPicker.tsx` | create |
| `tools/compose/frontend/src/features/libraries/pickers/TemplateBrowser.tsx` | create |

**Instructions:**

Build the schema list/editor flow against `/api/schemas` with a dual-pane JSON editor and route schema deletes through `EntityDeleteDialog`. Build the task-template list/editor against `/api/task-templates`. In `TaskTemplateEditorView`, use `createEditorStore()` (not the singleton workflow editor store), render the actor-slot side panel (`[data-testid='task-template-actor-slots-panel']`) that lets users add/remove/reorder slot definitions, and persist them to `actor_slots` via `PUT /api/task-templates/{id}`. Export `RolePicker`, `SchemaPicker`, and `TemplateBrowser` as selection-only React components for SF-6 consumption — they load list data only, with no embedded delete-preflight logic.

**Acceptance Criteria:**

- Create a task template with actor slots, save it, refresh the page, and reopen it; the actor slots and default bindings are still present.
- Open schema and task-template pickers from consuming UI and observe that they load current library data without calling reference-preflight endpoints or surfacing plugin-only controls.

**Counterexamples:**

- Do not keep `actor_slots` in frontend state only — they must survive a full page refresh from persisted storage.
- Do not import the singleton workflow editor store into `TaskTemplateEditorView`.
- Do not reintroduce `PluginPicker` or plugin library routes.

**Requirement IDs:** REQ-1, REQ-2, REQ-4, REQ-5

**Journey IDs:** J-2, J-4

**Citations:**

- **[code]** `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:22`
  - Excerpt: `custom_task_templates` must persist `actor_slots` through an Alembic migration and API support.
  - Reasoning: Actor-slot persistence and reload behavior are direct SF-7 requirements.
- **[code]** `.iriai/artifacts/features/beced7b1/reviews/architecture.md:176`
  - Excerpt: TaskTemplateEditorView creates a separate editorStore instance via `createEditorStore()` factory.
  - Reasoning: The template editor must stay isolated from the workflow editor's singleton state.
- **[code]** `.iriai/artifacts/features/beced7b1/reviews/system-design-gate-review.md:157`
  - Excerpt: CRUD for /api/roles, /api/schemas, /api/task-templates, /api/plugins.
  - Reasoning: The accepted endpoint naming for template CRUD is `/api/task-templates`, not the stale `/api/templates` path.

### Journey Verifications

**Journey J-1:**

- Step 1:
  - [browser] [data-testid='roles-page'] is visible and [data-testid='roles-create-btn'] is enabled.
  - Test IDs: roles-page, roles-create-btn
- Step 2:
  - [browser] [data-testid='role-editor'] renders [data-testid='role-tools-builtins-section'] and [data-testid='role-tools-custom-section'] after tool data loads.
  - [api] GET /api/tools returns built-in tools plus the caller's custom tools for the role editor checklist.
  - Test IDs: role-editor, role-tools-builtins-section, role-tools-custom-section
- Step 3:
  - [browser] After save, [data-testid='role-picker'] contains the new role and [data-testid='role-save-btn'] returns to the idle state.
  - Test IDs: role-picker, role-save-btn

**Journey J-2:**

- Step 1:
  - [browser] [data-testid='entity-delete-dialog'] opens, [data-testid='entity-delete-dialog-loading'] is visible, and [data-testid='entity-delete-dialog-delete-btn'] is hidden or disabled until the preflight resolves.
  - Test IDs: entity-delete-dialog, entity-delete-dialog-loading, entity-delete-dialog-delete-btn
- Step 2:
  - [api] GET /api/roles/references/{id} returns persisted workflow names from `workflow_entity_refs` before any DELETE attempt.
- Step 3:
  - [browser] After the referencing workflow is saved without the role, reopening the dialog removes [data-testid='entity-delete-dialog-reference-list'] and shows [data-testid='entity-delete-dialog-delete-btn'] with [data-testid='entity-delete-dialog-cancel-btn'].
  - Test IDs: entity-delete-dialog-reference-list, entity-delete-dialog-delete-btn, entity-delete-dialog-cancel-btn

**Journey J-3:**

- Step 1:
  - [browser] Clicking [data-testid='libraries-tools-link'] opens [data-testid='tools-page'] and tool delete opens [data-testid='entity-delete-dialog'].
  - Test IDs: libraries-tools-link, tools-page, entity-delete-dialog
- Step 2:
  - [api] GET /api/tools/references/{id} returns role names only (reference_kind: 'role'), and the blocking dialog renders them in [data-testid='entity-delete-dialog-role-list'] with no workflow validation chips ([data-testid='entity-delete-dialog-validation-codes'] is absent).
  - Test IDs: entity-delete-dialog-role-list
- Step 3:
  - [api] After the tool is removed from referencing roles, DELETE /api/tools/{id} returns 204 and GET /api/tools no longer includes the deleted custom tool.
  - [browser] The deleted tool no longer appears in [data-testid='tool-checklist-grid'] when a role editor is reopened.
  - Test IDs: tool-checklist-grid

**Journey J-4:**

- Step 1:
  - [browser] [data-testid='task-template-editor'] renders [data-testid='task-template-actor-slots-panel']; adding a slot creates a new [data-testid='task-template-actor-slot-row'] and [data-testid='task-template-save-btn'] persists it.
  - Test IDs: task-template-editor, task-template-actor-slots-panel, task-template-actor-slot-row, task-template-save-btn
- Step 2:
  - [api] GET /api/task-templates/{id} returns the saved `actor_slots` array after a page refresh.
  - [database] Query `SELECT actor_slots FROM custom_task_templates WHERE id = :id`; the stored JSON matches the saved slot definitions.

### Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-82 | `workflow_entity_refs` can drift from saved workflow state if any workflow mutation path in SF-5's `workflows.py` router bypasses the shared `reindex_workflow_refs` service call. | high | Route workflow create, import, duplicate, save-version, and soft-delete through one shared `workflow_reference_index` service function called within the same transaction, and regression-test every mutation path against the `GET /api/{entity}/references/{id}` endpoint. SF-5's `workflows.py` is the only SF-5 file SF-7 touches for this purpose. | STEP-1, STEP-2, STEP-4, STEP-6 |
| RISK-83 | A zero-reference preflight result can go stale between the GET preflight and the DELETE mutation — another workflow save could create a new reference in that window. | medium | Make every DELETE handler re-check the same persisted reference service (not the preflight hook) and return the same structured `409` payload so `EntityDeleteDialog` can rehydrate into the blocked state without a hard refresh. | STEP-2, STEP-4 |
| RISK-84 | Role-to-tool references remain string-based; renaming custom tools orphans existing role selections without any constraint-level enforcement. | medium | Treat custom tool names as immutable after creation in v1, keep tool delete checks role-backed, show a rename-warning banner in `ToolEditorView`, and document that future ID-based tool references require a separate runtime contract change to `Role.tools`. | STEP-2, STEP-5 |
| RISK-85 | Adding SF-7 tables through the wrong migration layer (e.g., back-editing the SF-5 initial migration) silently violates SF-5's five-table acceptance contract. | medium | Keep `tools`, `workflow_entity_refs`, and `actor_slots` in the separate SF-7 revision `0002_sf7_libraries_registry_extensions.py` with `down_revision` pointing to the SF-5 initial migration, and verify the SF-5 migration still creates exactly its five foundation tables. | STEP-1 |
| RISK-86 | `TaskTemplateEditorView` can leak editor state into the workflow editor if it reuses the singleton store instance. | low | Require `createEditorStore()` for the template editor path and add regression checks that multiple editor instances do not share selection or draft state. | STEP-6 |

### File Manifest

| Path | Action |
|------|--------|
| `tools/compose/backend/alembic/versions/0002_sf7_libraries_registry_extensions.py` | create |
| `tools/compose/backend/app/models/tool.py` | create |
| `tools/compose/backend/app/models/workflow_entity_ref.py` | create |
| `tools/compose/backend/app/models/custom_task_template.py` | modify |
| `tools/compose/backend/app/models/__init__.py` | modify |
| `tools/compose/backend/app/schemas/tool.py` | create |
| `tools/compose/backend/app/schemas/reference.py` | create |
| `tools/compose/backend/app/schemas/task_template.py` | modify |
| `tools/compose/backend/app/services/workflow_reference_index.py` | create |
| `tools/compose/backend/app/dependencies/library_validation.py` | create |
| `tools/compose/backend/app/middleware/request_size.py` | create |
| `tools/compose/backend/app/routers/workflows.py` | modify |
| `tools/compose/backend/app/routers/references.py` | create |
| `tools/compose/backend/app/routers/roles.py` | modify |
| `tools/compose/backend/app/routers/schemas.py` | modify |
| `tools/compose/backend/app/routers/task_templates.py` | modify |
| `tools/compose/backend/app/routers/tools.py` | create |
| `tools/compose/backend/app/main.py` | modify |
| `tools/compose/frontend/src/features/libraries/api/libraryClient.ts` | create |
| `tools/compose/frontend/src/features/libraries/hooks/useReferenceCheck.ts` | create |
| `tools/compose/frontend/src/features/libraries/shared/EntityDeleteDialog.tsx` | create |
| `tools/compose/frontend/src/features/libraries/shared/useLibraryInvalidation.ts` | create |
| `tools/compose/frontend/src/features/libraries/LibrarySecondaryNav.tsx` | create |
| `tools/compose/frontend/src/features/libraries/roles/RolesListPage.tsx` | create |
| `tools/compose/frontend/src/features/libraries/roles/RoleEditorView.tsx` | create |
| `tools/compose/frontend/src/features/libraries/roles/ToolChecklistGrid.tsx` | create |
| `tools/compose/frontend/src/features/libraries/tools/ToolsListPage.tsx` | create |
| `tools/compose/frontend/src/features/libraries/tools/ToolDetailView.tsx` | create |
| `tools/compose/frontend/src/features/libraries/tools/ToolEditorView.tsx` | create |
| `tools/compose/frontend/src/features/libraries/schemas/SchemasListPage.tsx` | create |
| `tools/compose/frontend/src/features/libraries/schemas/SchemaEditorView.tsx` | create |
| `tools/compose/frontend/src/features/libraries/templates/TemplatesListPage.tsx` | create |
| `tools/compose/frontend/src/features/libraries/templates/TaskTemplateEditorView.tsx` | create |
| `tools/compose/frontend/src/features/libraries/pickers/RolePicker.tsx` | create |
| `tools/compose/frontend/src/features/libraries/pickers/SchemaPicker.tsx` | create |
| `tools/compose/frontend/src/features/libraries/pickers/TemplateBrowser.tsx` | create |
| `tools/compose/frontend/src/App.tsx` | modify |

---

## Global File Manifest

Union of all file manifest entries across all 7 subfeatures.

| Path | Action | Source SF |
|------|--------|----------|
| `iriai-compose/iriai_compose/schema/__init__.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/base.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/actors.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/types.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/cost.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/nodes.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/edges.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/phases.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/workflow.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/plugins.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/templates.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/validation.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/yaml_io.py` | create | SF-1 |
| `iriai-compose/iriai_compose/schema/json_schema.py` | create | SF-1 |
| `iriai-compose/iriai_compose/__init__.py` | modify | SF-1 |
| `iriai-compose/pyproject.toml` | modify | SF-1 |
| `iriai-compose/tests/schema/test_models.py` | create | SF-1 |
| `iriai-compose/tests/schema/test_validation.py` | create | SF-1 |
| `iriai-compose/tests/schema/test_yaml_io.py` | create | SF-1 |
| `iriai-compose/tests/schema/test_json_schema.py` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/minimal_workflow.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/nested_children.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/hook_edges.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/branch_paths.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/loop_exits.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/pm_fold_map_loop.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/invalid_switch_function.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/invalid_merge_function.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/invalid_actor_interaction.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/invalid_root_stores.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/invalid_phase_phases_field.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/invalid_port_type.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/invalid_hook_edges_section.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/invalid_hook_transform.yaml` | create | SF-1 |
| `iriai-compose/tests/fixtures/schema/invalid_branch_unknown_path.yaml` | create | SF-1 |
| `iriai-compose/iriai_compose/actors.py` | read | SF-1 |
| `iriai-compose/iriai_compose/runner.py` | read | SF-1 |
| `iriai-compose/iriai_compose/tasks.py` | read | SF-1 |
| `iriai-compose/iriai_compose/workflow.py` | read | SF-1 |
| `.iriai/artifacts/features/beced7b1/plan-review-discussion-4.md` | read | SF-1 |
| `.iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md` | read | SF-1 |
| `.iriai/artifacts/features/beced7b1/broad/architecture.md` | read | SF-1 |
| `iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py` | read | SF-1 |
| `iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py` | read | SF-1 |
| `iriai-compose/iriai_compose/testing/__init__.py` | create | SF-3 |
| `iriai-compose/iriai_compose/testing/mock_runtime.py` | create | SF-3 |
| `iriai-compose/iriai_compose/testing/fixtures.py` | create | SF-3 |
| `iriai-compose/iriai_compose/testing/assertions.py` | create | SF-3 |
| `iriai-compose/iriai_compose/testing/snapshot.py` | create | SF-3 |
| `iriai-compose/iriai_compose/testing/runner.py` | create | SF-3 |
| `iriai-compose/iriai_compose/testing/validation.py` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/minimal_ask.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/minimal_branch.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/minimal_plugin.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/sequential_phase.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/map_phase.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/fold_phase.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/loop_phase.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/multi_phase.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/hook_edge.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/nested_phases.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/ask_gate_pattern.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/ask_choose_pattern.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/store_dot_notation.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/invalid/dangling_edge.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/invalid/cycle_detected.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/invalid/type_mismatch.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_actor_ref.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/invalid/duplicate_node_id.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_phase_mode_config.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_hook_edge_transform.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_store_ref.yaml` | create | SF-3 |
| `iriai-compose/tests/fixtures/workflows/invalid/invalid_switch_function_config.yaml` | create | SF-3 |
| `iriai-compose/tests/testing/__init__.py` | create | SF-3 |
| `iriai-compose/tests/testing/test_mock_runtime.py` | create | SF-3 |
| `iriai-compose/tests/testing/test_builder.py` | create | SF-3 |
| `iriai-compose/tests/testing/test_assertions.py` | create | SF-3 |
| `iriai-compose/tests/testing/test_validation_reexport.py` | create | SF-3 |
| `iriai-compose/tests/testing/test_snapshots.py` | create | SF-3 |
| `iriai-compose/tests/testing/test_runner.py` | create | SF-3 |
| `iriai-compose/tests/conftest.py` | read | SF-3 |
| `iriai-compose/iriai_compose/pending.py` | read | SF-3 |
| `iriai-compose/iriai_compose/schema/validation.py` | read | SF-3 |
| `iriai-compose/iriai_compose/schema/__init__.py` | read | SF-3 |
| `iriai-compose/iriai_compose/schema/yaml_io.py` | read | SF-3 |
| `iriai-compose/iriai_compose/schema/base.py` | read | SF-3 |
| `iriai-compose/iriai_compose/schema/nodes.py` | read | SF-3 |
| `iriai-compose/iriai_compose/schema/edges.py` | read | SF-3 |
| `iriai-compose/iriai_compose/schema/phases.py` | read | SF-3 |
| `iriai-compose/iriai_compose/schema/actors.py` | read | SF-3 |
| `iriai-compose/iriai_compose/schema/stores.py` | read | SF-3 |
| `iriai-compose/iriai_compose/schema/workflow.py` | read | SF-3 |
| `iriai-compose/iriai_compose/declarative/__init__.py` | read | SF-3 |
| `iriai-compose/iriai_compose/declarative/config.py` | read | SF-3 |
| `iriai_compose/plugins/__init__.py` | modify | SF-4 |
| `iriai_compose/plugins/types.py` | create | SF-4 |
| `iriai_compose/plugins/instances.py` | create | SF-4 |
| `iriai_compose/plugins/transforms.py` | create | SF-4 |
| `iriai_compose/plugins/adapters.py` | create | SF-4 |
| `tests/fixtures/workflows/migration/types/common.yaml` | create | SF-4 |
| `tests/fixtures/workflows/migration/types/planning.yaml` | create | SF-4 |
| `tests/fixtures/workflows/migration/types/develop.yaml` | create | SF-4 |
| `tests/fixtures/workflows/migration/types/bugfix.yaml` | create | SF-4 |
| `tests/fixtures/workflows/migration/planning.yaml` | create | SF-4 |
| `tests/fixtures/workflows/migration/develop.yaml` | create | SF-4 |
| `tests/fixtures/workflows/migration/bugfix.yaml` | create | SF-4 |
| `tests/fixtures/workflows/templates/gate_and_revise.yaml` | create | SF-4 |
| `tests/fixtures/workflows/templates/broad_interview.yaml` | create | SF-4 |
| `tests/fixtures/workflows/templates/interview_gate_review.yaml` | create | SF-4 |
| `tests/migration/__init__.py` | create | SF-4 |
| `tests/migration/assertions.py` | create | SF-4 |
| `tests/migration/conftest.py` | create | SF-4 |
| `tests/migration/test_planning.py` | create | SF-4 |
| `tests/migration/test_develop.py` | create | SF-4 |
| `tests/migration/test_bugfix.py` | create | SF-4 |
| `tests/migration/test_yaml_roundtrip.py` | create | SF-4 |
| `tests/migration/test_plugin_instances.py` | create | SF-4 |
| `tests/migration/test_edge_transforms.py` | create | SF-4 |
| `tests/migration/test_runtime_bridge.py` | create | SF-4 |
| `tests/migration/test_runtime_context.py` | create | SF-4 |
| `tests/migration/test_litmus.py` | create | SF-4 |
| `tests/migration/test_phase_modes.py` | create | SF-4 |
| `tests/migration/test_error_ports.py` | create | SF-4 |
| `tests/migration/test_context_hierarchy.py` | create | SF-4 |
| `tests/migration/test_templates.py` | create | SF-4 |
| `tests/migration/test_artifact_writes.py` | create | SF-4 |
| `tests/migration/test_live_smoke.py` | create | SF-4 |
| `tests/fixtures/seed/migration_seed.json` | create | SF-4 |
| `tests/fixtures/seed/seed_loader.py` | create | SF-4 |
| `iriai-build-v2/src/iriai_build_v2/workflows/_declarative.py` | create | SF-4 |
| `iriai-build-v2/src/iriai_build_v2/interfaces/cli/app.py` | modify | SF-4 |
| `tools/compose/backend/app/main.py` | create | SF-5 |
| `tools/compose/backend/app/config.py` | create | SF-5 |
| `tools/compose/backend/app/database.py` | create | SF-5 |
| `tools/compose/backend/app/auth.py` | create | SF-5 |
| `tools/compose/backend/app/dependencies/auth.py` | create | SF-5 |
| `tools/compose/backend/app/middleware/logging.py` | create | SF-5 |
| `tools/compose/backend/app/middleware/rate_limit.py` | create | SF-5 |
| `tools/compose/backend/app/models/workflow.py` | create | SF-5 |
| `tools/compose/backend/app/models/workflow_version.py` | create | SF-5 |
| `tools/compose/backend/app/models/role.py` | create | SF-5 |
| `tools/compose/backend/app/models/output_schema.py` | create | SF-5 |
| `tools/compose/backend/app/models/custom_task_template.py` | create | SF-5 |
| `tools/compose/backend/app/schemas/workflow.py` | create | SF-5 |
| `tools/compose/backend/app/services/hooks.py` | create | SF-5 |
| `tools/compose/backend/app/services/workflow_service.py` | create | SF-5 |
| `tools/compose/backend/app/services/role_service.py` | create | SF-5 |
| `tools/compose/backend/app/services/schema_service.py` | create | SF-5 |
| `tools/compose/backend/app/services/template_service.py` | create | SF-5 |
| `tools/compose/backend/app/routers/workflows.py` | create | SF-5 |
| `tools/compose/backend/app/routers/roles.py` | create | SF-5 |
| `tools/compose/backend/app/routers/schemas.py` | create | SF-5 |
| `tools/compose/backend/app/routers/templates.py` | create | SF-5 |
| `tools/compose/backend/app/routers/schema_export.py` | create | SF-5 |
| `tools/compose/backend/app/routers/health.py` | create | SF-5 |
| `tools/compose/backend/app/seed.py` | create | SF-5 |
| `tools/compose/backend/alembic/versions/0001_foundation_tables.py` | create | SF-5 |
| `tools/compose/backend/pyproject.toml` | create | SF-5 |
| `tools/compose/backend/Dockerfile` | create | SF-5 |
| `tools/compose/frontend/src/layouts/ExplorerLayout.tsx` | create | SF-5 |
| `tools/compose/frontend/src/components/sidebar/SidebarTree.tsx` | create | SF-5 |
| `tools/compose/frontend/src/components/sidebar/SidebarFolder.tsx` | create | SF-5 |
| `tools/compose/frontend/src/views/GridView.tsx` | create | SF-5 |
| `tools/compose/frontend/src/views/DetailsView.tsx` | create | SF-5 |
| `tools/compose/frontend/src/components/NewDropdown.tsx` | create | SF-5 |
| `tools/compose/frontend/src/components/ConfirmDialog.tsx` | create | SF-5 |
| `tools/compose/frontend/src/components/ContextMenu.tsx` | create | SF-5 |
| `tools/compose/frontend/src/components/MobileBlockScreen.tsx` | create | SF-5 |
| `tools/compose/frontend/src/components/EmptyState.tsx` | create | SF-5 |
| `tools/compose/frontend/src/components/SkeletonLoader.tsx` | create | SF-5 |
| `tools/compose/frontend/src/stores/entitiesStore.ts` | create | SF-5 |
| `tools/compose/frontend/src/stores/sidebarStore.ts` | create | SF-5 |
| `tools/compose/frontend/src/stores/uiStore.ts` | create | SF-5 |
| `tools/compose/frontend/src/api/client.ts` | create | SF-5 |
| `tools/compose/frontend/src/styles/windows-xp.css` | create | SF-5 |
| `tools/compose/frontend/package.json` | create | SF-5 |
| `tools/compose/frontend/vite.config.ts` | create | SF-5 |
| `tools/compose/frontend/tsconfig.json` | create | SF-5 |
| `tools/compose/frontend/Dockerfile` | create | SF-5 |
| `platform/toolshub/frontend/` | create | SF-5 |
| `platform/deploy-console/deploy-console-service/app/database.py` | read | SF-5 |
| `iriai_compose/declarative/schema.py` | read | SF-5 |
| `tools/compose/frontend/src/features/editor/store/editorStore.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/store/undoMiddleware.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/store/selectors.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/schema/schemaClient.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/schema/workflowContract.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/serialization/serializeWorkflow.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/serialization/deserializeWorkflow.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/serialization/autoLayout.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/validation/validationTypes.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/validation/schemaValidator.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/validation/clientValidator.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/validation/ValidationPanel.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/canvas/EditorCanvas.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/canvas/connectionValidator.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/canvas/canvasStyles.css` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/canvas/SelectionRectangle.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/nodes/nodeTypes.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/nodes/ErrorBadge.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/nodes/CollapsedGroupCard.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/nodes/AskFlowNode.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/nodes/BranchFlowNode.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/nodes/PluginFlowNode.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/nodes/TemplateFlowNode.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/phases/PhaseContainer.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/phases/PhaseLabelBar.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/phases/LoopExitPorts.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/edges/DataEdge.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/edges/HookEdge.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/edges/edgeTypes.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/toolbar/PaintMenuBar.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/toolbar/IconToolbar.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/toolbar/ToolbarButton.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/toolbar/ToolModeToggle.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/palette/NodePalette.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/palette/PaletteItem.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/palette/RolePalette.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/InspectorWindowManager.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/InspectorWindow.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/TetherLine.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/AskInspector.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/BranchInspector.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/PluginInspector.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/PhaseInspector.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/EdgeInspector.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/InspectorActions.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/PromptTemplateEditor.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/InlineRoleCreator.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/InlineOutputSchemaCreator.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/OutputPathsEditor.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/inspectors/SwitchFunctionEditor.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/hooks/useAutoSave.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/hooks/useKeyboardShortcuts.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/hooks/useDragAndDrop.ts` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/dialogs/ImportConfirmDialog.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/dialogs/PromotionDialog.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/dialogs/SaveAsTemplateDialog.tsx` | create | SF-6 |
| `tools/compose/frontend/src/features/editor/WorkflowEditorPage.tsx` | create | SF-6 |
| `iriai-compose/iriai_compose/__init__.py` | read | SF-6 |
| `iriai-build-v2/src/iriai_build_v2/workflows/planning/workflow.py` | read | SF-6 |
| `iriai-build-v2/src/iriai_build_v2/workflows/develop/workflow.py` | read | SF-6 |
| `iriai-build-v2/src/iriai_build_v2/workflows/bugfix/workflow.py` | read | SF-6 |
| `templates/fastapi-postgres/app/main.py` | read | SF-6 |
| `first-party-apps/events/events-backend/app/dependencies/auth.py` | read | SF-6 |
| `PACKAGES.md` | read | SF-6 |
| `tools/compose/backend/alembic/versions/0002_sf7_libraries_registry_extensions.py` | create | SF-7 |
| `tools/compose/backend/app/models/tool.py` | create | SF-7 |
| `tools/compose/backend/app/models/workflow_entity_ref.py` | create | SF-7 |
| `tools/compose/backend/app/models/custom_task_template.py` | modify | SF-7 |
| `tools/compose/backend/app/models/__init__.py` | modify | SF-7 |
| `tools/compose/backend/app/schemas/tool.py` | create | SF-7 |
| `tools/compose/backend/app/schemas/reference.py` | create | SF-7 |
| `tools/compose/backend/app/schemas/task_template.py` | modify | SF-7 |
| `tools/compose/backend/app/services/workflow_reference_index.py` | create | SF-7 |
| `tools/compose/backend/app/dependencies/library_validation.py` | create | SF-7 |
| `tools/compose/backend/app/middleware/request_size.py` | create | SF-7 |
| `tools/compose/backend/app/routers/workflows.py` | modify | SF-7 |
| `tools/compose/backend/app/routers/references.py` | create | SF-7 |
| `tools/compose/backend/app/routers/roles.py` | modify | SF-7 |
| `tools/compose/backend/app/routers/schemas.py` | modify | SF-7 |
| `tools/compose/backend/app/routers/task_templates.py` | modify | SF-7 |
| `tools/compose/backend/app/routers/tools.py` | create | SF-7 |
| `tools/compose/backend/app/main.py` | modify | SF-7 |
| `tools/compose/frontend/src/features/libraries/api/libraryClient.ts` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/hooks/useReferenceCheck.ts` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/shared/EntityDeleteDialog.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/shared/useLibraryInvalidation.ts` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/LibrarySecondaryNav.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/roles/RolesListPage.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/roles/RoleEditorView.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/roles/ToolChecklistGrid.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/tools/ToolsListPage.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/tools/ToolDetailView.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/tools/ToolEditorView.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/schemas/SchemasListPage.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/schemas/SchemaEditorView.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/templates/TemplatesListPage.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/templates/TaskTemplateEditorView.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/pickers/RolePicker.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/pickers/SchemaPicker.tsx` | create | SF-7 |
| `tools/compose/frontend/src/features/libraries/pickers/TemplateBrowser.tsx` | create | SF-7 |
| `tools/compose/frontend/src/App.tsx` | modify | SF-7 |

---

## Global Dependencies

Union of all dependencies across all 7 subfeatures.

- @dagrejs/dagre
- @homelocal/auth
- @tanstack/react-query
- @xyflow/react
- SF-1 (Declarative Schema)
- SF-2 (DAG Loader and Runner) — specifically: current_node_var ContextVar exported from iriai_compose.declarative
- SF-5 `routers/workflows.py` is the only SF-5 file SF-7 touches — it is modified to call `reindex_workflow_refs()` from SF-7's `workflow_reference_index` service within every workflow mutation transaction.
- SF-5 composer-app-foundation delivers the `tools/compose` FastAPI/React topology, PostgreSQL + Alembic base with `alembic_version_compose`, JWT middleware, and the five-table workflow/role/schema/task-template/workflow-version foundation. SF-7's migration `0002` sets `down_revision` to SF-5's initial migration.
- SF-6 workflow-editor persists role, schema, and task-template identifiers into saved workflow YAML (which triggers reindexing via the SF-7 hook) and consumes the exported `RolePicker`, `SchemaPicker`, and `TemplateBrowser` components.
- ajv
- ajv-formats
- alembic
- auth-python
- axios
- fastapi
- iriai-compose
- iriai-compose SF-1 (Declarative Schema)
- iriai-compose SF-2 (DAG Loader & Runner) — ABI owner: invoke() unchanged, node ContextVar, workflow→phase→actor→node merge, ExecutionResult + ExecutionHistory observability; no core checkpoint/resume
- iriai-compose SF-3 (Testing Framework) — consumer of SF-2 ABI: MockRuntime reads node ContextVar, no node_id kwarg, no checkpoint/resume dependency
- iriai-compose keeps the runtime `Role.tools` contract as `list[str]`, so SF-7 tool delete checks remain role-backed until a future runtime schema change.
- js-yaml
- psycopg[binary]
- pydantic>=2.0,<3.0
- pyyaml>=6.0
- react
- react-router
- react-router-dom
- sqlalchemy[asyncio]
- vite
- zustand

---

## Cross-Subfeature Interfaces

Derived from the decomposition dependency edges.

### SF-1 -> SF-2
- **Interface Type:** python_import
- **Description:** Loader imports Pydantic schema models (WorkflowConfig, NodeDefinition, EdgeDefinition, PhaseDefinition, etc.) to parse and validate YAML into typed objects. Runner imports node type enums and config models to dispatch execution.
- **Data Contract:** iriai_compose.declarative.schema module exports: WorkflowConfig, AskNode, MapNode, FoldNode, LoopNode, BranchNode, PluginNode, Edge, Phase, CostConfig, TransformRef, HookRef. All are Pydantic BaseModel subclasses with JSON Schema generation via model_json_schema().
- **Owner:** SF-1
- **Citations:**
- **[code]** `iriai-compose/iriai_compose/tasks.py`
  - Excerpt: Existing task types (Ask, Interview, Gate, Choose, Respond) as dataclass models
  - Reasoning: New schema models follow the same pattern but as Pydantic models for YAML/JSON validation

### SF-1 -> SF-3
- **Interface Type:** python_import
- **Description:** Testing framework imports schema models to validate structural correctness and type flow. Uses model_json_schema() for schema-level validation, field accessors for type flow checking across edges.
- **Data Contract:** Same schema module as SF-2 consumes. Additionally uses Edge.transform_ref and Node.output_type for type flow analysis.
- **Owner:** SF-1
- **Citations:**
- **[decision]** `D-10`
  - Excerpt: Custom testing framework built as we develop the schema
  - Reasoning: Testing framework validates schema correctness as a primary function

### SF-2 -> SF-3
- **Interface Type:** python_import
- **Description:** Testing framework uses the runner's run() function and DAG executor to run workflows against mock runtimes. Wraps run() with assertion hooks to track execution paths, artifact production, and branch decisions.
- **Data Contract:** iriai_compose.declarative.run(yaml_path, runtime, workspace, transform_registry, hook_registry) → ExecutionResult. ExecutionResult contains: nodes_executed (ordered list), artifacts (dict), branch_paths_taken (dict), cost_summary.
- **Owner:** SF-2
- **Citations:**
- **[code]** `iriai-compose/tests/conftest.py`
  - Excerpt: MockAgentRuntime records calls with role, prompt, output_type
  - Reasoning: Testing framework extends this mock pattern to work with the new runner

### SF-1 -> SF-4
- **Interface Type:** yaml_schema
- **Description:** Migration produces YAML files conforming to the schema defined in SF-1. The schema must be expressive enough to represent all patterns found in iriai-build-v2's three workflows.
- **Data Contract:** YAML files validated against WorkflowConfig JSON Schema. Migration may surface schema gaps that require SF-1 revisions.
- **Owner:** SF-1
- **Citations:**
- **[decision]** `D-11`
  - Excerpt: Migration plan for converting existing iriai-build-v2 workflows
  - Reasoning: Migration is the completeness test for the schema

### SF-2 -> SF-4
- **Interface Type:** python_import
- **Description:** Migration uses run() to execute translated YAML workflows and verify they produce equivalent behavior to the imperative Python versions.
- **Data Contract:** Same run() interface as SF-3 consumes. Migration also registers named transforms and hooks via TransformRegistry.register(name, fn) and HookRegistry.register(name, fn).
- **Owner:** SF-2
- **Citations:**
- **[code]** `iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py`
  - Excerpt: _build_subfeature_context(), _format_feedback(), to_str()
  - Reasoning: These imperative helpers must be registered as named transforms for the runner to resolve

### SF-3 -> SF-4
- **Interface Type:** python_import
- **Description:** Migration writes test suites using the testing framework's assertion helpers, mock runtimes, and fixtures to prove execution path equivalence.
- **Data Contract:** iriai_compose.testing exports: MockRuntime (configurable responses per role/node), assert_node_reached(result, node_id), assert_artifact_produced(result, key, schema), assert_branch_taken(result, branch_id, path), WorkflowTestCase base class.
- **Owner:** SF-3
- **Citations:**
- **[decision]** `D-10`
  - Excerpt: Custom testing framework built as we develop the schema
  - Reasoning: Migration is the primary consumer of the testing framework

### SF-1 -> SF-6
- **Interface Type:** json_schema
- **Description:** The workflow editor reads the JSON Schema (generated from SF-1's Pydantic models) to know what fields each node type requires, what edge types are valid, and what configuration options exist. The YAML pane serializes/deserializes using this schema. Validation uses it for type flow checking.
- **Data Contract:** JSON Schema published as a static artifact (e.g., workflow-schema.json) or fetched from a backend endpoint. Frontend uses it for: node inspector field generation, edge type validation, YAML syntax validation, export format.
- **Owner:** SF-1
- **Citations:**
- **[decision]** `D-15`
  - Excerpt: Dual-pane with visual graph editor primary, YAML secondary
  - Reasoning: Both the canvas and YAML pane need to understand the schema for rendering and validation

### SF-5 -> SF-6
- **Interface Type:** api_and_components
- **Description:** App foundation provides: authenticated API client (axios with JWT interceptor), React router shell (editor is a route), design system components (XP-themed buttons, panels, inputs), database-backed workflow CRUD (save/load/export endpoints), and auth context (user_id for scoping).
- **Data Contract:** API endpoints: GET/PUT /api/workflows/:id (full YAML content), POST /api/workflows/:id/versions (save new version), POST /api/workflows/:id/validate (server-side validation). React context: useAuth() hook providing user, accessToken. Component library: XPButton, XPPanel, XPInput, XPToolbar, XPSidebar.
- **Owner:** SF-5
- **Citations:**
- **[code]** `platform/deploy-console/deploy-console-frontend/src/app/styles/windows-xp.css`
  - Excerpt: XP-style inset/outset borders, purple gradients, frosted glass taskbar
  - Reasoning: Design system components from SF-5 are consumed by the editor

### SF-5 -> SF-7
- **Interface Type:** api_and_components
- **Description:** App foundation provides the same infrastructure as SF-6: authenticated API client, router shell (library pages are routes), design system components, and CRUD API endpoints for all 8 entity types.
- **Data Contract:** API endpoints: standard REST CRUD for /api/roles, /api/schemas, /api/templates, /api/phases, /api/plugins, /api/transforms. All scoped to authenticated user_id. Response format: { items: [...], total: int } for lists, individual entity for detail. Same React context and component library as SF-6.
- **Owner:** SF-5
- **Citations:**
- **[decision]** `D-14`
  - Excerpt: Screen map confirmed with workflows list as landing page
  - Reasoning: Library pages are sibling routes to the workflows list, all sharing the app shell

### SF-7 -> SF-6
- **Interface Type:** react_components
- **Description:** Libraries expose picker/selector components consumed by the editor's node inspectors. Role picker for Ask nodes, schema selector for output_type, template browser for the node palette, plugin selector, transform picker for edge inspector.
- **Data Contract:** React components: RolePicker({ onSelect, onCreateInline }), SchemaPicker({ onSelect }), TemplateBrowser({ onDrag }), PluginPicker({ onSelect }), TransformPicker({ edgeType, onSelect }). Each fetches from its own API endpoint and renders in the XP design system.
- **Owner:** SF-7
- **Citations:**
- **[decision]** `D-18`
  - Excerpt: Inline + library hybrid for roles
  - Reasoning: The editor needs picker components that bridge to the library data

### SF-6 -> SF-7
- **Interface Type:** callback_events
- **Description:** Editor triggers library mutations: inline role creation promotes to library, subgraph selection saves as custom task template, node group saves as phase template. Editor emits these as callbacks that library components handle.
- **Data Contract:** Callbacks: onPromoteRole(inlineRole) → creates Role via API, onSaveTemplate(selectedNodes, edges, interface) → creates CustomTaskTemplate via API, onSavePhase(selectedNodes, hooks, skipConditions) → creates PhaseTemplate via API. Returns created entity ID for the editor to reference.
- **Owner:** SF-6
- **Citations:**
- **[decision]** `D-18`
  - Excerpt: Inline + library hybrid for roles
  - Reasoning: Inline-to-library promotion requires the editor to trigger library writes