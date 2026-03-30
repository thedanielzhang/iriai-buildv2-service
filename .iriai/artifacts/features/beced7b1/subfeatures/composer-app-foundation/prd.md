<!-- SF: composer-app-foundation -->

#### REQ-75: functional (must)
SF-5 must follow the accepted repo topology: `tools/compose/backend` for the FastAPI backend, `tools/compose/frontend` for the compose SPA, and `platform/toolshub/frontend` for the static tools hub. `tools/iriai-workflows` is not part of the approved implementation path.

**Citations:**
- [decision] D-A3: "Repo topology is `tools/compose/backend`, `tools/compose/frontend`, and `platform/toolshub/frontend`." -- This is the accepted implementation contract and supersedes stale `tools/iriai-workflows` assumptions.


#### REQ-76: functional (must)
The compose backend must use a structured FastAPI service layout aligned with existing platform services: `app/main.py`, `app/config.py`, `app/database.py`, `app/models/`, `app/schemas/`, `app/routers/`, `app/dependencies/`, and `app/middleware/`, with Pydantic Settings-based environment configuration.

**Citations:**
- [code] platform/deploy-console/deploy-console-service/app/main.py:19: "Imports settings, database, logging, routers, and middleware from a structured FastAPI service layout." -- SF-5 should reuse the existing platform backend layout pattern instead of inventing a new service structure.


#### REQ-77: functional (must)
SF-5 persistence must use PostgreSQL with SQLAlchemy 2.x and Alembic as the schema source of truth, normalize `postgresql://` URLs to `postgresql+psycopg://`, and track migrations in the isolated `alembic_version_compose` table. SQLite is out of scope.

**Citations:**
- [code] platform/deploy-console/deploy-console-service/app/database.py:13: "Converts legacy postgresql:// URLs to postgresql+psycopg:// for psycopg3." -- SF-5 should follow the existing database URL normalization pattern used by platform services.
- [decision] D-A5: "PostgreSQL + Alembic is the compose foundation storage contract." -- Matches the approved platform direction and avoids stale SQLite drift.


