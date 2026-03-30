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

### STEP-9: Dependencies and Subpackage Skeleton

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

### STEP-10: YAML Loader (Thin Wrapper over SF-1)

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
- [code: SF-1 plan STEP-15 — `iriai_compose/schema/yaml_io.py` defines `load_workflow`, `load_workflow_lenient`, `dump_workflow`]
- [decision: H-1 — "Refactor: import SF-1's function, then add any runtime-specific validation"]

---

### STEP-11: RuntimeConfig, Plugin Registry (Three-Tier), and Built-in Plugins

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

### STEP-12: DAG Builder (Unified `ExecutionGraph`)

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

### STEP-13: Transform and Expression Execution

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

### STEP-14: Node Executors

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

### STEP-15: Phase Mode Executors + Unified `_execute_dag`

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
2. The auto-write for nodes is handled inside the executor (STEP-14). The `_execute_dag` level handles auto-write for phases (since phases don't go through node executors).

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

### STEP-15.5: Actor Hydration

**Objective:** Bridge `ActorDefinition` to runtime. Handles `context_store`, `handover_key`, `persistent` per D-SF1-25.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/actors.py` | modify |
| `iriai_compose/actors.py` | read |

**Instructions:**
- Hydrate `ActorDefinition` → `AgentActor` or `InteractionActor` (matching iriai-compose's existing actor model in [code: iriai_compose/actors.py]).
- `AgentActor.context_keys` populated from `ActorDefinition.context_keys`. These are MERGED with node-level `artifact_key` and `context_keys` at execution time (per STEP-14).
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

### STEP-16: Hook Execution

**Objective:** Fire-and-forget hook edges. Hook identification: source port in `hooks` container per D-SF1-21. Enforce `transform_fn=None`.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/hooks.py` | modify |
| `iriai_compose/declarative/errors.py` | modify |

**Instructions:**
- Hook edges identified at graph-build time (STEP-12) by checking if source port is in the `hooks` container.
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

### STEP-17: Top-level `run()` Function

**Objective:** Input validation → `build_execution_graph` → `_execute_dag` → artifact tracking → output validation → `ExecutionResult`.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/declarative/runner.py` | modify |
| `iriai_compose/declarative/__init__.py` | modify |

**Instructions:**
1. Accept `WorkflowConfig | str | Path` — load if string/path (delegates to SF-1 via STEP-10 loader).
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

### STEP-18: Update Public Exports

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

### STEP-19: Integration Tests

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
| RISK-18 | SF-1 schema not finalized | high | Start with STEP-9-3 | 2,6,7 |
| RISK-19 | Full exec() trust | medium | Author = operator | 5 |
| RISK-20 | Nested recursion | low | Max ~3 levels | 7 |
| RISK-21 | Plugin discovery | low | try/except | 3 |
| RISK-22 | Branch merge malformed | medium | Port-keyed dict guaranteed | 5,6 |
| RISK-23 | eval_predicate trust | medium | Same as transforms | 5,6 |
| RISK-24 | Map actor collision | high | `_make_parallel_actors()` | 7 |
| RISK-25 | fresh_sessions custom stores | low | Logs warning | 7 |
| RISK-26 | Hook failures invisible | medium | `hook_warnings` on result | 8,9 |
| RISK-27 | Missing InteractionRuntime | medium | Pre-flight validation | 7.5,9 |
| RISK-28 | Plugin version conflicts | low | Future: constraints | 3 |
| RISK-29 | iriai-build-v2 drift | low | Pattern verification + SF-4 | All |
| RISK-30 | Undeclared stores | low | Auto-create InMemory | 9 |
| RISK-31 | Missing store dot-notation | medium | Raise | 6 |
| RISK-32 | Branch-skipped elements | medium | `fired_edges` | 7,9 |
| RISK-33 | Loop exit routing | medium | `edge_matches_exit_path` | 7,9 |
| RISK-34 | Workflow cycles | low | Kahn's raises | 4 |
| RISK-35 | Missing port data | medium | Returns None | 7 |
| RISK-36 | `$input` targets non-entry | low | `$input` wins | 7 |
| RISK-37 | Element ID collision | medium | Raise on duplicate | 4 |
| RISK-38 | Loop exit tuple leaks | medium | Unwrap before storing | 7 |
| RISK-39 | Branch barrier deadlock | medium | 2× safety cap → DeadlockError | 7 |
| RISK-40 | Non-exclusive sequential fan-out | medium | Topo order, no parallelism | 6,7 |
| RISK-41 | Input type needs jsonschema | low | Pydantic dep or skip | 9 |
| RISK-42 | `$input` undeclared port | low | Validation | 4,9 |
| RISK-43 | SF-1 schema additions | medium | Additive only | 2 |
| RISK-44 | Conditionally-skipped branch source | medium | DeadlockError + validation warning | 7 |
| RISK-45 | Source port on non-dict | low | Returns as-is | 4,7 |
| RISK-46 | Output validation blocks results | low | Warns only | 9 |
| RISK-47 | `is_workflow` flag smell | low | Two thin wrappers alt. | 4 |
| RISK-48 | Source port vs exit path dual semantics | medium | Documented | 4,7 |
| RISK-49 | Resume blocks SF-4 litmus | medium | Correctness not efficiency | 9 |
| RISK-50 | `collection` wrong context | medium | Must pass `ctx` | 7 |
| RISK-51 | `accumulator_init` variables | low | Empty namespace | 7 |
| RISK-52 | PluginNode `outputs: []` confuses routing | low | No outgoing edges, no-op in `_activate_outgoing_edges` | 6,7 |
| RISK-53 | `instance_ref` ignored in plugin executor | medium | Three-tier resolution: concrete → type → instance → category | 6 |
| RISK-54 | `max_parallelism` ignored in Map | medium | Must use Semaphore when set | 7 |
| RISK-55 | `artifact_key` auto-write may cause unexpected store writes | low | Same risk as explicit `artifacts.put()` in iriai-build-v2. Store key `type_ref` validation catches type mismatches. [D-SF1-29] | 6,7,9 |
| RISK-56 | Auto-write overwrites existing artifact value | medium | Intentional — same as imperative `artifacts.put()` with `ON CONFLICT UPDATE`. If overwrites are problematic, use `artifact_write` with a versioned key instead. | 6,11 |
| RISK-57 | Dynamic artifact keys not supported in SF-2 | low | Static keys cover most patterns. Dynamic keys (per-iteration in folds) require custom plugin. Document as future enhancement. | 3,6 |
| RISK-58 | `artifact_key` resolution latency | low | `ContextProvider.resolve()` is async and may hit external storage. Single resolution per element (not per-retry). Cache within execution if needed. | 6,7 |
| RISK-59 | Tracking artifact writes for ExecutionResult | low | TrackingArtifactStore wrapper records `put()` keys from BOTH auto-writes and explicit plugin writes. | 9 |
| RISK-60 | `switch_function` returning unknown port name | medium | Runtime error with clear diagnostic: "switch_function returned 'X' but available ports are [...]". SF-1 validation ensures at least 1 output port. [D-SF1-28, RISK-32 in SF-1] | 5,6 |
| RISK-61 | `switch_function` and per-port conditions both present | low | SF-1 validator produces `invalid_switch_function_config` error. Runner asserts defensively. | 6 |
| RISK-62 | Category executor not registered for plugin's category | medium | `PluginNotFoundError` with "No category executor registered for 'mcp'" message. Clear action: register executor. | 3,6 |
| RISK-63 | Loader duplication if SF-1 changes `load_workflow` signature | low | SF-2's loader is a thin wrapper — changes propagate automatically. Runtime validation is additive. | 2 |

---

## New Dependencies

| Package | Version | Purpose | Added to |
|---------|---------|---------|----------|
| pyyaml | >=6.0,<7.0 | YAML parsing | `pyproject.toml` dependencies |

---


---