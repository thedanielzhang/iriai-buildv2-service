<!-- SF: libraries-registries -->

#### REQ-107: functional (must)
SF-7 must extend the accepted compose topology: library surfaces live inside the compose app backed by `tools/compose` frontend/backend and PostgreSQL + Alembic, not `tools/iriai-workflows` or SQLite.

**Citations:**
- [decision] D-GR-27: "`tools/compose` is accepted; `tools/iriai-workflows` is rejected." -- The revision must inherit the accepted topology rather than preserve stale paths.
- [decision] D-GR-28: "PostgreSQL + SQLAlchemy + Alembic remains canonical." -- This fixes the stale SQLite assumption.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/integration-review-sources-plan.md:6170: "`tools/compose/frontend`, `tools/compose/backend`, `tools/iriai-workflows` NOT used." -- The repo-wide accepted topology is recorded in the feature plan.


#### REQ-108: functional (must)
Roles, Schemas, and Task Templates must use a pre-delete reference check backed by `workflow_entity_refs`, introduced as an SF-7-owned follow-on PostgreSQL/Alembic extension; SF-5 remains limited to exactly five foundation tables and only exposes the workflow mutation hooks SF-7 needs to refresh the index.

**Citations:**
- [decision] D-GR-29: "SF-5 stays at five tables; `workflow_entity_refs` moves to SF-7 scope." -- This is the core ownership change requested in the Cycle 5 feedback.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:22: "Create exactly 5 SF-5 tables." -- The foundation contract leaves no room for foundation-owned reference-index tables.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-5.md:24: "SF-7 should own the `workflow_entity_refs` reference-index extension." -- The accepted Cycle 5 guidance explicitly moves ownership into SF-7.


#### REQ-109: functional (must)
SF-7 delete UX for Roles, Schemas, and Task Templates must be non-destructive: `EntityDeleteDialog` and `useReferenceCheck` call `GET /api/{entity}/references/{id}` before any DELETE request, and the backend must not parse workflow YAML on demand for that lookup.

**Citations:**
- [decision] D-GR-26: "`workflow_entity_refs` backs `GET /api/{entity}/references/{id}`." -- This is the canonical delete-preflight contract.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:26: "`useReferenceCheck` calls the references endpoint before delete." -- The SF-7 interaction design already encodes the desired UX.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:52: "Remove YAML-scan delete helpers in favor of indexed reference checks." -- The plan language matches the requested revision.


#### REQ-110: functional (must)
The Tool Library remains a full CRUD library page with list, detail, and editor views; registered tools populate the Role editor tool checklist via `GET /api/tools`, and tool delete protection remains role-backed rather than `workflow_entity_refs`-backed.

**Citations:**
- [decision] D-GR-7: "Tool Library restored with full CRUD and role integration." -- Tool CRUD remains active scope after the rebase.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md:106: "/tools route, Tool entity CRUD, role editor integration." -- The review history records the accepted tool-library scope.
- [code] /Users/danielzhang/src/iriai/iriai-compose/iriai_compose/actors.py:13: "`Role.tools` is `list[str]`." -- Tool delete checks still branch on persisted role arrays rather than workflow refs.


#### REQ-111: functional (must)
`custom_task_templates` must persist `actor_slots` through a follow-on Alembic migration and API support so task template actor-slot definitions survive reloads and remain reusable across workflows.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md:108: "Alembic migration for `actor_slots` is an implementation prerequisite." -- The revision must keep actor-slot persistence explicit.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/reviews/pm.md:143: "SF-7 adds actor_slots to CustomTaskTemplate." -- The cross-subfeature review confirms this is an SF-7 extension, not SF-5 foundation scope.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:199: "Without the migration, actor slot definitions are lost on reload." -- This captures the concrete failure the requirement prevents.


#### REQ-112: non-functional (should)
Library pages must feel immediate: warm-cache list pages load within 500ms, cold fetches within 2 seconds, and data access uses stale-while-revalidate query behavior.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/compile-sources-prd.md:6710: "Warm-cache within 500ms; cold fetches within 2 seconds." -- These are the established SF-7 responsiveness targets.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/prd_sf7_and_merged.md:271: "Use stale-while-revalidate query behavior for library APIs." -- The prior merged PRD already fixed the desired caching model.


#### REQ-113: security (must)
All library API endpoints require JWT Bearer auth, scope data to the authenticated user, and return 404 rather than 403 for cross-user access attempts.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:24: "Scope all resource access by authenticated `user_id`; return `404` for other users." -- SF-7 inherits compose foundation tenancy controls.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:25: "JWT auth on all non-health endpoints." -- Library APIs stay behind the same compose auth boundary.


#### REQ-114: security (must)
Server-side validation must enforce JSON payload size limits and entity-name sanitization across library entities, with clear 413/422 responses and matching frontend guards.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md:110: "256KB JSON payload size limits." -- The review history keeps payload limits as required SF-7 scope.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md:111: "Name sanitization regex on the server and frontend." -- Entity naming rules remain part of the accepted SF-7 guardrails.
- [research] OWASP Input Validation Cheat Sheet: "Apply server-side allowlist validation with length limits as early as possible." -- This supports rejecting malformed or oversized library payloads before persistence.


#### REQ-115: functional (must)
SF-7 library scope remains limited to Roles, Output Schemas, Task Templates, and Tools inside compose; do not restore Plugins Library pages, plugin endpoints, or PluginPicker surfaces.

**Citations:**
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:198: "Plugin surfaces must be removed rather than restored." -- The review explicitly called stale plugin surfaces the largest SF-7 contradiction.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:213: "Do not create a PluginPicker." -- The current SF-7 plan already narrows picker scope to non-plugin library entities.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:40: "Exclude plugin-management entities and `/api/plugins` from SF-5." -- The foundation contract also rejects plugin-management database/API surfaces.


## Acceptance Criteria

### Broad Acceptance Criteria
<!-- SF: libraries-registries -->

#### AC-86
- **User Action:** A user tries to delete a role that is still referenced by a saved workflow, then removes that reference in the workflow and saves again.
- **Expected:** Delete is blocked before any DELETE call with the referencing workflow list; after the workflow save, reopening delete shows the normal confirmation with no stale workflow names.
- **Not Criteria:** The user must not have to issue a DELETE request just to discover references, and stale reference rows must not remain after the saved workflow changes clear them.
- **Requirements:** REQ-108, REQ-109
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:26: "Delete preflight starts with the references endpoint." -- This is the intended user-visible flow for referenced roles.
- [decision] D-GR-29: "Reference-index ownership moves into SF-7 follow-on scope." -- The acceptance test must validate the rebased ownership model.