#### REQ-78: functional (must)
SF-5 database scope is exactly five foundation tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`. SF-5 must not add plugin tables, a tools table, phase-template tables, or `workflow_entity_refs`.

**Citations:**
- [decision] D-SF5-R1: "SF-5 is rebased to the accepted `tools/compose` + PostgreSQL/Alembic contract and stays limited to exactly five foundation tables." -- The explicit revision request for the current artifact.
- [decision] D-SF5-R2: "Foundation-level `workflow_entity_refs` assumptions are removed from SF-5; reference-index expansion belongs to SF-7." -- Prevents scope contamination and keeps table ownership aligned with the accepted contract.


#### REQ-79: functional (must)
`WorkflowVersion` is a required audit entity. Workflow create, import, and duplicate operations must create version 1 atomically, and save-version behavior must remain append-only and immutable. Version writes do not trigger mutation hook events on the parent Workflow entity. Version-history browsing, diffing, and restore UI are out of scope for SF-5.

**Citations:**
- [decision] D-13: "Backend workflow version recording remains required in v1 even though version-history UI is deferred." -- Auditability survives the v1 UI scope cut.
- [decision] D-SF5-R6: "`version_saved` does not emit a Workflow entity hook — versions are append-only audit rows, not mutable entity state." -- Prevents SF-7 from over-counting entity mutations caused by version writes.


#### REQ-80: security (must)
All non-health backend endpoints must require JWT Bearer authentication validated against auth-service JWKS, derive `user_id` from `sub`, and expose `dev_tier` for tools-hub gating.

**Citations:**
- [code] platform/deploy-console/deploy-console-service/app/dependencies/auth.py:83: "JWKS cache and fetch path are defined for JWT validation against the auth service." -- SF-5 should follow the same platform JWKS validation pattern for backend auth.
- [code] platform/auth/auth-service/app/routers/oauth.py:1196: "The access token includes the `dev_tier` claim." -- Tools-hub tier gating depends on a real token claim already emitted by auth-service.


#### REQ-81: security (must)
`workflows`, `roles`, `output_schemas`, and `custom_task_templates` must be user-scoped and soft-deletable. Cross-user access attempts must return not-found semantics rather than leaking record existence. `workflow_versions` are immutable audit rows and are not soft-deleted independently.

**Citations:**
- [decision] D-2: "Soft-delete with recovery was chosen as the safer data-management model." -- Soft delete is a user-facing recovery requirement, not just an implementation preference.


#### REQ-82: functional (must)
The backend must provide the standard platform error envelope, public `GET /health`, public `GET /ready` with database readiness checks, and an explicit production CORS allow-list for compose and tools-hub browser origins rather than wildcard credentials configuration.

**Citations:**
- [code] platform/deploy-console/deploy-console-service/app/schemas/errors.py:29: "ErrorResponse standardizes `error`, `error_description`, and optional `details`." -- SF-5 should match the platform error envelope so downstream clients can handle failures consistently.


#### REQ-83: security (must)
SF-5 must implement per-user rate limiting and structured JSON logging with request correlation and auth/import/delete event coverage, while avoiding raw workflow YAML and prompt-body logging.

**Citations:**
- [code] platform/deploy-console/deploy-console-service/app/middleware/rate_limit.py:16: "Rate-limit key extraction uses JWT `sub` when present and falls back to remote address." -- SF-5 should bucket limits by authenticated user whenever possible.


#### REQ-84: functional (must)
The backend must expose authenticated workflow CRUD, search, and cursor-paginated list/detail endpoints for user-owned workflows.

**Citations:**
- [decision] D-3: "Cursor-based pagination was chosen for platform consistency." -- The workflow list should follow the same pagination contract as the rest of the platform.


#### REQ-85: functional (must)
The workflow API must support duplicate, import, export, starter-template retrieval, and save-version actions, subject to the following canonical contracts: (a) Import endpoint: `POST /api/workflows/import` is the collection-level creation endpoint — it creates a new user-owned workflow from uploaded YAML. `POST /api/workflows/{id}/import` is not a valid SF-5 endpoint. Import must reject malformed YAML with parse errors, may return validation warnings for schema-invalid YAML, and must never create partial workflow state. (b) Starter template persistence: Starter templates are system-owned rows in the `workflows` table with `user_id='__system__'` and `deleted_at=NULL`, seeded by an Alembic data migration that reads iriai-build-v2 planning/develop/bugfix YAML source files at migration time. No filesystem template assets are served by the compose backend at request time. `GET /api/workflows/templates` returns starter templates without user-scoping. Duplicating a template creates a new user-owned workflow; the system template row is never modified by user actions.

**Citations:**
- [decision] D-SF5-R5: "The canonical import endpoint is `POST /api/workflows/import` (collection-level creation). `POST /api/workflows/{id}/import` is not a valid SF-5 endpoint." -- Import is fundamentally a creation operation. Standardizing on the collection form removes the endpoint path ambiguity between plan and system design.
- [decision] D-SF5-R4: "Starter templates are persisted as `user_id='__system__'` rows in the `workflows` table, seeded by an Alembic data migration. Filesystem asset serving is not used." -- DB rows are queryable via the same API layer as user-owned workflows and support duplicate/versioning semantics consistently without a separate asset-serving surface.
- [decision] D-7: "Starter templates and user workflows both belong on the landing page." -- Starter-template delivery is a product requirement, not a developer convenience.


#### REQ-86: functional (should)
SF-5 must provide baseline authenticated CRUD/list endpoints for `roles`, `output_schemas`, and `custom_task_templates` using the same user-scoping and soft-delete conventions, while deferring advanced delete-reference checks and tool-library surfaces to SF-7.

**Citations:**
- [decision] D-SF5-R2: "Reference-index expansion and advanced delete-reference checks belong to SF-7." -- SF-5 ships foundation-level CRUD only; SF-7 builds the reference-safety layer on top.


#### REQ-87: functional (must)
`GET /api/schema/workflow` is the canonical runtime schema endpoint and must return `WorkflowConfig.model_json_schema()` from `iriai-compose`. `POST /api/workflows/{id}/validate` must validate against that same runtime contract and return path/message error details.

**Citations:**
- [decision] D-GR-22: "Runtime schema delivery comes from `/api/schema/workflow`; static `workflow-schema.json` is build/test only." -- This is the explicit cycle-4 resolution for schema delivery.
- [code] .iriai/artifacts/features/beced7b1/broad/architecture.md:510: "`@router.get("/api/schema/workflow")` returns `WorkflowConfig.model_json_schema()`." -- The broad architecture already assumes runtime schema delivery from the backend endpoint.


#### REQ-88: functional (must)
Persisted and exported workflow YAML must use the canonical nested workflow contract: phase-contained nodes under `phases[].nodes` / `phases[].children`, hook wiring represented through ordinary edges, and no serialized `port_type` or separate `hooks` section.

**Citations:**
- [decision] D-GR-22: "YAML remains nested; hook wiring remains edge-based with no separate `hooks` section and no serialized `port_type`." -- Nested phase containment and edge-only hook serialization are the authoritative YAML contract across all affected subfeatures.


#### REQ-89: functional (must)
The compose frontend must live in `tools/compose/frontend` as a React 18 + TypeScript + Vite SPA using React Router, React Query, `@homelocal/auth`, and an authenticated API client for the compose backend.

**Citations:**
- [decision] D-19: "Vite was chosen for the new greenfield frontends." -- Bundler selection is already resolved for the compose and tools-hub apps.


#### REQ-90: functional (must)
SF-5 must provide the compose shell and workflows landing experience with exactly four foundation folders in the Explorer-style sidebar: Workflows, Roles, Output Schemas, and Task Templates. Plugin pages, Tool Library pages, and reference-check UI do not ship in SF-5.

**Citations:**
- [decision] D-18: "Compose uses Explorer-style sidebar navigation." -- This is the approved shell pattern for the compose app.
- [code] .iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/design-decisions.md:13: "The sidebar shows Workflows, Roles, Output Schemas, and Task Templates, with no Plugins folder." -- The current SF-5 design decision artifact already removes stale plugin-folder assumptions.


#### REQ-91: functional (must)
The tools hub must live at `platform/toolshub/frontend` as a static authenticated SPA that reads `dev_tier`, renders a hardcoded developer-tools card catalog, and routes the Workflow Composer card to `compose.iriai.app` in the same tab.

**Citations:**
- [decision] D-10: "The tools hub uses a hardcoded tool-card array for the initial release." -- The first release does not require a separate backend for the tools hub catalog.


#### REQ-92: functional (must)
SF-5's service layer must expose a stable, in-process mutation hook interface for all four foundation entity types (`Workflow`, `Role`, `OutputSchema`, `CustomTaskTemplate`). Hooks are invoked synchronously after a successful database commit and emit one of exactly four event kinds — `created`, `updated`, `soft_deleted`, `restored` — together with the entity type, entity id, and `user_id`. This enumeration is exhaustive: `imported`, `version_saved`, `deleted`, and any other event names are not valid hook events (import maps to `created`; a soft-delete is `soft_deleted` — there is no separate `deleted` kind; version writes do not trigger entity hooks). Hooks must cover all four entity types; a Workflow-only implementation is not sufficient. SF-7 (or any downstream extension) registers refresh callbacks against this interface without modifying SF-5 service code. SF-5 must never create or update `workflow_entity_refs` rows — that responsibility belongs entirely to SF-7 via this hook interface.

**Citations:**
- [decision] D-SF5-R3: "SF-5 exposes an in-process, post-commit mutation hook interface on all four foundation entity types; SF-7 subscribes to those hooks to maintain `workflow_entity_refs`. SF-5 never creates reference-index rows directly." -- Cleanly separates SF-5 from SF-7 without tight coupling while giving SF-7 a reliable trigger surface.
- [decision] D-SF5-R6: "The mutation hook event type enumeration is exhaustive: `created`, `updated`, `soft_deleted`, `restored`. No other event kinds exist." -- Removes the contradiction between plan and system design event-type lists and gives SF-7 a stable, closed interface to code against.


### SF-6: Workflow Editor & Canvas
<!-- SF: composer-app-foundation -->

#### AC-61
- **User Action:** An engineer inspects the approved SF-5 file/repo contract.
- **Expected:** The compose backend/frontend map to `tools/compose/{backend,frontend}`, the tools hub maps to `platform/toolshub/frontend`, and SF-5 does not depend on `tools/iriai-workflows`.
- **Not Criteria:** New SF-5 implementation work is planned under `tools/iriai-workflows`.
- **Requirements:** REQ-75, REQ-89, REQ-91
- **Citations:** - [decision] D-A3: "Repo topology is `tools/compose/backend`, `tools/compose/frontend`, and `platform/toolshub/frontend`." -- This is the accepted implementation contract and supersedes stale `tools/iriai-workflows` assumptions.


#### AC-62
- **User Action:** An engineer inspects the initial Alembic chain and database contract.
- **Expected:** The migration chain uses PostgreSQL, tracks revisions in `alembic_version_compose`, and creates exactly five SF-5 tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`.
- **Not Criteria:** SQLite remains the foundation engine, or plugin/tools/reference-index tables are created in SF-5.
- **Requirements:** REQ-77, REQ-78
- **Citations:** - [decision] D-A5: "PostgreSQL + Alembic is the compose foundation storage contract." -- Matches the approved platform direction and avoids stale SQLite drift.


