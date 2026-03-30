<!-- SF: composer-app-foundation -->
<section id="sf-composer-app-foundation" class="subfeature-section">
    <h2>SF-5 Composer App Foundation &amp; Tools Hub</h2>
    <div class="provenance">Subfeature: <code>composer-app-foundation</code></div>

    <h3>Overview</h3>
    <div class="overview-text">SF-5 establishes the canonical compose foundation: `tools/compose/backend` for the FastAPI + PostgreSQL service, `tools/compose/frontend` for the authenticated compose SPA, and `platform/toolshub/frontend` for the static tools hub. The backend persists workflow definitions as canonical nested YAML where phases contain `nodes` and `children`, hook wiring is serialized only as ordinary edges (`source`, `target`, `transform_fn`) with no persisted `port_type`, and `GET /api/schema/workflow` is the only runtime schema contract the composer consumes. SF-5 owns exactly five foundation tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`. After every workflow write (create, import, version_saved, deleted) the backend fires a `WorkflowMutationHook(workflow_id, operation)` event so downstream SF-7 can own the `workflow_entity_refs` reference-index extension without coupling SF-5 to that table. The frontend may keep a flat React Flow store internally, but every load, import, validate, save, and export boundary converts through the same iriai-compose declarative models that execution uses. This system design also fully specifies all four cross-subfeature edge data contracts per D-GR-41: (1) SF-5→SF-6: complete TypeScript type interfaces exported from `tools/compose/frontend/src/types/index.ts` and the authoritative JSON Schema field contract including `context_keys` and AskNode `task`/`context_text`; (2) SF-5→SF-7: `WorkflowMutationHookRegistry` Python interface and SQLAlchemy ORM model export contract; (3) SF-7→SF-6: reference endpoint paths, TypeScript response shapes, and delete preflight 409 contract; (4) SF-6→SF-7: exclusive hook-driven index rebuild path and component prop interfaces for usage panels.</div>

    <h3>Services</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Name</th><th>Kind</th><th>Description</th><th>Technology</th><th>Port</th><th>Journeys</th>
        </tr></thead>
        <tbody><tr>
            <td><code>SVC-59</code></td>
            <td><strong>User / Browser</strong></td>
            <td><code>external</code></td>
            <td>Developer using the tools hub and composer in a browser.</td>
            <td><code>Browser</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3, J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-60</code></td>
            <td><strong>Tools Hub Frontend</strong></td>
            <td><code>frontend</code></td>
            <td>Static React/Vite app at `platform/toolshub/frontend/`. Reads `dev_tier` from JWT, renders a hardcoded developer-tools card catalog, and same-tab navigates to `compose.iriai.app` when the Workflow Composer card is clicked. No backend of its own.</td>
            <td><code>React 18, Vite, TypeScript, @homelocal/auth</code></td>
            <td>—</td>
            <td>J-1</td>
        </tr><tr>
            <td><code>SVC-61</code></td>
            <td><strong>Compose Frontend</strong></td>
            <td><code>frontend</code></td>
            <td>Authenticated React/Vite SPA at `tools/compose/frontend/` with the Explorer-style sidebar (four folders: Workflows, Roles, Output Schemas, Task Templates), workflows landing page with starter template cards, and the schema-driven editor shell consumed by SF-6. Exports all TypeScript type interfaces for API responses through `tools/compose/frontend/src/types/index.ts` (the canonical barrel export that SF-6 imports exclusively from). Configures the shared Axios API client at `tools/compose/frontend/src/api/client.ts` that both SF-5 routes and SF-7 extension routes use. No plugin, tool-library, or reference-check surfaces in SF-5.</td>
            <td><code>React 18, Vite, TypeScript, React Query, Zustand, @homelocal/auth, Axios</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3, J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-62</code></td>
            <td><strong>Compose Backend</strong></td>
            <td><code>service</code></td>
            <td>FastAPI service at `tools/compose/backend/` exposing workflow CRUD, versioning, validation, starter templates, schema export, and baseline library entity CRUD. Stores raw nested YAML and never invents a second schema contract. Exports `WorkflowMutationHookRegistry` singleton at `app/state.py::mutation_hook_registry` and SQLAlchemy ORM models at `app/models.py` for SF-7 to consume. After every workflow write commits, fires `WorkflowMutationHook(workflow_id, operation)` so SF-7 can register its reference-index refresh handler. Also hosts SF-7-owned router at `app/routers/entity_refs.py` and SF-7-owned delete preflight dependencies — these routes are physically served by this process but logically owned by SF-7.</td>
            <td><code>Python 3.11, FastAPI, SQLAlchemy 2.x (async), Alembic, homelocal-auth, structlog</code></td>
            <td>8000</td>
            <td>J-1, J-2, J-3, J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-63</code></td>
            <td><strong>iriai-compose Declarative Schema Subpackage</strong></td>
            <td><code>service</code></td>
            <td>New schema subpackage at `iriai-compose/iriai_compose/schema/` to be created by SF-1 (declarative-schema subfeature). Imported by SF-5&#x27;s compose backend via `from iriai_compose.schema import WorkflowConfig`. Exposes: `WorkflowConfig`, `PhaseDefinition`, `AgentNode`, `AskNode`, `BranchNode`, `CustomNode`, `EvalNode`, `EdgeDefinition`, `PortDefinition`, `WorkflowCostConfig`, `PhaseCostConfig`, `NodeCostConfig`, `load_workflow()`, `validate_workflow()`. Does NOT export: MapNode, FoldNode, LoopNode, TransformRef, HookRef (these are phantom exports — they do not exist). The existing `iriai_compose` package independently exports `Phase` (ABC), `Workflow`, `Role`, and runtime primitives — these are distinct from the new declarative Pydantic models. Key field contracts: AskNode has fields `task` (required string) and `context_text` (optional string) — the field name `prompt` is not a valid AskNode field. WorkflowConfig has an optional `context_keys: list[str]` field at the workflow root. BranchNode uses per-port condition expressions per D-GR-35.</td>
            <td><code>Python 3.11, Pydantic v2, PyYAML</code></td>
            <td>—</td>
            <td>J-2, J-3, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-64</code></td>
            <td><strong>Auth Service</strong></td>
            <td><code>external</code></td>
            <td>Homelocal auth service issuing JWTs and serving JWKS for the FastAPI backend and OAuth flows for both SPAs. JWT `sub` becomes `user_id`; `dev_tier` claim gates tools-hub card visibility.</td>
            <td><code>OAuth 2.0, RS256 JWT, JWKS</code></td>
            <td>—</td>
            <td>J-1, J-2, J-3, J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-65</code></td>
            <td><strong>PostgreSQL Database</strong></td>
            <td><code>database</code></td>
            <td>Dedicated PostgreSQL instance managed by SQLAlchemy 2.x and Alembic. Migration chain tracked by `alembic_version_compose` table (isolated from all other platform services). Stores exactly five SF-5 foundation tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`. The `workflow_entity_refs` table is not created by SF-5 migrations — it is added by a separate SF-7 Alembic revision chained after the SF-5 initial revision.</td>
            <td><code>PostgreSQL, SQLAlchemy 2.x, Alembic, psycopg (asyncpg driver)</code></td>
            <td>—</td>
            <td>J-2, J-3, J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-66</code></td>
            <td><strong>Starter Template Bundle</strong></td>
            <td><code>database</code></td>
            <td>Bundled JSON/YAML assets checked into `tools/compose/backend/app/data/` containing translated starter workflows derived from the iriai-build-v2 planning/develop/bugfix reference flows.</td>
            <td><code>JSON, YAML files</code></td>
            <td>—</td>
            <td>J-3</td>
        </tr><tr>
            <td><code>SVC-67</code></td>
            <td><strong>SF-6 Workflow Editor Canvas</strong></td>
            <td><code>frontend</code></td>
            <td>Workflow canvas module owned by SF-6, co-located in `tools/compose/frontend/src/editor/`. Imports all TypeScript types exclusively from `tools/compose/frontend/src/types/index.ts` (the SF-5 barrel export): Workflow, WorkflowVersion, Role, OutputSchema, CustomTaskTemplate, StarterTemplate, PaginatedList&lt;T&gt;, ValidationIssue, ValidationResult, ImportResult, WorkflowEntityRefsResponse, EntityUsageReport, DeletePreflightConflict. Uses the shared Axios client from SF-5 (`src/api/client.ts`) to call both SF-5 workflow CRUD endpoints and SF-7 extension endpoints. Calls GET /api/schema/workflow at mount to drive the node palette. Calls GET /api/roles/{id}/usage, GET /api/schemas/{id}/usage, GET /api/templates/{id}/usage before rendering delete confirmation dialogs. Calls GET /api/workflows/{id}/entity-refs for the editor reference panel. Does NOT call any dedicated SF-7 index-rebuild endpoint — reference index rebuilds happen exclusively through SF-5 save hooks.</td>
            <td><code>React 18, React Flow, Zustand, TypeScript, Axios (shared SF-5 client)</code></td>
            <td>—</td>
            <td>J-2, J-3, J-4, J-5, J-6</td>
        </tr><tr>
            <td><code>SVC-68</code></td>
            <td><strong>SF-7 Libraries &amp; Registries Extension</strong></td>
            <td><code>service</code></td>
            <td>Backend and frontend extension module owned by SF-7. Backend: adds router at `tools/compose/backend/app/routers/entity_refs.py` with four read-only endpoints; injects `require_no_entity_refs` FastAPI dependency into SF-5&#x27;s delete endpoints for 409 preflight; registers `refresh_entity_refs` async handler into SF-5&#x27;s `WorkflowMutationHookRegistry` singleton during FastAPI lifespan startup; owns `workflow_entity_refs` DDL via separate SF-7 Alembic revision; optionally adds `actor_slots` column to `custom_task_templates` via additive migration. Imports `WorkflowORM`, `WorkflowVersionORM`, `RoleORM`, `OutputSchemaORM`, `CustomTaskTemplateORM` from `app/models.py` and `get_db` from `app/database.py` — never redefines session management. Frontend: adds `EntityUsagePanel` and `DeleteEntityDialog` React components consumed by SF-6 canvas.</td>
            <td><code>Python 3.11, FastAPI, SQLAlchemy 2.x (async), PyYAML (yaml_content scanning), React 18, TypeScript</code></td>
            <td>—</td>
            <td>J-2, J-4</td>
        </tr></tbody>
    </table>

    <h3>Connections</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>To</th><th>Protocol</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>CONN-101</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-102</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-103</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS/OAuth</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-104</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Browser navigation</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-105</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS/OAuth</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-106</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTP/REST</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-107</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTPS/JWKS</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-108</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>SQL</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-109</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-110</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Filesystem read</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-111</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>TypeScript import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-112</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>Python import</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-113</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTP/REST</code></td>
            <td></td>
        </tr><tr>
            <td><code>CONN-114</code></td>
            <td><code></code></td>
            <td><code></code></td>
            <td><code>HTTP/REST</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>API Endpoints</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>Method</th><th>Path</th><th>Service</th><th>Description</th><th>Auth</th>
        </tr></thead>
        <tbody><tr>
            <td><code>API-105</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schema/workflow</code></td>
            <td><code></code></td>
            <td>Returns the canonical WorkflowConfig JSON Schema used by the composer at runtime. Derived from `WorkflowConfig.model_json_schema()` in `iriai_compose.schema`. The schema exposes nested `phases[].nodes` and `phases[].children` plus edge-only hook wiring (`source`, `target`, `transform_fn`) and never ships a serialized `port_type` field. Key field constraints: WorkflowConfig root includes optional `context_keys: string[]` per D-GR-39; AskNode has required `task: string` and optional `context_text: string` (not `prompt`); BranchNode uses per-port condition expressions per D-GR-35; no `stores`, `plugin_instances`, `inputs`, or `outputs` at root. SF-6 must fetch this endpoint at editor mount and must not use a static bundled schema for runtime behavior.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-106</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows</code></td>
            <td><code></code></td>
            <td>Lists the caller&#x27;s workflows with cursor pagination and search.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-107</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows</code></td>
            <td><code></code></td>
            <td>Creates a workflow from nested YAML or a minimal skeleton, atomically writes WorkflowVersion v1, then fires WorkflowMutationHook(workflow_id, &#x27;created&#x27;).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-108</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Returns one workflow, including its canonical nested YAML document and current version number.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-109</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Updates workflow metadata and current YAML snapshot without creating a new version row.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-110</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/workflows/{id}</code></td>
            <td><code></code></td>
            <td>Soft-deletes a workflow by setting `deleted_at`, then fires WorkflowMutationHook(workflow_id, &#x27;deleted&#x27;) so SF-7 can purge its reference-index rows.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-111</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/{id}/duplicate</code></td>
            <td><code></code></td>
            <td>Duplicates an existing workflow, seeds a fresh WorkflowVersion v1 for the copy, and fires WorkflowMutationHook(new_workflow_id, &#x27;created&#x27;).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-112</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/import</code></td>
            <td><code></code></td>
            <td>Imports a YAML file or raw YAML body, validates it against the canonical schema contract, creates WorkflowVersion v1 on success, and fires WorkflowMutationHook(workflow_id, &#x27;imported&#x27;).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-113</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/{id}/versions</code></td>
            <td><code></code></td>
            <td>Validates and stores a new immutable WorkflowVersion snapshot, updates the workflow&#x27;s current YAML, then fires WorkflowMutationHook(workflow_id, &#x27;version_saved&#x27;).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-114</code></td>
            <td><code>POST</code></td>
            <td><code>/api/workflows/{id}/validate</code></td>
            <td><code></code></td>
            <td>Runs server-side validation against the same iriai-compose declarative models used for schema export and import. Returns path/message error details.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-115</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/{id}/export</code></td>
            <td><code></code></td>
            <td>Downloads the stored canonical YAML document for a workflow.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-116</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates/starters</code></td>
            <td><code></code></td>
            <td>Returns the bundled starter templates used on the Workflows landing page.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-117</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles</code></td>
            <td><code></code></td>
            <td>Lists saved role definitions for the authenticated user.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-118</code></td>
            <td><code>POST</code></td>
            <td><code>/api/roles</code></td>
            <td><code></code></td>
            <td>Creates a reusable role definition. Fields align with the iriai-compose Role contract: `prompt` (not `system_prompt`), `tools`, `model`, `effort`, and `metadata`.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-119</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Returns one role definition owned by the caller.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-120</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Updates an existing role definition.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-121</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/roles/{id}</code></td>
            <td><code></code></td>
            <td>Soft-deletes a role. In SF-5 the base handler performs the soft-delete; SF-7 injects a `require_no_entity_refs` FastAPI dependency that runs first and returns HTTP 409 if references exist. SF-6 must handle both 204 (success) and 409 (reference conflict).</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-122</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas</code></td>
            <td><code></code></td>
            <td>Lists saved output schemas for the authenticated user.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-123</code></td>
            <td><code>POST</code></td>
            <td><code>/api/schemas</code></td>
            <td><code></code></td>
            <td>Creates a reusable JSON Schema document for node and workflow outputs.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-124</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas/{id}</code></td>
            <td><code></code></td>
            <td>Returns one output schema owned by the caller.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-125</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/schemas/{id}</code></td>
            <td><code></code></td>
            <td>Updates an output schema definition.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-126</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/schemas/{id}</code></td>
            <td><code></code></td>
            <td>Soft-deletes an output schema. SF-7 injects `require_no_entity_refs` dependency for 409 preflight; SF-6 handles both 204 and 409.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-127</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates</code></td>
            <td><code></code></td>
            <td>Lists saved task templates for the authenticated user.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-128</code></td>
            <td><code>POST</code></td>
            <td><code>/api/templates</code></td>
            <td><code></code></td>
            <td>Creates a reusable task template whose `subgraph_yaml` follows the same canonical nested contract as full workflows.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-129</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates/{id}</code></td>
            <td><code></code></td>
            <td>Returns one task template owned by the caller.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-130</code></td>
            <td><code>PUT</code></td>
            <td><code>/api/templates/{id}</code></td>
            <td><code></code></td>
            <td>Updates a task template definition.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-131</code></td>
            <td><code>DELETE</code></td>
            <td><code>/api/templates/{id}</code></td>
            <td><code></code></td>
            <td>Soft-deletes a task template. SF-7 injects `require_no_entity_refs` dependency for 409 preflight; SF-6 handles both 204 and 409.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-132</code></td>
            <td><code>GET</code></td>
            <td><code>/health</code></td>
            <td><code></code></td>
            <td>Liveness probe for process-level availability.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-133</code></td>
            <td><code>GET</code></td>
            <td><code>/ready</code></td>
            <td><code></code></td>
            <td>Readiness probe that confirms the PostgreSQL database is reachable and Alembic migration is current against `alembic_version_compose`.</td>
            <td><code>—</code></td>
        </tr><tr>
            <td><code>API-134</code></td>
            <td><code>GET</code></td>
            <td><code>/api/workflows/{id}/entity-refs</code></td>
            <td><code></code></td>
            <td>SF-7-owned route served from the compose backend (app/routers/entity_refs.py). Returns the materialized reference index for a workflow — all library entities referenced by nodes in that workflow&#x27;s current yaml_content. SF-6 calls this for the editor reference panel. Index is rebuilt by SF-7&#x27;s WorkflowMutationHook handler on every save/import/create; last_indexed_at shows when the last rebuild completed.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-135</code></td>
            <td><code>GET</code></td>
            <td><code>/api/roles/{id}/usage</code></td>
            <td><code></code></td>
            <td>SF-7-owned route served from the compose backend. Returns all workflows that reference this role. SF-6 calls this before rendering the DeleteEntityDialog for a role — if total_references &gt; 0, the dialog shows a blocking warning with workflow links. Reads from the `workflow_entity_refs` materialized index.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-136</code></td>
            <td><code>GET</code></td>
            <td><code>/api/schemas/{id}/usage</code></td>
            <td><code></code></td>
            <td>SF-7-owned route served from the compose backend. Returns all workflows that reference this output schema. SF-6 calls this before rendering the DeleteEntityDialog for a schema.</td>
            <td><code>JWT Bearer</code></td>
        </tr><tr>
            <td><code>API-137</code></td>
            <td><code>GET</code></td>
            <td><code>/api/templates/{id}/usage</code></td>
            <td><code></code></td>
            <td>SF-7-owned route served from the compose backend. Returns all workflows that reference this custom task template. SF-6 calls this before rendering the DeleteEntityDialog for a template.</td>
            <td><code>JWT Bearer</code></td>
        </tr></tbody>
    </table>

    <h3>Call Paths</h3>
    <div class="call-path-block">
            <h4><code>CP-25</code>: First-time user authenticates in the tools hub and navigates into the composer without introducing a second launcher contract.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-2&#x27;, &#x27;action&#x27;: &#x27;Open tools.iriai.app&#x27;, &#x27;description&#x27;: &#x27;The browser loads the tools hub from `platform/toolshub/frontend/` with a developer-tools card catalog.&#x27;, &#x27;returns&#x27;: &#x27;Tools hub shell&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-2&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-6&#x27;, &#x27;action&#x27;: &#x27;Start OAuth flow&#x27;, &#x27;description&#x27;: &#x27;The tools hub redirects to auth-service and receives a JWT after login; `dev_tier` claim is extracted for card gating.&#x27;, &#x27;returns&#x27;: &#x27;Access token&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-2&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;Same-tab navigation to compose.iriai.app&#x27;, &#x27;description&#x27;: &#x27;Clicking the Workflow Composer card routes the browser to the compose frontend in the same tab.&#x27;, &#x27;returns&#x27;: &#x27;Composer URL loaded&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-6&#x27;, &#x27;action&#x27;: &#x27;Ensure authenticated compose session&#x27;, &#x27;description&#x27;: &#x27;The compose frontend completes its own OAuth callback or validates the existing token for the compose domain.&#x27;, &#x27;returns&#x27;: &#x27;Authenticated compose session&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-1&#x27;, &#x27;action&#x27;: &#x27;Render Workflows landing page&#x27;, &#x27;description&#x27;: &#x27;The authenticated browser sees the workflows list shell with the four SF-5 Explorer folders (Workflows, Roles, Output Schemas, Task Templates) and starter template cards.&#x27;, &#x27;returns&#x27;: &#x27;Composer home page&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-26</code>: The frontend loads both the workflow record and the authoritative composer schema before handing control to the canvas editor.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;Open /workflows/{id}/edit&#x27;, &#x27;description&#x27;: &#x27;The browser enters the editor route after creating or selecting a workflow.&#x27;, &#x27;returns&#x27;: &#x27;Editor shell loading state&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;GET /api/schema/workflow&#x27;, &#x27;description&#x27;: &#x27;The frontend fetches the only runtime schema contract it is allowed to use.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig JSON Schema&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-5&#x27;, &#x27;action&#x27;: &#x27;WorkflowConfig.model_json_schema()&#x27;, &#x27;description&#x27;: &#x27;The backend derives the schema directly from iriai-compose declarative models and caches the result.&#x27;, &#x27;returns&#x27;: &#x27;Canonical schema dict&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;GET /api/workflows/{id}&#x27;, &#x27;description&#x27;: &#x27;The frontend fetches the stored YAML snapshot for the workflow.&#x27;, &#x27;returns&#x27;: &#x27;Workflow record&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;SELECT workflow row&#x27;, &#x27;description&#x27;: &#x27;The backend reads the workflow record and current YAML snapshot from PostgreSQL.&#x27;, &#x27;returns&#x27;: &#x27;Workflow row&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;Flatten nested YAML to editor state&#x27;, &#x27;description&#x27;: &#x27;The frontend converts canonical `phases[].nodes` and `phases[].children` into its flat React Flow store without mutating the stored contract.&#x27;, &#x27;returns&#x27;: &#x27;Canvas-ready node and edge arrays&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-27</code>: Creating a workflow from scratch inserts the workflow row and immutable version row in one transaction, then fires the mutation hook so SF-7 can refresh its reference index.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows&#x27;, &#x27;description&#x27;: &#x27;The frontend submits the workflow name and either a user-authored nested YAML snapshot or a minimal skeleton.&#x27;, &#x27;returns&#x27;: &#x27;Create request&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-6&#x27;, &#x27;action&#x27;: &#x27;Validate JWT&#x27;, &#x27;description&#x27;: &#x27;The backend verifies the bearer token and extracts the user ID from `sub`.&#x27;, &#x27;returns&#x27;: &#x27;Authenticated user&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-5&#x27;, &#x27;action&#x27;: &#x27;Parse and validate YAML contract&#x27;, &#x27;description&#x27;: &#x27;The backend validates the document against the same nested schema returned by `/api/schema/workflow`.&#x27;, &#x27;returns&#x27;: &#x27;Validated WorkflowConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;INSERT Workflow + WorkflowVersion v1&#x27;, &#x27;description&#x27;: &#x27;The backend writes the workflow record and immutable version 1 snapshot in a single transaction. No `workflow_entity_refs` rows are written by SF-5.&#x27;, &#x27;returns&#x27;: &#x27;Persisted workflow ID&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &quot;Fire WorkflowMutationHook(workflow_id, &#x27;created&#x27;)&quot;, &#x27;description&#x27;: &#x27;After the transaction commits, the backend fires the mutation hook. SF-7 registers its reference-index refresh handler here at application startup; SF-5 catches and logs any handler exceptions without rolling back.&#x27;, &#x27;returns&#x27;: &#x27;Hook handlers invoked&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;201 Created&#x27;, &#x27;description&#x27;: &#x27;The backend returns the created workflow with `current_version = 1`.&#x27;, &#x27;returns&#x27;: &#x27;Workflow&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-28</code>: The landing page fetches starter templates from the bundled asset set and creates a user-owned workflow copy on demand.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;GET /api/templates/starters&#x27;, &#x27;description&#x27;: &#x27;The frontend loads the built-in starter cards for the landing page.&#x27;, &#x27;returns&#x27;: &#x27;StarterTemplate[]&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-8&#x27;, &#x27;action&#x27;: &#x27;Read starter bundle&#x27;, &#x27;description&#x27;: &#x27;The backend reads the translated starter templates from checked-in assets at `tools/compose/backend/app/data/`.&#x27;, &#x27;returns&#x27;: &#x27;Starter template payloads&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows&#x27;, &#x27;description&#x27;: &quot;When the user clicks Use Template, the frontend creates a new workflow using the selected starter template&#x27;s canonical YAML.&quot;, &#x27;returns&#x27;: &#x27;Create request&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-5&#x27;, &#x27;action&#x27;: &#x27;Validate starter YAML&#x27;, &#x27;description&#x27;: &#x27;The backend validates the starter payload before persistence so the starter bundle cannot drift from the runtime contract.&#x27;, &#x27;returns&#x27;: &#x27;Validated WorkflowConfig&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;Insert workflow copy and version 1&#x27;, &#x27;description&#x27;: &#x27;The backend persists the user-owned workflow and its first immutable version row. WorkflowMutationHook fires post-commit.&#x27;, &#x27;returns&#x27;: &#x27;Workflow copy&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-29</code>: Import uses the canonical nested contract and cleanly separates malformed YAML failures from schema-level warnings.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;Choose YAML file&#x27;, &#x27;description&#x27;: &#x27;The user selects a `.yaml` or `.yml` file from the import button.&#x27;, &#x27;returns&#x27;: &#x27;File payload&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows/import&#x27;, &#x27;description&#x27;: &#x27;The frontend uploads the file or raw YAML body.&#x27;, &#x27;returns&#x27;: &#x27;Import request&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-5&#x27;, &#x27;action&#x27;: &#x27;safe_load + model validation&#x27;, &#x27;description&#x27;: &#x27;The backend first parses YAML, then validates the nested contract and edge-only hook wiring against iriai-compose models.&#x27;, &#x27;returns&#x27;: &#x27;Validated WorkflowConfig or parse/validation errors&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;INSERT Workflow + WorkflowVersion v1 on success&#x27;, &#x27;description&#x27;: &#x27;Successful imports persist the workflow snapshot and version 1 in one transaction. No `workflow_entity_refs` rows are written by SF-5.&#x27;, &#x27;returns&#x27;: &#x27;Persisted workflow&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &quot;Fire WorkflowMutationHook(workflow_id, &#x27;imported&#x27;) on success&quot;, &#x27;description&#x27;: &#x27;After transaction commit, the backend fires the mutation hook for SF-7 handler invocation. Skipped entirely on validation failure.&#x27;, &#x27;returns&#x27;: &#x27;Hook handlers invoked&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;Return 201 or 400&#x27;, &#x27;description&#x27;: &#x27;Malformed YAML returns 400 with parser details; schema-valid imports return 201 and may include warning rows without blocking persistence.&#x27;, &#x27;returns&#x27;: &#x27;ImportResult or import error payload&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-30</code>: The editor serializes its flat state back to canonical nested YAML, validates it, stores an immutable version snapshot, and fires the mutation hook.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;action&#x27;: &#x27;Serialize flat canvas state&#x27;, &#x27;description&#x27;: &#x27;The frontend groups nodes by phase and emits canonical `phases[].nodes`, `phases[].children`, and `edges[]` with no persisted `port_type`.&#x27;, &#x27;returns&#x27;: &#x27;yaml_content&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows/{id}/validate&#x27;, &#x27;description&#x27;: &#x27;The frontend asks the backend to validate the serialized YAML before committing a version snapshot.&#x27;, &#x27;returns&#x27;: &#x27;ValidationResult: { valid, errors[] }&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-5&#x27;, &#x27;action&#x27;: &#x27;Validate via declarative models&#x27;, &#x27;description&#x27;: &#x27;The backend uses the same iriai-compose models and helpers that drive `/api/schema/workflow` and import.&#x27;, &#x27;returns&#x27;: &#x27;Validation result&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows/{id}/versions&#x27;, &#x27;description&#x27;: &#x27;After a valid response, the frontend submits the same YAML snapshot as a new immutable version.&#x27;, &#x27;returns&#x27;: &#x27;Version create request&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;INSERT WorkflowVersion and update workflow current snapshot&#x27;, &#x27;description&#x27;: &quot;The backend appends a version row and updates the workflow&#x27;s `yaml_content` and `current_version` in one transaction. No `workflow_entity_refs` rows are written by SF-5.&quot;, &#x27;returns&#x27;: &#x27;WorkflowVersion&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &quot;Fire WorkflowMutationHook(workflow_id, &#x27;version_saved&#x27;)&quot;, &#x27;description&#x27;: &quot;After the transaction commits, the backend fires the mutation hook. SF-7&#x27;s registered handler rebuilds the reference index for this workflow_id.&quot;, &#x27;returns&#x27;: &#x27;Hook handlers invoked&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-31</code>: Documents the complete SF-5→SF-6 edge: SF-5&#x27;s TypeScript barrel export provides all compile-time types; the runtime schema endpoint provides the authoritative JSON Schema with context_keys and correct AskNode fields.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-3&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Compile-time TypeScript barrel export&#x27;, &#x27;description&#x27;: &#x27;SF-5 exports from `tools/compose/frontend/src/types/index.ts`: Workflow, WorkflowVersion, Role, OutputSchema, CustomTaskTemplate, StarterTemplate, PaginatedList&lt;T&gt;, ValidationIssue, ValidationResult, ImportResult, WorkflowEntityRefsResponse, EntityUsageReport, DeletePreflightConflict. SF-6 imports exclusively from this path — no imports from deeper module paths.&#x27;, &#x27;returns&#x27;: &#x27;TypeScript interface types bound at compile time&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;GET /api/schema/workflow (editor mount)&#x27;, &#x27;description&#x27;: &#x27;SF-6 canvas fetches the authoritative WorkflowConfig JSON Schema using the shared Axios client from `src/api/client.ts`. This is the only schema source SF-6 may use — a bundled static schema is not permitted for runtime node palette construction.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig JSON Schema document&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-5&#x27;, &#x27;action&#x27;: &#x27;WorkflowConfig.model_json_schema() from iriai_compose.schema&#x27;, &#x27;description&#x27;: &#x27;Backend derives schema from the `iriai_compose.schema` subpackage (SF-1). Schema includes: optional `context_keys: string[]` at WorkflowConfig root; AskNode with required `task: string` and optional `context_text: string` (not `prompt`); BranchNode with per-port condition expressions; no `stores`, `plugin_instances`, `inputs`, `outputs` at root.&#x27;, &#x27;returns&#x27;: &#x27;Schema dict cached in application state&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Return WorkflowConfig JSON Schema&#x27;, &#x27;description&#x27;: &#x27;Backend returns the full JSON Schema. SF-6 must validate that the schema contains context_keys and AskNode.task before marking the editor as ready.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowConfig JSON Schema (200)&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Map schema to node palette&#x27;, &#x27;description&#x27;: &#x27;SF-6 canvas maps the JSON Schema discriminated union to the editor node palette: AgentNode, AskNode (task+context_text fields), BranchNode (per-port conditions), CustomNode, EvalNode. Caches schema; subscribes to version stamp for invalidation on backend redeploy.&#x27;, &#x27;returns&#x27;: &#x27;Node palette ready; editor unlocked for workflow load&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-32</code>: Documents the complete SF-5→SF-7 edge: SF-7 registers its reference-index handler into SF-5&#x27;s WorkflowMutationHookRegistry at app startup, then the chain fires on every workflow write to rebuild workflow_entity_refs.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-10&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;Register refresh_entity_refs handler at FastAPI lifespan startup&#x27;, &#x27;description&#x27;: &#x27;SF-7 calls `mutation_hook_registry.register(refresh_entity_refs)` during the FastAPI @asynccontextmanager lifespan startup block. The handler signature is `async def refresh_entity_refs(workflow_id: str, operation: WorkflowMutationOperation) -&gt; None`. SF-7 imports `mutation_hook_registry` from `app/state.py` and ORM models from `app/models.py`.&#x27;, &#x27;returns&#x27;: &#x27;Handler stored in registry&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;POST /api/workflows/{id}/versions { yaml_content, change_description }&#x27;, &#x27;description&#x27;: &#x27;SF-6 canvas submits a validated YAML snapshot as a new immutable version.&#x27;, &#x27;returns&#x27;: &#x27;Version create request&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;INSERT workflow_versions; UPDATE workflows.yaml_content in one transaction&#x27;, &#x27;description&#x27;: &#x27;SF-5 appends the version row and updates the workflow snapshot atomically.&#x27;, &#x27;returns&#x27;: &#x27;Committed transaction&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-10&#x27;, &#x27;action&#x27;: &quot;mutation_hook_registry.fire(workflow_id, &#x27;version_saved&#x27;) post-commit&quot;, &#x27;description&#x27;: &#x27;SF-5 invokes all registered handlers after the transaction commits. Handler exceptions are caught per-handler, logged via structlog, and never propagated to the HTTP response.&#x27;, &#x27;returns&#x27;: &#x27;Handler invocations dispatched (exceptions swallowed)&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-10&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;Parse yaml_content; DELETE prior refs; bulk INSERT workflow_entity_refs rows&#x27;, &#x27;description&#x27;: &#x27;SF-7 handler loads yaml_content via PyYAML, walks all PhaseDefinition.nodes extracting role_id, output_schema_id, and template_id references, deletes all existing workflow_entity_refs rows for this workflow_id, then inserts fresh rows. Operation is idempotent — safe to re-invoke.&#x27;, &#x27;returns&#x27;: &#x27;Reference index rebuilt; workflow_entity_refs current&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-33</code>: Documents the SF-7→SF-6 edge: SF-7 extension endpoints consumed by SF-6 for usage display and blocking delete preflights. Covers both the pre-dialog usage fetch and the 409 guard on the delete endpoint itself.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-1&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Click Delete Role (or schema / template)&#x27;, &#x27;description&#x27;: &#x27;User initiates delete from the library entity list. SF-6 intercepts the action before calling DELETE to fetch usage count.&#x27;, &#x27;returns&#x27;: &#x27;Delete intent&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;GET /api/roles/{id}/usage&#x27;, &#x27;description&#x27;: &#x27;SF-6 calls the SF-7-owned usage endpoint via the shared Axios client. No separate HTTP client is configured for SF-7 routes.&#x27;, &#x27;returns&#x27;: &#x27;GET usage request&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &quot;SELECT workflow_entity_refs WHERE entity_type=&#x27;role&#x27; AND entity_id=?&quot;, &#x27;description&#x27;: &#x27;SF-7 router handler queries the materialized reference index.&#x27;, &#x27;returns&#x27;: &#x27;Ref rows&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Return EntityUsageReport&#x27;, &#x27;description&#x27;: &quot;Backend returns: `{ entity_id, entity_type: &#x27;role&#x27;, referenced_by: [{ workflow_id, workflow_name, node_ids[] }], total_references: number }`.&quot;, &#x27;returns&#x27;: &#x27;EntityUsageReport (200)&#x27;}</li><li>{&#x27;sequence&#x27;: 5, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Render DeleteEntityDialog with usage&#x27;, &#x27;description&#x27;: &#x27;SF-6 renders DeleteEntityDialog (SF-7 component). If total_references &gt; 0, the dialog shows a blocking warning listing referenced workflows with links. The confirm button is disabled when blocking references exist — this is a blocking error state, not a dismissible warning.&#x27;, &#x27;returns&#x27;: &#x27;Dialog displayed; user decides&#x27;}</li><li>{&#x27;sequence&#x27;: 6, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;DELETE /api/roles/{id} (if user confirms with zero references)&#x27;, &#x27;description&#x27;: &quot;SF-6 calls the delete endpoint. SF-7&#x27;s `require_no_entity_refs` dependency runs first as a second line of enforcement — returns 409 if refs exist at delete time (race-condition guard). SF-6 handles 409 by re-rendering the blocking dialog.&quot;, &#x27;returns&#x27;: &#x27;204 No Content; or 409 DeletePreflightConflict: { detail: string, blocking_workflows: [{ id, name }] }&#x27;}</li></ol>
        </div><div class="call-path-block">
            <h4><code>CP-34</code>: Documents the SF-6→SF-7 edge for the editor reference panel: SF-6 fetches the workflow entity-refs index to display what library entities are used by the current workflow&#x27;s nodes. Index is already current because the hook chain (CP-SF5-8) ran on the last save.</h4>
            <ol class='call-path-steps'><li>{&#x27;sequence&#x27;: 1, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;action&#x27;: &#x27;GET /api/workflows/{id}/entity-refs&#x27;, &#x27;description&#x27;: &#x27;SF-6 canvas calls the SF-7-owned entity-refs endpoint when the editor reference panel is opened. Uses the shared Axios client — no separate client for SF-7 routes.&#x27;, &#x27;returns&#x27;: &#x27;GET entity-refs request&#x27;}</li><li>{&#x27;sequence&#x27;: 2, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-7&#x27;, &#x27;action&#x27;: &#x27;SELECT workflow_entity_refs WHERE workflow_id=?&#x27;, &#x27;description&#x27;: &#x27;SF-7 router handler reads the materialized index for this workflow.&#x27;, &#x27;returns&#x27;: &#x27;Ref rows with entity_type, entity_id, node_id, node_type, context&#x27;}</li><li>{&#x27;sequence&#x27;: 3, &#x27;from_service&#x27;: &#x27;SVC-SF5-4&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Return WorkflowEntityRefsResponse&#x27;, &#x27;description&#x27;: &#x27;Backend returns `{ workflow_id, refs: [{ entity_type, entity_id, node_id, node_type, context }], last_indexed_at }`.&#x27;, &#x27;returns&#x27;: &#x27;WorkflowEntityRefsResponse (200)&#x27;}</li><li>{&#x27;sequence&#x27;: 4, &#x27;from_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;to_service&#x27;: &#x27;SVC-SF5-9&#x27;, &#x27;action&#x27;: &#x27;Render reference panel grouped by entity_type&#x27;, &#x27;description&#x27;: &#x27;SF-6 displays the refs grouped by entity_type (Roles, Output Schemas, Task Templates). Each entry links to the library entity detail. `last_indexed_at` is shown to indicate when the index was last rebuilt. If the workflow has unsaved changes, SF-6 shows a stale-index notice prompting the user to save first.&#x27;, &#x27;returns&#x27;: &#x27;Reference panel rendered&#x27;}</li></ol>
        </div>

    <h3>Entities</h3>
    <div class="entity-block">
            <h4><code>ENT-90</code>: Workflow</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>Stable workflow identifier.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>User-visible workflow name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>string | null</code></td>
                        <td>Optional summary shown in list views.</td>
                    </tr><tr>
                        <td><code>yaml_content</code></td>
                        <td><code>text</code></td>
                        <td>Canonical nested YAML document. Phases persist their own `nodes` and nested `children`; hook links remain normal edges with `source`, `target`, and optional `transform_fn` only. Optional workflow-level `context_keys` field included when present.</td>
                    </tr><tr>
                        <td><code>current_version</code></td>
                        <td><code>integer</code></td>
                        <td>Latest immutable version number; mirrors the latest `workflow_versions.version_number`.</td>
                    </tr><tr>
                        <td><code>is_valid</code></td>
                        <td><code>boolean</code></td>
                        <td>Current validation status from server-side schema checks.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>string</code></td>
                        <td>JWT `sub` for ownership scoping.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>datetime</code></td>
                        <td>Creation timestamp.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Most recent metadata or YAML update.</td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Soft-delete marker.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-91</code>: WorkflowVersion</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>Stable version identifier.</td>
                    </tr><tr>
                        <td><code>workflow_id</code></td>
                        <td><code>UUID</code></td>
                        <td>Parent workflow.</td>
                    </tr><tr>
                        <td><code>version_number</code></td>
                        <td><code>integer</code></td>
                        <td>Monotonic version number.</td>
                    </tr><tr>
                        <td><code>yaml_content</code></td>
                        <td><code>text</code></td>
                        <td>Exact nested YAML snapshot saved at that point in time.</td>
                    </tr><tr>
                        <td><code>change_description</code></td>
                        <td><code>string | null</code></td>
                        <td>Optional human change note.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>string</code></td>
                        <td>JWT `sub` of the user who created the snapshot.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>datetime</code></td>
                        <td>Snapshot timestamp.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-92</code>: Role</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>Stable role identifier.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Display name used by the role builder and pickers.</td>
                    </tr><tr>
                        <td><code>prompt</code></td>
                        <td><code>text</code></td>
                        <td>Role prompt content. Field name is `prompt`, not `system_prompt` — matches the iriai-compose Role contract.</td>
                    </tr><tr>
                        <td><code>model</code></td>
                        <td><code>string | null</code></td>
                        <td>Optional model override (e.g. `claude-sonnet-4-6`).</td>
                    </tr><tr>
                        <td><code>effort</code></td>
                        <td><code>string | null</code></td>
                        <td>Optional effort level hint passed to the AgentRuntime. Matches the iriai-compose Role `effort` field.</td>
                    </tr><tr>
                        <td><code>tools</code></td>
                        <td><code>json</code></td>
                        <td>Allowed tool identifiers as a JSON list of strings.</td>
                    </tr><tr>
                        <td><code>metadata</code></td>
                        <td><code>json</code></td>
                        <td>Extensible role metadata as a JSON object.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>string</code></td>
                        <td>Owner JWT `sub`.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>datetime</code></td>
                        <td>Creation timestamp.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Most recent update.</td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Soft-delete marker.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-93</code>: OutputSchema</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>Stable schema identifier.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Display name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>string | null</code></td>
                        <td>Optional schema summary.</td>
                    </tr><tr>
                        <td><code>json_schema</code></td>
                        <td><code>json</code></td>
                        <td>Reusable JSON Schema document for node and workflow output contracts.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>string</code></td>
                        <td>Owner JWT `sub`.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>datetime</code></td>
                        <td>Creation timestamp.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Most recent update.</td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Soft-delete marker.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-94</code>: CustomTaskTemplate</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>UUID</code></td>
                        <td>Stable task template identifier.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Display name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>string | null</code></td>
                        <td>Optional summary.</td>
                    </tr><tr>
                        <td><code>subgraph_yaml</code></td>
                        <td><code>text</code></td>
                        <td>Canonical nested YAML subgraph using the same `nodes`, `children`, and edge-only hook contract as full workflows.</td>
                    </tr><tr>
                        <td><code>input_interface</code></td>
                        <td><code>json</code></td>
                        <td>Declared template input contract.</td>
                    </tr><tr>
                        <td><code>output_interface</code></td>
                        <td><code>json</code></td>
                        <td>Declared template output contract.</td>
                    </tr><tr>
                        <td><code>user_id</code></td>
                        <td><code>string</code></td>
                        <td>Owner JWT `sub`.</td>
                    </tr><tr>
                        <td><code>created_at</code></td>
                        <td><code>datetime</code></td>
                        <td>Creation timestamp.</td>
                    </tr><tr>
                        <td><code>updated_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Most recent update.</td>
                    </tr><tr>
                        <td><code>deleted_at</code></td>
                        <td><code>datetime | null</code></td>
                        <td>Soft-delete marker. SF-7 may extend this table with an `actor_slots` column via additive Alembic migration.</td>
                    </tr></tbody>
            </table>
        </div><div class="entity-block">
            <h4><code>ENT-95</code>: StarterTemplate</h4>
            <p></p>
            <table class="fields-table">
                <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
                <tbody><tr>
                        <td><code>id</code></td>
                        <td><code>string</code></td>
                        <td>Stable starter identifier.</td>
                    </tr><tr>
                        <td><code>name</code></td>
                        <td><code>string</code></td>
                        <td>Starter display name.</td>
                    </tr><tr>
                        <td><code>description</code></td>
                        <td><code>string</code></td>
                        <td>Landing-page summary.</td>
                    </tr><tr>
                        <td><code>yaml_content</code></td>
                        <td><code>text</code></td>
                        <td>Canonical nested YAML starter payload derived from iriai-build-v2 reference workflows.</td>
                    </tr><tr>
                        <td><code>category</code></td>
                        <td><code>string</code></td>
                        <td>Landing-page grouping tag.</td>
                    </tr></tbody>
            </table>
        </div>

    <h3>Entity Relations</h3>
    <table class="data-table">
        <thead><tr>
            <th>ID</th><th>From</th><th>Relation</th><th>To</th><th>Description</th>
        </tr></thead>
        <tbody><tr>
            <td><code>ER-80</code></td>
            <td><code>ENT-SF5-1</code></td>
            <td></td>
            <td><code>ENT-SF5-2</code></td>
            <td></td>
        </tr></tbody>
    </table>

    <h3>Decisions</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Decision</th></tr></thead>
        <tbody><tr>
            <td><code>D-77</code></td>
            <td>D-GR-22: `GET /api/schema/workflow` is the canonical composer schema source. Bundled `workflow-schema.json` is build/test-only and must never drive runtime editor behavior.</td>
        </tr><tr>
            <td><code>D-78</code></td>
            <td>D-SF5-1: Workflow persistence is canonical nested YAML. Phases store local `nodes` plus nested `children`; the frontend may flatten to React Flow state internally but never writes that shape to storage or over the API.</td>
        </tr><tr>
            <td><code>D-79</code></td>
            <td>D-SF5-2: Hook wiring is serialized only through `edges[]` rows using `source`, `target`, and optional `transform_fn`. No separate serialized hooks section and no persisted `port_type` field exist in backend contracts.</td>
        </tr><tr>
            <td><code>D-80</code></td>
            <td>D-SF5-3: The accepted repo topology is `tools/compose/backend` for the FastAPI service, `tools/compose/frontend` for the compose SPA, and `platform/toolshub/frontend` for the static tools hub. The `tools/iriai-workflows` path is not part of the approved implementation contract and must not appear in SF-5 artifacts.</td>
        </tr><tr>
            <td><code>D-81</code></td>
            <td>D-SF5-4: The backend derives schema export and YAML validation from the same iriai-compose declarative models (`iriai_compose.schema`, created by SF-1) to avoid contract drift between authoring and execution.</td>
        </tr><tr>
            <td><code>D-82</code></td>
            <td>D-SF5-5: PostgreSQL is the foundation database for SF-5, managed through SQLAlchemy 2.x (async) and Alembic. The migration chain is isolated to the `alembic_version_compose` version table and must not share a version table with deploy-console or any other platform service. SQLite is out of scope.</td>
        </tr><tr>
            <td><code>D-83</code></td>
            <td>D-SF5-6: WorkflowVersion is append-only and is created on workflow create, starter-template use, import, and duplicate (v1) and on explicit save-version (vN). Every workflow mutation has an auditable snapshot. Version-history UI is deferred to a later subfeature.</td>
        </tr><tr>
            <td><code>D-84</code></td>
            <td>D-SF5-7: SF-5 exposes a `WorkflowMutationHook(workflow_id: str, operation: Literal[&#x27;created&#x27;,&#x27;imported&#x27;,&#x27;version_saved&#x27;,&#x27;deleted&#x27;])` callable registry. After each workflow write transaction commits, SF-5 fires all registered handlers. SF-7 registers its reference-index refresh handler at application startup. SF-5 must never directly write or read `workflow_entity_refs` rows.</td>
        </tr><tr>
            <td><code>D-85</code></td>
            <td>D-SF5-8: Plugins remain runtime and YAML concerns owned by iriai-compose and consuming projects; SF-5 does not create plugin tables, plugin-management surfaces, or `/api/plugins` endpoints.</td>
        </tr><tr>
            <td><code>D-86</code></td>
            <td>D-SF5-9: SF-5 owns exactly five foundation tables (`workflows`, `workflow_versions`, `roles`, `output_schemas`, `custom_task_templates`). `workflow_entity_refs` is a SF-7-owned extension table. SF-5 migrations must not reference or create it. The five-table boundary is a hard constraint enforced at the Alembic level.</td>
        </tr><tr>
            <td><code>D-87</code></td>
            <td>D-EDGE-1 (SF-5→SF-6 TypeScript type contract): All SF-5→SF-6 type boundaries flow through a single barrel export at `tools/compose/frontend/src/types/index.ts`. SF-6 imports exclusively from this path — no direct imports from deeper module paths. Complete interface set: `interface Workflow { id: string; name: string; description: string | null; yaml_content: string; current_version: number; is_valid: boolean; user_id: string; created_at: string; updated_at: string | null; deleted_at: string | null }` — `interface WorkflowVersion { id: string; workflow_id: string; version_number: number; yaml_content: string; change_description: string | null; user_id: string; created_at: string }` — `interface Role { id: string; name: string; prompt: string; model: string | null; effort: string | null; tools: string[]; metadata: Record&lt;string,unknown&gt;; user_id: string; created_at: string; updated_at: string | null; deleted_at: string | null }` — `interface OutputSchema { id: string; name: string; description: string | null; json_schema: Record&lt;string,unknown&gt;; user_id: string; created_at: string; updated_at: string | null; deleted_at: string | null }` — `interface CustomTaskTemplate { id: string; name: string; description: string | null; subgraph_yaml: string; input_interface: Record&lt;string,unknown&gt;; output_interface: Record&lt;string,unknown&gt;; user_id: string; created_at: string; updated_at: string | null; deleted_at: string | null }` — `interface StarterTemplate { id: string; name: string; description: string; yaml_content: string; category: &#x27;starter&#x27; }` — `interface PaginatedList&lt;T&gt; { items: T[]; next_cursor: string | null; has_more: boolean }` — `interface ValidationIssue { path: string; message: string; severity: &#x27;error&#x27; | &#x27;warning&#x27; }` — `interface ValidationResult { valid: boolean; errors: ValidationIssue[] }` — `interface ImportResult { workflow: Workflow; validation_warnings?: ValidationIssue[] }`</td>
        </tr><tr>
            <td><code>D-88</code></td>
            <td>D-EDGE-2 (SF-5→SF-6 schema field contract): `GET /api/schema/workflow` derives from `WorkflowConfig.model_json_schema()` in `iriai_compose.schema` (SF-1&#x27;s new subpackage — not `iriai_compose.declarative` which does not exist). Authoritative field constraints for SF-6 node palette construction: (1) WorkflowConfig root includes optional `context_keys: string[]` per D-GR-39/SF-1→SF-4 contract — SF-6 must expose a workflow-level context_keys editor; (2) AskNode has required field `task: string` and optional field `context_text: string` — the field name `prompt` is not valid for AskNode and must not appear in SF-6 AskNode form fields; (3) BranchNode uses per-port condition expressions per D-GR-35 — no `switch_function` or `output_field` fields; (4) WorkflowConfig root has no `stores`, `plugin_instances`, `inputs`, or `outputs` fields; (5) existing iriai-compose exports `Phase` (ABC) and `Role` (dataclass) — these are runtime primitives distinct from the declarative `PhaseDefinition` and `WorkflowConfig` Pydantic models that SF-5 imports; (6) phantom exports MapNode, FoldNode, LoopNode, TransformRef, HookRef do not exist and must not appear in any SF-5 or SF-6 import statement.</td>
        </tr><tr>
            <td><code>D-89</code></td>
            <td>D-EDGE-3 (SF-5→SF-7 Python hook interface): SF-5 creates and exports a `WorkflowMutationHookRegistry` singleton at `tools/compose/backend/app/state.py::mutation_hook_registry`. Complete Python contract: `WorkflowMutationOperation = Literal[&#x27;created&#x27;, &#x27;imported&#x27;, &#x27;version_saved&#x27;, &#x27;deleted&#x27;]`; `MutationHandler = Callable[[str, WorkflowMutationOperation], Awaitable[None]]`; `class WorkflowMutationHookRegistry: def register(self, handler: MutationHandler) -&gt; None: ...; async def fire(self, workflow_id: str, operation: WorkflowMutationOperation) -&gt; None: ...`. `fire()` catches per-handler exceptions, logs via structlog, never propagates. `fire()` is called only after the primary workflow transaction has committed. SF-7 calls `mutation_hook_registry.register(refresh_entity_refs)` in a FastAPI `@asynccontextmanager` lifespan function. Handler signature: `async def refresh_entity_refs(workflow_id: str, operation: WorkflowMutationOperation) -&gt; None`. On `&#x27;deleted&#x27;` the handler purges all `workflow_entity_refs` rows for `workflow_id`. On `&#x27;created&#x27;`, `&#x27;imported&#x27;`, `&#x27;version_saved&#x27;` it scans `yaml_content` and upserts rows. Operation is idempotent.</td>
        </tr><tr>
            <td><code>D-90</code></td>
            <td>D-EDGE-4 (SF-5→SF-7 ORM model contract): SF-5 exposes the following SQLAlchemy 2.x async ORM models from `tools/compose/backend/app/models.py`: `WorkflowORM` (table: `workflows`), `WorkflowVersionORM` (table: `workflow_versions`), `RoleORM` (table: `roles`), `OutputSchemaORM` (table: `output_schemas`), `CustomTaskTemplateORM` (table: `custom_task_templates`). SF-7 imports these models and the `get_db` AsyncSession factory from `tools/compose/backend/app/database.py`. SF-7 must not redefine session management, re-declare table DDL, or modify the five foundation tables in its own migrations. SF-7&#x27;s only DDL rights are: (1) CREATE TABLE `workflow_entity_refs`, (2) ALTER TABLE `custom_task_templates` ADD COLUMN `actor_slots` — both via separate SF-7 Alembic revision files chained after the SF-5 initial revision in the `alembic_version_compose` chain.</td>
        </tr><tr>
            <td><code>D-91</code></td>
            <td>D-EDGE-5 (SF-7→SF-6 reference endpoint contract): SF-7 adds four read-only endpoints to the compose backend router at `app/routers/entity_refs.py`. All four require JWT Bearer auth and are called by SF-6 via the shared Axios client at `tools/compose/frontend/src/api/client.ts` — no separate HTTP client setup. TypeScript response shapes exported from `tools/compose/frontend/src/types/index.ts`: `interface WorkflowEntityRefsResponse { workflow_id: string; refs: Array&lt;{ entity_type: &#x27;role&#x27; | &#x27;output_schema&#x27; | &#x27;custom_task_template&#x27;; entity_id: string; node_id: string; node_type: string; context: string }&gt;; last_indexed_at: string }` — `interface EntityUsageReport { entity_id: string; entity_type: &#x27;role&#x27; | &#x27;output_schema&#x27; | &#x27;custom_task_template&#x27;; referenced_by: Array&lt;{ workflow_id: string; workflow_name: string; node_ids: string[] }&gt;; total_references: number }`. Endpoint paths: `GET /api/workflows/{id}/entity-refs → WorkflowEntityRefsResponse`; `GET /api/roles/{id}/usage → EntityUsageReport`; `GET /api/schemas/{id}/usage → EntityUsageReport`; `GET /api/templates/{id}/usage → EntityUsageReport`.</td>
        </tr><tr>
            <td><code>D-92</code></td>
            <td>D-EDGE-6 (SF-7→SF-6 delete preflight guard contract): SF-7 injects a FastAPI dependency `require_no_entity_refs(entity_id, entity_type)` into SF-5&#x27;s `DELETE /api/roles/{id}`, `DELETE /api/schemas/{id}`, `DELETE /api/templates/{id}` handlers. On reference conflict (`total_references &gt;0`), returns HTTP 409 with: `interface DeletePreflightConflict { detail: string; blocking_workflows: Array&lt;{ id: string; name: string }&gt; }`. The `detail` string format is: `&quot;&lt;EntityType&gt; &#x27;&lt;name&gt;&#x27; is referenced by &lt;N&gt; workflow(s). Remove all references before deleting.&quot;` SF-6 component `DeleteEntityDialog` prop interface: `interface DeleteEntityDialogProps { entityId: string; entityType: &#x27;role&#x27; | &#x27;output_schema&#x27; | &#x27;custom_task_template&#x27;; entityName: string; usage: EntityUsageReport; onConfirm: () =&gt; void; onCancel: () =&gt; void }`. Dialog must: render a blocking error state (not a dismissible warning) when `usage.total_references &gt; 0`; display `referenced_by` list with links to each workflow; disable the confirm button when blocking references exist. The 409 guard is the authoritative server-side enforcement layer; the pre-dialog usage fetch is a UX convenience only.</td>
        </tr><tr>
            <td><code>D-93</code></td>
            <td>D-EDGE-7 (SF-6→SF-7 index rebuild path): SF-6 never calls a dedicated SF-7 index-rebuild endpoint. Reference index rebuilds are triggered exclusively by SF-6&#x27;s save operations flowing through SF-5&#x27;s WorkflowMutationHook chain: `POST /api/workflows` fires `&#x27;created&#x27;`; `POST /api/workflows/import` fires `&#x27;imported&#x27;`; `POST /api/workflows/{id}/versions` fires `&#x27;version_saved&#x27;`; `DELETE /api/workflows/{id}` fires `&#x27;deleted&#x27;`. SF-7&#x27;s `refresh_entity_refs` handler: on `&#x27;deleted&#x27;` executes `DELETE FROM workflow_entity_refs WHERE workflow_id = ?`; on `&#x27;created&#x27;`/`&#x27;imported&#x27;`/`&#x27;version_saved&#x27;` loads `workflows.yaml_content`, parses via PyYAML `safe_load`, walks all `phases[].nodes` extracting `role_id`, `output_schema_id`, and `template_id` fields, deletes all prior `workflow_entity_refs` rows for this `workflow_id`, and bulk-inserts fresh rows. The rebuild is idempotent and safe to re-invoke on duplicate hook fires. SF-6 calls SF-7 endpoints only for user-facing reference display (`GET /api/roles/{id}/usage` etc.) — never to trigger index mutations.</td>
        </tr></tbody>
    </table>

    <h3>Risks</h3>
    <table class="data-table">
        <thead><tr><th>ID</th><th>Risk</th></tr></thead>
        <tbody><tr>
            <td><code>RISK-41</code></td>
            <td>RISK-1 (high): Flat React Flow state may serialize back to nested YAML incorrectly, especially for deeply nested phases or hook edges. Mitigation: make import/export round-trip tests mandatory and validate every save against the same iriai-compose models used by import.</td>
        </tr><tr>
            <td><code>RISK-42</code></td>
            <td>RISK-2 (medium): Frontend schema caching can drift after backend deploys if `/api/schema/workflow` changes and stale local data survives. Mitigation: refetch schema on app boot, attach an ETag or version stamp, and invalidate cached editor metadata whenever the schema changes.</td>
        </tr><tr>
            <td><code>RISK-43</code></td>
            <td>RISK-3 (medium): PostgreSQL connection pool exhaustion under bursty concurrent write patterns (repeated explicit saves, parallel imports). Mitigation: configure SQLAlchemy async pool with `pool_size=5, max_overflow=10`; keep transactions short; avoid holding connections across validation calls; use asyncpg driver for efficient connection reuse.</td>
        </tr><tr>
            <td><code>RISK-44</code></td>
            <td>RISK-4 (medium): WorkflowMutationHook handlers registered by SF-7 could raise exceptions, causing silent reference-index staleness. Mitigation: SF-5 hook dispatcher must catch and log all handler exceptions without rolling back the primary workflow transaction; SF-7 handlers must be idempotent and safe to re-invoke; add observability metrics for hook invocation failures.</td>
        </tr><tr>
            <td><code>RISK-45</code></td>
            <td>RISK-5 (low): Starter template assets can drift from iriai-build-v2 translations or the canonical schema. Mitigation: validate the bundled starter payloads against iriai-compose `WorkflowConfig` during CI and on backend startup.</td>
        </tr><tr>
            <td><code>RISK-46</code></td>
            <td>RISK-6 (medium): SF-7&#x27;s `refresh_entity_refs` handler scans `yaml_content` via PyYAML on every workflow save. For large workflows with many nodes, this adds latency to the post-commit hook path. Mitigation: handler runs post-commit and asynchronously (exception-swallowed), so it does not block the HTTP response. Add a timeout guard to the handler so a slow YAML scan does not hold the database connection indefinitely.</td>
        </tr><tr>
            <td><code>RISK-47</code></td>
            <td>RISK-7 (low): The SF-7 delete preflight 409 guard and the pre-dialog usage fetch can race if another user saves a workflow that adds a reference between the usage fetch and the delete call. Mitigation: the server-side 409 guard is the authoritative enforcement; SF-6&#x27;s pre-dialog fetch is UX-only. The race window results in a 409 error after the user confirmed — SF-6 must handle this gracefully by re-rendering the blocking dialog with fresh usage data.</td>
        </tr><tr>
            <td><code>RISK-48</code></td>
            <td>RISK-8 (low): `iriai_compose.schema` module (SF-1) does not exist yet — SF-5&#x27;s backend has a hard dependency on SF-1 completing first. Mitigation: SF-5 backend implementation is blocked until SF-1 exports `WorkflowConfig` from `iriai_compose.schema`. This dependency must be reflected in the implementation task DAG.</td>
        </tr></tbody>
    </table>
</section>
<hr/>
