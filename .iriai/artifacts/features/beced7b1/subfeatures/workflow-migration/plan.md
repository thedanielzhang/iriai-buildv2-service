### SF-4: Workflow Migration & Litmus Test

<!-- SF: workflow-migration -->



## Architecture

### Revision Summary (v2)

Three targeted revisions from the architecture integration review:

1. **[C-4] artifact_key auto-write adopted:** Nodes with `artifact_key` auto-write their output to the store after execution. Explicit `store` PluginNodes are eliminated wherever a producing AskNode already has `artifact_key` set. Hosting hooks fire from the AskNode's `on_end` instead of a (now-removed) store PluginNode. This reduces node count by ~30% across all three workflows.

2. **[H-4] Three-tier PluginRegistry API:** SF-2's `PluginRegistry` supports `register_type(name, interface)` for PluginInterface metadata and `register_instance(name, config)` for PluginInstanceConfig entries — not just `register(name, plugin)` for concrete Plugin ABC instances. All `register_plugin_types()` and `register_instances()` functions updated to use this three-tier API. PluginInterface declarations include a `category` field for CategoryExecutor dispatch.

3. **[H-3] SF-1's 21 confirmed validation error codes:** All test assertions updated to use the canonical 21-code set from SF-1's validation module (not the older 10-code approximation).

### Decision Log

| ID | Decision | Rationale | Citation |
|----|----------|-----------|----------|
| D-SF4-1 | Three-category reclassification of the original 12 specialized plugins: (A) infrastructure connectors → general plugin type instances, (B) pure data transforms → inline edge transforms via `transform_fn`, (C) agent-mediated computation → AskNodes | Eliminates 12 bespoke Plugin ABC classes. Category A operations are side-effect writes to external systems — they map to 5 general plugin types with instance configs. Category B operations are pure functions on data — they belong on edges as `transform_fn`. Category C operations require LLM reasoning — they are AskNodes with specific actors. | [decision: Q1 — three-category reclassification], [code: SF-2 plan: STEP-30 — Plugin ABC in declarative/plugins.py] |
| D-SF4-2 | Resume semantics via runner checkpoint store + branch node skip conditions | Runner's CheckpointStore (SF-2 D-SF2-32 deferred resume) handles phase-level skip. Within fold-mode phases, the fold accumulator inherently tracks completed items — resume replays the fold with a pre-populated accumulator. No explicit resume Branch nodes in the YAML; the runner determines which phases to skip based on `FeatureState.completed_phases`. | [code: SF-1 plan: Entity Hardening — "Resume/checkpoint pattern not in schema" → "Runner responsibility"], [research: iriai-build-v2 resume uses FeatureState.completed_phases] |
| D-SF4-3 | Task templates for reusable compound patterns: `gate_and_revise`, `broad_interview`, `interview_gate_review` | Three helpers account for ~410 lines of imperative code reused 15+ times across workflows. Templates are self-contained phases with parameterized inputs/outputs, referenced via `$ref` + `bind` in WorkflowConfig.templates (SF-1 TemplateRef). Inline expansion for one-off patterns. | [code: SF-1 plan: Entity Hardening — gate_and_revise/broad_interview/interview_gate_review pattern mappings] |
| D-SF4-4 | Output types defined as TypeDefinition entries in workflow YAML `types:` sections using JSON Schema Draft 2020-12 | TypeDefinition.schema_def uses JSON Schema (SF-1 D-SF1-7, D-SF1-22). All output models from iriai-build-v2 (Envelope, ScopeOutput, PRD, etc.) become `types:` entries. Type references via `output_type` on nodes and `type_ref` on ports enable edge type-checking. | [code: SF-1 plan: I/O Type Model D-SF1-22 — TypeDefinition with schema_def], [code: SF-2 plan: Schema Entity Reference — TypeDefinition] |
| D-SF4-5 | Behavioral equivalence test suite with ~50-60 tests across 3 workflows | Tests verify the migrated YAML produces equivalent control flow and artifacts to the imperative Python. MockRuntime (SF-3 D-SF3-2) with scripted responses exercises all branch paths. No live API calls in the default test suite. | [code: SF-3 plan: MockRuntime — responses dict keyed by (node_id, role_name)] |
| D-SF4-6 | 5 general plugin types replace all Category A infrastructure connectors: `store`, `hosting`, `mcp`, `subprocess`, `http` | Each type defines an interface (inputs, outputs, config_schema, operations) with a `category` field for CategoryExecutor dispatch [H-4]. Concrete instances (e.g., `artifact_db`, `doc_host`, `git`) are declared in workflow YAML with `plugin_type` + `instance` config. This decouples workflow YAML from implementation details — swapping a Postgres store for S3 changes only the instance config, not the workflow DAG. | [decision: Q2 — 5 general plugin types], [code: SF-2 plan: PluginRegistry.register_type() — H-4] |
| D-SF4-7 | **[REVISED C-4]** `artifact_key` has dual read+write semantics. Explicit `store` PluginNodes only for advanced cases | `artifact_key` on any node triggers: (1) READ before execution — existing value injected into context, (2) WRITE after execution — node output auto-persisted to store. This eliminates ~30% of nodes across the three workflows. Explicit `store` PluginNodes remain only for: writing to a DIFFERENT key, writing from PluginNodes (non-Ask), or custom write logic. `context_keys` remains the mechanism for read-only context injection (no write). | [decision: C-4 — artifact_key auto-write adopted], [code: SF-2 plan: D-SF1-29 — dual read+write], [code: SF-2 plan: lines 1187-1190 — auto-write in Ask executor] |
| D-SF4-8 | `develop.yaml` is standalone — no cross-file `$ref` to `planning.yaml` | SF-1 schema supports `$ref` only for intra-file template references. The 6 planning phases in develop.yaml are structurally identical but independently defined. Consistency tests (REQ-45) verify structural equivalence. | [code: SF-1 plan: WorkflowConfig — TemplateRef for intra-file refs only] |
| D-SF4-9 | Pre-seed data package with `is_example: true` flag for SF-5 database | All migrated content (workflows, roles, schemas, templates, plugin type definitions + instances) is packaged as a JSON seed file with `is_example: true`. SF-5's database seed script loads this idempotently. | [research: SF-5 plan: database seed script expects is_example flag] |
| D-SF4-10 | Context hierarchy uses 4-level additive merge: workflow `context_keys` + phase `context_keys` + actor `context_keys` + node `context_keys` | Matches SF-1's context hierarchy model (D-SF1-24). Phase-level context_keys eliminate per-node redundancy. Workflow-level `context_keys: ["project"]` is global. No level replaces another — all merge with deduplication. | [code: SF-1 plan: Context Hierarchy Model D-SF1-24 — workflow/phase/actor/node merge] |
| D-SF4-11 | Tiered context becomes an inline edge transform (`transform_fn`), NOT a plugin | `tiered_context_builder` is a pure data transform: it reads decomposition edges + completed artifacts/summaries from the fold accumulator and produces a formatted context string. No side effects, no external I/O. Reclassified as Category B. ~20 lines of Python in `transform_fn`. | [decision: Q1 — Category B reclassification], [code: iriai-build-v2 `_build_subfeature_context` in _helpers.py] |
| D-SF4-12 | **[REVISED C-4]** Hook edges (`on_end`) fire from the producing AskNode, not from a separate store PluginNode | With `artifact_key` auto-write, there is no intermediate `store` PluginNode between the producing AskNode and the hosting PluginNode. The hosting PluginNode hooks directly from the producing AskNode's `on_end` hook port. This simplifies the DAG topology while preserving the hosting side-effect pattern. | [decision: C-4 — artifact_key auto-write removes intermediate store nodes], [code: SF-1 plan: Store Model D-SF1-27 — hosting as DAG topology] |
| D-SF4-13 | `fresh_sessions: true` on loop-mode phases for gate review loops | The `interview_gate_review` pattern requires fresh agent sessions per iteration to prevent auto-approval contamination. `fresh_sessions` is on LoopConfig/FoldConfig (SF-1 D-SF1-16), not on actors. | [code: SF-1 plan: Phase Iteration Session Model D-SF1-16 — fresh_sessions on LoopConfig/FoldConfig] |
| D-SF4-14 | Test fixtures in `tests/fixtures/workflows/migration/` with `conftest.py` shared fixtures | Migration test fixtures are isolated in a `migration/` subdirectory. Shared fixtures in `tests/migration/conftest.py` provide common setup. | [code: SF-3 plan: D-SF3-3 — fixtures directory `tests/fixtures/workflows/`] |
| D-SF4-15 | External service integrations declared as PluginInterface only — instances of general types, no implementation in SF-4 | `preview` (mcp type), `git` (subprocess type), `feedback_notify` (http type) are external service integrations. SF-4 declares their instance config in `workflow.plugins` sections but does not implement the underlying services. Mock implementations provided for testing via MockRuntime. | [decision: Q2 — general plugin types with instances], [code: SF-2 plan: Schema Entity Reference — PluginInterface] |
| D-SF4-16 | Envelope[T] pattern uses LoopConfig exit_condition `"data.complete"` | The universal `envelope_done` predicate in iriai-build-v2 checks `data.complete`. This maps directly to `LoopConfig.exit_condition: "data.complete"`. | [code: SF-1 plan: Entity Hardening — Interview = Loop phase + Ask nodes, `envelope_done` → `"data.complete"`] |
| D-SF4-17 | Workflow invocation is a runner concern — no trigger/listener nodes in the workflow schema | The workflow declares its expected input via WorkflowConfig's `input_type` field (SF-1 D-SF1-22). How and when a workflow is invoked (manual CLI, webhook, scheduled, etc.) is determined by the runner application, not by nodes in the DAG. The first phase receives its input from the runner's invocation context. This keeps the workflow schema purely declarative — it describes *what* to do, not *how* to start. | [decision: user feedback — triggers are runner responsibility], [code: SF-1 plan: D-SF1-4 — phase I/O boundary, D-SF1-22 — input_type on WorkflowConfig] |
| D-SF4-18 | `generate_summary` becomes an AskNode with `actor: summarizer, model: claude-haiku` | Category C reclassification. Producing Tier 3 summaries requires LLM reasoning (compression, extraction of key entities). Not a pure transform — it is an AskNode with a dedicated summarizer actor. | [decision: Q1 — Category C → AskNode] |
| D-SF4-19 | `extract_revision_plan` becomes an AskNode with extraction prompt | Category C reclassification. Extracting a structured RevisionPlan from prose review feedback requires LLM reasoning. AskNode with `actor: extractor` and a structured output prompt. | [decision: Q1 — Category C → AskNode] |
| D-SF4-20 | `sd_converter` becomes a Branch → edge transform / AskNode hybrid | Category C reclassification with Branch optimization. First, try JSON parse as a Branch condition. Success path: edge transform converts parsed JSON to HTML. Failure path: AskNode converts markdown to structured JSON, then edge transform renders HTML. | [decision: Q1 — Category C → Branch + AskNode] |
| D-SF4-21 | Category B edge transforms use `transform_fn` with inline Python | 8 former plugins become edge transforms: `handover_compress`, `feedback_formatter` (format-only), `id_renumberer`, `collect_files`, `normalize_review_slugs`, `build_task_prompt`, `tiered_context_builder`, `build_env_overrides`. Each is a pure function expressed as ~5-25 lines of inline Python on an Edge's `transform_fn` field. | [decision: Q1 — Category B → inline edge transforms] |
| D-SF4-22 | **[NEW C-4]** `artifact_key` auto-write eliminates explicit `artifact_write` / `store` PluginNodes for simple write patterns | With C-4 resolution adopted: every AskNode that previously had a downstream `store` PluginNode (artifact_db, put, key) now has `artifact_key: "artifacts.{key}"` set directly. The runner auto-writes the output after execution. Hosting PluginNodes hook from the AskNode's `on_end`. `store` PluginNodes remain ONLY for: (a) PluginNode outputs that need persisting (e.g., preview_url from mcp), (b) writes to a different key, (c) custom write logic. Node count reduction: planning ~50→~35, develop ~60→~42, bugfix ~35→~27. | [decision: C-4 — artifact_key auto-write], [code: SF-2 plan: auto-write after execution when artifact_key is set] |
| D-SF4-23 | **[NEW H-4]** Three-tier PluginRegistry API for type + instance metadata registration | SF-2's PluginRegistry exposes three registration tiers: (1) `register(name, plugin)` for concrete Plugin ABC instances, (2) `register_type(name, interface)` for PluginInterface metadata, (3) `register_instance(name, config)` for PluginInstanceConfig entries. SF-4 uses tiers 2 and 3 only — no concrete Plugin ABC implementations. Each PluginInterface has a `category` field (e.g., "store", "hosting", "mcp", "cli", "http") enabling CategoryExecutor dispatch. | [decision: H-4 — three-tier plugin registration], [code: SF-2 plan: PluginRegistry class — register_type(), register_instance(), register_category_executor()] |
| D-SF4-24 | **[NEW H-3]** SF-1's 21 confirmed validation error codes used in all test assertions | SF-1 defines 21 canonical error codes: `dangling_edge`, `duplicate_node_id`, `duplicate_phase_id`, `invalid_actor_ref`, `invalid_phase_mode_config`, `invalid_hook_edge_transform`, `phase_boundary_violation`, `cycle_detected`, `unreachable_node`, `type_mismatch`, `invalid_branch_config`, `invalid_plugin_ref`, `missing_output_condition`, `invalid_io_config`, `invalid_type_ref`, `invalid_store_ref`, `invalid_store_key_ref`, `store_type_mismatch`, `invalid_switch_function_config`, `invalid_workflow_io_ref`, `missing_required_field`. All SF-4 test assertions use these exact codes. | [decision: H-3 — canonical validation codes], [code: SF-1 plan: STEP-33 — 21 error codes in validation.py] |