#### AC-63
- **User Action:** The user opens `tools.iriai.app`, authenticates, and clicks Workflow Composer.
- **Expected:** The tools hub shows the composer card and same-tab navigation lands on `compose.iriai.app` with the authenticated compose shell available.
- **Not Criteria:** Protected tool states are visible before auth resolves, or the composer opens in a new tab.
- **Requirements:** REQ-90, REQ-91
- **Citations:** - [decision] D-10: "The tools hub uses hardcoded tool cards for the initial tool catalog." -- The initial tools-hub experience is card-driven and does not depend on a backend catalog.


#### AC-64
- **User Action:** The user creates a workflow from the Workflows view.
- **Expected:** A workflow row and `WorkflowVersion` v1 are created atomically, and the workflow appears without a full-page reload.
- **Not Criteria:** A workflow is created without version 1, or the user must refresh to see it.
- **Requirements:** REQ-79, REQ-84, REQ-85
- **Citations:** - [decision] D-13: "Backend workflow version recording remains required in v1 for the audit trail." -- Creation is incomplete unless the initial version row exists immediately.


#### AC-65
- **User Action:** An authenticated caller requests `GET /api/schema/workflow`.
- **Expected:** The backend returns JSON Schema generated from `WorkflowConfig.model_json_schema()`.
- **Not Criteria:** A bundled static file is treated as the canonical runtime response.
- **Requirements:** REQ-87
- **Citations:** - [decision] D-GR-22: "`/api/schema/workflow` is the canonical schema delivery path for the composer." -- This is the direct acceptance test for the cycle-4 schema-source decision.


