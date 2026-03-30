### SF-3: Testing Framework

<!-- SF: testing-framework -->



## Architecture

### Decision Log

| ID | Decision | Rationale | Citation |
|----|----------|-----------|----------|
| D-SF3-1 | Assertion API — standalone functions | Matches existing iriai-compose test style with plain `assert` + helpers. Composes with pytest introspection. | [code: iriai-compose/tests/conftest.py] |
| D-SF3-2 | MockRuntime — constructor + response map keyed by `(node_id, role_name)` | Minimal extension for multi-node workflows without breaking existing API. | [code: iriai-compose/tests/conftest.py:21-54] |
| D-SF3-3 | Snapshot testing — fixtures directory `tests/fixtures/workflows/` | Multi-line YAML unreadable as inline strings. Fixtures directory is inspectable and diffable. | [research: pytest snapshot patterns] |
| D-SF3-4 | Module location — `iriai_compose/testing/`, installed via `pip install iriai-compose[testing]` | Tight dependency on schema models/runner. Co-location ensures version coherence. | [code: iriai-compose/pyproject.toml] |
| D-SF3-5 | SF-2 adds optional `node_id: str \| None = None` to `AgentRuntime.invoke()`. MockRuntime accepts it for response routing. | Testing framework should match implementation — the runner knows which node is executing and must pass that through. Backward compatible (kwarg with default None). | [decision: user Q3 — match implementation] |
| D-SF3-6 | `run_test()` is a thin convenience wrapper — constructs RuntimeConfig with in-memory stores, delegates to SF-2's `run(workflow, config, inputs=inputs)`. No exception swallowing. | Primary purpose is verifying our own implementations, not providing external test harness. Match implementation principle. | [decision: user Q4 — thin wrapper] |
| D-SF3-7 | SF-1 owns validation logic in `iriai_compose/schema/validation.py`. SF-3 re-exports via `iriai_compose.testing` for ergonomic imports, and adds `assert_validation_error()` as test-specific assertion. | SF-1 owns validation in the workflow building context; SF-3 owns it in the testing context. No duplication. | [decision: user Q2 — SF-1 owns, SF-3 re-exports] |
| D-SF3-8 | Sequential build — all steps assume SF-1 and SF-2 exist at implementation time | SF-3 sits after SF-1 and SF-2 in the dependency graph. No stubs or protocol abstractions needed. | [decision: user Q1 — option 3] |
| D-SF3-9 | `pyyaml` for snapshot testing, `deepdiff` NOT included — use unified diff for YAML comparison | pyyaml already in SF-2 dependencies. Custom diff with difflib is lighter than deepdiff and produces pytest-friendly output. | [code: iriai-compose/pyproject.toml — pyyaml added by SF-2] |
| D-SF3-10 | ValidationError codes aligned to SF-1's authoritative 21-code list. `invalid_transform_ref` removed (transforms inline per D-21). `invalid_hook_edge_transform` replaces `hook_with_transform`. All fixture filenames match code names. | [H-3] Architecture integration review: codes must match SF-1's `iriai_compose/schema/validation.py` exactly. | [code: SF-1 plan — STEP-24 validation.py codes], [decision: H-3 feedback] |
| D-SF3-11 | Import path for load/dump is `from iriai_compose.schema import load_workflow, dump_workflow` (package-level re-export). Canonical module is `iriai_compose/schema/yaml_io.py` but callers use the `__init__.py` re-export. | [M-5] SF-1's `__init__.py` re-exports both functions from `yaml_io.py`. Direct `yaml_io` import also valid but package-level preferred. | [code: SF-1 plan — schema/__init__.py exports] |
| D-SF3-12 | `run_test()` calls `run(workflow, config, inputs=inputs)` matching SF-2's exact signature: `async def run(workflow: WorkflowConfig \| str \| Path, config: RuntimeConfig, *, inputs: dict[str, Any] \| None = None) -> ExecutionResult`. `Feature` is passed via `RuntimeConfig.feature` field, not as a separate argument. | [C-3] SF-2's `RuntimeConfig` includes optional `feature` and `workspace` fields (auto-created when None). | [code: SF-2 plan — RuntimeConfig dataclass, run() signature] |

### Prerequisites from Other Subfeatures

**SF-1 (Declarative Schema) must provide:**
- `iriai_compose.schema` package-level re-exports: `WorkflowConfig`, `AskNode`, `BranchNode`, `PluginNode`, `PhaseDefinition`, `Edge`, `PortDefinition`, `NodeDefinition`, `ActorDefinition`, `RoleDefinition`, `TypeDefinition`, `MapConfig`, `FoldConfig`, `LoopConfig`, `SequentialConfig`
- `iriai_compose.schema.validation` module with: `validate_workflow()`, `validate_type_flow()`, `detect_cycles()` returning `list[ValidationError]`
- `iriai_compose.schema` package-level re-exports (from `yaml_io.py`): `load_workflow()`, `dump_workflow()` [D-SF3-11]
  - Direct import also valid: `from iriai_compose.schema.yaml_io import load_workflow, dump_workflow`
  - **NOT** `from iriai_compose.schema.io import ...` — no `io.py` module exists
- `ValidationError` dataclass with `code`, `path`, `message`, `context` fields
- Authoritative validation error codes (21 total) [D-SF3-10]:
  - `dangling_edge` — Edge references nonexistent node/port
  - `duplicate_node_id` — Two nodes share ID within a phase
  - `duplicate_phase_id` — Two phases share ID
  - `invalid_actor_ref` — Node actor not in `workflow.actors`
  - `invalid_phase_mode_config` — Missing mode-specific config (e.g., fold without accumulator_init)
  - `invalid_hook_edge_transform` — Hook-sourced edge has non-None `transform_fn`
  - `phase_boundary_violation` — `$input`/`$output` wiring errors
  - `cycle_detected` — DAG cycle found
  - `unreachable_node` — No incoming edges, not phase entry
  - `type_mismatch` — Edge source output type ≠ target input type
  - `invalid_branch_config` — Branch missing minimum ports
  - `invalid_plugin_ref` — plugin_ref/instance_ref not found in declared plugins
  - `missing_output_condition` — Multi-port node without conditions or switch_function (warning-level)
  - `invalid_io_config` — Both input_type and input_schema set simultaneously
  - `invalid_type_ref` — Type reference not in `workflow.types`
  - `invalid_store_ref` — Store name prefix not in `workflow.stores`
  - `invalid_store_key_ref` — Key not in non-open store
  - `store_type_mismatch` — Node output_type doesn't match store key type_ref
  - `invalid_switch_function_config` — BranchNode has both `switch_function` and per-port `condition`
  - `invalid_workflow_io_ref` — Workflow input/output `type_ref` not in `workflow.types`
  - `missing_required_field` — Required field missing in lenient loading path
  - ~~`invalid_transform_ref`~~ — REMOVED: transforms are inline Python on edges per D-21
  - ~~`invalid_hook_ref`~~ — REMOVED: replaced by `invalid_hook_edge_transform`

**SF-2 (DAG Loader & Runner) must provide:**
- `iriai_compose.declarative` module with: `run()`, `RuntimeConfig`, `ExecutionResult`, `PluginRegistry`, `load_workflow()` (re-export from SF-1), `load_runtime_config()`
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
      context_provider: ContextProvider | None = None   # None → DefaultContextProvider(artifacts)
      plugin_registry: PluginRegistry | None = None     # None → default with builtins
      workspace: Workspace | None = None
      feature: Feature | None = None                    # None → auto-created from workflow name
  ```
- `ExecutionResult` dataclass with: `success`, `error`, `nodes_executed`, `artifacts`, `branch_paths`, `cost_summary`, `duration_ms`, `workflow_output`, `hook_warnings`
- `AgentRuntime.invoke()` accepting optional `node_id: str | None = None` kwarg [D-SF3-5]
- `pyyaml>=6.0` as a project dependency (not optional)

---

## Module Structure

```
iriai_compose/
├── testing/
│   ├── __init__.py          # Public API re-exports (single import path)
│   ├── mock_runtime.py      # MockRuntime, MockInteraction
│   ├── fixtures.py          # WorkflowBuilder, minimal_ask_workflow, minimal_branch_workflow, minimal_plugin_workflow
│   ├── assertions.py        # assert_node_reached, assert_artifact, assert_branch_taken, assert_validation_error, assert_node_count, assert_phase_executed
│   ├── snapshot.py          # assert_yaml_round_trip, assert_yaml_equals, yaml_diff
│   ├── runner.py            # run_test (thin wrapper around SF-2 run())
│   └── validation.py        # Re-exports from iriai_compose.schema.validation
├── schema/                  # SF-1 (exists at build time)
│   ├── __init__.py          # Re-exports models, validation, yaml_io functions [D-SF3-11]
│   ├── models.py
│   ├── validation.py        # validate_workflow, validate_type_flow, detect_cycles — OWNED BY SF-1
│   └── yaml_io.py           # load_workflow, dump_workflow — OWNED BY SF-1 [D-SF3-11]
└── declarative/             # SF-2 (exists at build time)
    ├── __init__.py           # Re-exports run, RuntimeConfig, ExecutionResult, PluginRegistry
    ├── runner.py            # run() — OWNED BY SF-2
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
│       └── invalid/                          # [D-SF3-10] Filenames match SF-1 error codes exactly
│           ├── dangling_edge.yaml
│           ├── cycle_detected.yaml           # Was: cycle.yaml
│           ├── type_mismatch.yaml
│           ├── invalid_actor_ref.yaml        # Was: missing_actor.yaml
│           ├── duplicate_node_id.yaml        # Was: duplicate_ids.yaml
│           ├── invalid_phase_mode_config.yaml  # Was: invalid_phase_mode.yaml
│           └── invalid_hook_edge_transform.yaml  # Was: hook_with_transform.yaml
├── testing/                      # SF-3 self-tests
│   ├── __init__.py
│   ├── test_mock_runtime.py
│   ├── test_builder.py
│   ├── test_assertions.py
│   ├── test_validation_reexport.py
│   ├── test_snapshots.py
│   └── test_runner.py
└── conftest.py                   # Existing — unchanged
```

---

## Public API Contract

### `iriai_compose.testing.__init__`

```python
"""iriai_compose.testing — Purpose-built testing module for declarative workflows.

Install: pip install iriai-compose[testing]
Import:  from iriai_compose.testing import MockRuntime, WorkflowBuilder, run_test, ...
"""