### Prerequisites from Other Subfeatures

**SF-1 (Declarative Schema) must provide:**
- `iriai_compose.schema` module with: `WorkflowConfig`, `AskNode`, `BranchNode`, `PluginNode`, `PhaseDefinition`, `Edge`, `PortDefinition`, `NodeDefinition`, `ActorDefinition`, `RoleDefinition`, `TypeDefinition`, `MapConfig`, `FoldConfig`, `LoopConfig`, `SequentialConfig`, `StoreDefinition`, `StoreKeyDefinition`, `PluginInterface`, `PluginInstanceConfig`, `TemplateRef`, `CostConfig`
- `iriai_compose.schema.validation` module with: `validate_workflow()`, `validate_type_flow()`, `detect_cycles()` returning `list[ValidationError]` using the 21 canonical error codes [D-SF4-24]
- `iriai_compose.schema.io` module with: `load_workflow()`, `dump_workflow()`
- **Edge `transform_fn` support:** optional inline Python string on Edge model for Category B transforms
- **WorkflowConfig `input_type`:** declares expected input structure for the workflow's first phase [D-SF4-17]
- **`artifact_key` on NodeBase:** `str | None` field enabling dual read+write semantics [D-SF4-22, C-4]

**SF-2 (DAG Loader & Runner) must provide:**
- `iriai_compose.declarative` module with: `run()`, `load_workflow()`, `RuntimeConfig`, `ExecutionResult`, `PluginRegistry`, `CategoryExecutor`, `load_runtime_config()`
- Plugin ABC in `iriai_compose/declarative/plugins.py` with `execute()` method
- **PluginRegistry three-tier API [D-SF4-23, H-4]:**
  - `register(name, plugin)` — concrete Plugin ABC instances
  - `register_type(name, interface)` — PluginInterface metadata with `category` field
  - `register_instance(name, config)` — PluginInstanceConfig entries
  - `register_category_executor(category, executor)` — category dispatchers
  - `get_type(name)`, `get_instance(name)`, `has_type(name)`, `has_instance(name)` — lookups
- `ExecutionResult` with: `success`, `error`, `nodes_executed`, `artifacts`, `branch_paths`, `cost_summary`, `duration_ms`, `workflow_output`, `hook_warnings`
- **Edge transform execution:** runner evaluates `transform_fn` Python expressions during edge traversal
- **artifact_key auto-write [D-SF4-22, C-4]:** runner auto-writes node output to store at `artifact_key` after execution
- **Workflow invocation:** runner accepts initial input and passes it to the first phase's `$input` port [D-SF4-17]

**SF-3 (Testing Framework) must provide:**
- `iriai_compose.testing` module with: `MockRuntime`, `MockInteraction`, `WorkflowBuilder`, `run_test`
- Assertions: `assert_node_reached`, `assert_artifact`, `assert_branch_taken`, `assert_validation_error`, `assert_node_count`, `assert_phase_executed`
- Snapshot: `assert_yaml_round_trip`, `assert_yaml_equals`
- Validation re-exports: `validate_workflow`, `validate_type_flow`, `detect_cycles`

---

## Implementation Steps

### STEP-28: Plugin Type Interfaces, Instance Configs, and Edge Transform Catalog