#### AC-66
- **User Action:** A user opens `/workflows/{id}/edit`.
- **Expected:** The frontend requests the workflow record and `GET /api/schema/workflow` before rendering schema-dependent editing affordances.
- **Not Criteria:** The editor boots from a stale bundled schema or skips the runtime schema request.
- **Requirements:** REQ-87, REQ-89
- **Citations:** - [code] .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:261: "Editor boot requests the workflow record and `GET /api/schema/workflow`." -- SF-6 already assumes this backend integration point.


#### AC-67
- **User Action:** The user saves or exports a workflow with nested phases and hook edges.
- **Expected:** Persisted/exported YAML uses nested phase containment and edge-only hook serialization with no serialized `port_type`.
- **Not Criteria:** Save/export emits a flat root graph, a separate hooks section, or persisted `port_type`.
- **Requirements:** REQ-79, REQ-85, REQ-88
- **Citations:** - [decision] D-GR-22: "YAML remains nested and hook wiring remains edge-based with no serialized `port_type`." -- Both structural and hook-serialization assertions are part of the same resolved contract.


#### AC-68
- **User Action:** The user invokes `POST /api/workflows/import` with malformed YAML.
- **Expected:** Import returns parse errors and no workflow or version rows are created.
- **Not Criteria:** Partial workflow or version rows are persisted; the endpoint path used is `POST /api/workflows/{id}/import`; or the user receives only a generic failure with no path/message context.
- **Requirements:** REQ-85, REQ-88
- **Citations:** - [decision] D-SF5-R5: "The canonical import endpoint is `POST /api/workflows/import` (collection-level creation)." -- AC-8 must reference the canonical endpoint to verify the path standardization decision.
- [code] .iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/plan.md:608: "Import is a first-class endpoint with dedicated validation behavior." -- Import should be all-or-nothing and should not persist partial invalid workflows.