# Mock runtimes
from iriai_compose.testing.mock_runtime import MockRuntime, MockInteraction

# Workflow construction
from iriai_compose.testing.fixtures import (
    WorkflowBuilder,
    minimal_ask_workflow,
    minimal_branch_workflow,
    minimal_plugin_workflow,
)

# Execution
from iriai_compose.testing.runner import run_test

# Assertions (execution)
from iriai_compose.testing.assertions import (
    assert_node_reached,
    assert_artifact,
    assert_branch_taken,
    assert_node_count,
    assert_phase_executed,
)

# Assertions (validation) — test-specific, operates on list[ValidationError]
from iriai_compose.testing.assertions import assert_validation_error

# Snapshot testing
from iriai_compose.testing.snapshot import assert_yaml_round_trip, assert_yaml_equals

# Re-exports from SF-1 for ergonomic imports
from iriai_compose.testing.validation import validate_workflow, validate_type_flow, detect_cycles

# Re-export from SF-2 for use in test assertions
from iriai_compose.declarative import ExecutionResult

# Re-export from SF-1 for use in validation assertions
from iriai_compose.schema.validation import ValidationError
```

---

## Component Specifications

### MockRuntime (CMP-135)

**File:** `iriai_compose/testing/mock_runtime.py`

```python
from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel

from iriai_compose.runner import AgentRuntime, InteractionRuntime
from iriai_compose.actors import Role
from iriai_compose.workflow import Workspace
from iriai_compose.pending import Pending


class MockRuntime(AgentRuntime):
    """Configurable mock AgentRuntime for testing declarative workflows.

    Response routing priority:
    1. (node_id, role.name) exact match in responses dict
    2. (None, role.name) role-only match in responses dict
    3. handler callback (receives full call dict)
    4. default_response

    Extends the pattern from tests/conftest.py:MockAgentRuntime [D-SF3-2].
    """

    name = "test-mock"

    def __init__(
        self,
        responses: dict[tuple[str | None, str], str | BaseModel] | None = None,
        default_response: str | BaseModel = "mock response",
        handler: Callable[[dict[str, Any]], str | BaseModel] | None = None,
    ) -> None:
        self._responses = responses or {}
        self._default_response = default_response
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    async def invoke(
        self,
        role: Role,
        prompt: str,
        *,
        output_type: type[BaseModel] | None = None,
        workspace: Workspace | None = None,
        session_key: str | None = None,
        node_id: str | None = None,  # [D-SF3-5] Added by SF-2
    ) -> str | BaseModel:
        matched = False
        response = self._default_response

        call = {
            "node_id": node_id,
            "role": role,
            "prompt": prompt,
            "output_type": output_type,
            "workspace": workspace,
            "session_key": session_key,
            "matched": False,
        }

        # Priority 1: (node_id, role.name) exact match
        key_exact = (node_id, role.name)
        if key_exact in self._responses:
            response = self._responses[key_exact]
            matched = True
        else:
            # Priority 2: (None, role.name) role-only match
            key_role = (None, role.name)
            if key_role in self._responses:
                response = self._responses[key_role]
                matched = True
            elif self._handler:
                # Priority 3: handler callback
                response = self._handler(call)
                matched = True

        call["matched"] = matched
        self.calls.append(call)
        return response


class MockInteraction(InteractionRuntime):
    """Configurable mock InteractionRuntime for testing.

    Extends the pattern from tests/conftest.py:MockInteractionRuntime.
    """

    name = "test-mock-interaction"

    def __init__(
        self,
        approve: bool | str = True,
        choose: str = "",
        respond: str = "mock input",
    ) -> None:
        self._approve = approve
        self._choose = choose
        self._respond = respond
        self.calls: list[Pending] = []

    async def resolve(self, pending: Pending) -> str | bool:
        self.calls.append(pending)
        if pending.kind == "approve":
            return self._approve
        if pending.kind == "choose":
            return self._choose or (pending.options or [""])[0]
        return self._respond
```

**Backward compatibility:** Without `responses` or `handler`, `MockRuntime(default_response="x")` behaves identically to the existing `MockAgentRuntime(response="x")` in `tests/conftest.py`.

**Key difference from existing `MockAgentRuntime`:** `invoke()` signature accepts `node_id` kwarg. The `responses` dict enables per-node routing without a handler function. The `calls` list records `node_id` and `matched` for assertion introspection.

### WorkflowBuilder (CMP-139)

**File:** `iriai_compose/testing/fixtures.py`

```python
from __future__ import annotations

from typing import Any

from iriai_compose.schema.models import (
    WorkflowConfig,
    AskNode,
    BranchNode,
    PluginNode,
    PhaseDefinition,
    Edge,
    PortDefinition,
    ActorDefinition,
    RoleDefinition,
    TypeDefinition,
    SequentialConfig,
    MapConfig,
    FoldConfig,
    LoopConfig,
)