**Objective:** Define the 5 general plugin type interfaces (store, hosting, mcp, subprocess, http) with `category` fields for SF-2's CategoryExecutor dispatch [H-4]. Document all instance configurations. Catalog all Category B edge transforms. Register using SF-2's three-tier PluginRegistry API (`register_type()` + `register_instance()`). Document all Category C → AskNode conversions.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/plugins/__init__.py` | modify |
| `iriai_compose/plugins/types.py` | create |
| `iriai_compose/plugins/instances.py` | create |
| `iriai_compose/plugins/transforms.py` | create |
| `iriai_compose/declarative/plugins.py` | read |
| `iriai_compose/schema/nodes.py` | read |

**Instructions:**

1. **`iriai_compose/plugins/types.py`** — 5 general plugin type PluginInterface declarations with `category` field [H-4]:

   **`store` type** — KV persistence (explicit writes only; reads via `context_keys`, auto-writes via `artifact_key` [C-4]):
   ```python
   STORE_INTERFACE = PluginInterface(
       name="store",
       category="store",  # [H-4] CategoryExecutor dispatch key
       description="Key-value persistence. Used for explicit writes to different keys or from PluginNode outputs. Simple artifact writes use artifact_key auto-write instead [C-4].",
       inputs=[PortDefinition(name="data", type_ref="Any", description="Data to persist")],
       outputs=[PortDefinition(name="confirmation", type_ref="StoreWriteResult", description="Write confirmation with key and timestamp")],
       config_schema={
           "type": "object",
           "properties": {
               "operation": {"type": "string", "enum": ["put", "delete"]},
               "key": {"type": "string", "description": "Store key to write to"},
               "namespace": {"type": "string", "description": "Optional namespace prefix"}
           },
           "required": ["operation", "key"]
       },
       operations=["put", "delete"]
   )
   ```

   **`hosting` type** — Content hosting + URL generation + annotation collection:
   ```python
   HOSTING_INTERFACE = PluginInterface(
       name="hosting",
       category="service",  # [H-4] service category
       description="Content hosting with URL generation and feedback annotation collection.",
       inputs=[PortDefinition(name="content", type_ref="Any", description="Content to host")],
       outputs=[PortDefinition(name="hosted_url", type_ref="string", description="URL of hosted content")],
       config_schema={
           "type": "object",
           "properties": {
               "operation": {"type": "string", "enum": ["push", "update", "collect_annotations", "clear_feedback"]},
               "content_type": {"type": "string", "description": "MIME type or format hint"},
               "artifact_key": {"type": "string", "description": "Associated artifact store key"}
           },
           "required": ["operation"]
       },
       operations=["push", "update", "collect_annotations", "clear_feedback"]
   )
   ```

   **`mcp` type** — MCP tool invocation:
   ```python
   MCP_INTERFACE = PluginInterface(
       name="mcp",
       category="mcp",  # [H-4] mcp category
       description="Invokes tools on an MCP server.",
       inputs=[PortDefinition(name="tool_input", type_ref="Any", description="Tool-specific input")],
       outputs=[PortDefinition(name="tool_output", type_ref="Any", description="Tool-specific output")],
       config_schema={
           "type": "object",
           "properties": {
               "tool_name": {"type": "string", "description": "MCP tool to invoke"},
               "server": {"type": "string", "description": "MCP server identifier"},
               "timeout_ms": {"type": "integer", "default": 30000}
           },
           "required": ["tool_name", "server"]
       },
       operations=["call_tool"]
   )
   ```

   **`subprocess` type** — CLI command execution:
   ```python
   SUBPROCESS_INTERFACE = PluginInterface(
       name="subprocess",
       category="cli",  # [H-4] cli category
       description="Executes CLI commands in a subprocess.",
       inputs=[PortDefinition(name="args", type_ref="Any", description="Command arguments")],
       outputs=[PortDefinition(name="result", type_ref="SubprocessResult", description="Exit code, stdout, stderr")],
       config_schema={
           "type": "object",
           "properties": {
               "command": {"type": "string", "description": "Base command (e.g., 'git')"},
               "subcommand": {"type": "string", "description": "Subcommand (e.g., 'commit')"},
               "working_dir": {"type": "string", "description": "Working directory"},
               "timeout_ms": {"type": "integer", "default": 60000}
           },
           "required": ["command"]
       },
       operations=["execute"]
   )
   ```

   **`http` type** — Generic HTTP API calls:
   ```python
   HTTP_INTERFACE = PluginInterface(
       name="http",
       category="service",  # [H-4] service category
       description="Generic HTTP API calls with configurable method, headers, and body.",
       inputs=[PortDefinition(name="payload", type_ref="Any", description="Request body or parameters")],
       outputs=[PortDefinition(name="response", type_ref="HttpResponse", description="Status code, headers, body")],
       config_schema={
           "type": "object",
           "properties": {
               "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
               "url": {"type": "string"},
               "headers": {"type": "object"},
               "timeout_ms": {"type": "integer", "default": 10000}
           },
           "required": ["method", "url"]
       },
       operations=["request"]
   )

   ALL_PLUGIN_TYPES = [STORE_INTERFACE, HOSTING_INTERFACE, MCP_INTERFACE, SUBPROCESS_INTERFACE, HTTP_INTERFACE]
   ```

2. **`iriai_compose/plugins/instances.py`** — Instance configurations for the 3 workflows (unchanged from v1 — see original plan for full listings of ARTIFACT_DB, ARTIFACT_MIRROR, DOC_HOST, PREVIEW_MCP, PLAYWRIGHT_MCP, GIT_SUBPROCESS, FEEDBACK_NOTIFY).

3. **Category B edge transform catalog** — Document in `iriai_compose/plugins/transforms.py` (unchanged from v1 — see original plan for full Python strings of all 8 transforms: HANDOVER_COMPRESS_TRANSFORM, FEEDBACK_FORMATTER_TRANSFORM, ID_RENUMBERER_TRANSFORM, COLLECT_FILES_TRANSFORM, NORMALIZE_REVIEW_SLUGS_TRANSFORM, BUILD_TASK_PROMPT_TRANSFORM, TIERED_CONTEXT_BUILDER_TRANSFORM, BUILD_ENV_OVERRIDES_TRANSFORM).

4. **Category C → AskNode conversion catalog** — Unchanged from v1 (generate_summary, extract_revision_plan, sd_converter specs).

5. **Update `iriai_compose/plugins/__init__.py`** — **[REVISED H-4]** Use SF-2's three-tier PluginRegistry API:

   ```python
   from iriai_compose.plugins.types import ALL_PLUGIN_TYPES
   from iriai_compose.plugins.instances import ALL_PLUGIN_INSTANCES

   def register_plugin_types(registry: "PluginRegistry") -> None:
       """Register all 5 general plugin type interfaces via registry.register_type() [H-4].

       Uses PluginRegistry's Tier 2 API — NOT register() which expects Plugin ABC instances.
       Each interface has a `category` field enabling CategoryExecutor dispatch.
       """
       for interface in ALL_PLUGIN_TYPES:
           registry.register_type(interface.name, interface)

   def register_instances(registry: "PluginRegistry") -> None:
       """Register all plugin instance configs via registry.register_instance() [H-4].

       Uses PluginRegistry's Tier 3 API. Each instance references a plugin_type
       that must already be registered via register_type().
       """
       for instance in ALL_PLUGIN_INSTANCES:
           registry.register_instance(instance.instance_id, instance)

   def register_builtins(registry: "PluginRegistry") -> None:
       """Convenience: register both types and instances."""
       register_plugin_types(registry)
       register_instances(registry)
   ```

**Acceptance Criteria:**
- `from iriai_compose.plugins.types import STORE_INTERFACE, HOSTING_INTERFACE, MCP_INTERFACE, SUBPROCESS_INTERFACE, HTTP_INTERFACE` succeeds
- All 5 interfaces have `name`, `category`, `description`, `inputs`, `outputs`, `config_schema`, `operations` — `category` is the H-4 addition
- `register_plugin_types(registry)` calls `registry.register_type()` (NOT `registry.register()`) for each interface [H-4]
- `register_instances(registry)` calls `registry.register_instance()` (NOT `registry.register()`) for each instance [H-4]
- After registration: `registry.has_type("store")` returns True, `registry.get_type("store").category == "store"`
- After registration: `registry.has_instance("artifact_db")` returns True, `registry.get_instance("artifact_db").plugin_type == "store"`
- STORE_INTERFACE description mentions artifact_key auto-write as primary simple-write mechanism [C-4]
- All 8 edge transform strings are syntactically valid Python (`compile(code, '<string>', 'exec')` succeeds)
- `HANDOVER_COMPRESS_TRANSFORM` NEVER touches `failed_attempts`
- `BUILD_ENV_OVERRIDES_TRANSFORM` reads from `os.environ`, not from YAML config

**Counterexamples:**
- Do NOT use `registry.register()` for plugin types — that expects Plugin ABC instances. Use `registry.register_type()` [H-4]
- Do NOT use `registry.register()` for plugin instances — use `registry.register_instance()` [H-4]
- Do NOT implement Plugin ABC classes for the 5 types — they are PluginInterface declarations only [D-SF4-1, D-SF4-6]
- Do NOT omit `category` field on PluginInterface — it is required for CategoryExecutor dispatch [H-4]
- Do NOT put side-effect operations in edge transforms — transforms must be pure functions [D-SF4-21]
- Do NOT use `yaml.load()` anywhere — always `yaml.safe_load()` [code: SF-2 plan: STEP-29]
- Do NOT add `ruamel.yaml` dependency [code: SF-1 plan: D-SF1-8]

**Requirement IDs:** REQ-22, REQ-23, REQ-24, REQ-25, REQ-26, REQ-27, REQ-28, REQ-29, REQ-30, REQ-31 | **Journey IDs:** J-1, J-2, J-3, J-4

---

### STEP-29: Output Type Definitions

**Objective:** Define all output model types as `TypeDefinition` entries for use in workflow YAML `types:` sections. Each type uses JSON Schema Draft 2020-12 format per SF-1 `TypeDefinition.schema_def`. Types are defined as reusable YAML fragments that can be included in each workflow file's `types:` section.

**Scope:**
| Path | Action |
|------|--------|
| `tests/fixtures/workflows/migration/types/common.yaml` | create |
| `tests/fixtures/workflows/migration/types/planning.yaml` | create |
| `tests/fixtures/workflows/migration/types/develop.yaml` | create |
| `tests/fixtures/workflows/migration/types/bugfix.yaml` | create |

**Instructions:**

Unchanged from v1. See original plan for full type definitions. The `StoreWriteResult` type remains in common.yaml — still needed for the remaining explicit `store` PluginNodes (those writing PluginNode outputs or different-key writes).

**Acceptance Criteria:** Unchanged from v1.
**Counterexamples:** Unchanged from v1.

**Requirement IDs:** REQ-1, REQ-11, REQ-16, REQ-32 | **Journey IDs:** J-1, J-2, J-3

---

### STEP-30: Planning Workflow YAML (`planning.yaml`)

**Objective:** Translate the planning workflow's 6 phases into a single YAML file conforming to the SF-1 schema. **[C-4 REVISION]** AskNodes that produce artifacts use `artifact_key` for auto-write instead of explicit `store` PluginNodes. Hosting PluginNodes hook from the producing AskNode's `on_end`. Explicit `store` PluginNodes only appear where the write source is not an AskNode or the key differs. Node count reduced from ~50 to **~35** nodes.

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
   actors: { ... }  # ~10 roles
   templates: { ... }  # gate_and_revise, broad_interview, interview_gate_review
   ```

   **Note [C-4]:** `artifact_db` plugin instance is no longer declared at workflow level. Simple artifact writes use `artifact_key` on AskNodes — the runner auto-writes. Only `doc_host` remains as a PluginNode-based plugin.

