<!-- SF: workflow-migration -->
<section id="sf-workflow-migration" class="subfeature-section">
    <h2>SF-4 Workflow Migration &amp; Litmus Test</h2>
    <div class="provenance">Subfeature: <code>workflow-migration</code></div>

    <h3>Overview</h3>
    <div class="overview-text">SF-4 migrates three imperative Python workflows (planning, develop, bugfix) into declarative YAML conforming to the SF-1 schema, reclassifying 12 specialized plugins into three categories: general plugin type instances (store/hosting/mcp/subprocess/http/config), inline edge transforms for pure data functions, and AskNodes for LLM-mediated operations. iriai-build-v2 serves as the runner application with minimal updates: a thin _declarative.py wrapper imports iriai_compose.declarative.run(), maps BootstrappedEnv services to RuntimeConfig via D-A4 bridge adapters, and adds a --yaml CLI flag. RuntimeConfig uses authoritative field names per PRD R5: agent_runtime (singular AgentRuntime, not a dict), plugin_registry (not plugins dict). This revision aligns SF-4 to SF-2&#x27;s canonical non-breaking runtime ABI: AgentRuntime.invoke() stays unchanged, node_id is propagated through runner-managed ContextVars, hierarchical context assembly is standardized to workflow -&gt; phase -&gt; actor -&gt; node, and core checkpoint/resume is not part of the SF-2 contract. The plan defines six general plugin type interfaces, seven Category B edge transforms, three Category C AskNode conversions, three reusable task templates, ~50-55 behavioral equivalence tests, and a JSON seed package for SF-5 database seeding. D-GR-41 corrections applied: iriai_compose.schema export list corrected (phantom MapNode/FoldNode/LoopNode/TransformRef/HookRef removed, CostConfig replaced by WorkflowCostConfig/PhaseCostConfig/NodeCostConfig, 10+ missing exports added); run() signature fixed to (workflow: WorkflowConfig, config: RuntimeConfig, *, inputs=None); AskNode.actor_ref canonical field and prompt-only contract clarified; ActorDefinition.actor_type enum corrected to agent|human.</div>

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
            <td>Thin declarative runner wrapper (D-SF4-26). workflows/_declarative.py (~100 lines): calls schema_io.load_workflow(yaml_path) -&gt; WorkflowConfig, then run(workflow=loaded_config, config=RuntimeConfig(agent_runtime=ClaudeAgentRuntime(...), plugin_registry=registry, ...), inputs=None). CLI app.py gains --yaml flag on plan/develop/bugfix commands. Additive only — existing PlanningWorkflow, FullDevelopWorkflow, BugFixWorkflow and TrackedWorkflowRunner untouched. Existing ClaudeAgentRuntime/CodexAgentRuntime invoke signatures remain unchanged.</td>
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
            <h4><code>CP-19</code>: CLI --yaml flag triggers _declarative.py wrapper: loads WorkflowConfig from YAML, bootstraps BootstrappedEnv services into RuntimeConfig, then calls iriai_compose.declarative.run(workflow=loaded_config, config=RuntimeConfig(...))</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;to_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;action&#x27;: &quot;CLI: iriai-build plan &#x27;test&#x27; --workspace /path --yaml planning.yaml&quot;, &#x27;description&#x27;: &#x27;Click command parses --yaml flag, calls _run() with yaml_path parameter passed to _declarative.run_declarative()&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;to_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;action&#x27;: &#x27;bootstrap(workspace_path) -&gt; BootstrappedEnv&#x27;, &#x27;description&#x27;: &#x27;Standard bootstrap: asyncpg pool, stores, services. Same path as imperative workflows.&#x27;, &#x27;returns&#x27;: &#x27;BootstrappedEnv&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;to_service&#x27;: &#x27;schema_io&#x27;, &#x27;action&#x27;: &#x27;load_workflow(yaml_path) -&gt; WorkflowConfig&#x27;, &#x27;description&#x27;: &#x27;Load and hydrate YAML into WorkflowConfig before calling run(). REQUIRED: run() accepts WorkflowConfig object, not a yaml_path string.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;to_service&#x27;: &#x27;plugins_adapters&#x27;, &#x27;action&#x27;: &#x27;create_plugin_runtimes(services=env_services, feature_id=feature.id, artifacts=env.artifacts) -&gt; dict[str, PluginRuntime]&#x27;, &#x27;description&#x27;: &#x27;D-A4 bridge maps BootstrappedEnv services to PluginRuntime instances via Protocol-based adapters. hosting-&gt;HostingPluginAdapter, preview-&gt;McpPluginAdapter, git-&gt;SubprocessPluginAdapter, etc.&#x27;, &#x27;returns&#x27;: &#x27;dict[str, PluginRuntime]&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;to_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;action&#x27;: &#x27;RuntimeConfig(agent_runtime=ClaudeAgentRuntime(session_store=env.sessions), plugin_registry=registry, artifacts=env.artifacts, sessions=env.sessions, workspace=workspace, feature=feature)&#x27;, &#x27;description&#x27;: &#x27;Assemble RuntimeConfig. agent_runtime is a singular AgentRuntime instance (not dict[str, AgentRuntime]). plugin_registry wraps type_interfaces + instances.&#x27;, &#x27;returns&#x27;: &#x27;RuntimeConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;iriai_build_v2_runner&#x27;, &#x27;to_service&#x27;: &#x27;iriai_compose_declarative&#x27;, &#x27;action&#x27;: &#x27;run(workflow=loaded_config, config=runtime_config, inputs=None) -&gt; ExecutionResult&#x27;, &#x27;description&#x27;: &#x27;Calls SF-2 canonical ABI with WorkflowConfig object (not yaml_path). The wrapper does NOT pass node_id into AgentRuntime.invoke(); declarative runner injects phase/node identity through ContextVars per SF-2 contract.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionResult&#x27;}</li></ol>
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
                        <td><code>message</code></td>
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
            <td>D-SF4-22: iriai-build-v2 is read-only for all existing Python workflow classes. Only additive: workflows/_declarative.py wrapper + --yaml CLI flag.</td>
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