#### AC-69
- **User Action:** User A attempts to access User B's workflow by ID.
- **Expected:** The API returns a not-found response.
- **Not Criteria:** The API returns a permission error that confirms the record exists.
- **Requirements:** REQ-80, REQ-81
- **Citations:** - [code] .iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/plan.md:553: "Ownership checks return 404, not 403." -- The current SF-5 plan already defines the security posture for cross-user access.


#### AC-70
- **User Action:** An engineer inspects the SF-5 API surface.
- **Expected:** Workflow endpoints plus baseline role/schema/task-template CRUD exist, while `/api/plugins`, `/api/tools`, and `/api/{entity}/references/{id}` are absent from SF-5.
- **Not Criteria:** Plugin, tools, or reference-index surfaces are introduced as foundation APIs.
- **Requirements:** REQ-78, REQ-86, REQ-90
- **Citations:** - [decision] D-55: "Plugin database entities and `/api/plugins` are removed from SF-5." -- The current SF-5 design decisions already lock the API surface boundaries.


#### AC-71
- **User Action:** An engineer inspects production-ready backend behavior.
- **Expected:** `GET /health` and `GET /ready` exist, readiness checks database connectivity, and production CORS is limited to the compose/tools hub browser origins.
- **Not Criteria:** Production uses wildcard credentialed CORS or lacks a readiness check.
- **Requirements:** REQ-82
- **Citations:** - [code] platform/deploy-console/deploy-console-service/app/main.py:11: "FastAPI app wiring includes global error handling and middleware at the service entry point." -- Health and CORS behavior belong in the same service-level integration surface.


#### AC-72
- **User Action:** An authenticated caller exceeds the per-user API limit.
- **Expected:** The API returns `429` with retry guidance and logs the event without storing raw YAML or prompt bodies.
- **Not Criteria:** Rate limits are global-only, or structured logs capture full workflow bodies.
- **Requirements:** REQ-83
- **Citations:** - [code] platform/deploy-console/deploy-console-service/app/middleware/rate_limit.py:50: "Rate-limit handler returns HTTP 429 and logs rate-limit events." -- SF-5 should match the existing platform behavior for limit enforcement and logging.


#### AC-73
- **User Action:** An engineer inspects SF-5's service layer.
- **Expected:** A stable mutation hook registration interface exists; it accepts typed callbacks for exactly the four event kinds `created`, `updated`, `soft_deleted`, and `restored` on all four foundation entity types (`Workflow`, `Role`, `OutputSchema`, `CustomTaskTemplate`); hooks fire after successful commit; and no event kinds beyond these four exist in the interface. SF-5 contains no code that creates or updates `workflow_entity_refs` rows.
- **Not Criteria:** SF-5 creates reference-index rows directly; SF-7 must reach into SF-5 model internals to detect entity mutations; the hook interface covers workflows only; or additional event kinds such as `imported`, `version_saved`, or `deleted` exist in the interface.
- **Requirements:** REQ-78, REQ-92
- **Citations:** - [decision] D-SF5-R3: "SF-5 exposes an in-process, post-commit mutation hook interface on all four foundation entity types." -- The hook interface is SF-5's contribution to the SF-7 reference-index handoff.
- [decision] D-SF5-R6: "The mutation hook event type enumeration is exhaustive: `created`, `updated`, `soft_deleted`, `restored`." -- AC-13 must verify the closed enumeration to prevent SF-7 from coding against phantom event kinds.