2. **[C-4] artifact_key auto-write pattern** — How former imperative code maps:

   **Before (v1 — explicit store PluginNode):**
   ```yaml
   # 3 nodes: Ask → store PluginNode → hosting PluginNode
   - id: scope_resolver
     type: ask
     actor: pm
     ...
   - id: write_scope
     type: plugin
     plugin_ref: artifact_db
     config: { operation: put, key: scope }
   - id: host_scope
     type: plugin
     plugin_ref: doc_host
     config: { operation: push, artifact_key: scope }
     outputs: []
   edges:
     - source: scope_resolver.output → write_scope.input
     - source: write_scope.on_end → host_scope.input
   ```

   **After (v2 — artifact_key auto-write):**
   ```yaml
   # 2 nodes: Ask (with artifact_key) → hosting PluginNode via hook
   - id: scope_resolver
     type: ask
     actor: pm
     artifact_key: "artifacts.scope"  # [C-4] Auto-writes output to store
     ...
   - id: host_scope
     type: plugin
     plugin_ref: doc_host
     config: { operation: push, artifact_key: scope }
     outputs: []  # fire-and-forget
   edges:
     - source: scope_resolver.on_end → host_scope.input  # [C-4] Hook from AskNode, not store PluginNode
   ```

3. **Edge transform usage** — Unchanged from v1 (tiered_context_builder as transform_fn on edges).

4. **AskNode for `generate_summary`** (Category C) — Now also uses `artifact_key`:
   ```yaml
   - id: generate_summary
     type: ask
     actor: summarizer
     artifact_key: "artifacts.{{ current_slug }}_summary"  # [C-4] Auto-writes
     prompt: |
       Produce a compressed summary of this artifact...
     output_type: string
   ```

5. **Phase definitions (6 phases) — [C-4 revisions marked]:**

   **ScopingPhase** (`mode: loop`):
   - First phase — receives workflow input from runner invocation context [D-SF4-17]
   - Loop condition: `"data.complete"` (Envelope pattern) [D-SF4-16]
   - Nodes: `scope_interviewer` AskNode (actor: user, output_type: Envelope) → `scope_resolver` AskNode (actor: pm, **artifact_key: "artifacts.scope"** [C-4]) → Branch (complete/continue)
   - **[C-4] REMOVED:** `store` PluginNode for scope write — replaced by `artifact_key` on `scope_resolver`
   - Hosting: `host_scope` PluginNode (doc_host, push) hooked from `scope_resolver.on_end` [C-4]
   - Phase-level context_keys: `["project"]`

   **PMPhase** (`mode: sequential`):
   - Contains sub-phases and nodes:
     1. `broad_interview` template (`$ref: broad_interview`, bind: {lead_actor: lead_pm, output_type: PRD, artifact_key: prd})
     2. `decompose_and_gate` sub-phase: Ask (decomposer, **artifact_key: "artifacts.decomposition"** [C-4]) → `gate_and_revise` template
     3. `per_subfeature_fold` sub-phase (`mode: fold`):
        - `collection: "ctx['decomposition'].subfeatures"`
        - `accumulator_init: "{'completed_artifacts': {}, 'completed_summaries': {}}"`
        - `reducer: "{**accumulator, 'completed_artifacts': {**accumulator['completed_artifacts'], result['slug']: result['artifact']}, 'completed_summaries': {**accumulator['completed_summaries'], result['slug']: result.get('summary', '')}}"`
        - Edge with `tiered_context_builder` transform_fn → Ask (pm interview, **artifact_key: "artifacts.{{ current_slug }}"** [C-4]) → `gate_and_revise` template → `generate_summary` AskNode (actor: summarizer, **artifact_key: "artifacts.{{ current_slug }}_summary"** [C-4])
        - **[C-4] REMOVED:** All `store` PluginNodes for artifact writes and summary writes
     4. `integration_review` AskNode (actor: lead_pm, context_keys: all subfeature artifacts)
     5. `compile_artifacts` AskNode (actor: pm_compiler, **artifact_key: "artifacts.prd"** [C-4]) → edge with `id_renumberer` transform_fn (applied BEFORE auto-write via edge from compiler output)
     6. `interview_gate_review` template (`$ref`, bind: compiler_actor: pm_compiler, artifact_prefix: prd)
   - **[C-4] REMOVED:** All `store` PluginNodes for AskNode outputs
   - Hosting: PluginNodes (doc_host, push) hooked from respective AskNode `on_end` ports [C-4, D-SF4-12]
   - Phase-level context_keys: `["scope", "decomposition"]`

   **DesignPhase** (`mode: sequential`):
   - Same structural pattern as PMPhase with [C-4] simplification
   - Actors: lead_designer, designer, design_compiler
   - Output: DesignDecisions at `artifacts.design` via `artifact_key` [C-4]
   - **[C-4] REMOVED:** All `store` PluginNodes for AskNode outputs
   - Phase-level context_keys: `["scope", "prd"]`

   **ArchitecturePhase** (`mode: sequential`):
   - Dual artifact pattern: per-subfeature nodes have `artifact_key` for their primary output [C-4]
   - Two separate compilation sub-phases (plan + system design) — compilers use `artifact_key` [C-4]
   - `sd_converter` as Branch + AskNode [D-SF4-20]: unchanged logic
   - **[C-4] REMOVED:** All `store` PluginNodes for AskNode outputs
   - Phase-level context_keys: `["scope", "prd", "design"]`

   **PlanReviewPhase** (`mode: loop`):
   - Loop body:
     1. Map sub-phase (`mode: map`, `collection: "ctx['review_targets']"`, 3 parallel reviewer AskNodes: completeness, security, citation)
     2. Branch (all_approved → condition_met, any_failed → revision)
     3. Ask (architect revises based on feedback)
     4. Branch (iteration >= max → max_exceeded for user escalation)
   - `loop_config: {exit_condition: "all(r['approved'] for r in data.values())", max_iterations: 3}`
   - Phase-level context_keys: `["scope", "prd", "design", "plan"]`

   **TaskPlanningPhase** (`mode: sequential`):
   - `broad_interview` template (GlobalImplementationStrategy)
   - `per_subfeature_fold` sub-phase (ImplementationDAG per subfeature) — AskNodes use `artifact_key` [C-4]
   - Compile into unified ImplementationDAG — compiler uses `artifact_key` [C-4]
   - `interview_gate_review` template
   - **[C-4] REMOVED:** All `store` PluginNodes for AskNode outputs
   - Phase-level context_keys: `["scope", "prd", "design", "plan", "system_design"]`

6. **Actor definitions** (~10 roles) — Unchanged from v1.

7. **Workflow-level edges** connecting phases in sequence with typed ports.

**Acceptance Criteria:**
- `load_workflow("planning.yaml")` succeeds without errors
- `validate_workflow(config)` returns empty error list using SF-1's 21 codes [D-SF4-24]
- 6 phases with correct modes: scoping=loop, pm=sequential, design=sequential, architecture=sequential, plan_review=loop, task_planning=sequential
- Workflow declares `input_type` for runner invocation [D-SF4-17]
- **[C-4]** AskNodes that produce artifacts have `artifact_key` set (e.g., `artifact_key: "artifacts.scope"`)
- **[C-4]** NO explicit `store` PluginNodes after AskNodes that have `artifact_key` — auto-write handles persistence
- **[C-4]** Hosting PluginNodes hook from the producing AskNode's `on_end`, NOT from a store PluginNode [D-SF4-12 revised]
- **[C-4]** `store` PluginNodes only appear for non-AskNode outputs that need explicit persistence
- `tiered_context_builder` appears as `transform_fn` on edges [D-SF4-21]
- `generate_summary` appears as AskNode with `artifact_key` set [D-SF4-18, C-4]
- `id_renumberer` appears as `transform_fn` on edge [D-SF4-21]
- All actor references resolve to `workflow.actors` entries
- All plugin references resolve to `workflow.plugins` entries (doc_host; store PluginNodes only if present)
- Node count ~35 (reduced from ~50 by eliminating ~15 store PluginNodes) [C-4]
- No resume Branch nodes — checkpoint handles resume [D-SF4-2]
- `fresh_sessions: true` on interview_gate_review loop config [D-SF4-13]

**Counterexamples:**
- Do NOT add explicit `store` PluginNodes after AskNodes that have `artifact_key` — use auto-write [C-4]
- Do NOT hook hosting PluginNodes from store PluginNodes — hook from the producing AskNode's `on_end` [C-4]
- Do NOT declare `artifact_db` as a plugin instance at workflow level if no explicit `store` PluginNodes remain [C-4]
- Do NOT use specialized plugin classes — they are reclassified [D-SF4-1]
- Do NOT use compound nodes (FoldNode, MapNode, LoopNode) — use phase modes
- Do NOT add resume Branch nodes — CheckpointStore handles resume [D-SF4-2]
- Do NOT put side-effect operations in `transform_fn` — transforms must be pure [D-SF4-21]
- Do NOT put `fresh_sessions` on actors — goes on LoopConfig/FoldConfig [D-SF4-13]
- Do NOT confuse `artifact_key` (auto-write to store) with `context_keys` (read-only context injection) [C-4]
- Do NOT use cross-file `$ref` — templates are intra-file only [D-SF4-8]
- Do NOT add trigger/listener nodes [D-SF4-17]

**Requirement IDs:** REQ-1, REQ-2, REQ-3, REQ-4, REQ-5, REQ-6, REQ-7, REQ-8, REQ-9, REQ-10, REQ-32, REQ-33, REQ-34 | **Journey IDs:** J-1

---

### STEP-31: Develop Workflow YAML (`develop.yaml`)

**Objective:** Translate the develop workflow as a standalone YAML file containing all 7 phases. **[C-4 REVISION]** All AskNode artifact writes use `artifact_key` auto-write. Node count reduced from ~60 to **~42**. Self-contained — no cross-file refs. Runner handles invocation [D-SF4-17].

**Scope:**
| Path | Action |
|------|--------|
| `tests/fixtures/workflows/migration/develop.yaml` | create |
| `tests/fixtures/workflows/migration/planning.yaml` | read |
| `tests/fixtures/workflows/migration/types/common.yaml` | read |
| `tests/fixtures/workflows/migration/types/planning.yaml` | read |
| `tests/fixtures/workflows/migration/types/develop.yaml` | read |

**Instructions:**