#### AC-87
- **User Action:** An engineer inspects the initial SF-5 migration and the first SF-7 extension migrations for compose.
- **Expected:** SF-5 creates exactly five foundation tables (`workflows`, `workflow_versions`, `roles`, `output_schemas`, `custom_task_templates`), while SF-7 follow-on Alembic revisions add `workflow_entity_refs`, the `tools` table, and the `actor_slots` column on `custom_task_templates` inside the compose PostgreSQL backend.
- **Not Criteria:** The foundation migration must not create `workflow_entity_refs`, `tools`, plugin tables, or SQLite-specific persistence, and the extension work must not target `tools/iriai-workflows`.
- **Requirements:** REQ-107, REQ-108, REQ-111
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:58: "Exactly 5 tables exist in the SF-5 foundation migration." -- This anchors the inspection criterion for the foundation layer.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/reviews/pm.md:143: "SF-7 adds a Tool entity and actor_slots." -- The extension inspection must confirm these stay in SF-7 scope.


#### AC-88
- **User Action:** A user creates a task template with actor slots, saves it, refreshes the page, and reopens the template.
- **Expected:** Actor slots are fully persisted with names, type constraints, and default bindings, and the API returns `actor_slots` on reload.
- **Not Criteria:** Actor slots must not exist only in frontend state or disappear on reload.
- **Requirements:** REQ-111
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:199: "Without the migration, actor slots are lost on page reload." -- This is the direct user-facing acceptance condition for the fix.


#### AC-89
- **User Action:** A user edits a custom tool and then opens a Role editor that references it.
- **Expected:** The tool detail view updates, and the Role editor checklist shows the updated tool metadata after query invalidation.
- **Not Criteria:** Editing must not create a second tool record or leave stale tool metadata in the Role editor.
- **Requirements:** REQ-110
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:131: "Tool Library keeps list, detail, and editor flows." -- The updated tool must propagate across that whole flow.


#### AC-90
- **User Action:** A user tries to delete a custom tool that is still referenced by roles.
- **Expected:** Delete is blocked with the referencing role names; after removing those role references, the standard delete confirmation appears and the tool disappears from Role editor checklists.
- **Not Criteria:** The tool must not be deleted while still referenced, and deleted tools must not remain selectable.
- **Requirements:** REQ-110
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:42: "Tool delete checks role usage, not workflow refs." -- This is the intended blocking contract for tool deletion.


#### AC-91
- **User Action:** A user submits oversized JSON or an invalid entity name through the UI or API.
- **Expected:** The server rejects the request with the documented 413 or 422 validation errors and no record is created or updated.
- **Not Criteria:** Validation must not exist only in the frontend, and malformed or oversized payloads must not be stored.
- **Requirements:** REQ-114
- **Citations:** - [research] OWASP Input Validation Cheat Sheet: "Server-side validation must happen before processing untrusted input." -- This supports the rejection behavior for malformed library payloads.