class WorkflowBuilder:
    """Fluent builder for constructing WorkflowConfig instances programmatically.

    Auto-generates minimal actors and phases when referenced but not explicitly defined.
    build() validates the result via Pydantic model-level validation and raises on error.

    Usage:
        wf = (WorkflowBuilder()
            .add_phase("p1", mode="sequential")
            .add_ask_node("n1", phase="p1", actor="pm", prompt="Do work")
            .build())
    """

    def __init__(self, name: str = "test-workflow") -> None:
        self._name = name
        self._actors: dict[str, ActorDefinition] = {}
        self._types: dict[str, TypeDefinition] = {}
        self._phases: dict[str, dict[str, Any]] = {}  # phase_id -> config
        self._nodes: dict[str, dict[str, Any]] = {}    # node_id -> {phase, ...}
        self._edges: list[dict[str, Any]] = []
        self._plugins: dict[str, Any] = {}
        self._stores: dict[str, Any] = {}

    def add_actor(self, name: str, role_prompt: str = "You are a test actor.", **kwargs) -> WorkflowBuilder:
        """Explicitly add an actor. kwargs: type, model, tools, context_keys, resolver."""
        actor_type = kwargs.pop("type", "agent")
        if actor_type == "agent":
            self._actors[name] = ActorDefinition(
                type="agent",
                role=RoleDefinition(
                    name=name,
                    prompt=role_prompt,
                    tools=kwargs.get("tools", []),
                    model=kwargs.get("model"),
                ),
                context_keys=kwargs.get("context_keys", []),
            )
        else:
            self._actors[name] = ActorDefinition(
                type="interaction",
                resolver=kwargs.get("resolver", "human"),
            )
        return self

    def add_type(self, name: str, schema: dict) -> WorkflowBuilder:
        """Add a named type definition."""
        self._types[name] = TypeDefinition(name=name, schema_def=schema)
        return self

    def add_phase(self, phase_id: str, mode: str = "sequential", **mode_config) -> WorkflowBuilder:
        """Add a phase. mode_config passed to the mode-specific config model."""
        self._phases[phase_id] = {"mode": mode, **mode_config}
        return self

    def add_ask_node(self, node_id: str, *, phase: str, actor: str, prompt: str, **kwargs) -> WorkflowBuilder:
        """Add an Ask node. Auto-creates phase (sequential) and actor if undefined."""
        self._ensure_phase(phase)
        self._ensure_actor(actor)
        self._nodes[node_id] = {
            "type": "ask", "phase": phase, "actor": actor, "prompt": prompt, **kwargs
        }
        return self

    def add_branch_node(self, node_id: str, *, phase: str, outputs: list[str], **kwargs) -> WorkflowBuilder:
        """Add a Branch node. outputs = list of output port names (min 1)."""
        self._ensure_phase(phase)
        self._nodes[node_id] = {
            "type": "branch", "phase": phase, "outputs": outputs, **kwargs
        }
        return self

    def add_plugin_node(self, node_id: str, *, phase: str, plugin_ref: str, **kwargs) -> WorkflowBuilder:
        """Add a Plugin node."""
        self._ensure_phase(phase)
        self._nodes[node_id] = {
            "type": "plugin", "phase": phase, "plugin_ref": plugin_ref, **kwargs
        }
        return self

    def add_edge(self, source: str, target: str, *, transform_fn: str | None = None, **kwargs) -> WorkflowBuilder:
        """Add an edge. source/target are 'node_id.port_name' or '$input'/'$output'."""
        self._edges.append({"source": source, "target": target, "transform_fn": transform_fn, **kwargs})
        return self

    def add_plugin(self, plugin_id: str, **kwargs) -> WorkflowBuilder:
        """Add a plugin interface declaration."""
        self._plugins[plugin_id] = kwargs
        return self

    def add_store(self, store_name: str, **kwargs) -> WorkflowBuilder:
        """Add a store declaration."""
        self._stores[store_name] = kwargs
        return self

    def build(self) -> WorkflowConfig:
        """Construct and validate the WorkflowConfig.

        Raises pydantic.ValidationError if the resulting config is structurally
        invalid at the Pydantic model level.

        Does NOT call validate_workflow() — the caller should do that separately
        to get the list[ValidationError] for structural checks.
        """
        # Build phase definitions with their nodes
        phase_defs = []
        for phase_id, phase_config in self._phases.items():
            mode = phase_config.pop("mode", "sequential")
            phase_nodes = []
            for node_id, node_config in self._nodes.items():
                if node_config["phase"] != phase_id:
                    continue
                node_type = node_config["type"]
                if node_type == "ask":
                    phase_nodes.append(AskNode(
                        id=node_id,
                        type="ask",
                        actor=node_config["actor"],
                        prompt=node_config["prompt"],
                        summary=node_config.get("summary"),
                        context_keys=node_config.get("context_keys", []),
                        artifact_key=node_config.get("artifact_key"),
                        output_type=node_config.get("output_type"),
                        outputs=node_config.get("outputs", [PortDefinition(name="output")]),
                    ))
                elif node_type == "branch":
                    output_ports = [
                        PortDefinition(name=p, condition=node_config.get("conditions", {}).get(p))
                        for p in node_config["outputs"]
                    ]
                    phase_nodes.append(BranchNode(
                        id=node_id,
                        type="branch",
                        outputs=output_ports,
                        merge_function=node_config.get("merge_function"),
                        inputs=node_config.get("inputs", [PortDefinition(name="input")]),
                    ))
                elif node_type == "plugin":
                    phase_nodes.append(PluginNode(
                        id=node_id,
                        type="plugin",
                        plugin_ref=node_config.get("plugin_ref"),
                        instance_ref=node_config.get("instance_ref"),
                        config=node_config.get("config"),
                        outputs=node_config.get("outputs", [PortDefinition(name="output")]),
                    ))

            # Build mode config
            mode_configs = {}
            if mode == "sequential":
                mode_configs["sequential_config"] = SequentialConfig()
            elif mode == "map":
                mode_configs["map_config"] = MapConfig(
                    collection=phase_config.get("collection", "ctx['items']"),
                    max_parallelism=phase_config.get("max_parallelism"),
                )
            elif mode == "fold":
                mode_configs["fold_config"] = FoldConfig(
                    collection=phase_config.get("collection", "ctx['items']"),
                    accumulator_init=phase_config.get("accumulator_init", "{}"),
                    reducer=phase_config.get("reducer", "{**accumulator, **result}"),
                    fresh_sessions=phase_config.get("fresh_sessions", False),
                )
            elif mode == "loop":
                mode_configs["loop_config"] = LoopConfig(
                    exit_condition=phase_config.get("exit_condition", "data.complete"),
                    max_iterations=phase_config.get("max_iterations"),
                    fresh_sessions=phase_config.get("fresh_sessions", False),
                )

            # Filter edges belonging to this phase (intra-phase)
            phase_edges = []
            phase_node_ids = {n.id for n in phase_nodes}
            for edge_config in self._edges:
                src_node = edge_config["source"].split(".")[0]
                tgt_node = edge_config["target"].split(".")[0]
                if src_node in phase_node_ids or tgt_node in phase_node_ids or src_node in ("$input",) or tgt_node in ("$output",):
                    phase_edges.append(Edge(
                        source=edge_config["source"],
                        target=edge_config["target"],
                        transform_fn=edge_config.get("transform_fn"),
                        description=edge_config.get("description"),
                    ))

            phase_defs.append(PhaseDefinition(
                id=phase_id,
                mode=mode,
                nodes=phase_nodes,
                edges=phase_edges,
                **mode_configs,
            ))

        # Workflow-level edges (between phases)
        phase_ids = {p.id for p in phase_defs}
        workflow_edges = []
        for edge_config in self._edges:
            src_node = edge_config["source"].split(".")[0]
            tgt_node = edge_config["target"].split(".")[0]
            if src_node in phase_ids or tgt_node in phase_ids:
                workflow_edges.append(Edge(
                    source=edge_config["source"],
                    target=edge_config["target"],
                    transform_fn=edge_config.get("transform_fn"),
                ))

        return WorkflowConfig(
            name=self._name,
            actors=self._actors,
            types=self._types,
            phases=phase_defs,
            edges=workflow_edges,
            plugins=self._plugins if self._plugins else {},
            stores=self._stores if self._stores else {},
        )

    def _ensure_phase(self, phase_id: str) -> None:
        if phase_id not in self._phases:
            self._phases[phase_id] = {"mode": "sequential"}

    def _ensure_actor(self, actor_name: str) -> None:
        if actor_name not in self._actors:
            self.add_actor(actor_name)


def minimal_ask_workflow(
    actor: str = "pm",
    prompt: str = "Do the task.",
    node_id: str = "ask_1",
    phase_id: str = "main",
    **node_kwargs,
) -> WorkflowConfig:
    """Factory: minimal valid workflow with one Ask node."""
    return (WorkflowBuilder()
        .add_phase(phase_id, mode="sequential")
        .add_ask_node(node_id, phase=phase_id, actor=actor, prompt=prompt, **node_kwargs)
        .build())


def minimal_branch_workflow(
    outputs: list[str] | None = None,
    phase_id: str = "main",
) -> WorkflowConfig:
    """Factory: minimal valid workflow with Ask → Branch → two Ask nodes."""
    paths = outputs or ["approved", "rejected"]
    return (WorkflowBuilder()
        .add_phase(phase_id, mode="sequential")
        .add_ask_node("producer", phase=phase_id, actor="pm", prompt="Produce output")
        .add_branch_node("gate", phase=phase_id, outputs=paths,
                         conditions={p: f"data.verdict == '{p}'" for p in paths})
        .add_ask_node("on_approved", phase=phase_id, actor="pm", prompt="Approved path")
        .add_ask_node("on_rejected", phase=phase_id, actor="pm", prompt="Rejected path")
        .add_edge("producer.output", "gate.input")
        .add_edge("gate.approved", "on_approved.input")
        .add_edge("gate.rejected", "on_rejected.input")
        .build())


def minimal_plugin_workflow(
    plugin_ref: str = "artifact_write",
    phase_id: str = "main",
) -> WorkflowConfig:
    """Factory: minimal valid workflow with Ask → Plugin."""
    return (WorkflowBuilder()
        .add_phase(phase_id, mode="sequential")
        .add_plugin(plugin_ref, id=plugin_ref, name=plugin_ref, description="test plugin")
        .add_ask_node("producer", phase=phase_id, actor="pm", prompt="Produce data")
        .add_plugin_node("save", phase=phase_id, plugin_ref=plugin_ref, config={"key": "output"})
        .add_edge("producer.output", "save.input")
        .build())
```

### Assertions (CMP-140 through CMP-143)

**File:** `iriai_compose/testing/assertions.py`

```python
from __future__ import annotations

from typing import Any, Callable

from iriai_compose.declarative import ExecutionResult
from iriai_compose.schema.validation import ValidationError