#### AC-74
- **User Action:** An authenticated user calls `GET /api/workflows/templates` and then duplicates a starter template.
- **Expected:** The response lists system-seeded starter templates (including the iriai-build-v2 planning/develop/bugfix workflows) sourced from `user_id='__system__'` DB rows; duplicating one creates a new user-owned workflow row with version 1.
- **Not Criteria:** Starter template content is loaded from a filesystem path at request time; duplicating a template modifies the system template row; or starter templates appear in the user's own editable workflow list without an explicit duplicate step.
- **Requirements:** REQ-79, REQ-85
- **Citations:** - [decision] D-SF5-R4: "Starter templates are persisted as `user_id='__system__'` rows, seeded by an Alembic data migration. Filesystem asset serving is not used." -- AC-14 directly verifies the persistence approach decision against the two competing options.


### SF-6: Workflow Editor & Canvas
<!-- SF: composer-app-foundation -->

#### J-24: Developer launches Compose from the Tools Hub
- **Actor:** Authenticated hobby-tier or pro-tier platform developer
- **Path:** happy
- **Preconditions:** The developer has a valid platform account and can authenticate with auth-service.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open `tools.iriai.app`. | A static authenticated tools hub loads with a developer-tools card catalog. | The page is blank while auth resolves, or protected cards appear actionable before auth state is known. | [decision] D-12 |
| 2 | Click the Workflow Composer card. | The browser navigates in the same tab to `compose.iriai.app`. | A new tab opens, or the route points to a stale `tools/iriai-workflows` URL. | [decision] D-A3 |
| 3 | Wait for compose to load. | The compose shell opens on the Workflows landing experience with the four SF-5 folders visible. | Plugin or tool-library surfaces appear in the SF-5 shell, or editor boot blocks the shell from rendering. | [code] .iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/design-decisions.md:13 |

- **Outcome:** The developer reaches the canonical compose app entry point through the tools hub.
- **Requirements:** REQ-75, REQ-90, REQ-91


#### J-25: Developer creates a workflow and starts editor bootstrap
- **Actor:** Authenticated compose user
- **Path:** happy
- **Preconditions:** The user is on the compose Workflows landing view.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Click the primary new-workflow action and submit the create form. | A workflow row is created and `WorkflowVersion` v1 exists immediately. | The workflow is created without an audit version, or the list requires a full refresh. | [decision] D-13 |
| 2 | Open the new workflow. | The app routes to `/workflows/{id}/edit` without losing authenticated state. | The route breaks auth state or opens an unknown workflow id. | [code] platform/deploy-console/deploy-console-frontend/src/App.tsx:79 |
| 3 | Let the editor bootstrap start. | The frontend requests both the workflow record and `GET /api/schema/workflow` before mounting schema-driven editor affordances. | The editor treats a static bundled schema as authoritative or skips runtime schema fetch. | [decision] D-GR-22 |

- **Outcome:** A new workflow exists with version 1 and is ready for schema-aware editing.
- **Requirements:** REQ-79, REQ-84, REQ-85, REQ-87, REQ-89


#### J-26: User saves and exports a canonical workflow definition
- **Actor:** Authenticated compose user editing a workflow
- **Path:** happy
- **Preconditions:** A workflow exists and the editor has schema data available.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Trigger save from the editor. | The payload is validated against the canonical runtime contract before persistence. | The save path persists the editor's flat internal graph as canonical YAML. | [code] .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1006 |
| 2 | Save completes. | The workflow stores nested phase YAML, hook connections remain edges, and a new immutable workflow version is appended. | Save writes a separate hooks section, stores serialized `port_type`, or updates workflow YAML without creating the next version row. | [decision] D-GR-22 |
| 3 | Export the workflow. | The downloaded YAML matches the same canonical structure used for save and validation. | Export emits a different schema shape than the one the backend validated and stored. | [code] .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1004 |

- **Outcome:** The saved and exported workflow remains portable and runtime-compatible.
- **Requirements:** REQ-79, REQ-85, REQ-88