1. **Workflow-level structure:** Same as planning.yaml but with 7 phases, additional types, and additional plugins (git, preview).

   ```yaml
   schema_version: "1.0"
   name: develop
   description: "Development workflow — planning + implementation"
   input_type: "ScopeOutput"
   context_keys: ["project"]

   plugins:
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

   **Note [C-4]:** `artifact_db` no longer declared. Simple writes via `artifact_key`. `store` PluginNodes only if PluginNode output needs persisting.

2. **Planning phases (1-6):** Structurally identical to planning.yaml with [C-4] simplification. Same modes, same template bindings, same edge transforms, same AskNode `artifact_key` patterns. **[C-4] REMOVED:** All `store` PluginNodes after AskNodes. Independent definitions — no cross-file `$ref` [D-SF4-8].

3. **ImplementationPhase** (phase 7, `mode: loop`):
   - `loop_config: {exit_condition: "data.user_approved is True"}`
   - Loop body:
     1. **Branch** (`has_feedback`): checks if rejection feedback exists
        - `has_feedback` port → fix path
        - `no_feedback` port → DAG execution path
     2. **Fix path**: Single AskNode (actor: `implementer`, **artifact_key: "artifacts.fix_result"** [C-4])
     3. **DAG execution path** — Fold > Map > Loop nesting (3 levels):
        - **Fold sub-phase** (`mode: fold`) over `dag.execution_order`:
          - Each group iteration body:
            - **Map sub-phase** (`mode: map`) — parallel tasks:
              - Edge with `build_task_prompt` transform_fn [D-SF4-21]
              - AskNode (actor: `implementer-g{idx}-t{idx}`, **artifact_key: "artifacts.impl_g{idx}_t{idx}"** [C-4])
              - **[C-4] REMOVED:** `store` PluginNodes after implementer AskNodes
            - Edge with `collect_files` transform_fn [D-SF4-21]
            - Verification AskNode (actor: `smoke_tester`)
            - **Retry loop sub-phase** (`mode: loop`, `max_iterations: 2`)
            - Edge with `handover_compress` transform_fn [D-SF4-21]
     4. **Sequential chain**: QA AskNode → Code Review AskNode → User Gate (AskNode with interaction actor)
   - `condition_met` port: user approved → workflow complete

4. **Additional actors:** `implementer`, `qa`/`smoke_tester`, `code_reviewer` — unchanged.

**Acceptance Criteria:**
- `load_workflow("develop.yaml")` succeeds without errors
- `validate_workflow(config)` returns empty list using SF-1's 21 codes [D-SF4-24]
- 7 phases present: 6 planning + implementation
- Planning phases structurally match planning.yaml with [C-4] simplification
- **[C-4]** Implementer AskNodes use `artifact_key` — no downstream `store` PluginNodes
- **[C-4]** Hosting hooks from AskNode `on_end` [D-SF4-12 revised]
- Node count ~42 (reduced from ~60) [C-4]
- `build_task_prompt`, `collect_files`, `handover_compress` as `transform_fn` on edges [D-SF4-21]
- Branch routes on rejection feedback, NOT iteration count [REQ-12]
- Fold > Map > Loop nesting correctly structured (3 levels)
- No cross-file `$ref` — fully self-contained [D-SF4-8]

**Counterexamples:**
- Do NOT add explicit `store` PluginNodes after AskNodes with `artifact_key` [C-4]
- Do NOT use cross-file `$ref` to planning.yaml [D-SF4-8]
- Do NOT branch on iteration count — branch on rejection feedback [REQ-12]
- Do NOT re-execute full DAG on rejection — only targeted fix path [REQ-15]
- Do NOT use shared actor names for parallel tasks
- Do NOT put side-effect operations in edge transforms [D-SF4-21]
- Do NOT add trigger/listener nodes [D-SF4-17]

**Requirement IDs:** REQ-11, REQ-12, REQ-13, REQ-14, REQ-15, REQ-45, REQ-49 | **Journey IDs:** J-2

---

### STEP-32: Bugfix Workflow YAML (`bugfix.yaml`)

**Objective:** Translate the bugfix workflow's 8 linear phases into a single YAML file. **[C-4 REVISION]** AskNode artifact writes use `artifact_key` auto-write. Explicit `store` PluginNodes remain only for PluginNode outputs that need persisting (e.g., preview_url from MCP plugin). Node count reduced from ~35 to **~27**.

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
   ```

   **Note [C-4]:** `artifact_db` IS still declared here because bugfix has PluginNode outputs (preview_url from MCP) that need explicit `store` writes. AskNode artifacts still use `artifact_key` auto-write.

2. **Phase definitions (8 phases) — [C-4 revisions marked]:**

   **BugIntakePhase** (`mode: loop`):
   - `loop_config: {exit_condition: "data.complete"}`
   - Ask (bug_interviewer, **artifact_key: "artifacts.bug_report"** [C-4]) → `gate_and_revise` template
   - **[C-4] REMOVED:** `store` PluginNode for bug_report write
   - Hosting: doc_host hooked from AskNode `on_end` [C-4]

   **EnvironmentSetupPhase** (`mode: sequential`):
   - `subprocess` PluginNode (`plugin_ref: git`, branch creation) — fire-and-forget
   - Edge with `build_env_overrides` transform_fn [D-SF4-21]
   - `mcp` PluginNode (`plugin_ref: preview`, deploy) — output is preview_url
   - **[C-4] KEPT:** `store` PluginNode (`plugin_ref: artifact_db`, put, preview_url) — PluginNode output, not AskNode, so explicit store needed

   **BaselinePhase** (`mode: sequential`):
   - `mcp` PluginNode (`plugin_ref: playwright`, run_e2e)
   - **[C-4] KEPT:** `store` PluginNode for baseline results — PluginNode output
   - `smoke_tester` AskNode

   **BugReproductionPhase** (`mode: sequential`):
   - `bug_reproducer` AskNode (**artifact_key: "artifacts.reproduction"** [C-4])
   - **[C-4] REMOVED:** `store` PluginNode for ReproductionResult

   **DiagnosisAndFixPhase** (`mode: loop`):
   - `loop_config: {exit_condition: "not data.reproduced", max_iterations: 3}`
   - Loop body:
     1. Map sub-phase (2 parallel RCA analysts) — AskNodes with distinct prompts
     2. `bug_fixer` AskNode (**artifact_key: "artifacts.fix_result"** [C-4])
     3. `subprocess` PluginNode (git commit) — fire-and-forget
     4. `subprocess` PluginNode (git push) — fire-and-forget
     5. `mcp` PluginNode (preview redeploy) — fire-and-forget
     6. `bug_reproducer` AskNode (verification)
     7. Branch: fixed → condition_met, still broken → handover_compress edge → loop
   - **[C-4] REMOVED:** `store` PluginNodes after AskNodes that have `artifact_key`

   **RegressionPhase** (`mode: sequential`):
   - `mcp` PluginNode (playwright E2E) → `smoke_tester` AskNode

   **ApprovalPhase** (`mode: sequential`):
   - Gate AskNode (user interaction)

   **CleanupPhase** (`mode: sequential`):
   - `mcp` PluginNode (preview teardown) — fire-and-forget

**Acceptance Criteria:**
- `load_workflow("bugfix.yaml")` succeeds without errors
- `validate_workflow(config)` returns empty list using SF-1's 21 codes [D-SF4-24]
- 8 phases in correct order
- **[C-4]** AskNodes producing artifacts have `artifact_key` — no downstream `store` PluginNodes
- **[C-4]** `store` PluginNodes remain ONLY for PluginNode outputs (preview_url, baseline) — those that are NOT from AskNodes
- **[C-4]** Hosting hooks from AskNode `on_end` [D-SF4-12 revised]
- Node count ~27 (reduced from ~35) [C-4]
- `artifact_db` IS still declared (needed for PluginNode output writes) [C-4]
- All infrastructure PluginNodes use general type instances [D-SF4-6]
- Parallel RCA uses map sub-phase with 2 analysts [REQ-19]
- DiagnosisAndFixPhase has `max_iterations: 3`
- Fire-and-forget nodes have `outputs: []`

**Counterexamples:**
- Do NOT add `store` PluginNodes after AskNodes that have `artifact_key` [C-4]
- Do NOT remove `store` PluginNodes for PluginNode outputs (preview_url, baseline) — those need explicit writes because the producing node is not an AskNode [C-4]
- Do NOT give both RCA analysts identical prompts
- Do NOT omit `max_exceeded` port on diagnosis loop
- Do NOT compress `failed_attempts` in handover_compress [REQ-24]
- Do NOT add trigger/listener nodes [D-SF4-17]

**Requirement IDs:** REQ-16, REQ-17, REQ-18, REQ-19, REQ-20, REQ-21, REQ-31 | **Journey IDs:** J-3

---

### STEP-33: Task Template YAML Files

**Objective:** Create three actor-centric task template YAML files. **[C-4 REVISION]** Templates use `artifact_key` on revision AskNodes for auto-write instead of explicit `store` PluginNodes. Hosting hooks from AskNode `on_end`. Explicit `store` PluginNodes removed from `gate_and_revise` and `broad_interview`. `interview_gate_review` retains `recompile` AskNode with `artifact_key` + id_renumberer edge transform.

**Scope:**
| Path | Action |
|------|--------|
| `tests/fixtures/workflows/templates/gate_and_revise.yaml` | create |
| `tests/fixtures/workflows/templates/broad_interview.yaml` | create |
| `tests/fixtures/workflows/templates/interview_gate_review.yaml` | create |

**Instructions:**