def assert_node_reached(
    result: ExecutionResult,
    node_id: str,
    *,
    before: str | None = None,
    after: str | None = None,
) -> None:
    """Assert a node was executed, optionally before/after another node.

    Raises AssertionError with diagnostic showing execution order.
    """
    ids = result.node_ids()
    if node_id not in ids:
        raise AssertionError(
            f"Node '{node_id}' was not reached.\n"
            f"Executed nodes: {ids}"
        )
    if before is not None:
        if before not in ids:
            raise AssertionError(
                f"Cannot check ordering: '{before}' was not reached.\n"
                f"Executed nodes: {ids}"
            )
        idx_node = result.node_index(node_id)
        idx_before = result.node_index(before)
        if idx_node >= idx_before:
            raise AssertionError(
                f"Expected '{node_id}' (position {idx_node}) before '{before}' (position {idx_before}).\n"
                f"Execution order: {ids}"
            )
    if after is not None:
        if after not in ids:
            raise AssertionError(
                f"Cannot check ordering: '{after}' was not reached.\n"
                f"Executed nodes: {ids}"
            )
        idx_node = result.node_index(node_id)
        idx_after = result.node_index(after)
        if idx_node <= idx_after:
            raise AssertionError(
                f"Expected '{node_id}' (position {idx_node}) after '{after}' (position {idx_after}).\n"
                f"Execution order: {ids}"
            )


# Sentinel for distinguishing "not provided" from None
_SENTINEL = object()


def assert_artifact(
    result: ExecutionResult,
    key: str,
    *,
    matches: Callable[[Any], bool] | None = None,
    equals: Any = _SENTINEL,
) -> None:
    """Assert an artifact was produced at the given key.

    matches: predicate function that receives the artifact value.
    equals: exact value comparison (use one or the other, not both).
    """
    if key not in result.artifacts:
        raise AssertionError(
            f"Artifact '{key}' not found.\n"
            f"Available artifacts: {list(result.artifacts.keys())}"
        )
    value = result.artifacts[key]
    if matches is not None and not matches(value):
        raise AssertionError(
            f"Artifact '{key}' did not match predicate.\n"
            f"Value: {value!r}"
        )
    if equals is not _SENTINEL and value != equals:
        raise AssertionError(
            f"Artifact '{key}' != expected.\n"
            f"Expected: {equals!r}\n"
            f"Actual:   {value!r}"
        )


def assert_branch_taken(
    result: ExecutionResult,
    branch: str,
    path: str,
) -> None:
    """Assert a BranchNode took a specific output path."""
    if branch not in result.branch_paths:
        raise AssertionError(
            f"Branch '{branch}' not found in execution results.\n"
            f"Recorded branches: {list(result.branch_paths.keys())}"
        )
    actual = result.branch_paths[branch]
    if actual != path:
        raise AssertionError(
            f"Branch '{branch}' took path '{actual}', expected '{path}'."
        )


def assert_node_count(result: ExecutionResult, expected: int) -> None:
    """Assert exactly N nodes were executed."""
    actual = len(result.nodes_executed)
    if actual != expected:
        raise AssertionError(
            f"Expected {expected} nodes executed, got {actual}.\n"
            f"Nodes: {result.node_ids()}"
        )


def assert_phase_executed(result: ExecutionResult, phase_id: str) -> None:
    """Assert at least one node from the given phase was executed."""
    phases_seen = {p for _, p in result.nodes_executed}
    if phase_id not in phases_seen:
        raise AssertionError(
            f"Phase '{phase_id}' was not executed.\n"
            f"Phases with executed nodes: {sorted(phases_seen)}"
        )


def assert_validation_error(
    errors: list[ValidationError],
    *,
    code: str | None = None,
    path: str | None = None,
) -> None:
    """Assert a specific validation error exists in the error list.

    At least one of code or path must be provided. Both can be provided
    for exact matching.

    Validation error codes are defined authoritatively by SF-1 [D-SF3-10]:
      dangling_edge, duplicate_node_id, duplicate_phase_id, invalid_actor_ref,
      invalid_phase_mode_config, invalid_hook_edge_transform, phase_boundary_violation,
      cycle_detected, unreachable_node, type_mismatch, invalid_branch_config,
      invalid_plugin_ref, missing_output_condition, invalid_io_config,
      invalid_type_ref, invalid_store_ref, invalid_store_key_ref,
      store_type_mismatch, invalid_switch_function_config,
      invalid_workflow_io_ref, missing_required_field
    """
    if code is None and path is None:
        raise ValueError("At least one of 'code' or 'path' must be provided")

    for error in errors:
        code_match = code is None or error.code == code
        path_match = path is None or error.path == path
        if code_match and path_match:
            return  # Found

    error_summary = "\n".join(
        f"  [{e.code}] {e.path}: {e.message}" for e in errors
    )
    criteria = []
    if code:
        criteria.append(f"code='{code}'")
    if path:
        criteria.append(f"path='{path}'")
    raise AssertionError(
        f"No validation error matching {', '.join(criteria)}.\n"
        f"Errors found ({len(errors)}):\n{error_summary or '  (none)'}"
    )
```

### Snapshot Testing (CMP-148, CMP-149)

**File:** `iriai_compose/testing/snapshot.py`

```python
from __future__ import annotations

import difflib
from pathlib import Path

import yaml

# [D-SF3-11] Import from package-level re-export, NOT from io.py (which doesn't exist)
from iriai_compose.schema import load_workflow, dump_workflow


def assert_yaml_round_trip(path: str | Path) -> None:
    """Assert that load → dump → load produces identical WorkflowConfig.

    Loads YAML from path, dumps back to string, loads again, compares.
    On mismatch, shows unified diff of the two YAML strings.
    """
    path = Path(path)
    original_text = path.read_text()
    config = load_workflow(path)
    round_tripped_text = dump_workflow(config)

    # Normalize: load both back to dicts for structural comparison
    original_dict = yaml.safe_load(original_text)
    round_tripped_dict = yaml.safe_load(round_tripped_text)

    if original_dict != round_tripped_dict:
        diff = yaml_diff(original_text, round_tripped_text,
                         fromfile=str(path), tofile="round-tripped")
        raise AssertionError(
            f"YAML round-trip mismatch for {path.name}:\n{diff}"
        )


def assert_yaml_equals(
    actual: str,
    expected: str | Path,
    *,
    fromfile: str = "actual",
    tofile: str = "expected",
) -> None:
    """Assert two YAML strings (or string vs file) are structurally equal.

    On mismatch, shows unified diff with line numbers.
    """
    if isinstance(expected, Path):
        tofile = str(expected)
        expected = expected.read_text()

    actual_dict = yaml.safe_load(actual)
    expected_dict = yaml.safe_load(expected)

    if actual_dict != expected_dict:
        diff = yaml_diff(expected, actual, fromfile=tofile, tofile=fromfile)
        raise AssertionError(f"YAML mismatch:\n{diff}")


def yaml_diff(a: str, b: str, *, fromfile: str = "a", tofile: str = "b") -> str:
    """Produce a unified diff between two YAML strings.

    Returns only changed lines with 3 lines of context (not entire file).
    """
    a_lines = a.splitlines(keepends=True)
    b_lines = b.splitlines(keepends=True)
    diff = difflib.unified_diff(a_lines, b_lines, fromfile=fromfile, tofile=tofile, n=3)
    return "".join(diff)
```

### Test Runner (CMP-147)

**File:** `iriai_compose/testing/runner.py`

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from iriai_compose.declarative import (
    run,
    RuntimeConfig,
    ExecutionResult,
    PluginRegistry,
)
from iriai_compose.schema.models import WorkflowConfig
from iriai_compose.workflow import Feature
from iriai_compose.testing.mock_runtime import MockRuntime, MockInteraction


async def run_test(
    workflow: WorkflowConfig | str | Path,
    *,
    runtime: MockRuntime | None = None,
    interaction: MockInteraction | dict[str, MockInteraction] | None = None,
    plugins: PluginRegistry | None = None,
    inputs: dict[str, Any] | None = None,
    feature_id: str = "test",
) -> ExecutionResult:
    """Execute a workflow against mock runtimes and return the result.

    Thin wrapper around SF-2's run() [D-SF3-6, D-SF3-12]. Constructs RuntimeConfig
    with in-memory stores and the provided MockRuntime. Exceptions propagate
    unmodified — this function does NOT catch or wrap errors.

    RuntimeConfig auto-creates InMemoryArtifactStore, InMemorySessionStore,
    and DefaultContextProvider when their fields are None [D-SF3-12].

    Args:
        workflow: WorkflowConfig object, YAML file path, or YAML string.
        runtime: MockRuntime for agent invocations. Defaults to MockRuntime().
        interaction: MockInteraction or dict of named interactions. Defaults to auto-approve.
        plugins: Plugin registry. Defaults to None (RuntimeConfig auto-creates with builtins).
        inputs: Workflow input values passed as `inputs=` kwarg to run(). Defaults to None.
        feature_id: Deterministic Feature ID for test isolation. Defaults to "test".

    Returns:
        ExecutionResult with success, nodes_executed, artifacts, branch_paths, etc.

    Note on SF-2 run() signature [D-SF3-12]:
        run(workflow, config, inputs=inputs) where config is RuntimeConfig.
        Feature is set via RuntimeConfig.feature field (auto-created when None).
    """
    if runtime is None:
        runtime = MockRuntime()

    if interaction is None:
        interaction_runtimes = {"auto": MockInteraction(approve=True)}
    elif isinstance(interaction, dict):
        interaction_runtimes = interaction
    else:
        interaction_runtimes = {"human": interaction, "auto": MockInteraction(approve=True)}

    feature = Feature(
        id=feature_id,
        name=f"Test: {feature_id}",
        slug=feature_id,
        workflow_name="test",
        workspace_id="test",
    )

    # [D-SF3-12] RuntimeConfig includes feature field. Stores (artifacts, sessions,
    # context_provider) default to None and are auto-created by SF-2's run().
    config = RuntimeConfig(
        agent_runtime=runtime,
        interaction_runtimes=interaction_runtimes,
        plugin_registry=plugins,
        feature=feature,
    )

    # [D-SF3-12] Exact call: run(workflow, config, inputs=inputs)
    return await run(workflow, config, inputs=inputs)
```

