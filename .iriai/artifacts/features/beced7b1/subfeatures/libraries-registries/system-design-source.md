<!-- SF: libraries-registries -->
<section id="sf-libraries-registries" class="subfeature-section">
    <h2>SF-7 Libraries &amp; Registries - System Design</h2>
    <div class="provenance">Subfeature: <code>libraries-registries</code></div>

    <h3>Overview</h3>
    <div class="overview-text">SF-7 adds four library pages (Roles, Output Schemas, Task Templates, Tools) and three picker components that integrate with the SF-6 workflow editor across a React 19 frontend and FastAPI backend. The sidebar contains exactly 5 entity-type folders: Workflows, Roles, Schemas, Templates, Tools per PRD REQ-2.

SF-7 is the exclusive owner of three follow-on Alembic migrations that extend SF-5&#x27;s 5-table foundation: `workflow_entity_refs` (reference index), `tools` (custom tool registry), and `actor_slots` (task-template actor-slot definitions). SF-5&#x27;s five foundation tables — `workflows`, `workflow_versions`, `roles`, `output_schemas`, `custom_task_templates` — are not modified by SF-7. SF-5 exposes a post-commit mutation hook interface (REQ-18) that fires typed events (`created`, `updated`, `soft_deleted`, `restored`) on all four foundation entity types after each successful database commit. SF-7 subscribes to the `Workflow` hook slot at application startup (FastAPI lifespan event), registering `refresh_entity_refs(workflow_id, user_id)` for `updated` events and `purge_entity_refs(workflow_id)` for `soft_deleted` events. The callbacks maintain the `workflow_entity_refs` materialized index in a separate SF-7-owned database transaction, enabling O(1) delete preflight checks — a single indexed lookup per entity ID regardless of workflow count.

