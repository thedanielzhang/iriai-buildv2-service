<!-- SF: declarative-schema -->
<section id="sf-declarative-schema" class="subfeature-section">
    <h2>SF-1 Declarative Schema &amp; Primitives</h2>
    <div class="provenance">Subfeature: <code>declarative-schema</code></div>

    <h3>Overview</h3>
    <div class="overview-text">SF-1 introduces iriai_compose/schema/, a pure-data Pydantic v2 subpackage defining the declarative workflow format for iriai-compose. Four key invariants: (1) actors discriminate on actor_type with only agent|human as valid values; (2) BranchNode uses per-port conditions on paths with optional merge_function for gather per D-GR-35; (3) WorkflowConfig root is closed to schema_version, workflow_version, name, description, metadata, actors, phases, edges, templates, plugins, types, cost_config, and context_keys only; (4) composer fetches JSON Schema at runtime from /api/schema/workflow while static workflow-schema.json is build/test-only. Cost config is split into three scoped types: WorkflowCostConfig (workflow.cost_config), PhaseCostConfig (phase.cost), NodeCostConfig (node.cost). AskNode.prompt is the sole canonical field for the task prompt string — not &#x27;task&#x27;, not &#x27;context_text&#x27;. Context injection uses context_keys at node/actor/phase/workflow levels. The schema module exports exactly: WorkflowConfig, PhaseDefinition, AskNode, BranchNode, PluginNode, NodeDefinition, EdgeDefinition, ActorDefinition, AgentActorDef, HumanActorDef, RoleDefinition, PortDefinition, WorkflowInputDefinition, WorkflowOutputDefinition, TypeDefinition, PluginInterface, TemplateDefinition, SequentialModeConfig, MapModeConfig, FoldModeConfig, LoopModeConfig, ModeConfig, WorkflowCostConfig, PhaseCostConfig, NodeCostConfig, ValidationError — and the functions load_workflow, dump_workflow, validate_workflow, validate_type_flow, detect_cycles, build_port_index, resolve_port_type, is_hook_source, parse_port_ref, generate_json_schema. MapNode, FoldNode, LoopNode, TransformRef, and HookRef are phantom types that do not exist in this package.</div>

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
            <td>SF-3 testing subpackage. Imports schema models and validation helpers from iriai_compose.schema. Test fixtures cover actor_type agent|human, BranchNode condition_type modes, path resolution, switch_function/merge_function rejection, nested phase containment, context_keys at all hierarchy levels including WorkflowConfig root.</td>
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
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;caller&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;validate_workflow(config)&#x27;, &#x27;description&#x27;: &#x27;Entry point. Builds port index including BranchNode.paths and dispatches all validation checks.&#x27;, &#x27;returns&#x27;: &#x27;list[ValidationError]&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_duplicate_ids()&#x27;, &#x27;description&#x27;: &#x27;Scan all phases and children recursively for node/phase ID collisions.&#x27;, &#x27;returns&#x27;: &#x27;duplicate_id errors&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_actor_refs_and_types()&#x27;, &#x27;description&#x27;: &quot;Verify each AskNode.actor references workflow.actors AND that every ActorDefinition uses actor_type in [&#x27;agent&#x27;,&#x27;human&#x27;]. Reject &#x27;interaction&#x27; alias.&quot;, &#x27;returns&#x27;: &#x27;invalid_actor_ref errors, invalid_actor_type errors&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_phase_configs()&#x27;, &#x27;description&#x27;: &#x27;Verify mode-specific mode_config presence, loop dual-exit port requirements, nested children containment.&#x27;, &#x27;returns&#x27;: &#x27;invalid_phase_mode_config errors&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_edges()&#x27;, &#x27;description&#x27;: &#x27;Resolve node_id.port_name references via dict lookup on inputs, outputs, hooks, or BranchNode.paths within the owning phase.&#x27;, &#x27;returns&#x27;: &#x27;dangling_edge errors&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_hook_edge_constraints()&#x27;, &#x27;description&#x27;: &#x27;Identify hook-sourced edges and enforce transform_fn=None.&#x27;, &#x27;returns&#x27;: &#x27;invalid_hook_edge_transform errors&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_cycles()&#x27;, &#x27;description&#x27;: &#x27;Detect cycles within each phase graph and nested children.&#x27;, &#x27;returns&#x27;: &#x27;cycle_detected errors&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_branch_per_port_conditions()&#x27;, &#x27;description&#x27;: &#x27;Verify each BranchNode.paths entry has valid per-port condition. Reject switch_function. merge_function valid only on gather (multi-input) BranchNodes per D-GR-35.&#x27;, &#x27;returns&#x27;: &#x27;invalid_branch_config errors&#x27;}</li><li>{&#x27;sequence&#x27;: 9, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_rejected_root_fields()&#x27;, &#x27;description&#x27;: &#x27;Reject stores, plugin_instances, context_text, and any field not in the PRD-canonical closed set (which NOW includes context_keys as a valid root field per D-GR-41).&#x27;, &#x27;returns&#x27;: &#x27;unsupported_root_field errors&#x27;}</li><li>{&#x27;sequence&#x27;: 10, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_expression_limits()&#x27;, &#x27;description&#x27;: &quot;Enforce expression size limits on BranchNode per-port condition strings when condition_type=&#x27;expression&#x27;.&quot;, &#x27;returns&#x27;: &#x27;expression_limit errors&#x27;}</li><li>{&#x27;sequence&#x27;: 11, &#x27;from_service&#x27;: &#x27;SVC-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-1&#x27;, &#x27;action&#x27;: &#x27;_check_type_flow()&#x27;, &#x27;description&#x27;: &#x27;Compare source output type versus target input type across data edges including BranchNode paths and cross-phase edges.&#x27;, &#x27;returns&#x27;: &#x27;type_mismatch errors&#x27;}</li></ol>
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
                        <td><code>Literal[&#x27;ask&#x27;,&#x27;branch&#x27;,&#x27;plugin&#x27;]</code></td>
                        <td>Node kind.</td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str] | None</code></td>
                        <td>Node-level runtime context selection keys.</td>
                    </tr><tr>
                        <td><code>artifact_key</code></td>
                        <td><code>str | None</code></td>
                        <td>Artifact storage key identifier associated with node output.</td>
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