**Key changes from previous version [D-SF3-12]:**
- Removed explicit `InMemoryArtifactStore`, `InMemorySessionStore`, `DefaultContextProvider` construction — `RuntimeConfig` auto-creates these when None.
- `Feature` is passed via `RuntimeConfig.feature` instead of being unused.
- Removed `artifacts` parameter from `run_test()` — if users need pre-populated artifacts, they can construct `RuntimeConfig` manually and call `run()` directly.
- Signature matches SF-2 exactly: `run(workflow, config, inputs=inputs)`.

### Validation Re-exports

**File:** `iriai_compose/testing/validation.py`

```python
"""Re-exports SF-1 validation functions for ergonomic test imports.

SF-1 owns validation logic in iriai_compose/schema/validation.py [D-SF3-7].
This module provides re-exports so test authors can use a single import path:

    from iriai_compose.testing import validate_workflow, assert_validation_error

Import path note [D-SF3-11]: SF-1's validation module is at
iriai_compose.schema.validation (not iriai_compose.schema.io).
"""

from iriai_compose.schema.validation import (
    validate_workflow,
    validate_type_flow,
    detect_cycles,
)

__all__ = ["validate_workflow", "validate_type_flow", "detect_cycles"]
```

---

## Implementation Steps

### STEP-20: pyproject.toml `[testing]` Extra + Subpackage Skeleton

**Objective:** Add the `testing` optional dependency group to `pyproject.toml` and create the `iriai_compose/testing/` subpackage with all module files as importable stubs.

**Scope:**
| Path | Action |
|------|--------|
| `iriai-compose/pyproject.toml` | modify |
| `iriai_compose/testing/__init__.py` | create |
| `iriai_compose/testing/mock_runtime.py` | create |
| `iriai_compose/testing/fixtures.py` | create |
| `iriai_compose/testing/assertions.py` | create |
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

2. Create `iriai_compose/testing/__init__.py` with the full public API docstring and placeholder comment `# Imports populated in subsequent steps`. Do NOT import anything yet — the dependencies (SF-1, SF-2) must exist first but the package must be structurally importable.

3. Create all 6 module files with module docstrings describing their purpose and `# Implementation in STEP-N` comments. Each file should be a valid Python module (importable without error) but contain no implementation.

