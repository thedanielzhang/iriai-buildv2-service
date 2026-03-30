<!-- SF: dag-loader-runner -->
<section id="sf-dag-loader-runner" class="subfeature-section">
    <h2>SF-2: DAG Loader &amp; Runner — D-GR-41 Canonical Cross-SF Edge Contracts</h2>
    <div class="provenance">Subfeature: <code>dag-loader-runner</code></div>

    <h3>Overview</h3>
    <div class="overview-text">SF-2 treats the current SF-1 PRD as the only authoritative declarative wire contract. WorkflowConfig root fields are limited to schema_version, workflow_version, name, description, metadata, context_keys, actors, phases, edges, templates, plugins, types, and cost_config. The workflow-level context_keys field (added per D-GR-41) supplies the top layer of the hierarchical merge order workflow→phase→actor→node. All 10 cross-subfeature edge data contracts are defined per D-GR-41: (1) SF-1→SF-2: imports from iriai_compose.schema; 23 valid exports; phantom types MapNode/FoldNode/LoopNode/TransformRef/HookRef do not exist. (2) SF-2→SF-3: run(workflow, config: RuntimeConfig, *, inputs=None) — not the stale (yaml_path, runtime, workspace, transform_registry, hook_registry). (3) SF-1→SF-4: WorkflowConfig carries context_keys at workflow level; AskNode uses task: str (NOT prompt); context_text: str | None is supplementary inline context. (4) SF-5→SF-6: full Foundation REST and schema/validation endpoints with TypeScript response shapes. (5) SF-5→SF-7: in-process mutation hook interface (MutationEvent). (6) SF-7→SF-6: reference-check and tool-list APIs with TypeScript contracts. (7) SF-6→SF-7: indirect — editor saves through SF-5; SF-5 fires MutationEvent; SF-7 refreshes workflow_entity_refs; editor has no direct SF-7 write dependency.</div>

    <h3>Services</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Name</th><th>Kind</th><th>Description</th><th>Technology</th><th>Port</th><th>Journeys</th>
        </tr></thead>
        <tbody><tr>
            <td><code>SVC-11</code></td>
            <td><strong>Declarative Runtime API</strong></td>
            <td><code>service</code></td>
            <td>Additive iriai_compose.declarative entrypoints: load_workflow(), validate(), run(workflow, config: RuntimeConfig, *, inputs=None).</td>
            <td><code>Python 3.11+</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3</td>
        </tr><tr>
            <td><code>SVC-12</code></td>
            <td><strong>Workflow Loader</strong></td>
            <td><code>service</code></td>
            <td>Parses YAML against iriai_compose.schema models. Rejects stale fields.</td>
            <td><code>Python, PyYAML, Pydantic</code></td>
            <td>—</td>
            <td>J-1, J-3</td>
        </tr><tr>
            <td><code>SVC-13</code></td>
            <td><strong>Structural Validator</strong></td>
            <td><code>service</code></td>
            <td>Shared validation used by validate() and run(). Enforces nested containment, typed ports, stale-field rejection.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3</td>
        </tr><tr>
            <td><code>SVC-14</code></td>
            <td><strong>Recursive Graph Builder</strong></td>
            <td><code>service</code></td>
            <td>Builds container-local DAGs from phases[].nodes and phases[].children.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td>J-1, J-3</td>
        </tr><tr>
            <td><code>SVC-15</code></td>
            <td><strong>Phase Mode Runner</strong></td>
            <td><code>service</code></td>
            <td>Executes sequential/map/fold/loop phases recursively. Assembles hierarchical context via ContextVar: workflow→phase→actor→node.</td>
            <td><code>Python, asyncio</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-16</code></td>
            <td><strong>Node Executor</strong></td>
            <td><code>service</code></td>
            <td>Dispatches Ask (uses task field), Branch (condition_type/condition/paths), Plugin nodes only.</td>
            <td><code>Python, asyncio</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-17</code></td>
            <td><strong>Expression Sandbox</strong></td>
            <td><code>service</code></td>
            <td>AST allowlist evaluator for transform_fn, branch expressions, loop/map/fold mode configs.</td>
            <td><code>Python AST sandbox</code></td>
            <td>—</td>
            <td>J-1, J-3</td>
        </tr><tr>
            <td><code>SVC-18</code></td>
            <td><strong>Actor Hydrator</strong></td>
            <td><code>service</code></td>
            <td>Bridges agent/human actors to existing runtime ABCs. AgentRuntime.invoke() unchanged.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-19</code></td>
            <td><strong>Plugin Runtime</strong></td>
            <td><code>service</code></td>
            <td>Resolves WorkflowConfig.plugins to executable implementations via RuntimeConfig.</td>
            <td><code>Python</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-20</code></td>
            <td><strong>SF-1 Declarative Schema</strong></td>
            <td><code>external</code></td>
            <td>Module iriai_compose.schema. 23 valid exports. Phantom types that do NOT exist: MapNode, FoldNode, LoopNode, TransformRef, HookRef. Correct names: PhaseDefinition, WorkflowCostConfig/PhaseCostConfig/NodeCostConfig.</td>
            <td><code>Python 3.11+, Pydantic v2</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3</td>
        </tr><tr>
            <td><code>SVC-21</code></td>
            <td><strong>SF-3 Testing Framework</strong></td>
            <td><code>service</code></td>
            <td>Fluent mock runtimes. Consumes run(workflow, config: RuntimeConfig, *, inputs=None) — NOT stale signature. Node matching via ContextVar.</td>
            <td><code>Python 3.11+, pytest</code></td>
            <td>—</td>
            <td>J-1, J-3</td>
        </tr><tr>
            <td><code>SVC-22</code></td>
            <td><strong>SF-4 Workflow Migration</strong></td>
            <td><code>service</code></td>
            <td>Translates iriai-build-v2 → declarative YAML. Ask.prompt → AskNode.task. WorkflowConfig carries context_keys at workflow level.</td>
            <td><code>Python 3.11+</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-23</code></td>
            <td><strong>Existing iriai-compose Runtime Interfaces</strong></td>
            <td><code>external</code></td>
            <td>AgentRuntime, InteractionRuntime, ArtifactStore, SessionStore, ContextProvider, WorkflowRunner.parallel(), DefaultWorkflowRunner. No breaking ABI changes.</td>
            <td><code>Python ABCs</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-24</code></td>
            <td><strong>Compose Backend (SF-5)</strong></td>
            <td><code>service</code></td>
            <td>FastAPI at tools/compose/backend. Five foundation tables. GET /api/schema/workflow, workflow CRUD, validation. Fires synchronous in-process MutationEvent after commits. Never creates workflow_entity_refs rows.</td>
            <td><code>FastAPI, PostgreSQL, Alembic</code></td>
            <td>443</td>
            <td>J-2, J-5</td>
        </tr><tr>
            <td><code>SVC-25</code></td>
            <td><strong>Compose Frontend / Workflow Editor (SF-6)</strong></td>
            <td><code>frontend</code></td>
            <td>React 18 + TypeScript at tools/compose/frontend. React Flow canvas. Flat internal store; normalizes to canonical nested YAML. Blocks on GET /api/schema/workflow failure. No direct SF-7 write dependency.</td>
            <td><code>React 18, TypeScript, React Flow, Zustand, Vite</code></td>
            <td>443</td>
            <td>J-2, J-5</td>
        </tr><tr>
            <td><code>SVC-26</code></td>
            <td><strong>Libraries &amp; Registries (SF-7)</strong></td>
            <td><code>service</code></td>
            <td>Owns workflow_entity_refs via own Alembic migration. Subscribes to SF-5 mutation hooks. Exposes GET /api/{entity}/references/{id}, tool CRUD, PATCH actor-slots.</td>
            <td><code>FastAPI, PostgreSQL, Alembic, React 18, TypeScript</code></td>
            <td>443</td>
            <td>J-5</td>
        </tr><tr>
            <td><code>SVC-27</code></td>
            <td><strong>Workflow YAML Files</strong></td>
            <td><code>database</code></td>
            <td>Canonical SF-1 YAML with phases[].nodes, phases[].children, cross-phase edges at root.</td>
            <td><code>YAML</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3</td>
        </tr><tr>
            <td><code>SVC-28</code></td>
            <td><strong>iriai-build-v2 Reference Workflows</strong></td>
            <td><code>external</code></td>
            <td>Existing planning/develop/bugfix workflows as the representability litmus test.</td>
            <td><code>Python workflows</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr></tbody>
    </table>

    <h3>Connections</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>To</th><th>Protocol</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>CONN-16</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-17</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-18</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-19</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-20</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-21</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-22</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>in-process callback</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-23</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-24</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>event-driven via SF-5 hooks</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-25</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>file I/O</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-26</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-27</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-28</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-29</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-30</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-31</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>in-memory data</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-32</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-33</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-34</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>runtime integration</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-35</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-36</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-37</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-38</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>async function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-39</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>async function call</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-40</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTP + Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-41</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-42</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>file generation</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>API Endpoints</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Method</th><th>Path</th><th>Service</th><th>Description</th><th>Auth</th>
        </tr></thead>
        <tbody><tr>
            <td><code>API-33</code></td>
            <td><code>GET</code></td>
            <td><code>iriai_compose.declarative.load_workflow</code></td>
            <td><code></code></td>
            <td>Parse YAML into WorkflowConfig from iriai_compose.schema. Rejects phantom types and stale fields.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-34</code></td>
            <td><code>POST</code></td>
            <td><code>iriai_compose.declarative.validate</code></td>
            <td><code></code></td>
            <td>Structural validation without live runtimes. Used by SF-3 and SF-4.</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-35</code></td>
            <td><code>POST</code></td>
            <td><code>iriai_compose.declarative.run</code></td>
            <td><code></code></td>
            <td>Execute workflow. Canonical signature: run(workflow, config: RuntimeConfig, *, inputs=None). NOT run(yaml_path, runtime, workspace, transform_registry, hook_registry).</td>
            <td><code>N/A</code></td>
        </tr><tr>
            <td><code>API-36</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schema/workflow</code></td>
            <td><code></code></td>
            <td>Live JSON Schema from WorkflowConfig.model_json_schema(). Only runtime schema source. Editor blocks on failure.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-37</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows</code></td>
            <td><code></code></td>
            <td>Cursor-paginated workflow list.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-38</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows</code></td>
            <td><code></code></td>
            <td>Create workflow + v1. Fires MutationEvent created.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-39</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Get workflow. Returns 404 for cross-user.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-40</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Update workflow. Fires MutationEvent updated.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-41</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete. Fires MutationEvent soft_deleted → SF-7 removes workflow_entity_refs.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-42</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/{id}/versions</code></td>
            <td><code></code></td>
            <td>Append immutable version snapshot.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-43</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/{id}/validate</code></td>
            <td><code></code></td>
            <td>Server-side deep validation via iriai_compose.declarative.validate(). Two-tier validation with SF-6 client checks.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-44</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/import</code></td>
            <td><code></code></td>
            <td>Import YAML. Rejects malformed (no rows created). Schema-invalid creates with is_valid=false.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-45</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/{id}/export</code></td>
            <td><code></code></td>
            <td>Export as canonical nested YAML. Content-Type: text/yaml.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-46</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/starter-templates</code></td>
            <td><code></code></td>
            <td>Built-in templates from iriai-build-v2 reference workflows.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-47</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles</code></td>
            <td><code></code></td>
            <td>List roles. Used by SF-6 actor picker.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-48</code></td>
            <td><code>POST</code></td>
            <td><code>/api/roles</code></td>
            <td><code></code></td>
            <td>Create role. Fires MutationEvent.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-49</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Update role. Fires MutationEvent updated.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-50</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete role. Caller must pre-check GET /api/roles/references/{id}. Fires MutationEvent soft_deleted.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-51</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas</code></td>
            <td><code></code></td>
            <td>List output schemas.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-52</code></td>
            <td><code>POST</code></td>
            <td><code>/api/schemas</code></td>
            <td><code></code></td>
            <td>Create output schema.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-53</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/schemas/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete schema. Pre-check GET /api/schemas/references/{id} first.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-54</code></td>
            <td><code>GET</code></td>
            <td><code>/api/task-templates</code></td>
            <td><code></code></td>
            <td>List task templates. actor_slots present only after SF-7 migration.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-55</code></td>
            <td><code>POST</code></td>
            <td><code>/api/task-templates</code></td>
            <td><code></code></td>
            <td>Create task template.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-56</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/task-templates/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete template. Pre-check references first.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-57</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles/references/{id}</code></td>
            <td><code></code></td>
            <td>Pre-delete reference check for role. Queries workflow_entity_refs.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-58</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas/references/{id}</code></td>
            <td><code></code></td>
            <td>Pre-delete reference check for output schema.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-59</code></td>
            <td><code>GET</code></td>
            <td><code>/api/task-templates/references/{id}</code></td>
            <td><code></code></td>
            <td>Pre-delete reference check for custom task template.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-60</code></td>
            <td><code>GET</code></td>
            <td><code>/api/tools</code></td>
            <td><code></code></td>
            <td>List tools. Used by SF-6 ToolChecklistProps.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-61</code></td>
            <td><code>POST</code></td>
            <td><code>/api/tools</code></td>
            <td><code></code></td>
            <td>Create tool.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-62</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/tools/{id}</code></td>
            <td><code></code></td>
            <td>Update tool.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-63</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/tools/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete tool. Role-backed protection: 409 { blocking_roles } if any Role.tools[] references this tool name.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-64</code></td>
            <td><code>PATCH</code></td>
            <td><code>/api/task-templates/{id}/actor-slots</code></td>
            <td><code></code></td>
            <td>Persist actor slots. SF-7 adds actor_slots JSONB column via its own Alembic migration.</td>
            <td><code>JWT</code></td>
        </tr><tr>
            <td><code>API-65</code></td>
            <td><code>HOOK</code></td>
            <td><code>sf5.services.register_mutation_hook(entity_type, callback)</code></td>
            <td><code></code></td>
            <td>In-process hook registration. Fires after successful commit. Failures logged but don&#x27;t rollback.</td>
            <td><code>N/A (in-process)</code></td>
        </tr></tbody>
    </table>

    <h3>Call Paths</h3>
    <div class="call-path-block">
            <h4><code>CP-8</code>: Platform engineer runs canonical nested YAML through the declarative runtime.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;workflow_yaml&#x27;, &#x27;to_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;action&#x27;: &#x27;run(workflow_path, config, inputs=None)&#x27;, &#x27;description&#x27;: &#x27;Start from YAML file.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionResult&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;to_service&#x27;: &#x27;sf2_loader&#x27;, &#x27;action&#x27;: &#x27;load_workflow — imports from iriai_compose.schema&#x27;, &#x27;description&#x27;: &#x27;Parse WorkflowConfig.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;sf2_loader&#x27;, &#x27;to_service&#x27;: &#x27;sf2_validator&#x27;, &#x27;action&#x27;: &#x27;validate nested structure and stale-field rejection&#x27;, &#x27;description&#x27;: &#x27;Reject port_type, switch_function, stores, plugin_instances.&#x27;, &#x27;returns&#x27;: &#x27;[] or field-path errors&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;to_service&#x27;: &#x27;sf2_graph_builder&#x27;, &#x27;action&#x27;: &#x27;build workflow graph&#x27;, &#x27;description&#x27;: &#x27;Build DAGs from nested phases.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionGraph&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;sf2_graph_builder&#x27;, &#x27;to_service&#x27;: &#x27;sf2_phase_runner&#x27;, &#x27;action&#x27;: &#x27;execute top-level phases recursively&#x27;, &#x27;description&#x27;: &#x27;Context assembled: WorkflowConfig.context_keys → phase → actor → node.&#x27;, &#x27;returns&#x27;: &#x27;phase outputs&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;sf2_phase_runner&#x27;, &#x27;to_service&#x27;: &#x27;sf2_node_executor&#x27;, &#x27;action&#x27;: &#x27;dispatch Ask/Branch/Plugin — AskNode.task not prompt&#x27;, &#x27;description&#x27;: &#x27;Three atomic types only.&#x27;, &#x27;returns&#x27;: &#x27;node outputs&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;sf2_node_executor&#x27;, &#x27;to_service&#x27;: &#x27;sf2_actor_adapter&#x27;, &#x27;action&#x27;: &#x27;hydrate actor_type: agent|human&#x27;, &#x27;description&#x27;: &#x27;AgentRuntime.invoke() unchanged.&#x27;, &#x27;returns&#x27;: &#x27;runtime output&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;sf2_node_executor&#x27;, &#x27;to_service&#x27;: &#x27;sf2_expression_runtime&#x27;, &#x27;action&#x27;: &#x27;evaluate transforms and branch conditions&#x27;, &#x27;description&#x27;: &#x27;AST sandbox.&#x27;, &#x27;returns&#x27;: &#x27;transformed payloads&#x27;}</li><li>{&#x27;sequence&#x27;: 9, &#x27;from_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;to_service&#x27;: &#x27;imperative_runtime&#x27;, &#x27;action&#x27;: &#x27;assemble hierarchical context, collect observability&#x27;, &#x27;description&#x27;: &#x27;ExecutionResult with history and phase_metrics.&#x27;, &#x27;returns&#x27;: &#x27;ExecutionResult&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-9</code>: Backend and editor share live schema from iriai_compose.schema.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/schema/workflow&#x27;, &#x27;description&#x27;: &#x27;Blocks on failure — no static fallback.&#x27;, &#x27;returns&#x27;: &#x27;JSON Schema&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;composer_backend&#x27;, &#x27;to_service&#x27;: &#x27;sf1_schema&#x27;, &#x27;action&#x27;: &#x27;WorkflowConfig.model_json_schema()&#x27;, &#x27;description&#x27;: &#x27;Same models SF-2 uses at runtime.&#x27;, &#x27;returns&#x27;: &#x27;JSON Schema&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_backend&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows/{id}/validate with canonical nested YAML&#x27;, &#x27;description&#x27;: &#x27;Normalized from flat canvas before submission.&#x27;, &#x27;returns&#x27;: &#x27;ValidationResponse&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;composer_backend&#x27;, &#x27;to_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;action&#x27;: &#x27;iriai_compose.declarative.validate(workflow)&#x27;, &#x27;description&#x27;: &#x27;Same nested contract.&#x27;, &#x27;returns&#x27;: &#x27;list[ValidationError]&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-10</code>: Stale YAML fails fast before execution.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;workflow_yaml&#x27;, &#x27;to_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;action&#x27;: &#x27;validate(workflow_path)&#x27;, &#x27;description&#x27;: &#x27;Stale YAML submitted.&#x27;, &#x27;returns&#x27;: &#x27;list[ValidationError]&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;sf2_public_api&#x27;, &#x27;to_service&#x27;: &#x27;sf2_loader&#x27;, &#x27;action&#x27;: &#x27;parse raw YAML fields&#x27;, &#x27;description&#x27;: &#x27;Expose raw field names for precise errors.&#x27;, &#x27;returns&#x27;: &#x27;parsed document&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;sf2_loader&#x27;, &#x27;to_service&#x27;: &#x27;sf2_validator&#x27;, &#x27;action&#x27;: &#x27;reject stale fields&#x27;, &#x27;description&#x27;: &#x27;port_type, switch_function, stores, plugin_instances, interaction alias blocked.&#x27;, &#x27;returns&#x27;: &#x27;field-path errors&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-11</code>: Editor blocks rather than falling back to stale schema.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/schema/workflow&#x27;, &#x27;description&#x27;: &#x27;Boot schema request.&#x27;, &#x27;returns&#x27;: &#x27;HTTP success or failure&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;composer_backend&#x27;, &#x27;to_service&#x27;: &#x27;composer_editor&#x27;, &#x27;action&#x27;: &#x27;return failure status&#x27;, &#x27;description&#x27;: &#x27;Surfaces unavailability.&#x27;, &#x27;returns&#x27;: &#x27;error response&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_editor&#x27;, &#x27;action&#x27;: &#x27;render schema-unavailable blocking state&#x27;, &#x27;description&#x27;: &#x27;No fallback to workflow-schema.json.&#x27;, &#x27;returns&#x27;: &#x27;blocked editor state&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-12</code>: SF-7 reference check prevents deletion of referenced library entities.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;sf7_libraries&#x27;, &#x27;action&#x27;: &#x27;GET /api/{entity}/references/{id}&#x27;, &#x27;description&#x27;: &#x27;Pre-fetch ReferenceCheckResult before rendering dialog.&#x27;, &#x27;returns&#x27;: &#x27;ReferenceCheckResult { can_delete, references, reference_count }&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_editor&#x27;, &#x27;action&#x27;: &#x27;render EntityDeleteDialog&#x27;, &#x27;description&#x27;: &#x27;Blocking message if can_delete: false.&#x27;, &#x27;returns&#x27;: &#x27;user decision&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_backend&#x27;, &#x27;action&#x27;: &#x27;DELETE /api/{entity}/{id}&#x27;, &#x27;description&#x27;: &#x27;Only if can_delete: true.&#x27;, &#x27;returns&#x27;: &#x27;204 No Content&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;composer_backend&#x27;, &#x27;to_service&#x27;: &#x27;sf7_libraries&#x27;, &#x27;action&#x27;: &#x27;MutationEvent { event: soft_deleted } via hook callback&#x27;, &#x27;description&#x27;: &#x27;SF-7 removes workflow_entity_refs rows.&#x27;, &#x27;returns&#x27;: &#x27;void&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-13</code>: Editor has no direct SF-7 write dependency — refresh flows through SF-5 mutation hooks.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;composer_editor&#x27;, &#x27;to_service&#x27;: &#x27;composer_backend&#x27;, &#x27;action&#x27;: &#x27;PUT /api/workflows/{id} with canonical nested YAML&#x27;, &#x27;description&#x27;: &#x27;Flat canvas state serialized to nested YAML.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowRecord&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;composer_backend&#x27;, &#x27;to_service&#x27;: &#x27;composer_backend&#x27;, &#x27;action&#x27;: &#x27;persist to PostgreSQL&#x27;, &#x27;description&#x27;: &#x27;Update committed.&#x27;, &#x27;returns&#x27;: &#x27;committed row&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;composer_backend&#x27;, &#x27;to_service&#x27;: &#x27;sf7_libraries&#x27;, &#x27;action&#x27;: &#x27;MutationEvent { entity_type: workflow, event: updated } via hook callback&#x27;, &#x27;description&#x27;: &#x27;SF-7 diffs entity_refs, upserts/deletes workflow_entity_refs. Hook failure logged but does not rollback.&#x27;, &#x27;returns&#x27;: &#x27;void&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;sf7_libraries&#x27;, &#x27;to_service&#x27;: &#x27;composer_editor&#x27;, &#x27;action&#x27;: &#x27;next GET /api/{entity}/references/{id} returns fresh data&#x27;, &#x27;description&#x27;: &#x27;Reference index current for subsequent delete preflights.&#x27;, &#x27;returns&#x27;: &#x27;ReferenceCheckResult (current)&#x27;}</li></ol>
        </div>

    <h3>Entities</h3>
    <div class="entity-block">
            <h4><code>ENT-29</code>: WorkflowConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>schema_version</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>workflow_version</code></td>
                        <td><code>int</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str] | None</code></td>
                        <td>Top of workflow→phase→actor→node hierarchy. SF-4 migration must preserve this.</td>
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
                        <td></td>
                    </tr><tr>
                        <td><code>templates</code></td>
                        <td><code>dict[str, TemplateDefinition] | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>plugins</code></td>
                        <td><code>dict[str, PluginInterface] | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>types</code></td>
                        <td><code>dict[str, dict] | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>cost_config</code></td>
                        <td><code>WorkflowCostConfig | None</code></td>
                        <td>NOT dict. NOT CostConfig. Type is WorkflowCostConfig.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-30</code>: AskNode</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>type</code></td>
                        <td><code>Literal[&#x27;ask&#x27;]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>actor_ref</code></td>
                        <td><code>str</code></td>
                        <td>Key into WorkflowConfig.actors.</td>
                    </tr><tr>
                        <td><code>task</code></td>
                        <td><code>str</code></td>
                        <td>Primary prompt text. NOT named prompt. Migration: imperative Ask.prompt → declarative AskNode.task.</td>
                    </tr><tr>
                        <td><code>context_text</code></td>
                        <td><code>str | None</code></td>
                        <td>Supplementary inline context. Separate from task.</td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str] | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, WorkflowInputDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>dict[str, WorkflowOutputDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>hooks</code></td>
                        <td><code>dict[str, WorkflowOutputDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>artifact_key</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>cost</code></td>
                        <td><code>NodeCostConfig | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-31</code>: WorkflowCostConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>budget_usd</code></td>
                        <td><code>float | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>alert_threshold_usd</code></td>
                        <td><code>float | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>track_tokens</code></td>
                        <td><code>bool</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-32</code>: PhaseCostConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>budget_usd</code></td>
                        <td><code>float | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>alert_threshold_usd</code></td>
                        <td><code>float | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-33</code>: NodeCostConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>budget_usd</code></td>
                        <td><code>float | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>max_tokens</code></td>
                        <td><code>int | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-34</code>: MutationEvent</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>entity_type</code></td>
                        <td><code>Literal[&#x27;workflow&#x27;,&#x27;role&#x27;,&#x27;output_schema&#x27;,&#x27;custom_task_template&#x27;]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>entity_id</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>event</code></td>
                        <td><code>Literal[&#x27;created&#x27;,&#x27;updated&#x27;,&#x27;soft_deleted&#x27;,&#x27;restored&#x27;]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>timestamp</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-35</code>: PhaseDefinition</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>mode</code></td>
                        <td><code>&#x27;sequential&#x27;|&#x27;map&#x27;|&#x27;fold&#x27;|&#x27;loop&#x27;</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>mode_config</code></td>
                        <td><code>ModeConfig | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>inputs</code></td>
                        <td><code>dict[str, WorkflowInputDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>outputs</code></td>
                        <td><code>dict[str, WorkflowOutputDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>hooks</code></td>
                        <td><code>dict[str, WorkflowOutputDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>nodes</code></td>
                        <td><code>list[NodeDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>children</code></td>
                        <td><code>list[PhaseDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>edges</code></td>
                        <td><code>list[EdgeDefinition]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>context_keys</code></td>
                        <td><code>list[str]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>cost</code></td>
                        <td><code>PhaseCostConfig | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>dict | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-36</code>: EdgeDefinition</h4>
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
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-37</code>: RuntimeConfig</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>agent_runtime</code></td>
                        <td><code>AgentRuntime</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>interaction_runtimes</code></td>
                        <td><code>dict[str, InteractionRuntime]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>artifacts</code></td>
                        <td><code>ArtifactStore</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>sessions</code></td>
                        <td><code>SessionStore | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>context_provider</code></td>
                        <td><code>ContextProvider</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>plugin_registry</code></td>
                        <td><code>dict[str, Any] | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>workspace</code></td>
                        <td><code>Workspace | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>feature</code></td>
                        <td><code>Feature | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>services</code></td>
                        <td><code>dict[str, Any] | None</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-38</code>: ExecutionResult</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>success</code></td>
                        <td><code>bool</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>output</code></td>
                        <td><code>Any</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>error</code></td>
                        <td><code>str | None</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>trace</code></td>
                        <td><code>dict[str, Any]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>history</code></td>
                        <td><code>list[ExecutionHistory]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>phase_metrics</code></td>
                        <td><code>dict[str, Any]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>cost_summary</code></td>
                        <td><code>dict[str, Any]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>hook_warnings</code></td>
                        <td><code>list[str]</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>duration_ms</code></td>
                        <td><code>float</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-39</code>: ValidationError</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>field_path</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>message</code></td>
                        <td><code>str</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>severity</code></td>
                        <td><code>&#x27;error&#x27;|&#x27;warning&#x27;</code></td>
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
            <td><code>ER-32</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>workflow_cost_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-33</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>phase_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-34</code></td>
            <td><code>workflow_config</code></td>
            <td></td>
            <td><code>edge_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-35</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>phase_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-36</code></td>
            <td><code>phase_definition</code></td>
            <td></td>
            <td><code>phase_cost_config</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-37</code></td>
            <td><code>ask_node</code></td>
            <td></td>
            <td><code>actor_definition</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-38</code></td>
            <td><code>mutation_event</code></td>
            <td></td>
            <td><code>validation_error</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-39</code></td>
            <td><code>execution_result</code></td>
            <td></td>
            <td><code>validation_error</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>Decisions</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Decision</th></tr></thead>
        <tbody><tr>
            <td><code>D-43</code></td>
            <td>D-SF2-11: iriai_compose.schema is the canonical module path. Phantom types that do NOT exist: MapNode, FoldNode, LoopNode, TransformRef, HookRef. Correct names: PhaseDefinition, WorkflowCostConfig/PhaseCostConfig/NodeCostConfig. [decision: D-GR-41]</td>
        </tr><tr>
            <td><code>D-44</code></td>
            <td>D-SF2-12: Canonical run() signature is run(workflow: WorkflowConfig|Path|str, config: RuntimeConfig, *, inputs: dict|None = None). Stale signature run(yaml_path, runtime, workspace, transform_registry, hook_registry) is invalid. [decision: D-GR-41]</td>
        </tr><tr>
            <td><code>D-45</code></td>
            <td>D-SF2-13: WorkflowConfig carries context_keys: list[str]|None at workflow level completing the top layer of the workflow→phase→actor→node hierarchy. [decision: D-GR-41]</td>
        </tr><tr>
            <td><code>D-46</code></td>
            <td>D-SF2-14: AskNode uses task: str as the primary prompt field NOT prompt. context_text: str|None is supplementary. Migration: Ask(actor=x, prompt=&#x27;...&#x27;) → AskNode(type: ask, actor_ref: &#x27;x&#x27;, task: &#x27;...&#x27;). [decision: D-GR-41] [code: iriai-compose/iriai_compose/tasks.py:49-54]</td>
        </tr><tr>
            <td><code>D-47</code></td>
            <td>D-SF2-15: SF-5 mutation hook interface is the only write path from editor to reference index. Editor has no direct SF-7 write dependency. [decision: D-GR-41]</td>
        </tr><tr>
            <td><code>D-48</code></td>
            <td>D-SF2-16: SF-5 mutation hooks fire synchronously in-process after successful DB commit. Callback failures logged but do not rollback. [decision: D-GR-37]</td>
        </tr></tbody>
    </table>

    <h3>Risks</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Risk</th></tr></thead>
        <tbody><tr>
            <td><code>RISK-18</code></td>
            <td>RISK-7 (medium): SF-7 mutation hook subscriber fails — reference index becomes stale without transaction rollback. Mitigation: log all hook failures; provide re-index endpoint. [decision: D-GR-37]</td>
        </tr><tr>
            <td><code>RISK-19</code></td>
            <td>RISK-8 (low): Library entity deleted between editor page load and save causes stale YAML reference. Mitigation: POST /api/workflows/{id}/validate runs SF-2 validate() which catches stale refs before persistence.</td>
        </tr></tbody>
    </table>
</section>
<hr/>