1. **`gate_and_revise.yaml`** — Approval loop pattern **[C-4 simplified]**:
   ```yaml
   name: gate_and_revise
   description: "Actor-centric template: approval loop with revision on rejection"
   parameters:
     artifact_key: { type: string, description: "Store key for the artifact under review" }
     producer_actor: { type: string, description: "Actor who revises on rejection" }
     approver_actor: { type: string, description: "Actor who approves/rejects" }
     output_type: { type: string, description: "Type ref for the artifact" }
     label: { type: string, description: "Display label for the gate" }
   phase:
     mode: loop
     loop_config:
       exit_condition: "data is True or data.approved is True"
     nodes:
       - id: present_artifact
         type: ask
         actor: "{{ approver_actor }}"
         prompt: "Review the {{ label }}. Approve or reject with feedback."
         context_keys: ["{{ artifact_key }}"]  # READ via context_keys
         outputs:
           - name: approved
             condition: "data is True"
           - name: rejected
             condition: "data is not True"
       - id: revise
         type: ask
         actor: "{{ producer_actor }}"
         prompt: "Revise based on feedback: {{ $input }}"
         output_type: "{{ output_type }}"
         artifact_key: "{{ artifact_key }}"  # [C-4] Auto-writes revision to store
       - id: host_revision
         type: plugin
         plugin_ref: doc_host
         config: { operation: push, artifact_key: "{{ artifact_key }}" }
         outputs: []  # fire-and-forget
     edges:
       - source: "$input"
         target: "present_artifact.input"
       - source: "present_artifact.approved"
         target: "$output"
       - source: "present_artifact.rejected"
         target: "revise.input"
         transform_fn: |
           def transform(data, ctx):
               verdict = data
               parts = []
               if verdict.get('feedback'):
                   parts.append(f"Reviewer feedback: {verdict['feedback']}")
               if verdict.get('annotations'):
                   for key, note in verdict['annotations'].items():
                       parts.append(f"  [{key}]: {note}")
               if ctx.get('hosted_url'):
                   parts.append(f"Hosted artifact: {ctx['hosted_url']}")
               return '\n'.join(parts) if parts else 'No specific feedback provided.'
       - source: "revise.on_end"
         target: "host_revision.input"  # [C-4] Hook from AskNode, not store PluginNode
       - source: "revise.output"
         target: "$output"
   ```

   **[C-4] Changes:** Removed `write_revision` store PluginNode. Added `artifact_key` on `revise` AskNode. Hosting hooks from `revise.on_end`.

2. **`broad_interview.yaml`** — Single-actor interview-to-completion **[C-4 simplified]**:
   ```yaml
   name: broad_interview
   description: "Actor-centric template: interview loop producing a single artifact"
   parameters:
     lead_actor: { type: string }
     output_type: { type: string }
     artifact_key: { type: string }
     initial_prompt: { type: string }
   phase:
     mode: loop
     loop_config:
       exit_condition: "data.complete"
     nodes:
       - id: interview_ask
         type: ask
         actor: "{{ lead_actor }}"
         prompt: "{{ initial_prompt }}\n\nPrevious context: {{ $input }}"
         output_type: "Envelope"
         artifact_key: "{{ artifact_key }}"  # [C-4] Auto-writes
       - id: host_artifact
         type: plugin
         plugin_ref: doc_host
         config: { operation: push, artifact_key: "{{ artifact_key }}" }
         outputs: []  # fire-and-forget
     edges:
       - source: "$input"
         target: "interview_ask.input"
       - source: "interview_ask.on_end"
         target: "host_artifact.input"  # [C-4] Hook from AskNode
       - source: "interview_ask.output"
         target: "$output"
   ```

   **[C-4] Changes:** Removed `write_artifact` store PluginNode. Added `artifact_key` on `interview_ask`. Hosting hooks from `interview_ask.on_end`.

3. **`interview_gate_review.yaml`** — Compiled artifact review **[C-4 simplified]**:
   ```yaml
   name: interview_gate_review
   description: "Actor-centric template: compiled artifact review with targeted revision"
   parameters:
     lead_actor: { type: string }
     compiler_actor: { type: string }
     decomposition_key: { type: string }
     artifact_prefix: { type: string }
     compiled_key: { type: string }
     base_role: { type: string }
     output_type: { type: string }
     broad_key: { type: string }
   phase:
     mode: loop
     loop_config:
       exit_condition: "data.approved is True"
       fresh_sessions: true  # [D-SF4-13]
     nodes:
       - id: review_ask
         type: ask
         actor: "{{ lead_actor }}"
         prompt: "Review the compiled {{ output_type }}. Approve or provide revision instructions."
         context_keys: ["{{ compiled_key }}"]
         outputs:
           - name: approved
             condition: "data.approved is True"
           - name: needs_revision
             condition: "data.approved is not True"
       - id: extract_revisions
         type: ask
         actor: extractor
         prompt: |
           Extract a structured revision plan from this review feedback...
         output_type: "RevisionPlan"
       - id: revision_fold
         type: phase
         mode: map
         map_config:
           collection: "ctx['revision_plan'].requests"
       - id: recompile
         type: ask
         actor: "{{ compiler_actor }}"
         prompt: "Recompile the {{ output_type }} incorporating all revisions."
         output_type: "{{ output_type }}"
         artifact_key: "{{ compiled_key }}"  # [C-4] Auto-writes compiled artifact
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
         transform_fn: |
           def transform(data, ctx):
               import re
               text = data
               prefixes = ['REQ', 'AC', 'J', 'STEP', 'CMP']
               for prefix in prefixes:
                   pattern = re.compile(rf'{prefix}-(\d+)')
                   matches = sorted(set(pattern.findall(text)), key=int)
                   for new_num, old_num in enumerate(matches, 1):
                       text = text.replace(f'{prefix}-{old_num}', f'{prefix}-{new_num}')
               return text
       - source: "recompile.on_end"
         target: "host_compiled.input"  # [C-4] Hook from AskNode
       - source: "recompile.output"
         target: "$output"
   ```

   **[C-4] Changes:** Removed `write_compiled` store PluginNode. Added `artifact_key` on `recompile`. Hosting hooks from `recompile.on_end`. `id_renumberer` transform is now on the edge INTO `recompile` (applied before the AskNode receives input, so auto-write persists the renumbered output).

**Acceptance Criteria:**
- All 3 template files parse as valid YAML
- **[C-4]** No `store` PluginNodes in any template — all artifact writes via `artifact_key` on AskNodes
- **[C-4]** Hosting hooks from AskNode `on_end`, not from store PluginNode [D-SF4-12 revised]
- `gate_and_revise`: `feedback_formatter` as edge transform_fn [D-SF4-21]
- `broad_interview`: loop-mode with `exit_condition: "data.complete"` [REQ-37]
- `interview_gate_review`: `fresh_sessions: true` [D-SF4-13]
- `interview_gate_review`: `extract_revision_plan` as AskNode [D-SF4-19]
- `interview_gate_review`: `id_renumberer` as edge transform_fn [D-SF4-21]
- All templates use `context_keys` for reads, `artifact_key` for writes [C-4]

**Counterexamples:**
- Do NOT add `store` PluginNodes for artifact writes in templates — use `artifact_key` [C-4]
- Do NOT hook hosting from store PluginNodes — hook from AskNode `on_end` [C-4]
- Do NOT omit `fresh_sessions: true` on interview_gate_review [D-SF4-13]
- Do NOT put side effects in edge transforms [D-SF4-21]

**Requirement IDs:** REQ-35, REQ-36, REQ-37 | **Journey IDs:** J-1, J-2, J-3

---

### STEP-34: Behavioral Equivalence Test Suite (~50-55 Tests)

**Objective:** Write comprehensive behavioral equivalence tests. **[REVISED]** Tests updated for: (1) artifact_key auto-write pattern instead of explicit store PluginNodes [C-4], (2) three-tier PluginRegistry API [H-4], (3) SF-1's 21 confirmed validation error codes [H-3].

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

**Instructions:**

1. **`tests/migration/conftest.py`** — Shared fixtures **[H-4 revised]**:

   ```python
   import pytest
   from pathlib import Path
   from iriai_compose.testing import MockRuntime, MockInteraction, run_test
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
       """Set up PluginRegistry using three-tier API [H-4]."""
       registry = PluginRegistry(auto_register_builtins=False)
       register_plugin_types(registry)   # Uses registry.register_type() [H-4]
       register_instances(registry)      # Uses registry.register_instance() [H-4]
       return registry
   ```

2. **`tests/migration/test_plugin_instances.py`** (~10 tests) **[H-4 revised]**:

   **Three-tier registry validation (4 tests) [H-4 NEW]:**
   - `test_register_type_uses_correct_api` — `register_plugin_types()` calls `registry.register_type()`, not `registry.register()`. Verify: `registry.has_type("store")` is True, `registry.has("store")` is False (no concrete Plugin ABC)
   - `test_register_instance_uses_correct_api` — `register_instances()` calls `registry.register_instance()`. Verify: `registry.has_instance("artifact_db")` is True
   - `test_type_has_category_field` — each registered type has a `category` field. `registry.get_type("store").category == "store"`, `registry.get_type("subprocess").category == "cli"`, `registry.get_type("mcp").category == "mcp"`
   - `test_instance_references_valid_type` — every instance's `plugin_type` matches a registered type. `registry.get_instance("artifact_db").plugin_type == "store"` and `registry.has_type("store")` is True

   **Instance config resolution (3 tests):**
   - `test_workflow_declared_plugins_resolve` — every `plugin_ref` in all 3 workflows resolves to either a registered instance (`registry.has_instance()`) or a workflow-level plugin declaration
   - `test_plugin_config_matches_type_schema` — each PluginNode's config validates against its type's config_schema
   - `test_store_has_no_read_operations` — `registry.get_type("store").operations == ["put", "delete"]`

   **[C-4] Auto-write pattern validation (3 tests):**
   - `test_ask_nodes_with_artifact_key_have_no_downstream_store_plugin` — scan all 3 workflows: no AskNode with `artifact_key` is followed by a `store` PluginNode writing to the same key
   - `test_store_plugins_only_for_non_ask_outputs` — every remaining `store` PluginNode's input edge originates from a PluginNode (not an AskNode)
   - `test_hosting_hooks_from_ask_not_store` — all hosting PluginNodes receive their hook edge from an AskNode's `on_end`, not from a store PluginNode