4. Verify `pip install -e ".[testing]"` succeeds and `import iriai_compose.testing` works (even if the __init__.py doesn't re-export yet).

**Acceptance Criteria:**
- `pip install -e ".[testing]"` completes without error
- `python -c "import iriai_compose.testing"` succeeds
- `python -c "import iriai_compose.testing.mock_runtime"` succeeds (empty module, no error)
- All 6 submodule files importable
- Existing tests (`pytest tests/`) pass unchanged — no import pollution

**Counterexamples:**
- Do NOT import SF-1 or SF-2 types in this step — they may not exist yet during skeleton creation
- Do NOT add `pyyaml` to testing extras — it is already a core dependency from SF-2
- Do NOT add `deepdiff` or `ruamel.yaml` — use stdlib `difflib` + `pyyaml` [D-SF3-9]
- Do NOT modify `tests/conftest.py` — the existing MockAgentRuntime stays for backward compat with existing tests

**Requirement IDs:** R8 | **Journey IDs:** J-7, J-8, J-9, J-10

---

### STEP-21: MockRuntime + MockInteraction

**Objective:** Implement the `MockRuntime` and `MockInteraction` classes that provide configurable fake runtimes for declarative workflow testing.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/mock_runtime.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/runner.py` | read |
| `iriai_compose/actors.py` | read |
| `iriai_compose/pending.py` | read |
| `tests/conftest.py` | read |

**Instructions:**

1. Implement `MockRuntime(AgentRuntime)` in `mock_runtime.py` exactly as specified in the Component Specifications section above. Key behaviors:
   - `invoke()` signature matches `AgentRuntime.invoke()` plus optional `node_id: str | None = None` [D-SF3-5]
   - Response lookup chain: `(node_id, role.name)` → `(None, role.name)` → `handler(call_dict)` → `default_response`
   - Every call appended to `self.calls` list with `{node_id, role, prompt, output_type, workspace, session_key, matched}`
   - `name = "test-mock"`

2. Implement `MockInteraction(InteractionRuntime)` following the same pattern as `MockInteractionRuntime` in `tests/conftest.py:57-79`. Key behaviors:
   - Constructor: `approve: bool | str = True`, `choose: str = ""`, `respond: str = "mock input"`
   - `resolve()` records calls and returns based on `pending.kind`
   - `name = "test-mock-interaction"`

3. Update `__init__.py` to import and re-export `MockRuntime` and `MockInteraction`.

**Acceptance Criteria:**
- `from iriai_compose.testing import MockRuntime, MockInteraction` works
- `MockRuntime()` with no args returns `"mock response"` for any invocation
- `MockRuntime(responses={("node_1", "pm"): "specific"})` returns `"specific"` when invoked with `node_id="node_1"` and `role.name="pm"`
- `MockRuntime(responses={("node_1", "pm"): "specific"})` returns `"mock response"` (default) when invoked with `node_id="other_node"` and `role.name="pm"`
- `MockRuntime(responses={(None, "pm"): "role-only"})` returns `"role-only"` when invoked with any `node_id` and `role.name="pm"`
- `MockRuntime(handler=lambda call: call["prompt"][:10])` returns first 10 chars of prompt
- `MockRuntime().calls` records all invocations with `node_id` field present
- `MockInteraction(approve=False).resolve(Pending(kind="approve", ...))` returns `False`

**Counterexamples:**
- Do NOT inherit from the existing `MockAgentRuntime` in `tests/conftest.py` — implement fresh from `AgentRuntime` ABC to avoid coupling to test infrastructure
- Do NOT require `responses` dict — it must be optional (default empty dict)
- Do NOT require `handler` — it must be optional (default None)
- Do NOT modify `AgentRuntime` in `runner.py` — that is SF-2's responsibility [D-SF3-5]

**Requirement IDs:** R8 | **Journey IDs:** J-8

---

### STEP-22: WorkflowBuilder + Factory Fixtures

**Objective:** Implement the fluent `WorkflowBuilder` and convenience factory functions (`minimal_ask_workflow`, `minimal_branch_workflow`, `minimal_plugin_workflow`) that programmatically construct valid `WorkflowConfig` instances for tests.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/fixtures.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/schema/models.py` | read |

**Instructions:**

1. Implement `WorkflowBuilder` in `fixtures.py` as specified in the Component Specifications section. Key behaviors:
   - Fluent API: every `add_*` method returns `self`
   - `add_ask_node(node_id, *, phase, actor, prompt)` — auto-creates phase (sequential) and actor (minimal AgentActor) if not explicitly defined
   - `add_branch_node(node_id, *, phase, outputs)` — `outputs` is list of port name strings
   - `add_plugin_node(node_id, *, phase, plugin_ref)` — references a declared plugin
   - `add_edge(source, target)` — uses `"node_id.port_name"` format
   - `build()` constructs `WorkflowConfig` from accumulated state. Pydantic validation fires on construction. Does NOT call `validate_workflow()` — structural validation is the caller's responsibility.

2. Implement the three factory functions:
   - `minimal_ask_workflow(actor, prompt, node_id, phase_id)` → single-phase, single-Ask workflow
   - `minimal_branch_workflow(outputs, phase_id)` → Ask → Branch → two Ask nodes
   - `minimal_plugin_workflow(plugin_ref, phase_id)` → Ask → Plugin

3. Update `__init__.py` to re-export all four symbols.

**Acceptance Criteria:**
- `WorkflowBuilder().add_ask_node("n", phase="p", actor="pm", prompt="x").build()` returns a valid `WorkflowConfig` without requiring explicit `add_phase` or `add_actor` calls
- `minimal_ask_workflow()` returns a `WorkflowConfig` with 1 phase, 1 node, 1 actor
- `minimal_branch_workflow()` returns a `WorkflowConfig` with 1 phase, 4 nodes (producer + gate + 2 path nodes), edges connecting them
- `WorkflowBuilder().add_phase("p", mode="loop", exit_condition="data.done").build()` creates a loop phase with `LoopConfig`
- All factories pass Pydantic model validation on `build()`

**Counterexamples:**
- `build()` must NOT call `validate_workflow()` — it only does Pydantic model-level validation (required fields, types). Structural validation (dangling edges, cycles) is separate.
- Factory functions must NOT require specifying defaultable fields — `minimal_ask_workflow()` with zero args must work
- Do NOT hardcode `outputs: [PortDefinition(name="output")]` for BranchNode — use the provided `outputs` list to create named ports

**Requirement IDs:** R8 | **Journey IDs:** J-7, J-10

---

### STEP-23: Validation Re-exports + `assert_validation_error`

**Objective:** Wire up the validation re-export layer from SF-1 and implement the `assert_validation_error` test assertion. All validation error codes align with SF-1's authoritative 21-code list [D-SF3-10].

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/validation.py` | modify |
| `iriai_compose/testing/assertions.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/schema/validation.py` | read |

**Instructions:**

1. Implement `testing/validation.py` as a pure re-export module. Import `validate_workflow`, `validate_type_flow`, `detect_cycles` from `iriai_compose.schema.validation` and re-export them. Add `__all__` list. Note [D-SF3-11]: the import is from `iriai_compose.schema.validation`, NOT from `iriai_compose.schema.io` (no such module exists).

2. Implement `assert_validation_error(errors, *, code=None, path=None)` in `assertions.py`:
   - Takes a `list[ValidationError]` (SF-1's type)
   - Requires at least one of `code` or `path`
   - Searches list for a matching error — returns silently on match
   - Raises `AssertionError` with diagnostic listing all errors on no match
   - Error summary format: `"  [{code}] {path}: {message}"` per error
   - Docstring includes the authoritative code list [D-SF3-10] for developer reference

3. Update `__init__.py` to re-export `validate_workflow`, `validate_type_flow`, `detect_cycles`, `assert_validation_error`, and `ValidationError`.

**Acceptance Criteria:**
- `from iriai_compose.testing import validate_workflow` works and calls SF-1's implementation
- `from iriai_compose.testing import ValidationError` works
- `assert_validation_error([ValidationError(code="dangling_edge", path="edges[0].target", message="...")], code="dangling_edge")` passes silently
- `assert_validation_error([ValidationError(code="cycle_detected", ...)], code="cycle_detected")` passes — uses SF-1's code name, not `"cycle"` [D-SF3-10]
- `assert_validation_error([ValidationError(code="invalid_actor_ref", ...)], code="invalid_actor_ref")` passes — uses SF-1's code name, not `"missing_actor"` [D-SF3-10]
- `assert_validation_error([], code="dangling_edge")` raises `AssertionError` with message "No validation error matching code='dangling_edge'"
- `assert_validation_error([...], code="nonexistent")` raises with full error listing

**Counterexamples:**
- Do NOT duplicate validation logic — only re-export from SF-1
- Do NOT call `validate_workflow()` inside `assert_validation_error()` — it operates on an already-computed error list
- `assert_validation_error()` must NOT return a bool — it raises `AssertionError` on failure [D-1]
- Do NOT use old code names: `cycle` (use `cycle_detected`), `missing_actor` (use `invalid_actor_ref`), `duplicate_ids` (use `duplicate_node_id`), `invalid_phase_mode` (use `invalid_phase_mode_config`), `hook_with_transform` (use `invalid_hook_edge_transform`), `invalid_transform_ref` (removed — transforms inline per D-21) [D-SF3-10]

**Requirement IDs:** R8, R23 | **Journey IDs:** J-7

---

### STEP-24: Execution Assertion Functions

**Objective:** Implement the remaining assertion functions that operate on `ExecutionResult`: `assert_node_reached`, `assert_artifact`, `assert_branch_taken`, `assert_node_count`, `assert_phase_executed`.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/assertions.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/declarative/__init__.py` | read |

**Instructions:**

1. Implement all five assertion functions in `assertions.py` exactly as specified in the Component Specifications section. Each function:
   - Takes `ExecutionResult` as first argument (from SF-2)
   - Raises `AssertionError` with diagnostic message including actual values
   - Never returns a value — void on success, raises on failure

2. `assert_node_reached(result, node_id, *, before=None, after=None)`:
   - Uses `result.node_ids()` and `result.node_index()` (methods on SF-2's ExecutionResult)
   - `before="X"` means `node_id` must appear at a lower index than `X`
   - `after="X"` means `node_id` must appear at a higher index than `X`
   - Diagnostic includes full execution order

3. `assert_artifact(result, key, *, matches=None, equals=_SENTINEL)`:
   - Uses `result.artifacts` dict
   - `matches` is an optional predicate `Callable[[Any], bool]`
   - `equals` is optional exact comparison (use sentinel to distinguish from `None`)
   - Diagnostic includes available artifact keys and actual value

4. `assert_branch_taken(result, branch, path)`:
   - Uses `result.branch_paths` dict `{branch_node_id: path_taken}`
   - Diagnostic includes recorded branches

5. `assert_node_count(result, expected)` and `assert_phase_executed(result, phase_id)` as straightforward wrappers.

6. Update `__init__.py` to re-export all five functions plus the already-added `assert_validation_error`.

**Acceptance Criteria:**
- `assert_node_reached(result, "n1", before="n2")` passes when n1 executed before n2
- `assert_node_reached(result, "n1", before="n2")` raises when n1 executed after n2, showing both positions
- `assert_artifact(result, "prd", matches=lambda v: "requirements" in v)` passes when artifact contains "requirements"
- `assert_branch_taken(result, "gate", "approved")` passes when gate took "approved" path
- All assertions raise `AssertionError` (not custom exception types) for pytest compatibility

**Counterexamples:**
- Assertions must NOT return bool — they raise on failure, return None on success [D-1]
- `assert_node_reached` must NOT require both `before` and `after` — each is independently optional
- `assert_artifact` must NOT require both `matches` and `equals` — each is independently optional

**Requirement IDs:** R8 | **Journey IDs:** J-8

---

### STEP-25: `run_test()` Wrapper

**Objective:** Implement the thin convenience wrapper that constructs `RuntimeConfig` and delegates to SF-2's `run(workflow, config, inputs=inputs)`.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/runner.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/declarative/__init__.py` | read |
| `iriai_compose/declarative/config.py` | read |
| `iriai_compose/workflow.py` | read |

**Instructions:**

1. Implement `run_test()` in `runner.py` exactly as specified in the Component Specifications section. Key behaviors [D-SF3-12]:
   - Accepts `WorkflowConfig | str | Path` (same flexibility as SF-2's `run()`)
   - `runtime` defaults to `MockRuntime()` (default_response="mock response")
   - `interaction` defaults to auto-approve `MockInteraction(approve=True)` registered as `"auto"`
   - Creates `Feature(id=feature_id, name=f"Test: {feature_id}", ...)` for test isolation
   - Constructs `RuntimeConfig` with `feature=feature` field — RuntimeConfig auto-creates `InMemoryArtifactStore`, `InMemorySessionStore`, and `DefaultContextProvider` when their fields are None
   - Passes `plugins` or `None` to `RuntimeConfig.plugin_registry` — SF-2 auto-registers builtins when None
   - Delegates to `run(workflow, config, inputs=inputs)` — exact signature match [D-SF3-12]
   - Returns `ExecutionResult` directly
   - **Does NOT catch exceptions** — they propagate to the test [D-SF3-6]

2. Update `__init__.py` to re-export `run_test` and `ExecutionResult`.

**Acceptance Criteria:**
- `result = await run_test(minimal_ask_workflow())` returns `ExecutionResult` with `success=True`
- `result = await run_test(minimal_ask_workflow(), runtime=MockRuntime(default_response="custom"))` uses custom runtime
- `result = await run_test("path/to/workflow.yaml")` loads from file path
- Exceptions from SF-2's `run()` propagate unmodified (e.g., `WorkflowLoadError` for bad YAML)
- `run_test()` call site is `run(workflow, config, inputs=inputs)` — matching SF-2's signature exactly [D-SF3-12]

**Counterexamples:**
- Do NOT catch or wrap exceptions — `run_test` is a thin wrapper [D-SF3-6]
- Do NOT manually construct `InMemoryArtifactStore`, `InMemorySessionStore`, or `DefaultContextProvider` — pass `None` and let `RuntimeConfig` auto-create them [D-SF3-12]
- Do NOT pass `Feature` as a separate argument to `run()` — it goes in `RuntimeConfig.feature` [D-SF3-12]
- Do NOT import from `iriai_compose.schema.io` — no such module exists [D-SF3-11]
- Do NOT import `run_test` from `iriai_compose.declarative` — it lives in `iriai_compose.testing.runner`

**Requirement IDs:** R8 | **Journey IDs:** J-8

---

### STEP-26: Snapshot Testing Functions

**Objective:** Implement `assert_yaml_round_trip` and `assert_yaml_equals` for verifying YAML serialization fidelity.

**Scope:**
| Path | Action |
|------|--------|
| `iriai_compose/testing/snapshot.py` | modify |
| `iriai_compose/testing/__init__.py` | modify |
| `iriai_compose/schema/__init__.py` | read |
| `iriai_compose/schema/yaml_io.py` | read |

**Instructions:**

1. Implement `assert_yaml_round_trip(path)` in `snapshot.py`:
   - Reads YAML from file path
   - Calls `load_workflow(path)` → `WorkflowConfig` — import from `iriai_compose.schema` (package-level re-export) [D-SF3-11]
   - Calls `dump_workflow(config)` → YAML string — import from `iriai_compose.schema` [D-SF3-11]
   - Parses both original and round-tripped through `yaml.safe_load()` for structural comparison
   - On mismatch: raises `AssertionError` with unified diff (3 lines context) via `yaml_diff()`

2. Implement `assert_yaml_equals(actual, expected)`:
   - `actual` is a YAML string
   - `expected` is a YAML string or `Path` (reads file if Path)
   - Structural comparison via `yaml.safe_load()` on both
   - On mismatch: unified diff

3. Implement helper `yaml_diff(a, b, *, fromfile, tofile)`:
   - Uses `difflib.unified_diff` with `n=3` context lines
   - Returns diff string (not the full files)

4. Update `__init__.py` to re-export `assert_yaml_round_trip` and `assert_yaml_equals`.

**Acceptance Criteria:**
- `assert_yaml_round_trip("tests/fixtures/workflows/minimal_ask.yaml")` passes for a well-formed fixture
- `assert_yaml_round_trip(path)` raises `AssertionError` with unified diff when round-trip changes content
- `assert_yaml_equals(dump_workflow(config), "tests/fixtures/workflows/expected.yaml")` compares structurally
- Diff output shows only changed lines (not entire file) [J-9 NOT criteria]
- Key ordering differences do NOT cause false failures (structural comparison via `yaml.safe_load()`) [J-9 NOT criteria]
- Import uses `from iriai_compose.schema import load_workflow, dump_workflow` — NOT `from iriai_compose.schema.io` [D-SF3-11]

**Counterexamples:**
- Do NOT use `ruamel.yaml` — use `pyyaml` (`yaml.safe_load`) [D-SF3-9]
- Do NOT use `deepdiff` — use structural dict comparison + `difflib` for human-readable output
- Do NOT show entire file content in error messages — only the unified diff
- Do NOT import from `iriai_compose.schema.io` — use `iriai_compose.schema` package-level re-export or `iriai_compose.schema.yaml_io` direct import [D-SF3-11]

**Requirement IDs:** R8 | **Journey IDs:** J-9

---

### STEP-27: YAML Fixture Files + Self-Tests

**Objective:** Create the YAML fixture files for all phase modes and invalid cases (filenames aligned to SF-1's authoritative error codes [D-SF3-10]), plus write comprehensive self-tests that verify every testing module works correctly.

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
| `tests/fixtures/workflows/invalid/dangling_edge.yaml` | create |
| `tests/fixtures/workflows/invalid/cycle_detected.yaml` | create |
| `tests/fixtures/workflows/invalid/type_mismatch.yaml` | create |
| `tests/fixtures/workflows/invalid/invalid_actor_ref.yaml` | create |
| `tests/fixtures/workflows/invalid/duplicate_node_id.yaml` | create |
| `tests/fixtures/workflows/invalid/invalid_phase_mode_config.yaml` | create |
| `tests/fixtures/workflows/invalid/invalid_hook_edge_transform.yaml` | create |
| `tests/testing/test_mock_runtime.py` | create |
| `tests/testing/test_builder.py` | create |
| `tests/testing/test_assertions.py` | create |
| `tests/testing/test_validation_reexport.py` | create |
| `tests/testing/test_snapshots.py` | create |
| `tests/testing/test_runner.py` | create |
| `tests/testing/__init__.py` | create |

**Instructions:**

1. **YAML Fixtures (valid):** Create one fixture per phase mode + edge case. Each fixture must conform to the SF-1 schema. Structure:
   - `minimal_ask.yaml`: Single sequential phase, one AskNode, one actor
   - `minimal_branch.yaml`: Sequential phase, Ask → Branch (2 outputs) → 2 Ask nodes
   - `minimal_plugin.yaml`: Sequential phase, Ask → Plugin (artifact_write)
   - `sequential_phase.yaml`: 3 Ask nodes chained sequentially
   - `map_phase.yaml`: Map phase with collection expression, single Ask inside
   - `fold_phase.yaml`: Fold phase with accumulator_init, reducer, single Ask inside
   - `loop_phase.yaml`: Loop phase with exit_condition, max_iterations, Ask + Branch inside
   - `multi_phase.yaml`: 2 sequential phases connected by workflow-level edge
   - `hook_edge.yaml`: Ask node with on_end hook edge to Plugin
   - `nested_phases.yaml`: Sequential > Loop > Map nesting

2. **YAML Fixtures (invalid) [D-SF3-10]:** One per validation error code, filenames matching SF-1's authoritative code names exactly. Each should trigger exactly one specific error:
   - `dangling_edge.yaml`: Edge referencing nonexistent node — triggers `dangling_edge`
   - `cycle_detected.yaml`: A→B→A cycle — triggers `cycle_detected` (NOT named `cycle.yaml`)
   - `type_mismatch.yaml`: Edge between incompatible types — triggers `type_mismatch`
   - `invalid_actor_ref.yaml`: AskNode referencing undefined actor — triggers `invalid_actor_ref` (NOT named `missing_actor.yaml`)
   - `duplicate_node_id.yaml`: Two nodes with same ID — triggers `duplicate_node_id` (NOT named `duplicate_ids.yaml`)
   - `invalid_phase_mode_config.yaml`: Fold phase without accumulator_init — triggers `invalid_phase_mode_config` (NOT named `invalid_phase_mode.yaml`)
   - `invalid_hook_edge_transform.yaml`: Hook-sourced edge with non-None transform_fn — triggers `invalid_hook_edge_transform` (NOT named `hook_with_transform.yaml`)

3. **Self-tests:** Write pytest tests for each testing module:
   - `test_mock_runtime.py`: Response routing priority, call recording, backward compat (no responses = default), MockInteraction approve/choose/respond
   - `test_builder.py`: WorkflowBuilder fluent API, auto-generation, all three factories, Pydantic validation on build
   - `test_assertions.py`: All 6 assertion functions with passing and failing cases, error message quality
   - `test_validation_reexport.py`: Verify re-exports point to SF-1 implementations, `assert_validation_error` with fixtures using SF-1 codes (`cycle_detected`, `invalid_actor_ref`, `duplicate_node_id`, `invalid_phase_mode_config`, `invalid_hook_edge_transform`)
   - `test_snapshots.py`: Round-trip on valid fixtures, assert_yaml_equals with matching and mismatching content. Import `load_workflow`/`dump_workflow` from `iriai_compose.schema` [D-SF3-11]
   - `test_runner.py`: run_test with minimal workflow, custom runtime, exception propagation. Verify `run()` called as `run(workflow, config, inputs=inputs)` [D-SF3-12]

**Acceptance Criteria:**
- All 10 valid YAML fixtures load via `load_workflow()` (imported from `iriai_compose.schema` [D-SF3-11]) without errors
- All 7 invalid YAML fixtures produce the expected `ValidationError` code when passed through `validate_workflow()`:
  - `dangling_edge.yaml` → code `"dangling_edge"`
  - `cycle_detected.yaml` → code `"cycle_detected"` [D-SF3-10]
  - `type_mismatch.yaml` → code `"type_mismatch"`
  - `invalid_actor_ref.yaml` → code `"invalid_actor_ref"` [D-SF3-10]
  - `duplicate_node_id.yaml` → code `"duplicate_node_id"` [D-SF3-10]
  - `invalid_phase_mode_config.yaml` → code `"invalid_phase_mode_config"` [D-SF3-10]
  - `invalid_hook_edge_transform.yaml` → code `"invalid_hook_edge_transform"` [D-SF3-10]
- `pytest tests/testing/` passes — all self-tests green
- `pytest tests/` passes — existing tests unaffected
- Snapshot round-trip passes for all valid fixtures

**Counterexamples:**
- Do NOT place test files in `iriai_compose/testing/` — tests go in `tests/testing/`
- Do NOT use `tests/conftest.py` fixtures — self-tests should use `iriai_compose.testing` exclusively
- Invalid fixtures must NOT have multiple errors — isolate one error per file for precise assertion testing
- YAML fixtures must NOT use `position` fields (UI-only, clutters test data)
- Do NOT name invalid fixtures with old code names [D-SF3-10]:
  - ~~`cycle.yaml`~~ → use `cycle_detected.yaml`
  - ~~`missing_actor.yaml`~~ → use `invalid_actor_ref.yaml`
  - ~~`duplicate_ids.yaml`~~ → use `duplicate_node_id.yaml`
  - ~~`invalid_phase_mode.yaml`~~ → use `invalid_phase_mode_config.yaml`
  - ~~`hook_with_transform.yaml`~~ → use `invalid_hook_edge_transform.yaml`
- Do NOT create a fixture for `invalid_transform_ref` — that code is removed (transforms inline per D-21) [D-SF3-10]
- Do NOT import `load_workflow` from `iriai_compose.schema.io` — import from `iriai_compose.schema` [D-SF3-11]

**Requirement IDs:** R8, R23 | **Journey IDs:** J-7, J-8, J-9, J-10

---

## Interfaces to Other Subfeatures

### SF-1 → SF-3 (Python Import)

SF-3 imports from `iriai_compose.schema` (package-level re-exports from `__init__.py`) [D-SF3-11]:
- **Models:** `WorkflowConfig`, `AskNode`, `BranchNode`, `PluginNode`, `PhaseDefinition`, `Edge`, `PortDefinition`, `ActorDefinition`, `RoleDefinition`, `TypeDefinition`, `SequentialConfig`, `MapConfig`, `FoldConfig`, `LoopConfig`, `NodeDefinition`
- **Validation:** `validate_workflow()`, `validate_type_flow()`, `detect_cycles()` → `list[ValidationError]` — from `iriai_compose.schema.validation`
- **I/O:** `load_workflow()`, `dump_workflow()` — from `iriai_compose.schema` (re-exported from `yaml_io.py`) [D-SF3-11]
  - Canonical source: `iriai_compose/schema/yaml_io.py`
  - Preferred import: `from iriai_compose.schema import load_workflow, dump_workflow`
  - Also valid: `from iriai_compose.schema.yaml_io import load_workflow, dump_workflow`
  - **NOT valid:** `from iriai_compose.schema.io import ...` — no `io.py` module exists
- **Types:** `ValidationError` dataclass — from `iriai_compose.schema.validation`

SF-3 re-exports validation functions through `iriai_compose.testing` for ergonomic imports [D-SF3-7].

### SF-2 → SF-3 (Python Import)

SF-3 imports from `iriai_compose.declarative`:
- **Execution:** `run(workflow, config: RuntimeConfig, *, inputs: dict[str, Any] | None = None) → ExecutionResult` [D-SF3-12]
- **Config:** `RuntimeConfig` dataclass — fields include `agent_runtime`, `interaction_runtimes`, `artifacts` (None=auto), `sessions` (None=auto), `context_provider` (None=auto), `plugin_registry` (None=auto), `workspace`, `feature` (None=auto) [D-SF3-12]
- **Result:** `ExecutionResult` dataclass (re-exported through testing.__init__)
- **Plugins:** `PluginRegistry`

SF-3's `run_test()` calls `run(workflow, config, inputs=inputs)` — exact signature match [D-SF3-6, D-SF3-12].

### SF-3 → SF-4 (Python Import)

SF-4 (migration test suites) imports from `iriai_compose.testing`:
- `MockRuntime`, `MockInteraction` — for mocking agent responses in migration tests
- `run_test` — for executing migrated YAML workflows
- `assert_node_reached`, `assert_artifact`, `assert_branch_taken` — for verifying execution paths
- `assert_phase_executed`, `assert_node_count` — for verifying phase coverage
- `validate_workflow`, `assert_validation_error` — for verifying migrated YAML is structurally valid. Uses SF-1's authoritative error codes [D-SF3-10]: `cycle_detected`, `invalid_actor_ref`, `duplicate_node_id`, `invalid_phase_mode_config`, `invalid_hook_edge_transform`, etc.
- `assert_yaml_round_trip` — for verifying serialization fidelity of migrated YAML
- `WorkflowBuilder` — for programmatic workflow construction in edge-case tests

### Existing Test Infrastructure

The existing `tests/conftest.py` with `MockAgentRuntime` and `MockInteractionRuntime` remains untouched. Existing tests in `tests/test_*.py` continue to use the old mocks. New tests targeting declarative workflows use `iriai_compose.testing` exclusively. No migration of existing tests required.

---

## Revision Change Summary

### [H-3] ValidationError Code Alignment [D-SF3-10]

All codes aligned to SF-1's authoritative 21-code list. Changes:

| Old (plan v1) | New (aligned to SF-1) | Affected Locations |
|---|---|---|
| `cycle` (fixture name) | `cycle_detected` | STEP-27 fixture, test_validation_reexport |
| `missing_actor` (fixture name) | `invalid_actor_ref` | STEP-27 fixture, test_validation_reexport |
| `duplicate_ids` (fixture name) | `duplicate_node_id` | STEP-27 fixture, test_validation_reexport |
| `invalid_phase_mode` (fixture name) | `invalid_phase_mode_config` | STEP-27 fixture, test_validation_reexport |
| `hook_with_transform` (fixture name) | `invalid_hook_edge_transform` | STEP-27 fixture, test_validation_reexport |
| `invalid_transform_ref` (code list) | REMOVED | Prerequisites, Component Specs |
| `invalid_hook_ref` (code list) | Replaced by `invalid_hook_edge_transform` | Prerequisites, Component Specs |

### [M-5] Import Path Fix [D-SF3-11]

All references to `iriai_compose.schema.io` updated:

| Old | New | Reason |
|---|---|---|
| `from iriai_compose.schema.io import load_workflow, dump_workflow` | `from iriai_compose.schema import load_workflow, dump_workflow` | Package-level re-export from `yaml_io.py` |
| `iriai_compose/schema/io.py` in module structure | `iriai_compose/schema/yaml_io.py` | Canonical module path per SF-1 plan |
| STEP-26 scope: `iriai_compose/schema/io.py` → read | `iriai_compose/schema/__init__.py` + `iriai_compose/schema/yaml_io.py` → read | Correct file references |

### [C-3] run_test() Signature Confirmation [D-SF3-12]

Confirmed `run(workflow, config, inputs=inputs)` matches SF-2's exact signature:
```python
async def run(
    workflow: WorkflowConfig | str | Path,
    config: RuntimeConfig,
    *,
    inputs: dict[str, Any] | None = None,
) -> ExecutionResult
```

Additional corrections:
- `Feature` now passed via `RuntimeConfig.feature` field (not as unused variable)
- Removed manual construction of `InMemoryArtifactStore`, `InMemorySessionStore`, `DefaultContextProvider` — `RuntimeConfig` auto-creates these when None
- Removed `artifacts` parameter from `run_test()` to keep wrapper thin

---

## Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-64 | SF-1 schema models not available at SF-3 build time | high | Sequential build order enforced by dependency graph. If SF-1 is delayed, SF-3 cannot start STEP-22+. | STEP-22, STEP-23, STEP-26, STEP-27 |
| RISK-65 | SF-2 `run()` / `ExecutionResult` not available at SF-3 build time | high | Sequential build order. If SF-2 is delayed, STEP-24, STEP-25 cannot start. STEP-21 (MockRuntime) can proceed since it only depends on existing `AgentRuntime` ABC. | STEP-24, STEP-25, STEP-27 |
| RISK-66 | SF-2 does not add `node_id` to `AgentRuntime.invoke()` | medium | Documented as prerequisite [D-SF3-5]. MockRuntime works without it (node_id defaults to None, falls back to role-only matching). Degraded but functional. | STEP-21 |
| RISK-67 | YAML fixture files don't match SF-1 schema changes during development | low | Fixtures are simple and easy to update. Self-tests (STEP-27) catch schema drift immediately. Fixture filenames now aligned to SF-1 codes [D-SF3-10] reducing drift risk. | STEP-27 |
| RISK-68 | `WorkflowBuilder.build()` edge assignment heuristic (intra-phase vs workflow-level) is too naive | medium | The current heuristic uses node ID prefix matching to assign edges to phases. If this proves insufficient for complex nested cases, add explicit `phase` parameter to `add_edge()`. SF-4 migration tests will surface this quickly. | STEP-22 |
| RISK-69 | SF-2's `run()` signature changes from `run(workflow, config, inputs=inputs)` | medium | Documented exact contract [D-SF3-12] verified against SF-2 plan. If SF-2 changes signature, only STEP-25 (`runner.py`) needs updating — single callsite. | STEP-25 |
| RISK-70 | SF-1's `load_workflow`/`dump_workflow` re-export path changes from `iriai_compose.schema` | low | Documented both import paths [D-SF3-11]: package-level (`iriai_compose.schema`) and direct (`iriai_compose.schema.yaml_io`). Only `snapshot.py` imports these — single file to update. | STEP-26 |

---


---