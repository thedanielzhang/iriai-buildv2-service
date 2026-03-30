<!-- SF: testing-framework -->
<section id="sf-testing-framework" class="subfeature-section">
    <h2>SF-3 Testing Framework — System Design</h2>
    <div class="provenance">Subfeature: <code>testing-framework</code></div>

    <h3>Overview</h3>
    <div class="overview-text">`iriai_compose.testing` is a purpose-built Python testing subpackage within `iriai-compose` for validating declarative workflow definitions. It provides fluent mock runtimes, fixture builders, execution assertions, validation re-exports, and YAML snapshot helpers.

**Edge contracts established per D-GR-41:**

**SF-1→SF-3:** SF-3 imports from `iriai_compose.schema`: `WorkflowConfig`, `PhaseDefinition`, `AskNode`, `BranchNode`, `PluginNode`, `ActorDefinition`, `AgentActorDef`, `HumanActorDef`, `Edge`, `PortDefinition`, `ValidationError`, `load_workflow()`, `dump_workflow()`, `validate_workflow()`, `validate_type_flow()`, `detect_cycles()`. Entity names are `PhaseDefinition` (not `Phase`) and `Edge` (not `EdgeDefinition`). Phantom exports `MapNode`, `FoldNode`, `LoopNode`, `TransformRef`, `HookRef` do not exist.

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
            <td>SF-1 schema models and validation logic consumed by SF-3 builders, snapshot helpers, and validation re-exports. Canonical public exports from `iriai_compose.schema` (per D-GR-34/D-GR-41): `WorkflowConfig`, `PhaseDefinition`, `AskNode`, `BranchNode`, `PluginNode`, `ActorDefinition`, `AgentActorDef`, `HumanActorDef`, `Edge`, `PortDefinition`, `ValidationError`, `load_workflow()`, `dump_workflow()`, `validate_workflow()`, `validate_type_flow()`, `detect_cycles()`. Phase execution modes (`MapConfig`, `FoldConfig`, `LoopConfig`, `SequentialConfig`) are separate config types attached to `PhaseDefinition.mode` — they are not node types. Phantom exports `MapNode`, `FoldNode`, `LoopNode`, `TransformRef`, and `HookRef` do not exist in `iriai_compose.schema` and must never be imported. Entity name is `PhaseDefinition` (not `Phase`), `Edge` (not `EdgeDefinition`). All port containers are `dict[str, PortDefinition]` keyed by port name.</td>
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
            <td>Thin async wrapper around the canonical SF-2 `run()` ABI. Assembles a `RuntimeConfig` from provided mocks: `runtime` → `config.agent_runtime`, `interaction` → `config.interaction_runtimes={&#x27;default&#x27;: interaction}`, `plugin_registry` → `config.plugin_registry`, `artifacts` → `config.artifacts`. Delegates directly through `run(workflow, config, inputs=inputs)` and returns `ExecutionResult` unchanged. Must not inject a `node_id` kwarg into `AgentRuntime.invoke()`, synthesize checkpoint/resume behavior, swallow exceptions, or rewrite `ExecutionResult` fields.</td>
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
            <td>D-SF3-16: SF-1→SF-3 import boundary established per D-GR-41. Canonical imports from `iriai_compose.schema`: `WorkflowConfig`, `PhaseDefinition`, `AskNode`, `BranchNode`, `PluginNode`, `ActorDefinition`, `AgentActorDef`, `HumanActorDef`, `Edge`, `PortDefinition`, `ValidationError`, `load_workflow()`, `dump_workflow()`, `validate_workflow()`, `validate_type_flow()`, `detect_cycles()`. Phantom exports `MapNode`, `FoldNode`, `LoopNode`, `TransformRef`, `HookRef` do not exist and are permanently prohibited. Entity names `PhaseDefinition` (not `Phase`) and `Edge` (not `EdgeDefinition`) are canonical. Phase execution modes (`MapConfig`, `FoldConfig`, `LoopConfig`, `SequentialConfig`) are config types on `PhaseDefinition.mode`, not node types.</td>
        </tr><tr>
            <td><code>D-65</code></td>
            <td>D-SF3-17: SF-2→SF-3 ABI edge established per D-GR-41. Canonical run() signature: `async def run(workflow: WorkflowConfig | str | Path, config: RuntimeConfig, *, inputs: dict[str, Any] | None = None) -&gt; ExecutionResult`. The stale `(yaml_path, runtime, workspace, transform_registry, hook_registry)` signature is permanently rejected. `ExecutionResult.nodes_executed` ordering is `(node_id, phase_id)` — node first, containing phase second. `ExecutionResult.hook_warnings: list[str]` is a confirmed SF-2 field SF-3 may assert against. `ExecutionHistory` is a confirmed SF-2 export (added per D-GR-34) and the type of `ExecutionResult.history`. `RuntimeConfig` fields: `agent_runtime: AgentRuntime`, `interaction_runtimes: dict[str, InteractionRuntime]`, `artifacts: ArtifactStore | None`, `sessions: SessionStore | None`, `context_provider: ContextProvider | None`, `plugin_registry: PluginRegistry | None`, `workspace: Workspace | None`, `feature: Feature | None`. `run_test()` wraps these fields into RuntimeConfig — `interaction` parameter maps to `interaction_runtimes={&#x27;default&#x27;: interaction}`.</td>
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
            <td>RISK-11 (MEDIUM): `run_test()` builds `RuntimeConfig` with `interaction_runtimes={&#x27;default&#x27;: interaction}` but the workflow expects a different key. Mitigation: document that the default key is `&#x27;default&#x27;`; SF-3 test fixtures should use `default` as the interaction runtime key.</td>
        </tr></tbody>
    </table>
</section>
<hr/>