3. **`tests/migration/test_edge_transforms.py`** (~10 tests) — Unchanged from v1. All 8 transform correctness tests plus 2 purity tests.

4. **`tests/migration/test_planning.py`** (~15 tests) **[C-4, H-3 revised]**:

   **Schema validation (5 tests) [H-3 codes used]:**
   - `test_planning_loads_without_error` — `load_workflow()` succeeds
   - `test_planning_validates_cleanly` — `validate_workflow()` returns empty list
   - `test_planning_has_six_phases` — exactly 6 phases in correct order
   - `test_planning_actor_refs_resolve` — no `invalid_actor_ref` errors [H-3]
   - `test_planning_plugin_refs_resolve` — no `invalid_plugin_ref` errors [H-3]

   **Phase execution order (2 tests):** Unchanged.

   **Branch paths (2 tests):** Unchanged.

   **Artifact production (2 tests) [C-4 revised]:**
   - `test_all_planning_artifacts_produced` — scope, prd, design, plan, system_design all present in `result.artifacts`
   - `test_artifact_writes_via_artifact_key` — **[C-4 REPLACES test_store_writes_are_explicit]** every expected artifact has a corresponding AskNode with `artifact_key` set (not an explicit `store` PluginNode). Verify by scanning workflow nodes: AskNodes have `artifact_key` matching store keys.

   **Fold/accumulator (2 tests):** Unchanged.

   **Fresh sessions (2 tests):** Unchanged.

5. **`tests/migration/test_develop.py`** (~15 tests) **[C-4 revised]**:

   **Schema validation (5 tests) [H-3 codes used]:**
   - `test_develop_loads_without_error`
   - `test_develop_validates_cleanly`
   - `test_develop_has_seven_phases`
   - `test_develop_planning_phases_match` — structural consistency including [C-4] `artifact_key` patterns
   - `test_develop_actor_refs_resolve` — no `invalid_actor_ref` errors [H-3]

   **Implementation phase structure (4 tests):** Unchanged logic, but [C-4] reduces node count.

   **DAG execution (4 tests):** Unchanged (edge transforms still tested).

   **Consistency (2 tests) [C-4 revised]:**
   - `test_planning_phase_modes_match` — same modes
   - `test_planning_artifact_key_patterns_match` — **[C-4 REPLACES test_planning_template_bindings_match]** same `artifact_key` usage patterns in planning phases of both files

6. **`tests/migration/test_bugfix.py`** (~12 tests) **[C-4 revised]**:

   **Schema validation (4 tests) [H-3 codes used]:**
   - `test_bugfix_loads_without_error`
   - `test_bugfix_validates_cleanly`
   - `test_bugfix_has_eight_phases`
   - `test_bugfix_phases_in_order` — no `phase_boundary_violation` errors [H-3]

   **Diagnosis loop (4 tests):** Unchanged.

   **Plugin instance integration (3 tests):** Unchanged (tests PluginNode-based plugins: git, preview, playwright).

   **[C-4] Hybrid store pattern (1 test):**
   - `test_bugfix_store_pattern_correct` — **[C-4 REPLACES test_all_bugfix_store_writes_explicit]** AskNode artifacts use `artifact_key` (no store PluginNode). PluginNode outputs (preview_url, baseline) use explicit `store` PluginNode (artifact_db). Both patterns coexist correctly.

7. **`tests/migration/test_yaml_roundtrip.py`** (~5 tests) — Unchanged.

8. **[H-3] Validation error code alignment** — All `assert_validation_error` calls use SF-1's canonical 21 codes:
   - `invalid_actor_ref` (NOT `invalid_actor`)
   - `invalid_plugin_ref` (NOT `missing_plugin`)
   - `invalid_phase_mode_config` (NOT `invalid_mode`)
   - `phase_boundary_violation` (NOT `boundary_error`)
   - `invalid_hook_edge_transform` (NOT `hook_transform_error`)
   - `invalid_store_ref` (for store validation)
   - `store_type_mismatch` (for store key type checking)
   - `invalid_type_ref` (for type reference validation)
   - `invalid_switch_function_config` (for branch config)
   - `invalid_workflow_io_ref` (for workflow I/O validation)

**Acceptance Criteria:**
- `pytest tests/migration/` passes — all ~50 tests green
- **[H-4]** Registry tests verify three-tier API: `register_type()`, `register_instance()`, `category` field
- **[C-4]** Auto-write pattern tests verify: no store PluginNodes after AskNodes with `artifact_key`, hosting hooks from AskNode `on_end`
- **[H-3]** All validation error assertions use SF-1's canonical 21 codes
- Edge transform tests verify: tiered context, handover compress, id_renumberer, transform purity [D-SF4-21]
- Every workflow has schema validation tests (Tier 1) [REQ-39]
- Every workflow has mock execution tests (Tier 2) [REQ-40]
- YAML round-trip tests pass [REQ-39]
- MockRuntime responses keyed by `(node_id, role_name)`
- No live API calls

**Counterexamples:**
- Do NOT use `registry.register()` in test fixtures — use `register_type()` + `register_instance()` [H-4]
- Do NOT assert store write explicitness for AskNode outputs — they use `artifact_key` auto-write [C-4]
- Do NOT use non-canonical validation error codes (e.g., `invalid_actor` instead of `invalid_actor_ref`) [H-3]
- Do NOT use live API calls
- Do NOT test resume/checkpoint — SF-2's responsibility

**Requirement IDs:** REQ-39, REQ-40, REQ-42, REQ-43, REQ-45 | **Journey IDs:** J-1, J-2, J-3, J-4

---

### STEP-35: Pre-Seed Data Package

**Objective:** Create a JSON seed file containing all migrated content for SF-5's database. **[C-4 REVISION]** Updated node counts reflecting artifact_key auto-write simplification.

**Scope:**
| Path | Action |
|------|--------|
| `tests/fixtures/seed/migration_seed.json` | create |
| `tests/fixtures/seed/seed_loader.py` | create |
| `tests/fixtures/workflows/migration/planning.yaml` | read |
| `tests/fixtures/workflows/migration/develop.yaml` | read |
| `tests/fixtures/workflows/migration/bugfix.yaml` | read |
| `tests/fixtures/workflows/templates/gate_and_revise.yaml` | read |
| `tests/fixtures/workflows/templates/broad_interview.yaml` | read |
| `tests/fixtures/workflows/templates/interview_gate_review.yaml` | read |

**Instructions:**

1. **`migration_seed.json`** structure **[C-4 node counts updated]**:
   ```json
   {
     "version": "3.0",
     "generated_from": "SF-4 workflow migration (v2 — artifact_key auto-write + three-tier registry)",
     "workflows": [
       {
         "name": "Planning",
         "slug": "planning",
         "description": "Planning workflow — scoping through task planning",
         "phase_count": 6,
         "node_count": 35,
         "yaml_path": "migration/planning.yaml",
         "is_example": true
       },
       {
         "name": "Full Develop",
         "slug": "develop",
         "description": "Development workflow — planning + implementation",
         "phase_count": 7,
         "node_count": 42,
         "yaml_path": "migration/develop.yaml",
         "is_example": true
       },
       {
         "name": "Bugfix",
         "slug": "bugfix",
         "description": "Bugfix workflow — intake through cleanup",
         "phase_count": 8,
         "node_count": 27,
         "yaml_path": "migration/bugfix.yaml",
         "is_example": true
       }
     ],
     "roles": [
       {"name": "pm", "model": "claude-opus-4-6", "category": "planning", "is_example": true},
       {"name": "designer", "model": "claude-opus-4-6", "category": "planning", "is_example": true},
       {"name": "architect", "model": "claude-opus-4-6", "category": "planning", "is_example": true},
       {"name": "task_planner", "model": "claude-opus-4-6", "category": "planning", "is_example": true},
       {"name": "implementer", "model": "claude-sonnet-4-6", "category": "implementation", "is_example": true},
       {"name": "qa", "model": "claude-sonnet-4-6", "category": "verification", "is_example": true},
       {"name": "reviewer", "model": "claude-sonnet-4-6", "category": "review", "is_example": true},
       {"name": "compiler", "model": "claude-sonnet-4-6", "category": "compilation", "is_example": true},
       {"name": "summarizer", "model": "claude-haiku", "category": "utility", "is_example": true},
       {"name": "extractor", "model": "claude-haiku", "category": "utility", "is_example": true}
     ],
     "schemas": [
       {"name": "Envelope", "category": "common", "is_example": true},
       {"name": "StoreWriteResult", "category": "common", "is_example": true},
       {"name": "ScopeOutput", "category": "planning", "is_example": true},
       {"name": "PRD", "category": "planning", "is_example": true},
       {"name": "DesignDecisions", "category": "planning", "is_example": true},
       {"name": "TechnicalPlan", "category": "planning", "is_example": true},
       {"name": "SystemDesign", "category": "planning", "is_example": true},
       {"name": "SubfeatureDecomposition", "category": "planning", "is_example": true},
       {"name": "ImplementationDAG", "category": "development", "is_example": true},
       {"name": "BugReport", "category": "bugfix", "is_example": true},
       {"name": "HandoverDoc", "category": "common", "is_example": true}
     ],
     "templates": [
       {"name": "gate_and_revise", "yaml_path": "templates/gate_and_revise.yaml", "is_example": true},
       {"name": "broad_interview", "yaml_path": "templates/broad_interview.yaml", "is_example": true},
       {"name": "interview_gate_review", "yaml_path": "templates/interview_gate_review.yaml", "is_example": true}
     ],
     "plugin_types": [
       {"name": "store", "category": "store", "description": "KV persistence (explicit writes only — simple writes via artifact_key)", "operations": ["put", "delete"], "is_example": true},
       {"name": "hosting", "category": "service", "description": "Content hosting + URLs + annotations", "operations": ["push", "update", "collect_annotations", "clear_feedback"], "is_example": true},
       {"name": "mcp", "category": "mcp", "description": "MCP tool invocation", "operations": ["call_tool"], "is_example": true},
       {"name": "subprocess", "category": "cli", "description": "CLI command execution", "operations": ["execute"], "is_example": true},
       {"name": "http", "category": "service", "description": "Generic HTTP API calls", "operations": ["request"], "is_example": true}
     ],
     "plugin_instances": [
       {"instance_id": "artifact_db", "plugin_type": "store", "description": "Primary artifact persistence (only for PluginNode outputs)", "is_example": true},
       {"instance_id": "artifact_mirror", "plugin_type": "store", "description": "Filesystem mirror for local dev", "is_example": true},
       {"instance_id": "doc_host", "plugin_type": "hosting", "description": "Document hosting via iriai-feedback", "is_example": true},
       {"instance_id": "preview", "plugin_type": "mcp", "description": "Preview deployment MCP server", "is_example": true},
       {"instance_id": "playwright", "plugin_type": "mcp", "description": "E2E testing via Playwright MCP", "is_example": true},
       {"instance_id": "git", "plugin_type": "subprocess", "description": "Git CLI operations", "is_example": true},
       {"instance_id": "feedback_notify", "plugin_type": "http", "description": "Browser refresh notification", "is_example": true}
     ],
     "edge_transforms": [
       {"name": "tiered_context_builder", "category": "context", "description": "Assembles tiered context for fold iterations", "is_example": true},
       {"name": "handover_compress", "category": "lifecycle", "description": "Compresses handover doc between iterations", "is_example": true},
       {"name": "feedback_formatter", "category": "formatting", "description": "Formats rejection verdict as feedback", "is_example": true},
       {"name": "id_renumberer", "category": "formatting", "description": "Re-numbers REQ/AC/J/STEP/CMP IDs sequentially", "is_example": true},
       {"name": "collect_files", "category": "aggregation", "description": "Flattens file lists from ImplementationResults", "is_example": true},
       {"name": "normalize_review_slugs", "category": "normalization", "description": "Normalizes SF-N keys to decomposition slugs", "is_example": true},
       {"name": "build_task_prompt", "category": "prompting", "description": "Constructs structured implementation prompts", "is_example": true},
       {"name": "build_env_overrides", "category": "environment", "description": "Reads environment variables for deploy", "is_example": true}
     ]
   }
   ```