`GET /api/{entity}/references/{id}` reads from `workflow_entity_refs`, and library delete dialogs plus DELETE guards use those indexed rows instead of delete-time YAML scans. To guard against stale-index drift when a post-commit callback fails (SF-5 has already committed; the SF-7 transaction fails independently), an APScheduler reconciliation job runs periodically within compose-backend and can also be triggered manually via `POST /api/admin/reconcile-entity-refs`. The reconciliation job performs a full resync of `workflow_entity_refs` against actual workflow `yaml_content`, providing a deterministic recovery path for any missed hook events. The Tool Library (REQ-4) combines hardcoded built-in Claude tools with user-registered custom tools from the `tools` table, while tool delete protection remains a Role `tools` array check because tools are referenced by roles rather than by workflows. `actor_slots` rows extend `custom_task_templates` with named slot definitions so that actor-slot assignments survive reloads and remain reusable across workflows.</div>

    <h3>Services</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Name</th><th>Kind</th><th>Description</th><th>Technology</th><th>Port</th><th>Journeys</th>
        </tr></thead>
        <tbody><tr>
            <td><code>SVC-102</code></td>
            <td><strong>compose-frontend</strong></td>
            <td><code>frontend</code></td>
            <td>React SPA hosted at tools/compose/frontend that contains the SF-7 library pages and picker components alongside the SF-6 workflow editor. SF-7 additions live under features/libraries/ and include 4 library pages (Roles, Schemas, Templates, Tools), 3 pickers, a promotion dialog, shared hooks, and delete dialogs that preflight GET /api/{entity}/references/{id} before destructive actions.</td>
            <td><code>React 19, Vite, React Router, Zustand, React Flow, CodeMirror 6</code></td>
            <td>—</td>
            <td>J-5, J-23, J-24, J-25, J-27, J-28, J-29</td>
        </tr><tr>
            <td><code>SVC-103</code></td>
            <td><strong>Libraries Feature (SF-7)</strong></td>
            <td><code>frontend</code></td>
            <td>Sub-module of compose-frontend providing the 4 library pages (RolesLibraryPage, SchemasLibraryPage, TemplatesLibraryPage, ToolsLibraryPage), 3 picker components (RolePicker, SchemaPicker, TemplatePicker), ToolChecklistGrid (consumed by Role editor), PromotionDialog, EntityDeleteDialog, and shared hooks. Delete flows call the dedicated references endpoint and render a blocking dialog before DELETE when workflows still reference the selected role, schema, or template.</td>
            <td><code>React 19, Zustand, TanStack Query</code></td>
            <td>—</td>
            <td>J-5, J-23, J-24, J-25, J-27, J-28, J-29</td>
        </tr><tr>
            <td><code>SVC-104</code></td>
            <td><strong>Editor Feature (SF-6)</strong></td>
            <td><code>frontend</code></td>
            <td>SF-6 workflow editor sub-module containing EditorCanvas, AskNode, BranchNode, inspectors, and palette. Hosts pickers from libraries-feature inside inspectors and emits promotion callbacks (onPromoteRole, onPromoteSchema, onSaveTemplate). When a library role, schema, or template is attached to workflow content, the editor persists the library UUID in workflow data so that SF-5&#x27;s workflow save fires the post-commit hook that SF-7 uses to refresh workflow_entity_refs.</td>
            <td><code>React Flow, editorStore (Zustand factory)</code></td>
            <td>—</td>
            <td>J-5, J-16, J-18, J-20, J-27, J-29</td>
        </tr><tr>
            <td><code>SVC-105</code></td>
            <td><strong>compose-backend</strong></td>
            <td><code>service</code></td>
            <td>FastAPI backend at tools/compose/backend. SF-7 adds library entity CRUD extensions (Roles, Schemas, Templates, Tools, ActorSlots), duplicate name validation, JSON Schema server-side validation (Draft 2020-12), idempotent inline-to-library promotion, and reference-checking for delete dialogs and DELETE guards. SF-7&#x27;s Alembic migrations create `workflow_entity_refs`, `tools`, and `actor_slots` as downstream extensions after SF-5&#x27;s five-table foundation; those migrations run in SF-7&#x27;s own revision chain within the shared alembic_version_compose history. At application startup (FastAPI lifespan event), SF-7 subscribes to SF-5&#x27;s REQ-18 post-commit mutation hook interface by registering `refresh_entity_refs(workflow_id, user_id)` against the Workflow `updated` slot and `purge_entity_refs(workflow_id)` against the Workflow `soft_deleted` slot. The callbacks execute synchronously in-process but in separate database transactions from SF-5&#x27;s commit; if a callback fails, SF-5&#x27;s transaction is already committed and the workflow save is not rolled back. To recover from stale-index drift caused by callback failures, an APScheduler BackgroundScheduler job (`reconcile_entity_refs_all`) runs on a configurable interval (default: 15 minutes) and can be triggered on-demand via POST /api/admin/reconcile-entity-refs. GET /api/{entity}/references/{id} and role/schema/template DELETE endpoints query workflow_entity_refs. Tool delete protection remains a role scan because custom tools are referenced from Role.tools arrays, not directly from workflows.</td>
            <td><code>FastAPI, SQLAlchemy async, asyncpg, Alembic, APScheduler, homelocal-auth, jsonschema</code></td>
            <td>—</td>
            <td>J-5, J-23, J-24, J-27, J-28, J-29</td>
        </tr><tr>
            <td><code>SVC-106</code></td>
            <td><strong>compose-db</strong></td>
            <td><code>database</code></td>
            <td>PostgreSQL 15+ database managed by Alembic (alembic_version_compose table). SF-5 owns exactly five foundation tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`. SF-7 extends the schema with three additional tables via SF-7-owned Alembic migrations: `workflow_entity_refs` (materialized reference index for O(1) delete preflight; maintained by SF-5 post-commit mutation hook callbacks registered by SF-7), `tools` (user-registered custom tools; built-in tools remain backend constants and are never stored), and `actor_slots` (named actor-slot definitions per custom_task_templates row, enabling reusable slot assignments across workflows). SF-7 migrations depend on SF-5&#x27;s initial migration revision and must run after it.</td>
            <td><code>PostgreSQL 15+, Alembic migrations</code></td>
            <td>—</td>
            <td>J-5, J-23, J-24, J-27, J-28, J-29</td>
        </tr></tbody>
    </table>

    <h3>Connections</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>To</th><th>Protocol</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>CONN-161</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST/HTTPS - JWT Bearer</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-162</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>SQL / asyncpg</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-163</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>React import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-164</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Callback / React prop</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-165</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST/HTTPS - JWT Bearer</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-166</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>REST/HTTPS - JWT Bearer</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>API Endpoints</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Method</th><th>Path</th><th>Service</th><th>Description</th><th>Auth</th>
        </tr></thead>
        <tbody><tr>
            <td><code>API-166</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles/check-name</code></td>
            <td><code></code></td>
            <td>Check role name uniqueness for the authenticated user.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-167</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas/check-name</code></td>
            <td><code></code></td>
            <td>Check output schema name uniqueness for the authenticated user.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-168</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates/check-name</code></td>
            <td><code></code></td>
            <td>Check task template name uniqueness for the authenticated user.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-169</code></td>
            <td><code>GET</code></td>
            <td><code>/api/tools/check-name</code></td>
            <td><code></code></td>
            <td>Check tool name uniqueness for the authenticated user. Query param: ?name=mcp__github__create_issue.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-170</code></td>
            <td><code>GET</code></td>
            <td><code>/api/tools</code></td>
            <td><code></code></td>
            <td>List all tools: hardcoded built-in tools (source=built_in, no id) merged with user-registered custom tools from the SF-7-owned tools table (source=mcp or custom_function, with id). Used by both ToolsLibraryPage and the Role editor&#x27;s ToolChecklistGrid (REQ-4).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-171</code></td>
            <td><code>GET</code></td>
            <td><code>/api/tools/{id}</code></td>
            <td><code></code></td>
            <td>Get a single custom tool by UUID. Returns 404 for non-existent or deleted tools.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-172</code></td>
            <td><code>GET</code></td>
            <td><code>/api/{entity}/references/{id}</code></td>
            <td><code></code></td>
            <td>Return total and referenced_by[] for a role, schema, or template by querying the SF-7-owned workflow_entity_refs table joined to active SF-5 workflow rows. Single indexed lookup — O(1) per entity ID. Used by EntityDeleteDialog before delete and by DELETE handlers for re-checks. Entity must be one of: roles, schemas, templates.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-173</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>SF-5-owned endpoint. Persists workflow YAML containing library role/schema/template UUIDs. After SF-5 commits the workflow update, SF-5&#x27;s Workflow post-commit hook fires the `updated` event; SF-7&#x27;s registered refresh_entity_refs callback re-fetches the workflow&#x27;s yaml_content and replaces that workflow&#x27;s workflow_entity_refs rows in a separate SF-7 transaction.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-174</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>SF-5-owned endpoint. Soft-deletes a workflow. After SF-5 commits the soft-delete, SF-5&#x27;s Workflow post-commit hook fires the `soft_deleted` event; SF-7&#x27;s registered purge_entity_refs callback removes that workflow&#x27;s workflow_entity_refs rows in a separate SF-7 transaction.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-175</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete a role with a workflow_entity_refs re-check. Returns 409 with referencing workflow names if any active workflow still references the role UUID in the SF-7-owned workflow_entity_refs table (O(1) indexed lookup).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-176</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/schemas/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete an output schema with a workflow_entity_refs re-check. Blocked if referenced by any active workflow.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-177</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/templates/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete a task template with a workflow_entity_refs re-check. Blocked if referenced by any active workflow.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-178</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/tools/{id}</code></td>
            <td><code></code></td>
            <td>Soft-delete a custom tool from the SF-7-owned tools table with role-reference checking. Scans non-deleted Roles&#x27; tools JSON arrays for the tool&#x27;s name and returns 409 with referencing role names if found. Built-in tools cannot be deleted because they have no id.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-179</code></td>
            <td><code>POST</code></td>
            <td><code>/api/roles</code></td>
            <td><code></code></td>
            <td>Create a new role with optional idempotent promotion flag. Returns the existing record on duplicate name when promote=true. Writes to SF-5-owned roles table.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-180</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Update an existing role in the SF-5-owned roles table, including tool selections sourced from GET /api/tools.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-181</code></td>
            <td><code>POST</code></td>
            <td><code>/api/schemas</code></td>
            <td><code></code></td>
            <td>Create a new output schema in the SF-5-owned output_schemas table. Validates json_schema against JSON Schema Draft 2020-12 before persisting.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-182</code></td>
            <td><code>POST</code></td>
            <td><code>/api/tools</code></td>
            <td><code></code></td>
            <td>Register a new custom tool in the SF-7-owned tools table. Returns 409 on duplicate name. Name max 200 chars, description max 500 chars, input_schema max 256KB (REQ-8).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-183</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/tools/{id}</code></td>
            <td><code></code></td>
            <td>Update a custom tool in the SF-7-owned tools table. Same body fields as POST. Returns 404 for non-existent or deleted tools. If the name changes, the UI warns that roles using the old string must be updated manually.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-184</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates/{id}/actor-slots</code></td>
            <td><code></code></td>
            <td>List all actor slot definitions for a task template, reading from the SF-7-owned actor_slots table. Returns each slot_key with its current default_role_id and role display name.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-185</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/templates/{id}/actor-slots/{slot_key}</code></td>
            <td><code></code></td>
            <td>Upsert a named actor slot definition for a task template in the SF-7-owned actor_slots table. slot_key must be unique per template. default_role_id null defines an unassigned slot. Used to persist actor-slot definitions so they survive reloads and remain reusable across workflow instances.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-186</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/templates/{id}/actor-slots/{slot_key}</code></td>
            <td><code></code></td>
            <td>Remove a named actor slot definition from the SF-7-owned actor_slots table. Returns 204. Does not block on role references because slot definitions are template-level metadata, not reference-tracked entities.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-187</code></td>
            <td><code>POST</code></td>
            <td><code>/api/admin/reconcile-entity-refs</code></td>
            <td><code></code></td>
            <td>Manually trigger a full reconciliation of the workflow_entity_refs materialized index against actual workflow yaml_content. Invokes the same reconcile_entity_refs_all() function used by the APScheduler periodic job. For each non-deleted workflow, re-parses yaml_content, computes the diff against current workflow_entity_refs rows, and atomically reconciles mismatches (DELETE stale rows + INSERT missing rows in a single transaction per workflow). Idempotent — safe to run at any time. Returns a summary of rows added, rows removed, and workflows scanned. Intended for operator use when post-commit hook failures are suspected.</td>
            <td><code>JWT Bearer</code></td>
        </tr></tbody>
    </table>

    <h3>Call Paths</h3>
    <div class="call-path-block">
            <h4><code>CP-40</code>: User creates a new library role, immediately uses it in the workflow editor, and saves the workflow. The workflow save triggers SF-5&#x27;s post-commit Workflow updated event; SF-7&#x27;s registered refresh_entity_refs callback rebuilds the materialized workflow_entity_refs rows so later delete checks are O(1) table lookups rather than YAML scans.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;Navigate to /roles, click &#x27;+ New Role&#x27;&quot;, &#x27;description&#x27;: &#x27;User opens the Roles library page and triggers the RoleEditorView render.&#x27;, &#x27;returns&#x27;: &#x27;RoleEditorView renders&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/roles/check-name?name=test-pm&#x27;, &#x27;description&#x27;: &#x27;Frontend validates role name uniqueness before allowing save.&#x27;, &#x27;returns&#x27;: &#x27;{ available: true }&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;POST /api/roles { name, model, system_prompt, tools, metadata }&#x27;, &#x27;description&#x27;: &#x27;Create the new role record in the SF-5-owned roles table.&#x27;, &#x27;returns&#x27;: &#x27;201 { id, name, ... }&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &#x27;Invalidate library list and picker caches&#x27;, &#x27;description&#x27;: &#x27;Role appears in both the library grid and RolePicker with its canonical UUID and display name.&#x27;, &#x27;returns&#x27;: &#x27;UI updated with new role option&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;editor-feature&#x27;, &#x27;action&#x27;: &#x27;Select the new role from RolePicker in AskInspector&#x27;, &#x27;description&#x27;: &#x27;The editor stores the role UUID on the Ask node while showing the role name in the UI.&#x27;, &#x27;returns&#x27;: &#x27;Node now references the library role&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;editor-feature&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;PUT /api/workflows/{id} { yaml_content: ...roleId... }&#x27;, &#x27;description&#x27;: &#x27;SF-5 workflow save endpoint persists the role UUID inside the serialized workflow definition and commits the transaction.&#x27;, &#x27;returns&#x27;: &#x27;200 { id, updated_at }&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;SF-5 post-commit hook fires Workflow updated event; SF-7 refresh_entity_refs callback re-fetches yaml_content and executes: DELETE FROM workflow_entity_refs WHERE workflow_id=:wid; INSERT INTO workflow_entity_refs (role/schema/template refs parsed from yaml)&#x27;, &#x27;description&#x27;: &quot;SF-7&#x27;s registered refresh_entity_refs callback runs in its own database transaction after SF-5&#x27;s commit. It re-fetches the workflow&#x27;s yaml_content, parses entity UUID references, and atomically replaces the materialized reference rows. Result is immediately available for O(1) delete preflight lookups.&quot;, &#x27;returns&#x27;: &#x27;Updated workflow_entity_refs rows (separate transaction)&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;editor-feature&#x27;, &#x27;action&#x27;: &#x27;Return workflow save success (step 6 already returned 200; ref refresh is post-commit)&#x27;, &#x27;description&#x27;: &quot;Editor save completes with 200 from SF-5. The workflow_entity_refs rows are refreshed asynchronously in SF-7&#x27;s post-commit callback.&quot;, &#x27;returns&#x27;: &#x27;200 workflow saved&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-41</code>: User promotes an inline role defined within a workflow node to the shared Roles library. The node switches from inline role data to the canonical library role UUID, and the next workflow save triggers SF-5&#x27;s Workflow updated post-commit event for SF-7 to refresh workflow_entity_refs.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;editor-feature&#x27;, &#x27;to_service&#x27;: &#x27;libraries-feature&#x27;, &#x27;action&#x27;: &#x27;Open PromotionDialog with inline role data&#x27;, &#x27;description&#x27;: &#x27;Workflow editor emits onPromoteRole callback; PromotionDialog renders with pre-filled inline role fields.&#x27;, &#x27;returns&#x27;: &#x27;PromotionDialog renders&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/roles/check-name?name=promoted-pm&#x27;, &#x27;description&#x27;: &#x27;Validate proposed library name is not already taken.&#x27;, &#x27;returns&#x27;: &#x27;{ available: true }&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;POST /api/roles { ...inlineRole, promote: true }&#x27;, &#x27;description&#x27;: &#x27;Idempotent promotion. Backend returns the existing record if the same role name was already promoted. Writes to SF-5-owned roles table.&#x27;, &#x27;returns&#x27;: &#x27;201 { id, name, ... } or 200 existing record&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;libraries-feature&#x27;, &#x27;to_service&#x27;: &#x27;editor-feature&#x27;, &#x27;action&#x27;: &#x27;onSave callback -&gt; updateNodeData(nodeId, { actor: role.id, actorLabel: role.name, inline_role: undefined })&#x27;, &#x27;description&#x27;: &#x27;Node in the workflow editor switches from inline role content to the canonical library role UUID while keeping the display label.&#x27;, &#x27;returns&#x27;: &#x27;Node reference updated in workflow graph&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;editor-feature&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;PUT /api/workflows/{id} { yaml_content: ...roleId... }&#x27;, &#x27;description&#x27;: &#x27;SF-5 workflow save endpoint persists the promoted role UUID in the serialized workflow definition and commits.&#x27;, &#x27;returns&#x27;: &#x27;200 { id, updated_at }&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;SF-5 post-commit hook fires Workflow updated event; SF-7 refresh_entity_refs callback re-fetches yaml_content and atomically replaces workflow_entity_refs rows in a separate transaction&#x27;, &#x27;description&#x27;: &quot;SF-7&#x27;s refresh_entity_refs callback runs after SF-5&#x27;s commit. It re-fetches the workflow YAML, parses entity refs including the newly promoted role UUID, and rebuilds the workflow&#x27;s materialized reference rows.&quot;, &#x27;returns&#x27;: &#x27;Updated workflow_entity_refs rows (separate transaction)&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;editor-feature&#x27;, &#x27;action&#x27;: &#x27;Return workflow save success (step 5 already returned 200; ref refresh is post-commit)&#x27;, &#x27;description&#x27;: &#x27;The node now references the reusable library role. Future delete checks resolve from workflow_entity_refs via O(1) indexed lookup.&#x27;, &#x27;returns&#x27;: &#x27;200 workflow saved&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-42</code>: User attempts to delete a role that is still referenced by at least one workflow. The frontend first calls the dedicated references endpoint, and both the pre-delete dialog and the DELETE guard read the SF-7-owned workflow_entity_refs table with a single O(1) indexed lookup instead of parsing workflow YAML.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &#x27;Click delete on a role card&#x27;, &#x27;description&#x27;: &#x27;User initiates deletion for a library role.&#x27;, &#x27;returns&#x27;: &#x27;Delete flow starts&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/roles/references/{id}&#x27;, &#x27;description&#x27;: &#x27;Frontend preflights the dedicated reference endpoint before showing a destructive confirmation.&#x27;, &#x27;returns&#x27;: &#x27;Pending reference lookup&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &quot;SELECT DISTINCT workflows.id, workflows.name FROM workflow_entity_refs JOIN workflows ON workflows.id = workflow_entity_refs.workflow_id WHERE workflow_entity_refs.entity_type=&#x27;role&#x27; AND workflow_entity_refs.entity_id=:role_id AND workflow_entity_refs.user_id=:uid AND workflows.deleted_at IS NULL&quot;, &#x27;description&#x27;: &quot;Backend resolves referencing workflows from the SF-7-owned workflow_entity_refs junction table joined to SF-5&#x27;s active workflow rows. Single indexed lookup — O(1) per entity ID.&quot;, &#x27;returns&#x27;: &#x27;Referencing workflow rows&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;200 { total: 1, referenced_by: [{ workflow_id, workflow_name: &#x27;Planning Workflow&#x27; }] }&quot;, &#x27;description&#x27;: &#x27;Reference endpoint returns the blocked-delete details needed by the dialog.&#x27;, &#x27;returns&#x27;: &#x27;Reference payload&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;Browser&#x27;, &#x27;action&#x27;: &quot;EntityDeleteDialog shows &#x27;Cannot delete - referenced by 1 workflow&#x27;&quot;, &#x27;description&#x27;: &#x27;Frontend renders the blocked-delete state with workflow names and no destructive confirm action.&#x27;, &#x27;returns&#x27;: &#x27;User sees blocked state&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;DELETE /api/roles/{id}&#x27;, &#x27;description&#x27;: &#x27;If a stale client or manual request still submits DELETE, the backend re-checks workflow_entity_refs before mutating data.&#x27;, &#x27;returns&#x27;: &#x27;Pending server-side delete guard&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &quot;SELECT 1 FROM workflow_entity_refs WHERE entity_type=&#x27;role&#x27; AND entity_id=:role_id AND user_id=:uid LIMIT 1&quot;, &#x27;description&#x27;: &#x27;DELETE re-check uses the same SF-7-owned workflow_entity_refs index (O(1) lookup) to prevent races between preflight and mutation. No YAML parsing occurs.&#x27;, &#x27;returns&#x27;: &#x27;Matching ref row still exists&#x27;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;409 { error: &#x27;reference_conflict&#x27;, total: 1, referenced_by: [{ workflow_id, workflow_name: &#x27;Planning Workflow&#x27; }] }&quot;, &#x27;description&#x27;: &#x27;Backend blocks the delete without any workflow YAML parsing.&#x27;, &#x27;returns&#x27;: &#x27;Blocked delete response&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-43</code>: User registers a custom MCP tool in the SF-7-owned tools table, then opens a Role editor where the tool appears in the ToolChecklistGrid. The user selects the tool and saves the role with the custom tool in its tools array.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;Navigate to /tools, click &#x27;New Tool&#x27;&quot;, &#x27;description&#x27;: &quot;User opens the Tools Library page. Two sections are visible: &#x27;Built-in Tools&#x27; (read-only cards, no DB rows) and &#x27;My Tools&#x27; (user-registered, from SF-7-owned tools table).&quot;, &#x27;returns&#x27;: &#x27;ToolEditorView renders (create mode)&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/tools/check-name?name=mcp__github__create_issue&#x27;, &#x27;description&#x27;: &#x27;Frontend validates tool name uniqueness before allowing save.&#x27;, &#x27;returns&#x27;: &#x27;{ available: true }&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &quot;POST /api/tools { name, description, source: &#x27;mcp&#x27;, input_schema }&quot;, &#x27;description&#x27;: &#x27;Create the new tool record in the SF-7-owned tools table.&#x27;, &#x27;returns&#x27;: &#x27;201 { id, name, source, ... }&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &#x27;Success toast + navigate to /tools list view&#x27;, &#x27;description&#x27;: &quot;Cache invalidation triggers a refetch; the tool appears in the &#x27;My Tools&#x27; section with an &#x27;MCP&#x27; badge.&quot;, &#x27;returns&#x27;: &#x27;UI updated with new tool in list&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;Navigate to /roles, open &#x27;code-reviewer&#x27; role editor, scroll to Tools section&quot;, &#x27;description&#x27;: &#x27;User opens the Role editor; the Tools section renders ToolChecklistGrid.&#x27;, &#x27;returns&#x27;: &#x27;RoleEditorView renders with Tools section&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/tools&#x27;, &#x27;description&#x27;: &#x27;ToolChecklistGrid fetches all tools. Response merges hardcoded built-in tools (backend constants) with user-registered tools from the SF-7-owned tools table.&#x27;, &#x27;returns&#x27;: &quot;{ tools: [ ...built-in, { id, name: &#x27;mcp__github__create_issue&#x27;, source: &#x27;mcp&#x27;, ... } ] }&quot;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &#x27;Check Read, Grep, Glob, and mcp__github__create_issue, then save role&#x27;, &#x27;description&#x27;: &quot;ToolChecklistGrid shows &#x27;Built-in&#x27; and &#x27;Registered&#x27; groups. User selects tools from both groups.&quot;, &#x27;returns&#x27;: &quot;Role saved with tools: [&#x27;Read&#x27;, &#x27;Grep&#x27;, &#x27;Glob&#x27;, &#x27;mcp__github__create_issue&#x27;]&quot;}</li><li>{&#x27;sequence&#x27;: 8, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &quot;PUT /api/roles/{id} { tools: [&#x27;Read&#x27;, &#x27;Grep&#x27;, &#x27;Glob&#x27;, &#x27;mcp__github__create_issue&#x27;] }&quot;, &#x27;description&#x27;: &#x27;Role entity is updated in the SF-5-owned roles table with the new tools array. Tool references remain role-local and do not participate in workflow_entity_refs.&#x27;, &#x27;returns&#x27;: &#x27;200 { id, name, tools: [...], ... }&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-44</code>: User attempts to delete a custom tool from the SF-7-owned tools table that is referenced by at least one Role&#x27;s tools array. Tool delete protection intentionally remains a Role scan and does not use workflow_entity_refs, because tools are not referenced directly by workflows.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;DELETE /api/tools/{id}&#x27;, &#x27;description&#x27;: &quot;User clicks delete on a custom tool card in the &#x27;My Tools&#x27; section; frontend sends the delete request.&quot;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;SELECT name FROM tools WHERE id=:tool_id AND deleted_at IS NULL&#x27;, &#x27;description&#x27;: &quot;Fetch the tool&#x27;s name from the SF-7-owned tools table so the backend can check Role tools arrays for that exact identifier.&quot;, &#x27;returns&#x27;: &quot;{ name: &#x27;mcp__github__create_issue&#x27; }&quot;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;SELECT id, name, tools FROM roles WHERE user_id=:uid AND deleted_at IS NULL&#x27;, &#x27;description&#x27;: &#x27;Backend fetches all non-deleted roles from the SF-5-owned roles table to scan their tools JSON arrays.&#x27;, &#x27;returns&#x27;: &#x27;Role rows with tools JSON arrays&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &quot;Scan each role&#x27;s tools array for tool name match&quot;, &#x27;description&#x27;: &#x27;In-memory role scan identifies referencing role names. This flow is separate from the workflow reference endpoint.&#x27;, &#x27;returns&#x27;: &#x27;List of referencing role names&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;409 { error: &#x27;reference_conflict&#x27;, details: [{ role_id, role_name }] }&quot;, &#x27;description&#x27;: &#x27;Backend returns blocked-delete response listing referencing roles.&#x27;, &#x27;returns&#x27;: &#x27;&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;Browser&#x27;, &#x27;action&#x27;: &quot;EntityDeleteDialog shows &#x27;Cannot delete - referenced by 2 roles&#x27;&quot;, &#x27;description&#x27;: &#x27;Frontend renders the blocked-delete state with role names so the user understands the dependency.&#x27;, &#x27;returns&#x27;: &#x27;User sees blocked state&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-45</code>: User opens a task template in the library and defines named actor slots (e.g., &#x27;pm&#x27;, &#x27;reviewer&#x27;) with optional default role assignments. SF-7&#x27;s actor_slots table persists these definitions so they survive reloads and are reusable across workflow instances.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;Navigate to /templates, open a template, click &#x27;Edit Actor Slots&#x27;&quot;, &#x27;description&#x27;: &#x27;User opens the ActorSlotsEditor panel within the TemplatesLibraryPage.&#x27;, &#x27;returns&#x27;: &#x27;ActorSlotsEditor renders&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;GET /api/templates/{id}/actor-slots&#x27;, &#x27;description&#x27;: &#x27;Load existing slot definitions from the SF-7-owned actor_slots table for this template.&#x27;, &#x27;returns&#x27;: &#x27;{ slots: [{ slot_key, default_role_id?, role_name? }] }&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;Browser&#x27;, &#x27;to_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;action&#x27;: &quot;Add new slot &#x27;pm&#x27;, select a default role from RolePicker, click Save&quot;, &#x27;description&#x27;: &#x27;User defines a new named actor slot with an optional default role assignment.&#x27;, &#x27;returns&#x27;: &#x27;Pending slot upsert&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;PUT /api/templates/{id}/actor-slots/pm { default_role_id: roleUUID }&#x27;, &#x27;description&#x27;: &quot;Upsert the slot definition in the SF-7-owned actor_slots table. slot_key &#x27;pm&#x27; is unique per template.&quot;, &#x27;returns&#x27;: &quot;{ slot_key: &#x27;pm&#x27;, default_role_id: roleUUID, role_name: &#x27;Product Manager&#x27;, updated_at }&quot;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;compose-frontend&#x27;, &#x27;to_service&#x27;: &#x27;Browser&#x27;, &#x27;action&#x27;: &quot;ActorSlotsEditor shows &#x27;pm → Product Manager&#x27; in the slot list&quot;, &#x27;description&#x27;: &quot;Slot definition is persisted and displayed. Future workflow instances referencing this template can resolve the &#x27;pm&#x27; slot to the assigned role.&quot;, &#x27;returns&#x27;: &#x27;Slot persisted and visible&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-46</code>: The APScheduler reconciliation job (or manual operator trigger via POST /api/admin/reconcile-entity-refs) resyncs the materialized workflow_entity_refs index against actual workflow yaml_content. Handles stale-index recovery for scenarios where SF-7&#x27;s post-commit hook callbacks failed after a SF-5 commit. The job is idempotent and produces the same result regardless of how many times it runs.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;APScheduler&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;Fire reconcile_entity_refs_all() on configured interval (default: 15 minutes) or via POST /api/admin/reconcile-entity-refs&#x27;, &#x27;description&#x27;: &#x27;Scheduler triggers the reconciliation function. Manual trigger path calls the same reconcile_entity_refs_all() function.&#x27;, &#x27;returns&#x27;: &#x27;Reconciliation started&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;SELECT id, user_id, yaml_content FROM workflows WHERE deleted_at IS NULL&#x27;, &#x27;description&#x27;: &#x27;Fetch all non-deleted workflows to inspect their current yaml_content. No lock held — reads are snapshot-consistent.&#x27;, &#x27;returns&#x27;: &#x27;List of active workflow rows&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;For each workflow: parse yaml_content to extract all library UUID refs (roles, schemas, templates)&#x27;, &#x27;description&#x27;: &#x27;In-process YAML parse extracts all entity_type / entity_id pairs from the workflow definition. Same parser used by refresh_entity_refs callback.&#x27;, &#x27;returns&#x27;: &#x27;Expected set of (entity_type, entity_id) pairs per workflow_id&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;SELECT workflow_id, entity_type, entity_id FROM workflow_entity_refs WHERE workflow_id = ANY(:workflow_ids)&#x27;, &#x27;description&#x27;: &#x27;Batch-fetch the current materialized rows for all active workflows in a single query.&#x27;, &#x27;returns&#x27;: &#x27;Current set of indexed (workflow_id, entity_type, entity_id) rows&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;Compute diff per workflow: missing_rows = expected − current; stale_rows = current − expected&#x27;, &#x27;description&#x27;: &#x27;Set arithmetic identifies which rows need to be inserted (missed by failed hooks) and which rows are stale (orphaned by failed purge hooks).&#x27;, &#x27;returns&#x27;: &#x27;Per-workflow diff maps&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-db&#x27;, &#x27;action&#x27;: &#x27;For each workflow with non-empty diff: BEGIN; DELETE stale rows; INSERT missing rows; COMMIT&#x27;, &#x27;description&#x27;: &#x27;Atomic reconcile transaction per workflow. Only workflows with diffs are touched. Workflows already in sync incur no write.&#x27;, &#x27;returns&#x27;: &#x27;Updated workflow_entity_refs rows; rows_added and rows_removed counters accumulated&#x27;}</li><li>{&#x27;sequence&#x27;: 7, &#x27;from_service&#x27;: &#x27;compose-backend&#x27;, &#x27;to_service&#x27;: &#x27;compose-backend&#x27;, &#x27;action&#x27;: &#x27;Return { workflows_scanned, rows_added, rows_removed, duration_ms }&#x27;, &#x27;description&#x27;: &#x27;Summary returned to the manual trigger caller (POST /api/admin/reconcile-entity-refs response body) or logged by the scheduler job.&#x27;, &#x27;returns&#x27;: &#x27;Reconciliation summary&#x27;}</li></ol>
        </div>

    <h3>Entities</h3>
    <div class="entity-block">
            <h4><code>ENT-109</code>: Workflow</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-5-owned table. Referenced by SF-7 for workflow_entity_refs refresh via post-commit hooks.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>yaml_content</code></td>
                        <td><code>TEXT</code></td>
                        <td>Serialized workflow definition containing library role, schema, and template UUID references. SF-7&#x27;s refresh_entity_refs callback and the reconciliation job both re-fetch this field to rebuild workflow_entity_refs rows.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-110</code>: Role</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-5-owned table (roles). Canonical library role identifier returned by RolePicker and persisted into workflow YAML content.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>model</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>system_prompt</code></td>
                        <td><code>TEXT</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>tools</code></td>
                        <td><code>JSON</code></td>
                        <td>Role-local tool references used by ToolChecklistGrid and tool delete protection. Not materialized into workflow_entity_refs.</td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>JSON</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>is_example</code></td>
                        <td><code>BOOLEAN</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-111</code>: OutputSchema</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-5-owned table (output_schemas). Canonical library output schema identifier persisted into workflow YAML content.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>TEXT</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>json_schema</code></td>
                        <td><code>JSON</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>is_example</code></td>
                        <td><code>BOOLEAN</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-112</code>: TaskTemplate</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-5-owned table (custom_task_templates). Canonical library task template identifier. SF-7 extends this entity with actor_slots rows via a separate SF-7-owned migration.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>TEXT</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>subgraph_yaml</code></td>
                        <td><code>TEXT</code></td>
                        <td>Serialized task template subgraph used when stamping or saving template refs.</td>
                    </tr><tr>
                        <td><code>input_interface</code></td>
                        <td><code>JSON</code></td>
                        <td>Declared input interface for the template subgraph.</td>
                    </tr><tr>
                        <td><code>output_interface</code></td>
                        <td><code>JSON</code></td>
                        <td>Declared output interface for the template subgraph.</td>
                    </tr><tr>
                        <td><code>is_example</code></td>
                        <td><code>BOOLEAN</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td></td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-113</code>: Tool</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-7-owned table (tools). Created by SF-7&#x27;s Alembic migration as the first downstream extension after SF-5&#x27;s five foundation tables. Primary key for custom tools only; built-in tools have no DB rows.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td>From JWT sub claim; scopes tools per user.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>VARCHAR(200)</code></td>
                        <td>Tool identifier, for example mcp__github__create_issue. Max 200 chars.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>VARCHAR(500)</code></td>
                        <td>What the tool does. Shown in Tool Library cards and the Role editor checklist.</td>
                    </tr><tr>
                        <td><code>source</code></td>
                        <td><code>VARCHAR</code></td>
                        <td>Tool origin type. &#x27;mcp&#x27; for MCP server tools, &#x27;custom_function&#x27; for custom definitions.</td>
                    </tr><tr>
                        <td><code>input_schema</code></td>
                        <td><code>JSON</code></td>
                        <td>Optional JSON Schema describing the tool&#x27;s input parameters.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td>Set on creation, immutable on update.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td>Set on every update.</td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td>Soft-delete pattern. Partial unique index excludes deleted rows.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-114</code>: WorkflowEntityRef</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-7-owned table (workflow_entity_refs). Created exclusively by SF-7&#x27;s Alembic migration — not part of SF-5&#x27;s five foundation tables. Stable row identifier for the materialized reference entry.</td>
                    </tr><tr>
                        <td><code>workflow_id</code></td>
                        <td><code>UUID</code></td>
                        <td>References the SF-5-owned workflows table. On SF-5 workflow soft-delete, SF-7&#x27;s purge_entity_refs post-commit callback removes rows; ON DELETE CASCADE is a safety net for hard-deletes and missed hook events.</td>
                    </tr><tr>
                        <td><code>entity_type</code></td>
                        <td><code>VARCHAR</code></td>
                        <td>Library entity class referenced by the workflow.</td>
                    </tr><tr>
                        <td><code>entity_id</code></td>
                        <td><code>UUID</code></td>
                        <td>UUID of the referenced Role, OutputSchema, or TaskTemplate (all in SF-5-owned tables). Indexed for O(1) delete preflight lookups by entity_id.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td>Owner scope used by the references endpoint and delete guards.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td>Timestamp when the current materialized ref row was created by SF-7&#x27;s refresh_entity_refs callback or the reconciliation job.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-115</code>: ActorSlot</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>SF-7-owned table (actor_slots). Created by SF-7&#x27;s Alembic migration as an extension to SF-5&#x27;s custom_task_templates. Stable row identifier for one named actor-slot definition.</td>
                    </tr><tr>
                        <td><code>template_id</code></td>
                        <td><code>UUID</code></td>
                        <td>References the SF-5-owned custom_task_templates table. Cascade ensures slot definitions are removed when a template is hard-deleted.</td>
                    </tr><tr>
                        <td><code>slot_key</code></td>
                        <td><code>VARCHAR(100)</code></td>
                        <td>Symbolic slot identifier used by workflow nodes (e.g. &#x27;pm&#x27;, &#x27;reviewer&#x27;). Unique per template.</td>
                    </tr><tr>
                        <td><code>default_role_id</code></td>
                        <td><code>UUID</code></td>
                        <td>Default library role UUID for this slot. Null means unassigned. References the SF-5-owned roles table.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>VARCHAR(500)</code></td>
                        <td>Human-readable description of what actor should fill this slot.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>VARCHAR</code></td>
                        <td>Owner scope from JWT sub claim. Scopes slot definitions per user.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td>Set on creation.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>TIMESTAMP</code></td>
                        <td>Set on every update.</td>
                    </tr></tbody>
            </table>
        </div>

    <h3>Entity Relations</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>Relation</th><th>To</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>ER-100</code></td>
            <td><code>workflow</code></td>
            <td></td>
            <td><code>workflow-entity-ref</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-101</code></td>
            <td><code>workflow</code></td>
            <td></td>
            <td><code>role</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-102</code></td>
            <td><code>workflow</code></td>
            <td></td>
            <td><code>output-schema</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-103</code></td>
            <td><code>workflow</code></td>
            <td></td>
            <td><code>task-template</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-104</code></td>
            <td><code>role</code></td>
            <td></td>
            <td><code>tool</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-105</code></td>
            <td><code>task-template</code></td>
            <td></td>
            <td><code>actor-slot</code></td>
            <td></td>
        </tr><tr>
            <td><code>ER-106</code></td>
            <td><code>actor-slot</code></td>
            <td></td>
            <td><code>role</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>Decisions</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Decision</th></tr></thead>
        <tbody><tr>
            <td><code>D-121</code></td>
            <td>D-SF7-1: TaskTemplateEditorView reuses SF-6 editor components (EditorCanvas, AskNode, BranchNode, inspectors) directly with no shared canvas abstraction layer. Each editor creates its own editorStore instance via factory function. Phase creation tools are disabled via a noPhaseTools prop.</td>
        </tr><tr>
            <td><code>D-122</code></td>
            <td>D-SF7-2 [REQ-18, D-GR-37]: Reference checking for role, schema, and template deletes subscribes to SF-5&#x27;s REQ-18 post-commit mutation hook interface and maintains the SF-7-owned materialized workflow_entity_refs index to provide O(1) delete preflight checks (single indexed lookup by entity_id, regardless of workflow count). The prior approach of parsing all workflow YAML at delete time (O(n×parse) per entity delete) is explicitly rejected. SF-7 registers refresh_entity_refs(workflow_id, user_id) against the Workflow `updated` hook slot and purge_entity_refs(workflow_id) against the Workflow `soft_deleted` slot at FastAPI lifespan startup. Both GET /api/{entity}/references/{id} and DELETE handler re-checks read the same workflow_entity_refs index — no YAML parsing occurs at delete time. Recovery from stale-index drift is handled by the periodic reconciliation job per D-SF7-8.</td>
        </tr><tr>
            <td><code>D-123</code></td>
            <td>D-SF7-3: SF-7 is the EXCLUSIVE owner of the workflow_entity_refs table and its Alembic migration. SF-5&#x27;s five-table foundation (workflows, workflow_versions, roles, output_schemas, custom_task_templates) does NOT include workflow_entity_refs. SF-5&#x27;s REQ-18 post-commit mutation hook interface fires typed events (created, updated, soft_deleted, restored) on all four foundation entity types after each successful database commit. SF-7 registers refresh_entity_refs(workflow_id, user_id) and purge_entity_refs(workflow_id) callbacks against the Workflow hook slot at application startup (FastAPI lifespan event). Those callbacks run in a separate SF-7-owned database transaction and do not re-enter SF-5&#x27;s transaction. SF-5 never creates or updates workflow_entity_refs rows — all library-facing backend flows (duplicate name validation, JSON Schema server-side validation, inline-to-library promotion, pre-delete reference dialogs, delete guard responses) remain SF-7 scope.</td>
        </tr><tr>
            <td><code>D-124</code></td>
            <td>D-SF7-4 [REQ-2]: Sidebar contains exactly 5 entity-type folders: Workflows, Roles, Schemas, Templates, Tools. Phases and Plugins are NOT sidebar folders.</td>
        </tr><tr>
            <td><code>D-125</code></td>
            <td>D-SF7-5 [REQ-4, D-GR-7]: Tool Library uses a two-tier data strategy: built-in Claude tools are hardcoded in the backend and always included in GET /api/tools responses (no DB rows), while user-registered custom tools are stored in the SF-7-owned tools table. Tool deletion intentionally remains a Role.tools array check and does not use workflow_entity_refs because tools are not referenced directly by workflows.</td>
        </tr><tr>
            <td><code>D-126</code></td>
            <td>D-GR-26: workflow_entity_refs is the canonical reference-tracking model behind GET /api/{entity}/references/{id}. Pre-delete dialogs use that endpoint, and DELETE handlers re-check the same SF-7-owned table to guard against stale clients or concurrent workflow edits.</td>
        </tr><tr>
            <td><code>D-127</code></td>
            <td>D-SF7-6: SF-5&#x27;s REQ-18 post-commit hook interface exposes two relevant slot types for SF-7: Workflow updated (covers create, import, duplicate, save-version) and Workflow soft_deleted. Both pass the workflow_id and user_id. SF-7&#x27;s refresh_entity_refs callback handles the updated event by opening a new AsyncSession, re-fetching the workflow&#x27;s yaml_content, parsing all library UUID references (roles, schemas, templates), and atomically replacing the workflow&#x27;s workflow_entity_refs rows (DELETE WHERE workflow_id=:wid + bulk INSERT). SF-7&#x27;s purge_entity_refs callback handles the soft_deleted event by deleting all workflow_entity_refs rows for that workflow_id. Both callbacks are registered synchronously during the FastAPI lifespan startup event by calling SF-5&#x27;s hook registration API, with SF-5 never importing SF-7 modules. The callbacks execute synchronously in-process but in separate database transactions from SF-5&#x27;s commit — if a callback fails, SF-5&#x27;s transaction is already committed and the workflow save is not rolled back. Stale-index recovery is owned by the reconciliation job (D-SF7-8).</td>
        </tr><tr>
            <td><code>D-128</code></td>
            <td>D-SF7-7: actor_slots persistence is SF-7-owned. SF-7&#x27;s Alembic migration creates the actor_slots table as an extension to SF-5&#x27;s custom_task_templates (FK: actor_slots.template_id -&gt; custom_task_templates.id ON DELETE CASCADE). Each row stores a named slot_key unique per template, an optional default_role_id pointing to the SF-5-owned roles table, and a description. The API surface is /api/templates/{id}/actor-slots with GET (list), PUT /{slot_key} (upsert), and DELETE /{slot_key} (remove). Actor slot definitions are not reference-tracked in workflow_entity_refs.</td>
        </tr><tr>
            <td><code>D-129</code></td>
            <td>D-SF7-8 [D-GR-37, RISK-1]: A periodic reconciliation job runs within compose-backend (APScheduler BackgroundScheduler, default interval: 15 minutes, configurable via RECONCILE_JOB_INTERVAL_MINUTES env var, disableable via RECONCILE_JOB_ENABLED=false) to resync workflow_entity_refs from workflow yaml_content in scenarios where SF-7&#x27;s post-commit hook callbacks fail (the SF-5 commit succeeds but the SF-7 separate transaction does not). The reconciliation function reconcile_entity_refs_all(): (1) queries all non-deleted workflows in a single SELECT, (2) batch-fetches current workflow_entity_refs rows for those workflow IDs, (3) for each workflow, parses yaml_content using the same entity-ref extractor as refresh_entity_refs, (4) computes the diff (missing inserts, stale deletes), and (5) for each workflow with a non-empty diff, opens an atomic transaction: DELETE stale rows + INSERT missing rows. Workflows already in sync incur no write. The job is idempotent — running it multiple times produces the same result. A manual trigger endpoint POST /api/admin/reconcile-entity-refs invokes the same reconcile_entity_refs_all() function and returns { workflows_scanned, rows_added, rows_removed, duration_ms }. The job is registered via FastAPI lifespan startup using the same startup hook that registers the mutation callbacks.</td>
        </tr></tbody>
    </table>

    <h3>Risks</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Risk</th></tr></thead>
        <tbody><tr>
            <td><code>RISK-59</code></td>
            <td>RISK-1 (Medium): workflow_entity_refs can drift from the true set of workflow entity references if SF-7&#x27;s post-commit callback fails (e.g., database error in the separate SF-7 transaction after SF-5 has already committed). Because SF-5 has already committed, the workflow save succeeds but the reference index is stale until the next workflow save or reconciliation run. Mitigation: (a) D-SF7-8 reconciliation job (APScheduler, 15-minute interval) deterministically resyncs the index from yaml_content, providing a bounded-time recovery path with no operator intervention; (b) a manual trigger endpoint (POST /api/admin/reconcile-entity-refs) allows on-demand resync when drift is suspected; (c) SF-7&#x27;s DELETE re-checks on role/schema/template delete provide a second safety net; (d) the ON DELETE CASCADE FK on workflow_entity_refs.workflow_id ensures rows are always cleaned up on hard-delete even if the callback was missed.</td>
        </tr><tr>
            <td><code>RISK-60</code></td>
            <td>RISK-2 (Medium): Sharing SF-6 editor components in TaskTemplateEditorView may cause state leaks between the workflow editor and the template editor. Mitigation: create isolated editorStore instances via a factory function per editor mount.</td>
        </tr><tr>
            <td><code>RISK-61</code></td>
            <td>RISK-3 (Low): Picker data freshness - a role or schema created in the library page may not appear in editor pickers until the next fetch cycle. Mitigation: TanStack Query cache invalidation on successful mutations.</td>
        </tr><tr>
            <td><code>RISK-62</code></td>
            <td>RISK-4 (Low): Inline-to-library promotion race condition if a user has two windows open simultaneously creating the same role name. Mitigation: backend idempotent promotion path returns the existing record on duplicate name when promote=true.</td>
        </tr><tr>
            <td><code>RISK-63</code></td>
            <td>RISK-5 (Medium): Tool name-based references in Role.tools JSON arrays are fragile - renaming a custom tool via PUT /api/tools/{id} does not automatically update roles that reference the old name. Mitigation: UI warning banner on rename and Role editor refetch from GET /api/tools so stale names become visible when edited.</td>
        </tr><tr>
            <td><code>RISK-64</code></td>
            <td>RISK-6 (Low): Tool deletion reference checking still scans all user roles in memory. Complexity is O(N*M), where N=roles and M=average tools per role. Mitigation: typical user counts are small, and the query stays scoped to the current user&#x27;s non-deleted roles.</td>
        </tr><tr>
            <td><code>RISK-65</code></td>
            <td>RISK-7 (Low): SF-7&#x27;s Alembic migration must declare its dependency on SF-5&#x27;s initial migration revision. If SF-5&#x27;s revision ID changes (e.g., squashed migration), SF-7&#x27;s down_revision reference breaks. Mitigation: treat SF-5&#x27;s initial revision ID as a stable anchor; document the dependency explicitly in SF-7&#x27;s migration file header.</td>
        </tr><tr>
            <td><code>RISK-66</code></td>
            <td>RISK-8 (Low): The reconciliation job (D-SF7-8) scans all non-deleted workflows on every run. At high workflow counts this may cause a momentary read spike. Mitigation: the batch SELECT reads yaml_content once; writes only touch workflows with diffs; the job interval (default 15 min) is tunable via RECONCILE_JOB_INTERVAL_MINUTES; the job can be disabled entirely via RECONCILE_JOB_ENABLED=false if the hook failure rate is negligible in production.</td>
        </tr></tbody>
    </table>
</section>
<hr/>


    <footer style="text-align: center; color: var(--muted); font-size: 0.85rem; margin-top: 3rem; padding: 1rem;">
        Generated by artifact compiler. All content preserved from source subfeature artifacts.
    </footer>
</body>
</html>