#### J-27: User imports a valid workflow YAML
- **Actor:** Authenticated compose user
- **Path:** happy
- **Preconditions:** The user has a valid iriai-compose YAML file (e.g. exported from iriai-build-v2).

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Navigate to the Workflows landing view and click Import. | An import dialog or file picker opens. | The import action routes to an existing workflow detail or requires an existing workflow id in the URL. | [decision] D-SF5-R5 |
| 2 | Upload the YAML file. | The frontend sends `POST /api/workflows/import` with the file content. | The request is routed to `POST /api/workflows/{id}/import`, conflating import with an instance-level update. | [decision] D-SF5-R5 |
| 3 | Import succeeds. | A new user-owned workflow row is created with `WorkflowVersion` v1, and the user is navigated to the new workflow detail. | The import overwrites an existing workflow, creates a workflow without a version row, or requires a full-page refresh to see the new entry. | [decision] D-13 |
| 4 | Mutation hooks fire. | SF-5's mutation hook interface emits a `created` event (not `imported`) for the new Workflow entity. | The import triggers a hook event kind other than `created`, or no hook fires for the new workflow. | [decision] D-SF5-R6 |

- **Outcome:** A valid YAML file is imported as a new user-owned workflow with version 1, and downstream hook subscribers receive a standard `created` event.
- **Requirements:** REQ-79, REQ-85, REQ-88, REQ-92


#### J-28: User starts from a starter template
- **Actor:** Authenticated compose user
- **Path:** happy
- **Preconditions:** The user is on the compose Workflows landing view. System starter templates have been seeded by the Alembic data migration.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Request `GET /api/workflows/templates`. | The backend returns system-seeded starter templates (including iriai-build-v2 planning/develop/bugfix workflows) sourced from `user_id='__system__'` DB rows. | Template content is loaded from a filesystem path at request time rather than from the DB. | [decision] D-SF5-R4 |
| 2 | Select a starter template to use. | A duplicate action is triggered, creating a new user-owned workflow row with version 1 derived from the template. | The system template row is modified, or the duplicate action fails because the template's `user_id` does not match the caller's id. | [decision] D-SF5-R4 |
| 3 | Open the duplicated workflow. | The new workflow appears in the user's workflow list and can be edited, saved, and exported. | The duplicate points back to the system template row, or the new workflow has no associated version 1 row. | [decision] D-13 |

- **Outcome:** The user has an editable copy of the starter template; the system template row is unchanged.
- **Requirements:** REQ-79, REQ-85


#### J-29: Canonical schema endpoint is unavailable during editor bootstrap
- **Actor:** Authenticated compose user opening a workflow
- **Path:** failure
- **Preconditions:** A workflow exists, but the schema endpoint is temporarily unavailable.
- **Failure Trigger:** `GET /api/schema/workflow` fails or times out.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open `/workflows/{id}/edit` while the schema endpoint is unavailable. | The editor route shows a blocking, recoverable schema-load error state with retry and back navigation. | The app silently falls back to a stale local schema or spins forever. | [decision] D-GR-22 |
| 2 | Retry after the backend recovers. | The editor bootstrap succeeds using the same runtime schema endpoint. | Recovery requires a hard refresh or a manual local-schema workaround. | [code] platform/deploy-console/deploy-console-frontend/src/App.tsx:46 |

- **Outcome:** Schema failure is explicit and recoverable without changing contracts.
- **Requirements:** REQ-87, REQ-89
- **Related Journey:** J-2


#### J-30: User imports malformed YAML
- **Actor:** Authenticated compose user importing a workflow file
- **Path:** failure
- **Preconditions:** The user has a YAML file to import.
- **Failure Trigger:** The uploaded file is malformed YAML.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Upload malformed YAML via `POST /api/workflows/import`. | The backend returns parse errors and creates no workflow or version rows. | A partial workflow is persisted; the user receives only a generic failure with no path/message context; or the error comes from `POST /api/workflows/{id}/import`. | [decision] D-SF5-R5 |
| 2 | Correct the file and retry import. | Import succeeds, creates a workflow plus version 1, and may surface non-blocking validation warnings if the YAML is structurally parseable but not fully schema-valid. | Retry leaves behind duplicate partial rows from the failed attempt. | [decision] D-GR-22 |

- **Outcome:** Invalid YAML is rejected safely, and retrying produces a clean imported workflow.
- **Requirements:** REQ-79, REQ-85, REQ-88
- **Related Journey:** J-8