2. **`seed_loader.py`** — Idempotent loader (unchanged logic from v1).

**Acceptance Criteria:**
- `migration_seed.json` is valid JSON, parseable without error
- Contains: 3 workflows, 10 roles, 11 schemas, 3 templates, 5 plugin types, 7 plugin instances, 8 edge transforms
- **[C-4]** Node counts: planning=35, develop=42, bugfix=27 (reduced from 50/60/35)
- **[H-4]** `plugin_types` entries include `category` field for CategoryExecutor dispatch
- **[C-4]** `artifact_db` instance description notes "only for PluginNode outputs"
- **[C-4]** `store` plugin type description notes "explicit writes only — simple writes via artifact_key"
- All entries have `is_example: true` flag [D-SF4-9]
- `seed_loader.py` is idempotent

**Counterexamples:**
- Do NOT include old node counts (50/60/35) — use [C-4] reduced counts [D-SF4-22]
- Do NOT omit `category` field on plugin_types [H-4]
- Do NOT hardcode database connection strings
- Do NOT make seed destructive — upsert only

**Requirement IDs:** REQ-32, REQ-47 | **Journey IDs:** J-1, J-2, J-3

---

## Interfaces to Other Subfeatures

### SF-1 → SF-4 (Python Import)

SF-4 imports from `iriai_compose.schema`:
- **Models:** `WorkflowConfig`, `AskNode`, `BranchNode`, `PluginNode`, `PhaseDefinition`, `Edge`, `PortDefinition`, `ActorDefinition`, `RoleDefinition`, `TypeDefinition`, `SequentialConfig`, `MapConfig`, `FoldConfig`, `LoopConfig`, `StoreDefinition`, `PluginInterface`, `PluginInstanceConfig`, `TemplateRef`
- **Validation:** `validate_workflow()`, `validate_type_flow()`, `detect_cycles()` returning `list[ValidationError]` with 21 canonical error codes [D-SF4-24]
- **I/O:** `load_workflow()`, `dump_workflow()`

**New SF-1 requirements from this revision:**
- Edge `transform_fn` field for inline Python transforms [D-SF4-21]
- PluginInstanceConfig model for general type instances [D-SF4-6]
- WorkflowConfig `input_type` field [D-SF4-17]
- `artifact_key` on NodeBase with dual read+write semantics [D-SF4-22, C-4]

### SF-2 → SF-4 (Python Import)

SF-4 imports from `iriai_compose.declarative`:
- **Execution:** `run()` executes migrated YAML workflows
- **Config:** `RuntimeConfig` for test execution setup (includes `plugin_registry: PluginRegistry | None`)
- **Plugins [H-4]:** `PluginRegistry` with three-tier API — SF-4 uses `register_type()` and `register_instance()` only
- **Loader:** `load_workflow()` for YAML parsing

**New SF-2 requirements from this revision:**
- Runner auto-writes node output to `artifact_key` after execution [D-SF4-22, C-4]
- PluginRegistry three-tier API: `register_type()`, `register_instance()`, `register_category_executor()` [D-SF4-23, H-4]
- Runner evaluates `transform_fn` Python expressions during edge traversal [D-SF4-21]
- Runner dispatches PluginNode execution based on `plugin_type` + instance config via CategoryExecutor [D-SF4-23, H-4]
- Runner accepts initial input and passes to first phase `$input` port [D-SF4-17]

### SF-3 → SF-4 (Python Import)

Unchanged from v1.

### SF-4 → SF-5 (Seed Data)

SF-4 produces `migration_seed.json` with updated node counts (35/42/27) and plugin_type `category` fields.

### SF-4 → SF-1 (Schema Gap Feedback)

**Known extensions required by this revision:**
- Edge `transform_fn` for inline Python [D-SF4-21]
- PluginInstanceConfig for general type instances [D-SF4-6]
- WorkflowConfig `input_type` [D-SF4-17]
- `artifact_key` dual read+write semantics on NodeBase [D-SF4-22, C-4]

---

## Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-71 | Schema gaps — SF-1 may not yet have Edge.transform_fn, PluginInstanceConfig, or artifact_key auto-write models | medium | Document as required SF-1 extensions. If SF-1 lags, SF-4 defines provisional models in `_compat.py`. | STEP-28, STEP-30, STEP-31, STEP-32 |
| RISK-72 | Edge transform complexity — `tiered_context_builder` (~20 lines of Python in a YAML string) is harder to read/debug than a named function | medium | Catalog all transforms in `iriai_compose/plugins/transforms.py` as named constants. Unit-tested in test_edge_transforms.py. | STEP-28, STEP-30 |
| RISK-73 | Runner transform execution safety — inline Python in `transform_fn` requires sandboxed eval | medium | Runner uses restricted exec(). SF-4 tests validate transforms run within restrictions. SF-2 responsibility. | STEP-30, STEP-31, STEP-32 |
| RISK-74 | Category C AskNode proliferation — 3 new actors (summarizer, extractor, sd_converter_agent) | low | Cheap models (haiku/sonnet). Actor definitions shared across phases. | STEP-30, STEP-31 |
| RISK-75 | Develop-planning structural drift | medium | Consistency tests (REQ-45) in CI. `test_develop_planning_phases_match` catches differences. | STEP-31, STEP-34 |
| RISK-76 | SF-2 runner not supporting all features at SF-4 build time (artifact_key auto-write, three-tier registry, edge transforms) | medium | Structural tests (Tier 1) need only SF-1. Execution tests (Tier 2) need SF-2. Build order preserves independence. | STEP-34 |
| RISK-77 | **[C-4 REVISED]** Missing artifact_key assignments — some AskNode outputs may not have `artifact_key` set when they should | medium | Systematic audit: grep all `artifacts.put()` in iriai-build-v2, cross-reference with AskNode `artifact_key` assignments in YAML. `test_artifact_writes_via_artifact_key` verifies coverage. | STEP-30, STEP-31, STEP-32 |
| RISK-78 | YAML file size — reduced by ~30% from [C-4] but still substantial for develop.yaml | low | Templates absorb ~15 nodes each. Named transform constants reduce inline YAML. [C-4] already reduces from ~60 to ~42 nodes. | STEP-31 |
| RISK-79 | **[H-4 NEW]** PluginRegistry API mismatch — SF-2 may ship with different method signatures than documented | medium | SF-4's `register_plugin_types()` and `register_instances()` are thin wrappers — easy to adapt. Test fixtures verify the API contract. If API differs, only `iriai_compose/plugins/__init__.py` needs updating. | STEP-28, STEP-34 |
| RISK-80 | **[H-3 NEW]** Validation error code drift — SF-1 may rename or add codes before shipping | low | All error code references are in test assertions, centralized in test files. A single grep+replace updates all references. The 21-code list is marked "authoritative" in SF-1's plan. | STEP-34 |

---

## File Manifest

| Path | Action |
|------|--------|
| `iriai_compose/plugins/__init__.py` | modify |
| `iriai_compose/plugins/types.py` | create |
| `iriai_compose/plugins/instances.py` | create |
| `iriai_compose/plugins/transforms.py` | create |
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
| `tests/fixtures/seed/migration_seed.json` | create |
| `tests/fixtures/seed/seed_loader.py` | create |

---


---