#### AC-92
- **User Action:** A user opens the compose library sidebar and library-selection pickers in the editor.
- **Expected:** The available library surfaces are Roles, Output Schemas, Task Templates, and Tools, with no Plugins page and no PluginPicker affordance.
- **Not Criteria:** A Plugins library, plugin endpoint affordance, or PluginPicker must not reappear in the rebased SF-7 surface.
- **Requirements:** REQ-115
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:205: "Pickers are RolePicker, SchemaPicker, and TemplateBrowser." -- The picker surface already excludes plugins in the revised plan.
- [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:213: "Do not create a PluginPicker." -- This is the concrete artifact-level guardrail.


#### AC-93
- **User Action:** User A attempts to access User B's role, schema, template, or tool by direct API or deep link.
- **Expected:** The request resolves as not found, and no foreign resource metadata is revealed.
- **Not Criteria:** The API must not return 403 or otherwise confirm that the other user's library item exists.
- **Requirements:** REQ-113
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:24: "Return `404` for other users' records." -- SF-7 inherits the compose tenancy boundary.


#### AC-94
- **User Action:** A user opens a library list, then revisits it in the same session after the initial load.
- **Expected:** The cached list renders within the warm-cache 500ms target and background refresh does not block interaction; a cold visit still resolves within the 2-second target.
- **Not Criteria:** The user must not sit behind a spinner beyond the cold-load target, and cached revisits must not feel like full reloads.
- **Requirements:** REQ-112
- **Citations:** - [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/compile-sources-prd.md:6710: "Warm-cache 500ms; cold fetches 2 seconds." -- This directly defines the page-load acceptance thresholds.


## User Journeys

### Broad Journeys
<!-- SF: libraries-registries -->

#### J-39: Create and Use a Role from the Roles Library
- **Actor:** Platform developer with at least one saved workflow in compose
- **Path:** happy
- **Preconditions:** Authenticated user in the compose app; Roles Library is accessible from the rebased compose shell.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open the Roles Library inside compose. | The list view loads with existing role cards, search, and a primary New Role action. | Other users' roles are not visible, and the user does not land in a stale `tools/iriai-workflows` surface. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:90 |
| 2 | Create a new role with a name, model, prompt, and selected tools. | The Role editor accepts the values and shows built-in and registered tools as selectable groups. | Registered tools are not missing, and invalid names are not accepted. | [code] /Users/danielzhang/src/iriai/iriai-compose/iriai_compose/actors.py:13 |
| 3 | Save the role and select it from the Ask-node role picker in a workflow. | The role appears in the library and becomes selectable from RolePicker in the workflow editor. | Duplicate role rows are not created, and the picker does not require delete-preflight data just to list roles. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:205 |

- **Outcome:** A reusable role exists in the compose library and can be attached to saved workflow content.
- **Requirements:** REQ-110, REQ-113, REQ-115


#### J-40: Delete a Role Referenced by Saved Workflows
- **Actor:** Platform developer cleaning up an unused role
- **Path:** failure
- **Preconditions:** A saved workflow currently references the role through persisted library data.
- **Failure Trigger:** The user initiates delete on a role that is still referenced by saved workflow content.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open delete for the referenced role. | A blocking dialog appears before any destructive request, listing the referencing workflows from the SF-7 reference index. | The role is not deleted, and the system does not parse workflow YAML or require a DELETE attempt just to discover references. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:26 |
| 2 | Remove the role reference in the workflow editor and save the workflow. | The workflow save succeeds and the role's reference status updates on the next delete preflight. | Unsaved editor changes are not treated as cleared references, and stale workflow names do not persist after the saved change. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:12 |
| 3 | Retry delete after all saved references are removed. | The standard delete confirmation appears and the user can safely remove the role. | The reference list is not stale, and the role is not blocked by a foundation-owned table that SF-5 was never supposed to create. | [decision] D-GR-29 |

- **Outcome:** The user understands why deletion was blocked, clears the saved references safely, and then deletes the role without stale index data.
- **Requirements:** REQ-108, REQ-109
- **Related Journey:** J-1


#### J-41: Delete a Tool Referenced by Roles
- **Actor:** Platform developer attempting to remove a custom tool
- **Path:** failure
- **Preconditions:** The custom tool is still referenced by one or more saved roles.
- **Failure Trigger:** The user initiates delete on a tool that is still referenced by role `tools` arrays.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Open delete for the referenced tool. | A blocking dialog lists the referencing roles and offers only Close until the role references are removed. | The tool is not deleted, and the dialog does not show workflow names or `workflow_entity_refs` validation codes. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:42 |
| 2 | Remove the tool from the referencing roles and save those roles. | The roles save successfully with updated `tools` arrays. | Other tool selections are not corrupted, and the saved role shape does not switch from string identifiers to a new ID model. | [code] /Users/danielzhang/src/iriai/iriai-compose/iriai_compose/actors.py:13 |
| 3 | Retry tool delete after the role saves complete. | The normal delete confirmation appears and the deleted tool disappears from later Role editor checklists. | The tool is not deleted while still referenced, and deleted tools do not remain selectable in the role editor. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:43 |

- **Outcome:** The tool is deleted only after all saved role references are removed, preserving role integrity.
- **Requirements:** REQ-110
- **Related Journey:** J-1


#### J-42: Persist Actor Slots in a Task Template
- **Actor:** Platform developer creating a reusable multi-agent task template
- **Path:** happy
- **Preconditions:** Authenticated user in the compose Task Templates editor with the scoped template canvas available.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Create a task template subgraph and define one or more actor slots. | The editor captures each actor slot's name, type constraint, and optional default binding. | Duplicate or unnamed actor slots are not accepted, and the editor does not imply that client-only state is enough. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:179 |
| 2 | Save the template, refresh the page, and reopen it. | The template reloads with the same actor slots intact because the server persists and returns `actor_slots`. | Actor slots are not dropped on reload, and they do not rely on local browser persistence to survive a refresh. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:199 |

- **Outcome:** The task template stores reusable actor-slot definitions that survive reloads and can be reused in later workflows.
- **Requirements:** REQ-111


#### J-43: Reject Invalid Actor Slot Definitions
- **Actor:** Platform developer editing a task template
- **Path:** failure
- **Preconditions:** Authenticated user is in the task-template editor and attempts to save invalid actor-slot data.
- **Failure Trigger:** The user enters malformed actor-slot data, such as duplicate names or an invalid default binding.

| Step | Action | Observes | Not Criteria | Citations |
|------|--------|----------|--------------|----------|
| 1 | Enter invalid actor-slot definitions and attempt to save the template. | The UI and API reject the save with clear validation feedback describing the invalid actor-slot data. | The template is not partially saved, and invalid actor-slot payloads are not silently normalized into persisted data. | [code] /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:207 |
| 2 | Correct the actor-slot data and save again. | The save succeeds, and a refresh reopens the template with only the corrected actor-slot definitions. | The user does not need to rely on local workarounds, and the server does not preserve the previously invalid slot payload. | [research] OWASP Input Validation Cheat Sheet |

- **Outcome:** The user is prevented from persisting invalid actor-slot data, corrects the issue, and saves a consistent template state.
- **Requirements:** REQ-111, REQ-114
- **Related Journey:** J-4


## Security Profile

### broad

- **Compliance Requirements:** No specific compliance requirements. Standard platform security practices apply.
- **Data Sensitivity:** Workflow configs may contain system prompts with proprietary process knowledge. Role definitions may contain sensitive operational instructions. No PII stored directly.
- **Pii Handling:** No PII in workflow configs. User identity (user_id from JWT sub claim) associated with owned resources for access control.
- **Auth Requirements:** JWT-based authentication via auth-service. All composer API endpoints require valid access token. Tools hub reads dev_tier claim for tier gating. Backend validates JWT via auth-python (JWKS endpoint).
- **Data Retention:** Workflow configs and versions retained indefinitely. No automatic purging.
- **Third Party Exposure:** Exported YAML files may be shared externally. They contain role prompts and workflow structure but no credentials or secrets. Plugin configurations may reference external service URLs.
- **Data Residency:** SQLite database local to the FastAPI backend deployment. Workflow YAML exports are portable files.
- **Risk Mitigation Notes:** Transforms and hooks reference Python functions by name — the runner resolves them at execution time, not the builder. No arbitrary code execution in the composer. Plugin credentials are stored in the runner environment, not in the YAML.

### declarative-schema

- **Compliance Requirements:** None beyond standard internal engineering controls.
- **Data Sensitivity:** Internal. Workflow YAML and JSON Schema expose proprietary orchestration structure, prompts, and typed interfaces, but not operational secrets by default.
- **Pii Handling:** No direct PII handling in the schema module. Human actors are declared abstractly rather than by storing personal profile data.
- **Auth Requirements:** The schema package itself has no intrinsic auth boundary. When exposed to the composer, the canonical `/api/schema/workflow` delivery path should inherit the backend's authenticated API policy rather than rely on a public static file.
- **Data Retention:** Not applicable for the library artifact itself. Exported YAML or generated JSON Schema retention is determined by the consuming service or repository.
- **Third Party Exposure:** YAML or JSON Schema may be shared externally, but the contract should expose workflow structure and types only; it should not require embedded credentials or secrets. Static `workflow-schema.json` is build/test only, reducing accidental runtime drift from checked-in artifacts.
- **Data Residency:** Not applicable at the library level; residency is inherited from whichever backend serves or stores workflows.
- **Risk Mitigation Notes:** The main product risk is contract drift between schema producer and consumers. D-GR-22, D-GR-30, and D-GR-35 mitigate that by making nested YAML, actor_type:agent|human, closed root field set, edge-based hook serialization, per-port branch conditions, and `/api/schema/workflow` the single canonical interface. All BranchNode per-port conditions are expressions subject to the sandbox; `switch_function` is rejected. Validation must fail fast on stale `switch_function`, the old `condition_type`/`condition`/`paths` branch shape, serialized `port_type`, or separate hook-section assumptions so downstream tools cannot silently diverge.

### dag-loader-runner

- **Compliance Requirements:** None beyond standard platform engineering controls.
- **Data Sensitivity:** Internal workflow definitions, prompts, typed interfaces, and execution metadata.
- **Pii Handling:** No new direct PII surface in the loader/runner itself. Human actors are schema-level interaction definitions (identity, channel) rather than stored credentials or profiles.
- **Auth Requirements:** Library-level runtime has no auth boundary; composer access to GET /api/schema/workflow is handled by SF-5, but the endpoint must remain the canonical runtime schema source for authoring.
- **Data Retention:** Execution-history retention is determined by the consuming application; SF-2 itself only defines the runtime result surface.
- **Third Party Exposure:** Only through configured agent/plugin runtimes or host-managed human-interaction channels supplied by the consuming application.
- **Data Residency:** No library-level residency guarantees.
- **Risk Mitigation Notes:** Treat the current SF-1 PRD as the only authoritative wire contract and fail fast on all stale variants. Reject alternate root fields (stores, plugin_instances), alternate actor discriminators (interaction), serialized hook metadata (port_type, hooks sections), and stale branch routing surfaces (switch_function, old condition_type/condition/paths, output_field mode per port) so downstream tools cannot drift back toward multiple workflow dialects. Per D-GR-35: merge_function is valid for gather and must not be rejected. All BranchNode per-port output conditions are expressions evaluated under the shared sandbox security contract (AST allowlist, timeout, size limits); there is no output_field declarative-lookup mode on branch output ports.

### testing-framework

- **Compliance Requirements:** None; testing-only library module.
- **Data Sensitivity:** Public synthetic test data.
- **Pii Handling:** No PII expected; mocks and fixtures use synthetic inputs.
- **Auth Requirements:** None at the library surface.
- **Data Retention:** Snapshot files remain developer-managed test artifacts.
- **Third Party Exposure:** None; no external calls are required for the revised contract.
- **Data Residency:** N/A.
- **Risk Mitigation Notes:** Prevent accidental ABI widening in downstream consumers. SF-3 must not force a breaking `AgentRuntime.invoke()` change, must not define a competing runtime-context contract, and must not reintroduce a mandatory checkpoint/resume dependency that SF-2 explicitly does not own. D-SF3-16 is the primary non-compliance risk; its removal must be verified before any SF-3 implementation file is written.

### workflow-migration

- **Compliance Requirements:** None
- **Data Sensitivity:** Internal
- **Pii Handling:** No PII in YAML workflow files or migration test fixtures.
- **Auth Requirements:** No new auth requirement for this PRD revision; runtime/plugin auth remains inherited from the consuming environment.
- **Data Retention:** YAML workflows, parity fixtures, and PRD artifacts remain version-controlled source files with standard repository retention.
- **Third Party Exposure:** No new third-party exposure introduced by this revision; runtime integrations still reference external services only through configured runtimes/plugins.
- **Data Residency:** No geographic residency constraint identified.
- **Risk Mitigation Notes:** The revision makes SF-2 the explicit and sole ABI owner so downstream consumers (SF-4 included) cannot widen the runtime boundary independently. Node-aware behavior is documented as ContextVar-based (runner-managed, not caller-supplied). Checkpoint/resume is explicitly an application-layer concern and must not re-enter the SF-2 core contract through consumer-layer workarounds. SF-4 is positioned as a downstream consumer; any SF-4 language that implies co-ownership of the SF-2 runtime boundary must be treated as a stale artifact requiring correction.

### composer-app-foundation

- **Compliance Requirements:** None beyond standard platform security controls.
- **Data Sensitivity:** Internal workflow definitions, prompts, reusable role/task metadata, and JSON Schemas.
- **Pii Handling:** No new end-user PII is stored in SF-5. The backend uses opaque JWT-derived `user_id` for ownership and reads `dev_tier` for tool access gating.
- **Auth Requirements:** JWT Bearer auth via auth-service JWKS on all non-health endpoints. Browser clients use the existing homelocal auth packages and send bearer tokens to the backend.
- **Data Retention:** Workflow, role, schema, and task-template rows are soft-deleted. `workflow_versions` remain append-only audit records. Automatic purge is out of scope for v1.
- **Third Party Exposure:** YAML exports can leave the platform when users download or share them. SF-5 foundation must not store plugin runtime secrets, custom tool configs, or runner-managed credentials.
- **Data Residency:** Railway-hosted PostgreSQL in the configured compose deployment region.
- **Risk Mitigation Notes:** Keep production CORS explicit; rely on bearer-token API calls rather than cookie-bound mutation flows; do not log raw YAML or prompt bodies; use `/api/schema/workflow` as the single runtime schema source; enforce the five-table SF-5 boundary so plugin, tools, and reference-index surfaces only land in their owning subfeatures; expose mutation hooks post-commit using the exhaustive four-kind enumeration (`created`, `updated`, `soft_deleted`, `restored`) so SF-7 can maintain reference-index state without SF-5 owning `workflow_entity_refs` rows; keep starter templates as `user_id='__system__'` DB rows so they are subject to the same query/access layer as user-owned workflows and never served from uncontrolled filesystem paths.

### workflow-editor

- **Compliance Requirements:** None specific to the editor beyond inherited platform controls.
- **Data Sensitivity:** Internal workflow definitions may contain proprietary prompts, role definitions, and process logic.
- **Pii Handling:** No workflow-specific PII is expected; authenticated user identity is used only for scoping and ownership.
- **Auth Requirements:** Standard JWT-based authenticated session via auth-react and backend auth enforcement.
- **Data Retention:** Workflow saves persist under the compose backend's `workflows` / `workflow_versions` retention behavior until deleted.
- **Third Party Exposure:** Users may export YAML externally; the editor must not embed secrets.
- **Data Residency:** Inherits `tools/compose/backend` Railway deployment and PostgreSQL/Alembic storage policy.
- **Risk Mitigation Notes:** The editor stores inline Python as data and does not execute it locally. Structural validation stays centralized through runtime schema fetch and backend `validate()`. If the canonical schema endpoint is unavailable, editing is blocked rather than falling back to a stale local schema. Core editor boot/save must stay within the accepted five-table compose foundation. Workflow mutation hooks (fired by SF-5 on create/update/delete) drive reference-index synchronization in SF-7 downstream; the editor has no write dependency on `workflow_entity_refs` and plugin/reference-index surfaces remain optional SF-7 additions.

### libraries-registries

- **Compliance Requirements:** No new external compliance regime is introduced beyond standard platform auth, tenancy isolation, and input-validation controls.
- **Data Sensitivity:** Internal — workflow-library metadata, prompts, schema JSON, and tool definitions.
- **Pii Handling:** No new high-sensitivity PII is introduced; the main identity field is JWT `sub`, used for ownership and tenancy scoping.
- **Auth Requirements:** JWT Bearer auth on compose library APIs via the existing auth-service boundary; all reads and writes are user-scoped and return 404 for cross-user access.
- **Data Retention:** Library entities follow the compose soft-delete lifecycle; reference-index rows are rebuilt or removed as workflows and library entities change. Automated hard-delete policy is out of scope for this revision.
- **Third Party Exposure:** No direct third-party exposure is added by library CRUD. Tool definitions may describe external systems, but secrets and runtime credentials are not stored in these tables.
- **Data Residency:** Compose library data resides in the compose PostgreSQL deployment region used by the accepted `tools/compose` backend.
- **Risk Mitigation Notes:** Keep SF-5 at five base tables; ship `workflow_entity_refs` and `tools` as SF-7 follow-on Alembic changes. Use non-destructive reference preflights before delete. Reject malformed or oversized payloads server-side. Do not restore plugin library surfaces. Keep tool references role-backed rather than workflow-ref-backed.

## Data Entities

### From: broad

#### Tool <!-- SF: libraries-registries -->
- **Fields:** id, user_id, name, description, source, input_schema, created_at, updated_at, deleted_at
- **Constraints:** Created by SF-7 as a follow-on table, not by the SF-5 foundation migration; Unique per user among non-deleted rows; Built-in tools are not stored in this table; Delete is blocked while any non-deleted role still references the tool name
- **New:** yes


#### WorkflowEntityRef <!-- SF: libraries-registries -->
- **Fields:** workflow_id, entity_type, entity_id, created_at
- **Constraints:** Created by SF-7 as a follow-on extension on top of the five-table foundation; Composite uniqueness on (workflow_id, entity_type, entity_id); Only persisted workflow references count toward delete blocking; Applies to roles, output schemas, and task templates; tools remain role-referenced
- **New:** yes


#### CustomTaskTemplate <!-- SF: libraries-registries -->
- **Fields:** actor_slots
- **Constraints:** `actor_slots` must be a JSON array of unique slot definitions; The API must persist and return `actor_slots` after reload; The `actor_slots` column is added by an SF-7 follow-on migration without expanding SF-5 beyond five foundation tables
- **New:** no


## Cross-Service Impacts

### From: broad

#### iriai-compose

- **Impact:** Major extension — new declarative format, YAML loader, DAG runner, primitive node types (Ask, Map, Fold, Loop, Branch, Plugin), edge transform system, hook system, phase groupings, run() entry point, and testing framework (iriai_compose.testing)
- **Action Needed:** Extend library with new modules: schema definition, loader, DAG executor, testing framework. Existing Python subclass API can be broken if needed — new declarative format supersedes it.

#### iriai-build-v2

- **Impact:** Read-only reference. Its 3 workflows (planning, develop, bugfix) are the litmus test — must be translatable to declarative YAML.
- **Action Needed:** No code changes. Analyze workflows to extract patterns and validate format completeness. Produce equivalent YAML representations.

#### auth-service

- **Impact:** No changes needed. JWT already includes dev_tier claim.
- **Action Needed:** None — existing JWT claims sufficient for tier gating.

#### tools.iriai.app (new)

- **Impact:** New minimal frontend app. Reads JWT, displays tier-gated tool cards.
- **Action Needed:** Build new React SPA with auth-react integration. Deploy on Railway.

#### deploy-console-frontend

- **Impact:** Design system reference. Windows XP theme CSS to be replicated or extracted into shared package.
- **Action Needed:** Consider extracting windows-xp.css and UI components into a shared @iriai/ui package, or copy theme files.

### From: declarative-schema

#### iriai-compose (SF-2 loader/runner)

- **Impact:** The loader and runner must consume nested phase containment as `phases[].nodes` and `phases[].children`, honor phase-local `edges`, resolve `actor_type: agent|human`, infer hook-vs-data behavior from port resolution with no serialized `port_type`, and evaluate `BranchNode` per-port conditions using the expression sandbox with non-exclusive fan-out.
- **Action Needed:** Update loader hydration, graph-building, and validation to treat `children` as the recursive phase field, evaluate per-port conditions non-exclusively (multiple outputs may fire), accept `merge_function` as a gather hook, reject stale actor/root/hook/branch fields including `switch_function`, `condition_type`, `output_field`, and `interaction` alias, and preserve additive compatibility with the imperative API.

#### iriai-compose (SF-3 testing framework)

- **Impact:** Fixtures and assertions must construct workflows in the nested YAML shape and stop assuming flat nodes, serialized `port_type`, or any alternate hook model. Branch fixtures must use the per-port `outputs` model with per-port `condition` expressions.
- **Action Needed:** Refresh schema fixtures, round-trip tests, and negative tests so they author nested phases, use `actor_type: agent|human`, use per-port branch conditions, and assert rejection of stale fields like `switch_function`, `condition_type`/`condition`/`paths`, and serialized `port_type`.

#### iriai-build-v2 migration tooling (SF-4)

- **Impact:** Migration output must emit nested phase YAML using `children`, ordinary hook edges, and per-port `BranchNode.outputs` conditions so the translated workflows are valid for both the loader and editor.
- **Action Needed:** Rewrite translation and fixture assumptions away from stale branch fields (`switch_function`, `condition_type`/`condition`/`paths`) and ensure build-v2 planning, develop, and bugfix workflows target the canonical per-port branch contract.

#### iriai-workflows backend (SF-5 composer-app-foundation)

- **Impact:** The backend becomes the canonical schema delivery layer through `GET /api/schema/workflow`; validation and editor bootstrap should consume the live schema rather than a bundled static file, and backend models must not add root `stores` / `plugin_instances` drift.
- **Action Needed:** Implement `/api/schema/workflow` as the authoritative composer schema endpoint, wire validation to the same schema package, and remove static-schema-first plus extra-root-field wording from PRD/plan artifacts.

#### iriai-workflows frontend (SF-6 workflow-editor)

- **Impact:** The editor's serializer/deserializer must keep its internal flat store private and round-trip to the nested YAML contract with `phases[].nodes`, `phases[].children`, ordinary hook edges, per-port `BranchNode.outputs` conditions, and no serialized `port_type`.
- **Action Needed:** Keep the transformation layer but rewrite stale PRD/system-design text so runtime schema fetch comes from `/api/schema/workflow`, hook serialization stays edge-based, branch UI reflects per-port non-exclusive conditions with optional `merge_function`, actor model uses `agent|human` only, and nested containment is the only YAML contract.

### From: dag-loader-runner

#### SF-1 Declarative Schema PRD

- **Impact:** SF-2 now treats the current SF-1 PRD as the only canonical wire contract, including the D-GR-35 per-port BranchNode model. BranchNode entity must reflect inputs/merge_function/outputs shape; old condition_type/condition/paths shape is stale.
- **Action Needed:** Align SF-1 BranchNode schema to D-GR-35: inputs dict + optional merge_function + outputs dict with per-port BranchOutputPort (PortDefinition + condition expression). Remove condition_type, top-level condition, paths, and output_field mode from BranchNode everywhere.

#### SF-1 stale plan / system-design artifacts

- **Impact:** Stale SF-1 artifacts still describe the old three-field BranchNode (condition_type/condition/paths) and may reference merge_function as rejected — both are now incorrect under D-GR-35. Also still reference runtime workflow-schema.json and alternate actor forms.
- **Action Needed:** Rewrite stale SF-1 plan/system-design BranchNode sections to the D-GR-35 per-port model. Remove rejections of merge_function; add rejections of switch_function and old condition_type/condition/paths fields. Also fix workflow-schema.json and interaction actor references.

#### SF-5 Composer App Foundation

- **Impact:** Backend must expose GET /api/schema/workflow from the same in-process SF-1 models SF-2 validates and runs, reflecting the D-GR-35 BranchNode shape with inputs/merge_function/outputs.
- **Action Needed:** Remove any static-schema-first assumptions; ensure the schema endpoint reflects BranchNode.outputs (per-port conditions) rather than the old paths shape. Keep endpoint behavior tied to canonical SF-1 models.

#### SF-6 Workflow Editor

- **Impact:** Editor may keep a flat internal store, but save/load/import/export must normalize to the canonical nested YAML contract including D-GR-35 BranchNode shape. merge_function must be accepted without error. Old condition_type/condition/paths must be rejected on import.
- **Action Needed:** Align BranchNode authoring surface to the inputs/merge_function/outputs per-port model; update serializer/importer to stop emitting or tolerating old condition_type/condition/paths/switch_function fields. Validate that merge_function is passed through correctly.

#### SF-3 Testing Framework

- **Impact:** Tests and fixtures must target the D-GR-35 BranchNode contract (per-port outputs, non-exclusive fan-out, merge_function valid, switch_function rejected, old three-field schema rejected).
- **Action Needed:** Refresh BranchNode fixtures to use inputs/merge_function/outputs per-port model. Update assertions so old condition_type/condition/paths/switch_function variants fail explicitly, and merge_function passes. Add non-exclusive fan-out test coverage (multiple ports firing simultaneously).

#### SF-4 Workflow Migration

- **Impact:** Migrated workflows must emit only the canonical SF-1 / D-GR-35 shape. Translated iriai-build-v2 BranchNode usages must use the per-port outputs model.
- **Action Needed:** Update migration emitters to produce D-GR-35 BranchNode output: translate any old condition_type/condition/paths shapes to inputs/merge_function/outputs per-port form. Verify translated iriai-build-v2 workflows validate and run against the canonical contract.

#### iriai-compose imperative runtime

- **Impact:** Declarative runtime remains additive and cannot break WorkflowRunner, DefaultWorkflowRunner, or existing host integrations. Human actor adaptation happens at the host boundary, not in the wire contract.
- **Action Needed:** Keep new declarative APIs under a separate namespace and preserve current runtime ABCs while adapting human actors at the host boundary.

### From: testing-framework

#### dag-loader-runner (SF-2)

- **Impact:** SF-2 REQ-11 is the canonical ABI contract and SF-2 is the sole owner: AgentRuntime.invoke() unchanged, node identity via runner ContextVar, hierarchical context workflow -> phase -> actor -> node, observability via ExecutionResult/ExecutionHistory, checkpoint/resume outside core contract.
- **Action Needed:** Keep AgentRuntime.invoke() unchanged matching current production ABC at runner.py:36–50. Propagate current node identity via ContextVar. Assemble hierarchical context in canonical order. Keep checkpoint/resume out of the mandatory core runtime contract. No new action needed beyond maintaining REQ-11.

#### testing-framework plan (SF-3)

- **Impact:** Plan decision D-SF3-16 and the stale ABC block at plan.md lines 78–90 showing `node_id: str | None = None` on `invoke()` directly contradict SF-2 REQ-11 and the production ABC. These are the primary non-compliance artifacts blocking implementation.
- **Action Needed:** Remove D-SF3-16 entirely. Correct the stale ABC block to match the production signature (no node_id). Rewrite all node-routing implementation notes to read from the runner ContextVar. Verify no test module adds node_id to invoke().

#### workflow-migration (SF-4)

- **Impact:** Migration tests, open questions, and bridge assumptions must consume the same SF-2 ABI and observability boundary as SF-3. Any SF-4 artifact that treats D-SF3-16 as a dependency is non-compliant.
- **Action Needed:** Align downstream migration artifacts to the unchanged AgentRuntime.invoke() interface, the canonical merge order, and the no-core-checkpoint/resume boundary. Remove any migration artifact that treats D-SF3-16 as a dependency or assumes invoke() carries node_id.

### From: workflow-migration

#### iriai-compose dag-loader-runner (SF-2)

- **Impact:** ABI owner. SF-4 explicitly treats SF-2 as the canonical publisher of the runtime contract: unchanged AgentRuntime.invoke(), ContextVar node propagation, workflow -> phase -> actor -> node merge order, ExecutionResult/ExecutionHistory observability, and no mandatory core checkpoint/resume API.
- **Action Needed:** Keep the published ABI stable as defined. SF-4 has no action items against SF-2; any conflict between SF-4 language and the SF-2 PRD is a stale SF-4 artifact that must be corrected.

#### iriai-compose testing-framework (SF-3)

- **Impact:** Downstream consumer aligned to SF-2 ABI. SF-4 parity tests consume SF-3 only where SF-3 is aligned to the SF-2-owned runtime contract (fluent mocks, ContextVar-based node matching, no node_id kwarg, no checkpoint/resume dependency).
- **Action Needed:** Maintain fluent mock runtimes that read current node identity from the SF-2-published ContextVar; remove any stale node_id kwarg or checkpoint/resume dependency. SF-4 must not consume SF-3 APIs that contradict the SF-2 ABI.

#### iriai-build-v2

- **Impact:** Downstream consumer. The declarative bridge and smoke coverage are explicitly constrained to the published SF-2 runner boundary and observability surface.
- **Action Needed:** Keep the consumer integration additive through run() and RuntimeConfig only, with no bridge-specific invoke shim and no requirement for SF-2-owned checkpoint/resume behavior. Resume is an application-layer concern for iriai-build-v2 to handle independently.

### From: composer-app-foundation

#### auth-service

- **Impact:** SF-5 consumes JWTs, JWKS validation, and the `dev_tier` claim for tools-hub/composer access flows.
- **Action Needed:** Register compose and tools-hub OAuth clients; no auth-service code changes are required.

#### deploy-console

- **Impact:** SF-5 reuses service layout, auth validation, logging/rate-limit patterns, and authenticated SPA shell conventions.
- **Action Needed:** Use deploy-console as an implementation reference only.

#### iriai-compose

- **Impact:** SF-5 depends on `WorkflowConfig.model_json_schema()` and runtime validation semantics to keep compose persistence aligned with the runner contract.
- **Action Needed:** Keep the compose backend pinned to the iriai-compose version that defines the canonical workflow schema.

#### iriai-build-v2

- **Impact:** SF-5 reads iriai-build-v2 planning/develop/bugfix YAML source files once at Alembic data migration time to seed `user_id='__system__'` starter template rows. No filesystem paths from iriai-build-v2 are retained in the compose service after migration.
- **Action Needed:** Read iriai-build-v2 YAML files during Alembic data migration only; no ongoing runtime dependency on iriai-build-v2 paths.

#### SF-6 Workflow Editor

- **Impact:** SF-6 consumes the authenticated compose shell, workflow CRUD/versioning, runtime schema endpoint, validation endpoint, and canonical YAML contract.
- **Action Needed:** Build editor flows against `/api/schema/workflow` and the nested workflow contract only.

#### SF-7 Libraries & Registries

- **Impact:** SF-7 owns the `workflow_entity_refs` reference-index table and subscribes to SF-5's mutation hook interface to keep that index fresh. SF-5 hooks cover all four entity types and emit exactly four event kinds (`created`, `updated`, `soft_deleted`, `restored`). SF-7 must not register against event kinds beyond these four. SF-7 also adds advanced library UI, reference-safe delete flows, a tools table, and custom_task_templates.actor_slots.
- **Action Needed:** SF-5 must ship the mutation hook interface (REQ-18) before SF-7 work begins. SF-5 must not create or mutate `workflow_entity_refs` rows at any point. SF-7 must not assume `imported`, `version_saved`, or `deleted` event kinds exist.

### From: workflow-editor

#### SF-1 Declarative Schema

- **Impact:** SF-6 now explicitly treats `phases[].nodes` / `phases[].children` as canonical and expects hook wiring to remain edge-only.
- **Action Needed:** Ensure SF-1 PRD/design/plan/system-design consistently use `children[]` and never describe a separate hooks section or serialized `port_type`.

#### SF-2 DAG Loader & Runner

- **Impact:** SF-6 depends on the loader and validator consuming the same nested structure and inferring hook edges from port resolution.
- **Action Needed:** Keep SF-2 validation and graph-build logic aligned to edge-only hook serialization and `transform_fn=None` for hook edges.

#### compose-frontend (tools/compose/frontend)

- **Impact:** SF-6 is mounted in the accepted compose SPA rather than a legacy `tools/iriai-workflows` shell.
- **Action Needed:** Keep routing, auth providers, and editor bootstrap inside `tools/compose/frontend`.

#### compose-backend (tools/compose/backend)

- **Impact:** SF-6 depends on workflow/version CRUD, roles, output schemas, custom task templates, validation, and `/api/schema/workflow` backed by PostgreSQL/Alembic and exactly five SF-5 foundation tables. SF-5 also fires workflow mutation hooks (create/update/delete lifecycle events) that downstream consumers can subscribe to; the editor itself does not subscribe to or depend on those hooks.
- **Action Needed:** Keep SF-5 limited to `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`; expose mutation hooks for SF-7 reference-index refresh; do not make `/api/plugins`, `workflow_entity_refs`, or reference-index endpoints a prerequisite for core editor boot/save.

#### SF-7 Libraries & Registries

- **Impact:** SF-7 owns the `workflow_entity_refs` reference-index table and `GET /api/{entity}/references/{id}` endpoint as a downstream extension of SF-5. SF-7 subscribes to SF-5 workflow mutation hooks to keep the reference index synchronized after editor save/create/delete flows; the editor's save path flows through SF-5 endpoints only and is unaware of the reference refresh.
- **Action Needed:** SF-7 must own all `workflow_entity_refs` schema and sync logic; plugin registry surfaces and reference-check affordances must remain additive and non-blocking for the core editor; templates and optional affordances must preserve `children[]` plus edge-based hook wiring without becoming a boot dependency.

### From: libraries-registries

#### SF-5 composer-app-foundation

- **Impact:** Provides the accepted `tools/compose` PostgreSQL/Alembic foundation, the five base tables, and workflow mutation hooks that SF-7 extends. SF-5 must not absorb `workflow_entity_refs`, `tools`, plugin tables, or SQLite assumptions.
- **Action Needed:** Keep SF-5 limited to `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`; expose workflow create/import/duplicate/save/delete mutation hooks so SF-7 can refresh the reference index from saved workflow state.

#### SF-6 workflow-editor

- **Impact:** Workflow saves determine when role/schema/template references become persisted and visible to library delete preflights.
- **Action Needed:** Continue saving persisted library references through the compose workflow routes so SF-7 can refresh `workflow_entity_refs` from saved state rather than unsaved canvas state.

#### SF-4 workflow-migration

- **Impact:** Imported or migrated workflows must produce the same persisted library-reference shape that the SF-7 index reads.
- **Action Needed:** Ensure workflow import and migration flows end at the compose workflow save boundary so SF-7 reference-index rows can be rebuilt after import.

#### iriai-compose

- **Impact:** `Role.tools` remains a string-array contract consumed by the Role editor and tool delete protection.
- **Action Needed:** Preserve the current `list[str]` tool identifier model for v1; any future move to tool IDs is a separate follow-up decision.

## Open Questions

### From: broad

- Should the Windows XP theme CSS be extracted into a shared @iriai/ui package, or should iriai-workflows copy the theme files from deploy-console?
- What URL should the workflow composer live at? (e.g., compose.iriai.app, workflows.iriai.app)
- For the migration: should the 3 translated iriai-build-v2 workflows ship as built-in starter templates in the composer?
- How should transforms and hooks be distributed? As a built-in library in iriai-compose, or user-defined in the composer, or both?
- Should the YAML format support $ref for reusable inline definitions (like JSON Schema $ref), or should all reuse go through the library system?

### From: declarative-schema

- No schema-shape open questions remain. Caching behavior for `/api/schema/workflow` is an implementation concern and does not change the canonical wire contract.

### From: testing-framework

- Should execution snapshots remain JSON-only, or is there still a case for YAML snapshot files?
- Should the enhanced `MockAgentRuntime` extend the existing test `MockAgentRuntime` from `iriai-compose/tests/conftest.py`, or remain a fresh implementation in the production `testing/` namespace?
- How deep should `validate_type_flow()` inspect inline transforms when inferring type compatibility?
- If resume-oriented helpers remain desirable in SF-3, should they be deferred to a follow-on artifact that layers above SF-2's observability surface rather than expanding the runner ABI?

### From: workflow-migration

- Should actor-centric templates use the exact same storage format in YAML and the composer's CustomTaskTemplate table, or does the composer wrap them with extra metadata?
- Is there a nesting-depth limit for phase modes beyond the four-level develop-workflow pattern?
- How should the runner resolve inline EdgeTransform `fn` names such as `envelope_extract`?
- What mechanism should the declarative path use for phase tracking in iriai-build-v2: callback/hook, wrapper around `run()`, or custom runner subclass?
- Should consumer-specific plugin implementations live in iriai-compose with dependency injection or in iriai-build-v2 as adapters?
- Should the migrated YAML workflow files live in iriai-build-v2 or in iriai-compose as portable reference workflows?

### From: composer-app-foundation

- Should `/api/schema/workflow` expose an ETag or schema hash so the frontend can safely cache and invalidate runtime schema changes?
- Should import validation reject unknown extra fields strictly, or allow warning-level tolerance for forward-compatible schema additions?
- Should SF-5's mutation hook interface be a simple in-process callback list, or should it use a lightweight event emitter pattern (e.g. Python `blinker`) to support multiple SF-7 subscribers without coupling to import order?

### From: libraries-registries

- Should `workflow_entity_refs` materialize `user_id` directly for faster queries, or should tenancy remain derived via joins to `workflows`?
- Should custom tool references remain name-based in `Role.tools` for v1, or should a later phase migrate them to stable tool IDs?
- What exact serialized shape should task-template actor-slot default bindings use in declarative workflow YAML so SF-1, SF-6, and SF-7 stay aligned?

## Out of Scope

### From: broad

- Multi-user collaboration on workflow configs
- Runtime agent execution inside the composer — it is a builder/config tool only
- Cost dashboards or analytics UI — cost configuration lives in the YAML schema for future runners
- Hot-swap UI — the builder produces versioned configs, runners handle swap mechanics
- Migration tooling from iriai-build v1 (legacy)
- Quality or subjective scoring — cost tracking limited to token counts and USD
- Mobile-responsive design — desktop-first tool for developers

### From: declarative-schema

- Execution-engine implementation details beyond the schema/validation contract.
- Any alternate flat YAML dialect with top-level nodes or detached phase membership lists.
- Separate serialized hook sections, hook registries, or hook-specific edge discriminators such as `port_type`.
- Treating static `workflow-schema.json` as the editor's canonical runtime schema source.
- Root `stores` or `plugin_instances` registries without new approval.
- Actor wire aliases other than `actor_type: agent|human` — `interaction` is explicitly excluded.
- `switch_function` or any other routing-function branch field — `merge_function` is valid for gather but is not a routing function.
- `output_field` as a BranchNode routing mode — removed by D-GR-35.
- The stale exclusive single-path `condition_type`/`condition`/`paths` BranchNode shape — replaced by per-port `outputs` model.
- Standalone Map/Fold/Loop node types or other compound-node replacements for phase modes.
- Replacing or breaking the existing imperative `iriai-compose` subclass API.
- Runtime agent execution inside the composer application.
- Migration tooling for legacy iriai-build v1 configs.

### From: dag-loader-runner

- Supporting a second serialized workflow dialect for flattened graphs or alternate root containers.
- Serializing hooks through port_type, hidden callback lists, or separate hook sections.
- Serializing branch logic through switch_function or the old three-field schema (condition_type / condition / paths). The D-GR-35 per-port outputs model with optional merge_function is the only valid branch routing surface.
- Per-port output_field declarative-lookup mode on branch output ports — per-port conditions are expressions only.
- Treating workflow-schema.json as a runtime/editor schema contract.
- Adding stores or plugin_instances to the declarative WorkflowConfig root without an explicit future PRD change.
- A mandatory built-in core checkpoint/resume API in SF-2.
- Runner-managed MCP subprocess lifecycle.
- Production-plugin test-mode branches as the live-test strategy.

### From: testing-framework

- Introducing new testing capabilities beyond the R18 ABI-alignment correction.
- Breaking the `AgentRuntime` ABC to add a `node_id` kwarg — explicitly prohibited by SF-2 REQ-11 and this PRD.
- Supporting alternate hierarchical context merge orders.
- Defining or requiring a built-in core checkpoint/resume API in SF-2.
- Restoring D-SF3-16 under any consumer-local framing.

### From: workflow-migration

- Changing the abstract `AgentRuntime.invoke()` signature.
- Introducing a `node_id` keyword contract in migration tests, bridge code, or any other downstream consumer.
- Treating checkpoint/resume as a mandatory core SF-2 runtime API or backfilling it through consumer-layer abstractions.
- Reopening the resolved hierarchical merge-order decision from D-GR-23.
- Co-ownership of the SF-2 runtime boundary by SF-4; SF-4 is a consumer only.

### From: composer-app-foundation

- Multi-user collaboration on workflow configs
- Runtime workflow execution inside the compose app or tools hub
- Reusing or extending `tools/iriai-workflows` as the canonical compose implementation path
- SQLite support or a SQLite-first local persistence contract
- Plugin registry UI, plugin tables, or `/api/plugins` endpoints
- Tool Library UI, custom tools table, or `/api/tools` endpoints
- `workflow_entity_refs` table creation, row materialization, or `GET /api/{entity}/references/{id}` in SF-5 — hook infrastructure is SF-5's responsibility; the reference index and its API belong to SF-7
- Version-history list, diff, or restore UI
- Phase template library pages
- Migration tooling from iriai-build v1
- Serving starter template content from filesystem paths at request time — all template content lives in DB rows seeded by Alembic data migration
- An instance-level import endpoint (`POST /api/workflows/{id}/import`) — if replace-from-import semantics are needed, that decision belongs to a future subfeature
- Mutation hook event kinds beyond the four canonical ones (`imported`, `version_saved`, `deleted`, etc.)

### From: workflow-editor

- YAML side pane and live bidirectional YAML editing
- Version-history browsing UI inside the editor
- Visual JSON Schema builder
- Named transform registry UI or transform picker
- Runtime workflow execution inside the editor
- Collaborative multi-user editing
- Separate serialized hooks section
- Serialized `port_type` field
- Runtime fallback to static `workflow-schema.json`
- MiniCanvasThumbnail / CMP-64
- `tools/iriai-workflows` as the editor deployment shell
- SQLite as a runtime persistence dependency for compose editor flows
- Core-editor boot dependency on `/api/plugins` or `GET /api/{entity}/references/{id}`
- Foundation-owned `workflow_entity_refs` expansion

### From: libraries-registries

- Plugins Library pages, plugin endpoints, and PluginPicker surfaces
- Phase Templates Library
- Multi-user sharing or collaboration on library entities
- Tool auto-discovery from MCP servers
- Template version history or versioning UI
- Changing SF-5 foundation ownership beyond the accepted five-table boundary