#### J-31: Cross-user workflow access is denied without leaking existence
- **Actor:** Authenticated platform developer
- **Path:** failure
- **Preconditions:** Another user's workflow id is known or guessed.
- **Failure Trigger:** The actor requests a workflow they do not own.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Navigate directly to another user's workflow detail or editor route. | The API and UI return a not-found result. | The UI or API confirms that the workflow exists but belongs to someone else. | [code] .iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/plan.md:553 |
| 2 | Return to the user's own workflow list. | The user's own data remains available and unchanged. | The failed lookup corrupts local session state or reveals cross-user metadata. | [decision] D-2 |

- **Outcome:** Unauthorized access is blocked without existence leakage.
- **Requirements:** REQ-80, REQ-81
- **Related Journey:** J-2


#### J-32: Tools hub session is missing and the user must authenticate first
- **Actor:** Platform developer without an active browser session
- **Path:** failure
- **Preconditions:** The user opens the tools hub from a logged-out browser state.
- **Failure Trigger:** Protected tools-hub content is requested without a valid auth session.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open `tools.iriai.app` without an active session. | The user is prompted to authenticate before protected tool cards are usable. | The hub exposes protected tool access states without auth or renders a blank page. | [code] platform/deploy-console/deploy-console-frontend/src/App.tsx:46 |
| 2 | Complete authentication and return to the tools hub. | The developer-tools card catalog appears and the Workflow Composer card can be used normally. | The user is stranded on the callback route or loses the intended return path. | [code] platform/deploy-console/deploy-console-frontend/src/App.tsx:79 |

- **Outcome:** Missing session state is resolved through normal auth flow without exposing protected tools.
- **Requirements:** REQ-80, REQ-91
- **Related Journey:** J-1


### SF-6: Workflow Editor & Canvas
#### Workflow <!-- SF: composer-app-foundation -->
- **Fields:** id: UUID, name: string, description: string | null, yaml_content: text, current_version: int, is_valid: bool, user_id: string, created_at: datetime, updated_at: datetime | null, deleted_at: datetime | null
- **Constraints:** Unique per user among non-deleted workflow names (user_id, name, deleted_at IS NULL); `yaml_content` stores canonical nested workflow YAML; `current_version` mirrors the latest `workflow_versions.version_number`; Rows with `user_id='__system__'` are system-seeded starter templates; they are never soft-deleted by user actions and are returned only by `GET /api/workflows/templates`
- **New:** yes


#### WorkflowVersion <!-- SF: composer-app-foundation -->
- **Fields:** id: UUID, workflow_id: UUID, version_number: int, yaml_content: text, change_description: string | null, user_id: string, created_at: datetime
- **Constraints:** Unique (workflow_id, version_number); Append-only after creation; Version writes do not trigger mutation hook events on the parent Workflow entity
- **New:** yes


#### Role <!-- SF: composer-app-foundation -->
- **Fields:** id: UUID, name: string, prompt: text, tools: JSON list[string], model: string | null, effort: string | null, metadata: JSON object, user_id: string, created_at: datetime, updated_at: datetime | null, deleted_at: datetime | null
- **Constraints:** Unique per user among non-deleted role names; Fields align with the current iriai_compose.Role contract (name, prompt, tools, model, effort, metadata)
- **New:** yes


#### OutputSchema <!-- SF: composer-app-foundation -->
- **Fields:** id: UUID, name: string, description: string | null, json_schema: JSON, user_id: string, created_at: datetime, updated_at: datetime | null, deleted_at: datetime | null
- **Constraints:** Unique per user among non-deleted schema names; `json_schema` stores the reusable output contract referenced by workflows
- **New:** yes


#### CustomTaskTemplate <!-- SF: composer-app-foundation -->
- **Fields:** id: UUID, name: string, description: string | null, subgraph_yaml: text, input_interface: JSON, output_interface: JSON, user_id: string, created_at: datetime, updated_at: datetime | null, deleted_at: datetime | null
- **Constraints:** Unique per user among non-deleted template names; SF-5 stores the foundation record only; SF-7 may extend this table with actor_slots
- **New:** yes


### From: workflow-editor
